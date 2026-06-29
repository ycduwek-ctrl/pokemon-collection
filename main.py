from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import gspread
from google.oauth2.service_account import Credentials
import cloudinary
import cloudinary.uploader
from PIL import Image
import io
import os
import json
import re
import base64
import requests

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

SCOPES = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
SHEET_URL = "https://docs.google.com/spreadsheets/d/1Hzn0CQCoZrMPKt-Hn6NfkXl6WhJ5tZC_-YILuFsiak0"

cloudinary.config(
    cloud_name=os.environ["CLOUDINARY_CLOUD_NAME"],
    api_key=os.environ["CLOUDINARY_API_KEY"],
    api_secret=os.environ["CLOUDINARY_API_SECRET"]
)

def get_sheet():
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT"])
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_url(SHEET_URL).worksheet("cards")

@app.get("/cards")
def get_cards():
    ws = get_sheet()
    return ws.get_all_records()

@app.post("/cards")
def add_card(data: dict):
    ws = get_sheet()
    rows = ws.get_all_records()
    cols = ["id","name","pokemon","set","number","year","condition","language","rarity","value","images","comments"]
    if not rows:
        ws.append_row(cols)
    ws.append_row([data.get(c,"") for c in cols])
    return {"ok": True}

@app.put("/cards/{card_id}")
def update_card(card_id: str, data: dict):
    ws = get_sheet()
    records = ws.get_all_records()
    cols = ["id","name","pokemon","set","number","year","condition","language","rarity","value","images","comments"]
    for i, row in enumerate(records):
        if str(row["id"]) == card_id:
            for j, col in enumerate(cols):
                if col in data:
                    ws.update_cell(i + 2, j + 1, data[col])
            return {"ok": True}
    return {"ok": False}

@app.delete("/cards/{card_id}")
def delete_card(card_id: str):
    ws = get_sheet()
    records = ws.get_all_records()
    for i, row in enumerate(records):
        if str(row["id"]) == card_id:
            ws.delete_rows(i + 2)
            return {"ok": True}
    return {"ok": False}

@app.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    data = await file.read()
    img = Image.open(io.BytesIO(data)).convert("RGB")
    # חיתוך יחס 2:3 כמו קלף פוקימון
    w, h = img.size
    target_ratio = 2/3
    if w/h > target_ratio:
        new_w = int(h * target_ratio)
        img = img.crop(((w-new_w)//2, 0, (w+new_w)//2, h))
    img = img.resize((400, 560), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=85)
    res = cloudinary.uploader.upload(buf.getvalue(), format="webp")
    return {"url": res["secure_url"]}

@app.post("/identify")
async def identify(front: UploadFile = File(...), back: UploadFile = File(None)):
    def compress(f):
        img = Image.open(io.BytesIO(f)).convert("RGB")
        img.thumbnail((600,600), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=75)
        return base64.b64encode(buf.getvalue()).decode()

    front_b64 = compress(await front.read())
    content = [
        {"type":"text","text":"""אתה מומחה לקלפי פוקימון. זהה את הקלף בתמונה והחזר JSON בלבד:
{"name":"שם הקלף המלא","pokemon":"שם הפוקימון","set":"שם הסדרה","number":"מספר הקלף בסדרה","year":"שנת הוצאה","condition":"מצב הקלף: Mint/Near Mint/Excellent/Good/Poor","language":"שפה: English/Japanese/Hebrew/Other","rarity":"נדירות: Common/Uncommon/Rare/Holo Rare/Ultra Rare/Secret Rare","value":"ערך משוער בדולרים (מספר בלבד)"}"""},
        {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{front_b64}"}}
    ]
    if back:
        back_b64 = compress(await back.read())
        content.append({"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{back_b64}"}})

    res = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_KEY']}","Content-Type":"application/json"},
        json={"model":"google/gemini-flash-1.5","messages":[{"role":"user","content":content}]},
        timeout=30
    )
    result = res.json()
    if "error" in result:
        return {"error": result["error"]["message"]}
    text = result["choices"][0]["message"]["content"].strip()
    text = re.sub(r'```json|```','',text).strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        return json.loads(match.group())
    return json.loads(text)

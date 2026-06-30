from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
import gspread
from google.oauth2.service_account import Credentials
import cloudinary
import cloudinary.uploader
from PIL import Image, ImageOps
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
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
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

def lookup_tcg_api(name, number=None):
    """חיפוש במאגר Pokemon TCG API האמיתי לפי שם (ואופציונלית מספר) להשלמת/תיקון פרטים ומחיר שוק אמיתי"""
    try:
        query = f'name:"{name}"'
        r = requests.get(
            "https://api.pokemontcg.io/v2/cards",
            params={"q": query, "pageSize": 10, "orderBy": "-set.releaseDate"},
            timeout=10
        )
        data = r.json().get("data", [])
        if not data:
            return None
        # אם יש מספר - ננסה להתאים בדיוק
        card = None
        if number:
            num_only = str(number).split("/")[0].lstrip("0") or "0"
            for c in data:
                if c.get("number","").lstrip("0") == num_only:
                    card = c
                    break
        if not card:
            card = data[0]
        price = None
        prices = card.get("tcgplayer", {}).get("prices", {})
        for variant in ["holofoil","normal","reverseHolofoil","1stEditionHolofoil","unlimitedHolofoil"]:
            if variant in prices and prices[variant].get("market"):
                price = prices[variant]["market"]
                break
        return {
            "set": card.get("set",{}).get("name"),
            "number": f'{card.get("number")}/{card.get("set",{}).get("printedTotal","")}',
            "year": (card.get("set",{}).get("releaseDate") or "")[:4],
            "value": round(price,2) if price else None,
            "rarity_api": card.get("rarity")
        }
    except Exception:
        return None

@app.post("/identify")
async def identify(front: UploadFile = File(...), back: UploadFile = File(None)):
    def compress(f):
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(f))).convert("RGB")
        img.thumbnail((600,600), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=75)
        return base64.b64encode(buf.getvalue()).decode()

    front_b64 = compress(await front.read())
    content = [
        {"type":"text","text":"""אתה מומחה לקלפי פוקימון (Pokemon TCG). תפקידך לקרוא מהתמונה רק את הפרטים שניתן לראות בבירור.

קרא מהקלף:
1. pokemon - שם הפוקימון בדיוק כפי שכתוב בכותרת הקלף באנגלית (למשל "Charizard", "Sliggoo", "Pikachu")
2. number - המספר בפינה התחתונה בפורמט המדויק כפי שמופיע (למשל "095/086", "4/102", "025")
3. language - שפת הטקסט הראשי על הקלף: English/Japanese/Hebrew/Other
4. condition - הערכת המצב הפיזי של הקלף עצמו שרואים בתמונה: Mint/Near Mint/Excellent/Good/Poor

אל תנחש שום פרט שלא ניתן לקרוא בבירור - השאר ריק.
החזר JSON בלבד: {"pokemon":"","number":"","language":"","condition":""}"""},
        {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{front_b64}"}}
    ]
    if back:
        back_b64 = compress(await back.read())
        content.append({"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{back_b64}"}})

    res = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_KEY']}","Content-Type":"application/json"},
        json={"model": "openrouter/auto","messages":[{"role":"user","content":content}]},
        timeout=30
    )
    result = res.json()
    if "error" in result:
        return {"error": result["error"]["message"]}
    text = result["choices"][0]["message"]["content"].strip()
    text = re.sub(r'```json|```','',text).strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    parsed = json.loads(match.group()) if match else json.loads(text)

    # ה-AI נתן שם + מספר + מצב + שפה
    # עכשיו מחפשים ב-Pokemon TCG API את כל שאר הנתונים
    search_name = parsed.get("pokemon","").strip()
    if not search_name:
        return parsed  # לא הצלחנו לזהות שם — מחזירים מה שיש

    # בקשה שנייה ל-AI — הערכת מחיר ונדירות (גיבוי אם ה-API לא יחזיר)
    content_price = [
        {"type":"text","text":f"""קלף פוקימון: {search_name}, מספר {parsed.get("number","")}.
החזר JSON בלבד עם הערכה גסה: {{"value":"מחיר בדולרים מספר בלבד","rarity":"Common/Uncommon/Rare/Holo Rare/Ultra Rare/Secret Rare"}}"""},
        {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{front_b64}"}}
    ]
    ai_price_res = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_KEY']}","Content-Type":"application/json"},
        json={"model":"openrouter/auto","messages":[{"role":"user","content":content_price}]},
        timeout=20
    )
    ai_price = {}
    try:
        pt = ai_price_res.json()["choices"][0]["message"]["content"].strip()
        pt = re.sub(r'```json|```','',pt).strip()
        m = re.search(r'\{.*\}', pt, re.DOTALL)
        ai_price = json.loads(m.group()) if m else {}
    except Exception:
        pass

    api_data = lookup_tcg_api(search_name, parsed.get("number",""))

    result_final = {
        "name":      search_name,
        "pokemon":   search_name,
        "number":    parsed.get("number",""),
        "language":  parsed.get("language",""),
        "condition": parsed.get("condition",""),
        # סדרה ושנה — מה-API בלבד (אמין)
        "set":    api_data.get("set","")   if api_data else "",
        "year":   api_data.get("year","")  if api_data else "",
        # נדירות — מה-API אם נמצא, אחרת מה-AI
        "rarity": (api_data.get("rarity_api","") if api_data and api_data.get("rarity_api") else ai_price.get("rarity","")),
        # מחיר — מה-API אם נמצא, אחרת הערכת AI
        "value":  (str(api_data["value"]) if api_data and api_data.get("value") else ai_price.get("value","")),
    }

    # אם ה-API מצא מספר מדויק יותר (עם סך הכל), נעדיף אותו
    if api_data and api_data.get("number") and "/" in api_data["number"]:
        result_final["number"] = api_data["number"]

    return result_final

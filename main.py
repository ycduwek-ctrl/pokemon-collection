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
from difflib import SequenceMatcher

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

def _clean_card_number(value):
    value = str(value or "").strip().upper()
    if value.isdigit():
        return str(int(value))
    return re.sub(r"[^A-Z0-9]", "", value)

def _card_number_parts(value):
    parts = str(value or "").split("/", 1)
    local_id = _clean_card_number(parts[0])
    set_count = _clean_card_number(parts[1]) if len(parts) > 1 else ""
    return local_id, set_count

def _english_card_name(value):
    return re.sub(r"\s*\([^)]*[\u0590-\u05FF][^)]*\)\s*$", "", str(value or "")).strip()

def _market_price_for_card(card_info):
    """Find an exact TCGdex card and return a stable TCGplayer market price."""
    english_name = _english_card_name(card_info.get("name") or card_info.get("pokemon"))
    card_number, printed_set_count = _card_number_parts(card_info.get("number"))
    set_name = str(card_info.get("set") or "").strip()

    if not english_name or not card_number:
        return {"value": "", "priceStatus": "missing-identifiers"}

    try:
        search = requests.get(
            "https://api.tcgdex.net/v2/en/cards",
            params={"name": english_name},
            timeout=12
        )
        search.raise_for_status()
        candidates = [
            c for c in search.json()
            if _clean_card_number(c.get("localId")) == card_number
            and str(c.get("name") or "").lower() == english_name.lower()
        ]

        if not candidates:
            return {"value": "", "priceStatus": "not-found"}

        details = []
        for candidate in candidates[:8]:
            response = requests.get(
                f"https://api.tcgdex.net/v2/en/cards/{candidate['id']}",
                timeout=12
            )
            if response.ok:
                details.append(response.json())

        if not details:
            return {"value": "", "priceStatus": "not-found"}

        # The denominator printed on cards (for example 095/086) is often a
        # stronger set identifier than a translated or AI-read set name.
        if printed_set_count.isdigit():
            count_matches = [
                d for d in details
                if str(((d.get("set") or {}).get("cardCount") or {}).get("official", "")) == printed_set_count
            ]
            if count_matches:
                details = count_matches

        # A card name and local number can exist in several sets. Use the set
        # name only to disambiguate; never guess when the match remains unclear.
        if len(details) > 1:
            if not set_name:
                return {"value": "", "priceStatus": "ambiguous"}
            scored = sorted([
                (
                    SequenceMatcher(
                        None,
                        set_name.lower(),
                        str((d.get("set") or {}).get("name") or "").lower()
                    ).ratio(),
                    d
                )
                for d in details
            ], key=lambda item: item[0], reverse=True)
            best_score, best_card = scored[0]
            second_score = scored[1][0] if len(scored) > 1 else 0
            if best_score < 0.55 or best_score - second_score < 0.10:
                return {"value": "", "priceStatus": "ambiguous"}
            card = best_card
        else:
            card = details[0]

        tcgplayer = (card.get("pricing") or {}).get("tcgplayer") or {}
        price_variants = {
            key: value for key, value in tcgplayer.items()
            if isinstance(value, dict) and value.get("marketPrice") is not None
        }
        if not price_variants:
            return {
                "value": "",
                "priceStatus": "price-unavailable",
                "marketCardId": card.get("id", "")
            }

        finish = str(card_info.get("finish") or "").lower()
        rarity = str(card_info.get("rarity") or "").lower()
        preferred = []
        if "reverse" in finish:
            preferred = ["reverse-holofoil", "reverse"]
        elif "holo" in finish or "holo" in rarity:
            preferred = ["holofoil", "holo"]
        elif "first" in finish:
            preferred = ["1st-edition", "1st-edition-holofoil"]
        else:
            preferred = ["normal", "unlimited"]

        chosen_key = next((key for key in preferred if key in price_variants), None)
        if not chosen_key and len(price_variants) == 1:
            chosen_key = next(iter(price_variants))
        if not chosen_key:
            return {
                "value": "",
                "priceStatus": "variant-ambiguous",
                "marketCardId": card.get("id", "")
            }

        price = float(price_variants[chosen_key]["marketPrice"])
        return {
            "value": f"{price:.2f}".rstrip("0").rstrip("."),
            "priceStatus": "matched",
            "priceSource": "TCGplayer",
            "priceVariant": chosen_key,
            "priceUpdatedAt": tcgplayer.get("updated", ""),
            "marketCardId": card.get("id", "")
        }
    except (requests.RequestException, ValueError, TypeError, KeyError):
        # Identification must keep working even when the free price service is
        # unavailable. A blank value is safer than an invented price.
        return {"value": "", "priceStatus": "service-unavailable"}

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
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(f))).convert("RGB")
        img.thumbnail((800, 800), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        return base64.b64encode(buf.getvalue()).decode()

    front_b64 = compress(await front.read())
    content = [
        {"type":"text","text":"""אתה מומחה לקלפי פוקימון (Pokemon TCG). זהה את הקלף בתמונה בדייקנות ובדוק היטב את כל הפרטים הנראים על הקלף עצמו.

קרא את פרטי הקלף, אך את השם החזר בפורמט קבוע:
- גם אם הקלף ביפנית, סינית או שפה אחרת, זהה את השם הרשמי באנגלית
- בשדה name כתוב: English Card Name (תעתיק/תרגום השם לעברית)
- דוגמה: Toxtricity VMAX (טוקסטריסיטי VMAX)
- תרגם או תעתק לעברית רק את שם הקלף. אל תתרגם לעברית סדרה, מצב, שפה, נדירות או פרטים אחרים
- שמור סימוני קלף כמו V, VMAX, VSTAR, GX, EX ו-ex כפי שהם

קרא ישירות מהקלף:
- שם הפוקימון הרשמי באנגלית, גם אם הוא מודפס על הקלף בשפה אחרת
- מספר הקלף ומספר הסדרה בפינה התחתונה (למשל 4/102)
- שם הסדרה הרשמי באנגלית (Base Set, Jungle, Fossil, Team Rocket וכו׳), גם אם הקלף בשפה אחרת
- שנת ההוצאה אם מופיעה
- נדירות לפי הסמל (♦=Common, ♦♦=Uncommon, ★=Rare, ★H=Holo Rare)
- גימור הקלף: normal / holofoil / reverse-holofoil / first-edition
- שפת הטקסט
- מצב פיזי של הקלף

אל תנחש מחיר ואל תחזיר הערכת שווי. המחיר יילקח לאחר הזיהוי ממאגר מחירי שוק.

החזר JSON בלבד:
{"name":"","pokemon":"","set":"","number":"","year":"","condition":"Mint/Near Mint/Excellent/Good/Poor","language":"English/Japanese/Hebrew/Other","rarity":"Common/Uncommon/Rare/Holo Rare/Ultra Rare/Secret Rare","finish":"normal/holofoil/reverse-holofoil/first-edition"}"""},
        {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{front_b64}"}}
    ]
    if back:
        back_b64 = compress(await back.read())
        content.append({"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{back_b64}"}})

    res = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_KEY']}","Content-Type":"application/json"},
        json={"model":"openrouter/auto","messages":[{"role":"user","content":content}]},
        timeout=30
    )
    result = res.json()
    if "error" in result:
        return {"error": result["error"]["message"]}
    text = result["choices"][0]["message"]["content"].strip()
    text = re.sub(r'```json|```','',text).strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    identified = json.loads(match.group() if match else text)
    identified.update(_market_price_for_card(identified))
    return identified

from fastapi import FastAPI, UploadFile, File, BackgroundTasks
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
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import threading

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

CARD_COLUMNS = [
    "id", "name", "pokemon", "set", "number", "year", "condition",
    "language", "rarity", "value", "images", "comments", "setCode",
    "catalogCardId", "finish", "priceSource", "priceUpdatedAt",
    "priceCheckedAt"
]

def get_sheet():
    info = json.loads(os.environ["GOOGLE_SERVICE_ACCOUNT"])
    creds = Credentials.from_service_account_info(info, scopes=SCOPES)
    client = gspread.authorize(creds)
    return client.open_by_url(SHEET_URL).worksheet("cards")

def _ensure_columns(ws):
    headers = ws.row_values(1)
    if not headers:
        ws.append_row(CARD_COLUMNS)
        return list(CARD_COLUMNS)
    for column in CARD_COLUMNS:
        if column not in headers:
            headers.append(column)
            ws.update_cell(1, len(headers), column)
    return headers

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

def _pokemon_tcg_api_price(card_info):
    """Primary free price lookup using Pokémon TCG API / TCGplayer data."""
    english_name = _english_card_name(card_info.get("name") or card_info.get("pokemon"))
    card_number, printed_set_count = _card_number_parts(card_info.get("number"))
    set_name = str(card_info.get("set") or "").strip()
    if not english_name or not card_number:
        return {"value": "", "priceStatus": "missing-identifiers"}

    try:
        query_name = english_name.replace("\\", " ").replace('"', " ")
        headers = {}
        api_key = os.environ.get("POKEMON_TCG_API_KEY")
        if api_key:
            headers["X-Api-Key"] = api_key
        response = requests.get(
            "https://api.pokemontcg.io/v2/cards",
            params={
                "q": f'name:"{query_name}"',
                "pageSize": 250,
                "select": "id,name,number,set,rarity,tcgplayer"
            },
            headers=headers,
            timeout=15
        )
        response.raise_for_status()
        candidates = [
            card for card in (response.json().get("data") or [])
            if _clean_card_number(card.get("number")) == card_number
        ]
        if not candidates:
            return {"value": "", "priceStatus": "not-found"}

        if printed_set_count.isdigit():
            exact_set = [
                card for card in candidates
                if str((card.get("set") or {}).get("printedTotal", "")) == printed_set_count
                or str((card.get("set") or {}).get("total", "")) == printed_set_count
            ]
            if exact_set:
                candidates = exact_set

        if len(candidates) > 1:
            scored = []
            for card in candidates:
                name_score = SequenceMatcher(
                    None, english_name.lower(), str(card.get("name") or "").lower()
                ).ratio()
                candidate_set = str((card.get("set") or {}).get("name") or "")
                set_score = SequenceMatcher(
                    None, set_name.lower(), candidate_set.lower()
                ).ratio() if set_name else 0
                scored.append(((name_score * 0.70) + (set_score * 0.30), card))
            scored.sort(key=lambda item: item[0], reverse=True)
            best_score, card = scored[0]
            second_score = scored[1][0] if len(scored) > 1 else 0
            if best_score < 0.62 or best_score - second_score < 0.06:
                return {"value": "", "priceStatus": "ambiguous"}
        else:
            card = candidates[0]

        tcgplayer = card.get("tcgplayer") or {}
        variants = {
            key: value for key, value in (tcgplayer.get("prices") or {}).items()
            if isinstance(value, dict) and value.get("market") is not None
        }
        if not variants:
            return {
                "value": "",
                "priceStatus": "price-unavailable",
                "marketCardId": card.get("id", "")
            }

        finish = str(card_info.get("finish") or "").lower()
        rarity = str(card_info.get("rarity") or "").lower()
        if "first" in finish:
            preferred = ["1stEditionHolofoil", "1stEditionNormal"]
        elif "reverse" in finish:
            preferred = ["reverseHolofoil"]
        elif "holo" in finish or "holo" in rarity:
            preferred = ["holofoil", "unlimitedHolofoil"]
        else:
            preferred = ["normal", "unlimited", "holofoil"]

        chosen_key = next((key for key in preferred if key in variants), None)
        if not chosen_key and len(variants) == 1:
            chosen_key = next(iter(variants))
        if not chosen_key:
            return {
                "value": "",
                "priceStatus": "variant-ambiguous",
                "marketCardId": card.get("id", "")
            }

        price = float(variants[chosen_key]["market"])
        return {
            "value": f"{price:.2f}".rstrip("0").rstrip("."),
            "priceStatus": "matched",
            "priceSource": "TCGplayer",
            "priceVariant": chosen_key,
            "priceUpdatedAt": tcgplayer.get("updatedAt", ""),
            "marketCardId": card.get("id", "")
        }
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return {"value": "", "priceStatus": "service-unavailable"}


def _enrich_identification_from_catalog(card_info):
    """Verify a foreign printing by its language, set code and collector number."""
    language = str(card_info.get("language") or "").strip().lower()
    language_codes = {
        "japanese": "ja", "chinese": "zh-tw", "korean": "ko",
        "french": "fr", "german": "de", "spanish": "es",
        "italian": "it", "portuguese": "pt", "thai": "th",
        "indonesian": "id"
    }
    lang = language_codes.get(language)
    if not lang:
        return card_info

    card_number, printed_set_count = _card_number_parts(card_info.get("number"))
    if not card_number:
        return card_info
    set_code = re.sub(r"[^A-Z0-9]", "", str(card_info.get("setCode") or "").upper())
    printed_name = str(card_info.get("printedName") or "").strip()

    try:
        search = requests.get(
            f"https://api.tcgdex.net/v2/{lang}/cards",
            params={"localId": card_number},
            timeout=12
        )
        search.raise_for_status()
        candidates = [
            card for card in search.json()
            if _clean_card_number(card.get("localId")) == card_number
        ]
        if set_code:
            code_matches = [
                card for card in candidates
                if re.sub(
                    r"[^A-Z0-9]", "",
                    str(card.get("id") or "").rsplit("-", 1)[0].upper()
                ) == set_code
            ]
            if code_matches:
                candidates = code_matches
        elif len(candidates) > 12:
            # Avoid guessing among dozens of foreign sets with the same number.
            return card_info

        details = []
        for candidate in candidates[:12]:
            response = requests.get(
                f"https://api.tcgdex.net/v2/{lang}/cards/{candidate['id']}",
                timeout=12
            )
            if response.ok:
                details.append(response.json())

        if printed_set_count.isdigit():
            count_matches = [
                card for card in details
                if str(((card.get("set") or {}).get("cardCount") or {}).get("official", "")) == printed_set_count
            ]
            if count_matches:
                details = count_matches

        if len(details) > 1 and printed_name:
            scored = sorted(
                [
                    (
                        SequenceMatcher(
                            None,
                            printed_name.casefold(),
                            str(card.get("name") or "").casefold()
                        ).ratio(),
                        card
                    )
                    for card in details
                ],
                key=lambda item: item[0],
                reverse=True
            )
            if scored[0][0] >= 0.72 and (
                len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.10
            ):
                details = [scored[0][1]]

        if len(details) != 1:
            return card_info
        card = details[0]
        card_set = card.get("set") or {}
        official_count = str((card_set.get("cardCount") or {}).get("official") or "")
        resolved_code = str(card.get("id") or "").rsplit("-", 1)[0]
        card_info["setCode"] = resolved_code
        card_info["catalogCardId"] = card.get("id", "")
        if official_count:
            card_info["number"] = f"{card.get('localId', card_number)}/{official_count}"
        if not card_info.get("set"):
            card_info["set"] = card_set.get("name", "")
        if not card_info.get("rarity") and card.get("rarity"):
            card_info["rarity"] = card["rarity"]
        return card_info
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return card_info


def _localized_tcgdex_price(card_info):
    """Look up the exact non-English printing and normalize its price to USD."""
    language = str(card_info.get("language") or "").strip().lower()
    language_codes = {
        "japanese": "ja", "chinese": "zh-tw", "korean": "ko",
        "french": "fr", "german": "de", "spanish": "es",
        "italian": "it", "portuguese": "pt", "thai": "th",
        "indonesian": "id"
    }
    lang = language_codes.get(language)
    if not lang:
        return {"value": "", "priceStatus": "language-not-supported"}

    raw_number = str(card_info.get("number") or "").split("/", 1)[0].strip()
    raw_local_id = re.sub(r"[^A-Za-z0-9]", "", raw_number)
    card_number, printed_set_count = _card_number_parts(card_info.get("number"))
    set_code = re.sub(r"[^A-Z0-9]", "", str(card_info.get("setCode") or "").upper())
    catalog_card_id = str(card_info.get("catalogCardId") or "").strip()
    if not card_number:
        return {"value": "", "priceStatus": "missing-collector-number"}

    try:
        details = []
        direct_ids = []
        if catalog_card_id:
            direct_ids.append(catalog_card_id)
        if set_code and raw_local_id:
            direct_ids.append(f"{set_code.lower()}-{raw_local_id}")
        for direct_id in dict.fromkeys(direct_ids):
            response = requests.get(
                f"https://api.tcgdex.net/v2/{lang}/cards/{direct_id}",
                timeout=12
            )
            if response.ok:
                detail = response.json()
                if catalog_card_id or _clean_card_number(detail.get("localId")) == card_number:
                    details = [detail]
                    break

        if not details:
            candidates = []
            query_ids = list(dict.fromkeys(
                value for value in [raw_local_id, card_number] if value
            ))
            for query_id in query_ids:
                search = requests.get(
                    f"https://api.tcgdex.net/v2/{lang}/cards",
                    params={"localId": query_id},
                    timeout=12
                )
                search.raise_for_status()
                candidates = [
                    card for card in search.json()
                    if _clean_card_number(card.get("localId")) == card_number
                ]
                if candidates:
                    break

            if set_code:
                code_matches = [
                    card for card in candidates
                    if re.sub(
                        r"[^A-Z0-9]", "",
                        str(card.get("id") or "").rsplit("-", 1)[0].upper()
                    ) == set_code
                ]
                if code_matches:
                    candidates = code_matches

            for candidate in candidates[:24]:
                response = requests.get(
                    f"https://api.tcgdex.net/v2/{lang}/cards/{candidate['id']}",
                    timeout=12
                )
                if response.ok:
                    details.append(response.json())

        if printed_set_count.isdigit() and not catalog_card_id:
            count_matches = [
                card for card in details
                if str(((card.get("set") or {}).get("cardCount") or {}).get("official", "")) == printed_set_count
            ]
            if count_matches:
                details = count_matches

        if len(details) > 1:
            printed_name = str(card_info.get("printedName") or "").strip()
            if printed_name:
                scored = sorted(
                    [
                        (
                            SequenceMatcher(
                                None,
                                printed_name.casefold(),
                                str(card.get("name") or "").casefold()
                            ).ratio(),
                            card
                        )
                        for card in details
                    ],
                    key=lambda item: item[0],
                    reverse=True
                )
                if scored[0][0] >= 0.72 and (
                    len(scored) == 1 or scored[0][0] - scored[1][0] >= 0.10
                ):
                    details = [scored[0][1]]

        if len(details) != 1:
            return {
                "value": "",
                "priceStatus": "not-found" if not details else "ambiguous"
            }
        card = details[0]
        pricing = card.get("pricing") or {}

        tcgplayer = pricing.get("tcgplayer") or {}
        variants = {
            key: value for key, value in tcgplayer.items()
            if isinstance(value, dict) and value.get("marketPrice") is not None
        }
        finish = str(card_info.get("finish") or "").lower()
        rarity = str(card_info.get("rarity") or "").lower()
        if "reverse" in finish:
            preferred = ["reverse-holofoil", "reverse"]
        elif "holo" in finish or "holo" in rarity:
            preferred = ["holofoil", "holo"]
        elif "first" in finish:
            preferred = ["1st-edition-holofoil", "1st-edition"]
        else:
            preferred = ["normal", "unlimited"]

        chosen = next((key for key in preferred if key in variants), None)
        if not chosen and len(variants) == 1:
            chosen = next(iter(variants))
        if chosen:
            price = float(variants[chosen]["marketPrice"])
            return {
                "value": f"{price:.2f}".rstrip("0").rstrip("."),
                "priceStatus": "matched",
                "priceSource": "TCGplayer",
                "priceVariant": chosen,
                "priceUpdatedAt": tcgplayer.get("updated", ""),
                "marketCardId": card.get("id", ""),
                "catalogCardId": card.get("id", "")
            }

        cardmarket = pricing.get("cardmarket") or {}
        if "holo" in finish or "holo" in rarity:
            euro_price = (
                cardmarket.get("trend-holo")
                or cardmarket.get("avg7-holo")
                or cardmarket.get("avg-holo")
            )
            variant = "holo"
            if not euro_price:
                euro_price = (
                    cardmarket.get("trend")
                    or cardmarket.get("avg7")
                    or cardmarket.get("avg")
                )
                variant = "market"
        else:
            euro_price = (
                cardmarket.get("trend")
                or cardmarket.get("avg7")
                or cardmarket.get("avg")
            )
            variant = "normal"
        if euro_price is None:
            return {
                "value": "",
                "priceStatus": "price-unavailable",
                "catalogCardId": card.get("id", "")
            }

        fx_response = requests.get(
            "https://api.frankfurter.dev/v2/rate/EUR/USD",
            timeout=8
        )
        fx_response.raise_for_status()
        usd_rate = float(fx_response.json()["rate"])
        usd_price = float(euro_price) * usd_rate
        return {
            "value": f"{usd_price:.2f}".rstrip("0").rstrip("."),
            "priceStatus": "matched",
            "priceSource": "Cardmarket",
            "priceVariant": variant,
            "priceUpdatedAt": cardmarket.get("updated", ""),
            "marketCardId": card.get("id", ""),
            "catalogCardId": card.get("id", ""),
            "originalPrice": euro_price,
            "originalCurrency": "EUR"
        }
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return {"value": "", "priceStatus": "service-unavailable"}


def _market_price_for_card(card_info):
    """Find a verified market price across localized and English catalogs."""
    localized = _localized_tcgdex_price(card_info)
    if localized.get("priceStatus") == "matched":
        return localized

    primary = _pokemon_tcg_api_price(card_info)
    if primary.get("priceStatus") == "matched":
        return primary

    english_name = _english_card_name(card_info.get("name") or card_info.get("pokemon"))
    card_number, printed_set_count = _card_number_parts(card_info.get("number"))
    set_name = str(card_info.get("set") or "").strip()

    # The printed collector number is the primary identifier. Names and set
    # text are supporting signals only, since AI can translate or misread them.
    if not card_number:
        return {"value": "", "priceStatus": "missing-collector-number"}

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
            and SequenceMatcher(
                None,
                str(c.get("name") or "").lower(),
                english_name.lower()
            ).ratio() >= 0.88
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

        # If the collector number still maps to several sets, use the English
        # name and set as supporting signals. Never choose on name alone.
        if len(details) > 1:
            scored = []
            for detail in details:
                detail_name = str(detail.get("name") or "")
                detail_set = str((detail.get("set") or {}).get("name") or "")
                name_score = SequenceMatcher(
                    None, english_name.lower(), detail_name.lower()
                ).ratio() if english_name else 0
                set_score = SequenceMatcher(
                    None, set_name.lower(), detail_set.lower()
                ).ratio() if set_name else 0
                score = (name_score * 0.65) + (set_score * 0.35)
                scored.append((score, detail))
            scored.sort(key=lambda item: item[0], reverse=True)
            best_score, best_card = scored[0]
            second_score = scored[1][0] if len(scored) > 1 else 0
            if best_score < 0.58 or best_score - second_score < 0.08:
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
    _ensure_columns(ws)
    return ws.get_all_records()

@app.post("/cards")
def add_card(data: dict):
    ws = get_sheet()
    headers = _ensure_columns(ws)
    ws.append_row([data.get(column, "") for column in headers])
    return {"ok": True}

@app.put("/cards/{card_id}")
def update_card(card_id: str, data: dict):
    ws = get_sheet()
    headers = _ensure_columns(ws)
    records = ws.get_all_records()
    column_indexes = {
        column: index + 1 for index, column in enumerate(headers)
    }
    for row_index, row in enumerate(records, start=2):
        if str(row["id"]) == card_id:
            updates = [
                gspread.Cell(row_index, column_indexes[column], data[column])
                for column in CARD_COLUMNS
                if column in data and column in column_indexes
            ]
            if updates:
                ws.update_cells(updates, value_input_option="USER_ENTERED")
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

_price_refresh_lock = threading.Lock()
_price_refresh_status = {
    "running": False,
    "checked": 0,
    "updated": 0,
    "matched": 0,
    "lastRun": "",
    "error": ""
}

def _refresh_one_card(card):
    try:
        return card, _market_price_for_card(card)
    except Exception as exc:
        return card, {
            "value": "",
            "priceStatus": "error",
            "error": str(exc)
        }

def _refresh_all_prices_job():
    if not _price_refresh_lock.acquire(blocking=False):
        return
    _price_refresh_status.update({
        "running": True,
        "checked": 0,
        "updated": 0,
        "matched": 0,
        "error": ""
    })
    try:
        ws = get_sheet()
        headers = _ensure_columns(ws)
        records = ws.get_all_records()
        column_indexes = {
            column: index + 1 for index, column in enumerate(headers)
        }
        today = datetime.now(timezone.utc).date().isoformat()
        due = [
            (row_index, card)
            for row_index, card in enumerate(records, start=2)
            if not str(card.get("priceCheckedAt") or "").startswith(today)
        ]

        results = []
        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {
                executor.submit(_refresh_one_card, card): row_index
                for row_index, card in due
            }
            for future in as_completed(futures):
                row_index = futures[future]
                card, price_result = future.result()
                results.append((row_index, card, price_result))
                _price_refresh_status["checked"] += 1

        cells = []
        checked_at = datetime.now(timezone.utc).isoformat()
        for row_index, card, result in results:
            cells.append(gspread.Cell(
                row_index,
                column_indexes["priceCheckedAt"],
                checked_at
            ))
            catalog_card_id = result.get("catalogCardId")
            if catalog_card_id:
                cells.append(gspread.Cell(
                    row_index,
                    column_indexes["catalogCardId"],
                    catalog_card_id
                ))
            if result.get("priceStatus") != "matched":
                continue
            cells.extend([
                gspread.Cell(
                    row_index,
                    column_indexes["value"],
                    result.get("value", "")
                ),
                gspread.Cell(
                    row_index,
                    column_indexes["priceSource"],
                    result.get("priceSource", "")
                ),
                gspread.Cell(
                    row_index,
                    column_indexes["priceUpdatedAt"],
                    result.get("priceUpdatedAt", "")
                )
            ])
            _price_refresh_status["matched"] += 1
            if str(card.get("value") or "") != str(result.get("value") or ""):
                _price_refresh_status["updated"] += 1

        if cells:
            ws.update_cells(cells, value_input_option="USER_ENTERED")
        _price_refresh_status["lastRun"] = checked_at
    except Exception as exc:
        _price_refresh_status["error"] = str(exc)
    finally:
        _price_refresh_status["running"] = False
        _price_refresh_lock.release()

@app.post("/maintenance/refresh-prices")
def refresh_all_prices(background_tasks: BackgroundTasks):
    if _price_refresh_status["running"]:
        return {"ok": True, "status": "already-running"}
    background_tasks.add_task(_refresh_all_prices_job)
    return {"ok": True, "status": "started"}

@app.get("/maintenance/refresh-prices/status")
def refresh_all_prices_status():
    return _price_refresh_status

@app.post("/price")
def refresh_market_price(data: dict):
    return _market_price_for_card(data)

@app.post("/upload")
async def upload_image(file: UploadFile = File(...)):
    data = await file.read()
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(data))).convert("RGB")
    # Pokemon cards are portrait. Phone cameras sometimes save a landscape
    # frame without a useful EXIF orientation, so normalize it before cropping.
    if img.width > img.height:
        img = img.rotate(90, expand=True)
    w, h = img.size
    # Keep one consistent 5:7 card frame. The previous 2:3 crop was resized
    # to 5:7 afterwards, which visibly stretched some uploads.
    target_ratio = 5/7
    if w/h > target_ratio:
        new_w = int(h * target_ratio)
        img = img.crop(((w-new_w)//2, 0, (w+new_w)//2, h))
    else:
        new_h = int(w / target_ratio)
        img = img.crop((0, (h-new_h)//2, w, (h+new_h)//2))
    img = img.resize((500, 700), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="WEBP", quality=85)
    res = cloudinary.uploader.upload(buf.getvalue(), format="webp")
    return {"url": res["secure_url"]}

@app.post("/identify")
async def identify(front: UploadFile = File(...), back: UploadFile = File(None)):
    def compress(f):
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(f))).convert("RGB")
        img.thumbnail((1200, 1200), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=90)
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

הזיהוי חייב להתחיל ממספר האספן (Collector Number):
- חפש בפינה התחתונה של הקלף את המספר המודפס בפורמט מספר/גודל-סדרה, למשל 025/102
- העתק את שני חלקי המספר בדיוק, כולל אפסים מובילים ואותיות אם קיימות
- אל תשתמש במספר מדוגמה ואל תנחש. אם המספר אינו קריא, החזר number ריק
- השתמש במספר האספן כנתון הראשי לזיהוי הקלף והסדרה; השם והסמל הם אימות נוסף

קרא ישירות מהקלף:
- את השם המדויק כפי שהוא מודפס בשפת המקור החזר בשדה printedName
- את השם הרשמי באנגלית החזר בשדות name ו-pokemon, גם אם הוא מודפס על הקלף בשפה אחרת
- מספר האספן המלא בפינה התחתונה
- קוד הסדרה הקצר שמודפס ליד מספר האספן, למשל SV11W, SV2a, S12a או SM10. החזר אותו בשדה setCode; אם אינו קריא החזר מחרוזת ריקה
- שם הסדרה הרשמי באנגלית (Base Set, Jungle, Fossil, Team Rocket וכו׳), גם אם הקלף בשפה אחרת
- שנת ההוצאה אם מופיעה
- נדירות לפי הסמל (♦=Common, ♦♦=Uncommon, ★=Rare, ★H=Holo Rare)
- גימור הקלף: normal / holofoil / reverse-holofoil / first-edition
- שפת הטקסט
- מצב פיזי של הקלף

אל תנחש מחיר ואל תחזיר הערכת שווי. המחיר יילקח לאחר הזיהוי ממאגר מחירי שוק.

החזר JSON בלבד:
{"name":"","printedName":"","pokemon":"","set":"","setCode":"","number":"","year":"","condition":"Mint/Near Mint/Excellent/Good/Poor","language":"English/Japanese/Chinese/Korean/French/German/Spanish/Italian/Portuguese/Thai/Indonesian/Hebrew/Other","rarity":"Common/Uncommon/Rare/Holo Rare/Ultra Rare/Secret Rare","finish":"normal/holofoil/reverse-holofoil/first-edition"}"""},
        {"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{front_b64}"}}
    ]
    if back:
        back_b64 = compress(await back.read())
        content.append({"type":"image_url","image_url":{"url":f"data:image/jpeg;base64,{back_b64}"}})

    res = requests.post(
        "https://openrouter.ai/api/v1/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_KEY']}","Content-Type":"application/json"},
        json={
            "model": "openrouter/auto",
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 900
        },
        timeout=30
    )
    result = res.json()
    if "error" in result:
        return {"error": result["error"]["message"]}
    text = result["choices"][0]["message"]["content"].strip()
    text = re.sub(r'```json|```','',text).strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    identified = json.loads(match.group() if match else text)
    identified = _enrich_identification_from_catalog(identified)
    identified.update(_market_price_for_card(identified))
    return identified

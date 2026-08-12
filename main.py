from fastapi import FastAPI, UploadFile, File, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
import gspread
from google.oauth2.service_account import Credentials
from PIL import Image, ImageOps
import asyncio
import io
import os
import json
import re
import base64
import requests
import threading
import time
from difflib import SequenceMatcher
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from hitim_auth import (
    public_auth_config,
    ensure_access_request,
    require_access,
    list_access_users,
    update_access_user,
    remove_access_user,
)

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

CARD_COLUMNS = [
    "id", "name", "pokemon", "set", "number", "year", "condition",
    "language", "rarity", "value", "images", "comments", "setCode",
    "catalogCardId", "catalogImage", "tcgplayerProductId", "finish", "priceSource",
    "priceUpdatedAt",
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

def _tcgdex_image_url(card):
    """Return TCGdex's high-resolution scan for an exact card record."""
    image_url = str((card or {}).get("image") or "").strip().rstrip("/")
    if not image_url:
        return ""
    if re.search(r"\.(?:webp|png|jpe?g)$", image_url, re.IGNORECASE):
        return image_url
    return f"{image_url}/high.webp"


_tcgdex_card_cache = {}
_tcgdex_card_cache_lock = threading.Lock()


def _fetch_tcgdex_card(language, card_id, timeout=6):
    cache_key = (str(language), str(card_id))
    now = time.monotonic()
    with _tcgdex_card_cache_lock:
        cached = _tcgdex_card_cache.get(cache_key)
        if cached and now - cached[0] < 12 * 60 * 60:
            return cached[1]
    try:
        response = requests.get(
            f"https://api.tcgdex.net/v2/{language}/cards/{card_id}",
            timeout=timeout
        )
        card = response.json() if response.ok else None
        if card:
            with _tcgdex_card_cache_lock:
                if len(_tcgdex_card_cache) > 1600:
                    _tcgdex_card_cache.clear()
                _tcgdex_card_cache[cache_key] = (now, card)
        return card
    except (requests.RequestException, ValueError, TypeError):
        return None


def _search_tcgdex_cards(language, timeout=5, **params):
    try:
        response = requests.get(
            f"https://api.tcgdex.net/v2/{language}/cards",
            params=params,
            timeout=timeout
        )
        response.raise_for_status()
        payload = response.json()
        return payload if isinstance(payload, list) else []
    except (requests.RequestException, ValueError, TypeError):
        return []


_tcgdex_sets_cache = {}
_tcgdex_sets_lock = threading.Lock()
_catalog_image_cache = {}
_catalog_image_cache_lock = threading.Lock()


def _normalized_pixels(image, size=(24, 34)):
    gray = ImageOps.autocontrast(image.convert("L")).resize(size, Image.LANCZOS)
    values = [float(value) for value in gray.getdata()]
    mean = sum(values) / max(1, len(values))
    variance = sum((value - mean) ** 2 for value in values) / max(1, len(values))
    deviation = max(8.0, variance ** 0.5)
    return [(value - mean) / deviation for value in values]


def _pixel_correlation(first, second):
    if not first or len(first) != len(second):
        return -1.0
    return sum(a * b for a, b in zip(first, second)) / len(first)


def _card_crop_variants(image):
    """Try several centered card windows so carpet/table around a card is ignored."""
    image = ImageOps.exif_transpose(image).convert("RGB")
    width, height = image.size
    ratio = 2.5 / 3.5
    variants = []
    for coverage in (1.0, 0.92, 0.84, 0.76, 0.68):
        crop_height = height * coverage
        crop_width = crop_height * ratio
        if crop_width > width * coverage:
            crop_width = width * coverage
            crop_height = crop_width / ratio
        for x_shift in (-0.06, 0, 0.06):
            for y_shift in (-0.06, 0, 0.06):
                center_x = (width / 2) + (width * x_shift)
                center_y = (height / 2) + (height * y_shift)
                left = max(0, min(width - crop_width, center_x - crop_width / 2))
                top = max(0, min(height - crop_height, center_y - crop_height / 2))
                variants.append(image.crop((left, top, left + crop_width, top + crop_height)))
    return variants


def _catalog_descriptor(image_url):
    now = time.monotonic()
    with _catalog_image_cache_lock:
        cached = _catalog_image_cache.get(image_url)
        if cached and now - cached[0] < 12 * 60 * 60:
            return cached[1]
    # The low scan is enough for matching and is considerably faster to fetch.
    match_url = re.sub(r"/high\.(webp|png|jpe?g)$", r"/low.\1", image_url, flags=re.IGNORECASE)
    response = requests.get(match_url, timeout=6)
    if not response.ok and match_url != image_url:
        response = requests.get(image_url, timeout=6)
    response.raise_for_status()
    image = Image.open(io.BytesIO(response.content)).convert("RGB")
    descriptor = _normalized_pixels(image)
    with _catalog_image_cache_lock:
        if len(_catalog_image_cache) > 800:
            _catalog_image_cache.clear()
        _catalog_image_cache[image_url] = (now, descriptor)
    return descriptor


def _visual_candidate_scores(source_image, candidates):
    source_descriptors = [
        _normalized_pixels(crop) for crop in _card_crop_variants(source_image)
    ]

    def score_candidate(candidate):
        card = candidate["card"]
        image_url = _tcgdex_image_url(card)
        if not image_url:
            return None
        try:
            target = _catalog_descriptor(image_url)
            visual = max(_pixel_correlation(source, target) for source in source_descriptors)
            return {**candidate, "visualScore": round(visual, 4), "catalogImage": image_url}
        except (requests.RequestException, OSError, ValueError, TypeError):
            return None

    if not candidates:
        return []
    with ThreadPoolExecutor(max_workers=min(6, len(candidates))) as executor:
        scored = [item for item in executor.map(score_candidate, candidates) if item]
    return sorted(scored, key=lambda item: item["visualScore"], reverse=True)


def _tcgdex_sets(language):
    now = time.monotonic()
    with _tcgdex_sets_lock:
        cached = _tcgdex_sets_cache.get(language)
        if cached and now - cached[0] < 12 * 60 * 60:
            return cached[1]
    response = requests.get(
        f"https://api.tcgdex.net/v2/{language}/sets",
        timeout=6
    )
    response.raise_for_status()
    sets = response.json() or []
    with _tcgdex_sets_lock:
        _tcgdex_sets_cache[language] = (now, sets)
    return sets


def _tcgdex_candidate_set_ids(language, card_info):
    """Translate printed abbreviations such as TWM to TCGdex IDs such as sv06."""
    set_code = re.sub(
        r"[^A-Z0-9]", "", str(card_info.get("setCode") or "").upper()
    )
    set_name = str(card_info.get("set") or "").strip()
    _, printed_count = _card_number_parts(card_info.get("number"))
    try:
        sets = _tcgdex_sets(language)
    except (requests.RequestException, ValueError, TypeError):
        return []

    scored = []
    for item in sets:
        item_id = str(item.get("id") or "")
        normalized_id = re.sub(r"[^A-Z0-9]", "", item_id.upper())
        official_count = str((item.get("cardCount") or {}).get("official") or "")
        name_score = SequenceMatcher(
            None,
            set_name.casefold(),
            str(item.get("name") or "").casefold()
        ).ratio() if set_name else 0
        code_match = bool(set_code and normalized_id == set_code)
        count_match = bool(printed_count and official_count == printed_count)
        score = (3 if code_match else 0) + (2 * name_score) + (1 if count_match else 0)
        if code_match or name_score >= 0.82 or (count_match and name_score >= 0.68):
            scored.append((score, name_score, item_id))
    scored.sort(reverse=True)
    if not scored:
        return []
    best = scored[0]
    if len(scored) > 1 and best[0] - scored[1][0] < 0.18 and not set_code:
        return []
    return [best[2]]

def _tcgplayer_image_url(product_id):
    product_id = str(product_id or "").strip()
    if product_id.endswith(".0"):
        product_id = product_id[:-2]
    if not product_id.isdigit():
        return ""
    return (
        "https://tcgplayer-cdn.tcgplayer.com/product/"
        f"{product_id}_in_1000x1000.jpg"
    )

def _with_catalog_image(payload, image_url):
    if image_url:
        payload["catalogImage"] = image_url
    return payload

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
                "select": "id,name,number,set,rarity,images,tcgplayer"
            },
            headers=headers,
            timeout=10
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

        catalog_image = (
            (card.get("images") or {}).get("large")
            or (card.get("images") or {}).get("small")
            or ""
        )
        tcgplayer = card.get("tcgplayer") or {}
        variants = {
            key: value for key, value in (tcgplayer.get("prices") or {}).items()
            if isinstance(value, dict) and value.get("market") is not None
        }
        if not variants:
            return _with_catalog_image({
                "value": "",
                "priceStatus": "price-unavailable",
                "marketCardId": card.get("id", "")
            }, catalog_image)

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
            return _with_catalog_image({
                "value": "",
                "priceStatus": "variant-ambiguous",
                "marketCardId": card.get("id", "")
            }, catalog_image)

        price = float(variants[chosen_key]["market"])
        return _with_catalog_image({
            "value": f"{price:.2f}".rstrip("0").rstrip("."),
            "priceStatus": "matched",
            "priceSource": "TCGplayer",
            "priceVariant": chosen_key,
            "priceUpdatedAt": tcgplayer.get("updatedAt", ""),
            "marketCardId": card.get("id", "")
        }, catalog_image)
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return {"value": "", "priceStatus": "service-unavailable"}


def _enrich_identification_from_catalog(card_info, source_image=None):
    """Turn OCR/AI hints into a verified catalog record; never trust them blindly."""
    language = str(card_info.get("language") or "").strip().lower()
    language_codes = {
        "english": "en",
        "japanese": "ja", "chinese": "zh-tw", "korean": "ko",
        "french": "fr", "german": "de", "spanish": "es",
        "italian": "it", "portuguese": "pt", "thai": "th",
        "indonesian": "id"
    }
    lang = language_codes.get(language)
    if not lang:
        return card_info

    number_hints = [card_info.get("number")]
    number_hints.extend(card_info.get("numberCandidates") or [])
    number_hints = list(dict.fromkeys(str(value or "").strip() for value in number_hints if value))[:4]
    clean_numbers = []
    printed_counts = set()
    for hint in number_hints:
        local_id, count = _card_number_parts(hint)
        raw_id = re.sub(r"[^A-Za-z0-9]", "", hint.split("/", 1)[0])
        if local_id:
            clean_numbers.append((local_id, raw_id or local_id))
        if count:
            printed_counts.add(count)
    clean_numbers = list(dict.fromkeys(clean_numbers))
    set_code = re.sub(r"[^A-Z0-9]", "", str(card_info.get("setCode") or "").upper())
    printed_name = str(card_info.get("printedName") or "").strip()
    english_name = _english_card_name(card_info.get("name") or card_info.get("pokemon"))
    if not clean_numbers and not printed_name and not english_name:
        return card_info

    try:
        candidate_ids = []
        if set_code:
            for card_number, raw_local_id in clean_numbers:
                direct_id = f"{set_code.lower()}-{raw_local_id}"
                direct = _fetch_tcgdex_card(lang, direct_id, timeout=4)
                if direct and _clean_card_number(direct.get("localId")) == card_number:
                    candidate_ids.append(direct_id)

        for card_number, raw_local_id in clean_numbers:
            candidates = [
                card for card in _search_tcgdex_cards(
                    lang, localId=raw_local_id or card_number
                )
                if _clean_card_number(card.get("localId")) == card_number
            ]
            candidates.sort(key=lambda card: (
                re.sub(r"[^A-Z0-9]", "", str(card.get("id") or "").rsplit("-", 1)[0].upper()) == set_code,
                SequenceMatcher(None, printed_name.casefold(), str(card.get("name") or "").casefold()).ratio()
            ), reverse=True)
            candidate_ids.extend(card.get("id") for card in candidates[:6] if card.get("id"))

        candidate_ids = list(dict.fromkeys(candidate_ids))[:10]

        # Collector numbers can be tiny or obscured by glare. In that case the
        # printed name is a useful way to build candidates, but the photo still
        # decides the final match below.
        name_hints = list(dict.fromkeys(filter(None, [
            printed_name,
            english_name,
        ])))[:2]
        for name_hint in name_hints:
            name_candidates = sorted(
                _search_tcgdex_cards(lang, name=name_hint),
                key=lambda card: SequenceMatcher(
                    None,
                    name_hint.casefold(),
                    str(card.get("name") or "").casefold()
                ).ratio(),
                reverse=True
            )
            candidate_ids.extend(
                card.get("id") for card in name_candidates[:8] if card.get("id")
            )

        candidate_ids = list(dict.fromkeys(candidate_ids))[:18]
        details = []
        if candidate_ids:
            with ThreadPoolExecutor(max_workers=min(4, len(candidate_ids))) as executor:
                details = [
                    {"card": detail, "languageCode": lang}
                    for detail in executor.map(
                        lambda card_id: _fetch_tcgdex_card(lang, card_id),
                        candidate_ids
                    ) if detail
                ]

        if printed_counts:
            details.sort(
                key=lambda item: _clean_card_number(
                    ((item["card"].get("set") or {}).get("cardCount") or {}).get("official", "")
                ) in printed_counts,
                reverse=True
            )

        if not details:
            return card_info

        scored = _visual_candidate_scores(source_image, details) if source_image else []
        if not scored:
            return card_info
        top = scored[0]
        second_score = scored[1]["visualScore"] if len(scored) > 1 else -1
        visually_verified = (
            top["visualScore"] >= 0.62
            or (top["visualScore"] >= 0.43 and top["visualScore"] - second_score >= 0.035)
            or (len(scored) == 1 and top["visualScore"] >= 0.36)
        )

        def candidate_payload(item):
            card = item["card"]
            card_set = card.get("set") or {}
            official_count = str((card_set.get("cardCount") or {}).get("official") or "")
            english_card = card if lang == "en" else _fetch_tcgdex_card("en", card.get("id", ""), timeout=3)
            official_name = str((english_card or {}).get("name") or card_info.get("name") or card.get("name") or "")
            hebrew_name = str(card_info.get("hebrewName") or "").strip()
            display_name = official_name + (f" ({hebrew_name})" if hebrew_name else "")
            return {
                "name": display_name,
                "pokemon": display_name,
                "printedName": str(card.get("name") or ""),
                "set": str(card_set.get("name") or card_info.get("set") or ""),
                "setCode": str(card.get("id") or "").rsplit("-", 1)[0],
                "number": f"{card.get('localId', '')}/{official_count}" if official_count else str(card.get("localId") or ""),
                "language": card_info.get("language") or "English",
                "finish": card_info.get("finish") or "",
                "rarity": card.get("rarity") or card_info.get("rarity") or "",
                "catalogCardId": card.get("id", ""),
                "catalogImage": item.get("catalogImage") or _tcgdex_image_url(card),
                "visualScore": item.get("visualScore", 0),
            }

        if not visually_verified:
            card_info.pop("catalogCardId", None)
            card_info.pop("catalogImage", None)
            card_info["needsConfirmation"] = True
            card_info["matchCandidates"] = [candidate_payload(item) for item in scored[:3]]
            return card_info

        card = top["card"]
        verified_payload = candidate_payload(top)
        card_set = card.get("set") or {}
        official_count = str((card_set.get("cardCount") or {}).get("official") or "")
        resolved_code = str(card.get("id") or "").rsplit("-", 1)[0]
        card_info["setCode"] = resolved_code
        card_info["catalogCardId"] = card.get("id", "")
        catalog_image = _tcgdex_image_url(card)
        if catalog_image:
            card_info["catalogImage"] = catalog_image
        if official_count:
            card_info["number"] = f"{card.get('localId', '')}/{official_count}"
        card_info["name"] = verified_payload["name"]
        card_info["pokemon"] = verified_payload["pokemon"]
        card_info["set"] = card_set.get("name", "") or card_info.get("set", "")
        card_info["printedName"] = card.get("name", "") or printed_name
        if not card_info.get("rarity") and card.get("rarity"):
            card_info["rarity"] = card["rarity"]
        card_info["visualMatchScore"] = top["visualScore"]
        card_info["needsConfirmation"] = False
        card_info.pop("matchCandidates", None)
        return card_info
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return card_info


def _localized_tcgdex_price(card_info):
    """Look up the exact non-English printing and normalize its price to USD."""
    language = str(card_info.get("language") or "").strip().lower()
    language_codes = {
        "": "en", "english": "en",
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
        if raw_local_id:
            direct_ids.extend(
                f"{set_id}-{raw_local_id}"
                for set_id in _tcgdex_candidate_set_ids(lang, card_info)
            )
        for direct_id in dict.fromkeys(direct_ids):
            response = requests.get(
                f"https://api.tcgdex.net/v2/{lang}/cards/{direct_id}",
                timeout=6
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
                    timeout=6
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

            candidate_ids = [candidate["id"] for candidate in candidates[:8]]
            if candidate_ids:
                with ThreadPoolExecutor(max_workers=min(4, len(candidate_ids))) as executor:
                    details = [
                        detail for detail in executor.map(
                            lambda card_id: _fetch_tcgdex_card(lang, card_id),
                            candidate_ids
                        ) if detail
                    ]

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
        catalog_image = _tcgdex_image_url(card)
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
            return _with_catalog_image({
                "value": f"{price:.2f}".rstrip("0").rstrip("."),
                "priceStatus": "matched",
                "priceSource": "TCGplayer",
                "priceVariant": chosen,
                "priceUpdatedAt": tcgplayer.get("updated", ""),
                "marketCardId": card.get("id", ""),
                "catalogCardId": card.get("id", "")
            }, catalog_image)

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
            return _with_catalog_image({
                "value": "",
                "priceStatus": "price-unavailable",
                "catalogCardId": card.get("id", "")
            }, catalog_image)

        fx_response = requests.get(
            "https://api.frankfurter.dev/v2/rate/EUR/USD",
            timeout=8
        )
        fx_response.raise_for_status()
        usd_rate = float(fx_response.json()["rate"])
        usd_price = float(euro_price) * usd_rate
        return _with_catalog_image({
            "value": f"{usd_price:.2f}".rstrip("0").rstrip("."),
            "priceStatus": "matched",
            "priceSource": "Cardmarket",
            "priceVariant": variant,
            "priceUpdatedAt": cardmarket.get("updated", ""),
            "marketCardId": card.get("id", ""),
            "catalogCardId": card.get("id", ""),
            "originalPrice": euro_price,
            "originalCurrency": "EUR"
        }, catalog_image)
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return {"value": "", "priceStatus": "service-unavailable"}


_TCGPLAYER_PUBLIC_HEADERS = {
    "Accept": "application/json",
    "User-Agent": "Mozilla/5.0 (compatible; PokemonCollection/1.0)"
}
_tcgplayer_set_cache = {}
_tcgplayer_guide_cache = {}

def _cached_tcgplayer_json(cache, key, url, params, ttl_seconds):
    cached = cache.get(key)
    now = time.monotonic()
    if cached and now - cached[0] < ttl_seconds:
        return cached[1]
    response = requests.get(
        url,
        params=params,
        headers=_TCGPLAYER_PUBLIC_HEADERS,
        timeout=8
    )
    response.raise_for_status()
    payload = response.json()
    cache[key] = (now, payload)
    return payload

def _tcgplayer_sets(category_id):
    payload = _cached_tcgplayer_json(
        _tcgplayer_set_cache,
        category_id,
        "https://mpapi.tcgplayer.com/v2/Catalog/SetNames",
        {"categoryId": category_id, "active": "true"},
        12 * 60 * 60
    )
    return payload.get("results") or []

def _tcgplayer_price_guide(set_id, product_type_id):
    payload = _cached_tcgplayer_json(
        _tcgplayer_guide_cache,
        (set_id, product_type_id),
        f"https://infinite-api.tcgplayer.com/priceguide/set/{set_id}/cards/",
        {"rows": 5000, "productTypeID": product_type_id},
        30 * 60
    )
    return payload.get("result") or []

def _tcgplayer_name(value):
    value = _english_card_name(value)
    return re.sub(
        r"\s*-\s*[A-Z0-9]+(?:/[A-Z0-9]+)?\s*$",
        "",
        value,
        flags=re.IGNORECASE
    ).strip()

def _tcgplayer_marketplace_price(card_info):
    """Fallback to TCGplayer's public marketplace data without an API key."""
    language = str(card_info.get("language") or "").strip().lower()
    if language == "japanese":
        category_id, product_type_id = 85, 143
    elif language in ("", "english"):
        category_id, product_type_id = 3, 1
    else:
        return {"value": "", "priceStatus": "language-not-supported"}

    english_name = _tcgplayer_name(
        card_info.get("name") or card_info.get("pokemon")
    )
    card_number, printed_set_count = _card_number_parts(
        card_info.get("number")
    )
    if not english_name or not card_number:
        return {"value": "", "priceStatus": "missing-identifiers"}

    try:
        stored_product_id = str(
            card_info.get("tcgplayerProductId") or ""
        ).strip()
        if stored_product_id.endswith(".0"):
            stored_product_id = stored_product_id[:-2]
        if stored_product_id.isdigit():
            response = requests.get(
                "https://mp-search-api.tcgplayer.com/v2/product/"
                f"{stored_product_id}/details",
                headers=_TCGPLAYER_PUBLIC_HEADERS,
                timeout=8
            )
            if response.ok:
                detail = response.json()
                detail_number = (
                    (detail.get("customAttributes") or {}).get("number")
                    or ""
                )
                detail_name = _tcgplayer_name(detail.get("productName"))
                number_ok = (
                    not detail_number
                    or _card_number_parts(detail_number)[0] == card_number
                )
                name_ok = (
                    not detail_name
                    or SequenceMatcher(
                        None,
                        detail_name.casefold(),
                        english_name.casefold()
                    ).ratio() >= 0.72
                )
                market_price = detail.get("marketPrice")
                if number_ok and name_ok and market_price is not None:
                    price = float(market_price)
                    return _with_catalog_image({
                        "value": f"{price:.2f}".rstrip("0").rstrip("."),
                        "priceStatus": "matched",
                        "priceSource": "TCGplayer",
                        "priceVariant": "market",
                        "priceUpdatedAt": datetime.now(
                            timezone.utc
                        ).isoformat(),
                        "tcgplayerProductId": stored_product_id
                    }, _tcgplayer_image_url(stored_product_id))

        set_code = re.sub(
            r"[^A-Z0-9]",
            "",
            str(card_info.get("setCode") or "").upper()
        )
        set_name = str(card_info.get("set") or "").strip()
        sets = _tcgplayer_sets(category_id)
        set_candidates = []
        if set_code:
            set_candidates = [
                item for item in sets
                if re.sub(
                    r"[^A-Z0-9]",
                    "",
                    str(item.get("abbreviation") or "").upper()
                ) == set_code
            ]

        if not set_candidates and set_name:
            scored_sets = sorted(
                [
                    (
                        SequenceMatcher(
                            None,
                            set_name.casefold(),
                            str(item.get("name") or "").casefold()
                        ).ratio(),
                        item
                    )
                    for item in sets
                ],
                key=lambda pair: pair[0],
                reverse=True
            )
            if scored_sets and scored_sets[0][0] >= 0.74 and (
                len(scored_sets) == 1
                or scored_sets[0][0] - scored_sets[1][0] >= 0.08
            ):
                set_candidates = [scored_sets[0][1]]

        if len(set_candidates) > 1 and set_name:
            set_candidates.sort(
                key=lambda item: SequenceMatcher(
                    None,
                    set_name.casefold(),
                    str(item.get("name") or "").casefold()
                ).ratio(),
                reverse=True
            )
            best_score = SequenceMatcher(
                None,
                set_name.casefold(),
                str(set_candidates[0].get("name") or "").casefold()
            ).ratio()
            second_score = SequenceMatcher(
                None,
                set_name.casefold(),
                str(set_candidates[1].get("name") or "").casefold()
            ).ratio()
            if best_score >= 0.62 and best_score - second_score >= 0.08:
                set_candidates = [set_candidates[0]]

        if len(set_candidates) != 1:
            return {
                "value": "",
                "priceStatus": "set-not-found"
                if not set_candidates else "ambiguous"
            }

        selected_set = set_candidates[0]
        rows = _tcgplayer_price_guide(
            int(selected_set["setNameId"]),
            product_type_id
        )
        rows = [
            row for row in rows
            if _card_number_parts(row.get("number"))[0] == card_number
            and (
                not printed_set_count
                or not _card_number_parts(row.get("number"))[1]
                or _card_number_parts(row.get("number"))[1]
                == printed_set_count
            )
        ]
        if not rows:
            return {"value": "", "priceStatus": "not-found"}

        products = {}
        for row in rows:
            product_id = str(row.get("productID") or "")
            if not product_id:
                continue
            product_name = _tcgplayer_name(row.get("productName"))
            score = SequenceMatcher(
                None,
                product_name.casefold(),
                english_name.casefold()
            ).ratio()
            products.setdefault(
                product_id,
                {"score": score, "rows": []}
            )["rows"].append(row)

        ranked = sorted(
            products.items(),
            key=lambda item: item[1]["score"],
            reverse=True
        )
        if not ranked or ranked[0][1]["score"] < 0.72:
            return {"value": "", "priceStatus": "not-found"}
        if len(ranked) > 1 and (
            ranked[0][1]["score"] - ranked[1][1]["score"] < 0.08
        ):
            return {"value": "", "priceStatus": "ambiguous"}

        product_id, selected = ranked[0]
        priced_rows = [
            row for row in selected["rows"]
            if row.get("marketPrice") is not None
            and str(row.get("condition") or "").lower().startswith(
                "near mint"
            )
        ]
        if not priced_rows:
            return _with_catalog_image({
                "value": "",
                "priceStatus": "price-unavailable",
                "tcgplayerProductId": product_id
            }, _tcgplayer_image_url(product_id))

        finish = str(card_info.get("finish") or "").lower()
        rarity = str(card_info.get("rarity") or "").lower()
        if "reverse" in finish:
            preferred_printings = ["reverse holofoil"]
        elif "holo" in finish or "holo" in rarity:
            preferred_printings = ["holofoil"]
        elif "first" in finish:
            preferred_printings = ["1st edition holofoil", "1st edition"]
        else:
            preferred_printings = ["normal"]

        chosen_rows = [
            row for row in priced_rows
            if str(row.get("printing") or "").lower()
            in preferred_printings
        ]
        if not chosen_rows:
            printings = {
                str(row.get("printing") or "").lower()
                for row in priced_rows
            }
            if len(printings) == 1:
                chosen_rows = priced_rows
        if len(chosen_rows) != 1:
            return _with_catalog_image({
                "value": "",
                "priceStatus": "variant-ambiguous",
                "tcgplayerProductId": product_id
            }, _tcgplayer_image_url(product_id))

        chosen = chosen_rows[0]
        price = float(chosen["marketPrice"])
        return _with_catalog_image({
            "value": f"{price:.2f}".rstrip("0").rstrip("."),
            "priceStatus": "matched",
            "priceSource": "TCGplayer",
            "priceVariant": chosen.get("printing", "market"),
            "priceUpdatedAt": datetime.now(timezone.utc).isoformat(),
            "tcgplayerProductId": product_id
        }, _tcgplayer_image_url(product_id))
    except (requests.RequestException, ValueError, TypeError, KeyError):
        return {"value": "", "priceStatus": "service-unavailable"}


_price_result_cache = {}
_price_cache_lock = threading.Lock()


def _price_cache_key(card_info):
    """Use only identity and printing fields that can change a market price."""
    values = (
        card_info.get("catalogCardId"),
        card_info.get("setCode"),
        card_info.get("number"),
        _english_card_name(card_info.get("name") or card_info.get("pokemon")),
        card_info.get("language"),
        card_info.get("finish"),
        card_info.get("rarity"),
    )
    return "|".join(str(value or "").strip().casefold() for value in values)


def _cached_market_price(cache_key):
    now = time.monotonic()
    with _price_cache_lock:
        cached = _price_result_cache.get(cache_key)
        if not cached or cached[0] <= now:
            if cached:
                _price_result_cache.pop(cache_key, None)
            return None
        return dict(cached[1])


def _remember_market_price(cache_key, result):
    status = result.get("priceStatus")
    ttl = 30 * 60 if status == "matched" else (
        45 if status == "service-unavailable" else 5 * 60
    )
    with _price_cache_lock:
        _price_result_cache[cache_key] = (
            time.monotonic() + ttl,
            dict(result),
        )
    return result


def _market_price_for_card(card_info):
    """Return the first verified exact price instead of waiting for every source."""
    if str(card_info.get("identityConfidence") or "").lower() == "unverified":
        return {"value": "", "priceStatus": "identity-unverified"}
    cache_key = _price_cache_key(card_info)
    cached = _cached_market_price(cache_key)
    if cached is not None:
        cached["priceCache"] = "hit"
        return cached

    catalog_image = str(card_info.get("catalogImage") or "").strip()
    language = str(card_info.get("language") or "english").strip().lower()
    card_number, _ = _card_number_parts(card_info.get("number"))
    has_exact_identity = bool(
        card_info.get("catalogCardId")
        or (card_info.get("setCode") and card_number)
    )
    direct_result = None

    # A set code plus collector number resolves to one TCGdex card and already
    # contains TCGplayer/Cardmarket pricing. This is the fastest and safest path.
    if has_exact_identity:
        direct_result = _localized_tcgdex_price(card_info)
        catalog_image = direct_result.get("catalogImage") or catalog_image
        if direct_result.get("priceStatus") == "matched":
            result = _with_catalog_image(direct_result, catalog_image)
            return _remember_market_price(cache_key, result)

    sources = {}
    if not has_exact_identity:
        sources["tcgdex"] = _localized_tcgdex_price
    if language in ("", "english"):
        sources["pokemon-tcg"] = _pokemon_tcg_api_price
        sources["tcgplayer"] = _tcgplayer_marketplace_price
    elif language == "japanese":
        sources["tcgplayer-jp"] = _tcgplayer_marketplace_price

    source_results = []
    matched = None
    if sources:
        executor = ThreadPoolExecutor(max_workers=min(3, len(sources)))
        futures = {
            executor.submit(source, card_info): name
            for name, source in sources.items()
        }
        try:
            for future in as_completed(futures):
                try:
                    result = future.result()
                except Exception:
                    result = {"value": "", "priceStatus": "service-unavailable"}
                source_results.append(result)
                catalog_image = result.get("catalogImage") or catalog_image
                if result.get("priceStatus") == "matched":
                    matched = result
                    break
        finally:
            if matched is not None:
                for future in futures:
                    future.cancel()
                executor.shutdown(wait=False, cancel_futures=True)
            else:
                executor.shutdown(wait=True)

    if matched is not None:
        result = _with_catalog_image(matched, catalog_image)
        return _remember_market_price(cache_key, result)

    candidates = ([direct_result] if direct_result else []) + source_results
    candidates = [result for result in candidates if result]
    preferred_statuses = (
        "variant-ambiguous", "ambiguous", "price-unavailable", "not-found",
        "missing-collector-number", "missing-identifiers", "service-unavailable"
    )
    for status in preferred_statuses:
        fallback = next(
            (result for result in candidates if result.get("priceStatus") == status),
            None
        )
        if fallback:
            result = _with_catalog_image(fallback, catalog_image)
            return _remember_market_price(cache_key, result)

    result = _with_catalog_image(
        {"value": "", "priceStatus": "not-found"},
        catalog_image
    )
    return _remember_market_price(cache_key, result)

@app.get("/health")
def health():
    return {
        "ok": True,
        "app": "Hitim",
        "build": "catalog-verified-v5",
        "authConfigured": public_auth_config()["configured"],
    }


@app.get("/auth/config")
def auth_config():
    return public_auth_config()


@app.post("/access/request")
def request_access(authorization: str = Header(None)):
    return ensure_access_request(authorization)


@app.get("/access/me")
def access_me(authorization: str = Header(None)):
    return ensure_access_request(authorization)


@app.get("/admin/users")
def admin_users(authorization: str = Header(None)):
    return list_access_users(authorization)


@app.patch("/admin/users/{user_id}")
def admin_update_user(user_id: str, data: dict, authorization: str = Header(None)):
    return update_access_user(authorization, user_id, str(data.get("status") or ""))


@app.delete("/admin/users/{user_id}")
def admin_delete_user(user_id: str, authorization: str = Header(None)):
    return remove_access_user(authorization, user_id)


def _legacy_cards():
    ws = get_sheet()
    return ws.get_all_records()


@app.get("/legacy/cards")
def legacy_cards(authorization: str = Header(None)):
    require_access(authorization, admin=True)
    return _legacy_cards()


@app.get("/cards")
def get_cards(authorization: str = Header(None)):
    require_access(authorization, admin=True)
    return _legacy_cards()

@app.post("/cards")
def add_card(data: dict, authorization: str = Header(None)):
    require_access(authorization, admin=True)
    raise HTTPException(status_code=410, detail="Hitim cards are stored on the user's device")

@app.put("/cards/{card_id}")
def update_card(card_id: str, data: dict, authorization: str = Header(None)):
    require_access(authorization, admin=True)
    raise HTTPException(status_code=410, detail="Hitim cards are stored on the user's device")

@app.delete("/cards/{card_id}")
def delete_card(card_id: str, authorization: str = Header(None)):
    require_access(authorization, admin=True)
    raise HTTPException(status_code=410, detail="Hitim cards are stored on the user's device")

_price_refresh_status = {
    "running": False,
    "checked": 0,
    "updated": 0,
    "matched": 0,
    "remaining": 0,
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

def _refresh_price_batch(limit=2):
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
        all_due = [
            (row_index, card)
            for row_index, card in enumerate(records, start=2)
            if not str(card.get("priceCheckedAt") or "").startswith(today)
        ]
        all_due.sort(
            key=lambda item: str(item[1].get("priceCheckedAt") or "")
        )
        batch_size = max(1, min(int(limit or 2), 4))
        due = all_due[:batch_size]
        checked_at = datetime.now(timezone.utc).isoformat()

        if not due:
            _price_refresh_status.update({
                "running": False,
                "remaining": 0,
                "lastRun": checked_at
            })
            return {
                "ok": True,
                "checked": 0,
                "matched": 0,
                "updated": 0,
                "remaining": 0,
                "checkedAt": checked_at
            }

        results = []
        with ThreadPoolExecutor(max_workers=min(2, len(due))) as executor:
            futures = {
                executor.submit(_refresh_one_card, card): row_index
                for row_index, card in due
            }
            for future in as_completed(futures):
                row_index = futures[future]
                card, price_result = future.result()
                results.append((row_index, card, price_result))

        cells = []
        matched = 0
        updated = 0
        for row_index, card, result in results:
            cells.append(gspread.Cell(
                row_index,
                column_indexes["priceCheckedAt"],
                checked_at
            ))
            catalog_card_id = result.get("catalogCardId")
            if catalog_card_id:
                cells.extend([
                    gspread.Cell(
                        row_index,
                        column_indexes["catalogCardId"],
                        catalog_card_id
                    ),
                    gspread.Cell(
                        row_index,
                        column_indexes["setCode"],
                        str(catalog_card_id).rsplit("-", 1)[0]
                    )
                ])
            catalog_image = result.get("catalogImage")
            if catalog_image:
                cells.append(gspread.Cell(
                    row_index,
                    column_indexes["catalogImage"],
                    catalog_image
                ))
            tcgplayer_product_id = result.get("tcgplayerProductId")
            if tcgplayer_product_id:
                cells.append(gspread.Cell(
                    row_index,
                    column_indexes["tcgplayerProductId"],
                    tcgplayer_product_id
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
            matched += 1
            if str(card.get("value") or "") != str(result.get("value") or ""):
                updated += 1

        if cells:
            ws.update_cells(cells, value_input_option="USER_ENTERED")

        remaining = max(0, len(all_due) - len(due))
        _price_refresh_status.update({
            "running": False,
            "checked": len(results),
            "matched": matched,
            "updated": updated,
            "remaining": remaining,
            "lastRun": checked_at
        })
        return {
            "ok": True,
            "checked": len(results),
            "matched": matched,
            "updated": updated,
            "remaining": remaining,
            "checkedAt": checked_at
        }
    except Exception as exc:
        _price_refresh_status.update({
            "running": False,
            "error": str(exc)
        })
        return {
            "ok": False,
            "error": str(exc),
            "remaining": _price_refresh_status.get("remaining", 0)
        }

@app.post("/maintenance/refresh-prices")
def refresh_all_prices(limit: int = 2, authorization: str = Header(None)):
    require_access(authorization, admin=True)
    raise HTTPException(status_code=410, detail="Price refresh now runs against each local gallery")

@app.get("/maintenance/refresh-prices/status")
def refresh_all_prices_status(authorization: str = Header(None)):
    require_access(authorization, admin=True)
    return _price_refresh_status

@app.post("/price")
def refresh_market_price(data: dict, authorization: str = Header(None)):
    require_access(authorization)
    return _market_price_for_card(data)

@app.post("/upload")
async def upload_image(file: UploadFile = File(...), authorization: str = Header(None)):
    require_access(authorization, admin=True)
    raise HTTPException(status_code=410, detail="Images are stored locally by the Hitim browser app")

@app.post("/identify")
async def identify(
    front: UploadFile = File(...),
    back: UploadFile = File(None),
    authorization: str = Header(None),
    mode: str = "quick"
):
    await asyncio.to_thread(require_access, authorization)
    mode = "deep" if str(mode).lower() == "deep" else "quick"

    def compress(raw):
        if not raw or len(raw) > 18 * 1024 * 1024:
            raise ValueError("invalid image size")
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw))).convert("RGB")
        img.thumbnail((960, 960), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=80, optimize=True)
        return base64.b64encode(buf.getvalue()).decode(), img

    try:
        front_raw = await front.read()
        # The back is useful for a deep condition check, not for fast identity.
        back_raw = await back.read() if back and mode == "deep" else None
        front_b64, front_image = await asyncio.to_thread(compress, front_raw)
        back_b64 = (await asyncio.to_thread(compress, back_raw))[0] if back_raw else None
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="התמונה אינה תקינה או גדולה מדי") from exc

    prompt = """Identify this exact Pokemon TCG card. Be fast and literal.
The printed collector number is the primary identifier. Copy the complete number exactly as printed near the bottom, preserving leading zeros, letters and the denominator. Return up to three plausible readings in `numberCandidates`, best first. Never invent an unreadable value. Read the printed set code/symbol and official set name as supporting identifiers.
For every language, return the official English card name in `name`, the original printed name in `printedName`, and a Hebrew transliteration of only the card name in `hebrewName`. Preserve suffixes such as V, VMAX, VSTAR, GX, EX and ex.
Classify `finish` only when visible. Do not estimate a price."""
    if mode == "deep":
        prompt += """
This is an optional deep pass. Also read the release year, rarity, and visible physical condition. Use Near Mint unless visible wear clearly supports another condition."""
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{front_b64}"}}
    ]
    if back_b64:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/jpeg;base64,{back_b64}"}
        })

    properties = {
        "name": {"type": "string"},
        "printedName": {"type": "string"},
        "hebrewName": {"type": "string"},
        "set": {"type": "string"},
        "setCode": {"type": "string"},
        "number": {"type": "string"},
        "numberCandidates": {
            "type": "array", "items": {"type": "string"}, "maxItems": 3
        },
        "language": {
            "type": "string",
            "enum": [
                "English", "Japanese", "Chinese", "Korean", "French",
                "German", "Spanish", "Italian", "Portuguese", "Thai",
                "Indonesian", "Hebrew", "Other"
            ]
        },
        "finish": {
            "type": "string",
            "enum": ["", "normal", "holofoil", "reverse-holofoil", "first-edition"]
        },
    }
    if mode == "deep":
        properties.update({
            "year": {"type": "string"},
            "condition": {
                "type": "string",
                "enum": ["Mint", "Near Mint", "Excellent", "Good", "Poor"]
            },
            "rarity": {
                "type": "string",
                "enum": [
                    "Common", "Uncommon", "Rare", "Holo Rare",
                    "Ultra Rare", "Secret Rare"
                ]
            },
        })
    schema = {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }

    def request_identification():
        try:
            primary_model = os.environ.get(
                "OPENROUTER_MODEL",
                "google/gemma-4-26b-a4b-it:free"
            )
            request_body = {
                "model": primary_model,
                "messages": [{"role": "user", "content": content}],
                "temperature": 0,
                "max_tokens": 440 if mode == "deep" else 280,
                "provider": {
                    "allow_fallbacks": True,
                    "require_parameters": True,
                    "sort": "latency"
                },
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {
                        "name": f"pokemon_card_{mode}",
                        "strict": True,
                        "schema": schema
                    }
                }
            }
            if primary_model != "openrouter/free":
                request_body["models"] = ["openrouter/free"]
            response = requests.post(
                "https://openrouter.ai/api/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {os.environ['OPENROUTER_KEY']}",
                    "Content-Type": "application/json",
                    "HTTP-Referer": "https://pokemon-collection-ecru.vercel.app",
                    "X-Title": "Hitim"
                },
                json=request_body,
                timeout=(6, 32 if mode == "deep" else 24)
            )
            response.raise_for_status()
            result = response.json()
            if result.get("error"):
                raise ValueError(str((result.get("error") or {}).get("message") or "provider error"))
            answer = result["choices"][0]["message"]["content"]
            if not isinstance(answer, str):
                raise ValueError("empty model response")
            answer = re.sub(r"```json|```", "", answer).strip()
            match = re.search(r"\{.*\}", answer, re.DOTALL)
            identified = json.loads(match.group() if match else answer)
            if not isinstance(identified, dict) or not (
                str(identified.get("name") or "").strip()
                or str(identified.get("number") or "").strip()
            ):
                raise ValueError("card was not identified")
            english_name = str(identified.get("name") or "").strip()
            hebrew_name = str(identified.get("hebrewName", "") or "").strip()
            display_name = english_name
            if hebrew_name and hebrew_name not in english_name:
                display_name = f"{english_name} ({hebrew_name})"
            identified["name"] = display_name
            identified["pokemon"] = display_name
            identified.setdefault("year", "")
            identified.setdefault("condition", "")
            identified.setdefault("rarity", "")
            return identified
        except requests.Timeout as exc:
            raise HTTPException(status_code=504, detail="הזיהוי מתעכב כרגע — נסה שוב") from exc
        except requests.RequestException as exc:
            raise HTTPException(status_code=503, detail="שירות הזיהוי אינו זמין כרגע") from exc
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail="לא הצלחנו לקרוא את פרטי הקלף מהתמונה") from exc

    identified = await asyncio.to_thread(request_identification)
    try:
        identified = await asyncio.wait_for(
            asyncio.to_thread(_enrich_identification_from_catalog, identified, front_image),
            timeout=12
        )
    except asyncio.TimeoutError:
        # Catalog verification improves certainty but must not block a valid scan.
        pass
    identified["identityConfidence"] = (
        "catalog" if identified.get("catalogCardId") else "unverified"
    )
    # Pricing is intentionally a separate request. A slow free market source
    # must never turn a successful card identification into a failed scan.
    identified.update({
        "value": "",
        "priceStatus": "pending",
        "identificationMode": mode,
    })
    return identified

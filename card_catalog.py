"""Small, read-only local catalogue used by Hitim's identification flow."""

from __future__ import annotations

import gzip
import os
from pathlib import Path
import re
import shutil
import sqlite3
import threading
import unicodedata
from difflib import SequenceMatcher
from urllib.parse import unquote


LANGUAGE_CODES = {
    "english": "en",
    "japanese": "ja",
    "chinese": "zh-tw",
    "korean": "ko",
    "french": "fr",
    "german": "de",
    "spanish": "es",
    "italian": "it",
    "portuguese": "pt",
    "thai": "th",
    "indonesian": "id",
}

ROOT = Path(__file__).resolve().parent
ARCHIVE_PATH = ROOT / "data" / "card_catalog.sqlite3.gz"
DATABASE_PATH = Path(
    os.environ.get("HITIM_CARD_CATALOG_PATH", "/tmp/hitim-card-catalog.sqlite3")
)
_catalog_lock = threading.Lock()
_catalog_ready = False


def normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", unquote(str(value or ""))).casefold()
    return re.sub(r"[^\w\u3040-\u30ff\u3400-\u9fff\uac00-\ud7af]+", "", text)


def normalize_number(value: object) -> str:
    text = unquote(str(value or "")).split("/", 1)[0].strip().upper()
    if text.isdigit():
        return str(int(text))
    return re.sub(r"[^A-Z0-9!?]", "", text)


def _database_is_current() -> bool:
    if not DATABASE_PATH.exists() or not ARCHIVE_PATH.exists():
        return False
    try:
        with sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True) as connection:
            row = connection.execute(
                "SELECT value FROM metadata WHERE key = 'catalog_version'"
            ).fetchone()
        archive_version = str(int(ARCHIVE_PATH.stat().st_mtime))
        return bool(row and row[0] == archive_version)
    except (OSError, sqlite3.Error, ValueError):
        return False


def ensure_catalog() -> bool:
    """Unpack the versioned catalogue once per container, then open it read-only."""
    global _catalog_ready
    if _catalog_ready:
        return True
    with _catalog_lock:
        if _catalog_ready:
            return True
        if not ARCHIVE_PATH.exists():
            return False
        if not _database_is_current():
            DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
            temporary = DATABASE_PATH.with_suffix(".tmp")
            with gzip.open(ARCHIVE_PATH, "rb") as source, temporary.open("wb") as target:
                shutil.copyfileobj(source, target)
            with sqlite3.connect(temporary) as connection:
                connection.execute(
                    "INSERT OR REPLACE INTO metadata(key, value) VALUES('catalog_version', ?)",
                    (str(int(ARCHIVE_PATH.stat().st_mtime)),),
                )
                connection.commit()
            temporary.replace(DATABASE_PATH)
        _catalog_ready = True
        return True


def _connection() -> sqlite3.Connection:
    if not ensure_catalog():
        raise FileNotFoundError("Hitim card catalogue is not installed")
    connection = sqlite3.connect(f"file:{DATABASE_PATH}?mode=ro", uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    return connection


def catalog_status() -> dict:
    try:
        with _connection() as connection:
            values = dict(connection.execute("SELECT key, value FROM metadata"))
        return {
            "ready": True,
            "cards": int(values.get("card_count", 0)),
            "languages": int(values.get("language_count", 0)),
            "sourceUpdatedAt": values.get("source_updated_at", ""),
        }
    except (FileNotFoundError, OSError, sqlite3.Error, ValueError):
        return {"ready": False, "cards": 0, "languages": 0, "sourceUpdatedAt": ""}


def lookup_card(card_info: dict) -> dict | None:
    """Find one safe local match using language + set + collector number."""
    language = LANGUAGE_CODES.get(str(card_info.get("language") or "").strip().lower())
    if not language:
        return None

    number = normalize_number(card_info.get("number"))
    set_code = normalize_text(card_info.get("setCode"))
    set_name = normalize_text(card_info.get("set"))
    printed_name = normalize_text(card_info.get("printedName"))
    english_name = normalize_text(card_info.get("name") or card_info.get("pokemon"))
    denominator = ""
    number_parts = str(card_info.get("number") or "").split("/", 1)
    if len(number_parts) == 2:
        denominator = normalize_number(number_parts[1])
    if not number and not printed_name and not english_name:
        return None

    conditions = ["c.language = ?"]
    parameters: list[object] = [language]
    if number:
        conditions.append("c.local_id_norm = ?")
        parameters.append(number)
    elif printed_name:
        conditions.append("c.name_norm = ?")
        parameters.append(printed_name)
    else:
        conditions.append("c.name_norm = ?")
        parameters.append(english_name)

    query = f"""
        SELECT c.card_id, c.set_id, c.local_id, c.printed_name, c.image_url,
               s.name AS set_name, s.name_norm AS set_name_norm,
               s.official_count, s.total_count
          FROM cards c
          LEFT JOIN sets s ON s.language = c.language AND s.set_id = c.set_id
         WHERE {' AND '.join(conditions)}
         LIMIT 120
    """
    try:
        with _connection() as connection:
            candidates = list(connection.execute(query, parameters))
            if not candidates:
                return None
            scored = []
            for candidate in candidates:
                candidate_set_code = normalize_text(candidate["set_id"])
                candidate_set_name = str(candidate["set_name_norm"] or "")
                candidate_name = normalize_text(candidate["printed_name"])
                score = 6 if number else 0
                score += 12 if set_code and candidate_set_code == set_code else 0
                score += 5 if denominator and normalize_number(candidate["official_count"]) == denominator else 0
                if set_name and candidate_set_name:
                    score += 5 * SequenceMatcher(None, set_name, candidate_set_name).ratio()
                if printed_name and candidate_name:
                    score += 4 * SequenceMatcher(None, printed_name, candidate_name).ratio()
                scored.append((score, candidate))
            scored.sort(key=lambda item: item[0], reverse=True)
            best_score, best = scored[0]
            second_score = scored[1][0] if len(scored) > 1 else -1

            exact_set = bool(set_code and normalize_text(best["set_id"]) == set_code)
            exact_total = bool(
                denominator and normalize_number(best["official_count"]) == denominator
            )
            safe_match = (
                len(scored) == 1
                or exact_set
                or (exact_total and best_score - second_score >= 1.0)
                or (best_score >= 12 and best_score - second_score >= 1.5)
            )
            if not safe_match:
                return None

            english = connection.execute(
                "SELECT printed_name FROM cards WHERE language = 'en' AND card_id = ? LIMIT 1",
                (best["card_id"],),
            ).fetchone()
    except (FileNotFoundError, OSError, sqlite3.Error):
        return None

    image_url = str(best["image_url"] or "").rstrip("/")
    if image_url and not re.search(r"\.(?:webp|png|jpe?g)$", image_url, re.IGNORECASE):
        image_url += "/high.webp"
    official_count = str(best["official_count"] or "")
    local_id = str(best["local_id"] or "")
    return {
        "catalogCardId": str(best["card_id"] or ""),
        "catalogImage": image_url,
        "setCode": str(best["set_id"] or ""),
        "set": str(best["set_name"] or card_info.get("set") or ""),
        "number": f"{local_id}/{official_count}" if official_count else local_id,
        "printedName": str(best["printed_name"] or card_info.get("printedName") or ""),
        "catalogEnglishName": str(english["printed_name"] if english else ""),
        "catalogMatch": "local",
    }

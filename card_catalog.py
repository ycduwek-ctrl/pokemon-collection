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
LANGUAGE_NAMES = {code: name.title() for name, code in LANGUAGE_CODES.items()}

_OCR_DIGITS = str.maketrans({
    "O": "0", "Q": "0", "D": "0",
    "I": "1", "L": "1", "|": "1",
    "Z": "2", "S": "5", "B": "8",
})
_OCR_NUMBER_PAIR = re.compile(
    r"(?<![A-Z0-9])"
    r"([A-Z]{0,4}[0-9OQDILZSB]{1,4})"
    r"\s*[/\\]\s*"
    r"([A-Z]{0,4}[0-9OQDILZSB]{1,4})"
    r"(?![A-Z0-9])",
    re.IGNORECASE,
)

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


def _repair_ocr_number(value: object) -> str:
    """Repair common OCR confusions without changing real letter prefixes."""
    compact = re.sub(r"[\s-]+", "", str(value or "").upper())
    match = re.fullmatch(r"([A-Z]{0,4})([0-9OQDILZSB]+)", compact)
    if not match:
        return normalize_number(compact)
    prefix, digits = match.groups()
    # Prefixes such as TG, GG and SVP are meaningful. A lone OCR-looking
    # prefix beside digits is much more likely to be a misread digit.
    if prefix and not re.fullmatch(r"(?:TG|GG|SVP|SWSH|SM|XY|BW|DP|HGSS|RC)", prefix):
        digits = prefix + digits
        prefix = ""
    return normalize_number(prefix + digits.translate(_OCR_DIGITS))


def extract_ocr_number_pairs(text: object) -> list[tuple[str, str]]:
    """Extract collector number pairs such as 043/185 or TG01/TG30."""
    source = unicodedata.normalize("NFKC", str(text or "")).upper()
    pairs: list[tuple[str, str]] = []
    for match in _OCR_NUMBER_PAIR.finditer(source):
        numerator = _repair_ocr_number(match.group(1))
        denominator = _repair_ocr_number(match.group(2))
        if numerator and denominator and (numerator, denominator) not in pairs:
            pairs.append((numerator, denominator))
    return pairs[:8]


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


def _image_url(value: object) -> str:
    image_url = str(value or "").rstrip("/")
    if image_url and not re.search(r"\.(?:webp|png|jpe?g)$", image_url, re.IGNORECASE):
        image_url += "/high.webp"
    return image_url


def lookup_ocr_text(text: object) -> dict | None:
    """Resolve OCR text to one unambiguous printing in the local catalogue.

    Collector number + denominator are the primary identity. Set codes and a
    printed name visible in the OCR text break ties between older sets that
    share the same numbering.
    """
    raw_text = unicodedata.normalize("NFKC", str(text or ""))
    pairs = extract_ocr_number_pairs(raw_text)
    if not pairs:
        return None
    normalized_text = normalize_text(raw_text)
    token_text = re.sub(r"[^A-Z0-9.]+", " ", raw_text.upper())
    tokens = {token for token in token_text.split() if len(token) >= 2}

    try:
        with _connection() as connection:
            rows: list[sqlite3.Row] = []
            for numerator, denominator in pairs:
                for language in LANGUAGE_NAMES:
                    rows.extend(connection.execute(
                        """
                        SELECT c.language, c.card_id, c.set_id, c.local_id,
                               c.printed_name, c.image_url,
                               s.name AS set_name, s.official_count
                          FROM cards c
                          JOIN sets s
                            ON s.language = c.language AND s.set_id = c.set_id
                         WHERE c.language = ? AND c.local_id_norm = ?
                         LIMIT 180
                        """,
                        (language, numerator),
                    ))

            scored: list[tuple[float, sqlite3.Row, str, str, bool, bool]] = []
            for row in rows:
                numerator = normalize_number(row["local_id"])
                matching_pairs = [pair for pair in pairs if pair[0] == numerator]
                if not matching_pairs:
                    continue
                official = normalize_number(row["official_count"])
                exact_official = any(pair[1] == official for pair in matching_pairs)
                if not exact_official:
                    continue

                set_id = str(row["set_id"] or "")
                set_token = re.sub(r"[^A-Z0-9.]", "", set_id.upper())
                exact_set = bool(len(set_token) >= 3 and set_token in tokens)
                printed_name = normalize_text(row["printed_name"])
                name_visible = bool(len(printed_name) >= 4 and printed_name in normalized_text)

                score = 24
                score += 30 if exact_set else 0
                score += 18 if name_visible else 0
                scored.append((
                    score, row, normalize_text(set_id), numerator,
                    exact_set, name_visible,
                ))

            if not scored:
                return None

            # Collapse translated printings of the same set/card identity.
            groups: dict[tuple[str, str], list[tuple]] = {}
            for item in scored:
                groups.setdefault((item[2], item[3]), []).append(item)
            ranked_groups = sorted(
                groups.values(), key=lambda group: max(item[0] for item in group), reverse=True
            )
            best_group = ranked_groups[0]
            best_score = max(item[0] for item in best_group)
            second_score = (
                max(item[0] for item in ranked_groups[1]) if len(ranked_groups) > 1 else -1
            )
            has_strong_tiebreaker = any(item[4] or item[5] for item in best_group)
            if len(ranked_groups) > 1 and not (
                has_strong_tiebreaker and best_score - second_score >= 10
            ):
                return None

            # Prefer the language whose printed name was actually read. If OCR
            # only found the number, use English for a stable display name.
            best_group.sort(key=lambda item: (
                item[5], item[4], bool(item[1]["image_url"]),
                item[1]["language"] == "en", item[0]
            ), reverse=True)
            best = best_group[0][1]
            image_row = next(
                (item[1] for item in best_group if str(item[1]["image_url"] or "").strip()),
                best,
            )
            english = connection.execute(
                "SELECT printed_name FROM cards WHERE language = 'en' AND card_id = ? LIMIT 1",
                (best["card_id"],),
            ).fetchone()
    except (FileNotFoundError, OSError, sqlite3.Error):
        return None

    official_count = str(best["official_count"] or "")
    local_id = str(best["local_id"] or "")
    english_name = str(english["printed_name"] if english else "").strip()
    printed_name = str(best["printed_name"] or "").strip()
    display_name = english_name or printed_name
    return {
        "name": display_name,
        "pokemon": display_name,
        "printedName": printed_name,
        "set": str(best["set_name"] or ""),
        "setCode": str(best["set_id"] or ""),
        "number": f"{local_id}/{official_count}" if official_count else local_id,
        "language": LANGUAGE_NAMES.get(str(best["language"]), "Other"),
        "finish": "",
        "year": "",
        "condition": "",
        "rarity": "",
        "catalogCardId": str(best["card_id"] or ""),
        "catalogImage": _image_url(image_row["image_url"]),
        "catalogMatch": "local-ocr",
        "identityConfidence": "catalog",
    }

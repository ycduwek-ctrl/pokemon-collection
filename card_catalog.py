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
_OCR_SEVEN_PAIR = re.compile(
    r"(?<![A-Z0-9])"
    r"([0-9OQDILZSB]{1,4})"
    r"7"
    r"([0-9OQDILZSB]{2,4})"
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
_name_index_lock = threading.Lock()
_name_index_cache: tuple[str, list[tuple[str, str, str, int]], dict[str, set[int]]] | None = None


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

    def add_pair(numerator: str, denominator: str) -> None:
        known_number = re.compile(r"^(?:(?:TG|GG|SVP|SWSH|SM|XY|BW|DP|HGSS|RC)?\d+)$")
        numerator_known = bool(known_number.fullmatch(numerator))
        denominator_known = bool(known_number.fullmatch(denominator))
        # Keep a long denominator even if the numerator acquired one stray
        # letter (for example G2005/782). Reject short artwork fragments such
        # as EE0/2, which otherwise look like collector numbers.
        recoverable_denominator = bool(denominator.isdigit() and len(denominator) >= 2)
        if not denominator_known or not (numerator_known or recoverable_denominator):
            return
        if numerator and denominator and (numerator, denominator) not in pairs:
            pairs.append((numerator, denominator))

    for match in _OCR_NUMBER_PAIR.finditer(source):
        numerator = _repair_ocr_number(match.group(1))
        denominator = _repair_ocr_number(match.group(2))
        add_pair(numerator, denominator)
    # Tesseract often reads the tiny slash on modern cards as a 7, for
    # example 025/167 becomes 0257167. Catalogue validation later prevents a
    # random seven-digit token from being accepted as a card number.
    for match in _OCR_SEVEN_PAIR.finditer(source):
        numerator = _repair_ocr_number(match.group(1))
        denominator = _repair_ocr_number(match.group(2))
        add_pair(numerator, denominator)
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


def _trigrams(value: str) -> set[str]:
    if len(value) < 3:
        return {value} if value else set()
    return {value[index:index + 3] for index in range(len(value) - 2)}


def _catalog_name_index() -> tuple[list[tuple[str, str, str, int]], dict[str, set[int]]]:
    """Return a small cached name index for fuzzy OCR title matching."""
    global _name_index_cache
    cache_key = str(DATABASE_PATH)
    if _name_index_cache and _name_index_cache[0] == cache_key:
        return _name_index_cache[1], _name_index_cache[2]
    with _name_index_lock:
        if _name_index_cache and _name_index_cache[0] == cache_key:
            return _name_index_cache[1], _name_index_cache[2]
        with _connection() as connection:
            names = [
                (str(row["language"]), str(row["name_norm"]),
                 str(row["printed_name"]), int(row["printing_count"]))
                for row in connection.execute(
                    """
                    SELECT language, name_norm, MIN(printed_name) AS printed_name,
                           COUNT(*) AS printing_count
                      FROM cards
                     WHERE language = 'en' AND LENGTH(name_norm) >= 4
                     GROUP BY language, name_norm
                    """
                )
            ]
        trigram_index: dict[str, set[int]] = {}
        for index, (_language, name_norm, _printed_name, _count) in enumerate(names):
            for trigram in _trigrams(name_norm):
                trigram_index.setdefault(trigram, set()).add(index)
        _name_index_cache = (cache_key, names, trigram_index)
        return names, trigram_index


def _ocr_title_fragments(raw_text: str) -> list[tuple[str, int]]:
    """Create short title-like fragments from the first OCR lines."""
    fragments: dict[str, int] = {}
    nonempty_lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
    for line_index, line in enumerate(nonempty_lines[:18]):
        line_words = re.sub(r"[^A-Z]+", " ", line.upper())
        if "EVOLVES FROM" in line_words or "PUT " in line_words and " STAGE " in f" {line_words} ":
            continue
        words = re.findall(r"[A-Za-z][A-Za-z'-]*", line)
        for start in range(len(words)):
            for size in range(1, min(5, len(words) - start) + 1):
                fragment = normalize_text(" ".join(words[start:start + size]))
                if 4 <= len(fragment) <= 30:
                    fragments[fragment] = min(line_index, fragments.get(fragment, line_index))
    return list(fragments.items())


def _find_ocr_names(raw_text: str) -> list[tuple[str, str, str, int, float]]:
    """Find likely printed card names, including small OCR misspellings."""
    names, trigram_index = _catalog_name_index()
    fragments = _ocr_title_fragments(raw_text)
    scored: dict[int, float] = {}

    # Fuzzy matching is limited to the title-heavy first lines so an attack,
    # illustrator or rules paragraph is not mistaken for the card's name.
    for fragment, line_index in fragments:
        possible: set[int] = set()
        for trigram in _trigrams(fragment):
            possible.update(trigram_index.get(trigram, ()))
        for index in possible:
            name_norm = names[index][1]
            length_gap = abs(len(name_norm) - len(fragment))
            if length_gap > max(5, round(len(name_norm) * .45)):
                continue
            ratio = SequenceMatcher(None, name_norm, fragment).ratio()
            if ratio < .78:
                continue
            adjusted = ratio - min(line_index, 12) * .004
            scored[index] = max(scored.get(index, 0), adjusted)

    normalized_text = normalize_text(raw_text)
    decorated_suffix = ""
    if "gxrule" in normalized_text:
        decorated_suffix = "gx"
    elif "exrule" in normalized_text or "cxrule" in normalized_text:
        decorated_suffix = "ex"
    elif "vrule" in normalized_text:
        decorated_suffix = "v"
    if decorated_suffix:
        for index, score in list(scored.items()):
            if names[index][1].endswith(decorated_suffix):
                scored[index] = min(1.0, score + .12)
            else:
                scored[index] = max(0, score - .12)

    ranked = sorted(scored.items(), key=lambda item: item[1], reverse=True)
    if not ranked:
        return []
    best_score = ranked[0][1]
    return [
        (*names[index], score)
        for index, score in ranked
        if score >= max(.78, best_score - .10)
    ][:6]


def _number_similarity(expected: object, observed: object) -> float:
    expected_text = re.sub(r"[^A-Z0-9]", "", str(expected or "").upper())
    observed_text = re.sub(r"[^A-Z0-9]", "", str(observed or "").upper())
    if not expected_text or not observed_text:
        return 0
    observed_letters = set(re.sub(r"[^A-Z]", "", observed_text))
    if expected_text.isdigit() and observed_letters.difference(set("OQDILZSB")):
        return 0
    expected_digits = re.sub(r"\D", "", expected_text).lstrip("0") or "0"
    observed_digits = re.sub(r"\D", "", observed_text).lstrip("0") or "0"
    if expected_text == observed_text or expected_digits == observed_digits:
        return 1.0
    ratio = SequenceMatcher(None, expected_digits, observed_digits).ratio()
    shorter, longer = sorted((expected_digits, observed_digits), key=len)
    if len(longer) - len(shorter) == 1 and shorter in longer:
        ratio = max(ratio, .90)
    elif len(longer) - len(shorter) <= 2 and longer.endswith(shorter):
        ratio = max(ratio, .82)
    if len(expected_digits) == len(observed_digits) and len(expected_digits) >= 2:
        differences = sum(a != b for a, b in zip(expected_digits, observed_digits))
        if differences == 1:
            ratio = max(ratio, .86)
    return ratio


def _set_recency_key(set_id: object) -> tuple[int, int, str]:
    """Stable tie-breaker which keeps the newest set codes first."""
    value = str(set_id or "").casefold()
    series_order = {"sv": 6, "swsh": 5, "sm": 4, "xy": 3, "bw": 2, "dp": 1}
    match = re.match(r"([a-z]+)(\d+)", value)
    if not match:
        return (0, 0, value)
    return (series_order.get(match.group(1), 0), int(match.group(2)), value)


def _estimated_set_year(set_id: object) -> int:
    """Estimate modern set year only as a candidate-ordering hint."""
    value = str(set_id or "").casefold()
    match = re.match(r"(sv|swsh|sm|xy|bw)(\d+)(\.\d+)?", value)
    if not match:
        return 0
    first_year = {"sv": 2023, "swsh": 2020, "sm": 2017, "xy": 2014, "bw": 2011}
    release_slot = int(match.group(2)) + (1 if match.group(3) else 0)
    return first_year[match.group(1)] + max(0, (release_slot - 1) // 4)


def _ocr_candidate_payload(
    connection: sqlite3.Connection,
    row: sqlite3.Row,
    image_row: sqlite3.Row,
    score: float,
) -> dict:
    english = connection.execute(
        "SELECT printed_name FROM cards WHERE language = 'en' AND card_id = ? LIMIT 1",
        (row["card_id"],),
    ).fetchone()
    official_count = str(row["official_count"] or "")
    local_id = str(row["local_id"] or "")
    english_name = str(english["printed_name"] if english else "").strip()
    printed_name = str(row["printed_name"] or "").strip()
    display_name = english_name or printed_name
    language = LANGUAGE_NAMES.get(str(row["language"]), "Other")
    return {
        "name": display_name,
        "pokemon": display_name,
        "printedName": printed_name,
        "set": str(row["set_name"] or ""),
        "setCode": str(row["set_id"] or ""),
        "number": f"{local_id}/{official_count}" if official_count else local_id,
        "language": language,
        "finish": "",
        "year": "",
        "condition": "",
        "rarity": "",
        "catalogCardId": str(row["card_id"] or ""),
        "catalogImage": _image_url(image_row["image_url"]),
        "catalogMatch": "local-ocr",
        "identityConfidence": "catalog",
        # The browser uses this flag to avoid presenting Japanese/Chinese text
        # as the user-facing name. A requested vision pass can then translate
        # the exact photographed printing without changing its catalog ID.
        "needsEnglishName": bool(language != "English" and not english_name),
        "candidateScore": round(score, 2),
    }


def _rank_ocr_candidates(text: object, limit: int = 80) -> list[dict]:
    raw_text = unicodedata.normalize("NFKC", str(text or ""))
    pairs = extract_ocr_number_pairs(raw_text)
    name_matches = _find_ocr_names(raw_text)
    if not pairs and not name_matches:
        return []

    token_text = re.sub(r"[^A-Z0-9.]+", " ", raw_text.upper())
    tokens = {token for token in token_text.split() if len(token) >= 2}
    printed_years = {
        int(value) for value in re.findall(r"(?<!\d)((?:19|20)\d{2})(?!\d)", raw_text)
        if 1996 <= int(value) <= 2100
    }
    name_scores = {
        (language, name_norm): (score, printing_count)
        for language, name_norm, _printed_name, printing_count, score in name_matches
    }
    rows: dict[tuple[str, str], sqlite3.Row] = {}

    try:
        with _connection() as connection:
            for numerator, _denominator in pairs:
                # Exact-number queries use the catalogue indexes. Near-number
                # recovery is supplied by the much smaller name result set.
                for language in LANGUAGE_NAMES:
                    for row in connection.execute(
                        """
                        SELECT c.language, c.card_id, c.set_id, c.local_id,
                               c.printed_name, c.name_norm, c.image_url,
                               s.name AS set_name, s.official_count
                          FROM cards c
                          JOIN sets s
                            ON s.language = c.language AND s.set_id = c.set_id
                         WHERE c.language = ? AND c.local_id_norm = ?
                         LIMIT 220
                        """,
                        (language, numerator),
                    ):
                        rows[(str(row["language"]), str(row["card_id"]))] = row

            for language, name_norm, _printed_name, _count, _score in name_matches:
                for row in connection.execute(
                    """
                    SELECT c.language, c.card_id, c.set_id, c.local_id,
                           c.printed_name, c.name_norm, c.image_url,
                           s.name AS set_name, s.official_count
                      FROM cards c
                      JOIN sets s
                        ON s.language = c.language AND s.set_id = c.set_id
                     WHERE c.language = ? AND c.name_norm = ?
                     LIMIT 220
                    """,
                    (language, name_norm),
                ):
                    rows[(str(row["language"]), str(row["card_id"]))] = row

            scored_rows: list[dict] = []
            strongest_read_name = max(
                (score for _language, _name, _printed, _count, score in name_matches),
                default=0,
            )
            for row in rows.values():
                name_score, printing_count = name_scores.get(
                    (str(row["language"]), str(row["name_norm"])), (0.0, 0)
                )
                set_token = re.sub(r"[^A-Z0-9.]", "", str(row["set_id"] or "").upper())
                exact_set = bool(len(set_token) >= 3 and set_token in tokens)
                exact_pair = False
                number_quality = 0.0
                best_numerator_similarity = 0.0
                best_denominator_similarity = 0.0
                for numerator, denominator in pairs:
                    numerator_similarity = _number_similarity(row["local_id"], numerator)
                    denominator_similarity = _number_similarity(row["official_count"], denominator)
                    quality = .45 * numerator_similarity + .55 * denominator_similarity
                    if quality > number_quality:
                        number_quality = quality
                        best_numerator_similarity = numerator_similarity
                        best_denominator_similarity = denominator_similarity
                    if numerator_similarity == 1 and denominator_similarity == 1:
                        exact_pair = True

                score = 48 * name_score
                if exact_pair:
                    # A clean pair is primary evidence, but OCR sometimes
                    # invents a slash inside artwork. When a strong title was
                    # also read, an unrelated pair must not outrank it.
                    score += 80 if name_score or exact_set or not name_matches else 42
                elif best_numerator_similarity >= .45 and best_denominator_similarity >= .45:
                    score += 38 * number_quality
                elif name_score >= .80 and best_denominator_similarity >= .82:
                    score += 22 * best_denominator_similarity
                elif name_score >= .80 and best_numerator_similarity >= .82:
                    score += 18 * best_numerator_similarity
                if not exact_pair:
                    score += 12 if best_numerator_similarity == 1 else 0
                    score += 12 if best_denominator_similarity == 1 else 0
                if exact_set:
                    score += 60
                estimated_year = _estimated_set_year(row["set_id"])
                if printed_years and estimated_year:
                    score += 14 if estimated_year in printed_years else -4
                # Once the title is read confidently, an unrelated card that
                # happens to share an OCR-like number should not appear next to
                # the real card. This prevents noisy artwork numbers from
                # creating Toxel/Toxtricity-style false options.
                if strongest_read_name >= .82 and name_score < strongest_read_name - .12:
                    if not exact_set:
                        score -= 34
                if score < 24:
                    continue
                scored_rows.append({
                    "row": row,
                    "score": score,
                    "name_score": name_score,
                    "printing_count": printing_count,
                    "exact_pair": exact_pair,
                    "exact_set": exact_set,
                    "number_quality": number_quality,
                    "numerator_similarity": best_numerator_similarity,
                    "denominator_similarity": best_denominator_similarity,
                })

            # Collapse translated records that represent the same set/number.
            groups: dict[tuple[str, str], list[dict]] = {}
            for item in scored_rows:
                row = item["row"]
                key = (normalize_text(row["set_id"]), normalize_number(row["local_id"]))
                groups.setdefault(key, []).append(item)

            ranked: list[dict] = []
            for group in groups.values():
                group.sort(key=lambda item: (
                    item["score"], item["name_score"], item["exact_set"],
                    item["row"]["language"] == "en", bool(item["row"]["image_url"]),
                ), reverse=True)
                best = group[0]
                image_item = next((item for item in group if (
                    item["row"]["language"] == best["row"]["language"]
                    and str(item["row"]["image_url"] or "").strip()
                )), None)
                if image_item is None:
                    image_item = next((item for item in group if (
                        item["row"]["language"] == "en"
                        and str(item["row"]["image_url"] or "").strip()
                    )), None)
                if image_item is None:
                    image_item = next((item for item in group if str(
                        item["row"]["image_url"] or ""
                    ).strip()), best)
                best["payload"] = _ocr_candidate_payload(
                    connection, best["row"], image_item["row"], best["score"]
                )
                # A visual choice without a catalogue image is not useful and
                # previously produced text-only options on phones.
                if best["payload"]["catalogImage"]:
                    ranked.append(best)

            ranked.sort(key=lambda item: (
                item["score"], item["exact_pair"], item["name_score"],
                item["row"]["language"] == "en",
                _set_recency_key(item["row"]["set_id"]),
            ), reverse=True)
            if not ranked:
                return []
            best_score = ranked[0]["score"]
            return [
                item for item in ranked
                if item["exact_pair"] or item["score"] >= best_score - 24
            ][:max(1, min(int(limit), 80))]
    except (FileNotFoundError, OSError, sqlite3.Error):
        return []


def lookup_ocr_result(text: object, limit: int = 6, offset: int = 0) -> dict:
    """Return either a safe match or visual candidates for one-tap confirmation."""
    page_limit = max(1, min(int(limit), 12))
    page_offset = max(0, min(int(offset), 72))
    ranked = _rank_ocr_candidates(text, limit=80)
    if not ranked:
        return {
            "match": None, "candidates": [], "candidateTotal": 0,
            "candidateOffset": page_offset, "hasMoreCandidates": False,
        }

    best = ranked[0]
    second_score = ranked[1]["score"] if len(ranked) > 1 else -1
    score_margin = best["score"] - second_score
    exact_pair_groups = sum(bool(item["exact_pair"]) for item in ranked)
    strongest_name_score = max(item["name_score"] for item in ranked)
    safe_exact_pair = bool(
        best["exact_pair"]
        and (best["name_score"] >= .78 or best["exact_set"] or strongest_name_score < .78)
        and (
            exact_pair_groups == 1
            or ((best["name_score"] >= .82 or best["exact_set"]) and score_margin >= 10)
        )
    )
    safe_repaired_pair = bool(
        best["name_score"] >= .82
        and best["number_quality"] >= .78
        and (best["numerator_similarity"] >= .78 or best["denominator_similarity"] >= .85)
        and score_margin >= 8
    )
    safe_unique_name = bool(
        best["name_score"] >= .92 and best["printing_count"] == 1
    )
    if safe_exact_pair or safe_repaired_pair or safe_unique_name:
        payload = dict(best["payload"])
        payload.pop("candidateScore", None)
        return {
            "match": payload, "candidates": [], "candidateTotal": 0,
            "candidateOffset": 0, "hasMoreCandidates": False,
        }
    candidate_total = len(ranked)
    page = ranked[page_offset:page_offset + page_limit]
    return {
        "match": None,
        "candidates": [dict(item["payload"]) for item in page],
        "candidateTotal": candidate_total,
        "candidateOffset": page_offset,
        "hasMoreCandidates": page_offset + len(page) < candidate_total,
    }


def lookup_ocr_text(text: object) -> dict | None:
    """Resolve OCR text to one safe printing, never a low-confidence guess."""
    return lookup_ocr_result(text)["match"]


def lookup_ocr_candidates(text: object, limit: int = 6) -> list[dict]:
    """Return ranked catalogue choices when OCR cannot safely choose one."""
    return lookup_ocr_result(text, limit=limit)["candidates"]

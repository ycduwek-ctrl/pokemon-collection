#!/usr/bin/env python3
"""Build Hitim's compact multilingual TCGdex index."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from card_catalog import normalize_number, normalize_text  # noqa: E402


DEFAULT_LANGUAGES = "en,fr,es,de,it,pt,ja,zh-tw,id,th,ko"
API = "https://api.tcgdex.net/v2"


def fetch_json(url: str):
    for attempt in range(4):
        try:
            request = Request(url, headers={"User-Agent": "Hitim-Catalog-Builder/1.0"})
            with urlopen(request, timeout=60) as response:
                return json.load(response)
        except (HTTPError, URLError, TimeoutError, ValueError):
            if attempt == 3:
                raise
            time.sleep(1.5 * (attempt + 1))


def fetch_language(language: str) -> tuple[str, list, list]:
    cards = fetch_json(f"{API}/{language}/cards")
    sets = fetch_json(f"{API}/{language}/sets")
    if not isinstance(cards, list) or not isinstance(sets, list):
        raise RuntimeError(f"Unexpected TCGdex response for {language}")
    return language, cards, sets


def build_database(database_path: Path, languages: list[str]) -> dict:
    if database_path.exists():
        database_path.unlink()
    connection = sqlite3.connect(database_path)
    connection.executescript("""
        PRAGMA journal_mode = OFF;
        PRAGMA synchronous = OFF;
        PRAGMA temp_store = MEMORY;
        CREATE TABLE cards (
            language TEXT NOT NULL,
            card_id TEXT NOT NULL,
            set_id TEXT NOT NULL,
            local_id TEXT NOT NULL,
            local_id_norm TEXT NOT NULL,
            printed_name TEXT NOT NULL,
            name_norm TEXT NOT NULL,
            image_url TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (language, card_id)
        ) WITHOUT ROWID;
        CREATE TABLE sets (
            language TEXT NOT NULL,
            set_id TEXT NOT NULL,
            name TEXT NOT NULL,
            name_norm TEXT NOT NULL,
            official_count TEXT NOT NULL DEFAULT '',
            total_count TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (language, set_id)
        ) WITHOUT ROWID;
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
    """)

    fetched: dict[str, tuple[list, list]] = {}
    with ThreadPoolExecutor(max_workers=min(6, len(languages))) as executor:
        futures = {executor.submit(fetch_language, language): language for language in languages}
        for future in as_completed(futures):
            language, cards, sets = future.result()
            fetched[language] = (cards, sets)
            print(f"{language}: {len(cards):,} cards, {len(sets):,} sets")

    card_count = 0
    for language in languages:
        cards, sets = fetched[language]
        set_rows = []
        for item in sets:
            counts = item.get("cardCount") or {}
            set_rows.append((
                language,
                str(item.get("id") or ""),
                str(item.get("name") or ""),
                normalize_text(item.get("name")),
                str(counts.get("official") or ""),
                str(counts.get("total") or ""),
            ))
        connection.executemany(
            "INSERT OR REPLACE INTO sets VALUES (?, ?, ?, ?, ?, ?)", set_rows
        )

        card_rows = []
        for item in cards:
            card_id = unquote(str(item.get("id") or ""))
            local_id = unquote(str(item.get("localId") or ""))
            set_id = card_id.rsplit("-", 1)[0] if "-" in card_id else ""
            printed_name = unicodedata.normalize("NFKC", str(item.get("name") or ""))
            if not card_id or not local_id or not printed_name:
                continue
            card_rows.append((
                language, card_id, set_id, local_id, normalize_number(local_id),
                printed_name, normalize_text(printed_name), str(item.get("image") or ""),
            ))
        connection.executemany(
            "INSERT OR REPLACE INTO cards VALUES (?, ?, ?, ?, ?, ?, ?, ?)", card_rows
        )
        card_count += len(card_rows)

    built_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    metadata = {
        "source": "TCGdex cards-database (MIT)",
        "source_updated_at": built_at,
        "card_count": str(card_count),
        "language_count": str(len(languages)),
        "languages": ",".join(languages),
        "schema_version": "1",
        "catalog_version": "build",
    }
    connection.executemany(
        "INSERT INTO metadata(key, value) VALUES(?, ?)", metadata.items()
    )
    connection.executescript("""
        CREATE INDEX cards_number_idx ON cards(language, local_id_norm);
        CREATE INDEX cards_set_number_idx ON cards(language, set_id, local_id_norm);
        CREATE INDEX cards_name_idx ON cards(language, name_norm);
        ANALYZE;
        VACUUM;
    """)
    connection.close()
    return {"cards": card_count, "languages": languages, "builtAt": built_at}


def gzip_database(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as input_file, destination.open("wb") as raw_output:
        with gzip.GzipFile(filename="card_catalog.sqlite3", mode="wb", fileobj=raw_output, mtime=0, compresslevel=9) as output:
            while chunk := input_file.read(1024 * 1024):
                output.write(chunk)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--languages", default=DEFAULT_LANGUAGES)
    parser.add_argument("--output", default=str(ROOT / "data" / "card_catalog.sqlite3.gz"))
    args = parser.parse_args()
    languages = [item.strip() for item in args.languages.split(",") if item.strip()]
    output = Path(args.output).resolve()
    with tempfile.TemporaryDirectory(prefix="hitim-catalog-") as temporary:
        database = Path(temporary) / "card_catalog.sqlite3"
        stats = build_database(database, languages)
        gzip_database(database, output)
        stats.update({
            "databaseBytes": database.stat().st_size,
            "archiveBytes": output.stat().st_size,
            "sha256": hashlib.sha256(output.read_bytes()).hexdigest(),
        })
    metadata_path = output.with_suffix(".json")
    metadata_path.write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n")
    print(json.dumps(stats, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""Build printing-specific image/rarity metadata from PokemonTCG's public data.

Inputs: --sets-json sets/en.json --cards-dir cards/en from
https://github.com/PokemonTCG/pokemon-tcg-data (MIT, images retain their owners).
Only exact set-name, normalized card-number AND card-name matches are accepted.
The original multilingual SQLite archive is never modified.
"""
import argparse
import gzip
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import card_catalog as catalog


def build(sets, directory):
    output = {'source': 'https://github.com/PokemonTCG/pokemon-tcg-data',
              'retrieved': '2026-09-16', 'sets': {}, 'cards': {}}
    with catalog._connection() as db:
        local = [dict(r) for r in db.execute("SELECT * FROM sets WHERE language='en'")]
        for source in sets:
            matched = [s for s in local if catalog.normalize_text(s['name']) == catalog.normalize_text(source['name'])]
            if len(matched) != 1:
                continue
            target = matched[0]['set_id']
            path = directory / (source['id'] + '.json')
            if not path.exists():
                continue
            rows = list(db.execute("SELECT * FROM cards WHERE language='en' AND set_id=?", (target,)))
            output['sets']['en|' + target] = {'logo': source.get('images', {}).get('logo', ''),
                                             'releaseDate': source.get('releaseDate', '')}
            for card in json.loads(path.read_text()):
                matches = [r for r in rows if r['local_id_norm'] == catalog.normalize_number(card['number'])
                           and r['name_norm'] == catalog.normalize_text(card['name'])]
                if len(matches) != 1:
                    continue
                images = card.get('images', {})
                output['cards']['en|' + matches[0]['card_id']] = {
                    'image': images.get('large', ''), 'thumbnail': images.get('small', ''),
                    'rarity': card.get('rarity', ''), 'sourceId': card['id']}
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--sets-json', type=Path, required=True)
    parser.add_argument('--cards-dir', type=Path, required=True)
    args = parser.parse_args()
    result = build(json.loads(args.sets_json.read_text()), args.cards_dir)
    path = ROOT / 'data' / 'catalog_enrichment.json.gz'
    path.write_bytes(gzip.compress(json.dumps(result, ensure_ascii=False, separators=(',', ':')).encode(), mtime=0))
    print(f"{len(result['cards'])} exact printings in {len(result['sets'])} sets")

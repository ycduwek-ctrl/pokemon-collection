"""Build exact-printing enrichment from cached public set galleries.

Usage: python scripts/build_asian_enrichment.py --pages DIR --smh FILE
       --gem FILE --gem-names FILE
Sources are recorded per set/card; no artwork is transferred across languages.
"""
import argparse
import gzip
import html
import json
from pathlib import Path
import re
import sqlite3
import tempfile

ROOT = Path(__file__).resolve().parents[1]

def build(pages, smh, gem, gem_names):
    with tempfile.NamedTemporaryFile() as f:
        f.write(gzip.decompress((ROOT/'data/card_catalog.sqlite3.gz').read_bytes())); f.flush()
        db = sqlite3.connect(f.name)
        rows = db.execute("SELECT card_id,set_id,local_id FROM cards WHERE language='ja'").fetchall()
    by_number = {(s.casefold(), str(int(n)) if n.isdigit() else n): cid for cid,s,n in rows}
    cards, sets, supplements = {}, {}, []
    for page in sorted(Path(pages).glob('*.html')):
        sid = page.stem; text = page.read_text()
        title = re.search(r'<title>(.*?) – Limitless</title>', text)
        sets['ja|'+sid] = {'displayName': html.unescape(title[1]) if title else sid, 'source': 'https://limitlesstcg.com/cards/jp/'+sid}
        for number, url in re.findall(r'<a href="/cards/jp/[^/]+/([^"/]+)"><img[^>]*class="card shadow" src="([^"]+)"', text):
            cid = by_number.get((sid.casefold(), number))
            if cid:
                cards['ja|'+cid] = {'image': html.unescape(url), 'source': sets['ja|'+sid]['source']}
    text = Path(smh).read_text()
    for block in re.findall(r'<tr data-hover=.*?</tr>', text, re.S):
        links = re.findall(r'<a href="/cards/jp/SMH/(\d+)">(.*?)</a>', block, re.S)
        if not links: continue
        number = links[0][0]; name = html.unescape(re.sub('<[^>]+>', '', links[1][1])).strip()
        image = html.unescape(re.search(r'data-hover="([^"]+)"', block)[1]).replace('_XS.png','_SM.png')
        supplements.append(dict(language='ja',cardId='SMH-'+number.zfill(3),setCode='SMH',setName='GX Starter Decks — GXスタートデッキ',localId=number.zfill(3),officialCount='131',printedName=name,englishName='',image=image,source='https://limitlesstcg.com/cards/jp/SMH'))
    names = {n: (en.strip(),cn.strip(),rarity.strip()) for n,en,cn,rarity in re.findall(r'CBB1C-(\d{4}) \| ([^|\n]+) \| ([^|\n]+) \| ([^\n]+)',Path(gem_names).read_text())}
    urls = set(re.findall(r'https://simplifiedcollector.com/wp-content/uploads/[^"\s<>]+?/\d{2}-\d{2}-\d{2}\.(?:webp|png|jpg)',Path(gem).read_text()))
    assert len(urls)==115, 'Incomplete Gem Pack gallery'
    for url in sorted(urls):
        group, variant, denominator = re.search(r'/(\d{2})-(\d{2})-(\d{2})\.',url).groups()
        # Source filenames contain a typo for the first Energy card.
        if (group,variant,denominator)==('17','01','06'): group='18'
        en, cn, rarity = names[group+variant]
        cid='CBB1C-'+group+variant
        supplements.append(dict(language='zh-cn',cardId=cid,setCode='CBB1C',setName='Gem Pack Vol. 1 — 宝石包 第一弹',localId=group+' '+variant,officialCount='',printedName=cn,englishName=en,image=url,source='https://simplifiedcollector.com/gem-pack-vol-1-card-list/'))
        cards['zh-cn|'+cid]={'number':group+' '+variant+'/'+denominator,'rarity': {'●':'Common','◆':'Uncommon','★':'Rare','★★':'Double Rare','★★★':'Ultra Rare'}.get(rarity,'Rare'), 'source':'https://www.pokeari.com/post/pokemon-tcg-gem-pack-vol-1-cbb1c-card-list'}
    assert len(supplements)==246
    return dict(cards=cards,sets=sets,supplements=supplements)

if __name__=='__main__':
    p=argparse.ArgumentParser()
    for arg in ('pages','smh','gem','gem-names'):p.add_argument('--'+arg,required=True)
    a=p.parse_args(); data=build(a.pages,a.smh,a.gem,a.gem_names)
    (ROOT/'data/catalog_asian_enrichment.json.gz').write_bytes(gzip.compress(json.dumps(data,ensure_ascii=False,separators=(',',':')).encode(),mtime=0))
    print({k:len(v) for k,v in data.items()})

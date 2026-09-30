"""Live USD ungraded estimates for explicitly verified supplemental printings.

Do not guess a URL from a name: each mapping must verify language, full card
number, set and the marketplace product identity. No prices are bundled.
"""
from decimal import Decimal, InvalidOperation
import re
import requests

PRINTINGS = {
    ('zh-cn', 'CBB1C-0703'): {
        'path': 'pokemon-chinese-gem-pack/captain-pikachu-stars-703',
        'title': 'Captain Pikachu [Stars] #703', 'product_id': '8710183',
        'number': '0703/09', 'variant': 'Stars · Ungraded',
    },
    ('ja', 'SMH-013'): {
        'path': 'pokemon-japanese-gx-starter-decks/charizard-gx-13',
        'title': 'Charizard GX #13', 'product_id': '7876493',
        'number': '013/131', 'variant': 'Normal · Ungraded',
    },
}


def parse_price(page, printing):
    url = 'https://www.pricecharting.com/game/' + printing['path']
    canonical = re.search(r'<link\b[^>]*rel="canonical"[^>]*href="([^"]+)"', page)
    heading = re.search(r'<h1\b[^>]*id="product_name"[^>]*title="(\d+)"[^>]*>(.*?)</h1>', page, re.S)
    currency = re.search(r'id="dropdown_selected_currency"[^>]*>\s*USD\s*<', page)
    if not canonical or canonical[1] != url or not heading or heading[1] != printing['product_id'] or not currency:
        return None
    if printing['title'] not in re.sub(r'\s+', ' ', re.sub(r'<[^>]+>', ' ', heading[2])):
        return None
    cell = re.search(r'<td\b[^>]*id="used_price"[^>]*>(.*?)</td>', page, re.S)
    if not cell:
        return None
    # Only the first price in the ungraded cell, never a change/sale/graded value.
    amount = re.search(r'<span\b[^>]*class="price js-price"[^>]*>\s*\$([\d,]+\.\d{2})\s*</span>', cell[1])
    if not amount:
        return None
    try:
        value = Decimal(amount[1].replace(',', ''))
        return format(value, '.2f') if value.is_finite() and value > 0 else None
    except InvalidOperation:
        return None


def supplemental_price(supplement, card_info):
    printing = PRINTINGS.get((supplement['language'], supplement['cardId']))
    if not printing:
        return {'value': '', 'priceStatus': 'price-unavailable'}
    number = re.sub(r'\s+', '', str(card_info.get('number') or ''))
    if number and number != printing['number']:
        return {'value': '', 'priceStatus': 'variant-ambiguous'}
    url = 'https://www.pricecharting.com/game/' + printing['path']
    try:
        response = requests.get(url, timeout=(10, 15), allow_redirects=False,
                                headers={"User-Agent": "Hitim/1.0 (card price lookup)", "Accept": "text/html"})
        if response.status_code != 200:
            return {'value': '', 'priceStatus': 'service-unavailable'}
        value = parse_price(response.text, printing)
        if value is None:
            return {'value': '', 'priceStatus': 'price-unavailable'}
        return {'value': value, 'priceStatus': 'matched', 'priceSource': 'PriceCharting',
                'priceVariant': printing['variant'], 'marketCardId': printing['product_id'],
                'priceUrl': url}
    except requests.RequestException:
        return {'value': '', 'priceStatus': 'service-unavailable'}

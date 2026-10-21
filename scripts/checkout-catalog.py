"""Generate the checkout subset from the monitor's *same* offline catalog.

No networking, no credentials, no rewritten product translations.
"""
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
data = root / 'crates/apw-core/data'
stores = json.loads((data / 'stores.json').read_text())
result = {}
for r in json.loads((root / 'src/lib/checkout-regions.json').read_text()):
    locale = r['locale']
    products = {}
    for family in json.loads((data / f'products_{locale}.json').read_text()):
        if family['category'] != 'iphone':
            continue
        for p in family['data'].get('products', []):
            part = p.get('partNumber')
            if not part:
                continue
            dimensions = {k: str(v) for k, v in p.items() if k.startswith('dimension') or k == 'familyType'}
            item = dict(category=family['category'], family=family['family'], dimensions=dimensions)
            if part in products and products[part] != item:
                raise ValueError(f'{locale} has ambiguous part mapping')
            products[part] = item
    local_stores = {}
    for entry in stores:
        if entry['locale'] != locale:
            continue
        for state in entry.get('state', [{'store': entry.get('store', [])}]):
            for s in state['store']:
                local_stores[s['id']] = dict(name=s['name'], address=s['address'])
    if not products or not local_stores:
        raise ValueError(f'Missing region catalog: {locale}')
    result[locale] = dict(products=products, stores=local_stores)
destination = root / 'src/lib/checkout-catalog.json'
destination.write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n')
print({locale: (len(c['products']), len(c['stores'])) for locale, c in result.items()})

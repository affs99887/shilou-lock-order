"""Compare all regional checkout hits to actual Rust offline CLI serialization."""
import json
from pathlib import Path
import subprocess
import sys

root=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(root/'checkout'))
from worker import plan_for_hit, DEFAULTS
from regional_checkout import REGIONS, catalog

cli=root/'target/release/apw'
counts={}
for locale in REGIONS.keys()-{'zh_CN'}:
    products=json.loads(subprocess.check_output([str(cli),'products','--locale',locale,'--category','iphone']))['products']
    stores=json.loads(subprocess.check_output([str(cli),'stores','--locale',locale]))['stores']
    assert {s['number'] for s in stores}==set(catalog()[locale]['stores'])
    plan=dict(DEFAULTS,checkout_locale=locale,financing='manual',payment='官网手动选择',max_price=1000000)
    count=0
    for product in products:
        for store in stores:
            target=dict(locale=locale,storeNumber=store['number'],partNumber=product['partNumber'])
            hit=dict(target=target,product=product,availability=dict(kind='in_stock'),lastCheckedMs=100000)
            p=plan_for_hit(plan,hit,100001)
            assert p['part_number']==product['partNumber'] and p['store_id']==store['number']
            count+=1
    counts[locale]=count
print({'rust_wire_catalog_combinations_verified':counts,'total':sum(counts.values()),'network_requests':0})

"""Authorized current-page product-only rehearsal. Never add to bag or submit."""
import copy
import json
import os
from pathlib import Path
import sys
import time
import uuid
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'checkout'),str(root/'checkout/vendor')]
from checkout_engine import DEFAULTS, CATALOG, Attempt, variants
from edge_browser import EdgeBrowser, EdgeSession

data=Path(os.environ['LOCALAPPDATA'])/'ApplePickupWatcherAuto'
session=EdgeSession(data)
assert session.endpoint(), 'No owned Edge session'
plan=json.loads((data/'checkout.json').read_text(encoding='utf-8'))
settings=None
for base in (Path(os.environ['APPDATA']),Path(os.environ['LOCALAPPDATA'])):
    f=base/'apple-pickup-watcher-auto/settings.v2.json'
    if f.exists(): settings=json.loads(f.read_text(encoding='utf-8'));break
assert settings, 'No saved monitoring targets; do not invent a target'
context=session.ensure()
pages=[p for p in context.pages if urlsplit(p.url).hostname=='www.apple.com.cn' and urlsplit(p.url).path.startswith('/shop/buy-iphone/iphone-18-pro')]
assert len(pages)==1, 'Cannot uniquely identify the preserved Pro selection page'
page=pages[0]
models=sorted([m for m in CATALOG if m.startswith('iphone-18-pro')],key=len,reverse=True)
target=None
for row in settings.get('targets',[]):
    title=row.get('productName','')
    model=next((m for m in models if CATALOG[m]['name'].replace(' ','').lower() in title.replace(' ','').lower()),None)
    if not model:continue
    color=next((c for c in CATALOG[model]['colors'] if c in title),None)
    capacity=next((c for c in CATALOG[model]['capacities'] if c in title),None)
    if color and capacity:
        plan.update(model=model,colors=[color],capacities=[capacity],auto_submit_order=False)
        target=variants(plan)[0];break
assert target, 'No corresponding saved Pro monitoring target'
browser=EdgeBrowser(session,(root/'checkout/vendor/checkout_browser.js').read_text(encoding='utf-8'))
browser.window='authorized-current-product'
browser.pages[browser.window]=page
browser.channels[browser.window]=context.new_cdp_session(page)
attempt=Attempt(plan,target,time.monotonic())
actions=[];reads=[];outcome='timeout';deadline=time.monotonic()+40
try:
    if '--navigate' in sys.argv:
        browser.navigate('https://www.apple.com.cn/shop/buy-iphone/iphone-18-pro',foreground=True)
    while time.monotonic()<deadline:
        snapshot=browser.call({},attempt.target)
        assert snapshot.get('stage')=='PRODUCT', 'Do not operate beyond the preserved product page'
        decision=attempt.decide(snapshot,time.monotonic())
        reads.append({'stage':snapshot.get('stage'),'reason':decision.reason,'decision':decision.kind,
            'selected':[{'group':c.get('group'),'label':c['label']} for c in snapshot.get('controls',[]) if c.get('selected')],
            'diagnostics':snapshot.get('diagnostics')})
        if decision.reason in ('add','product_continue'):
            outcome='product_configured_not_added';break
        if decision.kind in ('attention','sold_out','cooldown','over_budget'):
            outcome=decision.reason;break
        if decision.kind=='click':
            assert decision.reason in ('model','color','capacity','不折抵换购','不加 AppleCare+ 服务计划','applecare_add'), 'No cart or final actions permitted'
            result=browser.call({},attempt.target,dict(kind='click',ref=decision.ref,key=decision.key,expected=snapshot['fingerprint'],nonce=uuid.uuid4().hex))
            actions.append({'step':decision.reason,'result':result.get('result')})
        elif decision.kind!='wait':
            raise AssertionError('Unexpected non-selection action')
        time.sleep(.35)
finally:
    session.disconnect()
report={'target':target,'outcome':outcome,'actions':actions,'reads':reads,'added_to_bag':False,'orders':0,'payments':0,'profile_reads':0}
out=root/'qa-product-read.json';out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False))

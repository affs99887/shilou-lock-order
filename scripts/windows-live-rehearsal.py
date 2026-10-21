"""Explicitly authorized, non-submitting Windows checkout QA.

Uses the saved Pro monitoring target and existing private Edge profile. This
does not fabricate a monitor stock hit; actual stock/date eligibility is read
in the official checkout. No settings, profile values or session URLs in output.
"""
import copy
import json
import os
from pathlib import Path
import sys
import time
import re
from urllib.parse import urlsplit

root=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(root/'checkout'),str(root/'checkout/vendor')]
from worker import Bridge, Checkout
from checkout_engine import CATALOG, validate_preferences
from checkout_stores import STORES

bridge=Bridge()
assert not bridge.order_guard.blocked, 'Previous order must be reviewed; do not bypass order guard'
assert bridge.plan.get('checkout_locale','zh_CN')=='zh_CN', 'This rehearsal is only for the saved mainland target'
settings=None
for base in (Path(os.environ['APPDATA']),Path(os.environ['LOCALAPPDATA'])):
    p=base/'apple-pickup-watcher-auto/settings.v2.json'
    if p.is_file():settings=json.loads(p.read_text(encoding='utf-8'));break
assert settings, 'No saved monitor settings'
models=sorted([m for m in CATALOG if m.startswith('iphone-18-pro')],key=len,reverse=True)
plan=None
for row in settings.get('targets',[]):
    if row.get('locale')!='zh_CN':continue
    title=row.get('productName','')
    model=next((m for m in models if CATALOG[m]['name'].replace(' ','').lower() in title.replace(' ','').lower()),None)
    if not model:continue
    color=next((c for c in CATALOG[model]['colors'] if c in title),None)
    capacity=next((c for c in CATALOG[model]['capacities'] if c in title),None)
    store=next((s for s in STORES if s['id']==row.get('storeNumber')),None)
    if color and capacity and store:
        plan=validate_preferences(dict(bridge.plan,model=model,colors=[color],capacities=[capacity],
            fulfillment='pickup',store_id=store['id'],store=store['name'],pickup_city=store['city'],
            auto_submit_order=False,bark_enabled=False,release_at=''))
        break
assert plan, 'No corresponding saved Pro target; do not invent a product or store'
bridge.acquire()
bridge.cancel.clear();bridge.automatic=False;bridge.submission_confirmed=False
bridge.reserved=True;bridge.service=Checkout(bridge,plan)
service=bridge.service
report={'platform':'Windows','mode':'authorized_source_matched_checkout_rehearsal',
    'target':{k:plan[k] for k in ('model','colors','capacities','quantity','max_price','store_id','applecare','payment','financing','pickup_date_policy','pickup_dates')},
    'steps':[],'orders_submitted':0,'payments':0,'monitor_hits_fabricated':False,
    'settings_modified':False,'profile_values_logged':False}
out=root/'qa-live-checkout.json'
started=time.monotonic();last=None
from playwright.sync_api import Page
original_goto=Page.goto
def observed_goto(page,url,*args,**kwargs):
    begin=time.monotonic()
    try:
        return original_goto(page,url,*args,**kwargs)
    except Exception as error:
        code=re.search(r'\bERR_[A-Z0-9_]+\b',str(error))
        parsed=urlsplit(url)
        report.setdefault('navigation_failures',[]).append(dict(host=parsed.hostname,path=parsed.path,
            error_code=code.group(0) if code else type(error).__name__,seconds=round(time.monotonic()-begin,2)))
        raise
Page.goto=observed_goto
def save():
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
try:
    service.command('rehearse')
    deadline=time.monotonic()+240
    while time.monotonic()<deadline:
        if service.active:service.tick()
        elif service.login_watch:service.watch_login_tick()
        elif service.region_watch:service.watch_region_tick()
        status=bridge.status()
        mark=(status['mode'],status['stage'],status['reason'])
        if mark!=last:
            entry={'mode':status['mode'],'stage':status['stage'],'reason':status['reason'],
                   'elapsed':round(time.monotonic()-started,2),'intervention':status.get('intervention')}
            report['steps'].append(entry);last=mark
            print(json.dumps(entry,ensure_ascii=False),flush=True);save()
        if status['mode']=='ready':
            report['reached_review_before_submit']=True;break
        if not service.active and not service.login_watch and not service.region_watch:
            break
        time.sleep(.25)
    report.update(final_mode=service.status['mode'],final_stage=service.status['stage'],
                  reason=service.status['reason'],elapsed=round(time.monotonic()-started,2))
except Exception as error:
    report.update(final_mode='error',reason=str(error) if type(error) in (ValueError,RuntimeError) else 'Unexpected checkout exception; scene preserved')
    try:
        s=service.browser.call({},service.attempt.target if service.attempt else {})
        report['retained_page_stage']=s.get('stage')
        report['last_document_status']=service.browser.last_status.get(service.browser.window,(0,''))[0]
    except Exception:report['retained_page_stage']='unreadable'
finally:
    Page.goto=original_goto
    bridge.cancel.set();service.command('stop');bridge.release()
    service.browser.session.disconnect()
    report['stopped']=True;report['browser_preserved']=True;save()
print(json.dumps({k:v for k,v in report.items() if k!='steps'},ensure_ascii=False),flush=True)

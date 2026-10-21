"""Exercise the real Edge adapter against intercepted fixture pages, never Apple."""
import json
import copy
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import threading
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'checkout'),str(ROOT/'checkout/vendor')]
from edge_browser import EdgeBrowser,EdgeSession,find_edge
from checkout_engine import Attempt, DEFAULTS, variants
from regional_checkout import REGIONS,catalog
from regional_service import make_regional_browser

product='''<main><h1>购买 iPhone 18 Pro</h1><span data-autom="price">RMB 9,999</span>
<label><input type="radio" name="model" value="pro">iPhone 18 Pro</label>
<label><input type="radio" name="model" value="max">iPhone 18 Pro Max</label>
<label><input type="radio" name="color" disabled>银色</label>
<label><input type="radio" name="capacity" disabled>512GB</label>
<label><input type="radio" name="tradein" disabled>不折抵换购</label>
<label><input type="radio" name="applecare" disabled>不加 AppleCare+ 服务计划</label>
<button id="add" disabled>添加到购物袋</button></main><script>
window.__adds=0;window.__steps=[];
for(const input of document.querySelectorAll('input'))input.onchange=()=>{
 window.__steps.push(input.name);
 setTimeout(()=>{
  const next={model:'color',color:'capacity',capacity:'tradein',tradein:'applecare'}[input.name];
  if(next)document.querySelector('[name="'+next+'"]').disabled=false;
  else document.getElementById('add').disabled=false;
 },250);
};document.getElementById('add').onclick=()=>window.__adds++;
</script>'''

with tempfile.TemporaryDirectory(prefix='shilou-edge-qa-',ignore_cleanup_errors=True) as directory:
    session=EdgeSession(directory)
    session.directory.mkdir()
    process=subprocess.Popen([str(find_edge()),'--headless=new','--remote-debugging-port=0',
        '--remote-debugging-address=127.0.0.1','--user-data-dir='+str(session.directory),
        '--no-first-run','--no-default-browser-check','about:blank'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    try:
        deadline=time.monotonic()+20
        while time.monotonic()<deadline and not session.endpoint():time.sleep(.1)
        assert session.endpoint(),'Edge CDP endpoint unavailable'
        context=session.ensure()
        seen=[]
        def fixture(route):
            seen.append(route.request.resource_type)
            if '/shop/qa-denied' in route.request.url:
                route.fulfill(status=541,content_type='application/json',body='{}')
                return
            if '/shop/qa-not-found' in route.request.url:
                route.fulfill(status=404,content_type='text/html',body='<main><button>继续</button></main>')
                return
            if '/buy-iphone/' in route.request.url:
                body=product
            elif '.com.cn/' in route.request.url:
                body='<main><h1>你的购物袋是空的</h1><button>继续选购</button></main>'
            else:
                body='<main><h1>Payment method</h1><input name="cardnumber" autocomplete="cc-number"></main>'
            route.fulfill(status=200,content_type='text/html; charset=utf-8',body='<html><body>'+body+'</body></html>')
        context.route('**/*',fixture)
        browser=EdgeBrowser(session,(ROOT/'checkout/vendor/checkout_browser.js').read_text(encoding='utf-8'))
        browser.open('https://www.apple.com.cn/shop/bag',foreground=False)
        browser.page().wait_for_load_state('domcontentloaded')
        first=browser.window
        snapshot=browser.call({}, {})
        assert snapshot.get('stage')=='BAG',snapshot.get('stage')
        other=context.new_page()
        other.goto('https://www.apple.com.cn/shop/test-fixture')
        assert browser.window==first
        assert browser.call({}, {})['stage']=='BAG'
        other.close()
        browser.page().close()
        try:browser.call({}, {})
        except RuntimeError as e:assert '专属' in str(e)
        else:raise AssertionError('Closed owned page must not be adopted')
        browser.prepare_entry();assert not browser.window
        browser.open('https://www.apple.com.cn/shop/bag',foreground=False)
        browser.page().wait_for_load_state('domcontentloaded')
        assert browser.window!=first and browser.call({}, {})['stage']=='BAG'
        # Exercise the real serial service, not just a standalone click. Both
        # models share one page, and every dependent option loads later.
        from worker import Checkout
        for model in ('iphone-18-pro','iphone-18-pro-max'):
            p=dict(DEFAULTS,model=model,colors=['银色'],capacities=['512GB'],max_price=20000,applecare='none')
            browser.navigate('https://www.apple.com.cn/shop/bag',foreground=False)
            browser.page().wait_for_load_state('domcontentloaded')
            owner=SimpleNamespace(data=Path(directory),cancel=threading.Event(),automatic=False,
                submission_confirmed=False,retryable_end=False,notice=None,order_guard=SimpleNamespace(blocked=False))
            service=Checkout(owner,p);service.browser=browser
            service.run_preferences=copy.deepcopy(p);service.attempt=None
            service.phase='CLEAR_BAG';service.phase_since=time.monotonic();service.job='rehearse';service.active=True
            service.update(mode='running')
            dispatch=service.dispatch;ready=[]
            def guarded(snapshot,decision):
                if decision.reason in ('add','product_continue'):
                    ready.append(True);service.active=False
                else:dispatch(snapshot,decision)
            service.dispatch=guarded
            deadline=time.monotonic()+20
            while service.active and time.monotonic()<deadline:
                service.tick();time.sleep(.1)
            assert ready,service.status.get('reason')
            assert browser.page().evaluate('window.__adds')==0
            steps=browser.page().evaluate('window.__steps')
            assert steps==['model','color','capacity','tradein','applecare'],steps
            assert browser.call({},service.attempt.target)['product_model']==service.attempt.target['name']
        # A denial can arrive after a successful product snapshot but before a
        # click. Use real Edge events; intercepted traffic never leaves the PC.
        page=browser.page()
        page.evaluate("() => fetch('/shop/qa-denied',{method:'POST'}).then(r=>r.status)")
        denial=browser.call({}, {}, {'kind':'click','ref':'1','nonce':'blocked'})
        assert denial['stage']=='BLOCKED' and denial['http_status']==541
        assert denial['result']=='refused'
        assert page.evaluate('window.__adds')==0
        browser.navigate('https://www.apple.com.cn/shop/qa-not-found')
        page.wait_for_load_state('domcontentloaded')
        missing=browser.call({}, {}, {'kind':'click','ref':'1','nonce':'missing'})
        assert missing['stage']=='NOT_FOUND' and missing['http_status']==404
        assert missing['result']=='refused'
        # All regional Edge transports use their own storefront and never fill
        # a payment field; outgoing traffic is intercepted above.
        for locale in REGIONS.keys()-{'zh_CN'}:
            owner=SimpleNamespace(data=Path(directory),cancel=threading.Event())
            regional=make_regional_browser(owner,locale);regional.session=session
            c=catalog()[locale];part,item=next(iter(c['products'].items()))
            target=dict(checkout_locale=locale,currency=REGIONS[locale]['currency'],regional_product=item,
                        store_id=next(iter(c['stores'])),part_number=part)
            regional.open(REGIONS[locale]['base']+'/shop/checkout',foreground=False)
            regional.page().wait_for_load_state('domcontentloaded')
            s=regional.call({'phone':'never-inject','credit_card':'never-inject'},target)
            assert s['stage']=='PAYMENT',s.get('stage')
            assert regional.page().locator('input').input_value()==''
            regional.page().close()
        print(json.dumps({'real_edge':True,'owned_page_isolation':True,'explicit_recovery':True,
            'full_service_pro_and_max_selection':True,'empty_bag_to_product':True,'delayed_dependent_options':True,
            'six_regional_edge_transports':True,'payment_fields_untouched':True,
            'async_541_prevents_click':True,'http_404_distinct':True,
            'fixture_requests':len(seen),'apple_requests':0,'orders':0,'cart_adds':0}))
    finally:
        # This process was launched solely for this test, with a temporary
        # profile. Production disconnect deliberately leaves user pages open.
        if session.browser:
            session.browser.close()
        session.disconnect()
        if process.poll() is None:process.terminate()
        process.wait(timeout=15)

"""Native Safari fixture; never navigates to Apple or reads personal profiles."""
import copy
import json
from pathlib import Path
import sys
import time
import uuid

ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'checkout'),str(ROOT/'checkout/vendor')]
from checkout_engine import Attempt,DEFAULTS,variants
from checkout_service import Safari

OUT=ROOT/'checkout/qa-pickup-stability'
OUT.mkdir(exist_ok=True)
browser=Safari()
existing=browser.script('tell application "Safari" to get id of windows')
window=0
fixture='''<main><h1>你希望如何收到订单商品？</h1>
<label><input type="radio" name="method" checked>我要取货</label>
<label><input type="radio" name="retail-store" value="R575" checked>Apple 武汉</label>
<div id="dates"></div><div id="slots" aria-busy="true"></div>
<button id="next" disabled>继续填写取货详情</button></main>
<script>
window.__continued=0;window.__dateChanges=0;window.__timeChanges=0;
document.getElementById('next').onclick=()=>window.__continued++;
setTimeout(()=>{
 dates.innerHTML='<label for="date">取货日期</label><select id="date" name="pickup-date" required><option value="">请选择日期</option><option value="2026-10-23">October23</option><option value="2026-10-24">October24</option></select>';
 document.getElementById('date').onchange=()=>{
  window.__dateChanges++;slots.setAttribute('aria-busy','true');
  setTimeout(()=>{
   slots.innerHTML='<label for="time">选择签到时间</label><select id="time" name="checkout.pickup.checkInWindow" required><option value="">正在加载…</option><option value="10:00">10:00 – 10:15</option></select>';
   slots.setAttribute('aria-busy','false');
   document.getElementById('time').onchange=()=>{window.__timeChanges++;document.getElementById('next').disabled=false;};
  },1200);
 };
},1200);
</script>'''
try:
    window=int(browser.script('tell application "Safari"\nmake new document with properties {URL:"about:blank#pickup-stability-fixture"}\nreturn id of front window\nend tell'))
    assert str(window) not in existing.replace(',',' ').split(), 'Fixture must own a new window'
    browser.window=window;browser.bindings[window]=(1,['about:blank#pickup-stability-fixture'])
    def js(code):
        return browser.script(browser.bound('return do JavaScript '+json.dumps(code,ensure_ascii=False)+' in checkoutTab'))
    # Test-only source guard: production keeps its first-party HTTPS restriction.
    source=(ROOT/'checkout/vendor/checkout_browser.js').read_text()
    source=source.replace('if (location.href.startsWith("about:blank")) return {stage: "LOADING"};','')
    source=source.replace('if (location.protocol !== "https:" || !allowed(location.hostname)) return {stage: "FOREIGN"};','if(location.href!=="about:blank#pickup-stability-fixture") return {stage:"FOREIGN"};')
    source=source.replace('const path = location.pathname.toLowerCase();', 'const path = \"/shop/checkout\";')
    js('document.open();document.write('+json.dumps(fixture,ensure_ascii=False)+');document.close();"fixture"')
    js(source+';"adapter"')
    store_id,store_name=('R471','Apple 西湖') if 'hangzhou' in sys.argv else ('R575','Apple 武汉')
    if store_id!='R575':
        js("document.querySelector('[name=retail-store]').value="+json.dumps(store_id)+";document.querySelector('[name=retail-store]').parentElement.lastChild.textContent="+json.dumps(store_name,ensure_ascii=False))
    p=copy.deepcopy(DEFAULTS);p.update(pickup_date_policy='earliest',store=store_name,store_id=store_id,model='iphone-18-pro',colors=['银色'],capacities=['256GB'],fulfillment='pickup',financing='full',payment='支付宝',pickup_dates=['2026-10-23','2026-10-24'])
    a=Attempt(p,variants(p)[0],time.monotonic());a.bag_verified=True;a.started=time.monotonic();a.target['pickup_dates']=p['pickup_dates']
    actions=[];reads=[];deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        payload={'profile':{},'target':a.target}
        s=json.loads(js('JSON.stringify(appleCheckout('+json.dumps(payload,ensure_ascii=False)+'))'))
        d=a.decide(s,time.monotonic())
        reads.append({'stage':s.get('stage'),'diagnostics':s.get('diagnostics'),'schedule':s.get('pickup_schedule'),'decision':d.kind,'reason':d.reason})
        (OUT/'reads.json').write_text(json.dumps(reads,ensure_ascii=False,indent=2))
        assert d.kind not in ('attention','sold_out','cooldown'),d.reason
        if d.kind in ('click','set','fill'):
            payload['command']={'kind':d.kind,'ref':d.ref,'key':d.key,'value':d.value,'expected':s['fingerprint'],'nonce':uuid.uuid4().hex}
            result=json.loads(js('JSON.stringify(appleCheckout('+json.dumps(payload,ensure_ascii=False)+'))'))
            assert result.get('result')=='dispatched',result
            actions.append({'kind':d.kind,'key':d.key,'reason':d.reason})
        state=json.loads(js('JSON.stringify({continued:window.__continued,dateChanges:window.__dateChanges,timeChanges:window.__timeChanges})'))
        if state['continued']:
            assert state=={'continued':1,'dateChanges':1,'timeChanges':1},state
            assert a.fulfillment_verified
            report={'native_safari':True,'model':'iPhone 18 Pro','store_id':store_id,'date_panel_delay_seconds':1.2,'time_panel_delay_seconds':1.2,'retained_loading_placeholder':True,'check_in_window_select':True,'actions':actions,'readback':state,'apple_requests':0,'orders':0}
            (OUT/f'native-safari-{store_id}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2));print(json.dumps(report,ensure_ascii=False));break
        time.sleep(.25)
    else:raise AssertionError('Native Safari fixture timed out')
finally:
    if window:
        browser.script(f'tell application "Safari"\nif exists window id {window} then close window id {window}\nend tell')
    # Safari retains a closed window object with zero tabs until a later event.
    # Check its content/visibility, not only the stale scripting-window ID list.
    closed=browser.script(f'tell application "Safari"\nif not (exists window id {window}) then return "closed"\nif (count of tabs of window id {window}) is 0 then return "closed"\nreturn "open"\nend tell')
    assert closed=="closed",'Fixture must leave no open tabs'
    print(json.dumps({'test_window_closed':True}))

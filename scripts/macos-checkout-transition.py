"""Exercise outgoing-form resets in a dedicated local Safari fixture."""
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
browser=Safari();window=0
fixture='''<main><h1>你希望如何收到订单商品？</h1>
<label><input type="radio" name="method" checked>我要取货</label>
<label><input type="radio" name="retail-store" value="R683" checked>Apple 环球港</label>
<select name="pickup-date"><option value="">请选择</option><option selected value="2026-10-23">2026年10月23日</option></select>
<select name="pickup-time"><option selected value="slot">10:00 – 10:15</option></select>
<button id="next">继续填写取货详情</button></main>
<script>
window.__counts={dates:0,continues:0,contactContinues:0};
document.querySelector('select').onchange=function(){window.__counts.dates++;setTimeout(()=>this.value='',100)};
document.querySelector('#next').onclick=()=>{
 window.__counts.continues++;
 document.querySelector('select').value='';
 setTimeout(()=>{
 document.querySelector('main').innerHTML='<h1>现在请填写你的取货信息。</h1><input name="phone" type="tel" required><input name="email" type="email" required><button id="contact">继续选择付款方式</button>';
 document.querySelector('#contact').onclick=()=>window.__counts.contactContinues++;
 },18000);
};
</script>'''
try:
 window=int(browser.script('tell application "Safari"\nmake new document with properties {URL:"about:blank#checkout-transition-fixture"}\nreturn id of front window\nend tell'))
 browser.window=window;browser.bindings[window]=(1,['about:blank#checkout-transition-fixture'])
 def js(code):return browser.script(browser.bound('return do JavaScript '+json.dumps(code,ensure_ascii=False)+' in checkoutTab'))
 source=(ROOT/'checkout/vendor/checkout_browser.js').read_text()
 source=source.replace('if (location.href.startsWith("about:blank")) return {stage: "LOADING"};','')
 source=source.replace('if (location.protocol !== "https:" || !allowed(location.hostname)) return {stage: "FOREIGN"};','if(location.href!=="about:blank#checkout-transition-fixture") return {stage:"FOREIGN"};')
 source=source.replace('const path = location.pathname.toLowerCase();','const path = "/shop/checkout";')
 js('document.open();document.write('+json.dumps(fixture,ensure_ascii=False)+');document.close();"fixture"')
 js(source+';"adapter"')
 p=copy.deepcopy(DEFAULTS);p.update(fulfillment='pickup',store='Apple 环球港',store_id='R683',pickup_dates=['2026-10-23'],financing='full',payment='支付宝')
 a=Attempt(p,variants(p)[0],time.monotonic());a.bag_verified=True;a.started=time.monotonic()
 actions=[];deadline=time.monotonic()+35
 while time.monotonic()<deadline:
  payload={'target':a.target,'profile':{'phone':'13800000000','email':'fixture@example.invalid'}}
  s=json.loads(js('JSON.stringify(appleCheckout('+json.dumps(payload,ensure_ascii=False)+'))'))
  d=a.decide(s,time.monotonic())
  assert d.kind not in ('attention','sold_out','cooldown'),d.reason
  if d.kind in ('click','set','fill'):
   payload['command']={'kind':d.kind,'ref':d.ref,'key':d.key,'value':d.value,'expected':s['fingerprint'],'nonce':uuid.uuid4().hex}
   result=json.loads(js('JSON.stringify(appleCheckout('+json.dumps(payload,ensure_ascii=False)+'))'))
   assert result.get('result')=='dispatched',result
   actions.append(d.reason)
  counts=json.loads(js('JSON.stringify(window.__counts)'))
  if counts['contactContinues']:
   assert counts=={'dates':0,'continues':1,'contactContinues':1},counts
   assert a.fulfillment_verified and a.contact_verified
   report={'native_safari':True,'outgoing_form_reset':True,'transition_delay_seconds':18,'poll_delay_seconds':.1,'counts':counts,'actions':actions,'apple_requests':0,'orders':0}
   (ROOT/'checkout/qa-pickup-stability/native-transition-auto13.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
   print(json.dumps(report,ensure_ascii=False));break
  time.sleep(.1)
 else:raise AssertionError('transition fixture timed out')
finally:
 if window:browser.script(f'tell application "Safari"\nif exists window id {window} then close window id {window}\nend tell')

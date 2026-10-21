const {test}=require('node:test'),assert=require('node:assert/strict');
const {JSDOM}=require('jsdom'),fs=require('node:fs'),path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'vendor/checkout_browser.js'),'utf8');
const target={name:'iPhone 17',color:'白色',capacity:'256GB',quantity:1};
function page(html){const w=new JSDOM('<main>'+html+'</main>',{url:'https://www.apple.com.cn/shop/checkout',runScripts:'outside-only'}).window;
 w.HTMLElement.prototype.getClientRects=function(){return this.hidden?[]:[{}]};
 Object.defineProperty(w.document,'readyState',{value:'complete'});w.performance.getEntriesByType=()=>[];w.eval(code);return w;}
function radio(id,label,checked=false){return `<input type="radio" id="${id}" ${checked?'checked':''}><label for="${id}">${label}</label>`;}
test('official image-only payment labels identify providers without using numeric IDs',()=>{
 for(const provider of ['花呗分期','微信分付','招商银行','中国建设银行','工商银行']){
  const w=page('<h1>付款方式</h1><input type="radio" id="bank" name="checkout.selectBillingOption" value="installments0001864343"><label for="bank"><img alt="'+provider+'"></label>');
  const s=w.appleCheckout({target});
  assert.equal(s.controls[0].label,provider);
  assert.equal(s.controls[0].installment,true);
  w.close();
 }
});
test('additional-bank search does not block a selected named provider, other fields remain required',()=>{
 for(const provider of ['微信分付','未知银行']){
  const w=page('<h1>付款方式</h1><div class="rf-paymentoptions-group"><input checked type="radio" id="provider" name="checkout.selectBillingOption"><label for="provider"><img alt="'+provider+'"></label><div class="rf-paymentoptions-typeahead-autocomplete-dropdown"><input required name="selectBank" id="checkout.billing.billingOptions.bankLookUp.selectBank"><label for="checkout.billing.billingOptions.bankLookUp.selectBank">通过支付宝选择更多银行</label></div><input required name="credit-card-number"></div>');
  const s=w.appleCheckout({target});
  assert.equal(s.fields.length,provider==='微信分付'?1:2);
  assert.ok(s.fields.every(f=>f.required&&f.invalid));
  w.close();
 }
});
test('all five provider labels are installment choices, never full payment',()=>{
 for(const p of ['花呗分期','微信分付','招商银行','中国建设银行','工商银行']){
 const w=page('<h1>付款方式</h1>'+radio('p',p,true)+radio('t','24 期 0% 年化利率',true));
 const s=w.appleCheckout({target});assert.equal(s.controls[0].group,'payment');assert.equal(s.controls[0].installment,true);
 assert.equal(s.controls[0].label,p);assert.equal(s.installment_panel_stable,false);
 w.__appleCheckoutV2.installmentSince-=2000;
 assert.equal(w.appleCheckout({target}).installment_panel_stable,true);
 w.document.getElementById('t').checked=false;
 assert.equal(w.appleCheckout({target}).installment_panel_stable,false);w.close();}
});
test('bank one-month plan is recognised',()=>{
 const w=page('<h1>付款方式</h1>'+radio('p','招商银行',true)+radio('t','1 期 0% 年化利率',true));
 assert.equal(w.appleCheckout({target}).installment_selected_months,1);w.close();
});
function review(provider='微信分付',months=24){return `<div data-autom="review-order"><li><h2>iPhone 17 256GB 白色</h2><p>数量 1</p><p>送货</p></li><h2>付款详情</h2><h3>分期付款方案：</h3><img alt="${provider}"><p>${months} 个月，每月约 RMB 667</p><h2>总计</h2><p data-autom="total">总计 RMB 15,999</p><p>${months} 期，每期金额约为 RMB 667，总计约 RMB 15,999 (金融机构合作伙伴收取 0% 手续费)</p></div><button id="final">立即下单</button>`;}
test('review reads provider image, selected months and total fee',()=>{
 const w=page(review());const s=w.appleCheckout({target});
 assert.deepEqual(JSON.parse(JSON.stringify(s.review_payment)),{provider:'微信分付',months:24,fee_percent:0});
 assert.equal(s.stage,'REVIEW');assert.ok(s.final_order_ref);w.close();
});
test('ordinary click cannot place order, even with current fingerprint',()=>{
 const w=page(review());const s=w.appleCheckout({target});let clicks=0;w.document.getElementById('final').onclick=()=>clicks++;
 const r=w.appleCheckout({target,command:{kind:'click',ref:s.final_order_ref,expected:s.fingerprint,nonce:'test'}});
 assert.equal(r.result,'refused');assert.equal(clicks,0);w.close();
});
test('formal submit rechecks provider and amount then clicks only once',()=>{
 const w=page(review());const s=w.appleCheckout({target});let clicks=0;w.document.getElementById('final').onclick=()=>clicks++;
 const command={kind:'submit_order',ref:s.final_order_ref,expected:s.fingerprint,nonce:'test',authorized:true,max_price:20000,method:'delivery',financing:'official',provider:'微信分付',months:24,applecare:'none'};
 assert.equal(w.appleCheckout({target,command:{...command,months:12}}).result,'refused');
 assert.equal(w.appleCheckout({target,command:{...command,max_price:1000}}).result,'refused');
 assert.equal(w.appleCheckout({target,command}).result,'dispatched',JSON.stringify({total:s.total,items:s.review_items,quantity:s.review_quantity,extras:s.review_extras_match,method:s.review_method,care:s.review_applecare,payment:s.review_payment,errors:s.errors}));
 assert.equal(w.appleCheckout({target,command}).result,'duplicate');assert.equal(clicks,1);w.close();
});
test('receipt needs visible order identifier AND explicit pending payment',()=>{
 for(const [text,expected] of [['<h1>订单已提交</h1><p>订单号 W123456789 待支付</p>',true],
 ['<h1>订单已提交</h1><p>待支付</p>',false],['<h1>订单已提交</h1><p>订单号 W123456789 待支付 订单已取消</p>',false],
 ['<h1>准备下单了吗</h1><p>订单号 W123456789 待支付</p>',false]]){
 const w=page(text);assert.equal(w.appleCheckout({target}).order_pending_payment,expected);assert.ok(!JSON.stringify(w.appleCheckout({target})).includes('W123456789'));w.close();}
});
function deferredReview(extra=''){
 return `<div data-autom="review-order"><div class="rs-review-fulfillment"><h2>取货日期待付款完成后确定。</h2></div><li><h2>iPhone 17 256GB 白色</h2><p>数量 1</p></li><div class="rs-review-details"><h2>取货详情</h2>${extra}</div><h2>付款详情</h2><h3>全额付款：</h3><img alt="支付宝"><h2>总计</h2><p data-autom="total">总计 RMB 9,999</p></div><button id="final">立即下单</button>`;
}
const pickupTarget={...target,store:'Apple 环球港',store_id:'R683',store_names:['Apple 环球港','Apple 武汉'],pickup_date_policy:'earliest'};
test('HTTP denial takes precedence over the official not-found template',()=>{
 const w=page('<h1>Page Not Found</h1>');
 w.history.replaceState({},'', '/shop/404');
 for(const status of [403,429,541]){
  w.performance.getEntriesByType=()=>[{responseStatus:status}];
  const s=w.appleCheckout({target});assert.equal(s.stage,'BLOCKED');assert.equal(s.http_status,status);
 }
 w.performance.getEntriesByType=()=>[];
 assert.equal(w.appleCheckout({target}).stage,'NOT_FOUND');w.close();
});
test('deferred pickup requires verified attempt context and cannot become a specific date',()=>{
 const w=page(deferredReview());const s=w.appleCheckout({target:pickupTarget});let clicks=0;
 w.document.getElementById('final').onclick=()=>clicks++;
 assert.equal(s.review_pickup_deferred,true);
 assert.equal(s.review_store_matches,false);
 assert.equal(s.review_items[0].pickup_dates.length,0);
 const command={kind:'submit_order',ref:s.final_order_ref,expected:s.fingerprint,nonce:'deferred',authorized:true,max_price:20000,method:'pickup',financing:'full',payment:'支付宝',applecare:'none'};
 assert.equal(w.appleCheckout({target:pickupTarget,command}).result,'refused');
 assert.equal(w.appleCheckout({target:{...pickupTarget,pickup_date_policy:'specified'},command:{...command,verified_deferred_pickup:true}}).result,'refused');
 assert.equal(clicks,0);
 assert.equal(w.appleCheckout({target:pickupTarget,command:{...command,verified_deferred_pickup:true}}).result,'dispatched');
 assert.equal(w.appleCheckout({target:pickupTarget,command:{...command,verified_deferred_pickup:true}}).result,'duplicate');
 assert.equal(clicks,1);w.close();
});
test('deferred pickup cannot hide another store, delivery or extra phones',()=>{
 for(const html of [deferredReview('Apple 武汉'),deferredReview('<h2>送货详情</h2>'),
  deferredReview().replace('数量 1','数量 2'),
  deferredReview().replace('</li>','</li><li><h2>iPhone 17 256GB 黑色</h2><p>数量 1</p></li>')]){
  const w=page(html);assert.equal(w.appleCheckout({target:pickupTarget}).review_pickup_deferred,false);w.close();
 }
});
test('review edit controls are exact, visible and unique',()=>{
 const w=page(deferredReview('<button data-autom="changeShippingMethod">更改零售店</button><button data-autom="pickup-changeLink">更改</button><button data-autom="billing-changeLink">更改</button>'));
 assert.deepEqual(Object.keys(w.appleCheckout({target:pickupTarget}).review_edit_refs),['fulfillment','contact','payment']);
 w.document.querySelector('[data-autom="pickup-changeLink"]').hidden=true;
 assert.equal(w.appleCheckout({target:pickupTarget}).review_edit_refs.contact,undefined);
 w.document.querySelector('[data-autom="billing-changeLink"]').insertAdjacentHTML('afterend','<button data-autom="billing-changeLink">更改</button>');
 assert.equal(w.appleCheckout({target:pickupTarget}).review_edit_refs.payment,undefined);w.close();
});

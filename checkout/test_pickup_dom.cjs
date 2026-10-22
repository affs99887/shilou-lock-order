const {test}=require('node:test'),assert=require('node:assert/strict');
const {JSDOM}=require('jsdom'),fs=require('node:fs'),path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'vendor/checkout_browser.js'),'utf8');
function page(body){const w=new JSDOM('<main><h1>你希望如何收到订单商品？</h1>'+body+'</main>',{url:'https://www.apple.com.cn/shop/checkout',runScripts:'outside-only'}).window;
 w.HTMLElement.prototype.getClientRects=function(){return this.hidden?[]:[{}]};Object.defineProperty(w.document,'readyState',{value:'complete'});w.performance.getEntriesByType=()=>[];w.eval(code);return w;}
test('retained loading placeholder cannot hide available pickup times',()=>{
 const w=page('<select name="pickup-time"><option value="">正在加载…</option><option value="slot">10:00 – 10:15</option></select>');
 const s=w.appleCheckout({});assert.equal(s.pickup_schedule[0].disabled,false);assert.equal(s.pickup_schedule[0].choices.length,1);w.close();
});
test('check-in-window is a schedule select and does not become an unknown required field',()=>{
 const w=page('<label for="slot">选择签到时间</label><select id="slot" name="checkout.pickup.checkInWindow" required><option value="">请选择</option><option value="slot">10:00 – 10:15</option></select>');
 const s=w.appleCheckout({});assert.equal(s.pickup_schedule[0]?.key,'pickup_time');assert.equal(s.diagnostics.unknown_required_count,0);w.close();
});
test('busy appointment group remains loading even when old options are present',()=>{
 const w=page('<div aria-busy="true"><select name="pickup-time"><option value="slot">10:00 – 10:15</option></select></div>');
 assert.equal(w.appleCheckout({}).pickup_schedule[0].disabled,true);w.close();
});
test('store radios expose their own store number so another store can be chosen',()=>{
 const store=(id,name,extra='')=>`<input type="radio" name="checkout.fulfillment.pickupTab.pickup.storeLocator.selectStore" id="s-${id}" value="${id}" ${extra}><label for="s-${id}">${name}</label>`;
 const w=page(store('R683','Apple 环球港','disabled')+store('R390','Apple 香港广场'));
 const target={store:'Apple 环球港',store_id:'R683'};
 const stores=t=>[...w.appleCheckout({target:t}).controls].filter(c=>c.group==='store');
 assert.deepEqual(stores(target).map(c=>[c.store_id,c.store_matches,c.disabled]),[['R683',true,true],['R390',false,false]]);
 assert.deepEqual(stores({store:'Apple 香港广场',store_id:'R390'}).map(c=>c.store_matches),[false,true]);
 w.close();
});
test('phone verification and privacy consent expose safe handoff flags',()=>{
 const w=page('<h1>填写你的取货信息</h1><input name="phone" required><button data-autom="contactVerify-button-fullDaytimePhone">验证</button>');
 w.document.querySelector('h1').remove();
 let s=w.appleCheckout({});assert.equal(s.contact_verification_required,true);
 w.document.querySelector('button').disabled=true;assert.equal(w.appleCheckout({}).contact_verification_required,false);
 w.document.body.insertAdjacentHTML('beforeend','<div role="dialog"><h2>Apple 和你的数据隐私</h2></div>');
 assert.equal(w.appleCheckout({}).diagnostics.privacy_consent_required,true);w.close();
});

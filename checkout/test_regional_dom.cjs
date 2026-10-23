const {test}=require('node:test'),assert=require('node:assert/strict');
const {JSDOM}=require('jsdom'),fs=require('node:fs'),path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'vendor/regional_browser.js'),'utf8');
const regions=require('../src/lib/checkout-regions.json');
const catalog=require('../src/lib/checkout-catalog.json');
function page(locale,html,url){
 const r=regions.find(x=>x.locale===locale),part=Object.keys(catalog[locale].products)[0];
 const w=new JSDOM('<main>'+html+'</main>',{url:url||r.base+'/shop/checkout',runScripts:'outside-only'}).window;
 w.HTMLElement.prototype.getClientRects=function(){return this.hidden?[]:[{}]};
 Object.defineProperty(w.document,'readyState',{value:'complete',configurable:true});w.eval(script);
 const target={checkout_locale:locale,currency:r.currency,part_number:part,store_id:Object.keys(catalog[locale].stores)[0],regional_product:catalog[locale].products[part],clear_bag_before_purchase:true};
 return {w,target,run:(command,profile={})=>w.regionalCheckout({target,profile,command})};
}
test('all six foreign storefronts allow regional fields but never a final-order click',()=>{
 for(const [locale,title] of [['zh_HK','立即下單'],['zh_TW','立即下單'],['ja_JP','注文を確定'],['en_SG','Place Order'],['en_AU','Pay Now'],['en_MY','Buy Now']]){
  const a=page(locale,`<input autocomplete="given-name" required><button>${title}</button>`);
  const s=a.run();assert.equal(s.stage,'REVIEW');let clicks=0;
  a.w.document.querySelector('button').onclick=()=>clicks++;
  const command={kind:'click',ref:s.controls[0].ref,expected:s.fingerprint,nonce:'ordinary'};
  assert.equal(a.run(command).result,'refused');
  assert.equal(a.run({...command,kind:'submit_order',authorized:true,nonce:'formal'}).result,'refused');
  assert.equal(clicks,0);a.w.close();
 }
});
test('cross-region, credentials, hostile hosts and authentication forms never get contact values',()=>{
 for(const url of ['https://www.apple.com/us/shop/checkout','https://www.apple.com.cn/shop/checkout','https://evil.apple.com/sg/shop/checkout','https://www.apple.com.evil.test/sg/shop/checkout']){
  const a=page('en_SG','<input autocomplete="given-name">',url);assert.equal(a.run().stage,'FOREIGN');a.w.close();
 }
 const a=page('en_SG','<input type="password"><input autocomplete="given-name">');
 assert.equal(a.run(null,{given_name:'PRIVATE'}).stage,'LOGIN');assert.ok(!JSON.stringify(a.run()).includes('PRIVATE'));a.w.close();
});
test('field fills use native events, fresh fingerprints and document-local one-shot receipts',()=>{
 const a=page('en_SG','<input autocomplete="given-name" required>');
 const profile={given_name:'Example'},s=a.run(null,profile);const ref=s.fields[0].ref;let events=0;
 a.w.document.querySelector('input').oninput=()=>events++;
 const command={kind:'fill',ref,expected:s.fingerprint,nonce:'one'};
 assert.equal(a.run(command,profile).result,'dispatched');assert.equal(events,1);
 assert.equal(a.w.document.querySelector('input').value,'Example');
 const next=a.run(null,profile);assert.equal(next.fields[0].matches,true);assert.notEqual(next.fingerprint,s.fingerprint);
 assert.equal(a.run(command,profile).result,'stale');
 assert.equal(a.run({...command,expected:next.fingerprint},profile).result,'duplicate');assert.equal(events,1);a.w.close();
});
test('payment inputs and verification fields are never fillable',()=>{
 const a=page('en_AU','<input autocomplete="cc-number"><input autocomplete="one-time-code"><input name="cardSecurityCode">');
 const s=a.run();assert.equal(s.stage,'PAYMENT');assert.ok(s.fields.every(f=>f.key===''));
 for(const f of s.fields)assert.equal(a.run({kind:'fill',ref:f.ref,expected:s.fingerprint,nonce:f.ref},{phone:'PRIVATE'}).result,'refused');a.w.close();
});
test('exact local SKU, numeric quantity and explicit currency are read from the bag',()=>{
 for(const locale of regions.map(r=>r.locale).filter(l=>l!=='zh_CN')){
  const c=catalog[locale],part=Object.keys(c.products)[0],r=regions.find(x=>x.locale===locale);
  const a=page(locale,`<div data-autom="bag-item" data-part-number="${part}"><select name="quantity"><option value="1">1</option><option selected value="2">2</option></select></div><p data-autom="total" data-currency="${r.currency}">1,999</p><button>Checkout</button>`,r.base+'/shop/bag');
  const s=a.run();assert.equal(s.items[0].part,part);assert.equal(s.items[0].quantity,2);assert.equal(s.currency,r.currency);assert.equal(s.total,1999);a.w.close();
 }
});
test('a bare dollar sign is not accepted as proof of SGD vs AUD',()=>{
 const a=page('en_SG','<p data-autom="total">$1,999.00</p>');assert.equal(a.run().currency,null);a.w.close();
});
test('stock failure is scoped to actual availability messages, not related product recommendations',()=>{
 const a=page('en_SG','<p>Related accessory currently unavailable</p><button>Add to Bag</button>','https://www.apple.com/sg/shop/buy-iphone/iphone-17');
 assert.equal(a.run().stage,'PRODUCT');a.w.document.querySelector('main').insertAdjacentHTML('beforeend','<p data-autom="availability">Currently unavailable</p>');assert.equal(a.run().stage,'SOLD_OUT');a.w.close();
});
test('an empty parser result is not evidence that the bag is empty',()=>{
 const a=page('en_SG','<div>Unrecognised new bag layout</div>','https://www.apple.com/sg/shop/bag');assert.equal(a.run().bag_empty,false);assert.equal(a.run().stage,'LOADING');
 a.w.document.querySelector('main').innerHTML='<h1>Your bag is empty.</h1>';assert.equal(a.run().bag_empty,true);a.w.close();
});
test('completed bag documents wait for hydration and for the post-remove empty heading',()=>{
 const a=page('zh_HK','<h1 data-autom="bag-header"></h1><div data-autom="bag-items"></div>','https://www.apple.com/hk-zh/shop/bag');
 let s=a.run();assert.equal(s.stage,'LOADING');assert.equal(s.bag_loading,true);assert.equal(s.bag_empty,false);
 a.w.document.querySelector('main').innerHTML='<h1>你的購物袋總計為 HK$10,499。</h1><li class="rs-bag-item" data-part-number="MJRT4ZA/A"><select name="quantity"><option value="1">1</option></select><button>移除 iPhone</button></li><div data-autom="bagtotalvalue">HK$10,499</div>';
 s=a.run();assert.equal(s.stage,'BAG');assert.equal(s.items.length,1);
 a.w.document.querySelector('.rs-bag-item').remove();s=a.run();assert.equal(s.stage,'LOADING');assert.equal(s.bag_empty,false);
 a.w.document.querySelector('h1').textContent='你的購物袋沒有任何項目。';s=a.run();assert.equal(s.stage,'BAG');assert.equal(s.bag_empty,true);a.w.close();
});
test('a local SKU colour dimension uses exact canonical values',()=>{
 const a=page('en_SG','<input type="radio" name="dimensionColor" value="spaceblack"><input type="radio" name="dimensionColor" value="black">','https://www.apple.com/sg/shop/buy-iphone/iphone-17');
 a.target.regional_product={dimensions:{dimensionColor:'black'}};
 assert.deepEqual(Array.from(a.run().controls,c=>c.matches),[false,true]);a.w.close();
});
test('an add command rechecks the selected SKU even with current fingerprint',()=>{
 const a=page('en_SG','<input type="hidden" name="product" value="WRONG1/A"><button>Add to Bag</button>','https://www.apple.com/sg/shop/buy-iphone/iphone-17');
 let s=a.run();let clicks=0;a.w.document.querySelector('button').onclick=()=>clicks++;
 const command={kind:'click',ref:s.controls[0].ref,expected:s.fingerprint,nonce:'add'};
 assert.equal(a.run(command).result,'refused');
 a.w.document.querySelector('input').value=a.target.part_number;s=a.run();
 assert.equal(a.run({...command,expected:s.fingerprint}).result,'dispatched');assert.equal(clicks,1);a.w.close();
});
test('duplicate visible totals agree, conflicting totals are never chosen by maximum',()=>{
 const a=page('en_SG','<p data-autom="total">S$1,999.00</p><p data-autom="bagtotal">S$1,999.00</p>');
 assert.equal(a.run().total,1999);a.w.document.querySelector('[data-autom="bagtotal"]').textContent='S$2,000.00';
 assert.equal(a.run().total,null);a.w.close();
});

test('pending regional navigation cannot act on the previous document',()=>{
 const a=page('en_SG','<button>Continue</button>');let clicked=0;
 a.w.document.querySelector('button').onclick=()=>clicked++;
 const s=a.run();const request={target:a.target,profile:{},expected_path:'/sg/shop/buy-iphone/iphone-17',command:{kind:'click',ref:s.controls[0].ref,nonce:'old-page',expected:s.fingerprint}};
 assert.equal(a.w.regionalCheckout(request).stage,'LOADING');assert.equal(clicked,0);a.w.close();
});


test('owned Safari blank marker and loading documents wait without a false foreign-region result',()=>{
 const a=page('zh_HK','', 'about:blank#apple-checkout-v2-test');assert.equal(a.run().stage,'LOADING');a.w.close();
 const b=page('zh_HK','<button>加入購物袋</button>','https://www.apple.com/hk-zh/shop/buy-iphone/iphone-18-pro');
 Object.defineProperty(b.w.document,'readyState',{value:'loading',configurable:true});assert.equal(b.run().stage,'LOADING');b.w.close();
});
test('Hong Kong actual empty-bag heading is recognised, while footer help is not proof of emptiness',()=>{
 const a=page('zh_HK','<h1>你的購物袋沒有任何項目。</h1>','https://www.apple.com/hk-zh/shop/bag');
 assert.equal(a.run().bag_empty,true);
 a.w.document.querySelector('main').innerHTML='<h1>你的購物袋</h1><p>Your bag is empty.</p>';
 assert.equal(a.run().bag_empty,false);a.w.close();
});
test('Hong Kong bag parses lowercase product links, ignores accessory IDs and checks exact total',()=>{
 const a=page('zh_HK',`<li class="rs-bag-item" data-autom="bag-item-1"><a data-autom="bag-item-name" href="/hk-zh/shop/product/mjrt4za/a">iPhone</a><button id="accessory-sHHJ3ZX/A">Add accessory</button><select name="quantity"><option value="1">1</option></select></li><p data-autom="bagtotalvalue">HK$10,499</p><button data-autom="checkout">結帳</button><button data-autom="checkout">結帳</button>`,'https://www.apple.com/hk-zh/shop/bag');
 const s=a.run();assert.equal(s.items.length,1);assert.equal(s.items[0].part,'MJRT4ZA/A');assert.equal(s.items[0].quantity,1);assert.equal(s.items[0].applecare,false);assert.equal(s.currency,'HKD');assert.equal(s.total,10499);assert.equal(s.controls.filter(c=>c.autom==='checkout').length,2);
 a.w.document.querySelector('.rs-bag-item').insertAdjacentHTML('beforeend','<a href="/hk-zh/shop/product/wrong1/a">Other item</a>');assert.equal(a.run().items[0].part,null);a.w.close();
});
test('actual Hong Kong secure-store checkout login is a manual checkpoint, never a contact page',()=>{
 const a=page('zh_HK','<input autocomplete="given-name">','https://secure6.store.apple.com/hk-zh/shop/signIn');
 assert.equal(a.run(null,{given_name:'PRIVATE'}).stage,'LOGIN');assert.equal(a.w.document.querySelector('input').value,'');a.w.close();
 const b=page('zh_HK','<input autocomplete="given-name">','https://secure6.store.apple.com/us/shop/signIn');assert.equal(b.run().stage,'FOREIGN');b.w.close();
 const c=page('zh_HK','<input type="password">','https://secure6.store.apple.com.evil.test/hk-zh/shop/signIn');assert.equal(c.run().stage,'FOREIGN');c.w.close();
});
test('Hong Kong pickup tab is distinct from FAQ and its fulfillment continue button',()=>{
 const a=page('zh_HK','<button aria-pressed="true">我希望送貨</button><button>我會前來取貨</button><button data-autom="fulfillment-continue-button">繼續填寫取貨詳細資料</button><button data-autom="faq-button">送貨及取貨常見疑問</button>');
 const groups=Array.from(a.run().controls,c=>c.group);assert.deepEqual(groups,['','pickup','continue','']);a.w.close();
});
test('Hong Kong pickup search uses the catalog city and cannot fill arbitrary locations',()=>{
 const a=page('zh_HK','<input id="checkout.fulfillment.pickupTab.pickup.storeLocator.searchInput" required><button id="checkout.fulfillment.pickupTab.pickup.storeLocator.search">套用</button><button aria-pressed="true">我會前來取貨</button>');
 a.target.regional_store={address:{city:'香港'}};
 let s=a.run();const field=s.fields[0];assert.equal(field.key,'pickup_location');assert.equal(s.controls[0].group,'store_search');
 const command={kind:'search_location',ref:field.ref,value:'Other city',nonce:'wrong',expected:s.fingerprint};
 assert.equal(a.run(command).result,'refused');assert.equal(a.w.document.querySelector('input').value,'');
 assert.equal(a.run({...command,value:'香港',nonce:'right'}).result,'dispatched');s=a.run();assert.equal(s.fields[0].matches,true);assert.equal(a.w.document.querySelector('input').value,'香港');a.w.close();
});
test('Hong Kong pickup time selector is separate from date radios and contact is not fulfillment',()=>{
 const a=page('zh_HK','<input type="radio" id="bartPickupDateSelector22" value="22"><select id="checkout.fulfillment.pickupTab.pickup.timeSlot.dateTimeSlots.timeSlotValue" data-autom="pickup-availablewindow-dropdown"><option value="22-10:00-10:15">10:00 – 10:15</option></select>');
 assert.deepEqual(Array.from(a.run().controls,c=>c.group),['date','time']);a.w.close();
 const b=page('zh_HK','<input autocomplete="family-name" required><input type="checkbox" id="checkout.pickupContact.selfPickupContact.selfContact.address.isDaytimePhoneSelected"><a data-autom="apple-pickup-policy">查看 Apple 取貨政策</a><button data-autom="continue-button-label">繼續前往付款</button>');
 const s=b.run();assert.equal(s.stage,'CONTACT');assert.deepEqual(Array.from(s.controls,c=>c.group),['','','continue']);b.w.close();
});
test('unselected Hong Kong billing options identify PAYMENT and cannot fill card details',()=>{
 const a=page('zh_HK','<input type="radio" data-autom="checkout-billingOptions-CREDIT" value="CREDIT"><input autocomplete="given-name" required><button data-autom="continue-button-review">檢查你的訂單</button>');
 const s=a.run();assert.equal(s.stage,'PAYMENT');
 assert.equal(a.run({kind:'fill',ref:s.fields[0].ref,expected:s.fingerprint,nonce:'billing-fill'},{given_name:'Test'}).result,'refused');a.w.close();
});
test('Hong Kong checkout hydration waits and store tools do not masquerade as pickup tabs',()=>{
 const a=page('zh_HK','<div>正在載入</div>');assert.equal(a.run().stage,'LOADING');
 a.w.document.querySelector('main').innerHTML='<button>我會前來取貨</button><button data-autom="show-more-stores-button">顯示所有取貨地點</button><button data-autom="fulfillment-pickup-store-search-button">編輯取貨地點</button><button data-autom="fulfillment-continue-button">繼續前往取貨詳情</button><select data-autom="pickup-availablewindow-dropdown"><option value="" disabled>可用時段</option></select>';
 const s=a.run();assert.equal(s.stage,'FULFILLMENT');assert.deepEqual(Array.from(s.controls,c=>c.group),['pickup','more_stores','store_search_edit','continue','time']);assert.equal(s.controls[4].input_type,'select');assert.equal(s.controls[4].options.length,0);a.w.close();
});
test('Hong Kong contact fields outrank fading pickup controls and summary edit links',()=>{
 const a=page('zh_HK','<input autocomplete="family-name" required><button data-autom="checkout-fulfillmentCompanionBar-editLink">Change pickup details</button><button data-autom="continue-button-label">繼續前往付款</button><button data-autom="fulfillment-continue-button">繼續前往取貨詳情</button>');
 const s=a.run();assert.equal(s.stage,'CONTACT');assert.equal(s.controls[0].group,'');a.w.close();
});

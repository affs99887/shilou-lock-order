const {test}=require('node:test'),assert=require('node:assert/strict');
const {JSDOM}=require('jsdom'),fs=require('node:fs'),path=require('node:path');
const script=fs.readFileSync(path.join(__dirname,'vendor/regional_browser.js'),'utf8');
const regions=require('../src/lib/checkout-regions.json');
const catalog=require('../src/lib/checkout-catalog.json');
function page(locale,html,url){
 const r=regions.find(x=>x.locale===locale),part=Object.keys(catalog[locale].products)[0];
 const w=new JSDOM('<main>'+html+'</main>',{url:url||r.base+'/shop/checkout',runScripts:'outside-only'}).window;
 w.HTMLElement.prototype.getClientRects=function(){return this.hidden?[]:[{}]};
 Object.defineProperty(w.document,'readyState',{value:'complete'});w.eval(script);
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
 assert.equal(a.run(command,profile).result,'refused');
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
 const a=page('en_SG','<div>Unrecognised new bag layout</div>','https://www.apple.com/sg/shop/bag');assert.equal(a.run().bag_empty,false);
 a.w.document.querySelector('main').textContent='Your bag is empty.';assert.equal(a.run().bag_empty,true);a.w.close();
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

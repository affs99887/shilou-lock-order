/* Experimental international storefront adapter. No submit/payment operation.
 * Fixed Apple origins, document-local receipts and redacted snapshots only.
 */
function regionalCheckout(request) {
  'use strict';
  const bases={zh_HK:'/hk-zh',zh_TW:'/tw',ja_JP:'/jp',en_SG:'/sg',en_AU:'/au',en_MY:'/my'};
  const t=request.target||{}, prefix=bases[t.checkout_locale];
  if(location.href==='about:blank'||window.__appleCheckoutDeparting)return {stage:'LOADING'};
  if(!prefix||location.protocol!=='https:'||! /^(?:www|secure[0-9]+\.www|secure\.store|store)\.apple\.com$/.test(location.hostname)||!location.pathname.startsWith(prefix+'/shop/'))return {stage:'FOREIGN'};
  const root=document.querySelector('main,[role=main],#main')||document.body;
  if(!root)return {stage:'LOADING'};
  const norm=x=>String(x||'').normalize('NFKC').replace(/\s+/g,'').toLowerCase();
  const visible=e=>!!e&&!e.closest('[hidden],[aria-hidden="true"]')&&!!(e.offsetWidth||e.offsetHeight||e.getClientRects().length);
  const disabled=e=>!!e.disabled||e.getAttribute('aria-disabled')==='true';
  const label=e=>e.getAttribute('aria-label')||[...(e.labels||[])].map(l=>l.textContent).join(' ')||e.textContent||'';
  const selected=e=>!!e.checked||e.getAttribute('aria-checked')==='true'||e.getAttribute('aria-pressed')==='true'||e.getAttribute('aria-selected')==='true';
  const ctx=e=>[e.name,e.id,e.autocomplete,e.getAttribute('data-autom'),e.getAttribute('data-value'),label(e)].filter(Boolean).join(' ');
  const text=root.innerText||root.textContent||'', path=location.pathname.toLowerCase();
  if(/\/404\/?$/.test(path)||/page.*can[’']?t be found|ページが見つか|找不到.*網頁|找不到.*頁面/i.test(text))return {stage:'NOT_FOUND'};
  if(/session_expired/.test(path))return {stage:'SESSION_EXPIRED'};
  if(/access denied|too many requests|參考編號.*拒絕|アクセスが拒否/i.test(text))return {stage:'BLOCKED'};
  if([...document.querySelectorAll('input[type=password],iframe[src*="idmsa.apple.com"],iframe[src*="appleid.apple.com"]')].some(visible)||/\/signin(?:\/|$)/.test(path))return {stage:'LOGIN'};
  // An Edge navigation timeout can leave the previous document visible until
  // the requested storefront commits. Never act on that old document.
  if(request.expected_path&&!location.pathname.startsWith(request.expected_path))return {stage:'LOADING'};
  const registry=window.__apwRegional||(window.__apwRegional={ids:new WeakMap(),refs:new Map(),serial:0,done:new Set(),expected:new Map(),doc:crypto.randomUUID()});
  function ref(e){if(!registry.ids.has(e)){const id=String(++registry.serial);registry.ids.set(e,id);registry.refs.set(id,e);}return registry.ids.get(e);}
  const final=/place\s*(?:your\s*)?order|pay\s*now|buy\s*now|confirm\s*(?:and\s*)?(?:pay|purchase|order)|submit\s*order|立即下單|提交訂單|確認付款|立即付款|今すぐ購入|注文を確定|注文する|購入する|支払いを確定/i;
  const agreement=/agree|accept.*terms|authoris|authorize|subscribe|申請|同意|承諾|契約|申し込|ローン/i;
  const paymentField=/cc-number|cc-csc|cc-exp|credit.?card|card.?number|security.?code|cvv|cvc|カード番号|セキュリティコード/i;
  function field(e){
    const c=ctx(e);
    if(paymentField.test(c)||/password|otp|verification|验证码|驗證碼|認証|one-time-code/i.test(c))return '';
    if(/pickup|retail|store/i.test(c)&&/search|location|postal|zip/i.test(c))return 'pickup_location';
    const rules=[['family_name_kana',/family.*kana|last.*kana|フリガナ.*姓|姓.*フリガナ|セイ/i],['given_name_kana',/given.*kana|first.*kana|フリガナ.*名|名.*フリガナ|メイ/i],['family_name',/family-name|familyname|lastname|last_name|姓氏|名字（姓）/i],['given_name',/given-name|givenname|firstname|first_name|名字|名前/i],['phone',/tel|telephone|mobile|phone|電話/i],['email',/email|e-mail|電郵|電子郵件/i],['postal_code',/postal-code|postalcode|zipcode|zip_code|郵便番号|郵遞區號/i],['province',/address-level1|province|state|都道府県/i],['city',/address-level2|city|locality|市区町村|城市|縣市/i],['district',/address-level3|district|county|區域|地區/i],['address2',/address-line2|address2|apartment|building|建物/i],['address',/street-address|address-line1|address1|street|番地|地址/i]];
    return rules.find(([,pattern])=>pattern.test(c))?.[0]||'';
  }
  const elements=[...root.querySelectorAll('button,[role=button],input[type=radio],[role=radio],input[type=checkbox],select,a[data-autom]')].filter(visible);
  const dimensions=t.regional_product?.dimensions||{};
  function group(e){
    const c=ctx(e), a=e.getAttribute('data-autom')||'';
    if(/quantity/i.test(c)&&e.tagName==='SELECT')return 'quantity';
    if(/pickup.*date|checkin.*date/i.test(c))return 'date';
    if(/pickup.*time|checkin.*time/i.test(c))return 'time';
    if(/store|retail/i.test(c)&&(e.type==='radio'||e.getAttribute('role')==='radio'))return 'store';
    for(const k of Object.keys(dimensions))if(k!=='familyType'&&norm(c).includes(norm(k)))return k;
    if(/applecare/i.test(c))return 'applecare';
    if(/trade.?in/i.test(c))return 'trade_in';
    if(/paymentoption|purchaseoption|full.?price|pay.?in.?full/i.test(c))return 'purchase';
    if(/pickup|pick.?up|取貨|受け取り|受取/i.test(c))return 'pickup';
    if(/add-to-cart|addtobag/i.test(a)||/^(?:add to bag|加入購物袋|加到購物袋|バッグに追加)$/i.test(label(e).trim()))return 'add';
    if(/^(?:check\s*out|結帳|結賬|ご注文手続きへ)$/i.test(label(e).trim())||a==='checkout')return 'checkout';
    if(/remove|移除|削除/i.test(label(e))&&e.closest('[data-autom="bag-item"],[data-autom="bag-item-list-item"],.rs-bag-item'))return 'remove';
    if(/^(?:continue|continue to.*|review order|review your order|繼續|檢查訂單|確認訂單內容|続ける|ご注文の確認)$/i.test(label(e).trim()))return 'continue';
    return '';
  }
  const controls=elements.map(e=>{const g=group(e);const c=ctx(e);return {ref:ref(e),group:g,disabled:disabled(e),selected:selected(e),value:e.value||e.getAttribute('data-value')||'',label:label(e).trim(),matches:g==='store'?new RegExp('(?:^|[^A-Z0-9])'+t.store_id+'(?:$|[^0-9])').test([c,e.value,e.getAttribute('data-store-number')].join(' ')):!!dimensions[g]&&norm([e.value,e.getAttribute('data-value')].join(' ')).split(' ').includes(norm(dimensions[g])),options:e.tagName==='SELECT'?[...e.options].filter(o=>!o.disabled&&!o.parentElement.disabled).map(o=>({value:o.value,label:o.textContent.trim(),selected:o.selected})):[]};});
  controls.forEach((c,i)=>{if(c.group==='store'){const e=elements[i];c.scope=e.name||ref(e.closest('fieldset,[role=radiogroup]')||root);}});
  // Exact dimension comparison (never substring-match black vs spaceblack).
  controls.forEach((c,i)=>{if(dimensions[c.group]){const e=elements[i];c.matches=[e.value,e.getAttribute('data-value')].some(v=>norm(v)===norm(dimensions[c.group]));}});
  const fields=[...root.querySelectorAll('input:not([type=hidden]):not([type=radio]):not([type=checkbox]),select,textarea')].filter(visible).map(e=>({ref:ref(e),key:field(e),disabled:disabled(e),required:!!e.required||e.getAttribute('aria-required')==='true',filled:!!e.value,valid:e.getAttribute('aria-invalid')!=='true'&&(!e.validity||e.validity.valid),matches:!!field(e)&&!!e.value&&norm(e.value)===registry.expected.get(field(e)),options:e.tagName==='SELECT'?[...e.options].filter(o=>!o.disabled).map(o=>({value:o.value,label:o.textContent.trim()})):[]}));
  const rows=[...root.querySelectorAll('[data-autom="bag-item"],[data-autom="bag-item-list-item"],.rs-bag-item')].filter(visible);
  const items=rows.map(row=>{
    const material=[row.getAttribute('data-part-number'),...row.querySelectorAll('[data-part-number],a[href]')].map(x=>typeof x==='string'?x:x?.getAttribute('data-part-number')||x?.getAttribute('href')||'').join(' ');
    let decoded='';try{decoded=decodeURIComponent(material);}catch{return {part:null,quantity:null,applecare:null};}
    const parts=[...new Set([...decoded.matchAll(/\b[A-Z0-9]{5,12}\/A\b/g)].map(m=>m[0]))];
    const q=row.querySelector('select[name*=quantity],select[data-autom*=quantity]');
    const care=[...row.querySelectorAll('[data-autom*=applecare]')].filter(visible).some(e=>/AppleCare\+/.test(e.textContent));
    return {part:parts.length===1?parts[0]:null,quantity:q?Number(q.value):null,applecare:care,quantity_ref:q?ref(q):'',quantity_options:q?[...q.options].filter(o=>!o.disabled).map(o=>o.value):[]};
  });
  const totalNodes=[...root.querySelectorAll('[data-autom="total"],[data-autom="bagtotal"],[data-autom="orderTotal"],[data-autom="order-summary-total"]')].filter(visible);
  const currencies={HKD:/HK\$|HKD/,TWD:/NT\$|TWD/,JPY:/￥|¥|円|JPY/,SGD:/S\$|SGD/,AUD:/A\$|AUD/,MYR:/RM|MYR/,CNY:/RMB|CNY|人民幣|人民币/};
  const priceText=totalNodes.map(e=>e.textContent).join(' ');
  const currency=totalNodes.length>0&&totalNodes.every(e=>currencies[t.currency]?.test(e.textContent)||e.getAttribute('data-currency')===t.currency)&&
    !Object.entries(currencies).some(([code,pattern])=>code!==t.currency&&pattern.test(priceText))?t.currency:null;
  const priceMatches=[...priceText.matchAll(/(?:HK\$|NT\$|S\$|A\$|RM|￥|¥|HKD|TWD|SGD|AUD|MYR|JPY|\$)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/g)].map(m=>Number(m[1].replaceAll(',','')));
  const uniquePrices=[...new Set(priceMatches)];
  const total=totalNodes.length>0&&priceMatches.length===totalNodes.length&&uniquePrices.length===1?uniquePrices[0]:null;
  const errors=[...root.querySelectorAll('[role=alert],.form-message-error')].filter(visible).some(e=>!!e.textContent.trim());
  const availabilityText=[...root.querySelectorAll('[data-autom*=availability],[data-autom*=Availability],[role=alert]')].filter(visible).map(e=>e.textContent).join(' ');
  const soldOut=/currently unavailable|out of stock|no.*available.*pick.?up|暫無供應|無法取貨|無貨可取|在庫がありません|受け取れるストアがありません/i.test(availabilityText);
  const bag_empty=rows.length===0&&/your bag is empty|購物袋是空的|購物袋內沒有商品|バッグに商品がありません|バッグは空です/i.test(text);
  const productParts=[...new Set([...root.querySelectorAll('input[name="product"],input[name="partNumber"],input[name="part"],[data-autom="product-selection"][data-part-number]')].map(e=>e.value||e.getAttribute('data-part-number')).filter(v=>/^[A-Z0-9]{5,12}\/A$/.test(v||'')))];
  const product_part=productParts.length===1?productParts[0]:null;
  const paymentVisible=[...root.querySelectorAll('input')].some(e=>visible(e)&&paymentField.test(ctx(e)))||/payment|billing/.test(path)||/^.*(?:付款方式|付款與帳單|お支払い方法|Payment method).*$/m.test(text);
  let stage=/\/bag\/?$/.test(path)?'BAG':path.includes('/buy-iphone/')?'PRODUCT':paymentVisible?'PAYMENT':controls.some(c=>c.group==='store'||c.group==='pickup')?'FULFILLMENT':path.includes('/checkout')?'CONTACT':'UNKNOWN';
  if(elements.some(e=>final.test(label(e))))stage='REVIEW';
  if(soldOut&&stage!=='REVIEW')stage='SOLD_OUT';
  // Fingerprint covers live values but exposes only a hash, never PII.
  const state=JSON.stringify([path,stage,controls,items,total,currency,errors,product_part,fields.map(f=>[f.ref,f.matches,f.valid]),[...root.querySelectorAll('input,select,textarea')].filter(visible).map(e=>[ref(e),e.value,e.checked])]);
  let hash=2166136261;for(const ch of state){hash^=ch.charCodeAt(0);hash=Math.imul(hash,16777619);}
  const fingerprint=registry.doc+':'+(hash>>>0).toString(16);
  const snapshot={stage,fingerprint,controls,fields,items,total,currency,errors,bag_empty,product_part};
  if(!request.command)return snapshot;
  const cmd=request.command;
  if(cmd.kind==='submit_order'||!cmd.nonce||cmd.expected!==fingerprint)return {result:'refused'};
  if(registry.done.has(cmd.nonce))return {result:'duplicate'};
  const e=registry.refs.get(cmd.ref);
  if(!e||!e.isConnected||!visible(e)||disabled(e)||final.test(label(e))||agreement.test(label(e)))return {result:'refused'};
  const c=controls.find(x=>x.ref===cmd.ref), f=fields.find(x=>x.ref===cmd.ref);
  if(cmd.kind==='click'){
    if(!c||!c.group||c.group==='quantity'||c.group==='remove'&&!t.clear_bag_before_purchase||c.group==='continue'&&['REVIEW','PAYMENT'].includes(stage))return {result:'refused'};
    if(c.group==='add'&&product_part!==t.part_number)return {result:'refused'};
    registry.done.add(cmd.nonce);e.click();return {result:'dispatched'};
  }
  if(cmd.kind==='select'&&e.tagName==='SELECT'){
    const option=[...e.options].filter(o=>!o.disabled&&o.value===cmd.value);
    if(option.length!==1||!c?.group&&!f?.key)return {result:'refused'};
    registry.done.add(cmd.nonce);if(f?.key)registry.expected.set(f.key,norm(cmd.value));e.value=cmd.value;e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));e.blur();return {result:'dispatched'};
  }
  if(cmd.kind==='fill'&&f?.key&&f.key!=='pickup_location'&&stage!=='PAYMENT'&&stage!=='REVIEW'){
    const value=request.profile?.[f.key];if(!value||e.tagName==='SELECT')return {result:'refused'};
    registry.done.add(cmd.nonce);registry.expected.set(f.key,norm(value));const proto=e.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:HTMLInputElement.prototype;
    Object.getOwnPropertyDescriptor(proto,'value').set.call(e,value);
    e.dispatchEvent(new Event('input',{bubbles:true}));e.dispatchEvent(new Event('change',{bubbles:true}));e.blur();return {result:'dispatched'};
  }
  return {result:'refused'};
}

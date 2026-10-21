const {test}=require('node:test'),assert=require('node:assert/strict');
const {JSDOM}=require('jsdom'),fs=require('node:fs'),path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'vendor/checkout_browser.js'),'utf8');
function page(extra=''){
 const w=new JSDOM('<main><h1>购买 iPhone 18 Pro</h1><label><input type="radio" name="model" checked>iPhone 18 Pro</label><label><input type="radio" name="color" disabled '+extra+'>银色</label><label><input type="radio" name="color">冰川蓝色</label><label><input type="radio" name="capacity" disabled>512GB</label></main>',{url:'https://www.apple.com.cn/shop/buy-iphone/iphone-18-pro',runScripts:'outside-only'}).window;
 w.HTMLElement.prototype.getClientRects=function(){return this.hidden?[]:[{}]};
 Object.defineProperty(w.document,'readyState',{value:'complete'});w.performance.getEntriesByType=()=>[];w.eval(code);return w;
}
test('disabled target color with enabled siblings is not sufficient evidence of no stock',()=>{
 const w=page();const s=w.appleCheckout({target:{name:'iPhone 18 Pro',color:'银色',capacity:'512GB'}});
 assert.equal(s.stage,'PRODUCT');assert.equal(s.variant_unavailable,false);assert.equal(s.sold_out,false);w.close();
});
test('explicit scoped stock metadata still identifies a disabled variant',()=>{
 const w=page('data-availability="out-of-stock"');
 assert.equal(w.appleCheckout({target:{name:'iPhone 18 Pro',color:'银色',capacity:'512GB'}}).variant_unavailable,true);w.close();
});
test('empty-cart headings are recognized without requiring pre-added products',()=>{
 for(const text of ['你的购物袋是空的。','购物袋中没有商品','你的购物袋中还没有商品。','购物袋内没有任何商品','Your bag is empty.']){
  const w=page();w.history.replaceState({},'','/shop/bag');
  w.document.querySelector('main').innerHTML=`<h1>${text}</h1>`;
  const s=w.appleCheckout({target:{}});
  assert.equal(s.stage,'BAG',text);assert.equal(s.empty_bag,true,text);w.close();
 }
});
test('loading cart and empty-cart help text are not proof of an empty cart',()=>{
 for(const html of ['<h1>购物袋</h1>','<h1>购物袋</h1><p>购物袋中没有商品时，可以继续选购。</p>','<h1 hidden>你的购物袋是空的。</h1>']){
  const w=page();w.history.replaceState({},'','/shop/bag');w.document.querySelector('main').innerHTML=html;
  const s=w.appleCheckout({target:{}});assert.equal(s.empty_bag,false);assert.equal(s.stage,'LOADING');w.close();
 }
});

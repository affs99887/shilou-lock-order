// 取货地区选择：官网默认定位到别的城市时，要能点回省份重新选到目标城市。
const {test}=require('node:test'),assert=require('node:assert/strict');
const {JSDOM}=require('jsdom'),fs=require('node:fs'),path=require('node:path');
const code=fs.readFileSync(path.join(__dirname,'vendor/checkout_browser.js'),'utf8');
const target={fulfillment:'pickup',pickup_region:{province:'上海',city:'上海',district:'普陀区'}};

// 地区选择器展开时的页面：三个层级标签，active 指当前显示哪一层的列表。
function page({summary,tabs,active,options}){
 const tabHtml=tabs.map(([key,value,name],i)=>`<div role="tab" aria-selected="${key===active}" aria-controls="panel-${key}">${value} ${name}</div>`).join('');
 const panel=`<div id="panel-${active}">${options.map(o=>`<button type="button">${o}</button>`).join('')}</div>`;
 const w=new JSDOM(`<main><h1>你希望如何收到订单商品？</h1><input type="radio" id="m" checked><label for="m">我要取货</label>
  <p>显示此地附近的零售店</p><button aria-expanded="true">编辑邮编或城市、区县 ${summary}</button>${tabHtml}${panel}</main>`,
  {url:'https://www.apple.com.cn/shop/checkout',runScripts:'outside-only'}).window;
 w.HTMLElement.prototype.getClientRects=function(){return this.hidden?[]:[{}]};
 Object.defineProperty(w.document,'readyState',{value:'complete'});w.performance.getEntriesByType=()=>[];w.eval(code);
 const s=w.appleCheckout({target});w.close();return s.region_picker;
}
const nanjing=[['province','江苏','省/市/自治区'],['city','南京','城镇/城市'],['district','玄武区','区']];

test('default location in another province goes back to the province tab instead of giving up',()=>{
 const r=page({summary:'江苏 南京 玄武区',tabs:nanjing,active:'district',options:['玄武区','秦淮区','鼓楼区']});
 assert.equal(r.matched,false);
 assert.equal(r.next?.key,'region_pickup_tab_province');
 assert.notEqual(r.reason,'missing');
});
test('the province list then picks Shanghai directly',()=>{
 const r=page({summary:'江苏 南京 玄武区',tabs:nanjing,active:'province',options:['江苏','上海','浙江']});
 assert.equal(r.next?.key,'region_pickup_province');
});
test('a city list from another province also goes back to the province tab',()=>{
 const r=page({summary:'江苏 南京 玄武区',tabs:nanjing,active:'city',options:['南京','苏州','无锡']});
 assert.equal(r.next?.key,'region_pickup_tab_province');
});
test('Shanghai districts without the target district are not clicked automatically',()=>{
 // 上层已经是上海，只是列表里没有目标区：不自动点别的区，也不来回切换；稍后交给本人选。
 const shanghai=[['province','上海','省/市/自治区'],['city','上海','城镇/城市'],['district','黄浦区','区']];
 const r=page({summary:'上海 黄浦区',tabs:shanghai,active:'district',options:['黄浦区','徐汇区']});
 assert.equal(r.next,null);
 assert.equal(r.recovery?.key,'region_pickup_tab_province');
});

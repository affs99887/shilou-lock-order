// Isolated UI fixture: no native IPC, no Apple requests, no user settings.
import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { resolve, extname } from 'node:path';
import assert from 'node:assert/strict';
const out=resolve('ui-review/auto.5');await mkdir(out,{recursive:true});
const server=createServer(async(req,res)=>{try{const path=resolve('dist','.'+(req.url==='/'?'/index.html':req.url));if(!path.startsWith(resolve('dist')+'/'))throw Error('path');res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css'})[extname(path)]||'application/octet-stream');res.end(await readFile(path));}catch{res.writeHead(404);res.end();}});
await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
try{
const context=await browser.newContext({viewport:{width:1280,height:900},deviceScaleFactor:1});
await context.route('**/*',route=>route.request().url().startsWith(origin)?route.continue():route.abort());
await context.addInitScript(()=>{
 const callbacks=new Map();const events=new Map();let id=0;
 const stores=[{number:'TEST1',name:'武汉',title:'Apple 武汉'},{number:'TEST2',name:'长沙',title:'Apple 长沙'}];
 const products=['星光白','夜空色'].map((color,i)=>({partNumber:`DEMO${i}`,category:'iphone',family:'iPhone Duo',capacity:'256GB',color,title:`iPhone Duo 256GB ${color}`}));
 const rows=stores.flatMap((s,j)=>products.map((p,i)=>({target:{locale:'zh_CN',storeNumber:s.number,storeTitle:s.title,partNumber:p.partNumber,productName:p.title},availability:i===0&&j===0?{kind:'in_stock'}:i===1&&j===1?{kind:'unknown',reason:'transport',detail:'演示：连接暂不可用'}:{kind:'out_of_stock'},lastCheckedMs:Date.now(),consecutiveFailures:0})));
 const settings={locale:'zh_CN',targets:rows.map(r=>r.target),intervalSeconds:30,barkUrl:'',feishuWebhook:'',proxies:[],soundEnabled:true,openBagOnHit:false};
 const plan={max_price:20000,quantity:1,applecare:'none',payment:'支付宝',financing:'official',installment_option:'微信分付',installment_months:24,pickup_dates:['2026-10-23'],clear_bag_before_purchase:true,auto_submit_order:false};
 const status={armed:false,busy:false,stopping:false,mode:'idle',stage:'idle',reason:'尚未启用自动选购',elapsed:0,plan,submissionEnabled:false};
 window.__UI_CALLS__=[];
 window.__TAURI_INTERNALS__={transformCallback(fn){callbacks.set(++id,fn);return id;},unregisterCallback(){},invoke:async(cmd,args)=>{
 window.__UI_CALLS__.push({cmd,args});
 if(cmd==='plugin:event|listen'){events.set(args.event,args.handler);return ++id;}
 const values={list_regions:[{locale:'zh_CN',title:'中国大陆'}],list_categories:[{value:'iphone',title:'iPhone'}],get_settings:settings,get_snapshot:rows,is_running:false,list_stores:stores,list_products:products};
 if(cmd in values)return values[cmd];
 if(cmd==='save_settings'){Object.assign(settings,args.settings);return structuredClone(settings);}
 if(cmd==='checkout_command'){if(args.op==='configure')status.plan=args.payload.plan;return structuredClone(status);}
 return null;
 }};
 window.__EMIT__=(name,payload)=>callbacks.get(events.get(name))?.({event:name,payload});
});
const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto(origin);await page.getByText('尚未启用自动选购',{exact:false}).waitFor();
await page.evaluate(()=>window.__EMIT__('watcher://notice','界面演示数据，不代表实际库存。'));
await page.screenshot({path:out+'/center.png',fullPage:true});
for(const label of ['监控目标','选购方案','手机提醒']){await page.getByRole('button',{name:label,exact:true}).click();await page.screenshot({path:out+'/'+label+'.png',fullPage:true});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${label} overflows`);}
assert.equal(await page.locator('input:visible').count(),1);
await page.getByLabel('Bark 链接地址').fill('https://api.day.app/DemoKey/title/body?group=demo');
await page.getByRole('button',{name:'保存并测试提醒',exact:true}).click();await page.getByText('测试请求已发送，请查看手机通知').waitFor();
assert.equal(await page.getByLabel('Bark 链接地址').inputValue(),'https://api.day.app/DemoKey');
await page.getByRole('button',{name:'选购方案',exact:true}).click();await page.getByRole('spinbutton',{name:'总预算（含保障）'}).fill('18000');
await page.getByRole('button',{name:'捡漏中心',exact:true}).click();await page.getByRole('button',{name:'选购方案',exact:true}).click();assert.equal(await page.getByRole('spinbutton',{name:'总预算（含保障）'}).inputValue(),'18000','draft survives navigation');
assert.equal(await page.evaluate(()=>window.__UI_CALLS__.some(c=>c.cmd==='checkout_command'&&c.args.op!=='status')),false,'navigation never saves/arms/submits');
await page.getByRole('button',{name:'全款',exact:true}).click();assert.equal(await page.getByLabel('全款渠道').isEnabled(),true);await page.getByLabel('全款渠道').selectOption('微信支付');await page.screenshot({path:out+'/全款.png',fullPage:true});
await page.getByRole('button',{name:'分期',exact:true}).click();assert.equal(await page.getByRole('button',{name:'24 期',exact:true,pressed:true}).count(),1);
await page.getByRole('checkbox', {name:'自动下单，等待本人支付', exact:false}).check();
page.on('dialog', () => { throw Error('native confirmation must not be used'); });
await page.getByRole('button',{name:'启用有货自动选购',exact:true}).click();
await page.getByRole('dialog').waitFor();
await page.getByRole('button',{name:'取消',exact:true}).click();
assert.equal(await page.evaluate(()=>window.__UI_CALLS__.filter(c=>c.cmd==='checkout_command'&&c.args.op==='arm').length),0);
await page.getByRole('button',{name:'启用有货自动选购',exact:true}).click();
await page.getByRole('button',{name:'确认启用并授权下单',exact:true}).click();
await page.getByRole('dialog').waitFor({state:'hidden'});
assert.equal(await page.evaluate(()=>window.__UI_CALLS__.filter(c=>c.cmd==='checkout_command'&&c.args.op==='arm'&&c.args.payload.confirmSubmission===true).length),1);
await page.setViewportSize({width:1000,height:640});for(const label of ['捡漏中心','监控目标','选购方案','手机提醒']){await page.getByRole('button',{name:label,exact:true}).click();assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`${label} min-size overflow`);}
await page.getByRole('button',{name:'捡漏中心',exact:true}).click();await page.screenshot({path:out+'/center-1000.png',fullPage:true});assert.deepEqual(errors,[]);console.log('UI preview passed: four pages, two window sizes, draft retention, in-app confirmation cancel/accept, mocked IPC only, no external requests.');
}finally{await browser.close();server.close();}

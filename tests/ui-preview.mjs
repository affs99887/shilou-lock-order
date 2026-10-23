// Isolated UI fixture: no native IPC, no Apple requests, no user settings.
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { resolve, extname, sep } from 'node:path';
import assert from 'node:assert/strict';
const out=resolve('ui-review/single-page');await mkdir(out,{recursive:true});
const screenshot=(target,file,options={})=>process.env.UI_SCREENSHOTS==='0'?Promise.resolve():target.screenshot({path:out+'/'+file,...options});
const server=createServer(async(req,res)=>{try{const path=resolve('dist','.'+(new URL(req.url,'http://localhost').pathname==='/'?'/index.html':new URL(req.url,'http://localhost').pathname));if(!path.startsWith(resolve('dist')+sep))throw Error('path');res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css'})[extname(path)]||'application/octet-stream');res.end(await readFile(path));}catch{res.writeHead(404);res.end();}});
await new Promise(r=>server.listen(0,'127.0.0.1',r));const origin=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({executablePath:process.env.CHROME_PATH || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
try{
const context=await browser.newContext({viewport:{width:1280,height:900},deviceScaleFactor:1,colorScheme:'light'});
await context.route('**/*',route=>route.request().url().startsWith(origin)?route.continue():route.abort());
await context.addInitScript(()=>{
 const callbacks=new Map();const events=new Map();let id=0;
 const stores=['上海-环球港','上海-香港广场','上海-五角场','上海-上海环贸 iapm','上海-浦东','上海-静安','上海-南京东路'].map((title,i)=>({number:`TEST${i+1}`,name:title,title}));
 const products=['星光白','夜空色'].map((color,i)=>({partNumber:`DEMO${i}`,category:'iphone',family:'iphoneduo',capacity:'1TB',color,title:`iPhone Duo 1TB ${color}`}));
 const rows=new URLSearchParams(location.search).has('empty') ? [] : stores.map((s,j)=>{const p=products[j%2];return ({target:{locale:'zh_CN',storeNumber:s.number,storeTitle:s.title,partNumber:p.partNumber,productName:p.title},availability:j===0?{kind:'in_stock'}:j===3?{kind:'unknown',reason:'transport',detail:'演示：连接暂不可用'}:j===5?{kind:'unknown',reason:'not_yet_checked'}:{kind:'out_of_stock'},lastCheckedMs:Date.now(),consecutiveFailures:0});});
 const settings={locale:'zh_CN',targets:rows.map(r=>r.target),intervalSeconds:60,barkUrl:'',feishuWebhook:'',proxies:['http://127.0.0.1:18001','http://127.0.0.1:18002'],soundEnabled:false,openBagOnHit:false};
 const plan={max_price:20000,quantity:1,applecare:'none',payment:'支付宝',financing:'official',installment_option:'微信分付',installment_months:24,pickup_dates:['2026-10-23'],clear_bag_before_purchase:true,auto_submit_order:false};
 const status={armed:false,busy:false,stopping:false,mode:'idle',stage:'idle',reason:'尚未启用自动选购',elapsed:0,plan,submissionEnabled:false};
 window.__UI_CALLS__=[]; window.__CHECKOUT_STATUS__=status;
 window.__TAURI_EVENT_PLUGIN_INTERNALS__={unregisterListener(_event,eventId){events.delete(eventId);}};
 window.__TAURI_INTERNALS__={transformCallback(fn){callbacks.set(++id,fn);return id;},unregisterCallback(callbackId){callbacks.delete(callbackId);},invoke:async(cmd,args)=>{
 window.__UI_CALLS__.push({cmd,args});
 if(cmd==='plugin:event|listen'){events.set(++id,{event:args.event,handler:args.handler});return id;}
 if(cmd==='plugin:event|unlisten'){events.delete(args.eventId);return null;}
 const values={list_regions:[{locale:'zh_CN',title:'中国大陆'}],list_categories:[{value:'iphone',title:'iPhone'}],get_settings:settings,get_snapshot:rows,is_running:false,list_stores:stores,list_products:products};
 if(cmd in values)return values[cmd];
 if(cmd==='get_community_status')return {running:false,lastCheckedMs:null,lastSignal:null,error:null,nextCheckInSecs:null};
 if(cmd==='save_settings'){Object.assign(settings,args.settings);return structuredClone(settings);}
 if(cmd==='checkout_command'){if(args.op==='configure')status.plan=args.payload.plan;if(args.op==='arm'){status.armed=true;status.reason='已启用，等待命中';}if(args.op==='stop'){status.armed=false;status.reason='已停止选购';}if(args.op==='end_run')Object.assign(status,{busy:false,armed:status.autoPaused,mode:'stopped',singleRun:null,autoPaused:false,reason:'已结束本次选购。自动选购已恢复，有货后自动选购',lastResult:'已结束本次选购。自动选购已恢复，有货后自动选购'});if(args.op!=='status')window.__EMIT__('checkout://status',structuredClone(status));return structuredClone(status);}
 return null;
 }};
 window.__EMIT__=(name,payload)=>{for(const [eventId,listener] of events)if(listener.event===name)callbacks.get(listener.handler)?.({event:name,id:eventId,payload});};
});
const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto(origin);
await page.getByRole('heading',{name:'关注的库存'}).waitFor();
await page.getByRole('button',{name:'立即选购',exact:true}).waitFor();
const noOverflow=async()=>assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,'page must not overflow horizontally');
const targets=page.getByRole('complementary',{name:'添加监控目标'});
const inventory=page.getByTestId('inventory-page');
const table=page.getByRole('region',{name:'监控型号与库存状态'});
const targetBox=await targets.boundingBox(), tableBox=await inventory.boundingBox();
assert.ok(targetBox.x+targetBox.width<tableBox.x,'target selection and inventory share a row');
assert.ok(Math.abs(targetBox.y-tableBox.y)<2,'panels align at the top');
assert.ok((await table.boundingBox()).height>=320,'inventory keeps its readable height');
assert.equal(await page.getByRole('navigation').count(),0);
assert.equal(await page.getByLabel('总预算（含保障）',{exact:false}).isVisible(),false);
for(const name of ['启用有货自动选购','手动开始选购','测试演练','登录 Apple 账户'])assert.equal(await page.getByRole('button',{name,exact:true}).isVisible(),true,name+' stays on the page while settings are closed');
// 一屏布局：自动选购在最上面，日志一直展开并贴在窗口底部，页面本身不上下滚动。
const checkoutBox=await page.getByRole('region',{name:'自动选购'}).boundingBox();
const logs=page.getByRole('region',{name:'查询日志'});
assert.ok(checkoutBox.y+checkoutBox.height<targetBox.y,'checkout strip sits above targets and inventory');
assert.equal(await logs.isVisible(),true,'logs are open without a toggle');
const logsBox=await logs.boundingBox(), viewport=page.viewportSize();
assert.ok(logsBox.y>tableBox.y+tableBox.height-1,'logs sit below the inventory');
assert.ok(viewport.height-(logsBox.y+logsBox.height)<=24,'logs sit at the bottom of the window');
assert.equal(await page.evaluate(()=>document.documentElement.scrollHeight>innerHeight+1),false,'the page itself does not scroll vertically');
await noOverflow();
// 自动选购只占一小条：状态、按钮、进度各一行；当下能用的按钮排成一行，不会折成两行分不清主次。
assert.ok(checkoutBox.height<=170,`checkout strip stays compact (${checkoutBox.height}px)`);
const actionRows=()=>page.evaluate(()=>new Set([...document.querySelectorAll('.checkout-actions > button')].filter(b=>b.offsetParent).map(b=>Math.round(b.getBoundingClientRect().top))).size);
assert.equal(await actionRows(),1,'action buttons share one row');
assert.ok(targetBox.width>=300,'the add-monitor panel is wide enough for two fields per row');
for(const [width,height] of [[1000,800],[1280,760]]){
 await page.setViewportSize({width,height});
 assert.equal(await actionRows(),1,`action buttons share one row at ${width}px`);
 // 添加监控：标题和「添加」按钮固定在框内，选择项放不下时只有中间滚动。
 const panel=await targets.boundingBox(), add=await targets.getByRole('button',{name:'添加',exact:true}).boundingBox();
 assert.ok(add.y>=panel.y&&add.y+add.height<=panel.y+panel.height,`add button stays inside the panel at ${width}x${height}`);
 assert.equal(await targets.evaluate(e=>e.scrollHeight>e.clientHeight),false,'the panel itself never scrolls the add button away');
 await noOverflow();
}
await screenshot(page,'short-window.png');
await page.setViewportSize({width:1280,height:900});
await screenshot(page,'light.png',{fullPage:true});
await page.emulateMedia({colorScheme:'dark'});
await screenshot(page,'dark.png',{fullPage:true});
await page.emulateMedia({colorScheme:'light'});

// 有货行的「立即选购」点一下直接发出选购，不弹确认窗。
await page.getByRole('button',{name:'立即选购',exact:true}).click();
await page.waitForFunction(()=>window.__UI_CALLS__.some(c=>c.cmd==='checkout_command'&&c.args.op==='purchase'));
assert.equal(await page.getByRole('dialog').count(),0,'row purchase opens no confirmation dialog');

// Multi-select a real combination from the isolated catalog; folding must keep it.
async function pick(name,option){await page.getByRole('combobox',{name,exact:true}).click();await page.getByRole('option',{name:option,exact:true}).click();await page.keyboard.press('Escape');}
await pick('选择机型','iPhone Duo');
await pick('选择容量','1TB');
await pick('选择颜色','星光白');
await pick('选择自提门店','上海-环球港');
assert.equal(await targets.getByRole('button',{name:'添加',exact:true}).isEnabled(),true);
await page.getByRole('button',{name:'手机提醒',exact:true}).click();
await page.getByLabel('Bark 链接地址').fill('https://api.day.app/DemoKey/title/body?group=demo');
await page.getByRole('button',{name:'关闭手机提醒',exact:true}).click();
assert.equal(await page.getByLabel('Bark 链接地址').isVisible(),false);
assert.equal(await targets.getByRole('button',{name:'添加',exact:true}).isEnabled(),true);
await page.getByRole('button',{name:'手机提醒',exact:true}).click();
assert.equal(await page.getByLabel('Bark 链接地址').inputValue(),'https://api.day.app/DemoKey/title/body?group=demo');
await screenshot(page,'settings.png',{fullPage:true});
await page.getByRole('button',{name:'保存并测试提醒',exact:true}).click();
await page.getByText('测试请求已发送，请查看手机通知').waitFor();
assert.equal(await page.getByLabel('Bark 链接地址').inputValue(),'https://api.day.app/DemoKey');
await page.keyboard.press('Escape');
assert.equal(await page.getByLabel('Bark 链接地址').isVisible(),false,'Esc closes the settings modal');

const callsBeforePlan=await page.evaluate(()=>window.__UI_CALLS__.length);
await page.getByRole('button',{name:'方案设置',exact:true}).click();
await page.getByRole('spinbutton',{name:'总预算（含保障）'}).fill('18000');
await page.getByLabel('名字',{exact:true}).fill('演示');
await page.getByRole('button',{name:'关闭选购方案',exact:true}).click();
assert.equal(await page.getByLabel('名字',{exact:true}).isVisible(),false);
await page.getByRole('button',{name:'方案设置',exact:true}).click();
await screenshot(page,'plan-modal.png');
assert.equal(await page.getByRole('spinbutton',{name:'总预算（含保障）'}).inputValue(),'18000');
assert.equal(await page.getByLabel('名字',{exact:true}).inputValue(),'演示');
assert.equal(await page.evaluate(from=>window.__UI_CALLS__.slice(from).some(c=>c.cmd==='checkout_command'&&c.args.op!=='status'),callsBeforePlan),false,'folding never reads profiles, saves, arms or submits');
await page.getByRole('button',{name:'全款',exact:true}).click();
assert.equal(await page.getByLabel('全款渠道').isEnabled(),true);
await page.getByLabel('全款渠道').selectOption('微信支付');
await page.getByRole('button',{name:'分期',exact:true}).click();
await page.emulateMedia({colorScheme:'dark'});
const installments=page.getByRole('group',{name:'分期付款方案 · 点击期数即可设置'});
assert.notEqual(await installments.locator('div').first().evaluate(e=>getComputedStyle(e).backgroundColor),'rgb(255, 255, 255)');
await screenshot(installments,'dark-installments.png');
await noOverflow();
await page.emulateMedia({colorScheme:'light'});
await page.getByRole('checkbox',{name:'自动下单，等待本人支付',exact:false}).check();
await page.getByRole('button',{name:'关闭选购方案',exact:true}).click();
page.on('dialog',()=>{throw Error('native confirmation must not be used');});
// 一次点击直接启用：不弹二次确认，带上下单授权，并且接着开始库存监测。
await page.getByRole('button',{name:'启用有货自动选购',exact:true}).click();
await page.waitForFunction(()=>window.__UI_CALLS__.some(c=>c.cmd==='start_watching'));
assert.equal(await page.getByRole('dialog').count(),0,'arming opens no confirmation dialog');
assert.equal(await page.evaluate(()=>window.__UI_CALLS__.filter(c=>c.cmd==='checkout_command'&&c.args.op==='arm'&&c.args.payload.confirmSubmission===true).length),1);
assert.ok(await page.evaluate(()=>{const calls=window.__UI_CALLS__.map(c=>c.cmd==='checkout_command'?c.args.op:c.cmd);return calls.indexOf('arm')<calls.indexOf('start_watching');}),'monitoring starts after arming');
await page.evaluate(()=>window.__EMIT__('checkout://status',{...window.__CHECKOUT_STATUS__,armed:true,reason:'已启用，等待命中'}));
await page.getByRole('button',{name:'停止自动选购',exact:true}).waitFor();
assert.equal(await page.getByRole('button',{name:'停止自动选购',exact:true}).isVisible(),true,'stop stays on the page while the plan modal is closed');
assert.equal(await page.getByRole('button',{name:'手动开始选购',exact:true}).isEnabled(),true,'manual start works while auto checkout waits');
// 自动选购开着时手动开始，需要本人处理而暂停：主按钮是「继续」，另一个只结束这一次，自动选购随后恢复。
await page.evaluate(()=>window.__EMIT__('checkout://status',Object.assign(window.__CHECKOUT_STATUS__,{armed:false,busy:true,mode:'attention',singleRun:'manual',autoPaused:true,reason:'需要人工处理：LOGIN'})));
await page.getByRole('button',{name:'结束本次选购',exact:true}).waitFor();
assert.equal(await page.getByRole('button',{name:'人工处理后继续',exact:true}).isVisible(),true);
assert.equal(await page.getByRole('button',{name:'启用有货自动选购',exact:true}).count(),0,'start actions leave the row while a run is going');
assert.equal(await actionRows(),1);
await page.getByText('自动选购暂停中，结束后恢复').waitFor();
await screenshot(page.getByRole('region',{name:'自动选购'}),'checkout-paused-run.png');
await page.getByRole('button',{name:'结束本次选购',exact:true}).click();
await page.waitForFunction(()=>window.__UI_CALLS__.some(c=>c.cmd==='checkout_command'&&c.args.op==='end_run'));
assert.equal(await page.evaluate(()=>window.__UI_CALLS__.some(c=>c.cmd==='checkout_command'&&c.args.op==='stop')),false,'ending one run never sends the stop that turns auto checkout off');
await page.getByText('已结束本次选购。自动选购已恢复，有货后自动选购').waitFor();
await page.getByRole('button',{name:'停止自动选购',exact:true}).waitFor();
await page.evaluate(()=>{Object.assign(window.__CHECKOUT_STATUS__,{armed:false,lastResult:''});window.__EMIT__('checkout://status',window.__CHECKOUT_STATUS__);});

await page.evaluate(()=>window.__EMIT__('watcher://notice','界面演示数据，不代表实际库存。'));
assert.equal(await page.getByRole('button',{name:'查询日志',exact:true}).count(),0,'logs have no fold toggle');
await page.getByText('界面演示数据，不代表实际库存。',{exact:false}).waitFor();
for(const colorScheme of ['light','dark']){
 await page.emulateMedia({colorScheme});
 await page.setViewportSize({width:1000,height:640});
 await noOverflow();assert.ok((await table.boundingBox()).height>=320);
 await page.evaluate(()=>window.scrollTo(0,0));
 await screenshot(page,`${colorScheme}-1000.png`,{fullPage:true});
}
await page.goto(origin+'/?empty=1');
await page.getByText('从一个心仪的型号开始').waitFor();
assert.equal(await page.getByRole('button',{name:'开始监测',exact:true}).isEnabled(),false);
await screenshot(page,'empty.png',{fullPage:true});
assert.deepEqual(errors,[]);
console.log('UI preview passed: one-screen layout with a compact checkout strip on top and logs docked at the bottom, both themes, 1280/1000 widths, state-dependent action buttons on one row, add button fixed in the add-monitor panel, target/Bark/contact/plan draft retention in modals, one-click arming that also starts monitoring, ending a paused manual run without turning auto checkout off, one-click row purchase. Mocked IPC only; external requests blocked.');
}finally{await browser.close();server.close();}

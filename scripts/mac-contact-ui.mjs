// Production frontend, isolated Mac first-use state. No Apple or native purchase requests.
import { chromium } from 'playwright';
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { resolve, extname } from 'node:path';
import assert from 'node:assert/strict';

const out=resolve(process.env.APW_UI_OUTPUT||'ui-review/auto.8');await mkdir(out,{recursive:true});
const root=resolve('dist');
const server=createServer(async(req,res)=>{try{
 const path=resolve(root,'.'+(req.url==='/'?'/index.html':req.url));if(!path.startsWith(root+'/'))throw Error('path');
 res.setHeader('Content-Type',({'.html':'text/html','.js':'text/javascript','.css':'text/css'})[extname(path)]||'application/octet-stream');res.end(await readFile(path));
}catch{res.writeHead(404);res.end();}});
await new Promise(r=>server.listen(0,'127.0.0.1',r));
const origin=`http://127.0.0.1:${server.address().port}`;
const browser=await chromium.launch({executablePath:'/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',headless:true});
try{
 const context=await browser.newContext({viewport:{width:1280,height:900},deviceScaleFactor:1});
 await context.route('**/*',r=>r.request().url().startsWith(origin)?r.continue():r.abort());
 await context.addInitScript(()=>{
  const callbacks=new Map(),events=new Map();let id=0,saved=null;
  const target={locale:'zh_CN',storeNumber:'R683',storeTitle:'Apple 武汉',partNumber:'DEMO',productName:'iPhone 17 256GB 白色'};
  const settings={locale:'zh_CN',targets:[target],intervalSeconds:30,barkUrl:'',feishuWebhook:'',proxies:[],soundEnabled:false,openBagOnHit:false};
  const plan={max_price:20000,quantity:1,applecare:'none',payment:'支付宝',financing:'full',installment_option:'微信分付',installment_months:24,pickup_date_policy:'by_model',pickup_dates:['2026-10-23','2026-10-24','2026-10-25'],clear_bag_before_purchase:true,auto_submit_order:true};
  const status={platform:'macos',armed:false,busy:false,stopping:false,mode:'stopped',stage:'IDLE',reason:'未开始',elapsed:0,plan,submissionEnabled:false,error:'',profileRequired:false,activationPending:false};
  const emit=()=>callbacks.get(events.get('checkout://status'))?.({event:'checkout://status',payload:structuredClone(status)});
  window.__UI_INTERVENE__=(kind)=>{status.intervention=kind?{kind,title:'新加坡 · 请手动选择付款／分期方案',body:'请在 Safari 本地区 Apple 官网本人选择付款或分期；未提交订单、未付款。'}:null;emit();};
  window.__UI_OPS__=[];
  window.__TAURI_INTERNALS__={transformCallback(fn){callbacks.set(++id,fn);return id;},unregisterCallback(){},invoke:async(cmd,args)=>{
   if(cmd==='plugin:event|listen'){events.set(args.event,args.handler);return ++id;}
   const values={list_regions:[{locale:'zh_CN',title:'中国大陆'}],list_categories:[{value:'iphone',title:'iPhone'}],get_settings:settings,get_snapshot:[{target,availability:{kind:'out_of_stock'},lastCheckedMs:Date.now(),consecutiveFailures:0}],is_running:false,list_stores:[{number:'R683',name:'武汉',title:'Apple 武汉'}],list_products:[{partNumber:'DEMO',category:'iphone',family:'iPhone 17',capacity:'256GB',color:'白色',title:target.productName}]};
   if(cmd in values)return structuredClone(values[cmd]);
   if(cmd!=='checkout_command')return null;
   window.__UI_OPS__.push(args.op);
   if(args.op==='configure')status.plan=structuredClone(args.payload.plan);
   if(args.op==='read_profile'){if(!saved)throw '尚无已保存资料，请填写后保存';return structuredClone(saved);}
   if(args.op==='save_profile'){
    saved=structuredClone(args.payload.profile);status.busy=true;
    setTimeout(()=>{Object.assign(status,{busy:false,error:'',profileRequired:false,reason:'联系资料已加密保存并核对'});emit();},30);
   }
   if(args.op==='arm'){
    assertConfirmation(args.payload.confirmSubmission);
    Object.assign(status,{busy:true,activationPending:true});
    setTimeout(()=>{
     Object.assign(status,{busy:false,activationPending:false,armed:!!saved,submissionEnabled:!!saved,profileRequired:!saved,error:saved?'':'请在「选购方案 → 联系与收货资料」填写并保存资料。',reason:saved?'等待新库存；请开启上方库存监控':'请先填写并保存你自己的收货资料'});emit();
    },30);
   }
   if(args.op==='stop'){Object.assign(status,{armed:false,busy:false,stopping:false,submissionEnabled:false,reason:'已停止'});emit();}
   return structuredClone(status);
  }};
  function assertConfirmation(value){if(value!==true)throw Error('Submission confirmation missing');}
 });
 const page=await context.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(origin);
 await page.getByTestId('pickup-rule-summary').waitFor();
 assert.match(await page.getByTestId('pickup-rule-summary').innerText(),/Duo 仅限 2026-10-23、2026-10-24、2026-10-25；其他型号最早可取/);
 await page.getByRole('button',{name:'选购方案',exact:true}).click();
 assert.equal(await page.getByLabel('取货日期策略',{exact:true}).inputValue(),'by_model');
 assert.equal(await page.getByLabel('选购国家／地区',{exact:true}).locator('option').count(),7);
 for(let i=1;i<=3;i++)assert.equal(await page.getByLabel(`Duo 取货日期 ${i}`,{exact:true}).inputValue(),`2026-10-${22+i}`);
 await page.getByLabel('取货日期策略',{exact:true}).selectOption('earliest');
 assert.equal(await page.locator('input[type=date]').count(),0);
 await page.getByLabel('取货日期策略',{exact:true}).selectOption('by_model');
 for(let i=1;i<=3;i++)assert.equal(await page.getByLabel(`Duo 取货日期 ${i}`,{exact:true}).inputValue(),`2026-10-${22+i}`);
 await page.screenshot({path:out+'/00-model-pickup-rules.png',fullPage:true});
 await page.getByLabel('姓氏',{exact:true}).waitFor();
 assert.equal(await page.getByLabel('姓氏',{exact:true}).inputValue(),'');
 assert.equal(await page.evaluate(()=>window.__UI_OPS__.includes('read_profile')),false);
 await page.getByRole('button',{name:'启用有货自动选购',exact:true}).click();
 await page.getByRole('button',{name:'确认启用并授权下单',exact:true}).click();
 await page.getByRole('button',{name:'填写收货资料',exact:true}).waitFor();
 assert.equal(await page.getByTestId('submission-status').getByText('已授权自动下单').count(),0);
 await page.screenshot({path:out+'/01-missing-profile.png',fullPage:true});
 await page.getByRole('button',{name:'填写收货资料',exact:true}).click();
 for(const [label,value]of [['省份','湖北省'],['城市','武汉市'],['区县','江汉区']])await page.getByLabel(label,{exact:true}).selectOption({label:value});
 for(const [label,value]of [['姓氏','示'],['名字','例'],['手机号','13800000000'],['邮箱','demo@example.invalid'],['详细地址','仅供界面演练的虚构地址'],['邮编','430000'],['证件后四位','0616']])await page.getByLabel(label,{exact:true}).fill(value);
 await page.getByRole('button',{name:'保存联系资料',exact:true}).click();
 await page.getByText('已加密保存并核对，可以启用自动选购',{exact:true}).waitFor();
 await page.getByRole('button',{name:'读取已保存资料',exact:true}).click();
 assert.equal(await page.getByLabel('证件后四位',{exact:true}).inputValue(),'0616');
 await page.screenshot({path:out+'/02-saved-profile.png',fullPage:true});
 await page.getByRole('button',{name:'启用有货自动选购',exact:true}).click();
 await page.getByRole('button',{name:'确认启用并授权下单',exact:true}).click();
 await page.getByRole('button',{name:'已启用，等待有货',exact:true}).waitFor();
 await page.getByTestId('submission-status').getByText('已授权自动下单',{exact:true}).waitFor();
 await page.getByRole('button',{name:'停止选购',exact:true}).click();
 await page.getByTestId('submission-status').getByText('停在提交前',{exact:true}).waitFor();
 await page.getByLabel('选购国家／地区',{exact:true}).selectOption('en_SG');
 assert.equal(await page.getByLabel('总预算（含保障） · SGD',{exact:true}).inputValue(),'0');
 assert.equal(await page.getByLabel('当地电话（+65）',{exact:true}).inputValue(),'');
 assert.equal(await page.getByLabel('证件后四位',{exact:true}).count(),0);
 assert.equal(await page.getByRole('button',{name:'分期',exact:true}).count(),0);
 assert.equal(await page.getByRole('checkbox',{name:/自动下单，等待本人支付/}).isDisabled(),true);
 await page.getByLabel('总预算（含保障） · SGD',{exact:true}).fill('2500');
 for(const [label,value] of [['姓氏（当地官网格式）','Example'],['名字（当地官网格式）','Test'],['当地电话（+65）','81234567'],['邮箱','demo@example.invalid'],['街道地址','Offline example only'],['当地邮编','238839']])await page.getByLabel(label,{exact:true}).fill(value);
 await page.getByRole('button',{name:'保存联系资料',exact:true}).click();
 await page.getByText('已加密保存并核对，可以启用自动选购',{exact:true}).waitFor();
 await page.screenshot({path:out+'/03-sg-profile.png',fullPage:true});
 await page.evaluate(()=>window.__UI_INTERVENE__('payment'));
 await page.getByTestId('checkout-intervention').getByText('新加坡 · 请手动选择付款／分期方案',{exact:true}).waitFor();
 assert.match(await page.getByTestId('checkout-intervention').innerText(),/Bark/);
 assert.match(await page.getByTestId('checkout-intervention').innerText(),/不代表已付款或已锁单/);
 await page.screenshot({path:out+'/05-foreign-payment-alert.png',fullPage:true});
 await page.evaluate(()=>window.__UI_INTERVENE__(null));
 await page.getByTestId('checkout-intervention').waitFor({state:'detached'});
 await page.getByLabel('选购国家／地区',{exact:true}).selectOption('ja_JP');
 await page.getByLabel('姓氏假名',{exact:true}).waitFor();
 assert.equal(await page.getByLabel('当地电话（+81）',{exact:true}).inputValue(),'');
 await page.screenshot({path:out+'/04-japan-profile.png',fullPage:true});
 await page.setViewportSize({width:1000,height:640});
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 assert.deepEqual(errors,[]);
 console.log(JSON.stringify({mac_first_use_form:true,missing_profile_feedback:true,save_and_readback:true,leading_zero:true,confirmation_and_activation:true,stop_revokes_authority:true,foreign_region_clears_budget_and_profile:true,foreign_no_cn_id_or_installments:true,foreign_payment_intervention_visible:true,japan_kana_fields:true,minimum_width_no_overflow:true,apple_requests:0,orders:0}));
}finally{await browser.close();server.close();}

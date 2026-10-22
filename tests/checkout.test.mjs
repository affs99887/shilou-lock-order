import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {loadSource} from './load-source.mjs';
const presets=JSON.parse(readFileSync(new URL('../src/lib/installment-plans.json',import.meta.url)));
function ui(overrides={}, targets=[{locale:"zh_CN",storeNumber:"R575",partNumber:"TEST",storeTitle:"武汉",productName:"测试"}], props={}){
 const plan={max_price:20000,quantity:1,applecare:'none',payment:'支付宝',financing:'official',installment_option:'微信分付',installment_months:24,pickup_dates:[],clear_bag_before_purchase:true,auto_submit_order:false,...overrides};
 const status={plan,busy:false,armed:false,stopping:false};const hooks=[status,plan,'',false,true,''];let cursor=0;const calls=[];const dirty={current:false};
 const Component=loadSource('src/components/AutoCheckout.tsx',s=>{
  if(s==='react')return {useEffect(){},useRef:()=>dirty,useState(initial){const i=cursor++;if(!(i in hooks))hooks[i]=initial;return [hooks[i],v=>{hooks[i]=typeof v==='function'?v(hooks[i]):v;}];}};
  if(s==='@tauri-apps/api/core')return {invoke:async(_,args)=>{calls.push(args);return {...status,plan:hooks[1]};}};
  if(s==='@tauri-apps/api/event')return {listen:async()=>()=>{}};
  if(s==='@/lib/installment-plans.json')return presets;
  if(s==='@/lib/checkout-regions.json')return JSON.parse(readFileSync(new URL('../src/lib/checkout-regions.json',import.meta.url)));
  if(s.startsWith('@/components/'))return new Proxy({},{get:(_,n)=>String(n)});
 }).AutoCheckout;
 function nodes(){cursor=0;const found=[];function walk(e){if(!e||typeof e!=='object')return;if(e.type)found.push(e);for(const c of [e.props?.children??e].flat())if(c!==e)walk(c);}walk(Component({targets,...props}));return found;}
 return {hooks,calls,dirty,nodes,button:text=>nodes().find(n=>n.props.children===text)};
}
test('provider cards use individual term lists and one click writes both fields',()=>{
 const a=ui();const cards=a.nodes().filter(n=>n.type==='div'&&presets.some(p=>p.provider===n.key));
 assert.equal(cards.length,5);
 const huabei=cards[0];const terms=huabei.props.children[1].props.children;
 assert.deepEqual(terms.map(x=>x.props.children[0]),[3,6,12]);terms[1].props.onClick();
 assert.equal(a.hooks[1].installment_option,'花呗分期');assert.equal(a.hooks[1].installment_months,6);assert.equal(a.dirty.current,true);
});
test('formal arming opens an in-app confirmation and cancel performs no commands',async()=>{
 const a=ui({auto_submit_order:true});globalThis.window={confirm:()=>{throw Error('native confirmation must not be used');}};
 a.button('启用有货自动选购').props.onClick();await new Promise(setImmediate);
 assert.equal(a.calls.length,0);assert.equal(a.hooks[6],'arm');
 a.button('取消').props.onClick();assert.equal(a.hooks[6],null);assert.equal(a.calls.length,0);
});
test('rehearsal never sends formal submission confirmation, even if configured on',async()=>{
 const a=ui({auto_submit_order:true});globalThis.window={confirm:()=>{throw Error('unexpected');}};
 a.button('测试演练').props.onClick();await new Promise(setImmediate);
 assert.deepEqual(a.calls.map(x=>x.op),['configure','rehearse']);assert.equal(a.calls[1].payload.confirmSubmission,undefined);
});
test('idle armed checkout can switch explicitly to rehearsal without remaining disabled',async()=>{
 const a=ui({auto_submit_order:true});a.hooks[0].armed=true;
 assert.equal(a.button('测试演练').props.disabled,false);
 a.button('测试演练').props.onClick();await new Promise(setImmediate);
 assert.deepEqual(a.calls.map(x=>x.op),['disarm','configure','rehearse']);
 assert.equal(a.calls.at(-1).payload.confirmSubmission,undefined);
});
test('rehearsal remains disabled during an active checkout or stop',()=>{
 const a=ui();a.hooks[0].busy=true;
 assert.equal(a.button('测试演练').props.disabled,true);
 a.hooks[0].busy=false;a.hooks[0].stopping=true;
 assert.equal(a.button('测试演练').props.disabled,true);
});
test('formal arming confirms and saves the exact edited plan first',async()=>{
 const a=ui({auto_submit_order:true});globalThis.window={confirm:()=>true};
 a.button('启用有货自动选购').props.onClick();await new Promise(setImmediate);
 assert.equal(a.calls.length,0);
 a.button('确认启用并授权下单').props.onClick();await new Promise(setImmediate);
 assert.deepEqual(a.calls.map(x=>x.op),['configure','arm']);assert.equal(a.calls[1].payload.confirmSubmission,true);
 assert.equal(a.calls[0].payload.plan.installment_option,'微信分付');assert.equal(a.calls[0].payload.plan.installment_months,24);
});
test('submission badge shows backend authorization rather than unsaved checkbox',()=>{
 const a=ui({auto_submit_order:true});
 function badge(){return a.nodes().find(n=>n.props['data-testid']==='submission-status').props.children[1].props.children;}
 assert.equal(badge(),'停在提交前');
 a.hooks[0].submissionEnabled=true;
 assert.equal(badge(),'已授权自动下单');
});
test('paused checkout is not presented as actively purchasing',()=>{
 const a=ui();
 a.hooks[0].busy=true;
 a.hooks[0].mode='attention';
 assert.equal(a.nodes().find(n=>n.props['data-testid']==='checkout-execution-status').props.children[1].props.children,'已暂停，需要处理');
 assert.match(a.nodes().find(n=>n.props['data-testid']==='checkout-disabled-reason').props.children,/新库存不会另开订单/);
 a.hooks[0].mode='awaiting_payment';
 assert.equal(a.nodes().find(n=>n.props['data-testid']==='checkout-execution-status').props.children[1].props.children,'订单待支付');
});
test('full payment button switches from installments and exposes enabled full channel',()=>{
 const a=ui();a.button('全款').props.onClick();assert.equal(a.hooks[1].financing,'full');
 const select=a.nodes().find(n=>n.type==='select'&&n.props.value==='支付宝');assert.equal(select.props.disabled,false);
 select.props.onChange({target:{value:'微信支付'}});assert.equal(a.hooks[1].payment,'微信支付');
 a.button('分期').props.onClick();assert.equal(a.hooks[1].financing,'official');assert.equal(a.hooks[1].installment_option,'微信分付');assert.equal(a.hooks[1].installment_months,24);
});
test('payment switches are locked while checkout is armed; manual selection disarms submission',()=>{
 const a=ui({auto_submit_order:true});a.hooks[0].armed=true;assert.equal(a.button('全款').props.disabled,true);
 a.hooks[0].armed=false;a.button('官网手选').props.onClick();assert.equal(a.hooks[1].auto_submit_order,false);
});

test('armed checkout explains why activation and editing are locked',()=>{
 const a=ui();a.hooks[0].armed=true;
 assert.equal(a.button('已启用，等待有货').props.disabled,true);
 assert.match(a.nodes().find(n=>n.props['data-testid']==='checkout-disabled-reason').props.children,/先停止选购/);
});
test('arming without submission permission does not require order confirmation',async()=>{
 const a=ui();a.button('启用有货自动选购').props.onClick();await new Promise(setImmediate);
 assert.deepEqual(a.calls.map(x=>x.op),['configure','arm']);
 assert.equal(a.calls[1].payload.confirmSubmission,false);
});

test('empty targets and missing plan show distinct activation prerequisites',()=>{
 const a=ui({},[]);assert.equal(a.button('启用有货自动选购').props.disabled,true);
 assert.match(a.nodes().find(n=>n.props['data-testid']==='checkout-disabled-reason').props.children,/添加至少一个监控目标/);
 const b=ui();b.hooks[1]=null;assert.equal(b.button('启用有货自动选购').props.disabled,true);
 assert.match(b.nodes().find(n=>n.props['data-testid']==='checkout-disabled-reason').props.children,/组件尚未就绪/);
});
test('Mac purchase settings include the contact editor without requiring a Windows platform',()=>{
 const a=ui();a.hooks[0].platform='macos';
 assert.ok(a.nodes().some(n=>n.type==='ContactProfile'));
});
test('profile activation failure is visible and offers a path to the contact editor',()=>{
 const a=ui();a.hooks[4]=false;a.hooks[0].profileRequired=true;a.hooks[0].error='请填写并保存资料';
 assert.ok(a.nodes().some(n=>n.props.role==='alert'&&n.props.children==='请填写并保存资料'));
 a.button('填写收货资料').props.onClick();assert.equal(a.hooks[4],true);
 assert.ok(a.nodes().some(n=>n.type==='ContactProfile'));
});
test('contact shortcut stays on purchase settings when the editor is already expanded',()=>{
 let toggles=0;const a=ui({},undefined,{expanded:true,onSettings:()=>toggles++});a.hooks[0].profileRequired=true;
 a.button('填写收货资料').props.onClick();assert.equal(toggles,0);
 assert.ok(a.nodes().some(n=>n.type==='ContactProfile'));
});
test('region changes clear currency budget and never reuse CN installments or authority',()=>{
 const a=ui({auto_submit_order:true});
 a.nodes().find(n=>n.props['aria-label']==='选购国家／地区').props.onChange({target:{value:'ja_JP'}});
 assert.equal(a.hooks[1].max_price,0);assert.equal(a.hooks[1].auto_submit_order,false);
 assert.equal(a.hooks[1].financing,'manual');assert.equal(a.hooks[1].installment_option,'');
 assert.equal(a.hooks[1].payment,'官网手动选择');
 assert.equal(a.button('分期'),undefined);assert.equal(a.button('启用有货自动选购').props.disabled,true);
});

test('earliest pickup policy is explicit and keeps saved fixed dates when switching',()=>{
 const a=ui();const dates=[...a.hooks[1].pickup_dates];
 const select=a.nodes().find(n=>n.type==='select'&&n.props.value==='specified');
 select.props.onChange({target:{value:'earliest'}});
 assert.equal(a.hooks[1].pickup_date_policy,'earliest');
 assert.deepEqual(a.hooks[1].pickup_dates,dates);
 assert.equal(a.nodes().filter(n=>n.type==='Input'&&n.props.type==='date').length,0);
});

test('mixed model rule exposes Duo dates and stays visible on the home panel',()=>{
 const dates=['2026-10-23','2026-10-24','2026-10-25'];
 const a=ui({pickup_date_policy:'by_model',pickup_dates:dates});
 const fields=a.nodes().filter(n=>n.type==='Input'&&n.props.type==='date');
 assert.deepEqual(fields.map(n=>n.props.value),dates);
 const summary=()=>a.nodes().find(n=>n.props['data-testid']==='pickup-rule-summary');
 assert.match(summary().props.children[1],/Duo 仅限 2026-10-23、2026-10-24、2026-10-25；其他型号最早可取/);
 a.hooks[4]=false;
 assert.ok(summary());
 a.hooks[4]=true;
 const select=a.nodes().find(n=>n.props['aria-label']==='取货日期策略');
 select.props.onChange({target:{value:'earliest'}});
 select.props.onChange({target:{value:'by_model'}});
 assert.deepEqual(a.hooks[1].pickup_dates,dates);
});

test('required personal actions stay visible in the checkout panel',()=>{
 const a=ui();a.hooks[0].intervention={kind:'phone',title:'请完成取货手机号验证',body:'请回 Safari 本人验证'};
 const panel=a.nodes().find(n=>n.props['data-testid']==='checkout-intervention');
 assert.equal(panel.props.role,'alert');assert.equal(panel.props['aria-live'],'assertive');
 assert.equal(panel.props.children[0].props.children,'请完成取货手机号验证');
});

test('foreign payment handoff stays prominent and does not claim a paid or locked order',()=>{
 const a=ui({checkout_locale:'en_SG',financing:'manual',payment:'官网手动选择'});
 a.hooks[0].intervention={kind:'payment',title:'新加坡 · 请手动选择付款／分期方案',body:'请在 Safari 本地区 Apple 官网选择付款或分期方案；未提交订单。'};
 const panel=a.nodes().find(n=>n.props['data-testid']==='checkout-intervention');
 assert.equal(panel.props.role,'alert');
 assert.match(panel.props.children[0].props.children,/付款／分期/);
 assert.match(panel.props.children[2].props.children,/Bark/);
 assert.match(panel.props.children[2].props.children,/不代表已付款或已锁单/);
 a.hooks[0].intervention=null;
 assert.equal(a.nodes().find(n=>n.props['data-testid']==='checkout-intervention'),undefined);
});
test('manual start sends the selected target without asking when auto-submit is off', async () => {
 const targets=[{locale:"zh_CN",storeNumber:"R575",partNumber:"A",storeTitle:"武汉",productName:"甲"},{locale:"zh_CN",storeNumber:"R683",partNumber:"B",storeTitle:"环球港",productName:"乙"}];
 const a = ui({}, targets);
 const picker = a.nodes().find(n => n.props?.['aria-label'] === '选购目标');
 picker.props.onChange({ target: { value: 'zh_CN|R683|B' } });
 a.button('手动开始选购').props.onClick(); await new Promise(setImmediate);
 assert.deepEqual(a.calls.map(x => x.op), ['configure', 'manual']);
 assert.equal(a.calls[1].payload.targetKey, 'zh_CN|R683|B');
 assert.equal(a.calls[1].payload.confirmSubmission, false);
});
test('manual start with auto-submit asks first, then disarms an idle arming and sends the confirmation', async () => {
 const a = ui({ auto_submit_order: true }); a.hooks[0].armed = true;
 assert.equal(a.button('手动开始选购').props.disabled, false, '已启用但空闲时也能手动开始');
 a.button('手动开始选购').props.onClick(); await new Promise(setImmediate);
 assert.equal(a.calls.length, 0); assert.equal(a.hooks[6], 'manual');
 a.button('确认开始并授权下单').props.onClick(); await new Promise(setImmediate);
 assert.deepEqual(a.calls.map(x => x.op), ['disarm', 'configure', 'manual']);
 assert.equal(a.calls.at(-1).payload.confirmSubmission, true);
});
test('manual start stays disabled while a checkout runs, is stopping, or for other regions', () => {
 const busy = ui(); busy.hooks[0].busy = true;
 assert.equal(busy.button('手动开始选购').props.disabled, true);
 busy.hooks[0].busy = false; busy.hooks[0].stopping = true;
 assert.equal(busy.button('手动开始选购').props.disabled, true);
 const foreign = ui({ checkout_locale: 'en_SG' }, [{locale:"en_SG",storeNumber:"R669",partNumber:"X",storeTitle:"SG",productName:"x"}]);
 assert.equal(foreign.button('手动开始选购').props.disabled, true);
});
test('advance login is one click, needs no plan save, and shows the account state', async () => {
 const a = ui();
 const session = () => a.nodes().find(n => n.props?.['data-testid'] === 'checkout-session').props.children.join('');
 assert.match(session(), /尚未检查/);
 a.button('登录 Apple 账户').props.onClick(); await new Promise(setImmediate);
 assert.deepEqual(a.calls.map(x => x.op), ['login'], '只发 login，不保存方案、不启用选购');
 a.hooks[0] = { ...a.hooks[0], session: '账号页登录已确认；后续购买复用当前窗口，结账仍可能再次认证' };
 assert.match(session(), /账号页登录已确认/);
 a.hooks[0] = { ...a.hooks[0], busy: true, loginTask: true };
 const waiting = a.button('等待登录…');
 assert.equal(waiting.props.disabled, true, '登录进行中不能重复发起');
});
test('advance login stays disabled while checkout is armed or for other regions', () => {
 const armed = ui(); armed.hooks[0].armed = true;
 assert.equal(armed.button('登录 Apple 账户').props.disabled, true);
 const foreign = ui({ checkout_locale: 'en_SG' });
 assert.equal(foreign.button('登录 Apple 账户').props.disabled, true);
});

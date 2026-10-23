import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {loadSource} from './load-source.mjs';
const presets=JSON.parse(readFileSync(new URL('../src/lib/installment-plans.json',import.meta.url)));
function ui(overrides={}, targets=[{locale:"zh_CN",storeNumber:"R575",partNumber:"TEST",storeTitle:"武汉",productName:"测试"}], props={}){
 const plan={max_price:20000,quantity:1,applecare:'none',payment:'支付宝',financing:'official',installment_option:'微信分付',installment_months:24,pickup_dates:[],clear_bag_before_purchase:true,auto_submit_order:false,...overrides};
 const status={plan,busy:false,armed:false,stopping:false};const hooks=[status,plan,'',false,true,''];let cursor=0;const calls=[];const dirty={current:false};
 // 选购命令记下参数，其他后端命令（如开始监测）记下命令名；fail 里列出的会失败。
 const fail=new Set();
 const Component=loadSource('src/components/AutoCheckout.tsx',s=>{
  if(s==='react')return {useEffect(){},useRef:()=>dirty,useState(initial){const i=cursor++;if(!(i in hooks))hooks[i]=initial;return [hooks[i],v=>{hooks[i]=typeof v==='function'?v(hooks[i]):v;}];}};
  if(s==='@tauri-apps/api/core')return {invoke:async(cmd,args)=>{const call=cmd==='checkout_command'?args:{cmd};calls.push(call);if(fail.has(call.op??call.cmd))throw new Error('模拟失败');return {...status,plan:hooks[1]};}};
  if(s==='@tauri-apps/api/event')return {listen:async()=>()=>{}};
  if(s==='@/lib/installment-plans.json')return presets;
  if(s==='@/lib/checkout-regions.json')return JSON.parse(readFileSync(new URL('../src/lib/checkout-regions.json',import.meta.url)));
  if(s.startsWith('@/components/'))return new Proxy({},{get:(_,n)=>String(n)});
 }).AutoCheckout;
 function nodes(){cursor=0;const found=[];function walk(e){if(!e||typeof e!=='object')return;if(e.type)found.push(e);for(const c of [e.props?.children??e].flat())if(c!==e)walk(c);}walk(Component({targets,...props}));return found;}
 // 带上「是否被隐藏」：自身或任一上层 hidden，或者在没打开的配置弹窗里。
 // 弹窗底栏（footer）里的按钮也算在弹窗里。
 function shown(){cursor=0;const found=[];function walk(e,hidden){if(!e||typeof e!=='object')return;if(Array.isArray(e)){for(const c of e)walk(c,hidden);return;}const h=hidden||e.props?.hidden===true||(e.type==='ConfigModal'&&!e.props.open);if(e.type)found.push({node:e,hidden:h});walk(e.props?.children,h);if(e.type==='ConfigModal')walk(e.props.footer,h);}walk(Component({targets,...props}),false);return found;}
 return {hooks,calls,dirty,nodes,shown,fail,ops:()=>calls.map(x=>x.op??x.cmd),button:text=>nodes().find(n=>n.props.children===text)};
}
test('provider cards use individual term lists and one click writes both fields',()=>{
 const a=ui();const cards=a.nodes().filter(n=>n.type==='div'&&presets.some(p=>p.provider===n.key));
 assert.equal(cards.length,5);
 const huabei=cards[0];const terms=huabei.props.children[1].props.children;
 assert.deepEqual(terms.map(x=>x.props.children[0]),[3,6,12]);terms[1].props.onClick();
 assert.equal(a.hooks[1].installment_option,'花呗分期');assert.equal(a.hooks[1].installment_months,6);assert.equal(a.dirty.current,true);
});
test('arming with auto-submit starts directly, authorizes submission and starts monitoring',async()=>{
 const a=ui({auto_submit_order:true});globalThis.window={confirm:()=>{throw Error('native confirmation must not be used');}};
 a.button('启用有货自动选购').props.onClick();await new Promise(setImmediate);
 assert.equal(a.hooks[6],null,'不弹二次确认');
 assert.deepEqual(a.ops(),['configure','arm','start_watching']);
 assert.equal(a.calls[1].payload.confirmSubmission,true);
});
test('a failed arming never starts monitoring, and a monitoring failure is reported',async()=>{
 const a=ui();a.fail.add('arm');
 a.button('启用有货自动选购').props.onClick();await new Promise(setImmediate);
 assert.deepEqual(a.ops(),['configure','arm'],'启用失败时不开始监测');
 assert.match(a.hooks[2],/模拟失败/);
 const b=ui();b.fail.add('start_watching');
 b.button('启用有货自动选购').props.onClick();await new Promise(setImmediate);
 assert.deepEqual(b.ops(),['configure','arm','start_watching']);
 assert.match(b.hooks[2],/已启用自动选购，但库存监测没能开始/);
});
test('rehearsal never sends formal submission confirmation, even if configured on',async()=>{
 const a=ui({auto_submit_order:true});globalThis.window={confirm:()=>{throw Error('unexpected');}};
 a.button('测试演练').props.onClick();await new Promise(setImmediate);
 assert.deepEqual(a.calls.map(x=>x.op),['configure','rehearse']);assert.equal(a.calls[1].payload.confirmSubmission,undefined);
});
test('rehearsal while auto checkout waits leaves pausing to the backend instead of turning it off',async()=>{
 const a=ui({auto_submit_order:true});a.hooks[0].armed=true;
 assert.equal(a.button('测试演练').props.disabled,false);
 assert.match(a.button('测试演练').props.title,/自动选购先暂停，这次结束后自动恢复/);
 a.button('测试演练').props.onClick();await new Promise(setImmediate);
 assert.deepEqual(a.calls.map(x=>x.op),['rehearse'],'不先关掉自动选购，也不保存锁着的方案');
 assert.equal(a.calls.at(-1).payload.confirmSubmission,undefined);
});
test('rehearsal remains disabled during an active checkout or stop',()=>{
 const a=ui();a.hooks[0].busy=true;
 assert.equal(a.button('测试演练').props.disabled,true);
 a.hooks[0].busy=false;a.hooks[0].stopping=true;
 assert.equal(a.button('测试演练').props.disabled,true);
});
test('arming saves the exact edited plan before enabling',async()=>{
 const a=ui({auto_submit_order:true});
 a.button('启用有货自动选购').props.onClick();await new Promise(setImmediate);
 assert.deepEqual(a.ops(),['configure','arm','start_watching']);assert.equal(a.calls[1].payload.confirmSubmission,true);
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
 assert.match(a.button('人工处理后继续').props.title,/新库存不会另开订单/);
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

test('armed checkout turns the start button into a stop for auto checkout and explains the locked plan',async()=>{
 const a=ui();a.hooks[0].armed=true;
 assert.equal(a.button('启用有货自动选购'),undefined);
 const stop=a.button('停止自动选购');
 assert.equal(stop.props.hidden,false);assert.equal(stop.props.disabled,false);
 assert.match(stop.props.title,/库存监测照常进行/);
 stop.props.onClick();await new Promise(setImmediate);
 assert.deepEqual(a.ops(),['stop']);
 assert.ok(a.nodes().some(n=>n.type==='p'&&/请先在上方停止或结束后再改/.test(n.props.children)),'方案里说明为什么改不了');
});
// 选购操作那一排当下看得见的按钮文字。
function visibleButtons(a){const row=a.nodes().find(n=>n.props.className==='checkout-actions');return [row.props.children].flat().filter(b=>b&&!b.props.hidden).map(b=>b.props.children);}
test('only the buttons usable in the current state are shown, at most three besides login',()=>{
 const a=ui({},undefined,{expanded:false});
 assert.deepEqual(visibleButtons(a),['启用有货自动选购','手动开始选购','测试演练','登录 Apple 账户']);
 a.hooks[0].armed=true;
 assert.deepEqual(visibleButtons(a),['停止自动选购','手动开始选购','测试演练','登录 Apple 账户']);
 a.hooks[0].busy=true;
 assert.deepEqual(visibleButtons(a),['停止自动选购','登录 Apple 账户'],'自动选购的单子在跑');
 Object.assign(a.hooks[0],{armed:false,mode:'attention',singleRun:'manual',autoPaused:true});
 assert.deepEqual(visibleButtons(a),['人工处理后继续','结束本次选购','登录 Apple 账户']);
 assert.equal(a.button('人工处理后继续').props.variant,undefined,'需要处理时「继续」是实心主按钮');
 a.hooks[0].stopping=true;
 assert.deepEqual(visibleButtons(a),['正在停止…','登录 Apple 账户']);
});
test('main actions stay on the panel while plan details live in a closed modal',()=>{
 const a=ui({},undefined,{expanded:false});
 const state=label=>a.shown().find(x=>x.node.props.children===label||x.node.props['aria-label']===label);
 for(const label of ['选购目标','启用有货自动选购','手动开始选购','测试演练','登录 Apple 账户'])
  assert.equal(state(label)?.hidden,false,label+' 要一直显示在面板上');
 assert.equal(state('人工处理后继续').hidden,true,'没有暂停的选购时不占位置');
 assert.equal(state('保存设置').hidden,true,'详细方案在没打开的弹窗里');
 assert.equal(a.shown().find(x=>x.node.type==='ConfigModal').node.props.open,false);
 const open=ui({},undefined,{expanded:true});
 assert.equal(open.shown().find(x=>x.node.type==='ConfigModal').node.props.open,true);
 assert.equal(open.shown().find(x=>x.node.props.children==='保存设置').hidden,false);
});
test('opening and closing the plan modal goes through the page toggle',()=>{
 let toggles=0;
 const a=ui({},undefined,{expanded:false,onSettings:()=>toggles++});
 const modal=()=>a.shown().find(x=>x.node.type==='ConfigModal').node.props;
 modal().onClose();
 assert.equal(toggles,0,'已经关着时再关不应切换成打开');
 a.nodes().find(n=>n.props?.['aria-controls']==='purchase-plan').props.onClick();
 assert.equal(toggles,1);
 const shownOpen=ui({},undefined,{expanded:true,onSettings:()=>toggles++});
 shownOpen.shown().find(x=>x.node.type==='ConfigModal').node.props.onClose();
 assert.equal(toggles,2);
});
test('the stop control says what it stops and a single run only ends itself',async()=>{
 const a=ui({},undefined,{expanded:false});
 const stops=()=>a.shown().filter(x=>!x.hidden&&x.node.props.className==='stop-checkout').map(x=>x.node);
 assert.deepEqual(stops(),[]);
 a.hooks[0].armed=true;
 assert.deepEqual(stops().map(n=>n.props.children),['停止自动选购']);
 a.hooks[0].armed=false;a.hooks[0].busy=true;
 assert.deepEqual(stops().map(n=>n.props.children),['停止选购'],'自动选购暂停后留下的单子');
 Object.assign(a.hooks[0],{singleRun:'manual',autoPaused:true});
 const [end]=stops();
 assert.equal(end.props.children,'结束本次选购');
 assert.match(end.props.title,/自动选购随即恢复/);
 end.props.onClick();await new Promise(setImmediate);
 assert.deepEqual(a.ops(),['end_run'],'只结束这一次，不发会关掉自动选购的 stop');
 a.hooks[0].autoPaused=false;
 assert.doesNotMatch(stops()[0].props.title,/自动选购随即恢复/);
 a.hooks[0].stopping=true;
 assert.equal(stops()[0].props.children,'正在停止…');assert.equal(stops()[0].props.disabled,true);
});
test('the status line shows the running progress, and the last single run result when idle',()=>{
 const a=ui();
 const line=()=>a.nodes().find(n=>n.props.role==='status'&&n.props.className==='checkout-note');
 assert.equal(line().props.hidden,true,'空闲又没有结果时不占位置');
 a.hooks[0].lastResult='手动选购没买到：目标已售罄';
 assert.equal(line().props.hidden,false);assert.equal(line().props.children,'手动选购没买到：目标已售罄');
 Object.assign(a.hooks[0],{busy:true,singleRun:'manual',autoPaused:true,reason:'正在手动开始选购',elapsed:3.25});
 assert.equal(line().props.children,'正在手动开始选购 · 3.3 秒');
 const chip=a.nodes().find(n=>n.props['data-testid']==='checkout-execution-status');
 assert.equal(chip.props.children[1].props.children,'手动选购执行中');
 assert.equal(chip.props.children[2].props.children,'自动选购暂停中，结束后恢复');
 Object.assign(a.hooks[0],{busy:false,armed:true,singleRun:null,autoPaused:false,reason:'等待新库存'});
 assert.equal(line().props.children,'等待新库存','等待有货时不再显示上一次选购的耗时');
});
test('arming without auto-submit stops before submission and still starts monitoring',async()=>{
 const a=ui();a.button('启用有货自动选购').props.onClick();await new Promise(setImmediate);
 assert.deepEqual(a.ops(),['configure','arm','start_watching']);
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
 // 首页一行里省掉年份，悬停提示里是完整日期。
 assert.equal(summary().props.children[1],'Duo 仅限 10-23、10-24、10-25；其他型号最早可取');
 assert.equal(summary().props.title,'Duo 仅限 2026-10-23、2026-10-24、2026-10-25；其他型号最早可取');
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
test('manual start while auto checkout waits starts directly and keeps auto checkout for afterwards', async () => {
 const a = ui({ auto_submit_order: true }); a.hooks[0].armed = true;
 assert.equal(a.button('手动开始选购').props.disabled, false, '已启用但空闲时也能手动开始');
 assert.match(a.button('手动开始选购').props.title, /自动选购先暂停，这次结束后自动恢复/);
 a.button('手动开始选购').props.onClick(); await new Promise(setImmediate);
 assert.equal(a.hooks[6], null, '不弹二次确认');
 assert.deepEqual(a.ops(), ['manual'], '不先关掉自动选购，也不另外开始监测');
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
 // 账号状态只显示第一句，完整说明在悬停提示里；没确认登录前，下方提示怎样免于反复登录。
 const node = () => a.nodes().find(n => n.props?.['data-testid'] === 'checkout-session');
 const session = () => node().props.children.at(-1);
 const tip = () => a.nodes().find(n => n.props?.['data-testid'] === 'login-tip');
 assert.equal(session(), '开始选购时检查官网登录状态');
 assert.match(node().props.title, /仅在官网要求时由本人认证/);
 assert.match(tip().props.children, /保持我的登录状态/);
 a.button('登录 Apple 账户').props.onClick(); await new Promise(setImmediate);
 assert.deepEqual(a.calls.map(x => x.op), ['login'], '只发 login，不保存方案、不启用选购');
 a.hooks[0] = { ...a.hooks[0], session: '账号页登录已确认；后续购买复用当前窗口，结账仍可能再次认证' };
 assert.equal(session(), '账号页登录已确认');
 assert.match(node().props.title, /结账仍可能再次认证/);
 assert.equal(tip(), undefined, '确认登录后不再占一行提示');
 a.hooks[0] = { ...a.hooks[0], busy: true, loginTask: true };
 const waiting = a.button('等待登录…');
 assert.equal(waiting.props.disabled, true, '登录进行中不能重复发起');
});
test('a reached review page shows a calm banner, other handoffs stay as warnings', () => {
 const a = ui();
 const banner = () => a.nodes().find(n => n.props?.['data-testid'] === 'checkout-intervention');
 a.hooks[0].intervention = { kind: 'review', title: '已到订单核对页，请本人检查', body: '请在 Safari 核对' };
 assert.match(banner().props.className, /is-done/);
 a.hooks[0].intervention = { kind: 'login', title: 'Apple 需要登录', body: '请在 Safari 登录' };
 assert.doesNotMatch(banner().props.className, /is-done/);
});
test('advance login stays disabled while checkout is armed or for other regions', () => {
 const armed = ui(); armed.hooks[0].armed = true;
 assert.equal(armed.button('登录 Apple 账户').props.disabled, true);
 const foreign = ui({ checkout_locale: 'en_SG' });
 assert.equal(foreign.button('登录 Apple 账户').props.disabled, true);
});

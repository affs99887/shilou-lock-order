import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {loadSource} from './load-source.mjs';
const regions=JSON.parse(readFileSync(new URL('../src/lib/regions-2025.json',import.meta.url)));
function ui(reply=async()=>({busy:false,error:'保存失败'})){
 const hooks=[{},false,''];let cursor=0;const calls=[];
 const Component=loadSource('src/components/ContactProfile.tsx',s=>{
  if(s==='react')return {useState(initial){const i=cursor++;return [hooks[i]??initial,v=>hooks[i]=typeof v==='function'?v(hooks[i]):v];}};
  if(s==='@/lib/regions-2025.json')return regions;
  if(s==='@/lib/checkout-regions.json')return JSON.parse(readFileSync(new URL('../src/lib/checkout-regions.json',import.meta.url)));
  if(s==='@tauri-apps/api/core')return {invoke:async(_,args)=>{calls.push(args);return reply(args);}};
  if(s.startsWith('@/components/'))return new Proxy({},{get:(_,n)=>String(n)});
 }).ContactProfile;
 function nodes(locked=false,locale='zh_CN'){cursor=0;const found=[];function walk(e){if(!e||typeof e!=='object')return;if(e.type)found.push(e);for(const c of [e.props?.children??e].flat())if(c!==e)walk(c);}walk(Component({locked,locale}));return found;}
 function select(label,value){nodes().find(n=>n.props['aria-label']===label).props.onChange({target:{value}});}
 return {hooks,calls,nodes,select};
}
test('contact address options cascade and changing province clears city and district',()=>{
 const a=ui();a.select('省份','湖北省');a.select('城市','武汉市');a.select('区县','江汉区');
 assert.equal(a.hooks[0].district,'江汉区');
 a.select('省份','浙江省');assert.equal(a.hooks[0].city,'');assert.equal(a.hooks[0].district,'');
 const city=a.nodes().find(n=>n.props['aria-label']==='城市');
 assert.ok(city.props.children[1].some(o=>o.props.children==='杭州市'));
});
test('first-use form stays empty without silently reading local personal data',()=>{
 const a=ui();a.nodes();assert.deepEqual(a.hooks[0],{});assert.deepEqual(a.calls,[]);
});
test('explicit reading restores saved contact fields and the leading-zero suffix',async()=>{
 const a=ui(async args=>{assert.equal(args.op,'read_profile');return {family_name:'示',given_name:'例',id_last4:'0616'};});
 a.nodes().find(n=>n.props.children==='读取已保存资料').props.onClick();await new Promise(setImmediate);
 assert.equal(a.hooks[0].id_last4,'0616');assert.equal(a.hooks[0].family_name,'示');
});
test('legacy province and city names are shown in the standard selectors, not hidden raw values',async()=>{
 const a=ui(async()=>({province:'湖北',city:'武汉',district:'江汉区',full_name:'旧姓名'}));
 a.nodes().find(n=>n.props.children==='读取已保存资料').props.onClick();await new Promise(setImmediate);
 assert.equal(a.hooks[0].province,'湖北省');assert.equal(a.hooks[0].city,'武汉市');assert.equal(a.hooks[0].district,'江汉区');
 a.select('姓氏','新');assert.equal(a.hooks[0].full_name,'');
});
test('an old error while a queued save is busy does not become a false save failure',async()=>{
 let polls=0;
 const a=ui(async args=>args.op==='status'?(++polls===1?{busy:true,error:'旧启用失败'}:{busy:false,error:''}):{busy:true});
 a.nodes().find(n=>n.props.children==='保存联系资料').props.onClick();await new Promise(r=>setTimeout(r,700));
 assert.match(a.hooks[2],/已加密保存并核对/);assert.equal(polls,2);
});
test('municipalities expose a city and their districts',()=>{
 const a=ui();a.select('省份','北京市');a.select('城市','北京市');
 const district=a.nodes().find(n=>n.props['aria-label']==='区县');
 assert.ok(district.props.children[1].some(o=>o.props.children==='东城区'));
});
test('identity suffix stays text with its leading zero and editing is disabled during checkout',()=>{
 const a=ui();a.select('证件后四位','0616');assert.equal(a.hooks[0].id_last4,'0616');
 assert.equal(a.nodes(true).find(n=>n.type==='fieldset').props.disabled,true);
});
test('queued save failure is not displayed as success',async()=>{
 const a=ui();a.nodes().find(n=>n.props.children==='保存联系资料').props.onClick();
 await new Promise(r=>setTimeout(r,400));
 assert.match(a.hooks[2],/保存失败/);assert.doesNotMatch(a.hooks[2],/已加密保存/);
 assert.deepEqual(a.calls.map(c=>c.op),['save_profile','status']);
});
test('foreign profiles show regional contact fields without mainland identity',()=>{
 const a=ui();const sg=a.nodes(false,'en_SG');
 assert.ok(sg.some(n=>n.props['aria-label']==='当地电话（+65）'));
 assert.ok(!sg.some(n=>n.props['aria-label']==='证件后四位'));
 const jp=a.nodes(false,'ja_JP');assert.ok(jp.some(n=>n.props['aria-label']==='姓氏假名'));
});

test('all mainland address paths have selectable districts and preserve the chosen hierarchy',()=>{
 const a=ui();let cities=0,localities=0,special=0;
 const choices=label=>a.nodes().find(n=>n.props['aria-label']===label).props.children[1].map(n=>n.props.children);
 assert.equal(choices('省份').length,31);
 for(const p of regions){
  a.select('省份',p.name);assert.equal(a.hooks[0].city,'');assert.equal(a.hooks[0].district,'');
  const cs=choices('城市');assert.ok(cs.length,p.name);assert.equal(new Set(cs).size,cs.length);
  for(const c of cs){
   a.select('城市',c);assert.equal(a.hooks[0].district,'');cities++;
   const ds=choices('区县');assert.ok(ds.length,`${p.name}/${c}`);assert.equal(new Set(ds).size,ds.length);
   if(ds.length===1&&ds[0]===c)special++;
   for(const d of ds){a.select('区县',d);localities++;assert.deepEqual([a.hooks[0].province,a.hooks[0].city,a.hooks[0].district],[p.name,c,d]);}
  }
 }
 assert.equal(cities,369);assert.equal(localities,2849);assert.equal(special,36);
});

test('all special-city saved profiles restore their selectable district and save without losing it',async()=>{
 let saved={};const a=ui(async args=>args.op==='read_profile'?saved:args.op==='status'?{busy:false,error:''}:{});
 for(const p of regions){
  if(['11','12','31','50'].includes(p.code))continue;
  for(const c of p.children.filter(c=>!c.children?.length)){
   saved={province:p.name,city:c.name,district:c.name,id_last4:'0616'};
   a.nodes().find(n=>n.props.children==='读取已保存资料').props.onClick();await new Promise(setImmediate);
   assert.deepEqual(a.hooks[0],saved,`${p.name}/${c.name}`);assert.doesNotMatch(a.hooks[2],/重新选取/);
  }
 }
 a.nodes().find(n=>n.props.children==='保存联系资料').props.onClick();await new Promise(r=>setTimeout(r,400));
 assert.deepEqual(a.calls.findLast(c=>c.op==='save_profile').payload.profile,saved);
});

test('changing special cities clears the previous district and invalid saved districts remain unselected',async()=>{
 const a=ui(async()=>({province:'广东',city:'东莞',district:'江汉区'}));
 a.select('省份','广东省');a.select('城市','东莞市');a.select('区县','东莞市');
 a.select('城市','中山市');assert.equal(a.hooks[0].district,'');
 const district=a.nodes().find(n=>n.props['aria-label']==='区县');
 assert.deepEqual(district.props.children[1].map(n=>n.props.children),['中山市']);
 a.nodes().find(n=>n.props.children==='读取已保存资料').props.onClick();await new Promise(setImmediate);
 assert.equal(a.hooks[0].district,'');assert.match(a.hooks[2],/重新选取/);
});

test('administrative data contains unique codes, nonempty names and valid parent-child prefixes',()=>{
 const codes=new Set();
 function walk(node,parent){
  assert.match(node.code,/^\d{2}(?:\d{2}){0,2}$/);assert.ok(node.name.trim());
  assert.ok(!codes.has(node.code),node.code);codes.add(node.code);
  if(parent)assert.ok(node.code.startsWith(parent.code),`${parent.name}/${node.name}`);
  for(const child of node.children||[])walk(child,node);
 }
 for(const p of regions)walk(p);
});

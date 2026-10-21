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

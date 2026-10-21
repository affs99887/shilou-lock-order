import test from 'node:test';
import assert from 'node:assert/strict';
import {loadSource} from './load-source.mjs';
const {normalizeBarkAddress:n}=loadSource('src/lib/bark-address.ts');
test('Bark accepts base, full example, markdown and copied curl without executing it',()=>{
 for(const raw of ['https://api.day.app/ExampleKey',' https://api.day.app/ExampleKey/标题/正文?group=demo&ttl=600\u00a0','[提醒](https://api.day.app/ExampleKey/正文)','curl -X GET "https://api.day.app/ExampleKey/title/body?group=example"'])assert.equal(n(raw),'https://api.day.app/ExampleKey');
 assert.equal(n(''),'');assert.equal(n('https://notify.example/ExampleKey'),'https://notify.example/ExampleKey');
});
test('Bark rejects incomplete, ambiguous or credential-bearing addresses',()=>{
 for(const raw of ['https://api.day.app','not a link','https://api.day.app/push','https://user:pass@api.day.app/ExampleKey','https://notify.example/prefix/key/body','https://api.day.app/a https://api.day.app/b','https://open.feishu.cn/open-apis/bot/v2/hook/abc','https://api.day.app/a%2Fb/body'])assert.throws(()=>n(raw),raw);
});
function harness({saveFails=false,testFails=false}={}){
 const hooks=[null,false,'',''];let cursor=0;const calls=[];const ref={current:false};
 const C=loadSource('src/components/BarkSettings.tsx',s=>{
  if(s==='react')return {useRef:()=>ref,useState(initial){const i=cursor++;if(!(i in hooks))hooks[i]=initial;return[hooks[i],v=>hooks[i]=v];}};
  if(s==='@/lib/store')return {saveBarkAddress:async address=>{calls.push(['save',address]);if(saveFails)throw Error('private key error');}};
  if(s==='@tauri-apps/api/core')return {invoke:async cmd=>{calls.push([cmd]);if(testFails)throw Error('private endpoint');}};
  if(s.startsWith('@/components/'))return new Proxy({},{get:(_,n)=>String(n)});
 }).BarkSettings;
 function nodes(){cursor=0;const all=[];function walk(e){if(!e||typeof e!=='object')return;if(e.type)all.push(e);for(const c of [e.props?.children??e].flat())if(c!==e)walk(c);}walk(C({saved:'https://api.day.app/OldKey'}));return all;}
 return{hooks,calls,nodes,button:t=>nodes().find(e=>e.props.children===t)};
}
test('simple notification page has one field and saves normalized endpoint before test',async()=>{
 const a=harness();assert.equal(a.nodes().filter(e=>e.type==='Input').length,1);
 a.hooks[0]='https://api.day.app/NewKey/title/body';a.button('保存并测试提醒').props.onClick();await new Promise(setImmediate);
 assert.deepEqual(a.calls,[['save','https://api.day.app/NewKey'],['test_notify']]);
});
test('failed save never sends test; errors do not expose keys',async()=>{
 const a=harness({saveFails:true});a.button('保存并测试提醒').props.onClick();await new Promise(setImmediate);
 assert.equal(a.calls.length,1);assert.equal(a.hooks[2],'');assert.ok(a.hooks[3]);assert.ok(!a.hooks[3].includes('private'));
});
test('failed test is not reported as sent',async()=>{
 const a=harness({testFails:true});a.button('保存并测试提醒').props.onClick();await new Promise(setImmediate);assert.equal(a.hooks[2],'');assert.ok(a.hooks[3]);
});
test('blank address saves off but cannot send a test',async()=>{
 const a=harness();a.hooks[0]='';a.button('保存并测试提醒').props.onClick();await new Promise(setImmediate);assert.equal(a.calls.length,0);
 a.button('保存地址').props.onClick();await new Promise(setImmediate);assert.deepEqual(a.calls,[['save','']]);
});

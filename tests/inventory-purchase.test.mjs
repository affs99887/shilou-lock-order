import test from 'node:test';
import assert from 'node:assert/strict';
import { loadSource } from './load-source.mjs';
const target={locale:'zh_CN',storeNumber:'R683',storeTitle:'环球港',partNumber:'TEST',productName:'测试商品'};
const settle=()=>new Promise(setImmediate);
async function setup(patch={}) {
 let status={armed:false,busy:false,stopping:false,plan:{auto_submit_order:false},...patch};
 let cursor=0, listener;const slots=[], effects=[], calls=[];let handler;
 const hook=loadSource('src/lib/inventory-purchase.ts',name=>{
  if(name==='react')return {
   useState(initial){const i=cursor++;if(!(i in slots))slots[i]=initial;return [slots[i],v=>{slots[i]=v;}];},
   useRef(initial){const i=cursor++;if(!(i in slots))slots[i]={current:initial};return slots[i];},
   useEffect(effect){const i=cursor++;if(!(i in slots)){slots[i]=true;effects.push(effect);}},
  };
  if(name==='@tauri-apps/api/event')return {listen:async(_,fn)=>{listener=fn;return ()=>{};}};
  if(name==='@tauri-apps/api/core')return {invoke:async(_,args)=>{
   calls.push(args);if(handler)return handler(args,status);
   if(args.op==='purchase')status={...status,busy:true};
   return status;
  }};
 }).useInventoryPurchase;
 const render=()=>{cursor=0;return hook();};
 render();for(const effect of effects)effect();await settle();
 return {render,calls,event:patch=>{status={...status,...patch};listener({payload:status});},
  intercept:fn=>{handler=fn;}};
}
test('row purchase uses the selected target and saved submission mode',async()=>{
 const a=await setup();assert.equal(a.render().disabled,false);
 await a.render().purchase(target);
 const command=a.calls.find(c=>c.op==='purchase');
 assert.equal(command.payload.targetKey,'zh_CN|R683|TEST');
 assert.equal(command.payload.confirmSubmission,false);
 assert.equal(a.render().disabled,true);
});
test('armed, active, stopping and pending-order states cannot start another row',async()=>{
 for(const field of ['armed','busy','stopping','orderNeedsReview']) {
  const a=await setup({[field]:true});assert.equal(a.render().disabled,true);
  await a.render().purchase(target);assert.equal(a.calls.filter(c=>c.op==='purchase').length,0);
 }
});
test('two rapid clicks and two targets send only one purchase command',async()=>{
 const a=await setup();let release;
 const blocked=new Promise(resolve=>{release=resolve;});
 a.intercept(async(args,status)=>{if(args.op==='status')await blocked;return args.op==='purchase'?{...status,busy:true}:status;});
 const p=a.render().purchase(target);
 await a.render().purchase({...target,storeNumber:'R575'});
 release();await p;
 assert.equal(a.calls.filter(c=>c.op==='purchase').length,1);
 assert.equal(a.calls.find(c=>c.op==='purchase').payload.targetKey,'zh_CN|R683|TEST');
});
test('preflight notices a checkout started after the button was drawn',async()=>{
 const a=await setup();a.intercept(async(_,status)=>({...status,armed:true}));
 await a.render().purchase(target);
 assert.equal(a.calls.filter(c=>c.op==='purchase').length,0);
});
test('formal row purchase retains explicit confirmation and cancellation sends nothing',async()=>{
 const a=await setup({plan:{auto_submit_order:true}});
 await a.render().purchase(target);
 assert.deepEqual(a.render().confirmationTarget,target);
 assert.equal(a.calls.filter(c=>c.op==='purchase').length,0);
 a.render().cancel();assert.equal(a.render().confirmationTarget,null);
 await a.render().purchase(target);a.render().confirm();await settle();
 assert.equal(a.calls.find(c=>c.op==='purchase').payload.confirmSubmission,true);
});
test('backend rejection is visible and the button becomes available again',async()=>{
 const a=await setup();a.intercept(async(args,status)=>{if(args.op==='purchase')throw Error('目标当前未确认有货');return status;});
 await a.render().purchase(target);
 assert.equal(a.render().error,'目标当前未确认有货');assert.equal(a.render().disabled,false);
});
test('a stopped checkout allows the row button to start again',async()=>{
 const a=await setup();await a.render().purchase(target);
 assert.equal(a.render().disabled,true);
 a.event({busy:false,armed:false,stopping:false});
 assert.equal(a.render().disabled,false);
 await a.render().purchase(target);
 assert.equal(a.calls.filter(c=>c.op==='purchase').length,2);
});

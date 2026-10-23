import { useState } from "react";
import { invoke } from "@tauri-apps/api/core";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import regions from "@/lib/regions-2025.json";
import checkoutRegions from "@/lib/checkout-regions.json";

type Region = {name:string; code:string; children?:Region[]};
// 北京、天津、上海、重庆：省份本身就是城市，下一级直接是区县。
const municipalities = new Set(['11','12','31','50']);
function regionCities(province?:Region):Region[] {
  if (!province) return [];
  return municipalities.has(province.code)
    ? [{name:province.name,code:province.code,children:province.children}]
    : province.children || [];
}
function regionDistricts(city?:Region):Region[] {
  // 不设区县的城市（如东莞、中山）在 Apple 官网上区县一栏填的就是城市本身。
  return city ? city.children?.length ? city.children : [{name:city.name,code:city.code}] : [];
}
const fields: [string,string][] = [["family_name","姓氏"],["given_name","名字"],["phone","手机号"],
  ["email","邮箱"],["address","详细地址"],["postal_code","邮编"],["id_last4","证件后四位"],
  ["invoice_title","发票抬头（选填）"],["tax_id","税号（选填）"]];

export function ContactProfile({locked,locale='zh_CN',beforeSave}:{locked:boolean;locale?:string;beforeSave?:()=>Promise<void>}) {
  const [profile,setProfile]=useState<Record<string,string>>({});
  const [pending,setPending]=useState(false);
  const [message,setMessage]=useState("");
  const provinces:Region[]=regions;
  const province=provinces.find(r=>r.name===profile.province);
  const cities=regionCities(province);
  const districts=regionDistricts(cities.find(r=>r.name===profile.city));
  const local=checkoutRegions.find(r=>r.locale===locale)!;
  const foreign=locale!=='zh_CN';
  const localFields: [string,string][] = foreign ? [
    ['family_name','姓氏（当地官网格式）'],['given_name','名字（当地官网格式）'],['phone',`当地电话（+${local.phonePrefix}）`],['email','邮箱'],
    ...(['province','city','district'].filter(k=>local.required.includes(k)).map(k=>[k,({province:locale==='ja_JP'?'都道府县':locale==='en_AU'?'州／领地':'地区／州',city:'城市',district:'区／地区'} as Record<string,string>)[k]]) as [string,string][]),
    ['address','街道地址'],['address2','楼层／单元（选填）'],
    ...(local.required.includes('postal_code')?[['postal_code','当地邮编']] as [string,string][]:[]),
    ...(locale==='ja_JP'?[['family_name_kana','姓氏假名'],['given_name_kana','名字假名']] as [string,string][]:[])
  ]:fields;
  async function read(){
    setPending(true);setMessage("");
    try{
      const raw=await invoke<Record<string,string>>("checkout_command",{op:"read_profile",payload:{locale}});
      if(foreign){setProfile(raw);setMessage("已读取本地区独立保存的资料。");return;}
      const same=(a:string,b:string)=>a===b||a.replace(/[省市]$/,'')===b.replace(/[省市]$/,'');
      const p=provinces.find(r=>same(r.name,raw.province||''));
      const c=regionCities(p).find(r=>same(r.name,raw.city||''));
      const d=regionDistricts(c).find(r=>r.name===raw.district);
      setProfile({...raw,province:p?.name||'',city:c?.name||'',district:d?.name||''});
      if(!p||!c||!d)setMessage("资料已读取，请重新选取省份、城市和区县后保存。");
    }
    catch(e){setMessage(String(e));}
    finally{setPending(false);}
  }
  async function save(){
    setPending(true);setMessage("");
    try{
      await beforeSave?.();
      await invoke("checkout_command",{op:"save_profile",payload:{profile,locale}});
      const deadline=Date.now()+90000;
      while(Date.now()<deadline){
        await new Promise(r=>setTimeout(r,300));
        const state=await invoke<{busy:boolean;error?:string}>("checkout_command",{op:"status"});
        if(!state.busy){
          if(state.error)throw new Error(state.error);
          setMessage("已加密保存并核对，可以启用自动选购");return;
        }
      }
      throw new Error("保存尚未确认，请稍后读取已保存资料核对");
    }catch(e){setMessage(String(e));}finally{setPending(false);}
  }
  return <fieldset id="contact-profile" disabled={locked||pending} className="grid gap-3 rounded-xl border p-4">
    <legend className="font-semibold px-2">联系与收货资料 · {local.title}</legend>
    <div className="grid gap-3 sm:grid-cols-3">
      {!foreign&&([['province','省份',provinces],['city','城市',cities],['district','区县',districts]] as [string,string,Region[]][]).map(([key,label,options])=><label key={key}>{label}<select aria-label={label} className="block w-full rounded border bg-background text-foreground p-2" value={profile[key]||''} onChange={e=>setProfile(p=>({...p,[key]:e.target.value,...(key==='province'?{city:'',district:''}:key==='city'?{district:''}:{})}))}><option value="">请选择</option>{options.map(r=><option key={r.code}>{r.name}</option>)}</select></label>)}
      {localFields.map(([key,label])=><label key={key}>{label}<Input aria-label={label} autoComplete="off" type={key==='email'?'email':key==='phone'?'tel':'text'} maxLength={key==='id_last4'?4:key==='postal_code'?(foreign?8:6):500} value={profile[key]||''} onChange={e=>setProfile(p=>({...p,[key]:e.target.value,...(key==='family_name'||key==='given_name'?{full_name:''}:{})}))}/></label>)}
    </div>
    <div className="flex gap-2"><Button variant="outline" onClick={()=>void read()}>读取已保存资料</Button><Button onClick={()=>void save()}>保存联系资料</Button></div>
    <p className="text-muted-foreground">{foreign?'本地区资料独立加密保存，不套用大陆手机号或证件。地区名称须与当地官网一致；未匹配时由本人选择。':'填写你本人的资料并保存。资料仅在本机加密保存，证件后四位会保留前导零。'}</p>
    {pending&&<p role="status">正在处理资料，请完成可能出现的系统授权提示…</p>}
    {message&&<p role="status">{message}</p>}
  </fieldset>;
}

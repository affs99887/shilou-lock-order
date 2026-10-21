"""Read-only Apple store/date monitor. No cart, browser control or profile access."""
import copy
import gzip
import html
from html.parser import HTMLParser
import http.cookiejar
import json
import io
import re
import socket
import ssl
import threading
import time
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, build_opener, HTTPSHandler, HTTPCookieProcessor, HTTPRedirectHandler
from urllib.error import HTTPError, URLError
from tls_support import https_context

CHINA = timezone(timedelta(hours=8))
ENDPOINT = "https://www.apple.com.cn/shop/retail/pickup-message"
COLORS = {"星光白色":"Star White", "夜空色":"Night Sky", "白色":"White", "黑色":"Black",
          "银色":"Silver", "勃艮第酒红色":"Burgundy", "冰川蓝色":"Glacier Blue",
          "薰衣草紫色":"Lavender", "青雾蓝色":"Mist Blue", "鼠尾草绿色":"Sage"}

def clean(text):
    return html.unescape(re.sub(r"<[^>]*>", " ", str(text)))

def norm(text):
    return re.sub(r"\s+", "", clean(text)).lower()

def dates_in(text, today=None, allowed=()):
    today = today or datetime.now(CHINA).date()
    text = clean(text)
    found = set()
    full = r"(20\d{2})[-/年](\d{1,2})[-/月](\d{1,2})日?"
    for y, m, d in re.findall(full, text):
        try: found.add(date(int(y), int(m), int(d)).isoformat())
        except ValueError: pass
    rest = re.sub(full, "", text)
    for m, d in re.findall(r"(?<!\d)(\d{1,2})\s*月\s*(\d{1,2})\s*日", rest):
        month,day=int(m),int(d)
        explicit=[v for v in found if date.fromisoformat(v).month==month and date.fromisoformat(v).day==day]
        if explicit:continue
        candidates=[]
        for y in (today.year,today.year+1):
            try:
                candidate=date(y,month,day)
                if 0<=(candidate-today).days<=180:candidates.append(candidate.isoformat())
            except ValueError:pass
        preferred=[v for v in candidates if v in allowed]
        choices=preferred or candidates
        if len(choices)==1:found.add(choices[0])
    # Apple can render Chinese checkout date radios as "October20" with
    # opaque day-only values. Parse full/short English months explicitly;
    # never interpret a bare number as a calendar date.
    months = {name: index for index, names in enumerate((
        ('january','jan'),('february','feb'),('march','mar'),('april','apr'),
        ('may',),('june','jun'),('july','jul'),('august','aug'),
        ('september','sep','sept'),('october','oct'),('november','nov'),('december','dec')),1) for name in names}
    month_pattern = '|'.join(sorted(months,key=len,reverse=True))
    for match in re.finditer(r'(?<![a-z])(' + month_pattern + r')\.?\s*(\d{1,2})(?:st|nd|rd|th)?(?:[ ,]+(20\d{2}))?(?!\d)', text, re.I):
        month, day, year = months[match[1].lower()], int(match[2]), match[3]
        years = (int(year),) if year else (today.year, today.year+1)
        candidates=[]
        for y in years:
            try:
                candidate=date(y,month,day)
                if year or 0 <= (candidate-today).days <= 180:candidates.append(candidate.isoformat())
            except ValueError:pass
        preferred=[v for v in candidates if v in allowed]
        choices=preferred or candidates
        if len(choices)==1:found.add(choices[0])
    for word, days in (("今天",0),("今日",0),("明天",1),("后天",2)):
        if word in text: found.add((today+timedelta(days=days)).isoformat())
    return sorted(found)

class Scripts(HTMLParser):
    def __init__(self):
        super().__init__(); self.active=False; self.buffer=[]; self.values=[]
    def handle_starttag(self, tag, attrs):
        if tag == "script":
            a=dict(attrs); self.active=a.get("type")=="application/json"; self.buffer=[]
    def handle_data(self, data):
        if self.active: self.buffer.append(data)
    def handle_endtag(self, tag):
        if tag=="script" and self.active:
            try: self.values.append(json.loads("".join(self.buffer)))
            except ValueError: pass
            self.active=False

def walk(value, depth=0):
    if depth > 15: return
    if isinstance(value, dict):
        yield value
        for child in value.values(): yield from walk(child, depth+1)
    elif isinstance(value, list):
        for child in value: yield from walk(child, depth+1)
    elif isinstance(value, str) and value.lstrip().startswith(("{", "[")):
        try: yield from walk(json.loads(value), depth+1)
        except ValueError: pass

def discover(source, targets):
    """Only use exact model/capacity/color matches from live first-party JSON."""
    parser=Scripts(); parser.feed(source)
    rows=[r for v in parser.values for r in walk(v) if re.fullmatch(r"[A-Z0-9]+CH/A", str(r.get("partNumber","")))]
    result=[]
    for target in targets:
        titles={norm(target["name"]+target["capacity"]+target["color"]),
                norm(target["name"]+target["capacity"]+COLORS.get(target["color"],""))}
        matches={r["partNumber"]:r for r in rows if norm(r.get("name","")) in titles}
        if len(matches)!=1:
            raise ValueError("官网 SKU 不能唯一匹配所选规格；未使用旧型号或猜测编号")
        sku,r=next(iter(matches.items()))
        price=r.get("price",{}); price=price.get("fullPrice",0) if isinstance(price,dict) else 0
        from stock_labels import safe_product_url
        product_url = safe_product_url(r.get("url")) or safe_product_url(r.get("productUrl"))
        result.append({"sku":sku,"target":copy.deepcopy(target),"price":price,"product_url":product_url})
    return result

def parse_stock(payload, products, store_id, wanted_dates, today=None):
    if not isinstance(payload,dict) or payload.get("head",{}).get("status") not in (200,"200"):
        raise ValueError("库存响应状态无法确认")
    body=payload.get("body",{})
    if not isinstance(body,dict) or body.get("errorMessage"):
        raise ValueError("官网库存服务返回错误")
    stores=[s for s in body.get("stores",[]) if isinstance(s,dict) and s.get("storeNumber")==store_id]
    if len(stores)!=1 or not isinstance(stores[0].get("partsAvailability"),dict):
        raise ValueError("响应没有唯一的所选直营店；未使用附近其他门店")
    parts=stores[0]["partsAvailability"]; results=[]
    for p in products:
        part=parts.get(p["sku"],{}); regular=part.get("messageTypes",{}).get("regular",{})
        quote=" ".join(clean(v) for v in (part.get("pickupSearchQuote",""),regular.get("storePickupQuote",""),regular.get("storePickupQuote2_0","")))
        dates=dates_in(quote,today,wanted_dates); state="unknown"; selected=None
        display=part.get("pickupDisplay")
        title=regular.get("storePickupProductTitle","")
        target=p["target"]
        expected={norm(target["name"]+target["capacity"]+color) for color in (target['color'],COLORS.get(target['color'],target['color']))}
        if title and norm(title) not in expected:
            state="unknown"
        elif display=="unavailable": state="sold_out"
        elif display=="available" and part.get("storePickEligible") is not False:
            # Multiple dates/ranges are not evidence of a specific appointment.
            if len(dates)==1:
                selected=dates[0]; state="available" if selected in wanted_dates else "other_date"
        results.append({**copy.deepcopy(p),"store_id":store_id,"state":state,"pickup_date":selected,
                        "time":time.time(),"source":"apple_pickup","detail":"日期或响应未确认" if state=="unknown" else ""})
    return results

def choose(results, dates, budget, now, interval):
    eligible=[]
    for i,r in enumerate(results):
        price=r.get("price",0)
        if r["state"]=="available" and r.get("pickup_date") in dates and 0<=now-r["time"]<=max(30,interval*2):
            if not isinstance(price,(int,float)) or price<=0: continue
            if price*r["target"].get("quantity",1)>budget: continue
            eligible.append((dates.index(r["pickup_date"]),r.get("config_rank",i),r.get("store_rank",0),i,r))
    return min(eligible,key=lambda x:x[:4])[4] if eligible else None

def parse_recommendations(payload, products, anchor_id, wanted_dates, scope="single", today=None):
    """Use only stores in this official response; never invent nearby cities.

    The bundled directory supplies checkout region metadata, not recommendation
    order. Unrecognized IDs cannot safely be handed to the checkout engine.
    """
    from checkout_stores import STORES
    if scope == "single":
        rows = parse_stock(payload, products, anchor_id, wanted_dates, today)
        stores = [s for s in STORES if s["id"] == anchor_id]
        if len(stores) != 1:
            raise ValueError("基准门店资料不完整")
        for i, row in enumerate(rows):
            row.update(store_name=stores[0]["name"],store_city=stores[0]["city"],store_rank=0,config_rank=i)
        return rows, stores, []
    if scope != "nearby":
        raise ValueError("监控范围无法确认")
    if not isinstance(payload,dict) or payload.get("head",{}).get("status") not in (200,"200"):
        raise ValueError("库存响应状态无法确认")
    body=payload.get("body",{})
    if not isinstance(body,dict) or body.get("errorMessage") or not isinstance(body.get("stores"),list):
        raise ValueError("官网推荐门店响应无法确认")
    directory={s["id"]:s for s in STORES}
    rows=[]; stores=[]; skipped=[]; seen=set()
    for rank, official in enumerate(body["stores"]):
        if not isinstance(official,dict): raise ValueError("官网推荐门店资料不完整")
        sid=official.get("storeNumber")
        if not isinstance(sid,str) or not re.fullmatch(r"R\d+",sid) or sid in seen:
            raise ValueError("官网推荐门店编号不唯一")
        seen.add(sid)
        store=directory.get(sid)
        if store is None:
            skipped.append(sid); continue
        # Each branch retains strict status/SKU/date checking from single-store mode.
        items=parse_stock(payload,products,sid,wanted_dates,today)
        for i,row in enumerate(items):
            row.update(store_name=store["name"],store_city=store["city"],store_rank=rank,config_rank=i)
        rows.extend(items);stores.append(copy.deepcopy(store))
    if not stores: raise ValueError("官网未返回可识别的推荐门店；不会使用旧列表触发选购")
    return rows, stores, skipped

class RestrictedRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed=urlsplit(newurl)
        if parsed.scheme!="https" or parsed.hostname!="www.apple.com.cn":
            raise ValueError("官网监控入口跳转到其他站点；已停止该次请求")
        return super().redirect_request(req,fp,code,msg,headers,newurl)

class Client:
    def __init__(self):
        from pooled_transport import PooledReader
        self.opener=PooledReader()
        self.product_url=None
        self.last_response={}
        self.retry_after=""

    def verified_product(self, url):
        # Set only after live SKU parsing succeeds; never a user-supplied
        # arbitrary Referer or a login/session URL.
        from stock_labels import safe_product_url
        self.product_url=safe_product_url(url)

    def get(self,url):
        self.last_response={};self.retry_after=""
        parsed=urlsplit(url)
        if parsed.scheme!="https" or parsed.hostname!="www.apple.com.cn" or parsed.username or parsed.password or parsed.port not in (None,443) or not parsed.path.startswith("/shop/"):
            raise ValueError("监控请求不是已允许的 Apple 商店入口")
        inventory=parsed.path=="/shop/retail/pickup-message"
        headers={"Accept":"application/json, text/plain, */*" if inventory else "text/html",
                 "Accept-Language":"zh-CN,zh;q=0.9", "Accept-Encoding":"gzip"}
        if inventory and self.product_url:headers["Referer"]=self.product_url
        req=Request(url,headers=headers)
        try:
            with self.opener.open(req,timeout=10) as response:
                self.retry_after=response.headers.get("Retry-After","")
                content_type=response.headers.get("Content-Type","").split(";",1)[0].strip().lower()
                self.last_response={"http_status":response.status,
                    "content_type":content_type if content_type in ("application/json","text/html","text/plain") else "other",
                    "source_page":bool(inventory and self.product_url)}
                age=response.headers.get('Age')
                self.last_response['cache_age_seconds']=int(age) if isinstance(age,str) and age.isascii() and age.isdigit() and len(age)<10 else None
                self.last_response['cache_age_known']=self.last_response['cache_age_seconds'] is not None
                self.last_response['route']=getattr(self.opener,'route','unknown')
                if inventory and self.last_response['cache_age_seconds'] is not None and self.last_response['cache_age_seconds']>30:
                    raise ValueError('库存响应缓存超过 30 秒；本次库存未知，不触发选购')
                raw=response.read(4_000_001)
                self.last_response["response_bytes"]=len(raw)
                if len(raw)>4_000_000: raise ValueError("官网响应过大")
                if raw[:2]==b"\x1f\x8b":
                    with gzip.GzipFile(fileobj=io.BytesIO(raw)) as stream:raw=stream.read(8_000_001)
                if len(raw)>8_000_000: raise ValueError("官网响应过大")
                body=raw.decode("utf-8")
                self.last_response["body_kind"]="json" if body.lstrip().startswith(("{","[")) else "html" if body.lstrip().startswith("<") else "text"
                return body
        except HTTPError as exc:
            self.retry_after=exc.headers.get("Retry-After","") if exc.headers else ""
            self.last_response["http_status"]=exc.code
            raise

class PickupWorker:
    """One bounded read at a time. Owner consumes immutable generation replies."""
    def __init__(self, client=None):
        self.client=client or Client(); self.thread=None; self.reply=None; self.lock=threading.Lock()
        self.products=None; self.key=None
    def submit(self, generation, preferences):
        if self.thread and self.thread.is_alive(): return False
        with self.lock:
            if self.reply is not None: return False
        p=copy.deepcopy(preferences)
        self.thread=threading.Thread(target=self._run,args=(generation,p),daemon=True);self.thread.start();return True
    def _run(self,generation,p):
        from checkout_engine import CATALOG, variants
        from checkout_stores import STORES
        key=(p["model"],tuple(p["colors"]),tuple(p["capacities"]),p["priority"],p["quantity"])
        started=time.monotonic()
        message={"generation":generation,"request_kind":"catalog" if self.key!=key or self.products is None else "stock"}
        stage="catalog_request" if message["request_kind"]=="catalog" else "stock_request"
        try:
            if self.key!=key or self.products is None:
                source=self.client.get(CATALOG[p["model"]]["url"])
                stage="catalog_parse"
                products=discover(source,variants(p))
                self.products=products;self.key=key;message["discovered"]=True
                if isinstance(self.client,Client):self.client.verified_product(CATALOG[p["model"]]["url"])
            else:
                store=next(s for s in STORES if s["id"]==p["store_id"])
                # Up to 16 selected variants, a single request per interval.
                query=[("parts."+str(i),v["sku"]) for i,v in enumerate(self.products)]
                query += [("location",store["postal_code"]),("store",store["id"])]
                raw=self.client.get(ENDPOINT+"?"+urlencode(query))
                stage="stock_decode"
                payload=json.loads(raw)
                stage="stock_parse"
                status=payload.get("head",{}).get("status") if isinstance(payload,dict) else None
                if str(status) in ("403","429","541"):
                    message.update(error="Apple 库存服务拒绝访问",status=int(status),retry_after=self.client.retry_after if isinstance(self.client,Client) else "")
                else:
                    items,stores,skipped=parse_recommendations(payload,self.products,store["id"],p["pickup_dates"],p.get("pickup_scope","single"))
                    message.update(items=items,stores=stores,skipped_stores=skipped)
        except HTTPError as e:
            message.update(error="Apple HTTP "+str(e.code),status=e.code,retry_after=e.headers.get("Retry-After","") if e.headers else "",error_kind="http")
            e.close()
        except URLError as e:
            reason=e.reason
            kind="tls" if isinstance(reason,ssl.SSLError) else "dns" if isinstance(reason,socket.gaierror) else "timeout" if isinstance(reason,TimeoutError) else "network"
            message.update(error={"tls":"HTTPS 证书或握手失败","dns":"官网域名解析失败","timeout":"官网响应超时","network":"官网连接失败"}[kind]+"；本次库存未知",error_kind=kind)
        except TimeoutError:message.update(error="官网响应超时；本次库存未知",error_kind="timeout")
        except ValueError as e: message.update(error=str(e) if type(e) is ValueError else "库存响应无法解析",error_kind="parse")
        except Exception: message.update(error="官网连接或响应处理失败；本次库存未知",error_kind="unknown")
        if message.get("error"):
            message["failure_stage"]=stage
            message.setdefault("error_kind","access" if message.get("status") in (403,429,541) else "unknown")
        if isinstance(self.client,Client):
            message["diagnostics"]=dict(self.client.last_response)
        message["part_count"]=len(self.products or []) if message["request_kind"]=="stock" else 0
        message["duration_seconds"]=max(0,time.monotonic()-started)
        with self.lock: self.reply=message
    def take(self):
        with self.lock: result=self.reply;self.reply=None
        return result

def checkout_date_decision(s, allowed, exhausted=(), policy="specified"):
    """Constrain 0.6.1 pickup scheduling; no silent fallback to another date."""
    from checkout_engine import Decision
    if s.get('pickup_date_busy'):return Decision('wait','等待取货日期加载完成')
    groups=[p for p in s.get("pickup_schedule",[]) if p["key"]=="pickup_date"]
    if groups:
        for p in groups:
            choices=[c for c in p.get("choices",[]) if not c.get("disabled") and c["value"] not in exhausted]
            if policy == "earliest":
                if any(len(dates_in(c.get("label", "") + " " + c.get("value", ""))) != 1 for c in choices):
                    return Decision("attention", "官网日期无法完整识别，不能确认最早可取日期；保留页面请核对")
                matched=[(d[0],c) for c in choices if len(d:=dates_in(c.get("label", "")+" "+c.get("value", "")))==1]
            else:
                matched=[(allowed.index(d[0]),c) for c in choices if len(d:=dates_in(c.get("label","")+" "+c.get("value",""),allowed=allowed))==1 and d[0] in allowed]
            if p.get("disabled"): return Decision("wait","等待取货日期加载")
            known_choices=[c for c in p.get('choices',[]) if not c.get('disabled')]
            if not choices and not known_choices and not p.get('unavailable'):return Decision('wait','取货日期尚未确认')
            if not matched:
                unknown = [c for c in choices if len(dates_in(c.get("label", "") + " " + c.get("value", ""), allowed=allowed)) != 1]
                if unknown:
                    return Decision("attention", "官网取货日期无法明确识别；保留页面，请核对日期，不认定缺货")
                return Decision("sold_out","目标门店没有符合所选日期的取货安排")
            c=min(matched,key=lambda v:v[0])[1]
            if not c.get("selected"):
                return Decision("set" if p["kind"]=="select" else "click", "pickup_date",p["ref"] if p["kind"]=="select" else c["ref"],"pickup_date",c["value"])
    else:
        quotes=[c["label"] for c in s.get("controls",[]) if c.get("group")=="store" and c.get("store_matches") and c.get("selected")]
        dates=dates_in(" ".join(quotes),allowed=allowed)
        if len(dates)!=1: return Decision("attention","官网未明确显示所选门店的取货日期，请核对后继续；不猜测日期")
        if policy != "earliest" and dates[0] not in allowed: return Decision("sold_out","结账取货日期已变化，不在允许日期中；重新监控")
    return None

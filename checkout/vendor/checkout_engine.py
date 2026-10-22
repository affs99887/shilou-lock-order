"""Deterministic checkout policy. No browser, network, secrets or side effects."""
from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional
from checkout_stores import STORES

CATALOG = {
    "iphone-duo": {"name": "iPhone Duo", "colors": ["星光白色", "夜空色"],
                   "capacities": ["256GB", "512GB", "1TB", "2TB"]},
    "iphone-17": {"name": "iPhone 17", "colors": ["白色", "黑色", "薰衣草紫色", "青雾蓝色", "鼠尾草绿色"],
                  "capacities": ["256GB", "512GB"]},
    "iphone-18-pro": {"name": "iPhone 18 Pro", "colors": ["勃艮第酒红色", "银色", "黑色", "冰川蓝色"],
                      "capacities": ["256GB", "512GB", "1TB", "2TB"], "quantity_limit": 2},
    "iphone-18-pro-max": {"name": "iPhone 18 Pro Max", "colors": ["勃艮第酒红色", "银色", "黑色", "冰川蓝色"],
                          "capacities": ["256GB", "512GB", "1TB", "2TB"], "quantity_limit": 2},
}
for _key, _model in CATALOG.items():
    _model["url"] = "https://www.apple.com.cn/shop/buy-iphone/" + ("iphone-18-pro" if _key == "iphone-18-pro-max" else _key)
    # Eligibility is opt-in after verification, not inferred from the name.
    _model["upgrade_enrollment"] = _key in ("iphone-duo", "iphone-18-pro", "iphone-18-pro-max")

DEFAULTS = {
    "checkout_locale": "zh_CN",
    "version": 2, "model": "iphone-duo", "colors": ["星光白色", "夜空色"],
    "capacities": ["256GB", "512GB"], "priority": "color", "max_price": 20000,
    "fulfillment": "delivery_first", "store": "Apple 武汉", "store_id": "", "pickup_city": "", "quantity": 1,
    "release_at": "2026-10-16T20:00:00+08:00", "payment": "Apple Pay",
    "bark_enabled": True,
    "clear_bag_before_purchase": True,
    "applecare": "none", "trade_in": "none", "purchase_plan": "standard", "financing": "full",
    "accessories": "none", "invoice": "personal", "subsidy": "none",
    "installment_months": 0,
    "installment_option": "",
    "auto_submit_order": False,
    "pickup_date_policy": "specified",
    "pickup_dates": ["2026-10-23", "2026-10-24", "2026-10-25"],
    "pickup_interval": 5,
    "pickup_scope": "nearby",
}


def validate_preferences(value: dict) -> dict:
    if not isinstance(value, dict):
        raise ValueError("设置必须是对象")
    p = {k: copy.deepcopy(value.get(k, v)) for k, v in DEFAULTS.items()}
    from regional_checkout import region
    locale = p['checkout_locale']
    region(locale)
    if locale != 'zh_CN' and (p['auto_submit_order'] or p['financing'] != 'manual' or p['payment'] != '官网手动选择'):
        raise ValueError('国际选购须使用当地官网手选付款，不自动提交或套用大陆分期')
    # The old manual choice combined two different programs. Never silently
    # reinterpret a saved user's upgrade entitlement as ordinary Trade In.
    if "purchase_plan" not in value:
        p["purchase_plan"] = "reselect" if value.get("trade_in") == "manual" else "standard"
    if p["purchase_plan"] not in ("standard", "trade_in", "enroll", "upgrade", "reselect"):
        raise ValueError("购机方式无效")
    p["trade_in"] = "manual" if p["purchase_plan"] in ("trade_in", "reselect") else "none"
    if type(p["bark_enabled"]) is not bool:
        raise ValueError("Bark 开关必须是布尔值")
    if type(p["clear_bag_before_purchase"]) is not bool:
        raise ValueError("清空购物袋开关必须是布尔值")
    if type(p['auto_submit_order']) is not bool:
        raise ValueError('自动提交开关必须是布尔值')
    if p["model"] not in CATALOG:
        raise ValueError("不支持的机型；请先验证其官网适配器")
    model = CATALOG[p["model"]]
    for key in ("colors", "capacities"):
        if (not isinstance(p[key], list) or not p[key] or
                any(not isinstance(x, str) or x not in model[key] for x in p[key]) or
                len(set(p[key])) != len(p[key])):
            raise ValueError("颜色和容量必须非空、不重复，且属于所选机型")
    budget_limit = 100000 if locale == 'zh_CN' else 100000000
    if type(p["max_price"]) is not int or not 1 <= p["max_price"] <= budget_limit:
        raise ValueError("请设置本地区货币的有效总预算")
    if p["priority"] not in ("color", "capacity"):
        raise ValueError("排序方式错误")
    if p["fulfillment"] not in ("delivery_first", "delivery", "pickup"):
        raise ValueError("收货方式错误")
    if p["payment"] not in ("Apple Pay", "支付宝", "微信支付", "官网手动选择"):
        raise ValueError("支付方式错误")
    # Presets are observed choices, never authorization to sign credit agreements.
    if p.get('financing')=='official':
        from payment_options import INSTALLMENT_PLANS
        if not isinstance(p.get('installment_option'), str) or p.get('installment_months') not in INSTALLMENT_PLANS.get(p.get('installment_option'), ()):
            raise ValueError('请选择分期渠道下已核验的期数')
        if not isinstance(p['installment_option'],str) or not 1<=len(p['installment_option'])<=160:
            raise ValueError('请先从官网实际读取的选项选择分期渠道')
        if type(p['installment_months']) is not int or not 1<=p['installment_months']<=60:
            raise ValueError('请选择官网实际提供的分期期数')
    else:
        p['installment_months']=0;p['installment_option']=''
    if p['auto_submit_order'] and (p['financing']=='manual' or p['payment'] in ('Apple Pay','官网手动选择') or
                                   any(p.get(k)=='manual' for k in ('applecare','trade_in','accessories','invoice','subsidy'))):
        raise ValueError('自动下单需要明确的全款或官网分期方案，不能包含待人工选择的项目')
    for key, allowed in {"applecare": ("none", "add", "manual"),
                         "trade_in": ("none", "manual"), "financing": ("full", "manual", "official"),
                         "accessories": ("none", "manual"), "invoice": ("personal", "manual"),
                         "subsidy": ("none", "manual")}.items():
        if p[key] not in allowed:
            raise ValueError("选配设置无效：" + key)
    if not isinstance(p["release_at"], str):
        raise ValueError("开售时间错误")
    if p["release_at"]:
        try:
            dt = datetime.fromisoformat(p["release_at"])
            if dt.tzinfo is None:
                raise ValueError()
        except ValueError:
            raise ValueError("开售时间必须包含时区，如 +08:00") from None
    if type(p["quantity"]) is not int or not 1 <= p["quantity"] <= 2:
        raise ValueError("购买数量只能填写 1 或 2，实际可购数量以官网为准")
    if model.get("quantity_limit") and p["quantity"] > model["quantity_limit"]:
        raise ValueError(f'{model["name"]} 官网当前每位顾客限购 {model["quantity_limit"]} 部；不拆单绕过限购')
    selected = next((s for s in STORES if (s["id"] == p["store_id"] if p["store_id"] else s["name"] == p["store"])), None)
    if not selected:
        raise ValueError("请从官方门店选项中选择自提门店")
    p.update(store=selected["name"], store_id=selected["id"], pickup_city=selected["city"])
    p["version"] = 2
    if p["pickup_date_policy"] not in ("specified", "earliest", "by_model"):
        raise ValueError("请选择按型号、指定日期或最早可取日期")
    dates = p["pickup_dates"]
    if not isinstance(dates, list) or not (0 if p["pickup_date_policy"] == "earliest" else 1) <= len(dates) <= 3 or any(not isinstance(d,str) for d in dates) or len(set(dates)) != len(dates):
        raise ValueError("请选择 1–3 个不重复的取货日期，按优先顺序排列")
    from datetime import date
    try:
        if any(not isinstance(d, str) or date.fromisoformat(d).isoformat() != d for d in dates):
            raise ValueError()
    except (ValueError, TypeError):
        raise ValueError("取货日期格式应为 YYYY-MM-DD") from None
    if type(p["pickup_interval"]) is not int or not 5 <= p["pickup_interval"] <= 300:
        raise ValueError("门店检查间隔应为 5–300 秒；遇到限制自动延长")
    if p["pickup_scope"] not in ("single", "nearby"):
        raise ValueError("请选择仅基准门店或官网附近推荐门店")
    return p


# 门店选项文字里写着这些，就算按钮能点也不当作能取货。
STORE_UNAVAILABLE = re.compile(r"不可取货|无法取货|暂不可|不能取货|无货|不可用|售罄")


def resolve_pickup_preferences(plan: dict, model: str) -> dict:
    """Resolve the actual hit's model, never the saved plan's stale model.

    The saved dates remain editable and survive hits from other models.
    """
    p = copy.deepcopy(plan)
    if p.get("pickup_date_policy") == "by_model":
        family = re.sub(r"[-\s]", "", model).lower()
        p["pickup_date_policy"] = "specified" if family == "iphoneduo" else "earliest"
    return p


def purchase_plan_problem(p: dict) -> str:
    plan = p.get("purchase_plan", "standard")
    if plan == "reselect":
        return "旧版折抵／年年焕新选项已拆分，请重新选择购机方式并保存；尚未更改购物袋"
    if plan == "upgrade":
        return "已有年年焕新计划：请通过 Apple Store app 核验资格并升级，或到店办理；使用选购方案中的官方办理入口，尚未更改购物袋"
    if plan == "enroll":
        if not CATALOG.get(p.get("model"), {}).get("upgrade_enrollment"):
            return "当前机型未在已核对的年年焕新新加入名单中；请更换符合条件的机型，或改为普通购买"
        if p.get("applecare") != "add":
            return "新加入年年焕新需要同单加购 AppleCare+；请确认保障方案设置，费用计入订单预算"
    return ""


def variants(p: dict) -> list:
    if p["priority"] == "color":
        pairs = [(c, s) for c in p["colors"] for s in p["capacities"]]
    else:
        pairs = [(c, s) for s in p["capacities"] for c in p["colors"]]
    return [{"model": p["model"], "name": CATALOG[p["model"]]["name"],
             "color": c, "capacity": s, "quantity": p.get("quantity", 1)} for c, s in pairs]


def purchase_targets(p: dict) -> list:
    """Fulfillment is the OUTER priority: all delivery variants, then pickup."""
    methods = ("delivery", "pickup") if p["fulfillment"] == "delivery_first" else (p["fulfillment"],)
    return [{**target, "fulfillment": method, "store": p["store"], "store_id": p.get("store_id", ""),
             "pickup_city": p.get("pickup_city", "")} for method in methods for target in variants(p)]


def normal(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).lower()


def matches_target(text: str, target: dict) -> bool:
    text = normal(text)
    # Avoid iPhone 17 matching iPhone 17e, and Pro matching Pro Max.
    model = normal(target["name"])
    return bool(re.search(re.escape(model) + r"(?=256gb|512gb|1tb|2tb|[^a-z0-9]|$)", text)) and all(
        normal(target[k]) in text for k in ("capacity", "color"))


def retry_after_seconds(raw: Optional[str], now: float) -> float:
    if not raw:
        return 0
    try:
        return max(0, float(raw))
    except ValueError:
        from email.utils import parsedate_to_datetime
        try:
            return max(0, parsedate_to_datetime(raw).timestamp() - now)
        except (ValueError, TypeError, OverflowError):
            return 0


class CircuitBreaker:
    def __init__(self):
        self.failures = 0
        self.until = 0.0

    def reject(self, now: float, retry_after: float = 0) -> float:
        wait = min(30 * 2 ** min(self.failures, 4), 300)
        self.failures += 1
        self.until = max(self.until, now + max(wait, retry_after))
        return self.until

    def success(self):
        self.failures = 0

    def interval(self, p: dict, wall: float) -> float:
        launch = datetime.fromisoformat(p["release_at"]).timestamp() if p["release_at"] else wall
        return 3 if wall >= launch - 60 else 60


@dataclass
class Decision:
    kind: str
    reason: str = ""
    ref: str = ""
    key: str = ""
    value: str = ""


class Attempt:
    """One immutable-config attempt; ambiguous mutations are never replayed."""
    def __init__(self, preferences: dict, target: dict, now: float):
        self.preferences = resolve_pickup_preferences(preferences, target["model"])
        self.target = copy.deepcopy(target)
        self.target["quantity"] = preferences.get("quantity", 1)
        self.target["pickup_date_policy"] = self.preferences.get("pickup_date_policy", "specified")
        if preferences.get("pickup_date_policy") == "by_model":
            self.target["pickup_dates"] = list(self.preferences["pickup_dates"])
        self.pickup_confirmed_dates = []
        self.quantity_total_before = None
        for key in ("store", "store_id", "pickup_city"):
            self.target[key] = preferences.get(key, "")
        self.target['store_names'] = [s['name'] for s in STORES]
        store = next((x for x in STORES if x["id"] == self.target.get("store_id") or x["name"] == self.target.get("store")), None)
        if store:
            district = re.search(r"市([^市]{1,12}?(?:区|县|旗))", store["address"])
            self.target["pickup_region"] = {"province":store["province"], "city":store["city"],
                                            "district":district.group(1) if district else ""}
        # 手动开始时为 same_city：指定门店不能取货，就改选同城其他能取货的门店。
        # 取货地区始终按指定门店填，官网列出的就是它附近的门店。其余情况只认指定门店。
        self.store_choice = preferences.get("store_choice", "fixed")
        self.store_city = store["city"] if store else ""
        self.exhausted_stores = set()
        self.store_none_since = None
        self.store_exhaust_reason = ""
        self.created = now
        self.started = None
        self.last_progress = now
        self.fingerprint = None
        self.pending = None
        self.sent = {}
        self.action_times = {}
        self.bag_verified = False
        self.add_submitted = False
        self.contact_verified = False
        self.fulfillment_verified = False
        self.deferred_pickup_verified = False
        self.payment_verified = False
        self.method = target.get("fulfillment") or ("pickup" if preferences["fulfillment"] == "pickup" else "delivery")
        self.terminal = None
        self.slow_recorded = False
        self.care_wait_since = None
        self.product_care_wait_since = None
        self.product_choice_wait_since = {}
        self.pickup_empty_since = None
        self.pickup_schedule_wait_since = None
        self.pickup_exhausted_dates = set()
        self.stock_candidate = None
        self.stage_entered = None
        self.observed_stage = None
        self.region_actions = {}
        self.region_started = None
        self.region_wait = None
        self.pending_navigation = None
        self.payment_options_wait_since = None

    def reconcile_region(self, s, now):
        """Shared bounded read-back controller; only the adapter selects values.

        Region edits are reversible choices, unlike add/order navigation. Their
        retry ledger must not inherit the one-shot cart-write policy.
        """
        r = s.get("region_picker")
        if not r:
            return None
        if r.get("context") != self.method:
            return self.finish("attention", "地区选择用途与当前收货方式不一致；未修改地址")
        if not r.get("configured"):
            return self.finish("attention", "目标省市区资料不完整；不会沿用官网旧地址")
        if r.get("matched") and (r.get("kind") == "fields" or not r.get("open")):
            self.region_started = None
            self.region_wait = None
            return None
        if self.region_started is None:
            self.region_started = now
        if r.get("reason") in ("ambiguous", "missing"):
            return Decision("manual_region", "官网地区无法唯一匹配，请在 Safari 手动选择省、市、区；确认选好后自动继续")
        if now - self.region_started >= 45:
            return Decision("manual_region", "地区未能自动确认，请在 Safari 手动选择省、市、区；选好后自动继续")
        action = r.get("next")
        # ALL levels commit asynchronously, not just district. The editor can
        # briefly report closed or the old level while its child list loads.
        # Don't reopen/repeat/backtrack during that interval. A verified child
        # level may progress immediately; unrelated fingerprint changes aren't
        # evidence that the destination update has completed.
        prefix = "region_" + self.method + "_"
        levels = ("province", "city", "district")
        recent = [(stamp, levels.index(level)) for level in levels
                  for count, stamp in [self.region_actions.get(prefix + level, (0, -100))]
                  if count]
        if r.get("kind") == "tabs" and recent:
            committed_at, previous_level = max(recent)
            next_step = (action or {}).get("key", "").removeprefix(prefix).removeprefix("tab_")
            advances = r.get("open") and next_step in levels and levels.index(next_step) > previous_level
            if now - committed_at < 12 and not advances:
                return Decision("wait", "地区已提交，等待官网更新下级选项、地区摘要和门店列表")
        if not action:
            marker = (r.get("context"), r.get("step"), r.get("kind"))
            if self.region_wait is None or self.region_wait[0] != marker:
                self.region_wait = (marker, now)
            recovery = r.get("recovery") or {}
            if now - self.region_wait[1] >= 12 and recovery.get("key") == prefix + "tab_province":
                action = r["recovery"]
            else:
                if now - self.region_wait[1] >= 5:
                    return Decision("manual_region", "地区选项未能匹配，请在 Safari 手动选择；保留当前任务，选好后自动继续")
                return Decision("wait", "等待省市区下级选项加载并核对目标地区；未沿用旧地址")
        else:
            self.region_wait = None
        key = action.get("key", "")
        if key == prefix + "close":
            return Decision("wait", "等待地区选项加载；不会关闭正在切换的地区选择框")
        if not key.startswith("region_" + self.method + "_") or not action.get("ref"):
            return self.finish("attention", "地区操作未通过校验；保留页面")
        count, last = self.region_actions.get(key, (0, -100))
        if now - last < 5:
            return Decision("wait", "等待地区选择回读确认；不重复点击")
        if count >= 3 or sum(v[0] for v in self.region_actions.values()) >= 12:
            return Decision("manual_region", "地区切换重试后仍未生效，请在 Safari 手动选择；选好后自动继续")
        self.region_actions[key] = (count + 1, now)
        self.pending = (key, now)
        return Decision("set", key, action["ref"], key=key)

    def flexible_store(self) -> bool:
        return self.store_choice == "same_city" and self.method == "pickup" and bool(self.store_city)

    @staticmethod
    def store_of(control: dict):
        """认出门店选项是哪家店：优先用选项自带的门店编号，没有编号时要求文字与店名完全一致（与页面脚本同一规则）。"""
        store_id = control.get("store_id") or ""
        if store_id:
            return next((x for x in STORES if x["id"] == store_id), None)
        label = normal(control.get("label", "")).removeprefix("apple")
        return next((x for x in STORES if label and normal(x["name"]).removeprefix("apple") == label), None)

    def usable_stores(self, s: dict) -> list:
        """同城、未排除、可点且没写着不能取货的门店，保持官网列表顺序。"""
        found = []
        for c in s.get("controls", []):
            if c.get("group") != "store" or c.get("disabled"):
                continue
            store = self.store_of(c)
            if (store and store["city"] == self.store_city and store["id"] not in self.exhausted_stores
                    and not STORE_UNAVAILABLE.search(normal(c.get("label", "")))):
                found.append((store, c))
        return found

    def use_store(self, store: dict):
        """改用另一家门店。上一家门店的日期和时段记录不适用于新门店，一并清掉。"""
        self.target.update(store=store["name"], store_id=store["id"])
        self.preferences.update(store=store["name"], store_id=store["id"])
        self.pickup_exhausted_dates = set()
        self.pickup_confirmed_dates = []
        self.pickup_empty_since = None
        self.pickup_schedule_wait_since = None
        self.fulfillment_verified = False
        for key in [k for k in self.sent if k.startswith(("pickup_date:", "pickup_time:"))]:
            del self.sent[key]
            self.action_times.pop(key, None)

    def click_store(self, control: dict, now: float) -> Optional[Decision]:
        """选门店是可以改的选择，按门店分别计次：同一家点了两次仍没选上才停下。"""
        action = "store:" + self.target["store_id"]
        if control.get("selected"):
            return None
        count = self.sent.get(action, 0)
        if count and now - self.action_times.get(action, now) < 5:
            return Decision("wait", "等待门店选择生效：" + self.target["store"])
        if count >= 2:
            return self.finish("attention", "门店选项未生效：" + self.target["store"])
        self.sent[action] = count + 1
        self.action_times[action] = now
        self.pending = (action, now)
        return Decision("click", action, control["ref"])

    def ensure_store(self, s: dict, now: float, reason: str = "") -> Optional[Decision]:
        """手动开始时保证选中的是能取货的门店。

        页面上已选中一家能用的同城门店就直接采用；否则当前门店能用就不动；再否则改选
        官网列表里第一家能用的同城门店。选定的就是当前门店时返回 None，交给原有流程。
        连续 2 秒一家能用的都没有才结束。换门店后这次读到的页面数据是按旧门店算的，
        所以只发出点击或等待，下一轮重新读取。
        """
        stores = [c for c in s.get("controls", []) if c.get("group") == "store"]
        if not stores and not reason:
            return None  # 门店列表还没出来，交给原有逻辑等待或选择地区
        if stores and not any(self.store_of(c) for c in stores):
            return None  # 一家门店都认不出来：不能据此判断有没有货，交给原有流程等待或超时
        usable = self.usable_stores(s)
        if not usable:
            if self.store_none_since is None:
                self.store_none_since = now
            if now - self.store_none_since < 2:
                return Decision("wait", "同城门店暂时都不可取货，再次确认")
            last = reason or self.store_exhaust_reason
            return self.finish("sold_out", (last + "；" if last else "") + "同城其他门店也都不可取货")
        self.store_none_since = None
        picked = ([x for x in usable if x[1].get("selected")] or
                  [x for x in usable if x[0]["id"] == self.target.get("store_id")] or usable)
        store, control = picked[0]
        if sum(1 for x in usable if x[0]["id"] == store["id"]) > 1:
            return self.finish("attention", "门店选项匹配不唯一，请核对官网门店")
        if store["id"] == self.target.get("store_id"):
            return None
        self.use_store(store)
        return self.click_store(control, now) or Decision("wait", "已改选" + store["name"] + "，重新读取页面")

    def next_store(self, s: dict, now: float, reason: str) -> Decision:
        """当前门店不能取货：手动开始时排除它、换同城下一家；其余情况按原规则结束本次尝试。"""
        if not self.flexible_store():
            return self.finish("sold_out", reason)
        self.exhausted_stores.add(self.target.get("store_id", ""))
        self.store_exhaust_reason = reason
        return self.ensure_store(s, now, reason) or Decision("wait", "重新读取门店列表")

    def elapsed(self, now: float) -> float:
        return round(now - self.started, 3) if self.started is not None else 0

    def finish(self, kind, reason):
        self.terminal = Decision(kind, reason)
        return self.terminal

    def fulfillment_wait_reason(self, s):
        """Safe diagnostic text; never includes DOM labels or contact values."""
        wanted = "我要取货" if self.method == "pickup" else "为我送货"
        if not s.get(self.method + "_selected"):
            choices = [c for c in s.get("controls", []) if normal(c.get("label", "")) == normal(wanted)
                       and c.get("role") in ("radio", "option", "button")]
            if not choices:
                return "未识别到" + wanted + "控件"
            if len(choices) > 1:
                return wanted + "控件匹配不唯一"
            if choices[0].get("disabled"):
                return wanted + "控件暂不可用；不能据此认定目标门店无货"
            return wanted + "尚未确认选中"
        if self.method == "pickup":
            for key, label in (("store", "目标门店"), ("pickup_date", "取货日期"), ("pickup_time", "取货时段")):
                if not s.get(key + "_selected"):
                    pickers = [p for p in s.get("pickup_schedule", []) if p.get("key") == key and not p.get("selected")]
                    if pickers:
                        if any(p.get("unavailable") for p in pickers):
                            return label + "当前没有可用选项"
                        if any(p.get("disabled") or not p.get("choices") for p in pickers):
                            return label + "选项仍在加载或暂不可用"
                    return label + "尚未确认"
        return "收货方式已选中，等待后续信息或继续按钮"

    def decide(self, s: dict, now: float) -> Decision:
        if self.terminal:
            return self.terminal
        problem = purchase_plan_problem(self.preferences)
        if problem:
            return self.finish("attention", problem)
        stage = s.get("stage", "UNKNOWN")
        if s.get("http_status") in (403, 429, 541) or stage == "BLOCKED":
            return self.finish("cooldown", "Apple 拒绝访问，统一冷却")
        if stage in ("LOGIN", "CAPTCHA", "FOREIGN", "LOST_TAB", "SESSION_EXPIRED"):
            return self.finish("attention", "需要人工处理：" + stage)
        if stage == "ORDER_PLACED":
            return self.finish("attention", "检测到订单已提交；停止自动操作，不再次下单或支付")
        # Strong, scoped stock notices must survive two reads. Recognize them
        # before generic modal/time-out handling, but never before auth/block
        # or submitted-order guards. No modal button is clicked to recover.
        stock_issue = s.get("stock_issue")
        if stock_issue in ("all", self.method):
            if self.stock_candidate is None or self.stock_candidate[0] != stock_issue:
                self.stock_candidate = (stock_issue, now)
            if now - self.stock_candidate[1] < 1:
                return Decision("wait", "官网提示库存变化，正在二次确认；暂不点击或加购")
            return self.finish("sold_out", "已连续确认当前结账的缺货提示；按配置顺序重新尝试")
        self.stock_candidate = None
        if s.get("sold_out") or s.get("variant_unavailable"):
            return self.finish("sold_out", "目标已售罄")
        if s.get("diagnostics", {}).get("visible_dialog"):
            return self.finish("attention", "出现弹窗，需要核对内容；未自动确认协议或地址变更")
        if self.pending_navigation:
            departing, sent_at = self.pending_navigation
            if stage in (departing, "LOADING", "UNKNOWN"):
                # Apple resets/removes the outgoing form while its request is
                # pending. Those fields no longer represent choices to make.
                # A fingerprint change is not proof the next step has loaded.
                if s.get("errors"):
                    return self.finish("attention", "官网未接受上一步信息，请核对页面校验提示；未重复点击继续")
                if now - sent_at >= 30:
                    return self.finish("attention", "官网步骤切换超过 30 秒，已保留页面；未重复填写或点击继续")
                return Decision("wait", "已完成本步核对，等待官网切换下一步；不重复填写或选择")
            self.pending_navigation = None
        # Address failures hand off before generic stall timers. Do not mutate
        # a region before the requested fulfillment method is actually selected.
        r = s.get("region_picker")
        if self.bag_verified and r and (stage == "CONTACT" or (stage == "FULFILLMENT" and s.get(self.method + "_selected"))):
            if r.get("reason") in ("missing", "ambiguous") or (not r.get("matched") and now-self.last_progress >= 25):
                return self.reconcile_region(s, now) if r.get("reason") in ("missing", "ambiguous") else Decision("manual_region", "地区更新未能确认，请在 Safari 手动选择；选好后自动继续")
        # Evaluate a fresh read-back before declaring a stall. The browser call
        # may have completed while an asynchronous date/slot update was in flight.
        incoming = s.get("fingerprint", "")
        if self.fingerprint is not None and incoming and incoming != self.fingerprint:
            self.last_progress = now
            self.pending = None
        if self.started is not None and now - self.last_progress >= 30:
            detail = "：" + self.fulfillment_wait_reason(s) if stage == "FULFILLMENT" else ""
            return self.finish("attention", "连续 30 秒没有进展" + detail + "；保留页面，不重复加购")
        if stage == "NOT_FOUND":
            return self.finish("inspect_bag" if self.add_submitted else "canonical", "页面不存在，核对购物袋或官方入口")
        if stage not in ("LOADING", "UNKNOWN") and stage != self.observed_stage:
            self.observed_stage, self.stage_entered = stage, now
        if self.stage_entered is not None and now - self.stage_entered >= 90:
            return self.finish("attention", "当前步骤超过 90 秒未完成；可能为加载或校验异常，未认定缺货，未重复加购。请核对官网后继续")
        if s.get("purchasable") and self.started is None:
            self.started = now
        fingerprint = s.get("fingerprint", "")
        if fingerprint != self.fingerprint:
            self.fingerprint = fingerprint
            self.last_progress = now
            self.pending = None
        if stage == "WAIT_RELEASE":
            return Decision("monitor", "等待官方开放购买")
        if stage == "LOADING":
            return Decision("wait", "页面加载中")
        if self.pending and now - self.pending[1] < 5:
            return Decision("wait", "等待上一步生效")
        if stage == "UNKNOWN":
            if now - self.last_progress >= 30:
                return self.finish("attention", "未知页面持续 30 秒；已保留现场和脱敏诊断")
            return Decision("wait", "只读重新识别页面；不刷新、不重复点击（最多 30 秒）")

        def control(labels, roles=None):
            for label in labels:
                found = [c for c in s.get("controls", []) if normal(c["label"]) == normal(label)
                         and not c.get("disabled") and (roles is None or c.get("role") in roles)]
                if found:
                    return found[0]
            return None

        def click(c, action):
            if c.get("selected"):
                return None
            count = self.sent.get(action, 0)
            if count and now - self.action_times.get(action, now) < 5:
                return Decision("wait", "等待操作结果：" + action)
            # Navigation and cart writes are never replayed. Choices can retry once,
            # only after a fresh snapshot confirms that they are still unselected.
            is_choice = c.get("role") in ("radio", "option")
            if count >= (2 if is_choice else 1):
                if not is_choice:
                    return Decision("wait", "已发出操作，等待导航确认（不重复点击）：" + action)
                return self.finish("attention", "选项未生效：" + action)
            self.sent[action] = count + 1
            self.action_times[action] = now
            self.pending = (action, now)
            if action in ("continue_FULFILLMENT", "continue_CONTACT", "continue_PAYMENT"):
                self.pending_navigation = (stage, now)
            if action in ("add", "product_continue"):
                self.add_submitted = True
            return Decision("click", action, c["ref"])

        def schedule_choice(picker, option):
            # Slot values repeat across dates; retries belong to a date and
            # semantic picker, not an ephemeral DOM ref or the whole checkout.
            dates = tuple(tuple(c.get("value", "") for c in p.get("choices", []) if c.get("selected"))
                          for p in s.get("pickup_schedule", []) if p.get("key") == "pickup_date")
            context = repr(dates) if picker["key"] == "pickup_time" else ""
            action = picker["key"] + ":" + picker.get("id", picker["ref"]) + ":" + context + ":" + option["value"]
            if picker.get("kind") != "select":
                return click(option, action)
            count = self.sent.get(action, 0)
            if count and now - self.action_times.get(action, now) < 5:
                return Decision("wait", "等待取货选项校验生效")
            if count >= 2:
                return self.finish("attention", "取货日期或签到时段未通过官网校验；已保留现场")
            self.sent[action] = count + 1
            self.action_times[action] = now
            self.pending = (action, now)
            return Decision("set", action, picker["ref"], key=picker["key"], value=option["value"])

        if stage == "PRODUCT":
            def wait_for_choice(key):
                since=self.product_choice_wait_since.setdefault(key,now)
                label={"model":"机型","color":"颜色","capacity":"容量"}[key]
                if now-since >= 30:
                    return self.finish("attention", "目标"+label+"选项持续 30 秒未就绪；未认定缺货，已保留现场")
                return Decision("wait", "等待目标"+label+"选项加载并可用；不能仅凭禁用状态认定缺货")
            models = [c for c in s.get("controls", []) if c.get("group") == "model"]
            if models:
                choices = [c for c in models if normal(c["label"]) == normal(self.target["name"])]
                if len(choices) != 1:
                    return self.finish("attention", "无法唯一确认目标机型选项")
                if choices[0].get("disabled"):
                    return wait_for_choice('model')
                if not choices[0].get("selected"):
                    return click(choices[0], "model")
                self.product_choice_wait_since.pop('model',None)
            if s.get("product_model") and normal(s["product_model"]) != normal(self.target["name"]):
                return self.finish("attention", "打开的商品机型与目标不符")
            for key in ("color", "capacity"):
                c = control([self.target[key]], {"radio", "option"})
                if c and not c.get("selected"):
                    return click(c, key)
                if not c or not c.get("selected"):
                    return wait_for_choice(key)
                self.product_choice_wait_since.pop(key,None)
            if self.preferences.get("trade_in", "none") == "manual" and not s.get("trade_in_selected"):
                return self.finish("attention", "请在官网完成旧设备估价并选择折抵方案，再继续；不会替你声明设备状况")
            if self.preferences.get("trade_in", "none") == "none":
                c = control(["不折抵换购"], {"radio", "option"})
                if c and not c.get("selected"):
                    return click(c, "不折抵换购")
            care = self.preferences.get("applecare", "none")
            all_care_options = [c for c in s.get("controls", []) if c.get("group") == "applecare"]
            care_options = [c for c in all_care_options if not c.get("disabled")]

            def wait_for_care():
                # Product options are asynchronously rebuilt after trade-in and
                # capacity changes. Zero enabled choices is not ambiguity.
                # Use a dedicated deadline so unrelated DOM changes cannot
                # keep the attempt alive forever. Never advance to the bag.
                if self.product_care_wait_since is None:
                    self.product_care_wait_since = now
                if now - self.product_care_wait_since >= 30:
                    return self.finish("attention", "AppleCare+ 选项持续 30 秒未就绪，请在官网核对；未跳过保障选择或加购")
                return Decision("wait", "等待 AppleCare+ 选项加载并可用；不会跳过保障选择")

            if care == "none":
                c = control(["不加 AppleCare+ 服务计划"], {"radio", "option"})
                if not c:
                    return wait_for_care()
                if not c.get("selected"):
                    return click(c, "不加 AppleCare+ 服务计划")
            elif care == "add":
                # Include disabled alternatives when checking uniqueness: one
                # enabled plan plus another still loading is not yet unique.
                paid = [c for c in all_care_options if c.get("care_paid")]
                if not paid or any(c.get("disabled") for c in paid):
                    return wait_for_care()
                chosen = [c for c in paid if c.get("selected")]
                if len(chosen) == 1:
                    paid = chosen
                if len(paid) != 1:
                    return self.finish("attention", "无法唯一确认 AppleCare+ 方案，请在官网核对；不会擅自更换保障方案")
                if not paid[0].get("selected"):
                    return click(paid[0], "applecare_add")
            else:
                if not care_options:
                    return wait_for_care()
                if not any(c.get("selected") for c in care_options):
                    return self.finish("attention", "请在官网选择 AppleCare+ 方案，再继续")
            self.product_care_wait_since = None
            c = control(["添加到购物袋", "加入购物袋", "继续"], {"button", "link"})
            if c:
                if s.get("product_price", 0) <= 0:
                    return self.finish("attention", "商品价格未确认")
                if s["product_price"] * self.target["quantity"] > self.preferences["max_price"]:
                    return self.finish("over_budget", "超预算，仅记录")
                return click(c, "add" if "购物袋" in c["label"] else "product_continue")
        elif stage == "ATTACH":
            c = control(["查看购物袋"], {"button", "link"})
            if c:
                return click(c, "view_bag")
        elif stage == "BAG":
            if s.get("errors"):
                return self.finish("attention", "购物袋存在官网校验错误，请核对数量和库存；未点击结账")
            if s.get("bag_busy"):
                return Decision("wait", "等待购物袋更新完成")
            items = s.get("items", [])
            if (not items or any(not i.get("title") for i in items)) and now - self.last_progress < 5:
                return Decision("wait", "等待购物袋商品渲染")
            phones = [i for i in items if matches_target(i.get("title", ""), self.target)]
            extras = [i for i in items if i not in phones]
            wants_extras = self.preferences.get("accessories", "none") == "manual"
            if not phones or (extras and not wants_extras):
                return self.finish("attention", "购物袋含其他商品或目标缺失；未自动删除")
            wanted_quantity = self.target["quantity"]
            split_rows = len(phones) > 1
            if split_rows and not (wanted_quantity == 2 and len(phones) == 2 and
                    self.sent.get("quantity") and all(type(i.get("quantity")) is int and i["quantity"] == 1 for i in phones) and
                    len({normal(i["title"]) for i in phones}) == 1):
                return self.finish("attention", "购物袋有多条商品，无法确认是本次数量变更后的官网拆行；未重复加购或修改数量")
            if split_rows:
                care = self.preferences.get("applecare", "none")
                if (care == "add" and not all(i.get("applecare_included") is True for i in phones)) or (
                        care == "none" and any(i.get("applecare_included") is not False for i in phones)):
                    return self.finish("attention", "官网将数量拆行后，每台 AppleCare+ 尚未与方案一致；未重复添加或取消保障")
            if wants_extras:
                if not extras:
                    return self.finish("attention", "已到购物袋：请按需要添加配件，完成后点击继续；不会随机购买配件")
                if any(not i.get("title") or
                       re.match(r"^iphone(?:duo|air|se|\d{1,2})(?:e|pro|max)*(?:\d+gb|\d+tb)", normal(i["title"])) or
                       type(i.get("quantity")) is not int or i["quantity"] < 1 for i in extras):
                    return self.finish("attention", "配件名称或数量无法安全确认，请核对购物袋")
                self.target["extras"] = [{"title":i["title"],"quantity":i["quantity"]} for i in extras]
            item = phones[0]
            actual_quantity = sum(i["quantity"] for i in phones) if split_rows else item.get("quantity")
            if actual_quantity != wanted_quantity:
                if self.sent.get("quantity"):
                    if now - self.action_times["quantity"] < 15:
                        return Decision("wait", "等待官网确认购买数量；不会重复加购")
                    return self.finish("attention", "官网未接受目标数量；未减少数量或重复加购")
                if not item.get("quantity_ref") or wanted_quantity not in item.get("quantity_options", []):
                    return self.finish("attention", "官网当前不提供目标数量；请核对库存或限购，未减少数量")
                if item.get("quantity_disabled"):
                    return Decision("wait", "等待官网数量选项可用")
                self.sent["quantity"] = 1
                self.action_times["quantity"] = now
                self.quantity_total_before = s.get("total")
                self.pending = ("quantity", now)
                return Decision("set", "quantity", item["quantity_ref"], value=str(wanted_quantity))
            if self.sent.get("quantity") and (s.get("bag_busy") or s.get("total") == self.quantity_total_before or not s.get("total")):
                if now - self.action_times["quantity"] < 15:
                    return Decision("wait", "等待数量变更后的订单总价；未点击结账")
                return self.finish("attention", "数量已变化但官网总价未确认；请核对购物袋")
            total = s.get("total", 0)
            care = self.preferences.get("applecare", "none")
            care_state = s.get("bag_applecare")
            care_change = "add" if care == "add" and care_state != "included" else "remove" if care == "none" and care_state == "included" else ""
            if care_change:
                action = "bag_care_" + care_change
                if self.sent.get(action):
                    # Never repeat a cart mutation with an ambiguous result.
                    if now - self.action_times[action] < 15:
                        return Decision("wait", "正在核对购物袋 AppleCare+ 变更结果；不会重复操作")
                    return self.finish("attention", "AppleCare+ 变更尚未确认，已保留购物袋；未重复添加或移除")
                choices = [x for x in s.get("bag_care_actions", []) if x.get("kind") == care_change]
                if len(choices) == 1:
                    choice = choices[0]
                    c = next((c for c in s.get("controls", []) if c.get("ref") == choice.get("ref") and not c.get("disabled")), None)
                    if c and total > 0:
                        if care_change == "add":
                            price = choice.get("price", 0)
                            if price <= 0:
                                return self.finish("attention", "AppleCare+ 价格未确认，不自动添加")
                            if total + price * wanted_quantity > self.preferences["max_price"]:
                                return self.finish("over_budget", "添加 AppleCare+ 后超预算，仅记录")
                        return click(c, action)
            if care == "add" and care_state != "included":
                if self.care_wait_since is None:
                    self.care_wait_since = now
                if now - self.care_wait_since < 5:
                    return Decision("wait", "等待购物袋 AppleCare+ 明细加载；不重复加购")
                return self.finish("attention", "购物袋尚未确认包含 AppleCare+，请本人核对")
            self.care_wait_since = None
            if care == "none" and care_state == "included":
                return self.finish("attention", "购物袋包含未选择的 AppleCare+，请本人核对")
            if not total:
                if now - self.last_progress < 5:
                    return Decision("wait", "等待购物袋总价渲染")
                return self.finish("attention", "购物袋总价未确认")
            if total > self.preferences["max_price"]:
                return self.finish("over_budget", "购物袋超预算，仅记录")
            self.bag_verified = True
            if self.started is None:
                # An existing cart can enter here without visiting PRODUCT.
                # Start timing after its full checks, so later pickup choices
                # are not mistaken for a product page that never loaded.
                self.started = now
            c = control(["结账", "前往结账"], {"button", "link"})
            if c:
                return click(c, "checkout")
        elif stage == "FULFILLMENT":
            if not self.bag_verified:
                return self.finish("attention", "尚未核对购物袋，不能跳过检查")
            if self.method == "delivery" and s.get("delivery_unavailable"):
                # The service advances the global queue. Never prefer this
                # variant's pickup over a lower-ranked deliverable variant.
                return self.finish("sold_out", "当前配置配送不可用")
            wanted = "我要取货" if self.method == "pickup" else "为我送货"
            matches = [c for c in s.get("controls", []) if normal(c.get("label", "")) == normal(wanted)
                       and c.get("role") in ("radio", "option", "button") and not c.get("disabled")]
            if len(matches) > 1 and not s.get(self.method + "_selected"):
                return self.finish("attention", self.fulfillment_wait_reason(s) + "；未点击任何选项")
            c = control([wanted], {"radio", "option", "button"})
            if c and not c.get("selected"):
                return click(c, "method_" + self.method)
            if not s.get(self.method + "_selected"):
                return Decision("wait", self.fulfillment_wait_reason(s))
            region_decision = self.reconcile_region(s, now)
            if region_decision:
                return region_decision
            if self.method == "pickup":
                if self.flexible_store() and not s.get("pickup_region_open"):
                    store_decision = self.ensure_store(s, now)
                    if store_decision:
                        return store_decision
                if (self.target.get("pickup_dates") or self.preferences.get("pickup_date_policy") == "earliest") and s.get("store_selected"):
                    from pickup_monitor import checkout_date_decision
                    date_decision = checkout_date_decision(s, self.preferences["pickup_dates"], self.pickup_exhausted_dates, self.preferences.get("pickup_date_policy", "specified"))
                    if date_decision:
                        if date_decision.kind in ("set", "click"):
                            pickers = [p for p in s.get("pickup_schedule", []) if p.get("key") == "pickup_date"]
                            for p in pickers:
                                for c in p.get("choices", []):
                                    if c.get("value") == date_decision.value and (p["ref"] == date_decision.ref or c["ref"] == date_decision.ref):
                                        return schedule_choice(p, c)
                            return self.finish("attention", "取货日期控件变化，请核对")
                        if date_decision.kind == "attention" and not s.get("pickup_schedule"):
                            if self.pickup_schedule_wait_since is None:
                                self.pickup_schedule_wait_since = now
                            if now - self.pickup_schedule_wait_since < 12:
                                return Decision("wait", "门店已选中，等待取货日期面板和日期摘要加载（最多 12 秒）")
                        if date_decision.kind == "sold_out" and self.flexible_store():
                            return self.next_store(s, now, self.target["store"] + "：" + date_decision.reason)
                        return date_decision
                    self.pickup_schedule_wait_since = None
                    if self.preferences.get("pickup_date_policy") == "earliest":
                        from pickup_monitor import dates_in
                        chosen = [c.get("label", "") + " " + c.get("value", "")
                                  for p in s.get("pickup_schedule", []) if p.get("key") == "pickup_date"
                                  for c in p.get("choices", []) if c.get("selected") and not c.get("disabled")]
                        if not chosen:
                            chosen = [c.get("label", "") for c in s.get("controls", [])
                                      if c.get("group") == "store" and c.get("store_matches") and c.get("selected")]
                        self.pickup_confirmed_dates = sorted({d for label in chosen for d in dates_in(label)})
                if s.get("pickup_unavailable"):
                    return self.next_store(s, now, self.preferences["store"] + "自提不可用")
                if s.get("pickup_region_open"):
                    step = s.get("pickup_region_step", "")
                    value = self.target.get("pickup_region", {}).get(step, "")
                    if not value:
                        return self.finish("attention", "无法确认官网地区选择层级或目标门店地区；不会使用收货地址替代")
                    options = [c for c in s.get("controls", []) if c.get("role") == "button"
                               and normal(c.get("label", "")) == normal(value) and not c.get("disabled")]
                    if len(options) > 1:
                        return self.finish("attention", "官网自提地区选项匹配不唯一")
                    if options:
                        return click(options[0], "pickup_region_" + step)
                    return Decision("wait", "等待目标门店所在地区的官网选项加载")
                for key in ("store", "pickup_date", "pickup_time"):
                    if s.get(key + "_selected"):
                        continue
                    if key != "store" and s.get("pickup_schedule_not_required"):
                        continue
                    pickers = [p for p in s.get("pickup_schedule", []) if p.get("key") == key and not p.get("selected")]
                    if key != "store" and pickers:
                        picker = pickers[0]
                        available = [c for c in picker.get("choices", []) if not c.get("disabled") and not c.get("selected")]
                        if not picker.get("disabled") and available:
                            self.pickup_empty_since = None
                            if key == "pickup_time" and self.preferences.get("pickup_date_policy") == "earliest":
                                def slot_order(option):
                                    match=re.search(r"(\d{1,2})[:：]([0-5]\d)",option.get("label", ""))
                                    return int(match[1])*60+int(match[2]) if match and int(match[1])<24 else 24*60
                                available.sort(key=slot_order)
                            return schedule_choice(picker, available[0])
                        # Only explicit unavailability permits trying a later
                        # date; a loading/disabled/unknown selector is NOT stock.
                        if key == "pickup_time" and picker.get("unavailable"):
                            dates = [p for p in s.get("pickup_schedule", []) if p.get("key") == "pickup_date"]
                            if len(dates) != 1:
                                return self.finish("attention", "当前签到时段不可用，且存在多个日期组；请核对各商品的取货安排")
                            current = tuple(c["value"] for c in dates[0].get("choices", []) if c.get("selected"))
                            marker = (dates[0]["ref"], current)
                            if self.pickup_empty_since is None or self.pickup_empty_since[0] != marker:
                                self.pickup_empty_since = (marker, now)
                            if now - self.pickup_empty_since[1] < 5:
                                return Decision("wait", "当前日期暂无签到时段，再次确认后尝试下一日期")
                            self.pickup_exhausted_dates.update(current)
                            next_dates = [c for c in dates[0].get("choices", []) if not c.get("disabled") and c["value"] not in self.pickup_exhausted_dates]
                            if self.target.get("pickup_dates") and self.preferences.get("pickup_date_policy") != "earliest":
                                from pickup_monitor import dates_in
                                next_dates = [c for c in next_dates if len(ds := dates_in(c.get("label", "") + " " + c["value"])) == 1 and ds[0] in self.preferences["pickup_dates"]]
                            if next_dates:
                                return schedule_choice(dates[0], next_dates[0])
                            return self.next_store(s, now, "目标门店当前可选日期均无签到时段；结束本次尝试")
                        return Decision("wait", self.fulfillment_wait_reason(s))
                    options = [c for c in s.get("controls", []) if c.get("group") == key
                               and not c.get("disabled")]
                    if key == "store":
                        options = [c for c in options if c.get("store_matches", normal(c["label"]) == normal(self.preferences["store"]))]
                        if len(options) > 1:
                            return self.finish("attention", "目标门店匹配不唯一，请核对官网门店")
                    if options:
                        if key == "store" and self.flexible_store():
                            return self.click_store(options[0], now) or Decision("wait", "等待门店选择生效")
                        return click(options[0], key)  # Official chronological order.
                    if key == "store":
                        if s.get("region_picker"):
                            return Decision("wait", "地区已核对，等待目标门店列表更新；不重复打开地区菜单")
                        editors = [c for c in s.get("controls", []) if c.get("group") == "pickup_region_edit" and not c.get("disabled")]
                        if len(editors) == 1 and not self.sent.get("pickup_region_edit"):
                            return click(editors[0], "pickup_region_edit")
                        locations = [f for f in s.get("fields", []) if f.get("key") == "pickup_location"]
                        if len(locations) == 1 and locations[0].get("configured"):
                            f = locations[0]
                            if not f.get("matches"):
                                action = "fill:pickup_location"
                                if self.sent.get(action, 0) >= 2:
                                    return self.finish("attention", "门店搜索地区未生效，请在官网选择目标门店后继续")
                                self.sent[action] = self.sent.get(action, 0) + 1
                                self.pending = (action, now)
                                return Decision("fill", action, f["ref"], key="pickup_location")
                            search = control(["搜索", "查找", "应用", "更新", "查找零售店"], {"button"})
                            if search and not self.sent.get("pickup_search"):
                                return click(search, "pickup_search")
                    return Decision("wait", "等待 " + self.preferences["store"] + " 门店／取货时段；不会选择其他门店")
            self.fulfillment_verified = True
        elif stage not in ("CONTACT", "PAYMENT", "REVIEW"):
            return Decision("wait", "等待页面状态")

        if stage in ("FULFILLMENT", "CONTACT", "PAYMENT", "REVIEW"):
            if not self.bag_verified:
                return self.finish("attention", "购物袋核对记录缺失")
            if s.get("store_selection_conflict"):
                return self.finish("attention", "商品的取货门店不一致，请逐件选择目标门店后继续")
            if stage == "CONTACT":
                region_decision = self.reconcile_region(s, now)
                if region_decision:
                    return region_decision
            if self.preferences.get("financing", "full") == "manual" and (stage in ("PAYMENT", "REVIEW") or s.get("payment_choices_present")):
                return self.finish("attention", "已到付款环节：请在官网手动选择分期渠道、期数并继续结账；软件不再点击付款选项，也不会提交订单")
            if self.preferences.get("invoice", "personal") == "manual":
                company = [c for c in s.get("controls", []) if c.get("group") == "invoice" and c.get("invoice_company") and not c.get("disabled")]
                if len(company) == 1 and not company[0].get("selected"):
                    return click(company[0], "invoice_company")
            # Choose the requested provider before validating provider-specific
            # fields (e.g. the default card form must not block Alipay).
            if stage == "PAYMENT" or (stage == "REVIEW" and s.get("payment_choices_present")):
                if self.preferences.get('financing')=='official':
                    from payment_options import installment_action
                    choice,problem=installment_action(s,self.preferences['installment_option'],self.preferences['installment_months'])
                    if problem:
                        if problem == '官网未提供唯一匹配的分期渠道，请本人选择；不会替换方案':
                            matches=[c for c in s.get('controls',[]) if c.get('group')=='payment'
                                     and c.get('installment') and not c.get('disabled')
                                     and c.get('label')==self.preferences['installment_option']]
                            if not matches and stage=='PAYMENT':
                                if self.payment_options_wait_since is None:
                                    self.payment_options_wait_since=now
                                if now-self.payment_options_wait_since < 15:
                                    return Decision('wait','等待官网分期渠道加载完成；保持所选方案')
                        if problem == '等待分期渠道与期数面板稳定' and now-self.last_progress < 30:
                            return Decision('wait',problem)
                        return self.finish('attention',problem)
                    self.payment_options_wait_since = None
                    if choice:return click(choice,'official_installment_'+choice['group']+'_'+str(choice.get('installment_months',0)))
                    c={'selected':True}
                elif self.preferences["payment"] == "官网手动选择":
                    choices = [c for c in s.get("controls", []) if c.get("group") == "payment" and c.get("selected") and not c.get("disabled")]
                    c = choices[0] if len(choices) == 1 else None
                    if c is None or c.get("installment"):
                        return self.finish("attention", "已到付款环节：请在官网选择所需付款方案，完成后点击继续")
                else:
                    c = control([self.preferences["payment"]], {"radio", "option"})
                if (not c and self.payment_verified and self.sent.get("continue_PAYMENT")
                        and not s.get("errors") and not any(
                            x.get("group") == "payment" and x.get("selected") and not x.get("disabled")
                            for x in s.get("controls", []))):
                    return Decision("wait", "付款方案已核验，等待官网切换至订单核对页；不重复点击")
                if c and not c.get("selected"):
                    return click(c, "payment")
                self.payment_verified = bool(c and c.get("selected"))
                if not self.payment_verified:
                    return Decision("wait", "等待所选支付方式可用并确认选中")
            for f in s.get("fields", []):
                if s.get("region_picker") and f.get("key") in ("province", "city", "district"):
                    continue  # Already verified by the shared region controller.
                if f.get("key") and not f.get("matches"):
                    if not f.get("configured", True):
                        if f.get("required") and (not f.get("filled") or f.get("invalid")):
                            return self.finish("attention", "钥匙串缺少必填资料：" + f["key"])
                        continue
                    action = "fill:" + f["key"]
                    if f.get("editable") is False:
                        self.contact_verified = False
                        return self.finish("attention", "只读资料与保存内容不一致，请在官网修改后继续：" + f["key"])
                    if self.sent.get(action, 0) >= 2:
                        return self.finish("attention", "字段校验未通过：" + f["key"])
                    self.sent[action] = self.sent.get(action, 0) + 1
                    self.pending = (action, now)
                    return Decision("fill", action, f["ref"], key=f["key"])
                if f.get("required") and (not f.get("filled") or f.get("invalid")):
                    return self.finish("attention", "必填资料缺失或需人工输入")
            if s.get("errors"):
                return self.finish("attention", "页面仍有校验错误")
            if stage == "CONTACT":
                contact_keys = {f.get("key") for f in s.get("fields", []) if f.get("configured", True) and f.get("matches")}
                self.contact_verified = {"phone", "email"}.issubset(contact_keys) and all(
                    f.get("matches") for f in s["fields"] if f.get("key") and f.get("configured",True)
                    and not (s.get("region_picker") and f.get("key") in ("province", "city", "district")))
                if not self.contact_verified:
                    if not s.get('fields') and not s.get('final_button'):
                        return Decision('wait','等待联系人字段加载并回读，尚未确认资料')
                    return self.finish("attention", "联系人手机号和邮箱未完整回读确认，请核对官网资料后继续")
            if stage == "CONTACT" and s.get("contact_verification_required"):
                return self.finish("attention", "请本人完成联系人手机号码验证；验证完成后再继续，不重复加购")
            if s.get("final_button"):
                for key in ("fulfillment", "contact", "payment"):
                    action = "review_edit_" + key
                    if self.sent.get(action) and not getattr(self, key + "_verified"):
                        if now - self.action_times.get(action, now) < 15:
                            return Decision("wait", "等待官网打开核对步骤；不重复点击更改")
                        return self.finish("attention", "官网未打开所需核对步骤；保留订单页，未重复点击或提交")
                # Apple may skip saved steps after cart reuse or a review edit.
                # Revisit each missing checkpoint once through an explicit
                # official edit control; never infer it from saved account data.
                if (stage == "REVIEW" and self.method == "pickup" and self.bag_verified
                        and s.get("review_target_matches") and s.get("review_extras_match")
                        and s.get("review_quantity") == self.target["quantity"]):
                    for key in ("fulfillment", "contact", "payment"):
                        if not getattr(self, key + "_verified"):
                            edit_ref = s.get("review_edit_refs", {}).get(key)
                            edit = next((c for c in s.get("controls", [])
                                         if c.get("ref") == edit_ref and not c.get("disabled")
                                         and c.get("role") in ("button", "link")), None)
                            if edit:
                                return click(edit, "review_edit_" + key)
                            break
                self.deferred_pickup_verified = (
                    self.method == "pickup" and self.fulfillment_verified
                    and self.preferences.get("pickup_date_policy") == "earliest"
                    and len(self.pickup_confirmed_dates) == 1
                    and bool(self.target.get("store_id"))
                    and self.target["quantity"] == 1
                    and s.get("review_pickup_deferred") is True
                    and len(s.get("review_items", [])) == 1
                )
                if not s.get("review_extras_match"):
                    return self.finish("attention", "已到订单检查：配件及数量尚未全部核对，请展开订单明细后继续")
                review_items = s.get("review_items", [])
                if not review_items or not all(
                    r.get("target_matches") is True and type(r.get("quantity")) is int and 1 <= r["quantity"] <= 2
                    and r.get("method") == self.method
                    and (self.method != "pickup" or r.get("store_matches") is True or self.deferred_pickup_verified)
                    for r in review_items
                ) or sum(r["quantity"] for r in review_items) != self.target["quantity"]:
                    return self.finish("attention", "已到订单检查：各商品的配置、数量、收货方式或门店未逐项确认")
                if self.preferences.get("invoice", "personal") == "manual" and not s.get("review_company_invoice"):
                    return self.finish("attention", "已到订单检查：尚未确认企业发票，请核对官网发票信息后继续")
                if self.preferences.get("subsidy", "none") == "manual" and not s.get("review_subsidy"):
                    return self.finish("attention", "已到订单检查：尚未确认补贴或优惠券已应用，请在官网核验后继续")
                checks = (self.bag_verified, self.fulfillment_verified, self.contact_verified,
                          self.payment_verified, s.get("review_target_matches"),
                          s.get("review_quantity") == self.target["quantity"],
                          self.method != "pickup" or s.get("review_store_matches") is True or self.deferred_pickup_verified,
                          self.preferences.get("applecare", "none") == "manual" or bool(s.get("review_applecare")) == (self.preferences.get("applecare", "none") == "add"),
                          0 < s.get("total", 0) <= self.preferences["max_price"],
                          s.get("review_method") == self.method, not s.get("errors"))
                if all(checks):
                    if self.preferences.get('financing')=='official':
                        from payment_options import installment_action
                        pending,problem=installment_action(s,self.preferences['installment_option'],self.preferences['installment_months'])
                        if pending or problem:return self.finish('attention','最终页未确认所选分期渠道和期数，请本人核对；不会自动提交')
                    if not self.deferred_pickup_verified and (self.target.get("pickup_dates") or self.preferences.get("pickup_date_policy") == "earliest"):
                        from pickup_monitor import dates_in
                        expected_dates = self.pickup_confirmed_dates if self.preferences.get("pickup_date_policy") == "earliest" else self.preferences["pickup_dates"]
                        for row in review_items:
                            confirmed = dates_in(" ".join(row.get("pickup_dates", [])), allowed=expected_dates)
                            if len(confirmed) != 1 or confirmed[0] not in expected_dates:
                                return self.finish("attention", "已到核对页，但逐件取货日期未确认或已变化；请本人核对，不自动提交")
                    return self.finish("ready", "已到最终提交前；尚未下单／付款")
                return self.finish("attention", "已见提交按钮，但订单完整性尚未全部验证")
            if stage != "REVIEW":
                c = control(["继续", "继续填写取货详情", "继续填写送货详情", "继续填写送货地址", "继续选择付款方式", "检查订单", "查看订单", "继续检查订单"], {"button", "link"})
                if c:
                    return click(c, "continue_" + stage)
        if now - self.last_progress >= 5:
            return Decision("wait", "等待页面控件稳定并重新识别（最多 30 秒，不重复上一步）")
        return Decision("wait", "等待页面更新")

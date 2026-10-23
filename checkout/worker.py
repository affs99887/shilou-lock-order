"""Private stdin/stdout checkout adapter. No listener, inventory poller or shell IPC.

All Safari actions run on one worker thread. Stop invalidates queued starts and
is checked again before browser mutations. The production submit gate is closed
until the fork has passed live review-page acceptance.
"""
from __future__ import annotations
import copy
import json
import os
from pathlib import Path
import queue
import sys
import threading
import time
import traceback
import uuid
from order_guard import OrderGuard

sys.path.insert(0, str(Path(__file__).resolve().parent / 'vendor'))
import checkout_service as core
from offer_receipt import OfferHistory
from checkout_engine import CATALOG, DEFAULTS, validate_preferences, purchase_plan_problem, resolve_pickup_preferences, matches_target
from checkout_stores import STORES
from checkout_profile import store_profile, editable_profile, normalized_regional_profile
from regional_checkout import REGIONS, international_plan
from platform_runtime import application_data_root, data_directory, lock_file

DATA = data_directory()
SHARED_LOCK = application_data_root() / 'PurchaseAssistantPreview/service.lock'

def plan_for_hit(plan, hit, now=None, *, rehearsal=False, manual=False):
    now = time.time() * 1000 if now is None else now
    stamp = hit.get('lastCheckedMs')
    if not rehearsal and hit.get('source') is not None:
        # Explicit community evidence follows a separate freshness rule. It is
        # never relabeled as confirmed inventory, and all plan/order gates below
        # still run before the first browser action and again after preflight.
        reported, received = hit.get('reportedAtMs'), hit.get('receivedAtMs')
        if (hit.get('source') != 'sunbelife_aggregate' or manual or
            hit.get('target', {}).get('locale') != 'zh_CN' or
            type(reported) not in (int, float) or type(received) not in (int, float) or
            not 0 <= now - reported <= 180000 or not 0 <= now - received <= 15000 or reported > received):
            raise ValueError('共享到货线索无效或已过期；等待新的线索')
    elif not rehearsal and (hit.get('availability', {}).get('kind') != 'in_stock' or
        (not manual and (type(stamp) not in (int, float) or not 0 <= now - stamp <= 15000))):
        raise ValueError('库存未知或已过期；等待新的有效查询')
    target, product = hit.get('target', {}), hit.get('product', {})
    if target.get('locale') != 'zh_CN':
        return international_plan(plan, hit)
    if plan.get('checkout_locale', 'zh_CN') != 'zh_CN':
        raise ValueError('库存地区与选购方案地区不符')
    if target.get('locale') != 'zh_CN' or target.get('partNumber') != product.get('partNumber'):
        raise ValueError('仅支持已核验的中国大陆 iPhone 配置')
    family = str(product.get('family', '')).lower().replace('-', '').replace(' ', '')
    model = next((k for k in CATALOG if k.replace('-', '') == family), None)
    if not model or product.get('category') != 'iphone':
        raise ValueError('该商品尚无经过适配的选购流程；仅监控')
    store = next((s for s in STORES if s['id'] == target.get('storeNumber')), None)
    if not store:
        raise ValueError('该门店尚未完成选购适配；仅监控')
    p = resolve_pickup_preferences(plan, model)
    p.update(model=model, colors=[product['color']], capacities=[product['capacity']],
             store_id=store['id'], store=store['name'], fulfillment='pickup', release_at='', bark_enabled=False)
    p = validate_preferences(p)
    problem = purchase_plan_problem(p)
    if problem:
        raise ValueError(problem)
    return p

class CancellableSafari(core.Safari):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def script(self, source, timeout=12):
        # Covers direct navigation and snapshots as well as command dispatch.
        if self.owner.cancel.is_set():
            raise RuntimeError('已请求停止；未发送新的浏览器操作')
        return super().script(source, timeout)

def make_browser(owner):
    if sys.platform != 'win32':
        return CancellableSafari(owner)
    from edge_browser import EdgeBrowser, EdgeSession

    class CancellableEdge(EdgeBrowser):
        def page(self):
            if owner.cancel.is_set():
                raise RuntimeError('已请求停止；未发送新的浏览器操作')
            return super().page()

        def new_page(self):
            if owner.cancel.is_set():
                raise RuntimeError('已请求停止；未发送新的浏览器操作')
            return super().new_page()

        def evaluate(self, expression):
            if owner.cancel.is_set():
                raise RuntimeError('已请求停止；未发送新的浏览器操作')
            return super().evaluate(expression)

    return CancellableEdge(EdgeSession(owner.data), core.JS)


# 等本人登录或验证期间，隔多久再推送一次提醒。
LOGIN_REMINDER_SECONDS = 5 * 60

HANDOFFS = {
 'login': ('Apple 需要登录', '请在 Safari 当前选购窗口完成 Apple 登录；登录页有「保持我的登录状态」就勾上，Safari 退出后也不用重新登录。登录后会自动接着结账，不用点继续。购物袋保留，未下单或付款。'),
 'privacy': ('请确认 Apple 隐私说明', '官网弹出了 Apple 和你的数据隐私，请本人阅读并自主决定是否同意；软件不会替你勾选。未下单或付款。'),
 'phone': ('请完成取货手机号验证', '请在 Safari 联系人页完成手机号验证，验证码由本人输入；核验通过后从当前页面继续，不重新加购。'),
 'captcha': ('请完成官网安全验证', '请回到 Safari 完成验证码或人机验证；保留购物袋，未下单或付款。'),
 'region': ('请本人确认取货地区', '请在 Safari 选择并核对目标省市区；选好后回到软件继续，未下单或付款。'),
 'expired': ('Apple 结账会话已失效', '请回到软件核对当前购物袋和失效提示后再开始；不会自动重复移除、加购或下单。'),
 'not_found': ('Apple 页面暂时无法打开', '官网显示找不到页面，选购已暂停。请检查当前购物袋；这不代表无货，也不代表订单已创建。'),
 'blocked': ('Apple 暂时拒绝访问', '官网拒绝了选购请求，自动操作已暂停，库存监控将同步停止。请保留当前页面；若此前已点加购，请先核对购物袋，勿重复添加。'),
 'review': ('已到订单核对页，请本人检查', '请在 Safari 核对型号、数量、门店、日期及付款方案；尚未提交订单或付款。'),
 'contact': ('请检查官网联系资料', '联系人资料尚未通过官网核验，请回到软件查看当前原因，并在 Safari 核对后继续。未下单或付款。'),
 'order_unknown': ('请核对 Apple 下单结果', '下单请求已发出但结果未确认，请本人核对 Apple 订单；不要重复下单。'),
 'pickup': ('取货安排需要本人核对', '官网的门店、日期或签到时段尚未通过确认，请回软件查看具体原因并核对 Safari；未下单或付款。'),
 'payment': ('付款方案需要本人核对', '官网付款步骤尚未完成，请回软件查看当前原因，并在 Safari 核对付款渠道；未下单或付款。'),
 'cart': ('购物袋需要本人核对', '购物袋内容或操作结果尚未确认，请回软件查看原因；不会自动重复移除或加购。'),
 'product': ('商品选购需要本人核对', '官网商品选项尚未确认，请回软件查看原因并核对机型、容量、颜色；未下单或付款。'),
 'other': ('选购需要本人处理', '请回到软件查看当前步骤及处理原因，并核对 Safari 选购页面；流程已暂停。'),
}

class Checkout(core.Service):
    def __init__(self, owner, plan):
        self.owner = owner
        super().__init__(browser=make_browser(owner), preferences=plan, persist=False)
        self.submission_deadline = None
        self.next_order_check = 0
        self.last_handoff = None
        self.access_blocked = False
        self.trace = []
        self.trace_mark = None
        self.login_reminder_at = None
        # 官网显示「找不到页面」时自动重开的次数和下一次重开的时刻。
        self.missing_retries = 0
        self.missing_retry_at = None
        self.update(session='开始选购时检查官网登录状态；仅在官网要求时由本人认证')

    def command(self, name):
        if name in ('rehearse', 'stop'):
            self.missing_retries = 0
            self.missing_retry_at = None
        return super().command(name)

    def can_recover_missing(self):
        return (callable(getattr(self.browser, 'reopen_failed_entry', None)) and
                self.phase in ('CLEAR_BAG', 'RUN') and
                not self.owner.order_guard.blocked and not self.submission_deadline and
                not self.access_blocked and not self.owner.cancel.is_set())

    def recover_missing_page(self, snapshot, now):
        """官网显示「找不到页面」：隔 1 秒、3 秒各重开一次购物袋，不重复删除或加购。

        自动选购、手动开始、演练和库存表「立即选购」都走这里；两次都打不开才结束这次选购。"""
        if snapshot.get('stage') != 'NOT_FOUND' or not self.can_recover_missing():
            return False
        if self.phase == 'CLEAR_BAG' and self.pending_remove and now - self.phase_since > 30:
            return False  # 移除结果已超过 30 秒未确认：不再重开，交给购物袋检查停下
        if self.missing_retries >= 2:
            self.owner.retryable_end = True
            self.owner.end_reason = '官网页面重开 2 次后仍打不开'
            self.attention('官网页面重开 2 次后仍打不开')
            return True
        self.missing_retries += 1
        self.missing_retry_at = now + (1 if self.missing_retries == 1 else 3)
        self.update(mode='recovering', stage='NOT_FOUND', intervention=None,
                    reason=f'官网页面暂时打不开，正在自动重开并核对购物袋（{self.missing_retries}/2）')
        self.last_handoff = None
        return True

    def record_diagnostic(self, stage, status=0, action="", result="", diagnostics=None):
        # Fixed internal enums only. Never store DOM, URLs, contacts or cookies.
        allowed_stages = {"LOADING","PRODUCT","BAG","FULFILLMENT","CONTACT","PAYMENT",
                          "REVIEW","READY","BLOCKED","NOT_FOUND","LOGIN","CAPTCHA",
                          "SESSION_EXPIRED","UNKNOWN","FOREIGN","LOST_TAB","ACTION"}
        stage = stage if stage in allowed_stages else "OTHER"
        allowed_actions = {"model","color","capacity","add","product_continue","checkout",
                           "不折抵换购","不加 AppleCare+ 服务计划","applecare_add",
                           "method_pickup","method_delivery","store","payment",
                           "continue_FULFILLMENT","continue_CONTACT","continue_PAYMENT",
                           "review_edit_fulfillment","review_edit_contact","review_edit_payment",
                           "bag_recheck","page_reopen"}
        fixed_actions = {
            "清空购物袋：移除一项（可重新加购）": "remove_cart_item",
            "official_installment_payment_0": "installment_provider",
            **{"official_installment_installment_term_" + str(n): "installment_term"
               for n in (1,3,6,12,24,36)},
        }
        if action in fixed_actions:
            action = fixed_actions[action]
            allowed_actions.add(action)
        # Schedule retry keys contain DOM names and values; persist only their
        # fixed category. Contact values and arbitrary error text never enter
        # this diagnostic file.
        category = action.split(":", 1)[0]
        if category in ("pickup_date", "pickup_time", "fill", "store"):
            action = category
            allowed_actions.add(category)
        if action in {"region_" + method + "_" + step
                      for method in ("pickup", "delivery")
                      for step in ("open","province","city","district","tab_province","tab_city","tab_district")}:
            action = "region"
            allowed_actions.add(action)
        action = action if action in allowed_actions else ""
        result = result if result in ("dispatched","stale","refused","duplicate") else ""
        status = status if type(status) is int and 100 <= status <= 599 else 0
        # Edge 能看出被拒的是哪类请求；这里只记几个固定的是／否标记和状态码，不记网址。
        diagnostics = diagnostics or {}
        flags = {key: diagnostics[key] for key in (
            "access_document", "access_shop_request", "access_availability",
            "access_configuration", "access_bag", "edge_availability_unknown",
            "edge_auxiliary_wait") if type(diagnostics.get(key)) is bool}
        auxiliary_status = diagnostics.get("edge_auxiliary_http_status", 0)
        if type(auxiliary_status) is int and 100 <= auxiliary_status <= 599:
            flags["edge_auxiliary_http_status"] = auxiliary_status
        mark = (stage, status, action, result, tuple(sorted(flags.items())))
        if mark == self.trace_mark:
            return
        self.trace_mark = mark
        self.trace.append(dict(time=time.time(), stage=stage, http_status=status, action=action, result=result,
                               diagnostics=flags))
        self.trace = self.trace[-120:]
        try:
            core.atomic_json(self.owner.data / 'checkout-diagnostics.json', {"events": self.trace})
        except OSError:
            pass  # A diagnostic write must not change a checkout decision.

    def formal_submission(self):
        return self.owner.automatic and self.owner.submission_confirmed and self.execution_preferences.get('auto_submit_order')

    def update(self, **fields):
        if fields.get('mode')=='stopped':
            fields['intervention']=None
            self.last_handoff=None
        if sys.platform == 'win32' and isinstance(fields.get('reason'), str):
            fields['reason'] = fields['reason'].replace('这台 Mac 的 Safari', '这台电脑的 Edge').replace('Safari', 'Edge').replace('钥匙串', '加密资料')
        return super().update(**fields)

    def tick(self):
        if self.owner.cancel.is_set():
            return
        if self.missing_retry_at is not None:
            if time.monotonic() < self.missing_retry_at:
                return
            self.missing_retry_at = None
            # 移除结果还没确认时沿用原来的 30 秒期限，重开不延长它。
            keep_deadline = self.phase == 'CLEAR_BAG' and self.pending_remove
            # 只重开购物袋这个只读入口，不重放结果未确认的删除或加购。
            reopened = self.browser.reopen_failed_entry('https://www.apple.com.cn/shop/bag')
            self.record_diagnostic("ACTION", action="page_reopen")
            if reopened and (not self.attempt or not self.attempt.add_submitted):
                self.phase = 'CLEAR_BAG'
                self.attempt = None
            elif reopened and self.phase == 'RUN':
                # 已加购后回到购物袋：放开一次结账点击，否则会一直等上一次结账跳转。
                # 加购、改数量等其余操作记录保留，不会重复执行。
                self.attempt.sent.pop('checkout', None)
                self.attempt.action_times.pop('checkout', None)
            if not keep_deadline:
                self.phase_since = time.monotonic()
            self.update(mode='running', reason='页面已重新打开，正在核对购物袋后继续原配置', intervention=None)
            return
        super().tick()
        if self.status.get('mode') == 'ready' and self.formal_submission():
            self.submit_once()

    def submit_once(self):
        if self.owner.cancel.is_set():
            return
        p = self.execution_preferences
        if p['financing'] not in ('full', 'official') or p['payment'] in ('Apple Pay', '官网手动选择'):
            self.attention('该付款方案需本人处理，未提交订单')
            return
        if self.owner.order_guard.blocked:
            self.attention('上次提交尚未人工核对；已阻止重复下单')
            return
        snapshot = self.browser.call(self.profile, self.attempt.target)
        # Re-run all cart/contact/fulfillment/date/budget guards on a fresh read.
        check = copy.deepcopy(self.attempt)
        check.terminal = None
        decision = check.decide(snapshot, time.monotonic())
        if decision.kind != 'ready' or not snapshot.get('final_order_ref'):
            self.attention('提交前重新核对未通过；未提交订单')
            return
        if self.owner.cancel.is_set():
            return
        nonce = self.owner.order_guard.begin()
        command = dict(kind='submit_order', ref=snapshot['final_order_ref'], expected=snapshot['fingerprint'],
                       nonce=nonce, authorized=True, max_price=p['max_price'], method=self.attempt.method,
                       financing=p['financing'], provider=p['installment_option'], months=p['installment_months'],
                       applecare=p['applecare'], payment=p['payment'],
                       verified_deferred_pickup=check.deferred_pickup_verified)
        try:
            if self.owner.cancel.is_set():
                self.owner.order_guard.write('not_sent')
                return
            result = self.browser.call(self.profile, self.attempt.target, command)
        except Exception:
            self.owner.order_guard.write('unknown')
            self.attention('下单请求结果未明；请到 Apple 查看订单，禁止自动重试')
            return
        if result.get('result') in ('stale', 'refused'):
            self.owner.order_guard.write('not_sent')
            self.attention('页面已变化或提交核验不通过；未点击下单，请核对')
            return
        self.owner.order_guard.write('unknown')
        self.owner.armed = False
        self.submission_deadline = time.monotonic() + 30
        self.update(mode='submitting', stage='ORDER_CONFIRMATION', reason='已发出下单操作，正在核对官网结果；不重复点击')

    def verify_order_tick(self):
        if self.owner.cancel.is_set():
            self.submission_deadline = None
            return
        if time.monotonic() < self.next_order_check:
            return
        self.next_order_check = time.monotonic() + 1
        snapshot = self.browser.call({}, self.attempt.target)
        if snapshot.get('order_pending_payment') is True:
            self.owner.order_guard.write('awaiting_payment')
            self.submission_deadline = None
            self.update(mode='awaiting_payment', stage='AWAITING_PAYMENT', reason='官网显示订单待支付；请本人核对并支付')
            self.owner.notice = {'title':'Apple 订单待支付', 'body':'官网已显示订单待支付，请在 Apple 官方渠道核对并支付。手机可能需要重新登录；以官网支付期限为准。', 'id':uuid.uuid4().hex}
        elif time.monotonic() >= self.submission_deadline:
            self.submission_deadline = None
            self.attention('下单结果未能确认；请到 Apple 查看订单，禁止自动重试或继续创建订单')

    def require_user_action(self, kind):
        title, body = HANDOFFS[kind]
        if sys.platform == 'win32':
            body = body.replace('Safari', 'Edge')
        self.update(intervention={'kind':kind,'title':title,'body':body})
        if self.last_handoff != kind:
            self.last_handoff = kind
            self.owner.notice = {'kind':kind,'title':title,'body':body,'id':uuid.uuid4().hex}

    def handoff_kind(self):
        if self.access_blocked:
            return 'blocked'
        diag=self.status.get('diagnostics',{})
        stage=self.status.get('stage')
        if self.owner.order_guard.blocked:return 'order_unknown'
        if diag.get('privacy_consent_required'):return 'privacy'
        if diag.get('contact_verification_required'):return 'phone'
        if stage=='LOGIN':return 'login'
        if stage=='CAPTCHA':return 'captcha'
        if stage=='SESSION_EXPIRED':return 'expired'
        if stage=='NOT_FOUND':return 'not_found'
        if self.status.get('mode')=='manual_region':return 'region'
        if stage in ('REVIEW','READY'):return 'review'
        if stage=='CONTACT':return 'contact'
        if stage=='FULFILLMENT':return 'pickup'
        if stage=='PAYMENT':return 'payment'
        if stage=='BAG':return 'cart'
        if stage in ('PRODUCT','ATTACH'):return 'product'
        return 'other'

    def observe_diagnostics(self, snapshot):
        super().observe_diagnostics(snapshot)
        if snapshot.get('stage') == 'BLOCKED' or snapshot.get('http_status') in (403,429,541):
            self.access_blocked = True
        elif snapshot.get('stage') in ('PRODUCT','BAG','FULFILLMENT','CONTACT','PAYMENT','REVIEW'):
            self.access_blocked = False
        self.record_diagnostic(snapshot.get('stage'), snapshot.get('http_status') or
                               snapshot.get('diagnostics', {}).get('document_http_status', 0),
                               diagnostics=snapshot.get('diagnostics', {}))
        diag=snapshot.get('diagnostics',{})
        kind = ('privacy' if diag.get('privacy_consent_required') else
                'phone' if snapshot.get('contact_verification_required') else
                'login' if snapshot.get('stage')=='LOGIN' else
                'captcha' if snapshot.get('stage')=='CAPTCHA' else
                'expired' if snapshot.get('stage')=='SESSION_EXPIRED' else
                'not_found' if snapshot.get('stage')=='NOT_FOUND' else None)
        if kind == 'not_found' and (self.can_recover_missing() or
                                    (self.phase == 'CLEAR_BAG' and self.pending_remove)):
            # 先自动重开或重新读取购物袋，用完次数还不行时再通知本人。
            return
        if kind:self.require_user_action(kind)
        elif ((self.status.get('intervention') or {}).get('kind') in ('login','privacy','phone','captcha') or
              (snapshot.get('stage') in ('PRODUCT','BAG','FULFILLMENT','CONTACT','PAYMENT','REVIEW') and
               (self.status.get('intervention') or {}).get('kind') in ('expired','not_found'))):
            self.update(intervention=None)
            self.last_handoff=None

    def attention(self, reason):
        if getattr(self.owner, 'manual_run', False):
            # 手动开始和演练共用同一套单次流程，提示里别写成「演练」。
            reason = reason.replace('单次演练超过 5 分钟', '手动选购超过 5 分钟')
        if self.access_blocked:
            diagnostics = self.status.get('diagnostics', {})
            code = diagnostics.get('access_http_status', 0)
            # Edge 能分辨被拒的是哪类请求，写进提示里方便判断；Safari 看不到，就不写。
            component = ('库存／配送查询接口' if diagnostics.get('access_availability') else
                         '选配更新接口' if diagnostics.get('access_configuration') else
                         '购物袋接口' if diagnostics.get('access_bag') else
                         '网页' if diagnostics.get('access_document') else
                         '商店请求' if diagnostics.get('access_shop_request') else '')
            reason = ('Apple 返回 HTTP ' + str(code) if code in (403,429,541) else 'Apple 拒绝访问') + \
                ('（' + component + '）' if component else '') + \
                '；选购已暂停，库存监控同步停止。请先核对购物袋，不重复加购。'
        super().attention(reason)
        if self.handoff_kind()=='phone' and self.attempt and self.attempt.bag_verified and not self.owner.cancel.is_set():
            self.login_watch=True
            self.login_watch_until=time.monotonic()+900
            self.login_probe_at=0;self.login_candidate=None;self.login_candidate_count=0
            self.update(reason='请本人完成手机号验证；后台只读核验，完成后继续当前流程，不重复加购')

    def watch_login_tick(self):
        super().watch_login_tick()
        # 等本人登录或验证期间，每隔一段时间再推送一次：人可能没看到第一条。
        if not self.login_watch:
            self.login_reminder_at = None
            return
        now = time.monotonic()
        if self.login_reminder_at is None:
            self.login_reminder_at = now + LOGIN_REMINDER_SECONDS
        elif now >= self.login_reminder_at:
            self.login_reminder_at = now + LOGIN_REMINDER_SECONDS
            self.last_handoff = None
            self.require_user_action(self.handoff_kind())

    def notify(self, title, body):
        if self.status.get('mode') == 'ready' and self.formal_submission():return
        if self.status.get('mode') in ('ready','attention','manual_region') and not self.owner.retryable_end:
            self.require_user_action(self.handoff_kind())

    def event(self, kind, reason='', **extra):
        if kind == 'action':
            self.record_diagnostic("ACTION", action=reason, result=extra.get('result', ''))
        elif kind == 'bag_recheck':
            self.record_diagnostic("ACTION", action='bag_recheck')
            self.update(intervention=None)
            self.last_handoff = None
        # 官网明确没货、超预算，或单次选购时官网还没开卖，都不需要本人处理：
        # 自动选购等下一次有货再换目标，单次选购（手动开始、演练、库存表立即选购）直接结束。
        if kind in ('sold_out', 'over_budget') or (kind == 'not_released' and getattr(self.owner, 'single_run', None)):
            self.owner.retryable_end = True
            self.owner.end_reason = reason.replace('，仅记录', '')
        return super().event(kind, reason, **extra)

    def dispatch(self, snapshot, decision):
        if self.owner.cancel.is_set():
            return
        return super().dispatch(snapshot, decision)

    def start_product(self, fresh=False):
        if self.owner.cancel.is_set():
            return
        super().start_product(fresh)
        if self.attempt:
            self.attempt.target['pickup_dates'] = list(self.execution_preferences['pickup_dates'])

    def preflight(self, fresh=False):
        if not self.owner.cancel.is_set():
            if sys.platform == 'win32' and not fresh:
                self.browser.prepare_entry()
            result = super().preflight(fresh)
            if self.phase == 'CLEAR_BAG':
                self.update(reason='先核对现有购物袋；符合方案时直接结账，空袋时再选择商品')
            return result

    def rotate_entry(self, url):
        # Entry retries keep the owned tab and its page session. open() only
        # replaces a lost tab before checkout; it never adopts another tab.
        if not self.owner.cancel.is_set():
            self.browser.open(url, foreground=False)

    def clear_tick(self, snapshot, now):
        if self.owner.cancel.is_set():
            return
        if self.owner.order_guard.blocked:
            self.attention('上次下单结果尚未核对；保留购物袋，不重新移除或加购')
            return
        if snapshot.get('stage') == 'BAG':
            if snapshot.get('errors'):
                self.attention('购物袋存在官网校验错误；已暂停，未移除商品或重新加购')
                return
            if snapshot.get('bag_busy'):
                if now - self.phase_since > 30:
                    self.attention('购物袋更新超过 30 秒仍未完成；保留现场，不重复操作')
                return
        items = snapshot.get('items', [])
        on_bag = snapshot.get('stage') == 'BAG'
        # 上一次移除已经生效（商品行数变少了），不再算「移除结果未确认」，
        # 这样移掉多余商品后，剩下的目标商品可以直接拿来结账。
        if on_bag and self.pending_remove and len(items) < self.pending_remove[0]:
            self.pending_remove = None
        target = core.purchase_targets(self.execution_preferences)[self.index]
        # 购物袋里型号、颜色、容量、数量都和目标一致，且没带折抵换购的那一行。
        exact = [i for i in items if matches_target(i.get('title', ''), target) and
                 type(i.get('quantity')) is int and i['quantity'] == target['quantity'] and
                 not i.get('trade_in')]
        # 方案要折抵换购时不复用：折抵要在商品页由本人估价，购物袋里的旧商品不能代替。
        settled = (on_bag and self.pending_remove is None and
                   not snapshot.get('bag_busy') and not snapshot.get('errors') and
                   self.execution_preferences.get('trade_in', 'none') == 'none')
        # 购物袋里只有这一件目标商品：直接去结账，不管是否勾选了清空，都不移除再重新加购。
        # 结账前，购物袋那一步仍会用新读到的页面核对预算、AppleCare+、数量和页面报错。
        if settled and len(items) == 1 and len(exact) == 1:
            self.attempt = core.Attempt(self.execution_preferences, target, now)
            self.attempt.target['pickup_dates'] = list(self.execution_preferences['pickup_dates'])
            # A cart already exists. Never allow canonical-entry recovery
            # to add another copy after this point.
            self.attempt.add_submitted = True
            self.phase = 'RUN'
            self.phase_since = now
            self.update(mode='running', stage='BAG',
                        reason='购物袋已是目标型号、颜色、容量和数量；核对预算与保障后直接结账，不重新加购',
                        intervention=None)
            self.last_handoff = None
            self.event('bag_reused', '复用完全匹配的购物袋商品，下一次读取重新核对')
            return
        clear = self.execution_preferences['clear_bag_before_purchase']
        if items and not clear:
            self.attention('购物袋已有商品；未获清空授权，保留现场')
            return
        # 允许清空，且购物袋里除了目标商品还有别的：只移除别的，把目标排在最后留下。
        if clear and settled and len(exact) == 1 and len(items) > 1:
            snapshot = dict(snapshot, items=[i for i in items if i is not exact[0]] + exact)
        return super().clear_tick(snapshot, now)

class Bridge:
    def __init__(self, service_factory=Checkout, data=DATA):
        self.data = data
        data.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(data, 0o700)
        self.config = data / 'checkout.json'
        self.order_guard = OrderGuard(data / 'order-submission.json')
        self.submission_confirmed = False
        self.activation_pending = False
        self.profile_required = False
        self.plan = copy.deepcopy(DEFAULTS)
        self.plan.update(bark_enabled=False, release_at='', financing='full', payment='支付宝')
        if self.config.exists():
            # Corrupt settings are not silently replaced.
            self.plan = validate_preferences(json.loads(self.config.read_text(encoding='utf-8')))
        self.cancel = threading.Event()
        self.cancel.set()
        self.automatic = False
        self.retryable_end = False
        self.current_key = None
        self.avoid_until = {}
        self.service = service_factory(self, self.plan)
        self.service_factory = service_factory
        self.tasks = queue.Queue(maxsize=8)
        self.mutex = threading.RLock()
        self.armed = False
        self.reserved = False
        # 正在进行「提前登录」：只打开账号页等本人登录，不加购、不结账。
        self.login_task = False
        # 当前流程是「手动开始选购」：不需要监控确认有货，到选门店时可改选同城有货门店。
        self.manual_run = False
        # 单次选购（manual 手动开始、rehearse 演练、purchase 库存表立即选购）的种类；自动选购和提前登录为 None。
        self.single_run = None
        # 单次选购开始前自动选购已启用：先暂停，这次结束后恢复，并带回启用时的下单授权。
        self.resume_armed = False
        self.resume_submission = False
        # 这次单次选购走到过订单核对页；本人可能已在官网手动下单，结束后不恢复自动选购。
        self.reached_review = False
        # 官网明确没货、超预算或还没开卖的具体原因，单次选购自己结束时写进结果。
        self.end_reason = ''
        # 上一次单次选购怎么结束的，空闲时显示在状态行。
        self.last_result = ''
        self.stopping = False
        self.generation = 0
        self.notice = None
        self.file_lock = None
        self.error = ''
        self.closed = False
        self.offer_history = OfferHistory(data)

    def status(self):
        with self.mutex, self.service.lock:
            s = self.service.status
            return {'platform': 'windows' if sys.platform == 'win32' else 'macos',
                    'armed': self.armed, 'busy': self.reserved, 'stopping': self.stopping,
                    'mode': s.get('mode'), 'stage': s.get('stage'),
                    'error': self.error,
                    'reason': self.error or s.get('reason'), 'elapsed': self.service.timing().get('total_seconds', 0),
                    'plan': copy.deepcopy(self.plan), 'notice': self.notice,
                    'intervention': copy.deepcopy(s.get('intervention')),
                    'accessBlocked': bool(getattr(self.service, 'access_blocked', False)),
                    'submissionEnabled': bool(self.plan.get('auto_submit_order') and
                                              self.submission_confirmed and
                                              (self.armed or self.reserved) and not self.stopping),
                    'activationPending': self.activation_pending,
                    'profileRequired': self.profile_required,
                    'regions': list(REGIONS.values()),
                    'orderNeedsReview': self.order_guard.blocked,
                    'session': s.get('session'),
                    'loginTask': self.login_task,
                    'manualRun': self.manual_run,
                    'singleRun': self.single_run,
                    'autoPaused': bool(self.resume_armed and not self.reached_review and
                                       not self.order_guard.blocked),
                    'lastResult': self.last_result}

    def offer_reply(self, hits, outcome, selected=None):
        response = self.status()
        # This receipt belongs to this request only. A later status read must
        # not make a different hint look accepted because checkout is busy.
        response['offer'] = {'outcome': outcome, 'selectedIndex': selected}
        if any(isinstance(hit, dict) and hit.get('source') == 'sunbelife_aggregate' for hit in hits):
            self.offer_history.record(outcome, len(hits), selected, response)
        return response

    def request(self, message):
        op = message.get('op')
        if op == 'status':
            return self.status()
        with self.mutex:
            if op == 'purchase' and (self.armed or self.reserved or self.stopping):
                return self.status()
            if op in ('stop', 'disarm'):
                self.armed = False
                self.submission_confirmed = False
                self.activation_pending = False
                self.generation += 1
                # 停止就是关掉自动选购：正在跑的单次选购结束后也不再恢复它。
                self.resume_armed = False
                self.last_result = ''
                if op == 'stop':
                    self.cancel.set()
                    self.stopping = True
                return self.status()
            if op == 'end_run':
                # 只结束这一次单次选购，后台循环停好后再恢复它开始前已启用的自动选购。
                # 已经自己结束或正在停止时什么也不做，免得和自动结束撞在一起时报错。
                if self.single_run and self.reserved and not self.stopping:
                    self.cancel.set()
                    self.stopping = True
                    self.generation += 1
                return self.status()
            if op == 'offer':
                hits = message.get('hits', [])
                if not isinstance(hits, list):
                    return self.offer_reply([], 'ineligible')
                hits = hits[:1000]
                if self.stopping:
                    return self.offer_reply(hits, 'stopping')
                if self.order_guard.blocked:
                    return self.offer_reply(hits, 'order_review')
                # Describe an existing blocked task, but don't let its old
                # diagnostic veto a later explicit stop/re-arm forever.
                if (getattr(self.service, 'access_blocked', False) and
                        (self.reserved or not self.armed)):
                    return self.offer_reply(hits, 'blocked')
                if self.activation_pending:
                    return self.offer_reply(hits, 'activating')
                if self.reserved:
                    mode = self.service.status.get('mode')
                    outcome = ('paused' if mode in ('attention', 'manual_region') or self.error else
                               'review' if mode == 'ready' else 'busy')
                    return self.offer_reply(hits, outcome)
                if self.profile_required:
                    return self.offer_reply(hits, 'profile_required')
                if not self.armed:
                    return self.offer_reply(hits, 'not_armed')
                # Confirmed inventory or explicit community hints, in target order.
                rejected = ''
                cooling = False
                for index, hit in enumerate(hits):
                    if not isinstance(hit, dict) or not isinstance(hit.get('target'), dict):
                        rejected = '库存目标无法唯一对应选购方案'
                        continue
                    t = hit.get('target', {})
                    if any(not isinstance(t.get(field), str) for field in ('locale', 'partNumber', 'storeNumber')):
                        rejected = '库存目标无法唯一对应选购方案'
                        continue
                    key = (t.get('partNumber'), t.get('storeNumber')) if t.get('locale') == 'zh_CN' else (t.get('locale'), t.get('partNumber'), t.get('storeNumber'))
                    if time.monotonic() < self.avoid_until.get(key, 0):
                        cooling = True
                        continue
                    try:
                        plan_for_hit(self.plan, hit)
                    except (ValueError, KeyError, TypeError) as exc:
                        rejected = str(exc) if type(exc) is ValueError else '库存目标无法唯一对应选购方案'
                        continue
                    try:
                        self.tasks.put_nowait((self.generation, 'start', copy.deepcopy(hit)))
                    except queue.Full:
                        return self.offer_reply(hits, 'queue_full')
                    self.reserved = True
                    return self.offer_reply(hits, 'queued', index)
                if rejected:
                    self.service.update(reason=rejected)
                return self.offer_reply(hits, 'ineligible' if rejected else 'cooldown' if cooling else 'empty')
            if op == 'open_bag_if_idle':
                # Used only by the monitor. Hold the cross-process checkout
                # lock through the browser-open dispatch, not just a probe.
                if self.armed or self.reserved or self.stopping or self.order_guard.blocked:
                    return {'opened': False}
                locale = message.get('locale')
                if locale not in REGIONS:
                    raise ValueError('未知商店地区，不打开网页')
                try:
                    self.acquire()
                except ValueError:
                    return {'opened': False}
                try:
                    url = REGIONS[locale]['base'] + '/shop/bag'
                    if sys.platform == 'win32':
                        os.startfile(url)
                    else:
                        core.subprocess.run(['open', url], check=True, timeout=5)
                    return {'opened': True}
                finally:
                    self.release()
            if op == 'continue':
                if self.order_guard.blocked:
                    raise ValueError('已有未核对的下单请求；请先查看 Apple 订单，不可自动继续')
                if self.stopping or not self.reserved:
                    raise ValueError('当前没有可以继续的选购流程')
                self.tasks.put_nowait((self.generation, op, None))
                return self.status()
            # 自动选购已启用、正在等库存时，可以直接手动开始或演练：先暂停自动选购，这次结束后恢复。
            pause_auto = op in ('rehearse', 'manual') and self.armed and not self.reserved and not self.stopping
            if (self.reserved or self.armed or self.stopping) and not pause_auto:
                raise ValueError('请先停止自动选购，再修改设置或演练')
            if op == 'login':
                if self.plan.get('checkout_locale', 'zh_CN') != 'zh_CN':
                    raise ValueError('提前登录目前只支持中国大陆官网')
                if self.order_guard.blocked:
                    raise ValueError('上次下单结果需核对；请先查看 Apple 订单')
                self.reserved = True
                self.last_result = ''
                self.service.update(reason='正在打开 Apple 账号页；请在弹出的浏览器窗口登录')
                self.tasks.put_nowait((self.generation, op, message))
                return self.status()
            if op == 'read_profile':
                locale = message.get('locale', self.plan.get('checkout_locale', 'zh_CN'))
                return editable_profile() if locale == 'zh_CN' else editable_profile(locale)
            if op == 'acknowledge_order':
                if message.get('confirmed') is not True:
                    raise ValueError('请先本人核对 Apple 订单')
                self.order_guard.acknowledge()
                return self.status()
            if op == 'configure':
                p = validate_preferences(message['plan'])
                p.update(release_at='', fulfillment='pickup', bark_enabled=False)
                core.atomic_json(self.config, p)
                self.plan = p
                self.service.preferences = copy.deepcopy(p)
                return self.status()
            if op == 'import':
                choices = ('PurchaseAssistantMonitorNext', 'PurchaseAssistantPickupCandidate', 'PurchaseAssistantPickup')
                choices = choices + ('PurchaseAssistantPreview',)
                source = next((application_data_root() / d / 'preferences.json'
                               for d in choices if (application_data_root() / d / 'preferences.json').is_file()), None)
                if source is None:
                    raise ValueError('未找到旧选购方案，请在此设置')
                return self.request({'op': 'configure', 'plan': json.loads(source.read_text(encoding='utf-8'))})
            if op in ('arm', 'rehearse', 'purchase', 'manual', 'save_profile'):
                if op == 'save_profile' and message.get('locale', self.plan.get('checkout_locale', 'zh_CN')) != self.plan.get('checkout_locale', 'zh_CN'):
                    raise ValueError('请先保存选购地区，再保存对应地区资料')
                if op in ('arm', 'rehearse', 'purchase', 'manual') and self.order_guard.blocked:
                    raise ValueError('上次下单结果需核对；请先查看 Apple 订单，避免重复购买')
                if op in ('arm', 'purchase', 'manual') and self.plan.get('auto_submit_order') and message.get('confirmSubmission') is not True:
                    raise ValueError('正式自动下单必须单独确认；演练不会提交订单')
                if op == 'manual':
                    # 不看监控结果：本人确认官网已开卖后直接开始，有没有货由官网页面决定。
                    if message['hit'].get('target', {}).get('locale') != 'zh_CN':
                        raise ValueError('手动开始目前只支持中国大陆官网')
                    plan_for_hit(self.plan, message['hit'], rehearsal=True)
                if op == 'purchase':
                    plan_for_hit(self.plan, message['hit'], manual=True)
                # 检查都通过后才动状态：暂停自动选购后若再报错，自动选购就丢了。
                if op in ('rehearse', 'purchase', 'manual'):
                    self.single_run = op
                    self.resume_armed = pause_auto
                    self.resume_submission = pause_auto and self.submission_confirmed
                    self.reached_review = False
                    self.last_result = ''
                    self.armed = False
                if op == 'manual':
                    self.submission_confirmed = message.get('confirmSubmission') is True
                    self.service.update(reason='正在手动开始选购；指定门店不能取货时会改选同城能取货的门店')
                if op == 'arm':
                    self.activation_pending = True
                    self.submission_confirmed = False
                    self.last_result = ''
                    self.service.update(reason='正在校验已保存资料，请完成可能出现的系统授权提示')
                if op == 'purchase':
                    self.submission_confirmed = message.get('confirmSubmission') is True
                    self.service.update(reason='正在按所选有货目标准备选购')
                self.reserved = True
                if op == 'rehearse':
                    self.submission_confirmed = False
                    self.service.update(reason='正在准备测试演练；无需提前加购，将在官网核对可购买状态')
                self.tasks.put_nowait((self.generation, op, message))
                return self.status()
            raise ValueError('不支持的操作')

    def acquire(self):
        SHARED_LOCK.parent.mkdir(parents=True, exist_ok=True)
        self.file_lock = open(SHARED_LOCK, 'a+b')
        try:
            lock_file(self.file_lock)
        except OSError:
            self.release()
            raise ValueError('其他抢先购版本仍占用选购后台，请停止并关闭后再试') from None

    def release(self):
        if self.file_lock:
            self.file_lock.close()
            self.file_lock = None

    def end_single_run(self, how):
        """单次选购结束：清掉它的标记，并恢复它开始前已启用的自动选购。须持有 self.mutex。

        走到过订单核对页、有未核对的下单记录或官网拒绝过访问时不恢复：
        本人可能刚在官网手动下了单，或者官网还在限制访问。"""
        if self.reached_review:
            blocked = '这次走到过订单核对页，为免重复购买，自动选购没有恢复；需要时再点「启用有货自动选购」'
        elif self.order_guard.blocked:
            blocked = '有未核对的下单记录，自动选购没有恢复；核对订单后再启用'
        elif getattr(self.service, 'access_blocked', False):
            blocked = '官网拒绝过访问，自动选购没有恢复；稍后再点「启用有货自动选购」'
        else:
            blocked = ''
        resumed = self.resume_armed and not blocked
        if resumed:
            self.armed = True
            self.submission_confirmed = self.resume_submission
        else:
            self.submission_confirmed = False
        tail = '自动选购已恢复，有货后自动选购' if resumed else blocked if self.resume_armed else ''
        self.last_result = '。'.join(part for part in (how, tail) if part)
        if self.last_result:
            self.service.update(reason=self.last_result)
        self.single_run = None
        self.manual_run = False
        self.resume_armed = False
        self.resume_submission = False
        self.reached_review = False
        return resumed

    def require_profile(self):
        locale = self.plan.get('checkout_locale', 'zh_CN')
        return core.normalized_profile(require_pickup=True) if locale == 'zh_CN' else normalized_regional_profile(locale)

    def record_exception(self, exc):
        """Persist code locations only, never exception values or local variables."""
        code = uuid.uuid4().hex[:8]
        kinds = {'AttributeError','KeyError','TypeError','IndexError','NameError',
                 'ValueError','RuntimeError','TimeoutError','OSError','PermissionError'}
        files = {'worker.py','checkout_service.py','checkout_engine.py','checkout_profile.py',
                 'pickup_monitor.py','order_guard.py','regional_service.py','edge_browser.py',
                 'platform_runtime.py','payment_options.py'}
        frames = [{'file':Path(f.filename).name,'line':f.lineno}
                  for f in traceback.extract_tb(exc.__traceback__) if Path(f.filename).name in files]
        kind = type(exc).__name__ if type(exc).__name__ in kinds else 'InternalError'
        stage = self.service.status.get('stage')
        stage = stage if stage in {'BAG','FULFILLMENT','CONTACT','PAYMENT','REVIEW','READY',
                                  'PRODUCT','LOGIN','LOADING','CLEAR_BAG'} else 'OTHER'
        try:
            core.atomic_json(self.data/'checkout-error.json',
                             dict(code=code,time=time.time(),kind=kind,stage=stage,frames=frames))
        except OSError:
            pass
        return code, kind, stage

    def run(self):
        while not self.closed:
            try:
                if self.stopping:
                    self.service.command('stop')
                    self.release()
                    with self.mutex:
                        self.reserved = False
                        self.login_task = False
                        self.manual_run = False
                        self.stopping = False
                        self.error = ''
                        if self.single_run:
                            self.end_single_run('已结束本次选购')
                try:
                    epoch, op, data = self.tasks.get(timeout=.1)
                except queue.Empty:
                    op = None
                if op:
                    if epoch != self.generation:
                        continue
                    self.error = ''
                    if op == 'save_profile':
                        locale = self.plan.get('checkout_locale', 'zh_CN')
                        if locale == 'zh_CN':
                            store_profile(data['profile'])
                        else:
                            store_profile(data['profile'], locale)
                        self.profile_required = False
                        self.reserved = False
                        self.service.update(reason='联系资料已加密保存并核对')
                    elif op == 'arm':
                        try:
                            self.require_profile()
                        except (ValueError, RuntimeError) as exc:
                            self.profile_required = True
                            raise ValueError('请在「选购方案 → 联系与收货资料」填写并保存资料。' + str(exc)) from None
                        # Probe global ownership now; every actual attempt reacquires.
                        self.acquire()
                        self.release()
                        with self.mutex:
                            if epoch == self.generation:
                                self.submission_confirmed = data.get('confirmSubmission') is True
                                self.profile_required = False
                                self.armed = True
                                self.service.update(reason='等待新库存；库存监测已随启用一起开始，有货后自动选购')
                            self.activation_pending = False
                            self.reserved = False
                    elif op in ('start', 'rehearse', 'purchase', 'manual'):
                        hit = data if op == 'start' else data['hit']
                        no_stock_check = op in ('rehearse', 'manual')
                        plan_for_hit(self.plan, hit, rehearsal=no_stock_check, manual=op == 'purchase')
                        try:
                            self.require_profile()
                        except (ValueError, RuntimeError):
                            self.profile_required = True
                            raise ValueError('请在「选购方案 → 联系与收货资料」填写并保存资料') from None
                        self.profile_required = False
                        self.acquire()
                        with self.mutex:
                            if epoch != self.generation:
                                self.release()
                                continue
                            self.cancel.clear()
                            # Keychain prompts may take time: revalidate stock
                            # again after those waits, before any cart operation.
                            self.service.preferences = plan_for_hit(self.plan, hit, rehearsal=no_stock_check, manual=op == 'purchase')
                            if op == 'manual':
                                # 只作用于这一次执行，不写进保存的方案。
                                self.service.preferences['store_choice'] = 'same_city'
                            execution = self.service.preferences
                            previous_service = self.service
                            if self.service_factory is Checkout:
                                if execution.get('checkout_locale', 'zh_CN') != 'zh_CN':
                                    from regional_service import RegionalService
                                    self.service = RegionalService(self, execution)
                                elif not isinstance(self.service, Checkout):
                                    self.service = Checkout(self, execution)
                            if self.service is not previous_service and sys.platform == 'win32':
                                # Disconnect only our old driver, not Edge or
                                # any preserved review/payment window.
                                previous_service.browser.session.disconnect()
                            self.automatic = op in ('start', 'purchase', 'manual')
                            self.manual_run = op == 'manual'
                            if not self.automatic:
                                self.submission_confirmed = False
                            self.retryable_end = False
                            self.end_reason = ''
                            t = hit['target']
                            self.current_key = (t['partNumber'], t['storeNumber']) if t['locale'] == 'zh_CN' else (t['locale'], t['partNumber'], t['storeNumber'])
                        self.service.command('rehearse')
                    elif op == 'continue':
                        with self.mutex:
                            if epoch != self.generation:
                                continue
                            self.cancel.clear()
                        self.service.command('continue')
                    elif op == 'login':
                        self.acquire()
                        with self.mutex:
                            if epoch != self.generation:
                                self.release()
                                continue
                            self.cancel.clear()
                            if self.service_factory is Checkout and not isinstance(self.service, Checkout):
                                self.service = Checkout(self, copy.deepcopy(self.plan))
                        self.service.command('login')
                        self.login_task = True
                if not self.cancel.is_set():
                    if getattr(self.service, 'submission_deadline', None):
                        self.service.verify_order_tick()
                    elif self.service.active:
                        self.service.tick()
                    elif self.service.login_watch:
                        self.service.watch_login_tick()
                    elif self.service.region_watch:
                        self.service.watch_region_tick()
                    if self.login_task and not self.service.active:
                        # 提前登录结束（已确认登录、超时或需要本人处理）：交还选购后台，
                        # 之后才能启用自动选购或演练。
                        with self.mutex:
                            self.login_task = False
                            self.reserved = False
                        self.release()
                    mode = self.service.status.get('mode')
                    # 停在核对页等本人处理也算走到过核对页：本人可能就在那一页手动下了单。
                    if self.single_run and (mode in ('ready', 'submitting', 'awaiting_payment') or
                                            self.service.status.get('stage') in ('REVIEW', 'READY')):
                        self.reached_review = True
                    plain_end = (self.retryable_end and not self.order_guard.blocked and
                                 mode == 'attention' and not self.stopping)
                    # Retain ownership on unknown writes, login or final review.
                    if plain_end and self.single_run:
                        # 单次选购没买到时不用本人点停止，直接结束；它开始前已启用的自动选购随即恢复。
                        label = {'manual': '手动选购没买到', 'rehearse': '测试演练结束',
                                 'purchase': '这次选购没买到'}[self.single_run]
                        self.service.command('stop')
                        self.release()
                        self.cancel.set()
                        with self.mutex:
                            self.retryable_end = False
                            self.reserved = False
                            # 作废结束前排队的「继续」，免得它在已结束的流程上报错、关掉刚恢复的自动选购。
                            self.generation += 1
                            self.end_single_run(label + '：' + (self.end_reason or '官网显示暂时买不了'))
                            self.notice = {'title': label, 'body': self.last_result, 'id': uuid.uuid4().hex}
                    elif plain_end and self.armed:
                        self.avoid_until[self.current_key] = time.monotonic() + 30
                        self.service.command('stop')
                        self.service.update(reason='这次没买到：' + (self.end_reason or '官网明确无货或超预算') + '；等待新库存后再选购')
                        self.release()
                        self.cancel.set()
                        self.retryable_end = False
                        self.reserved = False
                    elif mode in ('ready', 'attention'):
                        self.armed = False
            except Exception as exc:
                code, kind, stage = self.record_exception(exc)
                step = {'BAG':'购物袋','CLEAR_BAG':'清空购物袋','FULFILLMENT':'取货安排',
                        'CONTACT':'联系资料','PAYMENT':'付款方式','REVIEW':'订单核对',
                        'READY':'提交前核对','PRODUCT':'选择商品','LOGIN':'登录',
                        'LOADING':'页面加载'}.get(stage,'当前步骤')
                self.error = str(exc) if type(exc) in (ValueError, RuntimeError) else f'{step}发生异常，已保留现场；问题记录 {code}'
                self.armed = False
                self.submission_confirmed = False
                self.activation_pending = False
                self.cancel.set()
                if self.file_lock:
                    self.service.attention(self.error)
                else:
                    self.reserved = False
                    if self.single_run:
                        # 单次选购还没碰浏览器就失败了，原因在 error 里；它开始前已启用的自动选购照常恢复。
                        with self.mutex:
                            self.end_single_run('')
        self.service.command('stop')
        self.release()
        if sys.platform == 'win32':
            self.service.browser.session.disconnect()

def main():
    # The Rust parent always speaks UTF-8, irrespective of Windows code page.
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8')
    if sys.argv[1:] == ['--self-test']:
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            bridge = Bridge(data=Path(directory))
            assert not bridge.status()['armed']
            assert bridge.cancel.is_set()
            assert not bridge.status()['submissionEnabled']
            from payment_options import official_options
            from pickup_monitor import dates_in
            assert dates_in('2026/10/23') == ['2026-10-23'] or set(dates_in('2026/10/23')) == {'2026-10-23'}
            assert official_options({'stage': 'UNKNOWN'}) == {'providers': [], 'terms': []}
            if sys.platform == 'win32':
                from playwright._impl._driver import compute_driver_executable
                from windows_vault import WindowsVault
                driver, cli = compute_driver_executable()
                assert Path(driver).is_file() and Path(cli).is_file()
                vault = WindowsVault(directory)
                vault.write_profile({'id_last4': '0616', 'test': '本地测试'})
                assert vault.load_profile()['id_last4'] == '0616'
                vault.delete_profile()
            print(json.dumps({'passed': True, 'browser_actions': 0, 'profile_reads': 0}))
        return
    bridge = Bridge()
    worker = threading.Thread(target=bridge.run)
    worker.start()
    try:
        while raw := sys.stdin.buffer.readline(262145):
            try:
                if len(raw) > 262144 or not raw.endswith(b'\n'):
                    raise ValueError('请求长度超限')
                value = bridge.request(json.loads(raw))
                reply = {'ok': True, 'value': value}
            except Exception as exc:
                reply = {'ok': False, 'error': str(exc) if type(exc) is ValueError else '请求未完成；未声明操作成功'}
            print(json.dumps(reply, ensure_ascii=False), flush=True)
    finally:
        bridge.request({'op': 'stop'})
        bridge.closed = True
        worker.join(timeout=30)

if __name__ == '__main__':
    main()

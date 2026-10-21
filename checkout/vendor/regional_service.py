"""Serial, guarded international checkout candidate (never submits an order).

One owned Safari page; same monitor, execution lock and stop token as CN.
Unsupported/ambiguous pages hand off, never silently change fulfillment.
"""
import copy
import json
from pathlib import Path
import threading
import time
import uuid
import sys
from urllib.parse import urlsplit

from checkout_service import Safari
from checkout_profile import normalized_regional_profile
from regional_checkout import REGIONS, allowed_url

JS = (Path(__file__).parent / 'regional_browser.js').read_text(encoding='utf-8')

HANDOFF_TITLES = {
    'login': '请完成 Apple 登录／安全验证',
    'payment': '请手动选择付款／分期方案',
    'review': '请本人核对订单并完成下单／支付',
    'contact': '请本人核对联系资料／地址',
    'pickup': '请本人核对门店／取货时间',
    'applecare': '请本人核对 AppleCare 方案',
    'cart': '请本人核对购物袋',
    'product': '请本人核对商品选配',
    'other': '国际选购需要本人处理',
}
STAGE_ACTIONS = {'LOGIN': 'login', 'PAYMENT': 'payment', 'REVIEW': 'review',
                 'CONTACT': 'contact', 'FULFILLMENT': 'pickup', 'BAG': 'cart',
                 'PRODUCT': 'product'}

class RegionalSafari(Safari):
    def __init__(self, owner, locale):
        super().__init__()
        self.owner, self.locale = owner, locale

    def script(self, source, timeout=12):
        if self.owner.cancel.is_set():
            raise RuntimeError('已请求停止；未发送新的浏览器操作')
        return super().script(source, timeout)

    def navigate(self, url, foreground=False):
        if not allowed_url(url, self.locale, navigation=True):
            raise ValueError('只允许本地区已核验的 Apple 商店入口')
        result = self.script(self.bound('set URL of checkoutTab to ' + json.dumps(url) + '\n' +
                                       (self.presentation_script() if foreground else '')))
        if result == 'LOST_TAB':
            raise RuntimeError('专属选购标签丢失；不接管其他页面')

    def call(self, profile, target, command=None):
        # Read-only probes (including login screens) receive no personal data.
        payload = dict(profile={}, target=target)
        if command:
            payload['command'] = command
            if command.get('kind') == 'fill':
                payload['profile'] = {command['key']: profile.get(command['key'], '')}
        expression = '(function(){' + JS + '\nreturn JSON.stringify(regionalCheckout(' + json.dumps(payload, ensure_ascii=False) + '));})()'
        result = self.script(self.bound('do JavaScript ' + json.dumps(expression, ensure_ascii=False) + ' in checkoutTab'))
        if result == 'LOST_TAB':
            return dict(stage='LOST_TAB')
        try:
            value = json.loads(result)
            if not isinstance(value, dict):
                raise ValueError()
            return value
        except (ValueError, TypeError):
            raise RuntimeError('国际选购回执无法确认；不会重复写入购物袋') from None


def make_regional_browser(owner, locale):
    if sys.platform != 'win32':
        return RegionalSafari(owner, locale)
    from edge_browser import EdgeBrowser, EdgeSession

    class RegionalEdge(EdgeBrowser):
        entrypoint = 'regionalCheckout'

        def validate_url(self, url):
            if not allowed_url(url, locale, navigation=True):
                raise ValueError('只允许本地区已核验的 Apple 商店入口')
            return urlsplit(url).path

        def check_cancelled(self):
            if owner.cancel.is_set():
                raise RuntimeError('已请求停止；未发送新的浏览器操作')

        def page(self):
            self.check_cancelled()
            return super().page()

        def new_page(self):
            self.check_cancelled()
            return super().new_page()

        def evaluate(self, expression):
            self.check_cancelled()
            return super().evaluate(expression)

        def call(self, profile, target, command=None):
            # Same privacy boundary as Safari: no contacts on read-only probes
            # or authentication pages; only the one requested field on fill.
            fields = ({command['key']: profile.get(command['key'], '')}
                      if command and command.get('kind') == 'fill' else {})
            return super().call(fields, target, command)

    return RegionalEdge(EdgeSession(owner.data), JS)


def decide(snapshot, plan, ledger):
    """Pure decision function, mutation verified by the next fresh snapshot."""
    s, p = snapshot, plan
    stage = s.get('stage')
    if stage in ('LOADING', 'LOGIN'):
        return dict(kind='wait')
    if stage in ('FOREIGN', 'NOT_FOUND', 'SESSION_EXPIRED', 'BLOCKED', 'UNKNOWN', 'LOST_TAB'):
        return dict(kind='handoff', reason='官网页面未通过地区或状态校验：' + stage)
    if stage == 'SOLD_OUT':
        return dict(kind='sold_out')
    if s.get('errors'):
        return dict(kind='handoff', reason='官网存在校验提示，请本人处理后继续')
    controls = [c for c in s.get('controls', []) if not c.get('disabled')]
    def one(group, predicate=lambda c: True):
        rows = [c for c in controls if c.get('group') == group and predicate(c)]
        return rows[0] if len(rows) == 1 else None
    def click(c, key):
        return dict(kind='click', ref=c['ref'], key=key)
    if stage == 'PRODUCT':
        # Main/catalog dimensions: never translate a CN color into another SKU.
        for key in p['regional_product']['dimensions']:
            if key == 'familyType':
                continue
            rows = [c for c in controls if c.get('group') == key]
            if not rows:
                continue
            matches = [c for c in rows if c.get('matches')]
            if len(matches) != 1:
                return dict(kind='handoff', reason='官网规格无法唯一匹配本地区 SKU：' + key)
            if not matches[0].get('selected'):
                return click(matches[0], key)
        import re
        choices = {
            'trade_in': re.compile(r'no.?trade|notrade|without|不|なし|利用しない', re.I),
            'purchase': re.compile(r'full.?price|pay.?in.?full|一括|全額|全款', re.I),
            'applecare': re.compile(r'no.?applecare|without|not.?now|不加|不需要|なし|加入しない', re.I)
        }
        if p['applecare'] != 'none':
            return dict(kind='handoff', action='applecare', reason='国际 AppleCare 价格及期限需官网核验；请本人选择，不擅自替换保障')
        for group, pattern in choices.items():
            rows = [c for c in controls if c.get('group') == group]
            if rows:
                negative = [c for c in rows if pattern.search(c.get('value', '') + ' ' + c.get('label', ''))]
                if len(negative) != 1:
                    return dict(kind='handoff', reason='官网选配无法唯一核验：' + group)
                if not negative[0].get('selected'):
                    return click(negative[0], group)
        add = one('add')
        if add:
            # An add outcome must be read in the bag, never repeated.
            if ledger.get('add_sent'):
                return dict(kind='inspect_bag')
            if s.get('product_part') != p['part_number']:
                return dict(kind='handoff', reason='官网当前选配 SKU 尚未核验，不添加可能错误的配置')
            return click(add, 'add')
        return dict(kind='wait')
    if stage == 'BAG':
        items = s.get('items', [])
        if ledger.get('clearing'):
            if items:
                if not p['clear_bag_before_purchase']:
                    return dict(kind='handoff', reason='购物袋已有商品，未授权清空')
                removals = [c for c in controls if c.get('group') == 'remove']
                if removals:
                    return click(removals[0], 'remove:' + removals[0]['ref'])
                return dict(kind='handoff', reason='购物袋商品无法安全移除，请本人处理')
            return dict(kind='open_product') if s.get('bag_empty') is True else dict(kind='handoff', reason='未能确认购物袋为空，保留现场请本人核对')
        if len(items) != 1 or items[0].get('part') != p['part_number']:
            return dict(kind='handoff', reason='购物袋 SKU 不符或无法逐项核验；不继续结账')
        item = items[0]
        if item.get('quantity') != p['quantity']:
            if str(p['quantity']) not in item.get('quantity_options', []):
                return dict(kind='handoff', reason='官网未提供要求数量；不拆单或换数量')
            return dict(kind='select', ref=item['quantity_ref'], value=str(p['quantity']), key='quantity')
        if item.get('applecare') != (p['applecare'] == 'add'):
            return dict(kind='handoff', reason='购物袋保障方案不符')
        if s.get('currency') != p['currency'] or not isinstance(s.get('total'), (int, float)):
            return dict(kind='handoff', reason='购物袋币种或含税总额无法唯一确认')
        if not 0 < s['total'] <= p['max_price']:
            return dict(kind='handoff', reason='本地区订单总额超过当地货币预算')
        checkout = one('checkout')
        return click(checkout, 'checkout') if checkout else dict(kind='wait')
    if stage in ('PAYMENT', 'REVIEW'):
        reason = ('请在 Safari 本地区 Apple 官网选择付款或分期方案；银行卡验证、授信及签约由本人完成。'
                  if stage == 'PAYMENT' else
                  '请在 Safari 核对商品、数量、保障、门店、日期、总价及付款方案；最终下单及支付由本人完成。')
        return dict(kind='handoff', action=STAGE_ACTIONS[stage],
                    reason=reason + '国际版未提交订单、未付款，不代表已经锁单。')
    if stage in ('FULFILLMENT', 'CONTACT'):
        stores = [c for c in controls if c.get('group') == 'store']
        if stores:
            for scope in {c.get('scope', 'store') for c in stores}:
                wanted = [c for c in stores if c.get('scope', 'store') == scope and c.get('matches')]
                if len(wanted) != 1:
                    return dict(kind='handoff', reason='目标门店不可选或无法逐项唯一匹配；不擅自送货')
                if not wanted[0].get('selected'):
                    return click(wanted[0], 'store:' + scope)
            ledger['store_verified'] = True
        elif not ledger.get('store_verified'):
            pickup = one('pickup')
            if pickup and not pickup.get('selected'):
                return click(pickup, 'pickup')
            return dict(kind='handoff', reason='取货门店尚未核验；请在官网选择目标门店后继续')
        # A date must have an exact ISO value. Never guess localized/relative dates.
        earliest = p.get('pickup_date_policy') == 'earliest'
        def date_key(value):
            from datetime import date
            try:
                return date.fromisoformat(value).isoformat() if date.fromisoformat(value).isoformat() == value else None
            except (ValueError, TypeError):
                return None
        def ordered_choices(choices, group):
            if group == 'date':
                if earliest:
                    if any(not date_key(c['value']) for c in choices):
                        return []
                    return sorted(choices, key=lambda c: c['value'])
                dates = p.get('pickup_dates', [])
                return sorted([c for c in choices if c['value'] in dates], key=lambda c: dates.index(c['value']))
            if earliest:
                import re
                ranked = []
                for c in choices:
                    match = re.search(r'(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)(?!\d)\s*(am|pm)?', c.get('label', '') or c['value'], re.I)
                    if not match:
                        return []
                    hour = int(match[1])
                    if match[3]:
                        if not 1 <= hour <= 12:
                            return []
                        hour = hour % 12 + (12 if match[3].lower() == 'pm' else 0)
                    ranked.append((hour*60+int(match[2]), c))
                return [c for _, c in sorted(ranked, key=lambda row: row[0])]
            return choices
        for group in ('date', 'time'):
            rows = [c for c in controls if c.get('group') == group]
            if rows:
                if len(rows) == 1 and rows[0].get('options'):
                    c = rows[0]
                    options = ordered_choices([o for o in c['options'] if o['value'] and not o.get('disabled')], group)
                    if not options:
                        return dict(kind='handoff', reason='取货日期或时段未匹配，请本人核对')
                    if c.get('value') not in [o['value'] for o in options] or (earliest and c.get('value') != options[0]['value']):
                        return dict(kind='select', ref=c['ref'], value=options[0]['value'], key=group)
                    if group == 'date':
                        ledger['date_verified'] = True
                    continue
                rows = ordered_choices(rows, group)
                if not rows:
                    return dict(kind='handoff', reason='取货日期未匹配设置，请本人选日期后继续')
                if not any(c.get('selected') for c in rows) or (earliest and not rows[0].get('selected')):
                    return click(rows[0], group)
                if group == 'date':
                    ledger['date_verified'] = True
        if (p.get('pickup_dates') or earliest) and not ledger.get('date_verified'):
            return dict(kind='handoff', reason='取货日期尚无法核验，请本人核对官网日期；不忽略已设日期范围')
        for f in s.get('fields', []):
            if f.get('disabled'):
                continue
            key = f.get('key')
            if not key:
                if f.get('required') and (not f.get('filled') or not f.get('valid')):
                    return dict(kind='handoff', action='contact', reason='官网新增必填字段，请本人填写后继续')
                continue
            value = ledger.get('profile', {}).get(key)
            if value and (not f.get('matches') or not f.get('valid')):
                if f.get('options'):
                    matches = [o for o in f['options'] if o['value'] == value or o['label'] == value]
                    if len(matches) != 1:
                        return dict(kind='handoff', action='contact', reason='官网地址选项未匹配，请本人选择后继续')
                    return dict(kind='select', ref=f['ref'], value=matches[0]['value'], key=key)
                return dict(kind='fill', ref=f['ref'], key=key)
            if f.get('required') and (not value or not f.get('valid')):
                return dict(kind='handoff', action='contact', reason='本地区联系资料不完整或官网校验未通过')
        next_button = one('continue')
        return click(next_button, 'continue:' + stage) if next_button else dict(kind='wait')
    return dict(kind='handoff', reason='官网页面尚未适配，已保留现场')


class RegionalService:
    def __init__(self, owner, plan):
        self.owner, self.preferences = owner, copy.deepcopy(plan)
        self.lock = threading.RLock()
        self.status = dict(mode='stopped', stage='IDLE', reason='国际选购候选流程未启动')
        self.active = self.login_watch = self.region_watch = False
        self.browser = make_regional_browser(owner, plan['checkout_locale'])
        self.started, self.next_tick, self.changed_at = 0, 0, 0
        self.fingerprint, self.pending = None, None
        self.ledger = {}
        self.sent = set()
        self.handoff_key = self.notice_id = None
        self.submission_deadline = None

    @property
    def execution_preferences(self):
        return self.preferences

    def timing(self):
        return {'total_seconds': round(time.monotonic() - self.started, 3) if self.started else 0}

    def update(self, **values):
        with self.lock:
            self.status.update(values)

    def require_user_action(self, reason, kind=None, waiting=False):
        """One intervention drives both the app and the background Bark queue.

        Re-reading/continuing the same document must not resend the same notice.
        Only fixed workflow text is included, never profile values or session URLs.
        """
        if self.owner.cancel.is_set():
            return
        if sys.platform == 'win32':
            reason = reason.replace('Safari', 'Edge')
        kind = kind or STAGE_ACTIONS.get(self.status.get('stage'), 'other')
        region = REGIONS[self.preferences['checkout_locale']]['title']
        title = region + ' · ' + HANDOFF_TITLES[kind]
        key = (kind, self.status.get('stage'), (self.fingerprint or '').split(':')[0], reason)
        with self.lock:
            self.status.update(mode='waiting_user' if waiting else 'attention', reason=reason,
                               intervention=dict(kind=kind, title=title, body=reason, region=region))
            if key != self.handoff_key:
                self.handoff_key = key
                self.notice_id = uuid.uuid4().hex
                self.owner.notice = dict(kind=kind, title=title, body=reason, id=self.notice_id)

    def clear_intervention(self, clear_notice=False):
        self.update(intervention=None)
        if clear_notice and (self.owner.notice or {}).get('id') == self.notice_id:
            self.owner.notice = None

    def attention(self, reason, kind=None):
        self.active = False
        self.require_user_action(reason, kind)

    def command(self, name):
        if name == 'stop':
            self.active = False
            self.clear_intervention(clear_notice=True)
            self.update(mode='stopped', stage='IDLE', reason='已停止，国际官网现场保留；未提交订单')
        elif name == 'rehearse':
            self.clear_intervention(clear_notice=True)
            self.handoff_key = self.notice_id = None
            self.pending = self.fingerprint = None
            self.next_tick = 0
            self.started = self.changed_at = time.monotonic()
            self.ledger = dict(clearing=True, profile=normalized_regional_profile(self.preferences['checkout_locale']))
            self.sent = set()
            self.browser.open(self.preferences['bag_url'])
            self.active = True
            self.update(mode='running', stage='BAG', reason='核对本地区购物袋，按已授权范围清理')
        elif name == 'continue':
            if self.pending:
                raise ValueError('上次操作结果尚未确认，请停止并核对购物袋；不重复操作')
            self.active = True
            self.clear_intervention()
            self.changed_at = time.monotonic()
            self.update(mode='running', reason='重新核验官网实际状态')

    def tick(self):
        now = time.monotonic()
        if not self.active or self.owner.cancel.is_set() or now < self.next_tick:
            return
        self.next_tick = now + .5
        s = self.browser.call(self.ledger.get('profile', {}), self.preferences)
        if self.owner.cancel.is_set():
            return
        if s.get('fingerprint') != self.fingerprint:
            if s.get('fingerprint'):
                self.pending = None
            self.fingerprint = s.get('fingerprint')
            self.changed_at = now
        self.update(stage=s.get('stage', 'UNKNOWN'))
        if s.get('stage') == 'LOGIN':
            self.require_user_action('请在 Safari 本地区官网本人登录／完成验证码，完成后自动重新识别；不要提交订单或付款。', 'login', waiting=True)
            if now - self.changed_at > 300:
                self.active = False
                self.update(mode='attention')
            return
        if self.status.get('mode') == 'waiting_user' and s.get('stage') != 'LOADING':
            self.clear_intervention()
            self.handoff_key = None
            self.update(mode='running', reason='已离开登录步骤，重新核对官网页面')
        if self.pending:
            if now - self.changed_at > 5:
                if self.pending == 'add':
                    self.pending = None
                    self.browser.navigate(self.preferences['bag_url'])
                    self.changed_at = now
                else:
                    self.attention('操作后未确认页面进展；保留现场，不重复点击')
            return
        if now - self.changed_at > 30:
            self.attention('官网超过 30 秒无可验证进展，请本人处理后继续')
            return
        d = decide(s, self.preferences, self.ledger)
        kind = d['kind']
        if kind == 'wait':
            return
        if kind == 'sold_out':
            self.owner.retryable_end = bool(self.owner.automatic)
            self.attention('官网明确无货，本次流程结束；等待新的有效库存')
        elif kind == 'handoff':
            self.attention(d['reason'], d.get('action'))
        elif kind == 'open_product':
            self.ledger['clearing'] = False
            self.browser.navigate(self.preferences['product_url'])
            self.changed_at = now
        elif kind == 'inspect_bag':
            self.browser.navigate(self.preferences['bag_url'])
            self.changed_at = now
        else:
            token = (d['key'], s['fingerprint'].split(':')[0])
            if token in self.sent:
                self.attention('同一步骤回读未能确认，保留现场；不重复点击或填入')
                return
            command = dict(d, expected=s['fingerprint'], nonce=uuid.uuid4().hex)
            if self.owner.cancel.is_set():
                return
            if d['key'] == 'add':
                self.ledger['add_sent'] = True
            self.pending = d['key']
            self.sent.add(token)
            self.clear_intervention()
            self.handoff_key = None
            self.changed_at = now
            result = self.browser.call(self.ledger.get('profile', {}), self.preferences, command)
            if result.get('result') != 'dispatched':
                self.attention('官网操作未确认，已保留现场；不会重新加购或提交订单')

"""手动开始：指定门店不能取货时改选同城能取货的门店；固定门店的原有行为不变。"""
import copy
import unittest
from unittest.mock import Mock

from worker import STORES, core  # 先导入 worker：它会把 vendor 目录加进导入路径
from checkout_engine import DEFAULTS, Attempt, validate_preferences, variants

NAMES = {s['id']: s['name'] for s in STORES}
BASE, SAME_CITY, OTHER_CITY = 'R683', 'R390', 'R575'  # 上海环球港、上海香港广场、武汉


def plan(choice):
    p = validate_preferences(dict(DEFAULTS, fulfillment='pickup', financing='full', payment='支付宝',
                                  store_id=BASE, pickup_date_policy='specified', pickup_dates=['2026-10-23']))
    if choice:
        p['store_choice'] = choice
    return p


def attempt(choice='same_city'):
    p = plan(choice)
    a = Attempt(p, variants(p)[0], 0)
    a.bag_verified = True
    a.started = 0
    a.target['pickup_dates'] = list(p['pickup_dates'])
    return a


def store(sid, selected=False, disabled=False, label=None):
    return dict(ref='s-' + sid, label=label or NAMES[sid], role='radio', group='store',
                store_id=sid, selected=selected, disabled=disabled)


def dates(*values, selected=None):
    return dict(key='pickup_date', ref='d', kind='select', selected=bool(selected), disabled=False,
                choices=[dict(ref='d', value=v, label=v, role='option', selected=v == selected, disabled=False)
                         for v in values])


def read(a, stores, fp, schedule=()):
    """按页面脚本的规则，用这次读取时的目标门店算出各项门店标记。"""
    controls = []
    for c in stores:
        c = dict(c)
        c['store_matches'] = c['store_id'] == a.target['store_id']
        controls.append(c)
    chosen = [c for c in controls if c['selected']]
    return dict(stage='FULFILLMENT', fingerprint=fp, controls=controls, fields=[], pickup_selected=True,
                store_selected=bool(chosen) and all(c['store_matches'] and not c['disabled'] for c in chosen),
                store_selection_conflict=any(not c['store_matches'] for c in chosen),
                pickup_unavailable=any(c['store_matches'] and c['disabled'] for c in controls),
                pickup_schedule=list(schedule))


class ManualStoreChoiceTests(unittest.TestCase):
    def test_fixed_store_keeps_the_original_sold_out_rule(self):
        for choice in (None, 'fixed'):
            a = attempt(choice)
            d = a.decide(read(a, [store(BASE, disabled=True), store(SAME_CITY)], 'a'), 1)
            self.assertEqual(d.kind, 'sold_out')
            self.assertIn('自提不可用', d.reason)
            self.assertEqual(a.target['store_id'], BASE)

    def test_unavailable_base_store_switches_to_a_same_city_store(self):
        a = attempt()
        stores = [store(BASE, disabled=True), store(OTHER_CITY), store(SAME_CITY)]
        d = a.decide(read(a, stores, 'a'), 1)
        self.assertEqual((d.kind, d.reason, d.ref), ('click', 'store:' + SAME_CITY, 's-' + SAME_CITY))
        self.assertEqual((a.target['store_id'], a.target['store']), (SAME_CITY, NAMES[SAME_CITY]))
        # 选上之后按新门店继续：选日期，不再回头点原门店。
        stores[2] = store(SAME_CITY, selected=True)
        d = a.decide(read(a, stores, 'b', [dates('2026-10-23')]), 2)
        self.assertEqual((d.kind, d.value), ('set', '2026-10-23'))

    def test_other_city_store_is_never_chosen(self):
        a = attempt()
        stores = [store(BASE, disabled=True), store(OTHER_CITY)]
        self.assertEqual(a.decide(read(a, stores, 'a'), 1).kind, 'wait')
        d = a.decide(read(a, stores, 'a'), 3.5)
        self.assertEqual(d.kind, 'sold_out')
        self.assertIn('同城其他门店也都不可取货', d.reason)
        self.assertEqual(a.target['store_id'], BASE)

    def test_unrecognisable_store_list_is_not_reported_as_no_stock(self):
        a = attempt()
        unknown = dict(store(SAME_CITY), store_id='', label='某个新门店 今天可取货')
        for t in (1, 5):
            d = a.decide(read(a, [unknown], 'a'), t)
            self.assertNotEqual(d.kind, 'sold_out')
        self.assertEqual(a.target['store_id'], BASE)

    def test_store_without_allowed_dates_moves_to_the_next_store(self):
        a = attempt()
        a.sent['pickup_date:d::2026-10-26'] = 1
        a.pickup_exhausted_dates.add('2026-10-26')
        stores = [store(BASE, selected=True), store(SAME_CITY)]
        d = a.decide(read(a, stores, 'a', [dates('2026-10-26')]), 1)
        self.assertEqual((d.kind, d.reason), ('click', 'store:' + SAME_CITY))
        self.assertEqual(a.exhausted_stores, {BASE})
        # 换门店后，上一家的日期记录作废。
        self.assertFalse(any(k.startswith('pickup_date:') for k in a.sent))
        self.assertEqual(a.pickup_exhausted_dates, set())

    def test_no_stock_text_on_an_enabled_store_counts_as_unavailable(self):
        a = attempt()
        stores = [store(BASE, label=NAMES[BASE] + ' 目前不可取货'), store(SAME_CITY, label=NAMES[SAME_CITY] + ' 今天可取货')]
        d = a.decide(read(a, stores, 'a'), 1)
        self.assertEqual((d.kind, d.reason), ('click', 'store:' + SAME_CITY))

    def test_usable_store_already_selected_on_the_page_is_adopted_without_a_click(self):
        a = attempt()
        d = a.decide(read(a, [store(BASE), store(SAME_CITY, selected=True)], 'a'), 1)
        self.assertEqual(d.kind, 'wait')
        self.assertEqual(a.target['store_id'], SAME_CITY)
        self.assertEqual(a.sent, {})

    def test_usable_base_store_is_clicked_with_a_per_store_retry_budget(self):
        a = attempt()
        s = read(a, [store(BASE), store(SAME_CITY)], 'a')
        self.assertEqual(a.decide(s, 1).reason, 'store:' + BASE)
        self.assertEqual(a.decide(s, 3).kind, 'wait')
        self.assertEqual(a.decide(read(a, [store(BASE), store(SAME_CITY)], 'b'), 7).reason, 'store:' + BASE)
        self.assertEqual(a.decide(read(a, [store(BASE), store(SAME_CITY)], 'c'), 13).kind, 'attention')

    def test_continue_reconsiders_stores_that_were_excluded(self):
        p = plan('same_city')
        service = core.Service(browser=Mock(), preferences=copy.deepcopy(p), persist=False)
        service.attempt = attempt()
        service.attempt.exhausted_stores = {BASE, SAME_CITY}
        service.attempt.sent.update({'store:' + BASE: 2, 'add': 1})
        service.phase = 'RUN'
        service.status['mode'] = 'attention'
        service.command('continue')
        self.assertEqual(service.attempt.exhausted_stores, set())
        self.assertEqual(service.attempt.sent, {'add': 1}, '加购记录必须保留，只有门店点击次数重新计')


if __name__ == '__main__':
    unittest.main()

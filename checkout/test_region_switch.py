"""取货地区从别的城市切到目标城市：点回省份要立刻执行，刚选完省份时不来回切换。"""
import unittest

from worker import DEFAULTS  # 先导入 worker：它会把 vendor 目录加进导入路径
from checkout_engine import Attempt, validate_preferences, variants


def picker(step, next_key=None):
    r = dict(context='pickup', kind='tabs', open=True, matched=False, configured=True, step=step, reason='loading')
    if next_key:
        r['next'] = dict(key=next_key, ref=next_key + '-ref')
    return dict(stage='FULFILLMENT', region_picker=r)


class RegionSwitchTests(unittest.TestCase):
    def setUp(self):
        p = validate_preferences(dict(DEFAULTS, fulfillment='pickup', financing='full', payment='支付宝', store_id='R683'))
        self.a = Attempt(p, variants(p)[0], 0)

    def test_going_back_to_the_province_tab_happens_at_once(self):
        d = self.a.reconcile_region(picker('district', 'region_pickup_tab_province'), 1)
        self.assertEqual((d.kind, d.key), ('set', 'region_pickup_tab_province'))

    def test_full_switch_from_another_city_to_shanghai(self):
        steps = [(1, 'district', 'region_pickup_tab_province'),
                 (2, 'province', 'region_pickup_province'),
                 (4, 'district', 'region_pickup_district')]
        for now, step, key in steps:
            d = self.a.reconcile_region(picker(step, key), now)
            self.assertEqual((d.kind, d.key), ('set', key), key)

    def test_stale_old_city_list_right_after_choosing_shanghai_does_not_bounce_back(self):
        self.a.reconcile_region(picker('province', 'region_pickup_province'), 2)
        # 刚点了上海，页面还没刷新，仍显示旧城市的区列表：12 秒内不回头再点省份。
        d = self.a.reconcile_region(picker('district', 'region_pickup_tab_province'), 3)
        self.assertEqual(d.kind, 'wait')


if __name__ == '__main__':
    unittest.main()

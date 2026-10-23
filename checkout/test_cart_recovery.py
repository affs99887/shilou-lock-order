"""Cart read-back recovery uses mocked pages and a temporary data directory."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, call, patch

from worker import Bridge, core


class CartRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bridge = Bridge(data=Path(self.temp.name))
        self.bridge.cancel.clear()
        self.service = self.bridge.service
        self.service.preferences.update(
            model='iphone-18-pro', colors=['冰川蓝色'], capacities=['256GB'],
            quantity=1, fulfillment='pickup', pickup_date_policy='earliest',
            clear_bag_before_purchase=True)
        self.service.phase = 'CLEAR_BAG'
        self.service.phase_since = 10
        self.service.active = True
        self.service.job = 'rehearse'
        self.service.update(mode='running')
        self.service.browser = Mock()
        self.bag = dict(stage='BAG', fingerprint='old-bag',
                        items=[dict(title='旧商品', quantity=1, remove_ref='old-row')])

    def tick(self, snapshot, now, receipt=None):
        browser = self.service.browser
        browser.call.side_effect = [snapshot, receipt] if receipt else None
        browser.call.return_value = snapshot
        with patch.object(core.time, 'monotonic', return_value=now):
            self.service.tick()

    def remove(self):
        self.tick(self.bag, 11, dict(result='dispatched'))

    def writes(self):
        return [c.args[2] for c in self.service.browser.call.call_args_list if len(c.args) == 3]

    def missing(self, now):
        """官网显示找不到页面，再到约定时刻执行那次重开。"""
        self.tick(dict(stage='NOT_FOUND'), now)
        self.tick(dict(stage='LOADING'), now + (1 if self.service.missing_retries == 1 else 3))

    def test_missing_page_reopens_cart_once_and_confirmed_empty_starts_product(self):
        self.remove()
        self.tick(dict(stage='NOT_FOUND'), 16)
        browser = self.service.browser
        browser.reopen_failed_entry.assert_not_called()  # 先等 1 秒
        self.assertTrue(self.service.active)
        self.assertIsNone(self.bridge.notice)
        self.assertIsNone(self.service.status.get('intervention'))
        self.tick(dict(stage='LOADING'), 17)
        browser.reopen_failed_entry.assert_called_once_with('https://www.apple.com.cn/shop/bag')
        self.assertEqual(self.service.pending_remove, (1, 11, False))
        self.tick(dict(stage='LOADING'), 18)
        self.assertIsNone(self.service.attempt)
        self.tick(dict(stage='BAG', empty_bag=True, items=[]), 19)
        self.assertEqual(self.service.phase, 'RUN')
        self.assertEqual(browser.navigate.call_args_list, [
            call(core.CATALOG['iphone-18-pro']['url'], foreground=True)])
        self.assertEqual([c['kind'] for c in self.writes()], ['remove'])
        trace = json.loads((Path(self.temp.name) / 'checkout-diagnostics.json').read_text())['events']
        self.assertEqual([e['http_status'] for e in trace if e['stage'] == 'NOT_FOUND'], [0])
        self.assertEqual(sum(e['action'] == 'page_reopen' for e in trace), 1)

    def test_unchanged_cart_never_repeats_remove_and_keeps_original_deadline(self):
        self.remove()
        self.missing(16)
        # 重开后购物袋没变：超过 5 秒再读一次核实，删除本身不重复；期限仍从原来算起。
        for now in (25, 39):
            self.tick(self.bag, now)
        self.assertTrue(self.service.active)
        self.tick(self.bag, 41)
        self.assertFalse(self.service.active)
        self.assertIn('30 秒', self.service.status['reason'])
        self.assertIsNone(self.service.attempt)
        self.assertEqual(len(self.writes()), 1)
        self.service.browser.reopen_failed_entry.assert_called_once()
        self.service.browser.navigate.assert_called_once_with('https://www.apple.com.cn/shop/bag')

    def test_repeated_missing_page_reopens_twice_then_ends_without_writes(self):
        self.remove()
        self.missing(16)
        self.missing(18)
        self.assertTrue(self.service.active)
        self.tick(dict(stage='NOT_FOUND'), 22)
        self.assertFalse(self.service.active)
        self.assertTrue(self.bridge.retryable_end, '打不开不需要本人处理：单次选购自己结束，自动选购等下一次有货')
        self.assertEqual(self.bridge.end_reason, '官网页面重开 2 次后仍打不开')
        self.assertEqual(self.service.browser.reopen_failed_entry.call_count, 2)
        self.service.browser.navigate.assert_not_called()
        self.assertEqual(len(self.writes()), 1)

    def test_reopen_clears_old_pause_banner(self):
        self.remove()
        self.service.require_user_action('not_found')
        self.tick(dict(stage='NOT_FOUND'), 16)
        self.assertIsNone(self.service.status.get('intervention'))
        self.assertEqual(self.service.status['mode'], 'recovering')

    def test_delayed_response_recheck_does_not_use_up_page_reopens(self):
        self.remove()
        self.tick(self.bag, 17)
        self.service.browser.navigate.assert_called_once_with('https://www.apple.com.cn/shop/bag')
        self.missing(18)
        self.assertTrue(self.service.active)
        self.service.browser.reopen_failed_entry.assert_called_once_with('https://www.apple.com.cn/shop/bag')
        self.assertEqual(self.service.browser.navigate.call_count, 1)
        self.assertEqual(len(self.writes()), 1)

    def test_expired_deadline_does_not_reopen_missing_page(self):
        self.remove()
        self.tick(dict(stage='NOT_FOUND'), 41)
        self.assertFalse(self.service.active)
        self.assertEqual(self.bridge.notice['kind'], 'not_found')
        self.service.browser.navigate.assert_not_called()

    def test_missing_entry_without_pending_remove_reopens_without_writes(self):
        self.tick(dict(stage='NOT_FOUND'), 11)
        self.assertTrue(self.service.active)
        self.assertIsNone(self.bridge.notice)
        self.tick(dict(stage='LOADING'), 12)
        self.service.browser.reopen_failed_entry.assert_called_once_with('https://www.apple.com.cn/shop/bag')
        self.service.browser.navigate.assert_not_called()
        self.assertEqual(self.writes(), [])

    def test_unrecognized_cart_is_not_treated_as_empty_after_reopen(self):
        self.remove()
        self.missing(16)
        self.tick(dict(stage='BAG', items=[]), 18)
        self.assertIsNone(self.service.attempt)
        self.tick(dict(stage='BAG', items=[]), 41)
        self.assertFalse(self.service.active)
        self.service.browser.reopen_failed_entry.assert_called_once()
        self.service.browser.navigate.assert_not_called()

    def test_busy_or_invalid_empty_cart_does_not_start_product(self):
        self.remove()
        self.missing(16)
        self.tick(dict(stage='BAG', empty_bag=True, items=[], bag_busy=True), 18)
        self.assertIsNone(self.service.attempt)
        self.tick(dict(stage='BAG', empty_bag=True, items=[], errors=['invalid']), 19)
        self.assertFalse(self.service.active)
        self.assertIsNone(self.service.attempt)
        self.service.browser.navigate.assert_not_called()

    def test_remaining_row_is_removed_only_after_count_decreases(self):
        self.bag['items'].append(dict(title='另一商品', quantity=1, remove_ref='other-row'))
        self.remove()
        self.missing(16)
        remaining = dict(stage='BAG', fingerprint='remaining', items=self.bag['items'][1:])
        self.tick(remaining, 18, dict(result='dispatched'))
        self.assertEqual([c['ref'] for c in self.writes()], ['old-row', 'other-row'])
        self.assertIsNone(self.service.attempt)

    def test_access_denial_and_authentication_require_handling_without_recheck(self):
        for stage, code, kind in [('NOT_FOUND', 541, 'blocked'), ('BLOCKED', 403, 'blocked'),
                                  ('BLOCKED', 429, 'blocked'), ('LOGIN', 0, 'login'),
                                  ('CAPTCHA', 0, 'captcha'), ('SESSION_EXPIRED', 0, 'expired')]:
            with self.subTest(stage=stage, code=code):
                self.service.active = True
                self.service.breaker.until = 0
                self.service.access_blocked = False
                self.service.pending_remove = (1, 11, False)
                self.tick(dict(stage=stage, http_status=code), 16)
                self.assertFalse(self.service.active)
                self.assertEqual(self.bridge.notice['kind'], kind)
                self.service.browser.navigate.assert_not_called()
                self.assertEqual(self.writes(), [])

    def test_stop_arriving_during_snapshot_prevents_recheck(self):
        self.service.pending_remove = (1, 11, False)
        def cancelled_read(*args):
            self.bridge.cancel.set()
            return dict(stage='NOT_FOUND')
        self.service.browser.call.side_effect = cancelled_read
        with patch.object(core.time, 'monotonic', return_value=16):
            self.service.tick()
        self.service.browser.navigate.assert_not_called()
        self.assertEqual(self.service.pending_remove, (1, 11, False))

    def test_unconfirmed_order_prevents_cart_recovery(self):
        self.service.pending_remove = (1, 11, False)
        self.bridge.order_guard.begin()
        self.tick(dict(stage='NOT_FOUND'), 16)
        self.assertFalse(self.service.active)
        self.assertEqual(self.bridge.notice['kind'], 'order_unknown')
        self.service.browser.navigate.assert_not_called()

    def test_failed_reopen_still_uses_up_its_allowance(self):
        self.remove()
        self.service.browser.reopen_failed_entry.side_effect = RuntimeError('navigation not confirmed')
        for now in (16, 18):
            self.tick(dict(stage='NOT_FOUND'), now)
            with self.assertRaisesRegex(RuntimeError, 'navigation not confirmed'):
                self.tick(dict(stage='NOT_FOUND'), now + (1 if now == 16 else 3))
        self.tick(dict(stage='NOT_FOUND'), 22)
        self.assertFalse(self.service.active)
        self.assertEqual(self.service.browser.reopen_failed_entry.call_count, 2)
        self.service.browser.navigate.assert_not_called()
        self.assertEqual(len(self.writes()), 1)

    def test_browser_without_reopen_keeps_the_single_recheck(self):
        # 没有重开能力的浏览器（如测试替身）仍按原规则：删除后官网 404 先重新读一次购物袋。
        self.service.browser = Mock(spec=['call', 'navigate', 'open', 'window'])
        self.remove()
        self.tick(dict(stage='NOT_FOUND'), 16)
        self.service.browser.navigate.assert_called_once_with('https://www.apple.com.cn/shop/bag')
        self.tick(dict(stage='NOT_FOUND'), 17)
        self.assertFalse(self.service.active)
        self.assertEqual(self.bridge.notice['kind'], 'not_found')
        self.assertEqual(len(self.writes()), 1)


if __name__ == '__main__':
    unittest.main()

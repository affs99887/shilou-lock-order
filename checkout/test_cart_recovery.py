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

    def test_missing_page_reopens_cart_once_and_confirmed_empty_starts_product(self):
        self.remove()
        self.tick(dict(stage='NOT_FOUND'), 16)
        self.service.browser.navigate.assert_called_once_with('https://www.apple.com.cn/shop/bag')
        self.assertTrue(self.service.active)
        self.assertIsNone(self.bridge.notice)
        self.assertIsNone(self.service.status.get('intervention'))
        self.assertEqual(self.service.pending_remove, (1, 11, True))
        self.tick(dict(stage='LOADING'), 17)
        self.assertIsNone(self.service.attempt)
        self.tick(dict(stage='BAG', empty_bag=True, items=[]), 18)
        self.assertEqual(self.service.phase, 'RUN')
        self.assertEqual(self.service.browser.navigate.call_args_list, [
            call('https://www.apple.com.cn/shop/bag'),
            call(core.CATALOG['iphone-18-pro']['url'], foreground=True)])
        self.assertEqual([c['kind'] for c in self.writes()], ['remove'])
        trace = json.loads((Path(self.temp.name) / 'checkout-diagnostics.json').read_text())['events']
        self.assertEqual([e['http_status'] for e in trace if e['stage'] == 'NOT_FOUND'], [0])
        self.assertEqual(sum(e['action'] == 'bag_recheck' for e in trace), 1)

    def test_unchanged_cart_never_repeats_remove_and_keeps_original_deadline(self):
        self.remove()
        self.tick(dict(stage='NOT_FOUND'), 16)
        for now in (17, 25, 39):
            self.tick(self.bag, now)
        self.assertTrue(self.service.active)
        self.tick(self.bag, 41)
        self.assertFalse(self.service.active)
        self.assertIn('30 秒', self.service.status['reason'])
        self.assertIsNone(self.service.attempt)
        self.assertEqual(len(self.writes()), 1)
        self.service.browser.navigate.assert_called_once_with('https://www.apple.com.cn/shop/bag')

    def test_repeated_missing_page_stops_and_notifies(self):
        self.remove()
        self.tick(dict(stage='NOT_FOUND'), 16)
        self.tick(dict(stage='NOT_FOUND'), 17)
        self.assertFalse(self.service.active)
        self.assertEqual(self.bridge.notice['kind'], 'not_found')
        self.assertEqual(self.service.browser.navigate.call_count, 1)
        self.assertEqual(len(self.writes()), 1)

    def test_resumed_recheck_clears_old_pause_banner_and_reports_a_new_failure(self):
        self.remove()
        self.service.require_user_action('not_found')
        old_notice = self.bridge.notice['id']
        self.tick(dict(stage='NOT_FOUND'), 16)
        self.assertIsNone(self.service.status.get('intervention'))
        self.tick(dict(stage='NOT_FOUND'), 17)
        self.assertEqual(self.bridge.notice['kind'], 'not_found')
        self.assertNotEqual(self.bridge.notice['id'], old_notice)

    def test_delayed_response_and_missing_page_share_one_recheck(self):
        self.remove()
        self.tick(self.bag, 17)
        self.tick(dict(stage='NOT_FOUND'), 18)
        self.assertFalse(self.service.active)
        self.assertEqual(self.bridge.notice['kind'], 'not_found')
        self.assertEqual(self.service.browser.navigate.call_count, 1)
        self.assertEqual(len(self.writes()), 1)

    def test_expired_deadline_does_not_reopen_missing_page(self):
        self.remove()
        self.tick(dict(stage='NOT_FOUND'), 41)
        self.assertFalse(self.service.active)
        self.assertEqual(self.bridge.notice['kind'], 'not_found')
        self.service.browser.navigate.assert_not_called()

    def test_missing_entry_without_pending_remove_is_not_retried(self):
        self.tick(dict(stage='NOT_FOUND'), 11)
        self.assertFalse(self.service.active)
        self.assertEqual(self.bridge.notice['kind'], 'not_found')
        self.service.browser.navigate.assert_not_called()
        self.assertEqual(self.writes(), [])

    def test_unrecognized_cart_is_not_treated_as_empty_after_recheck(self):
        self.remove()
        self.tick(dict(stage='NOT_FOUND'), 16)
        self.tick(dict(stage='BAG', items=[]), 17)
        self.assertIsNone(self.service.attempt)
        self.tick(dict(stage='BAG', items=[]), 41)
        self.assertFalse(self.service.active)
        self.assertEqual(self.service.browser.navigate.call_count, 1)

    def test_busy_or_invalid_empty_cart_does_not_start_product(self):
        self.remove()
        self.tick(dict(stage='NOT_FOUND'), 16)
        self.tick(dict(stage='BAG', empty_bag=True, items=[], bag_busy=True), 17)
        self.assertIsNone(self.service.attempt)
        self.tick(dict(stage='BAG', empty_bag=True, items=[], errors=['invalid']), 18)
        self.assertFalse(self.service.active)
        self.assertIsNone(self.service.attempt)
        self.assertEqual(self.service.browser.navigate.call_count, 1)

    def test_remaining_row_is_removed_only_after_count_decreases(self):
        self.bag['items'].append(dict(title='另一商品', quantity=1, remove_ref='other-row'))
        self.remove()
        self.tick(dict(stage='NOT_FOUND'), 16)
        remaining = dict(stage='BAG', fingerprint='remaining', items=self.bag['items'][1:])
        self.tick(remaining, 17, dict(result='dispatched'))
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

    def test_navigation_exception_does_not_restore_recheck_allowance(self):
        self.remove()
        self.service.browser.navigate.side_effect = RuntimeError('navigation not confirmed')
        with self.assertRaisesRegex(RuntimeError, 'navigation not confirmed'):
            self.tick(dict(stage='NOT_FOUND'), 16)
        self.tick(dict(stage='NOT_FOUND'), 17)
        self.assertFalse(self.service.active)
        self.assertEqual(self.service.browser.navigate.call_count, 1)
        self.assertEqual(len(self.writes()), 1)


if __name__ == '__main__':
    unittest.main()

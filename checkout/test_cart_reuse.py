import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from worker import Bridge, core


class CartReuseTests(unittest.TestCase):
    def test_empty_cart_starts_product_for_both_clear_preferences(self):
        self.service.start_product = Mock()
        for clear in (True, False):
            self.service.preferences['clear_bag_before_purchase'] = clear
            self.service.clear_tick(dict(stage='BAG', empty_bag=True, items=[]), 11)
        self.assertEqual(self.service.start_product.call_count, 2)
        self.service.dispatch.assert_not_called()

    def test_blank_or_unrecognized_cart_is_not_assumed_empty(self):
        self.service.start_product = Mock()
        self.service.clear_tick(dict(stage='LOADING', items=[]), 11)
        self.service.clear_tick(dict(stage='BAG', items=[]), 11)
        self.service.start_product.assert_not_called()
        self.service.dispatch.assert_not_called()
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bridge = Bridge(data=Path(self.temp.name))
        self.bridge.cancel.clear()
        self.service = self.bridge.service
        self.service.preferences.update(
            model='iphone-18-pro', colors=['冰川蓝色'], capacities=['256GB'],
            quantity=1, fulfillment='pickup', pickup_date_policy='earliest',
            clear_bag_before_purchase=False)
        self.service.phase = 'CLEAR_BAG'
        self.service.phase_since = 10
        self.service.browser = Mock()
        self.service.dispatch = Mock()
        self.snapshot = dict(stage='BAG', fingerprint='bag',
                             items=[dict(title='iPhone 18 Pro 256GB 冰川蓝色', quantity=1)])

    def test_exact_existing_target_is_reused_without_cart_write(self):
        self.service.clear_tick(self.snapshot, 11)
        self.assertEqual(self.service.phase, 'RUN')
        self.assertTrue(self.service.attempt.add_submitted)
        self.assertFalse(self.service.attempt.bag_verified)
        self.service.dispatch.assert_not_called()
        self.service.browser.navigate.assert_not_called()
        # Reuse is not checkout approval: the fresh BAG read must pass budget.
        snapshot = copy.deepcopy(self.snapshot)
        snapshot.update(total=100001, controls=[], bag_applecare='none')
        decision = self.service.attempt.decide(snapshot, 12)
        self.assertNotEqual(decision.kind, 'ready')
        self.assertNotEqual(decision.reason, 'checkout')

    def test_matching_cart_is_reused_even_with_legacy_clear_setting(self):
        self.service.preferences['clear_bag_before_purchase']=True
        self.snapshot['items'][0]['remove_ref']='remove'
        self.service.clear_tick(self.snapshot,11)
        self.assertTrue(self.service.attempt.add_submitted)
        self.assertEqual(self.service.phase,'RUN')
        self.service.dispatch.assert_not_called()
        self.service.browser.navigate.assert_not_called()

    def test_reused_cart_dispatches_only_checkout_after_fresh_validation(self):
        self.service.preferences['clear_bag_before_purchase'] = True
        self.service.active = True
        del self.service.dispatch  # Exercise the real dispatcher against a mock browser.
        self.service.clear_tick(self.snapshot, 11)
        fresh = {**copy.deepcopy(self.snapshot), 'total': 9999, 'bag_applecare': 'none',
                 'controls': [dict(ref='checkout', label='结账', role='button')]}
        self.service.browser.call.side_effect = [fresh, dict(result='dispatched')]
        with patch.object(core.time, 'monotonic', return_value=12):
            self.service.tick()
        writes = [c.args[2] for c in self.service.browser.call.call_args_list if len(c.args) == 3]
        self.assertEqual([(c['kind'], c['ref']) for c in writes], [('click', 'checkout')])
        self.service.browser.navigate.assert_not_called()
        self.service.browser.open.assert_not_called()

    def test_unconfirmed_coverage_waits_then_pauses_without_checkout(self):
        self.service.clear_tick(self.snapshot, 11)
        snapshot = {**self.snapshot, 'total': 9999, 'bag_applecare': 'unconfirmed',
                    'controls': [dict(ref='checkout', label='结账', role='button')]}
        self.assertEqual(self.service.attempt.decide(snapshot, 12).kind, 'wait')
        self.assertEqual(self.service.attempt.decide(snapshot, 17).kind, 'attention')
        self.assertFalse(self.service.attempt.bag_verified)

    def test_unwanted_coverage_does_not_checkout_without_a_verified_remove_control(self):
        self.service.clear_tick(self.snapshot, 11)
        snapshot = {**self.snapshot, 'total': 9999, 'bag_applecare': 'included',
                    'controls': [dict(ref='checkout', label='结账', role='button')]}
        self.assertEqual(self.service.attempt.decide(snapshot, 12).kind, 'attention')
        self.assertFalse(self.service.attempt.bag_verified)

    def test_requested_coverage_and_budget_are_checked_for_existing_cart(self):
        self.service.preferences['applecare'] = 'add'
        self.service.clear_tick(self.snapshot, 11)
        snapshot = {**self.snapshot, 'total': 9999, 'bag_applecare': 'included',
                    'controls': [dict(ref='checkout', label='结账', role='button')]}
        self.assertEqual(self.service.attempt.decide(snapshot, 12).reason, 'checkout')

    def test_entry_retry_reuses_owned_browser_instead_of_rotating_windows(self):
        self.service.preflight(fresh=True)
        self.service.browser.open.assert_called_once_with('https://www.apple.com.cn/shop/bag', foreground=False)
        self.service.browser.rotate.assert_not_called()
        self.assertIn('符合方案时直接结账', self.service.status['reason'])

    def test_cancelled_retry_never_opens_a_window(self):
        self.bridge.cancel.set()
        self.service.preflight(fresh=True)
        self.service.rotate_entry('https://www.apple.com.cn/shop/bag')
        self.service.browser.assert_not_called()
        self.assertEqual(self.service.browser.mock_calls, [])

    def test_clearing_removes_only_other_items_then_reuses_the_target(self):
        self.service.preferences['clear_bag_before_purchase']=True
        target=dict(title='iPhone 18 Pro 256GB 冰川蓝色',quantity=1,remove_ref='target-row')
        self.snapshot['items']=[target,dict(title='旧商品',quantity=1,remove_ref='old-row')]
        self.service.clear_tick(self.snapshot,11)
        self.assertEqual(self.service.dispatch.call_args.args[1].ref,'old-row')
        self.assertIsNone(self.service.attempt)
        # 移除还没生效（行数没变）：不复用，也不再点移除。
        self.service.dispatch.reset_mock()
        self.service.clear_tick(self.snapshot,12)
        self.service.dispatch.assert_not_called()
        self.assertIsNone(self.service.attempt)
        # 只剩目标那一行：直接复用去结账。
        self.service.clear_tick(dict(self.snapshot,items=[target]),13)
        self.assertEqual(self.service.phase,'RUN')
        self.assertTrue(self.service.attempt.add_submitted)
        self.service.dispatch.assert_not_called()

    def test_cleared_cart_still_starts_from_the_product_page(self):
        self.service.preferences['clear_bag_before_purchase']=True
        self.snapshot['items']=[dict(title='旧商品',quantity=1,remove_ref='old-row')]
        self.service.clear_tick(self.snapshot,11)
        self.assertEqual(self.service.dispatch.call_args.args[1].ref,'old-row')
        self.service.start_product=Mock()
        self.service.clear_tick(dict(stage='BAG',empty_bag=True,items=[]),13)
        self.service.start_product.assert_called_once()

    def test_target_with_trade_in_is_not_reused(self):
        self.snapshot['items'][0].update(trade_in=True,remove_ref='target-row')
        self.service.clear_tick(self.snapshot,11)
        self.assertIsNone(self.service.attempt)
        self.assertEqual(self.service.status['mode'],'attention')
        self.service.dispatch.assert_not_called()
        self.service.preferences['clear_bag_before_purchase']=True
        self.service.status['mode']='running'
        self.service.clear_tick(self.snapshot,12)
        self.assertIsNone(self.service.attempt)
        self.assertEqual(self.service.dispatch.call_args.args[1].ref,'target-row')

    def test_bag_step_refuses_checkout_when_an_item_carries_trade_in(self):
        self.service.clear_tick(self.snapshot,11)
        snapshot=copy.deepcopy(self.snapshot)
        snapshot['items'][0]['trade_in']=True
        snapshot.update(total=9999,bag_applecare='none',
                        controls=[dict(ref='checkout',label='结账',role='button')])
        decision=self.service.attempt.decide(snapshot,12)
        self.assertEqual(decision.kind,'attention')
        self.assertIn('折抵换购',decision.reason)

    def test_multiple_cart_rows_are_removed_serially_then_start_target(self):
        self.service.preferences['clear_bag_before_purchase']=True
        self.snapshot['items']=[dict(title='旧商品',quantity=1,remove_ref='one'),
                               dict(title='另一商品',quantity=1,remove_ref='two')]
        self.service.clear_tick(self.snapshot,11)
        self.assertEqual(self.service.dispatch.call_args.args[1].ref,'one')
        self.snapshot['items'].pop(0)
        self.service.clear_tick(self.snapshot,12)
        self.assertEqual(self.service.dispatch.call_args.args[1].ref,'two')
        self.assertEqual(self.service.dispatch.call_count,2)

    def test_verified_existing_cart_starts_timer_without_product_navigation(self):
        self.service.clear_tick(self.snapshot, 11)
        snapshot = copy.deepcopy(self.snapshot)
        snapshot.update(total=9999, bag_applecare='none',
                        controls=[dict(ref='checkout', label='结账', role='button')])
        decision = self.service.attempt.decide(snapshot, 12)
        self.assertEqual(decision.reason, 'checkout')
        self.assertTrue(self.service.attempt.bag_verified)
        self.assertEqual(self.service.attempt.started, 12)

    def test_different_variant_quantity_or_extra_item_is_not_reused(self):
        for title, quantity, extra in (
            ('iPhone 18 Pro Max 256GB 冰川蓝色', 1, False),
            ('iPhone 18 Pro 512GB 冰川蓝色', 1, False),
            ('iPhone 18 Pro 256GB 银色', 1, False),
            ('iPhone 18 Pro 256GB 冰川蓝色', 2, False),
            ('iPhone 18 Pro 256GB 冰川蓝色', 1, True),
        ):
            with self.subTest(title=title, quantity=quantity, extra=extra):
                snapshot = copy.deepcopy(self.snapshot)
                snapshot['items'][0].update(title=title, quantity=quantity)
                if extra:
                    snapshot['items'].append(dict(title='配件', quantity=1))
                self.service.clear_tick(snapshot, 11)
                self.assertIsNone(self.service.attempt)
                self.service.dispatch.assert_not_called()

    def test_pending_remove_never_becomes_a_fresh_attempt(self):
        self.service.pending_remove = (1, 10, False)
        self.service.clear_tick(self.snapshot, 11)
        self.assertIsNone(self.service.attempt)
        self.service.dispatch.assert_not_called()

    def test_trade_in_plan_cannot_skip_its_product_page_checks(self):
        self.service.preferences.update(purchase_plan='trade_in', trade_in='manual')
        self.service.clear_tick(self.snapshot, 11)
        self.assertIsNone(self.service.attempt)
        self.service.dispatch.assert_not_called()

    def test_pending_order_still_blocks_reuse(self):
        self.bridge.order_guard.begin()
        self.service.clear_tick(self.snapshot, 11)
        self.assertIsNone(self.service.attempt)

    def test_loading_cart_is_not_cleared_or_reused(self):
        self.snapshot['bag_busy'] = True
        self.service.clear_tick(self.snapshot, 11)
        self.assertIsNone(self.service.attempt)
        self.service.dispatch.assert_not_called()

    def test_missing_page_and_expired_session_have_distinct_notifications(self):
        # 能自动重开时，找不到页面先不通知；重开次数用完或不能重开时才通知本人。
        self.service.observe_diagnostics(dict(stage='NOT_FOUND', diagnostics={}))
        self.assertIsNone(self.bridge.notice)
        self.service.browser = Mock(spec=['call', 'navigate', 'open'])
        for stage, kind in [('NOT_FOUND', 'not_found'), ('SESSION_EXPIRED', 'expired')]:
            self.service.observe_diagnostics(dict(stage=stage, diagnostics={}))
            self.assertEqual(self.bridge.notice['kind'], kind)


if __name__ == '__main__':
    unittest.main()

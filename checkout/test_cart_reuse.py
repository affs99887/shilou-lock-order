import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

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

    def test_explicit_clear_takes_priority_over_same_item_reuse(self):
        self.service.preferences['clear_bag_before_purchase']=True
        self.snapshot['items'][0]['remove_ref']='remove'
        self.service.clear_tick(self.snapshot,11)
        self.assertIsNone(self.service.attempt)
        self.assertEqual(self.service.phase,'CLEAR_BAG')
        self.assertEqual(self.service.dispatch.call_args.args[1].kind,'remove')
        self.service.dispatch.reset_mock()
        self.service.clear_tick(self.snapshot,12)
        self.service.dispatch.assert_not_called()
        self.service.start_product=Mock()
        self.service.clear_tick(dict(stage='BAG',empty_bag=True,items=[]),13)
        self.service.start_product.assert_called_once()

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
        for stage, kind in [('NOT_FOUND', 'not_found'), ('SESSION_EXPIRED', 'expired')]:
            self.service.observe_diagnostics(dict(stage=stage, diagnostics={}))
            self.assertEqual(self.bridge.notice['kind'], kind)


if __name__ == '__main__':
    unittest.main()

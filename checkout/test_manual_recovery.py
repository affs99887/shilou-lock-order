import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from worker import Bridge, core
from edge_browser import EdgeBrowser


class MissingPageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.bridge = Bridge(data=Path(self.temp.name))
        self.service = self.bridge.service
        self.bridge.cancel.clear()
        self.bridge.reserved = True
        self.service.phase = 'CLEAR_BAG'
        self.service.phase_since = 99
        self.service.active = True
        self.service.run_preferences = copy.deepcopy(self.bridge.plan)
        self.service.browser = Mock()

    def test_original_entry_order_checks_bag_before_opening_selected_product(self):
        self.service.preflight()
        self.service.browser.open.assert_called_once_with('https://www.apple.com.cn/shop/bag')
        self.service.browser.navigate.assert_not_called()
        self.service.clear_tick({'stage': 'LOADING'}, self.service.phase_since + 1)
        self.service.browser.navigate.assert_not_called()
        self.service.clear_tick({'stage': 'BAG', 'items': [], 'empty_bag': True}, self.service.phase_since + 2)
        model = self.service.execution_preferences['model']
        self.service.browser.navigate.assert_called_once_with(core.CATALOG[model]['url'], foreground=True)
        self.assertEqual(self.service.phase, 'RUN')

    def test_remove_then_404_reopens_without_replaying_delete_and_resumes_after_empty_bag(self):
        pending = (1, 99, False)
        self.service.pending_remove = pending
        self.service.observe_diagnostics({'stage': 'NOT_FOUND'})
        self.assertIsNone(self.bridge.notice)
        self.assertTrue(self.service.recover_missing_page({'stage': 'NOT_FOUND'}, 100))
        self.service.dispatch = Mock()
        self.service.start_product = Mock()
        with patch('worker.time.monotonic', return_value=100.5):
            self.service.tick()
        self.service.browser.reopen_failed_entry.assert_not_called()
        with patch('worker.time.monotonic', return_value=102):
            self.service.tick()
        self.service.browser.reopen_failed_entry.assert_called_once_with('https://www.apple.com.cn/shop/bag')
        self.assertEqual(self.service.pending_remove, pending)
        self.service.browser.call.return_value = {'stage': 'BAG', 'empty_bag': True, 'items': []}
        with patch('worker.time.monotonic', return_value=103):
            self.service.tick()
        self.service.start_product.assert_called_once()
        self.service.dispatch.assert_not_called()

    def test_two_retries_then_terminal_failure_instead_of_infinite_reload(self):
        for now in (100, 105):
            self.assertTrue(self.service.recover_missing_page({'stage': 'NOT_FOUND'}, now))
            with patch('worker.time.monotonic', return_value=now + 4):
                self.service.tick()
        self.assertTrue(self.service.recover_missing_page({'stage': 'NOT_FOUND'}, 110))
        self.assertEqual(self.service.browser.reopen_failed_entry.call_count, 2)
        self.assertTrue(self.bridge.retryable_end)
        self.assertEqual(self.service.status['mode'], 'attention')

    def test_stop_cancels_scheduled_reopen(self):
        self.service.recover_missing_page({'stage': 'NOT_FOUND'}, 100)
        self.bridge.request({'op': 'stop'})
        with patch('worker.time.monotonic', return_value=110):
            self.service.tick()
        self.service.browser.reopen_failed_entry.assert_not_called()

    def test_submission_or_access_denial_never_restarts(self):
        self.bridge.order_guard.begin()
        self.assertFalse(self.service.recover_missing_page({'stage': 'NOT_FOUND'}, 100))
        self.service.browser.reopen_failed_entry.assert_not_called()

    def test_windows_recovery_uses_the_same_retry_and_cancellation_guard(self):
        with patch('worker.sys.platform', 'win32'):
            self.assertTrue(self.service.recover_missing_page({'stage': 'NOT_FOUND'}, 100))
        self.service.access_blocked = True
        self.assertFalse(self.service.can_recover_missing())

    def test_browser_without_recovery_support_does_not_schedule_it(self):
        self.service.browser = object()
        self.assertFalse(self.service.recover_missing_page({'stage': 'NOT_FOUND'}, 100))

    def test_post_add_reopen_allows_one_more_checkout_click_and_keeps_the_chosen_store(self):
        # 自动选购（固定门店）和手动开始（换过同城门店）都一样：只放开结账，加购不重复，换好的门店保留。
        from checkout_engine import DEFAULTS, validate_preferences, variants
        from checkout_stores import STORES
        for choice in ('fixed', 'same_city'):
            with self.subTest(choice=choice):
                p = validate_preferences(dict(DEFAULTS, fulfillment='pickup', financing='full', payment='支付宝',
                                              store_id='R683', pickup_date_policy='earliest'))
                p['store_choice'] = choice
                attempt = core.Attempt(p, variants(p)[0], 0)
                if choice == 'same_city':
                    attempt.use_store(next(s for s in STORES if s['id'] == 'R390'))
                attempt.add_submitted = attempt.bag_verified = True
                attempt.sent.update(add=1, checkout=1)
                attempt.action_times.update(add=50, checkout=60)
                self.service.phase = 'RUN'
                self.service.attempt = attempt
                self.service.missing_retries = 0
                self.assertTrue(self.service.recover_missing_page({'stage': 'NOT_FOUND'}, 100))
                with patch('worker.time.monotonic', return_value=102):
                    self.service.tick()
                self.assertIs(self.service.attempt, attempt)
                self.assertEqual(self.service.phase, 'RUN')
                self.assertNotIn('checkout', attempt.sent, '回到购物袋后能再点一次结账')
                self.assertEqual(attempt.sent['add'], 1, '加购不重复')
                self.assertEqual(attempt.target['store_id'], 'R390' if choice == 'same_city' else 'R683')

    def test_post_add_recovery_keeps_existing_attempt_and_never_readds(self):
        self.service.phase = 'RUN'
        attempt = Mock(add_submitted=True)
        self.service.attempt = attempt
        self.service.recover_missing_page({'stage': 'NOT_FOUND'}, 100)
        with patch('worker.time.monotonic', return_value=102):
            self.service.tick()
        self.assertIs(self.service.attempt, attempt)
        self.assertEqual(self.service.phase, 'RUN')


class OwnedWindowTests(unittest.TestCase):
    def test_reopen_closes_only_bound_failed_tab(self):
        browser = core.Safari()
        browser.window = 123
        browser.windows = [123, 456]
        browser.bindings = {123: (1, ['https://www.apple.com.cn/shop/404']), 456: (1, ['about:blank'])}
        browser.bound = Mock(side_effect=lambda code: 'BOUND:' + code)
        browser.script = Mock(return_value='CLOSED')
        browser.open = Mock()
        self.assertTrue(browser.reopen_failed_entry('https://www.apple.com.cn/shop/bag'))
        script = browser.script.call_args.args[0]
        self.assertIn('close checkoutTab', script)
        self.assertNotIn('close windows', script)
        self.assertIn(456, browser.windows)
        self.assertNotIn(123, browser.windows)
        browser.open.assert_called_once()

    def test_page_that_already_recovered_is_not_closed(self):
        browser = core.Safari()
        browser.window = 123
        browser.bound = Mock(side_effect=lambda s: s)
        browser.script = Mock(return_value='RECOVERED')
        browser.open = Mock()
        self.assertFalse(browser.reopen_failed_entry('https://www.apple.com.cn/shop/bag'))
        browser.open.assert_not_called()
        self.assertEqual(browser.window, 123)


class WindowsRecoveryTests(unittest.TestCase):
    def browser(self, cls=EdgeBrowser):
        browser = cls(Mock(), '')
        browser.window = 'failed'
        page, personal = Mock(), Mock()
        browser.page = Mock(return_value=page)
        browser.pages.update(failed=page, personal=personal)
        browser.windows = ['failed', 'personal']
        browser.call = Mock(return_value={'stage': 'NOT_FOUND', 'http_status': 404})
        browser.navigate = Mock()
        def new_page():
            browser.window = 'replacement'
            browser.pages['replacement'] = Mock()
        browser.new_page = Mock(side_effect=new_page)
        return browser, page, personal

    def test_external_edge_replaces_only_failed_owned_page(self):
        browser, failed, personal = self.browser()
        self.assertTrue(browser.reopen_failed_entry('https://www.apple.com.cn/shop/bag'))
        browser.navigate.assert_called_once_with('https://www.apple.com.cn/shop/bag', foreground=True)
        failed.close.assert_called_once()
        personal.close.assert_not_called()
        self.assertNotIn('failed', browser.pages)
        self.assertIn('personal', browser.pages)
        self.assertEqual(browser.window, 'replacement')

    def test_recovered_page_and_access_denial_are_not_reopened(self):
        for stage in ('BAG', 'PRODUCT', 'REVIEW', 'BLOCKED', 'CAPTCHA', 'LOGIN'):
            browser, failed, _ = self.browser()
            browser.call.return_value = {'stage': stage}
            self.assertFalse(browser.reopen_failed_entry('https://www.apple.com.cn/shop/bag'))
            browser.navigate.assert_not_called()
            browser.new_page.assert_not_called()
            failed.close.assert_not_called()

    def test_failed_replacement_keeps_old_page_for_inspection(self):
        browser, failed, _ = self.browser()
        browser.navigate.side_effect = RuntimeError('navigation failed')
        with self.assertRaises(RuntimeError):
            browser.reopen_failed_entry('https://www.apple.com.cn/shop/bag')
        failed.close.assert_not_called()
        self.assertIn('failed', browser.pages)


if __name__ == '__main__':
    unittest.main()

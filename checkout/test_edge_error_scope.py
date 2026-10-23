"""Regression for auxiliary-error scope; all traffic and pages are synthetic."""
import json
import unittest
from unittest.mock import Mock, patch

from edge_browser import EdgeBrowser
from checkout_service import Service
from checkout_engine import Attempt, DEFAULTS, variants


class EdgeErrorScopeTests(unittest.TestCase):
    def setUp(self):
        self.browser = EdgeBrowser(Mock(), "")
        self.browser.window = "owned"
        self.page = Mock(url="https://www.apple.com.cn/shop/buy-iphone/iphone-18-pro")
        self.page.is_closed.return_value = False
        self.browser.pages["owned"] = self.page
        self.browser.channels["owned"] = Mock()
        self.browser.document_generations["owned"] = 0
        self.browser.last_status["owned"] = (200, "")
        self.browser.product_stability["owned"] = ("stable", 0)
        self.browser.evaluate = Mock(side_effect=lambda expression:
            "complete" if expression == "document.readyState" else
            {"stage": "PRODUCT", "fingerprint": "stable"})

    def request(self, path, method="GET", navigation=False):
        request = Mock(url="https://www.apple.com.cn" + path,
                       method=method, resource_type="fetch", frame=self.page.main_frame)
        request.is_navigation_request.return_value = navigation
        self.browser.request_started("owned", self.page, request)
        return request

    def respond(self, request, status, headers=None, finish=True):
        response = Mock(request=request, status=status, headers=headers or {},
                        frame=self.page.main_frame)
        self.browser.response("owned", self.page, response)
        if finish:
            self.browser.request_finished("owned", request)

    def settled(self):
        self.browser.product_stability["owned"] = ("stable", 0)
        return self.browser.call({}, {})

    def test_auxiliary_failure_preserves_dom_and_unknown_diagnostic(self):
        for path in ("/shop/fulfillment-messages", "/shop/retail/pickup-message",
                     "/shop/favoritesx/fetch", "/shop/api/product-gallery"):
            with self.subTest(path=path):
                self.respond(self.request(path + "?session=secret"), 541)
                result = self.settled()
                self.assertEqual(result["stage"], "PRODUCT")
                self.assertNotIn("http_status", result)
                self.assertIsNone(self.browser.access_failure())
                self.assertNotIn("secret", json.dumps(result))

    def test_success_clears_only_same_read_not_other_errors_or_writes(self):
        path = "/shop/fulfillment-messages?parts=one"
        self.respond(self.request(path), 541)
        self.respond(self.request("/shop/favoritesx/fetch"), 403)
        self.respond(self.request(path), 200)
        self.assertFalse(self.browser.auxiliary_diagnostics()["edge_availability_unknown"])
        self.assertEqual(self.browser.auxiliary_diagnostics()["edge_auxiliary_error_count"], 1)
        self.respond(self.request("/shop/bag/add", "POST"), 541)
        self.respond(self.request("/shop/favoritesx/fetch"), 200)
        self.assertEqual(self.browser.access_failure()["stage"], "BLOCKED")

    def test_older_failed_request_cannot_overwrite_newer_success(self):
        older = self.request("/shop/fulfillment-messages")
        newer = self.request("/shop/fulfillment-messages")
        self.respond(newer, 200)
        self.respond(older, 541)
        self.assertEqual(self.browser.auxiliary_diagnostics()["edge_auxiliary_error_count"], 0)

    def test_old_document_response_cannot_poison_new_page(self):
        for path in ("/shop/fulfillment-messages", "/shop/bag/add", "/shop/updateSummary"):
            old = self.request(path, "POST")
            document = self.request("/shop/buy-iphone/iphone-18-pro", navigation=True)
            self.respond(document, 200)
            self.respond(old, 541)
            self.assertIsNone(self.browser.access_failure())
            self.assertEqual(self.browser.auxiliary_diagnostics()["edge_auxiliary_error_count"], 0)
        self.assertFalse(self.browser.request_generations)

    def test_all_writes_unknown_reads_and_configuration_still_block(self):
        for path, method in (("/shop/fulfillment-messages", "POST"),
                             ("/shop/bag/add", "POST"), ("/shop/checkout", "POST"),
                             ("/shop/unknown", "GET"), ("/shop/updateSummary", "GET")):
            self.respond(self.request(path, method), 429)
            result = self.browser.call({}, {}, {"kind": "click", "nonce": "must-not-dispatch"})
            self.assertEqual(result["stage"], "BLOCKED")
            self.assertEqual(result["result"], "refused")
            self.browser.evaluate.assert_not_called()

    def test_explicit_retry_after_blocks_actions_without_global_access_flag(self):
        with patch("edge_browser.time.monotonic", return_value=100):
            self.respond(self.request("/shop/fulfillment-messages"), 541, {"retry-after": "90"})
            result = self.browser.call({}, {}, {"kind": "click", "expected": "stable"})
            self.assertEqual(result["stage"], "LOADING")
            self.assertEqual(result["read_wait_seconds"], 90)
            self.assertEqual(result["result"], "stale")
            self.assertIsNone(self.browser.access_failure())
            self.browser.evaluate.assert_not_called()
            # New navigation does not cancel the explicit server waiting time.
            self.respond(self.request("/shop/buy-iphone/iphone-18-pro", navigation=True), 200)
            self.assertIsNotNone(self.browser.auxiliary_cooldown())
        with patch("edge_browser.time.monotonic", return_value=191):
            self.assertEqual(self.settled()["stage"], "PRODUCT")

    def test_read_wait_preserves_attempt_and_does_not_dispatch(self):
        service = Service(browser=self.browser, preferences=dict(DEFAULTS), persist=False)
        service.phase = "RUN"
        service.attempt = Attempt(dict(DEFAULTS), variants(dict(DEFAULTS))[0], 0)
        attempt = service.attempt
        self.browser.call = Mock(return_value={"stage": "LOADING", "read_wait_seconds": 90})
        service.dispatch = Mock()
        service.tick()
        self.assertIs(service.attempt, attempt)
        self.assertEqual(service.phase, "RUN")
        self.assertIn("等待", service.status["reason"])
        service.dispatch.assert_not_called()
        self.assertEqual(service.breaker.until, 0)

    def test_auxiliary_response_during_click_does_not_erase_write_receipt(self):
        def evaluate(expression):
            if expression == "document.readyState":
                return "complete"
            self.respond(self.request("/shop/fulfillment-messages"), 541, {"retry-after": "90"})
            return {"stage": "PRODUCT", "result": "dispatched", "fingerprint": "stable"}
        self.browser.evaluate.side_effect = evaluate
        result = self.browser.call({}, {}, {"kind": "click", "expected": "stable", "nonce": "one"})
        self.assertEqual(result["result"], "dispatched")
        self.assertEqual(self.browser.call({}, {})["stage"], "LOADING")

    def test_attach_screen_on_product_url_can_advance_after_readback(self):
        self.browser.product_stability.clear()
        self.browser.evaluate.side_effect = lambda expression: (
            "complete" if expression == "document.readyState" else
            {"stage": "ATTACH", "fingerprint": "attach", "result": "dispatched"})
        with patch("edge_browser.time.monotonic", return_value=100):
            self.assertEqual(self.browser.call({}, {})["stage"], "LOADING")
        with patch("edge_browser.time.monotonic", return_value=100.4):
            self.assertEqual(self.browser.call({}, {})["stage"], "ATTACH")
            result = self.browser.call({}, {}, {"kind":"click", "expected":"attach", "nonce":"view-bag"})
            self.assertEqual(result["result"], "dispatched")
        self.assertNotIn("owned", self.browser.product_stability)


if __name__ == "__main__":
    unittest.main()

"""Shared-hint receipts use only temporary files and never run browser tasks."""
import json
import queue
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from worker import Bridge
from offer_receipt import OfferHistory, MAX_EVENTS, sanitized
from test_worker import community_hit, hit


class OfferReceiptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = Path(self.temp.name)
        self.bridge = Bridge(data=self.data)
        clock = patch('worker.time.time', return_value=100)
        clock.start()
        self.addCleanup(clock.stop)

    def offer(self, hits=None):
        return self.bridge.request({'op': 'offer', 'hits': [community_hit()] if hits is None else hits})

    def assert_not_queued(self, outcome):
        receipt = self.offer()['offer']
        self.assertEqual(receipt, {'outcome': outcome, 'selectedIndex': None})
        self.assertTrue(self.bridge.tasks.empty())
        self.assertEqual(OfferHistory(self.data).events[-1]['outcome'], outcome)

    def test_disabled_and_stopping_report_reason_without_starting(self):
        self.assert_not_queued('not_armed')
        self.bridge.request({'op': 'stop'})
        self.assert_not_queued('stopping')

    def test_existing_task_keeps_ownership_and_reports_pause_review_or_busy(self):
        self.bridge.reserved = True
        for mode, outcome in [('attention', 'paused'), ('manual_region', 'paused'),
                              ('ready', 'review'), ('running', 'busy')]:
            with self.subTest(mode=mode):
                self.bridge.service.update(mode=mode)
                self.assert_not_queued(outcome)
                self.assertTrue(self.bridge.reserved)
                self.assertFalse(self.bridge.armed)
        self.bridge.error = 'private internal detail'
        self.assert_not_queued('paused')

    def test_order_access_profile_and_activation_guards_have_explicit_receipts(self):
        self.bridge.armed = True
        for owner, field, outcome in [(self.bridge, 'activation_pending', 'activating'),
                                     (self.bridge, 'profile_required', 'profile_required')]:
            with self.subTest(outcome=outcome):
                setattr(owner, field, True)
                self.assert_not_queued(outcome)
                setattr(owner, field, False)
        self.bridge.reserved = True
        self.bridge.service.access_blocked = True
        self.assert_not_queued('blocked')
        self.bridge.reserved = False
        self.bridge.service.access_blocked = False
        self.bridge.order_guard.begin()
        self.assert_not_queued('order_review')

    def test_explicit_rearm_is_not_vetoed_by_an_old_access_diagnostic(self):
        self.bridge.service.access_blocked = True
        self.bridge.armed = True
        self.assertEqual(self.offer()['offer']['outcome'], 'queued')
        self.assertEqual(self.bridge.tasks.qsize(), 1)

    def test_only_valid_selected_target_is_accepted_and_matches_rust_fixture(self):
        self.bridge.armed = True
        bad = community_hit()
        bad['product']['family'] = 'unsupported'
        response = self.offer([bad, community_hit(), community_hit()])
        fixture = json.loads((Path(__file__).parent / 'fixtures/offer-receipt.json').read_text())
        self.assertEqual({key: response[key] for key in fixture}, fixture)
        self.assertEqual(self.bridge.tasks.qsize(), 1)
        self.assertEqual(self.bridge.tasks.get_nowait()[2], community_hit())
        self.assertNotIn('offer', self.bridge.request({'op': 'status'}))
        self.assertEqual(self.offer()['offer']['outcome'], 'busy')
        self.assertTrue(self.bridge.tasks.empty())

    def test_invalid_old_and_empty_hints_never_start_checkout(self):
        self.bridge.armed = True
        old = community_hit()
        old['reportedAtMs'] = -100000
        malformed = community_hit()
        malformed['target']['storeNumber'] = []
        for hints in ([old], [None], [malformed], 'invalid'):
            self.assertEqual(self.offer(hints)['offer']['outcome'], 'ineligible')
            self.assertFalse(self.bridge.reserved)
        self.assertEqual(self.offer([])['offer']['outcome'], 'empty')
        self.assertTrue(self.bridge.tasks.empty())

    def test_cooldown_keeps_waiting_without_reserving(self):
        self.bridge.armed = True
        target = community_hit()['target']
        self.bridge.avoid_until[(target['partNumber'], target['storeNumber'])] = float('inf')
        self.assert_not_queued('cooldown')
        self.assertFalse(self.bridge.reserved)

    def test_full_queue_does_not_leave_a_phantom_reservation(self):
        self.bridge.armed = True
        self.bridge.tasks = queue.Queue(maxsize=1)
        self.bridge.tasks.put_nowait(('existing',))
        self.assertEqual(self.offer()['offer']['outcome'], 'queue_full')
        self.assertFalse(self.bridge.reserved)
        self.assertEqual(self.bridge.tasks.get_nowait(), ('existing',))

    def test_diagnostic_write_failure_does_not_undo_or_repeat_acceptance(self):
        self.bridge.armed = True
        with patch('offer_receipt.atomic_json', side_effect=OSError('disk full')):
            self.assertEqual(self.offer()['offer']['outcome'], 'queued')
            self.assertEqual(self.offer()['offer']['outcome'], 'busy')
        self.assertEqual(self.bridge.tasks.qsize(), 1)

    def test_shared_history_survives_restart_and_contains_no_payload_or_profile(self):
        self.bridge.service.update(reason='private@example.invalid')
        private = community_hit()
        private['secret'] = 'sensitive-token'
        private['target']['storeTitle'] = 'private-shop'
        self.offer([private])
        stored = (self.data / 'checkout-offers.json').read_text()
        for secret in ('private@example.invalid', 'sensitive-token', 'private-shop', 'TESTCH/A'):
            self.assertNotIn(secret, stored)
        again = Bridge(data=self.data)
        self.assertEqual(again.offer_history.events, self.bridge.offer_history.events)
        self.assertFalse(again.armed)
        self.assertEqual(set(again.offer_history.events[0]),
                         {'atMs', 'outcome', 'candidates', 'selectedIndex', 'armed', 'busy', 'stopping'})

    def test_local_inventory_does_not_fill_shared_hint_history(self):
        self.offer([hit()])
        self.assertFalse(self.bridge.offer_history.path.exists())

    def test_history_is_bounded_and_malformed_records_are_ignored(self):
        history = self.bridge.offer_history
        with patch('offer_receipt.atomic_json'):
            for _ in range(MAX_EVENTS + 5):
                history.record('not_armed', 1, None, self.bridge.status())
        self.assertEqual(len(history.events), MAX_EVENTS)
        good = history.events[-1]
        for broken in (None, [], {'outcome': []}, {**good, 'atMs': True},
                       {**good, 'outcome': 'queued', 'selectedIndex': True},
                       {**good, 'busy': 'false'}):
            self.assertIsNone(sanitized(broken))
        history.path.write_text(json.dumps({'events': [None, {'outcome': []}, good]}))
        self.assertEqual(OfferHistory(self.data).events, [good])
        history.path.write_text('{broken')
        self.assertEqual(OfferHistory(self.data).events, [])


if __name__ == '__main__':
    unittest.main()

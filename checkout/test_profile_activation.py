"""First-use activation regression, with no browser or real Keychain access."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from worker import Bridge
from checkout_profile import validate_profile
from test_worker import FakeCheckout

PROFILE = dict(family_name='示', given_name='例', phone='13800000000', email='demo@example.invalid',
               province='湖北省', city='武汉市', district='江汉区', address='仅供离线测试',
               postal_code='430000', id_last4='0616')

class ProfileActivationTests(unittest.TestCase):
    def test_mac_explicit_read_is_supported_but_startup_does_not_read_profile(self):
        with tempfile.TemporaryDirectory() as d, patch('worker.sys.platform', 'darwin'), \
                patch('worker.editable_profile', return_value=PROFILE) as read:
            bridge = Bridge(FakeCheckout, data=Path(d))
            read.assert_not_called()
            self.assertEqual(bridge.request({'op': 'read_profile'})['id_last4'], '0616')
            self.assertFalse(bridge.status()['armed'])
            read.assert_called_once()

    def test_missing_profile_then_save_then_arm_and_stop_reports_actual_authority(self):
        saved = {}
        def load(**kwargs):
            if not saved:
                raise RuntimeError('请先填写并保存你自己的收货资料')
            return validate_profile(saved)
        def save(profile):
            saved.update(validate_profile(profile))
        def settled(bridge):
            deadline = time.monotonic() + 3
            while bridge.status()['busy'] and time.monotonic() < deadline:
                time.sleep(.01)
            self.assertFalse(bridge.status()['busy'])
            return bridge.status()

        with tempfile.TemporaryDirectory() as d:
            bridge = Bridge(FakeCheckout, data=Path(d))
            bridge.request({'op': 'configure', 'plan': dict(bridge.plan, auto_submit_order=True)})
            self.assertFalse(bridge.status()['submissionEnabled'])
            with patch('worker.core.normalized_profile', side_effect=load), \
                    patch('worker.store_profile', side_effect=save), patch.object(bridge, 'acquire'), \
                    patch.object(bridge, 'release'):
                thread = threading.Thread(target=bridge.run)
                thread.start()
                try:
                    queued = bridge.request({'op': 'arm', 'confirmSubmission': True})
                    self.assertTrue(queued['activationPending'])
                    state = settled(bridge)
                    self.assertFalse(state['armed'])
                    self.assertFalse(state['submissionEnabled'])
                    self.assertTrue(state['profileRequired'])
                    self.assertIn('联系与收货资料', state['error'])
                    bridge.request({'op': 'save_profile', 'profile': PROFILE})
                    state = settled(bridge)
                    self.assertFalse(state['profileRequired'])
                    self.assertEqual(saved['id_last4'], '0616')
                    self.assertFalse(state['submissionEnabled'])
                    bridge.request({'op': 'arm', 'confirmSubmission': True})
                    state = settled(bridge)
                    self.assertTrue(state['armed'])
                    self.assertTrue(state['submissionEnabled'])
                    self.assertFalse(state['activationPending'])
                    state = bridge.request({'op': 'stop'})
                    self.assertFalse(state['armed'])
                    self.assertFalse(state['submissionEnabled'])
                finally:
                    bridge.closed = True
                    thread.join(3)
                    self.assertFalse(thread.is_alive())

    def test_incomplete_profile_error_names_the_field_in_chinese(self):
        with self.assertRaisesRegex(ValueError, '请填写手机号'):
            validate_profile(dict(PROFILE, phone=''))

if __name__ == '__main__':
    unittest.main()

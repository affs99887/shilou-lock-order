"""Exercise the shipped worker with a temporary, uniquely named Keychain item.

No real contact profile, browser, stock query or order is used.
Run with consumer/.build-venv/bin/python scripts/mac-profile-smoke.py HELPER.
"""
import json
import marshal
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
from unittest.mock import patch
import uuid

from PyInstaller.archive.readers import CArchiveReader

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'checkout'))
namespace = {'__name__': 'isolated_packaged_profile_qa', '__file__': str(ROOT / 'checkout/worker.py')}
exec(marshal.loads(CArchiveReader(sys.argv[1]).extract('worker')), namespace)
import checkout_profile
import safari_checkout_autofill

class Service:
    def __init__(self, owner, plan):
        self.lock = threading.RLock()
        self.status = {'mode': 'stopped', 'stage': 'IDLE'}
        self.preferences = plan
    def update(self, **fields): self.status.update(fields)
    def command(self, command): self.status.update(mode='stopped', stage='IDLE')
    def timing(self): return {'total_seconds': 0}

suffix = uuid.uuid4().hex
service = 'local.apple-pickup-watcher.profile.qa.' + suffix
account = 'qa-' + suffix
profile = dict(family_name='示', given_name='例', phone='13800000000', email='demo@example.invalid',
               province='湖北省', city='武汉市', district='江汉区', address='仅供本机隔离测试',
               postal_code='430000', id_last4='0616')

def settled(bridge):
    deadline = time.monotonic() + 75
    while bridge.status()['busy'] and time.monotonic() < deadline:
        time.sleep(.05)
    result = bridge.status()
    assert not result['busy'], 'Profile operation did not finish'
    return result

try:
    with tempfile.TemporaryDirectory(prefix='mac-profile-qa-') as directory, \
            patch.object(safari_checkout_autofill, 'KEYCHAIN_SERVICE', service), \
            patch.object(safari_checkout_autofill, 'KEYCHAIN_ACCOUNT', account), \
            patch.object(checkout_profile, 'KEYCHAIN_SERVICE', service), \
            patch.object(checkout_profile, 'KEYCHAIN_ACCOUNT', account):
        bridge = namespace['Bridge'](Service, data=Path(directory))
        bridge.request({'op': 'configure', 'plan': dict(bridge.plan, auto_submit_order=True)})
        assert not bridge.status()['submissionEnabled']
        with patch.object(bridge, 'acquire'), patch.object(bridge, 'release'):
            thread = threading.Thread(target=bridge.run)
            thread.start()
            try:
                bridge.request({'op': 'arm', 'confirmSubmission': True})
                state = settled(bridge)
                assert state['profileRequired'] and not state['armed'] and not state['submissionEnabled']
                bridge.request({'op': 'save_profile', 'profile': profile})
                state = settled(bridge)
                assert not state['error'], state['error']
                restored = bridge.request({'op': 'read_profile'})
                assert restored['id_last4'] == '0616' and restored['phone'] == '13800000000'
                bridge.request({'op': 'arm', 'confirmSubmission': True})
                state = settled(bridge)
                assert not state['error'], state['error']
                assert state['armed'] and state['submissionEnabled']
                stopped = bridge.request({'op': 'stop'})
                assert not stopped['armed'] and not stopped['submissionEnabled']
            finally:
                bridge.closed = True
                thread.join(5)
                assert not thread.is_alive()
finally:
    removed = subprocess.run(['security', 'delete-generic-password', '-s', service, '-a', account], capture_output=True)
    assert removed.returncode in (0, 44), 'Temporary Keychain item cleanup failed'

print(json.dumps({'packaged_worker': True, 'fresh_mac_missing_profile_rejected': True,
                  'native_keychain_save_and_readback': True, 'leading_zero_preserved': True,
                  'activation_after_save': True, 'stop_revokes_submission_permission': True,
                  'temporary_keychain_item_removed': True, 'personal_profile_reads': 0,
                  'browser_actions': 0, 'orders': 0}))

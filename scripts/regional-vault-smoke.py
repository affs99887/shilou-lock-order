"""Six fictitious profiles in uniquely named QA Keychain entries, then cleanup.

Never reads or modifies any user's saved contact, password or Apple session.
"""
from pathlib import Path
import subprocess
import sys
from unittest.mock import patch
import uuid

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'checkout/vendor'))
import checkout_profile as profile

identity = uuid.uuid4().hex
service = 'local.apple-pickup-watcher.regional.qa.' + identity
account = 'qa-' + identity
cases = {'zh_HK': ('51234567',''), 'zh_TW': ('0912345678','100'),
         'ja_JP': ('09012345678','100-0001'), 'en_SG': ('81234567','238839'),
         'en_AU': ('0412345678','2000'), 'en_MY': ('0123456789','55188')}
completed = []
try:
    with patch.object(profile, 'KEYCHAIN_SERVICE', service), patch.object(profile, 'KEYCHAIN_ACCOUNT', account):
        for locale, (phone, postal) in cases.items():
            fake = dict(family_name='Example',given_name='Test',phone=phone,email='test@example.invalid',
                        province='Offline example',city='Offline example',district='Offline example',
                        address='Offline example only',postal_code=postal,family_name_kana='テスト',given_name_kana='デモ')
            profile.store_profile(fake, locale)
            actual = profile.editable_profile(locale)
            assert actual['phone'] == phone and actual['postal_code'] == postal
            assert actual['family_name_kana'] == 'テスト'
            assert actual['id_last4'] == ''
            completed.append(locale)
        assert len(completed) == 6
finally:
    for locale in cases:
        result = subprocess.run(['security','delete-generic-password','-s',service+'.region.'+locale,'-a',account],capture_output=True)
        assert result.returncode in (0,44), 'QA Keychain cleanup not confirmed'
print({'regional_keychain_readbacks': len(completed), 'temporary_entries_removed': True,
       'personal_profile_reads': 0, 'apple_requests': 0, 'orders': 0})

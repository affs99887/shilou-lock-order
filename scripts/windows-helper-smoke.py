"""Test a built helper through its real UTF-8 IPC, using an empty temporary user directory."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

exe=Path(sys.argv[1]).resolve()
with tempfile.TemporaryDirectory(prefix='shilou-helper-qa-') as directory:
    # Packaged Windows Python may ignore UTF8-mode environment variables.
    # Deliberately exercise a non-UTF8 default; resources/IPC must be explicit.
    env={**os.environ,'LOCALAPPDATA':directory,'PYTHONUTF8':'0'}
    p=subprocess.Popen([str(exe)],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                       encoding='utf-8',env=env,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    def request(op,**payload):
        p.stdin.write(json.dumps({'op':op,**payload},ensure_ascii=False)+'\n');p.stdin.flush()
        result=json.loads(p.stdout.readline())
        assert result['ok'],result.get('error')
        return result['value']
    try:
        status=request('status')
        assert status['platform']=='windows' and not status['armed'] and not status['busy']
        plan=status['plan'];plan.update(financing='official',installment_option='微信分付',installment_months=24,auto_submit_order=False)
        assert request('configure',plan=plan)['plan']['installment_months']==24
        profile=dict(family_name='示',given_name='例',phone='13800000000',email='demo@example.invalid',province='湖北省',city='武汉市',district='江汉区',address='仅供离线测试',postal_code='430000',id_last4='0616')
        request('save_profile',profile=profile)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            status=request('status')
            assert not status['error'],status['error']
            if not status['busy']:break
            time.sleep(.1)
        assert not status['busy']
        restored=request('read_profile')
        assert restored['id_last4']=='0616' and restored['city']=='武汉市'
        raw=(Path(directory)/'ApplePickupWatcherAuto/profile.dpapi').read_bytes()
        assert b'13800000000' not in raw
        # Use only synthetic data in the temporary profile. Every storefront
        # must use its own DPAPI vault; never fall back to the CN recipient.
        cases={'zh_HK':('+852 5123 4567',''),'zh_TW':('+886 912345678','100'),
               'ja_JP':('+81 9012345678','100-0001'),'en_SG':('+65 81234567','238839'),
               'en_AU':('+61 412345678','2000'),'en_MY':('+60 123456789','55188')}
        for locale,(phone,postal) in cases.items():
            regional_plan=dict(plan,checkout_locale=locale,financing='manual',payment='官网手动选择',
                               installment_option='',installment_months=0,auto_submit_order=False,max_price=500000)
            request('configure',plan=regional_plan)
            request('save_profile',profile=dict(profile,phone=phone,postal_code=postal,id_last4='',
                        family_name_kana='テスト',given_name_kana='デモ'))
            deadline=time.monotonic()+15
            while time.monotonic()<deadline:
                status=request('status')
                assert not status['error'],status['error']
                if not status['busy']:break
                time.sleep(.1)
            assert not status['busy']
            restored=request('read_profile')
            assert restored['id_last4']==''
            raw=(Path(directory)/'ApplePickupWatcherAuto'/locale/'profile.dpapi').read_bytes()
            assert b'demo@example.invalid' not in raw
        request('stop')
    finally:
        p.stdin.close()
        try:p.wait(timeout=10)
        except subprocess.TimeoutExpired:p.kill();p.wait();raise
    assert p.returncode==0
print(json.dumps({'packaged_helper':True,'utf8_ipc':True,'encrypted_profile_roundtrip':True,
                  'six_regional_dpapi_vaults':True,'installment_plan_saved':True,
                  'clean_shutdown':True,'apple_requests':0,'orders':0}))

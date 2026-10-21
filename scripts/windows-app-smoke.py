"""Inspect the installed Tauri UI with empty QA data; do not start monitoring."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from playwright.sync_api import sync_playwright

exe=Path(sys.argv[1]).resolve()
out=Path(sys.argv[2]).resolve();out.mkdir(parents=True,exist_ok=True)
with tempfile.TemporaryDirectory(prefix='shilou-app-qa-',ignore_cleanup_errors=True) as d:
    with socket.socket() as s:
        s.bind(('127.0.0.1',0));port=s.getsockname()[1]
    env={**os.environ,'LOCALAPPDATA':d,'APPDATA':d,'WEBVIEW2_USER_DATA_FOLDER':str(Path(d)/'webview'),
        'WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS':f'--remote-debugging-port={port} --remote-debugging-address=127.0.0.1'}
    app=subprocess.Popen([str(exe)],env=env)
    try:
        base=f'http://127.0.0.1:{port}'
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        deadline=time.monotonic()+40
        while time.monotonic()<deadline:
            try:
                with opener.open(base+'/json/version',timeout=1) as r: info=json.load(r)
                break
            except Exception:
                assert app.poll() is None,'Installed app exited before UI became ready'
                time.sleep(.3)
        else:raise AssertionError('Installed WebView2 did not become ready')
        with sync_playwright() as p:
            browser=p.chromium.connect_over_cdp(base)
            deadline=time.monotonic()+20
            page=None
            while time.monotonic()<deadline:
                pages=[v for c in browser.contexts for v in c.pages if 'localhost' in v.url or 'tauri' in v.url]
                if pages:page=pages[0];break
                time.sleep(.2)
            assert page,'No app WebView found'
            page.get_by_text('捡漏中心',exact=True).first.wait_for()
            state=page.evaluate("window.__TAURI_INTERNALS__.invoke('checkout_command',{op:'status'})")
            assert state['platform']=='windows' and not state['armed'] and not state['submissionEnabled']
            page.screenshot(path=str(out/'01-monitor.png'),full_page=True)
            page.get_by_role('button',name='选购方案',exact=False).first.click()
            page.get_by_role('button',name='分期',exact=True).click()
            page.get_by_role('button',name='24 期',exact=True).first.click()
            page.get_by_label('省份',exact=True).select_option(label='湖北省')
            page.get_by_label('城市',exact=True).select_option(label='武汉市')
            page.get_by_label('区县',exact=True).select_option(label='江汉区')
            for label,value in [('姓氏','示'),('名字','例'),('手机号','13800000000'),('邮箱','demo@example.invalid'),
                                ('详细地址','仅供 Windows 隔离测试的虚构地址'),('邮编','430000'),('证件后四位','0616')]:
                page.get_by_label(label,exact=True).fill(value)
            page.get_by_role('button',name='保存联系资料',exact=True).click()
            page.get_by_text('已加密保存并核对，可以启用自动选购',exact=True).wait_for()
            page.get_by_role('button',name='读取已保存资料',exact=True).click()
            assert page.get_by_label('证件后四位',exact=True).input_value()=='0616'
            page.screenshot(path=str(out/'02-plan.png'),full_page=True)
            page.get_by_role('button',name='全款',exact=True).click()
            assert page.get_by_role('button',name='全款',exact=True).get_attribute('aria-pressed')=='true'
            page.get_by_role('button',name='手机提醒',exact=False).first.click()
            assert page.get_by_role('button',name='测试提醒',exact=False).count() or page.get_by_text('测试提醒').count()
            page.screenshot(path=str(out/'03-bark.png'),full_page=True)
            # The target lives only in this process's temporary QA profile.
            page.evaluate("""async () => {
              const invoke=window.__TAURI_INTERNALS__.invoke;
              const products=await invoke('list_products',{locale:'zh_CN',category:'iphone'});
              const stores=await invoke('list_stores',{locale:'zh_CN'});
              const p=products[0], s=stores[0];
              await invoke('set_targets',{targets:[{locale:'zh_CN',storeNumber:s.number,
                storeTitle:s.title,partNumber:p.partNumber,productName:p.title}]});
            }""")
            page.reload()
            page.get_by_role('button',name='选购方案',exact=True).click()
            activate=page.get_by_role('button',name='启用有货自动选购',exact=True)
            activate.wait_for()
            page.get_by_role('checkbox',name='自动下单，等待本人支付',exact=False).check()
            # Native smoke checks the visible dialog and cancellation only.
            # Confirmation IPC is covered by the offline interaction tests.
            activate.click()
            page.get_by_role('dialog').wait_for()
            assert page.get_by_role('button',name='确认启用并授权下单',exact=True).is_enabled()
            page.screenshot(path=str(out/'04-confirmation.png'),full_page=True)
            page.get_by_role('button',name='取消',exact=True).click()
            page.get_by_role('dialog').wait_for(state='hidden')
            final=page.evaluate("window.__TAURI_INTERNALS__.invoke('checkout_command',{op:'status'})")
            assert not final['armed'] and not final['busy']
            page.get_by_label('选购国家／地区',exact=True).select_option('en_SG')
            assert page.get_by_label('总预算（含保障） · SGD',exact=True).input_value()=='0'
            assert page.get_by_label('证件后四位',exact=True).count()==0
            assert page.get_by_role('button',name='分期',exact=True).count()==0
            assert page.get_by_label('当地电话（+65）',exact=True).input_value()==''
            page.screenshot(path=str(out/'05-regional-plan.png'),full_page=True)
            page.get_by_label('选购国家／地区',exact=True).select_option('ja_JP')
            page.get_by_label('姓氏假名',exact=True).wait_for()
            report={'installed_app':str(exe.name),'native_webview':True,'packaged_helper_connected':True,
                'default_stopped':True,'province_city_district':True,'full_payment_switch':True,
                'first_use_profile_save_readback':True,'leading_zero_preserved':True,
                'foreign_region_resets_budget_and_profile':True,'japan_kana_fields':True,
                'installment_controls':True,'bark_page':True,'in_app_confirmation_and_cancel':True,'confirm_button_enabled':True,'arming_not_executed':True,'orders':0,'monitor_started':False}
            (out/'app-smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print(json.dumps(report,ensure_ascii=False))
    except Exception:
        import traceback
        (out/"failure.txt").write_text(traceback.format_exc(),encoding="utf-8")
        raise
    finally:
        # Only the isolated QA process tree is owned by this script.
        subprocess.run(['taskkill','/PID',str(app.pid),'/T','/F'],capture_output=True)

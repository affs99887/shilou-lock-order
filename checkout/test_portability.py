"""OS boundary regression; no personal profiles, orders or network requests."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from worker import Bridge, make_browser
import platform_runtime
from windows_vault import WindowsVault
from edge_browser import EdgeBrowser
from checkout_profile import store_profile


class PortabilityTests(unittest.TestCase):
    def test_windows_path_uses_localappdata_not_mac_library(self):
        with patch.object(platform_runtime.sys, 'platform', 'win32'), patch.dict(os.environ, {'LOCALAPPDATA':'C:/TestUser/AppData/Local'}):
            self.assertEqual(str(platform_runtime.data_directory()).replace('\\','/'), 'C:/TestUser/AppData/Local/ApplePickupWatcherAuto')

    def test_stop_blocks_windows_navigation_and_javascript_before_driver(self):
        with tempfile.TemporaryDirectory() as d:
            owner=Mock(data=Path(d),cancel=threading.Event())
            owner.cancel.set()
            with patch('worker.sys.platform','win32'):
                browser=make_browser(owner)
            browser.session.ensure=Mock(side_effect=AssertionError('must not connect'))
            for operation in (lambda:browser.new_page(),lambda:browser.page(),lambda:browser.evaluate('1+1')):
                with self.assertRaisesRegex(RuntimeError,'停止'):operation()
            browser.session.ensure.assert_not_called()

    def test_windows_profile_uses_vault_and_retains_leading_zero(self):
        p=dict(family_name='示',given_name='例',phone='13800000000',email='demo@example.invalid',province='湖北省',city='武汉市',district='江汉区',address='示例地址',postal_code='430000',id_last4='0616')
        with patch('checkout_profile.sys.platform','win32'), patch('safari_checkout_autofill.write_profile',return_value={'saved':True}) as save, patch('checkout_profile.subprocess.run') as shell:
            self.assertEqual(store_profile(p),{'saved':True})
            self.assertEqual(save.call_args.args[0]['id_last4'],'0616')
            shell.assert_not_called()

    def test_windows_native_dpapi_roundtrip(self):
        if sys.platform!='win32':self.skipTest('requires Windows DPAPI')
        with tempfile.TemporaryDirectory() as d:
            vault=WindowsVault(d)
            p={'phone':'13800000000','id_last4':'0616','family_name':'示例'}
            vault.write_profile(p)
            self.assertEqual(vault.load_profile(),p)
            self.assertNotIn(b'13800000000',vault.path.read_bytes())
            vault.delete_profile()
            self.assertFalse(vault.path.exists())

    def test_exclusive_lock_across_processes_then_release(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'service.lock'
            code='import sys;from platform_runtime import lock_file;f=open(sys.argv[1],"a+b");lock_file(f)'
            env={**os.environ,'PYTHONPATH':str(Path(platform_runtime.__file__).parent)}
            with path.open('a+b') as f:
                platform_runtime.lock_file(f)
                self.assertNotEqual(subprocess.run([sys.executable,'-c',code,str(path)],env=env,capture_output=True).returncode,0)
            self.assertEqual(subprocess.run([sys.executable,'-c',code,str(path)],env=env,capture_output=True).returncode,0)

    def test_edge_uncertain_mutation_does_not_repeat(self):
        session=Mock()
        page=Mock()
        page.is_closed.return_value=False
        browser=EdgeBrowser(session,'function appleCheckout(){return {}}')
        browser.window='owned';browser.pages['owned']=page
        channel=Mock();browser.channels['owned']=channel
        browser.evaluate=Mock(side_effect=RuntimeError('unknown write'))
        with self.assertRaisesRegex(RuntimeError,'不重复操作'):
            browser.call({}, {}, {'kind':'click','nonce':'one'})
        self.assertEqual(browser.evaluate.call_count,1)

    def test_edge_does_not_adopt_other_tabs(self):
        browser=EdgeBrowser(Mock(),'')
        browser.window='closed-owned'
        with self.assertRaisesRegex(RuntimeError,'不接管其他页面'):browser.page()

    def test_utf8_saved_config_can_be_reloaded(self):
        with tempfile.TemporaryDirectory() as d:
            b=Bridge(data=Path(d))
            b.request({'op':'configure','plan':b.plan})
            restored=Bridge(data=Path(d))
            self.assertEqual(restored.plan['payment'],'支付宝')
            self.assertFalse(restored.armed)

    def test_commit_timeout_keeps_readback_pending_without_reopening_product(self):
        browser=EdgeBrowser(Mock(),'')
        page=Mock();browser.page=Mock(return_value=page)
        page.goto.side_effect=TimeoutError('late commit')
        browser.present=Mock()
        browser.navigate('https://www.apple.com.cn/shop/buy-iphone/iphone-18-pro',foreground=True)
        self.assertTrue(browser.navigation_pending)
        self.assertEqual(browser.expected_path,'/shop/buy-iphone/iphone-18-pro')
        self.assertEqual(page.goto.call_count,1)
        browser.present.assert_called_once()

    def test_window_foreground_failure_does_not_cancel_owned_page_navigation(self):
        browser=EdgeBrowser(Mock(),'');page=Mock();browser.page=Mock(return_value=page)
        browser.present=Mock(side_effect=RuntimeError('foreground unavailable'))
        browser.navigate('https://www.apple.com.cn/shop/buy-iphone/iphone-18-pro',foreground=True)
        self.assertTrue(browser.presentation_failed)
        self.assertEqual(page.goto.call_count,1)
        browser.page=Mock(side_effect=RuntimeError('owned page closed'))
        with self.assertRaisesRegex(RuntimeError,'closed'):
            browser.navigate('https://www.apple.com.cn/shop/bag',foreground=True)

    def test_network_failure_is_not_hidden_as_a_navigation_timeout(self):
        browser=EdgeBrowser(Mock(),'');browser.page=Mock()
        browser.page.return_value.goto.side_effect=RuntimeError('network failure')
        with self.assertRaisesRegex(RuntimeError,'导航失败'):
            browser.navigate('https://www.apple.com.cn/shop/bag')
        self.assertFalse(browser.navigation_pending)

    def test_network_changed_retries_safe_get_once_only(self):
        for url in ('https://www.apple.com.cn/shop/bag',
                    'https://www.apple.com.cn/shop/buy-iphone/iphone-18-pro'):
            browser=EdgeBrowser(Mock(),'');page=Mock();browser.page=Mock(return_value=page)
            page.goto.side_effect=[RuntimeError('net::ERR_NETWORK_CHANGED secretURL'),None]
            browser.navigate(url)
            self.assertEqual(page.goto.call_count,2)
            self.assertEqual(browser.navigation_retries,1)
            page.goto.side_effect=RuntimeError('net::ERR_NETWORK_CHANGED secretURL')
            with self.assertRaisesRegex(RuntimeError,'一次恢复未成功') as caught:
                browser.navigate(url)
            self.assertNotIn('secretURL',str(caught.exception))
            self.assertEqual(page.goto.call_count,4)

    def test_network_changed_does_not_replay_checkout_or_login_entry(self):
        browser=EdgeBrowser(Mock(),'');page=Mock();browser.page=Mock(return_value=page)
        for path in ('checkout','signIn','bag/add'):
            page.goto.reset_mock();page.goto.side_effect=RuntimeError('net::ERR_NETWORK_CHANGED')
            with self.assertRaises(RuntimeError):browser.navigate('https://www.apple.com.cn/shop/'+path)
            self.assertEqual(page.goto.call_count,1)

    def test_shop_fetch_denial_blocks_click_before_script_execution(self):
        browser=EdgeBrowser(Mock(),'')
        browser.window='owned';page=Mock();browser.pages['owned']=page
        page.is_closed.return_value=False
        browser.channels['owned']=Mock()
        browser.evaluate=Mock()
        request=Mock(url='https://www.apple.com.cn/shop/bag/add?session=secret',
                     resource_type='fetch', method='POST')
        request.is_navigation_request.return_value=False
        response=Mock(request=request,status=541,headers={'retry-after':'60'})
        browser.response('owned',page,response)
        result=browser.call({}, {}, {'kind':'click','nonce':'one'})
        self.assertEqual(result['stage'],'BLOCKED')
        self.assertEqual(result['result'],'refused')
        self.assertEqual(result['http_status'],541)
        self.assertTrue(result['diagnostics']['access_shop_request'])
        self.assertNotIn('secret',json.dumps(result))
        browser.evaluate.assert_not_called()
        # A fresh successful document, not a background asset, clears the denial.
        response.status=200;response.frame=page.main_frame
        request.is_navigation_request.return_value=True
        browser.response('owned',page,response)
        self.assertIsNone(browser.access_failure())

    def test_document_denial_and_true_404_are_distinct_and_never_clicked(self):
        browser=EdgeBrowser(Mock(),'');browser.window='owned'
        browser.pages['owned']=Mock();browser.channels['owned']=Mock()
        browser.pages['owned'].is_closed.return_value=False
        browser.evaluate=Mock()
        for status,stage in ((541,'BLOCKED'),(403,'BLOCKED'),(429,'BLOCKED'),(404,'NOT_FOUND')):
            browser.last_status['owned']=(status,'')
            result=browser.call({}, {}, {'kind':'click','nonce':'one'})
            self.assertEqual(result['stage'],stage)
            self.assertEqual(result['result'],'refused')
            self.assertEqual(result['http_status'],status)
        browser.evaluate.assert_not_called()

    def test_third_party_failure_does_not_classify_storefront_as_denied(self):
        browser=EdgeBrowser(Mock(),'');browser.window='owned';page=Mock()
        request=Mock(url='https://example.invalid/shop/analytics',resource_type='fetch',method='GET')
        request.is_navigation_request.return_value=False
        browser.response('owned',page,Mock(request=request,status=403,headers={}))
        self.assertIsNone(browser.access_failure())

    def test_windows_regional_adapter_restricts_storefront_and_contact_payload(self):
        from regional_service import make_regional_browser
        with tempfile.TemporaryDirectory() as d, patch('regional_service.sys.platform','win32'):
            owner=Mock(data=Path(d),cancel=threading.Event())
            browser=make_regional_browser(owner,'en_SG')
            self.assertEqual(browser.entrypoint,'regionalCheckout')
            self.assertEqual(browser.validate_url('https://www.apple.com/sg/shop/bag'),'/sg/shop/bag')
            for url in ('https://www.apple.com.cn/shop/bag','https://www.apple.com/au/shop/bag'):
                with self.assertRaises(ValueError):browser.validate_url(url)
            with patch.object(EdgeBrowser,'call',return_value={'stage':'LOGIN'}) as call:
                browser.call({'phone':'secret','given_name':'Test'},{})
                self.assertEqual(call.call_args.args[0],{})
                browser.call({'phone':'secret','given_name':'Test'},{},{'kind':'fill','key':'given_name'})
                self.assertEqual(call.call_args.args[0],{'given_name':'Test'})
            owner.cancel.set()
            with self.assertRaisesRegex(RuntimeError,'停止'):browser.new_page()

    def test_windows_handoff_uses_edge_in_both_app_and_bark(self):
        with tempfile.TemporaryDirectory() as d, patch('worker.sys.platform','win32'):
            bridge=Bridge(data=Path(d))
            bridge.service.require_user_action('login')
            self.assertIn('Edge',bridge.notice['body'])
            self.assertNotIn('Safari',bridge.status()['intervention']['body'])

if __name__=='__main__':unittest.main()

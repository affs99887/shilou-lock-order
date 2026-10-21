"""Offline international acceptance: synthetic pages != live Apple acceptance."""
import copy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from worker import plan_for_hit
from checkout_engine import DEFAULTS, validate_preferences
from checkout_profile import validate_profile, editable_profile, store_profile
from regional_checkout import REGIONS, catalog, allowed_url
from regional_service import decide

class RegionsTests(unittest.TestCase):
    def make(self, locale):
        c = catalog()[locale]
        part, product = next(iter(c['products'].items()))
        store = next(iter(c['stores']))
        plan = dict(DEFAULTS, checkout_locale=locale, financing='manual', payment='官网手动选择', max_price=500000)
        h = dict(target=dict(locale=locale, partNumber=part, storeNumber=store),
                 availability=dict(kind='in_stock'), lastCheckedMs=100000,
                 product=dict(partNumber=part, family=product['dimensions'].get('familyType',product['family']), category='iphone'))
        return plan, h, plan_for_hit(plan, h, now=100001)

    def test_monitor_regions_and_checkout_regions_are_identical(self):
        from pathlib import Path
        import re
        source=(Path(__file__).resolve().parents[1]/'crates/apw-core/src/model.rs').read_text()
        declared=set(re.findall(r'locale: "([a-z]{2}_[A-Z]{2})"',source))
        self.assertEqual(declared,set(REGIONS))
        self.assertEqual(set(catalog()),set(REGIONS))

    def test_all_foreign_regions_map_their_actual_sku_and_store(self):
        for locale in REGIONS.keys()-{'zh_CN'}:
            with self.subTest(locale=locale):
                plan, h, p = self.make(locale)
                self.assertEqual(p['store_id'], h['target']['storeNumber'])
                self.assertEqual(p['currency'],REGIONS[locale]['currency'])
                self.assertTrue(allowed_url(p['product_url'],locale,True))
                for kind in ('unknown','out_of_stock'):
                    h['availability']['kind']=kind
                    with self.assertRaises(ValueError):plan_for_hit(plan,h,100001)

    def test_foreign_forbids_cross_region_catalog_and_mainland_payment(self):
        plan,h,_=self.make('en_SG')
        for patcher in (dict(auto_submit_order=True),dict(financing='full'),dict(payment='支付宝'),dict(checkout_locale='ja_JP')):
            with self.assertRaises(ValueError):plan_for_hit(dict(plan,**patcher),h,100001)
        h['target']['storeNumber']=next(iter(catalog()['zh_CN']['stores']))
        with self.assertRaises(ValueError):plan_for_hit(plan,h,100001)

    def test_region_url_is_bound_to_storefront_not_merely_apple_hostname(self):
        for locale in REGIONS.keys()-{'zh_CN'}:
            base=REGIONS[locale]['base']
            self.assertTrue(allowed_url(base+'/shop/bag',locale,True))
            for url in ('https://www.apple.com.cn/shop/bag','https://www.apple.com/us/shop/bag',
                        base+'/shop/../shop/bag',base+'/shop/%2e%2e/shop/bag',
                        base.replace('www.apple.com','www.apple.com.evil.test')+'/shop/bag',
                        base+'/shop/bag?x=1',base.replace('https://','http://')+'/shop/bag'):
                self.assertFalse(allowed_url(url,locale,True),url)

    def test_international_budget_does_not_use_cn_range_or_convert_currency(self):
        p,_,_=self.make('ja_JP')
        self.assertEqual(validate_preferences(p)['max_price'],500000)
        with self.assertRaises(ValueError):validate_preferences(dict(p,max_price=0))
        with self.assertRaises(ValueError):validate_preferences(dict(p,auto_submit_order=True))

    def test_profiles_validate_local_phone_and_postal_rules_and_do_not_require_cn_id(self):
        cases={'zh_HK':('+852 5123 4567',''),'zh_TW':('+886 912345678','100'),
               'ja_JP':('+81 9012345678','100-0001'),'en_SG':('+65 81234567','238839'),
               'en_AU':('+61 412345678','2000'),'en_MY':('+60 123456789','55188')}
        for locale,(phone,postal) in cases.items():
            raw=dict(family_name='Example',given_name='Test',phone=phone,email='test@example.invalid',
                     province='Example',city='Example',district='Example',address='Offline fixture',
                     postal_code=postal,family_name_kana='テスト',given_name_kana='デモ')
            p=validate_profile(raw,locale=locale)
            self.assertEqual(p['id_last4'],'')
            with self.assertRaises(ValueError):validate_profile(dict(raw,phone='13800000000'),locale=locale)
            with self.assertRaises(ValueError):validate_profile(dict(raw,credit_card='secret'),locale=locale)

    def test_foreign_profile_uses_separate_vault_and_explicit_region(self):
        with patch('checkout_profile.regional_vault',return_value={'phone':'81234567'}) as vault:
            self.assertEqual(editable_profile('en_SG')['phone'],'81234567')
            vault.assert_called_once_with('en_SG')

    def test_unavailable_unknown_payment_and_duplicate_add_never_blindly_click(self):
        _,_,p=self.make('en_SG')
        for stage in ('UNKNOWN','BLOCKED','NOT_FOUND','SESSION_EXPIRED','LOST_TAB','PAYMENT','REVIEW'):
            self.assertEqual(decide(dict(stage=stage),p,{})['kind'],'handoff')
        self.assertEqual(decide(dict(stage='SOLD_OUT'),p,{})['kind'],'sold_out')
        s=dict(stage='PRODUCT',controls=[dict(group='add',ref='1')])
        self.assertEqual(decide(s,p,dict(add_sent=True))['kind'],'inspect_bag')

    def test_bag_must_match_exact_sku_quantity_currency_and_care_before_checkout(self):
        _,_,p=self.make('en_SG')
        s=dict(stage='BAG',items=[dict(part=p['part_number'],quantity=1,applecare=False)],
               total=1500,currency='SGD',controls=[dict(group='checkout',ref='1')])
        self.assertEqual(decide(s,p,{})['key'],'checkout')
        for key,value in [('total',None),('total',600000),('currency','CNY')]:
            self.assertEqual(decide(dict(s,**{key:value}),p,{})['kind'],'handoff')
        for key,value in [('part','OTHER/A'),('quantity',2),('applecare',True)]:
            bad=copy.deepcopy(s);bad['items'][0][key]=value
            self.assertEqual(decide(bad,p,{})['kind'],'handoff')

    def test_store_must_be_selected_and_dates_cannot_be_guessed(self):
        _,_,p=self.make('en_SG')
        s=dict(stage='FULFILLMENT',controls=[dict(group='store',ref='1',matches=True,selected=False)])
        self.assertEqual(decide(s,p,{})['key'],'store:store')
        s['controls'][0]['matches']=False
        self.assertEqual(decide(s,p,{})['kind'],'handoff')
        s['controls']=[dict(group='store',ref='1',matches=True,selected=True),dict(group='date',ref='2',value='2099-01-01')]
        self.assertEqual(decide(s,p,{})['kind'],'handoff')

    def test_two_item_fulfillment_does_not_accept_only_one_target_store(self):
        _,_,p=self.make('en_SG')
        s=dict(stage='FULFILLMENT',controls=[dict(group='store',scope='item1',ref='1',matches=True,selected=True),
            dict(group='store',scope='item2',ref='2',matches=False,selected=True)])
        self.assertEqual(decide(s,p,{})['kind'],'handoff')

    def test_ambiguous_product_cannot_add_and_empty_parser_cannot_clear(self):
        _,_,p=self.make('en_SG')
        s=dict(stage='PRODUCT',controls=[dict(group='add',ref='1')])
        self.assertEqual(decide(s,p,{})['kind'],'handoff')
        self.assertEqual(decide(dict(s,product_part=p['part_number']),p,{})['key'],'add')
        self.assertEqual(decide(dict(stage='BAG',items=[]),p,dict(clearing=True))['kind'],'handoff')

    def test_region_directory_does_not_fall_back_to_current_stable_data(self):
        import os
        from platform_runtime import data_directory
        with patch.dict(os.environ,{'APW_CHECKOUT_VARIANT':'international'}):
            self.assertEqual(data_directory().name,'ApplePickupWatcherInternational')
        with patch.dict(os.environ,{'APW_CHECKOUT_VARIANT':''}):
            self.assertEqual(data_directory().name,'ApplePickupWatcherAuto')

    def test_manually_completed_unknown_field_can_continue_after_validation(self):
        _,_,p=self.make('en_SG')
        ledger=dict(store_verified=True,date_verified=True)
        s=dict(stage='CONTACT',fields=[dict(key='',required=True,filled=False,valid=False)],
               controls=[dict(group='continue',ref='next')])
        self.assertEqual(decide(s,p,ledger)['kind'],'handoff')
        s['fields'][0].update(filled=True,valid=True)
        self.assertEqual(decide(s,p,ledger)['key'],'continue:CONTACT')

    def test_service_progression_stops_at_payment_without_an_order(self):
        from regional_service import RegionalService
        import threading
        from types import SimpleNamespace
        _,_,p=self.make('en_SG')
        states=[dict(stage='BAG',bag_empty=True,items=[]),
            dict(stage='PRODUCT',product_part=p['part_number'],controls=[dict(group='add',ref='a')]),
            dict(stage='BAG',items=[dict(part=p['part_number'],quantity=1,applecare=False)],
                 total=1500,currency='SGD',controls=[dict(group='checkout',ref='c')]),
            dict(stage='FULFILLMENT',controls=[dict(group='store',ref='s',matches=True,selected=True),
                 dict(group='date',ref='d',value=p['pickup_dates'][0],selected=True),dict(group='continue',ref='n')]),
            dict(stage='CONTACT',controls=[dict(group='continue',ref='n2')],fields=[dict(ref='f',key='given_name',filled=False,valid=True,required=True,matches=False)]),
            dict(stage='CONTACT',controls=[dict(group='continue',ref='n2')],fields=[dict(ref='f',key='given_name',filled=True,valid=True,required=True,matches=True)]),
            dict(stage='PAYMENT')]
        class Browser:
            def __init__(self):self.index=0;self.calls=[]
            def open(self,url):self.calls.append(('open',url))
            def navigate(self,url):self.calls.append(('navigate',url));self.index+=1
            def call(self,profile,target,command=None):
                if command:
                    self.calls.append((command['kind'],command.get('key')));self.index+=1
                    return dict(result='dispatched')
                return dict(states[self.index],fingerprint='document:'+str(self.index))
        owner=SimpleNamespace(cancel=threading.Event(),automatic=False,notice=None,retryable_end=False,
                              data=Path(tempfile.gettempdir())/'apw-region-unused-fixture')
        service=RegionalService(owner,p);service.browser=Browser()
        with patch('regional_service.normalized_regional_profile',return_value={'given_name':'Example'}):
            service.command('rehearse')
        for _ in range(12):
            if not service.active:break
            service.next_tick=0;service.tick()
        self.assertEqual(service.status['stage'],'PAYMENT')
        self.assertEqual(service.status['mode'],'attention')
        self.assertEqual(sum(c==('click','add') for c in service.browser.calls),1)
        self.assertFalse(any(c[0]=='submit_order' for c in service.browser.calls))
        self.assertIn('本人',owner.notice['body'])
        self.assertEqual(service.status['intervention']['kind'], 'payment')
        self.assertEqual(service.status['intervention']['body'], owner.notice['body'])

    def manual_service(self, locale, stage):
        from regional_service import RegionalService
        import threading
        from types import SimpleNamespace
        from unittest.mock import Mock
        _, _, plan = self.make(locale)
        owner = SimpleNamespace(cancel=threading.Event(), automatic=False, notice=None, retryable_end=False,
                                data=Path(tempfile.gettempdir())/'apw-region-unused-fixture')
        service = RegionalService(owner, plan)
        service.browser = Mock()
        service.browser.call.return_value = dict(stage=stage, fingerprint='doc:original')
        service.active = True
        service.changed_at = __import__('time').monotonic()
        return service, owner

    def test_each_foreign_payment_and_review_emit_matching_app_and_bark_notice(self):
        for locale in REGIONS.keys() - {'zh_CN'}:
            for stage, kind in [('PAYMENT', 'payment'), ('REVIEW', 'review')]:
                with self.subTest(locale=locale, stage=stage):
                    service, owner = self.manual_service(locale, stage)
                    service.tick()
                    card = service.status['intervention']
                    self.assertEqual(card['kind'], kind)
                    self.assertEqual(card['region'], REGIONS[locale]['title'])
                    self.assertEqual((card['title'], card['body']), (owner.notice['title'], owner.notice['body']))
                    self.assertIn('未提交订单', owner.notice['body'])
                    self.assertFalse(service.active)
                    service.browser.call.assert_called_once_with({}, service.preferences)

    def test_manual_continue_on_same_payment_document_does_not_repeat_bark(self):
        service, owner = self.manual_service('en_SG', 'PAYMENT')
        service.tick()
        notice = copy.deepcopy(owner.notice)
        for _ in range(3):
            service.command('continue')
            service.next_tick = 0
            service.tick()
            self.assertEqual(owner.notice, notice)
            self.assertEqual(service.status['intervention']['kind'], 'payment')
        service.browser.call.return_value = dict(stage='REVIEW', fingerprint='doc:review')
        service.command('continue'); service.next_tick = 0; service.tick()
        self.assertNotEqual(owner.notice['id'], notice['id'])
        self.assertEqual(owner.notice['kind'], 'review')

    def test_login_notifies_immediately_once_and_resumes_after_completion(self):
        service, owner = self.manual_service('en_SG', 'LOGIN')
        service.tick()
        notice = copy.deepcopy(owner.notice)
        self.assertEqual(notice['kind'], 'login')
        self.assertTrue(service.active)
        self.assertEqual(service.status['mode'], 'waiting_user')
        # DOM changes while entering credentials must not cause another push.
        service.browser.call.return_value = dict(stage='LOGIN', fingerprint='doc:edited')
        service.next_tick = 0; service.tick()
        self.assertEqual(owner.notice, notice)
        service.browser.call.return_value = dict(stage='PRODUCT', fingerprint='doc:product')
        service.next_tick = 0; service.tick()
        self.assertIsNone(service.status['intervention'])
        self.assertEqual(service.status['mode'], 'running')

    def test_address_and_applecare_manual_steps_emit_both_channels(self):
        for stage, patcher, kind in [
            ('CONTACT', dict(fields=[dict(key='', required=True, filled=False, valid=False)]), 'contact'),
            ('PRODUCT', dict(), 'applecare'),
        ]:
            service, owner = self.manual_service('ja_JP', stage)
            service.ledger.update(store_verified=True, date_verified=True)
            if stage == 'PRODUCT': service.preferences['applecare'] = 'add'
            service.browser.call.return_value.update(patcher)
            service.tick()
            self.assertEqual(service.status['intervention']['kind'], kind)
            self.assertEqual(owner.notice['kind'], kind)

    def test_stop_or_cancel_suppresses_new_notices_and_preserves_page(self):
        service, owner = self.manual_service('en_SG', 'PAYMENT')
        service.tick()
        service.command('stop')
        self.assertIsNone(service.status['intervention'])
        self.assertIsNone(owner.notice)
        service.next_tick = 0; service.tick()
        self.assertEqual(service.browser.call.call_count, 1)
        service, owner = self.manual_service('en_SG', 'PAYMENT')
        owner.cancel.set()
        service.tick()
        service.browser.call.assert_not_called()
        self.assertIsNone(owner.notice)

    def test_cancel_during_read_does_not_emit_late_manual_notice(self):
        service, owner = self.manual_service('en_SG', 'PAYMENT')
        def read(*args):
            owner.cancel.set()
            return dict(stage='PAYMENT', fingerprint='doc:late')
        service.browser.call.side_effect = read
        service.tick()
        self.assertIsNone(owner.notice)
        self.assertIsNone(service.status.get('intervention'))

if __name__=='__main__':unittest.main()

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
            secure=base.replace('www.apple.com','secure6.store.apple.com')+'/shop/signIn'
            self.assertTrue(allowed_url(secure,locale))
            self.assertFalse(allowed_url(secure,locale,True))
            self.assertFalse(allowed_url(secure.replace('apple.com','apple.com.evil.test'),locale))
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

    def test_duplicate_canonical_checkout_buttons_are_one_action_after_bag_verification(self):
        _,_,p=self.make('zh_HK')
        s=dict(stage='BAG',items=[dict(part=p['part_number'],quantity=1,applecare=False)],
               total=10499,currency='HKD',controls=[dict(group='checkout',ref=str(i),autom='checkout',label='結帳') for i in (1,2)])
        self.assertEqual(decide(s,p,{})['ref'],'1')
        for key,value in [('autom','other'),('label','Other checkout')]:
            bad=copy.deepcopy(s);bad['controls'][1][key]=value
            self.assertEqual(decide(bad,p,{})['kind'],'wait')
        s['items'][0]['part']='WRONG1/A'
        self.assertEqual(decide(s,p,{})['kind'],'handoff')

    def test_outgoing_product_after_add_never_reselects_partial_dimensions(self):
        _,_,p=self.make('zh_HK')
        s=dict(stage='PRODUCT',controls=[dict(group='dimensionColor',ref='1',matches=False)])
        self.assertEqual(decide(s,p,dict(add_sent=True))['kind'],'inspect_bag')

    def test_two_item_fulfillment_does_not_accept_only_one_target_store(self):
        _,_,p=self.make('en_SG')
        s=dict(stage='FULFILLMENT',controls=[dict(group='store',scope='item1',ref='1',matches=True,selected=True),
            dict(group='store',scope='item2',ref='2',matches=False,selected=True)])
        self.assertEqual(decide(s,p,{})['kind'],'handoff')

    def test_hong_kong_location_search_is_catalog_bound_and_precedes_store_selection(self):
        _,_,p=self.make('zh_HK');p['regional_store']={'address':{'city':'香港'}}
        s=dict(stage='FULFILLMENT',fields=[dict(key='pickup_location',ref='location',valid=False,matches=False)],
               controls=[dict(group='pickup',ref='tab',selected=True),dict(group='store_search',ref='search',disabled=True)])
        self.assertEqual(decide(s,p,{})['kind'],'search_location')
        self.assertEqual(decide(s,p,{})['value'],'香港')
        s['fields'][0].update(valid=True,matches=True);s['controls'][1]['disabled']=False
        self.assertEqual(decide(s,p,{})['key'],'store_search')
        s['controls'].append(dict(group='store',ref='wrong-store',matches=False,selected=False))
        self.assertEqual(decide(s,p,{})['kind'],'handoff')

    def test_hong_kong_delayed_store_date_and_slot_panels_advance_without_manual_confirmation(self):
        _,_,p=self.make('zh_HK');p['pickup_date_policy']='earliest'
        ledger={};s=dict(stage='FULFILLMENT',controls=[dict(group='continue',ref='next')])
        self.assertEqual(decide(s,p,ledger)['kind'],'wait')
        store=dict(group='store',ref='store',matches=True,selected=False)
        s['controls'].append(store);self.assertEqual(decide(s,p,ledger)['key'],'store:store')
        store['selected']=True;self.assertEqual(decide(s,p,ledger)['kind'],'wait')
        s['controls'].append(dict(group='date',ref='date',value='2099-10-23',selected=True))
        self.assertEqual(decide(s,p,ledger)['kind'],'wait')
        slot=dict(group='time',ref='time',input_type='select',value='',options=[]);s['controls'].append(slot)
        self.assertEqual(decide(s,p,ledger)['kind'],'wait')
        slot['options']=[dict(value='23-10:00-10:15',label='10:00 – 10:15')]
        self.assertEqual(decide(s,p,ledger)['value'],'23-10:00-10:15')
        self.assertFalse(ledger['time_verified'])
        slot['value']='23-10:00-10:15'
        self.assertEqual(decide(s,p,ledger)['key'],'continue:FULFILLMENT')
        self.assertTrue(ledger['store_verified'] and ledger['date_verified'] and ledger['time_verified'])

    def test_offscreen_target_expands_stores_once_and_unavailable_target_never_switches(self):
        _,_,p=self.make('zh_HK')
        s=dict(stage='FULFILLMENT',controls=[dict(group='store',ref='other',matches=False,selected=False),dict(group='more_stores',ref='more')])
        self.assertEqual(decide(s,p,{})['key'],'more_stores')
        self.assertEqual(decide(s,p,dict(stores_expanded=True))['kind'],'handoff')
        s['controls'].append(dict(group='store',ref='target',matches=True,selected=False))
        self.assertEqual(decide(s,p,dict(stores_expanded=True))['ref'],'target')
        s['controls'][-1]['disabled']=True
        self.assertEqual(decide(s,p,{})['kind'],'handoff')
        s['controls']=[s['controls'][-1]]
        self.assertEqual(decide(s,p,{})['kind'],'handoff')

    def test_hong_kong_month_label_day_value_and_earliest_slot_are_verified(self):
        from datetime import datetime
        _,_,p=self.make('zh_HK');p['pickup_date_policy']='earliest'
        s=dict(stage='FULFILLMENT',controls=[dict(group='store',ref='store',matches=True,selected=True),
            dict(group='date',ref='past',label='october21',value='21',disabled=True),
            dict(group='date',ref='earliest',label='october22',value='22',selected=True),
            dict(group='date',ref='later',label='october23',value='23',selected=False),
            dict(group='time',ref='time',value='',options=[dict(value='22-11:00-11:15',label='11:00 – 11:15'),dict(value='22-10:00-10:15',label='10:00 – 10:15')])])
        ledger={}
        with patch('pickup_monitor.datetime',wraps=datetime) as clock:
            clock.now.return_value=datetime(2026,10,21)
            decision=decide(s,p,ledger)
            self.assertEqual(decision['key'],'time');self.assertEqual(decision['value'],'22-10:00-10:15')
            self.assertEqual(ledger['pickup_date'],'2026-10-22')
            stale=copy.deepcopy(s);stale['controls'][2]['disabled']=True
            stale['controls'][2]['selected']=False;stale['controls'][3]['selected']=True
            self.assertEqual(decide(stale,p,{})['kind'],'wait')
            for bad in [('22','22'),('october23','22')]:
                broken=copy.deepcopy(s);broken['controls'][2].update(label=bad[0],value=bad[1])
                self.assertEqual(decide(broken,p,{})['kind'],'handoff')
            specified=copy.deepcopy(p);specified.update(pickup_date_policy='specified',pickup_dates=['2026-10-23'])
            self.assertEqual(decide(s,specified,{})['ref'],'later')

    def test_ambiguous_product_cannot_add_and_empty_parser_cannot_clear(self):
        _,_,p=self.make('en_SG')
        s=dict(stage='PRODUCT',controls=[dict(group='add',ref='1')])
        self.assertEqual(decide(s,p,{})['kind'],'handoff')
        self.assertEqual(decide(dict(s,product_part=p['part_number']),p,{})['key'],'add')
        self.assertEqual(decide(dict(stage='BAG',items=[]),p,dict(clearing=True))['kind'],'handoff')

    def test_hong_kong_no_applecare_label_is_selected_without_confusing_paid_care(self):
        _,_,p=self.make('zh_HK')
        snapshot=dict(stage='PRODUCT',controls=[
            dict(group='applecare',ref='paid',value='on',label='AppleCare+ 服務計劃 HK$1,799'),
            dict(group='applecare',ref='none',value='on',label='無 AppleCare+ 服務計劃保障'),
            dict(group='applecare',ref='more',label='顯示更多')])
        result=decide(snapshot,p,{})
        self.assertEqual(result['key'],'applecare');self.assertEqual(result['ref'],'none')
        snapshot['controls'].append(dict(group='applecare',ref='duplicate',label='無 AppleCare+'))
        self.assertEqual(decide(snapshot,p,{})['kind'],'handoff')

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
        for _ in range(20):
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

    def test_add_departure_keeps_receipt_until_bag_even_if_product_controls_change(self):
        import time
        service,owner=self.manual_service('zh_HK','PRODUCT')
        service.ledger={'add_sent':True}
        service.pending='add';service.pending_since=time.monotonic()-6
        service.fingerprint='doc:before'
        service.browser.call.return_value.update(fingerprint='doc:after',controls=[dict(group='dimensionColor',matches=False)])
        service.tick()
        service.browser.navigate.assert_called_once_with(service.preferences['bag_url'])
        self.assertEqual(service.pending,'inspect_bag');self.assertTrue(service.active)
        service.browser.call.return_value=dict(stage='LOADING')
        service.next_tick=0;service.tick()
        service.browser.navigate.assert_called_once()
        self.assertEqual(service.pending,'inspect_bag')
        service.browser.call.return_value=dict(stage='BAG',fingerprint='bag:ready',items=[])
        service.next_tick=0;service.tick()
        service.next_tick=0;service.tick()
        self.assertIsNone(service.pending);self.assertFalse(service.active)
        self.assertEqual(service.browser.call.call_count,4)

    def test_checkout_departure_does_not_click_twice_when_bag_fingerprint_changes(self):
        import time
        service,owner=self.manual_service('zh_HK','BAG')
        service.pending='checkout';service.pending_since=time.monotonic()-31
        service.fingerprint='doc:before'
        service.browser.call.return_value.update(fingerprint='doc:after')
        service.tick()
        self.assertFalse(service.active)
        service.browser.call.assert_called_once_with({},service.preferences)
        service.browser.navigate.assert_not_called()

    def test_loading_bag_waits_then_clears_only_after_items_are_visible(self):
        service,owner=self.manual_service('zh_HK','LOADING')
        service.preferences['clear_bag_before_purchase']=True
        service.ledger={'clearing':True}
        service.browser.call.return_value.update(bag_loading=True,items=[],bag_empty=False)
        service.tick();self.assertTrue(service.active);self.assertIsNone(owner.notice)
        service.browser.call.return_value=dict(stage='BAG',fingerprint='doc:loaded',items=[dict(part='MJRT4ZA/A')],controls=[dict(group='remove',ref='remove')])
        service.browser.call.side_effect=lambda profile,target,command=None:dict(result='dispatched') if command else service.browser.call.return_value
        service.next_tick=0;service.tick()
        service.next_tick=0;service.tick()
        self.assertEqual(service.pending,'remove:remove')
        service.browser.call.return_value=dict(stage='LOADING',bag_loading=True,fingerprint='doc:removed',items=[],bag_empty=False)
        service.next_tick=0;service.tick();self.assertTrue(service.active);service.browser.navigate.assert_not_called()
        service.browser.call.return_value=dict(stage='BAG',fingerprint='doc:empty',items=[],bag_empty=True)
        service.next_tick=0;service.tick()
        service.next_tick=0;service.tick()
        service.browser.navigate.assert_called_once_with(service.preferences['product_url'])
        self.assertEqual(sum(call.args[2].get('key','').startswith('remove:') for call in service.browser.call.call_args_list if len(call.args)>2),1)

    def test_changing_incomplete_bag_fingerprints_cannot_wait_forever(self):
        service,owner=self.manual_service('zh_HK','LOADING')
        service.browser.call.return_value.update(bag_loading=True)
        service.tick();service.bag_loading_since-=31
        service.browser.call.return_value['fingerprint']='doc:still-loading'
        service.next_tick=0;service.tick()
        self.assertFalse(service.active);self.assertEqual(owner.notice['kind'],'cart')
        service.browser.navigate.assert_not_called()

    def test_explicit_stale_refusal_can_retry_but_unknown_add_outcome_cannot(self):
        for receipt in [dict(result='stale',dispatched=False),dict(result='refused'),{}]:
            service,owner=self.manual_service('zh_HK','PRODUCT')
            snapshot=dict(stage='PRODUCT',fingerprint='doc:product',product_part=service.preferences['part_number'],controls=[dict(group='add',ref='add')])
            receipts=[receipt,dict(result='dispatched')]
            service.browser.call.side_effect=lambda profile,target,command=None:receipts.pop(0) if command else snapshot
            service.tick()
            if receipt.get('result')=='stale':
                self.assertTrue(service.active);self.assertFalse(service.ledger.get('add_sent'));self.assertFalse(service.sent)
                service.next_tick=0;service.tick()
                self.assertTrue(service.ledger['add_sent']);self.assertEqual(service.pending,'add')
            else:
                self.assertFalse(service.active);self.assertTrue(service.ledger['add_sent'])
                service.next_tick=0;service.tick();self.assertEqual(len(receipts),1)

    def test_stale_refusal_retries_are_bounded(self):
        service,owner=self.manual_service('zh_HK','PRODUCT')
        snapshot=dict(stage='PRODUCT',fingerprint='doc:product',product_part=service.preferences['part_number'],controls=[dict(group='add',ref='add')])
        service.browser.call.side_effect=lambda profile,target,command=None:dict(result='stale',dispatched=False) if command else snapshot
        for _ in range(6):service.next_tick=0;service.tick()
        self.assertFalse(service.active);self.assertFalse(service.ledger.get('add_sent'))
        self.assertEqual(sum(len(c.args)>2 for c in service.browser.call.call_args_list),4)

    def test_more_store_results_wait_for_target_without_requiring_manual_confirmation(self):
        import time
        service,owner=self.manual_service('zh_HK','FULFILLMENT')
        service.pending='more_stores';service.pending_since=time.monotonic()
        service.fingerprint='doc:before';service.ledger={'stores_expanded':True}
        service.browser.call.return_value.update(fingerprint='doc:expanding',controls=[dict(group='store',ref='other',matches=False)])
        service.tick();self.assertTrue(service.active);self.assertEqual(service.pending,'more_stores');self.assertIsNone(owner.notice)
        service.browser.call.return_value['controls'].append(dict(group='store',ref='target',matches=True,selected=False))
        service.browser.call.return_value['fingerprint']='doc:target'
        service.browser.call.side_effect=lambda profile,target,command=None:dict(result='dispatched') if command else service.browser.call.return_value
        service.next_tick=0;service.tick();service.next_tick=0;service.tick()
        self.assertEqual(service.pending,'store:store')
        commands=[c.args[2] for c in service.browser.call.call_args_list if len(c.args)>2]
        self.assertEqual(len(commands),1);self.assertEqual(commands[0]['ref'],'target')

    def test_continuation_waits_for_new_step_instead_of_repeating_during_spa_transition(self):
        import time
        service,owner=self.manual_service('zh_HK','FULFILLMENT')
        service.ledger=dict(store_verified=True,date_verified=True,time_verified=True,
                            profile=dict(family_name='TEST',given_name='CHECKOUT',email='checkout-test@example.invalid',phone='81810000'))
        service.fingerprint='doc:before'
        service.pending='continue:FULFILLMENT';service.pending_since=time.monotonic()
        service.sent.add(('continue:FULFILLMENT','doc'))
        # Same document and old step remain visible while the accepted click
        # updates controls. A changed fingerprint is not a second click permit.
        service.browser.call.return_value=dict(stage='FULFILLMENT',fingerprint='doc:departing',controls=[dict(group='continue',ref='old')])
        service.tick();self.assertTrue(service.active);self.assertIsNone(owner.notice)
        self.assertEqual(service.pending,'continue:FULFILLMENT')
        service.browser.call.return_value=dict(stage='LOADING',fingerprint='doc:loading')
        service.next_tick=0;service.tick();self.assertEqual(service.pending,'continue:FULFILLMENT')
        service.browser.call.return_value=dict(stage='CONTACT',fingerprint='doc:contact',fields=[dict(ref='name',key='family_name',required=True,valid=False,matches=False,filled=False)])
        service.browser.call.side_effect=lambda profile,target,command=None:dict(result='dispatched') if command else service.browser.call.return_value
        service.next_tick=0;service.tick()
        self.assertEqual(service.pending,'family_name');self.assertTrue(service.active)
        commands=[c.args[2] for c in service.browser.call.call_args_list if len(c.args)>2]
        self.assertEqual([(c['kind'],c['key']) for c in commands],[('fill','family_name')])

    def test_contact_continue_waits_through_same_step_updates_until_payment(self):
        import time
        service,owner=self.manual_service('zh_HK','CONTACT')
        service.fingerprint='doc:before';service.pending='continue:CONTACT';service.pending_since=time.monotonic()
        service.browser.call.return_value=dict(stage='CONTACT',fingerprint='doc:departing')
        service.tick();self.assertTrue(service.active);self.assertIsNone(owner.notice)
        service.browser.call.return_value=dict(stage='PAYMENT',fingerprint='doc:payment')
        service.next_tick=0;service.tick()
        self.assertFalse(service.active);self.assertEqual(owner.notice['kind'],'payment')
        self.assertEqual(service.browser.call.call_count,2)

    def test_contact_fill_waits_for_its_value_readback_despite_unrelated_dom_updates(self):
        import time
        service,owner=self.manual_service('zh_HK','CONTACT')
        service.ledger=dict(store_verified=True,date_verified=True,time_verified=True,profile=dict(family_name='TEST',given_name='CHECKOUT'))
        service.fingerprint='doc:before';service.pending='family_name';service.pending_since=time.monotonic();service.sent.add(('family_name','doc'))
        service.browser.call.return_value=dict(stage='CONTACT',fingerprint='doc:rendering',fields=[dict(key='family_name',ref='name',required=True,valid=False,matches=False)])
        service.tick();self.assertTrue(service.active);self.assertIsNone(owner.notice);self.assertEqual(service.pending,'family_name')
        service.browser.call.return_value=dict(stage='CONTACT',fingerprint='doc:readback',fields=[dict(key='family_name',ref='name',required=True,valid=True,matches=True),dict(key='given_name',ref='given',required=True,valid=False,matches=False)])
        service.browser.call.side_effect=lambda profile,target,command=None:dict(result='dispatched') if command else service.browser.call.return_value
        service.next_tick=0;service.tick()
        commands=[c.args[2] for c in service.browser.call.call_args_list if len(c.args)>2]
        self.assertEqual([(c['kind'],c['key']) for c in commands],[('fill','given_name')])

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

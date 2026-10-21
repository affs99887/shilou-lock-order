import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from worker import Bridge, Checkout, DEFAULTS
from order_guard import OrderGuard
from payment_options import installment_action, INSTALLMENT_PLANS
from checkout_engine import validate_preferences, Attempt, variants

def payment(provider='微信分付', months=24, selected=True):
    return dict(stage='PAYMENT',installment_panel_stable=True,installment_selected_months=months,
        controls=[dict(ref='p',group='payment',installment=True,label=provider,selected=selected),
                  dict(ref='t',group='installment_term',installment_months=months,label=f'{months} 期 0% 年化利率',selected=True)])

class InstallmentTests(unittest.TestCase):
    def test_ui_and_engine_presets_identical(self):
        data=json.loads((Path(__file__).parents[1]/'src/lib/installment-plans.json').read_text())
        self.assertEqual({p['provider']:tuple(p['months']) for p in data},INSTALLMENT_PLANS)
    def test_every_preset_validates_and_reads_back(self):
        for provider, terms in INSTALLMENT_PLANS.items():
            for n in terms:
                p=validate_preferences({**DEFAULTS,'financing':'official','installment_option':provider,'installment_months':n})
                self.assertEqual(p['installment_months'],n)
                self.assertEqual(installment_action(payment(provider,n),provider,n),(None,''))
    def test_impossible_pair_refused(self):
        for provider,n in [('花呗分期',24),('工商银行',1),('不存在',12),('微信分付',True)]:
            with self.assertRaises(ValueError):validate_preferences({**DEFAULTS,'financing':'official','installment_option':provider,'installment_months':n})
    def test_select_exact_provider_first(self):
        c,e=installment_action(payment(selected=False),'微信分付',24)
        self.assertEqual((c['ref'],e),('p',''))
    def test_stale_panel_waits(self):
        s=payment();s['installment_panel_stable']=False
        self.assertIn('等待',installment_action(s,'微信分付',24)[1])
    def test_duplicate_and_missing_term_refused(self):
        for months in (6,24):
            s=payment();s['controls'].append(copy.deepcopy(s['controls'][1]))
            self.assertTrue(installment_action(s,'微信分付',months)[1])
    def test_rates_not_inferred(self):
        for rate in ('3 期','24 期 10% 年化利率','24 期 0.5% 手续费'):
            s=payment();s['controls'][1]['label']=rate
            self.assertTrue(installment_action(s,'微信分付',24)[1])
    def test_review_requires_exact_terms(self):
        s={'stage':'REVIEW','review_payment':{'provider':'微信分付','months':24,'fee_percent':0}}
        self.assertEqual(installment_action(s,'微信分付',24),(None,''))
        for key,value in [('provider','花呗分期'),('months',12),('fee_percent',1)]:
            x=copy.deepcopy(s);x['review_payment'][key]=value
            self.assertTrue(installment_action(x,'微信分付',24)[1])
    def test_null_review_payment_is_unconfirmed_not_an_exception(self):
        for value in (None,False,'',[]):
            choice,problem=installment_action(
                {'stage':'REVIEW','review_payment':value},'微信分付',24)
            self.assertIsNone(choice)
            self.assertTrue(problem)
    def test_payment_heading_may_precede_installment_options(self):
        p=validate_preferences({**DEFAULTS,'payment':'支付宝','financing':'official',
                                'installment_option':'微信分付','installment_months':24})
        a=Attempt(p,variants(p)[0],0);a.bag_verified=True
        s=dict(stage='PAYMENT',fingerprint='heading',controls=[],fields=[])
        self.assertEqual(a.decide(s,0).kind,'wait')
        s.update(fingerprint='choices',controls=payment(selected=False)['controls'])
        self.assertEqual(a.decide(s,1).reason,'official_installment_payment_0')

    def test_missing_provider_deadline_is_not_reset_by_page_animation(self):
        p=validate_preferences({**DEFAULTS,'payment':'支付宝','financing':'official',
                                'installment_option':'微信分付','installment_months':24})
        a=Attempt(p,variants(p)[0],0);a.bag_verified=True
        for t in (0,5,10,14):
            self.assertEqual(a.decide(dict(stage='PAYMENT',fingerprint=str(t),controls=[],fields=[]),t).kind,'wait')
        self.assertEqual(a.decide(dict(stage='PAYMENT',fingerprint='15',controls=[],fields=[]),15).kind,'attention')
    def test_manual_modes_cannot_auto_submit(self):
        for change in ({'financing':'manual'},{'payment':'Apple Pay'},{'applecare':'manual'}):
            with self.assertRaises(ValueError):validate_preferences({**DEFAULTS,**change,'auto_submit_order':True})
    def test_each_plan_continues_to_review_without_submitting(self):
        for provider,terms in INSTALLMENT_PLANS.items():
            for months in terms:
                prefs=validate_preferences({**DEFAULTS,'model':'iphone-17','colors':['白色'],'capacities':['256GB'],
                    'fulfillment':'delivery','payment':'支付宝','financing':'official','installment_option':provider,'installment_months':months})
                a=Attempt(prefs,variants(prefs)[0],0)
                a.bag_verified=a.fulfillment_verified=a.contact_verified=True;a.method='delivery'
                s=payment(provider,months);s.update(fingerprint='payment',fields=[])
                s['controls'].append(dict(ref='next',label='检查订单',role='button',selected=False,disabled=False))
                d=a.decide(s,1);self.assertEqual((d.kind,d.ref),('click','next'))
                r=dict(stage='REVIEW',fingerprint='review',fields=[],controls=[],final_button=True,review_extras_match=True,
                    review_items=[dict(target_matches=True,quantity=1,method='delivery')],review_quantity=1,
                    review_target_matches=True,review_applecare=False,review_method='delivery',total=5999,
                    review_payment=dict(provider=provider,months=months,fee_percent=0))
                self.assertEqual(a.decide(r,2).kind,'ready')

class OrderTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)
        self.bridge=Bridge(data=self.path)
    def tearDown(self):self.temp.cleanup()
    def test_pending_survives_restart(self):
        self.bridge.order_guard.begin()
        other=Bridge(data=self.path)
        with self.assertRaises(ValueError):other.request({'op':'arm','confirmSubmission':True})
        with self.assertRaises(ValueError):other.request({'op':'rehearse'})
        self.assertTrue(other.status()['orderNeedsReview'])
    def test_ack_requires_user_confirmation(self):
        self.bridge.order_guard.begin()
        with self.assertRaises(ValueError):self.bridge.request({'op':'acknowledge_order'})
        self.bridge.request({'op':'acknowledge_order','confirmed':True})
        self.assertFalse(self.bridge.order_guard.blocked)
    def test_formal_arming_needs_separate_confirmation(self):
        self.bridge.plan['auto_submit_order']=True
        with self.assertRaises(ValueError):self.bridge.request({'op':'arm'})
        self.assertTrue(self.bridge.tasks.empty())
    def test_rehearsal_never_submits(self):
        s=self.bridge.service
        s.run_preferences={**DEFAULTS,'auto_submit_order':True}
        self.bridge.submission_confirmed=True
        self.bridge.automatic=False
        s.status['mode']='ready'
        with patch('worker.core.Service.tick'), patch.object(s,'submit_once') as submit:
            s.tick();submit.assert_not_called()
    def test_invalid_review_does_not_dispatch(self):
        s=self.bridge.service;self.bridge.cancel.clear()
        s.run_preferences={**DEFAULTS,'payment':'支付宝'};s.attention=Mock()
        s.attempt=Mock(target={},method='delivery');s.attempt.decide.return_value=Mock(kind='attention')
        s.browser=Mock();s.browser.call.return_value={'stage':'REVIEW'}
        s.submit_once()
        self.assertEqual(s.browser.call.call_count,1)
        self.assertFalse(self.bridge.order_guard.blocked)
    def test_transport_failure_is_durable_unknown(self):
        s=self.bridge.service;self.bridge.cancel.clear()
        s.run_preferences={**DEFAULTS,'payment':'支付宝'};s.attention=Mock()
        s.attempt=Mock(target={},method='delivery');s.attempt.decide.return_value=Mock(kind='ready')
        s.browser=Mock();s.browser.call.side_effect=[{'stage':'REVIEW','final_order_ref':'f','fingerprint':'x'},TimeoutError()]
        s.submit_once()
        self.assertTrue(self.bridge.order_guard.blocked)
        self.assertEqual(s.browser.call.call_count,2)
        s.submit_once();self.assertEqual(s.browser.call.call_count,2)
    def test_receipt_only_notifies_after_verified(self):
        s=self.bridge.service;self.bridge.cancel.clear();self.bridge.order_guard.begin()
        s.attempt=Mock(target={});s.submission_deadline=10**12
        s.browser=Mock();s.browser.call.return_value={'order_pending_payment':False}
        s.verify_order_tick();self.assertIsNone(self.bridge.notice)
        s.next_order_check=0;s.browser.call.return_value={'order_pending_payment':True}
        s.verify_order_tick()
        self.assertEqual(self.bridge.order_guard.record['status'],'awaiting_payment')
        self.assertEqual(self.bridge.notice['title'],'Apple 订单待支付')
    def test_ledger_contains_no_contact_or_order_data(self):
        self.bridge.order_guard.begin()
        self.assertEqual(set(self.bridge.order_guard.record),{'status','updated_at','nonce'})

if __name__=='__main__':unittest.main()

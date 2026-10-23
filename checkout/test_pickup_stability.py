import copy
import unittest
from datetime import date, datetime
from unittest.mock import patch
from worker import DEFAULTS
from checkout_engine import Attempt, variants, validate_preferences
from pickup_monitor import checkout_date_decision,dates_in

class PickupStabilityTests(unittest.TestCase):
    def attempt(self):
        p=copy.deepcopy(DEFAULTS);p.update(fulfillment='pickup',financing='full',payment='支付宝',pickup_dates=['2026-10-23','2026-10-24'])
        a=Attempt(p,variants(p)[0],0);a.bag_verified=True;a.started=0
        a.target['pickup_dates']=p['pickup_dates']
        return a
    def snapshot(self, fp='a'):
        return dict(stage='FULFILLMENT',fingerprint=fp,controls=[],fields=[],pickup_selected=True,store_selected=True,pickup_schedule=[])
    def date(self, label='10月23日', value='2026-10-23', selected=False):
        return dict(key='pickup_date',ref='date',kind='select',selected=selected,disabled=False,choices=[dict(ref='date',value=value,label=label,role='option',selected=selected,disabled=False)])
    def test_confirmed_store_id_does_not_reopen_search_or_clear_slot(self):
        a=self.attempt()
        s=self.snapshot()
        s.update(store_id_verified=True,pickup_date_selected=True,pickup_time_selected=False,
                 region_picker=dict(context='pickup',kind='tabs',open=False,matched=False,configured=True,
                                    reason='loading',next=dict(ref='search',key='region_pickup_open')),
                 pickup_schedule=[self.date(selected=True),dict(key='pickup_time',ref='slot',id='slot',kind='select',selected=False,disabled=False,
                    choices=[dict(ref='slot',value='23-19:15-19:30',label='19:15 – 19:30',role='option',selected=False,disabled=False)])])
        decision=a.decide(s,0)
        self.assertEqual(decision.key,'pickup_time')
        s.update(fingerprint='slot-readback',pickup_time_selected=True,
                 controls=[dict(ref='next',label='继续填写取货详情',role='button',selected=False,disabled=False)])
        s['pickup_schedule'][1]['selected']=True
        s['pickup_schedule'][1]['choices'][0]['selected']=True
        decision=a.decide(s,1)
        self.assertEqual(decision.ref,'next')
        self.assertTrue(a.fulfillment_verified)

    def test_search_region_guard_remains_without_exact_store_id_or_when_editor_open(self):
        for patch in ({'store_id_verified':False},{'store_selection_conflict':True},{'pickup_region_open':True}):
            a=self.attempt();s=self.snapshot()
            s.update(store_id_verified=True,region_picker=dict(context='pickup',kind='tabs',open=False,matched=False,configured=True,
                     reason='loading',next=dict(ref='search',key='region_pickup_open')))
            s.update(patch)
            self.assertEqual(a.decide(s,0).key,'region_pickup_open')

    def test_date_panel_can_arrive_after_store_without_immediate_attention(self):
        a=self.attempt();s=self.snapshot()
        self.assertEqual(a.decide(s,0).kind,'wait')
        self.assertEqual(a.decide(s,4).kind,'wait')
        s.update(fingerprint='date-ready',pickup_schedule=[self.date()])
        self.assertEqual(a.decide(s,7).key,'pickup_date')
    def test_current_snapshot_progress_precedes_stall_timeout(self):
        a=self.attempt();s=self.snapshot();s.update(pickup_schedule=[self.date()],pickup_date_selected=False)
        self.assertEqual(a.decide(s,0).kind,'set')
        s.update(fingerprint='new-date',pickup_date_selected=True,pickup_time_selected=True)
        s['pickup_schedule'][0].update(selected=True)
        s['pickup_schedule'][0]['choices'][0]['selected']=True
        s['controls']=[dict(ref='next',label='继续填写取货详情',role='button',selected=False,disabled=False)]
        self.assertEqual(a.decide(s,31).ref,'next')
    def test_same_slot_on_a_later_date_has_a_separate_retry_budget(self):
        a=self.attempt();s=self.snapshot()
        def arrange(date):
            p=self.date(value=date,selected=True);p['choices'][0]['label']=date
            s.update(fingerprint=date,pickup_schedule=[p,dict(key='pickup_time',ref='slot',kind='select',selected=False,disabled=False,choices=[dict(ref='slot',value='10:00',label='10:00 – 10:15',role='option',selected=False,disabled=False)])],pickup_date_selected=True)
        arrange('2026-10-23')
        self.assertEqual(a.decide(s,0).key,'pickup_time')
        self.assertEqual(a.decide(s,5).key,'pickup_time')
        a.pickup_exhausted_dates.add('2026-10-23')
        arrange('2026-10-24')
        self.assertEqual(a.decide(s,6).key,'pickup_time')
    def test_loading_downstream_time_panel_cannot_deadlock_date_choice(self):
        a=self.attempt();s=self.snapshot();s.update(bag_busy=True,pickup_schedule=[self.date()])
        self.assertEqual(a.decide(s,0).key,'pickup_date')
    def test_readonly_checkouts_without_date_panel_do_not_wait_forever(self):
        a=self.attempt();s=self.snapshot()
        self.assertEqual(a.decide(s,0).kind,'wait')
        self.assertEqual(a.decide(s,12).kind,'attention')
    @patch('pickup_monitor.datetime', wraps=datetime)
    def test_earliest_date_ignores_stale_saved_dates_and_orders_actual_dates(self, clock):
        clock.now.return_value=datetime(2026,10,20)
        s=self.snapshot();p=self.date(label='October22',value='22')
        p['choices'] += [dict(ref='date',value='20',label='October20',role='option',selected=False,disabled=False),dict(ref='date',value='21',label='October21',role='option',selected=False,disabled=False)]
        s['pickup_schedule']=[p]
        self.assertEqual(checkout_date_decision(s,['2026-10-23'],policy='earliest').value,'20')
        self.assertEqual(checkout_date_decision(s,[],exhausted={'20'},policy='earliest').value,'21')
    def test_earliest_policy_accepts_no_fixed_dates_but_still_confirms_date(self):
        a=self.attempt();p=validate_preferences({**a.preferences,'pickup_date_policy':'earliest','pickup_dates':[]})
        a=Attempt(p,variants(p)[0],0);a.bag_verified=True
        s=self.snapshot();s.update(pickup_schedule=[self.date(value='2026-10-23',selected=True)],pickup_date_selected=True,pickup_time_selected=True)
        s['controls']=[dict(ref='next',label='继续填写取货详情',role='button',selected=False,disabled=False)]
        self.assertEqual(a.decide(s,0).ref,'next')
        self.assertEqual(a.pickup_confirmed_dates,['2026-10-23'])
    def test_earliest_policy_still_rejects_unrecognizable_dates(self):
        s=self.snapshot();s['pickup_schedule']=[self.date(label='星期五',value='opaque')]
        self.assertEqual(checkout_date_decision(s,[],policy='earliest').kind,'attention')
    def test_earliest_time_is_ordered_by_clock_not_dom_order(self):
        a=self.attempt();a.preferences['pickup_date_policy']='earliest'
        s=self.snapshot();s.update(pickup_date_selected=True,pickup_schedule=[self.date(selected=True),dict(key='pickup_time',ref='time',kind='select',selected=False,disabled=False,choices=[dict(ref='time',value='late',label='16:00 – 16:15',role='option',selected=False,disabled=False),dict(ref='time',value='early',label='10:00 – 10:15',role='option',selected=False,disabled=False)])])
        self.assertEqual(a.decide(s,0).value,'early')
    def test_earliest_still_requires_review_date_to_match_chosen_date(self):
        for actual,expected in [('2026-10-20','ready'),('2026-10-21','attention')]:
            a=self.attempt();a.preferences['pickup_date_policy']='earliest'
            a.fulfillment_verified=a.contact_verified=a.payment_verified=True
            a.pickup_confirmed_dates=['2026-10-20']
            s=dict(stage='REVIEW',fingerprint='review',controls=[],fields=[],final_button=True,review_extras_match=True,review_items=[dict(target_matches=True,quantity=1,method='pickup',store_matches=True,pickup_dates=[actual])],review_target_matches=True,review_quantity=1,review_method='pickup',review_store_matches=True,review_applecare=False,total=9999,review_full_payment='支付宝',errors=False)
            self.assertEqual(a.decide(s,0).kind,expected)
    @patch('pickup_monitor.datetime', wraps=datetime)
    def test_live_english_month_labels_and_day_only_values(self, clock):
        clock.now.return_value=datetime(2026,10,20)
        for label in ('October20','Oct 20','OCT. 20th','October 20, 2026'):
            self.assertEqual(dates_in(label,today=date(2026,10,20)),['2026-10-20'])
        self.assertEqual(dates_in('20',today=date(2026,10,20)),[])
        s=self.snapshot();s['pickup_schedule']=[self.date(label='October20',value='20',selected=True)]
        self.assertIsNone(checkout_date_decision(s,['2026-10-20']))
        self.assertEqual(checkout_date_decision(s,['2026-10-23']).kind,'sold_out')
    def test_official_deferred_date_only_accepts_same_attempt_verified_earliest_pickup(self):
        for policy, verified, dates, expected in [
            ('earliest', True, ['2026-10-20'], 'ready'),
            ('specified', True, ['2026-10-23'], 'attention'),
            ('earliest', False, ['2026-10-20'], 'attention'),
            ('earliest', True, [], 'attention'),
        ]:
            with self.subTest(policy=policy, verified=verified, dates=dates):
                a=self.attempt()
                a.preferences['pickup_date_policy']=policy
                a.target['store_id']='R683'
                a.bag_verified=a.contact_verified=a.payment_verified=True
                a.fulfillment_verified=verified
                a.pickup_confirmed_dates=dates
                s=dict(stage='REVIEW',fingerprint='deferred',controls=[],fields=[],
                       final_button=True,review_extras_match=True,
                       review_items=[dict(target_matches=True,quantity=1,method='pickup',store_matches=False,pickup_dates=[])],
                       review_target_matches=True,review_quantity=1,review_method='pickup',
                       review_store_matches=False,review_pickup_deferred=True,
                       review_applecare=False,total=9999,review_full_payment='支付宝',errors=False)
                self.assertEqual(a.decide(s,0).kind,expected)
    def test_skipped_saved_steps_are_revisited_in_order_without_replaying_edits(self):
        a=self.attempt()
        s=dict(stage='REVIEW',fingerprint='r',final_button=True,fields=[],
               review_target_matches=True,review_extras_match=True,review_quantity=1,
               review_edit_refs={'fulfillment':'f','contact':'c','payment':'p'},
               controls=[dict(ref=ref,label='更改',role='link') for ref in ('f','c','p')])
        self.assertEqual(a.decide(s,0).reason,'review_edit_fulfillment')
        self.assertEqual(a.decide(s,6).kind,'wait')
        a.fulfillment_verified=True;s['fingerprint']='f-returned'
        self.assertEqual(a.decide(s,7).reason,'review_edit_contact')
        a.contact_verified=True;s['fingerprint']='c-returned'
        self.assertEqual(a.decide(s,8).reason,'review_edit_payment')
        self.assertEqual(a.sent['review_edit_fulfillment'],1)
    def test_payment_transition_does_not_erase_verified_choice(self):
        a=self.attempt()
        s=dict(stage='PAYMENT',fingerprint='payment',fields=[],controls=[
            dict(ref='ali',label='支付宝',role='radio',group='payment',selected=True),
            dict(ref='next',label='查看订单',role='button')])
        self.assertEqual(a.decide(s,0).reason,'continue_PAYMENT')
        self.assertTrue(a.payment_verified)
        s.update(fingerprint='departing',controls=[])
        self.assertEqual(a.decide(s,1).kind,'wait')
        self.assertTrue(a.payment_verified)
    def test_departing_pickup_never_reselects_reset_schedule(self):
        a=self.attempt()
        s=self.snapshot()
        s.update(pickup_schedule=[self.date()],pickup_date_selected=False)
        self.assertEqual(a.decide(s,0).kind,'set')
        self.assertEqual(a.decide(s,5).kind,'set')
        s.update(fingerprint='selected',pickup_date_selected=True,pickup_time_selected=True)
        s['pickup_schedule'][0]=self.date(selected=True)
        s['controls']=[dict(ref='next',label='继续填写取货详情',role='button')]
        self.assertEqual(a.decide(s,6).reason,'continue_FULFILLMENT')
        # Apple's outgoing component resets before its heading disappears.
        # Re-entering date selection here used to exhaust its retry budget.
        s.update(fingerprint='outgoing-reset',pickup_date_selected=False)
        s['pickup_schedule'][0]=self.date()
        self.assertEqual(a.decide(s,12).kind,'wait')
        contact=dict(stage='CONTACT',fingerprint='contact',controls=[],fields=[
            dict(ref='name',key='family_name',configured=True,editable=True,
                 filled=False,matches=False,required=True,invalid=True)])
        self.assertEqual(a.decide(contact,13).reason,'fill:family_name')
        self.assertTrue(a.fulfillment_verified)

    def test_departing_contact_does_not_refill_reset_fields(self):
        a=self.attempt()
        fields=[dict(ref=k,key=k,configured=True,editable=True,filled=True,matches=True)
                for k in ('phone','email')]
        s=dict(stage='CONTACT',fingerprint='contact',fields=fields,
               controls=[dict(ref='next',label='继续选择付款方式',role='button')])
        self.assertEqual(a.decide(s,0).reason,'continue_CONTACT')
        s.update(fingerprint='reset')
        fields[0].update(filled=False,matches=False)
        self.assertEqual(a.decide(s,6).kind,'wait')
        self.assertTrue(a.contact_verified)

    def test_departure_deadline_survives_unrelated_page_updates(self):
        a=self.attempt()
        s=dict(stage='PAYMENT',fingerprint='p',fields=[],controls=[
            dict(ref='ali',label='支付宝',role='radio',group='payment',selected=True),
            dict(ref='next',label='查看订单',role='button')])
        self.assertEqual(a.decide(s,0).reason,'continue_PAYMENT')
        for t in (1,10,20,29):
            s['fingerprint']=str(t)
            self.assertEqual(a.decide(s,t).kind,'wait')
        s['fingerprint']='timeout'
        d=a.decide(s,30)
        self.assertEqual(d.kind,'attention')
        self.assertIn('切换',d.reason)

    def test_departure_still_stops_on_server_validation_error(self):
        a=self.attempt()
        s=dict(stage='CONTACT',fingerprint='c',fields=[
            dict(ref=k,key=k,configured=True,filled=True,matches=True)
            for k in ('phone','email')],controls=[
            dict(ref='next',label='继续选择付款方式',role='button')])
        self.assertEqual(a.decide(s,0).reason,'continue_CONTACT')
        s.update(fingerprint='error',errors=True)
        self.assertEqual(a.decide(s,1).kind,'attention')
    def test_english_dates_keep_year_rollover_and_ambiguity_checks(self):
        self.assertEqual(dates_in('January2',today=date(2026,12,31)),['2027-01-02'])
        self.assertEqual(dates_in('February30',today=date(2026,1,1)),[])
        self.assertEqual(dates_in('October20 October21',today=date(2026,10,20)),['2026-10-20','2026-10-21'])
    def test_unparsed_date_is_unknown_instead_of_out_of_stock(self):
        s=self.snapshot();s['pickup_schedule']=[self.date(label='星期五',value='opaque-day')]
        self.assertEqual(checkout_date_decision(s,['2026-10-23']).kind,'attention')

if __name__=='__main__':unittest.main()

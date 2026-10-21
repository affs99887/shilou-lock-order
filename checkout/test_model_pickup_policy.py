"""Mixed-model monitoring must not broaden Duo's authorized date range."""
import copy
import unittest

from checkout_engine import DEFAULTS, CATALOG, Attempt, variants, validate_preferences
from pickup_monitor import checkout_date_decision
from worker import plan_for_hit
from regional_checkout import catalog
from regional_service import decide

DATES = ['2026-10-23', '2026-10-24', '2026-10-25']

class ModelPickupPolicyTests(unittest.TestCase):
    def setUp(self):
        self.plan = validate_preferences(dict(DEFAULTS, pickup_date_policy='by_model', pickup_dates=DATES))

    def hit(self, model):
        m = CATALOG[model]
        return dict(target=dict(locale='zh_CN', storeNumber='R575', partNumber='TESTCH/A'),
                    availability=dict(kind='in_stock'), lastCheckedMs=100000,
                    product=dict(partNumber='TESTCH/A', family=model.replace('-', ''), category='iphone',
                                 color=m['colors'][0], capacity=m['capacities'][0]))

    def schedule(self):
        return dict(stage='FULFILLMENT', pickup_schedule=[dict(key='pickup_date', ref='d', kind='select', disabled=False,
            choices=[dict(ref='d', value=d, label=d, selected=False, disabled=False) for d in ['2026-10-20', '2026-10-25', '2026-10-23']])])

    def test_actual_hit_resolves_rule_and_never_mutates_saved_dates(self):
        before = copy.deepcopy(self.plan)
        for model in ['iphone-duo', 'iphone-18-pro', 'iphone-18-pro-max', 'iphone-17', 'iphone-duo']:
            with self.subTest(model=model):
                p = plan_for_hit(self.plan, self.hit(model), now=100001)
                expected = 'specified' if model == 'iphone-duo' else 'earliest'
                self.assertEqual(p['pickup_date_policy'], expected)
                decision = checkout_date_decision(self.schedule(), p['pickup_dates'], policy=p['pickup_date_policy'])
                self.assertEqual(decision.value, '2026-10-23' if model == 'iphone-duo' else '2026-10-20')
                self.assertEqual(self.plan, before)

    def test_duo_with_only_outside_dates_does_not_choose_earlier_or_later(self):
        p = plan_for_hit(self.plan, self.hit('iphone-duo'), now=100001)
        s = self.schedule()
        s['pickup_schedule'][0]['choices'] = [dict(ref='d', value=d, label=d, selected=False, disabled=False)
                                                   for d in ['2026-10-20', '2026-10-26']]
        self.assertEqual(checkout_date_decision(s, p['pickup_dates'], policy=p['pickup_date_policy']).kind, 'sold_out')

    def test_direct_attempt_also_uses_target_model_not_saved_duo(self):
        p = dict(self.plan, model='iphone-18-pro', colors=['冰川蓝色'], capacities=['256GB'])
        a = Attempt(p, variants(p)[0], 0)
        self.assertEqual(a.preferences['pickup_date_policy'], 'earliest')
        self.assertEqual(a.target['pickup_date_policy'], 'earliest')
        self.assertEqual(p['pickup_date_policy'], 'by_model')

    def test_legacy_settings_keep_their_exact_policy(self):
        for policy in ['specified', 'earliest']:
            p = plan_for_hit(dict(self.plan, pickup_date_policy=policy), self.hit('iphone-duo'), now=100001)
            self.assertEqual(p['pickup_date_policy'], policy)
        with self.assertRaises(ValueError):
            validate_preferences(dict(self.plan, pickup_dates=[]))

    def foreign(self, duo=False):
        locale='en_SG'
        c=catalog()[locale]
        part,item=next((k,v) for k,v in c['products'].items()
                       if (v['dimensions'].get('familyType') == 'iphoneduo') == duo)
        store=next(iter(c['stores']))
        h=dict(target=dict(locale=locale,storeNumber=store,partNumber=part),
               availability=dict(kind='in_stock'),lastCheckedMs=100000,
               product=dict(partNumber=part,family=item['dimensions']['familyType'],category='iphone'))
        p=dict(self.plan,checkout_locale=locale,financing='manual',payment='官网手动选择',auto_submit_order=False)
        return plan_for_hit(p,h,now=100001)

    def test_international_catalog_model_uses_same_rules_and_preserves_payment_boundary(self):
        for duo in [True, False]:
            p=self.foreign(duo)
            self.assertEqual(p['pickup_date_policy'], 'specified' if duo else 'earliest')
            self.assertEqual(p['pickup_dates'], DATES)
            self.assertFalse(p['auto_submit_order'])
            self.assertEqual(decide(dict(stage='PAYMENT'),p,{})['kind'],'handoff')

    def test_international_earliest_date_and_time_override_later_selected_options(self):
        p=self.foreign()
        store=dict(group='store',ref='store',selected=True,matches=True)
        date=dict(group='date',ref='d',value='2026-10-23',options=[dict(value=d,label=d) for d in ['2026-10-23','2026-10-20']])
        time=dict(group='time',ref='t',value='late',options=[dict(value='late',label='16:00–16:15'),dict(value='early',label='10:00–10:15')])
        s=dict(stage='FULFILLMENT',controls=[store,date,time])
        self.assertEqual(decide(s,p,{})['value'],'2026-10-20')
        date['value']='2026-10-20'
        self.assertEqual(decide(s,p,{})['value'],'early')
        time['options'][0]['label']='3:00 PM – 3:15 PM'
        time['options'][1]['label']='10:00 AM – 10:15 AM'
        self.assertEqual(decide(s,p,{})['value'],'early')
        date['options'][0]=dict(value='opaque',label='Tomorrow')
        self.assertEqual(decide(s,p,{})['kind'],'handoff')

    def test_international_duo_rejects_available_date_outside_range(self):
        p=self.foreign(True)
        s=dict(stage='FULFILLMENT',controls=[dict(group='store',ref='s',selected=True,matches=True),
              dict(group='date',ref='d',value='2026-10-20',selected=True)])
        self.assertEqual(decide(s,p,{})['kind'],'handoff')

if __name__=='__main__':
    unittest.main()

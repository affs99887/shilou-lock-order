"""Product configurators may temporarily disable dependent options."""
import unittest
from checkout_engine import Attempt, DEFAULTS, variants

class ProductLoadingTests(unittest.TestCase):
    def make(self):
        p=dict(DEFAULTS,model='iphone-18-pro',colors=['银色'],capacities=['512GB'])
        a=Attempt(p,variants(p)[0],0)
        s=dict(stage='PRODUCT',fingerprint='loading',product_model='iPhone 18 Pro',controls=[
            dict(ref='m',label='iPhone 18 Pro',role='radio',group='model',selected=True,disabled=False),
            dict(ref='c',label='银色',role='radio',selected=False,disabled=True),
            dict(ref='s',label='512GB',role='radio',selected=False,disabled=True)])
        return a,s

    def test_dependent_color_waits_more_than_five_seconds_then_continues(self):
        a,s=self.make()
        self.assertEqual(a.decide(s,0).kind,'wait')
        self.assertEqual(a.decide(s,8).kind,'wait')
        s['controls'][1]['disabled']=False;s['fingerprint']='color-ready'
        self.assertEqual(a.decide(s,9).reason,'color')
        s['controls'][1]['selected']=True;s['fingerprint']='color-selected'
        self.assertEqual(a.decide(s,10).kind,'wait')
        s['controls'][2]['disabled']=False;s['fingerprint']='capacity-ready'
        self.assertEqual(a.decide(s,18).reason,'capacity')
        self.assertIsNone(a.terminal)

    def test_loading_choice_times_out_as_attention_not_stock(self):
        a,s=self.make()
        self.assertEqual(a.decide(s,0).kind,'wait')
        s['fingerprint']='unrelated-animation'
        d=a.decide(s,31)
        self.assertEqual(d.kind,'attention')
        self.assertIn('未认定缺货',d.reason)

    def test_temporarily_disabled_model_waits_without_a_false_mismatch(self):
        a,s=self.make();s['controls'][0].update(selected=False,disabled=True)
        self.assertEqual(a.decide(s,0).kind,'wait')
        s['controls'][0]['disabled']=False;s['fingerprint']='model-ready'
        self.assertEqual(a.decide(s,4).reason,'model')

    def test_explicit_unavailable_variant_still_ends_attempt(self):
        a,s=self.make();s['variant_unavailable']=True
        self.assertEqual(a.decide(s,0).kind,'sold_out')

if __name__=='__main__':unittest.main()

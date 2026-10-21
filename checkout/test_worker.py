import copy
import tempfile
import unittest
import threading
import json
from pathlib import Path
from unittest.mock import Mock, patch
from worker import Bridge, CancellableSafari, plan_for_hit, DEFAULTS, STORES

def hit():
    return {'target': {'locale': 'zh_CN', 'partNumber': 'TESTCH/A', 'storeNumber': STORES[0]['id']},
            'availability': {'kind': 'in_stock'}, 'lastCheckedMs': 100000,
            'product': {'partNumber': 'TESTCH/A', 'family': 'iphone17', 'category': 'iphone',
                        'capacity': '256GB', 'color': '白色'}}

class CandidateTests(unittest.TestCase):
    def test_manual_selection_keeps_confirmed_stock_and_exact_mapping_without_automatic_age_gate(self):
        self.assertEqual(plan_for_hit(DEFAULTS,hit(),now=999999,manual=True)['store_id'],STORES[0]['id'])
        for state in ('unknown','out_of_stock'):
            h=hit();h['availability']['kind']=state
            with self.assertRaises(ValueError):plan_for_hit(DEFAULTS,h,manual=True)
        h=hit();h['target']['storeNumber']='INVALID'
        with self.assertRaises(ValueError):plan_for_hit(DEFAULTS,h,manual=True)
    def test_rehearsal_does_not_require_stock_but_validates_configuration(self):
        h = hit()
        h.pop('availability')
        h.pop('lastCheckedMs')
        self.assertEqual(plan_for_hit(DEFAULTS, h, rehearsal=True)['model'], 'iphone-17')
        with self.assertRaises(ValueError):
            plan_for_hit(DEFAULTS, h)
        h['target']['storeNumber'] = 'INVALID'
        with self.assertRaises(ValueError):
            plan_for_hit(DEFAULTS, h, rehearsal=True)
    def test_rust_wire_fixture(self):
        raw=json.loads((Path(__file__).parent/'fixtures/apw-hit.json').read_text())
        p=plan_for_hit(DEFAULTS,raw,100001)
        self.assertEqual(p['model'],'iphone-17')
        self.assertEqual(p['store_id'],'R683')

    def test_exact_target(self):
        p = plan_for_hit(DEFAULTS, hit(), now=100001)
        self.assertEqual((p['model'], p['colors'], p['capacities'], p['store_id']),
                         ('iphone-17', ['白色'], ['256GB'], STORES[0]['id']))
        self.assertEqual(p['pickup_dates'], DEFAULTS['pickup_dates'])
        self.assertEqual(p['max_price'], DEFAULTS['max_price'])

    def test_unknown_and_sold_out_never_trigger(self):
        for state in ('unknown', 'out_of_stock'):
            h=hit(); h['availability']['kind']=state
            with self.assertRaises(ValueError):plan_for_hit(DEFAULTS,h,100001)

    def test_age_and_future_rejected(self):
        for stamp in (None, True, 0, 100001, 84999):
            h=hit();h['lastCheckedMs']=stamp
            with self.assertRaises(ValueError):plan_for_hit(DEFAULTS,h,100000)

    def test_unmapped_product_store_region_rejected(self):
        for field,value in [('locale','en-US'),('storeNumber','R_FAKE'),('partNumber','OTHER')]:
            h=hit();h['target'][field]=value
            with self.assertRaises(ValueError):plan_for_hit(DEFAULTS,h,100001)
        for field,value in [('family','iphone17e'),('color','不存在的颜色'),('capacity','8TB'),('category','mac')]:
            h=hit();h['product'][field]=value
            with self.assertRaises(ValueError):plan_for_hit(DEFAULTS,h,100001)

    def test_cap_and_budget_not_overridden(self):
        for field,value in [('quantity',3),('max_price',0)]:
            p=copy.deepcopy(DEFAULTS);p[field]=value
            with self.assertRaises(ValueError):plan_for_hit(p,hit(),100001)

class BridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.b=Bridge(data=Path(self.temp.name))
    def tearDown(self):self.temp.cleanup()

    def test_default_stopped_no_profile_read(self):
        self.assertFalse(self.b.status()['armed'])
        self.assertFalse(self.b.status()['submissionEnabled'])
        self.assertTrue(self.b.cancel.is_set())

    def test_manual_purchase_is_noop_when_armed_busy_or_stopping(self):
        for field in ('armed','reserved','stopping'):
            setattr(self.b,field,True)
            self.b.request({'op':'purchase','hit':hit()})
            self.assertTrue(self.b.tasks.empty())
            setattr(self.b,field,False)

    def test_concurrent_manual_purchases_reserve_only_one_exact_target(self):
        gate=threading.Barrier(8)
        def purchase():
            gate.wait();self.b.request({'op':'purchase','hit':hit()})
        threads=[threading.Thread(target=purchase) for _ in range(8)]
        for t in threads:t.start()
        for t in threads:t.join(timeout=2)
        self.assertTrue(all(not t.is_alive() for t in threads))
        self.assertEqual(self.b.tasks.qsize(),1)
        _,op,data=self.b.tasks.get_nowait()
        self.assertEqual(op,'purchase')
        self.assertEqual(data['hit']['target'],hit()['target'])

    def test_invalid_manual_purchase_does_not_leave_checkout_reserved(self):
        h=hit();h['availability']['kind']='unknown'
        with self.assertRaises(ValueError):self.b.request({'op':'purchase','hit':h})
        self.assertFalse(self.b.reserved)
        self.assertTrue(self.b.tasks.empty())

    def test_manual_purchase_preserves_order_guard_and_submission_permission(self):
        self.b.plan['auto_submit_order']=True
        with self.assertRaises(ValueError):self.b.request({'op':'purchase','hit':hit()})
        self.assertFalse(self.b.reserved)
        self.b.order_guard.begin()
        with self.assertRaises(ValueError):self.b.request({'op':'purchase','hit':hit(),'confirmSubmission':True})
        self.assertTrue(self.b.tasks.empty())

    def test_offer_disarmed_no_task(self):
        self.b.request({'op':'offer','hits':[hit()]})
        self.assertTrue(self.b.tasks.empty())

    @patch('worker.time.time',return_value=100)
    def test_one_candidate_only(self, _):
        self.b.armed=True
        self.b.request({'op':'offer','hits':[hit(),hit()]})
        self.b.request({'op':'offer','hits':[hit()]})
        self.assertEqual(self.b.tasks.qsize(),1)
        self.assertTrue(self.b.reserved)

    @patch('worker.time.time',return_value=100)
    def test_simultaneous_same_product_hits_keep_one_reserved_store(self, _):
        self.b.armed=True
        hits=[hit(),hit()]
        hits[1]['target']['storeNumber']=STORES[1]['id']
        gate=threading.Barrier(8)
        def offer():
            gate.wait()
            self.b.request({'op':'offer','hits':copy.deepcopy(hits)})
        threads=[threading.Thread(target=offer) for _ in range(8)]
        for t in threads:t.start()
        for t in threads:t.join(timeout=2)
        self.assertTrue(all(not t.is_alive() for t in threads))
        self.assertEqual(self.b.tasks.qsize(),1)
        _,op,selected=self.b.tasks.get_nowait()
        self.assertEqual(op,'start')
        self.assertEqual(selected['target']['storeNumber'],STORES[0]['id'])
        self.b.request({'op':'offer','hits':list(reversed(hits))})
        self.assertTrue(self.b.tasks.empty())

    def test_exception_record_omits_message_contacts_and_paths(self):
        self.b.service.status['stage']='REVIEW'
        try:
            raise KeyError('private-contact@example.invalid')
        except KeyError as exc:
            code,kind,stage=self.b.record_exception(exc)
        data=json.loads((Path(self.temp.name)/'checkout-error.json').read_text())
        self.assertEqual((data['code'],kind,stage),(code,'KeyError','REVIEW'))
        self.assertNotIn('private',json.dumps(data))
        self.assertNotIn(self.temp.name,json.dumps(data))

    @patch('worker.time.time',return_value=100)
    def test_stop_cancels_queued_generation(self, _):
        self.b.armed=True
        self.b.request({'op':'offer','hits':[hit()]})
        epoch,_,_=self.b.tasks.get_nowait()
        self.b.request({'op':'stop'})
        self.assertNotEqual(epoch,self.b.generation)
        self.assertTrue(self.b.stopping)
        self.assertTrue(self.b.cancel.is_set())

    def test_no_mid_checkout_changes(self):
        self.b.reserved=True
        with self.assertRaises(ValueError):self.b.request({'op':'configure','plan':DEFAULTS})
        with self.assertRaises(ValueError):self.b.request({'op':'rehearse','hit':hit()})

    def test_stop_before_browser_blocks_even_direct_navigation(self):
        browser=CancellableSafari(self.b)
        with patch('checkout_service.subprocess.run') as run:
            with self.assertRaises(RuntimeError):browser.script('anything')
            run.assert_not_called()

    def test_save_and_reload_never_autoarms(self):
        self.b.request({'op':'configure','plan':DEFAULTS})
        reloaded=Bridge(data=Path(self.temp.name))
        self.assertFalse(reloaded.armed)
        self.assertEqual(reloaded.plan['quantity'],DEFAULTS['quantity'])

    def test_corrupt_preferences_preserved(self):
        path=Path(self.temp.name)/'checkout.json'
        path.write_text('{broken')
        with self.assertRaises(ValueError):Bridge(data=Path(self.temp.name))
        self.assertEqual(path.read_text(),'{broken')

    def test_no_arbitrary_commands(self):
        with self.assertRaises(ValueError):self.b.request({'op':'shell','cmd':'anything'})

    def test_monitor_open_is_blocked_by_any_local_checkout_state(self):
        for key in ('armed', 'reserved', 'stopping'):
            setattr(self.b, key, True)
            with patch.object(self.b, 'acquire') as acquire:
                self.assertFalse(self.b.request({'op':'open_bag_if_idle','locale':'zh_CN'})['opened'])
                acquire.assert_not_called()
            setattr(self.b, key, False)

    def test_other_process_checkout_lock_blocks_monitor_open(self):
        with patch('worker.SHARED_LOCK', Path(self.temp.name)/'shared.lock'), \
                patch('worker.core.subprocess.run') as opened, \
                patch('worker.os.startfile', create=True) as win_opened:
            other = Bridge(data=Path(self.temp.name)/'other')
            other.acquire()
            try:
                self.assertFalse(self.b.request({'op':'open_bag_if_idle','locale':'zh_CN'})['opened'])
                opened.assert_not_called()
                win_opened.assert_not_called()
            finally:
                other.release()

    def test_monitor_open_keeps_shared_lock_during_dispatch(self):
        with patch('worker.SHARED_LOCK', Path(self.temp.name)/'shared.lock'):
            def opened(*args, **kw):
                other = Bridge(data=Path(self.temp.name)/'other')
                with self.assertRaises(ValueError):
                    other.acquire()
                self.assertIn(args[0], (['open','https://www.apple.com.cn/shop/bag'],
                                        'https://www.apple.com.cn/shop/bag'))
            with patch('worker.core.subprocess.run', side_effect=opened), \
                    patch('worker.os.startfile', side_effect=opened, create=True):
                self.assertTrue(self.b.request({'op':'open_bag_if_idle','locale':'zh_CN'})['opened'])
            self.assertIsNone(self.b.file_lock)

class FakeCheckout:
    outcome = 'sold_out'
    def __init__(self, owner, plan):
        self.browser = Mock()
        self.owner=owner;self.lock=threading.RLock();self.preferences=plan
        self.active=False;self.login_watch=False;self.region_watch=False
        self.status={'mode':'stopped','stage':'IDLE'}
    def command(self, name):
        self.active=name=='rehearse'
        self.status['mode']='running' if self.active else 'stopped'
    def timing(self):return {'total_seconds':0}
    def update(self, **kw):self.status.update(kw)
    def attention(self, reason):self.status.update(mode='attention',reason=reason)
    def tick(self):
        self.active=False
        self.status['mode']='ready' if self.outcome=='ready' else 'attention'
        self.owner.retryable_end=self.outcome=='sold_out'
        self.owner.closed=True

class RecoveryTests(unittest.TestCase):
    @patch('worker.core.normalized_profile', return_value={})
    @patch.object(Bridge, 'acquire')
    def test_manual_purchase_runs_saved_plan_for_selected_target(self, *_):
        with tempfile.TemporaryDirectory() as d:
            b=Bridge(FakeCheckout,data=Path(d));b.plan['auto_submit_order']=True
            b.request({'op':'purchase','hit':hit(),'confirmSubmission':True});b.run()
            self.assertFalse(b.error)
            self.assertTrue(b.automatic)
            self.assertTrue(b.submission_confirmed)
            self.assertEqual(b.service.preferences['store_id'],hit()['target']['storeNumber'])
    @patch('worker.core.normalized_profile', return_value={})
    @patch.object(Bridge, 'acquire')
    def test_user_rehearsal_starts_without_a_monitor_snapshot_and_never_submits(self, *_):
        with tempfile.TemporaryDirectory() as d:
            b = Bridge(FakeCheckout, data=Path(d))
            b.plan['auto_submit_order'] = True
            b.submission_confirmed = True
            h = hit()
            h.pop('availability')
            h.pop('lastCheckedMs')
            b.request({'op':'rehearse','hit':h})
            self.assertFalse(b.submission_confirmed)
            b.run()
            self.assertFalse(b.error)
            self.assertFalse(b.automatic)
            self.assertIsNotNone(b.current_key)
            self.assertFalse(b.status()['submissionEnabled'])
    @patch('worker.core.normalized_profile',return_value={})
    @patch('worker.time.time',return_value=100)
    @patch.object(Bridge,'acquire')
    def test_definite_stock_loss_can_choose_next_fresh_hit(self, *_):
        with tempfile.TemporaryDirectory() as d:
            b=Bridge(FakeCheckout,data=Path(d));b.armed=True
            b.request({'op':'offer','hits':[hit()]});b.run()
            self.assertTrue(b.armed)
            self.assertFalse(b.reserved)
            self.assertIn(('TESTCH/A',STORES[0]['id']),b.avoid_until)
            b.request({'op':'offer','hits':[hit()]})
            self.assertTrue(b.tasks.empty())
            other=hit();other['target']['storeNumber']=STORES[1]['id']
            b.request({'op':'offer','hits':[other]})
            self.assertEqual(b.tasks.qsize(),1)

    @patch('worker.core.normalized_profile',return_value={})
    @patch('worker.time.time',return_value=100)
    @patch.object(Bridge,'acquire')
    def test_unknown_or_ready_never_automatic_switch(self, *_):
        for outcome in ('unknown','ready'):
            with tempfile.TemporaryDirectory() as d:
                b=Bridge(FakeCheckout,data=Path(d));b.service.outcome=outcome;b.armed=True
                b.request({'op':'offer','hits':[hit()]});b.run()
                self.assertFalse(b.armed)
                self.assertTrue(b.reserved)
                b.request({'op':'offer','hits':[hit()]})
                self.assertTrue(b.tasks.empty())

if __name__=='__main__':unittest.main()

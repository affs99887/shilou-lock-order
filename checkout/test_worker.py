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

def community_hit():
    h = hit()
    h.pop('availability'); h.pop('lastCheckedMs')
    h.update(source='sunbelife_aggregate', reportedAtMs=90000, receivedAtMs=100000)
    return h

class CandidateTests(unittest.TestCase):
    def test_community_hint_preserves_mapping_and_does_not_claim_confirmed_stock(self):
        h = community_hit()
        self.assertEqual(plan_for_hit(DEFAULTS, h, now=100001)['store_id'], STORES[0]['id'])
        self.assertNotIn('availability', h)
        for field, value in [('source','unknown'), ('reportedAtMs', -100000), ('reportedAtMs',100001),
                             ('reportedAtMs',True), ('receivedAtMs',84999), ('receivedAtMs',100001), ('receivedAtMs',None)]:
            bad=copy.deepcopy(h); bad[field]=value
            with self.subTest(field=field,value=value), self.assertRaises(ValueError):
                plan_for_hit(DEFAULTS,bad,now=100000)
        h['target']['partNumber']='WRONG'
        with self.assertRaises(ValueError): plan_for_hit(DEFAULTS,h,now=100000)
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
    def test_community_offer_requires_arming_and_shares_the_single_order_reservation(self, _):
        self.b.request({'op':'offer','hits':[community_hit()]})
        self.assertTrue(self.b.tasks.empty())
        self.b.armed=True
        self.b.request({'op':'offer','hits':[community_hit()]})
        self.b.request({'op':'offer','hits':[hit(),community_hit()]})
        self.assertEqual(self.b.tasks.qsize(),1)
        _,op,h=self.b.tasks.get_nowait()
        self.assertEqual(op,'start'); self.assertEqual(h['source'],'sunbelife_aggregate')
        self.b.request({'op':'stop'})
        self.b.request({'op':'offer','hits':[community_hit()]})
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
    reason = '同城其他门店也都不可取货'
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
        self.owner.end_reason=self.reason if self.outcome=='sold_out' else ''
        self.owner.closed=True

def one_pass(b):
    """让后台循环只跑一圈：先处理停止，再取一次排队的任务，然后退出。"""
    get = b.tasks.get
    def once(timeout=None):
        b.closed = True
        return get(timeout=0.01)
    b.tasks.get = once
    b.closed = False
    b.run()

class RecoveryTests(unittest.TestCase):
    @patch('worker.core.normalized_profile', return_value={})
    @patch.object(Bridge, 'acquire')
    def test_manual_purchase_runs_saved_plan_for_selected_target(self, *_):
        with tempfile.TemporaryDirectory() as d:
            b=Bridge(FakeCheckout,data=Path(d));b.plan['auto_submit_order']=True;b.service.outcome='unknown'
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

def unchecked_hit():
    """界面手动开始时发来的目标：只有门店和商品，没有任何库存结果。"""
    h = hit()
    h.pop('availability')
    h.pop('lastCheckedMs')
    return h

class ManualStartTests(unittest.TestCase):
    @patch('worker.core.normalized_profile', return_value={})
    @patch.object(Bridge, 'acquire')
    def test_manual_start_needs_no_stock_and_switches_stores_only_for_this_run(self, *_):
        with tempfile.TemporaryDirectory() as d:
            b = Bridge(FakeCheckout, data=Path(d))
            b.service.outcome = 'unknown'
            self.assertTrue(b.request({'op': 'manual', 'hit': unchecked_hit()})['busy'])
            b.run()
            self.assertFalse(b.error)
            self.assertTrue(b.automatic)
            self.assertTrue(b.status()['manualRun'])
            self.assertEqual(b.service.preferences['store_choice'], 'same_city')
            self.assertEqual(b.service.preferences['store_id'], hit()['target']['storeNumber'])
            self.assertNotIn('store_choice', b.plan, '换门店只作用于这一次，不写进保存的方案')

    def test_auto_submit_needs_its_own_confirmation_and_order_guard_still_blocks(self):
        with tempfile.TemporaryDirectory() as d:
            b = Bridge(FakeCheckout, data=Path(d))
            b.plan['auto_submit_order'] = True
            with self.assertRaises(ValueError):
                b.request({'op': 'manual', 'hit': unchecked_hit()})
            self.assertFalse(b.reserved)
            b.request({'op': 'manual', 'hit': unchecked_hit(), 'confirmSubmission': True})
            self.assertTrue(b.submission_confirmed)
            self.assertEqual(b.tasks.qsize(), 1)
        with tempfile.TemporaryDirectory() as d:
            b = Bridge(FakeCheckout, data=Path(d))
            b.order_guard.begin()
            with self.assertRaises(ValueError):
                b.request({'op': 'manual', 'hit': unchecked_hit()})
            self.assertFalse(b.reserved)
            self.assertTrue(b.tasks.empty())

    def test_manual_start_is_mainland_only_and_refused_during_a_running_checkout(self):
        with tempfile.TemporaryDirectory() as d:
            b = Bridge(FakeCheckout, data=Path(d))
            h = unchecked_hit()
            h['target']['locale'] = 'en_SG'
            b.armed = True
            with self.assertRaises(ValueError):
                b.request({'op': 'manual', 'hit': h})
            self.assertTrue(b.armed, '检查没通过时不能先把自动选购关掉')
            b.reserved = True
            with self.assertRaises(ValueError):
                b.request({'op': 'manual', 'hit': unchecked_hit()})
            self.assertTrue(b.tasks.empty())

    def test_manual_start_pauses_waiting_auto_checkout_with_its_authorization(self):
        with tempfile.TemporaryDirectory() as d:
            b = Bridge(FakeCheckout, data=Path(d))
            b.plan['auto_submit_order'] = True
            b.armed, b.submission_confirmed = True, True
            s = b.request({'op': 'manual', 'hit': unchecked_hit(), 'confirmSubmission': True})
            self.assertFalse(s['armed'])
            self.assertTrue(s['busy'])
            self.assertTrue(s['autoPaused'])
            self.assertEqual(s['singleRun'], 'manual')
            self.assertTrue(b.resume_submission)
            # 改设置、启用之类仍要等这次结束。
            with self.assertRaises(ValueError):
                b.request({'op': 'configure', 'plan': b.plan})

    @patch('worker.core.normalized_profile', return_value={})
    @patch('worker.time.time', return_value=100)
    @patch.object(Bridge, 'acquire')
    def test_manual_sold_out_ends_by_itself_and_resumes_auto_checkout(self, *_):
        with tempfile.TemporaryDirectory() as d:
            b = Bridge(FakeCheckout, data=Path(d))
            b.plan['auto_submit_order'] = True
            b.armed, b.submission_confirmed = True, True
            b.request({'op': 'manual', 'hit': unchecked_hit(), 'confirmSubmission': True})
            b.run()
            s = b.status()
            self.assertFalse(s['busy'], '没买到时不用本人点停止')
            self.assertTrue(s['armed'], '之前开着的自动选购恢复')
            self.assertTrue(s['submissionEnabled'], '恢复时带回启用时的下单授权')
            self.assertIsNone(s['singleRun'])
            self.assertFalse(s['autoPaused'])
            self.assertEqual(s['lastResult'], '手动选购没买到：同城其他门店也都不可取货。自动选购已恢复，有货后自动选购')
            self.assertEqual(b.notice['title'], '手动选购没买到')
            self.assertEqual(b.avoid_until, {}, '没买到的门店不进等待期：自动选购有货就能马上用')
            b.request({'op': 'offer', 'hits': [hit()]})
            self.assertEqual(b.tasks.qsize(), 1, '恢复后新库存照常接手')

    @patch('worker.core.normalized_profile', return_value={})
    @patch.object(Bridge, 'acquire')
    def test_rehearsal_and_row_purchase_also_end_by_themselves_without_turning_auto_on(self, *_):
        for op in ('rehearse', 'purchase'):
            with tempfile.TemporaryDirectory() as d:
                b = Bridge(FakeCheckout, data=Path(d))
                b.request({'op': op, 'hit': hit() if op == 'purchase' else unchecked_hit()})
                b.run()
                s = b.status()
                self.assertFalse(s['busy'], op)
                self.assertFalse(s['armed'], '开始前没启用的，结束后也不启用')
                self.assertFalse(b.submission_confirmed)
                self.assertTrue(s['lastResult'].startswith('测试演练结束' if op == 'rehearse' else '这次选购没买到'))

    @patch('worker.core.normalized_profile', return_value={})
    @patch.object(Bridge, 'acquire')
    def test_a_run_that_reached_the_review_page_never_resumes_auto_checkout(self, *_):
        with tempfile.TemporaryDirectory() as d:
            b = Bridge(FakeCheckout, data=Path(d))
            b.service.outcome = 'ready'
            b.armed = True
            b.request({'op': 'manual', 'hit': unchecked_hit()})
            b.run()
            self.assertTrue(b.status()['busy'], '到核对页要留给本人处理')
            self.assertFalse(b.status()['autoPaused'])
            b.request({'op': 'end_run'})
            one_pass(b)
            s = b.status()
            self.assertFalse(s['busy'])
            self.assertFalse(s['armed'], '本人可能已在官网手动下单，不能再自动买一台')
            self.assertIn('走到过订单核对页', s['lastResult'])

    @patch('worker.core.normalized_profile', return_value={})
    @patch.object(Bridge, 'acquire')
    def test_ending_a_paused_manual_run_resumes_auto_but_stop_turns_everything_off(self, *_):
        for op, armed in (('end_run', True), ('stop', False)):
            with tempfile.TemporaryDirectory() as d:
                b = Bridge(FakeCheckout, data=Path(d))
                b.service.outcome = 'unknown'
                b.armed = True
                b.request({'op': 'manual', 'hit': unchecked_hit()})
                b.run()
                self.assertTrue(b.status()['busy'], '需要本人处理的暂停仍要等本人')
                b.request({'op': op})
                one_pass(b)
                s = b.status()
                self.assertFalse(s['busy'], op)
                self.assertEqual(s['armed'], armed, op)
                self.assertIsNone(s['singleRun'])

    def test_end_run_only_touches_single_runs(self):
        with tempfile.TemporaryDirectory() as d:
            b = Bridge(FakeCheckout, data=Path(d))
            b.armed, b.reserved = True, True
            s = b.request({'op': 'end_run'})
            self.assertFalse(s['stopping'], '自动选购的单子要用停止，不能被「结束本次」悄悄留着自动选购')
            self.assertTrue(s['armed'])

    @patch('worker.core.normalized_profile', side_effect=ValueError('资料不完整'))
    def test_manual_start_that_fails_before_the_browser_still_resumes_auto(self, _):
        with tempfile.TemporaryDirectory() as d:
            b = Bridge(FakeCheckout, data=Path(d))
            b.armed = True
            b.request({'op': 'manual', 'hit': unchecked_hit()})
            one_pass(b)
            s = b.status()
            self.assertFalse(s['busy'])
            self.assertTrue(s['armed'])
            self.assertIn('联系与收货资料', s['error'])

    def test_plain_ends_are_marked_for_every_single_run_but_not_release_for_auto(self):
        with tempfile.TemporaryDirectory() as d:
            b = Bridge(data=Path(d))
            b.single_run = 'manual'
            b.service.event('sold_out', '同城其他门店也都不可取货')
            self.assertTrue(b.retryable_end)
            self.assertEqual(b.end_reason, '同城其他门店也都不可取货')
            b.retryable_end = False
            b.service.event('over_budget', '购物袋超预算，仅记录')
            self.assertEqual(b.end_reason, '购物袋超预算')
            b.retryable_end = False
            b.service.event('not_released', '官网尚未开放购买')
            self.assertTrue(b.retryable_end)
            b.retryable_end, b.single_run = False, None
            b.service.event('not_released', '官网尚未开放购买')
            self.assertFalse(b.retryable_end, '自动选购遇到还没开卖照旧停下等本人')

class FakeLogin(FakeCheckout):
    """提前登录：打开账号页后第一次检查就确认已登录。"""
    def __init__(self, owner, plan):
        super().__init__(owner, plan);self.commands=[]
    def command(self, name):
        self.commands.append(name);self.active=name=='login'
    def tick(self):
        self.active=False
        self.status.update(mode='completed',session='账号页登录已确认')
        self.owner.closed=True

class LoginTaskTests(unittest.TestCase):
    @patch.object(Bridge,'acquire')
    def test_login_runs_only_when_idle_and_hands_the_worker_back(self, *_):
        with tempfile.TemporaryDirectory() as d:
            b=Bridge(FakeLogin,data=Path(d))
            b.armed=True
            with self.assertRaises(ValueError):b.request({'op':'login'})
            b.armed=False
            self.assertTrue(b.request({'op':'login'})['busy'])
            b.run()
            # 末尾的 stop 是测试结束后台循环时的收尾，不是登录流程发出的。
            self.assertEqual(b.service.commands,['login','stop'])
            self.assertFalse(b.error)
            self.assertFalse(b.reserved,'登录确认后要交还选购后台，之后才能启用自动选购')
            self.assertFalse(b.status()['loginTask'])
            self.assertEqual(b.status()['session'],'账号页登录已确认')
    def test_login_is_only_offered_for_the_mainland_store(self):
        with tempfile.TemporaryDirectory() as d:
            b=Bridge(FakeLogin,data=Path(d));b.plan['checkout_locale']='en_SG'
            with self.assertRaises(ValueError):b.request({'op':'login'})
            self.assertFalse(b.reserved)

if __name__=='__main__':unittest.main()

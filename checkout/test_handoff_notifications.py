import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from worker import Bridge,HANDOFFS
from checkout_engine import Decision

class HandoffNotifications(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.b=Bridge(data=Path(self.tmp.name));self.s=self.b.service
    def tearDown(self):self.tmp.cleanup()
    def test_login_privacy_phone_are_visible_and_each_notifies_once(self):
        snapshots=[dict(stage='LOGIN',diagnostics={}),dict(stage='LOGIN',diagnostics={'privacy_consent_required':True}),dict(stage='CONTACT',contact_verification_required=True,diagnostics={'contact_verification_required':True})]
        previous=None
        for snap,kind in zip(snapshots,('login','privacy','phone')):
            self.s.observe_diagnostics(snap);notice=self.b.notice.copy()
            self.assertEqual(notice['kind'],kind);self.assertEqual(self.b.status()['intervention']['kind'],kind)
            self.assertNotEqual(notice['id'],previous)
            self.s.observe_diagnostics(snap);self.assertEqual(self.b.notice['id'],notice['id']);previous=notice['id']
    def test_verification_completion_clears_banner_and_stop_clears_intervention(self):
        self.s.observe_diagnostics(dict(stage='CONTACT',contact_verification_required=True,diagnostics={'contact_verification_required':True}))
        self.s.observe_diagnostics(dict(stage='CONTACT',diagnostics={}))
        self.assertIsNone(self.b.status()['intervention'])
        self.s.require_user_action('login');self.s.command('stop')
        self.assertIsNone(self.b.status()['intervention'])
    def test_fixed_messages_never_include_dom_or_personal_data(self):
        self.s.update(mode='attention',stage='CONTACT',reason='secret raw DOM 13800000000 demo@example.invalid')
        self.s.notify('ignored','secret')
        self.assertEqual(self.b.notice['kind'],'contact')
        self.assertNotIn('secret',self.b.notice['body']);self.assertNotIn('13800000000',self.b.notice['body'])

    def test_checkout_action_trace_keeps_receipt_without_private_retry_keys(self):
        for action in ('pickup_date:private-dom:2026-10-23',
                       'pickup_time:private-slot','fill:private-field',
                       'continue_FULFILLMENT'):
            self.s.event('action',action,result='dispatched',detail='private-text')
        events=json.loads((Path(self.tmp.name)/'checkout-diagnostics.json').read_text())['events']
        self.assertEqual([e['action'] for e in events],
                         ['pickup_date','pickup_time','fill','continue_FULFILLMENT'])
        self.assertTrue(all(e['result']=='dispatched' for e in events))
        self.assertNotIn('private',json.dumps(events))

    def test_stale_continue_cancels_navigation_wait_because_no_click_happened(self):
        from checkout_engine import Attempt,variants
        self.b.cancel.clear()
        self.s.attempt=Attempt(self.s.preferences,variants(self.s.preferences)[0],0)
        self.s.attempt.pending_navigation=('FULFILLMENT',0)
        self.s.attempt.sent['continue_FULFILLMENT']=1
        self.s.browser=Mock()
        self.s.browser.call.return_value={'result':'stale'}
        self.s.dispatch({'fingerprint':'old'},Decision('click','continue_FULFILLMENT','1'))
        self.assertIsNone(self.s.attempt.pending_navigation)
        self.assertEqual(self.s.attempt.sent['continue_FULFILLMENT'],0)

    def test_denial_latches_monitor_stop_and_saves_only_sanitized_evidence(self):
        self.s.observe_diagnostics(dict(stage='BLOCKED',http_status=541,
            diagnostics={'access_http_status':541,'access_shop_request':True,'contact':'secret'}))
        self.s.attention('untrusted raw error secret')
        self.assertTrue(self.b.status()['accessBlocked'])
        self.assertEqual(self.b.notice['kind'],'blocked')
        self.assertIn('541',self.b.status()['reason'])
        self.s.command('stop')
        self.assertTrue(self.b.status()['accessBlocked'])
        report=(Path(self.tmp.name)/'checkout-diagnostics.json').read_text(encoding='utf-8')
        self.assertNotIn('secret',report)
        self.assertEqual(json.loads(report)['events'][-1]['http_status'],541)
        # Stop doesn't prove recovery. A healthy owned shop page does.
        self.s.observe_diagnostics(dict(stage='PRODUCT',diagnostics={'document_http_status':200}))
        self.assertFalse(self.b.status()['accessBlocked'])

    def test_denial_arriving_between_snapshot_and_click_also_stops_monitor(self):
        self.b.cancel.clear()
        self.s.browser=Mock()
        self.s.browser.call.return_value={'stage':'BLOCKED','http_status':541,
            'diagnostics':{'access_http_status':541},'result':'refused','reason':'access_denied'}
        self.s.dispatch({'fingerprint':'old'},Decision('click','add','1'))
        self.assertTrue(self.b.status()['accessBlocked'])
        self.assertEqual(self.b.status()['mode'],'attention')
        self.assertIn('541',self.b.status()['reason'])

    def test_different_steps_do_not_share_ids_on_low_resolution_windows_clock(self):
        with patch('worker.time.time_ns',return_value=100):
            ids=[]
            for kind in ('login','privacy','phone','payment'):
                self.s.require_user_action(kind);ids.append(self.b.notice['id'])
            self.assertEqual(len(set(ids)),4)
    def test_waiter_does_not_resume_while_verification_is_still_required(self):
        self.s.login_watch=True;self.s.login_watch_until=10**12;self.s.login_probe_at=0
        self.s.browser=Mock();self.s.browser.call.return_value={'stage':'CONTACT','contact_verification_required':True,'diagnostics':{'contact_verification_required':True}}
        self.s.command=Mock();self.s.watch_login_tick();self.s.command.assert_not_called()

    def test_verified_contact_resumes_only_after_two_matching_readbacks(self):
        self.s.login_watch=True;self.s.login_watch_until=10**12;self.s.phase='RUN'
        self.s.attempt=Mock();self.s.attempt.bag_verified=True
        self.s.browser=Mock();self.s.browser.call.return_value={'stage':'CONTACT','fingerprint':'verified','diagnostics':{}}
        self.s.command=Mock()
        for i in range(2):
            self.s.login_probe_at=0;self.s.watch_login_tick()
            self.assertEqual(self.s.command.call_count,i)
        self.s.command.assert_called_once_with('continue')

    def login_attempt(self):
        from checkout_engine import Attempt,variants
        attempt=Attempt(self.s.preferences,variants(self.s.preferences)[0],0)
        attempt.bag_verified=True
        return attempt

    def test_login_during_checkout_waits_thirty_minutes_then_resumes_itself(self):
        import checkout_service
        self.b.cancel.clear()
        self.s.phase='RUN';self.s.active=True;self.s.next_probe=0;self.s.attempt=self.login_attempt()
        self.s.browser=Mock();self.s.browser.call.return_value={'stage':'LOGIN','diagnostics':{}}
        with patch('worker.time.monotonic',return_value=5000.0):
            self.s.tick()
        self.assertTrue(self.s.login_watch,'结账中途要求登录时应当等本人登录，而不是直接停住')
        self.assertEqual(checkout_service.LOGIN_WAIT_SECONDS,30*60)
        self.assertEqual(self.s.login_watch_until,5000.0+checkout_service.LOGIN_WAIT_SECONDS)
        self.assertEqual(self.b.notice['kind'],'login')
        self.assertIn('自动接着结账',self.b.notice['body'])

    def test_login_returning_to_bag_resumes_with_one_fresh_checkout_click(self):
        self.s.login_watch=True;self.s.login_watch_until=10**12;self.s.phase='RUN'
        self.s.attempt=self.login_attempt()
        self.s.attempt.sent.update(add=1,checkout=1);self.s.attempt.action_times.update(add=0,checkout=0)
        self.s.browser=Mock();self.s.browser.call.return_value={'stage':'BAG','fingerprint':'bag','diagnostics':{}}
        self.s.command=Mock()
        for i in range(2):
            self.s.login_probe_at=0;self.s.watch_login_tick()
        self.s.command.assert_called_once_with('continue')
        self.assertNotIn('checkout',self.s.attempt.sent,'被登录打断的那次结账可以再点一次')
        self.assertEqual(self.s.attempt.sent['add'],1,'加购记录必须保留，不能重复加购')

    def test_bag_after_login_without_a_verified_bag_still_waits_for_a_person(self):
        self.s.login_watch=True;self.s.login_watch_until=10**12;self.s.phase='RUN'
        self.s.attempt=Mock();self.s.attempt.bag_verified=False
        self.s.browser=Mock();self.s.browser.call.return_value={'stage':'BAG','fingerprint':'bag','diagnostics':{}}
        self.s.command=Mock();self.s.attention=Mock()
        self.s.login_probe_at=0;self.s.watch_login_tick()
        self.s.command.assert_not_called();self.s.attention.assert_called_once()

    def test_waiting_for_login_reminds_again_every_five_minutes(self):
        from worker import LOGIN_REMINDER_SECONDS
        self.s.login_watch=True;self.s.login_watch_until=10**12;self.s.phase='RUN'
        self.s.attempt=self.login_attempt()
        self.s.browser=Mock();self.s.browser.call.return_value={'stage':'LOGIN','diagnostics':{}}
        clock=[1000.0]
        with patch('worker.time.monotonic',side_effect=lambda:clock[0]):
            self.s.login_probe_at=0;self.s.watch_login_tick()
            first=self.b.notice['id']
            clock[0]+=LOGIN_REMINDER_SECONDS-1;self.s.login_probe_at=0;self.s.watch_login_tick()
            self.assertEqual(self.b.notice['id'],first,'不到间隔不重复提醒')
            clock[0]+=2;self.s.login_probe_at=0;self.s.watch_login_tick()
            self.assertNotEqual(self.b.notice['id'],first,'到了间隔再提醒一次')
            self.assertEqual(self.b.notice['kind'],'login')

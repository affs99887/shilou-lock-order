"""One authorized live rehearsal using the saved plan, always stop before submit."""
import copy,json,sys,tempfile,time,hashlib,marshal,types,importlib.abc,importlib.util,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path[:0]=[str(ROOT/'checkout'),str(ROOT/'checkout/vendor')]
# Load the actual installed helper's application bytecode, not later source edits.
from PyInstaller.archive.readers import CArchiveReader
helper=Path.home()/'Applications/拾漏锁单.app/Contents/Resources/resources/apw-checkout'
if '--source-current' in sys.argv:
 import worker
else:
 archive=CArchiveReader(str(helper));pyz=archive.open_embedded_archive('PYZ.pyz')
 application_modules={n for n in pyz.toc if n.startswith(('checkout_', 'regional_')) or n in {'safari_checkout_autofill','pickup_monitor','stock_labels','tls_support','platform_runtime','payment_options','order_guard'}}
 class InstalledLoader(importlib.abc.MetaPathFinder,importlib.abc.Loader):
  def find_spec(self,fullname,path=None,target=None):
   if fullname in application_modules:return importlib.util.spec_from_loader(fullname,self)
  def create_module(self,spec):return None
  def exec_module(self,module):
   module.__file__=str(ROOT/'checkout/vendor'/(module.__name__+'.py'))
   exec(pyz.extract(module.__name__),module.__dict__)
 sys.meta_path.insert(0,InstalledLoader())
 worker=types.ModuleType('installed_worker');worker.__file__=str(ROOT/'checkout/worker.py');sys.modules[worker.__name__]=worker
 exec(marshal.loads(archive.extract('worker')),worker.__dict__)
 worker.core.JS=archive.extract('checkout_browser.js').decode('utf-8')
 worker.core.ADAPTER_VERSION=hashlib.sha256(worker.core.JS.encode()).hexdigest()
Bridge=worker.Bridge
from checkout_engine import validate_preferences,Decision,Attempt,variants
saved=Path.home()/'Library/Application Support/ApplePickupWatcherAuto/checkout.json'
p=json.loads(saved.read_text())
p.update(model='iphone-18-pro',colors=['冰川蓝色'],capacities=['256GB'],store='Apple 武汉',store_id='R575',fulfillment='pickup',auto_submit_order=False)
if '--current-dates' in sys.argv:p['pickup_dates']=['2026-10-20','2026-10-21','2026-10-22']
if '--earliest' in sys.argv:p['pickup_date_policy']='earliest'
p=validate_preferences(p)
previous=ROOT/'checkout/qa-pickup-stability'/('live-rehearsal-pickup-resume.json' if '--resume-contact' in sys.argv else 'live-rehearsal-bag-resume.json' if '--resume-page' in sys.argv else 'live-rehearsal-rerun.json')
resume_bag=json.loads(previous.read_text()) if any(v in sys.argv for v in ('--resume-bag','--resume-page','--resume-contact')) else None
out=ROOT/'checkout/qa-pickup-stability'/('live-rehearsal-contact-resume.json' if '--resume-contact' in sys.argv else 'live-rehearsal-pickup-resume.json' if '--resume-page' in sys.argv else 'live-rehearsal-bag-resume.json' if resume_bag else 'live-rehearsal-rerun.json')
report={'model':'iPhone 18 Pro','capacity':'256GB','color':'冰川蓝色','store_id':'R575','allowed_dates':p['pickup_dates'],'final_submission_enabled':False,'timeline':[],'orders_created':0,'installed_helper_sha256':hashlib.sha256(helper.read_bytes()).hexdigest(),'installed_bytecode':'--source-current' not in sys.argv}
with tempfile.TemporaryDirectory(prefix='shilou-live-rehearsal-') as directory:
 b=Bridge(data=Path(directory));b.plan=copy.deepcopy(p);b.service.preferences=copy.deepcopy(p)
 b.automatic=False;b.submission_confirmed=False;b.cancel.clear()
 service=b.service
 try:
  b.acquire()
  print('Starting authorized single rehearsal; final submission disabled',flush=True)
  if resume_bag:
   # Re-read the cart authoritatively; do not repeat an uncertain remove/add.
   service.browser.window=resume_bag['browser_window']
   service.browser.bindings[service.browser.window]=(1,[''])
   service.profile=worker.core.normalized_profile(require_pickup=True)
   check=service.browser.call(service.profile,{})
   assert check.get('stage')==('CONTACT' if '--resume-contact' in sys.argv else 'FULFILLMENT' if '--resume-page' in sys.argv else 'BAG'),'Only the verified current cart or pickup page may be resumed'
   service.run_preferences=copy.deepcopy(p);service.job='rehearse'
   service.start_timing();service.phase='RUN';service.phase_since=time.monotonic()
   service.attempt=Attempt(p,variants(p)[0],time.monotonic())
   service.attempt.target['pickup_dates']=list(p['pickup_dates'])
   service.attempt.add_submitted=True
   if '--resume-page' in sys.argv or '--resume-contact' in sys.argv:
    assert resume_bag.get('outcome',{}).get('stage')==('CONTACT' if '--resume-contact' in sys.argv else 'FULFILLMENT')
    service.attempt.bag_verified=True
    service.attempt.sent.update(add=1,checkout=1)
    if '--resume-contact' in sys.argv:
     service.attempt.fulfillment_verified=True
     service.attempt.pickup_confirmed_dates=['2026-10-20']
   service.attempt.started=time.monotonic()
   service.active=True
   service.update(mode='running',reason='保留已核对购物袋，从当前页面继续演练；禁止下单')
   report['resumed_existing_cart']=True
  else:
   service.command('rehearse')
  end=time.monotonic()+240;last=None;guest_sent=False
  while time.monotonic()<end:
   assert not service.formal_submission()
   if service.active:service.tick()
   elif service.login_watch:
    if not guest_sent:
     fresh=service.browser.call(service.profile,service.attempt.target if service.attempt else {})
     guest=[c for c in fresh.get('controls',[]) if c.get('label')=='以游客身份继续' and c.get('role')=='button' and not c.get('disabled')]
     if fresh.get('stage')=='LOGIN' and len(guest)==1:
      guest_sent=True
      result=service.browser.call(service.profile,service.attempt.target,dict(kind='click',ref=guest[0]['ref'],expected=fresh['fingerprint'],nonce=uuid.uuid4().hex))
      report['guest_continue_result']=result.get('result')
      print('Guest checkout action: '+str(result.get('result')),flush=True)
    service.watch_login_tick()
   state=service.snapshot()
   entry={k:state.get(k) for k in ['mode','stage','reason','elapsed','diagnostics']}
   marker={k:v for k,v in entry.items() if k!='elapsed'}
   if marker!=last:
    report['timeline'].append(entry);last=marker
    report['browser_window']=service.browser.window
    out.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(entry,ensure_ascii=False),flush=True)
   if not service.active and not service.login_watch:break
   time.sleep(.5)
  report['outcome']={k:service.status.get(k) for k in ['mode','stage','reason']}
 finally:
  b.cancel.set();service.command('stop');b.release()
  report['stopped']=True;report['remaining_active']=service.active
  out.write_text(json.dumps(report,ensure_ascii=False,indent=2))
print(json.dumps({'outcome':report.get('outcome'),'stopped':report['stopped'],'orders_created':0},ensure_ascii=False),flush=True)

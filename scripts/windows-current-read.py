"""Read the existing owned Edge page only; never click, navigate or read contacts."""
import json
import os
from pathlib import Path
import sys
import tarfile
import urllib.request
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root/'checkout'), str(root/'checkout/vendor')]
from checkout_engine import DEFAULTS, variants
from edge_browser import EdgeSession

directory = Path(os.environ['LOCALAPPDATA'])/'ApplePickupWatcherAuto'
session = EdgeSession(directory)
endpoint = session.endpoint()
assert endpoint, 'No verified owned Edge endpoint; do not launch or adopt another browser'
raw = json.loads((directory/'checkout.json').read_text(encoding='utf-8'))
plan = dict(DEFAULTS, **{k:v for k,v in raw.items() if k in DEFAULTS})
target = variants(plan)[0]
sources = {'current_source': (root/'checkout/vendor/checkout_browser.js').read_text(encoding='utf-8')}
installed = Path(sys.argv[1])
archive = installed/'resources/OpenSource/Source.tar.gz'
if archive.is_file():
    with tarfile.open(archive) as package:
        sources['installed_source'] = package.extractfile('checkout/vendor/checkout_browser.js').read().decode('utf-8')
reports=[]
frozen_matches = {}
if archive.is_file():
    from PyInstaller.archive.readers import CArchiveReader
    frozen = CArchiveReader(str(installed/'resources/apw-checkout.exe')).open_embedded_archive('PYZ.pyz')
    with tarfile.open(archive) as package:
        for module in ('checkout_engine', 'edge_browser'):
            code = frozen.extract(module)
            source = package.extractfile('checkout/vendor/'+module+'.py').read().decode('utf-8')
            frozen_matches[module] = code == compile(source, code.co_filename, 'exec')
with sync_playwright() as driver:
    browser = driver.chromium.connect_over_cdp(endpoint, timeout=4000)
    for page in browser.contexts[0].pages:
        url=urlsplit(page.url)
        if url.hostname not in ('www.apple.com.cn',) or not url.path.startswith('/shop/buy-iphone/'):
            continue
        row={'host':url.hostname,'path':url.path,'reads':{}}
        for name,source in sources.items():
            try:
                value=page.evaluate('() => {'+source+'\nreturn appleCheckout('+json.dumps({'profile':{},'target':target},ensure_ascii=False)+');}')
                row['reads'][name]={
                    'stage':value.get('stage'), 'product_model':value.get('product_model'),
                    'diagnostics':value.get('diagnostics'), 'product_price':value.get('product_price'),
                    'controls':[{k:c.get(k) for k in ('label','role','group','selected','disabled')}
                        for c in value.get('controls',[]) if c.get('group') in ('model','applecare') or c.get('role')=='radio'
                        or c.get('label') in ('添加到购物袋','加入购物袋','继续')]
                }
            except Exception:
                row['reads'][name]={'read_failed':True}
        reports.append(row)
    # Driver disconnect only. Never close the real Edge/context/page.
print(json.dumps({'owned_product_pages':reports,'configured_model':plan.get('model'),
    'configured_colors':plan.get('colors'),'configured_capacities':plan.get('capacities'),
    'configured_applecare':plan.get('applecare'),'frozen_matches_bundled_source':frozen_matches,
    'browser_actions':0,'profile_reads':0},ensure_ascii=False))

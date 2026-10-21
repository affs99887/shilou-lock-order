"""Read owned Edge diagnostics without navigation, actions or contact values."""
import json
import os
from pathlib import Path
import sys
from urllib.parse import urlsplit
from playwright.sync_api import sync_playwright

root = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(root / 'checkout/vendor')]
from edge_browser import EdgeSession

endpoint = EdgeSession(Path(os.environ['LOCALAPPDATA']) / 'ApplePickupWatcherAuto').endpoint()
assert endpoint, 'Owned Edge is not available'
source = (root / 'checkout/vendor/checkout_browser.js').read_text(encoding='utf-8')
with sync_playwright() as driver:
    browser = driver.chromium.connect_over_cdp(endpoint, timeout=4000)
    rows = []
    for page in browser.contexts[0].pages:
        url = urlsplit(page.url)
        if not (url.hostname or '').endswith(('apple.com.cn', 'apple.com')):
            continue
        row = dict(host=url.hostname, path=url.path)
        try:
            channel = browser.contexts[0].new_cdp_session(page)
            history = channel.send('Page.getNavigationHistory')
            row['history'] = [
                dict(host=urlsplit(item['url']).hostname, path=urlsplit(item['url']).path,
                     transition=item.get('transitionType'))
                for item in history['entries']
                if (urlsplit(item['url']).hostname or '').endswith(('apple.com.cn', 'apple.com'))
            ]
            channel.detach()
            snapshot = page.evaluate('() => {' + source + '\nreturn appleCheckout({profile:{},target:{}});}')
            row.update(stage=snapshot.get('stage'), diagnostics=snapshot.get('diagnostics'))
            row['resources'] = page.evaluate("""() => performance.getEntriesByType('resource')
              .filter(x => /\\/shop\\/|appleauth/.test(x.name)).slice(-35).map(x => {
                const u=new URL(x.name);return {host:u.hostname,path:u.pathname,
                  status:x.responseStatus,duration:Math.round(x.duration),type:x.initiatorType};
              })""")
            row['navigation'] = page.evaluate("""() => performance.getEntriesByType('navigation').map(x =>
              ({status:x.responseStatus,redirectCount:x.redirectCount,type:x.type,
                duration:Math.round(x.duration)}))""")
        except Exception:
            row['read_failed'] = True
        rows.append(row)
    print(json.dumps(dict(pages=rows, actions=0), ensure_ascii=False))

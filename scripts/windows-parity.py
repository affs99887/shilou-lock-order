"""Verify shared behavior against the installed, already-delivered Mac baseline."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile

root=Path(__file__).resolve().parents[1]
args=argparse.ArgumentParser()
args.add_argument('--baseline',type=Path,required=True)
args=args.parse_args()
archive=args.baseline
names=['src/App.tsx','src/components/AutoCheckout.tsx','src/components/ContactProfile.tsx',
       'src/components/BarkSettings.tsx','src/lib/installment-plans.json',
       'src/lib/checkout-regions.json','src/lib/checkout-catalog.json',
       'src/lib/bark-address.ts','checkout/vendor/checkout_engine.py',
       'checkout/vendor/checkout_browser.js','checkout/vendor/payment_options.py',
       'checkout/vendor/pickup_monitor.py','checkout/vendor/checkout_profile.py','checkout/order_guard.py']
names += [str(p.relative_to(root)) for p in sorted((root/'crates/apw-core/src').rglob('*.rs'))]
rows=[]
with tarfile.open(archive) as tar:
    for name in names:
        old=tar.extractfile(name).read();new=(root/name).read_bytes()
        scoped=False
        if old!=new and name.endswith('checkout_engine.py'):
            def outside_product(data):
                s=data.decode();start=s.index('        if stage == "PRODUCT":');end=s.index('        elif stage == "ATTACH":',start)
                return (s[:start]+s[end:]).replace('        self.product_choice_wait_since = {}\n','')
            scoped=outside_product(old)==outside_product(new)
        elif old!=new and name.endswith('checkout_browser.js'):
            def outside_stock_choice(data):
                s=data.decode();start=s.index('  const choiceUnavailable =');end=s.index('  // Coverage belongs',start)
                return s[:start]+s[end:]
            scoped=outside_stock_choice(old)==outside_stock_choice(new)
        rows.append({'file':name,'unchanged':old==new,'product_loading_fix_only':scoped,'sha256':hashlib.sha256(new).hexdigest()})
assert all(row['unchanged'] or row['product_loading_fix_only'] for row in rows),[r['file'] for r in rows if not (r['unchanged'] or r['product_loading_fix_only'])]
out=root/'deliverables/windows/auto.12/parity.json'
out.parent.mkdir(parents=True,exist_ok=True)
out.write_text(json.dumps({'baseline':'Mac 0.6.2-auto.11','windows':'0.6.2-auto.12','files':rows,
 'platform_changes':['Windows Edge navigation timeout/presentation recovery','International Edge adapter and per-region DPAPI profiles','Edge-specific manual action notices','Private diagnostics and Windows build tests'],
 'live_foreign_checkout_verified':False},ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps({'unchanged_business_logic_files':sum(r['unchanged'] for r in rows),
 'product_loading_only_changes':sum(r['product_loading_fix_only'] for r in rows),'passed':True}))

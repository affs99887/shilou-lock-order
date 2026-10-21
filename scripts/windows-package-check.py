"""Match installed frozen code/assets to the tested source, without launching it."""
import json
import marshal
from pathlib import Path
import sys
import tarfile
from PyInstaller.archive.readers import CArchiveReader

app=Path(sys.argv[1])
resources=app/'resources'
reader=CArchiveReader(str(resources/'apw-checkout.exe'))
modules=reader.open_embedded_archive('PYZ.pyz')
verified=[]
with tarfile.open(resources/'OpenSource/Source.tar.gz') as archive:
    for name in ('checkout_engine','checkout_service','checkout_profile','edge_browser',
                 'regional_checkout','regional_service','platform_runtime','windows_vault'):
        code=modules.extract(name)
        source=archive.extractfile('checkout/vendor/'+name+'.py').read().decode('utf-8')
        assert code==compile(source,code.co_filename,'exec'),name
        verified.append(name)
    code=marshal.loads(reader.extract('worker'))
    source=archive.extractfile('checkout/worker.py').read().decode('utf-8')
    assert code==compile(source,code.co_filename,'exec'),'worker'
    verified.append('worker')
    for name in ('checkout_browser.js','regional_browser.js'):
        assert reader.extract(name)==archive.extractfile('checkout/vendor/'+name).read(),name
    for name in ('checkout-regions.json','checkout-catalog.json'):
        assert json.loads(reader.extract(name))==json.loads(archive.extractfile('src/lib/'+name).read()),name
print(json.dumps({'installed_frozen_modules_match_tested_source':verified,'browser_assets_match':True,
                  'region_catalog_assets_match':True,'browser_actions':0,'profile_reads':0}))

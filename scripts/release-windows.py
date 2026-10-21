"""Two-file consumer ZIP. Never include QA logs, profiles or build materials."""
import hashlib
import json
from pathlib import Path
import zipfile
import argparse

root=Path(__file__).resolve().parents[1]
version=json.loads((root/'src-tauri/tauri.windows.conf.json').read_text(encoding='utf-8'))['version']
args=argparse.ArgumentParser()
args.add_argument('--verified-installer-sha256',required=True)
expected=args.parse_args().verified_installer_sha256
assert len(expected)==64
source=root/'deliverables/windows'/version.split('-',1)[1]
installer=source/'拾漏锁单-安装.exe'
assert hashlib.sha256(installer.read_bytes()).hexdigest()==expected
archive=root.parents[1]/'交付'/f'拾漏锁单-Windows-{version}.zip'
assert not archive.exists(),'Do not overwrite an existing release'
with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as package:
    for name in ('拾漏锁单-安装.exe','使用说明.txt'):
        package.write(source/name,'拾漏锁单-Windows/'+name)
with zipfile.ZipFile(archive) as package:
    assert package.testzip() is None
    assert set(package.namelist())=={'拾漏锁单-Windows/拾漏锁单-安装.exe','拾漏锁单-Windows/使用说明.txt'}
    assert all(info.flag_bits & 0x800 for info in package.infolist())
print(json.dumps({'archive':str(archive),'sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),
                  'files':2,'bytes':archive.stat().st_size},ensure_ascii=False))

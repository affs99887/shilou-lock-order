"""Export reproducible source only; never export runtime settings or credentials."""
from pathlib import Path
import shutil
import tarfile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'deliverables/windows'
OUT.mkdir(parents=True,exist_ok=True)
trees=['src','src-tauri','crates','checkout','scripts','tests','public','assets','docs']
top=['.gitignore','Cargo.toml','Cargo.lock','package.json','pnpm-lock.yaml','components.json','index.html',
     'tsconfig.json','tsconfig.node.json','vite.config.ts','LICENSE','NOTICE','README.md','README.en.md','FORK.md']
allowed={'.py','.rs','.js','.cjs','.mjs','.ts','.tsx','.css','.html','.json','.toml','.yaml','.yml','.md','.txt','.png','.svg','.ico','.icns','.plist','.mp3','.wav','.ps1','.sh','.lock'}
paths=[ROOT/p for p in top]
for tree in trees:
    for p in (ROOT/tree).rglob('*'):
        relative=p.relative_to(ROOT)
        if not p.is_file() or any(x in {'build','target','__pycache__','node_modules','resources','.DS_Store','deliverables','ui-review'} or x.startswith(('.', 'qa-')) for x in relative.parts):continue
        if p.suffix not in allowed or p.name.startswith(('acceptance-','official-payment-observation-')):continue
        paths.append(p)
archive=OUT/'shilou-windows-source.tar.gz'
with tarfile.open(archive,'w:gz') as tar:
    for p in sorted(set(paths)):
        tar.add(p,arcname=str(p.relative_to(ROOT)),recursive=False)
print(archive)

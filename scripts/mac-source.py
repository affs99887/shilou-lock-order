"""Stage corresponding source and licenses; exclude settings and build outputs."""
from pathlib import Path
import shutil
import tarfile
import sys

root = Path(__file__).resolve().parents[1]
destination = root / 'src-tauri/resources' / ('OpenSourceInternational' if '--international' in sys.argv[1:] else 'OpenSource')
destination.mkdir(parents=True, exist_ok=True)
for name in ['LICENSE', 'NOTICE']:
    shutil.copy2(root / name, destination / name)
trees = ['src', 'src-tauri', 'crates', 'checkout', 'scripts', 'tests', 'public', 'assets', 'docs']
top = ['.gitignore', 'AGENTS.md', 'Cargo.toml', 'Cargo.lock', 'package.json', 'pnpm-lock.yaml',
       'components.json', 'index.html', 'tsconfig.json', 'tsconfig.node.json', 'vite.config.ts',
       'LICENSE', 'NOTICE', 'README.md', 'README.en.md', 'FORK.md']
extensions = {'.py', '.rs', '.js', '.cjs', '.mjs', '.ts', '.tsx', '.css', '.html', '.json',
              '.toml', '.yaml', '.yml', '.md', '.txt', '.png', '.svg', '.ico', '.icns', '.plist',
              '.mp3', '.wav', '.ps1', '.sh', '.lock'}
paths = [root / name for name in top if (root / name).is_file()]
for tree in trees:
    for path in (root / tree).rglob('*'):
        relative = path.relative_to(root)
        if not path.is_file() or any(part.startswith(('.', 'qa-')) or part in {'build', 'target', '__pycache__', 'node_modules', 'resources', 'deliverables', 'ui-review'} for part in relative.parts):
            continue
        if path.suffix not in extensions or path.name.startswith(('acceptance-', 'official-payment-observation-')):
            continue
        paths.append(path)
with tarfile.open(destination / 'Source.tar.gz', 'w:gz') as archive:
    for path in sorted(set(paths)):
        archive.add(path, arcname=str(path.relative_to(root)), recursive=False)
print('MAC_CORRESPONDING_SOURCE_READY')

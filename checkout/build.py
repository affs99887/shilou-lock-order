"""Build a private helper with its own Python runtime; users need no Python."""
from pathlib import Path
import subprocess
import sys
import os

ROOT = Path(__file__).resolve().parent
international = '--international' in sys.argv[1:]
name = 'apw-checkout-international' if international else 'apw-checkout'
work = ROOT / ('build/international' if international else 'build')
os.environ['PYINSTALLER_CONFIG_DIR'] = str(work / 'cache')
subprocess.run([sys.executable, str(ROOT.parent / 'scripts/checkout-catalog.py')], check=True)
platform_args = (['--collect-all', 'playwright', '--hidden-import', 'windows_vault',
                  '--hidden-import', 'edge_browser'] if sys.platform == 'win32'
                 else ['--hidden-import', 'AppKit'])
subprocess.run([sys.executable, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile',
    '--name', name, '--distpath', str(ROOT.parent / 'src-tauri/resources'),
    '--workpath', str(work), '--specpath', str(work),
    '--paths', str(ROOT / 'vendor'), '--add-data', str(ROOT / 'vendor/checkout_browser.js') + ':.',
    '--add-data', str(ROOT / 'vendor/regional_browser.js') + ':.',
    '--add-data', str(ROOT.parent / 'src/lib/checkout-regions.json') + ':.',
    '--add-data', str(ROOT.parent / 'src/lib/checkout-catalog.json') + ':.',
    '--hidden-import', 'pickup_monitor', '--hidden-import', 'payment_options',
    *platform_args, str(ROOT / 'worker.py')], check=True)

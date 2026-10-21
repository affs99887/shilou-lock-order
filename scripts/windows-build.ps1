$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$tools = Join-Path $env:LOCALAPPDATA 'ShilouBuildTools'
$env:RUSTUP_HOME = Join-Path $tools 'rustup'
$env:CARGO_HOME = Join-Path $tools 'cargo'
$env:PATH = "$env:CARGO_HOME\bin;$env:PATH"
$python = Join-Path $tools 'python\Scripts\python.exe'
$env:LIBCLANG_PATH = Join-Path $tools 'python\Lib\site-packages\clang\native'
$env:PYTHONUTF8 = '1'
$env:CARGO_BUILD_JOBS = '4'
$env:RUSTUP_TOOLCHAIN = '1.98.0'
if (!$env:CARGO_TARGET_DIR) { $env:CARGO_TARGET_DIR = "$root\target-windows-1.98" }
$env:PYTHONPATH = "$root\checkout;$root\checkout\vendor"
function CheckExit($message) { if ($LASTEXITCODE -ne 0) { throw $message } }
& $python -m unittest discover -s checkout -p 'test_*.py'
CheckExit 'Windows checkout tests failed'
& $python checkout/build.py
CheckExit 'Checkout helper build failed'
& "$root\src-tauri\resources\apw-checkout.exe" --self-test
CheckExit 'Packaged helper smoke test failed'
& $python scripts/windows-helper-smoke.py "$root\src-tauri\resources\apw-checkout.exe"
CheckExit 'Packaged IPC test failed'
New-Item -ItemType Directory -Force src-tauri/resources/OpenSource | Out-Null
Copy-Item LICENSE,NOTICE src-tauri/resources/OpenSource/
if (!(Test-Path src-tauri/resources/OpenSource/Source.tar.gz)) { throw 'Corresponding source missing' }
& npx.cmd --yes pnpm@9 install --frozen-lockfile
CheckExit 'Frontend dependency installation failed'
& npm.cmd test
CheckExit 'Frontend tests failed'
& node checkout/test_payment_dom.cjs
CheckExit 'Order guards failed'
& node checkout/test_pickup_dom.cjs
CheckExit 'Pickup selection guards failed'
& node checkout/test_product_dom.cjs
CheckExit 'Product loading guards failed'
& node checkout/test_regional_dom.cjs
CheckExit 'Regional selection guards failed'
& $python scripts/windows-edge-smoke.py
CheckExit 'Real Edge adapter fixtures failed'
& cargo test -p apw-core --locked
CheckExit 'Monitoring regression failed'
& npm.cmd run tauri -- build --config src-tauri/tauri.windows.conf.json --bundles nsis --no-sign
CheckExit 'Windows application build failed'
Write-Output 'WINDOWS_BUILD_READY'

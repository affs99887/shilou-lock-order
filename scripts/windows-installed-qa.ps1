$ErrorActionPreference = 'Stop'
$env:PYTHONUTF8 = '1'
$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $env:LOCALAPPDATA 'ShilouBuildTools\python\Scripts\python.exe'
$version = (Get-Content "$root\src-tauri\tauri.windows.conf.json" -Raw | ConvertFrom-Json).version
$reg = Get-ItemProperty HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\* |
    Where-Object { $_.DisplayVersion -eq $version } | Select-Object -First 1
if (!$reg) { throw 'QA installation not found' }
$installed = $reg.InstallLocation.Trim([char]34)
$ErrorActionPreference = 'Continue'
& $python "$root\scripts\windows-app-smoke.py" "$installed\apw-app.exe" "$root\qa-installed" *> "$root\qa-installed.log"
exit $LASTEXITCODE

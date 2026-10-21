$ErrorActionPreference = 'Stop'
$root = Join-Path $env:LOCALAPPDATA 'ShilouBuildTools'
New-Item -ItemType Directory -Force $root | Out-Null
$env:RUSTUP_HOME = Join-Path $root 'rustup'
$env:CARGO_HOME = Join-Path $root 'cargo'
if (!(Test-Path "$env:CARGO_HOME\bin\cargo.exe")) {
    Invoke-WebRequest 'https://win.rustup.rs/x86_64' -OutFile "$root\rustup-init.exe"
    & "$root\rustup-init.exe" -y --no-modify-path --profile minimal --default-toolchain stable
    if ($LASTEXITCODE -ne 0) { throw 'Rust installation failed' }
}
& "$env:CARGO_HOME\bin\rustup.exe" toolchain install 1.98.0 --profile minimal
if ($LASTEXITCODE -ne 0) { throw 'Pinned Rust installation failed' }
$vswhere = "${env:ProgramFiles(x86)}\Microsoft Visual Studio\Installer\vswhere.exe"
$vs = if (Test-Path $vswhere) { & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath }
if (!$vs) {
    Invoke-WebRequest 'https://aka.ms/vs/17/release/vs_BuildTools.exe' -OutFile "$root\vs_BuildTools.exe"
    $p = Start-Process "$root\vs_BuildTools.exe" -ArgumentList '--quiet --wait --norestart --nocache --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended' -Wait -PassThru
    if ($p.ExitCode -notin @(0,3010)) { throw "C++ tools installation failed: $($p.ExitCode)" }
}
py -3.12 -m venv "$root\python"
& "$root\python\Scripts\python.exe" -m pip install pyinstaller playwright certifi libclang
if ($LASTEXITCODE -ne 0) { throw 'Python build dependencies failed' }
Write-Output 'TOOLS_READY'

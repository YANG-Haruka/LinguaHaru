<#
Build the LinguaHaru PORTABLE distribution (embeddable Python + source + launchers).

Why portable (not PyInstaller): the app must run in a REAL Python so the Plugins
page can `pip install` heavy plugins (PDF/OCR/STT-torch) on demand and the model
folder can be dropped in — exactly the AstrBot/ComfyUI model. The frozen exe
can't pip-install later. This produces a small base (~300MB); plugins + models
are downloaded by the user afterward.

Layout produced (dist_portable/LinguaHaru/):
  python/      embeddable Python 3.12 + pip + base/web/qt deps (NO ML plugins)
  core/ webapp/ qt_app/ config/ plugins/ assets/ app_qt.py version.json ...
  models/      (created on first run; user drops models here)
  Start-Web.bat / Start-Desktop.bat

Usage:  pwsh -File tools/build_portable.ps1
#>
param(
  [string]$PyVer = "3.12.10",
  [string]$Out   = "dist_portable\LinguaHaru"
)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path "$PSScriptRoot\..").Path
$out  = Join-Path $repo $Out
$py   = Join-Path $out "python\python.exe"

Write-Host ">>> Clean output: $out"
Remove-Item -Recurse -Force $out -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Force $out | Out-Null

# --- 1. Embeddable Python -----------------------------------------------------
$zip = Join-Path $env:TEMP "py-embed-$PyVer.zip"
if (-not (Test-Path $zip)) {
  Write-Host ">>> Download Python $PyVer embeddable"
  Invoke-WebRequest "https://www.python.org/ftp/python/$PyVer/python-$PyVer-embed-amd64.zip" -OutFile $zip
}
Expand-Archive -Path $zip -DestinationPath (Join-Path $out "python") -Force

# --- 2. Enable site + pip + put the app root on sys.path ----------------------
# The embeddable ._pth is the ENTIRE sys.path (isolated mode; PYTHONPATH ignored).
# '..' = the portable root (next to python/) so core/webapp/qt_app import.
$pth = (Get-ChildItem (Join-Path $out "python") -Filter "python*._pth" | Select-Object -First 1).FullName
(Get-Content $pth) -replace '^#import site','import site' | Set-Content $pth
Add-Content $pth "Lib\site-packages"
Add-Content $pth ".."
$getpip = Join-Path $env:TEMP "get-pip.py"
if (-not (Test-Path $getpip)) { Invoke-WebRequest "https://bootstrap.pypa.io/get-pip.py" -OutFile $getpip }
$env:PYTHONNOUSERSITE = "1"   # isolate from %APPDATA%\Python so the base is self-contained
& $py $getpip --no-warn-script-location | Out-Null

# --- 3. Base deps (NO ML plugins) ---------------------------------------------
Write-Host ">>> pip install base + web + qt (isolated)"
& $py -m pip install --no-warn-script-location --no-user -q `
    -r (Join-Path $repo "requirements\base.txt") `
    -r (Join-Path $repo "requirements\web.txt") `
    -r (Join-Path $repo "requirements\qt.txt")
& $py -m pip cache purge 2>$null | Out-Null

# --- 4. Copy the app source ---------------------------------------------------
Write-Host ">>> Copy app source"
$items = @("core","webapp","qt_app","config","plugins","assets","app_qt.py","version.json",
           "README.md","README_ZH.md","README_JP.md","LICENSE")
foreach ($it in $items) {
  $src = Join-Path $repo $it
  if (Test-Path $src) { Copy-Item -Recurse -Force $src (Join-Path $out $it) }
}
# Strip __pycache__ from the copied source.
Get-ChildItem $out -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# --- 5. Launchers -------------------------------------------------------------
$web = @"
@echo off
rem LinguaHaru Web — open http://127.0.0.1:8080 in your browser
set PYTHONNOUSERSITE=1
cd /d "%~dp0"
python\python.exe -m webapp.server
pause
"@
Set-Content -Path (Join-Path $out "Start-Web.bat") -Value $web -Encoding ascii
$desk = @"
@echo off
rem LinguaHaru Desktop (Qt)
set PYTHONNOUSERSITE=1
cd /d "%~dp0"
start "" python\pythonw.exe app_qt.py
"@
Set-Content -Path (Join-Path $out "Start-Desktop.bat") -Value $desk -Encoding ascii

# --- 6. Report ----------------------------------------------------------------
$mb = "{0:N0} MB" -f ((Get-ChildItem -Recurse $out | Measure-Object Length -Sum).Sum/1MB)
Write-Host ">>> DONE. Portable base at: $out  ($mb)"

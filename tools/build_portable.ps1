<#
Build the LinguaHaru PORTABLE distribution (embeddable Python + source + launchers).

Why portable (not PyInstaller): the app must run in a REAL Python so the Plugins
page can `pip install` heavy plugins (PDF/OCR/STT-torch) on demand and the model
folder can be dropped in — exactly the AstrBot/ComfyUI model. The frozen exe
can't pip-install later. The base ships NO ML plugins; plugins + models are
downloaded by the user afterward.

Flavors:
  web      Web UI only (FastAPI). No PySide6 -> ~120 MB.
  desktop  Qt desktop UI. PySide6 (pruned of WebEngine/Quick/QML/3D) -> ~300 MB.
  both     builds both (default).

Layout (dist_portable/LinguaHaru-<flavor>/):
  python/   embeddable Python 3.12 + pip + deps for that flavor (NO ML plugins)
  core/ webapp|qt_app/ config/ plugins/ assets/ ... version.json
  Start-Web.bat | Start-Desktop.bat

Usage:  pwsh -File tools/build_portable.ps1 [-Flavor web|desktop|both]
#>
param(
  [string]$PyVer  = "3.12.10",
  [ValidateSet("web","desktop","both")][string]$Flavor = "both"
)
$ErrorActionPreference = "Stop"
$repo = (Resolve-Path "$PSScriptRoot\..").Path

# PySide6 Qt modules the app + qfluentwidgets actually use (everything else is
# pruned from the desktop flavor). Verified by grepping qt_app/ + qfluentwidgets.
$QtKeep = @("Core","Gui","Widgets","Svg","SvgWidgets","Multimedia",
            "MultimediaWidgets","Xml","Network","DBus")

function Build-Flavor([string]$flavor) {
  $out = Join-Path $repo ("dist_portable\LinguaHaru-" + $flavor)
  $py  = Join-Path $out "python\python.exe"
  Write-Host "============================================================"
  Write-Host ">>> Building '$flavor' -> $out"
  Remove-Item -Recurse -Force $out -ErrorAction SilentlyContinue
  New-Item -ItemType Directory -Force $out | Out-Null

  # 1. Embeddable Python
  $zip = Join-Path $env:TEMP "py-embed-$PyVer.zip"
  if (-not (Test-Path $zip)) {
    Invoke-WebRequest "https://www.python.org/ftp/python/$PyVer/python-$PyVer-embed-amd64.zip" -OutFile $zip
  }
  Expand-Archive -Path $zip -DestinationPath (Join-Path $out "python") -Force

  # 2. Enable site + pip; the embeddable ._pth IS the whole sys.path (PYTHONPATH
  #    is ignored), so add '..' (the portable root) for core/webapp/qt_app.
  $pth = (Get-ChildItem (Join-Path $out "python") -Filter "python*._pth" | Select-Object -First 1).FullName
  (Get-Content $pth) -replace '^#import site','import site' | Set-Content $pth
  Add-Content $pth "Lib\site-packages"
  Add-Content $pth ".."
  $getpip = Join-Path $env:TEMP "get-pip.py"
  if (-not (Test-Path $getpip)) { Invoke-WebRequest "https://bootstrap.pypa.io/get-pip.py" -OutFile $getpip }
  $env:PYTHONNOUSERSITE = "1"
  & $py $getpip --no-warn-script-location | Out-Null

  # 2b. Bundle uv next to python.exe so the Plugins page installs deps with uv
  #     (much faster than pip; core.module_manager._uv_exe() finds it here) and
  #     fall back to pip if absent. Also used for this build's own base install.
  $uvExe = Join-Path $out "python\uv.exe"
  $uvZip = Join-Path $env:TEMP "uv-win-x64.zip"
  if (-not (Test-Path $uvZip)) {
    Invoke-WebRequest "https://github.com/astral-sh/uv/releases/latest/download/uv-x86_64-pc-windows-msvc.zip" -OutFile $uvZip
  }
  $uvTmp = Join-Path $env:TEMP "uv-extract"
  Remove-Item -Recurse -Force $uvTmp -ErrorAction SilentlyContinue
  Expand-Archive $uvZip -DestinationPath $uvTmp -Force
  Copy-Item (Join-Path $uvTmp "uv.exe") $uvExe -Force

  # setuptools + wheel: the embeddable python ships NEITHER, so any later plugin
  # whose dependency builds from an sdist (no prebuilt wheel) fails with
  # "Cannot import 'setuptools.build_meta'". Needed for the Plugins page to install
  # such plugins at runtime.
  & $uvExe pip install --python $py -q setuptools wheel

  # 3. Base deps for this flavor (NO ML plugins) — installed with uv (fast).
  $reqs = @("-r", (Join-Path $repo "requirements\base.txt"))
  if ($flavor -eq "web")     { $reqs += @("-r", (Join-Path $repo "requirements\web.txt")) }
  if ($flavor -eq "desktop") { $reqs += @("-r", (Join-Path $repo "requirements\qt.txt")) }
  Write-Host ">>> uv pip install ($flavor) base deps"
  & $uvExe pip install --python $py @reqs

  # 3b. Prune PySide6 (desktop only): drop WebEngine/Quick/QML/3D + unused modules.
  if ($flavor -eq "desktop") {
    $ps = Join-Path $out "python\Lib\site-packages\PySide6"
    if (Test-Path $ps) {
      $before = (Get-ChildItem -Recurse $ps | Measure-Object Length -Sum).Sum/1MB
      # Remove module .pyd / Qt6*.dll whose module name isn't in the keep list.
      Get-ChildItem $ps -File | Where-Object {
        ($_.Name -match '^Qt6?(\w+?)\.(dll|pyd)$' -or $_.Name -match '^Qt(\w+?)\.pyd$') -and
        ($matches[1] -and $QtKeep -notcontains $matches[1])
      } | ForEach-Object {
        # keep core Qt6Core/Gui/Widgets explicitly (matched above), delete the rest
        Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
      }
      # Big always-unused trees + the 196 MB WebEngine + QML/Quick runtimes.
      foreach ($d in @("qml","Qt6\qml","resources","translations","examples",
                       "Qt6WebEngineCore.dll","QtWebEngineProcess.exe",
                       "opengl32sw.dll","Qt63D*.dll","Qt6Quick*.dll","Qt6Qml*.dll",
                       "Qt6Pdf*.dll","Qt6Designer*.dll","Qt6ShaderTools.dll",
                       "Qt63DRender.dll")) {
        Get-ChildItem -Path $ps -Filter $d -ErrorAction SilentlyContinue | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
      }
      $after = (Get-ChildItem -Recurse $ps | Measure-Object Length -Sum).Sum/1MB
      Write-Host (">>> PySide6 pruned: {0:N0} MB -> {1:N0} MB" -f $before,$after)
    }
  }

  # 4. App source for this flavor. glossary/ ships its Default.csv (top-level now).
  $common = @("core","config","plugins","assets","requirements","glossary","version.json","README.md","LICENSE")
  $ui = if ($flavor -eq "web") { @("webapp") } else { @("qt_app","app_qt.py") }
  foreach ($it in ($common + $ui)) {
    $src = Join-Path $repo $it
    if (Test-Path $src) { Copy-Item -Recurse -Force $src (Join-Path $out $it) }
  }
  Get-ChildItem $out -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
  # Never ship the developer's LOCAL runtime config / secrets / mutable data — only
  # the tracked default template (the app seeds system_config.json from it on first
  # run). Mutable runtime dirs (data/, result/, log/) are dev-local; scrub them all.
  Remove-Item (Join-Path $out "config\system_config.json") -Force -ErrorAction SilentlyContinue
  Remove-Item -Recurse -Force (Join-Path $out "data") -ErrorAction SilentlyContinue
  Remove-Item -Recurse -Force (Join-Path $out "result") -ErrorAction SilentlyContinue
  Remove-Item -Recurse -Force (Join-Path $out "log") -ErrorAction SilentlyContinue

  # 5. Launcher
  if ($flavor -eq "web") {
    $bat = "@echo off`r`nrem LinguaHaru Web - open http://127.0.0.1:8080`r`nset PYTHONNOUSERSITE=1`r`ncd /d `"%~dp0`"`r`npython\python.exe -m webapp.server`r`npause`r`n"
    Set-Content -Path (Join-Path $out "Start-Web.bat") -Value $bat -Encoding ascii
  } else {
    $bat = "@echo off`r`nrem LinguaHaru Desktop`r`nset PYTHONNOUSERSITE=1`r`ncd /d `"%~dp0`"`r`nstart `"`" python\pythonw.exe app_qt.py`r`n"
    Set-Content -Path (Join-Path $out "Start-Desktop.bat") -Value $bat -Encoding ascii
  }

  $mb = "{0:N0} MB" -f ((Get-ChildItem -Recurse $out | Measure-Object Length -Sum).Sum/1MB)
  Write-Host ">>> DONE '$flavor': $out  ($mb)"
}

if ($Flavor -eq "both") { Build-Flavor "web"; Build-Flavor "desktop" }
else { Build-Flavor $Flavor }

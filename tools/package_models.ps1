<#
Package each supported model into its OWN zip, so they can be hosted on a netdisk
and users download just the one(s) they need. Each zip's internal paths are
relative to models/, so the user unzips it INTO the app's models/ folder and the
model lands at the right place — ready to use, no Hugging Face needed.

Usage:  pwsh -File tools/package_models.ps1
Output: dist_models/<id>.zip  (+ dist_models/SHA256SUMS.txt)
#>
$ErrorActionPreference = "Stop"
Add-Type -AssemblyName System.IO.Compression.FileSystem
$repo = (Resolve-Path "$PSScriptRoot\..").Path
$models = Join-Path $repo "models"
$out = Join-Path $repo "dist_models"
New-Item -ItemType Directory -Force $out | Out-Null

# id -> @{ paths = <dirs relative to models/>; note = <what it's for> }
# Deps are bundled together (SenseVoice needs fsmn-vad; each OCR size needs the
# shared textline-orientation model). Unused Paddle extras are NOT packaged.
$VAD = "modelscope/models/iic/speech_fsmn_vad_zh-cn-16k-common-pytorch"
$TEXTLINE = "paddlex/official_models/PP-LCNet_x1_0_textline_ori"
$MANIFEST = [ordered]@{
  # --- STT (video subtitles / real-time voice) ---
  "stt-sensevoice-small"   = @{ paths = @("models--FunAudioLLM--SenseVoiceSmall", $VAD); note = "SenseVoice Small (+fsmn-vad). zh/en/ja/ko/yue, fast." }
  "stt-whisper-tiny"       = @{ paths = @("whisper/models--Systran--faster-whisper-tiny"); note = "faster-whisper tiny (~75MB)" }
  "stt-whisper-base"       = @{ paths = @("whisper/models--Systran--faster-whisper-base"); note = "faster-whisper base (~145MB)" }
  "stt-whisper-small"      = @{ paths = @("whisper/models--Systran--faster-whisper-small"); note = "faster-whisper small (~490MB)" }
  "stt-whisper-large-v3-turbo" = @{ paths = @("whisper/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo"); note = "faster-whisper large-v3-turbo (~1.6GB)" }
  "stt-whisper-large-v2"   = @{ paths = @("whisper/models--Systran--faster-whisper-large-v2"); note = "faster-whisper large-v2 (~3GB)" }
  "stt-anime-whisper"      = @{ paths = @("hub/models--litagin--anime-whisper"); note = "Anime-Whisper, expressive Japanese (~3GB)" }
  "stt-qwen3-asr-0.6b"     = @{ paths = @("hub/models--Qwen--Qwen3-ASR-0.6B"); note = "Qwen3-ASR 0.6B (~1.9GB)" }
  "stt-qwen3-asr-1.7b"     = @{ paths = @("hub/models--Qwen--Qwen3-ASR-1.7B"); note = "Qwen3-ASR 1.7B, most accurate (~4.7GB)" }
  # --- OCR (image translation); each size = det + rec + shared textline model ---
  "ocr-tiny"   = @{ paths = @("paddlex/official_models/PP-OCRv6_tiny_det",   "paddlex/official_models/PP-OCRv6_tiny_rec",   $TEXTLINE); note = "PP-OCRv6 tiny (fastest)" }
  "ocr-small"  = @{ paths = @("paddlex/official_models/PP-OCRv6_small_det",  "paddlex/official_models/PP-OCRv6_small_rec",  $TEXTLINE); note = "PP-OCRv6 small (default)" }
  "ocr-medium" = @{ paths = @("paddlex/official_models/PP-OCRv6_medium_det", "paddlex/official_models/PP-OCRv6_medium_rec", $TEXTLINE); note = "PP-OCRv6 medium (most accurate)" }
  # --- PDF layout (BabelDOC) + image inpaint (LaMa) ---
  # EVERYTHING the PDF plugin needs (BabelDOC layout model + fonts + cmaps +
  # tiktoken) — run tools/babeldoc_offline_assets.py first so the cache is
  # complete. babeldoc\assets holds the offline_assets_<tag>.zip — the same
  # fonts compressed AGAIN; shipping it would double the pack size.
  "pdf" = @{ paths = @("babeldoc"); exclude = @("babeldoc\assets"); note = "PDF translation pack: BabelDOC layout model + fonts + cmaps" }
  "image-inpaint-lama" = @{ paths = @("lama"); note = "LaMa inpaint (erase source text from images)" }
}

$sums = @()
foreach ($id in $MANIFEST.Keys) {
  $entry = $MANIFEST[$id]
  # Stage the model under a tree rooted at models/ so the zip preserves relative
  # paths (unzip into models/ -> lands correctly).
  $stage = Join-Path $out "_stage_$id"
  Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
  $missing = $false
  foreach ($p in $entry.paths) {
    $src = Join-Path $models $p
    if (-not (Test-Path $src)) { Write-Output ">>> SKIP $id (missing: $p)"; $missing = $true; break }
    $dst = Join-Path $stage $p
    New-Item -ItemType Directory -Force (Split-Path $dst) | Out-Null
    Copy-Item -Recurse -Force $src $dst
  }
  if ($missing) { Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue; continue }
  foreach ($ex in @($entry.exclude)) {
    if ($ex) {
      $p = Join-Path $stage $ex
      if (Test-Path $p) { Remove-Item -Recurse -Force $p }
    }
  }
  $zip = Join-Path $out "$id.zip"
  if (Test-Path $zip) {   # skip already-built (resume after a failure)
    Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
    $h0 = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
    $line = "{0,-32} {1,10}  {2}" -f "$id.zip", ("{0:N0} MB" -f ((Get-Item $zip).Length/1MB)), $h0
    $sums += $line; Write-Output ">>> (exists) $line"; continue
  }
  # ZipFile.CreateFromDirectory streams to disk + supports Zip64 (Compress-Archive
  # fails on >2GB with "Stream was too long"). Fastest: model weights are already
  # high-entropy, so Optimal barely shrinks them but costs a lot of time.
  [System.IO.Compression.ZipFile]::CreateFromDirectory(
    $stage, $zip, [System.IO.Compression.CompressionLevel]::Fastest, $false)
  Remove-Item -Recurse -Force $stage -ErrorAction SilentlyContinue
  $h = (Get-FileHash $zip -Algorithm SHA256).Hash.ToLower()
  $mb = "{0:N0} MB" -f ((Get-Item $zip).Length / 1MB)
  $line = "{0,-32} {1,10}  {2}" -f "$id.zip", $mb, $h
  $sums += $line
  Write-Output ">>> $line"
}
$sums | Set-Content (Join-Path $out "SHA256SUMS.txt") -Encoding ascii
Write-Output ">>> DONE. Per-model zips in: $out"

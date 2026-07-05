<div align="center">
  <img src="assets/img/ico.png" alt="LinguaHaru" height="180" />

  <h1>LinguaHaru</h1>

  <p>
    English | <a href="README_ZH.md">简体中文</a> | <a href="README_JP.md">日本語</a>
    <br/>
    <a href="https://github.com/YANG-Haruka/LinguaHaru/wiki/en-Home" target="_blank">User Guide (Wiki)</a>
  </p>

  <p>
    <img src="https://img.shields.io/github/v/release/YANG-Haruka/LinguaHaru?style=flat-square" alt="Release"/>
    <img src="https://img.shields.io/github/downloads/YANG-Haruka/LinguaHaru/total?style=flat-square" alt="Downloads"/>
    <img src="https://img.shields.io/github/license/YANG-Haruka/LinguaHaru?style=flat-square" alt="License"/>
    <img src="https://img.shields.io/github/stars/YANG-Haruka/LinguaHaru?style=flat-square" alt="Stars"/>
    <img src="https://img.shields.io/badge/python-3.12-blue?style=flat-square" alt="Python"/>
  </p>

  <p><b>One-click, high-quality AI translation for documents, subtitles, images, video and live speech.</b><br/>
  Two polished frontends (native desktop and web console) over one battle-tested engine — unzip and run, no installation.</p>
</div>

---

## Supported Formats

| Category | Formats |
|---|---|
| Office documents | DOCX · PPTX · XLSX · PDF |
| Text & e-books | TXT · Markdown · EPUB · HTML · ODT · JSON · CSV / TSV |
| Subtitles | SRT · VTT · ASS · SSA · LRC |
| Images (plugin) | PNG · JPG · WebP · BMP … including scanned manga |
| Video & audio (plugin) | MP4 · MKV · MP3 · WAV … automatic transcription plus subtitle translation |
| Live speech (plugin) | Microphone or system audio, translated as you speak |

## Why LinguaHaru

**Translation engine built for quality and speed.** Smart batching, concurrent requests with per-model rate control, translation caching, glossary enforcement, placeholder protection and post-translation QA checks. Atomic incremental writes make every job crash-safe; interrupted runs resume exactly where they stopped, and the 6.0 engine is 90%+ faster than the previous line.

**Two full frontends, one backend.**
- **Desktop** — native Windows app in Fluent Design (light/dark, floating live captions, drag-and-drop everywhere).
- **Web console** — the same features in your browser, with multi-user session isolation, LAN sharing and a public server mode.

**Everything heavy is a plugin.** The base download stays small; install only what you need from the in-app Plugins page. NVIDIA GPUs are detected automatically and the GPU runtimes (CUDA torch, onnxruntime-gpu) are installed for you. Uninstalling a plugin reclaims every dependency it brought in.

| Plugin | What it adds | GPU |
|---|---|---|
| PDF | Layout-preserving PDF translation (BabelDOC engine) | — |
| Image OCR | Image translation with text re-rendering; PP-OCRv6 | Auto (~60× faster) |
| Manga mode | Bubble detection, text erasing and re-lettering for scanned comics/PDFs, with proofread-and-re-export | Auto |
| Video / Audio | Speech transcription (SenseVoice, Whisper, Qwen3-ASR) and subtitle translation; ffmpeg bundled | Auto |
| Real-Time Voice | Streaming captions with sentence correction, hallucination guards and glossary enforcement | Auto |
| Voice input | Microphone input and read-aloud on the quick-translate page | Auto |

**Daily-driver conveniences.** Google-style quick translation with voice input and read-aloud; a full glossary manager (create / import / delete) applied across document, quick and live translation; per-project history with one-click resume; live token and cost estimation; an in-app proofreading editor with re-export; a first-run interactive tour; 13 fully localized UI languages.

**Safe and stable.** Fatal API errors (bad key, no balance) are detected and explained in your language; failed or stopped runs land in history and can be resumed; per-user session isolation and CSRF protection on the web; SHA-256-verified self-update with automatic rollback.

**Mainland-China friendly.** Dependencies, models and updates all fall back to fast mirrors automatically (Tsinghua PyPI, hf-mirror, ModelScope, Baidu BOS, ghproxy) — no VPN required.

## Quick Start

### Option A (recommended): portable build

No Python, no CUDA setup. Download, unzip, double-click.

1. Grab a package from [Releases](https://github.com/YANG-Haruka/LinguaHaru/releases/latest):
   - `LinguaHaru-*-desktop-portable.zip` — native desktop app
   - `LinguaHaru-*-web-portable.zip` — web console (opens your browser automatically)
2. Unzip anywhere, then run `Start-Desktop.bat` or `Start-Web.bat`.
3. In **Interface Management**, add an interface (e.g. DeepSeek), paste your API key, click the card to activate.
4. Need PDF / OCR / video / live voice? Install the plugin from the **Plugins** page — one click, GPU handled automatically.
5. When a new version ships, the app updates itself in place — plugins, models and settings are preserved.

### Option B: run from source

```bash
conda create -n lingua-haru python=3.12 && conda activate lingua-haru
pip install -r requirements/base.txt

pip install -r requirements/web.txt   # web:     python -m webapp.server
pip install -r requirements/qt.txt    # desktop: python app_qt.py
```

Optional plugins can be installed from the in-app Plugins page, or manually:

```bash
pip install -r plugins/pdf/requirements.txt       # PDF (BabelDOC)
pip install -r plugins/ocr/requirements.txt       # image OCR + manga mode
pip install -r plugins/video/requirements.txt     # video/audio + real-time voice
pip install -r plugins/speechio/requirements.txt  # voice input + read-aloud
```

### Recommended engine

**[DeepSeek](https://platform.deepseek.com/)'s latest Flash model** — fast, high-quality and inexpensive. Paste your API key into Interface Management and activate.

Local models (Ollama / LM Studio) are supported for offline or privacy-sensitive use, but online APIs are noticeably faster and better for most workloads.

## Preview

<div align="center">
  <img src="assets/img/sample.gif" width="80%" alt="Preview"/>
</div>

## Deployment

| Mode | How |
|---|---|
| Desktop | `Start-Desktop.bat` (portable) or `python app_qt.py` |
| Local web | `Start-Web.bat` (portable) or `python -m webapp.server` — auto-picks a free port from 8080 |
| LAN sharing | Toggle LAN mode in web Settings; every device on the network can use one host |
| Public server | Server mode hides key/model management from visitors and serves with the host's key |
| Docker | `docker compose up -d` — see `Dockerfile` / `docker-compose.yml`; honors `HOST` / `PORT` / `ADMIN_PASSWORD` |

## Project Structure

```
core/                Backend — all non-UI logic
  engine/            Translation engine (batching, retry, QA, caching)
  translators/       Per-format translator classes (docx, pptx, xlsx, pdf, srt, ...)
  pipelines/         Extract/restore pipelines + media (STT) / image (OCR) / manga
  llm/               LLM API wrappers (online / local)
webapp/              Web frontend — FastAPI + static HTML/CSS/JS
qt_app/              Desktop frontend — PySide6 + Fluent Widgets
plugins/             Optional-plugin manifests (installed on demand in-app)
config/              Static config — prompts, locales, api_config, default settings
assets/              Static assets — icons, images, tiktoken data
glossary/            Seed glossary (Default.csv)
requirements/        base.txt + per-frontend extras
tools/               Build scripts (portable builder)
tests/               Test suite (formats corpus, web sessions, i18n, updater, ...)
```

## Support & Contact

<div align="center">
  <a href="https://www.harukayang.com/" target="_blank">Homepage</a> ·
  <a href="https://www.linkedin.com/in/yang-haruka/" target="_blank">LinkedIn</a> ·
  QQ 3234306205 · WeChat HarukaQnQ
  <br/><br/>
  <img src="assets/img/support_qr.png" width="200" alt="Support QR"/>
  <p>If LinguaHaru saves you time, a coffee keeps the updates coming.<br/>
  Want the same combined WeChat + Alipay QR? See the <a href="https://www.harukayang.com/combined-pay.html" target="_blank">guide</a>.</p>
</div>

## Disclaimer

This software is fully open-source under the GPL-3.0 license and can be freely used.
It only provides AI-based translation services; the creator holds no responsibility for the translated content.
Please ensure your use complies with applicable laws and regulations.
Attribution is always appreciated and makes us happy~ but it's totally optional (´ω｀)♡

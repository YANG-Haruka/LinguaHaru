<div align="center">
  <img src="assets/img/ico.png" alt="LinguaHaru" id="title" style="height: 200px; width: auto;" />

English | [简体中文](README_ZH.md) | [日本語](README_JP.md) 
<br/><a href="https://github.com/YANG-Haruka/LinguaHaru/wiki/en-Home" target="_blank">📚 User Guide (Wiki)</a>


<div align=center><img src="https://img.shields.io/github/v/release/YANG-Haruka/LinguaHaru"/>   <img src="https://img.shields.io/github/license/YANG-Haruka/LinguaHaru"/>   <img src="https://img.shields.io/github/stars/YANG-Haruka/LinguaHaru"/></div>
<p align='center'>Next-generation AI translation tool that provides high-quality, precise translations for various common file formats with a single click</p>
<h3 align='center'>Supported File Formats</h3>
<p align='center'><b>📄 DOCX</b> • <b>📊 XLSX</b> • <b>📑 PPTX</b> • <b>📰 PDF</b> • <b>📝 TXT</b> • <b>🎬 SRT/ASS/VTT/LRC</b> • <b>📘 MD</b> • <b>📚 EPUB</b> • <b>🗂 CSV/TSV</b> • <b>🌐 HTML</b> • <b>📃 ODT</b> • <b>🔤 JSON</b></p>

</div>
<h2 id="What's This">What's This?</h2>
This translation tool is based on cutting-edge large language models, offering exceptional translation quality with minimal operation, supporting multiple document formats and languages.

It provides the following features:

- **Two frontends**: a Web UI (browser) and a Desktop app (Qt Fluent Design) over one shared backend — pick whichever you like.
- **Multi-format**: .docx / .pptx / .xlsx / .pdf / .txt / subtitles (srt/ass/vtt/lrc) / .md / .epub / csv / html / odt / json, plus images and video/audio.
- **Global languages**: 13+ languages (Chinese/English/Japanese/Korean/Russian, …); the UI is localized too.
- **One-click translation**: drag a file in; with translation modes (precise/general), glossary, bilingual output, and per-format options.
- **Home "Translate"**: Google-Translate-style quick short-text translation, with voice input and read-aloud.
- **Real-time voice**: translate as you speak, with automatic sentence splitting and live source/translation display.
- **Plugin system + market**: PDF, image OCR, video/audio subtitles, real-time voice and voice-input are optional plugins, installed on demand (bundled uv, fast); you can also download self-contained third-party plugins from a remote market without updating the main app.
- **Smart update** (portable build): one-click in-app update that preserves installed plugins, models and your settings/interfaces.
- **First-run onboarding**: an interactive spotlight tour of every page.
- **Flexible engines**: online APIs (DeepSeek / OpenAI, …) and local models (Ollama / LM Studio).
- **China-friendly**: HuggingFace / PyPI / GitHub auto-probe the official source and fall back to mirrors (hf-mirror / Tsinghua / ghproxy) when unreachable.
- **LAN sharing** (web only): one host, used by every device on the local network.


<h2 id="install">Installation and Usage</h2>

### Option A (recommended): portable build — unzip and run

No Python, no CUDA. Just download, unzip, double-click.

1. Download from [Releases](https://github.com/YANG-Haruka/LinguaHaru/releases/latest):
    - `LinguaHaru-web.zip` — Web UI (browser)
    - `LinguaHaru-desktop.zip` — Desktop (Qt)
2. Unzip anywhere.
3. Launch:
    - `Start-Web.bat` — **opens your browser automatically** (no need to type the URL)
    - `Start-Desktop.bat` — native window
4. In **Interface Management**, add an interface (e.g. DeepSeek), paste your API key, click the card to activate.
5. For PDF / image OCR / video subtitles / real-time voice, install the plugin **on demand** in the **Plugins** page (bundled uv = fast; auto-uses a China mirror when needed).
6. Models can be downloaded from the Plugins page, or fetched per-model from a netdisk (see [model guide](docs/MODELS.md)) and unzipped into the app's `models/` folder.

> When a new version is available, the portable build can **smart-update** in place, preserving installed plugins, models and your settings.

### Option B: run from source (dev / advanced)

1. Python 3.12 (a [Conda](https://www.anaconda.com/download) env is recommended)
    ```bash
    conda create -n lingua-haru python=3.12 && conda activate lingua-haru
    ```
2. Core deps + one frontend
    ```bash
    pip install -r requirements/base.txt
    pip install -r requirements/web.txt   # web:     python -m webapp.server  (http://127.0.0.1:8080)
    pip install -r requirements/qt.txt    # desktop: python app_qt.py
    ```
3. Optional plugins (or install them from the in-app Plugins page)
    ```bash
    pip install -r plugins/pdf/requirements.txt       # PDF (BabelDOC, layout-preserving)
    pip install -r plugins/ocr/requirements.txt       # image OCR
    pip install -r plugins/video/requirements.txt     # video/audio subtitles (ffmpeg bundled) + real-time voice
    pip install -r plugins/speechio/requirements.txt  # Translate-page voice input + read-aloud
    # Models download to models/ on first use (GPU speech transcription needs a CUDA build of torch).
    ```

### Local LLMs (optional)
Besides online APIs, local [Ollama](https://ollama.com/) / LM Studio are supported, e.g. `ollama pull qwen2.5`, then activate the local interface in Interface Management.

<h2 id="preview">Preview</h2>
<div align="center">
  <img src="assets/img/sample.gif" width="80%"/>
</div>


## Project Structure
Clear split: **`core/` = backend** (all non-UI logic), **`webapp/` + `qt_app/` = frontends**, **`config/` = static config**, **`assets/` = static assets**, **`data/` = mutable runtime state**.
```
core/                Backend — all non-UI logic
  engine/            Translation engine (base translator, response checker, splitter)
  translators/       Per-format translator classes (docx, pptx, xlsx, pdf, srt, ...)
  pipelines/         Per-format extract/restore + media (STT) / image (OCR)
  llm/               LLM API wrappers (online / offline)
  backend.py + services: languages, history, pricing, updater, api_keys,
                     optional_modules, module_manager, prompts, logging
webapp/              Web frontend — FastAPI (server.py) + static/ (HTML/CSS/JS)
qt_app/              Desktop frontend — PySide6 + Fluent Widgets
config/              Static config — system_config.json, api_config/, prompts/, locales/
assets/              Static assets — img/ (icons, gif), models/ (tiktoken BPE)
data/                Mutable runtime — temp/, result/, log/, web_uploads/ (gitignored);
                     glossary/ (tracked); mykeys/ (gitignored, local API keys)
requirements/        base.txt + per-feature extras (web, qt, ocr, pdf, video)
tests/               Test suite (corpus per format, qt, web sessions, i18n, ...)
```

## Reference Projects
- [ollama-python](https://github.com/ollama/ollama-python)
- [PDFMathTranslate](https://github.com/Byaidu/PDFMathTranslate)

## To-Do List
- Add continue translation functionality.

## Changelog
- 2026/06
**V5.1 update**: a new **portable build** (embedded Python, unzip-and-run); a **plugin market** (install on demand + download third-party plugins remotely); **smart update** (one-click, preserving plugins/models/settings); a first-run **onboarding tour**; the web build now **opens the browser automatically**; models can be **downloaded per-model from a netdisk**; automatic China-mirror fallback (HF/PyPI/GitHub); many translation-quality and stability fixes.
- 2026/01/28
V5.0 update: Updated PDF library. Optimized UI interface. Added more practical features. Thanks for a year of companionship!
- 2025/05/09
V3.0 update: Added multithreading and continuation translation features. Added translation support for Markdown files. Enhanced support for the Qwen3 series. Optimized log display.
- 2025/04/02  
Updated to v2.3, adding custom icons/Title and supporting multi-task queues. Optimized translation result detection logic. Added a feature to show the translation result with the original text.
- 2025/03/14
Updated to V2.0, added support for Txt files. Optimized Word/Excel/long text translation. Added customizable retry count functionality. Improved display of translation results.
- 2025/02/01  
Updated the processing logic for failed translations.
- 2025/01/15  
Fixed a bug in PDF translation, added multilingual support, and petted the kitty.
- 2025/01/11  
Added support for PDF. Reference project: [PDFMathTranslate](https://github.com/Byaidu/PDFMathTranslate)
- 2025/01/10    
Added support for deepseek-v3. Now you can use API for translation (more stable).  
Get API: https://www.deepseek.com/
- 2025/01/03  
Happy New Year! Revised logic, added review functionality, and enhanced logging.


## Software Disclaimer  
This software is fully open-source under the GPL-3.0 license and can be freely used.
It only provides AI-based translation services; the creator holds no responsibility for the translated content.
Please ensure your use complies with applicable laws and regulations.
Attribution is always appreciated and makes us happy~ but it's totally optional (´ω｀)♡

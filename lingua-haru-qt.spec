# -*- mode: python ; coding: utf-8 -*-
# pyinstaller lingua-haru-qt.spec
#
# Builds the LinguaHaru native DESKTOP app (PySide6 + qfluentwidgets). Entry point
# is app_qt.py. Shares the SAME core/ backend + translators + ML engines as the web
# build (lingua-haru.spec); only the UI layer differs (qt_app/ instead of webapp/).
# NOTE: after editing this spec, run an actual build to verify the bundle:
#     pyinstaller lingua-haru-qt.spec
# BUILD ENV: requires setuptools<81 (PyInstaller's altgraph imports pkg_resources,
# removed in setuptools 81+). Verified on the conda Python 3.12 env; conda stdlib
# DLLs are bundled explicitly below. ONEDIR (heavy ML stack -> no onefile).
import os
import sys
import glob

from PyInstaller.utils.hooks import collect_all

# chardet 7.x mypyc-compiled *.pyd are imported at the C level (invisible to the
# bytecode scanner) -> glob them all, else `import chardet` fails at runtime and
# every document format reports "Unsupported file type". See lingua-haru.spec.
import chardet as _cd
_cd_root = os.path.dirname(os.path.dirname(_cd.__file__))
chardet_pyds = [(p, os.path.relpath(os.path.dirname(p), _cd_root))
                for p in glob.glob(os.path.join(os.path.dirname(_cd.__file__), "**", "*.pyd"),
                                   recursive=True)]
# Grab chardet's data files (models/models.bin) + rich's dynamic width-data
# submodule (rich._unicode_data.unicodeNN-N-N) — both missed by the scanner.
chardet_collect = collect_all("chardet")
rich_collect = collect_all("rich")

# Conda keeps stdlib extension DLLs under <env>/Library/bin (see web spec).
CONDA_LIBBIN = os.path.join(os.path.dirname(sys.executable), "Library", "bin")
_CONDA_DLLS = [
    "ffi.dll", "ffi-8.dll", "ffi-7.dll",
    "libbz2.dll", "bz2.dll",
    "libexpat.dll", "expat.dll",
    "liblzma.dll",
    "sqlite3.dll",
]
conda_dll_binaries = [
    (os.path.join(CONDA_LIBBIN, n), ".")
    for n in _CONDA_DLLS if os.path.exists(os.path.join(CONDA_LIBBIN, n))
]


def filter_strings(items):
    return [item for item in items if isinstance(item, str)]


def filter_binaries(items):
    result = []
    for item in items:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            result.append((item[0], item[1]))
    return result


def filter_datas(items):
    result = []
    for item in items:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            result.append((item[0], item[1]))
    return result


def _safe_collect(name):
    try:
        return collect_all(name)
    except Exception:
        return ([], [], [])


# Desktop UI stack: PySide6 (handled mostly by PyInstaller's bundled hook) +
# qfluentwidgets (ships QSS / images / fonts as package data -> must collect) +
# darkdetect (theme detection).
qfluent_collect = _safe_collect("qfluentwidgets")
darkdetect_collect = _safe_collect("darkdetect")

# tiktoken (chunk budgeting) + BabelDOC (PDF) + onnxruntime (RapidOCR / LaMa).
tiktoken_collect = collect_all("tiktoken")
babeldoc_collect = collect_all("babeldoc")
onnxruntime_collect = collect_all("onnxruntime")

# Bundle ffmpeg (imageio-ffmpeg) so video/audio needs NO system ffmpeg.
try:
    imageio_ffmpeg_collect = collect_all("imageio_ffmpeg")
except Exception:
    imageio_ffmpeg_collect = ([], [], [])

# Optional ML engines (same as the web build): RapidOCR (ONNX), faster-whisper
# (ctranslate2), SenseVoice (funasr/torch), Qwen3-ASR (transformers), neural VAD,
# system-audio capture. Models themselves live in the writable data/models.
rapidocr_collect = _safe_collect("rapidocr")
faster_whisper_collect = _safe_collect("faster_whisper")
ctranslate2_collect = _safe_collect("ctranslate2")
funasr_collect = _safe_collect("funasr")
torch_collect = _safe_collect("torch")
torchaudio_collect = _safe_collect("torchaudio")
modelscope_collect = _safe_collect("modelscope")
qwen_asr_collect = _safe_collect("qwen_asr")
transformers_collect = _safe_collect("transformers")
ten_vad_collect = _safe_collect("ten_vad")
soundcard_collect = _safe_collect("soundcard")
edge_tts_collect = _safe_collect("edge_tts")   # 翻译 page read-aloud
_ENGINE_COLLECTS = [rapidocr_collect, faster_whisper_collect, ctranslate2_collect,
                    funasr_collect, torch_collect, torchaudio_collect, modelscope_collect,
                    qwen_asr_collect, transformers_collect,
                    ten_vad_collect, soundcard_collect, edge_tts_collect]


# Translators are loaded dynamically (importlib) by extension -> class string, so
# derive them from backend.TRANSLATOR_MODULES (every format gets bundled).
def _derive_translator_modules():
    try:
        from core.backend import TRANSLATOR_MODULES
        mods = set()
        for dotted_cls in TRANSLATOR_MODULES.values():
            mods.add(dotted_cls.rsplit(".", 1)[0])
        return sorted(mods)
    except Exception as e:
        print(f"[spec] WARN: could not derive translators ({e}); using static list")
        return [
            "core.translators.excel_translator", "core.translators.word_translator",
            "core.translators.ppt_translator", "core.translators.pdf_translator",
            "core.translators.subtitle_translator", "core.translators.txt_translator",
            "core.translators.md_translator", "core.translators.epub_translator",
            "core.translators.csv_translator", "core.translators.extra_formats_translator",
            "core.translators.image_translator", "core.translators.video_translator",
        ]


translator_modules = _derive_translator_modules()
print(f"[spec] bundling {len(translator_modules)} translator modules: {translator_modules}")

# Qt UI package: app_qt.py imports these statically (main_window -> pages/workers);
# onboarding is imported inside a method, so declare it explicitly to be safe.
qt_modules = [
    "qt_app", "qt_app.main_window", "qt_app.translate_page", "qt_app.quick_page",
    "qt_app.live_page", "qt_app.interface_page", "qt_app.glossary_page",
    "qt_app.proofread_page", "qt_app.history_page", "qt_app.plugins_page",
    "qt_app.settings_page", "qt_app.onboarding", "qt_app.worker",
    "qt_app.live_worker", "qt_app.progress_dashboard", "qt_app.sky_background",
    "qt_app.i18n",
    "core.backend", "core.api_keys",
]

# PySide6 submodules used beyond the core (SVG icons, multimedia for TTS playback).
pyside_modules = [
    "PySide6.QtSvg", "PySide6.QtSvgWidgets", "PySide6.QtMultimedia",
    "PySide6.QtNetwork",
]

all_hiddenimports = filter_strings(
    qfluent_collect[1]
    + darkdetect_collect[1]
    + tiktoken_collect[1]
    + babeldoc_collect[1]
    + onnxruntime_collect[1]
    + imageio_ffmpeg_collect[1]
    + sum((c[1] for c in _ENGINE_COLLECTS), [])
    + chardet_collect[1] + rich_collect[1]
    + translator_modules
    + qt_modules
    + pyside_modules
    + [
        'tiktoken', 'tiktoken.core', 'tiktoken.load', 'tiktoken.registry',
        'tiktoken_ext', 'tiktoken_ext.openai_public',
        'babeldoc', 'babeldoc.translator', 'babeldoc.format.pdf',
        'pymupdf', 'fitz',
        'docx', 'pptx', 'openpyxl', 'xlwings',
        'PIL', 'cv2',
    ]
)

all_binaries = filter_binaries(
    qfluent_collect[2]
    + babeldoc_collect[2]
    + onnxruntime_collect[2]
    + imageio_ffmpeg_collect[2]
    + sum((c[2] for c in _ENGINE_COLLECTS), [])
    + chardet_collect[2] + rich_collect[2]
) + chardet_pyds + conda_dll_binaries

all_datas = filter_datas(
    qfluent_collect[0]
    + darkdetect_collect[0]
    + tiktoken_collect[0]
    + babeldoc_collect[0]
    + onnxruntime_collect[0]
    + imageio_ffmpeg_collect[0]
    + sum((c[0] for c in _ENGINE_COLLECTS), [])
    + chardet_collect[0] + rich_collect[0]
) + [('assets/models/', 'assets/models/'), ('assets/img/', 'assets/img/'),
     ('assets/icons/', 'assets/icons/'),
     ('config/', 'config/'), ('glossary/', 'glossary/'),
     ('plugins/', 'plugins/')]

a = Analysis(
    ["app_qt.py"],
    pathex=[CONDA_LIBBIN] if os.path.isdir(CONDA_LIBBIN) else [],
    binaries=all_binaries,
    datas=all_datas,
    hiddenimports=all_hiddenimports,
    excludes=[
        'pytest', 'pytest_cov', 'coverage',
        'matplotlib',
        'IPython', 'jupyter', 'notebook', 'ipykernel',
        'tkinter', '_tkinter', 'tcl', 'tk',
        'gradio', 'gradio_client',
        # Web stack is not needed by the desktop app.
        'fastapi', 'uvicorn', 'starlette',
    ],
    module_collection_mode={
        "tiktoken": "py"
    },
)

pyz = PYZ(a.pure)

# ONEDIR (folder) — heavy ML stack; onefile would unpack GBs to temp each launch.
# console=False: native GUI, no console window. Distribute dist/LinguaHaru-Qt/ (zip).
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LinguaHaru-Qt",
    debug=False,
    upx=True,
    console=False,
    icon="assets/img/ico.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    upx=True,
    name="LinguaHaru-Qt",
)

# -*- mode: python ; coding: utf-8 -*-
# pyinstaller lingua-haru.spec
#
# Builds the LinguaHaru Web app (FastAPI + uvicorn + the custom static
# frontend). The entry point starts uvicorn (see webapp/server.py __main__).
# NOTE: after editing this spec, run an actual build to verify the bundle:
#     pyinstaller lingua-haru.spec
# BUILD ENV: requires setuptools<81 (PyInstaller's altgraph imports pkg_resources,
# which setuptools removed in 81+). Verified working on a conda Python 3.12 env;
# the conda-specific stdlib DLLs are bundled explicitly below.
import os
import sys
import glob

from PyInstaller.utils.hooks import collect_all

# chardet 7.x ships mypyc-compiled *.pyd (e.g. pipeline/orchestrator__mypyc.pyd)
# that its wrapper .pyd files import at the C level — invisible to PyInstaller's
# bytecode scanner, so they're missed and `import chardet` fails at runtime
# ("No module named chardet.pipeline.orchestrator__mypyc"), which made EVERY
# document format report "Unsupported file type". Glob ALL chardet .pyd explicitly.
import chardet as _cd
_cd_root = os.path.dirname(os.path.dirname(_cd.__file__))
chardet_pyds = [(p, os.path.relpath(os.path.dirname(p), _cd_root))
                for p in glob.glob(os.path.join(os.path.dirname(_cd.__file__), "**", "*.pyd"),
                                   recursive=True)]

# Conda keeps stdlib extension DLLs (ffi, bz2, lzma, sqlite3, expat) under
# <env>/Library/bin, which PyInstaller does not search by default - bundle them
# explicitly at the root so _ctypes/_bz2/_lzma/_sqlite3/pyexpat load.
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
    """Filter out non-string items from a list."""
    return [item for item in items if isinstance(item, str)]

def filter_binaries(items):
    """Ensure binaries are 2-tuples (src, dest)."""
    result = []
    for item in items:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            result.append((item[0], item[1]))
    return result

def filter_datas(items):
    """Ensure datas are 2-tuples (src, dest)."""
    result = []
    for item in items:
        if isinstance(item, (list, tuple)) and len(item) >= 2:
            result.append((item[0], item[1]))
    return result

# Collect the web stack (FastAPI / uvicorn / starlette + websockets for the
# Gemini Live proxy and multipart for uploads).
fastapi_collect = collect_all("fastapi")
starlette_collect = collect_all("starlette")
uvicorn_collect = collect_all("uvicorn")
websockets_collect = collect_all("websockets")
multipart_collect = collect_all("multipart")

# Collect tiktoken
tiktoken_collect = collect_all("tiktoken")

# Collect BabelDOC and dependencies (optional PDF module)
babeldoc_collect = collect_all("babeldoc")
onnxruntime_collect = collect_all("onnxruntime")

# Bundle the ffmpeg binary so the packaged app needs NO system/PATH ffmpeg.
# imageio-ffmpeg ships the executable under imageio_ffmpeg/binaries/.
try:
    imageio_ffmpeg_collect = collect_all("imageio_ffmpeg")
except Exception:
    imageio_ffmpeg_collect = ([], [], [])

# Optional ML engines bundled so the packaged app works out of the box:
#   - Image OCR: RapidOCR (pure ONNX; onnxruntime collected above). PaddleOCR is
#     intentionally NOT bundled — paddlepaddle is huge and hostile to PyInstaller;
#     RapidOCR is the lightweight packaged engine (run from source for Paddle).
#   - Speech (video subtitles + real-time voice): faster-whisper (ctranslate2) and
#     SenseVoice (funasr/torch). Models themselves are NOT bundled — they live in
#     the writable data/models next to the exe (downloaded / dropped in by the user).
def _safe_collect(name):
    try:
        return collect_all(name)
    except Exception:
        return ([], [], [])

rapidocr_collect = _safe_collect("rapidocr")
faster_whisper_collect = _safe_collect("faster_whisper")
ctranslate2_collect = _safe_collect("ctranslate2")
funasr_collect = _safe_collect("funasr")
torch_collect = _safe_collect("torch")
torchaudio_collect = _safe_collect("torchaudio")
modelscope_collect = _safe_collect("modelscope")
# Qwen3-ASR (transformers backend) + neural VAD / system-audio capture.
# _safe_collect returns empty if a package is absent/hostile, so the build never
# breaks; the features fall back gracefully at runtime when not bundled.
qwen_asr_collect = _safe_collect("qwen_asr")
transformers_collect = _safe_collect("transformers")
ten_vad_collect = _safe_collect("ten_vad")
soundcard_collect = _safe_collect("soundcard")
_ENGINE_COLLECTS = [rapidocr_collect, faster_whisper_collect, ctranslate2_collect,
                    funasr_collect, torch_collect, torchaudio_collect, modelscope_collect,
                    qwen_asr_collect, transformers_collect,
                    ten_vad_collect, soundcard_collect]

# Translators are loaded dynamically (importlib) by extension -> class string, so
# PyInstaller can't see them. Derive the module list straight from the source of
# truth (backend.TRANSLATOR_MODULES) so EVERY format (epub/csv/html/odt/json/vtt/
# ass/lrc/image/video/...) is bundled and nothing silently shows "unsupported".
def _derive_translator_modules():
    try:
        from core.backend import TRANSLATOR_MODULES
        mods = set()
        for dotted_cls in TRANSLATOR_MODULES.values():
            mods.add(dotted_cls.rsplit(".", 1)[0])   # strip the class name
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

webapp_modules = [
    "webapp", "webapp.server", "webapp.sessions",
    "core.backend", "core.api_keys",
]

# uvicorn loads its loop/protocol implementations lazily by string name, so
# they must be declared as hidden imports for the frozen build.
uvicorn_runtime = [
    "uvicorn.lifespan.on", "uvicorn.lifespan.off",
    "uvicorn.loops.auto", "uvicorn.loops.asyncio",
    "uvicorn.protocols.http.auto", "uvicorn.protocols.http.h11_impl",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.protocols.websockets.websockets_impl",
    "anyio", "h11",
]

# Combine all hiddenimports and filter non-strings
all_hiddenimports = filter_strings(
    fastapi_collect[1]
    + starlette_collect[1]
    + uvicorn_collect[1]
    + websockets_collect[1]
    + multipart_collect[1]
    + tiktoken_collect[1]
    + babeldoc_collect[1]
    + onnxruntime_collect[1]
    + imageio_ffmpeg_collect[1]
    + sum((c[1] for c in _ENGINE_COLLECTS), [])
    + translator_modules
    + webapp_modules
    + uvicorn_runtime
    + [
        'tiktoken', 'tiktoken.core', 'tiktoken.load', 'tiktoken.registry',
        'tiktoken_ext', 'tiktoken_ext.openai_public',
        'babeldoc', 'babeldoc.translator', 'babeldoc.format.pdf',
        'pymupdf', 'fitz',
        'docx', 'pptx', 'openpyxl', 'xlwings',
        'PIL', 'cv2',
        'scipy', 'scipy.ndimage', 'scipy.signal', 'scipy.special',
        'skimage', 'skimage.metrics',
    ]
)

# Filter binaries and datas to ensure proper tuple format
all_binaries = filter_binaries(
    babeldoc_collect[2]
    + onnxruntime_collect[2]
    + imageio_ffmpeg_collect[2]
    + sum((c[2] for c in _ENGINE_COLLECTS), [])
) + chardet_pyds + conda_dll_binaries

all_datas = filter_datas(
    fastapi_collect[0]
    + starlette_collect[0]
    + uvicorn_collect[0]
    + websockets_collect[0]
    + multipart_collect[0]
    + tiktoken_collect[0]
    + babeldoc_collect[0]
    + onnxruntime_collect[0]
    + imageio_ffmpeg_collect[0]
    + sum((c[0] for c in _ENGINE_COLLECTS), [])
) + [('assets/models/', 'assets/models/'), ('assets/img/', 'assets/img/'),
     ('assets/icons/', 'assets/icons/'), ('webapp/static/', 'webapp/static/'),
     ('config/', 'config/'), ('data/glossary/', 'data/glossary/')]

a = Analysis(
    ["webapp/server.py"],
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
    ],
    module_collection_mode={
        "tiktoken": "py"
    },
)

pyz = PYZ(a.pure)

# ONEDIR build (folder, not single exe): required for the heavy ML stack
# (torch/ctranslate2 etc.) — onefile would unpack ~GBs to a temp dir on every
# launch (30-60s cold start). Distribute the dist/LinguaHaru/ folder (zip it).
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LinguaHaru",
    debug=False,
    upx=True,
    console=True,
    icon="assets/img/ico.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    upx=True,
    name="LinguaHaru",
)

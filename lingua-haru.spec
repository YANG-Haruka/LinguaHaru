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

from PyInstaller.utils.hooks import collect_all

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

translator_modules = [
    "core.translators.excel_translator",
    "core.translators.word_translator",
    "core.translators.ppt_translator",
    "core.translators.pdf_translator",
    "core.translators.subtitle_translator",
    "core.translators.txt_translator",
    "core.translators.md_translator",
]

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
) + conda_dll_binaries

all_datas = filter_datas(
    fastapi_collect[0]
    + starlette_collect[0]
    + uvicorn_collect[0]
    + websockets_collect[0]
    + multipart_collect[0]
    + tiktoken_collect[0]
    + babeldoc_collect[0]
    + onnxruntime_collect[0]
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

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    name="LinguaHaru",
    debug=False,
    upx=True,
    console=True,
    icon="assets/img/ico.ico",
)

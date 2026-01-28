# -*- mode: python ; coding: utf-8 -*-
# pyinstaller lingua-haru.spec
from PyInstaller.utils.hooks import collect_all

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

# Collect gradio packages
gradio_collect = collect_all("gradio")
gradio_client_collect = collect_all("gradio_client")
safehttpx_collect = collect_all("safehttpx")
groovy_collect = collect_all("groovy")

# Collect tiktoken
tiktoken_collect = collect_all("tiktoken")

# Collect BabelDOC and dependencies
babeldoc_collect = collect_all("babeldoc")
onnxruntime_collect = collect_all("onnxruntime")

translator_modules = [
    "translator.excel_translator",
    "translator.word_translator",
    "translator.ppt_translator",
    "translator.pdf_translator",
    "translator.subtitle_translator",
    "translator.txt_translator",
    "translator.md_translator",
]

# Combine all hiddenimports and filter non-strings
all_hiddenimports = filter_strings(
    gradio_collect[1]
    + gradio_client_collect[1]
    + safehttpx_collect[1]
    + groovy_collect[1]
    + tiktoken_collect[1]
    + babeldoc_collect[1]
    + onnxruntime_collect[1]
    + translator_modules
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
)

all_datas = filter_datas(
    gradio_collect[0]
    + gradio_client_collect[0]
    + safehttpx_collect[0]
    + groovy_collect[0]
    + tiktoken_collect[0]
    + babeldoc_collect[0]
    + onnxruntime_collect[0]
) + [('models/', 'models/')]

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=all_binaries,
    datas=all_datas,
    hiddenimports=all_hiddenimports,
    excludes=[
        'pytest', 'pytest_cov', 'coverage',
        'matplotlib',
        'IPython', 'jupyter', 'notebook', 'ipykernel',
        'tkinter', '_tkinter', 'tcl', 'tk',
    ],
    module_collection_mode={
        "gradio": "py",
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
    icon="img/ico.ico",
)

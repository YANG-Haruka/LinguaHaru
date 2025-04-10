# -*- mode: python ; coding: utf-8 -*-
# pyinstaller lingua-haru.spec  
from PyInstaller.utils.hooks import collect_all

#  gradio、gradio_client、safehttp、safehttpx
gradio_collect = collect_all("gradio")
gradio_client_collect = collect_all("gradio_client")
safehttp_collect = collect_all("safehttp")
safehttpx_collect = collect_all("safehttpx")
groovy_collect = collect_all("groovy")

translator_modules = [
    "translator.word_translator", 
    "translator.ppt_translator",
    "translator.excel_translator",
    "translator.pdf_translator",
    "translator.subtile_translator",
    "translator.txt_translator",
    "translator.word_translator_bilingual",
    "translator.excel_translator_test",
    "translator"
]

translator_collects = []
for module in translator_modules:
    try:
        translator_collects.append(collect_all(module))
    except Exception:
        print(f"Warning: Could not collect {module}")

translator_datas = []
translator_imports = []
for collect in translator_collects:
    translator_datas.extend(collect[0])
    translator_imports.extend(collect[1])

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=(
        gradio_collect[0]
        + gradio_client_collect[0]
        + safehttp_collect[0]
        + safehttpx_collect[0]
        + groovy_collect[0]
        + translator_datas
    ),
    hiddenimports=(
        gradio_collect[1]
        + gradio_client_collect[1]
        + safehttp_collect[1]
        + safehttpx_collect[1]
        + groovy_collect[1]
        + translator_imports
        + translator_modules
    ),
    excludes=[],
    module_collection_mode={"gradio": "py"},
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
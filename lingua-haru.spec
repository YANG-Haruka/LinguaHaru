# -*- mode: python ; coding: utf-8 -*-
# pyinstaller lingua-haru.spec  
from PyInstaller.utils.hooks import collect_all

#  gradio、gradio_client、safehttp、safehttpx
gradio_collect = collect_all("gradio")
gradio_client_collect = collect_all("gradio_client")
safehttp_collect = collect_all("safehttp")
safehttpx_collect = collect_all("safehttpx")

a = Analysis(
    ["app.py"],
    pathex=[],
    binaries=[],
    datas=(
        gradio_collect[0]
        + gradio_client_collect[0]
        + safehttp_collect[0]
        + safehttpx_collect[0]
    ),
    hiddenimports=(
        gradio_collect[1]
        + gradio_client_collect[1]
        + safehttp_collect[1]
        + safehttpx_collect[1]
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

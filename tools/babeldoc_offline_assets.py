#!/usr/bin/env python
"""Release tooling: build the BabelDOC offline assets package (PDF fonts pack).

Completes the local BabelDOC asset cache (fonts + cmap + tiktoken + DocLayout
onnx) by running babeldoc's warmup, then generates babeldoc's own offline
package at models/babeldoc/assets/offline_assets_<tag>.zip. That is the ONE
location babeldoc's restore looks in by default, and the app restores from it
automatically (see model_store.restore_babeldoc_offline_assets), so a user who
can't reach github/HF never downloads fonts mid-translation.

Run before `package_models.ps1` so pdf.zip ships a COMPLETE cache:

    D:\\Software\\Miniconda3\\envs\\lingua-haru\\python.exe tools/babeldoc_offline_assets.py

Needs network (the warmup downloads whatever the cache is missing).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import model_store


def main():
    target = model_store.redirect_babeldoc_cache()
    if not target:
        print("ERROR: could not redirect the BabelDOC cache", file=sys.stderr)
        return 1
    from babeldoc.assets import assets

    print(f"[1/2] warmup: completing asset cache at {target} ...")
    assets.warmup()

    print("[2/2] generating offline assets package ...")
    assets.generate_offline_assets_package()

    tag = assets.get_offline_assets_tag()
    zip_path = os.path.join(target, "assets", f"offline_assets_{tag}.zip")
    size_mb = os.path.getsize(zip_path) / 1024 / 1024
    print(f"DONE: {zip_path}  ({size_mb:.0f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

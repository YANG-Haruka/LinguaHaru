"""LinguaHaru native desktop app (Qt + Fluent Design).

The native desktop experience: reuses LinguaHaru's translation backend
directly and never imports the Gradio web app.

    pip install -r requirements-qt.txt
    python app_qt.py
"""

import os
import sys
from pathlib import Path


def _patch_tiktoken():
    """Use bundled tiktoken BPE files when present (mirrors app.py)."""
    try:
        import tiktoken.load
    except ImportError:
        return
    tiktoken_dir = Path(__file__).parent / "models" / "tiktoken"
    if not tiktoken_dir.exists():
        return
    mapping = {
        "o200k_base.tiktoken": tiktoken_dir / "o200k_base.tiktoken",
        "cl100k_base.tiktoken": tiktoken_dir / "cl100k_base.tiktoken",
    }
    original = tiktoken.load.read_file_cached

    def patched(blobpath, expected_hash=None):
        for pattern, local_path in mapping.items():
            if pattern in blobpath and local_path.exists():
                with open(local_path, "rb") as f:
                    return f.read()
        return original(blobpath, expected_hash)

    tiktoken.load.read_file_cached = patched


_patch_tiktoken()

# Run with the repo root as cwd so config/glossary/temp resolve as expected.
os.chdir(os.path.dirname(os.path.abspath(__file__)))


def _install_qt_log_filter():
    """Silence the benign 'QFont::setPointSize: Point size <= 0 (-1)' spam.

    It originates inside qfluentwidgets (a font built with setPixelSize has
    pointSize()==-1, which is then fed back into setPointSize); it is harmless
    and not from our code. Filter just that line; pass everything else through.
    """
    from PySide6.QtCore import qInstallMessageHandler

    def handler(mode, context, message):
        if "setPointSize" in message and "Point size" in message:
            return
        sys.stderr.write(message + "\n")

    qInstallMessageHandler(handler)


def main():
    import multiprocessing
    multiprocessing.freeze_support()

    from PySide6.QtWidgets import QApplication
    from qt_app.main_window import MainWindow

    _install_qt_log_filter()
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

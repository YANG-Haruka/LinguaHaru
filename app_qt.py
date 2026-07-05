"""LinguaHaru native desktop app (Qt + Fluent Design).

The native desktop experience: reuses LinguaHaru's translation backend
directly and never imports the Gradio web app.

    pip install -r requirements/qt.txt
    python app_qt.py
"""

import os
import sys
from pathlib import Path


def _patch_tiktoken():
    """Use bundled tiktoken BPE files when present."""
    try:
        import tiktoken.load
    except ImportError:
        return
    tiktoken_dir = Path(__file__).parent / "assets" / "models" / "tiktoken"
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

# Point all model libraries (whisper/funasr/babeldoc/OCR) at the unified
# data/models cache BEFORE any of them are imported. defer_network=True skips
# the slow HF-endpoint probe + legacy-cache migration here; main() runs them
# (plus the local Ollama/LM Studio scan) in a background thread AFTER the window
# is shown, so the UI appears instantly.
from core.model_store import setup_model_env  # noqa: E402
setup_model_env(defer_network=True)
try:   # let downloaded market plugins hook into the app (best-effort)
    from core import plugins_registry as _pr  # noqa: E402
    _pr.activate_downloaded_plugins()
except Exception:  # noqa: BLE001
    pass


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

    # Record uncaught exceptions (main + worker threads) in the system log.
    try:
        from core.log_config import install_excepthooks, system_event
        install_excepthooks()
        system_event("LinguaHaru desktop starting")
    except Exception:  # noqa: BLE001
        pass

    # Don't let Windows throttle our CPU when the window is minimized/backgrounded
    # — translation and real-time voice keep running at full speed (process-wide).
    try:
        from core.power import disable_background_throttling
        disable_background_throttling()
    except Exception:  # noqa: BLE001
        pass

    # Recover history rows left "running" by a previous crash / force-quit:
    # flip them to "interrupted" so they show up (and can be continued).
    try:
        from core import backend
        from core.translation_history import TranslationHistoryManager
        _n = TranslationHistoryManager(
            log_dir=backend.history_dir()).mark_running_as_interrupted()
        if _n:
            print(f"Recovered {_n} interrupted translation(s) from a previous session")
    except Exception:  # noqa: BLE001 — never block startup on history recovery
        pass

    # Build the UI WITHOUT the slow startup work: skip the local Ollama/LM Studio
    # probe during page construction (it runs in the background warm-up below).
    try:
        from core.llm.offline_translation import defer_local_scan
        defer_local_scan()
    except Exception:  # noqa: BLE001
        pass

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    # Now that the window is visible, do the deferred slow work off the UI thread
    # (HF-endpoint probe, legacy-cache migration, local-model scan, disk retention)
    # and refresh the affected pages when done — so startup feels instant.
    _start_background_warmup(window)
    sys.exit(app.exec())


def _start_background_warmup(window):
    from PySide6.QtCore import QThread, QTimer

    class _Warmup(QThread):
        def run(self):
            try:
                from core.model_store import finish_model_env_setup
                finish_model_env_setup()
            except Exception:  # noqa: BLE001
                pass
            try:
                from core import backend
                backend.scan_local_models(force_refresh=True)
            except Exception:  # noqa: BLE001
                pass
            try:
                from core.retention import run_retention
                run_retention()
            except Exception:  # noqa: BLE001
                pass

    def _refresh():
        # Reflect any local models found + the resolved active interface.
        for fn in (
            lambda: window.interface_page.reload(),
            lambda: window.translate_page.refresh_active_interface(),
        ):
            try:
                fn()
            except Exception:  # noqa: BLE001
                pass

    w = _Warmup(window)            # parented -> Qt owns it (no GC-while-running)
    w.finished.connect(_refresh)
    window._warmup_worker = w      # keep a reference; closeEvent sweep waits on it
    # Small delay so the first paint happens before we spin up the worker.
    QTimer.singleShot(80, w.start)


if __name__ == "__main__":
    main()

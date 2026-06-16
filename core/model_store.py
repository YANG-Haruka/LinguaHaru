"""Central, single-location store for all downloaded ML models.

Every engine (SenseVoice/funasr, faster-whisper, BabelDOC layout model, OCR)
downloads into ONE folder — `data/models` by default, or a user-chosen path in
system_config ("models_dir"). `setup_model_env()` points the libraries' cache
env vars there and must run BEFORE those libraries are imported. The Settings
"Model Management" view uses `list_models()` / `current_dir()`, and a location
change can `migrate_to()` the existing files.
"""
import os
import shutil

from core.paths import DATA_DIR, SYSTEM_CONFIG
from core.log_config import app_logger

_DEFAULT = os.path.join(DATA_DIR, "models")


def _read_cfg():
    try:
        import json
        with open(SYSTEM_CONFIG, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {}


def current_dir():
    """The configured models directory (absolute), defaulting to data/models."""
    cfg = _read_cfg()
    d = cfg.get("models_dir") or _DEFAULT
    return os.path.abspath(d)


def pick_hf_endpoint():
    """Choose the huggingface endpoint for model downloads.

    Order: explicit HF_ENDPOINT env  ->  config "hf_endpoint"  ->  probe: prefer
    the official endpoint when reachable, fall back to the China mirror
    (hf-mirror.com) only when it isn't. The blind mirror default used to stall
    EVERY download whenever hf-mirror was flaky/down (and slowed users who can
    reach huggingface.co directly). Probed once; hf libs read HF_ENDPOINT at
    import, so this must run before they're imported."""
    explicit = os.environ.get("HF_ENDPOINT")
    if explicit:
        return explicit
    try:
        configured = _read_cfg().get("hf_endpoint")
        if configured:
            return configured
    except Exception:  # noqa: BLE001
        pass
    # One short probe of the official endpoint only (so offline/firewalled first
    # launches wait ~2s, not 6s). Reachable -> official; otherwise fall back to
    # the China mirror without a second probe. Set HF_ENDPOINT or config
    # "hf_endpoint" to skip probing entirely.
    import urllib.request
    try:
        urllib.request.urlopen(
            urllib.request.Request("https://huggingface.co", method="HEAD"), timeout=2)
        app_logger.info("HF endpoint: https://huggingface.co")
        return "https://huggingface.co"
    except Exception:  # noqa: BLE001 — unreachable/blocked -> mirror
        app_logger.info("HF endpoint: https://hf-mirror.com (official unreachable)")
        return "https://hf-mirror.com"


def setup_model_env():
    """Point every model library's cache at the unified dir. Idempotent; uses
    setdefault so an explicit user env var still wins. Call at app startup,
    BEFORE the model libraries are imported (they read these env vars at import).

    BabelDOC has no cache env var (its CACHE_FOLDER is a hardcoded module
    global); it is redirected separately by `redirect_babeldoc_cache()`, called
    from the PDF translator just before its first run."""
    md = current_dir()
    os.makedirs(md, exist_ok=True)
    # faster-whisper + any huggingface_hub download
    os.environ.setdefault("HF_HOME", md)
    os.environ.setdefault("HUGGINGFACE_HUB_CACHE", os.path.join(md, "hub"))
    # funasr/modelscope fallback
    os.environ.setdefault("MODELSCOPE_CACHE", os.path.join(md, "modelscope"))
    # PaddleOCR / PaddleX official models (PP-OCRv6 etc.) -> md/paddlex/official_models
    os.environ.setdefault("PADDLE_PDX_CACHE_HOME", os.path.join(md, "paddlex"))
    # Pick the huggingface endpoint once (hf libs read HF_ENDPOINT at import).
    if "HF_ENDPOINT" not in os.environ:
        os.environ["HF_ENDPOINT"] = pick_hf_endpoint()
    # One-time pull-in of models already downloaded to the OLD default caches.
    try:
        migrate_legacy_caches()
    except Exception as e:  # noqa: BLE001 — never let migration break startup
        app_logger.warning(f"Legacy cache migration skipped: {e}")
    return md


def _move_tree_contents(old, new, summary):
    """Move every top-level entry from `old` into `new`, entry-by-entry.

    Skips (does not clobber) any entry that already exists in `new`. Best-effort:
    per-entry errors are caught and recorded, never raised. Records relocations
    in `summary['moved']` and skips in `summary['skipped']`."""
    try:
        entries = os.listdir(old)
    except OSError as e:
        summary["skipped"].append(f"{old} (listdir failed: {e})")
        return
    os.makedirs(new, exist_ok=True)
    for entry in entries:
        src = os.path.join(old, entry)
        dst = os.path.join(new, entry)
        if os.path.exists(dst):
            summary["skipped"].append(dst + " (exists)")
            continue
        try:
            shutil.move(src, dst)
            summary["moved"].append(dst)
        except Exception as e:  # noqa: BLE001
            summary["skipped"].append(f"{src} (move failed: {e})")


def migrate_legacy_caches():
    """One-time, idempotent, best-effort move of models from the OLD default
    cache locations into the unified models dir, so they aren't re-downloaded.

    Pairs migrated:
      ~/.paddlex        -> <md>/paddlex
      ~/.cache/babeldoc -> <md>/babeldoc

    Guarded by a marker file (`<md>/.legacy_migrated`) so it runs at most once.
    Moves entry-by-entry; never clobbers existing files in the destination and
    never raises. Returns a summary dict {moved: [...], skipped: [...]}.
    """
    summary = {"moved": [], "skipped": []}
    md = current_dir()
    marker = os.path.join(md, ".legacy_migrated")
    if os.path.exists(marker):
        summary["skipped"].append(marker + " (already migrated)")
        return summary

    home = os.path.expanduser("~")
    pairs = [
        (os.path.join(home, ".paddlex"), os.path.join(md, "paddlex")),
        (os.path.join(home, ".cache", "babeldoc"), os.path.join(md, "babeldoc")),
    ]

    os.makedirs(md, exist_ok=True)
    for old, new in pairs:
        try:
            # Old must be a real directory (skip missing, files, and symlinks).
            if not os.path.isdir(old) or os.path.islink(old):
                summary["skipped"].append(old + " (absent or not a real dir)")
                continue
            if os.path.abspath(old) == os.path.abspath(new):
                summary["skipped"].append(old + " (same as destination)")
                continue
            _move_tree_contents(old, new, summary)
        except Exception as e:  # noqa: BLE001 — best-effort, per-pair guard
            summary["skipped"].append(f"{old} (error: {e})")

    # Mark done even if some entries were skipped, so we don't retry every start.
    try:
        with open(marker, "w", encoding="utf-8") as f:
            f.write("legacy caches migrated\n")
    except Exception as e:  # noqa: BLE001
        app_logger.warning(f"Could not write migration marker {marker}: {e}")

    if summary["moved"]:
        app_logger.info(
            f"Migrated {len(summary['moved'])} legacy model entrie(s) into {md}")
    return summary


def redirect_babeldoc_cache():
    """Point BabelDOC's hardcoded ~/.cache/babeldoc at the unified models dir.

    BabelDOC exposes no cache env var: `babeldoc.const.CACHE_FOLDER` is a module
    global read at call time by `get_cache_file_path()`. We repoint it (and the
    import-time tiktoken cache it derives) AFTER importing babeldoc but BEFORE
    the first PDF translation downloads the DocLayout model / fonts. Best-effort:
    any failure leaves BabelDOC on its own default and never blocks PDF."""
    try:
        import babeldoc.const as const
        target = os.path.join(current_dir(), "babeldoc")
        os.makedirs(target, exist_ok=True)
        from pathlib import Path
        const.CACHE_FOLDER = Path(target)
        tk = os.path.join(target, "tiktoken")
        os.makedirs(tk, exist_ok=True)
        const.TIKTOKEN_CACHE_FOLDER = Path(tk)
        os.environ["TIKTOKEN_CACHE_DIR"] = tk
        app_logger.info(f"BabelDOC cache redirected to {target}")
        return target
    except Exception as e:  # noqa: BLE001
        app_logger.warning(f"Could not redirect BabelDOC cache: {e}")
        return None


def whisper_dir():
    """Download root for faster-whisper models (under the unified dir)."""
    d = os.path.join(current_dir(), "whisper")
    os.makedirs(d, exist_ok=True)
    return d


def _dir_size(path):
    total = 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def human_size(n):
    size = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if size < 1024 or unit == "TB":
            return f"{int(size)} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024.0


# Known model folders -> friendly label (matched by substring on the dir name).
_KNOWN = [
    ("SenseVoiceSmall", "SenseVoice (语音转文字)"),
    ("whisper", "Faster-Whisper (语音转文字)"),
    ("faster-whisper", "Faster-Whisper (语音转文字)"),
    ("fsmn-vad", "FSMN-VAD (语音断句)"),
    ("DocLayout", "BabelDOC 版面模型 (PDF)"),
    ("doclayout", "BabelDOC 版面模型 (PDF)"),
    ("babeldoc", "BabelDOC 模型/字体 (PDF)"),
    ("paddlex", "PaddleOCR (图片识别)"),
    ("PP-OCR", "PaddleOCR (图片识别)"),
    ("rapidocr", "RapidOCR (图片识别)"),
]


def list_models():
    """Downloaded models as dicts: {name, label, path, size, size_h}. Scans the
    unified dir one or two levels deep for recognizable model folders."""
    base = current_dir()
    found = []
    seen = set()
    if not os.path.isdir(base):
        return found

    def _label(name):
        for key, lbl in _KNOWN:
            if key.lower() in name.lower():
                return lbl
        return name

    # Walk a bounded depth so huge trees don't stall the UI.
    for root, dirs, _files in os.walk(base):
        # Skip HF bookkeeping dirs (.locks/.cache) entirely.
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        depth = root[len(base):].count(os.sep)
        if depth >= 3:
            dirs[:] = []
            continue
        for d in list(dirs):
            full = os.path.join(root, d)
            if full in seen:
                continue
            if any(k.lower() in d.lower() for k, _ in _KNOWN):
                size = _dir_size(full)
                if size <= 0:
                    continue  # empty placeholder dir, not actually downloaded
                found.append({"name": d, "label": _label(d), "path": full,
                              "size": size, "size_h": human_size(size)})
                seen.add(full)
                dirs.remove(d)  # don't descend into a counted model
    found.sort(key=lambda m: m["label"])
    return found


def find_model_dirs(substrings):
    """Map each probe substring -> list of model-dir paths under the models tree
    whose folder name contains it (case-insensitive) and which is non-empty.
    Used to tell whether a specific model id is downloaded, and what to delete."""
    base = current_dir()
    subs = [s.lower() for s in substrings if s]
    hits = {s: [] for s in subs}
    if not subs or not os.path.isdir(base):
        return hits
    for root, dirs, _files in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        depth = root[len(base):].count(os.sep)
        if depth >= 6:
            dirs[:] = []
            continue
        for d in list(dirs):
            dl = d.lower()
            for s in subs:
                if s in dl:
                    full = os.path.join(root, d)
                    if _dir_size(full) > 0:
                        hits[s].append(full)
                    dirs.remove(d)      # counted; don't descend into it
                    break
    return hits


def delete_model_dirs(substrings):
    """Remove every model dir matching any of the probe substrings. Returns the
    number of directories removed."""
    removed = 0
    for paths in find_model_dirs(substrings).values():
        for p in paths:
            try:
                shutil.rmtree(p)
                removed += 1
            except OSError:
                pass
    return removed


def set_models_dir(new_dir, move=False):
    """Persist a new models directory. If move=True, relocate existing model
    files there first. Returns (ok, message)."""
    new_dir = os.path.abspath(new_dir)
    old_dir = current_dir()
    if new_dir == old_dir:
        return True, "unchanged"
    try:
        os.makedirs(new_dir, exist_ok=True)
        if move and os.path.isdir(old_dir):
            for entry in os.listdir(old_dir):
                src = os.path.join(old_dir, entry)
                dst = os.path.join(new_dir, entry)
                if os.path.exists(dst):
                    continue  # don't clobber
                shutil.move(src, dst)
        import json
        cfg = _read_cfg()
        cfg["models_dir"] = new_dir
        with open(SYSTEM_CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=4, ensure_ascii=False)
        app_logger.info(f"Models directory set to {new_dir} (moved={move})")
        return True, "ok"
    except Exception as e:  # noqa: BLE001
        app_logger.error(f"Failed to set models dir: {e}")
        return False, str(e)

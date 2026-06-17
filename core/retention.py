"""Disk retention for logs and results.

Logs (diagnostic .log files) and result/output files accumulate over time. This
applies the user-configured limits — by count, age and total size for logs; by
total size for results — deleting OLDEST first. Run once at app startup (Qt +
web), alongside the history retention that lives in translation_history.py.

All limits use 0 = unlimited. Everything here is best-effort and fully guarded:
retention must never crash a launch.
"""

import os
import time

from core.log_config import app_logger


def _iter_files(root, suffix=None):
    """Yield (path, size, mtime) for files under root (recursively)."""
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            if suffix and not name.endswith(suffix):
                continue
            p = os.path.join(dirpath, name)
            try:
                st = os.stat(p)
                yield p, st.st_size, st.st_mtime
            except OSError:
                continue


def _rm(path):
    try:
        os.remove(path)
        return True
    except OSError as e:
        app_logger.warning(f"Retention: could not delete {path}: {e}")
        return False


def prune_logs(log_dir, max_files=0, max_age_days=0, max_size_mb=0):
    """Delete .log files exceeding any of the limits (oldest first). Never
    touches .db / .json (history) files. Returns the number deleted."""
    if not os.path.isdir(log_dir):
        return 0
    # Never prune the always-on system log (system.log + rotation backups) — it's
    # self-bounded by RotatingFileHandler.
    logs = sorted((t for t in _iter_files(log_dir, ".log")
                   if not os.path.basename(t[0]).startswith("system.log")),
                  key=lambda t: t[2])  # oldest first
    deleted = 0

    # 1) Age: drop anything older than the cutoff.
    if max_age_days and max_age_days > 0:
        cutoff = time.time() - max_age_days * 86400
        for p, _s, m in list(logs):
            if m < cutoff and _rm(p):
                deleted += 1
        logs = [(p, s, m) for (p, s, m) in logs if os.path.exists(p)]

    # 2) Count: keep only the newest max_files.
    if max_files and max_files > 0 and len(logs) > max_files:
        for p, _s, _m in logs[:len(logs) - max_files]:
            if _rm(p):
                deleted += 1
        logs = [(p, s, m) for (p, s, m) in logs if os.path.exists(p)]

    # 3) Size: trim oldest until total <= cap.
    if max_size_mb and max_size_mb > 0:
        cap = max_size_mb * 1024 * 1024
        total = sum(s for _p, s, _m in logs)
        for p, s, _m in logs:
            if total <= cap:
                break
            if _rm(p):
                deleted += 1
                total -= s

    if deleted:
        app_logger.info(f"Retention: removed {deleted} old log file(s)")
    return deleted


def prune_results(result_dir, max_size_mb=0):
    """Keep the result dir under max_size_mb by deleting the OLDEST top-level
    entries (per-task folders / files) first. Returns the number removed."""
    import shutil
    if not max_size_mb or max_size_mb <= 0 or not os.path.isdir(result_dir):
        return 0
    cap = max_size_mb * 1024 * 1024

    def _entry_size(path):
        if os.path.isfile(path):
            try:
                return os.path.getsize(path)
            except OSError:
                return 0
        return sum(s for _p, s, _m in _iter_files(path))

    entries = []
    for name in os.listdir(result_dir):
        p = os.path.join(result_dir, name)
        try:
            entries.append((p, _entry_size(p), os.path.getmtime(p)))
        except OSError:
            continue
    entries.sort(key=lambda t: t[2])  # oldest first
    total = sum(s for _p, s, _m in entries)
    removed = 0
    for p, s, _m in entries:
        if total <= cap:
            break
        try:
            if os.path.isfile(p):
                os.remove(p)
            else:
                shutil.rmtree(p, ignore_errors=True)
            total -= s
            removed += 1
        except OSError as e:
            app_logger.warning(f"Retention: could not delete result {p}: {e}")
    if removed:
        app_logger.info(f"Retention: removed {removed} old result entr(ies) to stay under "
                        f"{max_size_mb} MB")
    return removed


def run_retention():
    """Apply log + result retention from config. Best-effort; never raises."""
    try:
        from core import backend
        from core.paths import DATA_DIR
        cfg = backend.read_config()
        log_dir = os.path.join(DATA_DIR, "log")
        result_dir = cfg.get("result_dir") or os.path.join(DATA_DIR, "result")
        prune_logs(
            log_dir,
            max_files=int(cfg.get("log_max_files", 0) or 0),
            max_age_days=int(cfg.get("log_max_age_days", 0) or 0),
            max_size_mb=int(cfg.get("log_max_size_mb", 0) or 0),
        )
        prune_results(result_dir, max_size_mb=int(cfg.get("result_max_size_mb", 0) or 0))
    except Exception as e:  # noqa: BLE001 — retention must never block startup
        app_logger.warning(f"Retention sweep skipped: {e}")

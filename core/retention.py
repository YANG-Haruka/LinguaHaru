"""Disk retention for logs and results.

Logs (diagnostic .log files) and result/output files accumulate over time. This
applies the user-configured limits — by count, age and total size for logs; by
total size for results — deleting OLDEST first. Run once at app startup (Qt +
web), alongside the history retention that lives in translation_history.py.
The working folders (temp + web uploads) are also swept each time a task ends:
the app stays open for days, so "at the next launch" may be a week away.

All limits use 0 = unlimited. Everything here is best-effort and fully guarded:
retention must never crash a launch.
"""

import os
import re
import shutil
import threading
import time

from core.log_config import app_logger

# A web session's folder (webapp.sessions' id format). The web app nests each
# run one level down — temp/<session>/<run>, uploads/<session>/<task> — so with
# one browser there is ONE top-level folder, always the newest: pruning has to
# look inside it or it never removes anything.
_SESSION_DIR = re.compile(r"[0-9a-f]{12,32}\Z")
# A run's own output folder: "2026-11-09_15-34-28" (desktop) or the same with the
# task's short id (web, inside the session's folder). Nothing else in a result
# folder is the app's to delete by size.
_RUN_DIR = re.compile(r"\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2}(_[0-9a-f]{6})?\Z")

# Working folders and inputs of the tasks running in THIS process (hold/release,
# set by BaseTranslator.process). History protects a task that can be continued,
# but a PDF or a transcribe-only video writes no row with its folder while it
# runs, and neither writes into the folder for hours (BabelDOC's cache and the
# speech model are elsewhere): only "written in the last hour" stood between
# such a task and a sweep started by another task's end.
_active = {}
_active_lock = threading.Lock()


def hold(*paths):
    with _active_lock:
        for p in paths:
            if p:
                _active[_norm(p)] = _active.get(_norm(p), 0) + 1


def release(*paths):
    with _active_lock:
        for p in paths:
            if p and _norm(p) in _active:
                _active[_norm(p)] -= 1
                if _active[_norm(p)] <= 0:
                    del _active[_norm(p)]
# Loose files the video pipeline writes NEXT TO a doc folder (transcript +
# transcription checkpoint). Only these names: any other loose file is not ours.
_SIDECARS = (".srt", ".asr_ckpt.jsonl")
# Nothing written to within the last hour is pruned by a sweep: that covers the
# task that just ended, a proofread doc being edited, an upload still arriving
# and a run that has not written its history row yet — in any process.
_QUIET_S = 3600


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


def prune_results(result_dir, max_size_mb=0, keep_newest=3, min_age_s=3600):
    """Keep the result dir under max_size_mb by deleting the OLDEST entries
    (per-task folders / files) first. Returns the number removed.

    Only the app's own run folders (``_RUN_DIR``) are ever deleted by size.
    The cap used to take the oldest top-level entries whatever they were:
    - a real-time session's transcript and recording (``realtime_*``) — the
      recording of a two-day meeting is 5.5 GB before it is compressed, more
      than the default 5 GB cap by itself: at the next start every older result
      was deleted to make room, and then the recording as well (these go with
      their history record);
    - the user's own files, when the result folder was pointed (it is a setting)
      at a folder that holds other things;
    - on the web, a session's whole folder at once.
    Nor the newest few runs, or anything written in the last hour."""
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

    paths = []
    for name in os.listdir(result_dir):
        p = os.path.join(result_dir, name)
        if _SESSION_DIR.match(name) and os.path.isdir(p):     # web: <result>/<session>/<output>
            paths += [os.path.join(p, n) for n in os.listdir(p)]
        else:
            paths.append(p)
    entries = []
    for p in paths:
        if not (_RUN_DIR.match(os.path.basename(p)) and os.path.isdir(p)):
            continue
        try:
            entries.append((p, _entry_size(p), os.path.getmtime(p)))
        except OSError:
            continue
    entries.sort(key=lambda t: t[2])  # oldest first
    total = sum(s for _p, s, _m in entries)
    removed = 0
    now = time.time()
    for p, s, m in entries[:max(0, len(entries) - keep_newest)]:
        if total <= cap:
            break
        if now - m < min_age_s:
            continue
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


def _norm(path):
    return os.path.normcase(os.path.realpath(path))


def _run_entries(root):
    """What the runs left under a working root, one entry per run: the per-run
    folders (desktop: <root>/<run>; web: <root>/<session>/<run>) plus the video
    pipeline's loose sidecar files beside them."""
    out = []
    for name in os.listdir(root):
        p = os.path.join(root, name)
        # A session folder holds only run folders. One that has a doc's own
        # files directly in it is a DOCUMENT that happens to be named in hex
        # (old flat layout): that stays one entry — never half a doc folder.
        if (_SESSION_DIR.match(name) and os.path.isdir(p) and not any(
                os.path.exists(os.path.join(p, f)) for f in ("src.json", "manifest.json"))):
            out += [os.path.join(p, n) for n in os.listdir(p)]
        else:
            out.append(p)
    return [p for p in out if os.path.isdir(p) or p.endswith(_SIDECARS)]


def _resumable():
    """Paths a "Continue" from history still reads: for every record that
    carries a resume payload — failed / stopped / interrupted, and also running /
    paused / queued, since a run is written to history when it starts — its
    working folder, its doc folder in there, and its input file (for the web,
    the upload). Finished records carry no payload.

    Raises when a history database cannot be read: the caller then skips the
    sweep rather than prune what it could not check."""
    import glob
    import json
    import sqlite3
    from core import backend
    out = []
    # <history>/translation_history.db (desktop + local web) and
    # <history>/<session>/translation_history.db (LAN / public web).
    for db in glob.glob(os.path.join(glob.escape(backend.history_dir()), "**",
                                     "translation_history.db"), recursive=True):
        con = sqlite3.connect(db, timeout=5)
        try:
            rows = con.execute("SELECT resume_info FROM records WHERE resume_info != ''").fetchall()
        except sqlite3.OperationalError as e:
            if "no such" not in str(e):
                raise
            rows = []      # a DB from before resume existed: nothing in it continues
        finally:
            con.close()
        for (raw,) in rows:
            try:
                info = json.loads(raw)
            except ValueError:
                continue   # neither frontend can continue a record it cannot parse
            if not isinstance(info, dict):
                continue
            src, tdir = info.get("input_file_path"), info.get("temp_dir")
            if src:
                out.append(_norm(src))
            if tdir:
                out.append(_norm(tdir))
                if src:
                    out.append(_norm(os.path.join(
                        tdir, os.path.splitext(os.path.basename(src))[0])))
    return out


def _spared(path, protect):
    """True when `path` is one of the (normalized) `protect` paths, contains one,
    or is a sidecar file of one (<doc>.srt beside the protected <doc> folder)."""
    p = _norm(path)
    return any(f == p or f.startswith(p + os.sep) or p.startswith(f + ".")
               for f in protect)


def prune_temp(temp_dir, max_size_mb=0, keep_newest=3, protect=(), min_age_s=0):
    """Keep the working-folder tree under `max_size_mb` by deleting the OLDEST
    per-run folders first, always keeping the `keep_newest` most recent ones.

    Intermediates are the bulk of the disk use (extracted audio, page images,
    checkpoints) while the actual outputs live in result/ and are tiny. Nothing
    pruned them, so one machine reached 110 GB across 42 stale run folders.
    Bounded by SIZE, not age, and mirroring prune_results: a user whose files
    are small never loses anything, and the newest runs stay proofreadable /
    resumable no matter what. Whole folders only — a half-deleted working folder
    would break resume and proofread in confusing ways.

    `protect` (see _resumable) and `min_age_s` (seconds since the last write
    anywhere inside) exempt an entry however old its folder is: this also runs
    while tasks are running, paused, or waiting in history to be continued.

    Returns the number of entries removed.
    """
    if not max_size_mb or not os.path.isdir(temp_dir):
        return 0
    limit = max_size_mb * 1024 * 1024

    def _size(path):                  # -> (bytes, time of the newest write inside)
        newest = os.path.getmtime(path)
        if os.path.isfile(path):
            return os.path.getsize(path), newest
        total = 0
        for dirpath, _d, files in os.walk(path):
            for fn in files:
                try:
                    st = os.stat(os.path.join(dirpath, fn))
                    total += st.st_size
                    newest = max(newest, st.st_mtime)
                except OSError:
                    pass
        return total, newest

    try:
        dirs = _run_entries(temp_dir)
    except OSError:
        return 0
    sized = []
    for d in dirs:
        try:
            sized.append((os.path.getmtime(d), *_size(d), d))
        except OSError:
            pass
    total = sum(s for _m, s, _n, _d in sized)
    if total <= limit:
        return 0
    sized.sort(reverse=True)                      # newest first
    kept = [d for _m, _s, _n, d in sized if os.path.isdir(d)][:max(0, int(keep_newest))]
    quiet_before = time.time() - min_age_s
    removed = 0
    for _m, size, newest, d in sized[::-1]:       # oldest first
        if total <= limit:
            break
        if d in kept or (min_age_s and newest > quiet_before) or _spared(d, protect):
            continue
        try:
            if os.path.isdir(d):
                shutil.rmtree(d)
            else:
                os.remove(d)
            total -= size
            removed += 1
        except OSError as e:
            app_logger.warning(f"Could not prune temp folder {d}: {e}")
    return removed


def remove_upload(path, upload_root):
    """Delete ONE web upload that no task will read again; True when deleted.

    Refuses anything outside `upload_root` — in local mode the web app shares
    its history with the desktop app, whose records point at the user's own
    files — and anything a history record can still continue from (an
    interrupted task re-reads its upload; so does a second task still running
    on the same file). Best-effort; never raises."""
    try:
        real, root = _norm(path), _norm(upload_root)
        if not real.startswith(root + os.sep) or not os.path.isfile(real):
            return False
        if real in _resumable():
            return False
        os.remove(real)
        task_dir = os.path.dirname(real)
        if task_dir != root:
            try:
                os.rmdir(task_dir)     # only goes once its last upload is gone
            except OSError:
                pass
        return True
    except Exception as e:  # noqa: BLE001
        app_logger.warning(f"Could not remove upload {path}: {e}")
        return False


def run_retention(working_only=False):
    """Apply log + result + working-folder retention from config. Best-effort;
    never raises. `working_only` (a task just ended) leaves logs and results
    alone: result retention removes whole top-level entries of the result
    folder, which on the web is the session folder holding the output that was
    produced a second ago."""
    try:
        from core import backend
        from core.paths import RUNTIME_ROOT, DATA_DIR
        cfg = backend.read_config()
        # Per-project logs live in the result folders now; this only sweeps any
        # legacy top-level .log files. system.log (log/) is self-bounded.
        log_dir = os.path.join(RUNTIME_ROOT, "log")
        temp_dir, result_dir, _ = backend.get_custom_paths()
        if not working_only:
            prune_logs(
                log_dir,
                max_files=int(cfg.get("log_max_files", 0) or 0),
                max_age_days=int(cfg.get("log_max_age_days", 0) or 0),
                max_size_mb=int(cfg.get("log_max_size_mb", 0) or 0),
            )
            prune_results(result_dir, max_size_mb=int(cfg.get("result_max_size_mb", 0) or 0))
        # Working folders: off unless configured, and the newest few are always
        # kept so Proofread/Continue still work for recent runs.
        protect = _resumable() + list(_active)
        cap = int(cfg.get("temp_max_size_mb", 20000) or 0)
        keep = int(cfg.get("temp_keep_newest", 3) or 0)
        n = prune_temp(temp_dir, max_size_mb=cap, keep_newest=keep,
                       protect=protect, min_age_s=_QUIET_S)
        # Web uploads (webapp.server.UPLOAD_DIR), same rule and same cap. A
        # finished task's upload is deleted when it ends; this bounds the rest
        # (uploads from before that existed, a document too big to be copied
        # for Proofread, a record deleted from the desktop app).
        n += prune_temp(os.path.join(DATA_DIR, "uploads"), max_size_mb=cap,
                        keep_newest=keep, protect=protect, min_age_s=_QUIET_S)
        if n:
            app_logger.info(f"Retention: removed {n} stale working folder(s) from temp")
    except Exception as e:  # noqa: BLE001 — retention must never block startup
        app_logger.warning(f"Retention sweep skipped: {e}")


_sweeping = threading.Lock()


def sweep_after_task():
    """Bound the working folders now that a task ended (success, failure or
    stop). Returns at once: the walk runs on its own thread, so neither the
    task's worker nor a UI / event-loop thread waits for it. Never raises —
    it is called from the task's ``finally``."""
    def _go():
        # One walk at a time: a batch ends many tasks within the same second.
        if _sweeping.acquire(blocking=False):
            try:
                run_retention(working_only=True)
            finally:
                _sweeping.release()
    try:
        threading.Thread(target=_go, name="retention", daemon=True).start()
    except Exception:  # noqa: BLE001
        pass

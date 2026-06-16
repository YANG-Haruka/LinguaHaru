"""
Translation History (per-project records), SQLite-backed.

Each finished translation is one "project" row: input file, file type, languages,
model, status, timing, tokens, cost, and output path. SQLite (stdlib, no extra
dependency) makes the history robust (atomic, no torn JSON) and lets the UI
browse projects organized by FILE TYPE and sorted by TIME.

The public API (TranslationHistoryManager + create_translation_record +
format_duration/format_tokens) is unchanged, so existing callers keep working;
get_all_records() now also accepts file_type / sort filters, and there's a
file_types() helper for the browse UI. An existing translation_summary.json is
imported once on first use.
"""

import os
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any

from core.log_config import app_logger

# Columns stored per project (order matters for row<->dict mapping).
_FIELDS = [
    "id", "start_time", "end_time", "duration_seconds", "total_tokens",
    "src_lang", "src_lang_display", "dst_lang", "dst_lang_display",
    "model", "use_online", "input_file", "file_type",
    "output_file_path", "log_file_path", "status",
    "cost_amount", "cost_currency",
    # Translation-mode snapshot (for reproducibility / "why did this run differ").
    "translation_mode", "translation_tone", "translation_length", "translation_style",
]


def _file_type(input_file: str) -> str:
    """Lowercased extension without the dot, e.g. 'docx' (or '' if none)."""
    ext = os.path.splitext(input_file or "")[1].lower().lstrip(".")
    return ext


def _retention_limits():
    """(max_records, max_age_days) from config (0 = unlimited / no age limit)."""
    try:
        from core import backend
        cfg = backend.read_config()
        return (int(cfg.get("history_max_records", 1000) or 0),
                int(cfg.get("history_max_age_days", 0) or 0))
    except Exception:  # noqa: BLE001
        return (1000, 0)


class TranslationHistoryManager:
    """Per-project translation history, stored in SQLite."""

    MAX_RECORDS = 1000  # default prune cap (overridden by config)

    def __init__(self, log_dir: str = "log"):
        self.log_dir = log_dir
        self.db_path = os.path.join(log_dir, "translation_history.db")
        # Kept for one-time migration from the old JSON history.
        self.summary_file = os.path.join(log_dir, "translation_summary.json")
        os.makedirs(self.log_dir, exist_ok=True)
        self._init_db()
        self._migrate_json_once()

    @contextmanager
    def _connect(self):
        # sqlite3.Connection's own `with` only commits/rolls back — it does NOT
        # close. On Windows that leaves the .db file locked (WinError 32) and
        # WAL -wal/-shm files lingering. This wrapper commits on success, rolls
        # back on error, and ALWAYS closes, so `with self._connect() as conn:`
        # call sites are unchanged.
        conn = sqlite3.connect(self.db_path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")        # safe concurrent reads
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        try:
            with self._connect() as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS records (
                        id TEXT PRIMARY KEY,
                        start_time TEXT, end_time TEXT,
                        duration_seconds INTEGER, total_tokens INTEGER,
                        src_lang TEXT, src_lang_display TEXT,
                        dst_lang TEXT, dst_lang_display TEXT,
                        model TEXT, use_online INTEGER,
                        input_file TEXT, file_type TEXT,
                        output_file_path TEXT, log_file_path TEXT,
                        status TEXT, cost_amount REAL, cost_currency TEXT,
                        translation_mode TEXT, translation_tone TEXT,
                        translation_length TEXT, translation_style TEXT
                    )""")
                # Add any columns missing from an older DB (additive migration) —
                # BEFORE creating indexes, which may reference those columns.
                have = {r[1] for r in conn.execute("PRAGMA table_info(records)").fetchall()}
                for col in _FIELDS:
                    if col not in have:
                        conn.execute(f"ALTER TABLE records ADD COLUMN {col} TEXT")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_start ON records(start_time)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_ftype ON records(file_type)")
        except Exception as e:  # noqa: BLE001
            app_logger.error(f"Error initializing history DB: {e}")

    def _migrate_json_once(self):
        """Import the legacy translation_summary.json once, then leave it."""
        if not os.path.exists(self.summary_file):
            return
        try:
            with self._connect() as conn:
                if conn.execute("SELECT COUNT(*) FROM records").fetchone()[0] > 0:
                    return  # already have data; don't re-import
            with open(self.summary_file, "r", encoding="utf-8") as f:
                old = json.load(f)
            for rec in (old if isinstance(old, list) else []):
                self.add_record(rec)
            app_logger.info(f"Imported {len(old)} legacy history records into SQLite")
        except Exception as e:  # noqa: BLE001
            app_logger.warning(f"Legacy history import skipped: {e}")

    def _row_to_dict(self, row) -> Dict[str, Any]:
        d = {k: row[k] for k in row.keys()}
        d["use_online"] = bool(d.get("use_online"))
        return d

    def add_record(self, record: Dict[str, Any]) -> bool:
        """Insert or update a project record (keyed by id)."""
        try:
            rec = dict(record)
            if not rec.get("file_type"):
                rec["file_type"] = _file_type(rec.get("input_file", ""))
            values = [rec.get(f) for f in _FIELDS]
            placeholders = ",".join("?" * len(_FIELDS))
            cols = ",".join(_FIELDS)
            with self._connect() as conn:
                conn.execute(f"INSERT OR REPLACE INTO records ({cols}) VALUES ({placeholders})", values)
                self._prune(conn)
            return True
        except Exception as e:  # noqa: BLE001
            app_logger.error(f"Error adding translation record: {e}")
            return False

    def _prune(self, conn):
        """Apply retention: drop records older than max_age_days and beyond
        max_records (both from config; 0 = unlimited)."""
        max_records, max_age_days = _retention_limits()
        if max_age_days and max_age_days > 0:
            cutoff = (datetime.now() - timedelta(days=max_age_days)).isoformat()
            conn.execute("DELETE FROM records WHERE start_time < ?", (cutoff,))
        if max_records and max_records > 0:
            conn.execute(
                "DELETE FROM records WHERE id NOT IN "
                "(SELECT id FROM records ORDER BY start_time DESC LIMIT ?)",
                (max_records,))

    def prune_now(self) -> bool:
        """Apply retention immediately (e.g. after the user changes the limits)."""
        try:
            with self._connect() as conn:
                self._prune(conn)
            return True
        except Exception as e:  # noqa: BLE001
            app_logger.warning(f"History prune failed: {e}")
            return False

    def get_all_records(self, limit: Optional[int] = None, file_type: Optional[str] = None,
                        sort_by: str = "start_time", descending: bool = True) -> List[Dict[str, Any]]:
        """Records for the browse UI, optionally filtered by file_type and sorted
        (by start_time / input_file / file_type / total_tokens / status)."""
        sort_col = sort_by if sort_by in {
            "start_time", "input_file", "file_type", "total_tokens", "status",
            "duration_seconds"} else "start_time"
        order = "DESC" if descending else "ASC"
        sql = "SELECT * FROM records"
        params: list = []
        if file_type:
            sql += " WHERE file_type = ?"
            params.append(file_type.lower().lstrip("."))
        sql += f" ORDER BY {sort_col} {order}"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        try:
            with self._connect() as conn:
                rows = conn.execute(sql, params).fetchall()
            return [self._row_to_dict(r) for r in rows]
        except Exception as e:  # noqa: BLE001
            app_logger.warning(f"Error reading translation history: {e}")
            return []

    def file_types(self) -> List[str]:
        """Distinct file types present (for the browse filter)."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    "SELECT DISTINCT file_type FROM records WHERE file_type != '' "
                    "ORDER BY file_type").fetchall()
            return [r["file_type"] for r in rows]
        except Exception:  # noqa: BLE001
            return []

    def get_record_by_id(self, record_id: str) -> Optional[Dict[str, Any]]:
        try:
            with self._connect() as conn:
                row = conn.execute("SELECT * FROM records WHERE id = ?", (record_id,)).fetchone()
            return self._row_to_dict(row) if row else None
        except Exception:  # noqa: BLE001
            return None

    def delete_record(self, record_id: str) -> bool:
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM records WHERE id = ?", (record_id,))
            return True
        except Exception as e:  # noqa: BLE001
            app_logger.error(f"Error deleting translation record: {e}")
            return False

    def clear_all_records(self) -> bool:
        try:
            with self._connect() as conn:
                conn.execute("DELETE FROM records")
            return True
        except Exception as e:  # noqa: BLE001
            app_logger.error(f"Error clearing translation records: {e}")
            return False

    def clear_all_records_and_files(self) -> Dict[str, int]:
        """Delete the OUTPUT and LOG files this history produced, then clear the
        records. Returns {"records", "files_deleted"}. Only removes files we
        generated (output_file_path / log_file_path) — never the user's original
        input_file — and only real existing files (no dir/recursive deletes)."""
        deleted = 0
        try:
            records = self.get_all_records()
        except Exception:  # noqa: BLE001
            records = []
        for rec in records:
            for key in ("output_file_path", "log_file_path"):
                p = rec.get(key)
                if p and os.path.isfile(p):
                    try:
                        os.remove(p)
                        deleted += 1
                    except OSError as e:
                        app_logger.warning(f"Could not delete history file {p}: {e}")
        ok = self.clear_all_records()
        return {"records": len(records) if ok else 0, "files_deleted": deleted}


def create_translation_record(
    translation_id: str,
    start_time: datetime,
    end_time: datetime,
    total_tokens: int,
    src_lang: str,
    src_lang_display: str,
    dst_lang: str,
    dst_lang_display: str,
    model: str,
    use_online: bool,
    input_file: str,
    output_file_path: str,
    log_file_path: str,
    status: str,
    cost_amount: Optional[float] = None,
    cost_currency: Optional[str] = None,
    translation_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Create a translation/project record dictionary."""
    duration_seconds = int((end_time - start_time).total_seconds())
    opts = translation_options or {}
    return {
        "id": translation_id,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "duration_seconds": duration_seconds,
        "total_tokens": total_tokens,
        "src_lang": src_lang,
        "src_lang_display": src_lang_display,
        "dst_lang": dst_lang,
        "dst_lang_display": dst_lang_display,
        "model": model,
        "use_online": use_online,
        "input_file": input_file,
        "file_type": _file_type(input_file),
        "output_file_path": output_file_path,
        "log_file_path": log_file_path,
        "status": status,
        "cost_amount": cost_amount,
        "cost_currency": cost_currency,
        "translation_mode": opts.get("mode", ""),
        "translation_tone": opts.get("tone", ""),
        "translation_length": opts.get("length", ""),
        "translation_style": opts.get("style", ""),
    }


def save_live_session(
    source_lines: List[str],
    translated_lines: List[str],
    src_display: str,
    dst_display: str,
    model: str,
    use_online: bool,
    result_dir: str,
    log_dir: str,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
) -> Optional[Dict[str, Any]]:
    """Persist a real-time-voice session: write a bilingual transcript file and
    add a history record (file_type 'realtime'). Source/translated lines are the
    timestamped lines shown in the two live panes; they're paired by their
    ``[HH:MM:SS]`` prefix. Returns the record, or None if there's nothing to save.
    """
    import re
    import uuid

    src = [ln.strip() for ln in (source_lines or []) if ln and ln.strip()]
    dst = [ln.strip() for ln in (translated_lines or []) if ln and ln.strip()]
    if not src and not dst:
        return None
    end_time = end_time or datetime.now()
    start_time = start_time or end_time

    def _ts(line: str) -> Optional[str]:
        m = re.match(r"^\[(\d{2}:\d{2}:\d{2})\]", line)
        return m.group(1) if m else None

    # Pair translations to their source by the shared timestamp (handles
    # out-of-order LLM replies); leftover translations are appended at the end.
    out_by_ts: Dict[str, list] = {}
    for ln in dst:
        t = _ts(ln)
        if t:
            out_by_ts.setdefault(t, []).append(ln)
    body = [f"# Real-Time Voice {end_time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"# {src_display} -> {dst_display} | {model}", ""]
    for sl in src:
        body.append(sl)
        t = _ts(sl)
        if t and out_by_ts.get(t):
            body.append(out_by_ts[t].pop(0))
        body.append("")
    leftover = [ln for rem in out_by_ts.values() for ln in rem]
    body.extend(leftover)

    os.makedirs(result_dir, exist_ok=True)
    name = f"realtime_{end_time.strftime('%Y%m%d_%H%M%S')}.txt"
    out_path = os.path.join(result_dir, name)
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            f.write("\n".join(body).rstrip() + "\n")
    except Exception as e:  # noqa: BLE001
        app_logger.warning(f"Could not write live transcript: {e}")
        out_path = ""

    rec = create_translation_record(
        translation_id=uuid.uuid4().hex[:12],
        start_time=start_time, end_time=end_time, total_tokens=0,
        src_lang="", src_lang_display=src_display,
        dst_lang="", dst_lang_display=dst_display,
        model=model, use_online=use_online,
        input_file=name, output_file_path=out_path, log_file_path="",
        status="success",
    )
    rec["file_type"] = "realtime"     # group under its own type in the browse UI
    TranslationHistoryManager(log_dir=log_dir).add_record(rec)
    return rec


def format_duration(seconds: int) -> str:
    """Format a duration in seconds, e.g. '5m 23s' or '1h 30m 45s'."""
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    remaining_seconds = seconds % 60
    if minutes < 60:
        return f"{minutes}m {remaining_seconds}s" if remaining_seconds else f"{minutes}m"
    hours = minutes // 60
    remaining_minutes = minutes % 60
    parts = [f"{hours}h"]
    if remaining_minutes:
        parts.append(f"{remaining_minutes}m")
    if remaining_seconds:
        parts.append(f"{remaining_seconds}s")
    return " ".join(parts)


def format_tokens(tokens: int) -> str:
    """Format a token count with a K suffix, e.g. '12.5K' or '500'."""
    if tokens and tokens >= 1000:
        return f"{tokens / 1000:.1f}K"
    return str(tokens or 0)

"""Persistent translation cache / TM — a STANDALONE feature (not the history DB).

Caches finished segment translations keyed by (exact source text, params_sig) so
a re-run, a repeated line, or a series of similar files reuses past output: zero
tokens, zero latency, and consistency. Designed deliberately separate from
``translation_history`` (different lifecycle, different schema, user-clearable for
privacy).

Design decisions
----------------
* **Exact source-text key** (v1): the key hashes the *verbatim* source segment,
  so ``Hello {name}`` and ``Hello {user}`` are DIFFERENT entries — the cached
  translation is always correct for that exact source, with no placeholder
  re-mapping needed. (Masked-source dedup across placeholder variants is a future
  optimization, intentionally not done here to stay correct-by-construction.)
* **params_sig** folds in EVERY output-affecting variable — model, src/dst lang,
  sampling mode/temperature, prompt version, glossary hash, masking flag — so a
  change to any of them never reuses a stale translation. Caller builds it via
  ``params_sig(...)``.
* **SQLite in WAL mode + busy_timeout** so the translator's thread pool doesn't
  hit ``database is locked``.
* **Privacy**: ``clear()`` wipes everything; entries store source+target text, so
  the user controls retention.
* **Bounded**: ``prune(max_rows)`` drops the least-recently-used rows.

This module is pure storage; the integration (look up before the LLM, write after)
lives in the translator and is gated by config ``translation_cache`` (default off).
"""
import hashlib
import os
import sqlite3
import threading
import time

from core.log_config import app_logger

_DB_PATH = None
_conn = None
_LOCK = threading.Lock()
_MAX_ROWS = 200_000   # LRU cap; prune() trims to this
_puts_since_prune = 0
_PRUNE_EVERY = 2000   # amortize the COUNT/DELETE: prune once every N put_many calls


def _db_path():
    global _DB_PATH
    if _DB_PATH is None:
        from core.paths import DATA_DIR
        _DB_PATH = os.path.join(DATA_DIR, "tm.sqlite")
    return _DB_PATH


def _get_conn():
    global _conn
    if _conn is not None:
        return _conn
    with _LOCK:
        if _conn is not None:
            return _conn
        path = _db_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        conn = sqlite3.connect(path, check_same_thread=False, timeout=10.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=10000")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS tm ("
            " key TEXT PRIMARY KEY,"      # sha1(src_hash | params_sig)
            " src TEXT NOT NULL,"
            " dst TEXT NOT NULL,"
            " params TEXT NOT NULL,"      # params_sig (for debugging / selective purge)
            " used_at REAL NOT NULL)")
        conn.commit()
        _conn = conn
    return _conn


def params_sig(model, src_lang, dst_lang, mode="", temperature=None,
               glossary_hash="", mask=True, prompt_version="v1"):
    """Stable signature of everything that affects a translation's output. Two
    requests with the same params_sig + same source must yield interchangeable
    translations."""
    parts = [str(model), str(src_lang), str(dst_lang), str(mode),
             "" if temperature is None else f"{float(temperature):.3f}",
             str(glossary_hash), "m1" if mask else "m0", str(prompt_version)]
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()[:16]


def glossary_hash(glossary_terms):
    """Order-independent hash of the active glossary terms (list of [src, dst, …]
    or similar), so a glossary edit invalidates affected cache entries."""
    if not glossary_terms:
        return ""
    try:
        rows = sorted("\t".join(str(x) for x in row) for row in glossary_terms)
    except Exception:  # noqa: BLE001
        rows = sorted(str(row) for row in glossary_terms)
    return hashlib.sha1("\n".join(rows).encode("utf-8")).hexdigest()[:12]


def _key(src, sig):
    return hashlib.sha1((sig + "\x00" + src).encode("utf-8")).hexdigest()


def get_many(items, sig):
    """Look up many sources at once. ``items`` is an iterable of source strings;
    returns ``{src: dst}`` for the ones found. Never raises (cache is best-effort)."""
    srcs = [s for s in items if isinstance(s, str) and s.strip()]
    if not srcs:
        return {}
    try:
        conn = _get_conn()
        keys = {_key(s, sig): s for s in srcs}
        found = {}
        # Chunk the IN(...) to stay under SQLite's variable limit.
        klist = list(keys)
        with _LOCK:
            for i in range(0, len(klist), 500):
                chunk = klist[i:i + 500]
                q = "SELECT key, dst FROM tm WHERE key IN (%s)" % ",".join("?" * len(chunk))
                for k, dst in conn.execute(q, chunk):
                    found[keys[k]] = dst
            if found:   # bump LRU timestamps for hits
                now = time.time()
                conn.executemany("UPDATE tm SET used_at=? WHERE key=?",
                                 [(now, _key(s, sig)) for s in found])
                conn.commit()
        return found
    except Exception as e:  # noqa: BLE001
        app_logger.warning(f"TM get_many failed: {e}")
        return {}


def put_many(pairs, sig):
    """Store many (src, dst) pairs under ``sig``. Skips empty / identical-to-source
    entries (no value in caching a non-translation). Never raises."""
    rows = [(_key(s, sig), s, d, sig, time.time())
            for s, d in pairs
            if isinstance(s, str) and isinstance(d, str) and s.strip() and d.strip()
            and d.strip() != s.strip()]
    if not rows:
        return
    global _puts_since_prune
    do_prune = False
    try:
        conn = _get_conn()
        with _LOCK:
            conn.executemany(
                "INSERT INTO tm(key, src, dst, params, used_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET dst=excluded.dst, used_at=excluded.used_at",
                rows)
            conn.commit()
            # Counter lives under the lock so concurrent workers don't both trip
            # the threshold; prune() runs AFTER releasing (it re-acquires _LOCK).
            _puts_since_prune += 1
            if _puts_since_prune >= _PRUNE_EVERY:
                _puts_since_prune = 0
                do_prune = True
    except Exception as e:  # noqa: BLE001
        app_logger.warning(f"TM put_many failed: {e}")
        return
    # Amortized LRU cap so the DB can't grow unbounded (prune was never called).
    if do_prune:
        prune()


def prune(max_rows=_MAX_ROWS):
    """Trim to the most-recently-used ``max_rows`` entries."""
    try:
        conn = _get_conn()
        with _LOCK:
            n = conn.execute("SELECT COUNT(*) FROM tm").fetchone()[0]
            if n > max_rows:
                conn.execute(
                    "DELETE FROM tm WHERE key IN ("
                    " SELECT key FROM tm ORDER BY used_at ASC LIMIT ?)", (n - max_rows,))
                conn.commit()
    except Exception as e:  # noqa: BLE001
        app_logger.warning(f"TM prune failed: {e}")


def stats():
    """(rows, bytes) for the UI; (0, 0) on any error."""
    try:
        conn = _get_conn()
        with _LOCK:
            rows = conn.execute("SELECT COUNT(*) FROM tm").fetchone()[0]
        size = os.path.getsize(_db_path()) if os.path.exists(_db_path()) else 0
        return int(rows), int(size)
    except Exception:  # noqa: BLE001
        return 0, 0


def clear():
    """Wipe the whole cache (privacy / reset). Returns True on success."""
    try:
        conn = _get_conn()
        with _LOCK:
            conn.execute("DELETE FROM tm")
            conn.commit()
            conn.execute("VACUUM")
        return True
    except Exception as e:  # noqa: BLE001
        app_logger.warning(f"TM clear failed: {e}")
        return False

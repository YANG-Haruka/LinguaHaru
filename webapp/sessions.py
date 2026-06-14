"""Per-session isolation for the multi-user Web deploy.

The desktop (Qt) app is single-user, so ``core.backend`` keeps a global view
of translations and proofreading. The Web app can serve many users at once, so
this module scopes BOTH translation paths AND proofreading to a per-browser
session id, with IDOR / path-traversal protection.

A session id is an opaque hex token the browser sends with every request (set
as an httponly cookie on first contact). It is ALSO the name of the session's
subfolder under the temp/result/log base dirs, so concurrent users never share
a path and one user can never reach another user's documents. Because the id is
used as a filesystem path component, ``valid_session_id`` accepts only short
lowercase-hex tokens — no separators, no ``..``.
"""

import os
import re
import threading
import uuid

from core import backend

SESSION_COOKIE = "lh_session"
_SID_RE = re.compile(r"\A[0-9a-f]{6,32}\Z")

# session_id -> True when a stop was requested. In-memory only.
_stop_flags = {}
_lock = threading.Lock()


class StopTranslationException(Exception):
    """Raised inside a translation when the caller's session requests a stop."""


def new_session_id():
    """A fresh opaque session id (safe to use as a path component)."""
    return uuid.uuid4().hex[:12]


def valid_session_id(sid):
    """Only short lowercase-hex tokens are accepted — this is what makes the id
    safe to use as a filesystem path component (no traversal, no separators)."""
    return bool(sid) and bool(_SID_RE.match(sid))


def session_paths(session_id):
    """(temp, result, log) dirs scoped to this session, created on demand.

    Each is ``<base>/<session_id>``, so a fresh translation's
    ``_clear_temp_folder()`` wipes only the caller's temp subtree — never
    another user's."""
    if not valid_session_id(session_id):
        raise ValueError(f"Invalid session id: {session_id!r}")
    temp_dir, result_dir, log_dir = backend.get_custom_paths()
    paths = tuple(os.path.join(d, session_id)
                  for d in (temp_dir, result_dir, log_dir))
    for d in paths:
        os.makedirs(d, exist_ok=True)
    return paths


# --- per-session stop registry --------------------------------------------- #
def reset_stop_flag(session_id):
    """Clear a session's stop flag before a new translation."""
    with _lock:
        _stop_flags[session_id] = False


def request_stop(session_id):
    """Mark THIS session's translation to stop. No-op for a falsy id."""
    with _lock:
        if session_id:
            _stop_flags[session_id] = True


def check_stop_requested(session_id):
    """Raise StopTranslationException if this session asked to stop."""
    with _lock:
        if _stop_flags.get(session_id, False):
            raise StopTranslationException("Translation stopped by user")
    return False


def clear_stop_flag(session_id):
    """Drop a finished session's stop flag."""
    with _lock:
        _stop_flags.pop(session_id, None)


def disconnect(session_id):
    """Browser tab closed: stop the session's run (the in-flight worker reads
    the flag and aborts)."""
    request_stop(session_id)


# --- proofreading, scoped to the caller's session -------------------------- #
def proofread_doc_dir(doc_name, session_id):
    """Resolve a proofread doc name to a folder, enforcing session ownership.

    A doc must be namespaced as ``<session_id>/<doc>`` and owned by the CALLER
    (blocks IDOR), and must resolve strictly inside the temp base (blocks path
    traversal). Returns None on any violation."""
    if not doc_name or not valid_session_id(session_id):
        return None
    norm = doc_name.replace("\\", "/")
    owner = norm.split("/", 1)[0] if "/" in norm else None
    if owner != session_id:
        return None
    temp_dir, _, _ = backend.get_custom_paths()
    base = os.path.realpath(temp_dir)
    candidate = os.path.realpath(os.path.join(base, doc_name))
    if not candidate.startswith(base + os.sep):
        return None
    return candidate


def list_proofread_docs(session_id):
    """Finished, proofreadable docs the caller may see: only their own session
    subtree (``temp/<sid>/<doc>``). PDF is excluded (handled by
    ``backend._is_finished_doc``)."""
    docs = []
    if not valid_session_id(session_id):
        return docs
    temp_dir, _, _ = backend.get_custom_paths()
    sess_dir = os.path.join(temp_dir, session_id)
    try:
        if os.path.isdir(sess_dir):
            for sub in sorted(os.listdir(sess_dir)):
                folder = os.path.join(sess_dir, sub)
                if os.path.isdir(folder) and backend._is_finished_doc(folder):
                    docs.append(f"{session_id}/{sub}")
    except OSError:
        pass
    return docs

import logging
import sys
import os
import threading
import contextvars
from datetime import datetime
from logging.handlers import RotatingFileHandler
from colorama import Fore, Style, init

init(autoreset=True)

# The task whose per-project log the CURRENT execution context belongs to. Set at
# the start of a translation and propagated into its ThreadPoolExecutor workers,
# so concurrent translations each write to their OWN log file without interleaving
# (the standard contextvars approach for per-request/per-task log isolation).
_log_task = contextvars.ContextVar("log_task", default=None)


class SimpleColoredFormatter(logging.Formatter):
    COLORS = {
        'DEBUG': Fore.BLUE,
        'INFO': Fore.GREEN,
        'WARNING': Fore.YELLOW,
        'ERROR': Fore.RED,
        'CRITICAL': Fore.RED
    }

    def format(self, record):
        log_color = self.COLORS.get(record.levelname, Fore.WHITE)
        levelname = record.levelname
        msg = super().format(record)
        return f"{log_color}[{levelname}] {msg}{Style.RESET_ALL}"


_FILE_FMT = logging.Formatter(fmt='%(asctime)s - [%(levelname)s] - %(message)s',
                              datefmt='%Y-%m-%d %H:%M:%S')


class _SystemFilter(logging.Filter):
    """system.log keeps only PROBLEMS (warnings/errors) + explicit lifecycle
    events (records flagged sysevent=True). Routine INFO chatter — progress,
    per-segment stages — stays out, so the system log is a clean, monitorable
    record of 'what happened / what went wrong'."""

    def filter(self, record):
        return record.levelno >= logging.WARNING or getattr(record, "sysevent", False)


class _TaskRoutingHandler(logging.Handler):
    """Routes each log record to the file handler of the task bound to the
    current context (``_log_task``). A record with no bound task is ignored here
    (the console + system-log handlers still emit it). Thread-safe: several
    translations can log concurrently, each to its own file, no interleaving."""

    def __init__(self, level=logging.DEBUG):
        super().__init__(level)
        self._handlers = {}              # task_id -> FileHandler
        self._lock = threading.Lock()
        # Fallback task for records from threads that never got the contextvar
        # (e.g. BabelDOC's internal worker threads during PDF translation).
        self._fallback = None
        self.setFormatter(_FILE_FMT)

    def set_fallback(self, task_id):
        with self._lock:
            self._fallback = task_id

    def open_task(self, task_id, path):
        fh = logging.FileHandler(path, mode='a', encoding='utf-8')
        fh.setLevel(self.level)
        fh.setFormatter(self.formatter)
        with self._lock:
            old = self._handlers.pop(task_id, None)
            self._handlers[task_id] = fh
        if old:
            old.close()

    def close_task(self, task_id):
        with self._lock:
            fh = self._handlers.pop(task_id, None)
        if fh:
            fh.close()

    def close(self):
        """Close all per-task file handlers (called by logging.shutdown)."""
        with self._lock:
            handlers = list(self._handlers.values())
            self._handlers.clear()
        for fh in handlers:
            try:
                fh.close()
            except Exception:  # noqa: BLE001
                pass
        super().close()

    def emit(self, record):
        tid = _log_task.get()
        with self._lock:
            fh = self._handlers.get(tid if tid is not None else self._fallback)
        if fh is not None:
            fh.emit(record)             # FileHandler has its own lock


class FileLogger:
    def __init__(self, name="app_logger", console_level=logging.INFO, file_level=logging.DEBUG):
        """Console (colored, INFO) + an always-on rotating system log + a
        per-task routing handler (full DEBUG detail, isolated per translation)."""
        self.name = name
        self.console_level = console_level
        self.file_level = file_level
        self.logger = logging.getLogger(name)
        self.logger.setLevel(min(console_level, file_level))
        # Don't bubble to the root logger: funasr/modelscope call
        # logging.basicConfig() at import, which adds a DEBUG root handler that
        # would otherwise re-print every app_logger line (incl. DEBUG API
        # request/response dumps) in the ugly default format + duplicate INFO.
        self.logger.propagate = False
        self._routing = None             # _TaskRoutingHandler (per-project logs)

        if not self.logger.hasHandlers():
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(console_level)
            console_handler.setFormatter(SimpleColoredFormatter(fmt='%(message)s'))
            self.logger.addHandler(console_handler)
            self._setup_system_log()
            # Per-project log at INFO: translation stages / retries / stats —
            # NOT full prompt/response (those are gated separately by config).
            self._routing = _TaskRoutingHandler(level=logging.INFO)
            self.logger.addHandler(self._routing)

        # Quiet noisy third-party loggers (some get pulled to DEBUG by the
        # speech stack's root basicConfig) so the console stays readable.
        for _noisy in ("httpx", "httpcore", "openai", "urllib3", "asyncio",
                       "modelscope", "funasr", "matplotlib", "numba"):
            logging.getLogger(_noisy).setLevel(logging.WARNING)

    def _setup_system_log(self):
        """One always-on, size-bounded system log so a system-level error is
        always captured even when no translation is running. Kept simple: a
        single rotating file under data/log."""
        try:
            from core.paths import DATA_DIR
            log_dir = os.path.join(DATA_DIR, "log")
            os.makedirs(log_dir, exist_ok=True)
            h = RotatingFileHandler(
                os.path.join(log_dir, "system.log"),
                maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
            h.setLevel(logging.INFO)
            h.setFormatter(_FILE_FMT)
            h.addFilter(_SystemFilter())   # problems + lifecycle only
            self.logger.addHandler(h)
        except Exception:  # noqa: BLE001 — never let logging setup break startup
            pass

    # --- per-task (per-project) logs ------------------------------------- #
    def open_task_log(self, task_id, log_dir, filename):
        """Start a per-project log file for ``task_id`` under ``log_dir`` (the
        project's result folder). Returns the log file path."""
        os.makedirs(log_dir, exist_ok=True)
        safe = os.path.basename(filename)
        safe = ''.join(c for c in safe if c.isalnum() or c in '._- ')
        stamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        path = os.path.join(log_dir, f"{stamp}_{safe}.log")
        if self._routing is not None:
            self._routing.open_task(task_id, path)
        return path

    def close_task_log(self, task_id):
        if self._routing is not None:
            self._routing.close_task(task_id)

    @staticmethod
    def bind_task(task_id):
        """Bind this context (current thread) to ``task_id`` so its logs route to
        that task's file. Returns a token for unbind_task()."""
        return _log_task.set(task_id)

    @staticmethod
    def unbind_task(token):
        try:
            _log_task.reset(token)
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def worker_initializer(task_id):
        """ThreadPoolExecutor initializer: bind each worker thread to the task so
        segment-translation logs route to the task's file too."""
        _log_task.set(task_id)

    def attach_to_logger(self, logger_name, level=logging.INFO):
        """Route a third-party logger (and its children) into the per-task +
        system logs too — e.g. BabelDOC, whose own worker threads don't get our
        contextvar. Idempotent. Keeps propagation so the console is unaffected."""
        lg = logging.getLogger(logger_name)
        lg.setLevel(level)
        if self._routing is not None and self._routing not in lg.handlers:
            lg.addHandler(self._routing)
        for h in self.logger.handlers:          # the rotating system handler
            if isinstance(h, RotatingFileHandler) and h not in lg.handlers:
                lg.addHandler(h)

    def set_fallback_task(self, task_id):
        """Route records from contextvar-less threads (BabelDOC) to this task.
        Set just before such work, cleared right after."""
        if self._routing is not None:
            self._routing.set_fallback(task_id)

    def clear_fallback_task(self):
        if self._routing is not None:
            self._routing.set_fallback(None)

    def get_logger(self):
        return self.logger


# Create file logger instance
file_logger = FileLogger(console_level=logging.INFO, file_level=logging.DEBUG)
app_logger = file_logger.get_logger()


def system_event(msg, level=logging.INFO):
    """Log a LIFECYCLE event that belongs in the system log even at INFO level
    (startup, config migration, plugin install/uninstall, task start/finish).
    Flagged so it passes the system-log filter; also appears in the console and
    (if a task is bound) the project log."""
    app_logger.log(level, msg, extra={"sysevent": True})


def install_excepthooks():
    """Record otherwise-uncaught exceptions (main thread + worker threads) in the
    system log so a crash is always reconstructable. Chains to the originals so
    the normal traceback still prints. Idempotent."""
    import traceback
    if getattr(install_excepthooks, "_done", False):
        return
    install_excepthooks._done = True
    _orig = sys.excepthook

    def _hook(exc_type, exc, tb):
        try:
            system_event("Uncaught exception:\n" + "".join(
                traceback.format_exception(exc_type, exc, tb)).strip(), level=logging.ERROR)
        except Exception:  # noqa: BLE001
            pass
        _orig(exc_type, exc, tb)
    sys.excepthook = _hook

    def _thook(args):
        try:
            system_event(f"Uncaught exception in thread {args.thread.name}:\n" + "".join(
                traceback.format_exception(args.exc_type, args.exc_value,
                                           args.exc_traceback)).strip(), level=logging.ERROR)
        except Exception:  # noqa: BLE001
            pass
    threading.excepthook = _thook

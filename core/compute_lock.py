"""Global lock that serializes heavy LOCAL compute so concurrent translation
tasks don't thrash a single GPU (or a weak CPU).

Only BATCH local model inference is gated — file transcription (video/audio
subtitles) and image OCR. Deliberately NOT gated:
  - LLM translation: network-bound (online API / a separate ollama-lm-studio
    process), so it should run in parallel across files.
  - Real-time + quick-translate voice STT: interactive and latency-sensitive;
    they keep their own _STT_LOCK (serializing their own utterances) and must
    not be blocked for minutes behind a long batch transcription. (Trade-off:
    live recognition can still contend with a running batch job on the GPU.)
So e.g. three videos transcribe one-at-a-time on the GPU, but their
text-translation phases can still overlap.

Usage:
    from core.compute_lock import GPU_LOCK
    with GPU_LOCK:
        ...heavy inference...
"""
import contextlib
import threading

GPU_LOCK = threading.Lock()

_waiters = 0
_waiters_mu = threading.Lock()


@contextlib.contextmanager
def queued():
    """Count this thread while it waits for GPU_LOCK, yielding how many tasks
    (including this one) are queued. A plain Lock exposes no queue length, and
    without it a file that sits 20 minutes behind three other transcriptions
    looks indistinguishable from one that is stuck."""
    global _waiters
    with _waiters_mu:
        _waiters += 1
        n = _waiters
    try:
        yield n
    finally:
        with _waiters_mu:
            _waiters -= 1

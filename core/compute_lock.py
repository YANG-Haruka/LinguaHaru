"""Global lock that serializes heavy LOCAL compute so concurrent translation
tasks don't thrash a single GPU (or a weak CPU).

Only LOCAL model inference is gated — speech-to-text transcription, image OCR,
and speaker diarization. LLM translation is NOT gated: it's network-bound
(online API or a separate ollama/lm-studio process) and benefits from running
in parallel across files. So e.g. three videos transcribe one-at-a-time on the
GPU, but their text-translation phases can still overlap.

Usage:
    from core.compute_lock import GPU_LOCK
    with GPU_LOCK:
        ...heavy inference...
"""
import threading

GPU_LOCK = threading.Lock()

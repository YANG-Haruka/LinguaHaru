"""Global lock that serializes heavy LOCAL compute so concurrent translation
tasks don't thrash a single GPU (or a weak CPU).

Only BATCH local model inference is gated — file transcription (video/audio
subtitles), image OCR, and speaker diarization. Deliberately NOT gated:
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
import threading

GPU_LOCK = threading.Lock()

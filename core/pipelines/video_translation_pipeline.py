# pipeline/video_translation_pipeline.py
# Video/audio subtitle transcription. ffmpeg extracts the audio track, then a
# selectable speech-to-text (STT) engine transcribes it into a timed SRT:
#   - faster-whisper (multilingual, several model sizes)
#   - SenseVoice via funasr (zh/en/ja/ko/yue only, fast & accurate for those)
# The SRT is then (optionally) translated by the existing SRT pipeline.
#
# Optional module - requires: faster-whisper and/or funasr (pip), plus ffmpeg.
import os
import json
import shutil
import subprocess
import tempfile
import threading

from core.log_config import app_logger

# Serialize model LOADING. Two batch jobs (e.g. two videos) starting at once
# both tried to load the same model via device_map/accelerate concurrently,
# which corrupted the load ("Cannot copy out of meta tensor"). Loading is rare
# and quick relative to a translation, so one global lock is fine; inference
# still runs concurrently once the (shared) model is cached.
_LOAD_LOCK = threading.RLock()

# --- STT model catalogue ----------------------------------------------------
# Each entry: id (stored in config), label (UI), engine, size/model name.
# Curated "friendly subset" of speech-to-text models. `disk` = approximate
# download size, `vram` = approximate GPU peak (CPU mode uses RAM, not VRAM).
# Real-time voice favors the smaller/faster ones (lower latency).
STT_MODELS = [
    {"id": "sensevoice-small",       "engine": "sensevoice", "size": "iic/SenseVoiceSmall",
     "label": "SenseVoice Small (zh/en/ja/ko/yue · fast)", "disk": "~900MB", "vram": "~1–2GB"},
    {"id": "whisper-tiny",           "engine": "whisper",    "size": "tiny",
     "label": "Whisper Tiny (fastest)", "disk": "~75MB", "vram": "~1GB"},
    {"id": "whisper-base",           "engine": "whisper",    "size": "base",
     "label": "Whisper Base", "disk": "~145MB", "vram": "~1GB"},
    {"id": "whisper-small",          "engine": "whisper",    "size": "small",
     "label": "Whisper Small (balanced)", "disk": "~490MB", "vram": "~2GB"},
    {"id": "whisper-large-v3-turbo", "engine": "whisper",    "size": "large-v3-turbo",
     "label": "Whisper Large-v3 Turbo (accurate)", "disk": "~1.6GB", "vram": "~6GB"},
    {"id": "qwen3-asr-0.6b",         "engine": "qwen3asr",   "size": "Qwen/Qwen3-ASR-0.6B",
     "label": "Qwen3-ASR 0.6B (accurate · 30 langs)", "disk": "~2GB", "vram": "~3GB"},
    {"id": "qwen3-asr-1.7b",         "engine": "qwen3asr",   "size": "Qwen/Qwen3-ASR-1.7B",
     "label": "Qwen3-ASR 1.7B (most accurate)", "disk": "~4GB", "vram": "~6GB"},
]

# Default for video subtitles AND real-time voice: SenseVoice is small + fast.
DEFAULT_STT_MODEL = "sensevoice-small"

# Single source of truth mapping a UI language code (core.languages_config)
# to the language SenseVoice's recognizer expects. SenseVoice recognizes
# Mandarin and Cantonese; Traditional-Chinese audio is Mandarin, so zh-Hant
# maps to "zh". Cantonese ("yue") is recognized via auto-detect and normalized
# back to "zh" on output (see _recognize_sensevoice) since it has no UI code.
_SENSEVOICE_LANG_MAP = {"zh": "zh", "zh-Hant": "zh", "en": "en", "ja": "ja", "ko": "ko"}

# Language codes that SenseVoice can transcribe; everything else is disabled in
# the UI when SenseVoice is selected. Derived from the map above (no drift).
SENSEVOICE_SUPPORTED_CODES = set(_SENSEVOICE_LANG_MAP)

from core.paths import SYSTEM_CONFIG as _SYSTEM_CONFIG  # absolute; frozen-safe

_whisper_models = {}   # size -> WhisperModel
_sensevoice = None     # (asr_model, vad_model)
_qwen_models = {}      # repo id -> Qwen3ASRModel


def _stt_device():
    """Return 'cuda' if a CUDA-capable GPU + GPU torch build are present, else
    'cpu'. STT is many times faster on GPU; this is auto-detected so a machine
    with an NVIDIA GPU (and a CUDA torch build) uses it without any config."""
    try:
        import torch
        if torch.cuda.is_available():
            return "cuda"
    except Exception:  # noqa: BLE001 — torch missing or broken -> CPU
        pass
    return "cpu"


def stt_model_ids():
    return [m["id"] for m in STT_MODELS]


def get_stt_model(model_id):
    for m in STT_MODELS:
        if m["id"] == model_id:
            return m
    return STT_MODELS[0]


def _selected_model_for(config_key):
    try:
        with open(_SYSTEM_CONFIG, encoding="utf-8") as f:
            cfg_id = json.load(f).get(config_key)
        if cfg_id and any(m["id"] == cfg_id for m in STT_MODELS):
            return cfg_id
    except Exception:
        pass
    return None


def get_selected_stt_model():
    """STT model id for VIDEO/AUDIO subtitles (config 'stt_model')."""
    cfg = _selected_model_for("stt_model")
    if cfg:
        return cfg
    env = os.environ.get("LINGUAHARU_WHISPER_MODEL")
    if env:
        # Back-compat: env held a bare whisper size like "small"
        return env if env in stt_model_ids() else f"whisper-{env}"
    return DEFAULT_STT_MODEL


def get_selected_live_stt_model():
    """STT model id for REAL-TIME VOICE (config 'live_stt_model'), independent
    of the video-subtitle model so each plugin picks its own."""
    return _selected_model_for("live_stt_model") or DEFAULT_STT_MODEL


def get_selected_quick_stt_model():
    """STT model id for QUICK-TRANSLATE voice input (config 'quick_stt_model'),
    independent of the live/video models."""
    return _selected_model_for("quick_stt_model") or DEFAULT_STT_MODEL


def _resolve_stt_engine(model_def):
    """Resolve (engine, size) honoring which optional deps are actually
    installed, falling back across engines so a missing dependency degrades
    gracefully instead of hard-failing transcription.

    e.g. config selects SenseVoice but only faster-whisper is installed -> use
    whisper 'small' rather than crashing with ModuleNotFoundError: funasr."""
    import importlib.util
    has_funasr = importlib.util.find_spec("funasr") is not None
    has_whisper = importlib.util.find_spec("faster_whisper") is not None
    has_qwen = importlib.util.find_spec("qwen_asr") is not None
    engine, size = model_def["engine"], model_def["size"]
    if engine == "qwen3asr" and not has_qwen:
        # Qwen3-ASR package not installed -> degrade to the best available engine.
        if has_funasr:
            app_logger.warning("qwen-asr not installed; falling back to SenseVoice.")
            return "sensevoice", "iic/SenseVoiceSmall"
        if has_whisper:
            app_logger.warning("qwen-asr not installed; falling back to faster-whisper 'small'.")
            return "whisper", "small"
    if engine == "sensevoice" and not has_funasr and has_whisper:
        app_logger.warning(
            "SenseVoice (funasr) not installed; falling back to faster-whisper 'small'.")
        return "whisper", "small"
    if engine == "whisper" and not has_whisper and has_funasr:
        app_logger.warning(
            "faster-whisper not installed; falling back to SenseVoice.")
        return "sensevoice", "iic/SenseVoiceSmall"
    return engine, size


def _inuse_stt_keys():
    """(engine, size) keys for the STT models currently selected by ANY feature
    (video subtitles / real-time voice / quick-translate voice)."""
    keys = set()
    for getter in (get_selected_stt_model, get_selected_live_stt_model,
                   get_selected_quick_stt_model):
        try:
            keys.add(_resolve_stt_engine(get_stt_model(getter())))
        except Exception:  # noqa: BLE001
            pass
    return keys


def release_stt_model(model_id):
    """Force-free a specific STT model from the in-memory cache so its files on
    disk can be deleted (Windows file locks). Best-effort."""
    global _sensevoice
    spec = next((m for m in STT_MODELS if m["id"] == model_id), None)
    if not spec:
        return
    engine, size = spec.get("engine"), spec.get("size")
    try:
        if engine == "whisper":
            _whisper_models.pop(size, None)
        elif engine == "qwen3asr":
            _qwen_models.pop(size, None)
        elif engine == "sensevoice":
            _sensevoice = None
        import gc
        gc.collect()
    except Exception:  # noqa: BLE001
        pass


def release_unused_stt_models():
    """Free any loaded STT model that NO feature selects anymore, so switching a
    model releases the previous one (RAM/VRAM). Features sharing the SAME model
    keep using the single cached instance — no double-load. Called on feature
    start (preload) and on model switches."""
    global _sensevoice
    inuse = _inuse_stt_keys()
    freed = []
    for size in list(_whisper_models):
        if ("whisper", size) not in inuse:
            del _whisper_models[size]
            freed.append(f"whisper:{size}")
    for repo in list(_qwen_models):
        if ("qwen3asr", repo) not in inuse:
            del _qwen_models[repo]
            freed.append(f"qwen:{repo}")
    if _sensevoice is not None and not any(e == "sensevoice" for e, _s in inuse):
        _sensevoice = None
        freed.append("sensevoice")
    if freed:
        app_logger.info(f"Released unused STT model(s): {', '.join(freed)}")
        try:
            import gc
            import torch
            gc.collect()
            if torch.cuda.is_available():
                torch.cuda.empty_cache()
        except Exception:  # noqa: BLE001
            pass
    return freed


# --- audio extraction -------------------------------------------------------
def _format_srt_time(seconds):
    if seconds is None or seconds < 0:
        seconds = 0.0
    ms = int(round((seconds - int(seconds)) * 1000))
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d},{ms:03d}"


def extract_audio_to_wav(media_path, output_dir):
    """Extract/normalize the audio track to 16 kHz mono WAV via ffmpeg.

    Uses the pip-bundled imageio-ffmpeg binary when present (no PATH install
    needed); falls back to a system ffmpeg on PATH."""
    from core.optional_modules import ffmpeg_exe
    exe = ffmpeg_exe()
    if exe is None:
        raise RuntimeError(
            "ffmpeg not found - install the Video/Audio plugin (bundles ffmpeg "
            "via imageio-ffmpeg), or install ffmpeg from https://ffmpeg.org/."
        )
    wav_path = os.path.join(output_dir, "audio_16k.wav")
    cmd = [exe, "-y", "-i", media_path, "-vn",
           "-ac", "1", "-ar", "16000", "-f", "wav", wav_path]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed: {proc.stderr[-500:]}")
    return wav_path


# --- whisper engine ---------------------------------------------------------
def _get_whisper_model(size):
    if size in _whisper_models:
        return _whisper_models[size]
    with _LOAD_LOCK:
        if size not in _whisper_models:   # double-check inside the lock
            from faster_whisper import WhisperModel
            from core.model_store import whisper_dir
            dev = _stt_device()
            # float16 on GPU is fast+accurate; int8 on CPU is ~4x faster.
            ctype = "float16" if dev == "cuda" else "int8"
            app_logger.info(
                f"Loading faster-whisper '{size}' on {dev} ({ctype})...")
            # download_root keeps whisper models in the unified data/models dir.
            _whisper_models[size] = WhisperModel(
                size, device=dev, compute_type=ctype, download_root=whisper_dir())
    return _whisper_models[size]


def _transcribe_whisper(wav_path, size, src_lang, progress_callback):
    """Yield (start, end, text) tuples for each spoken segment."""
    model = _get_whisper_model(size)
    language = src_lang.split("-")[0] if src_lang else None  # zh, en, ja, ...
    segments, info = model.transcribe(wav_path, language=language, vad_filter=True)
    duration = getattr(info, "duration", None) or 0
    out = []
    for seg in segments:
        text = seg.text.strip()
        if text:
            out.append((seg.start, seg.end, text))
        if progress_callback and duration:
            # Full 0..1 of this phase; caller maps it into the extraction range.
            progress_callback(min(seg.end / duration, 1.0),
                              desc=f"Transcribing (whisper-{size})...")
    return out


# --- SenseVoice engine (funasr) --------------------------------------------
def _sensevoice_lang(src_lang):
    """Map a UI language code to SenseVoice's recognizer code (or 'auto').

    Tries the full code first (so zh-Hant resolves), then the base subtag.
    SenseVoice also accepts 'yue' (Cantonese) directly if ever passed."""
    code = src_lang or ""
    if code in _SENSEVOICE_LANG_MAP:
        return _SENSEVOICE_LANG_MAP[code]
    base = code.split("-")[0]
    if base == "yue":
        return "yue"
    return _SENSEVOICE_LANG_MAP.get(base, "auto")


# SenseVoice on the HF mirror, downloaded file-by-file. modelscope's link
# stalls on the 893MB model.pt (not a bandwidth issue — the connection hangs);
# the HF mirror is fast and reliable per file. Falls back to modelscope id.
_SENSEVOICE_HF_REPO = "FunAudioLLM/SenseVoiceSmall"
_SENSEVOICE_FILES = ["model.pt", "config.yaml", "configuration.json", "am.mvn",
                     "chn_jpn_yue_eng_ko_spectok.bpe.model"]


def _sensevoice_local_dir():
    """Fetch SenseVoice from the HF mirror (per-file) and return its local dir,
    or None if the mirror is unreachable (caller falls back to modelscope).

    Downloads into a stable, project-local cache (data/models) instead of the
    user's global HF cache, so the model lives in a predictable place and is
    downloaded only once (hf_hub_download reuses cached files)."""
    from core.model_store import current_dir, pick_hf_endpoint
    if "HF_ENDPOINT" not in os.environ:
        os.environ["HF_ENDPOINT"] = pick_hf_endpoint()
    cache_dir = current_dir()
    os.makedirs(cache_dir, exist_ok=True)
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        return None
    root = None
    for fname in _SENSEVOICE_FILES:
        last = None
        for _ in range(4):
            try:
                last = hf_hub_download(_SENSEVOICE_HF_REPO, fname, cache_dir=cache_dir)
                break
            except Exception:  # noqa: BLE001 - transient mirror hiccup, retry
                last = None
        if not last:
            return None
        root = os.path.dirname(last)
    return root


def _get_sensevoice(model_name):
    global _sensevoice
    if _sensevoice is not None:
        return _sensevoice
    with _LOAD_LOCK:
        if _sensevoice is None:   # double-check inside the lock
            from funasr import AutoModel
            dev = _stt_device()
            app_logger.info(f"Loading SenseVoice + fsmn-vad on {dev} (downloads on first use)...")
            local = _sensevoice_local_dir()
            if local:
                app_logger.info(f"SenseVoice from HF mirror: {local}")
                asr = AutoModel(model=local, disable_update=True, device=dev)
            else:  # mirror unreachable -> modelscope (may be slow)
                app_logger.warning("HF mirror unavailable; loading SenseVoice via modelscope.")
                asr = AutoModel(model=model_name, disable_update=True, device=dev)
            vad = AutoModel(model="fsmn-vad", disable_update=True, device=dev,
                            vad_kwargs={"max_single_segment_time": 30000})
            _sensevoice = (asr, vad)
    return _sensevoice


_vad_only = None  # fsmn-vad loaded standalone (so Qwen needn't load SenseVoice)


def _get_vad():
    """fsmn-vad on its own (used to time-segment audio for the Qwen engine)."""
    global _vad_only
    if _vad_only is not None:
        return _vad_only
    with _LOAD_LOCK:
        if _vad_only is None:   # double-check inside the lock
            from funasr import AutoModel
            _vad_only = AutoModel(model="fsmn-vad", disable_update=True,
                                  device=_stt_device(),
                                  vad_kwargs={"max_single_segment_time": 30000})
    return _vad_only


# --- Qwen3-ASR engine (qwen-asr) -------------------------------------------
_QWEN_LANG_CODE = {"Chinese": "zh", "English": "en", "Japanese": "ja",
                   "Korean": "ko", "Cantonese": "zh"}


def _get_qwen(model_name):
    if model_name in _qwen_models:
        return _qwen_models[model_name]
    with _LOAD_LOCK:
        if model_name not in _qwen_models:   # double-check inside the lock
            from qwen_asr import Qwen3ASRModel
            dm = "cuda:0" if _stt_device() == "cuda" else "cpu"
            app_logger.info(f"Loading {model_name} on {dm} (downloads on first use)...")
            _qwen_models[model_name] = Qwen3ASRModel.from_pretrained(model_name, device_map=dm)
    return _qwen_models[model_name]


# --- Speaker diarization (who spoke) — cam++ voiceprint embeddings ----------
# VAD says WHEN someone speaks; this says WHO. Shared primitive used two ways:
#   - subtitles: embed every transcribed segment, cluster offline -> labels
#   - real-time: assign each utterance online to the nearest known speaker
_speaker_embedder = None
_DIA_THRESHOLD = 0.55   # cosine distance; below this two clips are the same speaker


def _get_speaker_embedder():
    global _speaker_embedder
    if _speaker_embedder is not None:
        return _speaker_embedder
    with _LOAD_LOCK:
        if _speaker_embedder is None:   # double-check inside the lock
            from funasr import AutoModel
            app_logger.info(f"Loading speaker model (cam++) on {_stt_device()}...")
            _speaker_embedder = AutoModel(model="cam++", disable_update=True,
                                          device=_stt_device())
    return _speaker_embedder


def embed_speaker(audio_f32, sample_rate=16000):
    """L2-normalized 192-d cam++ voiceprint for a mono 16k float32 clip
    (None if too short or on error)."""
    import numpy as np
    if audio_f32 is None or len(audio_f32) < int(0.25 * sample_rate):
        return None
    try:
        res = _get_speaker_embedder().generate(input=audio_f32, fs=sample_rate)
        emb = res[0]["spk_embedding"]
        v = emb.detach().cpu().numpy().reshape(-1) if hasattr(emb, "detach") \
            else np.asarray(emb).reshape(-1)
        v = v.astype("float32")
        n = float(np.linalg.norm(v)) or 1.0
        return v / n
    except Exception as e:  # noqa: BLE001
        app_logger.warning(f"Speaker embedding failed: {e}")
        return None


def _load_wav_16k_mono(path):
    """Read a 16k mono 16-bit PCM wav (what extract_audio_to_wav writes) into a
    float32 array in [-1, 1]."""
    import wave
    import numpy as np
    with wave.open(path, "rb") as w:
        sr = w.getframerate()
        raw = w.readframes(w.getnframes())
    a = np.frombuffer(raw, dtype=np.int16).astype("float32") / 32768.0
    return a, sr


def diarize_triples(wav_path, triples):
    """Assign a speaker number (1..N) to each (start, end, text) triple by
    clustering cam++ embeddings. Returns a list of ints (all 1 on any failure,
    so labeling degrades gracefully to single-speaker)."""
    try:
        import numpy as np
        from sklearn.cluster import AgglomerativeClustering
        audio, sr = _load_wav_16k_mono(wav_path)
        embs, idx = [], []
        for i, (s, e, _t) in enumerate(triples):
            seg = audio[int(s * sr):int(e * sr)]
            v = embed_speaker(seg, sr)
            if v is not None:
                embs.append(v)
                idx.append(i)
        labels_out = [1] * len(triples)
        if len(embs) <= 1:
            return labels_out
        X = np.vstack(embs)
        cl = AgglomerativeClustering(
            n_clusters=None, distance_threshold=_DIA_THRESHOLD,
            metric="cosine", linkage="average")
        raw_labels = cl.fit_predict(X)
        # Renumber clusters by first appearance -> stable 1..N.
        mapping, nxt = {}, 1
        for j, lab in zip(idx, raw_labels):
            if lab not in mapping:
                mapping[lab] = nxt
                nxt += 1
            labels_out[j] = mapping[lab]
        app_logger.info(f"Diarization: {nxt - 1} speaker(s) across {len(triples)} segments")
        return labels_out
    except Exception as e:  # noqa: BLE001
        app_logger.warning(f"Diarization failed, single speaker: {e}")
        return [1] * len(triples)


class OnlineSpeakerAssigner:
    """Streaming speaker labeling for real-time captions: keep a running centroid
    per speaker and assign each new utterance to the nearest one (or a new
    speaker if none is close enough). No global clustering — low latency."""

    def __init__(self, threshold=_DIA_THRESHOLD):
        self.threshold = threshold
        self._centroids = []   # list of (np.array, count)

    def assign(self, audio_f32, sample_rate=16000):
        """Return a 1-based speaker id for this utterance (1 if embedding fails,
        so it never blocks captioning)."""
        import numpy as np
        v = embed_speaker(audio_f32, sample_rate)
        if v is None:
            return 1
        best, best_d = -1, 1e9
        for i, (c, _n) in enumerate(self._centroids):
            d = float(1.0 - np.dot(v, c) / ((np.linalg.norm(c)) or 1.0))
            if d < best_d:
                best, best_d = i, d
        if best >= 0 and best_d <= self.threshold:
            c, n = self._centroids[best]
            self._centroids[best] = ((c * n + v) / (n + 1), n + 1)  # update centroid
            return best + 1
        self._centroids.append((v, 1))
        return len(self._centroids)


def _qwen_text(result):
    return (result.text if hasattr(result, "text") else str(result)).strip()


def _recognize_qwen(audio, src_lang, model_name, sample_rate=16000):
    """Recognize one utterance (16 kHz float32) with Qwen3-ASR (no temp file —
    qwen-asr accepts a (ndarray, sr) tuple)."""
    model = _get_qwen(model_name)
    results = model.transcribe((audio, sample_rate))
    if not results:
        return "", (src_lang or "auto")
    r = results[0]
    detected = _QWEN_LANG_CODE.get(getattr(r, "language", None), src_lang or "auto")
    return _qwen_text(r), detected


def _transcribe_qwen(wav_path, model_name, src_lang, progress_callback):
    """VAD-segment the audio (for SRT timing) then batch-recognize the segments
    with Qwen3-ASR. Falls back to a single whole-file pass if VAD is unavailable."""
    import wave
    import numpy as np

    model = _get_qwen(model_name)
    with wave.open(wav_path, "rb") as wf:
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    try:
        vad_res = _get_vad().generate(input=wav_path)
        segments = (vad_res[0].get("value") if vad_res else None) or []
    except Exception:  # noqa: BLE001 — no funasr/VAD -> one big segment
        segments = []
    if not segments:
        segments = [[0, len(audio) / sr * 1000.0]]

    chunks, spans = [], []
    for s_ms, e_ms in segments:
        c = audio[int(s_ms / 1000 * sr):int(e_ms / 1000 * sr)]
        if c.size:
            chunks.append((c, sr))
            spans.append((s_ms / 1000.0, e_ms / 1000.0))

    out = []
    BATCH = 16
    total = len(chunks) or 1
    for i in range(0, len(chunks), BATCH):
        results = model.transcribe(chunks[i:i + BATCH])
        for (start, end), r in zip(spans[i:i + BATCH], results):
            txt = _qwen_text(r)
            if txt:
                out.append((start, end, txt))
        if progress_callback:
            progress_callback(min(1.0, (i + BATCH) / total), desc="Transcribing (Qwen3-ASR)...")
    return out


def _transcribe_sensevoice(wav_path, model_name, src_lang, progress_callback):
    """VAD-segment the audio, recognize each segment with SenseVoice, and return
    (start, end, text) tuples. Timing comes from the VAD so the SRT is aligned."""
    import wave
    import numpy as np
    from funasr.utils.postprocess_utils import rich_transcription_postprocess

    asr, vad = _get_sensevoice(model_name)
    vad_res = vad.generate(input=wav_path)
    segments = (vad_res[0].get("value") if vad_res else None) or []

    with wave.open(wav_path, "rb") as wf:
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    lang = _sensevoice_lang(src_lang)
    total = len(segments) or 1
    out = []
    for i, seg in enumerate(segments):
        s_ms, e_ms = seg[0], seg[1]
        chunk = audio[int(s_ms / 1000 * sr):int(e_ms / 1000 * sr)]
        if chunk.size == 0:
            continue
        res = asr.generate(input=chunk, fs=sr, language=lang, use_itn=True)
        text = rich_transcription_postprocess(res[0]["text"]).strip() if res else ""
        if text:
            out.append((s_ms / 1000.0, e_ms / 1000.0, text))
        if progress_callback:
            # Emit the full 0..1 of this phase; the caller maps it into the
            # extraction sub-range (e.g. 0..50%) via EXTRACTION_PROGRESS_SHARE.
            progress_callback((i + 1) / total, desc="Transcribing (SenseVoice)...")
    return out


def recognizer_ready(getter=None):
    """True if the selected STT model is already loaded (no first-use load
    delay). Defaults to the real-time model; pass get_selected_quick_stt_model
    (etc.) to check another feature's model."""
    getter = getter or get_selected_live_stt_model
    try:
        engine, size = _resolve_stt_engine(get_stt_model(getter()))
        if engine == "qwen3asr":
            return size in _qwen_models
        if engine == "sensevoice":
            return _sensevoice is not None
        return size in _whisper_models
    except Exception:  # noqa: BLE001
        return False


def preload_recognizer(model_id=None):
    """Load the real-time-voice STT model now (downloads on first use) + warm up,
    so the first utterance isn't blocked. model_id defaults to the live-voice
    selection; the engine degrades gracefully if its dep is missing. True=ready."""
    import time
    try:
        release_unused_stt_models()   # free models no feature selects anymore
        model_def = get_stt_model(model_id or get_selected_live_stt_model())
        engine, size = _resolve_stt_engine(model_def)
        if engine == "sensevoice":
            t0 = time.time()
            _get_sensevoice(model_def["size"])
            app_logger.info(f"SenseVoice loaded in {time.time() - t0:.1f}s; warming up…")
            try:
                import numpy as np
                asr, _vad = _sensevoice
                t1 = time.time()
                asr.generate(input=np.zeros(16000, dtype=np.float32), fs=16000,
                             language="auto", use_itn=True)
                app_logger.info(f"SenseVoice warm-up done in {time.time() - t1:.1f}s")
            except Exception as e:  # noqa: BLE001
                app_logger.warning(f"SenseVoice warm-up skipped: {e}")
            return True
        if engine == "qwen3asr":
            t0 = time.time()
            _get_qwen(size)
            app_logger.info(f"Qwen3-ASR ({size}) loaded in {time.time() - t0:.1f}s")
            return True
        _get_whisper_model(size)
        return True
    except Exception as e:  # noqa: BLE001
        app_logger.error(f"Preload recognizer failed: {e}")
    return False


def recognize_utterance(pcm16_bytes, src_lang=None, sample_rate=16000, model_id=None):
    """Recognize one short utterance (raw mono PCM16) for real-time voice.

    Routes to the engine of the selected live model (model_id defaults to the
    live-voice selection), degrading gracefully if that engine's dep is missing.
    Returns (text, detected_lang) — text is '' if no speech.

    The client does VAD and sends a complete utterance, so no server-side
    segmentation is needed here."""
    import importlib.util
    import time
    import numpy as np

    audio = np.frombuffer(pcm16_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if audio.size == 0:
        return "", None
    # Boost quiet speech so the recognizer can hear it: peak-normalize toward
    # ~0.95, but cap the gain (≤12x) so near-silence/noise isn't blown up into
    # garbage. This is why a soft voice still gets recognized.
    peak = float(np.max(np.abs(audio)))
    if 0.0 < peak < 0.95:
        audio = audio * min(12.0, 0.95 / peak)
    dur = audio.size / float(sample_rate or 16000)
    t0 = time.time()

    has_funasr = importlib.util.find_spec("funasr") is not None
    has_whisper = importlib.util.find_spec("faster_whisper") is not None
    has_qwen = importlib.util.find_spec("qwen_asr") is not None
    if not (has_funasr or has_whisper or has_qwen):
        raise RuntimeError(
            "No speech-to-text engine installed. Install the Real-Time Voice "
            "plugin (faster-whisper / funasr / qwen-asr).")

    model_def = get_stt_model(model_id or get_selected_live_stt_model())
    engine, size = _resolve_stt_engine(model_def)
    if engine == "sensevoice":
        text, detected = _recognize_sensevoice(audio, src_lang, sample_rate, model_def["size"])
        app_logger.info(f"STT(SenseVoice) {dur:.1f}s audio -> {time.time() - t0:.2f}s")
    elif engine == "qwen3asr":
        text, detected = _recognize_qwen(audio, src_lang, size, sample_rate)
        app_logger.info(f"STT(Qwen3-ASR:{size}) {dur:.1f}s audio -> {time.time() - t0:.2f}s")
    else:
        text, detected = _recognize_whisper(audio, src_lang, size)
        app_logger.info(f"STT(whisper:{size}) {dur:.1f}s audio -> {time.time() - t0:.2f}s")
    return text, detected


def _recognize_sensevoice(audio, src_lang, sample_rate, model_name):
    import re
    from funasr.utils.postprocess_utils import rich_transcription_postprocess
    asr, _vad = _get_sensevoice(model_name)
    lang = _sensevoice_lang(src_lang)
    res = asr.generate(input=audio, fs=sample_rate, language=lang, use_itn=True)
    if not res:
        return "", None
    raw = res[0].get("text", "") if isinstance(res[0], dict) else str(res[0])
    m = re.search(r"<\|([a-z]{2,3})\|>", raw)
    detected = m.group(1) if m else None
    if detected == "yue":
        detected = "zh"
    return _strip_sensevoice_marks(rich_transcription_postprocess(raw)), detected


# SenseVoice's rich_transcription_postprocess injects emotion/event emojis
# (<|HAPPY|>→😊, <|BGM|>→🎼, <|Applause|>→👏, …). We want plain text for
# translation/captioning, so strip those markers (and any leftover <|tag|>).
_SENSEVOICE_EMOJIS = set("😊😔😡😐🤢😱🎼👏😀😄😭🤧😷🤔🥱🎤🎶❓")


def _strip_sensevoice_marks(text):
    import re
    text = re.sub(r"<\|[^|]*\|>", "", text or "")
    text = "".join(ch for ch in text if ch not in _SENSEVOICE_EMOJIS)
    return re.sub(r"\s{2,}", " ", text).strip()


def _recognize_whisper(audio, src_lang, size="small"):
    """Recognize a pre-VAD'd utterance (16 kHz float32) with faster-whisper.
    The client already segmented speech, so vad_filter is off (it can drop short
    clips); beam_size=1 keeps it responsive for real time."""
    model = _get_whisper_model(size)
    language = (src_lang or "").split("-")[0] or None
    segments, info = model.transcribe(
        audio, language=language, vad_filter=False, beam_size=1)
    text = " ".join(s.text.strip() for s in segments).strip()
    detected = getattr(info, "language", None) or language
    return text, detected


# --- public entry point -----------------------------------------------------
def _speaker_labels_enabled():
    """Config toggle: prefix subtitle cues with a speaker label (S1/S2/...)."""
    try:
        from core.paths import SYSTEM_CONFIG
        with open(SYSTEM_CONFIG, encoding="utf-8") as f:
            return bool(json.load(f).get("subtitle_speaker_labels", False))
    except Exception:  # noqa: BLE001
        return False


def transcribe_media_to_srt(media_path, temp_dir, src_lang=None, progress_callback=None,
                            transcript_copy_dir=None, stt_model=None):
    """Transcribe a video/audio file and write an SRT next to the temp data.

    stt_model: an id from STT_MODELS; defaults to the UI-selected one.
    Returns the path of the generated SRT (named after the media file)."""
    filename = os.path.splitext(os.path.basename(media_path))[0]
    os.makedirs(temp_dir, exist_ok=True)

    model_id = stt_model or get_selected_stt_model()
    model_def = get_stt_model(model_id)
    engine, size = _resolve_stt_engine(model_def)

    with tempfile.TemporaryDirectory(dir=temp_dir) as audio_dir:
        if progress_callback:
            progress_callback(0.01, desc="Extracting audio...")
        wav_path = extract_audio_to_wav(media_path, audio_dir)

        if progress_callback:
            progress_callback(0.03, desc=f"Transcribing ({engine})...")

        try:
            if engine == "sensevoice":
                triples = _transcribe_sensevoice(wav_path, size, src_lang, progress_callback)
            elif engine == "qwen3asr":
                triples = _transcribe_qwen(wav_path, size, src_lang, progress_callback)
            else:
                triples = _transcribe_whisper(wav_path, size, src_lang, progress_callback)
        except Exception as e:  # noqa: BLE001 — log WHY (e.g. model download
            # failed) into the per-file log instead of failing silently, then
            # re-raise so the job is still marked failed.
            app_logger.error(
                f"STT transcription failed (engine={engine}, model={model_id}): "
                f"{type(e).__name__}: {e}")
            raise

        # Optional speaker labels: prefix each cue with "S1: "/"S2: " (the tag
        # survives translation as a name prefix). Done while the wav still exists.
        if triples and _speaker_labels_enabled():
            if progress_callback:
                progress_callback(0.48, desc="Identifying speakers...")
            spk = diarize_triples(wav_path, triples)
            triples = [(s, e, f"S{spk[i]}: {t}") for i, (s, e, t) in enumerate(triples)]

    if not triples:
        raise RuntimeError("Transcription produced no speech segments")

    srt_lines = []
    for i, (start, end, text) in enumerate(triples, start=1):
        srt_lines.append(f"{i}\n{_format_srt_time(start)} --> {_format_srt_time(end)}\n{text}\n\n")

    srt_path = os.path.join(temp_dir, f"{filename}.srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        f.writelines(srt_lines)

    # Keep a copy of the raw transcript for the user
    if transcript_copy_dir:
        os.makedirs(transcript_copy_dir, exist_ok=True)
        shutil.copyfile(srt_path, os.path.join(transcript_copy_dir, f"{filename}_transcribed.srt"))

    app_logger.info(f"Transcribed {len(triples)} segments via {model_id} -> {srt_path}")
    return srt_path

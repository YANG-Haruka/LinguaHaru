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
import hashlib
import shutil
import subprocess
import tempfile
import threading
import time

from core.log_config import app_logger

# Serialize model LOADING. Two batch jobs (e.g. two videos) starting at once
# both tried to load the same model via device_map/accelerate concurrently,
# which corrupted the load ("Cannot copy out of meta tensor"). Loading is rare
# and quick relative to a translation, so one global lock is fine; inference
# still runs concurrently once the (shared) model is cached.
_LOAD_LOCK = threading.RLock()


def _tr(key, lang):
    """Localize a progress label via the shared LABEL_TRANSLATIONS (UI language)."""
    from core.languages_config import LABEL_TRANSLATIONS
    labels = LABEL_TRANSLATIONS.get(lang, LABEL_TRANSLATIONS.get("en", {}))
    return labels.get(key, key)

# --- STT model catalogue ----------------------------------------------------
# Each entry: id (stored in config), label (UI), engine, size/model name.
# Curated "friendly subset" of speech-to-text models. `disk` = approximate
# download size, `vram` = approximate GPU peak (CPU mode uses RAM, not VRAM).
# Real-time voice favors the smaller/faster ones (lower latency).
STT_MODELS = [
    {"id": "sensevoice-small",       "engine": "sensevoice", "size": "iic/SenseVoiceSmall",
     "label": "SenseVoice Small — general zh/en/ja/ko, fast & light",
     "disk": "~900MB", "vram": "~1–2GB"},
    {"id": "whisper-tiny",           "engine": "whisper",    "size": "tiny",
     "label": "Whisper Tiny — multilingual, fastest (low accuracy)", "disk": "~75MB", "vram": "~1GB"},
    {"id": "whisper-base",           "engine": "whisper",    "size": "base",
     "label": "Whisper Base — multilingual, fast", "disk": "~145MB", "vram": "~1GB"},
    {"id": "whisper-small",          "engine": "whisper",    "size": "small",
     "label": "Whisper Small — multilingual, balanced", "disk": "~490MB", "vram": "~2GB"},
    {"id": "whisper-large-v3-turbo", "engine": "whisper",    "size": "large-v3-turbo",
     "label": "Whisper Large-v3 Turbo — multilingual, accurate & fast", "disk": "~1.6GB", "vram": "~6GB"},
    {"id": "whisper-large-v2",       "engine": "whisper",    "size": "large-v2",
     "label": "Whisper Large-v2 — best for EXPRESSIVE ENGLISH (low hallucination)",
     "disk": "~3GB", "vram": "~5GB"},
    {"id": "anime-whisper",          "engine": "animewhisper", "size": "litagin/anime-whisper",
     "label": "Anime-Whisper — tuned for Japanese expressive / NSFW audio (JA only)",
     "disk": "~3GB", "vram": "~2GB"},
    {"id": "qwen3-asr-0.6b",         "engine": "qwen3asr",   "size": "Qwen/Qwen3-ASR-0.6B",
     "label": "Qwen3-ASR 0.6B — multilingual (30+ langs), accurate", "disk": "~2GB", "vram": "~3GB"},
    {"id": "qwen3-asr-1.7b",         "engine": "qwen3asr",   "size": "Qwen/Qwen3-ASR-1.7B",
     "label": "Qwen3-ASR 1.7B — multilingual, most accurate (best general pick)",
     "disk": "~4GB", "vram": "~6GB"},
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
    has_transformers = importlib.util.find_spec("transformers") is not None
    engine, size = model_def["engine"], model_def["size"]
    if engine == "animewhisper" and not has_transformers:
        # transformers missing -> degrade to a MULTILINGUAL whisper (anime-whisper
        # is Japanese; large-v2 is tuned for English here, so prefer large-v3-turbo),
        # else SenseVoice.
        if has_whisper:
            app_logger.warning("transformers not installed; anime-whisper -> faster-whisper 'large-v3-turbo'.")
            return "whisper", "large-v3-turbo"
        if has_funasr:
            return "sensevoice", "iic/SenseVoiceSmall"
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
        elif engine == "animewhisper":
            _animewhisper_models.pop(size, None)
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
    for repo in list(_animewhisper_models):
        if ("animewhisper", repo) not in inuse:
            del _animewhisper_models[repo]
            freed.append(f"anime-whisper:{repo}")
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
    # Round to integer milliseconds FIRST, then decompose — otherwise a value
    # like 1.9996s gives ms=round(999.6)=1000 -> illegal "00:00:01,1000".
    total_ms = int(round(seconds * 1000))
    h, rem = divmod(total_ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms = divmod(rem, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


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
            # Prefer the cache with NO network check (local_files_only) so a
            # slow/blocked HF link doesn't stall every launch; only hit the
            # network when the model isn't cached yet.
            try:
                _whisper_models[size] = WhisperModel(
                    size, device=dev, compute_type=ctype,
                    download_root=whisper_dir(), local_files_only=True)
            except Exception:  # noqa: BLE001 — not cached yet -> download
                _whisper_models[size] = WhisperModel(
                    size, device=dev, compute_type=ctype, download_root=whisper_dir())
    return _whisper_models[size]


def _transcribe_whisper(wav_path, size, src_lang, progress_callback, ui_lang="en",
                        check_stop=None, checkpoint_path=None):
    """Yield (start, end, text) tuples for each spoken segment.

    Resumable: whisper segments the audio itself (no external VAD), so a resume
    can't skip individual segments — instead it replays the checkpointed ones and
    restarts decoding from just past the last one by clipping the audio (whisper
    decodes from t=0 otherwise). Clipping at a segment boundary is a natural pause,
    so no speech is cut; timestamps are shifted back by the clip offset."""
    model = _get_whisper_model(size)
    # User's source language forces Whisper's decode language (zh/en/ja/…); "auto"
    # or empty -> None so Whisper auto-detects (passing the literal "auto" errors).
    language = src_lang.split("-")[0] if (src_lang and src_lang != "auto") else None

    # Resume: load already-decoded segments; restart decoding past the last one.
    done, offset = [], 0.0
    if checkpoint_path and os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    s, e, t = json.loads(line)
                    done.append((s, e, t))
            if done:
                offset = max(e for _, e, _ in done)
        except Exception:  # noqa: BLE001 — corrupt checkpoint -> start over
            done, offset = [], 0.0

    # Anti-hallucination on noisy / non-speech-heavy audio (the moaning/breathy
    # case): condition_on_previous_text=False is the single biggest fix for
    # runaway repetition/context drift; the thresholds drop degenerate/no-speech
    # segments; no_repeat_ngram_size breaks token loops. (whisper-large-v3 is
    # known to loop ~4x more than v2 here, so these matter.)
    _wkw = dict(language=language, vad_filter=True,
                condition_on_previous_text=False, no_repeat_ngram_size=4,
                no_speech_threshold=0.6, compression_ratio_threshold=2.4)
    if offset > 0:
        import wave
        import numpy as np
        with wave.open(wav_path, "rb") as wf:
            sr = wf.getframerate()
            raw = wf.readframes(wf.getnframes())
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        clipped = audio[int(offset * sr):]   # wav is 16k mono; whisper assumes 16k
        app_logger.info(f"faster-whisper resume: {len(done)} segments done, "
                        f"continuing from {offset:.0f}s")
        segments, info = model.transcribe(clipped, **_wkw)
        duration = offset + (getattr(info, "duration", None) or 0)
    else:
        segments, info = model.transcribe(wav_path, **_wkw)
        duration = getattr(info, "duration", None) or 0

    out = [(s, e, t) for s, e, t in done if t]
    ckpt_f = open(checkpoint_path, "a", encoding="utf-8") if checkpoint_path else None
    try:
        for seg in segments:
            # Pause/stop checkpoint between segments: pause blocks here in place
            # (the whisper iterator + this process stay alive, so resume continues
            # from this exact segment); stop raises out of the loop.
            if check_stop:
                check_stop()
            text = _clean_asr_text(seg.text)
            s, e = seg.start + offset, seg.end + offset   # shift past the clip
            if text:
                out.append((s, e, text))
            if ckpt_f:   # record every segment (even empty) so offset advances
                ckpt_f.write(json.dumps([s, e, text]) + "\n")
                ckpt_f.flush()
            if progress_callback and duration:
                # Full 0..1 of this phase; caller maps it into the extraction range.
                # Show elapsed/total seconds so a long file reads as progressing.
                progress_callback(min(e / duration, 1.0),
                                  desc=f"{_tr('Transcribing', ui_lang)} "
                                       f"{int(e)}/{int(duration)}s")
    finally:
        if ckpt_f:
            ckpt_f.close()
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
        # Fast path: if the file is already cached, resolve it with NO network
        # call. Otherwise hf_hub_download does a metadata HEAD against HF_ENDPOINT
        # on EVERY launch — fine on a fast link, but on a slow/blocked network
        # (e.g. huggingface.co from China) each of the 5 files can take seconds
        # and retry, turning a ~12s load into ~1 min. The model rarely changes,
        # so trusting the cache is the right default.
        try:
            last = hf_hub_download(_SENSEVOICE_HF_REPO, fname, cache_dir=cache_dir,
                                   local_files_only=True)
        except Exception:  # noqa: BLE001 — not cached yet; fall through to download
            last = None
        if not last:
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


# TEN-VAD offline segmentation — the same neural VAD the real-time path uses
# (noise-robust, rejects music/silence/non-speech better than the old energy VAD).
# Offline we see the whole file, so the live VAD's pre-roll/partial/progressive-
# silence machinery isn't needed — just merge per-frame speech flags into segments.
_TEN_HOP = 256          # 16 ms @ 16 kHz (TEN-VAD's frame size)
# Defaults tuned for EXPRESSIVE speech (JAV/anime): a lower threshold + shorter
# minimum catch quiet/breathy/short utterances (あっ, んっ, whispers, gasps) that
# 0.5/280ms would drop. Override per dataset via config (stt_vad_threshold /
# stt_vad_min_ms). VAD thresholds are meant to be tuned to the audio.
_TEN_THRESHOLD = 0.35
_TEN_HANG_MS = 300.0    # bridge silences shorter than this inside one segment
_TEN_MIN_MS = 160.0     # drop blips shorter than this (low, to keep short interjections)
_TEN_MAX_MS = 30000.0   # cap a segment (also keeps Qwen/SenseVoice batches even)
_TEN_PAD_MS = 100.0     # keep a little lead-in/out so boundary words aren't clipped


def _vad_params():
    """(threshold, min_ms) for TEN-VAD, with optional config overrides so the VAD
    can be tuned to the dataset without code changes."""
    thr, min_ms = _TEN_THRESHOLD, _TEN_MIN_MS
    try:
        from core.paths import SYSTEM_CONFIG
        with open(SYSTEM_CONFIG, encoding="utf-8") as f:
            cfg = json.load(f)
        thr = float(cfg.get("stt_vad_threshold", thr))
        min_ms = float(cfg.get("stt_vad_min_ms", min_ms))
    except Exception:  # noqa: BLE001
        pass
    return thr, min_ms


def _ten_vad_segments(audio_i16, sr=16000, check_stop=None):
    """Segment 16 kHz mono int16 audio into [[s_ms, e_ms], ...] speech regions with
    TEN-VAD. Returns None if ten_vad is unavailable / errors, so the caller falls
    back to fsmn-vad. Deterministic (fixed threshold) so resume keys stay stable."""
    import numpy as np
    threshold, min_ms = _vad_params()
    try:
        from ten_vad import TenVad
        vad = TenVad(hop_size=_TEN_HOP, threshold=threshold)
    except Exception:  # noqa: BLE001 — lib missing -> caller uses fsmn-vad
        return None
    frame_ms = _TEN_HOP / sr * 1000.0
    n = len(audio_i16) // _TEN_HOP
    segs = []
    on, start_f, sil = False, 0, 0

    def _flush(end_f):
        s_ms, e_ms = start_f * frame_ms, end_f * frame_ms
        if e_ms - s_ms >= min_ms:
            segs.append([max(0.0, s_ms - _TEN_PAD_MS),
                         min(n * frame_ms, e_ms + _TEN_PAD_MS)])

    for i in range(n):
        if check_stop and (i & 0x3FF) == 0:   # interruptible every ~16s of audio
            check_stop()
        fr = np.ascontiguousarray(audio_i16[i * _TEN_HOP:(i + 1) * _TEN_HOP])
        try:
            _p, flag = vad.process(fr)
        except Exception:  # noqa: BLE001 — bail to fsmn-vad
            return None
        if not on:
            if flag:
                on, start_f, sil = True, i, 0
        else:
            sil = 0 if flag else sil + 1
            dur_ms = (i - start_f + 1) * frame_ms
            if sil * frame_ms >= _TEN_HANG_MS or dur_ms >= _TEN_MAX_MS:
                _flush(i - sil + 1)
                on, sil = False, 0
    if on:
        _flush(n)
    return segs


def _window_segments(total_ms, win_ms=25000.0, overlap_ms=1000.0):
    """Overlapping fixed windows over the whole file, used ONLY when both VADs find
    no speech. A single whole-file segment would blow past the model's chunk limit
    and trigger repetition loops; ~25s windows with ~1s overlap keep each pass sane
    while the overlap stops a word being cut across the boundary."""
    if total_ms <= win_ms:
        return [[0.0, total_ms]]
    step = win_ms - overlap_ms
    out, start = [], 0.0
    while start < total_ms:
        out.append([start, min(total_ms, start + win_ms)])
        start += step
    return out


def _segment_speech(wav_path, audio_i16, sr, check_stop=None):
    """Speech segments [[s_ms, e_ms], ...] for the external-VAD engines. Prefers
    TEN-VAD (noise-robust neural VAD, shared with real-time); on a None OR EMPTY
    result falls back to fsmn-vad; if that is also empty, to overlapping windows
    (never one whole-file segment)."""
    total_ms = len(audio_i16) / sr * 1000.0
    segs = _ten_vad_segments(audio_i16, sr, check_stop)
    if segs:
        app_logger.info(f"TEN-VAD: {len(segs)} speech segments")
        return segs
    # TEN-VAD missing (None) or found nothing (empty) -> try fsmn-vad.
    try:
        vad_res = _get_vad().generate(input=wav_path)
        segs = (vad_res[0].get("value") if vad_res else None) or []
    except Exception:  # noqa: BLE001 — no funasr/VAD
        segs = []
    if segs:
        app_logger.info(f"fsmn-VAD: {len(segs)} speech segments")
        return segs
    app_logger.info("VAD found no speech; using overlapping windows")
    return _window_segments(total_ms)


# --- Qwen3-ASR engine (qwen-asr) -------------------------------------------
_QWEN_LANG_CODE = {"Chinese": "zh", "English": "en", "Japanese": "ja",
                   "Korean": "ko", "Cantonese": "zh"}
# UI language code -> the language NAME qwen-asr expects. Passing this STOPS
# Qwen3-ASR's auto-detect from mis-identifying expressive/noisy speech (it would
# read Japanese moaning as English and then loop/hallucinate). Unknown/auto ->
# None (let it auto-detect, the old behavior).
# UI code -> Qwen3-ASR language NAME. Qwen3-ASR supports 50+ languages; map the UI
# languages to their standard English names. A name Qwen rejects falls back to
# auto-detect at call time (see _transcribe_qwen), so an over-broad map is safe.
_QWEN_LANG_NAME = {"zh": "Chinese", "zh-Hant": "Chinese", "en": "English",
                   "ja": "Japanese", "ko": "Korean", "yue": "Cantonese",
                   "de": "German", "es": "Spanish", "fr": "French", "it": "Italian",
                   "pt": "Portuguese", "ru": "Russian", "th": "Thai", "vi": "Vietnamese",
                   "ar": "Arabic", "hi": "Hindi", "id": "Indonesian", "tr": "Turkish",
                   "nl": "Dutch", "pl": "Polish"}


def _qwen_language(src_lang):
    """Map a UI src_lang code to qwen-asr's language name, or None for auto."""
    if not src_lang or src_lang == "auto":
        return None
    return _QWEN_LANG_NAME.get(src_lang) or _QWEN_LANG_NAME.get(src_lang.split("-")[0])


def _qwen_accel_kwargs():
    """GPU acceleration kwargs for Qwen3-ASR load (per the official repo): bf16 +
    FlashAttention 2 if flash-attn is installed, else PyTorch SDPA — both far
    faster than the fp32/eager default. CPU keeps defaults. Returned separately
    so we can retry without them if the installed qwen-asr/transformers rejects
    a kwarg."""
    if _stt_device() != "cuda":
        return {}
    kw = {}
    try:
        import torch
        if torch.cuda.is_bf16_supported():
            kw["dtype"] = torch.bfloat16
    except Exception:  # noqa: BLE001
        pass
    import importlib.util
    kw["attn_implementation"] = (
        "flash_attention_2" if importlib.util.find_spec("flash_attn") else "sdpa")
    return kw


def _get_qwen(model_name):
    if model_name in _qwen_models:
        return _qwen_models[model_name]
    with _LOAD_LOCK:
        if model_name not in _qwen_models:   # double-check inside the lock
            from qwen_asr import Qwen3ASRModel
            dm = "cuda:0" if _stt_device() == "cuda" else "cpu"
            accel = _qwen_accel_kwargs()
            app_logger.info(f"Loading {model_name} on {dm} "
                            f"({accel.get('attn_implementation', 'default')}, "
                            f"{'bf16' if accel.get('dtype') is not None else 'default dtype'})...")

            def _load(extra):
                # Local-first: skip the network metadata check when cached.
                try:
                    return Qwen3ASRModel.from_pretrained(
                        model_name, device_map=dm, local_files_only=True, **extra)
                except TypeError:
                    raise   # bad kwarg -> let the caller retry with fewer
                except Exception:  # noqa: BLE001 — not cached yet -> download
                    return Qwen3ASRModel.from_pretrained(model_name, device_map=dm, **extra)

            _t0 = time.time()
            try:
                _qwen_models[model_name] = _load(accel)
            except Exception as e:  # noqa: BLE001 — accel kwarg unsupported (old pkg / no flash)
                app_logger.warning(f"Qwen3-ASR accel load failed ({e}); loading without it")
                _qwen_models[model_name] = _load({})
            app_logger.info(f"{model_name} loaded in {time.time() - _t0:.1f}s")
            _tune_qwen_generation(_qwen_models[model_name])
    return _qwen_models[model_name]


def _tune_qwen_generation(qmodel):
    """Cap runaway generation on noisy / non-speech audio. qwen-asr calls
    generate() with max_new_tokens=512, so on ambiguous audio the model fails to
    emit EOS and generates the full 512 tokens per chunk (~15s for one 20s
    segment), making a noise-heavy file several times slower. A <=30s speech
    segment never needs more than ~200 tokens, so 256 halves those runaways with
    a safe margin.

    Empirically benchmarked on the problem clips: 256 gives ~2x on runaway
    segments and never truncates real speech or slows well-behaved ones. A
    repetition_penalty / no_repeat_ngram_size was tried and REJECTED — it forces
    the model off genuinely-repeated speech ("I'm getting dizzy. I'm getting
    dizzy…") into a hallucination spiral (0.8s/75 chars -> 15s/3438 chars). Audio
    pre-processing (Demucs vocal isolation, spectral denoise) was also rejected:
    it erased real speech to empty output on these clips."""
    try:
        qmodel.max_new_tokens = 256   # was 512
        app_logger.info("Qwen3-ASR: max_new_tokens capped at 256")
    except Exception as e:  # noqa: BLE001 — never block loading on this
        app_logger.debug(f"Qwen3-ASR generation tuning skipped: {e}")


def _qwen_text(result):
    return (result.text if hasattr(result, "text") else str(result)).strip()


def _recognize_qwen(audio, src_lang, model_name, sample_rate=16000):
    """Recognize one utterance (16 kHz float32) with Qwen3-ASR (no temp file —
    qwen-asr accepts a (ndarray, sr) tuple)."""
    model = _get_qwen(model_name)
    _lang = _qwen_language(src_lang)
    try:
        results = model.transcribe((audio, sample_rate), language=_lang) if _lang \
            else model.transcribe((audio, sample_rate))
    except ValueError as e:
        if _lang and "language" in str(e).lower():
            results = model.transcribe((audio, sample_rate))   # auto-detect fallback
        else:
            raise
    if not results:
        return "", (src_lang or "auto")
    r = results[0]
    detected = _QWEN_LANG_CODE.get(getattr(r, "language", None), src_lang or "auto")
    return _clean_asr_text(_qwen_text(r)), detected


def _transcribe_qwen(wav_path, model_name, src_lang, progress_callback, ui_lang="en",
                     check_stop=None, checkpoint_path=None):
    """VAD-segment the audio (for SRT timing) then batch-recognize the segments
    with Qwen3-ASR. Falls back to a single whole-file pass if VAD is unavailable.

    Resumable: when a prior (stopped) run left a checkpoint, the already-recognized
    segments are loaded and skipped — only the remaining ones are transcribed.
    VAD is deterministic on the same audio, so segment boundaries line up across
    runs and each is keyed by its (start_ms, end_ms)."""
    import wave
    import numpy as np

    model = _get_qwen(model_name)
    _qlang = _qwen_language(src_lang)   # force the language so auto-detect can't
    if _qlang:                          # mis-read expressive speech and loop
        app_logger.info(f"Qwen3-ASR transcribing as {_qlang}")
    with wave.open(wav_path, "rb") as wf:
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    audio_i16 = np.frombuffer(raw, dtype=np.int16)
    audio = audio_i16.astype(np.float32) / 32768.0
    if progress_callback:   # VAD on a long file takes a while with no inner ticks
        progress_callback(0.04, desc=f"{_tr('Detecting speech', ui_lang)}...")
    segments = _segment_speech(wav_path, audio_i16, sr, check_stop)
    if not segments:   # only reachable on empty audio
        segments = _window_segments(len(audio) / sr * 1000.0)

    chunks, spans, keys = [], [], []
    for s_ms, e_ms in segments:
        c = audio[int(s_ms / 1000 * sr):int(e_ms / 1000 * sr)]
        if c.size:
            chunks.append((c, sr))
            spans.append((s_ms / 1000.0, e_ms / 1000.0))
            keys.append((int(round(s_ms)), int(round(e_ms))))

    # Resume: load segments a prior stopped run already recognized (keyed by their
    # ms boundaries). texts[k] is None until segment k is transcribed.
    done = {}
    if checkpoint_path and os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    s_ms, e_ms, t = json.loads(line)
                    done[(int(round(s_ms)), int(round(e_ms)))] = t
        except Exception:  # noqa: BLE001 — a corrupt checkpoint just re-transcribes
            done = {}
    texts = [done.get(k) for k in keys]
    total = len(chunks) or 1
    todo = [k for k in range(len(chunks)) if texts[k] is None]
    if done:
        app_logger.info(f"Qwen3-ASR resume: {total - len(todo)}/{total} segments "
                        f"already done, transcribing {len(todo)} more")

    # One model.transcribe() call is atomic (can't report sub-batch progress), so
    # the batch size sets how often the bar moves AND the Pause/Stop latency.
    # Group by AUDIO DURATION rather than a fixed chunk COUNT: VAD chunks vary from
    # ~1s to ~30s, so a fixed count makes batches take wildly different wall-times
    # (the bar races, then "sticks" on a batch full of long chunks). A fixed audio
    # budget makes every batch ~the same compute time -> smooth, evenly-paced
    # progress. The 4090 (bf16) chews ~60s of audio in a few seconds; CPU keeps the
    # budget small so Stop still lands quickly.
    BUDGET = 60.0 if _stt_device() == "cuda" else 12.0   # seconds of audio per batch
    ckpt_f = open(checkpoint_path, "a", encoding="utf-8") if checkpoint_path else None
    try:
        p, first = 0, True
        while p < len(todo):
            if check_stop:   # pause/stop between batches (see _transcribe_whisper)
                check_stop()
            # Take todo segments until adding the next exceeds the budget (>= 1).
            grp, acc = [], 0.0
            while p < len(todo):
                k = todo[p]
                d = spans[k][1] - spans[k][0]
                if grp and acc + d > BUDGET:
                    break
                grp.append(k)
                acc += d
                p += 1
            _bt = time.time()
            _batch = [chunks[k] for k in grp]
            if _qlang:
                try:
                    results = model.transcribe(_batch, language=_qlang)
                except ValueError as e:   # Qwen rejects an unsupported language name
                    if "language" in str(e).lower():
                        app_logger.warning(f"Qwen3-ASR rejected language {_qlang!r}; auto-detecting")
                        _qlang = None
                        results = model.transcribe(_batch)
                    else:
                        raise
            else:
                results = model.transcribe(_batch)
            if first:   # log throughput so an early Stop still shows the speed
                first = False
                app_logger.info(f"Qwen3-ASR first batch: {len(grp)} chunks "
                                f"({acc:.0f}s audio) in {time.time() - _bt:.1f}s")
            for k, r in zip(grp, results):
                txt = _clean_asr_text(_qwen_text(r))
                texts[k] = txt
                if ckpt_f:
                    ckpt_f.write(json.dumps([keys[k][0], keys[k][1], txt]) + "\n")
            if ckpt_f:
                ckpt_f.flush()
            if progress_callback:
                got = sum(1 for t in texts if t is not None)
                progress_callback(min(1.0, got / total),
                                  desc=f"{_tr('Transcribing', ui_lang)} {got}/{total}")
    finally:
        if ckpt_f:
            ckpt_f.close()

    return [(spans[k][0], spans[k][1], texts[k]) for k in range(len(chunks)) if texts[k]]


# --- Anime-Whisper engine (litagin/anime-whisper, transformers) -------------
# A kotoba-whisper-v2 fine-tune on ~5300h of expressive Japanese (galgame/anime,
# incl. NSFW moaning/breathing/non-verbal). Japanese-only; far better + faster
# than Qwen3 on this content (Qwen3 auto-mis-reads it as English and loops).
_animewhisper_models = {}   # repo id -> transformers ASR pipeline


def _get_animewhisper(model_name):
    if model_name in _animewhisper_models:
        return _animewhisper_models[model_name]
    with _LOAD_LOCK:
        if model_name not in _animewhisper_models:   # double-check inside the lock
            import torch
            from transformers import pipeline
            dev = "cuda" if _stt_device() == "cuda" else "cpu"
            dtype = torch.float16 if dev == "cuda" else torch.float32
            app_logger.info(f"Loading anime-whisper ({model_name}) on {dev}...")
            _animewhisper_models[model_name] = pipeline(
                "automatic-speech-recognition", model=model_name,
                device=dev, torch_dtype=dtype, chunk_length_s=30, batch_size=1)
    return _animewhisper_models[model_name]


# Per the model card: NO initial prompt (causes hallucination); the card's
# DEFAULT is no_repeat_ngram_size=0 / repetition_penalty=1.0 (only raise to 5-10
# if a clip shows runaway repetition). Forcing no_repeat globally distorts the
# genuinely-repeated speech this model is meant to capture (moaning/aizuchi), so
# we leave it off; max_new_tokens bounds runaway and the post cleaner trims it.
_ANIME_GEN_KW = {"language": "Japanese", "max_new_tokens": 256}
_ANIME_BATCH = 8   # VAD segments per pipeline batch (GPU throughput vs resume granularity)


def _animewhisper_text(result):
    return ((result or {}).get("text") or "").strip()


def _recognize_animewhisper(audio, src_lang, model_name, sample_rate=16000):
    """Recognize one utterance (16k float32) with anime-whisper (Japanese)."""
    pipe = _get_animewhisper(model_name)
    res = pipe(audio, generate_kwargs=_ANIME_GEN_KW)
    return _clean_asr_text(_animewhisper_text(res)), "ja"


def _transcribe_animewhisper(wav_path, model_name, src_lang, progress_callback, ui_lang="en",
                             check_stop=None, checkpoint_path=None):
    """VAD-segment then recognize each segment with anime-whisper. Japanese-only
    (src_lang ignored — the model is fixed-Japanese). Resumable like the others."""
    import wave
    import numpy as np

    pipe = _get_animewhisper(model_name)
    with wave.open(wav_path, "rb") as wf:
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    audio_i16 = np.frombuffer(raw, dtype=np.int16)
    audio = audio_i16.astype(np.float32) / 32768.0
    if progress_callback:
        progress_callback(0.04, desc=f"{_tr('Detecting speech', ui_lang)}...")
    segments = _segment_speech(wav_path, audio_i16, sr, check_stop)
    if not segments:   # only reachable on empty audio
        segments = _window_segments(len(audio) / sr * 1000.0)
    keys = [(int(round(s)), int(round(e))) for s, e in segments]

    done = {}
    if checkpoint_path and os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    s_ms, e_ms, t = json.loads(line)
                    done[(int(round(s_ms)), int(round(e_ms)))] = t
        except Exception:  # noqa: BLE001
            done = {}
    texts = [done.get(k) for k in keys]
    total = len(segments) or 1

    # Batch pending clips through the HF pipeline (whisper-large-v2 sized): GPU
    # utilization is far higher feeding a list than one short clip at a time. Group
    # so a Stop/checkpoint/progress still happens every _ANIME_BATCH segments
    # (resumability stays fine-grained instead of all-or-nothing per file).
    pending = [i for i, t in enumerate(texts) if t is None]
    ckpt_f = open(checkpoint_path, "a", encoding="utf-8") if checkpoint_path else None
    try:
        first = True
        for b in range(0, len(pending), _ANIME_BATCH):
            if check_stop:
                check_stop()
            group = pending[b:b + _ANIME_BATCH]
            clips, idx_with_audio = [], []
            for i in group:
                s_ms, e_ms = segments[i]
                clip = audio[int(s_ms / 1000 * sr):int(e_ms / 1000 * sr)]
                if clip.size == 0:
                    texts[i] = ""
                else:
                    clips.append(clip)
                    idx_with_audio.append(i)
            if clips:
                _bt = time.time()
                res = pipe(clips, generate_kwargs=_ANIME_GEN_KW, batch_size=len(clips))
                # A list input yields a list of results, in order.
                res = res if isinstance(res, list) else [res]
                for i, r in zip(idx_with_audio, res):
                    texts[i] = _clean_asr_text(_animewhisper_text(r))
                if first:
                    first = False
                    app_logger.info(f"anime-whisper first batch: {len(clips)} clips "
                                    f"in {time.time() - _bt:.1f}s")
            if ckpt_f:
                for i in group:
                    ckpt_f.write(json.dumps([keys[i][0], keys[i][1], texts[i]]) + "\n")
                ckpt_f.flush()
            if progress_callback:
                got = sum(1 for t in texts if t is not None)
                progress_callback(min(1.0, got / total),
                                  desc=f"{_tr('Transcribing', ui_lang)} {got}/{total}")
    finally:
        if ckpt_f:
            ckpt_f.close()

    return [(segments[i][0] / 1000.0, segments[i][1] / 1000.0, texts[i])
            for i in range(len(segments)) if texts[i]]


def _transcribe_sensevoice(wav_path, model_name, src_lang, progress_callback, ui_lang="en",
                           check_stop=None, checkpoint_path=None):
    """VAD-segment the audio, recognize each segment with SenseVoice, and return
    (start, end, text) tuples. Timing comes from the VAD so the SRT is aligned.

    Resumable like _transcribe_qwen: segments already recognized by a prior
    stopped run are loaded (by their ms boundaries) and skipped."""
    import wave
    import numpy as np
    from funasr.utils.postprocess_utils import rich_transcription_postprocess

    asr, _vad = _get_sensevoice(model_name)  # _vad: ensure fsmn-vad is loaded for _segment_speech
    if progress_callback:
        progress_callback(0.04, desc=f"{_tr('Detecting speech', ui_lang)}...")

    with wave.open(wav_path, "rb") as wf:
        sr = wf.getframerate()
        raw = wf.readframes(wf.getnframes())
    audio_i16 = np.frombuffer(raw, dtype=np.int16)
    audio = audio_i16.astype(np.float32) / 32768.0

    # TEN-VAD (noise-robust) first; fall back to fsmn-vad, then windows.
    segments = _segment_speech(wav_path, audio_i16, sr, check_stop)

    lang = _sensevoice_lang(src_lang)
    total = len(segments) or 1
    keys = [(int(round(seg[0])), int(round(seg[1]))) for seg in segments]

    # Resume: load segments a prior stopped run already recognized.
    done = {}
    if checkpoint_path and os.path.exists(checkpoint_path):
        try:
            with open(checkpoint_path, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    s_ms, e_ms, t = json.loads(line)
                    done[(int(round(s_ms)), int(round(e_ms)))] = t
        except Exception:  # noqa: BLE001 — a corrupt checkpoint just re-transcribes
            done = {}
    texts = [done.get(k) for k in keys]
    if done:
        remaining = sum(1 for t in texts if t is None)
        app_logger.info(f"SenseVoice resume: {total - remaining}/{total} segments "
                        f"already done, transcribing {remaining} more")

    ckpt_f = open(checkpoint_path, "a", encoding="utf-8") if checkpoint_path else None
    try:
        for i, seg in enumerate(segments):
            if texts[i] is None:   # not yet done -> recognize it
                if check_stop:   # pause/stop between segments (see _transcribe_whisper)
                    check_stop()
                s_ms, e_ms = seg[0], seg[1]
                chunk = audio[int(s_ms / 1000 * sr):int(e_ms / 1000 * sr)]
                if chunk.size == 0:
                    texts[i] = ""
                else:
                    res = asr.generate(input=chunk, fs=sr, language=lang, use_itn=True)
                    texts[i] = _clean_asr_text(rich_transcription_postprocess(res[0]["text"])) if res else ""
                if ckpt_f:
                    ckpt_f.write(json.dumps([keys[i][0], keys[i][1], texts[i]]) + "\n")
                    ckpt_f.flush()
            if progress_callback:
                # Emit the full 0..1 of this phase; the caller maps it into the
                # extraction sub-range (e.g. 0..50%) via EXTRACTION_PROGRESS_SHARE.
                progress_callback((i + 1) / total,
                                  desc=f"{_tr('Transcribing', ui_lang)} {i + 1}/{total}")
    finally:
        if ckpt_f:
            ckpt_f.close()

    return [(segments[i][0] / 1000.0, segments[i][1] / 1000.0, texts[i])
            for i in range(len(segments)) if texts[i]]


def recognizer_ready(getter=None):
    """True if the selected STT model is already loaded (no first-use load
    delay). Defaults to the real-time model; pass get_selected_quick_stt_model
    (etc.) to check another feature's model."""
    getter = getter or get_selected_live_stt_model
    try:
        engine, size = _resolve_stt_engine(get_stt_model(getter()))
        if engine == "qwen3asr":
            return size in _qwen_models
        if engine == "animewhisper":
            return size in _animewhisper_models
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
        if engine == "animewhisper":
            t0 = time.time()
            _get_animewhisper(size)
            app_logger.info(f"anime-whisper loaded in {time.time() - t0:.1f}s")
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
    elif engine == "animewhisper":
        text, detected = _recognize_animewhisper(audio, src_lang, size, sample_rate)
        app_logger.info(f"STT(anime-whisper) {dur:.1f}s audio -> {time.time() - t0:.2f}s")
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


# ASR engines inject non-speech glyphs we don't want in subtitles/captions:
# SenseVoice emotion/event emojis (<|HAPPY|>→😊, <|BGM|>→🎼, …), and music notes
# (♪ ♫ 🎵) that Whisper/Qwen emit for singing/BGM. Strip them (+ any <|tag|>).
_SENSEVOICE_EMOJIS = set("😊😔😡😐🤢😱🎼👏😀😄😭🤧😷🤔🥱🎤🎶❓"
                         "♪♫♬♩🎵🎧🎙🥁")


def _clean_asr_text(text):
    """Remove ASR non-speech markers (emotion/event emojis, music notes, <|tag|>)
    so subtitles/captions are plain text. Applied to ALL STT engines."""
    import re
    text = re.sub(r"<\|[^|]*\|>", "", text or "")
    text = "".join(ch for ch in text if ch not in _SENSEVOICE_EMOJIS)
    text = re.sub(r"\s{2,}", " ", text).strip()
    return _collapse_repeats(text)


def _collapse_repeats(text, max_run=8):
    """CONSERVATIVELY trim only EXTREME, near-certain hallucination loops; never
    touch genuine expressive repetition. Expressive/JAV speech really does repeat
    (だめ×5, あ×5, もっと×5, "I love you"×4), so the default is to LEAVE the raw
    transcription alone — the original garbage source (Japanese mis-read as English
    -> "Angkor, Angkor…") is now fixed upstream by forcing the language, so heavy
    cleaning is no longer needed and was corrupting real lines.

    Two layers, both with high thresholds:
    (1) a 1-12 char unit repeated **10+** times in a row collapses to 4 (catches a
        runaway ××20 loop; a real ×5 survives untouched);
    (2) a space-separated token repeated **more than max_run (8)** times collapses
        to max_run."""
    if not text:
        return text
    import re
    text = re.sub(r"(.{1,12}?)\1{9,}", lambda m: m.group(1) * 4, text)
    toks = text.split()
    if len(toks) <= max_run:
        return text
    out, run, prev = [], 0, None
    for t in toks:
        key = re.sub(r"[,，、.。!！?？…\-]+$", "", t).lower()
        if key and key == prev:
            run += 1
            if run > max_run:
                continue   # drop only the truly excessive tail
        else:
            run, prev = 1, key
        out.append(t)
    return " ".join(out)


# Back-compat alias (older call sites).
_strip_sensevoice_marks = _clean_asr_text


def _recognize_whisper(audio, src_lang, size="small"):
    """Recognize a pre-VAD'd utterance (16 kHz float32) with faster-whisper.
    The client already segmented speech, so vad_filter is off (it can drop short
    clips); beam_size=1 keeps it responsive for real time."""
    model = _get_whisper_model(size)
    language = src_lang.split("-")[0] if (src_lang and src_lang != "auto") else None
    segments, info = model.transcribe(
        audio, language=language, vad_filter=False, beam_size=1)
    text = _clean_asr_text(" ".join(s.text.strip() for s in segments))
    detected = getattr(info, "language", None) or language
    return text, detected


# --- public entry point -----------------------------------------------------
def _ckpt_path(temp_dir, filename, model_id, src_lang, wav_path):
    """Checkpoint path fingerprinted by what actually determines the transcript:
    the model, the forced language, the VAD profile, and the audio size. Resuming
    after the user switches model/language (or re-encodes the audio) then starts a
    FRESH checkpoint instead of cross-reading a mismatched one. Was keyed by engine
    alone, so e.g. whisper-turbo -> whisper-tiny silently reused the old segments."""
    thr, min_ms = _vad_params()
    try:
        sz = os.path.getsize(wav_path)
    except OSError:
        sz = 0
    sig = f"{model_id}|{src_lang}|{thr}|{min_ms}|{sz}"
    h = hashlib.sha1(sig.encode("utf-8")).hexdigest()[:10]
    return os.path.join(temp_dir, f"{filename}.{h}.asr_ckpt.jsonl")


def _run_transcription(engine, size, wav_path, src_lang, progress_callback,
                       session_lang, model_id, check_stop=None, checkpoint_path=None):
    """Dispatch to the selected STT engine; log WHY on failure (download/load
    error) to the per-file log instead of failing silently, then re-raise."""
    try:
        if engine == "sensevoice":
            return _transcribe_sensevoice(wav_path, size, src_lang, progress_callback, session_lang,
                                          check_stop, checkpoint_path)
        if engine == "qwen3asr":
            return _transcribe_qwen(wav_path, size, src_lang, progress_callback, session_lang,
                                    check_stop, checkpoint_path)
        if engine == "animewhisper":
            return _transcribe_animewhisper(wav_path, size, src_lang, progress_callback, session_lang,
                                            check_stop, checkpoint_path)
        return _transcribe_whisper(wav_path, size, src_lang, progress_callback, session_lang,
                                   check_stop, checkpoint_path)
    except Exception as e:  # noqa: BLE001
        app_logger.error(
            f"STT transcription failed (engine={engine}, model={model_id}): "
            f"{type(e).__name__}: {e}")
        raise


def transcribe_media_to_srt(media_path, temp_dir, src_lang=None, progress_callback=None,
                            transcript_copy_dir=None, stt_model=None, session_lang="en",
                            check_stop=None):
    """Transcribe a video/audio file and write an SRT next to the temp data.

    stt_model: an id from STT_MODELS; defaults to the UI-selected one.
    session_lang localizes the progress labels to the UI language.
    Returns the path of the generated SRT (named after the media file)."""
    filename = os.path.splitext(os.path.basename(media_path))[0]
    os.makedirs(temp_dir, exist_ok=True)

    model_id = stt_model or get_selected_stt_model()
    model_def = get_stt_model(model_id)
    engine, size = _resolve_stt_engine(model_def)

    from core.compute_lock import GPU_LOCK
    with tempfile.TemporaryDirectory(dir=temp_dir) as audio_dir:
        if progress_callback:
            progress_callback(0.01, desc=f"{_tr('Extracting audio', session_lang)}...")
        wav_path = extract_audio_to_wav(media_path, audio_dir)   # ffmpeg (CPU) — ungated

        # Serialize the GPU/CPU-heavy transcription across concurrent tasks so
        # they don't thrash one device; show a "waiting" hint if another is busy.
        # Poll the lock (instead of a plain blocking acquire) so a Stop/Pause
        # while QUEUED is honored within ~0.3s — otherwise a waiting file can't
        # see the stop flag until it finally gets the GPU.
        got_lock = GPU_LOCK.acquire(blocking=False)
        if not got_lock:
            if progress_callback:
                progress_callback(0.02, desc=f"{_tr('Waiting for compute', session_lang)}...")
            while not got_lock:
                if check_stop:
                    check_stop()   # raises on stop / blocks while paused (still queued)
                got_lock = GPU_LOCK.acquire(timeout=0.3)
        try:
            if check_stop:
                check_stop()   # bail right after acquiring, before the heavy model load
            if progress_callback:
                progress_callback(0.03, desc=f"{_tr('Loading speech model', session_lang)}...")
            # Checkpoint survives a Stop (it lives in temp_dir, not the audio temp
            # dir) so a Continue resumes transcription instead of redoing it.
            # Fingerprinted by model/language/VAD/audio so switching any of them
            # starts fresh instead of cross-reading a mismatched transcript.
            ckpt_path = _ckpt_path(temp_dir, filename, model_id, src_lang, wav_path)
            triples = _run_transcription(engine, size, wav_path, src_lang,
                                         progress_callback, session_lang, model_id, check_stop,
                                         checkpoint_path=ckpt_path)
        finally:
            GPU_LOCK.release()

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

    # Transcription finished -> the checkpoint is spent; drop it so a fresh
    # re-translation of this file doesn't reuse a stale partial transcript.
    try:
        os.remove(ckpt_path)
    except OSError:
        pass

    app_logger.info(f"Transcribed {len(triples)} segments via {model_id} -> {srt_path}")
    return srt_path

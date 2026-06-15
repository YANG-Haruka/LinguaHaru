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

from core.log_config import app_logger

# --- STT model catalogue ----------------------------------------------------
# Each entry: id (stored in config), label (UI), engine, size/model name.
STT_MODELS = [
    {"id": "whisper-small",          "engine": "whisper",    "size": "small",
     "label": "Whisper Small (fast)"},
    {"id": "whisper-medium",         "engine": "whisper",    "size": "medium",
     "label": "Whisper Medium"},
    {"id": "whisper-large-v3",       "engine": "whisper",    "size": "large-v3",
     "label": "Whisper Large-v3 (best)"},
    {"id": "whisper-large-v3-turbo", "engine": "whisper",    "size": "large-v3-turbo",
     "label": "Whisper Large-v3 Turbo"},
    {"id": "sensevoice-small",       "engine": "sensevoice", "size": "iic/SenseVoiceSmall",
     "label": "SenseVoice Small (zh/en/ja/ko/yue)"},
]

DEFAULT_STT_MODEL = "whisper-small"

# Single source of truth mapping a UI language code (core.languages_config)
# to the language SenseVoice's recognizer expects. SenseVoice recognizes
# Mandarin and Cantonese; Traditional-Chinese audio is Mandarin, so zh-Hant
# maps to "zh". Cantonese ("yue") is recognized via auto-detect and normalized
# back to "zh" on output (see _recognize_sensevoice) since it has no UI code.
_SENSEVOICE_LANG_MAP = {"zh": "zh", "zh-Hant": "zh", "en": "en", "ja": "ja", "ko": "ko"}

# Language codes that SenseVoice can transcribe; everything else is disabled in
# the UI when SenseVoice is selected. Derived from the map above (no drift).
SENSEVOICE_SUPPORTED_CODES = set(_SENSEVOICE_LANG_MAP)

_SYSTEM_CONFIG = os.path.join("config", "system_config.json")

_whisper_models = {}   # size -> WhisperModel
_sensevoice = None     # (asr_model, vad_model)


def stt_model_ids():
    return [m["id"] for m in STT_MODELS]


def get_stt_model(model_id):
    for m in STT_MODELS:
        if m["id"] == model_id:
            return m
    return STT_MODELS[0]


def get_selected_stt_model():
    """The STT model id chosen in the UI (config), env override, or default."""
    try:
        with open(_SYSTEM_CONFIG, encoding="utf-8") as f:
            cfg_id = json.load(f).get("stt_model")
        if cfg_id and any(m["id"] == cfg_id for m in STT_MODELS):
            return cfg_id
    except Exception:
        pass
    env = os.environ.get("LINGUAHARU_WHISPER_MODEL")
    if env:
        # Back-compat: env held a bare whisper size like "small"
        return env if env in stt_model_ids() else f"whisper-{env}"
    return DEFAULT_STT_MODEL


def _resolve_stt_engine(model_def):
    """Resolve (engine, size) honoring which optional deps are actually
    installed, falling back across engines so a missing dependency degrades
    gracefully instead of hard-failing transcription.

    e.g. config selects SenseVoice but only faster-whisper is installed -> use
    whisper 'small' rather than crashing with ModuleNotFoundError: funasr."""
    import importlib.util
    has_funasr = importlib.util.find_spec("funasr") is not None
    has_whisper = importlib.util.find_spec("faster_whisper") is not None
    engine, size = model_def["engine"], model_def["size"]
    if engine == "sensevoice" and not has_funasr and has_whisper:
        app_logger.warning(
            "SenseVoice (funasr) not installed; falling back to faster-whisper 'small'.")
        return "whisper", "small"
    if engine == "whisper" and not has_whisper and has_funasr:
        app_logger.warning(
            "faster-whisper not installed; falling back to SenseVoice.")
        return "sensevoice", "iic/SenseVoiceSmall"
    return engine, size


# --- audio extraction -------------------------------------------------------
def _format_srt_time(seconds):
    if seconds is None or seconds < 0:
        seconds = 0.0
    ms = int(round((seconds - int(seconds)) * 1000))
    s = int(seconds)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d},{ms:03d}"


def extract_audio_to_wav(media_path, output_dir):
    """Extract/normalize the audio track to 16 kHz mono WAV via ffmpeg."""
    if shutil.which("ffmpeg") is None:
        raise RuntimeError(
            "ffmpeg not found on PATH - it is required for video/audio translation. "
            "Install it from https://ffmpeg.org/ or via your package manager."
        )
    wav_path = os.path.join(output_dir, "audio_16k.wav")
    cmd = ["ffmpeg", "-y", "-i", media_path, "-vn",
           "-ac", "1", "-ar", "16000", "-f", "wav", wav_path]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg audio extraction failed: {proc.stderr[-500:]}")
    return wav_path


# --- whisper engine ---------------------------------------------------------
def _get_whisper_model(size):
    if size not in _whisper_models:
        from faster_whisper import WhisperModel
        app_logger.info(f"Loading faster-whisper model '{size}' (downloads on first use)...")
        _whisper_models[size] = WhisperModel(size, device="auto")
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
            progress_callback(0.03 + 0.05 * min(seg.end / duration, 1.0),
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
    or None if the mirror is unreachable (caller falls back to modelscope)."""
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        return None
    root = None
    for fname in _SENSEVOICE_FILES:
        last = None
        for _ in range(4):
            try:
                last = hf_hub_download(_SENSEVOICE_HF_REPO, fname)
                break
            except Exception:  # noqa: BLE001 - transient mirror hiccup, retry
                last = None
        if not last:
            return None
        root = os.path.dirname(last)
    return root


def _get_sensevoice(model_name):
    global _sensevoice
    if _sensevoice is None:
        from funasr import AutoModel
        app_logger.info("Loading SenseVoice + fsmn-vad (downloads on first use)...")
        local = _sensevoice_local_dir()
        if local:
            app_logger.info(f"SenseVoice from HF mirror: {local}")
            asr = AutoModel(model=local, disable_update=True)
        else:  # mirror unreachable -> modelscope (may be slow)
            app_logger.warning("HF mirror unavailable; loading SenseVoice via modelscope.")
            asr = AutoModel(model=model_name, disable_update=True)
        vad = AutoModel(model="fsmn-vad", disable_update=True,
                        vad_kwargs={"max_single_segment_time": 30000})
        _sensevoice = (asr, vad)
    return _sensevoice


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
            progress_callback(0.03 + 0.05 * (i + 1) / total, desc="Transcribing (SenseVoice)...")
    return out


def recognizer_ready():
    """True if the local STT model is already loaded (no first-use load delay)."""
    import importlib.util
    if importlib.util.find_spec("funasr") is not None:
        return _sensevoice is not None
    return bool(_whisper_models)


def preload_recognizer(model_name="iic/SenseVoiceSmall"):
    """Load the local STT model now (downloads on first use) so the first
    utterance isn't blocked on a multi-second model load. Also runs a tiny
    warm-up inference: the very first generate() lazily builds graphs/buffers and
    is otherwise several times slower than steady state. Returns True on ready."""
    import importlib.util
    import time
    try:
        if importlib.util.find_spec("funasr") is not None:
            t0 = time.time()
            _get_sensevoice(model_name)
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
        if importlib.util.find_spec("faster_whisper") is not None:
            model_def = get_stt_model(get_selected_stt_model())
            size = model_def["size"] if model_def["engine"] == "whisper" else "small"
            _get_whisper_model(size)
            return True
    except Exception as e:  # noqa: BLE001
        app_logger.error(f"Preload recognizer failed: {e}")
    return False


def recognize_utterance(pcm16_bytes, src_lang=None, sample_rate=16000,
                        model_name="iic/SenseVoiceSmall"):
    """Recognize one short utterance (raw mono PCM16) with the available STT
    engine.

    Prefers SenseVoice (funasr) when installed; otherwise falls back to
    faster-whisper, so real-time local voice works with whichever engine the
    Video/Audio plugin provided. Returns (text, detected_lang) — text is '' if
    no speech.

    Used by real-time *local* voice translation: the client does VAD and sends a
    complete utterance, so no server-side segmentation is needed here."""
    import importlib.util
    import time
    import numpy as np

    audio = np.frombuffer(pcm16_bytes, dtype=np.int16).astype(np.float32) / 32768.0
    if audio.size == 0:
        return "", None
    dur = audio.size / float(sample_rate or 16000)
    t0 = time.time()
    if importlib.util.find_spec("funasr") is not None:
        text, detected = _recognize_sensevoice(audio, src_lang, sample_rate, model_name)
        app_logger.info(f"STT(SenseVoice) {dur:.1f}s audio -> {time.time() - t0:.2f}s")
        return text, detected
    if importlib.util.find_spec("faster_whisper") is not None:
        text, detected = _recognize_whisper(audio, src_lang)
        app_logger.info(f"STT(whisper) {dur:.1f}s audio -> {time.time() - t0:.2f}s")
        return text, detected
    raise RuntimeError(
        "No speech-to-text engine installed. Install the Video/Audio plugin "
        "(faster-whisper or funasr).")


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
    return rich_transcription_postprocess(raw).strip(), detected


def _recognize_whisper(audio, src_lang):
    """Recognize a pre-VAD'd utterance (16 kHz float32) with faster-whisper.
    The client already segmented speech, so vad_filter is off (it can drop short
    clips); beam_size=1 keeps it responsive for real time."""
    model_def = get_stt_model(get_selected_stt_model())
    size = model_def["size"] if model_def["engine"] == "whisper" else "small"
    model = _get_whisper_model(size)
    language = (src_lang or "").split("-")[0] or None
    segments, info = model.transcribe(
        audio, language=language, vad_filter=False, beam_size=1)
    text = " ".join(s.text.strip() for s in segments).strip()
    detected = getattr(info, "language", None) or language
    return text, detected


# --- public entry point -----------------------------------------------------
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

        if engine == "sensevoice":
            triples = _transcribe_sensevoice(wav_path, size, src_lang, progress_callback)
        else:
            triples = _transcribe_whisper(wav_path, size, src_lang, progress_callback)

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

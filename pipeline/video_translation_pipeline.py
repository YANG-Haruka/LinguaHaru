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

from config.log_config import app_logger

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

# Language codes (config.languages_config) that SenseVoice can transcribe.
# Everything else must be disabled in the UI when SenseVoice is selected.
SENSEVOICE_SUPPORTED_CODES = {"zh", "zh-Hant", "en", "ja", "ko"}

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
    code = (src_lang or "").split("-")[0]
    return code if code in {"zh", "en", "ja", "ko", "yue"} else "auto"


def _get_sensevoice(model_name):
    global _sensevoice
    if _sensevoice is None:
        from funasr import AutoModel
        app_logger.info(f"Loading SenseVoice '{model_name}' + fsmn-vad (downloads on first use)...")
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

    with tempfile.TemporaryDirectory(dir=temp_dir) as audio_dir:
        if progress_callback:
            progress_callback(0.01, desc="Extracting audio...")
        wav_path = extract_audio_to_wav(media_path, audio_dir)

        if progress_callback:
            progress_callback(0.03, desc=f"Transcribing ({model_id})...")

        if model_def["engine"] == "sensevoice":
            triples = _transcribe_sensevoice(wav_path, model_def["size"], src_lang, progress_callback)
        else:
            triples = _transcribe_whisper(wav_path, model_def["size"], src_lang, progress_callback)

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

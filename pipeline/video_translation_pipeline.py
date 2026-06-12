# pipeline/video_translation_pipeline.py
# Video/audio subtitle translation: ffmpeg extracts the audio track,
# faster-whisper transcribes it into timed segments, the segments become a
# standard SRT which is then translated by the existing SRT pipeline.
#
# Optional module - requires: faster-whisper (pip) and ffmpeg (on PATH)
import os
import shutil
import subprocess
import tempfile

from config.log_config import app_logger

# Model size: tiny/base/small/medium/large-v3/large-v3-turbo
WHISPER_MODEL_SIZE = os.environ.get("LINGUAHARU_WHISPER_MODEL", "small")

_whisper_model = None


def _get_whisper_model():
    global _whisper_model
    if _whisper_model is None:
        from faster_whisper import WhisperModel
        app_logger.info(f"Loading faster-whisper model '{WHISPER_MODEL_SIZE}' (downloads on first use)...")
        _whisper_model = WhisperModel(WHISPER_MODEL_SIZE, device="auto")
    return _whisper_model


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


def transcribe_media_to_srt(media_path, temp_dir, src_lang=None, progress_callback=None,
                            transcript_copy_dir=None):
    """Transcribe a video/audio file and write an SRT next to the temp data.

    Returns the path of the generated SRT (named after the media file so the
    SRT pipeline's temp folder matches the translator's expectations)."""
    filename = os.path.splitext(os.path.basename(media_path))[0]
    os.makedirs(temp_dir, exist_ok=True)

    with tempfile.TemporaryDirectory(dir=temp_dir) as audio_dir:
        if progress_callback:
            progress_callback(0.01, desc="Extracting audio...")
        wav_path = extract_audio_to_wav(media_path, audio_dir)

        if progress_callback:
            progress_callback(0.03, desc=f"Transcribing (whisper-{WHISPER_MODEL_SIZE})...")

        model = _get_whisper_model()
        # Whisper uses bare ISO codes (zh, en, ja, ...); None = auto-detect
        language = src_lang.split("-")[0] if src_lang else None
        segments, info = model.transcribe(wav_path, language=language, vad_filter=True)

        duration = getattr(info, "duration", None) or 0
        srt_lines = []
        for i, seg in enumerate(segments, start=1):
            text = seg.text.strip()
            if not text:
                continue
            srt_lines.append(f"{i}\n{_format_srt_time(seg.start)} --> {_format_srt_time(seg.end)}\n{text}\n\n")
            if progress_callback and duration:
                # Transcription occupies the first ~8% of the overall progress
                progress_callback(0.03 + 0.05 * min(seg.end / duration, 1.0),
                                  desc=f"Transcribing (whisper-{WHISPER_MODEL_SIZE})...")

    if not srt_lines:
        raise RuntimeError("Transcription produced no speech segments")

    srt_path = os.path.join(temp_dir, f"{filename}.srt")
    with open(srt_path, "w", encoding="utf-8") as f:
        f.writelines(srt_lines)

    # Keep a copy of the raw transcript for the user
    if transcript_copy_dir:
        os.makedirs(transcript_copy_dir, exist_ok=True)
        shutil.copyfile(srt_path, os.path.join(transcript_copy_dir, f"{filename}_transcribed.srt"))

    app_logger.info(f"Transcribed {len(srt_lines)} segments "
                    f"(detected language: {getattr(info, 'language', 'unknown')}) -> {srt_path}")
    return srt_path

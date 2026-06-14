# Availability detection for optional translation modules.
# Core formats are always available; PDF, image and video translation light
# up when their extra dependencies are installed (requirements-pdf.txt,
# requirements-ocr.txt, requirements-video.txt).
import importlib.util
import shutil

IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".bmp", ".webp"]
MEDIA_EXTENSIONS = [".mp4", ".mkv", ".mov", ".avi", ".webm", ".mp3", ".wav", ".m4a", ".flac"]


def pdf_translation_available():
    return importlib.util.find_spec("babeldoc") is not None


def image_translation_available():
    # Either the lightweight engine (rapidocr) or PaddleOCR works
    has_engine = (importlib.util.find_spec("rapidocr") is not None
                  or importlib.util.find_spec("paddleocr") is not None)
    return has_engine and all(importlib.util.find_spec(mod) is not None
                              for mod in ("cv2", "PIL"))


def video_translation_available():
    # Either STT engine works: faster-whisper or SenseVoice (funasr).
    has_stt = (importlib.util.find_spec("faster_whisper") is not None
               or importlib.util.find_spec("funasr") is not None)
    return has_stt and shutil.which("ffmpeg") is not None


def available_optional_extensions():
    extensions = []
    if pdf_translation_available():
        extensions.append(".pdf")
    if image_translation_available():
        extensions.extend(IMAGE_EXTENSIONS)
    if video_translation_available():
        extensions.extend(MEDIA_EXTENSIONS)
    return extensions


def module_status():
    """Status of each optional module for display in the UI."""
    ocr_engine = ("PP-OCRv6 (PaddleOCR)"
                  if importlib.util.find_spec("paddleocr") is not None
                  else "PP-OCRv5 (RapidOCR)")
    return [
        {"name": "PDF", "available": pdf_translation_available(),
         "detail": "BabelDOC", "install": "pip install -r requirements-pdf.txt"},
        {"name": "Image OCR", "available": image_translation_available(),
         "detail": ocr_engine, "install": "pip install -r requirements-ocr.txt"},
        {"name": "Video/Audio", "available": video_translation_available(),
         "detail": "faster-whisper / SenseVoice + ffmpeg",
         "install": "pip install -r requirements-video.txt (+ ffmpeg)"},
    ]

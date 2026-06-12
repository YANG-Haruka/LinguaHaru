# Availability detection for optional translation modules.
# Core formats are always available; image and video translation only light
# up when their extra dependencies are installed (see requirements-ocr.txt
# and requirements-video.txt).
import importlib.util
import shutil

IMAGE_EXTENSIONS = [".png", ".jpg", ".jpeg", ".bmp", ".webp"]
MEDIA_EXTENSIONS = [".mp4", ".mkv", ".mov", ".avi", ".webm", ".mp3", ".wav", ".m4a", ".flac"]


def image_translation_available():
    return all(importlib.util.find_spec(mod) is not None
               for mod in ("rapidocr", "cv2", "PIL"))


def video_translation_available():
    return (importlib.util.find_spec("faster_whisper") is not None
            and shutil.which("ffmpeg") is not None)


def available_optional_extensions():
    extensions = []
    if image_translation_available():
        extensions.extend(IMAGE_EXTENSIONS)
    if video_translation_available():
        extensions.extend(MEDIA_EXTENSIONS)
    return extensions

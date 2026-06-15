"""Text-to-speech for "read the translation aloud" (Google-Translate-style 🔊).

Uses edge-tts (Microsoft Edge online TTS): free, high quality, many languages,
~zero install footprint — but needs the network. One sensible default voice per
language. Part of the "翻译语音输入" plugin (with the STT voice input).
"""
from core.log_config import app_logger

# language code -> a good default edge-tts neural voice.
_VOICES = {
    "en": "en-US-AriaNeural",
    "zh": "zh-CN-XiaoxiaoNeural",
    "zh-Hant": "zh-HK-HiuMaanNeural",
    "ja": "ja-JP-NanamiNeural",
    "ko": "ko-KR-SunHiNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "es": "es-ES-ElviraNeural",
    "it": "it-IT-ElsaNeural",
    "pt": "pt-BR-FranciscaNeural",
    "ru": "ru-RU-SvetlanaNeural",
    "th": "th-TH-PremwadeeNeural",
    "vi": "vi-VN-HoaiMyNeural",
}
_DEFAULT_VOICE = "en-US-AriaNeural"


def tts_available():
    import importlib.util
    return importlib.util.find_spec("edge_tts") is not None


def voice_for(lang):
    """Resolve a translation language (display name like '中文' or code 'zh')
    to a default voice."""
    if not lang:
        return _DEFAULT_VOICE
    code = lang
    try:
        from core.languages_config import LANGUAGE_MAP
        if lang in LANGUAGE_MAP:
            code = LANGUAGE_MAP[lang]
    except Exception:  # noqa: BLE001
        pass
    if code in _VOICES:                 # exact (keeps zh-Hant distinct from zh)
        return _VOICES[code]
    return _VOICES.get(code.split("-")[0].lower(), _DEFAULT_VOICE)


def synthesize(text, lang):
    """Return MP3 audio bytes for `text` in `lang`'s default voice (b'' on
    failure / empty text). Blocking — callers run it off the UI/event thread."""
    text = (text or "").strip()
    if not text:
        return b""
    try:
        import asyncio
        import edge_tts

        voice = voice_for(lang)

        async def _run():
            buf = bytearray()
            async for chunk in edge_tts.Communicate(text, voice).stream():
                if chunk.get("type") == "audio" and chunk.get("data"):
                    buf += chunk["data"]
            return bytes(buf)

        return asyncio.run(_run())
    except Exception as e:  # noqa: BLE001 — network/voice errors -> no audio
        app_logger.warning(f"TTS synthesize failed: {e}")
        return b""

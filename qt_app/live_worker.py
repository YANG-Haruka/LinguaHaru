"""Background bridge to the Gemini Live API for real-time voice translation.

Runs an asyncio websockets client on its own QThread and relays results to the
GUI thread via signals. The desktop app talks to Gemini directly (the key is
local), unlike the Web app which proxies through FastAPI.
"""
import asyncio
import base64
import json
import threading

from PySide6.QtCore import QThread, Signal

_URL = ("wss://generativelanguage.googleapis.com/ws/"
        "google.ai.generativelanguage.v1beta.GenerativeService.BidiGenerateContent")
_MODEL = "models/gemini-3.5-live-translate-preview"

# funasr/SenseVoice is not thread-safe — serialize recognition across utterances.
_STT_LOCK = threading.Lock()


class PreloadWorker(QThread):
    """Load the local STT model up front so the first utterance isn't blocked on
    a multi-second model load. Emits done(ready)."""
    done = Signal(bool)

    def run(self):
        try:
            from core.pipelines.video_translation_pipeline import preload_recognizer
            self.done.emit(bool(preload_recognizer()))
        except Exception:  # noqa: BLE001
            self.done.emit(False)


class LiveRecognizeWorker(QThread):
    """Recognize a (possibly partial) PCM16 buffer locally — NO translation.

    Used for streaming captions: the page sends the growing audio of the current
    utterance every ~360ms; we re-run STT and emit the full text-so-far. The page
    applies stable-prefix commit on the text. ``is_final`` marks the end-of-
    utterance pass (flush the remainder)."""
    done = Signal(str, str, bool)   # (text, detected_lang, is_final)

    def __init__(self, pcm_bytes, sample_rate, is_final, parent=None):
        super().__init__(parent)
        self._pcm = pcm_bytes
        self._sr = sample_rate
        self._final = is_final

    def run(self):
        try:
            from core.pipelines.video_translation_pipeline import recognize_utterance
            with _STT_LOCK:
                text, detected = recognize_utterance(self._pcm, sample_rate=self._sr)
            self.done.emit(text or "", detected or "auto", self._final)
        except Exception:  # noqa: BLE001 — transient; the next partial retries
            self.done.emit("", "auto", self._final)


class LiveTranslateWorker(QThread):
    """Translate one finalized sentence (LLM). Emits done(timestamp, translated)
    so the matching source/translation lines line up by timestamp."""
    done = Signal(str, str)

    def __init__(self, ts, source, src_code, dst_code, model, use_online,
                 api_key, parent=None):
        super().__init__(parent)
        self._ts = ts
        self._source = source
        self._src = src_code
        self._dst = dst_code
        self._model = model
        self._online = use_online
        self._key = api_key

    def run(self):
        try:
            from core.llm.llm_wrapper import translate_text_simple
            translated, ok, _usage = translate_text_simple(
                self._source, self._src or "auto", self._dst, self._model,
                self._online, self._key)
            self.done.emit(self._ts, translated if ok else "")
        except Exception:  # noqa: BLE001
            self.done.emit(self._ts, "")


class LiveWorker(QThread):
    inputText = Signal(str)    # recognized source text (incremental)
    outputText = Signal(str)   # translated text (incremental)
    audio = Signal(bytes)      # translated 24kHz PCM16 chunk
    status = Signal(str)       # "listening" / "closed" / "error: ..."

    def __init__(self, api_key, target_code, parent=None):
        super().__init__(parent)
        self._key = api_key
        self._target = target_code
        self._loop = None
        self._ws = None
        self._sendq = None

    def run(self):
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception as e:  # noqa: BLE001
            self.status.emit(f"error: {str(e)[:200]}")
        finally:
            try:
                self._loop.close()
            except Exception:
                pass

    async def _main(self):
        import websockets
        self._sendq = asyncio.Queue()
        async with websockets.connect(f"{_URL}?key={self._key}", max_size=None) as ws:
            self._ws = ws
            await ws.send(json.dumps({"setup": {
                "model": _MODEL,
                "generationConfig": {
                    "responseModalities": ["AUDIO"],
                    "translationConfig": {"targetLanguageCode": self._target,
                                          "echoTargetLanguage": True},
                },
                "inputAudioTranscription": {},
                "outputAudioTranscription": {},
            }}))
            self.status.emit("listening")
            sender = asyncio.create_task(self._sender(ws))
            try:
                async for raw in ws:
                    d = json.loads(raw)
                    sc = d.get("serverContent") or {}
                    if sc.get("inputTranscription", {}).get("text"):
                        self.inputText.emit(sc["inputTranscription"]["text"])
                    if sc.get("outputTranscription", {}).get("text"):
                        self.outputText.emit(sc["outputTranscription"]["text"])
                    for p in (sc.get("modelTurn", {}) or {}).get("parts", []):
                        data = p.get("inlineData", {}).get("data")
                        if data:
                            self.audio.emit(base64.b64decode(data))
            finally:
                sender.cancel()
        self.status.emit("closed")

    async def _sender(self, ws):
        while True:
            chunk = await self._sendq.get()
            if chunk is None:
                return
            await ws.send(json.dumps({"realtimeInput": {
                "audio": {"data": base64.b64encode(chunk).decode(),
                          "mimeType": "audio/pcm;rate=16000"}}}))

    def send_audio(self, data: bytes):
        """Thread-safe: queue a 16kHz PCM16 chunk to forward to Gemini."""
        if self._loop and self._sendq is not None:
            self._loop.call_soon_threadsafe(self._sendq.put_nowait, bytes(data))

    def stop(self):
        """Thread-safe: stop streaming and close the socket."""
        if not self._loop:
            return
        self._loop.call_soon_threadsafe(self._sendq.put_nowait, None)

        async def _close():
            if self._ws is not None:
                await self._ws.close()
        try:
            asyncio.run_coroutine_threadsafe(_close(), self._loop)
        except Exception:
            pass

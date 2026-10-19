"""Optional LaMa inpainting via ONNX Runtime (no torch/numpy-version conflict).

Used by the image translator to erase original text far more cleanly than
OpenCV's Telea on complex/textured backgrounds (manga panels, photos). Fully
OPTIONAL and self-degrading: if the model file isn't downloaded (or anything
fails) inpaint() returns None and the caller falls back to cv2.inpaint.

Model: Carve/LaMa-ONNX `lama_fp32.onnx` (~208MB), kept under the unified
data/models dir. onnxruntime is already a dependency (RapidOCR uses it), so no
new heavy package is required — the simple-lama-inpainting PyPI package was
rejected because it pins numpy<2 and pulls a CPU-only torch, which breaks the
project's numpy-2 / CUDA-torch stack.
"""
import os
import threading

from core.log_config import app_logger

_LAMA_REPO = "Carve/LaMa-ONNX"
_LAMA_FILE = "lama_fp32.onnx"

_session = None
_session_tried = False
_LOCK = threading.Lock()


def _model_dir():
    from core import model_store
    d = os.path.join(model_store.current_dir(), "lama")
    os.makedirs(d, exist_ok=True)
    return d


def _model_path():
    return os.path.join(_model_dir(), _LAMA_FILE)


def lama_available():
    """True if the LaMa ONNX model is downloaded (so the UI can show it ready)."""
    p = _model_path()
    return os.path.exists(p) and os.path.getsize(p) > 1_000_000


# The SAME file (identical sha256) is mirrored on ModelScope, which mainland
# China reaches reliably. hf-mirror.com alone wasn't enough: it can redirect to
# huggingface.co, and a blocked HF host cost ~7 min of retries before failing.
_LAMA_SHA256 = "1faef5301d78db7dda502fe59966957ec4b79dd64e16f03ed96913c7a4eb68d6"
_MODELSCOPE_URL = ("https://www.modelscope.cn/models/codetrend/LaMa_Inpainting_Model_ONNX"
                   "/resolve/master/lama_fp32.onnx")


def _fetch(url, dst, timeout=30):
    """Stream url -> dst via a temp file; verify sha256 before the rename."""
    import hashlib
    import urllib.request
    tmp = dst + ".part"
    h = hashlib.sha256()
    req = urllib.request.Request(url, headers={"User-Agent": "LinguaHaru"})
    with urllib.request.urlopen(req, timeout=timeout) as r, open(tmp, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
            h.update(chunk)
    if h.hexdigest() != _LAMA_SHA256:
        os.remove(tmp)
        raise RuntimeError(f"checksum mismatch from {url}")
    os.replace(tmp, dst)


def download_lama(progress=None):
    """Download lama_fp32.onnx into data/models/lama. Source order: HuggingFace
    first when it's directly reachable, otherwise ModelScope first (mainland
    China); each source is tried with bounded timeouts, and every result is
    sha256-checked. Returns the path, or raises with every source's reason."""
    if lama_available():
        return _model_path()
    from core.model_store import setup_model_env
    setup_model_env()
    if progress:
        progress("Downloading LaMa inpainting model…")
    dst = _model_path()
    hf = os.environ.get("HF_ENDPOINT", "https://huggingface.co").rstrip("/")
    hf_url = f"{hf}/{_LAMA_REPO}/resolve/main/{_LAMA_FILE}"
    sources = ([hf_url, _MODELSCOPE_URL] if "huggingface.co" in hf
               else [_MODELSCOPE_URL, hf_url])
    errors = []
    for url in sources:
        try:
            _fetch(url, dst)
            app_logger.info(f"LaMa model ready: {dst} (from {url.split('/')[2]})")
            return dst
        except Exception as e:  # noqa: BLE001 — try the next source
            errors.append(f"{url.split('/')[2]}: {e}")
            app_logger.warning(f"LaMa download from {url.split('/')[2]} failed: {e}")
    raise RuntimeError("LaMa model download failed — " + " | ".join(errors))


def _get_session():
    global _session, _session_tried
    if _session is not None or _session_tried:
        return _session
    with _LOCK:
        if _session is not None or _session_tried:
            return _session
        # Model not downloaded YET -> return None WITHOUT latching _session_tried,
        # so a later call (after the user finishes the download) still loads it.
        # _session_tried is only set once we actually attempt to load a present
        # model (so a genuine load failure isn't retried every frame).
        if not lama_available():
            return None
        _session_tried = True
        try:
            _session = _load_session()
        except Exception as e:  # noqa: BLE001
            app_logger.warning(f"LaMa load failed, will use cv2 inpaint: {e}")
            _session = None
    return _session


def _load_session():
    """Create the ONNX session, GPU-first when the CUDA EP is present (the OCR
    plugin installs onnxruntime-gpu on NVIDIA machines). LaMa runs in the MAIN
    process, where torch (STT) may already have loaded a DIFFERENT cuDNN — then
    the CUDA EP builds fine but dies at inference time. So DRY-RUN one inference
    on the GPU session and fall back to CPU if it throws: inpainting must never
    end up slower/broken than plain CPU."""
    import numpy as np
    import onnxruntime as ort
    if "CUDAExecutionProvider" in ort.get_available_providers():
        try:
            sess = ort.InferenceSession(
                _model_path(),
                providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
            ins = sess.get_inputs()
            s = next((i.shape[2] for i in ins
                      if len(i.shape) == 4 and isinstance(i.shape[2], int)), 512)
            sess.run(None, {ins[0].name: np.zeros((1, 3, s, s), np.float32),
                            ins[1].name: np.zeros((1, 1, s, s), np.float32)})
            app_logger.info("LaMa ONNX inpainter loaded (GPU)")
            return sess
        except Exception as e:  # noqa: BLE001 — cuDNN clash / bad EP -> CPU
            app_logger.warning(f"LaMa GPU session unusable ({e}); using CPU")
    sess = ort.InferenceSession(_model_path(), providers=["CPUExecutionProvider"])
    app_logger.info("LaMa ONNX inpainter loaded (CPU)")
    return sess


def inpaint(image_bgr, mask):
    """Inpaint the masked regions of a BGR uint8 image with LaMa. Returns a BGR
    uint8 image, or None if LaMa is unavailable / errors (caller uses cv2)."""
    sess = _get_session()
    if sess is None:
        return None
    try:
        import numpy as np
        import cv2
        h, w = image_bgr.shape[:2]
        # The Carve model has a FIXED 512x512 input. Resize image+mask to 512,
        # run, resize the fill back, and composite ONLY the masked region onto the
        # original — so everything except the (erased) text stays full-resolution.
        ins = {i.name: i.shape for i in sess.get_inputs()}
        S = 512
        for shp in ins.values():
            if len(shp) == 4 and isinstance(shp[2], int):
                S = shp[2]
                break
        small = cv2.resize(image_bgr, (S, S), interpolation=cv2.INTER_AREA)
        small_mask = cv2.resize((mask > 0).astype(np.uint8) * 255, (S, S),
                                interpolation=cv2.INTER_NEAREST)
        rgb = cv2.cvtColor(small, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        img_in = np.transpose(rgb, (2, 0, 1))[None].astype(np.float32)        # 1,3,S,S
        mask_in = ((small_mask > 0).astype(np.float32))[None, None]           # 1,1,S,S
        names = list(ins)
        out = sess.run(None, {names[0]: img_in, names[1]: mask_in})[0]
        out = out[0].transpose(1, 2, 0)            # S,S,3
        if out.max() <= 1.5:                       # some exports emit [0,1], others [0,255]
            out = out * 255.0
        out = np.clip(out, 0, 255).astype(np.uint8)
        out_bgr = cv2.cvtColor(out, cv2.COLOR_RGB2BGR)
        fill_full = cv2.resize(out_bgr, (w, h), interpolation=cv2.INTER_CUBIC)
        m3 = (mask > 0)[..., None]
        return np.where(m3, fill_full, image_bgr).astype(np.uint8)
    except Exception as e:  # noqa: BLE001 — any failure -> caller falls back to cv2
        app_logger.warning(f"LaMa inpaint failed, using cv2: {e}")
        return None

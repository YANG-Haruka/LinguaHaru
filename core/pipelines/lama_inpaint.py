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


def download_lama(progress=None):
    """Download lama_fp32.onnx into data/models/lama (HF official-first, like the
    rest of the model stack). Returns the path, or raises on failure."""
    if lama_available():
        return _model_path()
    from huggingface_hub import hf_hub_download
    from core.model_store import setup_model_env
    setup_model_env()
    if progress:
        progress("Downloading LaMa inpainting model…")
    src = hf_hub_download(repo_id=_LAMA_REPO, filename=_LAMA_FILE)
    # Copy into the stable project dir (hf cache may be elsewhere).
    import shutil
    dst = _model_path()
    if os.path.abspath(src) != os.path.abspath(dst):
        shutil.copyfile(src, dst)
    app_logger.info(f"LaMa model ready: {dst}")
    return dst


def _get_session():
    global _session, _session_tried
    if _session is not None or _session_tried:
        return _session
    with _LOCK:
        if _session is not None or _session_tried:
            return _session
        _session_tried = True
        if not lama_available():
            return None
        try:
            import onnxruntime as ort
            _session = ort.InferenceSession(
                _model_path(), providers=["CPUExecutionProvider"])
            app_logger.info("LaMa ONNX inpainter loaded")
        except Exception as e:  # noqa: BLE001
            app_logger.warning(f"LaMa load failed, will use cv2 inpaint: {e}")
            _session = None
    return _session


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

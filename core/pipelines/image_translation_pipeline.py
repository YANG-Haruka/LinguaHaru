# pipeline/image_translation_pipeline.py
# Image translation: RapidOCR (PP-OCRv5 ONNX models, no paddlepaddle) for
# text detection/recognition, LinguaHaru pipeline for translation, then the
# translated text is rendered back onto an inpainted copy of the image.
#
# Optional module - requires: rapidocr, onnxruntime, opencv-python-headless
import json
import os
import sys
import threading

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from rapidocr import RapidOCR

from .skip_pipeline import should_translate
from core.log_config import app_logger

_ocr_engines = {}   # (size, ocr_lang) -> (kind, engine)
# Serialize engine LOADING: two images OCR'd at once would otherwise both build
# the (non-thread-safe) PaddleOCR/RapidOCR engine concurrently. Inference reuses
# the cached engine afterwards.
_OCR_LOAD_LOCK = threading.Lock()

# PaddleOCR PP-OCRv6 size variant (det+rec). "small" is the light default —
# noticeably smaller/faster than "medium" with only a minor accuracy drop;
# "tiny" is fastest, "medium" most accurate. Driven by config "ocr_model_size"
# (the Image-OCR plugin's model selector sets this).

# Translation source language -> (RapidOCR Rec.lang_type, PaddleOCR lang). Only
# the non-Chinese/English scripts are listed; everything else (zh/zh-Hant/en/th/
# auto/unknown) maps to None = the default Chinese+English model (current
# behaviour, unchanged). Recognizing e.g. Japanese/Korean with the matching
# model is far more accurate than the default. The OCR language auto-follows the
# translation source language (no UI).
_OCR_LANG = {
    "ja": ("japan", "japan"),
    "ko": ("korean", "korean"),
    "ru": ("cyrillic", "cyrillic"),
    "fr": ("latin", "latin"), "de": ("latin", "latin"), "es": ("latin", "latin"),
    "it": ("latin", "latin"), "pt": ("latin", "latin"), "vi": ("latin", "latin"),
}


def _normalize_ocr_lang(src_lang):
    """Map a translation source language (display name or code) to an OCR-engine
    language pair, or None for the default Chinese+English model."""
    if not src_lang:
        return None
    code = src_lang
    try:
        from core.languages_config import LANGUAGE_MAP
        if src_lang in LANGUAGE_MAP:
            code = LANGUAGE_MAP[src_lang]
    except Exception:  # noqa: BLE001
        pass
    return _OCR_LANG.get(code.split("-")[0].lower())


def _ocr_model_size(manga=False):
    """The selected PP-OCRv6 size. Manga translation has its OWN model setting
    (the 漫画翻译 plugin, default medium for accuracy) separate from plain image
    OCR (default small). Same resolver the Plugins page and the model pickers use,
    so what they show is what runs."""
    from core.optional_modules import plugin_current_model
    return plugin_current_model("漫画翻译" if manga else "Image OCR")


_USE_CUDA = None


def _ocr_use_cuda():
    """True when onnxruntime's CUDA execution provider is available, so RapidOCR
    runs PP-OCRv6 on the GPU. Safe to request even if the provider later fails to
    initialize — RapidOCR transparently falls back to CPU. Only the torch-free OCR
    subprocess actually gets CUDA (the main process has torch, whose cuDNN clashes
    with onnxruntime-gpu's, so there RapidOCR auto-degrades to CPU)."""
    global _USE_CUDA
    if _USE_CUDA is None:
        try:
            import onnxruntime as ort
            _USE_CUDA = "CUDAExecutionProvider" in ort.get_available_providers()
        except Exception:  # noqa: BLE001
            _USE_CUDA = False
    return _USE_CUDA


def _ocr_use_dml():
    """True when onnxruntime-directml is installed (AMD / Intel GPUs on Windows,
    see module_manager._maybe_install_directml_onnxruntime)."""
    try:
        import onnxruntime as ort
        return "DmlExecutionProvider" in ort.get_available_providers()
    except Exception:  # noqa: BLE001
        return False


def _lang_code(src_lang):
    """Translation source language (display name or code) -> short code, e.g.
    'ja', 'zh', 'fr'."""
    if not src_lang:
        return ""
    code = src_lang
    try:
        from core.languages_config import LANGUAGE_MAP
        if src_lang in LANGUAGE_MAP:
            code = LANGUAGE_MAP[src_lang]
    except Exception:  # noqa: BLE001
        pass
    return code.split("-")[0].lower()


def _rapidocr_v6_lang(src_lang):
    """Map the source language to a PP-OCRv6 lang_type, or None when PP-OCRv6
    doesn't cover it (Korean/Cyrillic -> None -> PaddleOCR, which has those
    models). zh/en/auto/unknown -> 'ch' (PP-OCRv6's multilingual default)."""
    code = _lang_code(src_lang)
    if not code or code == "auto":
        return "ch"
    if code in ("ko", "ru"):
        return None     # PP-OCRv6 has no Korean/Cyrillic model; use PaddleOCR
    try:
        from rapidocr.utils.model_resolver import PP_OCRV6_LANGS, COMMON_LANG_ALIASES
    except Exception:  # noqa: BLE001 — older RapidOCR without PP-OCRv6
        return None
    lang = COMMON_LANG_ALIASES.get(code, code)
    return lang if lang in PP_OCRV6_LANGS else "ch"


def _rapidocr_root():
    """RapidOCR keeps its ONNX models in its own package dir by default — outside
    models/, so the netdisk OCR packs couldn't supply them and every fresh
    install re-downloaded. Pin it to models/rapidocr, moving over any models
    the package dir already holds (so existing installs don't re-download)."""
    import shutil
    from core.model_store import current_dir
    root = os.path.join(current_dir(), "rapidocr")
    os.makedirs(root, exist_ok=True)
    try:
        import rapidocr
        pkg = os.path.join(os.path.dirname(rapidocr.__file__), "models")
        for name in os.listdir(pkg):
            if name.endswith(".onnx") and not os.path.exists(os.path.join(root, name)):
                shutil.move(os.path.join(pkg, name), os.path.join(root, name))
    except Exception:  # noqa: BLE001 — best-effort; RapidOCR downloads what's missing
        pass
    return root


def _build_rapidocr_v6(v6_lang, size):
    """Build a PP-OCRv6 RapidOCR engine (GPU when available). Same v6 models as
    PaddleOCR, run via onnxruntime so the GPU is usable on Windows without the
    paddle/torch cuDNN conflict."""
    from rapidocr.utils.typings import OCRVersion, ModelType
    mt = {"tiny": ModelType.TINY, "small": ModelType.SMALL,
          "medium": ModelType.MEDIUM}.get(size, ModelType.MEDIUM)
    if v6_lang == "japan" and mt == ModelType.TINY:
        mt = ModelType.SMALL    # PP-OCRv6 tiny has no Japanese model
    use_cuda = _ocr_use_cuda()
    use_dml = not use_cuda and _ocr_use_dml()
    app_logger.info(
        f"Loading RapidOCR PP-OCRv6 ({v6_lang}, {mt.value}, "
        f"{'GPU' if use_cuda else 'GPU (DirectML)' if use_dml else 'CPU'})...")
    params = {
        "Global.model_root_dir": _rapidocr_root(),
        "EngineConfig.onnxruntime.use_cuda": use_cuda,
        "EngineConfig.onnxruntime.use_dml": use_dml,
        "Det.ocr_version": OCRVersion.PPOCRV6, "Det.lang_type": v6_lang, "Det.model_type": mt,
        "Rec.ocr_version": OCRVersion.PPOCRV6, "Rec.lang_type": v6_lang, "Rec.model_type": mt,
    }
    return ("rapid", RapidOCR(params=params))

# OCR recognition confidence below this is treated as noise: not translated and
# left untouched on the image, rather than rendering a garbled translation.
_MIN_OCR_CONFIDENCE = 0.6

# CJK-capable fonts to try, in order
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",        # Windows: Microsoft YaHei
    r"C:\Windows\Fonts\meiryo.ttc",      # Windows: Japanese
    r"C:\Windows\Fonts\malgun.ttf",      # Windows: Korean
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]


def _get_ocr_engine(src_lang=None, manga=False):
    """Best available OCR engine, recognition-language aware.

    `src_lang` (the translation source language) auto-selects the recognition
    model: Japanese/Korean/Cyrillic/Latin get their matching model, everything
    else uses the default Chinese+English model. `manga` picks the manga-specific
    model size (config manga_ocr_model_size). Engines are cached per
    (size, language). Returns (kind, engine) with kind in {"paddle", "rapid"}."""
    lang = _normalize_ocr_lang(src_lang)            # (rapid_rec, paddle_lang) or None
    size = _ocr_model_size(manga)
    v6_lang = _rapidocr_v6_lang(src_lang)           # PP-OCRv6 lang_type, or None
    key = (size, v6_lang, lang[0] if lang else None)
    if key in _ocr_engines:
        return _ocr_engines[key]
    with _OCR_LOAD_LOCK:
        if key in _ocr_engines:      # double-check inside the lock
            return _ocr_engines[key]
        return _load_ocr_engine(key, v6_lang, lang, size)


def _paddle_device():
    """Use the GPU for PaddleOCR when a CUDA paddle build is present. The default
    pip `paddlepaddle` is CPU-only — install `paddlepaddle-gpu` (matching your
    CUDA) to make OCR run on the GPU. Harmless on the CPU build (returns 'cpu')."""
    try:
        import paddle
        if paddle.device.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0:
            return "gpu"
    except Exception:  # noqa: BLE001
        pass
    return "cpu"


def _load_ocr_engine(key, v6_lang, lang, size):
    # PRIMARY: RapidOCR PP-OCRv6 (ONNX). Same v6 models as PaddleOCR but runs on
    # onnxruntime, so it uses the GPU (~60x faster, identical accuracy) inside the
    # torch-free OCR subprocess without the paddle/torch cuDNN conflict that makes
    # PaddleOCR GPU impossible on Windows. PP-OCRv6 lacks Korean/Cyrillic, so
    # v6_lang is None for those -> PaddleOCR fallback below.
    if v6_lang is not None:
        try:
            engine = _build_rapidocr_v6(v6_lang, size)
            _ocr_engines[key] = engine
            return engine
        except Exception as e:  # noqa: BLE001
            app_logger.warning(f"RapidOCR PP-OCRv6 unavailable ({e}); trying PaddleOCR.")
    try:
        # DLL load-order fix: paddle's oneDNN/MKL DLLs break ctranslate2
        # (faster-whisper) if paddle loads first; the reverse order works.
        # Pre-import faster_whisper when both optional modules are present.
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            pass
        from paddleocr import PaddleOCR
        common = dict(use_doc_orientation_classify=False, use_doc_unwarping=False,
                      use_textline_orientation=True,
                      # Run on the GPU when a CUDA paddle build is installed
                      # (much faster); CPU build -> "cpu" (unchanged).
                      device=_paddle_device(),
                      # paddle 3.3.1 oneDNN hits an unimplemented PIR op on
                      # Windows CPU (ConvertPirAttribute2RuntimeAttribute)
                      enable_mkldnn=False)
        if lang:
            # Language-specific model: let PaddleOCR pick det/rec for that lang.
            app_logger.info(f"Loading PaddleOCR engine (lang={lang[1]})...")
            engine = ("paddle", PaddleOCR(lang=lang[1], **common))
        else:
            app_logger.info(f"Loading PaddleOCR engine (PP-OCRv6_{size})...")
            engine = ("paddle", PaddleOCR(
                text_detection_model_name=f"PP-OCRv6_{size}_det",
                text_recognition_model_name=f"PP-OCRv6_{size}_rec",
                **common))
    except Exception as e:
        if not isinstance(e, ImportError):
            app_logger.warning(f"PaddleOCR unavailable ({e}), falling back to RapidOCR")
        from rapidocr.utils.typings import OCRVersion
        params = {"Global.model_root_dir": _rapidocr_root(),
                  "Det.ocr_version": OCRVersion.PPOCRV5,
                  "Rec.ocr_version": OCRVersion.PPOCRV5}
        if lang:
            params["Rec.lang_type"] = lang[0]
            app_logger.info(f"Loading RapidOCR engine (rec lang={lang[0]})...")
        else:
            app_logger.info("Loading RapidOCR engine (PP-OCRv5)...")
        engine = ("rapid", RapidOCR(params=params))
    _ocr_engines[key] = engine
    return engine


def _run_ocr_inproc(file_path, src_lang=None, manga=False):
    """Run OCR and normalize results to a list of (text, quad_points, score).
    Inference is serialized via GPU_LOCK so concurrent image tasks (or a video
    transcription) don't thrash one GPU/CPU."""
    from core.compute_lock import GPU_LOCK
    kind, engine = _get_ocr_engine(src_lang, manga)
    with GPU_LOCK:
        if kind == "paddle":
            result = engine.predict(file_path)[0]
            texts = list(result.get("rec_texts") or [])
            boxes = list(result.get("rec_polys") if result.get("rec_polys") is not None
                         else result.get("rec_boxes") or [])
            scores = list(result.get("rec_scores") or [])
        else:
            result = engine(file_path)
            texts = list(result.txts or [])
            boxes = list(result.boxes if result.boxes is not None else [])
            scores = list(result.scores or [])
    # numpy boxes -> plain lists so the result pickles cleanly back from the OCR
    # subprocess (and is JSON-safe downstream).
    boxes = [b.tolist() if isinstance(b, np.ndarray) else b for b in boxes]
    scores = [float(s) for s in scores]
    return texts, boxes, scores


# --- OCR process isolation -------------------------------------------------- #
# PaddleOCR/RapidOCR is CPU-bound C++/Python that HOLDS the Python GIL during
# model build + inference. On a QThread that starves the Qt main thread (timer +
# repaints can't run) and the desktop UI freezes. Running OCR in a persistent
# child process lets the worker thread block on the result (releasing the GIL),
# so the UI stays responsive. The model loads once in the child and is reused.
_ocr_pool = None
_ocr_pool_lock = threading.Lock()


def _bundled_nvidia_dir():
    """In a frozen build the nvidia CUDA-12 runtime DLLs are bundled under
    <bundle>/nvidia_cuda (see lingua-haru*.spec) because there is no
    site-packages/nvidia/ to preload from. Returns that dir if it exists, else
    None (source runs / CPU-only builds use onnxruntime's normal DLL search)."""
    base = getattr(sys, "_MEIPASS", None)
    if not base:
        return None
    nv = os.path.join(base, "nvidia_cuda")
    return nv if os.path.isdir(nv) else None


def _ocr_subprocess_enabled():
    """Config `ocr_subprocess` (default True): isolate OCR in a child process so it
    can't freeze the desktop UI. Set False to run in-process (e.g. debugging)."""
    try:
        from core.paths import SYSTEM_CONFIG
        with open(SYSTEM_CONFIG, encoding="utf-8") as f:
            return bool(json.load(f).get("ocr_subprocess", True))
    except Exception:  # noqa: BLE001
        return True


def _get_ocr_pool():
    global _ocr_pool
    if _ocr_pool is None:
        with _ocr_pool_lock:
            if _ocr_pool is None:
                import concurrent.futures
                # 1 worker = the engine is built once and reused; concurrent OCR
                # calls queue (same as the old GPU_LOCK serialization).
                _ocr_pool = concurrent.futures.ProcessPoolExecutor(max_workers=1)
    return _ocr_pool


def _detect_boxes_inproc(file_path, manga=False):
    """Text DETECTION only (no recognition): the line quads for the vision-LLM
    OCR engine, which then just reads each crop. ~0.4 s on a CPU."""
    from core.compute_lock import GPU_LOCK
    kind, engine = _get_ocr_engine(None, manga)   # det is language-independent
    with GPU_LOCK:
        if kind == "rapid":
            # RapidOCR's per-call use_* flags are sticky (update_params setattr's
            # them on the shared engine): restore, or the next full OCR returns
            # detection only.
            prev = {k: getattr(engine, k) for k in ("use_det", "use_cls", "use_rec")}
            try:
                boxes = engine(file_path, use_det=True, use_cls=False, use_rec=False).boxes
            finally:
                engine.update_params(**prev)
        else:   # PaddleOCR fallback has no det-only call: take the full result's polys
            res = engine.predict(file_path)[0]
            boxes = res.get("rec_polys") if res.get("rec_polys") is not None else res.get("rec_boxes")
    return [b.tolist() if isinstance(b, np.ndarray) else b for b in (boxes if boxes is not None else [])]


def _ocr_worker_entry(file_path, src_lang, manga=False, det_only=False):
    """Runs in the OCR child process. The child didn't run the app's startup, so it
    must set up the model env (cache dirs / HF endpoint) before building the OCR
    engine. The engine is cached in the child across calls (max_workers=1)."""
    try:
        from core import model_store
        model_store.setup_model_env()
        model_store.finish_model_env_setup()
    except Exception as e:  # noqa: BLE001 — best-effort; engine build may still find caches
        # Don't fail silently: this is the OCR child's ONLY chance to set the model
        # cache dirs / HF endpoint, so if it throws the engine build may download
        # against the wrong endpoint (or hang). Log it so slow/failing OCR is
        # diagnosable instead of a mystery.
        app_logger.warning(f"OCR child model-env setup failed: {e}")
    # Preload CUDA/cuDNN DLLs so onnxruntime-gpu's CUDA provider can initialize in
    # this torch-free child -> RapidOCR runs on the GPU. Source runs load them from
    # the nvidia-*-cu12 wheels; a FROZEN build has no site-packages, so the nvidia
    # DLLs are bundled under <bundle>/nvidia_cuda (see lingua-haru*.spec) and we add
    # that dir to the DLL search path + preload from it. No-op on CPU-only installs.
    try:
        import onnxruntime as ort
        nv = _bundled_nvidia_dir()
        if nv:
            try:
                os.add_dll_directory(nv)
            except Exception:  # noqa: BLE001
                pass
            ort.preload_dlls(directory=nv)
        else:
            ort.preload_dlls()
    except Exception:  # noqa: BLE001 — older onnxruntime (no preload_dlls) / CPU build
        pass
    if det_only:
        return _detect_boxes_inproc(file_path, manga)
    return _run_ocr_inproc(file_path, src_lang, manga)


def _run_ocr(file_path, src_lang=None, manga=False):
    """OCR an image with the configured engine (Settings: online / self-hosted
    OCR); a remote failure falls back to on-device OCR so the task still runs."""
    from core.pipelines import ocr_remote
    engine = ocr_remote.selected_engine()
    if engine != "local":
        try:
            return ocr_remote.run(file_path, src_lang,
                                  lambda p: _run_local(p, src_lang, manga, det_only=True))
        except Exception as e:  # noqa: BLE001 — misconfigured / offline -> on-device
            app_logger.warning(f"OCR engine '{engine}' failed ({e}); using on-device OCR.")
    return _run_local(file_path, src_lang, manga)


def _run_local(file_path, src_lang=None, manga=False, det_only=False):
    """On-device OCR, isolated in a child process so it can't freeze the UI (GIL).
    Falls back to in-process if the subprocess is unavailable / crashes."""
    inproc = _detect_boxes_inproc if det_only else _run_ocr_inproc
    if not _ocr_subprocess_enabled():
        return inproc(file_path, manga) if det_only else inproc(file_path, src_lang, manga)
    try:
        return _get_ocr_pool().submit(_ocr_worker_entry, file_path, src_lang, manga,
                                      det_only).result()
    except Exception as e:  # noqa: BLE001 — broken pool / child crash / pickling
        app_logger.warning(f"OCR subprocess failed ({e}); running in-process.")
        global _ocr_pool
        with _ocr_pool_lock:
            try:
                if _ocr_pool is not None:
                    _ocr_pool.shutdown(wait=False, cancel_futures=True)
            except Exception:  # noqa: BLE001
                pass
            _ocr_pool = None
        return inproc(file_path, manga) if det_only else inproc(file_path, src_lang, manga)


def _find_font_path():
    for path in _FONT_CANDIDATES:
        if os.path.exists(path):
            return path
    return None


def _box_bounds(box):
    """(x_min, y_min, x_max, y_max) for a quad (Nx2) or flat [x1,y1,x2,y2] box."""
    pts = np.asarray(box, dtype=float)
    if pts.ndim == 1 and pts.size == 4:
        return float(pts[0]), float(pts[1]), float(pts[2]), float(pts[3])
    return (float(pts[:, 0].min()), float(pts[:, 1].min()),
            float(pts[:, 0].max()), float(pts[:, 1].max()))


def _reading_order(items, rtl):
    """Sort OCR (text, box, score) items into reading order so the LLM sees them
    in narrative sequence (improves cross-region coherence). Rows are banded by
    vertical overlap (top->bottom); within a row left->right, or right->left for
    RTL scripts (manga / vertical Japanese). Pure geometry, no model."""
    rows = []   # each: {y0, y1, members:[(cx, item)]}
    for it in items:
        x0, y0, x1, y1 = _box_bounds(it[1])
        cx = (x0 + x1) / 2.0
        h = max(y1 - y0, 1.0)
        placed = None
        for r in rows:
            # same row if vertical centers overlap within ~60% of the line height
            if abs(((r["y0"] + r["y1"]) / 2) - ((y0 + y1) / 2)) < 0.6 * h:
                placed = r
                break
        if placed is None:
            rows.append({"y0": y0, "y1": y1, "members": [(cx, it)]})
        else:
            placed["members"].append((cx, it))
            placed["y0"] = min(placed["y0"], y0)
            placed["y1"] = max(placed["y1"], y1)
    rows.sort(key=lambda r: r["y0"])
    out = []
    for r in rows:
        r["members"].sort(key=lambda m: m[0], reverse=rtl)
        out.extend(it for _cx, it in r["members"])
    return out


def _manga_mode():
    """Config flag `manga_mode` (default off): merge OCR lines per speech bubble +
    translate each bubble as one sentence. Set per-run by the frontends."""
    try:
        from core.paths import SYSTEM_CONFIG
        with open(SYSTEM_CONFIG, encoding="utf-8") as f:
            return bool(json.load(f).get("manga_mode", False))
    except Exception:  # noqa: BLE001
        return False


def _group_text_regions(line_regions):
    """Merge OCR text-lines that belong to the same speech bubble into one region.

    Ported from manga-image-translator's textline_merge idea, reduced to plain
    geometry (union-find, no networkx): two lines merge when they are close
    (polygon gap <= ~1 char), share a writing direction (both vertical columns or
    both horizontal rows), have similar font size, and are aligned on the shared
    axis. Within a merged bubble the lines are concatenated in reading order
    (vertical = right->left columns; horizontal = top->bottom rows), giving the LLM
    a whole sentence instead of fragments. Returns merged regions, each with the
    concatenated `value`, the union `rect` (for rendering) and `erase_rects` (the
    member line boxes, for precise text removal)."""
    def dims(r):
        return r[2] - r[0], r[3] - r[1]

    def direction(r):
        w, h = dims(r)
        return "v" if h > 1.3 * max(w, 1) else "h"

    def char_size(r):
        return min(dims(r))

    def gap(a, b):
        dx = max(0, max(a[0], b[0]) - min(a[2], b[2]))
        dy = max(0, max(a[1], b[1]) - min(a[3], b[3]))
        return (dx * dx + dy * dy) ** 0.5

    def can_merge(a, b):
        csa, csb = char_size(a), char_size(b)
        cs = max(min(csa, csb), 1)
        if direction(a) != direction(b):
            return False
        if max(csa, csb) / cs > 1.6:          # font sizes must be similar
            return False
        if gap(a, b) > 1.0 * cs:              # within ~1 character
            return False
        if direction(a) == "v":               # vertical: columns share x-extent
            return min(a[2], b[2]) - max(a[0], b[0]) > -0.6 * cs
        return min(a[3], b[3]) - max(a[1], b[1]) > -0.6 * cs   # horizontal: rows share y

    n = len(line_regions)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    rects = [r["rect"] for r in line_regions]
    for i in range(n):
        for j in range(i + 1, n):
            # Only merge lines we'd actually translate (don't pull OCR noise in).
            if line_regions[i]["needs_translation"] and line_regions[j]["needs_translation"] \
                    and can_merge(rects[i], rects[j]):
                parent[find(i)] = find(j)

    from collections import defaultdict
    comps = defaultdict(list)
    for i in range(n):
        comps[find(i)].append(i)

    merged = []
    for idxs in comps.values():
        d = direction(rects[idxs[0]])
        if d == "v":   # right->left columns, top->bottom within a column
            idxs.sort(key=lambda i: (-(rects[i][0] + rects[i][2]) / 2, rects[i][1]))
        else:          # top->bottom rows, left->right within a row
            idxs.sort(key=lambda i: ((rects[i][1] + rects[i][3]) / 2, rects[i][0]))
        members = [line_regions[i] for i in idxs]
        mrects = [m["rect"] for m in members]
        union = [min(r[0] for r in mrects), min(r[1] for r in mrects),
                 max(r[2] for r in mrects), max(r[3] for r in mrects)]
        merged.append({
            "value": "".join(m["value"] for m in members),
            "rect": union,
            "erase_rects": mrects,
            "score": min(m["score"] for m in members),
            "needs_translation": any(m["needs_translation"] for m in members),
        })
    # Reading order across bubbles: top->bottom, right->left (manga) for coherent
    # LLM context and a sensible companion text file.
    merged.sort(key=lambda m: (m["rect"][1], -m["rect"][2]))
    return merged


def ocr_and_group_image(file_path, src_lang=None, manga=None):
    """OCR an image and return (content_data, regions) WITHOUT touching disk.

    content_data = the translatable entries (count_src/type/value); regions = every
    detected region with rect/score/needs_translation (+ erase_rects/value). In
    manga mode the regions are bubble-grouped AND the manga-specific OCR model is
    used. `manga` defaults to the 漫画模式 config flag; the manga PDF translator
    passes manga=True explicitly (it's always manga, even for auto-detected scans).
    Reused per-page by the manga PDF translator; extract_image_content_to_json()
    wraps this and writes the files."""
    if manga is None:
        manga = _manga_mode()
    texts, boxes, scores = _run_ocr(file_path, src_lang, manga)
    # Some engines/versions omit scores; zip() would then truncate to the
    # shortest list and silently drop ALL text. Pad with 1.0 (treated confident).
    if len(scores) < len(texts):
        scores = list(scores) + [1.0] * (len(texts) - len(scores))

    # Reorder into reading order so count_src follows the narrative (coherent LLM
    # context). RTL: Arabic/Hebrew always; Japanese ONLY when the layout is
    # actually vertical columns (most boxes tall-and-narrow = manga/縦書き) — a
    # normal HORIZONTAL Japanese screenshot must read left-to-right.
    _base = (src_lang or "").split("-")[0].lower()
    _rtl = _base in ("ar", "he")
    if _base == "ja":
        _vert = sum(1 for b in boxes
                    if (lambda x0, y0, x1, y1: (y1 - y0) > 1.5 * max(x1 - x0, 1))(*_box_bounds(b)))
        _rtl = _vert >= max(1, len(boxes) // 2)   # majority vertical -> RTL columns
    items = _reading_order(list(zip(texts, boxes, scores)), _rtl)
    if items:
        texts, boxes, scores = (list(t) for t in zip(*items))

    # Build one entry per OCR text-line first (count_src assigned after optional
    # manga grouping, so the ids stay sequential whichever path we take).
    line_regions = []
    for text, box, score in zip(texts, boxes, scores):
        text = (text or "").strip()
        if not text:
            continue
        # Bounding rectangle: quads (N x 2 points) or flat [x1,y1,x2,y2]
        points = np.asarray(box, dtype=float)
        if points.ndim == 1 and points.size == 4:
            x_min, y_min, x_max, y_max = points
        else:
            x_min, y_min = points.min(axis=0)
            x_max, y_max = points.max(axis=0)
        rect = [int(x_min), int(y_min), int(x_max), int(y_max)]

        score_val = float(score) if score is not None else 0.0
        # Low-confidence regions are likely OCR noise: keep them in regions.json
        # (so the companion text file still lists them) but do not translate or
        # erase them, leaving the original pixels intact.
        confident = score_val >= _MIN_OCR_CONFIDENCE
        line_regions.append({
            "value": text,
            "rect": rect,
            "score": score_val,
            "needs_translation": should_translate(text) and confident,
        })

    # MANGA MODE: merge OCR text-lines belonging to the same speech bubble into ONE
    # region, so the bubble is translated as a whole sentence (a name/clause split
    # across lines stays intact) instead of fragment-by-fragment. No model needed —
    # pure geometry (see _group_text_regions). Off => one region per line (default).
    if manga:
        line_regions = _group_text_regions(line_regions)

    content_data = []
    regions = []
    for i, region in enumerate(line_regions, 1):
        region["count_src"] = i
        regions.append(region)
        if region["needs_translation"]:
            content_data.append({
                "count_src": i,
                "type": "text",
                "value": region["value"],
            })
    return content_data, regions


def extract_image_content_to_json(file_path, temp_dir, src_lang=None):
    """Run OCR on the image and save recognized text regions to src.json.

    `src_lang` auto-selects the OCR recognition language (Japanese/Korean/etc.)."""
    content_data, regions = ocr_and_group_image(file_path, src_lang)

    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join(temp_dir, filename)
    os.makedirs(temp_folder, exist_ok=True)

    json_path = os.path.join(temp_folder, "src.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(content_data, f, ensure_ascii=False, indent=4)
    with open(os.path.join(temp_folder, "regions.json"), "w", encoding="utf-8") as f:
        json.dump(regions, f, ensure_ascii=False, indent=4)

    app_logger.info(f"OCR found {len(regions)} text regions, {len(content_data)} to translate")
    if not content_data:
        app_logger.warning("No translatable text found in image")
    return json_path


def _lama_enabled():
    """Config flag image_inpaint_lama (default off): use the optional LaMa model
    for text erasure instead of cv2 Telea."""
    try:
        from core.paths import SYSTEM_CONFIG
        with open(SYSTEM_CONFIG, encoding="utf-8") as f:
            return bool(json.load(f).get("image_inpaint_lama", False))
    except Exception:  # noqa: BLE001
        return False


def _is_cjk_lang(lang):
    return (lang or "").split("-")[0].lower() in ("zh", "ja", "ko")


def _text_colors(rgb, rect):
    """(fg, stroke) chosen from the patched background luminance so text stays
    readable on any background: dark text + light halo on light bg, vice-versa."""
    x1, y1, x2, y2 = rect
    h, w = rgb.shape[:2]
    crop = rgb[max(0, y1):min(h, y2), max(0, x1):min(w, x2)]
    lum = float(crop.mean()) if crop.size else 255.0
    if lum < 128:
        return (235, 235, 235), (20, 20, 20)   # light text, dark halo
    return (20, 20, 20), (245, 245, 245)        # dark text, light halo


# No HarfBuzz 'vert' substitution in PIL, so punctuation would be drawn in its
# horizontal form (comma in the bottom-left corner, brackets lying sideways).
# Swap in the Unicode vertical presentation forms instead; the CJK fonts
# _find_font_path picks (YaHei, Meiryo, PingFang, Noto CJK) all carry them.
_VERTICAL_FORMS = str.maketrans({
    "，": "︐", ",": "︐", "、": "︑", "。": "︒", "：": "︓", ":": "︓",
    "；": "︔", ";": "︔", "！": "︕", "!": "︕", "？": "︖", "?": "︖",
    "…": "︙", "—": "︱", "～": "︴", "（": "︵", "(": "︵", "）": "︶", ")": "︶",
    "【": "︻", "】": "︼", "《": "︽", "》": "︾", "「": "﹁", "」": "﹂",
    "『": "﹃", "』": "﹄", "“": "﹁", "”": "﹂", "‘": "﹃", "’": "﹄",
})


# Closing punctuation never starts a column (Chinese/Japanese line-breaking rule).
_NO_COLUMN_START = set("︐︑︒︓︔︕︖︙︶︼︾﹂﹄")


def _columns(chars, per_col):
    """Split chars into columns of <= per_col. A column that would end mid-phrase
    ends after its last punctuation instead (if that keeps it at least half full),
    and never leaves closing punctuation to start the next column."""
    cols, i = [], 0
    while i < len(chars):
        n = min(per_col, len(chars) - i)
        if i + n < len(chars) and chars[i + n - 1] not in _NO_COLUMN_START:
            cut = max((j + 1 for j in range(n) if chars[i + j] in _NO_COLUMN_START), default=0)
            if cut * 2 >= n:
                n = cut
        while n > 1 and i + n < len(chars) and chars[i + n] in _NO_COLUMN_START:
            n -= 1
        cols.append(chars[i:i + n])
        i += n
    return cols


def _clean_breaks(cols):
    """Every column but the last ends on punctuation (no word split across columns)."""
    return all(c[-1] in _NO_COLUMN_START for c in cols[:-1])


def _render_vertical(draw, text, rect, font_path, fg, stroke, max_size=None):
    """Stack characters top->bottom in columns laid out right->left (CJK vertical
    writing). The font size is chosen so the whole block fits the box in BOTH axes
    (the column count * column width <= box width, and chars-per-column * line
    height <= box height), then the block is centered. max_size caps it (the source
    text's character size, so a short translation is not blown up to fill the
    box). PIL-only (no HarfBuzz vertical glyph substitution, but fine for
    caption-style overlays)."""
    x1, y1, x2, y2 = rect
    box_w, box_h = max(x2 - x1, 8), max(y2 - y1, 8)
    chars = [c for c in text.translate(_VERTICAL_FORMS) if c.strip()]
    if not chars:
        return
    line_gap = 1.06
    size, cols, best = 8, None, None
    # A single column can't be wider than the box; shrink until the whole grid fits
    # the box height AND width (this is what prevents the old vertical overflow).
    # Then keep shrinking (by at most 20%) if that lets every column break on
    # punctuation instead of mid-word.
    for s in range(min(box_h, box_w, max_size or box_h), 7, -1):
        step = s * line_gap
        cols = _columns(chars, max(1, int(box_h // step)))
        if len(cols) * step > box_w:
            continue
        if best is None or _clean_breaks(cols):
            best = (s, cols)
        if _clean_breaks(cols) or s < 0.8 * best[0]:
            break
    if best:
        size, cols = best
    step = size * line_gap
    if not best:                              # nothing fits: 8 px floor
        cols = _columns(chars, max(1, int(box_h // step)))
    font = (ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default())
    sw = max(1, size // 12)
    # Center the grid in the box; lay columns out right -> left.
    grid_w = len(cols) * step
    grid_h = max(len(c) for c in cols) * step
    x_right = x2 - max(0, (box_w - grid_w)) / 2.0
    y_top = y1 + max(0, (box_h - grid_h)) / 2.0
    for col, column in enumerate(cols):
        cx = x_right - (col + 1) * step + (step - size) / 2.0
        for row, ch in enumerate(column):
            draw.text((cx, y_top + row * step), ch, font=font, fill=fg,
                      stroke_width=sw, stroke_fill=stroke)


def _fit_text(draw, text, rect, font_path):
    """Pick a font size and line wrapping so the text fits inside rect."""
    x1, y1, x2, y2 = rect
    box_w, box_h = max(x2 - x1, 8), max(y2 - y1, 8)
    # A translation may come back with explicit line breaks; honor them as hard
    # breaks. Pillow's textlength() raises "can't measure length of multiline text"
    # on ANY string containing a newline, so a '\n' must never reach it below.
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    for size in range(box_h, 7, -1):
        font = (ImageFont.truetype(font_path, size) if font_path
                else ImageFont.load_default())
        # Greedy character wrap (works for CJK and degrades fine for Latin)
        lines, line = [], ""
        for ch in text:
            if ch == "\n":            # hard break — flush, never measure a newline
                lines.append(line)
                line = ""
                continue
            candidate = line + ch
            if draw.textlength(candidate, font=font) <= box_w or not line:
                line = candidate
            else:
                lines.append(line)
                line = ch
        lines.append(line)

        line_h = size * 1.15
        if line_h * len(lines) <= box_h or size == 8:
            return font, lines, line_h
    font = ImageFont.load_default()
    return font, text.split("\n"), 12


def _apply_inpaint(image, mask, dilate=True):
    """Inpaint the masked text regions out of a BGR image (LaMa when enabled +
    installed, else OpenCV Telea). Returns the cleaned BGR image. This is the slow
    step; manga proofread caches its result (see inpaint_page). dilate=False for an
    already-tight glyph mask (ink erase)."""
    k = max(3, int(round(0.005 * max(image.shape[:2]))))   # dilation ~ image scale
    if dilate:
        mask = cv2.dilate(mask, np.ones((k, k), np.uint8), iterations=1)
    if _lama_enabled():
        try:
            from core.pipelines.lama_inpaint import inpaint as _lama_inpaint
            out = _lama_inpaint(image, mask)
            if out is not None:
                app_logger.info("Inpaint: LaMa")
                return out
        except Exception:  # noqa: BLE001
            pass
    app_logger.info("Inpaint: cv2 Telea")
    return cv2.inpaint(image, mask, max(5, k), cv2.INPAINT_TELEA)


def _erase_mode():
    """Config `image_erase_mode`: "ink" (default) erases only the text strokes found
    inside each OCR box; "box" is the legacy behaviour (inpaint the whole dilated
    OCR box, which smears tight speech-balloon outlines)."""
    try:
        from core.paths import SYSTEM_CONFIG
        with open(SYSTEM_CONFIG, encoding="utf-8") as f:
            return "box" if json.load(f).get("image_erase_mode") == "box" else "ink"
    except Exception:  # noqa: BLE001
        return "ink"


_FLAT_BG_MAD = 8.0   # per-channel median abs. deviation (0-255) under which a
                     # background counts as flat -> fill with its colour, no inpaint


def _char_size(region):
    """~one character size: median short side of the region's OCR line boxes."""
    rects = region.get("erase_rects") or [region["rect"]]
    return max(4, int(np.median([min(r[2] - r[0], r[3] - r[1]) for r in rects])))


def _ink(gray, ref):
    """(ink, faint) bool masks of gray, judged against ref (the OCR box): ink =
    clearly darker than the box's background (median) - text strokes; faint = any
    clear tone step (thin/light outlines, coloured ground) - only used to find walls.
    Flipped when the box holds light text on a dark ground."""
    bg = float(np.median(ref))
    lo, hi = np.percentile(ref, (2, 98))
    if hi - bg > bg - lo:
        gray, bg, lo = 255 - gray, 255 - bg, 255 - hi
    return gray < bg - max(35.0, 0.5 * (bg - lo)), gray < bg - 35.0


def _balloon(walls, rect, cs):
    """Interior (bool mask) of the closed shape around rect in a window whose
    non-text ink is `walls` (balloon outline, panel border, art), or None.

    The light area is opened by ~cs/3 so a speech tail or small outline gap does not
    leak it; the region holding the text box must then not reach the window edge
    (= enclosed), and its holes (the glyphs) are filled."""
    g = max(3, cs // 6) | 1                           # seal small breaks in the outline
    walls = cv2.morphologyEx(walls.astype(np.uint8), cv2.MORPH_CLOSE,
                             cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (g, g)))
    light = 1 - walls
    rad = max(2, cs // 3)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * rad + 1, 2 * rad + 1))
    n, lab, st, _ = cv2.connectedComponentsWithStats(cv2.erode(light, k), connectivity=4)
    x0, y0, x1, y1 = rect
    counts = np.bincount(lab[y0:y1, x0:x1].ravel(), minlength=n)
    counts[0] = 0
    o = int(counts.argmax())
    if counts[o] == 0:
        return None
    l, t, w, h = st[o, :4]
    if l == 0 or t == 0 or l + w >= walls.shape[1] or t + h >= walls.shape[0]:
        return None                                   # leaks out: no closed balloon
    # Grow back geodesically (never across a wall) to fill the corners and the
    # pockets around glyphs; 2*rad steps reach into a tail only a little.
    comp = (lab == o).astype(np.uint8)
    cross = cv2.getStructuringElement(cv2.MORPH_CROSS, (3, 3))
    for _ in range(2 * rad):
        comp = cv2.dilate(comp, cross) & light
    # Light pockets cut off by a character touching the outline: enclosed light
    # areas lying inside the text box, right next to the interior, and not mere
    # slivers (e.g. the gap between an outline and a panel border).
    rest = light & (1 - comp)
    n2, lab2, st2, _ = cv2.connectedComponentsWithStats(rest, connectivity=4)
    l2, t2, w2, h2 = (st2[:, i] for i in range(4))
    q = max(2, cs // 4)
    thick = np.zeros(n2)
    np.maximum.at(thick, lab2.ravel(), cv2.distanceTransform(rest, cv2.DIST_L2, 3).ravel())
    pocket = (l2 >= x0 - q) & (t2 >= y0 - q) & (l2 + w2 <= x1 + q) & (t2 + h2 <= y1 + q) & (thick >= cs / 8)
    nearby = np.zeros(n2, bool)
    nearby[np.unique(lab2[cv2.dilate(comp, k) > 0])] = True
    pocket &= nearby
    pocket[0] = False
    interior = _fill_holes(comp | pocket[lab2].astype(np.uint8))
    if interior[y0:y1, x0:x1].sum() < 0.4 * max(1, (x1 - x0) * (y1 - y0)):
        return None
    return interior.astype(bool)


def _fused_glyphs(ink, interior, cs, boxes):
    """Characters touching the balloon outline merge into it (a wall). Return their
    strokes: the wall ink next to the interior that lies deeper than the outline's
    own stroke width from the light outside (the outline itself never does), in
    glyph-sized pieces (a long piece = dark art, left alone)."""
    b = max(3, cs // 4)
    kb = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * b + 1, 2 * b + 1))
    near = cv2.dilate(interior.astype(np.uint8), kb).astype(bool) & ink & ~interior
    if not near.any():
        return near
    dt = cv2.distanceTransform(near.astype(np.uint8), cv2.DIST_L2, 3)
    ridge = near & (dt >= cv2.dilate(dt, np.ones((3, 3), np.uint8)))
    ow = 2 * float(np.median(dt[ridge]))        # stroke width, from the stroke centre lines
    # the outside = light areas except small pockets between a glyph and the outline
    light = (~ink & ~interior).astype(np.uint8)
    n, lab, st, cents = cv2.connectedComponentsWithStats(light, connectivity=4)
    hull = np.zeros_like(light)
    pts = cv2.findNonZero(interior.astype(np.uint8))
    cv2.fillConvexPoly(hull, cv2.convexHull(pts), 1)
    pocket = np.zeros(n, bool)          # touches a text box, centred inside the balloon
    pocket[np.unique(lab[boxes & light.astype(bool)])] = True
    cx = np.clip(cents[:, 0].round().astype(int), 0, light.shape[1] - 1)
    cy = np.clip(cents[:, 1].round().astype(int), 0, light.shape[0] - 1)
    pocket &= hull[cy, cx] > 0
    big = (st[:, 4] > cs * cs // 2) | ~pocket
    big[0] = False
    deep = cv2.distanceTransform((~big[lab]).astype(np.uint8), cv2.DIST_L2, 3) > ow + 1
    n, lab, st, _ = cv2.connectedComponentsWithStats((near & deep).astype(np.uint8), connectivity=8)
    ok = np.maximum(st[:, 2], st[:, 3]) <= cs
    ok[0] = False
    return ok[lab]


def _close(m, cs):
    """Morphological closing with a ~character-sized disc (fills character-sized
    dents); padded so the window edge counts as outside."""
    p = cs | 1
    padded = cv2.copyMakeBorder(m.astype(np.uint8), p, p, p, p, cv2.BORDER_CONSTANT, value=0)
    return cv2.morphologyEx(padded, cv2.MORPH_CLOSE, cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (p, p)))[p:-p, p:-p].astype(bool)


def _fill_holes(m):
    cnts, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    out = np.zeros_like(m)
    cv2.drawContours(out, cnts, -1, 1, -1)
    return out


def _text_layout(image, region):
    """Find one region's text ink. Returns (X0, Y0, glyphs, interior, ink, cs) for a
    window at (X0, Y0) around the region, or None. glyphs = bool mask of text
    strokes to erase; interior = enclosing balloon interior or None; ink = all ink.

    Text = ink components lying inside a (slightly padded) OCR line box that are not
    long thin lines/loops; everything else is a wall. If the walls close a balloon
    around the text, every small ink mark inside it is text (also lines the OCR
    missed), and the outline itself is never touched."""
    cs = _char_size(region)
    H, W = image.shape[:2]
    rx0, ry0, rx1, ry1 = (int(v) for v in region["rect"])
    p = 4 * cs
    X0, Y0, X1, Y1 = max(0, rx0 - p), max(0, ry0 - p), min(W, rx1 + p), min(H, ry1 + p)
    win = image[Y0:Y1, X0:X1]
    gray = cv2.cvtColor(win, cv2.COLOR_BGR2GRAY) if win.ndim == 3 else win
    rect = (max(0, rx0 - X0), max(0, ry0 - Y0), max(0, rx1 - X0), max(0, ry1 - Y0))
    ref = gray[rect[1]:rect[3], rect[0]:rect[2]]
    if ref.size < 16:
        return None
    ink, faint = _ink(gray, ref)
    n, lab, st, _ = cv2.connectedComponentsWithStats(ink.astype(np.uint8), connectivity=8)
    l, t, w, h, a = (st[:, i] for i in range(5))
    q = max(2, cs // 4)
    text = np.zeros(n, bool)
    boxes = np.zeros(ink.shape, np.uint8)
    for er in region.get("erase_rects") or [region["rect"]]:
        ex0, ey0, ex1, ey1 = er[0] - X0 - q, er[1] - Y0 - q, er[2] - X0 + q, er[3] - Y0 + q
        text |= (l >= ex0) & (t >= ey0) & (l + w <= ex1) & (t + h <= ey1)
        cv2.rectangle(boxes, (int(ex0), int(ey0)), (int(ex1), int(ey1)), 1, -1)
    big = np.maximum(w, h) > 2 * cs
    text &= ~(big & ((np.minimum(w, h) < q) | (a < 0.1 * w * h)))   # lines / loops
    text[0] = False
    glyphs = text[lab]
    # Walls: every non-glyph-sized mark, faint ones included (a thin light outline
    # must still close the balloon); glyph-sized marks incl. text the OCR missed are not.
    nf, labf, stf, _ = cv2.connectedComponentsWithStats(faint.astype(np.uint8), connectivity=8)
    big_f = np.maximum(stf[:, 2], stf[:, 3]) > 1.5 * cs
    # a chain of touching characters stays inside the text box; an outline doesn't
    lf, tf, wf, hf = (stf[:, i] for i in range(4))
    big_f &= ~((lf >= rect[0] - q) & (tf >= rect[1] - q)
               & (lf + wf <= rect[2] + q) & (tf + hf <= rect[3] + q))
    big_f[0] = False
    interior = _balloon(big_f[labf] & ~glyphs, rect, cs)
    if interior is not None:
        # Every ink mark enclosed by the balloon is text — unless something big
        # (a drawing) is in there, i.e. the "balloon" is really a panel: reject.
        inside = np.bincount(lab[interior], minlength=n) == a
        inside[0] = False
        if np.any(inside & ~text & (np.maximum(w, h) > 1.5 * cs)):
            interior = None
        else:
            interior |= _fused_glyphs(ink, interior, cs, boxes.astype(bool))
            glyphs = ink & interior
            # + OCR'd glyphs touching the outline (merged into the wall in the faint
            # mask): they sit in character-sized dents of the interior
            dent = np.bincount(lab[ink & _close(interior, cs)], minlength=n) >= 0.6 * a
            glyphs |= (text & dent & (np.maximum(w, h) <= 1.5 * cs))[lab]
            # complete glyph-sized strokes mostly inside (tips touching the outline)
            part = np.bincount(lab[glyphs], minlength=n) >= 0.8 * a
            part &= np.maximum(w, h) <= 1.5 * cs
            part[0] = False
            glyphs |= part[lab]
    return X0, Y0, glyphs, interior, ink, cs


def _erase(image, regions):
    """Return a BGR copy with the text of `regions` removed.

    Default ("ink" mode): per region, only the text strokes are masked (dilated a
    little for anti-aliasing / JPEG halos), never the balloon outline. On a flat
    background (typical white balloon) the strokes are filled with the local
    background colour; on a textured one they are inpainted locally. A region whose
    ink can't be isolated falls back to the legacy box inpaint."""
    if _erase_mode() == "box":
        mask = np.zeros(image.shape[:2], np.uint8)
        for region in regions:
            for er in region.get("erase_rects", [region["rect"]]):
                cv2.rectangle(mask, (er[0], er[1]), (er[2], er[3]), 255, -1)
        return _apply_inpaint(image, mask) if regions else image.copy()

    out = image.copy()
    legacy = np.zeros(image.shape[:2], np.uint8)
    for region in regions:
        lay = _text_layout(image, region)
        x0, y0, x1, y1 = (int(v) for v in region["rect"])
        area = (x1 - x0) * (y1 - y0) if lay is None or lay[3] is None else lay[3].sum()
        if lay is None or not lay[2].any() or lay[2].sum() > 0.6 * max(1, area):
            for er in region.get("erase_rects", [region["rect"]]):
                cv2.rectangle(legacy, (er[0], er[1]), (er[2], er[3]), 255, -1)
            continue
        X0, Y0, glyphs, interior, ink, cs = lay
        d = max(2, cs // 12)
        m = cv2.dilate(glyphs.astype(np.uint8),
                       cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * d + 1, 2 * d + 1)))
        # never paint over (or right next to) ink that isn't text: outlines, art
        m &= 1 - cv2.dilate((ink & ~glyphs).astype(np.uint8), np.ones((3, 3), np.uint8))
        mb = m.astype(bool)
        clean = ~mb & ~cv2.dilate(ink.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool)
        if interior is not None:
            clean &= interior
        win = out[Y0:Y0 + m.shape[0], X0:X0 + m.shape[1]]
        # local background (normalised blur over clean pixels) + its grain
        k = 2 * cs + 1
        cf = clean.astype(np.float32)
        den = cv2.blur(cf, (k, k))
        local = cv2.blur(win.astype(np.float32) * cf[..., None], (k, k)) / np.maximum(den, 1e-6)[..., None]
        ring = clean & (cv2.dilate(m, np.ones((k, k), np.uint8)) > 0)
        if ring.sum() >= 20 and den[mb].min() > 0.02:
            res = win[ring].astype(np.float32) - local[ring]
            mad = np.median(np.abs(res), axis=0)
            if mad.max() <= _FLAT_BG_MAD:
                # flat: local background colour plus grain drawn from around the
                # text (a plain colour fill shows up as smooth glyph ghosts)
                res = res[(np.abs(res) <= 3 * mad + 3).all(axis=1)]
                grain = res[np.random.default_rng(0).integers(len(res), size=int(mb.sum()))]
                win[mb] = np.clip(local[mb] + grain, 0, 255).astype(np.uint8)
                continue
        win[:] = _apply_inpaint(win, m * 255, dilate=False)    # textured: local inpaint
    if legacy.any():
        out = _apply_inpaint(out, legacy)
    return out


def _balloon_body(interior, rect, cs):
    """The balloon interior without its speech tail: 1920s box balloons are open
    where the tail joins, so the interior runs down the tail and its bounding box
    with it. An opening with a ~1-character square drops anything narrower than a
    character (the tail); keep the piece holding most of the OCR box."""
    k = cv2.getStructuringElement(cv2.MORPH_RECT, (cs | 1, cs | 1))
    body = cv2.morphologyEx(interior.astype(np.uint8), cv2.MORPH_OPEN, k)
    n, lab = cv2.connectedComponents(body, connectivity=4)
    x0, y0, x1, y1 = (max(0, v) for v in rect)
    counts = np.bincount(lab[y0:y1, x0:x1].ravel(), minlength=n)
    counts[0] = 0
    return lab == counts.argmax() if counts.max() > 0 else interior


def _inner_rect(body, mg):
    """An axis-aligned rect (window coords) inside `body` kept >= mg px off its
    edge: the bounding box of the eroded body, its sides moved inward until each
    side lies inside (an outline bulging inward pushes the side in)."""
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * mg + 1, 2 * mg + 1))
    inner = cv2.erode(body.astype(np.uint8), k).astype(bool)
    if not inner.any():
        inner = body
    ys, xs = np.nonzero(inner)
    x0, y0, x1, y1 = int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())
    while x1 - x0 > 8 and y1 - y0 > 8:
        sides = {"l": inner[y0:y1 + 1, x0].mean(), "r": inner[y0:y1 + 1, x1].mean(),
                 "t": inner[y0, x0:x1 + 1].mean(), "b": inner[y1, x0:x1 + 1].mean()}
        worst = min(sides, key=sides.get)
        if sides[worst] >= 0.99:
            break
        if worst == "l":
            x0 += 1
        elif worst == "r":
            x1 -= 1
        elif worst == "t":
            y0 += 1
        else:
            y1 -= 1
    return x0, y0, x1, y1


def _render_rects(image, regions):
    """Where to letter each region's translation: the OCR rect kept inside its
    speech balloon's interior (minus a margin); in a box-like balloon (tight 1920s
    balloons, where the OCR rect pokes out over the outline) spread over the whole
    interior - unless another region shares that balloon (then each keeps its own
    OCR rect, clamped to the interior)."""
    full, own = [], []
    for region in regions:
        rect = [int(v) for v in region["rect"]]
        lay = None if _erase_mode() == "box" else _text_layout(image, region)
        if lay is None or lay[3] is None:
            full.append(rect)
            own.append(rect)
            continue
        X0, Y0, _g, interior, _ink, cs = lay
        interior = _balloon_body(interior, (rect[0] - X0, rect[1] - Y0, rect[2] - X0, rect[3] - Y0), cs)
        ys, xs = np.nonzero(interior)
        x0, y0, x1, y1 = _inner_rect(interior, max(2, cs // 4))
        ib = [X0 + x0, Y0 + y0, X0 + x1, Y0 + y1]
        cl = [max(rect[0], ib[0]), max(rect[1], ib[1]), min(rect[2], ib[2]), min(rect[3], ib[3])]
        cl = cl if cl[2] - cl[0] >= 8 and cl[3] - cl[1] >= 8 else rect
        boxy = interior.sum() >= 0.8 * (np.ptp(xs) + 1) * (np.ptp(ys) + 1)
        full.append(ib if boxy and ib[2] - ib[0] >= 8 and ib[3] - ib[1] >= 8 else cl)
        own.append(cl)

    def overlap(r, o):
        return min(r[2], o[2]) > max(r[0], o[0]) and min(r[3], o[3]) > max(r[1], o[1])
    return [own[i] if any(overlap(f, full[j]) for j in range(len(full)) if j != i) else f
            for i, f in enumerate(full)]


def inpaint_page(image, regions):
    """Return a clean (text-erased) BGR copy with EVERY translatable region removed,
    independent of the current translations. Cacheable: manga proofread re-export
    redraws edited text on this clean page instead of re-running the slow inpaint
    (~70s -> near-instant for a 10-page book)."""
    return _erase(image, [r for r in regions if r.get("needs_translation")])


def render_on_image(image, regions, translations, dst_lang=None, clean_bgr=None):
    """Erase the original text (inpaint) and draw the translations onto a BGR image.

    image: cv2 BGR ndarray. regions: list with rect/needs_translation/erase_rects.
    translations: {count_src: translated}. clean_bgr: an already-inpainted BGR base
    (e.g. a cached clean page) — when given, the slow inpaint step is skipped and
    text is drawn straight onto it. Returns a PIL RGB image. Pure in-memory (no
    disk) so the manga PDF translator can reuse it per page."""
    erase, texts = [], []
    for region in regions:
        translated = translations.get(region["count_src"])
        if not region.get("needs_translation") or not translated:
            continue
        # Text is erased per member text-line (see _erase) and rendered into the
        # region rect, kept inside its speech balloon (see _render_rects).
        erase.append(region)
        texts.append(translated)
    to_render = list(zip(_render_rects(image, erase), texts, erase)) if erase else []

    if clean_bgr is not None:
        image = clean_bgr            # pre-inpainted clean page -> skip the slow step
    elif to_render:
        image = _erase(image, erase)

    # Render translations with PIL (proper CJK shaping)
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    pil_image = Image.fromarray(rgb)
    draw = ImageDraw.Draw(pil_image)
    font_path = _find_font_path()
    if font_path is None:
        app_logger.warning("No CJK-capable font found, falling back to PIL default font")

    cjk_dst = _is_cjk_lang(dst_lang)
    for rect, text, region in to_render:
        x1, y1, x2, y2 = rect
        w, h = x2 - x1, y2 - y1
        # Vertical layout for CJK targets in a tall, narrow box (manga columns).
        vertical = cjk_dst and h > 1.6 * w and h >= 40
        fg, stroke = _text_colors(rgb, rect)
        if vertical:
            _render_vertical(draw, text, rect, font_path, fg, stroke,
                             max_size=int(0.75 * _char_size(region)))  # glyph ~3/4 of an OCR column
        else:
            font, lines, line_h = _fit_text(draw, text, rect, font_path)
            sw = max(1, font.size // 12) if hasattr(font, "size") else 1
            for i, line in enumerate(lines):
                draw.text((x1, y1 + i * line_h), line, font=font, fill=fg,
                          stroke_width=sw, stroke_fill=stroke)
    return pil_image


def write_translated_content_to_image(file_path, original_json_path, translated_json_path,
                                      temp_dir, result_dir, src_lang=None, dst_lang=None):
    """Erase original text regions and render translations in their place.

    Also writes a side-by-side text file (original -> translation) for cases
    where the re-rendered layout is not good enough."""
    filename = os.path.splitext(os.path.basename(file_path))[0]
    extension = os.path.splitext(file_path)[1].lower()
    temp_folder = os.path.join(temp_dir, filename)

    with open(os.path.join(temp_folder, "regions.json"), encoding="utf-8") as f:
        regions = json.load(f)
    with open(translated_json_path, encoding="utf-8") as f:
        translated_data = json.load(f)

    translations = {item["count_src"]: item["translated"] for item in translated_data}

    image = cv2.imdecode(np.fromfile(file_path, dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        # OpenCV can't decode some formats (e.g. GIF); fall back to PIL.
        try:
            pil = Image.open(file_path).convert("RGB")
            image = cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR)
        except Exception as e:
            raise RuntimeError(f"Failed to read image: {file_path} ({e})")

    pil_image = render_on_image(image, regions, translations, dst_lang)

    os.makedirs(result_dir, exist_ok=True)
    lang_suffix = f"{src_lang}2{dst_lang}" if src_lang and dst_lang else "translated"
    result_path = os.path.join(result_dir, f"{filename}_{lang_suffix}{extension}")
    pil_image.save(result_path)

    # Companion text file with original -> translation pairs
    pairs_path = os.path.join(result_dir, f"{filename}_{lang_suffix}.txt")
    with open(pairs_path, "w", encoding="utf-8") as f:
        for region in regions:
            translated = translations.get(region["count_src"], "")
            f.write(f"{region['value']}\n{translated or region['value']}\n\n")

    app_logger.info(f"Translated image saved to: {result_path}")
    return result_path

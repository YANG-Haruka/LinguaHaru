# pipeline/image_translation_pipeline.py
# Image translation: RapidOCR (PP-OCRv5 ONNX models, no paddlepaddle) for
# text detection/recognition, LinguaHaru pipeline for translation, then the
# translated text is rendered back onto an inpainted copy of the image.
#
# Optional module - requires: rapidocr, onnxruntime, opencv-python-headless
import json
import os
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
_OCR_SIZES = ("tiny", "small", "medium")

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


def _ocr_model_size():
    try:
        import json as _json
        from core.paths import SYSTEM_CONFIG
        with open(SYSTEM_CONFIG, encoding="utf-8") as f:
            size = _json.load(f).get("ocr_model_size", "small")
        return size if size in _OCR_SIZES else "small"
    except Exception:  # noqa: BLE001
        return "small"

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


def _get_ocr_engine(src_lang=None):
    """Best available OCR engine, recognition-language aware.

    `src_lang` (the translation source language) auto-selects the recognition
    model: Japanese/Korean/Cyrillic/Latin get their matching model, everything
    else uses the default Chinese+English model. Engines are cached per
    (size, language). If the heavy optional paddleocr package is installed, use
    PP-OCRv6 (paddlepaddle); otherwise the lightweight RapidOCR (PP-OCRv5 ONNX).
    Returns (kind, engine) with kind in {"paddle", "rapid"}."""
    lang = _normalize_ocr_lang(src_lang)            # (rapid_rec, paddle_lang) or None
    size = _ocr_model_size()
    key = (size, lang[0] if lang else None)
    if key in _ocr_engines:
        return _ocr_engines[key]
    with _OCR_LOAD_LOCK:
        if key in _ocr_engines:      # double-check inside the lock
            return _ocr_engines[key]
        return _load_ocr_engine(key, lang, size)


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


def _load_ocr_engine(key, lang, size):
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
        params = {"Det.ocr_version": OCRVersion.PPOCRV5,
                  "Rec.ocr_version": OCRVersion.PPOCRV5}
        if lang:
            params["Rec.lang_type"] = lang[0]
            app_logger.info(f"Loading RapidOCR engine (rec lang={lang[0]})...")
        else:
            app_logger.info("Loading RapidOCR engine (PP-OCRv5)...")
        engine = ("rapid", RapidOCR(params=params))
    _ocr_engines[key] = engine
    return engine


def _run_ocr(file_path, src_lang=None):
    """Run OCR and normalize results to a list of (text, quad_points, score).
    Inference is serialized via GPU_LOCK so concurrent image tasks (or a video
    transcription) don't thrash one GPU/CPU."""
    from core.compute_lock import GPU_LOCK
    kind, engine = _get_ocr_engine(src_lang)
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
    return texts, boxes, scores


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


def ocr_and_group_image(file_path, src_lang=None):
    """OCR an image and return (content_data, regions) WITHOUT touching disk.

    content_data = the translatable entries (count_src/type/value); regions = every
    detected region with rect/score/needs_translation (+ erase_rects/value). In
    manga mode the regions are bubble-grouped. Reused per-page by the manga PDF
    translator; extract_image_content_to_json() wraps this and writes the files."""
    texts, boxes, scores = _run_ocr(file_path, src_lang)
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
    if _manga_mode():
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


def _render_vertical(draw, text, rect, font_path, fg, stroke):
    """Stack characters top->bottom in columns laid out right->left (CJK vertical
    writing). The font size is chosen so the whole block fits the box in BOTH axes
    (the column count * column width <= box width, and chars-per-column * line
    height <= box height), then the block is centered. PIL-only (no HarfBuzz
    vertical glyph substitution, but fine for caption-style overlays)."""
    x1, y1, x2, y2 = rect
    box_w, box_h = max(x2 - x1, 8), max(y2 - y1, 8)
    chars = [c for c in text if c.strip()]
    if not chars:
        return
    line_gap = 1.06
    size = 8
    # A single column can't be wider than the box; shrink until the whole grid fits
    # the box height AND width (this is what prevents the old vertical overflow).
    for s in range(min(box_h, box_w), 7, -1):
        step = s * line_gap
        per_col = max(1, int(box_h // step))
        ncols = (len(chars) + per_col - 1) // per_col
        if ncols * step <= box_w:
            size = s
            break
    step = size * line_gap
    per_col = max(1, int(box_h // step))
    ncols = (len(chars) + per_col - 1) // per_col
    font = (ImageFont.truetype(font_path, size) if font_path else ImageFont.load_default())
    sw = max(1, size // 12)
    # Center the grid in the box; lay columns out right -> left.
    grid_w = ncols * step
    rows_in_first = min(per_col, len(chars))
    grid_h = rows_in_first * step
    x_right = x2 - max(0, (box_w - grid_w)) / 2.0
    y_top = y1 + max(0, (box_h - grid_h)) / 2.0
    for idx, ch in enumerate(chars):
        col, row = divmod(idx, per_col)
        cx = x_right - (col + 1) * step + (step - size) / 2.0
        cy = y_top + row * step
        draw.text((cx, cy), ch, font=font, fill=fg, stroke_width=sw, stroke_fill=stroke)


def _fit_text(draw, text, rect, font_path):
    """Pick a font size and line wrapping so the text fits inside rect."""
    x1, y1, x2, y2 = rect
    box_w, box_h = max(x2 - x1, 8), max(y2 - y1, 8)

    for size in range(box_h, 7, -1):
        font = (ImageFont.truetype(font_path, size) if font_path
                else ImageFont.load_default())
        # Greedy character wrap (works for CJK and degrades fine for Latin)
        lines, line = [], ""
        for ch in text:
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
    return font, [text], 12


def render_on_image(image, regions, translations, dst_lang=None):
    """Erase the original text (inpaint) and draw the translations onto a BGR image.

    image: cv2 BGR ndarray. regions: list with rect/needs_translation/erase_rects.
    translations: {count_src: translated}. Returns a PIL RGB image. Pure in-memory
    (no disk) so the manga PDF translator can reuse it per page."""
    mask = np.zeros(image.shape[:2], np.uint8)
    to_render = []
    for region in regions:
        translated = translations.get(region["count_src"])
        if not region.get("needs_translation") or not translated:
            continue
        # Manga-grouped regions erase each member text-line box (precise); plain
        # regions erase their single rect. Text is rendered into the union rect.
        for er in region.get("erase_rects", [region["rect"]]):
            cv2.rectangle(mask, (er[0], er[1]), (er[2], er[3]), 255, -1)
        to_render.append((region["rect"], translated))

    if to_render:
        k = max(3, int(round(0.005 * max(image.shape[:2]))))   # dilation ~ image scale
        mask = cv2.dilate(mask, np.ones((k, k), np.uint8), iterations=1)
        # Prefer LaMa (clean fill on complex backgrounds) when enabled + its model
        # is installed; otherwise OpenCV Telea (always available).
        lama_out = None
        if _lama_enabled():
            try:
                from core.pipelines.lama_inpaint import inpaint as _lama_inpaint
                lama_out = _lama_inpaint(image, mask)
            except Exception:  # noqa: BLE001
                lama_out = None
        image = lama_out if lama_out is not None else cv2.inpaint(image, mask, max(5, k), cv2.INPAINT_TELEA)
        app_logger.info(f"Inpaint: {'LaMa' if lama_out is not None else 'cv2 Telea'}")

    # Render translations with PIL (proper CJK shaping)
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    pil_image = Image.fromarray(rgb)
    draw = ImageDraw.Draw(pil_image)
    font_path = _find_font_path()
    if font_path is None:
        app_logger.warning("No CJK-capable font found, falling back to PIL default font")

    cjk_dst = _is_cjk_lang(dst_lang)
    for rect, text in to_render:
        x1, y1, x2, y2 = rect
        w, h = x2 - x1, y2 - y1
        # Vertical layout for CJK targets in a tall, narrow box (manga columns).
        vertical = cjk_dst and h > 1.6 * w and h >= 40
        fg, stroke = _text_colors(rgb, rect)
        if vertical:
            _render_vertical(draw, text, rect, font_path, fg, stroke)
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

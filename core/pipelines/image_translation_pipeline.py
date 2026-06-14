# pipeline/image_translation_pipeline.py
# Image translation: RapidOCR (PP-OCRv5 ONNX models, no paddlepaddle) for
# text detection/recognition, LinguaHaru pipeline for translation, then the
# translated text is rendered back onto an inpainted copy of the image.
#
# Optional module - requires: rapidocr, onnxruntime, opencv-python-headless
import json
import os

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
from rapidocr import RapidOCR

from .skip_pipeline import should_translate
from core.log_config import app_logger

_ocr_engine = None

# CJK-capable fonts to try, in order
_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",        # Windows: Microsoft YaHei
    r"C:\Windows\Fonts\meiryo.ttc",      # Windows: Japanese
    r"C:\Windows\Fonts\malgun.ttf",      # Windows: Korean
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/System/Library/Fonts/PingFang.ttc",
]


def _get_ocr_engine():
    """Best available OCR engine.

    If the heavy optional paddleocr package is installed, use PP-OCRv6
    (paddlepaddle runtime); otherwise the lightweight default is RapidOCR
    pinned to PP-OCRv5 ONNX models (v6 has no ONNX conversion yet).
    Returns (kind, engine) with kind in {"paddle", "rapid"}."""
    global _ocr_engine
    if _ocr_engine is None:
        try:
            # DLL load-order fix: paddle's oneDNN/MKL DLLs break ctranslate2
            # (faster-whisper) if paddle loads first; the reverse order works.
            # Pre-import faster_whisper when both optional modules are present.
            try:
                import faster_whisper  # noqa: F401
            except ImportError:
                pass
            from paddleocr import PaddleOCR
            app_logger.info("Loading PaddleOCR engine (PP-OCRv6)...")
            _ocr_engine = ("paddle", PaddleOCR(
                ocr_version="PP-OCRv6",
                use_doc_orientation_classify=False,
                use_doc_unwarping=False,
                use_textline_orientation=True,
                # paddle 3.3.1 oneDNN hits an unimplemented PIR op on
                # Windows CPU (ConvertPirAttribute2RuntimeAttribute)
                enable_mkldnn=False,
            ))
        except Exception as e:
            if not isinstance(e, ImportError):
                app_logger.warning(f"PaddleOCR unavailable ({e}), falling back to RapidOCR")
            app_logger.info("Loading RapidOCR engine (PP-OCRv5)...")
            from rapidocr.utils.typings import OCRVersion
            _ocr_engine = ("rapid", RapidOCR(params={
                "Det.ocr_version": OCRVersion.PPOCRV5,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
            }))
    return _ocr_engine


def _run_ocr(file_path):
    """Run OCR and normalize results to a list of (text, quad_points, score)."""
    kind, engine = _get_ocr_engine()
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


def extract_image_content_to_json(file_path, temp_dir):
    """Run OCR on the image and save recognized text regions to src.json."""
    texts, boxes, scores = _run_ocr(file_path)

    content_data = []
    regions = []
    count = 0

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

        count += 1
        region = {
            "count_src": count,
            "value": text,
            "rect": rect,
            "score": float(score) if score is not None else 0.0,
            "needs_translation": should_translate(text),
        }
        regions.append(region)
        if region["needs_translation"]:
            content_data.append({
                "count_src": count,
                "type": "text",
                "value": text,
            })

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
        raise RuntimeError(f"Failed to read image: {file_path}")

    # Erase translated regions via inpainting
    mask = np.zeros(image.shape[:2], np.uint8)
    to_render = []
    for region in regions:
        translated = translations.get(region["count_src"])
        if not region.get("needs_translation") or not translated:
            continue
        x1, y1, x2, y2 = region["rect"]
        cv2.rectangle(mask, (x1, y1), (x2, y2), 255, -1)
        to_render.append((region["rect"], translated))

    if to_render:
        image = cv2.inpaint(image, mask, 7, cv2.INPAINT_TELEA)

    # Render translations with PIL (proper CJK shaping)
    pil_image = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
    draw = ImageDraw.Draw(pil_image)
    font_path = _find_font_path()
    if font_path is None:
        app_logger.warning("No CJK-capable font found, falling back to PIL default font")

    for rect, text in to_render:
        font, lines, line_h = _fit_text(draw, text, rect, font_path)
        x1, y1, _, _ = rect
        for i, line in enumerate(lines):
            draw.text((x1, y1 + i * line_h), line, font=font, fill=(20, 20, 20))

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

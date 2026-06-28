# Manga mode for PDF: render each page to an image, OCR + bubble-group + translate
# (the image pipeline), render the translation back onto each page, then REPACK the
# pages into a PDF (PDF in -> PDF out). Reuses the base translator's batching /
# history / coverage by emitting ONE combined src.json across all pages.
import io
import json
import os

import cv2
import numpy as np

from core.engine.base_translator import DocumentTranslator
from core.pipelines.image_translation_pipeline import (
    ocr_and_group_image, render_on_image)
from core.log_config import app_logger

_RENDER_DPI = 150   # page raster resolution for OCR + output (good legibility/size)


def _fitz():
    """Lazy import so a no-PDF environment still loads; clear error if missing."""
    try:
        import fitz  # PyMuPDF
        return fitz
    except ImportError as e:
        raise RuntimeError(
            "Manga PDF mode needs PyMuPDF (pymupdf). Install/repair the Image OCR "
            "plugin, or the PDF plugin, then retry."
        ) from e


class MangaPdfTranslator(DocumentTranslator):
    """PDF + 漫画模式. Pages are rasterized, translated as manga images, and
    repacked into a PDF. Proofread/re-export is handled by the backend reading
    manga_pages.json (see reexport_manga_pdf)."""

    EXTRACTION_PROGRESS_SHARE = 0.4   # OCR/raster up front -> 0-40%, translate -> 40-100%

    def extract_content_to_json(self, progress_callback=None):
        """Rasterize every page, OCR+group each, and write ONE combined src.json
        (globally-unique count_src) plus manga_pages.json (per-page image + regions)
        for the render/proofread step."""
        fitz = _fitz()
        pages_dir = os.path.join(self.file_dir, "manga_pages")
        os.makedirs(pages_dir, exist_ok=True)

        doc = fitz.open(self.input_file_path)
        combined, pages_meta, count = [], [], 0
        try:
            n = doc.page_count
            for i in range(n):
                if self.check_stop_requested:
                    self.check_stop_requested()
                pix = doc[i].get_pixmap(dpi=_RENDER_DPI)
                rel = os.path.join("manga_pages", f"page_{i + 1:03d}.png")
                img_path = os.path.join(self.file_dir, rel)
                pix.save(img_path)
                content, regions = ocr_and_group_image(img_path, self.src_lang)
                # Make count_src globally unique across pages.
                id_map = {}
                for r in regions:
                    count += 1
                    id_map[r["count_src"]] = count
                    r["count_src"] = count
                for c in content:
                    combined.append({**c, "count_src": id_map[c["count_src"]]})
                pages_meta.append({
                    "page": i + 1, "image": rel,   # relative to file_dir (re-export safe)
                    "width": pix.width, "height": pix.height, "regions": regions,
                })
                if progress_callback:
                    # progress_callback is base's _extract_cb, which already maps
                    # into [0, EXTRACTION_PROGRESS_SHARE]; pass the raw 0..1 fraction.
                    self.update_ui_safely(
                        progress_callback, (i + 1) / max(n, 1), f"OCR {i + 1}/{n}")
        finally:
            doc.close()

        with open(self.src_json_path, "w", encoding="utf-8") as f:
            json.dump(combined, f, ensure_ascii=False, indent=4)
        with open(os.path.join(self.file_dir, "manga_pages.json"), "w", encoding="utf-8") as f:
            json.dump(pages_meta, f, ensure_ascii=False, indent=4)
        app_logger.info(f"Manga PDF: {len(pages_meta)} pages, {len(combined)} bubbles to translate")
        return self.src_json_path

    def write_translated_json_to_file(self, json_path, translated_json_path, progress_callback=None):
        """Render translations onto each page image and repack into a PDF at the
        path the base translator expects (result_dir/<name>_<src2dst>.pdf)."""
        with open(translated_json_path, encoding="utf-8") as f:
            translations = {it["count_src"]: it["translated"] for it in json.load(f)}
        with open(os.path.join(self.file_dir, "manga_pages.json"), encoding="utf-8") as f:
            pages_meta = json.load(f)

        result_path = render_manga_pages_to_pdf(
            pages_meta, translations, self.file_dir, self.result_dir, self.dst_lang,
            os.path.splitext(os.path.basename(self.input_file_path))[0],
            self.src_lang)
        app_logger.info(f"Manga PDF saved: {result_path}")
        return result_path


def render_manga_pages_to_pdf(pages_meta, translations, file_dir, result_dir,
                              dst_lang, name, src_lang, out_suffix=""):
    """Render translations onto each page image and repack into a PDF. Shared by the
    initial run and proofread re-export. Page-image paths in pages_meta are relative
    to file_dir. Returns the output PDF path
    (result_dir/<name>_<src2dst>[out_suffix].pdf). Proofread re-export passes
    out_suffix="_proofread" so it never clobbers the original translated PDF."""
    fitz = _fitz()
    out = fitz.open()
    try:
        for pm in pages_meta:
            img_path = os.path.join(file_dir, pm["image"])
            img = cv2.imdecode(np.fromfile(img_path, dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is None:
                continue
            pil = render_on_image(img, pm["regions"], translations, dst_lang)
            # Embed pages as JPEG (q88): a lossless PNG of a 150-dpi manga page is
            # ~20MB, ballooning a 10-page PDF to >200MB; JPEG keeps line art crisp
            # at a fraction of the size.
            buf = io.BytesIO()
            pil.convert("RGB").save(buf, format="JPEG", quality=88, optimize=True)
            data = buf.getvalue()
            img_pdf = fitz.open(stream=data, filetype="jpeg")
            rect = img_pdf[0].rect
            page = out.new_page(width=rect.width, height=rect.height)
            page.insert_image(rect, stream=data)
            img_pdf.close()

        os.makedirs(result_dir, exist_ok=True)
        suffix = f"{src_lang}2{dst_lang}" if src_lang and dst_lang else "translated"
        result_path = os.path.join(result_dir, f"{name}_{suffix}{out_suffix}.pdf")
        out.save(result_path)
        return result_path
    finally:
        out.close()

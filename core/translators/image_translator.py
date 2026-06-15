# Optional module: image translation (OCR + render back).
# Requires: pip install -r requirements/ocr.txt
from core.pipelines.image_translation_pipeline import (
    extract_image_content_to_json, write_translated_content_to_image)
from core.engine.base_translator import DocumentTranslator


class ImageTranslator(DocumentTranslator):
    # OCR is the up-front step: map it into 0-50%, translation into 50-100%.
    EXTRACTION_PROGRESS_SHARE = 0.5

    def extract_content_to_json(self, progress_callback=None):
        return extract_image_content_to_json(self.input_file_path, self.temp_dir)

    def write_translated_json_to_file(self, json_path, translated_json_path, progress_callback=None):
        write_translated_content_to_image(
            self.input_file_path, json_path, translated_json_path,
            self.temp_dir, self.result_dir, self.src_lang, self.dst_lang)

# Thin translator classes for the additional formats. All inherit the full
# pipeline (dedupe, splitting, glossary, retry, context) from
# DocumentTranslator and only wire extraction/write-back.
from textProcessing.base_translator import DocumentTranslator

from pipeline.html_translation_pipeline import (
    extract_html_content_to_json, write_translated_content_to_html)
from pipeline.odt_translation_pipeline import (
    extract_odt_content_to_json, write_translated_content_to_odt)
from pipeline.json_translation_pipeline import (
    extract_json_content_to_json, write_translated_content_to_json)
from pipeline.subtitle_formats_pipeline import (
    extract_vtt_content_to_json, write_translated_content_to_vtt,
    extract_ass_content_to_json, write_translated_content_to_ass,
    extract_lrc_content_to_json, write_translated_content_to_lrc)


class _SimpleTranslator(DocumentTranslator):
    _extract = None
    _write = None

    def extract_content_to_json(self, progress_callback=None):
        return type(self)._extract(self.input_file_path, self.temp_dir)

    def write_translated_json_to_file(self, json_path, translated_json_path, progress_callback=None):
        type(self)._write(self.input_file_path, json_path, translated_json_path,
                          self.temp_dir, self.result_dir, self.src_lang, self.dst_lang)


class HtmlTranslator(_SimpleTranslator):
    _extract = staticmethod(extract_html_content_to_json)
    _write = staticmethod(write_translated_content_to_html)


class OdtTranslator(_SimpleTranslator):
    _extract = staticmethod(extract_odt_content_to_json)
    _write = staticmethod(write_translated_content_to_odt)


class JsonTranslator(_SimpleTranslator):
    _extract = staticmethod(extract_json_content_to_json)
    _write = staticmethod(write_translated_content_to_json)


class VttTranslator(_SimpleTranslator):
    _extract = staticmethod(extract_vtt_content_to_json)
    _write = staticmethod(write_translated_content_to_vtt)


class AssTranslator(_SimpleTranslator):
    _extract = staticmethod(extract_ass_content_to_json)
    _write = staticmethod(write_translated_content_to_ass)


class LrcTranslator(_SimpleTranslator):
    _extract = staticmethod(extract_lrc_content_to_json)
    _write = staticmethod(write_translated_content_to_lrc)

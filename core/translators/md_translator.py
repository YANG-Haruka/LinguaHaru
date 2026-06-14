from core.pipelines.md_translation_pipeline import extract_md_content_to_json, write_translated_content_to_md
from core.engine.base_translator import DocumentTranslator


class MdTranslator(DocumentTranslator):
    """Markdown translator. bilingual_mode appends originals as blockquotes."""

    def __init__(self, *args, bilingual_mode=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.bilingual_mode = bilingual_mode

    def extract_content_to_json(self, progress_callback=None):
        return extract_md_content_to_json(self.input_file_path, self.temp_dir)

    def write_translated_json_to_file(self, json_path, translated_json_path, progress_callback=None):
        write_translated_content_to_md(self.input_file_path, json_path, translated_json_path,
                                       self.temp_dir, self.result_dir, self.src_lang, self.dst_lang,
                                       bilingual_mode=self.bilingual_mode)

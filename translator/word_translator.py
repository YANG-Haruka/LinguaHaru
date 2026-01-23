from pipeline.word_translation_pipeline import extract_word_content_to_json, write_translated_content_to_word
from textProcessing.base_translator import DocumentTranslator


class WordTranslator(DocumentTranslator):
    """
    Unified Word translator supporting standard and bilingual modes.

    Args:
        bilingual_mode: If True, format content as bilingual (original + translated).
    """

    def __init__(self, *args, bilingual_mode=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.bilingual_mode = bilingual_mode

    def extract_content_to_json(self, progress_callback=None):
        return extract_word_content_to_json(self.input_file_path, self.temp_dir)

    def write_translated_json_to_file(self, json_path, translated_json_path, progress_callback=None):
        write_translated_content_to_word(
            self.input_file_path,
            json_path,
            translated_json_path,
            self.temp_dir,
            self.result_dir,
            bilingual_mode=self.bilingual_mode,
            src_lang=self.src_lang,
            dst_lang=self.dst_lang
        )

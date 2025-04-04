from pipeline.word_translation_pipeline_bilingual import extract_word_content_to_json, write_translated_content_to_word
from .base_translator import DocumentTranslator

class WordTranslator(DocumentTranslator):
    def extract_content_to_json(self,progress_callback=None):
        return extract_word_content_to_json(self.input_file_path)

    def write_translated_json_to_file(self, json_path, translated_json_path,progress_callback=None):
        write_translated_content_to_word(self.input_file_path, json_path, translated_json_path)

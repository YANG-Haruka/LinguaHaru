from pipeline.word_translation_pipeline import extract_paragraphs_to_json, write_translated_json_to_word
from .base_translator import DocumentTranslator


class TxtTranslator(DocumentTranslator):
    def extract_content_to_json(self):
        return extract_paragraphs_to_json(self.input_file)

    def write_translated_json_to_file(self, json_path, translated_json_path):
        write_translated_json_to_word(self.input_file, json_path, translated_json_path)

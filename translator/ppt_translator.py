from pipeline.ppt_translation_pipeline import extract_ppt_content_to_json, write_translated_content_to_ppt
from .base_translator import DocumentTranslator

class PptTranslator(DocumentTranslator):
    def extract_content_to_json(self):
        return extract_ppt_content_to_json(self.input_file_path)

    def write_translated_json_to_file(self, json_path, translated_json_path):
        write_translated_content_to_ppt(self.input_file_path, json_path, translated_json_path)

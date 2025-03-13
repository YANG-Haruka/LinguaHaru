from pipeline.excel_translation_pipeline import extract_excel_content_to_json, write_translated_content_to_excel
from .base_translator import DocumentTranslator

class ExcelTranslator(DocumentTranslator):
    def extract_content_to_json(self,progress_callback=None):
        return extract_excel_content_to_json(self.input_file_path)

    def write_translated_json_to_file(self, json_path, translated_json_path,progress_callback=None):
        write_translated_content_to_excel(self.input_file_path, json_path, translated_json_path)

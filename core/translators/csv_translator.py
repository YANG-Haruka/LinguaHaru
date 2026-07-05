from core.pipelines.csv_translation_pipeline import (
    extract_csv_content_to_json, write_translated_content_to_csv)
from core.engine.base_translator import DocumentTranslator


class CsvTranslator(DocumentTranslator):
    def extract_content_to_json(self, progress_callback=None):
        return extract_csv_content_to_json(self.input_file_path, self.temp_dir)

    def write_translated_json_to_file(self, json_path, translated_json_path, progress_callback=None):
        write_translated_content_to_csv(
            self.input_file_path, json_path, translated_json_path,
            self.temp_dir, self.result_dir, self.src_lang, self.dst_lang)

from pipeline.excel_translation_pipeline import extract_excel_content_to_json, write_translated_content_to_excel
from textProcessing.base_translator import DocumentTranslator


class ExcelTranslator(DocumentTranslator):
    """
    Unified Excel translator supporting openpyxl, xlwings, and bilingual modes.

    Args:
        use_xlwings: If True, use xlwings for extraction/writing (requires Excel installed).
                     This mode supports shapes, groups, SmartArt, and drawings.
        bilingual_mode: If True, format content as bilingual (original + translated).
                        Only works when use_xlwings=True.
    """

    def __init__(self, *args, use_xlwings=False, bilingual_mode=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.use_xlwings = use_xlwings
        self.bilingual_mode = bilingual_mode

    def extract_content_to_json(self, progress_callback=None):
        return extract_excel_content_to_json(
            self.input_file_path,
            self.temp_dir,
            use_xlwings=self.use_xlwings
        )

    def write_translated_json_to_file(self, json_path, translated_json_path, progress_callback=None):
        write_translated_content_to_excel(
            self.input_file_path,
            json_path,
            translated_json_path,
            self.result_dir,
            use_xlwings=self.use_xlwings,
            bilingual_mode=self.bilingual_mode,
            src_lang=self.src_lang,
            dst_lang=self.dst_lang
        )

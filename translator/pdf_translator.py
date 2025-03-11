from .PDFMathTranslate.high_level import extract_and_translate,write_translated_result
from .PDFMathTranslate.doclayout import OnnxModel
from .PDFMathTranslate.cache import init_db,clean_all_dbs
from .base_translator import DocumentTranslator
from contextlib import contextmanager
from .PDFMathTranslate import shared_constants
import os

model = OnnxModel.load_available()

class PdfTranslator(DocumentTranslator):
    def extract_content_to_json(self,progress_callback=None):
        if progress_callback:
            progress_callback(0, desc="Initializing and extracting PDF content...")
        _,self.cache_folder= init_db(remove_exists=True)

        shared_constants.PDF_FILE_NAME = os.path.splitext(os.path.basename(self.input_file_path))[0]
        temp_folder = os.path.join("temp", shared_constants.PDF_FILE_NAME)
        os.makedirs(self.file_dir, exist_ok=True)

        # translate(files=input_file,model=model,thread=1,lang_in=self.src_lang,lang_out=self.dst_lang,service="google")
        extract_and_translate(input_file=self.input_file_path,model=model,thread=1,lang_in=self.src_lang,lang_out=self.dst_lang,service="google")
        
        return os.path.join(temp_folder,"src.json")
    
    def write_translated_json_to_file(self, json_path, translated_json_path,progress_callback=None):
        if progress_callback:
            progress_callback(0, desc="Preparing to write translated content...")

        write_translated_result(input_file=self.input_file_path,model=model,thread=1,lang_in=self.src_lang,lang_out=self.dst_lang,service="google")
        if progress_callback:
            progress_callback(80, desc="File writing complete, cleaning db...")
        clean_all_dbs(self.cache_folder)

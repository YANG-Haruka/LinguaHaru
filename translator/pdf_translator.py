from .PDFMathTranslate.high_level import translate,extract_and_translate,write_translated_result
from .PDFMathTranslate.doclayout import OnnxModel
from .PDFMathTranslate.cache import init_db,clean_db
from .base_translator import DocumentTranslator
from contextlib import contextmanager

model = OnnxModel.load_available()

class PdfTranslator(DocumentTranslator):
    def extract_content_to_json(self,progress_callback=None):
        if progress_callback:
            progress_callback(0, desc="Initializing and extracting PDF content...")

        init_db(remove_exists=True)
        input_file = [self.input_file_path]
        # translate(files=input_file,model=model,thread=1,lang_in=self.src_lang,lang_out=self.dst_lang,service="google")
        extract_and_translate(input_file=self.input_file_path,model=model,thread=1,lang_in=self.src_lang,lang_out=self.dst_lang,service="google")
        
        return "temp/src.json"
    
    def write_translated_json_to_file(self, json_path, translated_json_path,progress_callback=None):
        if progress_callback:
            progress_callback(0, desc="Preparing to write translated content...")

        write_translated_result(input_file=self.input_file_path,model=model,thread=1,lang_in=self.src_lang,lang_out=self.dst_lang,service="google")
        if progress_callback:
            progress_callback(80, desc="File writing complete, cleaning db...")
        clean_db()

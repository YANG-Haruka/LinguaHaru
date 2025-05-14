import os
import zipfile
import tempfile
import shutil
import gradio as gr
from importlib import import_module
from config.log_config import app_logger, file_logger
from config.languages_config import LABEL_TRANSLATIONS, get_language_code
from .app_config import TRANSLATOR_MODULES, EXCEL_TRANSLATOR_MODE_2, WORD_TRANSLATOR_BILINGUAL
from .app_queue import StopTranslationException, check_stop_requested, reset_stop_flag

def get_translator_class(file_extension, excel_mode_2=False, word_bilingual_mode=False):
    """Dynamically import and return the appropriate translator class for the file extension."""
    if file_extension.lower() == ".xlsx" and excel_mode_2:
        module_path = EXCEL_TRANSLATOR_MODE_2
    elif file_extension.lower() == ".docx" and word_bilingual_mode:
        module_path = WORD_TRANSLATOR_BILINGUAL
    else:
        module_path = TRANSLATOR_MODULES.get(file_extension.lower())
    
    if not module_path:
        return None
    
    try:
        # Split into module path and class name
        module_name, class_name = module_path.rsplit('.', 1)
        
        # Import the module
        module = import_module(module_name)
        
        # Get the class
        translator_class = getattr(module, class_name)
        return translator_class
    except (ImportError, AttributeError) as e:
        app_logger.exception(f"Error importing translator for {file_extension}: {e}")
        return None

def translate_files(
    files, model, src_lang, dst_lang, use_online, api_key, max_retries=4, max_token=768, thread_count=4,
    excel_mode_2=False, word_bilingual_mode=False, session_lang="en", continue_mode=False, progress=gr.Progress(track_tqdm=True)
):
    """Translate one or multiple files using the chosen model."""
    reset_stop_flag()  # Reset stop flag at the beginning
    
    labels = LABEL_TRANSLATIONS.get(session_lang, LABEL_TRANSLATIONS["en"])
    stop_text = labels.get("Stop Translation", "Stop Translation")
    
    if not files:
        return gr.update(value=None, visible=False), "Please select file(s) to translate.", gr.update(value=stop_text, interactive=False)

    if use_online and not api_key:
        return gr.update(value=None, visible=False), "API key is required for online models.", gr.update(value=stop_text, interactive=False)

    src_lang_code = get_language_code(src_lang)
    dst_lang_code = get_language_code(dst_lang)

    # Common progress callback function
    def progress_callback(progress_value, desc=None):
        if check_stop_requested():
            raise StopTranslationException("Translation stopped by user")
        progress(progress_value, desc=desc)

    try:
        # Check if multiple files or single file
        if isinstance(files, list) and len(files) > 1:
            result = process_multiple_files(
                files, model, src_lang_code, dst_lang_code, 
                use_online, api_key, max_token, max_retries, thread_count, excel_mode_2, word_bilingual_mode, continue_mode, progress_callback
            )
        else:
            # Handle single file case
            single_file = files[0] if isinstance(files, list) else files
            result = process_single_file(
                single_file, model, src_lang_code, dst_lang_code, 
                use_online, api_key, max_token, max_retries, thread_count, excel_mode_2, word_bilingual_mode, continue_mode, progress_callback
            )
        
        return result[0], result[1], gr.update(value=stop_text, interactive=False)
        
    except StopTranslationException:
        return gr.update(value=None, visible=False), "Translation stopped by user.", gr.update(value=stop_text, interactive=False)
    except Exception as e:
        return gr.update(value=None, visible=False), f"Error: {str(e)}", gr.update(value=stop_text, interactive=False)

def process_single_file(
    file, model, src_lang_code, dst_lang_code, 
    use_online, api_key, max_token, max_retries, thread_count, excel_mode_2, word_bilingual_mode, continue_mode, progress_callback
):
    """Process a single file for translation."""
    file_name = os.path.basename(file.name)
    
    # Create a new log file for this file
    file_logger.create_file_log(file_name)
    
    app_logger.info(f"Processing file: {file_name}")
    app_logger.info(f"Source language: {src_lang_code}, Target language: {dst_lang_code}, Model: {model}")
    
    file_name, file_extension = os.path.splitext(file.name)
    
    translator_class = get_translator_class(file_extension, excel_mode_2, word_bilingual_mode)

    if not translator_class:
        return (
            gr.update(value=None, visible=False),
            f"Unsupported file type '{file_extension}'."
        )

    try:
        # Pass check_stop_requested function to translator
        translator = translator_class(
            file.name, model, use_online, api_key,
            src_lang_code, dst_lang_code, continue_mode, max_token=max_token, max_retries=max_retries,
            thread_count=thread_count
        )
        
        # Add check_stop_requested as an attribute
        translator.check_stop_requested = check_stop_requested
        
        progress_callback(0, desc="Initializing translation...")

        translated_file_path, missing_counts = translator.process(
            file_name, file_extension, progress_callback=progress_callback
        )
        progress_callback(1, desc="Done!")

        if missing_counts:
            msg = f"Warning: Missing segments for keys: {sorted(missing_counts)}"
            return gr.update(value=translated_file_path, visible=True), msg

        return gr.update(value=translated_file_path, visible=True), "Translation complete."
    
    except StopTranslationException:
        app_logger.info("Translation stopped by user")
        return gr.update(value=None, visible=False), "Translation stopped by user."
    except ValueError as e:
        return gr.update(value=None, visible=False), f"Translation failed: {str(e)}"
    except Exception as e:
        app_logger.exception("Error processing file")
        return gr.update(value=None, visible=False), f"Error: {str(e)}"
    
def process_multiple_files(
    files, model, src_lang_code, dst_lang_code, 
    use_online, api_key, max_token, max_retries, thread_count, excel_mode_2, word_bilingual_mode, continue_mode, progress_callback
):
    """Process multiple files and return a zip archive."""
    # Create a temporary directory for the translated files
    temp_dir = tempfile.mkdtemp(prefix="translated_")
    zip_path = os.path.join(temp_dir, "translated_files.zip")
    
    try:
        valid_files = []
        
        # Validate all files
        for file_obj in files:
            _, ext = os.path.splitext(file_obj.name)
            if get_translator_class(ext, excel_mode_2, word_bilingual_mode):
                file_name = os.path.basename(file_obj.name)
                valid_files.append((file_obj, file_name))
        
        if not valid_files:
            shutil.rmtree(temp_dir)
            return gr.update(value=None, visible=False), "No supported files found."
        
        # Create a zip file
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            total_files = len(valid_files)
            
            for i, (file_obj, rel_path) in enumerate(valid_files):
                # Create a new log file for the current file being processed
                file_logger.create_file_log(rel_path)
                
                app_logger.info(f"Processing file {i+1}/{total_files}: {rel_path}")
                
                file_name, file_extension = os.path.splitext(file_obj.name)
                base_name = os.path.basename(file_name)
                
                # Update progress with initial file info
                progress_callback(i / total_files, desc=f"Starting to process {rel_path} (File {i+1}/{total_files})")
                
                # Create translator for this file
                translator_class = get_translator_class(file_extension, excel_mode_2, word_bilingual_mode)
                if not translator_class:
                    continue  # Skip unsupported files (should not happen due to earlier validation)
                
                try:
                    # Process file
                    translator = translator_class(
                        file_obj.name, model, use_online, api_key,
                        src_lang_code, dst_lang_code, continue_mode, max_token=max_token, max_retries=max_retries,
                        thread_count=thread_count
                    )
                    
                    # Create output directory
                    output_dir = os.path.join(temp_dir, "files")
                    os.makedirs(output_dir, exist_ok=True)
                    
                    # Create progress callback that shows individual file progress and overall position
                    def file_progress(value, desc=None):
                        file_desc = desc if desc else ""
                        overall_info = f" (File {i+1}/{total_files})"
                        progress_callback(i / total_files + value / total_files, desc=f"{file_desc}{overall_info}")
                    
                    translated_file_path, _ = translator.process(
                        os.path.join(output_dir, base_name),
                        file_extension,
                        progress_callback=file_progress
                    )
                    
                    # Add to zip
                    zipf.write(
                        translated_file_path, 
                        os.path.basename(translated_file_path)
                    )
                except Exception as e:
                    app_logger.exception(f"Error processing file {rel_path}: {e}")
                    # Continue with next file
        
        progress_callback(1, desc="Done!")
        return gr.update(value=zip_path, visible=True), f"Translation completed. {total_files} files processed."
    
    except Exception as e:
        app_logger.exception("Error processing files")
        shutil.rmtree(temp_dir)
        return gr.update(value=None, visible=False), f"Error processing files: {str(e)}"
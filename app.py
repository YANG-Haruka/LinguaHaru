import gradio as gr
import os
import zipfile
import tempfile
import shutil
import json
from importlib import import_module
from llmWrapper.offline_translation import populate_sum_model
from typing import List, Tuple
from config.log_config import app_logger
import socket
import sys
import base64
import threading
import queue
from functools import partial

# Import language configs
from config.languages_config import LABEL_TRANSLATIONS, get_available_languages, get_language_code, add_custom_language
#-------------------------------------------------------------------------
# Constants and Configuration
#-------------------------------------------------------------------------

# Dictionary mapping file extensions to their corresponding translator module paths
TRANSLATOR_MODULES = {
    ".docx": "translator.word_translator.WordTranslator",
    ".pptx": "translator.ppt_translator.PptTranslator",
    ".xlsx": "translator.excel_translator.ExcelTranslator",
    ".pdf": "translator.pdf_translator.PdfTranslator",
    ".srt": "translator.subtile_translator.SubtitlesTranslator",
    ".txt": "translator.txt_translator.TxtTranslator",
    ".md": "translator.md_translator.MdTranslator",
    # ".epub": "translator.epub_translator.EpubTranslator"
}

# Alternative Excel translator module path (Mode 2)
EXCEL_TRANSLATOR_MODE_2 = "translator.excel_translator_test.ExcelTranslator"
WORD_TRANSLATOR_BILINGUAL = "translator.word_translator_bilingual.WordTranslator"

# Global task queue and counter
task_queue = queue.Queue()
active_tasks = 0
task_lock = threading.Lock()

# Global variables for stop functionality
translation_stop_requested = False
current_translation_task = None
stop_lock = threading.Lock()

def enqueue_task(
    translate_func, files, model, src_lang, dst_lang, 
    use_online, api_key, max_retries, max_token, thread_count, excel_mode_2, word_bilingual_mode, glossary_name, session_lang, progress
):
    """Enqueue a translation task or execute it immediately if no tasks are running."""
    global active_tasks
    
    with task_lock:
        if active_tasks == 0:
            # No active tasks, start immediately
            active_tasks += 1
            # Return None to indicate the task should start immediately
            return None
        else:
            # Tasks are running, add to queue
            task_info = {
                "files": files,
                "model": model,
                "src_lang": src_lang,
                "dst_lang": dst_lang,
                "use_online": use_online,
                "api_key": api_key,
                "max_retries": max_retries,
                "max_token": max_token,
                "thread_count": thread_count,
                "excel_mode_2": excel_mode_2,
                "word_bilingual_mode": word_bilingual_mode,
                "glossary_name": glossary_name,
                "session_lang": session_lang
            }
            task_queue.put(task_info)
            queue_position = task_queue.qsize()
            return f"Task added to queue. Position: {queue_position}"

def process_task_with_queue(
    translate_func, files, model, src_lang, dst_lang, 
    use_online, api_key, max_retries, max_token, thread_count, excel_mode_2, word_bilingual_mode, glossary_name, session_lang, progress
):
    """Process a translation task and handle queue management."""
    global active_tasks
    if progress is None:
        progress = gr.Progress(track_tqdm=True)
    
    queue_msg = enqueue_task(
        translate_func, files, model, src_lang, dst_lang, 
        use_online, api_key, max_retries, max_token, thread_count, excel_mode_2, word_bilingual_mode, glossary_name, session_lang, progress
    )

    labels = LABEL_TRANSLATIONS.get(session_lang, LABEL_TRANSLATIONS["en"])
    stop_text = labels.get("Stop Translation", "Stop Translation")
    
    if queue_msg:
        return gr.update(value=None, visible=False), queue_msg, gr.update(value=stop_text, interactive=False)
    
    try:
        result = translate_func(
            files, model, src_lang, dst_lang, 
            use_online, api_key, max_retries, max_token, thread_count, excel_mode_2, word_bilingual_mode, glossary_name, session_lang, progress
        )
        process_next_task_in_queue(translate_func, progress)
        
        return result[0], result[1], result[2]
    except Exception as e:
        with task_lock:
            active_tasks -= 1
        process_next_task_in_queue(translate_func, progress)
        return gr.update(value=None, visible=False), f"Error: {str(e)}", gr.update(value=stop_text, interactive=False)

def process_next_task_in_queue(translate_func, progress):
    """Process the next task in the queue if available."""
    global active_tasks
    
    with task_lock:
        active_tasks -= 1
        
        if not task_queue.empty():
            next_task = task_queue.get()
            active_tasks += 1
            threading.Thread(
                target=process_queued_task,
                args=(translate_func, next_task, progress),
                daemon=True
            ).start()

def process_queued_task(translate_func, task_info, progress):
    """Process a task from the queue in a separate thread."""
    try:
        if progress is None:
            progress = gr.Progress(track_tqdm=True)
        result = translate_func(
            task_info["files"],
            task_info["model"],
            task_info["src_lang"],
            task_info["dst_lang"],
            task_info["use_online"],
            task_info["api_key"],
            task_info["max_retries"],
            task_info["max_token"],
            task_info["thread_count"],
            task_info["excel_mode_2"],
            task_info["word_bilingual_mode"],
            task_info["glossary_name"],
            task_info.get("session_lang", "en"),
            progress
        )    
    except Exception as e:
        app_logger.exception(f"Error processing queued task: {e}")
    finally:
        process_next_task_in_queue(translate_func, progress)

class StopTranslationException(Exception):
    """Custom exception for when translation is stopped by user"""
    pass

def request_stop_translation(session_lang):
    """Request to stop the current translation."""
    global translation_stop_requested
    
    labels = LABEL_TRANSLATIONS.get(session_lang, LABEL_TRANSLATIONS["en"])
    stopping_text = labels.get("Stopping", "Stopping...")
    
    with stop_lock:
        translation_stop_requested = True
    
    return gr.update(value=stopping_text, interactive=False)

def reset_stop_flag():
    """Reset the stop flag for new translations."""
    global translation_stop_requested
    
    with stop_lock:
        translation_stop_requested = False

def check_stop_requested():
    """Check if stop has been requested."""
    with stop_lock:
        if translation_stop_requested:
            raise StopTranslationException("Translation stopped by user")
        return False

def modified_translate_button_click(
    translate_files_func, files, model, src_lang, dst_lang, 
    use_online, api_key, max_retries, max_token, thread_count, excel_mode_2, word_bilingual_mode, glossary_name,
    session_lang, continue_mode=False, progress=gr.Progress(track_tqdm=True)
):
    """Modified version of the translate button click handler that uses the task queue."""
    global current_translation_task
    
    labels = LABEL_TRANSLATIONS.get(session_lang, LABEL_TRANSLATIONS["en"])
    stop_text = labels.get("Stop Translation", "Stop Translation")
    
    # Reset the UI and stop flag
    output_file_update = gr.update(visible=False)
    status_message = None
    reset_stop_flag()
    
    if not files:
        return output_file_update, "Please select file(s) to translate.", gr.update(value=stop_text, interactive=False)
    
    if use_online and not api_key:
        return output_file_update, "API key is required for online models.", gr.update(value=stop_text, interactive=False)
    
    def wrapped_translate_func(files, model, src_lang, dst_lang, 
                              use_online, api_key, max_retries, max_token, thread_count,
                              excel_mode_2, word_bilingual_mode, glossary_name, session_lang, progress):
        return translate_files_func(files, model, src_lang, dst_lang, 
                                   use_online, api_key, max_retries, max_token, thread_count,
                                   excel_mode_2, word_bilingual_mode, glossary_name, session_lang,
                                   continue_mode=continue_mode, progress=progress)
    
    return process_task_with_queue(
        wrapped_translate_func, files, model, src_lang, dst_lang, 
        use_online, api_key, max_retries, max_token, thread_count, excel_mode_2, word_bilingual_mode, glossary_name, session_lang, progress
    )

def check_temp_translation_exists(files):
    """Check if temporary translation folders exist for any of the input files in the 'temp' directory."""
    if not files:
        return False, "No files selected."
    
    # Ensure temp directory exists
    temp_base_dir = "temp"
    os.makedirs(temp_base_dir, exist_ok=True)
    
    found_folders = []
    
    for file_obj in files:
        # Get filename without extension
        filename = os.path.splitext(os.path.basename(file_obj.name))[0]
        
        # Look for exact matching folder in the temp directory
        temp_folder = os.path.join(temp_base_dir, filename)
        
        if os.path.exists(temp_folder) and os.path.isdir(temp_folder):
            found_folders.append(temp_folder)
    
    if found_folders:
        return True, f"Found {len(found_folders)} existing translation folders."
    else:
        return False, "No existing translations found."

#-------------------------------------------------------------------------
# System Configuration Functions
#-------------------------------------------------------------------------

def read_system_config():
    """Read the system configuration from the config file."""
    config_path = os.path.join("config", "system_config.json")
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "lan_mode": False,
            "default_online": False,
            "max_token": MAX_TOKEN,
            "show_model_selection": True,
            "show_mode_switch": True,
            "show_lan_mode": True,
            "show_max_retries": True,
            "show_thread_count": True,
            "excel_mode_2": False,
            "word_bilingual_mode": False,
            "default_thread_count_online": 2,
            "default_thread_count_offline": 4,
            "default_src_lang": "English",
            "default_dst_lang": "English"
        }

def write_system_config(config):
    """Write the system configuration to the config file."""
    config_path = os.path.join("config", "system_config.json")
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=4, ensure_ascii=False)

def update_lan_mode(lan_mode):
    """Update system config with new LAN mode setting."""
    config = read_system_config()
    config["lan_mode"] = lan_mode
    write_system_config(config)
    return config["lan_mode"]

def update_online_mode(use_online):
    """Update system config with new online mode setting."""
    config = read_system_config()
    config["default_online"] = use_online
    write_system_config(config)
    return config["default_online"]

def update_max_retries(max_retries):
    """Update system config with new max retries setting."""
    config = read_system_config()
    config["max_retries"] = max_retries
    write_system_config(config)
    return max_retries

def update_thread_count(thread_count):
    """Update system config with new thread count setting."""
    config = read_system_config()
    # Update the appropriate thread count based on the current mode
    if config.get("default_online", False):
        config["default_thread_count_online"] = thread_count
    else:
        config["default_thread_count_offline"] = thread_count
    write_system_config(config)
    return thread_count

def update_excel_mode(excel_mode_2):
    """Update system config with new Excel mode setting."""
    config = read_system_config()
    config["excel_mode_2"] = excel_mode_2
    write_system_config(config)
    return excel_mode_2

def update_word_bilingual_mode(word_bilingual_mode):
    """Update system config with new Word bilingual mode setting."""
    config = read_system_config()
    config["word_bilingual_mode"] = word_bilingual_mode
    write_system_config(config)
    return word_bilingual_mode

def update_language_preferences(src_lang=None, dst_lang=None):
    """Update system config with new language preferences."""
    config = read_system_config()
    
    if src_lang is not None:
        config["default_src_lang"] = src_lang
    if dst_lang is not None:
        config["default_dst_lang"] = dst_lang
        
    write_system_config(config)
    return config.get("default_src_lang"), config.get("default_dst_lang")

def get_default_languages():
    """Get default source and target languages from config."""
    config = read_system_config()
    default_src = config.get("default_src_lang", "English")
    default_dst = config.get("default_dst_lang", "English")
    return default_src, default_dst

def update_language_preferences(src_lang=None, dst_lang=None):
    """Update system config with new language preferences."""
    config = read_system_config()
    
    if src_lang is not None:
        config["default_src_lang"] = src_lang
    if dst_lang is not None:
        config["default_dst_lang"] = dst_lang
        
    write_system_config(config)
    return config.get("default_src_lang"), config.get("default_dst_lang")

def on_src_language_change(src_lang):
    """Handler for source language dropdown change."""
    if src_lang != CUSTOM_LABEL:
        update_language_preferences(src_lang=src_lang)
    
    # Return UI updates for custom language controls
    if src_lang == CUSTOM_LABEL:
        return gr.update(visible=True), gr.update(visible=True)
    else:
        return gr.update(visible=False), gr.update(visible=False)

def on_dst_language_change(dst_lang):
    """Handler for target language dropdown change."""
    if dst_lang != CUSTOM_LABEL:
        update_language_preferences(dst_lang=dst_lang)
    
    # Return UI updates for custom language controls
    if dst_lang == CUSTOM_LABEL:
        return gr.update(visible=True), gr.update(visible=True)
    else:
        return gr.update(visible=False), gr.update(visible=False)

def find_available_port(start_port=9980, max_attempts=20):
    """Find an available port starting from `start_port`."""
    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", port))
                return port
        except OSError:
            continue
    raise RuntimeError("No available port found.")

def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller"""
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

def load_application_icon(config):
    """Load the application icon using img_path from system_config.json."""
    # Get icon path from config
    img_path = config.get("img_path", "img/ico.ico")
    
    # Define MIME types for different image formats
    mime_types = {
        'ico': 'image/x-icon',
        'png': 'image/png',
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'gif': 'image/gif',
        'svg': 'image/svg+xml'
    }
    
    # Paths to try in order
    icon_paths_to_try = []
    
    # 1. Try absolute path if img_path is absolute
    if os.path.isabs(img_path):
        icon_paths_to_try.append(img_path)
    
    # 2. Try from current directory
    if not os.path.isabs(img_path):
        icon_paths_to_try.append(img_path)
    
    # 3. Try from PyInstaller _MEIPASS
    try:
        # PyInstaller creates a temp folder and stores path in _MEIPASS
        base_path = sys._MEIPASS
        # If img_path is not absolute, add it to _MEIPASS path
        if not os.path.isabs(img_path):
            meipass_path = os.path.join(base_path, img_path)
            icon_paths_to_try.append(meipass_path)
    except Exception:
        # Not running from PyInstaller bundle
        pass
    
    # 4. Add default img/ico.ico as last resort (if not already in the list)
    default_icon = "img/ico.ico"
    if img_path != default_icon:
        # Try from current directory
        if default_icon not in icon_paths_to_try:
            icon_paths_to_try.append(default_icon)
        
        # Try from _MEIPASS
        try:
            base_path = sys._MEIPASS
            default_meipass_path = os.path.join(base_path, default_icon)
            if default_meipass_path not in icon_paths_to_try:
                icon_paths_to_try.append(default_meipass_path)
        except Exception:
            pass
    
    # Try each path in order
    for icon_path in icon_paths_to_try:
        try:
            if os.path.isfile(icon_path):
                image_type = icon_path.split('.')[-1].lower()
                mime_type = mime_types.get(image_type, 'image/png')
                
                app_logger.info(f"Loading icon from: {icon_path}")
                with open(icon_path, "rb") as f:
                    encoded_image = base64.b64encode(f.read()).decode("utf-8")
                return encoded_image, mime_type
        except Exception as e:
            app_logger.warning(f"Failed to load icon from {icon_path}: {e}")
            # Try next path
    
    # If all else fails, log an error
    app_logger.error("Failed to load any icon, application will run without an icon")
    return None, None

#-------------------------------------------------------------------------
# Glossary Management Functions
#-------------------------------------------------------------------------

def get_glossary_files():
    """Get all CSV files from the glossary directory."""
    glossary_dir = "glossary"
    
    # Ensure glossary directory exists
    os.makedirs(glossary_dir, exist_ok=True)
    
    # Check if Default.csv exists, create if not
    default_glossary = os.path.join(glossary_dir, "Default.csv")
    if not os.path.exists(default_glossary):
        # Create a basic default glossary file
        with open(default_glossary, 'w', encoding='utf-8', newline='') as f:
            import csv
            writer = csv.writer(f)
            writer.writerow(["Source", "Target", "Notes"])  # Header row
            writer.writerow(["Example", "示例", "Sample entry"])  # Sample entry
    
    # Get all CSV files
    try:
        csv_files = [f for f in os.listdir(glossary_dir) if f.endswith('.csv')]
        # Sort files with Default.csv first
        csv_files.sort(key=lambda x: (x != 'Default.csv', x.lower()))
        return [os.path.splitext(f)[0] for f in csv_files]  # Return without .csv extension
    except Exception as e:
        app_logger.warning(f"Error reading glossary directory: {e}")
        return ["Default"]

def update_glossary_selection(glossary_name):
    """Update system config with selected glossary."""
    config = read_system_config()
    config["default_glossary"] = glossary_name
    write_system_config(config)
    return glossary_name

def get_default_glossary():
    """Get default glossary from config."""
    config = read_system_config()
    return config.get("default_glossary", "Default")

def upload_glossary_file(file_obj, session_lang):
    """Handle glossary file upload."""
    if not file_obj:
        labels = LABEL_TRANSLATIONS.get(session_lang, LABEL_TRANSLATIONS["en"])
        return gr.update(), labels.get("No file selected", "No file selected."), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False)
    
    glossary_dir = "glossary"
    os.makedirs(glossary_dir, exist_ok=True)
    
    try:
        # Get the original filename
        original_name = os.path.basename(file_obj.name)
        
        # Check if it's a CSV file
        if not original_name.lower().endswith('.csv'):
            labels = LABEL_TRANSLATIONS.get(session_lang, LABEL_TRANSLATIONS["en"])
            return gr.update(), labels.get("Only CSV files are allowed", "Only CSV files are allowed."), gr.update(visible=False), gr.update(visible=False), gr.update(visible=False)
        
        # Copy file to glossary directory
        dest_path = os.path.join(glossary_dir, original_name)
        
        # If file already exists, add a number suffix
        counter = 1
        base_name, ext = os.path.splitext(original_name)
        while os.path.exists(dest_path):
            new_name = f"{base_name}_{counter}{ext}"
            dest_path = os.path.join(glossary_dir, new_name)
            counter += 1
        
        # Copy the file
        import shutil
        shutil.copy2(file_obj.name, dest_path)
        
        # Update glossary choices
        updated_choices = get_glossary_files() + ["+"]
        new_glossary_name = os.path.splitext(os.path.basename(dest_path))[0]
        
        # Update config with new glossary
        update_glossary_selection(new_glossary_name)
        
        labels = LABEL_TRANSLATIONS.get(session_lang, LABEL_TRANSLATIONS["en"])
        success_msg = labels.get("Glossary uploaded successfully", "Glossary uploaded successfully") + f": {new_glossary_name}"
        
        return gr.update(choices=updated_choices, value=new_glossary_name), success_msg, gr.update(visible=False), gr.update(visible=False), gr.update(visible=False)
        
    except Exception as e:
        app_logger.exception(f"Error uploading glossary file: {e}")
        labels = LABEL_TRANSLATIONS.get(session_lang, LABEL_TRANSLATIONS["en"])
        error_msg = labels.get("Error uploading file", "Error uploading file") + f": {str(e)}"
        return gr.update(), error_msg, gr.update(visible=False), gr.update(visible=False), gr.update(visible=False)

def on_glossary_change(glossary_value, session_lang):
    """Handle glossary selection change."""
    if glossary_value == "+":
        # Show file upload dialog
        return gr.update(visible=True), gr.update(visible=True), gr.update(visible=True)
    else:
        # Update config and hide upload controls
        if glossary_value:
            update_glossary_selection(glossary_value)
        return gr.update(visible=False), gr.update(visible=False), gr.update(visible=False)
    
#-------------------------------------------------------------------------
# Language and Localization Functions
#-------------------------------------------------------------------------

def parse_accept_language(accept_language: str) -> List[Tuple[str, float]]:
    """Parse Accept-Language into (language, q) pairs."""
    if not accept_language:
        return []
    
    languages = []
    for item in accept_language.split(','):
        item = item.strip()
        if not item:
            continue
        if ';q=' in item:
            lang, q = item.split(';q=')
            q = float(q)
        else:
            lang = item
            q = 1.0
        languages.append((lang, q))
    
    return sorted(languages, key=lambda x: x[1], reverse=True)

def get_user_lang(request: gr.Request) -> str:
    """Return the top user language code that matches LANGUAGE_MAP."""
    accept_lang = request.headers.get("accept-language", "").lower()
    parsed = parse_accept_language(accept_lang)
    
    if not parsed:
        return "en"
    
    highest_lang, _ = parsed[0]
    highest_lang = highest_lang.lower()

    if highest_lang.startswith("ja"):
        return "ja"
    elif highest_lang.startswith(("zh-tw", "zh-hk", "zh-hant")):
        return "zh-Hant"
    elif highest_lang.startswith(("zh-cn", "zh-hans", "zh")):
        return "zh"
    elif highest_lang.startswith("es"):
        return "es"
    elif highest_lang.startswith("fr"):
        return "fr"
    elif highest_lang.startswith("de"):
        return "de"
    elif highest_lang.startswith("it"):
        return "it"
    elif highest_lang.startswith("pt"):
        return "pt"
    elif highest_lang.startswith("ru"):
        return "ru"
    elif highest_lang.startswith("ko"):
        return "ko"
    elif highest_lang.startswith("th"):
        return "th"
    elif highest_lang.startswith("vi"):
        return "vi"
    elif highest_lang.startswith("en"):
        return "en"

    return "en"

def set_labels(session_lang: str):
    """Update UI labels according to the chosen language."""
    labels = LABEL_TRANSLATIONS.get(session_lang, LABEL_TRANSLATIONS["en"])
    
    file_upload_label = "Upload Files"
    if "Upload Files" in labels:
        file_upload_label = labels["Upload Files"]
    elif "Upload File" in labels:
        file_upload_label = labels["Upload File"] + "s"
    
    return {
        src_lang: gr.update(label=labels["Source Language"]),
        dst_lang: gr.update(label=labels["Target Language"]),
        use_online_model: gr.update(label=labels["Use Online Model"]),
        lan_mode_checkbox: gr.update(label=labels["Local Network Mode (Restart to Apply)"]),
        model_choice: gr.update(label=labels["Models"]),
        glossary_choice: gr.update(label=labels.get("Glossary", "Glossary")),  # Add glossary label
        max_retries_slider: gr.update(label=labels["Max Retries"]),
        thread_count_slider: gr.update(label=labels["Thread Count"]),
        api_key_input: gr.update(label=labels["API Key"]),
        file_input: gr.update(label=file_upload_label),
        output_file: gr.update(label=labels["Download Translated File"]),
        status_message: gr.update(label=labels["Status Message"]),
        translate_button: gr.update(value=labels["Translate"]),
        continue_button: gr.update(value=labels["Continue Translation"]),
        excel_mode_checkbox: gr.update(label=labels.get("Excel Mode", "Excel Mode")),
        word_bilingual_checkbox: gr.update(label=labels.get("Word Bilingual", "Word Bilingual")),
        stop_button: gr.update(value=labels.get("Stop Translation", "Stop Translation")),
        glossary_upload_button: gr.update(value=labels.get("Upload Glossary", "Upload Glossary"))
    }

#-------------------------------------------------------------------------
# UI and Model Functions
#-------------------------------------------------------------------------

def update_model_list_and_api_input(use_online):
    """Switch model options and show/hide API Key, also update the config."""
    # Update the system config with the new online mode
    update_online_mode(use_online)
    config = read_system_config()
    
    # Get appropriate thread count based on the mode
    thread_count = config.get("default_thread_count_online", 2) if use_online else config.get("default_thread_count_offline", 4)
    
    if use_online:
        if default_online_model and default_online_model in online_models:
            default_online_value = default_online_model
        else:
            default_online_value = online_models[0] if online_models else None
        return (
            gr.update(choices=online_models, value=default_online_value),
            gr.update(visible=True, value=""),
            gr.update(value=thread_count)
        )
    else:
        if default_local_model and default_local_model in local_models:
            default_local_value = default_local_model
        else:
            default_local_value = local_models[0] if local_models else None
        return (
            gr.update(choices=local_models, value=default_local_value),
            gr.update(visible=False, value=""),
            gr.update(value=thread_count)
        )

def init_ui(request: gr.Request):
    """Set user language and update labels on page load."""
    user_lang = get_user_lang(request)
    config = read_system_config()
    
    lan_mode_state = config.get("lan_mode", False)
    default_online_state = config.get("default_online", False)
    max_token_state = config.get("max_token", MAX_TOKEN)
    excel_mode_2_state = config.get("excel_mode_2", False)
    word_bilingual_mode_state = config.get("word_bilingual_mode", False)
    # Always use default 4 for max retries
    max_retries_state = 4
    
    # Get thread count based on mode
    thread_count_state = config.get("default_thread_count_online", 2) if default_online_state else config.get("default_thread_count_offline", 4)
    
    # Get visibility settings
    show_max_retries = config.get("show_max_retries", True)
    show_thread_count = config.get("show_thread_count", True)
    default_src_lang, default_dst_lang = get_default_languages()
    
    # Get default glossary
    default_glossary = get_default_glossary()
    glossary_choices = get_glossary_files() + ["+"]
    
    # Update use_online_model checkbox based on default_online setting
    use_online_value = default_online_state
    
    # Update model choices based on online/offline mode
    if use_online_value:
        model_choices = online_models
        if default_online_model and default_online_model in online_models:
            model_value = default_online_model
        else:
            model_value = online_models[0] if online_models else None
    else:
        model_choices = local_models
        if default_local_model and default_local_model in local_models:
            model_value = default_local_model
        else:
            model_value = local_models[0] if local_models else None
    
    label_updates = set_labels(user_lang)
    
    # Add visibility updates for max_retries and thread_count
    label_updates[max_retries_slider] = gr.update(label=LABEL_TRANSLATIONS.get(user_lang, LABEL_TRANSLATIONS["en"])["Max Retries"], visible=show_max_retries)
    label_updates[thread_count_slider] = gr.update(label=LABEL_TRANSLATIONS.get(user_lang, LABEL_TRANSLATIONS["en"])["Thread Count"], visible=show_thread_count)
    
    # Prepare return values - now INCLUDING glossary and upload controls
    label_values = list(label_updates.values())
    
    # Return settings values and UI updates (now WITH glossary components)
    return [
        user_lang, 
        lan_mode_state, 
        default_online_state,
        max_token_state,
        max_retries_state,
        excel_mode_2_state,
        word_bilingual_mode_state,
        thread_count_state,
        use_online_value,
        gr.update(choices=model_choices, value=model_value),  # model_choice update
        gr.update(choices=glossary_choices, value=default_glossary),  # glossary_choice update
        gr.update(visible=False),  # glossary_upload_file (initially hidden)
        gr.update(visible=False)   # glossary_upload_button (initially hidden)
    ] + label_values

def get_default_dropdown_value(saved_lang, dropdown_choices):
    """Get the appropriate default value for language dropdowns."""

    if saved_lang in dropdown_choices:
        return saved_lang
    return saved_lang

def show_mode_checkbox(files):
    """Show Excel mode checkbox if Excel files are present and Word bilingual checkbox if Word files are present."""
    if not files:
        return gr.update(visible=False), gr.update(visible=False)
    
    # Check if at least one Excel file is present
    excel_files = [f for f in files if os.path.splitext(f.name)[1].lower() == ".xlsx"]
    excel_visible = bool(excel_files)
    
    # Check if at least one Word file is present
    word_files = [f for f in files if os.path.splitext(f.name)[1].lower() == ".docx"]
    word_visible = bool(word_files)
    
    return gr.update(visible=excel_visible), gr.update(visible=word_visible)

def update_continue_button(files):
    """Check if temp folders exist for the uploaded files and update the continue button state."""
    if not files:
        return gr.update(interactive=False)
    
    # If multiple files are selected, disable the continue button
    if isinstance(files, list) and len(files) > 1:
        return gr.update(interactive=False)
    
    # Check if the single file is a PDF
    single_file = files[0] if isinstance(files, list) else files
    file_extension = os.path.splitext(single_file.name)[1].lower()
    
    # Disable continue button for PDF files
    if file_extension == ".pdf":
        return gr.update(interactive=False)
    
    # Only check for temp folders if a single non-PDF file is selected
    has_temp, _ = check_temp_translation_exists(files)
    return gr.update(interactive=has_temp)

#-------------------------------------------------------------------------
# Translation Processing Functions
#-------------------------------------------------------------------------

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
    excel_mode_2=False, word_bilingual_mode=False, glossary_name="Default", session_lang="en", continue_mode=False, progress=gr.Progress(track_tqdm=True)
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
    
    # Convert glossary name to full path
    glossary_path = os.path.join("glossary", f"{glossary_name}.csv") if glossary_name else None

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
                use_online, api_key, max_token, max_retries, thread_count, excel_mode_2, word_bilingual_mode, glossary_path, continue_mode, progress_callback
            )
        else:
            # Handle single file case
            single_file = files[0] if isinstance(files, list) else files
            result = process_single_file(
                single_file, model, src_lang_code, dst_lang_code, 
                use_online, api_key, max_token, max_retries, thread_count, excel_mode_2, word_bilingual_mode, glossary_path, continue_mode, progress_callback
            )
        
        return result[0], result[1], gr.update(value=stop_text, interactive=False)
        
    except StopTranslationException:
        return gr.update(value=None, visible=False), "Translation stopped by user.", gr.update(value=stop_text, interactive=False)
    except Exception as e:
        return gr.update(value=None, visible=False), f"Error: {str(e)}", gr.update(value=stop_text, interactive=False)

def process_single_file(
    file, model, src_lang_code, dst_lang_code, 
    use_online, api_key, max_token, max_retries, thread_count, excel_mode_2, word_bilingual_mode, glossary_path, continue_mode, progress_callback
):
    """Process a single file for translation."""
    file_name = os.path.basename(file.name)
    
    # Create a new log file for this file
    from config.log_config import file_logger
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
        # Pass check_stop_requested function to translator with glossary_path
        translator = translator_class(
            file.name, model, use_online, api_key,
            src_lang_code, dst_lang_code, continue_mode, 
            max_token=max_token, max_retries=max_retries,
            thread_count=thread_count, glossary_path=glossary_path
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
    use_online, api_key, max_token, max_retries, thread_count, excel_mode_2, word_bilingual_mode, glossary_path, continue_mode, progress_callback
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
                from config.log_config import file_logger
                file_logger.create_file_log(rel_path)
                
                app_logger.info(f"Processing file {i+1}/{total_files}: {rel_path}")
                
                file_name, file_extension = os.path.splitext(file_obj.name)
                base_name = os.path.basename(file_name)
                
                # Update progress with initial file info
                progress_callback(i / total_files, desc=f"Starting to process {rel_path} (File {i+1}/{total_files})")
                
                # Create translator for this file, passing excel_mode_2 parameter
                translator_class = get_translator_class(file_extension, excel_mode_2, word_bilingual_mode)
                if not translator_class:
                    continue  # Skip unsupported files (should not happen due to earlier validation)
                
                try:
                    # Process file with glossary_path
                    translator = translator_class(
                        file_obj.name, model, use_online, api_key,
                        src_lang_code, dst_lang_code, continue_mode, max_token=max_token, max_retries=max_retries,
                        thread_count=thread_count, glossary_path=glossary_path
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

#-------------------------------------------------------------------------
# Main Application Initialization
#-------------------------------------------------------------------------

# Load local and online models
local_models = populate_sum_model() or []
CUSTOM_LABEL = "+ Add Custom…"
dropdown_choices = get_available_languages() + [CUSTOM_LABEL]
config_dir = "config/api_config"
online_models = [
    os.path.splitext(f)[0] for f in os.listdir(config_dir) 
    if f.endswith(".json") and f != "Custom.json"
]

# Read initial configuration
config = read_system_config()
initial_lan_mode = config.get("lan_mode", False)
initial_default_online = config.get("default_online", False)
initial_max_token = config.get("max_token", 768)
initial_max_retries = config.get("max_retries", 4)
initial_excel_mode_2 = config.get("excel_mode_2", False)
initial_word_bilingual_mode = config.get("word_bilingual_mode", False)
initial_thread_count_online = config.get("default_thread_count_online", 2)
initial_thread_count_offline = config.get("default_thread_count_offline", 4)
initial_thread_count = initial_thread_count_online if initial_default_online else initial_thread_count_offline
app_title = config.get("app_title", "LinguaHaru")
app_title_web = "LinguaHaru" if app_title == "" else app_title
img_path = config.get("img_path", "img/ico.png")
img_height = config.get("img_height", 250)

# Update global MAX_TOKEN from config
MAX_TOKEN = initial_max_token

# Get show_model_selection and show_mode_switch from config
initial_show_model_selection = config.get("show_model_selection", True)
initial_show_mode_switch = config.get("show_mode_switch", True)
initial_show_lan_mode = config.get("show_lan_mode", True)
initial_show_max_retries = config.get("show_max_retries", True)
initial_show_thread_count = config.get("show_thread_count", True)
default_local_model = config.get("default_local_model", "")
default_online_model = config.get("default_online_model", "")

encoded_image, mime_type = load_application_icon(config)

#-------------------------------------------------------------------------
# Gradio UI Construction
#-------------------------------------------------------------------------

# Create a Gradio blocks interface
with gr.Blocks(
    title=app_title_web,
    css="""
    footer { visibility: hidden; }

    /* Language row */
    #lang-row {
        display: grid !important;
        grid-template-columns: 1fr auto 1fr !important;
        align-items: center !important;
        gap: 8px !important;
        margin-bottom: 20px;
    }

    #lang-row .gr-dropdown:first-child {
        grid-column: 1 !important;
    }

    #swap-btn {
        grid-column: 2 !important;
        width: 42px !important;
        height: 42px !important;
        justify-self: center !important;
    }

    #lang-row .gr-dropdown:last-child {
        grid-column: 3 !important;
    }

    /* Model and Glossary row */
    #model-glossary-row {
        display: grid !important;
        grid-template-columns: 1fr 1fr !important;
        gap: 10px !important;
    }
    """
) as demo:
    gr.HTML(f"""
    <div style="text-align: center;">
        <h1>{app_title}</h1>
        <img src="data:{mime_type};base64,{encoded_image}" alt="{app_title} Logo" 
                style="display: block; height: {img_height}px; width: auto; margin: 0 auto;">
    </div>
    """)
    
    # Custom footer with attribution and GitHub link
    gr.HTML("""
    <div style="position: fixed; bottom: 0; left: 0; width: 100%; 
                text-align: center; padding: 10px 0;">
        Made by Haruka-YANG | Version: 3.2 | 
        <a href="https://github.com/YANG-Haruka/LinguaHaru" target="_blank">Visit Github</a>
    </div>
    """)
    
    session_lang = gr.State("en")
    lan_mode_state = gr.State(initial_lan_mode)
    default_online_state = gr.State(initial_default_online)
    max_token_state = gr.State(initial_max_token)
    max_retries_state = gr.State(initial_max_retries)
    excel_mode_2_state = gr.State(initial_excel_mode_2)
    word_bilingual_mode_state = gr.State(initial_word_bilingual_mode)
    thread_count_state = gr.State(initial_thread_count)

    default_src_lang, default_dst_lang = get_default_languages()

    with gr.Row(elem_id="lang-row"):
        src_lang = gr.Dropdown(
            choices=dropdown_choices,
            label="Source Language",
            value=default_src_lang,
            interactive=True,
            allow_custom_value=True
        )
        swap_button = gr.Button(
            "🔁",
            elem_id="swap-btn",
            elem_classes="swap-button"
        )
        dst_lang = gr.Dropdown(
            choices=dropdown_choices,
            label="Target Language",
            value=default_dst_lang,
            interactive=True,
            allow_custom_value=True
        )
        # Hidden controls for custom-language entry
        custom_lang_input = gr.Textbox(
            label="New language display name",
            placeholder="e.g. Klingon",
            visible=False
        )
        add_lang_button = gr.Button("Create New Language", visible=False)

    # Settings section (always visible)
    with gr.Row():
        with gr.Column(scale=1):
            use_online_model = gr.Checkbox(
                label="Use Online Model", 
                value=initial_default_online, 
                visible=initial_show_mode_switch
            )
        
        with gr.Column(scale=1):
            lan_mode_checkbox = gr.Checkbox(
                label="Local Network Mode (Restart to Apply)", 
                value=initial_lan_mode,
                visible=initial_show_lan_mode
            )
    
    with gr.Row():
        with gr.Column(scale=1):
            max_retries_slider = gr.Slider(
                minimum=1,
                maximum=10,
                step=1,
                value=initial_max_retries,
                label="Max Retries",
                visible=initial_show_max_retries
            )
        
        with gr.Column(scale=1):
            thread_count_slider = gr.Slider(
                minimum=1,
                maximum=16,
                step=1,
                value=initial_thread_count,
                label="Thread Count",
                visible=initial_show_thread_count
            )
    
    with gr.Row():
        excel_mode_checkbox = gr.Checkbox(
            label="Use Excel Mode 2", 
            value=initial_excel_mode_2, 
            visible=False
        )
        
    word_bilingual_checkbox = gr.Checkbox(
        label="Use Word Bilingual Mode", 
        value=initial_word_bilingual_mode, 
        visible=False
    )

    # Model and Glossary selection (NEW: Side by side)
    with gr.Row():
        with gr.Column(scale=1):
            model_choice = gr.Dropdown(
                choices=local_models if not initial_default_online else online_models,
                label="Models",
                value=local_models[0] if not initial_default_online and local_models else (
                    online_models[0] if initial_default_online and online_models else None
                ),
                visible=initial_show_model_selection,
                allow_custom_value=True 
            )
        
        with gr.Column(scale=1):
            # Glossary selection dropdown
            glossary_choice = gr.Dropdown(
                choices=get_glossary_files() + ["+"],
                label="Glossary",
                value=get_default_glossary(),
                interactive=True
            )

    # Hidden glossary upload controls
    with gr.Row() as glossary_upload_row:
        with gr.Column():
            glossary_upload_file = gr.File(
                label="Upload Glossary CSV",
                file_types=[".csv"],
                visible=False
            )
            glossary_upload_button = gr.Button("Upload Glossary", visible=False)

    api_key_input = gr.Textbox(
        label="API Key", 
        placeholder="Enter your API key here", 
        value="",
        visible=initial_default_online
    )
    
    file_input = gr.File(
        label="Upload Files (.docx, .pptx, .xlsx, .pdf, .srt, .txt, .md)",
        file_types=[".docx", ".pptx", ".xlsx", ".pdf", ".srt", ".txt", ".md"],
        file_count="multiple"
    )
    output_file = gr.File(label="Download Translated File", visible=False)
    status_message = gr.Textbox(label="Status Message", interactive=False, visible=True)

    with gr.Row():
        translate_button = gr.Button("Translate")
        continue_button = gr.Button("Continue Translation", interactive=False)  # Initially disabled
        stop_button = gr.Button("Stop Translation", interactive=False)  # Initially disabled

    # Event handlers
    use_online_model.change(
        update_model_list_and_api_input,
        inputs=use_online_model,
        outputs=[model_choice, api_key_input, thread_count_slider]
    )
    
    # Add LAN mode
    lan_mode_checkbox.change(
        update_lan_mode,
        inputs=lan_mode_checkbox,
        outputs=lan_mode_state
    )
    
    # Add Max Retries
    max_retries_slider.change(
        update_max_retries,
        inputs=max_retries_slider,
        outputs=max_retries_state
    )
    
    # Add Thread Count
    thread_count_slider.change(
        update_thread_count,
        inputs=thread_count_slider,
        outputs=thread_count_state
    )

    excel_mode_checkbox.change(
        update_excel_mode,
        inputs=excel_mode_checkbox,
        outputs=excel_mode_2_state
    )

    word_bilingual_checkbox.change(
        update_word_bilingual_mode,
        inputs=word_bilingual_checkbox,
        outputs=word_bilingual_mode_state
    )
    
    file_input.change(
        fn=lambda files: [show_mode_checkbox(files)[0], 
                        show_mode_checkbox(files)[1], 
                        update_continue_button(files)],
        inputs=file_input,
        outputs=[excel_mode_checkbox, word_bilingual_checkbox, continue_button]
    )

    # Glossary event handlers
    glossary_choice.change(
        on_glossary_change,
        inputs=[glossary_choice, session_lang],
        outputs=[glossary_upload_row, glossary_upload_file, glossary_upload_button]
    )

    glossary_upload_button.click(
        upload_glossary_file,
        inputs=[glossary_upload_file, session_lang],
        outputs=[glossary_choice, status_message, glossary_upload_row, glossary_upload_file, glossary_upload_button]
    )

    # Update event handlers for translate button (existing code remains the same)
    translate_button.click(
        lambda: (gr.update(visible=False), None, gr.update(interactive=False), gr.update(interactive=False), gr.update(interactive=True)),
        inputs=[],
        outputs=[output_file, status_message, translate_button, continue_button, stop_button]
    ).then(
        partial(modified_translate_button_click, translate_files),
        inputs=[
            file_input, model_choice, src_lang, dst_lang, 
            use_online_model, api_key_input, max_retries_slider, max_token_state,
            thread_count_slider, excel_mode_checkbox, word_bilingual_checkbox, glossary_choice, session_lang
        ],
        outputs=[output_file, status_message, stop_button]
    ).then(
        lambda session_lang: (
            gr.update(interactive=True), 
            gr.update(interactive=True), 
            gr.update(value=LABEL_TRANSLATIONS.get(session_lang, LABEL_TRANSLATIONS["en"]).get("Stop Translation", "Stop Translation"), interactive=False)
        ),
        inputs=[session_lang],
        outputs=[translate_button, continue_button, stop_button]
    )

    # In the continue_button.click event:
    continue_button.click(
        lambda: (gr.update(visible=False), None, gr.update(interactive=False), gr.update(interactive=False), gr.update(interactive=True)),
        inputs=[],
        outputs=[output_file, status_message, translate_button, continue_button, stop_button]
    ).then(
        partial(modified_translate_button_click, translate_files, continue_mode=True),
        inputs=[
            file_input, model_choice, src_lang, dst_lang, 
            use_online_model, api_key_input, max_retries_slider, max_token_state,
            thread_count_slider, excel_mode_checkbox, word_bilingual_checkbox, glossary_choice, session_lang
        ],
        outputs=[output_file, status_message, stop_button]
    ).then(
        lambda session_lang: (
            gr.update(interactive=True), 
            gr.update(interactive=True), 
            gr.update(value=LABEL_TRANSLATIONS.get(session_lang, LABEL_TRANSLATIONS["en"]).get("Stop Translation", "Stop Translation"), interactive=False)
        ),
        inputs=[session_lang],
        outputs=[translate_button, continue_button, stop_button]
    )

    # Update stop button handler to pass session_lang:
    stop_button.click(
        request_stop_translation,
        inputs=[session_lang],
        outputs=[stop_button]
    )

    # Existing language handlers...
    def swap_languages(src_lang, dst_lang):
        """Swap source and target languages."""        
        # Update preferences with swapped values
        update_language_preferences(src_lang=dst_lang, dst_lang=src_lang)
        
        # Return swapped values
        return dst_lang, src_lang
    
    def on_dropdown_change(val):
        if val == CUSTOM_LABEL:
            return gr.update(visible=True), gr.update(visible=True)
        else:
            return gr.update(visible=False), gr.update(visible=False)

    # Replace these event handlers:
    src_lang.change(on_src_language_change, inputs=src_lang, outputs=[custom_lang_input, add_lang_button])
    dst_lang.change(on_dst_language_change, inputs=dst_lang, outputs=[custom_lang_input, add_lang_button])
    swap_button.click(swap_languages, inputs=[src_lang, dst_lang], outputs=[src_lang, dst_lang])

    # 2) Create New Language
    def on_add_new(lang_name):
        success, msg = add_custom_language(lang_name)
        new_choices = get_available_languages() + [CUSTOM_LABEL]
        # pick the newly created language as the selected value
        new_val = lang_name if success else CUSTOM_LABEL
        return (
            gr.update(choices=new_choices, value=new_val),
            gr.update(choices=new_choices, value=new_val),
            gr.update(visible=False),
            gr.update(visible=False)
        )

    add_lang_button.click(
        on_add_new,
        inputs=[custom_lang_input],
        outputs=[src_lang, dst_lang, custom_lang_input, add_lang_button]
    )

    # On page load, set user language and labels (UPDATED to include glossary components)
    demo.load(
        fn=init_ui,
        inputs=None,
        outputs=[
            session_lang, lan_mode_state, default_online_state, max_token_state, max_retries_state,
            excel_mode_2_state, word_bilingual_mode_state, thread_count_state,
            use_online_model, model_choice, glossary_choice, glossary_upload_file, glossary_upload_button,
            src_lang, dst_lang, use_online_model, lan_mode_checkbox,
            model_choice, glossary_choice, max_retries_slider, thread_count_slider,
            api_key_input, file_input, output_file, status_message, translate_button,
            continue_button, excel_mode_checkbox, word_bilingual_checkbox, stop_button, glossary_upload_button
        ]
    )

#-------------------------------------------------------------------------
# Application Launch
#-------------------------------------------------------------------------

available_port = find_available_port(start_port=9980)

if initial_lan_mode:
    demo.launch(server_name="0.0.0.0", server_port=available_port, share=False, inbrowser=True)
else:
    demo.launch(server_port=available_port, share=False, inbrowser=True)
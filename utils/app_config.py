import os
import json
import socket
import sys
import base64
from config.log_config import app_logger

# Constants
TRANSLATOR_MODULES = {
    ".docx": "translator.word_translator.WordTranslator",
    ".pptx": "translator.ppt_translator.PptTranslator",
    ".xlsx": "translator.excel_translator.ExcelTranslator",
    ".pdf": "translator.pdf_translator.PdfTranslator",
    ".srt": "translator.subtile_translator.SubtitlesTranslator",
    ".txt": "translator.txt_translator.TxtTranslator",
    ".md": "translator.md_translator.MdTranslator",
}

EXCEL_TRANSLATOR_MODE_2 = "translator.excel_translator_test.ExcelTranslator"
WORD_TRANSLATOR_BILINGUAL = "translator.word_translator_bilingual.WordTranslator"

# Constants
TRANSLATOR_MODULES = {
    ".docx": "translator.word_translator.WordTranslator",
    ".pptx": "translator.ppt_translator.PptTranslator",
    ".xlsx": "translator.excel_translator.ExcelTranslator",
    ".pdf": "translator.pdf_translator.PdfTranslator",
    ".srt": "translator.subtile_translator.SubtitlesTranslator",
    ".txt": "translator.txt_translator.TxtTranslator",
    ".md": "translator.md_translator.MdTranslator",
}

EXCEL_TRANSLATOR_MODE_2 = "translator.excel_translator_test.ExcelTranslator"
WORD_TRANSLATOR_BILINGUAL = "translator.word_translator_bilingual.WordTranslator"

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
            "max_token": 768,
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

def load_application_icon(config):
    """Load the application icon using img_path from system_config.json."""
    img_path = config.get("img_path", "img/ico.ico")
    
    mime_types = {
        'ico': 'image/x-icon',
        'png': 'image/png',
        'jpg': 'image/jpeg',
        'jpeg': 'image/jpeg',
        'gif': 'image/gif',
        'svg': 'image/svg+xml'
    }
    
    icon_paths_to_try = []
    
    if os.path.isabs(img_path):
        icon_paths_to_try.append(img_path)
    
    if not os.path.isabs(img_path):
        icon_paths_to_try.append(img_path)
    
    try:
        base_path = sys._MEIPASS
        if not os.path.isabs(img_path):
            meipass_path = os.path.join(base_path, img_path)
            icon_paths_to_try.append(meipass_path)
    except Exception:
        pass
    
    default_icon = "img/ico.ico"
    if img_path != default_icon:
        if default_icon not in icon_paths_to_try:
            icon_paths_to_try.append(default_icon)
        
        try:
            base_path = sys._MEIPASS
            default_meipass_path = os.path.join(base_path, default_icon)
            if default_meipass_path not in icon_paths_to_try:
                icon_paths_to_try.append(default_meipass_path)
        except Exception:
            pass
    
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
    
    app_logger.error("Failed to load any icon, application will run without an icon")
    return None, None
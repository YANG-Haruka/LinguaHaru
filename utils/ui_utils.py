import gradio as gr
import os
from typing import List, Tuple
from config.log_config import app_logger
from config.languages_config import LABEL_TRANSLATIONS, get_available_languages, add_custom_language
from llmWrapper.offline_translation import populate_sum_model

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

    language_map = {
        "ja": "ja",
        "zh-tw": "zh-Hant", "zh-hk": "zh-Hant", "zh-hant": "zh-Hant",
        "zh-cn": "zh", "zh-hans": "zh", "zh": "zh",
        "es": "es", "fr": "fr", "de": "de", "it": "it",
        "pt": "pt", "ru": "ru", "ko": "ko", "th": "th",
        "vi": "vi", "en": "en"
    }
    
    for prefix, lang_code in language_map.items():
        if highest_lang.startswith(prefix):
            return lang_code
    
    return "en"

def set_labels(session_lang: str, ui_components):
    """Update UI labels according to the chosen language."""
    labels = LABEL_TRANSLATIONS.get(session_lang, LABEL_TRANSLATIONS["en"])
    
    file_upload_label = "Upload Files"
    if "Upload Files" in labels:
        file_upload_label = labels["Upload Files"]
    elif "Upload File" in labels:
        file_upload_label = labels["Upload File"] + "s"
    
    return {
        'src_lang': gr.update(label=labels["Source Language"]),
        'dst_lang': gr.update(label=labels["Target Language"]),
        'use_online_model': gr.update(label=labels["Use Online Model"]),
        'lan_mode_checkbox': gr.update(label=labels["Local Network Mode (Restart to Apply)"]),
        'model_choice': gr.update(label=labels["Models"]),
        'max_retries_slider': gr.update(label=labels["Max Retries"]),
        'thread_count_slider': gr.update(label=labels["Thread Count"]),
        'api_key_input': gr.update(label=labels["API Key"]),
        'file_input': gr.update(label=file_upload_label),
        'output_file': gr.update(label=labels["Download Translated File"]),
        'status_message': gr.update(label=labels["Status Message"]),
        'translate_button': gr.update(value=labels["Translate"]),
        'continue_button': gr.update(value=labels["Continue Translation"]),
        'excel_mode_checkbox': gr.update(label=labels.get("Excel Mode", "Excel Mode")),
        'word_bilingual_checkbox': gr.update(label=labels.get("Word Bilingual", "Word Bilingual")),
        'stop_button': gr.update(value=labels.get("Stop Translation", "Stop Translation"))
    }

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

def update_model_list_and_api_input(use_online, config):
    """Switch model options and show/hide API Key, also update the config."""
    from .app_config import update_online_mode, read_system_config
    
    # Update the system config with the new online mode
    update_online_mode(use_online)
    config = read_system_config()
    
    # Get appropriate thread count based on the mode
    thread_count = config.get("default_thread_count_online", 2) if use_online else config.get("default_thread_count_offline", 4)
    
    # Load models from configuration
    local_models = populate_sum_model() or []
    
    config_dir = "config/api_config"
    online_models = [
        os.path.splitext(f)[0] for f in os.listdir(config_dir) 
        if f.endswith(".json") and f != "Custom.json"
    ]
    
    default_local_model = config.get("default_local_model", "")
    default_online_model = config.get("default_online_model", "")
    
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

def on_src_language_change(src_lang, CUSTOM_LABEL):
    """Handler for source language dropdown change."""
    from .app_config import update_language_preferences
    
    if src_lang != CUSTOM_LABEL:
        update_language_preferences(src_lang=src_lang)
    
    # Return UI updates for custom language controls
    if src_lang == CUSTOM_LABEL:
        return gr.update(visible=True), gr.update(visible=True)
    else:
        return gr.update(visible=False), gr.update(visible=False)

def on_dst_language_change(dst_lang, CUSTOM_LABEL):
    """Handler for target language dropdown change."""
    from .app_config import update_language_preferences
    
    if dst_lang != CUSTOM_LABEL:
        update_language_preferences(dst_lang=dst_lang)
    
    # Return UI updates for custom language controls
    if dst_lang == CUSTOM_LABEL:
        return gr.update(visible=True), gr.update(visible=True)
    else:
        return gr.update(visible=False), gr.update(visible=False)

def on_add_new(lang_name, CUSTOM_LABEL):
    """Create New Language"""
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

def swap_languages(src_lang, dst_lang):
    """Swap source and target languages."""
    from .app_config import update_language_preferences
    
    # Update preferences with swapped values
    update_language_preferences(src_lang=dst_lang, dst_lang=src_lang)
    
    # Return swapped values
    return dst_lang, src_lang
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

# Import language configs
from config.languages_config import LANGUAGE_MAP, LABEL_TRANSLATIONS

# Dictionary mapping file extensions to their corresponding translator module paths
TRANSLATOR_MODULES = {
    ".docx": "translator.word_translator.WordTranslator",
    ".pptx": "translator.ppt_translator.PptTranslator",
    ".xlsx": "translator.excel_translator.ExcelTranslator",
    ".pdf": "translator.pdf_translator.PdfTranslator",
    ".srt": "translator.subtile_translator.SubtitlesTranslator",
    ".txt": "translator.txt_translator.TxtTranslator",
    # ".epub": "translator.epub_translator.EpubTranslator"
}

MAX_TOKEN = 768

def read_system_config():
    """Read the system configuration from the config file."""
    config_path = os.path.join("config", "system_config.json")
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"lan_mode": False}

def write_system_config(config):
    """Write the system configuration to the config file."""
    config_path = os.path.join("config", "system_config.json")
    os.makedirs(os.path.dirname(config_path), exist_ok=True)
    with open(config_path, 'w') as f:
        json.dump(config, f, indent=4)

def find_available_port(start_port=9980, max_attempts=20):
    """Find an available port starting from `start_port`. Try up to `max_attempts`."""
    for port in range(start_port, start_port + max_attempts):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    raise RuntimeError("No available port found.")

# Dynamic import function to load translator class only when needed
def get_translator_class(file_extension):
    """Dynamically import and return the appropriate translator class for the file extension."""
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

# Main translation function
def translate_files(
    files, model, src_lang, dst_lang, use_online, api_key, max_retries=4,
    progress=gr.Progress(track_tqdm=True)
):
    """Translate one or multiple files using the chosen model."""
    if not files:
        return gr.update(value=None, visible=False), "Please select file(s) to translate."

    if use_online and not api_key:
        return gr.update(value=None, visible=False), "API key is required for online models."

    src_lang_code = LANGUAGE_MAP.get(src_lang, "en")
    dst_lang_code = LANGUAGE_MAP.get(dst_lang, "en")

    # Common progress callback function
    def progress_callback(progress_value, desc=None):
        progress(progress_value, desc=desc)

    # Check if multiple files or single file
    if isinstance(files, list) and len(files) > 1:
        return process_multiple_files(
            files, model, src_lang_code, dst_lang_code, 
            use_online, api_key, MAX_TOKEN, max_retries, progress_callback
        )
    else:
        # Handle single file case
        single_file = files[0] if isinstance(files, list) else files
        return process_single_file(
            single_file, model, src_lang_code, dst_lang_code, 
            use_online, api_key, MAX_TOKEN, max_retries, progress_callback
        )

def process_single_file(
    file, model, src_lang_code, dst_lang_code, 
    use_online, api_key, max_token, max_retries, progress_callback
):
    """Process a single file for translation."""
    file_name, file_extension = os.path.splitext(file.name)
    translator_class = get_translator_class(file_extension)

    if not translator_class:
        return (
            gr.update(value=None, visible=False),
            f"Unsupported file type '{file_extension}'."
        )

    try:
        translator = translator_class(
            file.name, model, use_online, api_key,
            src_lang_code, dst_lang_code, max_token=max_token, max_retries=max_retries
        )
        progress_callback(0, desc="Initializing translation...")

        translated_file_path, missing_counts = translator.process(
            file_name, file_extension, progress_callback=progress_callback
        )
        progress_callback(1, desc="Done!")

        if missing_counts:
            msg = f"Warning: Missing segments for keys: {sorted(missing_counts)}"
            return gr.update(value=translated_file_path, visible=True), msg

        return gr.update(value=translated_file_path, visible=True), "Translation complete."
    except ValueError as e:
        return gr.update(value=None, visible=False), f"Translation failed: {str(e)}"
    except Exception as e:
        app_logger.exception("Error processing file")
        return gr.update(value=None, visible=False), f"Error: {str(e)}"

def process_multiple_files(
    files, model, src_lang_code, dst_lang_code, 
    use_online, api_key, max_token, max_retries, progress_callback
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
            if get_translator_class(ext):
                # Use filename as relative path
                file_name = os.path.basename(file_obj.name)
                valid_files.append((file_obj, file_name))
        
        if not valid_files:
            shutil.rmtree(temp_dir)
            return gr.update(value=None, visible=False), "No supported files found."
        
        # Create a zip file
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            total_files = len(valid_files)
            
            for i, (file_obj, rel_path) in enumerate(valid_files):
                file_name, file_extension = os.path.splitext(file_obj.name)
                base_name = os.path.basename(file_name)
                
                # Update progress with initial file info
                progress_callback(i / total_files, desc=f"Starting to process {rel_path} (File {i+1}/{total_files})")
                
                # Create translator for this file
                translator_class = get_translator_class(file_extension)
                if not translator_class:
                    continue  # Skip unsupported files (should not happen due to earlier validation)
                
                try:
                    # Process file
                    translator = translator_class(
                        file_obj.name, model, use_online, api_key,
                        src_lang_code, dst_lang_code, max_token=max_token, max_retries=max_retries
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

# Load local and online models
local_models = populate_sum_model() or []
config_dir = "config/api_config"
online_models = [
    os.path.splitext(f)[0] for f in os.listdir(config_dir) 
    if f.endswith(".json") and f != "Custom.json"
]

def update_model_list_and_api_input(use_online):
    """Switch model options and show/hide API Key."""
    if use_online:
        return (
            gr.update(choices=online_models, value=online_models[3] if online_models else None),
            gr.update(visible=True)
        )
    else:
        default_local_value = local_models[0] if local_models else None
        return (
            gr.update(choices=local_models, value=default_local_value),
            gr.update(visible=False)
        )

# Parse Accept-Language
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

def update_lan_mode(lan_mode):
    """Update system config with new LAN mode setting."""
    config = read_system_config()
    config["lan_mode"] = lan_mode
    write_system_config(config)
    return config["lan_mode"]

# We don't need to save the max retries in the system config
def update_max_retries(max_retries):
    """Just return the current max retries without updating the system config."""
    return max_retries

# Apply labels based on user language
def set_labels(session_lang: str):
    """Update UI labels according to the chosen language."""
    labels = LABEL_TRANSLATIONS.get(session_lang, LABEL_TRANSLATIONS["en"])
    
    # Update file upload label
    file_upload_label = "Upload Files"
    if "Upload Files" in labels:
        file_upload_label = labels["Upload Files"]
    elif "Upload File" in labels:
        # Modify existing label for multiple files
        file_upload_label = labels["Upload File"] + "s"
    
    return {
        src_lang: gr.update(label=labels["Source Language"]),
        dst_lang: gr.update(label=labels["Target Language"]),
        use_online_model: gr.update(label=labels["Use Online Model"]),
        lan_mode_checkbox: gr.update(label=labels["Local Network Mode (Restart to Apply)"]),
        model_choice: gr.update(label=labels["Models"]),
        max_retries_slider: gr.update(label=labels["Max Retries"]),
        api_key_input: gr.update(label=labels["API Key"]),
        file_input: gr.update(label=file_upload_label),
        output_file: gr.update(label=labels["Download Translated File"]),
        status_message: gr.update(label=labels["Status Message"]),
        translate_button: gr.update(value=labels["Translate"]),
    }

def init_ui(request: gr.Request):
    """Set user language and update labels on page load."""
    user_lang = get_user_lang(request)
    config = read_system_config()
    
    lan_mode_state = config.get("lan_mode", False)
    # Always use default 4 for max retries
    max_retries_state = 4
    
    label_updates = set_labels(user_lang)
    return [user_lang, lan_mode_state, max_retries_state] + list(label_updates.values())

config = read_system_config()
initial_lan_mode = config.get("lan_mode", False)
initial_max_retries = 4  # Always use default 4
initial_max_tokens = 768  

# Build Gradio interface
with gr.Blocks(title="AI Office Translator") as demo:
    gr.Markdown("# AI-Office-Translator\n### Made by Haruka-YANG")
    session_lang = gr.State("en")
    lan_mode_state = gr.State(initial_lan_mode)
    max_retries_state = gr.State(initial_max_retries)

    with gr.Row():
        src_lang = gr.Dropdown(
            [
                "English", "中文", "繁體中文", "日本語", "Español", 
                "Français", "Deutsch", "Italiano", "Português", 
                "Русский", "한국어", "ภาษาไทย", "Tiếng Việt"
            ],
            label="Source Language",
            value="English"
        )
        dst_lang = gr.Dropdown(
            [
                "English", "中文", "繁體中文", "日本語", "Español", 
                "Français", "Deutsch", "Italiano", "Português", 
                "Русский", "한국어", "ภาษาไทย", "Tiếng Việt"
            ],
            label="Target Language",
            value="English"
        )

    with gr.Row():
        use_online_model = gr.Checkbox(label="Use Online Model", value=False)
        lan_mode_checkbox = gr.Checkbox(label="Local Network Mode (Restart to Apply)", value=initial_lan_mode)

    with gr.Row():
        default_local_value = local_models[0] if local_models else None
        model_choice = gr.Dropdown(
            choices=local_models,
            label="Models",
            value=default_local_value
        )
        max_retries_slider = gr.Slider(
            minimum=1,
            maximum=10,
            step=1,
            value=initial_max_retries,
            label="Max Retries"
        )

    api_key_input = gr.Textbox(label="API Key", placeholder="Enter your API key here", visible=False)
    file_input = gr.File(
        label="Upload Files (.docx, .pptx, .xlsx, .pdf, .srt, .txt)",
        file_types=[".docx", ".pptx", ".xlsx", ".pdf", ".srt", ".txt"],
        file_count="multiple"
    )
    output_file = gr.File(label="Download Translated File", visible=False)
    status_message = gr.Textbox(label="Status Message", interactive=False, visible=True)
    translate_button = gr.Button("Translate")

    # Event handlers
    use_online_model.change(
        update_model_list_and_api_input,
        inputs=use_online_model,
        outputs=[model_choice, api_key_input]
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

    # Hide download button and reset status first
    translate_button.click(
        lambda: (gr.update(visible=False), None),
        inputs=[],
        outputs=[output_file, status_message]
    )

    # Then translate
    translate_button.click(
        translate_files,
        inputs=[
            file_input, model_choice, src_lang, dst_lang, 
            use_online_model, api_key_input, max_retries_slider
        ],
        outputs=[output_file, status_message]
    )

    # On page load, set user language and labels
    demo.load(
        fn=init_ui,
        inputs=None,
        outputs=[
            session_lang, lan_mode_state, max_retries_state, src_lang, dst_lang, use_online_model,
            lan_mode_checkbox, model_choice, max_retries_slider, api_key_input, file_input, 
            output_file, status_message, translate_button
        ]
    )

available_port = find_available_port(start_port=9980)

if initial_lan_mode:
    demo.launch(server_name="0.0.0.0", server_port=available_port, share=False, inbrowser=True)
else:
    demo.launch(server_port=available_port, share=False, inbrowser=True)
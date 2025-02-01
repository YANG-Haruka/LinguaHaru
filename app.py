import gradio as gr
import os
from translator.excel_translator import ExcelTranslator
from translator.ppt_translator import PptTranslator
from translator.word_translator import WordTranslator
from translator.pdf_translator import PdfTranslator
from llmWrapper.ollama_wrapper import populate_sum_model
from typing import List, Tuple

# ------------------------------------------------------------------------------------
# 1) A dictionary mapping human-readable language names to codes used by translation
# ------------------------------------------------------------------------------------
LANGUAGE_MAP = {
    "日本語": "ja",
    "中文": "zh",
    "繁體中文": "zh-Hant",
    "English": "en",
    "Español": "es",
    "Français": "fr",
    "Deutsch": "de",
    "Italiano": "it",
    "Português": "pt",
    "Русский": "ru",
    "한국어": "ko"
}

# ------------------------------------------------------------------------------------
# 2) Label translations (UI text) for different languages
# ------------------------------------------------------------------------------------
LABEL_TRANSLATIONS = {
    "en": {
        "Source Language": "Source Language",
        "Target Language": "Target Language",
        "Use Online Model": "Use Online Model",
        "Models": "Models",
        "API Key": "API Key",
        "Max Tokens": "Max Tokens",
        "Upload File": "Upload Office File (.docx, .pptx, .xlsx, .pdf)",
        "Download Translated File": "Download Translated File",
        "Status Message": "Status Message",
        "Translate": "Translate"
    },
    "zh": {
        "Source Language": "源语言",
        "Target Language": "目标语言",
        "Use Online Model": "使用在线模型",
        "Models": "模型",
        "API Key": "API 密钥",
        "Max Tokens": "最大令牌数",
        "Upload File": "上传文件 (.docx, .pptx, .xlsx, .pdf)",
        "Download Translated File": "下载翻译文件",
        "Status Message": "状态消息",
        "Translate": "翻译"
    },
    "ja": {
        "Source Language": "ソース言語",
        "Target Language": "ターゲット言語",
        "Use Online Model": "オンラインモデルを使用",
        "Models": "モデル",
        "API Key": "APIキー",
        "Max Tokens": "最大トークン数",
        "Upload File": "オフィスファイルをアップロード (.docx, .pptx, .xlsx, .pdf)",
        "Download Translated File": "翻訳ファイルをダウンロード",
        "Status Message": "ステータスメッセージ",
        "Translate": "翻訳"
    },
    # You can add more languages here if needed.
}

# ------------------------------------------------------------------------------------
# 3) The main file translation function 
# ------------------------------------------------------------------------------------
def translate_file(file, model, src_lang, dst_lang, use_online, api_key, max_token=768, progress=gr.Progress(track_tqdm=True)):
    """
    Handles the translation process with a progress bar and simple error handling.
    """
    if file is None:
        return gr.update(value=None, visible=False), "Please select a file to translate."

    if use_online and not api_key:
        return gr.update(value=None, visible=False), "API key is required for online models. Please enter your API key."

    def progress_callback(progress_value, desc=None):
        progress(progress_value, desc=desc)

    src_lang_code = LANGUAGE_MAP.get(src_lang, "en")
    dst_lang_code = LANGUAGE_MAP.get(dst_lang, "en")

    # Check file extension
    file_name, file_extension = os.path.splitext(file.name)
    translator_class = {
        ".docx": WordTranslator,
        ".pptx": PptTranslator,
        ".xlsx": ExcelTranslator,
        ".pdf": PdfTranslator,
    }.get(file_extension.lower())

    if not translator_class:
        return (
            gr.update(value=None, visible=False),
            f"Unsupported file type '{file_extension}'. Please upload a .docx, .pptx, .xlsx, or .pdf file."
        )

    try:
        # Initialize the right translator
        translator = translator_class(
            file.name, 
            model, 
            use_online, 
            api_key,
            src_lang_code, 
            dst_lang_code, 
            max_token=max_token
        )
        progress(0, desc="Initializing translation...")

        # Perform translation
        translated_file_path, missing_counts = translator.process(
            file_name, file_extension, progress_callback=progress_callback
        )
        progress(1, desc="Completed! Thanks for using ^_^")

        # Check if any segments were missing
        if missing_counts:
            warning_message = f"Warning: Some segments are missing translations for keys: {sorted(missing_counts)}"
            return gr.update(value=translated_file_path, visible=True), warning_message

        # Return success
        return gr.update(value=translated_file_path, visible=True), "Translation completed successfully! You can now download the file."

    except ValueError as e:
        # Specific errors, e.g. empty content
        return gr.update(value=None, visible=False), f"Translation failed: {str(e)}\nFile: {file.name}"
    except Exception as e:
        # Catch-all for unexpected errors
        return gr.update(value=None, visible=False), f"An unexpected error occurred: {str(e)}\nFile: {file.name}"


# ------------------------------------------------------------------------------------
# 4) Load local and online models
# ------------------------------------------------------------------------------------
local_models = populate_sum_model() or []
online_models = ["deepseekv3"]

def update_model_list_and_api_input(use_online):
    """
    When the checkbox changes, switch between local/online models and 
    show/hide the API Key input field.
    """
    if use_online:
        return (
            gr.update(choices=online_models, value=online_models[0]),
            gr.update(visible=True)
        )
    else:
        default_local_value = local_models[0] if local_models else None
        return (
            gr.update(choices=local_models, value=default_local_value),
            gr.update(visible=False)
        )


# ------------------------------------------------------------------------------------
# 5) Parse the Accept-Language header for language codes
# ------------------------------------------------------------------------------------
def parse_accept_language(accept_language: str) -> List[Tuple[str, float]]:
    """
    Parse the Accept-Language header into a list of (language-tag, qvalue) pairs.
    
    Args:
        accept_language: Accept-Language header string like 'en,zh-cn;q=0.9,zh;q=0.8'
        
    Returns:
        List of tuples containing (language tag, quality value)
    """
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
    """
    Determine user's preferred language based on Accept-Language header.
    Returns the language code with highest q-value.
    
    Args:
        request: Gradio Request object
        
    Returns:
        Language code: 'zh-Hant', 'zh', 'ja', or 'en'
    """
    accept_lang = request.headers.get("accept-language", "").lower()
    parsed = parse_accept_language(accept_lang)
    
    # Early return if no languages specified
    if not parsed:
        return "en"
        
    # Get the language with highest q-value
    highest_lang, highest_q = parsed[0]
    highest_lang = highest_lang.lower()
    
    # Map the highest priority language to our supported formats
    if highest_lang.startswith(('zh-tw', 'zh-hk', 'zh-hant')):
        return "zh-Hant"
    elif highest_lang.startswith(('zh-cn', 'zh-hans', 'zh')):
        return "zh"
    elif highest_lang.startswith('ja'):
        return "ja"
    elif highest_lang.startswith('en'):
        return "en"
    
    # If highest priority language is not supported, default to English
    return "en"

# ------------------------------------------------------------------------------------
# 6) When the page loads, apply the labels based on the detected language
# ------------------------------------------------------------------------------------
def set_labels(session_lang: str):
    """
    Use the session_lang (e.g. 'zh', 'ja', 'en') to look up the correct 
    UI text in LABEL_TRANSLATIONS, then return updates for each component.
    """
    labels_dict = LABEL_TRANSLATIONS.get(session_lang, LABEL_TRANSLATIONS["en"])

    return {
        src_lang: gr.update(label=labels_dict["Source Language"]),
        dst_lang: gr.update(label=labels_dict["Target Language"]),
        use_online_model: gr.update(label=labels_dict["Use Online Model"]),
        model_choice: gr.update(label=labels_dict["Models"]),
        api_key_input: gr.update(label=labels_dict["API Key"]),
        max_token: gr.update(label=labels_dict["Max Tokens"]),
        file_input: gr.update(label=labels_dict["Upload File"]),
        output_file: gr.update(label=labels_dict["Download Translated File"]),
        status_message: gr.update(label=labels_dict["Status Message"]),
        translate_button: gr.update(value=labels_dict["Translate"]),  # Button text uses "value"
    }

def init_ui(request: gr.Request):
    user_lang = get_user_lang(request)
    return [user_lang] + list(set_labels(user_lang).values())

# ------------------------------------------------------------------------------------
# 7) Build the Gradio interface
# ------------------------------------------------------------------------------------
with gr.Blocks() as demo:
    # A hidden state to store the user's language, default is "en"
    session_lang = gr.State("en")

    # UI components (initially in English or any default language)
    with gr.Row():
        src_lang = gr.Dropdown(
            ["English", "中文", "繁體中文", "日本語", "Español", "Français", "Deutsch", "Italiano", "Português", "Русский", "한국어"],
            label="Source Language",
            value="English"
        )
        dst_lang = gr.Dropdown(
            ["English", "中文", "繁體中文", "日本語", "Español", "Français", "Deutsch", "Italiano", "Português", "Русский", "한국어"],
            label="Target Language",
            value="English"
        )

    with gr.Row():
        use_online_model = gr.Checkbox(label="Use Online Model", value=False)

    default_local_value = local_models[0] if local_models else None
    model_choice = gr.Dropdown(
        choices=local_models,
        label="Models",
        value=default_local_value
    )

    api_key_input = gr.Textbox(label="API Key", placeholder="Enter your API key here", visible=False)
    max_token = gr.Number(label="Max Tokens", value=768)

    file_input = gr.File(
        label="Upload Office File (.docx, .pptx, .xlsx, .pdf)",
        file_types=[".docx", ".pptx", ".xlsx", ".pdf"]
    )
    output_file = gr.File(label="Download Translated File", visible=False)
    status_message = gr.Textbox(label="Status Message", interactive=False, visible=True)

    translate_button = gr.Button("Translate")

    # Interaction for changing model lists
    use_online_model.change(
        update_model_list_and_api_input,
        inputs=use_online_model,
        outputs=[model_choice, api_key_input]
    )

    # When clicking "Translate" we first hide the download button and reset status
    translate_button.click(
        lambda: (gr.update(visible=False), None),
        inputs=[],
        outputs=[output_file, status_message]
    )

    # Then perform the actual file translation
    translate_button.click(
        translate_file,
        inputs=[file_input, model_choice, src_lang, dst_lang, use_online_model, api_key_input, max_token],
        outputs=[output_file, status_message]
    )

    # On page load: 1) get user language, 2) set labels accordingly
    demo.load(
        fn=init_ui,
        inputs=None,
        outputs=[session_lang, src_lang, dst_lang, use_online_model, model_choice,
                api_key_input, max_token, file_input, output_file, 
                status_message, translate_button]
    )

# Finally, launch the Gradio app
demo.launch(server_port=9980, share=True)

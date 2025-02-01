import gradio as gr
import os
from translator.excel_translator import ExcelTranslator
from translator.ppt_translator import PptTranslator
from translator.word_translator import WordTranslator
from translator.pdf_translator import PdfTranslator
from llmWrapper.ollama_wrapper import populate_sum_model

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

def translate_file(file, model, src_lang, dst_lang, use_online, api_key, max_token=768, progress=gr.Progress(track_tqdm=True)):
    """Handles the translation process with a progress bar and error handling."""
    if file is None:
        return gr.update(value=None, visible=False), "Please select a file to translate."

    if use_online and not api_key:
        return gr.update(value=None, visible=False), "API key is required for online models. Please enter your API key."

    def progress_callback(progress_value, desc=None):
        progress(progress_value, desc=desc)

    src_lang_code = LANGUAGE_MAP.get(src_lang, "en")
    dst_lang_code = LANGUAGE_MAP.get(dst_lang, "en")

    # Verify file type
    file_name, file_extension = os.path.splitext(file.name)
    translator_class = {
        ".docx": WordTranslator,
        ".pptx": PptTranslator,
        ".xlsx": ExcelTranslator,
        ".pdf": PdfTranslator,
    }.get(file_extension.lower())

    if not translator_class:
        return gr.update(value=None, visible=False), f"Unsupported file type '{file_extension}'. Please upload a .docx, .pptx, .xlsx, or .pdf file."

    try:
        # Initialize translator and progress
        translator = translator_class(file.name, model, use_online, api_key, src_lang_code, dst_lang_code, max_token=max_token)
        progress(0, desc="Initializing translation...")

        # Start translation and get result file path
        translated_file_path, missing_counts = translator.process(file_name, file_extension, progress_callback=progress_callback)
        progress(1, desc="Completed! Thanks for using ^_^")

        # Check for missing translations
        if missing_counts:
            warning_message = f"Warning: Some segments are missing translations for keys: {sorted(missing_counts)}"
            return gr.update(value=translated_file_path, visible=True), warning_message

        # Return the file path for download and a success message
        return gr.update(value=translated_file_path, visible=True), "Translation completed successfully! You can now download the file."

    except ValueError as e:
        # Handle specific errors like empty JSON (cell_data is empty)
        return gr.update(value=None, visible=False), f"Translation failed: {str(e)}\nFile: {file.name}"

    except Exception as e:
        # Catch any other unexpected errors and provide feedback
        return gr.update(value=None, visible=False), f"An unexpected error occurred: {str(e)}\nFile: {file.name}"

# Load available models
local_models = populate_sum_model()
online_models = ["deepseekv3"]

def update_model_list_and_api_input(use_online):
    if use_online:
        return (
            gr.update(choices=online_models, label="Online Models", value=online_models[0]),
            gr.update(visible=True)
        )
    else:
        return (
            gr.update(choices=local_models, label="Local Models", value=local_models[0] if local_models else None),
            gr.update(visible=False)
        )

# Build Gradio interface
with gr.Blocks() as demo:
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

    model_choice = gr.Dropdown(
        choices=local_models,
        label="Local Models",
        value=local_models[0] if local_models else None
    )

    api_key_input = gr.Textbox(label="API Key", placeholder="Enter your API key here", visible=False)

    max_token = gr.Number(label="Max Tokens", value=768)

    file_input = gr.File(
        label="Upload Office File (.docx, .pptx, .xlsx, .pdf)",
        file_types=[".docx", ".pptx", ".xlsx", ".pdf"]
    )
    output_file = gr.File(label="Download Translated File", visible=False)  # Initially hidden
    status_message = gr.Textbox(label="Status Message", interactive=False, visible=True)

    # Update model list and API key input visibility when checkbox changes
    use_online_model.change(
        update_model_list_and_api_input,
        inputs=use_online_model,
        outputs=[model_choice, api_key_input]
    )

    translate_button = gr.Button("Translate")

    translate_button.click(
        lambda *args: (gr.update(visible=False), None),  # Hide download button before translation starts
        inputs=[],
        outputs=[output_file, status_message]  # Reset status message and download button
    )

    translate_button.click(
        translate_file,
        inputs=[file_input, model_choice, src_lang, dst_lang, use_online_model, api_key_input, max_token],
        outputs=[output_file, status_message]  # Update status message and file output
    )

# Launch Gradio app
demo.launch(server_port=9980, share=False)

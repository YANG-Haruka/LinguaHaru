import gradio as gr
import os
from translator.excel_translator import ExcelTranslator
from translator.ppt_translator import PptTranslator
from translator.word_translator import WordTranslator
from llmWrapper.ollama_wrapper import populate_sum_model

LANGUAGE_MAP = {
    "日本語": "ja",
    "中文": "zh",
    "English": "en",
}

def translate_file(file, model, src_lang, dst_lang, max_token=1024, progress=gr.Progress(track_tqdm=True)):
    """Handles the translation process with a progress bar."""
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
    }.get(file_extension.lower())

    if not translator_class:
        return "Unsupported file type. Please upload a .docx, .pptx, or .xlsx file."

    try:
        # Initialize translator and progress
        translator = translator_class(file.name, model, src_lang_code, dst_lang_code, max_token=max_token)
        progress(0, desc="Initializing translation...")

        # Start translation and get result file path
        translated_file_path = translator.process(file_name, file_extension, progress_callback=progress_callback)
        progress(1, desc="Completed! Thanks for using ^_^")

        # Return the file path for download
        return translated_file_path

    except Exception as e:
        return f"An error occurred during translation: {str(e)}"

# Load available models
available_models = populate_sum_model()

# Build Gradio interface
with gr.Blocks() as demo:
    with gr.Row():
        src_lang = gr.Dropdown(["English", "中文", "日本語"], label="Source Language", value="English")
        dst_lang = gr.Dropdown(["English", "中文", "日本語"], label="Target Language", value="English")
    
    with gr.Row():
        model_choice = gr.Dropdown(
            available_models,
            label="Model (QWen series models are recommended)",
            value=available_models[0] if available_models else None,
        )
        max_token = gr.Number(label="Max Tokens", value=1024)

    file_input = gr.File(
        label="Upload Office File (.docx, .pptx, .xlsx)",
        file_types=[".docx", ".pptx", ".xlsx"]
    )
    output = gr.File(label="Download Translated File")  # Use gr.File for downloadable output

    translate_button = gr.Button("Translate")
    translate_button.click(
        translate_file,
        inputs=[file_input, model_choice, src_lang, dst_lang, max_token],
        outputs=output,  # Output is now a file for download
    )

# Launch Gradio app
demo.launch(server_port=9980, share=False)
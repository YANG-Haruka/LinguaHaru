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

def translate_file(file, model, src_lang, dst_lang, use_online, max_token=1024, progress=gr.Progress(track_tqdm=True)):
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

    # Initialize translator and progress
    translator = translator_class(file.name, model, use_online, src_lang_code, dst_lang_code, max_token=max_token)
    progress(0, desc="Initializing translation...")

    # Start translation and get result file path
    translated_file_path = translator.process(file_name, file_extension, progress_callback=progress_callback)
    progress(1, desc="Completed! Thanks for using ^_^")

    # Return the file path for download
    return translated_file_path

# Load available models
local_models = populate_sum_model()
online_models = ["deepseekv3"]

def update_model_list(use_online):
    if use_online:
        return gr.update(choices=online_models, label="Online Models", value=online_models[0])
    else:
        return gr.update(choices=local_models, label="Local Models", value=local_models[0] if local_models else None)

# Build Gradio interface
with gr.Blocks() as demo:
    with gr.Row():
        src_lang = gr.Dropdown(["English", "中文", "日本語"], label="Source Language", value="English")
        dst_lang = gr.Dropdown(["English", "中文", "日本語"], label="Target Language", value="English")

    with gr.Row():
        use_online_model = gr.Checkbox(label="Use Online Model", value=False)

    model_choice = gr.Dropdown(
        choices=local_models,
        label="Local Models",
        value=local_models[0] if local_models else None
    )

    max_token = gr.Number(label="Max Tokens", value=1024)

    file_input = gr.File(
        label="Upload Office File (.docx, .pptx, .xlsx)",
        file_types=[".docx", ".pptx", ".xlsx"]
    )
    output = gr.File(label="Download Translated File")  # Use gr.File for downloadable output

    # Update model list when checkbox changes
    use_online_model.change(
        update_model_list,
        inputs=use_online_model,
        outputs=model_choice
    )

    translate_button = gr.Button("Translate")
    translate_button.click(
        translate_file,
        inputs=[file_input, model_choice, src_lang, dst_lang, use_online_model, max_token],
        outputs=output,  # Output is now a file for download
    )

# Launch Gradio app
demo.launch(server_port=9980, share=False)

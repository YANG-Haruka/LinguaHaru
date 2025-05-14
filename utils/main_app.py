import gradio as gr
import os
from functools import partial

# Import config and utilities
from .app_config import (
    read_system_config, find_available_port, load_application_icon,
    update_lan_mode, update_max_retries, update_thread_count,
    update_excel_mode, update_word_bilingual_mode, get_default_languages
)
from .app_queue import modified_translate_button_click, request_stop_translation
from .ui_utils import (
    get_available_languages, on_src_language_change, on_dst_language_change,
    show_mode_checkbox, update_continue_button, update_model_list_and_api_input,
    get_user_lang, set_labels, on_add_new, swap_languages
)
from .translation_process import translate_files
from config.languages_config import LABEL_TRANSLATIONS
from llmWrapper.offline_translation import populate_sum_model

# Initialize UI state
def init_ui(request: gr.Request):
    """Set user language and update labels on page load."""
    user_lang = get_user_lang(request)
    config = read_system_config()
    
    lan_mode_state = config.get("lan_mode", False)
    default_online_state = config.get("default_online", False)
    max_token_state = config.get("max_token", 768)
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
    
    # Update use_online_model checkbox based on default_online setting
    use_online_value = default_online_state
    
    # Update model choices based on online/offline mode
    # Load local and online models
    local_models = populate_sum_model() or []
    config_dir = "config/api_config"
    online_models = [
        os.path.splitext(f)[0] for f in os.listdir(config_dir) 
        if f.endswith(".json") and f != "Custom.json"
    ]
    
    default_local_model = config.get("default_local_model", "")
    default_online_model = config.get("default_online_model", "")
    
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
    
    # Create UI component references (placeholders for now)
    ui_components = {}
    label_updates = set_labels(user_lang, ui_components)
    
    # Add visibility updates for max_retries and thread_count
    label_updates['max_retries_slider'] = gr.update(label=LABEL_TRANSLATIONS.get(user_lang, LABEL_TRANSLATIONS["en"])["Max Retries"], visible=show_max_retries)
    label_updates['thread_count_slider'] = gr.update(label=LABEL_TRANSLATIONS.get(user_lang, LABEL_TRANSLATIONS["en"])["Thread Count"], visible=show_thread_count)
    
    # Prepare return values - now INCLUDING stop_button
    label_values = list(label_updates.values())
    
    # Return settings values and UI updates (now WITH stop_button)
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
        gr.update(choices=model_choices, value=model_value)   # Another model update
    ] + label_values

def create_app():
    """Create and return the Gradio app instance."""
    
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
    img_height = config.get("img_height", 250)

    # Get show_model_selection and show_mode_switch from config
    initial_show_model_selection = config.get("show_model_selection", True)
    initial_show_mode_switch = config.get("show_mode_switch", True)
    initial_show_lan_mode = config.get("show_lan_mode", True)
    initial_show_max_retries = config.get("show_max_retries", True)
    initial_show_thread_count = config.get("show_thread_count", True)

    encoded_image, mime_type = load_application_icon(config)

    # Create a Gradio blocks interface
    with gr.Blocks(
        title=app_title_web,
        css="""
        footer { visibility: hidden; }

        /* Language row */
        #lang-row {
            display: flex !important;
            align-items: center !important;
            gap: 8px !important;
            margin-bottom: 20px;
        }

        #lang-row .gr-dropdown {
            flex: 1 1 0 !important;
            min-width: 200px;
        }

        #swap-btn {
            flex: 0 0 42px !important;
            max-width: 42px !important;
            min-width: 42px !important;
            height: 42px !important;
        }

        #swap-btn button {
            width: 42px !important;
            height: 42px !important;
            padding: 0 !important;
            font-size: 20px !important;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            aspect-ratio: 1/1 !important;
            border-radius: var(--button-border-radius, 8px);
        }

        #swap-btn button:hover {
            transform: rotate(180deg);
            transition: transform 0.3s ease;
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

        # Model choice and API key input
        with gr.Row():
            model_choice = gr.Dropdown(
                choices=local_models if not initial_default_online else online_models,
                label="Models",
                value=local_models[0] if not initial_default_online and local_models else (
                    online_models[0] if initial_default_online and online_models else None
                ),
                visible=initial_show_model_selection,
                allow_custom_value=True 
            )

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
            partial(update_model_list_and_api_input, config=config),
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

        # Update event handlers for translate button
        translate_button.click(
            lambda: (gr.update(visible=False), None, gr.update(interactive=False), gr.update(interactive=False), gr.update(interactive=True)),
            inputs=[],
            outputs=[output_file, status_message, translate_button, continue_button, stop_button]
        ).then(
            partial(modified_translate_button_click, translate_files),
            inputs=[
                file_input, model_choice, src_lang, dst_lang, 
                use_online_model, api_key_input, max_retries_slider, max_token_state,
                thread_count_slider, excel_mode_checkbox, word_bilingual_checkbox, session_lang
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

        continue_button.click(
            lambda: (gr.update(visible=False), None, gr.update(interactive=False), gr.update(interactive=False), gr.update(interactive=True)),
            inputs=[],
            outputs=[output_file, status_message, translate_button, continue_button, stop_button]
        ).then(
            partial(modified_translate_button_click, translate_files, continue_mode=True),
            inputs=[
                file_input, model_choice, src_lang, dst_lang, 
                use_online_model, api_key_input, max_retries_slider, max_token_state,
                thread_count_slider, excel_mode_checkbox, word_bilingual_checkbox, session_lang
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

        # Replace these event handlers:
        src_lang.change(
            partial(on_src_language_change, CUSTOM_LABEL=CUSTOM_LABEL), 
            inputs=src_lang, 
            outputs=[custom_lang_input, add_lang_button]
        )
        
        dst_lang.change(
            partial(on_dst_language_change, CUSTOM_LABEL=CUSTOM_LABEL), 
            inputs=dst_lang, 
            outputs=[custom_lang_input, add_lang_button]
        )
        
        # Swap languages button handler
        swap_button.click(
            swap_languages,
            inputs=[src_lang, dst_lang],
            outputs=[src_lang, dst_lang]
        )

        # 2) Create New Language
        add_lang_button.click(
            partial(on_add_new, CUSTOM_LABEL=CUSTOM_LABEL),
            inputs=[custom_lang_input],
            outputs=[src_lang, dst_lang, custom_lang_input, add_lang_button]
        )

        # On page load, set user language and labels
        demo.load(
            fn=init_ui,
            inputs=None,
            outputs=[
                session_lang, lan_mode_state, default_online_state, max_token_state, max_retries_state,
                excel_mode_2_state, word_bilingual_mode_state, thread_count_state,
                use_online_model, model_choice, model_choice,
                src_lang, dst_lang, use_online_model, lan_mode_checkbox,
                model_choice, max_retries_slider, thread_count_slider,
                api_key_input, file_input, output_file, status_message, translate_button,
                continue_button, excel_mode_checkbox, word_bilingual_checkbox, stop_button
            ]
        )

    return demo

def launch_app():
    """Launch the application."""
    demo = create_app()
    config = read_system_config()
    initial_lan_mode = config.get("lan_mode", False)
    
    available_port = find_available_port(start_port=9980)
    
    if initial_lan_mode:
        demo.launch(server_name="0.0.0.0", server_port=available_port, share=False, inbrowser=True)
    else:
        demo.launch(server_port=available_port, share=False, inbrowser=True)
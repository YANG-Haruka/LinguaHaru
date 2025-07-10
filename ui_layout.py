"""
UI Layout Module for LinguaHaru
Separated layout components and CSS styles
"""

import gradio as gr
import os
from config.languages_config import get_available_languages


def get_custom_css():
    """Return custom CSS styles"""
    return """
    footer { visibility: hidden; }
    
    /* Dark Theme Variables */
    :root {
        --bg-primary: #0f0f23 !important;
        --bg-secondary: #1a1b26 !important;
        --bg-tertiary: #24283b !important;
        --bg-card: #1f2335 !important;
        --border-color: #414868 !important;
        --text-primary: #c0caf5 !important;
        --text-secondary: #9aa5ce !important;
        --accent-primary: #7aa2f7 !important;
        --accent-secondary: #bb9af7 !important;
        --accent-success: #9ece6a !important;
        --accent-warning: #e0af68 !important;
        --accent-danger: #f7768e !important;
        --shadow: 0 4px 12px rgba(0, 0, 0, 0.3) !important;
        --radius: 12px !important;
        --radius-small: 8px !important;
    }

    /* Language row */
    #lang-row {
        display: grid !important;
        grid-template-columns: 1fr auto 1fr !important;
        align-items: center !important;
        gap: 10px !important;
        margin-bottom: 20px !important;
        background: var(--bg-secondary) !important;
        padding: 5px !important;
        border-radius: var(--radius) !important;
        border: 1px solid var(--border-color) !important;
        width: 100% !important;
        box-sizing: border-box !important;
    }

    #lang-row > div:first-child {
        grid-column: 1 !important;
    }

    #swap-btn {
        grid-column: 2 !important;
        width: 50px !important;
        height: 50px !important;
        justify-self: center !important;
        background: linear-gradient(135deg, var(--accent-primary), var(--accent-secondary)) !important;
        border: none !important;
        border-radius: 50% !important;
        color: white !important;
        font-size: 1.2rem !important;
        transition: all 0.3s ease !important;
        box-shadow: var(--shadow) !important;
        cursor: pointer !important;
    }

    #swap-btn:hover {
        transform: rotate(180deg) scale(1.1) !important;
        box-shadow: 0 6px 20px rgba(122, 162, 247, 0.4) !important;
    }

    #lang-row > div:last-child {
        grid-column: 3 !important;
    }

    /* Language dropdown styles */
    #lang-row .lang-dropdown,
    #lang-row .gr-dropdown.lang-dropdown,
    #lang-row .gr-dropdown {
        position: relative !important;
        background: var(--bg-tertiary) !important;
    }

    /* Dropdown container */
    #lang-row .lang-dropdown .gr-dropdown-container,
    #lang-row .lang-dropdown [data-testid="dropdown-container"],
    #lang-row .gr-dropdown.lang-dropdown .gr-dropdown-container,
    #lang-row .gr-dropdown.lang-dropdown [data-testid="dropdown-container"],
    #lang-row .gr-dropdown .gr-dropdown-container,
    #lang-row .gr-dropdown [data-testid="dropdown-container"] {
        position: relative !important;
        background: var(--bg-tertiary) !important;
    }

    /* Dropdown list - flexbox layout */
    #lang-row .lang-dropdown .gr-dropdown-list,
    #lang-row .lang-dropdown [role="listbox"],
    #lang-row .lang-dropdown .dropdown-content,
    #lang-row .lang-dropdown .options-container,
    #lang-row .gr-dropdown.lang-dropdown .gr-dropdown-list,
    #lang-row .gr-dropdown.lang-dropdown [role="listbox"],
    #lang-row .gr-dropdown.lang-dropdown .dropdown-content,
    #lang-row .gr-dropdown.lang-dropdown .options-container,
    #lang-row .gr-dropdown .gr-dropdown-list,
    #lang-row .gr-dropdown [role="listbox"],
    #lang-row .gr-dropdown .dropdown-content,
    #lang-row .gr-dropdown .options-container,
    #lang-row div:first-child .gr-dropdown [role="listbox"],
    #lang-row div:last-child .gr-dropdown [role="listbox"] {
        display: flex !important;
        flex-wrap: wrap !important;
        justify-content: flex-start !important;
        align-content: flex-start !important;
        gap: 3px !important;
        padding: 8px !important;
        max-height: 400px !important;
        overflow-y: auto !important;
        overflow-x: hidden !important;
        background: var(--bg-card) !important;
        border: 2px solid var(--border-color) !important;
        border-radius: var(--radius) !important;
        box-shadow: 0 8px 25px rgba(0, 0, 0, 0.4) !important;
        z-index: 1000 !important;
        width: 100% !important;
        max-width: 580px !important;
        min-width: 300px !important;
        box-sizing: border-box !important;
    }

    /* Dropdown options - 5 columns per row */
    #lang-row .lang-dropdown .gr-dropdown-option,
    #lang-row .lang-dropdown [role="option"],
    #lang-row .lang-dropdown .dropdown-item,
    #lang-row .lang-dropdown .option,
    #lang-row .gr-dropdown.lang-dropdown .gr-dropdown-option,
    #lang-row .gr-dropdown.lang-dropdown [role="option"],
    #lang-row .gr-dropdown.lang-dropdown .dropdown-item,
    #lang-row .gr-dropdown.lang-dropdown .option,
    #lang-row .gr-dropdown .gr-dropdown-option,
    #lang-row .gr-dropdown [role="option"],
    #lang-row .gr-dropdown .dropdown-item,
    #lang-row .gr-dropdown .option,
    #lang-row div:first-child .gr-dropdown [role="option"],
    #lang-row div:last-child .gr-dropdown [role="option"] {
        flex: 0 0 calc(20% - 3px) !important;
        padding: 8px 4px !important;
        border-radius: var(--radius-small) !important;
        text-align: center !important;
        cursor: pointer !important;
        transition: all 0.3s ease !important;
        background: var(--bg-tertiary) !important;
        color: var(--text-primary) !important;
        border: 1px solid var(--border-color) !important;
        font-size: 0.85rem !important;
        font-weight: 500 !important;
        white-space: nowrap !important;
        overflow: hidden !important;
        text-overflow: ellipsis !important;
        min-height: 32px !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        box-sizing: border-box !important;
        margin: 0 !important;
        float: left !important;
        min-width: 80px !important;
        max-width: 120px !important;
    }

    /* Hover effects */
    #lang-row .lang-dropdown .gr-dropdown-option:hover,
    #lang-row .lang-dropdown [role="option"]:hover,
    #lang-row .lang-dropdown .dropdown-item:hover,
    #lang-row .lang-dropdown .option:hover,
    #lang-row .gr-dropdown.lang-dropdown .gr-dropdown-option:hover,
    #lang-row .gr-dropdown.lang-dropdown [role="option"]:hover,
    #lang-row .gr-dropdown.lang-dropdown .dropdown-item:hover,
    #lang-row .gr-dropdown.lang-dropdown .option:hover,
    #lang-row .gr-dropdown .gr-dropdown-option:hover,
    #lang-row .gr-dropdown [role="option"]:hover,
    #lang-row .gr-dropdown .dropdown-item:hover,
    #lang-row .gr-dropdown .option:hover,
    #lang-row div:first-child .gr-dropdown [role="option"]:hover,
    #lang-row div:last-child .gr-dropdown [role="option"]:hover {
        background: linear-gradient(135deg, var(--accent-primary), var(--accent-secondary)) !important;
        color: white !important;
        transform: translateY(-2px) scale(1.02) !important;
        box-shadow: 0 6px 20px rgba(122, 162, 247, 0.4) !important;
        border-color: var(--accent-primary) !important;
        z-index: 1001 !important;
    }

    /* Selected state */
    #lang-row .lang-dropdown .gr-dropdown-option.selected,
    #lang-row .lang-dropdown .gr-dropdown-option[aria-selected="true"],
    #lang-row .lang-dropdown [role="option"][aria-selected="true"],
    #lang-row .lang-dropdown .dropdown-item.selected,
    #lang-row .lang-dropdown .option.selected,
    #lang-row .gr-dropdown.lang-dropdown .gr-dropdown-option.selected,
    #lang-row .gr-dropdown.lang-dropdown .gr-dropdown-option[aria-selected="true"],
    #lang-row .gr-dropdown.lang-dropdown [role="option"][aria-selected="true"],
    #lang-row .gr-dropdown.lang-dropdown .dropdown-item.selected,
    #lang-row .gr-dropdown.lang-dropdown .option.selected,
    #lang-row .gr-dropdown .gr-dropdown-option.selected,
    #lang-row .gr-dropdown .gr-dropdown-option[aria-selected="true"],
    #lang-row .gr-dropdown [role="option"][aria-selected="true"],
    #lang-row .gr-dropdown .dropdown-item.selected,
    #lang-row .gr-dropdown .option.selected,
    #lang-row div:first-child .gr-dropdown [role="option"][aria-selected="true"],
    #lang-row div:last-child .gr-dropdown [role="option"][aria-selected="true"] {
        background: linear-gradient(135deg, var(--accent-secondary), var(--accent-primary)) !important;
        color: white !important;
        border-color: var(--accent-secondary) !important;
        font-weight: 700 !important;
        box-shadow: 0 4px 15px rgba(187, 154, 247, 0.4) !important;
    }

    /* Selected hover effect */
    #lang-row .lang-dropdown .gr-dropdown-option.selected:hover,
    #lang-row .lang-dropdown .gr-dropdown-option[aria-selected="true"]:hover,
    #lang-row .lang-dropdown [role="option"][aria-selected="true"]:hover,
    #lang-row .lang-dropdown .dropdown-item.selected:hover,
    #lang-row .lang-dropdown .option.selected:hover,
    #lang-row .gr-dropdown.lang-dropdown .gr-dropdown-option.selected:hover,
    #lang-row .gr-dropdown.lang-dropdown .gr-dropdown-option[aria-selected="true"]:hover,
    #lang-row .gr-dropdown.lang-dropdown [role="option"][aria-selected="true"]:hover,
    #lang-row .gr-dropdown.lang-dropdown .dropdown-item.selected:hover,
    #lang-row .gr-dropdown.lang-dropdown .option.selected:hover,
    #lang-row .gr-dropdown .gr-dropdown-option.selected:hover,
    #lang-row .gr-dropdown .gr-dropdown-option[aria-selected="true"]:hover,
    #lang-row .gr-dropdown [role="option"][aria-selected="true"]:hover,
    #lang-row .gr-dropdown .dropdown-item.selected:hover,
    #lang-row .gr-dropdown .option.selected:hover,
    #lang-row div:first-child .gr-dropdown [role="option"][aria-selected="true"]:hover,
    #lang-row div:last-child .gr-dropdown [role="option"][aria-selected="true"]:hover {
        background: linear-gradient(135deg, var(--accent-primary), var(--accent-success)) !important;
        transform: translateY(-3px) scale(1.05) !important;
        box-shadow: 0 8px 25px rgba(122, 162, 247, 0.6) !important;
    }

    /* Tablet responsive */
    @media (max-width: 1024px) {
        #lang-row .lang-dropdown .gr-dropdown-option,
        #lang-row .lang-dropdown [role="option"],
        #lang-row .lang-dropdown .dropdown-item,
        #lang-row .lang-dropdown .option,
        #lang-row .gr-dropdown.lang-dropdown .gr-dropdown-option,
        #lang-row .gr-dropdown.lang-dropdown [role="option"],
        #lang-row .gr-dropdown.lang-dropdown .dropdown-item,
        #lang-row .gr-dropdown.lang-dropdown .option,
        #lang-row .gr-dropdown .gr-dropdown-option,
        #lang-row .gr-dropdown [role="option"],
        #lang-row .gr-dropdown .dropdown-item,
        #lang-row .gr-dropdown .option,
        #lang-row div:first-child .gr-dropdown [role="option"],
        #lang-row div:last-child .gr-dropdown [role="option"] {
            flex: 0 0 calc(25% - 3px) !important;
            padding: 8px 3px !important;
            font-size: 0.8rem !important;
            min-width: 70px !important;
            max-width: 100px !important;
        }
    }

    /* Mobile responsive */
    @media (max-width: 768px) {
        #lang-row {
            grid-template-columns: 1fr !important;
            grid-template-rows: 1fr auto 1fr !important;
            gap: 10px !important;
            padding: 15px !important;
        }
        
        #lang-row > div:first-child {
            grid-column: 1 !important;
            grid-row: 1 !important;
        }
        
        #swap-btn {
            grid-column: 1 !important;
            grid-row: 2 !important;
            justify-self: center !important;
            width: 40px !important;
            height: 40px !important;
        }
        
        #lang-row > div:last-child {
            grid-column: 1 !important;
            grid-row: 3 !important;
        }
        
        #lang-row .lang-dropdown .gr-dropdown-option,
        #lang-row .lang-dropdown [role="option"],
        #lang-row .lang-dropdown .dropdown-item,
        #lang-row .lang-dropdown .option,
        #lang-row .gr-dropdown.lang-dropdown .gr-dropdown-option,
        #lang-row .gr-dropdown.lang-dropdown [role="option"],
        #lang-row .gr-dropdown.lang-dropdown .dropdown-item,
        #lang-row .gr-dropdown.lang-dropdown .option,
        #lang-row .gr-dropdown .gr-dropdown-option,
        #lang-row .gr-dropdown [role="option"],
        #lang-row .gr-dropdown .dropdown-item,
        #lang-row .gr-dropdown .option,
        #lang-row div:first-child .gr-dropdown [role="option"],
        #lang-row div:last-child .gr-dropdown [role="option"] {
            flex: 0 0 calc(33.333% - 3px) !important;
            padding: 6px 2px !important;
            font-size: 0.75rem !important;
            min-width: 60px !important;
            max-width: 80px !important;
        }
    }

    @media (max-width: 480px) {
        #lang-row .lang-dropdown .gr-dropdown-option,
        #lang-row .lang-dropdown [role="option"],
        #lang-row .lang-dropdown .dropdown-item,
        #lang-row .lang-dropdown .option,
        #lang-row .gr-dropdown.lang-dropdown .gr-dropdown-option,
        #lang-row .gr-dropdown.lang-dropdown [role="option"],
        #lang-row .gr-dropdown.lang-dropdown .dropdown-item,
        #lang-row .gr-dropdown.lang-dropdown .option,
        #lang-row .gr-dropdown .gr-dropdown-option,
        #lang-row .gr-dropdown [role="option"],
        #lang-row .gr-dropdown .dropdown-item,
        #lang-row .gr-dropdown .option,
        #lang-row div:first-child .gr-dropdown [role="option"],
        #lang-row div:last-child .gr-dropdown [role="option"] {
            flex: 0 0 calc(50% - 3px) !important;
            padding: 6px 2px !important;
            font-size: 0.7rem !important;
            min-width: 50px !important;
            max-width: 70px !important;
        }
    }

    /* Model and Glossary row */
    #model-glossary-row {
        display: grid !important;
        grid-template-columns: 1fr 1fr !important;
        gap: 10px !important;
    }
    """


def create_header(app_title, encoded_image, mime_type, img_height):
    """Create app header"""
    return gr.HTML(f"""
    <div style="text-align: center;">
        <h1>{app_title}</h1>
        <img src="data:{mime_type};base64,{encoded_image}" alt="{app_title} Logo" 
                style="display: block; height: {img_height}px; width: auto; margin: 0 auto;">
    </div>
    """)


def create_footer():
    """Create app footer"""
    return gr.HTML("""
    <div style="position: fixed; bottom: 0; left: 0; width: 100%; 
                text-align: center; padding: 10px 0;">
        Made by Haruka-YANG | Version: 3.6 | 
        <a href="https://github.com/YANG-Haruka/LinguaHaru" target="_blank">Visit Github</a>
    </div>
    """)


def create_language_section(default_src_lang, default_dst_lang):
    """Create language selection section"""
    CUSTOM_LABEL = "+ Add Custom…"
    dropdown_choices = get_available_languages() + [CUSTOM_LABEL]
    
    with gr.Row(elem_id="lang-row"):
        src_lang = gr.Dropdown(
            choices=dropdown_choices,
            label="Source Language",
            value=default_src_lang,
            interactive=True,
            allow_custom_value=True,
            elem_classes=["lang-dropdown"]
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
            allow_custom_value=True,
            elem_classes=["lang-dropdown"]
        )
        # Hidden custom language controls
        custom_lang_input = gr.Textbox(
            label="New language display name",
            placeholder="e.g. Klingon",
            visible=False
        )
        add_lang_button = gr.Button("Create New Language", visible=False)
    
    return src_lang, swap_button, dst_lang, custom_lang_input, add_lang_button


def create_settings_section(config):
    """Create settings section"""
    initial_lan_mode = config.get("lan_mode", False)
    initial_default_online = config.get("default_online", False)
    initial_max_retries = config.get("max_retries", 4)
    initial_thread_count_online = config.get("default_thread_count_online", 2)
    initial_thread_count_offline = config.get("default_thread_count_offline", 4)
    initial_thread_count = initial_thread_count_online if initial_default_online else initial_thread_count_offline
    initial_excel_mode_2 = config.get("excel_mode_2", False)
    initial_word_bilingual_mode = config.get("word_bilingual_mode", False)
    
    # Visibility settings
    initial_show_mode_switch = config.get("show_mode_switch", True)
    initial_show_lan_mode = config.get("show_lan_mode", True)
    initial_show_max_retries = config.get("show_max_retries", True)
    initial_show_thread_count = config.get("show_thread_count", True)
    
    # Online/LAN mode settings
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
    
    # Retry and thread settings
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
    
    # Excel and Word mode settings
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
    
    return (use_online_model, lan_mode_checkbox, max_retries_slider, 
            thread_count_slider, excel_mode_checkbox, word_bilingual_checkbox)


def create_model_glossary_section(config, local_models, online_models, get_glossary_files_func, get_default_glossary_func):
    """Create model and glossary selection section"""
    initial_default_online = config.get("default_online", False)
    initial_show_model_selection = config.get("show_model_selection", True)
    initial_show_glossary = config.get("show_glossary", True)
    
    with gr.Row(elem_id="model-glossary-row"):
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
        
        with gr.Column(scale=1, visible=initial_show_glossary):
            # Glossary selection dropdown
            glossary_choice = gr.Dropdown(
                choices=get_glossary_files_func() + ["+"],
                label="Glossary",
                value=get_default_glossary_func(),
                interactive=True,
                visible=initial_show_glossary
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
    
    return (model_choice, glossary_choice, glossary_upload_row, 
            glossary_upload_file, glossary_upload_button)


def create_main_interface(config):
    """Create main translation interface"""
    initial_default_online = config.get("default_online", False)
    
    # API key input
    api_key_input = gr.Textbox(
        label="API Key", 
        placeholder="Enter your API key here", 
        value="",
        visible=initial_default_online
    )
    
    # File upload
    file_input = gr.File(
        label="Upload Files (.docx, .pptx, .xlsx, .pdf, .srt, .txt, .md)",
        file_types=[".docx", ".pptx", ".xlsx", ".pdf", ".srt", ".txt", ".md"],
        file_count="multiple"
    )
    
    # Output and status
    output_file = gr.File(label="Download Translated File", visible=False)
    status_message = gr.Textbox(label="Status Message", interactive=False, visible=True)
    
    # Action buttons
    with gr.Row():
        translate_button = gr.Button("Translate")
        continue_button = gr.Button("Continue Translation", interactive=False)
        stop_button = gr.Button("Stop Translation", interactive=False)
    
    return (api_key_input, file_input, output_file, status_message, 
            translate_button, continue_button, stop_button)


def create_state_variables(config):
    """Create state variables"""
    return {
        'session_lang': gr.State("en"),
        'lan_mode_state': gr.State(config.get("lan_mode", False)),
        'default_online_state': gr.State(config.get("default_online", False)),
        'max_token_state': gr.State(config.get("max_token", 768)),
        'max_retries_state': gr.State(config.get("max_retries", 4)),
        'excel_mode_2_state': gr.State(config.get("excel_mode_2", False)),
        'word_bilingual_mode_state': gr.State(config.get("word_bilingual_mode", False)),
        'thread_count_state': gr.State(config.get("default_thread_count_online", 2) if config.get("default_online", False) else config.get("default_thread_count_offline", 4))
    }
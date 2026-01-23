"""
UI Layout Module for LinguaHaru
Separated layout components and CSS styles
Design: Ethereal Glass Morphism with Japanese Minimalism
"""

import gradio as gr
import os
from config.languages_config import get_available_languages


def get_custom_css():
    """Return custom CSS styles - Ethereal Glass Morphism Theme"""
    return """
    /* ═══════════════════════════════════════════════════════════════
       LINGUAHARU - ETHEREAL GLASS MORPHISM THEME
       Inspired by Japanese minimalism and spring aesthetics
    ═══════════════════════════════════════════════════════════════ */

    /* Import distinctive fonts */
    @import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&family=Crimson+Pro:ital,wght@0,400;0,500;1,400&display=swap');

    /* CSS Variables for theming */
    :root {
        --haru-primary: #e8b4b8;
        --haru-primary-deep: #d4919a;
        --haru-accent: #7eb8da;
        --haru-accent-deep: #5a9fc7;
        --haru-warm: #f5e6d3;
        --haru-text: #2d3748;
        --haru-text-soft: #4a5568;
        --haru-surface: rgba(255, 255, 255, 0.7);
        --haru-surface-elevated: rgba(255, 255, 255, 0.85);
        --haru-border: rgba(232, 180, 184, 0.3);
        --haru-shadow: rgba(45, 55, 72, 0.08);
        --haru-glow: rgba(232, 180, 184, 0.4);
        --haru-gradient-start: #fdf2f4;
        --haru-gradient-end: #e8f4f8;
        --font-display: 'Outfit', sans-serif;
        --font-body: 'Outfit', sans-serif;
        --font-accent: 'Crimson Pro', serif;
        --radius-sm: 8px;
        --radius-md: 12px;
        --radius-lg: 20px;
        --radius-xl: 28px;
        --transition-fast: 0.2s cubic-bezier(0.4, 0, 0.2, 1);
        --transition-smooth: 0.4s cubic-bezier(0.4, 0, 0.2, 1);
        --transition-bounce: 0.5s cubic-bezier(0.34, 1.56, 0.64, 1);
    }

    /* Dark theme variables */
    .dark {
        --haru-primary: #d4919a;
        --haru-primary-deep: #c77a85;
        --haru-accent: #7eb8da;
        --haru-accent-deep: #9ecae8;
        --haru-warm: #3d3530;
        --haru-text: #f7fafc;
        --haru-text-soft: #cbd5e0;
        --haru-surface: rgba(26, 32, 44, 0.8);
        --haru-surface-elevated: rgba(45, 55, 72, 0.9);
        --haru-border: rgba(212, 145, 154, 0.25);
        --haru-shadow: rgba(0, 0, 0, 0.3);
        --haru-glow: rgba(212, 145, 154, 0.3);
        --haru-gradient-start: #1a202c;
        --haru-gradient-end: #1e2836;
    }

    /* ═══════════════════════════════════════════════════════════════
       BASE STYLES & BACKGROUND
    ═══════════════════════════════════════════════════════════════ */

    .gradio-container {
        font-family: var(--font-body) !important;
        background: linear-gradient(135deg, var(--haru-gradient-start) 0%, var(--haru-gradient-end) 50%, var(--haru-gradient-start) 100%) !important;
        background-attachment: fixed !important;
        min-height: 100vh !important;
        position: relative !important;
    }

    /* Animated background orbs */
    .gradio-container::before {
        content: '';
        position: fixed;
        top: -50%;
        left: -50%;
        width: 200%;
        height: 200%;
        background:
            radial-gradient(circle at 20% 80%, rgba(232, 180, 184, 0.12) 0%, transparent 50%),
            radial-gradient(circle at 80% 20%, rgba(126, 184, 218, 0.1) 0%, transparent 50%),
            radial-gradient(circle at 40% 40%, rgba(245, 230, 211, 0.08) 0%, transparent 40%);
        animation: floatOrbs 30s ease-in-out infinite;
        pointer-events: none !important;
        z-index: -1 !important;
    }

    @keyframes floatOrbs {
        0%, 100% { transform: translate(0, 0) rotate(0deg); }
        33% { transform: translate(30px, -30px) rotate(120deg); }
        66% { transform: translate(-20px, 20px) rotate(240deg); }
    }

    /* Main content wrapper */
    .gradio-container > .main,
    .gradio-container .wrap,
    .gradio-container .contain {
        position: relative !important;
        z-index: 1 !important;
        pointer-events: auto !important;
    }

    /* Hide default footer */
    footer { visibility: hidden !important; }

    /* ═══════════════════════════════════════════════════════════════
       TYPOGRAPHY
    ═══════════════════════════════════════════════════════════════ */

    h1, h2, h3, h4, h5, h6 {
        font-family: var(--font-display) !important;
        font-weight: 600 !important;
        color: var(--haru-text) !important;
        letter-spacing: -0.02em !important;
    }

    label, .label-wrap, span {
        font-family: var(--font-body) !important;
        color: var(--haru-text-soft) !important;
        font-weight: 500 !important;
    }

    p, input, textarea, select, button {
        font-family: var(--font-body) !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       THEME TOGGLE BUTTON
    ═══════════════════════════════════════════════════════════════ */

    #theme-toggle-btn {
        position: fixed !important;
        top: 24px !important;
        right: 24px !important;
        width: 52px !important;
        height: 52px !important;
        border-radius: 50% !important;
        border: 1px solid var(--haru-border) !important;
        background: var(--haru-surface-elevated) !important;
        backdrop-filter: blur(20px) !important;
        -webkit-backdrop-filter: blur(20px) !important;
        color: var(--haru-text) !important;
        font-size: 22px !important;
        cursor: pointer !important;
        transition: var(--transition-bounce) !important;
        z-index: 9999 !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        box-shadow:
            0 4px 24px var(--haru-shadow),
            0 0 0 1px rgba(255, 255, 255, 0.1) inset !important;
    }

    #theme-toggle-btn:hover {
        transform: scale(1.1) rotate(15deg) !important;
        box-shadow:
            0 8px 32px var(--haru-shadow),
            0 0 20px var(--haru-glow) !important;
    }

    #theme-toggle-btn:active {
        transform: scale(0.95) !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       GLASS CARD CONTAINERS
    ═══════════════════════════════════════════════════════════════ */

    .gr-group, .gr-box, .gr-panel, .gr-form {
        background: var(--haru-surface) !important;
        backdrop-filter: blur(16px) !important;
        -webkit-backdrop-filter: blur(16px) !important;
        border: 1px solid var(--haru-border) !important;
        border-radius: var(--radius-lg) !important;
        box-shadow:
            0 4px 30px var(--haru-shadow),
            0 0 0 1px rgba(255, 255, 255, 0.05) inset !important;
        transition: var(--transition-smooth) !important;
        overflow: visible !important;
    }

    .gr-group:hover, .gr-box:hover {
        box-shadow:
            0 8px 40px var(--haru-shadow),
            0 0 0 1px var(--haru-border) !important;
    }

    /* Ensure all containers allow dropdown overflow */
    .gr-block, .gr-row, .gr-column, .contain, .wrap {
        overflow: visible !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       LANGUAGE SELECTION ROW
    ═══════════════════════════════════════════════════════════════ */

    #lang-row {
        display: grid !important;
        grid-template-columns: 1fr auto 1fr !important;
        align-items: center !important;
        gap: 16px !important;
        margin-bottom: 24px !important;
        padding: 20px !important;
        background: var(--haru-surface) !important;
        backdrop-filter: blur(16px) !important;
        border-radius: var(--radius-xl) !important;
        border: 1px solid var(--haru-border) !important;
        box-shadow: 0 4px 30px var(--haru-shadow) !important;
        position: relative !important;
        z-index: 1000 !important;
        overflow: visible !important;
    }

    #lang-row > div:first-child {
        grid-column: 1 !important;
    }

    #lang-row > div:last-child {
        grid-column: 3 !important;
    }

    /* Swap button */
    #swap-btn {
        grid-column: 2 !important;
        width: 56px !important;
        height: 56px !important;
        border-radius: 50% !important;
        background: linear-gradient(135deg, var(--haru-primary) 0%, var(--haru-accent) 100%) !important;
        border: none !important;
        color: white !important;
        font-size: 1.5rem !important;
        cursor: pointer !important;
        transition: var(--transition-bounce) !important;
        box-shadow:
            0 4px 20px rgba(232, 180, 184, 0.4),
            0 0 0 4px rgba(232, 180, 184, 0.1) !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
    }

    #swap-btn:hover {
        transform: scale(1.15) rotate(180deg) !important;
        box-shadow:
            0 8px 30px rgba(232, 180, 184, 0.5),
            0 0 0 8px rgba(232, 180, 184, 0.15) !important;
    }

    #swap-btn:active {
        transform: scale(1.05) rotate(180deg) !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       LANGUAGE DROPDOWN STYLES
    ═══════════════════════════════════════════════════════════════ */

    #lang-row .lang-dropdown,
    #lang-row .gr-dropdown {
        position: relative !important;
        z-index: 1001 !important;
        overflow: visible !important;
    }

    /* Dropdown input wrapper - ensure clickable */
    #lang-row .gr-dropdown > div {
        pointer-events: auto !important;
        cursor: pointer !important;
        overflow: visible !important;
    }

    /* Gradio 5.x specific - remove gap between input and dropdown list */
    #lang-row .gr-dropdown > div:has(ul),
    #lang-row .gr-dropdown > div:has([role="listbox"]) {
        margin-top: 0 !important;
        padding-top: 0 !important;
    }

    /* Fix dropdown options container positioning for Gradio 5 */
    #lang-row .options {
        margin-top: 0 !important;
        top: 100% !important;
    }

    /* Gradio 5.x - remove any gap in dropdown wrapper divs */
    #lang-row .gr-dropdown div[data-testid],
    #lang-row .gr-dropdown .wrap,
    #lang-row .gr-dropdown .container {
        margin-top: 0 !important;
        margin-bottom: 0 !important;
        gap: 0 !important;
    }

    /* Dropdown list container */
    #lang-row .gr-dropdown ul,
    #lang-row .gr-dropdown [role="listbox"] {
        display: flex !important;
        flex-wrap: wrap !important;
        gap: 6px !important;
        padding: 12px !important;
        max-height: 360px !important;
        overflow-y: auto !important;
        background: var(--haru-surface-elevated) !important;
        backdrop-filter: blur(20px) !important;
        -webkit-backdrop-filter: blur(20px) !important;
        border: 2px solid var(--haru-primary) !important;
        border-radius: var(--radius-lg) !important;
        box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3) !important;
        z-index: 99999 !important;
        position: absolute !important;
        top: 100% !important;
        left: 0 !important;
        width: max-content !important;
        max-width: 520px !important;
        min-width: 280px !important;
        margin-top: 0 !important;
    }

    /* Dropdown options */
    #lang-row .gr-dropdown li,
    #lang-row .gr-dropdown [role="option"] {
        flex: 0 0 calc(20% - 6px) !important;
        padding: 10px 6px !important;
        border-radius: var(--radius-md) !important;
        text-align: center !important;
        cursor: pointer !important;
        transition: var(--transition-fast) !important;
        font-size: 0.82rem !important;
        font-weight: 500 !important;
        color: var(--haru-text) !important;
        background: transparent !important;
        border: 1px solid transparent !important;
        min-height: 36px !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        pointer-events: auto !important;
    }

    #lang-row .gr-dropdown li:hover,
    #lang-row .gr-dropdown [role="option"]:hover {
        background: linear-gradient(135deg, rgba(232, 180, 184, 0.2) 0%, rgba(126, 184, 218, 0.2) 100%) !important;
        border-color: var(--haru-border) !important;
        transform: translateY(-1px) !important;
    }

    #lang-row .gr-dropdown li.selected,
    #lang-row .gr-dropdown li[aria-selected="true"],
    #lang-row .gr-dropdown [role="option"][aria-selected="true"] {
        background: linear-gradient(135deg, var(--haru-primary) 0%, var(--haru-accent) 100%) !important;
        color: white !important;
        font-weight: 600 !important;
        border-color: transparent !important;
        box-shadow: 0 4px 12px rgba(232, 180, 184, 0.3) !important;
    }

    /* Responsive dropdown */
    @media (max-width: 1024px) {
        #lang-row .gr-dropdown li,
        #lang-row .gr-dropdown [role="option"] {
            flex: 0 0 calc(25% - 6px) !important;
            font-size: 0.78rem !important;
        }
    }

    @media (max-width: 768px) {
        #lang-row {
            grid-template-columns: 1fr !important;
            grid-template-rows: auto auto auto !important;
            gap: 12px !important;
            padding: 16px !important;
        }

        #lang-row > div:first-child { grid-row: 1 !important; grid-column: 1 !important; }
        #swap-btn {
            grid-column: 1 !important;
            grid-row: 2 !important;
            justify-self: center !important;
            width: 48px !important;
            height: 48px !important;
        }
        #lang-row > div:last-child { grid-row: 3 !important; grid-column: 1 !important; }

        #lang-row .gr-dropdown li,
        #lang-row .gr-dropdown [role="option"] {
            flex: 0 0 calc(33.333% - 6px) !important;
            font-size: 0.75rem !important;
            padding: 8px 4px !important;
        }
    }

    @media (max-width: 480px) {
        #lang-row .gr-dropdown li,
        #lang-row .gr-dropdown [role="option"] {
            flex: 0 0 calc(50% - 6px) !important;
        }
    }

    /* ═══════════════════════════════════════════════════════════════
       CUSTOM LANGUAGE INPUT ROW
    ═══════════════════════════════════════════════════════════════ */

    /* Custom language wrapper - no extra spacing */
    #custom-lang-wrapper {
        gap: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
    }

    /* Custom language row styling */
    #custom-lang-row {
        display: flex !important;
        align-items: flex-end !important;
        gap: 12px !important;
        margin-top: -8px !important;
        margin-bottom: 16px !important;
        padding: 16px 20px !important;
        background: linear-gradient(135deg,
            rgba(232, 180, 184, 0.08) 0%,
            rgba(126, 184, 218, 0.08) 100%) !important;
        backdrop-filter: blur(12px) !important;
        border-radius: 0 0 var(--radius-xl) var(--radius-xl) !important;
        border: 1px solid var(--haru-border) !important;
        border-top: none !important;
        animation: slideDown 0.3s ease-out !important;
    }

    @keyframes slideDown {
        from {
            opacity: 0;
            transform: translateY(-10px);
        }
        to {
            opacity: 1;
            transform: translateY(0);
        }
    }

    #custom-lang-row .custom-lang-input input {
        background: var(--haru-surface-elevated) !important;
        border: 2px solid var(--haru-primary) !important;
        border-radius: var(--radius-md) !important;
        padding: 12px 16px !important;
        font-size: 0.95rem !important;
    }

    #custom-lang-row .custom-lang-input input:focus {
        box-shadow: 0 0 0 4px rgba(232, 180, 184, 0.2) !important;
    }

    #custom-lang-row .custom-lang-btn {
        min-width: 100px !important;
        height: 46px !important;
        border-radius: var(--radius-md) !important;
        font-weight: 600 !important;
        font-size: 0.9rem !important;
    }

    /* When custom-lang-wrapper is visible, adjust lang-row border */
    #lang-row:has(+ #custom-lang-wrapper:not([style*="display: none"])) {
        border-radius: var(--radius-xl) var(--radius-xl) 0 0 !important;
        margin-bottom: 0 !important;
    }

    @media (max-width: 640px) {
        #custom-lang-row {
            flex-direction: column !important;
            align-items: stretch !important;
        }

        #custom-lang-row .custom-lang-btn {
            width: 100% !important;
        }
    }

    /* ═══════════════════════════════════════════════════════════════
       MODEL & GLOSSARY ROW
    ═══════════════════════════════════════════════════════════════ */

    #model-glossary-row {
        display: grid !important;
        grid-template-columns: 1fr 1fr !important;
        gap: 20px !important;
        margin-bottom: 16px !important;
        align-items: start !important;
    }

    #model-glossary-row > div {
        min-width: 0 !important;
        overflow: visible !important;
    }

    #model-glossary-row .gr-dropdown {
        width: 100% !important;
    }

    @media (max-width: 640px) {
        #model-glossary-row {
            grid-template-columns: 1fr !important;
        }
    }

    /* Model column with inline refresh button */
    #model-column {
        position: relative !important;
    }

    #model-column > div:first-child label span {
        display: inline-flex !important;
        align-items: center !important;
    }

    #model-dropdown {
        width: 100% !important;
    }

    #model-refresh-btn {
        position: absolute !important;
        top: 8px !important;
        left: 48px !important;
        min-width: 22px !important;
        max-width: 22px !important;
        height: 22px !important;
        padding: 0 !important;
        border-radius: 50% !important;
        background: transparent !important;
        border: none !important;
        font-size: 13px !important;
        line-height: 1 !important;
        cursor: pointer !important;
        transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
        color: var(--haru-text-muted) !important;
        opacity: 0.6 !important;
        z-index: 10 !important;
        display: inline-flex !important;
        align-items: center !important;
        justify-content: center !important;
    }

    #model-refresh-btn:hover {
        background: var(--haru-primary-light) !important;
        color: var(--haru-primary) !important;
        opacity: 1 !important;
        transform: rotate(180deg) !important;
    }

    #model-refresh-btn:active {
        transform: rotate(360deg) !important;
    }

    /* Glossary upload wrapper - similar to custom language row */
    #glossary-upload-wrapper {
        gap: 0 !important;
        margin: 0 !important;
        padding: 0 !important;
    }

    #glossary-upload-row {
        margin-top: 12px !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       GLOSSARY UPLOAD AREA
    ═══════════════════════════════════════════════════════════════ */

    #glossary-upload {
        border: 2px dashed var(--haru-primary) !important;
        border-radius: var(--radius-lg) !important;
        padding: 32px 24px !important;
        background: linear-gradient(135deg,
            rgba(232, 180, 184, 0.08) 0%,
            rgba(126, 184, 218, 0.08) 100%) !important;
        transition: var(--transition-smooth) !important;
        margin-top: 12px !important;
        position: relative !important;
        overflow: hidden !important;
    }

    #glossary-upload::before {
        content: '';
        position: absolute;
        top: 0;
        left: -100%;
        width: 100%;
        height: 100%;
        background: linear-gradient(90deg,
            transparent,
            rgba(232, 180, 184, 0.1),
            transparent);
        transition: var(--transition-smooth);
    }

    #glossary-upload:hover {
        border-color: var(--haru-primary-deep) !important;
        background: linear-gradient(135deg,
            rgba(232, 180, 184, 0.15) 0%,
            rgba(126, 184, 218, 0.15) 100%) !important;
        transform: translateY(-3px) !important;
        box-shadow: 0 12px 40px rgba(232, 180, 184, 0.2) !important;
    }

    #glossary-upload:hover::before {
        left: 100%;
    }

    /* ═══════════════════════════════════════════════════════════════
       BUTTONS - Primary Action
    ═══════════════════════════════════════════════════════════════ */

    .gr-button {
        font-family: var(--font-display) !important;
        font-weight: 600 !important;
        letter-spacing: 0.02em !important;
        border-radius: var(--radius-md) !important;
        transition: var(--transition-bounce) !important;
        position: relative !important;
        overflow: hidden !important;
    }

    .gr-button-primary,
    button.primary {
        background: linear-gradient(135deg, var(--haru-primary) 0%, var(--haru-primary-deep) 50%, var(--haru-accent) 100%) !important;
        background-size: 200% 200% !important;
        animation: gradientShift 3s ease infinite !important;
        border: none !important;
        color: white !important;
        padding: 14px 32px !important;
        font-size: 1rem !important;
        box-shadow:
            0 4px 20px rgba(232, 180, 184, 0.4),
            0 0 0 1px rgba(255, 255, 255, 0.1) inset !important;
    }

    @keyframes gradientShift {
        0%, 100% { background-position: 0% 50%; }
        50% { background-position: 100% 50%; }
    }

    .gr-button-primary:hover,
    button.primary:hover {
        transform: translateY(-3px) scale(1.02) !important;
        box-shadow:
            0 8px 30px rgba(232, 180, 184, 0.5),
            0 0 0 1px rgba(255, 255, 255, 0.2) inset !important;
    }

    .gr-button-primary:active,
    button.primary:active {
        transform: translateY(-1px) scale(0.98) !important;
    }

    /* Secondary buttons */
    .gr-button-secondary,
    button.secondary {
        background: var(--haru-surface) !important;
        backdrop-filter: blur(10px) !important;
        border: 1px solid var(--haru-border) !important;
        color: var(--haru-text) !important;
    }

    .gr-button-secondary:hover,
    button.secondary:hover {
        background: var(--haru-surface-elevated) !important;
        border-color: var(--haru-primary) !important;
        transform: translateY(-2px) !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       INPUT FIELDS & TEXTAREAS
    ═══════════════════════════════════════════════════════════════ */

    /* Textbox container */
    .gr-textbox {
        background: transparent !important;
    }

    .gr-textbox > label {
        font-family: var(--font-display) !important;
        font-weight: 600 !important;
        font-size: 0.9rem !important;
        color: var(--haru-text) !important;
        margin-bottom: 8px !important;
        display: block !important;
    }

    input[type="text"],
    input[type="password"],
    textarea,
    .gr-textbox textarea,
    .gr-textbox input {
        background: var(--haru-surface) !important;
        border: 2px solid var(--haru-border) !important;
        border-radius: var(--radius-md) !important;
        color: var(--haru-text) !important;
        font-family: var(--font-body) !important;
        font-size: 0.95rem !important;
        transition: var(--transition-fast) !important;
        padding: 14px 18px !important;
        line-height: 1.5 !important;
        width: 100% !important;
        box-sizing: border-box !important;
    }

    input[type="text"]::placeholder,
    input[type="password"]::placeholder,
    textarea::placeholder,
    .gr-textbox textarea::placeholder,
    .gr-textbox input::placeholder {
        color: var(--haru-text-soft) !important;
        opacity: 0.6 !important;
        font-style: italic !important;
    }

    input[type="text"]:hover,
    input[type="password"]:hover,
    textarea:hover,
    .gr-textbox textarea:hover,
    .gr-textbox input:hover {
        border-color: var(--haru-primary) !important;
        background: var(--haru-surface-elevated) !important;
    }

    input[type="text"]:focus,
    input[type="password"]:focus,
    textarea:focus,
    .gr-textbox textarea:focus,
    .gr-textbox input:focus {
        border-color: var(--haru-primary) !important;
        background: var(--haru-surface-elevated) !important;
        box-shadow:
            0 0 0 4px rgba(232, 180, 184, 0.15),
            0 8px 25px var(--haru-shadow) !important;
        outline: none !important;
    }

    /* Status message special styling */
    .gr-textbox[data-testid="textbox"] textarea {
        font-family: 'SF Mono', 'Fira Code', 'JetBrains Mono', 'Consolas', monospace !important;
        font-size: 0.85rem !important;
        line-height: 1.7 !important;
        background: linear-gradient(135deg, var(--haru-surface) 0%, var(--haru-surface-elevated) 100%) !important;
        min-height: 100px !important;
    }

    /* API Key input special styling */
    input[type="password"],
    .gr-textbox input[type="password"] {
        font-family: 'SF Mono', monospace !important;
        letter-spacing: 2px !important;
    }

    /* Textarea resize handle */
    textarea {
        resize: vertical !important;
        min-height: 80px !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       DROPDOWNS (General)
    ═══════════════════════════════════════════════════════════════ */

    .gr-dropdown {
        border-radius: var(--radius-md) !important;
        position: relative !important;
        z-index: 50 !important;
    }

    .gr-dropdown > div:first-child {
        background: var(--haru-surface) !important;
        border: 2px solid var(--haru-border) !important;
        border-radius: var(--radius-md) !important;
        transition: var(--transition-fast) !important;
        cursor: pointer !important;
        padding: 2px !important;
    }

    .gr-dropdown > div:first-child:hover {
        border-color: var(--haru-primary) !important;
        background: var(--haru-surface-elevated) !important;
    }

    .gr-dropdown > div:first-child:focus-within {
        border-color: var(--haru-primary) !important;
        box-shadow: 0 0 0 4px rgba(232, 180, 184, 0.15) !important;
    }

    /* Dropdown input field */
    .gr-dropdown input {
        background: transparent !important;
        border: none !important;
        padding: 12px 14px !important;
        font-size: 0.95rem !important;
        cursor: pointer !important;
    }

    /* Dropdown arrow */
    .gr-dropdown svg {
        color: var(--haru-primary) !important;
        transition: var(--transition-fast) !important;
    }

    .gr-dropdown:hover svg {
        transform: translateY(2px) !important;
    }

    /* Dropdown list (general) */
    .gr-dropdown ul {
        background: var(--haru-surface-elevated) !important;
        backdrop-filter: blur(20px) !important;
        -webkit-backdrop-filter: blur(20px) !important;
        border: 2px solid var(--haru-primary) !important;
        border-radius: var(--radius-md) !important;
        box-shadow: 0 20px 60px rgba(0, 0, 0, 0.3) !important;
        z-index: 99999 !important;
        padding: 8px !important;
        margin-top: 0 !important;
        position: absolute !important;
    }

    .gr-dropdown li {
        padding: 10px 14px !important;
        border-radius: var(--radius-sm) !important;
        cursor: pointer !important;
        transition: var(--transition-fast) !important;
        font-size: 0.9rem !important;
        color: var(--haru-text) !important;
    }

    .gr-dropdown li:hover {
        background: linear-gradient(135deg, rgba(232, 180, 184, 0.15) 0%, rgba(126, 184, 218, 0.15) 100%) !important;
    }

    .gr-dropdown li.selected,
    .gr-dropdown li[aria-selected="true"] {
        background: linear-gradient(135deg, var(--haru-primary) 0%, var(--haru-accent) 100%) !important;
        color: white !important;
        font-weight: 600 !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       CHECKBOXES
    ═══════════════════════════════════════════════════════════════ */

    .gr-checkbox {
        transition: var(--transition-fast) !important;
    }

    .gr-checkbox label {
        display: flex !important;
        align-items: center !important;
        gap: 10px !important;
        cursor: pointer !important;
        padding: 8px 12px !important;
        border-radius: var(--radius-md) !important;
        transition: var(--transition-fast) !important;
    }

    .gr-checkbox label:hover {
        background: rgba(232, 180, 184, 0.08) !important;
    }

    .gr-checkbox input[type="checkbox"] {
        width: 20px !important;
        height: 20px !important;
        border-radius: 6px !important;
        border: 2px solid var(--haru-border) !important;
        transition: var(--transition-fast) !important;
        accent-color: var(--haru-primary) !important;
    }

    .gr-checkbox input[type="checkbox"]:checked {
        background: linear-gradient(135deg, var(--haru-primary) 0%, var(--haru-accent) 100%) !important;
        border-color: var(--haru-primary) !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       SLIDERS
    ═══════════════════════════════════════════════════════════════ */

    .gr-slider input[type="range"] {
        -webkit-appearance: none !important;
        appearance: none !important;
        height: 6px !important;
        border-radius: 3px !important;
        background: linear-gradient(90deg, var(--haru-primary) 0%, var(--haru-accent) 100%) !important;
        opacity: 0.3 !important;
        transition: var(--transition-fast) !important;
    }

    .gr-slider input[type="range"]:hover {
        opacity: 0.5 !important;
    }

    .gr-slider input[type="range"]::-webkit-slider-thumb {
        -webkit-appearance: none !important;
        appearance: none !important;
        width: 20px !important;
        height: 20px !important;
        border-radius: 50% !important;
        background: linear-gradient(135deg, var(--haru-primary) 0%, var(--haru-accent) 100%) !important;
        cursor: pointer !important;
        box-shadow: 0 2px 10px rgba(232, 180, 184, 0.4) !important;
        transition: var(--transition-bounce) !important;
    }

    .gr-slider input[type="range"]::-webkit-slider-thumb:hover {
        transform: scale(1.2) !important;
        box-shadow: 0 4px 20px rgba(232, 180, 184, 0.6) !important;
    }

    .gr-slider input[type="range"]::-moz-range-thumb {
        width: 20px !important;
        height: 20px !important;
        border-radius: 50% !important;
        background: linear-gradient(135deg, var(--haru-primary) 0%, var(--haru-accent) 100%) !important;
        cursor: pointer !important;
        border: none !important;
        box-shadow: 0 2px 10px rgba(232, 180, 184, 0.4) !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       FILE UPLOAD AREA
    ═══════════════════════════════════════════════════════════════ */

    .gr-file {
        border: 2px dashed var(--haru-border) !important;
        border-radius: var(--radius-lg) !important;
        background: var(--haru-surface) !important;
        transition: var(--transition-smooth) !important;
        position: relative !important;
    }

    .gr-file:hover {
        border-color: var(--haru-primary) !important;
        background: linear-gradient(135deg,
            rgba(232, 180, 184, 0.05) 0%,
            rgba(126, 184, 218, 0.05) 100%) !important;
        transform: translateY(-2px) !important;
        box-shadow: 0 8px 30px var(--haru-shadow) !important;
    }

    .gr-file.dragging {
        border-color: var(--haru-primary-deep) !important;
        background: linear-gradient(135deg,
            rgba(232, 180, 184, 0.12) 0%,
            rgba(126, 184, 218, 0.12) 100%) !important;
        box-shadow: 0 0 0 4px rgba(232, 180, 184, 0.2) !important;
    }

    /* ═══════════════════════════════════════════════════════════════
       CUSTOM SCROLLBAR
    ═══════════════════════════════════════════════════════════════ */

    ::-webkit-scrollbar {
        width: 8px;
        height: 8px;
    }

    ::-webkit-scrollbar-track {
        background: transparent;
        border-radius: 4px;
    }

    ::-webkit-scrollbar-thumb {
        background: linear-gradient(180deg, var(--haru-primary) 0%, var(--haru-accent) 100%);
        border-radius: 4px;
        opacity: 0.6;
    }

    ::-webkit-scrollbar-thumb:hover {
        opacity: 1;
    }

    /* ═══════════════════════════════════════════════════════════════
       LOADING & PROGRESS ANIMATIONS
    ═══════════════════════════════════════════════════════════════ */

    .generating {
        position: relative;
    }

    .generating::after {
        content: '';
        position: absolute;
        bottom: 0;
        left: 0;
        height: 3px;
        background: linear-gradient(90deg,
            var(--haru-primary),
            var(--haru-accent),
            var(--haru-primary));
        background-size: 200% 100%;
        animation: shimmer 1.5s ease-in-out infinite;
        border-radius: 2px;
        width: 100%;
    }

    @keyframes shimmer {
        0% { background-position: -200% 0; }
        100% { background-position: 200% 0; }
    }

    /* Pulse animation for active states */
    @keyframes pulse {
        0%, 100% { opacity: 1; }
        50% { opacity: 0.6; }
    }

    .processing {
        animation: pulse 2s ease-in-out infinite;
    }

    /* ═══════════════════════════════════════════════════════════════
       RESPONSIVE MOBILE ADJUSTMENTS
    ═══════════════════════════════════════════════════════════════ */

    @media (max-width: 768px) {
        #theme-toggle-btn {
            top: 12px !important;
            right: 12px !important;
            width: 44px !important;
            height: 44px !important;
            font-size: 18px !important;
        }

        .gr-button-primary,
        button.primary {
            padding: 12px 24px !important;
            font-size: 0.9rem !important;
        }
    }

    /* ═══════════════════════════════════════════════════════════════
       ENTRANCE ANIMATIONS
    ═══════════════════════════════════════════════════════════════ */

    @keyframes fadeInUp {
        from {
            opacity: 0;
            transform: translateY(20px);
        }
        to {
            opacity: 1;
            transform: translateY(0);
        }
    }

    .gr-group, .gr-box, #lang-row, #model-glossary-row {
        animation: fadeInUp 0.6s ease-out backwards;
    }

    .gr-group:nth-child(1) { animation-delay: 0.1s; }
    .gr-group:nth-child(2) { animation-delay: 0.2s; }
    .gr-group:nth-child(3) { animation-delay: 0.3s; }
    .gr-group:nth-child(4) { animation-delay: 0.4s; }

    #lang-row { animation-delay: 0.15s; }
    #model-glossary-row { animation-delay: 0.25s; }
    """


def create_header(app_title, encoded_image, mime_type, img_height):
    """Create app header with elegant styling"""
    return gr.HTML(f"""
    <div style="text-align: center; padding: 20px 0 30px; animation: fadeInUp 0.8s ease-out;">
        <h1 style="
            font-family: 'Outfit', sans-serif;
            font-size: 2.5rem;
            font-weight: 700;
            background: linear-gradient(135deg, #e8b4b8 0%, #7eb8da 100%);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
            background-clip: text;
            margin-bottom: 16px;
            letter-spacing: -0.02em;
        ">{app_title}</h1>
        <img src="data:{mime_type};base64,{encoded_image}" alt="{app_title} Logo"
             style="
                display: block;
                height: {img_height}px;
                width: auto;
                margin: 0 auto;
                filter: drop-shadow(0 8px 30px rgba(232, 180, 184, 0.3));
                animation: float 6s ease-in-out infinite;
             ">
    </div>
    <style>
        @keyframes float {{
            0%, 100% {{ transform: translateY(0px); }}
            50% {{ transform: translateY(-10px); }}
        }}
    </style>
    """)


def create_footer():
    """Create app footer with refined styling"""
    return gr.HTML("""
    <div style="
        position: fixed;
        bottom: 0;
        left: 0;
        width: 100%;
        text-align: center;
        padding: 16px 0;
        background: linear-gradient(to top, rgba(253, 242, 244, 0.95) 0%, transparent 100%);
        backdrop-filter: blur(10px);
        font-family: 'Outfit', sans-serif;
        font-size: 0.85rem;
        color: #4a5568;
        z-index: 100;
    ">
        <span style="opacity: 0.8;">Crafted with </span>
        <span style="color: #e8b4b8;">♥</span>
        <span style="opacity: 0.8;"> by </span>
        <span style="font-weight: 600; background: linear-gradient(135deg, #e8b4b8, #7eb8da); -webkit-background-clip: text; -webkit-text-fill-color: transparent;">Haruka-YANG</span>
        <span style="opacity: 0.5; margin: 0 8px;">|</span>
        <span style="opacity: 0.8;">Version 4.2</span>
        <span style="opacity: 0.5; margin: 0 8px;">|</span>
        <a href="https://github.com/YANG-Haruka/LinguaHaru" target="_blank"
           style="
               color: #7eb8da;
               text-decoration: none;
               font-weight: 500;
               transition: color 0.2s ease;
           "
           onmouseover="this.style.color='#e8b4b8'"
           onmouseout="this.style.color='#7eb8da'">
            GitHub →
        </a>
    </div>
    <style>
        .dark div[style*="position: fixed"][style*="bottom: 0"] {
            background: linear-gradient(to top, rgba(26, 32, 44, 0.95) 0%, transparent 100%) !important;
            color: #cbd5e0 !important;
        }
    </style>
    """)


def create_theme_toggle():
    """Create theme toggle button"""
    return gr.Button(
        "🌙",
        elem_id="theme-toggle-btn"
    )


def create_language_section(default_src_lang, default_dst_lang, get_label=None):
    """Create language selection section with i18n support"""
    # Default get_label function if none provided
    if get_label is None:
        get_label = lambda key: key

    custom_label = get_label("Add Custom Language")
    dropdown_choices = get_available_languages() + [custom_label]

    with gr.Row(elem_id="lang-row"):
        src_lang = gr.Dropdown(
            choices=dropdown_choices,
            label=get_label("Source Language"),
            value=default_src_lang,
            interactive=True,
            allow_custom_value=True,
            elem_classes=["lang-dropdown"]
        )
        swap_button = gr.Button(
            "⇄",
            elem_id="swap-btn",
            elem_classes="swap-button"
        )
        dst_lang = gr.Dropdown(
            choices=dropdown_choices,
            label=get_label("Target Language"),
            value=default_dst_lang,
            interactive=True,
            allow_custom_value=True,
            elem_classes=["lang-dropdown"]
        )

    # Custom language input section - use Column wrapper for better visibility control
    with gr.Column(elem_id="custom-lang-wrapper", visible=False) as custom_lang_row:
        with gr.Row(elem_id="custom-lang-row"):
            custom_lang_input = gr.Textbox(
                label=get_label("New Language Name"),
                placeholder=get_label("Language Name Placeholder"),
                scale=3,
                elem_classes=["custom-lang-input"]
            )
            add_lang_button = gr.Button(
                get_label("Create Language"),
                scale=1,
                variant="primary",
                elem_classes=["custom-lang-btn"]
            )

    return src_lang, swap_button, dst_lang, custom_lang_input, add_lang_button, custom_lang_row


def create_settings_section(config):
    """Create settings section"""
    initial_lan_mode = config.get("lan_mode", False)
    initial_default_online = config.get("default_online", False)
    initial_max_retries = config.get("max_retries", 4)
    initial_thread_count_online = config.get("default_thread_count_online", 2)
    initial_thread_count_offline = config.get("default_thread_count_offline", 4)
    initial_thread_count = initial_thread_count_online if initial_default_online else initial_thread_count_offline
    initial_excel_mode_2 = config.get("excel_mode_2", False)
    initial_excel_bilingual_mode = config.get("excel_bilingual_mode", False)
    initial_word_bilingual_mode = config.get("word_bilingual_mode", False)

    initial_show_mode_switch = config.get("show_mode_switch", True)
    initial_show_lan_mode = config.get("show_lan_mode", True)
    initial_show_max_retries = config.get("show_max_retries", True)
    initial_show_thread_count = config.get("show_thread_count", True)

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

        excel_bilingual_checkbox = gr.Checkbox(
            label="Use Excel Bilingual Mode",
            value=initial_excel_bilingual_mode,
            visible=False
        )

        word_bilingual_checkbox = gr.Checkbox(
            label="Use Word Bilingual Mode",
            value=initial_word_bilingual_mode,
            visible=False
        )

    return (use_online_model, lan_mode_checkbox, max_retries_slider,
            thread_count_slider, excel_mode_checkbox, excel_bilingual_checkbox, word_bilingual_checkbox)


def create_model_glossary_section(config, local_models, online_models, get_glossary_files_func, get_default_glossary_func, get_label=None):
    """Create model and glossary selection section"""
    if get_label is None:
        get_label = lambda key: key

    initial_default_online = config.get("default_online", False)
    initial_show_model_selection = config.get("show_model_selection", True)
    initial_show_glossary = config.get("show_glossary", True)

    # Add glossary option with translated label
    add_glossary_label = get_label("Add Glossary")
    glossary_choices = get_glossary_files_func() + [add_glossary_label]

    with gr.Row(elem_id="model-glossary-row"):
        with gr.Column(scale=1, elem_id="model-column"):
            model_choice = gr.Dropdown(
                choices=local_models if not initial_default_online else online_models,
                label="Models",
                value=local_models[0] if not initial_default_online and local_models else (
                    online_models[0] if initial_default_online and online_models else None
                ),
                visible=initial_show_model_selection,
                allow_custom_value=True,
                elem_id="model-dropdown"
            )
            model_refresh_btn = gr.Button(
                "⟳",
                visible=initial_show_model_selection,
                elem_id="model-refresh-btn",
                min_width=24
            )

        with gr.Column(scale=1, visible=initial_show_glossary):
            glossary_choice = gr.Dropdown(
                choices=glossary_choices,
                label=get_label("Glossary"),
                value=get_default_glossary_func(),
                interactive=True,
                visible=initial_show_glossary,
                elem_id="glossary-dropdown"
            )

    # Upload row - appears below when "Add Glossary" is selected
    with gr.Column(elem_id="glossary-upload-wrapper", visible=False) as glossary_upload_row:
        with gr.Row(elem_id="glossary-upload-row"):
            glossary_upload_file = gr.File(
                label=get_label("Drop CSV file here"),
                file_types=[".csv"],
                elem_id="glossary-upload"
            )

    return (model_choice, model_refresh_btn, glossary_choice, glossary_upload_row, glossary_upload_file)


def create_main_interface(config):
    """Create main translation interface"""
    initial_default_online = config.get("default_online", False)

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
        translate_button = gr.Button("Translate", variant="primary")
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
        'excel_bilingual_mode_state': gr.State(config.get("excel_bilingual_mode", False)),
        'word_bilingual_mode_state': gr.State(config.get("word_bilingual_mode", False)),
        'thread_count_state': gr.State(config.get("default_thread_count_online", 2) if config.get("default_online", False) else config.get("default_thread_count_offline", 4))
    }

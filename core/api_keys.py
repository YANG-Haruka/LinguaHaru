# Shared per-provider API-key storage used by BOTH the Web (Gradio) app and the
# Qt desktop app, so a key entered in one place works in the other.
#
# Keys are stored per PROVIDER (not per model) under mykeys/<provider>.json, so
# models from the same company share one key (e.g. DeepSeek Flash and Pro).
import os
import json

from core.log_config import app_logger

# Repo root = parent of this config/ directory.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def get_mykeys_dir():
    """Return the mykeys directory (created if missing)."""
    mykeys_dir = os.path.join(_REPO_ROOT, "data", "mykeys")
    os.makedirs(mykeys_dir, exist_ok=True)
    return mykeys_dir


def provider_of(model_name):
    """Group API keys by provider so models from one company share a key
    (e.g. DeepSeek Flash and Pro use the same DeepSeek key). The provider is the
    text in the leading parentheses of the config name — "(Deepseek) ..." ->
    "Deepseek" — otherwise the name itself (e.g. "Custom")."""
    s = str(model_name or "").strip()
    if not s:
        return "default"
    if s.startswith("(") and ")" in s:
        return s[1:s.index(")")].strip() or "default"
    return s


def sanitize_model_name(model_name):
    """Sanitize a name into a valid filename."""
    if not model_name:
        return "default"
    invalid_chars = '<>:"/\\|?*'
    sanitized = model_name
    for char in invalid_chars:
        sanitized = sanitized.replace(char, '_')
    sanitized = sanitized.replace('(', '').replace(')', '').replace(' ', '_')
    return sanitized.strip('_') or "default"


def _key_file(model_name):
    return os.path.join(get_mykeys_dir(), f"{sanitize_model_name(provider_of(model_name))}.json")


def load_api_key_for_model(model_name):
    """Load the API key for a model's provider (shared across that provider's models)."""
    key_file = _key_file(model_name)
    try:
        if os.path.exists(key_file):
            with open(key_file, 'r', encoding='utf-8') as f:
                return json.load(f).get("api_key", "")
    except (json.JSONDecodeError, IOError) as e:
        app_logger.warning(f"Failed to load API key for {model_name}: {e}")
    return ""


def save_api_key_for_model(model_name, api_key):
    """Save the API key for a model's provider (shared across that provider's models)."""
    provider = provider_of(model_name)
    try:
        with open(_key_file(model_name), 'w', encoding='utf-8') as f:
            json.dump({"provider": provider, "api_key": api_key}, f, ensure_ascii=False, indent=2)
        app_logger.info(f"API key saved for provider: {provider}")
    except IOError as e:
        app_logger.error(f"Failed to save API key for {model_name}: {e}")


def delete_api_key_for_model(model_name):
    """Delete the API key file for a model's provider."""
    key_file = _key_file(model_name)
    try:
        if os.path.exists(key_file):
            os.remove(key_file)
            app_logger.info(f"API key deleted for provider: {provider_of(model_name)}")
    except IOError as e:
        app_logger.error(f"Failed to delete API key for {model_name}: {e}")

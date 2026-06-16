# Shared per-provider API-key storage used by BOTH the Web (Gradio) app and the
# Qt desktop app, so a key entered in one place works in the other.
#
# Keys are stored per PROVIDER (not per model) under mykeys/<provider>.json, so
# models from the same company share one key (e.g. DeepSeek Flash and Pro).
import os
import json
import base64

from core.log_config import app_logger
from core.paths import DATA_DIR


# --- at-rest protection -----------------------------------------------------
# On Windows, encrypt the key with DPAPI (CryptProtectData) so it is bound to
# the current OS user — another machine/user that copies the file off disk can't
# read it. On POSIX we fall back to plaintext + 0600 file permissions. Either
# way the file format stays JSON and old plaintext files keep loading.

def _dpapi_protect(plaintext):
    """Return base64 DPAPI ciphertext for a string, or None if unavailable."""
    if os.name != "nt" or not plaintext:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        data = plaintext.encode("utf-8")
        blob_in = _BLOB(len(data), ctypes.cast(ctypes.create_string_buffer(data),
                                               ctypes.POINTER(ctypes.c_char)))
        blob_out = _BLOB()
        if not ctypes.windll.crypt32.CryptProtectData(
                ctypes.byref(blob_in), None, None, None, None, 0,
                ctypes.byref(blob_out)):
            return None
        try:
            raw = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return base64.b64encode(raw).decode("ascii")
    except Exception as e:  # noqa: BLE001
        app_logger.warning(f"DPAPI protect unavailable, storing key in plaintext: {e}")
        return None


def _dpapi_unprotect(b64):
    """Decrypt base64 DPAPI ciphertext back to a string, or None on failure."""
    if os.name != "nt" or not b64:
        return None
    try:
        import ctypes
        from ctypes import wintypes

        class _BLOB(ctypes.Structure):
            _fields_ = [("cbData", wintypes.DWORD),
                        ("pbData", ctypes.POINTER(ctypes.c_char))]

        raw = base64.b64decode(b64)
        blob_in = _BLOB(len(raw), ctypes.cast(ctypes.create_string_buffer(raw),
                                              ctypes.POINTER(ctypes.c_char)))
        blob_out = _BLOB()
        if not ctypes.windll.crypt32.CryptUnprotectData(
                ctypes.byref(blob_in), None, None, None, None, 0,
                ctypes.byref(blob_out)):
            return None
        try:
            out = ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            ctypes.windll.kernel32.LocalFree(blob_out.pbData)
        return out.decode("utf-8")
    except Exception as e:  # noqa: BLE001
        app_logger.warning(f"DPAPI unprotect failed: {e}")
        return None


def _restrict_perms(path):
    """Best-effort owner-only file permissions (effective on POSIX)."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def get_mykeys_dir():
    """Return the mykeys directory (created if missing). Anchored to the writable
    runtime DATA_DIR so a frozen build stores keys next to the exe, not in the
    ephemeral read-only bundle."""
    mykeys_dir = os.path.join(DATA_DIR, "mykeys")
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
                data = json.load(f)
            enc = data.get("api_key_enc")
            if enc:  # DPAPI-encrypted (Windows); fall through to "" if undecryptable
                return _dpapi_unprotect(enc) or ""
            return data.get("api_key", "")  # legacy/plaintext
    except (json.JSONDecodeError, IOError) as e:
        app_logger.warning(f"Failed to load API key for {model_name}: {e}")
    return ""


def save_api_key_for_model(model_name, api_key):
    """Save the API key for a model's provider (shared across that provider's models)."""
    provider = provider_of(model_name)
    enc = _dpapi_protect(api_key)
    if enc:
        payload = {"provider": provider, "api_key_enc": enc, "enc": "dpapi"}
    else:  # POSIX / DPAPI unavailable: plaintext, but lock down file perms
        payload = {"provider": provider, "api_key": api_key}
    try:
        path = _key_file(model_name)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        _restrict_perms(path)
        app_logger.info(f"API key saved for provider: {provider}"
                        f"{' (DPAPI-encrypted)' if enc else ''}")
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

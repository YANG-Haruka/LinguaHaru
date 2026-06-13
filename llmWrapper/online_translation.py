import re
import logging
import json
import os
import threading
import time
from collections import deque
from openai import OpenAI
from config.log_config import app_logger

CONFIG_DIR = "config/api_config"


class HardApiError(Exception):
    """Unrecoverable API error (bad key/config/quota): retrying cannot help,
    so the whole translation should stop immediately instead of burning the
    1-hour retry budget."""


# --- multi-key rotation ----------------------------------------------------
# The API key field accepts multiple comma-separated keys. Calls rotate
# through them; keys that hit unrecoverable errors are quarantined and the
# run only hard-fails when no usable key remains.
_key_lock = threading.Lock()
_key_counter = 0
_bad_keys = set()


def _split_keys(api_key):
    return [k.strip() for k in (api_key or "").split(",") if k.strip()]


def _pick_api_key(api_key):
    global _key_counter
    keys = _split_keys(api_key)
    if len(keys) <= 1:
        return api_key
    with _key_lock:
        usable = [k for k in keys if k not in _bad_keys]
        if not usable:
            raise HardApiError("All API keys failed authentication/quota checks")
        _key_counter += 1
        return usable[_key_counter % len(usable)]


def _quarantine_key(api_key, used_key, reason):
    """Mark one key as dead. Returns True if other keys remain usable."""
    keys = _split_keys(api_key)
    with _key_lock:
        _bad_keys.add(used_key)
        remaining = [k for k in keys if k not in _bad_keys]
    app_logger.warning(f"API key ...{used_key[-6:]} quarantined ({reason}); "
                       f"{len(remaining)} key(s) remaining")
    return bool(remaining)


# --- RPM limiter -----------------------------------------------------------
class _RpmLimiter:
    """Sliding-window requests-per-minute limiter.

    Configured via "rpm_limit" in config/system_config.json (0/absent =
    unlimited). The value is read once per process."""
    _UNSET = object()

    def __init__(self):
        self.lock = threading.Lock()
        self.windows = {}
        self.limit = self._UNSET  # global limit, lazily loaded

    def _load_limit(self):
        try:
            with open(os.path.join("config", "system_config.json"), encoding="utf-8") as f:
                value = int(json.load(f).get("rpm_limit", 0))
            return value if value > 0 else None
        except Exception:
            return None

    def wait(self, key="_global", limit_override=None):
        """Per-model limits ("rpm" in the model's api_config json) override
        the global "rpm_limit"; each scope keeps its own sliding window."""
        if limit_override:
            limit = limit_override
        else:
            if self.limit is self._UNSET:
                self.limit = self._load_limit()
                if self.limit:
                    app_logger.info(f"RPM limit active: {self.limit} requests/minute")
            limit = self.limit
            key = "_global"
        if not limit:
            return
        while True:
            with self.lock:
                window = self.windows.setdefault(key, deque())
                now = time.time()
                while window and now - window[0] > 60:
                    window.popleft()
                if len(window) < limit:
                    window.append(now)
                    return
                sleep_for = 60 - (now - window[0]) + 0.05
            time.sleep(min(sleep_for, 5))


_rpm_limiter = _RpmLimiter()

# Error substrings that mean "retrying with the same key cannot succeed"
_HARD_ERROR_MARKERS = ("unauthorized", "401", "invalid api key", "invalid_api_key",
                       "incorrect api key", "403", "forbidden",
                       "insufficient", "quota", "exceeded your current quota")

def load_model_config(model):
    """
    Load the JSON config for the given model name.
    """
    json_path = os.path.join(CONFIG_DIR, f"{model}.json")
    if not os.path.exists(json_path):
        app_logger.error(f"Model config file not found: {json_path}")
        return None

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config
    except json.JSONDecodeError:
        app_logger.error(f"Failed to parse JSON file: {json_path}")
        return None

def fix_json_format(text):
    """
    Fix the JSON format of the response text.
    Handles various cases of non-standard JSON from LLM responses.
    """
    # Remove any markdown code block indicators
    text = re.sub(r'```json|```', '', text).strip()
    
    # Check for empty or invalid responses
    if not text:
        app_logger.error("Model returned empty response")
        return None
    
    # If only format markers, return None
    if text in ["```json", "```"]:
        app_logger.error("Model returned only format markers")
        return None
    
    # Case 1: Multiple JSON objects concatenated - the most common issue
    try:
        # Try to parse as a complete JSON object first
        json.loads(text)
        return text  # Already valid JSON
    except json.JSONDecodeError:
        # Not valid JSON, try to fix
        pass
        
    # Try to parse multiple JSON objects on separate lines
    try:
        # Extract all JSON-like objects
        objects = re.findall(r'(\{.*?\})', text, re.DOTALL)
        
        if not objects:
            # Fall back to simply wrapping everything in {}
            app_logger.warning("No JSON objects found in response, wrapping text")
            return json.dumps({"translated_text": text}, ensure_ascii=False)
            
        # Parse each object and merge them
        merged_data = {}
        for obj_str in objects:
            try:
                obj = json.loads(obj_str)
                merged_data.update(obj)
            except json.JSONDecodeError:
                app_logger.warning(f"Couldn't parse object: {obj_str}")
                
        if merged_data:
            return json.dumps(merged_data, ensure_ascii=False)
        else:
            # If all parsing failed, wrap the text in a JSON object with a default key
            app_logger.warning("Failed to parse any objects, using fallback")
            return json.dumps({"translated_text": text}, ensure_ascii=False)
            
    except Exception as e:
        app_logger.error(f"Error fixing JSON format: {e}")
        # Last resort: wrap everything in a JSON object
        return json.dumps({"translated_text": text}, ensure_ascii=False)
    
def translate_online(api_key, messages, model):
    """
    Perform translation using an online API with config from a JSON file.

    Returns:
        tuple: (translation_result, success_status, token_usage)
            - translation_result: Translated text or error message
            - success_status: True if API call successful, False if network/auth error
            - token_usage: dict with 'prompt_tokens', 'completion_tokens', 'total_tokens' or None
    """
    # Load model config
    model_config = load_model_config(model)
    if not model_config:
        return "Model configuration not found", False, None
        
    # Get API settings from the config
    base_url = model_config.get("base_url")
    api_model = model_config.get("model")
    top_p = model_config.get("top_p")
    temperature = model_config.get("temperature")
    presence_penalty = model_config.get("presence_penalty")
    frequency_penalty = model_config.get("frequency_penalty")
    thinking_type = model_config.get("thinking_type")

    if not base_url or not api_model:
        app_logger.error(f"Invalid model config: {model}")
        return "Invalid model configuration", False, None

    used_key = _pick_api_key(api_key)

    try:
        # Per-model rpm from the model config overrides the global limit
        model_rpm = model_config.get("rpm")
        if model_rpm:
            _rpm_limiter.wait(key=model, limit_override=int(model_rpm))
        else:
            _rpm_limiter.wait()
        # Initialize API client
        client = OpenAI(api_key=used_key, base_url=base_url)

        # Prepare parameters for the API call
        params = {
            "model": api_model,
            "messages": messages,
            "stream": False
        }
        
        # Prepare extra_body for non-standard parameters
        extra_body = {}
        
        # Add standard OpenAI parameters directly to params
        if top_p is not None:
            params["top_p"] = top_p
        if temperature is not None:
            params["temperature"] = temperature
        if presence_penalty is not None:
            params["presence_penalty"] = presence_penalty
        if frequency_penalty is not None:
            params["frequency_penalty"] = frequency_penalty
            
        # Add non-standard parameters to extra_body
        if thinking_type is not None:
            extra_body["thinking_type"] = thinking_type
        
        # Add extra_body if there are non-standard parameters
        if extra_body:
            params["extra_body"] = extra_body

        # Log the messages being sent to the API
        app_logger.debug(f"Sending messages to API: {json.dumps(messages, ensure_ascii=False, indent=2)}")
        app_logger.debug(f"API parameters: {json.dumps(params, ensure_ascii=False, indent=2)}")

        # Send request
        response = client.chat.completions.create(**params)
        
    except HardApiError:
        raise
    except Exception as e:
        error_msg = str(e).lower()
        app_logger.error(f"API call failed: {e}")

        # Hard errors: retrying the same key is pointless. Quarantine the key;
        # if other keys remain the caller retries (soft) with the next key,
        # otherwise abort the whole translation immediately.
        if any(marker in error_msg for marker in _HARD_ERROR_MARKERS):
            if len(_split_keys(api_key)) > 1 and _quarantine_key(api_key, used_key, str(e)[:80]):
                return f"API key quarantined, retrying with next key: {str(e)}", False, None
            raise HardApiError(f"Unrecoverable API error: {str(e)}")

        # Soft errors: rate limit / network / server hiccups - worth retrying
        if "connection" in error_msg or "network" in error_msg:
            return f"Network error: {str(e)}", False, None
        elif "rate limit" in error_msg or "429" in error_msg:
            return "Rate limit exceeded", False, None
        else:
            return f"API request failed: {str(e)}", False, None

    try:
        # Extract token usage from response
        token_usage = None
        if response and hasattr(response, 'usage') and response.usage:
            token_usage = {
                'prompt_tokens': getattr(response.usage, 'prompt_tokens', 0) or 0,
                'completion_tokens': getattr(response.usage, 'completion_tokens', 0) or 0,
                'total_tokens': getattr(response.usage, 'total_tokens', 0) or 0
            }
            app_logger.debug(f"Token usage: {token_usage}")

        if response and response.choices:
            app_logger.debug(f"API Response: {response}")
            translated_text = response.choices[0].message.content

            if not translated_text:
                app_logger.warning("Empty content in API response")
                return "Empty response from API", True, token_usage

            # Remove unnecessary system content
            clean_translated_text = re.sub(r'<think>.*?</think>', '', translated_text, flags=re.DOTALL).strip()

            # Fix JSON format for online API responses
            fixed_json = fix_json_format(clean_translated_text)

            if fixed_json is None:
                app_logger.error("Failed to parse API response format")
                return clean_translated_text, True, token_usage

            return fixed_json, True, token_usage
        else:
            app_logger.warning(f"Invalid response structure from {api_model}")
            return "Invalid API response structure", True, token_usage

    except Exception as e:
        app_logger.error(f"Response parsing failed: {e}")
        # Return raw response if available, otherwise error message
        if response:
            try:
                return str(response.choices[0].message.content), True, None
            except:
                pass
        return f"Error parsing API response: {str(e)}", True, None
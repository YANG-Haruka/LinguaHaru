import re
import json
import os
import threading
import time
from collections import deque
from openai import OpenAI
from core.log_config import app_logger
from core.paths import API_CONFIG_DIR as CONFIG_DIR, SYSTEM_CONFIG


class HardApiError(Exception):
    """Unrecoverable API error (bad key/config/quota): retrying cannot help,
    so the whole translation should stop immediately instead of burning the
    1-hour retry budget.

    ``category`` is a stable key the UI maps to a localized message:
    "insufficient_balance" / "invalid_key" / "server_error" / "api_error".
    """

    def __init__(self, message, category="api_error"):
        super().__init__(message)
        self.category = category


# --- multi-key rotation ----------------------------------------------------
# The API key field accepts multiple comma-separated keys. Calls rotate
# through them; keys that hit unrecoverable errors are quarantined and the
# run only hard-fails when no usable key remains.
_key_lock = threading.Lock()
_key_counter = 0
# key -> unix-ts until which it stays quarantined. TIME-LIMITED so a transient
# quota/429 blip doesn't permanently disable a key for the whole process life
# (this server can run for days); the key is retried automatically after the TTL.
_bad_keys = {}
_KEY_QUARANTINE_TTL = 600   # seconds


def _split_keys(api_key):
    return [k.strip() for k in (api_key or "").split(",") if k.strip()]


def _key_usable(k, now):
    return _bad_keys.get(k, 0) <= now


def _pick_api_key(api_key):
    global _key_counter
    keys = _split_keys(api_key)
    if len(keys) <= 1:
        return api_key
    now = time.time()
    with _key_lock:
        usable = [k for k in keys if _key_usable(k, now)]
        if not usable:
            raise HardApiError("All API keys failed authentication/quota checks",
                               category="invalid_key")
        _key_counter += 1
        return usable[_key_counter % len(usable)]


def _quarantine_key(api_key, used_key, reason):
    """Quarantine one key for _KEY_QUARANTINE_TTL seconds. Returns True if other
    keys remain usable right now."""
    keys = _split_keys(api_key)
    now = time.time()
    with _key_lock:
        _bad_keys[used_key] = now + _KEY_QUARANTINE_TTL
        remaining = [k for k in keys if _key_usable(k, now)]
    app_logger.warning(f"API key ...{used_key[-6:]} quarantined for "
                       f"{_KEY_QUARANTINE_TTL}s ({reason}); {len(remaining)} key(s) usable now")
    return bool(remaining)


# --- RPM limiter -----------------------------------------------------------
# Conservative safety net for models that declare no per-model "rpm" when the
# user hasn't set a global "rpm_limit" either. Stops a freshly-added model from
# hammering a strict provider into HTTP 429. DeepSeek isn't affected — its
# api_config files set per-model rpm from DeepSeek's published concurrency caps
# (flash=2500 / pro=500; DeepSeek limits concurrency, not RPM). To go unlimited,
# set "rpm_limit": 0 in system_config.json.
_DEFAULT_RPM = 60


class _RpmLimiter:
    """Sliding-window requests-per-minute limiter.

    Resolution of the global limit: an explicit "rpm_limit" in
    config/system_config.json wins (0 = unlimited); if the key is absent we fall
    back to the conservative _DEFAULT_RPM safety net. Per-model "rpm" in a
    model's api_config json overrides this entirely. The value is read once per
    process."""
    _UNSET = object()

    def __init__(self):
        self.lock = threading.Lock()
        self.windows = {}
        self.limit = self._UNSET  # global limit, lazily loaded

    def _load_limit(self):
        try:
            with open(SYSTEM_CONFIG, encoding="utf-8") as f:
                cfg = json.load(f)
            if "rpm_limit" in cfg:                       # explicit user choice
                value = int(cfg.get("rpm_limit") or 0)
                return value if value > 0 else None      # explicit 0 = unlimited
            return _DEFAULT_RPM                           # unconfigured -> safety net
        except Exception:
            return _DEFAULT_RPM

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


def reset_rpm_limit_cache():
    """Drop the cached global RPM so the next request re-reads system_config —
    lets a Settings change to rpm_limit take effect without a process restart.
    Also drops the adaptive concurrency limiter so a new max_api_concurrency
    ceiling is picked up."""
    _rpm_limiter.limit = _RpmLimiter._UNSET
    global _api_limiter
    _api_limiter = None  # re-read the concurrency ceiling on next request


# --- global API concurrency cap -------------------------------------------- #
# Without this, the Web layer (up to 6 tasks) x per-model threads (Flash=16)
# could fire dozens of simultaneous requests at the provider. This bounds the
# TOTAL in-flight LLM requests across the whole process, complementing the
# per-minute RPM window. 16 is a safer default — 32 caused timeout storms when
# several videos translated at once. Raise via config "max_api_concurrency".
_DEFAULT_API_CONCURRENCY = 16


class _AdaptiveLimiter:
    """AIMD concurrency limiter for LLM calls. Drop in N files (10 or 1000) and
    it self-tunes: on a timeout / 429 it HALVES the allowed in-flight requests
    (multiplicative decrease), and after a streak of successes it grows back by 1
    (additive increase) up to the configured ceiling. So the provider is never
    flooded into a timeout storm regardless of how many tasks are queued."""

    def __init__(self, start, lo, hi):
        self._cv = threading.Condition()
        self._limit = max(lo, min(start, hi))
        self._lo, self._hi = lo, hi
        self._active = 0
        self._ok = 0

    def acquire(self):
        with self._cv:
            while self._active >= self._limit:
                self._cv.wait()
            self._active += 1

    def release(self):
        with self._cv:
            self._active = max(0, self._active - 1)
            self._cv.notify()

    def record_ok(self):
        with self._cv:
            self._ok += 1
            if self._ok >= 12 and self._limit < self._hi:
                self._limit += 1
                self._ok = 0
                self._cv.notify()

    def record_overload(self):
        with self._cv:
            self._ok = 0
            self._limit = max(self._lo, self._limit // 2)

    @property
    def limit(self):
        return self._limit


_api_limiter = None
_api_sem_lock = threading.Lock()


def _get_api_limiter():
    global _api_limiter
    with _api_sem_lock:
        if _api_limiter is None:
            try:
                with open(SYSTEM_CONFIG, encoding="utf-8") as f:
                    hi = int(json.load(f).get("max_api_concurrency", _DEFAULT_API_CONCURRENCY))
            except Exception:
                hi = _DEFAULT_API_CONCURRENCY
            hi = max(1, hi)
            # Start gently (≤8) and ramp up only if the provider keeps up.
            _api_limiter = _AdaptiveLimiter(start=min(8, hi), lo=2, hi=hi)
        return _api_limiter


# --- adaptive backoff on HTTP 429 (per model) ------------------------------ #
# When a provider returns 429, honor its Retry-After (or a default) by parking
# new requests for that model until the cooldown elapses — i.e. the limiter
# learns the real limit instead of hammering on.
_cooldowns = {}          # model -> epoch until which to back off
_cooldown_lock = threading.Lock()


def _cooldown_wait(model, max_wait=120):
    """Block until this model's cooldown (set from Retry-After / 429 / 5xx) truly
    expires — in small chunks (so a re-extended cooldown is honored), capped at
    max_wait so a pathological cooldown can't hang a worker forever."""
    waited = 0.0
    while waited < max_wait:
        with _cooldown_lock:
            until = _cooldowns.get(model, 0)
        remaining = until - time.time()
        if remaining <= 0:
            return
        chunk = min(remaining, 2.0)
        time.sleep(chunk)
        waited += chunk


def _set_cooldown(model, seconds):
    with _cooldown_lock:
        _cooldowns[model] = max(_cooldowns.get(model, 0), time.time() + max(1, seconds))


def _parse_retry_after(exc, default=10):
    """Pull Retry-After (seconds) from an OpenAI/HTTP exception, else default."""
    try:
        hdrs = getattr(getattr(exc, "response", None), "headers", None) or {}
        val = hdrs.get("retry-after") or hdrs.get("Retry-After")
        if val:
            return int(float(val))
    except Exception:
        pass
    return default


def _http_status(exc):
    """HTTP status code from an OpenAI SDK / httpx exception, or None. Prefer the
    structured attribute over scraping the message text."""
    s = getattr(exc, "status_code", None)
    if isinstance(s, int):
        return s
    s = getattr(getattr(exc, "response", None), "status_code", None)
    return s if isinstance(s, int) else None


def _classify_api_exception(exc):
    """Classify an API exception by SDK type + HTTP status FIRST (robust), with
    the message string only as a last-resort fallback. Returns one of:
    'timeout' | 'rate_limit' | 'server' | 'hard' | 'connection' | 'unknown'."""
    try:
        import openai
        if isinstance(exc, openai.APITimeoutError):
            return "timeout"
        if isinstance(exc, openai.APIConnectionError):
            return "connection"
        if isinstance(exc, openai.RateLimitError):
            return "rate_limit"
        if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
            return "hard"
    except Exception:  # noqa: BLE001 — openai missing/old; fall through to status/text
        pass
    status = _http_status(exc)
    if status == 429:
        return "rate_limit"
    if status in (401, 402, 403):
        return "hard"
    if status in (500, 502, 503, 504):
        return "server"
    msg = str(exc).lower()
    if "timed out" in msg or "timeout" in msg:
        return "timeout"
    if "rate limit" in msg or "429" in msg or "too many requests" in msg:
        return "rate_limit"
    if ("500" in msg or "502" in msg or "503" in msg or "internal server error" in msg
            or "service unavailable" in msg or "overloaded" in msg or "bad gateway" in msg):
        return "server"
    if any(marker in msg for marker in _HARD_ERROR_MARKERS):
        return "hard"
    if "connection" in msg or "network" in msg:
        return "connection"
    return "unknown"

# Error substrings that mean "retrying with the same key cannot succeed"
_HARD_ERROR_MARKERS = ("unauthorized", "401", "invalid api key", "invalid_api_key",
                       "incorrect api key", "authentication fails", "403", "forbidden",
                       "insufficient", "insufficient balance", "quota",
                       "exceeded your current quota", "402")


def classify_fatal_error(error_msg):
    """Map a provider error message to a stable category the UI localizes.

    DeepSeek/OpenAI codes (api-docs.deepseek.com, platform.openai.com):
      402 / "Insufficient Balance"      -> insufficient_balance  (most important)
      401 / 403 / auth / invalid key    -> invalid_key
      otherwise                         -> api_error
    (500/503 server errors stay RETRYABLE — they're transient, not fatal.)
    """
    m = (error_msg or "").lower()
    if ("402" in m or "insufficient balance" in m or "insufficient_balance" in m
            or "insufficient funds" in m or "insufficient_quota" in m
            or "quota" in m or "exceeded your current quota" in m
            or "insufficient" in m):
        return "insufficient_balance"
    if ("401" in m or "403" in m or "unauthorized" in m or "forbidden" in m
            or "invalid api key" in m or "invalid_api_key" in m
            or "incorrect api key" in m or "authentication" in m
            or "api key" in m):
        return "invalid_key"
    return "api_error"

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

def _sanitize_model_filename(model_id):
    """Make a model id safe to use as a Windows/Unix filename."""
    sanitized = model_id
    for char in '<>:"/\\|?*':
        sanitized = sanitized.replace(char, '_')
    return sanitized.strip() or "model"


def fetch_models_into_configs(selected_model, api_key, timeout=5):
    """
    Query the selected model config's base_url via the OpenAI-compatible
    GET /models endpoint and write a "(Fetched) <id>.json" config (copying
    base_url and generation params from the selected config, with "model"
    replaced) for every model id not already covered by a hand-written config.

    Re-fetching overwrites the same "(Fetched) ..." files, so the list
    self-dedupes. Failures are graceful: nothing is written.

    Returns:
        tuple: (added_count, error_message). error_message is None on success.
    """
    base_config = load_model_config(selected_model)
    if not base_config or not base_config.get("base_url"):
        return 0, f"No base_url in config for '{selected_model}'"

    keys = _split_keys(api_key)
    try:
        client = OpenAI(api_key=keys[0] if keys else (api_key or "-"),
                        base_url=base_config["base_url"], timeout=timeout)
        page = client.models.list()
    except Exception as e:
        app_logger.warning(f"Model list fetch failed for {selected_model}: {e}")
        return 0, f"Model list fetch failed: {e}"

    # Model ids already covered by hand-written (non-fetched) configs
    covered_ids = set()
    try:
        for fname in os.listdir(CONFIG_DIR):
            if not fname.endswith(".json") or fname.startswith("(Fetched)"):
                continue
            existing = load_model_config(os.path.splitext(fname)[0])
            if existing and existing.get("model"):
                covered_ids.add(existing["model"])
    except OSError as e:
        return 0, f"Cannot read config dir: {e}"

    added = 0
    for entry in getattr(page, "data", None) or []:
        model_id = getattr(entry, "id", None)
        if not model_id or model_id in covered_ids:
            continue
        new_config = dict(base_config)
        new_config["model"] = model_id
        target = os.path.join(CONFIG_DIR, f"(Fetched) {_sanitize_model_filename(model_id)}.json")
        try:
            with open(target, "w", encoding="utf-8") as f:
                json.dump(new_config, f, ensure_ascii=False, indent=4)
            added += 1
        except OSError as e:
            app_logger.warning(f"Could not write fetched model config {target}: {e}")

    app_logger.info(f"Fetched model list from {base_config['base_url']}: {added} config(s) written")
    return added, None


def _balanced_json_objects(text):
    """Top-level {...} substrings, respecting string literals + nesting. Unlike a
    non-greedy regex this never truncates a value that itself contains braces
    (ICU placeholders, code, nested objects)."""
    objs = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(text):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                objs.append(text[start:i + 1])
                start = -1
    return objs


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
        # Extract all balanced top-level objects (string/nesting aware).
        objects = _balanced_json_objects(text)

        if not objects:
            # Plain-text reply (e.g. the simple/live translation prompt asks for
            # exactly that) -> wrap it. Debug, not warning: it's normal + noisy.
            app_logger.debug("No JSON objects found in response, wrapping text")
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
    
def _disable_thinking_default():
    """Whether to turn off model 'thinking'/reasoning for translation by default.
    Translation never needs chain-of-thought; it only adds latency + cost.
    Set system_config "disable_thinking": false to opt out."""
    try:
        with open(SYSTEM_CONFIG, encoding="utf-8") as f:
            return bool(json.load(f).get("disable_thinking", True))
    except Exception:
        return True


def _json_object_mode_default():
    """Whether to request DeepSeek/OpenAI JSON mode (response_format json_object)
    for batch document/subtitle translation. Reduces format errors / truncation.
    Safe because every translate prompt mentions "JSON" (DeepSeek's requirement);
    _create_completion drops it if a provider rejects it. Opt out with
    system_config "json_object_mode": false."""
    try:
        with open(SYSTEM_CONFIG, encoding="utf-8") as f:
            return bool(json.load(f).get("json_object_mode", True))
    except Exception:
        return True


def debug_llm_io():
    """Whether to log FULL prompt + response to the project log. OFF by default
    so users' source/translated text and prompts are never written. Turn on with
    system_config "debug_llm_io": true only when deep-debugging the LLM I/O."""
    try:
        with open(SYSTEM_CONFIG, encoding="utf-8") as f:
            return bool(json.load(f).get("debug_llm_io", False))
    except Exception:
        return False


def _looks_like_param_error(err):
    msg = str(err).lower()
    return any(m in msg for m in (
        "thinking", "unexpected", "unrecognized", "unknown", "invalid_request",
        "invalid request", "extra_body", "not supported", "unsupported", "400"))


def _create_completion(client, params):
    """Call the chat API. If a speculative param a provider doesn't support is
    rejected (thinking-disable in extra_body, or response_format JSON mode), drop
    it and retry once so translation still works on every backend."""
    try:
        return client.chat.completions.create(**params)
    except Exception as e:
        if not _looks_like_param_error(e):
            raise
        retry = dict(params)
        changed = False
        eb = params.get("extra_body") or {}
        if "thinking" in eb:
            eb2 = {k: v for k, v in eb.items() if k != "thinking"}
            if eb2:
                retry["extra_body"] = eb2
            else:
                retry.pop("extra_body", None)
            changed = True
        if "response_format" in retry:
            retry.pop("response_format", None)
            changed = True
        if changed:
            app_logger.warning("Provider rejected an optional param; retrying without it")
            return client.chat.completions.create(**retry)
        raise


def translate_online(api_key, messages, model, mode_params=None, json_mode=False):
    """
    Perform translation using an online API with config from a JSON file.

    json_mode: when True AND enabled in config, request response_format
    json_object (batch document/subtitle path only — the prompt mentions "JSON").
    The plain-text real-time / quick-translate path must leave this False.

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
    # The active translation mode overrides the model-config sampling params
    # (e.g. "precise" pins temperature low for stable JSON/term consistency);
    # providers that reject custom sampling get neither.
    try:
        from core.translation_modes import resolve_sampling
        temperature, top_p = resolve_sampling(model_config, temperature, top_p, params=mode_params)
    except Exception:  # noqa: BLE001
        pass
    thinking_type = model_config.get("thinking_type")
    # Output cap. With larger input batches the reply (a translation ~ as long as
    # the source) can exceed a provider's small default (DeepSeek defaults to 4K),
    # truncating the JSON and forcing a retry. Let a model raise it (DeepSeek max
    # 8192). Only sent when configured, so providers that reject it are unaffected.
    max_completion_tokens = model_config.get("max_completion_tokens")

    if not base_url or not api_model:
        app_logger.error(f"Invalid model config: {model}")
        return "Invalid model configuration", False, None

    used_key = _pick_api_key(api_key)

    # Back off if this model is cooling down from a recent 429, then honor the
    # per-minute RPM window before consuming a global-concurrency slot.
    _cooldown_wait(model)
    model_rpm = model_config.get("rpm")
    if model_rpm:
        _rpm_limiter.wait(key=model, limit_override=int(model_rpm))
    else:
        _rpm_limiter.wait()
    _limiter = _get_api_limiter()
    _limiter.acquire()

    try:
        # Initialize API client. An explicit timeout means a stalled request
        # (common under heavy concurrency, e.g. several videos at once) fails and
        # retries in ~90s instead of hanging a worker on the SDK's ~10min default.
        client = OpenAI(api_key=used_key, base_url=base_url, timeout=90.0, max_retries=0)

        # Prepare parameters for the API call
        params = {
            "model": api_model,
            "messages": messages,
            "stream": False
        }
        
        # Prepare extra_body for non-standard parameters
        extra_body = {}
        
        # Add standard OpenAI parameters directly to params
        if max_completion_tokens is not None:
            try:
                params["max_tokens"] = int(max_completion_tokens)
            except (TypeError, ValueError):
                pass
        if top_p is not None:
            params["top_p"] = top_p
        if temperature is not None:
            params["temperature"] = temperature
        # presence/frequency penalty intentionally NOT sent: DeepSeek ignores them
        # in thinking mode (the default) and doesn't document them otherwise; 0.0
        # is a no-op anyway. (See translation-engine-redesign.md §1.)

        # DeepSeek/OpenAI JSON mode for the batch path (prompt mentions "JSON").
        # _create_completion drops it if the provider rejects it.
        if json_mode and _json_object_mode_default():
            params["response_format"] = {"type": "json_object"}

        # Thinking/reasoning control. A per-model "thinking_type" is passed
        # through verbatim; otherwise translation disables thinking by default
        # (faster + cheaper; the answer is unaffected). _create_completion falls
        # back to a plain call if the provider rejects the param.
        if thinking_type is not None:
            extra_body["thinking_type"] = thinking_type
        elif _disable_thinking_default():
            extra_body["thinking"] = {"type": "disabled"}

        # Add extra_body if there are non-standard parameters
        if extra_body:
            params["extra_body"] = extra_body

        # Log the messages being sent to the API
        # Full prompt is logged ONLY when explicitly debugging LLM I/O (it
        # contains the user's source text). Off by default for privacy.
        if debug_llm_io():
            app_logger.info(f"LLM request: {json.dumps(messages, ensure_ascii=False)}")

        # Send request (with thinking-disable fallback)
        response = _create_completion(client, params)
        _limiter.record_ok()   # the call returned -> the provider is keeping up

    except HardApiError:
        raise
    except Exception as e:
        # Classify by SDK exception type + HTTP status first (robust); the message
        # string is only a last-resort fallback inside _classify_api_exception.
        kind = _classify_api_exception(e)

        # Timeout: the provider is overloaded (often a flood of queued files).
        # Shrink concurrency (AIMD), brief cooldown, and retry — instead of
        # spamming errors. This is why dropping 100 files no longer storms.
        if kind == "timeout":
            _limiter.record_overload()
            _set_cooldown(model, 5)
            app_logger.warning(
                f"API timeout; reduced concurrency to {_limiter.limit}, will retry")
            return f"API timeout, retrying: {str(e)}", False, None

        app_logger.error(f"API call failed: {e}")

        # Rate limit (429): retryable — back off concurrency + RPM, park the model
        # for Retry-After seconds. (Checked before 'hard' so a 429 body that
        # contains "exceeded" isn't misread as a fatal quota error.)
        if kind == "rate_limit":
            cooldown = _parse_retry_after(e)
            _limiter.record_overload()
            _set_cooldown(model, cooldown)
            return f"Rate limit exceeded; backing off {cooldown}s", False, None

        # Server errors (500/502/503/504): transient per DeepSeek/OpenAI docs —
        # back off briefly and retry, don't abort.
        if kind == "server":
            _limiter.record_overload()
            _set_cooldown(model, 10)
            app_logger.warning(f"API server error (retrying): {str(e)[:120]}")
            return f"Server error, retrying: {str(e)}", False, None

        # Hard errors (401/402/403 / bad key / insufficient balance): retrying the
        # same key is pointless. Quarantine the key; if other keys remain the
        # caller retries (soft) with the next key, otherwise abort with a category
        # the UI turns into a clear message.
        if kind == "hard":
            if len(_split_keys(api_key)) > 1 and _quarantine_key(api_key, used_key, str(e)[:80]):
                return f"API key quarantined, retrying with next key: {str(e)}", False, None
            category = classify_fatal_error(str(e).lower())
            app_logger.error(
                f"FATAL API error [{category}] — aborting translation: {str(e)[:200]}")
            raise HardApiError(f"Unrecoverable API error: {str(e)}", category=category)

        # Connection / unknown: worth a retry.
        if kind == "connection":
            return f"Network error: {str(e)}", False, None
        return f"API request failed: {str(e)}", False, None
    finally:
        _limiter.release()

    try:
        # Extract token usage from response (incl. DeepSeek KV-cache telemetry:
        # cache-hit input tokens are ~50x cheaper, so surface hit/miss for cost).
        token_usage = None
        if response and hasattr(response, 'usage') and response.usage:
            u = response.usage
            token_usage = {
                'prompt_tokens': getattr(u, 'prompt_tokens', 0) or 0,
                'completion_tokens': getattr(u, 'completion_tokens', 0) or 0,
                'total_tokens': getattr(u, 'total_tokens', 0) or 0,
                'cache_hit_tokens': getattr(u, 'prompt_cache_hit_tokens', 0) or 0,
                'cache_miss_tokens': getattr(u, 'prompt_cache_miss_tokens', 0) or 0,
            }
        if response and response.choices:
            choice0 = response.choices[0]
            finish_reason = getattr(choice0, "finish_reason", None)
            # insufficient_system_resource = transient server interruption (per
            # DeepSeek docs) -> retryable like a 503: back off and re-queue.
            if finish_reason == "insufficient_system_resource":
                _limiter.record_overload()
                _set_cooldown(model, 10)
                app_logger.warning("finish_reason=insufficient_system_resource; retrying")
                return "Insufficient system resource, retrying", False, token_usage
            if finish_reason == "length":
                app_logger.warning("finish_reason=length: output truncated "
                                   "(valid items still partial-accepted; rest retried)")
            elif finish_reason == "content_filter":
                app_logger.warning("finish_reason=content_filter: some content filtered")
            translated_text = choice0.message.content
            # Full response only when debugging LLM I/O (contains translated
            # text); otherwise a concise summary: model + tokens + length.
            if debug_llm_io():
                app_logger.info(f"LLM response: {translated_text}")
            elif token_usage:
                app_logger.info(
                    f"LLM ok: {api_model} · {token_usage['total_tokens']} tok · "
                    f"{len(translated_text or '')} chars")

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
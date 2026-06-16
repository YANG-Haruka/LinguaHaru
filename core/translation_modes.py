"""Translation modes — a per-run profile (sampling params now; prompt rules and
QA hooks are declared for later use). The active mode is config key
`translation_mode` (default "precise").

Parameter priority: user advanced params > mode params > model-config defaults.
There is no user-advanced-param layer yet, so currently the mode's sampling
params OVERRIDE the model-config temperature/top_p — this is what lets the
default "precise" mode pin temperature low (≈0.1) even when a provider preset
ships a high value (e.g. DeepSeek's 1.3), which destabilizes document/JSON
translation.

Some providers reject non-default sampling params (e.g. Anthropic Claude
Opus 4.7+). For those, sampling is dropped entirely rather than sent.
"""
import json
import os

from core.paths import CONFIG_DIR
from core.log_config import app_logger

_PATH = os.path.join(CONFIG_DIR, "translation_modes.json")

# Built-in fallback if the config file is missing/invalid.
_BUILTIN = {
    "precise":  {"label": "精准", "temperature": 0.1, "top_p": 0.9},
    "natural":  {"label": "自然", "temperature": 0.4, "top_p": 0.95},
    "polish":   {"label": "润色", "temperature": 0.6, "top_p": 0.95},
    "subtitle": {"label": "字幕精简", "temperature": 0.25, "top_p": 0.9},
}
DEFAULT_MODE = "precise"


def load_modes():
    modes = {k: dict(v) for k, v in _BUILTIN.items()}
    try:
        with open(_PATH, encoding="utf-8") as f:
            user = json.load(f)
        for key, val in (user or {}).items():
            if isinstance(val, dict):
                modes[key] = {**modes.get(key, {}), **val}
    except FileNotFoundError:
        pass
    except Exception as e:  # noqa: BLE001
        app_logger.warning(f"translation_modes.json invalid, using built-ins: {e}")
    return modes


def get_active_mode():
    try:
        from core import backend
        mode = backend.get_config("translation_mode", DEFAULT_MODE)
    except Exception:  # noqa: BLE001
        mode = DEFAULT_MODE
    return mode if mode in load_modes() else DEFAULT_MODE


def active_params():
    return load_modes().get(get_active_mode(), _BUILTIN[DEFAULT_MODE])


def _sampling_supported(model_config):
    """False for providers that reject non-default temperature/top_p."""
    base = str(model_config.get("base_url") or "").lower()
    mid = str(model_config.get("model") or "").lower()
    if "anthropic" in base or "claude" in mid:
        return False
    return True


def resolve_sampling(model_config, cfg_temp, cfg_top_p, params=None):
    """(temperature, top_p) to actually send. The mode params override the
    model-config values; if the provider can't take custom sampling, returns
    (None, None) so the caller omits them. Pass `params` (a snapshot's mode
    params) to avoid re-reading the global active mode — important for
    concurrent/LAN tasks."""
    if not _sampling_supported(model_config):
        return None, None
    p = params if params is not None else active_params()
    temp = p.get("temperature", cfg_temp)
    top_p = p.get("top_p", cfg_top_p)
    return temp, top_p


def snapshot():
    """Capture the active translation options ONCE (at task start), so a
    concurrent task — or another LAN user changing the global config mid-run —
    can't perturb this task's sampling, second pass, QA, or context behavior.
    Low layers should use this snapshot instead of re-reading global config."""
    try:
        from core import backend
        cfg = backend.read_config()
    except Exception:  # noqa: BLE001
        cfg = {}
    return {
        "mode": get_active_mode(),
        "params": dict(active_params()),
        "tone": str(cfg.get("translation_tone", "") or ""),
        "length": str(cfg.get("translation_length", "") or ""),
        "style": str(cfg.get("translation_style", "") or "").strip(),
        "with_context": bool(cfg.get("translate_with_context", False)),
    }


def offline_temperature(default=0.3):
    """Sampling temperature for local (Ollama / LM Studio) translation."""
    return active_params().get("temperature", default)


def active_prompt_hint():
    """A one-line instruction for the active mode, appended to the system prompt
    so the mode actually changes translation behavior (not just sampling). Kept
    short so it never dominates the target-language system prompt."""
    return str(active_params().get("prompt_hint", "")).strip()


_TONES = {
    "formal": "Use a formal, professional tone.",
    "casual": "Use a casual, conversational tone.",
}
_LENGTHS = {
    "keep": "Keep each translation close in length to its source.",
    "expand": "You may lengthen the translation where it improves clarity and flow.",
    "short": "Make each translation as concise as possible.",
}


def active_advanced_hint():
    """Optional one-line modifiers from the Advanced area (tone / length / free
    style guide), appended after the mode hint. Empty when nothing is set."""
    try:
        from core import backend
        tone = str(backend.get_config("translation_tone", "") or "")
        length = str(backend.get_config("translation_length", "") or "")
        style = str(backend.get_config("translation_style", "") or "").strip()
    except Exception:  # noqa: BLE001
        return ""
    parts = []
    if tone in _TONES:
        parts.append(_TONES[tone])
    if length in _LENGTHS:
        parts.append(_LENGTHS[length])
    if style:
        parts.append("Follow this style guide: " + style)
    return " ".join(parts)


def active_second_pass():
    """The active mode's second-pass step name (e.g. 'polish_target'), or '' if
    the mode runs a single pass."""
    return str(active_params().get("second_pass", "")).strip()

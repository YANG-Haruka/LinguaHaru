"""Approximate cost estimation for a finished translation run.

Token prices are USD per 1,000,000 tokens as (input, output). DeepSeek numbers
are the official rates (cache-MISS input + output — the conservative upper bound;
real cost is lower when prompt caching hits). Other providers are approximations,
edit as needed. `estimate_cost` converts to the UI language's currency using a
LIVE exchange rate (fetched + cached ~12h, with an offline fallback): zh → CNY,
ja → JPY, everything else → USD.

Sources: https://api-docs.deepseek.com/quick_start/pricing
         https://open.er-api.com  (FX, no key)
"""
import json
import os
import tempfile
import time
import urllib.request

# model-name substring (lowercased) -> (input_usd_per_1M, output_usd_per_1M)
# Order matters: more specific keys first.
_PRICES = {
    # DeepSeek — official (cache-miss input / output)
    "deepseek-v4-flash": (0.14, 0.28),
    "deepseek-v4-pro": (0.435, 0.87),
    "deepseek-reasoner": (0.14, 0.28),   # = flash (thinking), deprecating 2026-07
    "deepseek-chat": (0.14, 0.28),       # = flash (non-thinking)
    "deepseek": (0.14, 0.28),
    # Others — approximate, edit to taste
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4o": (2.50, 10.00),
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "o4-mini": (1.10, 4.40),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini": (0.30, 2.50),
    "claude-3-5-haiku": (0.80, 4.00),
    "claude": (3.00, 15.00),
    "qwen": (0.20, 0.60),
    "glm": (0.10, 0.30),
}
_DEFAULT_PRICE = (0.50, 1.50)   # unknown model fallback

_CCY_BY_LANG = {"zh": "CNY", "zh-Hant": "CNY", "ja": "JPY"}
_SYMBOL = {"CNY": "¥", "JPY": "¥", "USD": "$"}

# --- live exchange rates (USD -> currency), cached to a temp file ------------ #
_FX_CACHE = os.path.join(tempfile.gettempdir(), "linguaharu_fx.json")
_FX_TTL = 12 * 3600
_FX_FALLBACK = {"USD": 1.0, "CNY": 7.2, "JPY": 155.0}   # used only if offline


def _fetch_fx():
    """USD->{CNY,JPY,USD} from a free no-key API; None if all sources fail."""
    sources = (
        "https://open.er-api.com/v6/latest/USD",
        "https://api.frankfurter.dev/v1/latest?base=USD&symbols=CNY,JPY",
    )
    for url in sources:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "LinguaHaru"})
            with urllib.request.urlopen(req, timeout=4) as r:
                rates = (json.load(r) or {}).get("rates", {})
            if rates.get("CNY") and rates.get("JPY"):
                return {"USD": 1.0, "CNY": float(rates["CNY"]), "JPY": float(rates["JPY"])}
        except Exception:
            continue
    return None


def _get_rates():
    """Fresh rates if cache is young; else fetch; else stale cache; else fallback."""
    now = time.time()
    try:
        with open(_FX_CACHE, encoding="utf-8") as f:
            cached = json.load(f)
        if now - cached.get("ts", 0) < _FX_TTL and cached.get("rates"):
            return cached["rates"]
    except Exception:
        cached = None

    fresh = _fetch_fx()
    if fresh:
        try:                       # atomic write so a concurrent reader never sees a torn file
            tmp = f"{_FX_CACHE}.{os.getpid()}.tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"ts": now, "rates": fresh}, f)
            os.replace(tmp, _FX_CACHE)
        except Exception:
            pass
        return fresh
    if cached and cached.get("rates"):     # stale but better than nothing
        return cached["rates"]
    return _FX_FALLBACK


def _price_for(model):
    m = (model or "").lower()
    for key, price in _PRICES.items():
        if key in m:
            return price
    return _DEFAULT_PRICE


def estimate_cost(model, prompt_tokens, completion_tokens, ui_lang="en"):
    """Return (amount, symbol, currency_code) — approximate run cost in the UI
    language's currency, using a live exchange rate. Offline/local models ~0."""
    pin, pout = _price_for(model)
    usd = (prompt_tokens or 0) / 1_000_000 * pin + (completion_tokens or 0) / 1_000_000 * pout
    ccy = _CCY_BY_LANG.get(ui_lang, "USD")
    rate = _get_rates().get(ccy, _FX_FALLBACK.get(ccy, 1.0))
    return usd * rate, _SYMBOL[ccy], ccy

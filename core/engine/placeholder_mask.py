"""PRE-LLM placeholder masking.

Protects machine tokens (printf specifiers, shell/template vars, single-brace
ICU placeholders) inside otherwise-translatable strings from being altered or
translated by the LLM: each token is replaced with a neutral private-use
sentinel before translation and restored byte-identically afterwards.

Example:
    "Hello %s, you have {count} new messages"
the ``%s`` and ``{count}`` come back exactly as they went in.

Design notes
------------
* Sentinels use Unicode Private-Use-Area delimiters U+E000 / U+E001 wrapping a
  decimal index, e.g. ``\\uE000 3 \\uE001``. PUA codepoints carry no linguistic
  meaning, so the model leaves them alone, and they can never collide with the
  reserved pipeline markers (``{{...}}``, ``[formula_n]``, ``␊``/``␍``).
* The single-brace ``{...}`` matcher explicitly refuses to touch any
  ``{{...}}`` double-brace marker (HLINK / INLINE / FIELD / FOOTNOTE / MATH).
* Masking is a no-op fast-path when a string has no detectable token (the
  overwhelming common case for normal prose) — zero behavior change there.
* Honors the ``mask_placeholders`` flag in system_config (DEFAULT TRUE).
"""

import json
import re

from core.paths import SYSTEM_CONFIG

# Private-use sentinel delimiters. The model will not translate PUA codepoints,
# and they cannot appear in any reserved marker.
_SENT_OPEN = ""
_SENT_CLOSE = ""


def _sentinel(i):
    return f"{_SENT_OPEN}{i}{_SENT_CLOSE}"


# Matches a sentinel and captures its index (for unmask).
_SENTINEL_RE = re.compile(re.escape(_SENT_OPEN) + r"(\d+)" + re.escape(_SENT_CLOSE))

# printf / C-style conversion specifiers, including:
#   %%               literal percent
#   %s %d %i %f %x   plain conversions
#   %.2f %05d        flags / width / precision
#   %1$s             positional argument
# We require the spec to end in a known conversion letter so a bare '%' in
# prose ("50% off") is NOT matched.
_PRINTF_RE = re.compile(
    r"%(?:%|"                       # %% literal
    r"(?:\d+\$)?"                   # optional positional: 1$
    r"[-+ 0#]*"                     # flags
    r"\d*"                          # width
    r"(?:\.\d+)?"                   # precision
    r"[diouxXeEfFgGaAcspn])"       # conversion letter
)

# ${name} and $name (word chars only). ${...} first so it wins over $name.
_SHELL_BRACED_RE = re.compile(r"\$\{\w+\}")
_SHELL_BARE_RE = re.compile(r"\$\w+")


def _find_single_brace_spans(text):
    """Return (start, end) spans of balanced single-brace ``{...}`` groups,
    skipping any double-brace ``{{...}}`` reserved markers.

    A ``{`` that is immediately preceded OR followed by another ``{``/``}`` is
    part of a double-brace marker and must never be masked.
    """
    spans = []
    n = len(text)
    i = 0
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        # Skip double-brace markers: '{{' opens a reserved marker.
        if i + 1 < n and text[i + 1] == "{":
            i += 2
            continue
        # A '{' immediately preceded by '{' or '}' belongs to a marker.
        if i > 0 and text[i - 1] in "{}":
            i += 1
            continue
        # Scan for the matching close brace, tracking nesting (ICU plural).
        depth = 0
        j = i
        closed = False
        while j < n:
            c = text[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    closed = True
                    break
            j += 1
        if not closed:
            break  # unbalanced; leave the rest untouched
        # Reject if the char right after the close brace is another '}', which
        # would make this the inner of a '}}' reserved-marker tail.
        if j + 1 < n and text[j + 1] == "}":
            i = j + 1
            continue
        spans.append((i, j + 1))
        i = j + 1
    return spans


def _mask_enabled():
    try:
        with open(SYSTEM_CONFIG, encoding="utf-8") as f:
            return bool(json.load(f).get("mask_placeholders", True))
    except Exception:  # noqa: BLE001 - config missing/corrupt -> default on
        return True


def _has_token(text):
    """Cheap pre-check for the no-token fast path."""
    return ("%" in text) or ("$" in text) or ("{" in text)


def _token_spans(text):
    """Non-overlapping (start, end) spans of every machine token in ``text``
    (printf / shell / single-brace ICU), left-to-right, longest-wins on overlap.
    Shared by mask() and extract_tokens() so validation sees exactly what masking
    protected."""
    spans = []  # (start, end)
    for rx in (_PRINTF_RE, _SHELL_BRACED_RE, _SHELL_BARE_RE):
        for m in rx.finditer(text):
            spans.append((m.start(), m.end()))
    spans.extend(_find_single_brace_spans(text))
    if not spans:
        return []
    # Sort and drop overlaps (earlier/longer wins). ${name} (braced) is added
    # before $name (bare) and starts earlier, so it survives the overlap purge.
    spans.sort(key=lambda s: (s[0], -(s[1] - s[0])))
    chosen = []
    last_end = -1
    for start, end in spans:
        if start >= last_end:
            chosen.append((start, end))
            last_end = end
    return chosen


def extract_tokens(text):
    """The machine tokens (``%s``, ``${var}``, ``{count}`` …) in ``text`` as a
    list of literal strings, for round-trip validation: a translation should
    carry the same multiset back. Empty for non-str / no-token input."""
    if not isinstance(text, str) or not _has_token(text):
        return []
    return [text[s:e] for s, e in _token_spans(text)]


def mask(text):
    """Replace detected machine tokens with neutral sentinels.

    Returns ``(masked_text, mapping)`` where mapping is a dict
    ``{index: original_token}``. When masking is disabled, the string has no
    detectable token, or the input is not a non-empty str, returns the input
    unchanged with an empty mapping (fast path, zero allocation churn).
    """
    if not isinstance(text, str) or not text:
        return text, {}
    if not _mask_enabled() or not _has_token(text):
        return text, {}

    chosen = _token_spans(text)
    if not chosen:
        return text, {}

    out = []
    mapping = {}
    cursor = 0
    for idx, (start, end) in enumerate(chosen):
        out.append(text[cursor:start])
        out.append(_sentinel(idx))
        mapping[idx] = text[start:end]
        cursor = end
    out.append(text[cursor:])
    return "".join(out), mapping


def unmask(text, mapping):
    """Restore original tokens from ``mapping`` ({index: token}).

    Robust to a model that dropped or duplicated a sentinel: every sentinel
    present is replaced with its token (duplicates each restore correctly);
    sentinels whose index is missing from the mapping are left as-is rather
    than crashing. Missing (dropped) sentinels simply don't appear.
    """
    if not mapping or not isinstance(text, str) or _SENT_OPEN not in text:
        return text

    def _sub(m):
        idx = int(m.group(1))
        return mapping.get(idx, m.group(0))

    return _SENTINEL_RE.sub(_sub, text)

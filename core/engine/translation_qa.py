"""Mode-aware translation QA — lightweight, NON-blocking post-checks.

After a translation finishes, each translation mode declares a `qa` list (see
config/translation_modes.json). This module runs those checks over the restored
result items (each {count_src, original, translated, ...}) and returns warnings.
It NEVER raises and NEVER blocks/retries a translation — it only surfaces issues
(logged + written to qa.json) so the user can spot problems.

Checks:
- placeholders:    original vs translated must carry the same placeholder set
                   ({name} ${var} %s {0} {{token}}).
- length_ratio:    flag translations absurdly longer/shorter than the source.
- subtitle_length: flag translated lines too wide for one subtitle line.
- glossary_terms:  if a glossary source term is in the original, its target term
                   should appear in the translation.
Unknown qa keys are ignored.
"""
import re

# {{token}} | ${var} | %1$s | %s/%d/%f | {name}/{0}
_PH = re.compile(r"\{\{.*?\}\}|\$\{[^}]*\}|%\d+\$[a-zA-Z]|%[sdfgSDFG]|\{[^{}]*\}")

# Per-line subtitle width budget in "cells" (a full-width CJK char = 2 cells).
# Netflix-derived: JA 13 full-width chars/line (26 cells), zh/ko ~16 (32), Latin
# ~42 chars/line. Default 42. Was a single loose 84 for every language.
SUBTITLE_MAX_CELLS = 42
SUBTITLE_MAX_CELLS_BY_LANG = {
    "ja": 26, "zh": 32, "zh-Hant": 32, "zh-Hans": 32, "ko": 32,
}
SUBTITLE_MAX_LINES = 2

# Item "type" values that are actually subtitle cues (so width/line checks don't
# fire on ordinary document paragraphs, which are legitimately long).
_SUBTITLE_TYPES = {"subtitle", "srt", "vtt", "ass"}


def _subtitle_max_cells(dst_lang):
    base = (dst_lang or "").split("-")[0]
    return (SUBTITLE_MAX_CELLS_BY_LANG.get(dst_lang)
            or SUBTITLE_MAX_CELLS_BY_LANG.get(base)
            or SUBTITLE_MAX_CELLS)


def _placeholders(s):
    return sorted(_PH.findall(s or ""))


def _cells(s):
    n = 0
    for c in (s or ""):
        n += 2 if ("　" <= c <= "鿿" or "＀" <= c <= "￯") else 1
    return n


def _pairs(dst_items):
    out = []
    for it in (dst_items or []):
        if not isinstance(it, dict):
            continue
        out.append((it.get("count_src"),
                    str(it.get("original", "") or ""),
                    str(it.get("translated", "") or ""),
                    it.get("type", "text")))
    return out


def check_placeholders(pairs):
    return [k for k, src, dst, _typ in pairs
            if src and dst and _placeholders(src) != _placeholders(dst)]


def check_length_ratio(pairs, lo=0.25, hi=4.0, min_len=8):
    bad = []
    for k, src, dst, _typ in pairs:
        ls = len(src.strip())
        ld = len(dst.strip())
        if ls >= min_len and ld > 0:
            r = ld / ls
            if r < lo or r > hi:
                bad.append(k)
    return bad


def check_subtitle_length(pairs, dst_lang=None):
    """Flag subtitle cues whose translated line is too wide for the target
    language (per-language cell budget). Only subtitle-typed items are checked."""
    max_cells = _subtitle_max_cells(dst_lang)
    bad = []
    for k, _src, dst, typ in pairs:
        if typ not in _SUBTITLE_TYPES:
            continue
        lines = (dst or "").splitlines() or [dst or ""]
        if any(_cells(line) > max_cells for line in lines):
            bad.append(k)
    return bad


def check_subtitle_lines(pairs, max_lines=SUBTITLE_MAX_LINES):
    """Flag subtitle cues that wrap to more than max_lines lines."""
    bad = []
    for k, _src, dst, typ in pairs:
        if typ not in _SUBTITLE_TYPES:
            continue
        if len((dst or "").splitlines()) > max_lines:
            bad.append(k)
    return bad


def check_glossary_terms(pairs, glossary):
    bad = []
    for k, src, dst, _typ in pairs:
        for st, dt in (glossary or []):
            if st and dt and st in src and dt not in dst:
                bad.append({"id": k, "term": st, "expected": dt})
    return bad


def run(mode_qa, dst_items, glossary=None, dst_lang=None):
    """Return {check_name: [offending ids / details]} for the mode's qa list."""
    pairs = _pairs(dst_items)
    qa = set(mode_qa or [])
    warns = {}
    if "placeholders" in qa:
        b = check_placeholders(pairs)
        if b:
            warns["placeholders"] = b
    if "length_ratio" in qa:
        b = check_length_ratio(pairs)
        if b:
            warns["length_ratio"] = b
    if "subtitle_length" in qa:
        b = check_subtitle_length(pairs, dst_lang)
        if b:
            warns["subtitle_length"] = b
        b = check_subtitle_lines(pairs)
        if b:
            warns["subtitle_lines"] = b
    if "glossary_terms" in qa and glossary:
        b = check_glossary_terms(pairs, glossary)
        if b:
            warns["glossary_terms"] = b
    return warns

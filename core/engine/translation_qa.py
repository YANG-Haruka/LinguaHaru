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

SUBTITLE_MAX_CELLS = 84


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
                    str(it.get("translated", "") or "")))
    return out


def check_placeholders(pairs):
    return [k for k, src, dst in pairs
            if src and dst and _placeholders(src) != _placeholders(dst)]


def check_length_ratio(pairs, lo=0.25, hi=4.0, min_len=8):
    bad = []
    for k, src, dst in pairs:
        ls = len(src.strip())
        ld = len(dst.strip())
        if ls >= min_len and ld > 0:
            r = ld / ls
            if r < lo or r > hi:
                bad.append(k)
    return bad


def check_subtitle_length(pairs, max_cells=SUBTITLE_MAX_CELLS):
    bad = []
    for k, src, dst in pairs:
        lines = (dst or "").splitlines() or [dst or ""]
        if any(_cells(line) > max_cells for line in lines):
            bad.append(k)
    return bad


def check_glossary_terms(pairs, glossary):
    bad = []
    for k, src, dst in pairs:
        for st, dt in (glossary or []):
            if st and dt and st in src and dt not in dst:
                bad.append({"id": k, "term": st, "expected": dt})
    return bad


def run(mode_qa, dst_items, glossary=None):
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
        b = check_subtitle_length(pairs)
        if b:
            warns["subtitle_length"] = b
    if "glossary_terms" in qa and glossary:
        b = check_glossary_terms(pairs, glossary)
        if b:
            warns["glossary_terms"] = b
    return warns

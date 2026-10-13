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
- numbers:         "<number> <scale word>" (322 billion) must keep its value in
                   the translation (zh/ja/ko 万/亿, es/fr/de scale words; see
                   scale_number_mismatches).
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

# Reading-speed ceiling (characters per second) by target language. Netflix-
# derived: CJK count full characters (lower ceilings), Latin counts characters.
SUBTITLE_MAX_CPS = 20.0
SUBTITLE_MAX_CPS_BY_LANG = {"ja": 7.0, "zh": 9.0, "zh-Hant": 9.0, "zh-Hans": 9.0, "ko": 12.0}

# Item "type" values that are actually subtitle cues (so width/line checks don't
# fire on ordinary document paragraphs, which are legitimately long).
_SUBTITLE_TYPES = {"subtitle", "srt", "vtt", "ass"}


def _is_subtitle(item):
    """A cue is a subtitle if it carries timing (the SRT/VTT/ASS extractors emit
    start_time/end_time but no 'type'), or its type says so."""
    return ("start_time" in item and "end_time" in item) \
        or item.get("type") in _SUBTITLE_TYPES


def _ts_seconds(ts):
    """SRT/VTT timestamp 'HH:MM:SS,mmm' (or '.mmm') -> seconds; None if unpar.."""
    m = re.match(r"(\d{1,2}):(\d{2}):(\d{2})[,.](\d{1,3})", str(ts or "").strip())
    if not m:
        return None
    h, mm, s, ms = (int(g) for g in m.groups())
    return h * 3600 + mm * 60 + s + ms / 1000.0


def _subtitle_max_cps(dst_lang):
    base = (dst_lang or "").split("-")[0]
    return (SUBTITLE_MAX_CPS_BY_LANG.get(dst_lang)
            or SUBTITLE_MAX_CPS_BY_LANG.get(base)
            or SUBTITLE_MAX_CPS)


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


def _subtitle_cues(dst_items):
    """[(count_src, translated_text, item)] for items that are subtitle cues."""
    out = []
    for it in (dst_items or []):
        if isinstance(it, dict) and _is_subtitle(it):
            out.append((it.get("count_src"), str(it.get("translated", "") or ""), it))
    return out


def check_subtitle_length(dst_items, dst_lang=None):
    """Flag subtitle cues whose translated line is too wide for the target
    language (per-language cell budget)."""
    max_cells = _subtitle_max_cells(dst_lang)
    bad = []
    for k, dst, it in _subtitle_cues(dst_items):
        lines = (dst or "").splitlines() or [dst or ""]
        # An SRT sentence group is split back over cue_count cues on output,
        # so its joined line gets that many lines' budget.
        if any(_cells(line) > max_cells * it.get("cue_count", 1) for line in lines):
            bad.append(k)
    return bad


def check_subtitle_lines(dst_items, max_lines=SUBTITLE_MAX_LINES):
    """Flag subtitle cues that wrap to more than max_lines lines."""
    return [k for k, dst, _it in _subtitle_cues(dst_items)
            if len((dst or "").splitlines()) > max_lines]


def check_subtitle_cps(dst_items, dst_lang=None):
    """Flag subtitle cues whose translated text exceeds the language's reading
    speed (chars per second). Needs cue timing; cues without it are skipped."""
    max_cps = _subtitle_max_cps(dst_lang)
    bad = []
    for k, dst, it in _subtitle_cues(dst_items):
        s = _ts_seconds(it.get("start_time"))
        e = _ts_seconds(it.get("end_time"))
        if s is None or e is None or e <= s:
            continue
        chars = len((dst or "").replace("\n", "").strip())
        if chars and chars / (e - s) > max_cps:
            bad.append(k)
    return bad


# --- Numbers with a scale word ("322 billion") --------------------------------
# RULE: every "<number> <scale word>" in the source must reappear in the
# translation with the same VALUE (±0.5 %), in whatever form the target uses:
# "3220亿" / "3,220億" / "3220억" / "322 mil millones" / "322 Milliarden" /
# "322,000,000,000" all pass; "322 亿" (×10 off), "322 억", "322 バリオン" (no
# unit -> 322) fail. CJK counts in 万/亿 (10^4/10^8), not 10^3/10^6/10^9, which
# is exactly where small models slip. Only languages listed here are checked;
# any other pair is skipped (never a false positive from an unknown language).
_SCALES = {
    "en": {"trillion": 1e12, "billion": 1e9, "million": 1e6, "thousand": 1e3},
    "zh": {"万亿": 1e12, "千亿": 1e11, "百亿": 1e10, "十亿": 1e9, "亿": 1e8, "億": 1e8,
           "千万": 1e7, "百万": 1e6, "十万": 1e5, "万": 1e4, "萬": 1e4, "千": 1e3},
    "ja": {"兆": 1e12, "千億": 1e11, "百億": 1e10, "十億": 1e9, "億": 1e8,
           "千万": 1e7, "百万": 1e6, "十万": 1e5, "万": 1e4, "千": 1e3},
    "ko": {"조": 1e12, "천억": 1e11, "백억": 1e10, "십억": 1e9, "억": 1e8,
           "천만": 1e7, "백만": 1e6, "십만": 1e5, "만": 1e4, "천": 1e3},
    "es": {"billones": 1e12, "billón": 1e12, "mil millones": 1e9, "millardos": 1e9,
           "millardo": 1e9, "millones": 1e6, "millón": 1e6, "mil": 1e3},
    "fr": {"billions": 1e12, "billion": 1e12, "milliards": 1e9, "milliard": 1e9,
           "millions": 1e6, "million": 1e6, "mille": 1e3},
    "de": {"billionen": 1e12, "billion": 1e12, "milliarden": 1e9, "milliarde": 1e9,
           "mrd": 1e9, "millionen": 1e6, "million": 1e6, "mio": 1e6, "tausend": 1e3},
}
_SCALE_RX = {
    lang: re.compile(
        r"(?<![\d.,])(\d+(?:[.,\u00a0\u202f]\d+)*)[ \u00a0\u202f]?"
        r"(?:(" + "|".join(re.escape(w) for w in sorted(t, key=len, reverse=True))
        + r")" + ("" if lang in ("zh", "ja", "ko") else r"(?![^\W\d_])") + r")?",
        re.IGNORECASE)
    for lang, t in _SCALES.items()
}
# Native large units used when PRE-writing "322 billion" for a CJK target.
_NATIVE_UNITS = {
    "zh": ((1e12, "万亿"), (1e8, "亿"), (1e4, "万")),
    "zh-Hant": ((1e12, "兆"), (1e8, "億"), (1e4, "萬")),
    "ja": ((1e12, "兆"), (1e8, "億"), (1e4, "万")),
    "ko": ((1e12, "조"), (1e8, "억"), (1e4, "만")),
}
# A number just before the match makes it the end of a range ("1-2 billion").
_RANGE_LEFT = re.compile(r"\d\s*(?:[-–—~～]|to)\s*$", re.IGNORECASE)


def _base_lang(lang):
    return (lang or "").split("-")[0].lower()


def _num_values(s):
    """Every plausible value of a digit string: '.' or ',' may be the decimal
    point (the other one grouping in threes). '3,220' -> {3220, 3.22}."""
    s = s.replace("\u00a0", "").replace("\u202f", "")
    out = set()
    for dec, grp in ((".", ","), (",", ".")):
        parts = s.split(dec)
        if len(parts) > 2:
            continue
        groups = parts[0].split(grp)
        if any(len(g) != 3 for g in groups[1:]):
            continue
        try:
            out.add(float("".join(groups) + ("." + parts[1] if len(parts) == 2 else "")))
        except ValueError:
            pass
    return out


def _match_values(m, lang):
    scale = _SCALES[lang].get((m.group(2) or "").lower(), 1) if m.group(2) else 1
    return {n * scale for n in _num_values(m.group(1))}


def scale_number_mismatches(src, dst, src_lang, dst_lang):
    """Source '<number> <scale word>' expressions whose value is missing from
    the translation (see RULE above). [] when fine or the pair isn't covered."""
    s_rx = _SCALE_RX.get(_base_lang(src_lang))
    d_rx = _SCALE_RX.get(_base_lang(dst_lang))
    if not s_rx or not d_rx or not src or not dst:
        return []
    have = set()
    for m in d_rx.finditer(dst):
        have |= _match_values(m, _base_lang(dst_lang))
    bad = []
    for m in s_rx.finditer(src):
        if not m.group(2):
            continue
        want = _match_values(m, _base_lang(src_lang))
        if not any(abs(t - w) <= 0.005 * w for w in want for t in have):
            bad.append(m.group(0).strip())
    return bad


def localize_scale_numbers(text, src_lang, dst_lang):
    """PRE-LLM: rewrite source '<number> <scale word>' into the CJK target's own
    units so the model only has to copy it ("322 billion" -> "3220亿" for zh,
    "3220億" ja, "3220억" ko). Unchanged for other targets or ambiguous digits."""
    units = _NATIVE_UNITS.get(dst_lang) or (
        _NATIVE_UNITS["zh-Hant"] if dst_lang in ("zh-TW", "zh-HK") else
        _NATIVE_UNITS.get(_base_lang(dst_lang)))
    rx = _SCALE_RX.get(_base_lang(src_lang))
    if not units or not rx or not text or _base_lang(src_lang) == _base_lang(dst_lang):
        return text

    def _sub(m):
        # "1-2 billion" / "1 to 2 billion": converting only "2 billion" would
        # give "1-20亿" — leave ranges to the model.
        if _RANGE_LEFT.search(text, 0, m.start()):
            return m.group(0)
        vals = _match_values(m, _base_lang(src_lang)) if m.group(2) else ()
        if len(vals) != 1:
            return m.group(0)
        v = vals.pop()
        n, unit = v, ""
        for size, u in units:
            if v >= size:
                n, unit = v / size, u
                break
        return f"{n:.4f}".rstrip("0").rstrip(".") + unit

    return rx.sub(_sub, text)


def check_number_scale(pairs, src_lang, dst_lang):
    out = []
    for k, src, dst, _typ in pairs:
        b = scale_number_mismatches(src, dst, src_lang, dst_lang)
        if b:
            out.append({"id": k, "numbers": b})
    return out


def check_glossary_terms(pairs, glossary):
    bad = []
    for k, src, dst, _typ in pairs:
        for st, dt in (glossary or []):
            if st and dt and st in src and dt not in dst:
                bad.append({"id": k, "term": st, "expected": dt})
    return bad


def run(mode_qa, dst_items, glossary=None, dst_lang=None, src_lang=None):
    """Return {check_name: [offending ids / details]} for the mode's qa list."""
    pairs = _pairs(dst_items)
    qa = set(mode_qa or [])
    warns = {}
    if "numbers" in qa:
        b = check_number_scale(pairs, src_lang, dst_lang)
        if b:
            warns["numbers"] = b
    if "placeholders" in qa:
        b = check_placeholders(pairs)
        if b:
            warns["placeholders"] = b
    if "length_ratio" in qa:
        b = check_length_ratio(pairs)
        if b:
            warns["length_ratio"] = b
    if "subtitle_length" in qa:
        b = check_subtitle_length(dst_items, dst_lang)
        if b:
            warns["subtitle_length"] = b
        b = check_subtitle_lines(dst_items)
        if b:
            warns["subtitle_lines"] = b
        b = check_subtitle_cps(dst_items, dst_lang)
        if b:
            warns["subtitle_cps"] = b
    if "glossary_terms" in qa and glossary:
        b = check_glossary_terms(pairs, glossary)
        if b:
            warns["glossary_terms"] = b
    return warns

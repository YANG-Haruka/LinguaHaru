# AI glossary extraction (AiNiee-style): before translating, ask the LLM to
# scan the document for proper nouns / recurring terms and propose
# translations. The result is validated, merged with the user's glossary for
# this run (user terms win) and saved next to the output for review.
#
# Enabled via "auto_extract_glossary": true in config/system_config.json.
import csv
import json
import os
import re
import unicodedata

from core.log_config import app_logger
# NOTE: translate_online/translate_offline are imported lazily inside
# extract_glossary_terms() — importing them at module load pulls in `openai`,
# which made even _parse_terms() (and unit tests) fail with ModuleNotFoundError
# in environments without the online deps installed.

MAX_SAMPLE_CHARS = 9000
MAX_TERMS = 60
MIN_TERM_LEN = 2
MAX_TERM_LEN = 80

_EXTRACTION_PROMPT = (
    "You are a terminology extraction assistant. From the {src_lang} document "
    "sample below, extract up to {max_terms} terms that must be translated "
    "CONSISTENTLY across the document: proper nouns, person / product / "
    "organization / place names, and domain-specific recurring terms.\n"
    "Strict rules:\n"
    "- Each source term must appear VERBATIM in the sample (copy it exactly).\n"
    "- Extract terms, NOT whole sentences or phrases of common words.\n"
    "- Do NOT extract placeholders, variables, tags, URLs, emails, file paths, "
    "code, numbers, or punctuation-only strings.\n"
    "- Do NOT include generic everyday words that need no fixed translation.\n"
    "Propose a {dst_lang} translation for each term. Output ONLY a JSON array of "
    "[source_term, translated_term] pairs, with no explanations:\n"
    '[["term1", "translation1"], ["term2", "translation2"]]\n\n'
    "Document sample:\n{sample}"
)


def _norm(s):
    """Normalization key for dedup: NFKC (folds full/half-width), casefolded."""
    return unicodedata.normalize("NFKC", str(s)).strip().casefold()


def _build_sample(values):
    """Stratified sample (head + middle + tail) so terms that first appear late
    in long documents (new characters, later chapters) still get covered —
    instead of only the first MAX_SAMPLE_CHARS."""
    joined = "\n".join(v for v in values if v)
    if len(joined) <= MAX_SAMPLE_CHARS:
        return joined
    third = MAX_SAMPLE_CHARS // 3
    head = joined[:third]
    mid0 = max(0, len(joined) // 2 - third // 2)
    mid = joined[mid0:mid0 + third]
    tail = joined[-third:]
    return f"{head}\n…\n{mid}\n…\n{tail}"


_URL_RE = re.compile(r"https?://|www\.", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
_NUMERICISH_RE = re.compile(r"^[\d\s.,:%/v#x+\-—–()]+$", re.IGNORECASE)
_PLACEHOLDER_CH = set("{}$%<>")
_SENT_END = "。！？!?…"


def _looks_like_noise(src):
    """True if a candidate source term is something we should NOT treat as a
    glossary term: too short/long, a URL/email, numeric/version, a placeholder
    or code fragment, or a whole sentence rather than a term."""
    s = src.strip()
    if not (MIN_TERM_LEN <= len(s) <= MAX_TERM_LEN):
        return True
    if "\n" in s or "\t" in s:
        return True
    if _URL_RE.search(s) or _EMAIL_RE.search(s):
        return True
    if _NUMERICISH_RE.match(s):
        return True
    if any(c in _PLACEHOLDER_CH for c in s):
        return True
    if s[-1] in _SENT_END:                      # ends like a sentence
        return True
    # Whole-sentence/phrase heuristic: long latin terms with many words.
    if re.fullmatch(r"[\x00-\x7f]+", s) and len(s.split()) > 6:
        return True
    # CJK term that is really a sentence (long + contains sentence punctuation).
    if len(s) > 24 and any(c in s for c in "，、,;；"):
        return True
    return False


def _parse_terms(raw):
    """Parse the LLM's JSON array of [src, dst] pairs (or [{src,dst}]). No
    document validation here — that's done in _clean_terms with the corpus."""
    if not raw:
        return []
    match = re.search(r"\[.*\]", raw, re.DOTALL)
    if not match:
        return []
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return []
    terms = []
    for entry in data:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            src, dst = str(entry[0]).strip(), str(entry[1]).strip()
        elif isinstance(entry, dict):
            src = str(entry.get("src") or entry.get("source") or "").strip()
            dst = str(entry.get("dst") or entry.get("target") or "").strip()
        else:
            continue
        if src and dst and src != dst:
            terms.append((src, dst))
    return terms


def _clean_terms(terms, corpus):
    """Validate + dedup parsed terms: drop noise (URLs/numbers/sentences/…),
    keep only terms whose source actually appears in the document, and remove
    duplicate sources (normalized). Order preserved."""
    corpus_norm = _norm(corpus)
    seen = set()
    cleaned = []
    for src, dst in terms:
        if _looks_like_noise(src):
            continue
        if _norm(src) not in corpus_norm:        # must really occur in the doc
            continue
        key = _norm(src)
        if key in seen:
            continue
        seen.add(key)
        cleaned.append((src, dst))
        if len(cleaned) >= MAX_TERMS:
            break
    return cleaned


def extract_glossary_terms(values, model, use_online, api_key, src_lang, dst_lang,
                           check_stop=None):
    """One-shot LLM term extraction from document text values, validated against
    the document. `check_stop` (callable) is honored before the request so a
    pending Stop doesn't have to wait for extraction to even begin."""
    corpus = "\n".join(v for v in (values or []) if v)
    sample = _build_sample(values)
    if len(sample) < 50:
        return []

    if callable(check_stop):
        check_stop()   # raises if the user already requested stop

    prompt = _EXTRACTION_PROMPT.format(src_lang=src_lang, dst_lang=dst_lang,
                                       max_terms=MAX_TERMS, sample=sample)
    messages = [{"role": "user", "content": prompt}]
    try:
        if use_online:
            from core.llm.online_translation import translate_online
            raw, success, _ = translate_online(api_key, messages, model)
        else:
            from core.llm.offline_translation import translate_offline
            raw, success, _ = translate_offline(messages, model)
    except Exception as e:
        app_logger.warning(f"Glossary extraction call failed: {e}")
        return []
    if not success:
        app_logger.warning(f"Glossary extraction failed: {raw}")
        return []

    terms = _clean_terms(_parse_terms(raw), corpus)
    app_logger.info(f"AI glossary extraction kept {len(terms)} validated terms")
    return terms


def write_merged_glossary(terms, user_glossary_entries, output_path, src_lang, dst_lang):
    """Write user glossary entries + AI terms (user entries win on conflict).
    Dedup is normalized (case + full/half-width) so 'App' and 'app' don't both
    appear."""
    seen = {_norm(src) for src, _ in user_glossary_entries}
    merged = list(user_glossary_entries)
    for src, dst in terms:
        if _norm(src) in seen:
            continue
        seen.add(_norm(src))
        merged.append((src, dst))

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([src_lang, dst_lang])
        writer.writerows(merged)
    return output_path

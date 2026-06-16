# AI glossary extraction (AiNiee-style): before translating, ask the LLM to
# scan the document for proper nouns / recurring terms and propose
# translations. The result is merged with the user's glossary for this run
# and saved next to the output for review.
#
# Enabled via "auto_extract_glossary": true in config/system_config.json.
import csv
import json
import os
import re

from core.log_config import app_logger
# NOTE: translate_online/translate_offline are imported lazily inside
# extract_glossary_terms() — importing them at module load pulls in `openai`,
# which made even _parse_terms() (and unit tests) fail with ModuleNotFoundError
# in environments without the online deps installed.

MAX_SAMPLE_CHARS = 8000
MAX_TERMS = 60

_EXTRACTION_PROMPT = (
    "You are a terminology extraction assistant. Scan the {src_lang} document "
    "sample below and extract up to {max_terms} terms that must be translated "
    "consistently: proper nouns, person/product/organization names, and "
    "domain-specific recurring terms. Propose a {dst_lang} translation for each.\n"
    "Output ONLY a JSON array of [source_term, translated_term] pairs, no "
    "explanations:\n"
    '[["term1", "translation1"], ["term2", "translation2"]]\n\n'
    "Document sample:\n{sample}"
)


def _build_sample(values):
    parts, size = [], 0
    for value in values:
        if size >= MAX_SAMPLE_CHARS:
            break
        parts.append(value)
        size += len(value) + 1
    return "\n".join(parts)[:MAX_SAMPLE_CHARS]


def _parse_terms(raw):
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
    return terms[:MAX_TERMS]


def extract_glossary_terms(values, model, use_online, api_key, src_lang, dst_lang):
    """One-shot LLM term extraction from document text values."""
    sample = _build_sample(values)
    if len(sample) < 50:
        return []

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

    terms = _parse_terms(raw)
    app_logger.info(f"AI glossary extraction found {len(terms)} terms")
    return terms


def write_merged_glossary(terms, user_glossary_entries, output_path, src_lang, dst_lang):
    """Write user glossary entries + AI terms (user entries win on conflict)."""
    seen = {src for src, _ in user_glossary_entries}
    merged = list(user_glossary_entries)
    merged.extend((src, dst) for src, dst in terms if src not in seen)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow([src_lang, dst_lang])
        writer.writerows(merged)
    return output_path

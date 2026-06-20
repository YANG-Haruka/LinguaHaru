import json
import os
import re
import time
import tempfile
import threading
from core.log_config import app_logger
from rich import box
from rich import markup
from rich.table import Table
from rich.console import Console


# --- crash-safe + buffered persistence ------------------------------------- #
# Writes go to a temp file + os.replace (atomic) so a crash / out-of-credit
# mid-write can never corrupt the file. The append-only RESULT file is also kept
# in memory and flushed at most every _FLUSH_INTERVAL s, which removes the
# per-segment full-file read+write (the old O(n^2) bottleneck under high
# concurrency). failed/src files are small and have mixed direct-disk access, so
# they are written atomically but NOT cached.
_FLUSH_INTERVAL = 1.5
_result_cache = {}      # path -> list (append-only buffer)
_result_dirty = {}      # path -> bool
_result_last = {}       # path -> last flush time
_result_lock = threading.RLock()


def _atomic_write_json(path, obj):
    """Write JSON via a temp file + atomic rename (no torn/partial files)."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False, indent=4)
        os.replace(tmp, path)
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def flush_results(path=None):
    """Force-write buffered RESULT data to disk. Call before anything reads the
    result file from disk (check/sort, restore) and on stop/finish so partial
    progress is always persisted (resumable)."""
    with _result_lock:
        paths = [path] if path else list(_result_cache.keys())
        for p in paths:
            if _result_dirty.get(p):
                _atomic_write_json(p, _result_cache[p])
                _result_dirty[p] = False
                _result_last[p] = time.time()
            elif path and not os.path.exists(p):
                # Nothing succeeded -> the file was never created. Materialize an
                # empty result so check_and_sort/restore can fall every segment
                # back to source instead of hitting FileNotFoundError.
                _atomic_write_json(p, _result_cache.get(p, []))


def invalidate_results(path):
    """Drop the in-memory result buffer for a path (call at the start of a fresh
    run so a re-translated doc doesn't inherit a stale buffer)."""
    with _result_lock:
        _result_cache.pop(path, None)
        _result_dirty.pop(path, None)
        _result_last.pop(path, None)
    

def detect_language_characters(text, lang_code):
    """
    Detect if text contains characters from specific language
    """
    patterns = {
        # East Asian
        "zh": r'[\u4e00-\u9fff]',  # Chinese simplified
        "zh-Hant": r'[\u4e00-\u9fff\u3400-\u4dbf]',  # Chinese traditional
        "ja": r'[\u3040-\u309f\u30a0-\u30ff\u4e00-\u9faf]',  # Japanese
        "ko": r'[\uac00-\ud7af\u1100-\u11ff]',  # Korean
        
        # Other scripts
        "ru": r'[\u0400-\u04FF]',  # Russian
        "th": r'[\u0e00-\u0e7f]',  # Thai
        "vi": r'[\u00C0-\u1EF9]',  # Vietnamese
    }
    
    # Latin languages
    latin_langs = ["en", "es", "fr", "de", "it", "pt"]
    
    if lang_code in patterns:
        pattern = re.compile(patterns[lang_code])
        status_lan = bool(pattern.search(text))
        return status_lan
    
    return False

def clean_json(text):
    """Clean JSON text"""
    if text is None:
        app_logger.warning("clean_json received None")
        return ""
    if not isinstance(text, str):
        app_logger.warning(f"Expected string, got {type(text)}")
        text = str(text)

    text = text.strip().lstrip("\ufeff")  # Remove BOM
    text = re.sub(r'^```json\n|\n```$', '', text, flags=re.MULTILINE)  # Remove markdown

    # Fix trailing commas
    text = re.sub(r',\s*}', '}', text)
    text = re.sub(r',\s*\]', ']', text)
    return text

def _loads_lenient(text):
    """Best-effort JSON parse for LLM output that wrapped the object in prose or
    a code fence: slice from the first '{' to the last '}', strip trailing commas,
    and retry. Returns the dict, or None if still unparseable. (BallonsTranslator-
    style shape recovery — avoids failing a whole batch over a stray sentence.)"""
    if not isinstance(text, str):
        return None
    s = clean_json(text)
    i, j = s.find("{"), s.rfind("}")
    if i == -1 or j == -1 or j <= i:
        return None
    body = re.sub(r",\s*}", "}", re.sub(r",\s*\]", "]", s[i:j + 1]))
    try:
        obj = json.loads(body)
        return obj if isinstance(obj, dict) else None
    except (json.JSONDecodeError, ValueError):
        return None


# Structural placeholders that must survive translation untouched:
# {{FIELD...}} / {{FOOTNOTE_REF_n}} etc., [formula_n], and line markers
PLACEHOLDER_PATTERN = re.compile(r'\{\{[^}]+\}\}|\[formula_\d+\]|[␊␍]')


def _placeholders_preserved(original, translated):
    """The structural-placeholder multiset must be IDENTICAL (not just
    non-reduced): a dropped placeholder corrupts the document, and an EXTRA one
    the model invented is just as wrong on write-back."""
    from collections import Counter
    need = Counter(PLACEHOLDER_PATTERN.findall(original))
    if not need:
        return True
    have = Counter(PLACEHOLDER_PATTERN.findall(translated))
    return have == need


def _machine_tokens_preserved(original, translated):
    """The masked machine-token (%s, ${var}, {count}, …) multiset must be
    IDENTICAL — a dropped sentinel can't be restored, and an invented one renders
    a bogus placeholder. (placeholder_mask.unmask silently can't restore a dropped
    sentinel, so without this the loss is invisible until the string is broken.)"""
    from collections import Counter
    from core.engine.placeholder_mask import extract_tokens
    need = Counter(extract_tokens(original))
    have = Counter(extract_tokens(translated))
    return have == need


def _structural_intact(original, translated):
    """Hard structural integrity: placeholders + formula/field/line markers +
    machine tokens all match exactly. A failure here must NEVER be shipped (even
    as best-effort) — the document would be corrupted; fall back to source."""
    return _placeholders_preserved(original, translated) and \
        _machine_tokens_preserved(original, translated)


def _is_repetition_degenerate(original, translated):
    """True if the translation looks like a runaway repetition loop: much longer
    than the source AND highly compressible (low entropy = repeated content).
    Conservative thresholds so legitimate CJK->Latin expansion isn't flagged."""
    t = (translated or "").strip()
    if len(t) < 40 or len(t) <= 2 * len(original or "") + 40:
        return False
    import zlib
    raw = t.encode("utf-8")
    ratio = len(raw) / max(len(zlib.compress(raw, 6)), 1)
    return ratio > 4.0   # normal prose ~2-3; >4 means heavy repetition


def is_translation_valid(original, translated, src_lang, dst_lang):
    """
    Check if translation is valid
    """
    # Basic checks
    if not translated or translated.strip() == "":
        return False

    # A translation that drops fields/formulas/line markers corrupts the
    # document on write-back - send it to retry instead
    if not _placeholders_preserved(original, translated):
        app_logger.warning("Translation dropped structural placeholders, marking invalid")
        return False

    # Machine tokens (%s / ${var} / {count}) were masked then restored; if the
    # model dropped one, the restore can't bring it back -> the rendered string
    # would be broken. Validate the multiset round-trips.
    if not _machine_tokens_preserved(original, translated):
        app_logger.warning("Translation dropped machine tokens (%s/${}/{}), marking invalid")
        return False

    # Runaway repetition loop (degenerate output) -> retry instead of shipping.
    if _is_repetition_degenerate(original, translated):
        app_logger.warning("Translation looks like a repetition loop, marking invalid")
        return False

    # Language validation
    non_latin_langs = ["zh", "zh-Hant", "ja", "ko", "ru", "th"]
       
    # Check if identical
    if translated.strip() == original.strip():
        if src_lang in non_latin_langs:
            if detect_language_characters(translated, src_lang):
                return False
            else:
                return True
        else:
            return False
    
    # Check target language
    if dst_lang in non_latin_langs:
        if not detect_language_characters(translated, dst_lang):
            return False
    
    return True

def process_translation_results(original_text, translated_text, SRC_SPLIT_JSON_PATH, RESULT_SPLIT_JSON_PATH, FAILED_JSON_PATH, src_lang, dst_lang, last_try=False, needs_review_path=None):
    """
    Process translation results.

    Three terminal states for each item:
      * translated   — passed validation; written to the result normally.
      * needs_review — ONLY on the final round (last_try): a non-empty output that
                       still fails validation. We keep the best-effort text (so the
                       document has content, not a hole) but record it in
                       needs_review_path so coverage can report it honestly instead
                       of silently counting garbage as "translated".
      * failed       — empty/unparseable output; re-queued (or, after the last
                       round, left to fall back to the source text downstream).
    """
    CONSOLE = Console(highlight=True, tab_size=4)
    needs_review = []   # [{count_split, original, translated}] — last_try only
    
    if not translated_text:
        app_logger.warning("No translated text received")
        _mark_all_as_failed(original_text, FAILED_JSON_PATH)
        return {}

    successful_translations = []
    failed_translations = []
    result_dict = {}
    
    # Track successful count_splits
    successful_count_splits = []

    # Parse original
    try:
        original_json = json.loads(clean_json(original_text))
    except json.JSONDecodeError as e:
        app_logger.warning(f"Failed to parse original: {e}")
        _mark_all_as_failed(original_text, FAILED_JSON_PATH)
        return {}

    # Parse translated (LLM output -> lenient: tolerate prose around the JSON)
    try:
        translated_json = json.loads(clean_json(translated_text))
    except json.JSONDecodeError:
        translated_json = _loads_lenient(translated_text)
    if translated_json is None:
        app_logger.warning("Failed to parse translated (even leniently)")
        _mark_all_as_failed(original_text, FAILED_JSON_PATH)
        return {}

    # Check if all identical (not last try)
    if not last_try:
        if translated_json == original_json:
            # Check if first try
            existing_fail = []
            try:
                with open(FAILED_JSON_PATH, 'r', encoding='utf-8') as f:
                    for item in json.load(f):
                        existing_fail.append(item.get('count_split'))
            except Exception:
                pass
            
            orig_counts = []
            for k in original_json.keys():
                try:
                    orig_counts.append(int(k))
                except:
                    orig_counts.append(k)
            is_first_try = not set(orig_counts).issubset(set(existing_fail))
            
            if is_first_try:
                app_logger.info("First attempt - displaying results")
                fail_table = Table(
                    box=box.ASCII2,
                    expand=True,
                    title="Failed Translations (First Attempt)",
                    highlight=True,
                    show_lines=True,
                    border_style="yellow",
                    collapse_padding=True,
                )
                fail_table.add_column("Split Count", style="cyan", no_wrap=True)
                fail_table.add_column("Original", style="white", overflow="fold")
                fail_table.add_column("Translated", style="yellow", overflow="fold")
                for key, value in original_json.items():
                    fail_table.add_row(str(key), markup.escape(str(value)), markup.escape(str(value)))
                Console(highlight=True, tab_size=4).print(fail_table)
                
                _mark_all_as_failed(original_text, FAILED_JSON_PATH)
                # Return failure (not the originals): returning source text here
                # would poison previous_content context and double-report success
                return {}
            else:
                app_logger.warning("All translations identical - marking as failed")
                fail_table = Table(
                    box=box.ASCII2,
                    expand=True,
                    title="Failed Translations",
                    highlight=True,
                    show_lines=True,
                    border_style="yellow",
                    collapse_padding=True,
                )
                fail_table.add_column("Split Count", style="cyan", no_wrap=True)
                fail_table.add_column("Original", style="white", overflow="fold")
                fail_table.add_column("Translated", style="yellow", overflow="fold")
                for key, value in original_json.items():
                    fail_table.add_row(str(key), markup.escape(str(value)), markup.escape(str(value)))
                Console(highlight=True, tab_size=4).print(fail_table)
                _mark_all_as_failed(original_text, FAILED_JSON_PATH)
                return {}

    # Process each item
    for key, value in original_json.items():
        # Get translated value
        if translated_json is not None:
            translated_value = translated_json.get(key, "").strip()
        else:
            translated_value = ""
        
        # Last try mode: keep a non-empty output as best-effort ONLY if its
        # structure (placeholders / formula / field / line markers / machine
        # tokens) is intact — a structural break corrupts the document, so it must
        # fall back to source (FAILED), never be written even as needs_review.
        # Soft issues (language / length / repetition) are accepted as needs_review.
        if last_try:
            if translated_value and translated_value.strip() != "":
                if not _structural_intact(value, translated_value):
                    app_logger.warning(
                        f"Last-try output broke structure (id {key}); failing -> source")
                    failed_translations.append({"count_split": int(key), "value": value})
                else:
                    successful_translations.append({
                        "count_split": int(key),
                        "original": value,
                        "translated": translated_value
                    })
                    result_dict[key] = translated_value
                    try:
                        successful_count_splits.append(int(key))
                    except (ValueError, TypeError):
                        successful_count_splits.append(key)
                    if not is_translation_valid(value, translated_value, src_lang, dst_lang):
                        needs_review.append({
                            "count_split": int(key),
                            "original": value,
                            "translated": translated_value
                        })
            else:
                failed_translations.append({
                    "count_split": int(key),
                    "value": value
                })
        else:
            # Normal mode - validate translation
            if is_translation_valid(value, translated_value, src_lang, dst_lang):
                successful_translations.append({
                    "count_split": int(key),
                    "original": value,
                    "translated": translated_value
                })
                result_dict[key] = translated_value
                
                try:
                    successful_count_splits.append(int(key))
                except (ValueError, TypeError):
                    successful_count_splits.append(key)
            else:
                failed_translations.append({
                    "count_split": int(key), 
                    "value": value
                })

    # Display successful translations
    if successful_translations:
        success_table = Table(
            box=box.ASCII2,
            expand=True,
            title="Successful Translations",
            highlight=True,
            show_lines=True,
            border_style="green",
            collapse_padding=True,
        )
        success_table.add_column("Split Count", style="cyan", no_wrap=True)
        success_table.add_column("Original", style="white", overflow="fold")
        success_table.add_column("Translated", style="bright_green", overflow="fold")
        
        for item in successful_translations:
            success_table.add_row(
                str(item['count_split']),
                markup.escape(str(item['original'])),
                markup.escape(str(item['translated']))
            )
        
        CONSOLE.print(success_table)
    
    # Display failed translations
    if failed_translations:
        border_style = "red" if last_try else "yellow"
        result_style = "bright_red" if last_try else "yellow"
        
        failed_table = Table(
            box=box.ASCII2,
            expand=True,
            title="Failed Translations",
            highlight=True,
            show_lines=True,
            border_style=border_style,
            collapse_padding=True,
        )
        failed_table.add_column("Split Count", style="cyan", no_wrap=True)
        failed_table.add_column("Original", style="white", overflow="fold")
        failed_table.add_column("Result", style=result_style, overflow="fold")
        
        for item in failed_translations:
            if not translated_json.get(str(item['count_split']), "").strip():
                failed_table.add_row(
                    str(item['count_split']),
                    markup.escape(str(item['value'])),
                    markup.escape('""')
                )
            else:
                failed_table.add_row(
                    str(item['count_split']),
                    markup.escape(str(item['value'])),
                    markup.escape(str(translated_json.get(str(item['count_split']), '')))
                )
        
        CONSOLE.print(failed_table)
 
    # Save successful translations
    save_json(RESULT_SPLIT_JSON_PATH, successful_translations)

    # Save failed translations
    if failed_translations:
        save_failed_json_without_duplicates(FAILED_JSON_PATH, failed_translations)

    # Record best-effort-but-invalid items (last_try only) so coverage can report
    # them. Appended (not overwritten) since one round calls this per batch.
    if needs_review and needs_review_path:
        _append_needs_review(needs_review_path, needs_review)
        app_logger.warning(f"{len(needs_review)} item(s) accepted as best-effort "
                           f"(needs review)")
    
    # Update translation status in source file
    if successful_count_splits:
        try:
            with open(SRC_SPLIT_JSON_PATH, "r", encoding="utf-8") as f:
                src_data = json.load(f)
                
            # Update status
            updated_count = 0
            for item in src_data:
                # Use correct field name: count_split
                count_split = item.get("count_split")
                
                # Ensure type matching
                if count_split is not None:
                    try:
                        count_split_int = int(count_split)
                        if count_split_int in successful_count_splits:
                            item["translated_status"] = True
                            updated_count += 1
                    except (ValueError, TypeError):
                        app_logger.warning(f"Invalid count_split: {count_split}")
                
            # Save updated file (atomic)
            _atomic_write_json(SRC_SPLIT_JSON_PATH, src_data)

            app_logger.info(f"Updated translation status for {updated_count} items")
                
        except Exception as e:
            # Error table
            error_table = Table(
                box=box.ASCII2,
                title="Error Updating Status",
                highlight=True,
                border_style="red",
                collapse_padding=True,
            )
            error_table.add_column("Error", style="bright_red")
            error_table.add_row(markup.escape(str(e)))
            
            CONSOLE.print(error_table)
            app_logger.error(f"Error updating translation status: {e}")
    
    return result_dict

def _mark_all_as_failed(original_text, FAILED_JSON_PATH):
    """Mark all segments as failed"""
    failed_segments = []

    try:
        original_json = json.loads(clean_json(original_text))
        for key, value in original_json.items():
            failed_segments.append({
                "count_split": int(key),
                "value": str(value).strip()    # value may be non-str on a malformed reply
            })
    except json.JSONDecodeError as e:
        app_logger.warning(f"Error parsing original: {e}")
        return

    save_failed_json_without_duplicates(FAILED_JSON_PATH, failed_segments)
    app_logger.warning("All segments marked as failed")

def save_json(filepath, data):
    """Append translated items to the RESULT file via an in-memory buffer.

    The buffer is loaded from disk once, then appended to in memory and flushed
    atomically at most every _FLUSH_INTERVAL s — instead of re-reading and
    re-writing the whole (growing) file on every segment. flush_results() forces
    a write; it is called before any disk read of the result file."""
    with _result_lock:
        if filepath not in _result_cache:
            existing = []
            if os.path.exists(filepath):
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        existing = json.load(f)
                    if not isinstance(existing, list):
                        existing = []
                except (json.JSONDecodeError, OSError):
                    existing = []
            _result_cache[filepath] = existing
            _result_last[filepath] = 0.0

        _result_cache[filepath].extend(data)
        _result_dirty[filepath] = True

        now = time.time()
        if now - _result_last.get(filepath, 0.0) >= _FLUSH_INTERVAL:
            _atomic_write_json(filepath, _result_cache[filepath])
            _result_dirty[filepath] = False
            _result_last[filepath] = now

def save_failed_json_without_duplicates(filepath, data):
    """Save failed JSON without duplicates"""
    # Load existing
    if os.path.exists(filepath):
        with open(filepath, "r", encoding="utf-8") as f:
            try:
                existing_data = json.load(f)
                if not isinstance(existing_data, list):
                    existing_data = []
            except json.JSONDecodeError:
                existing_data = []
    else:
        existing_data = []

    # Track existing count_splits
    existing_count_splits = set()
    for item in existing_data:
        count_split = item.get("count_split")
        if count_split is not None:
            try:
                existing_count_splits.add(int(count_split))
            except (ValueError, TypeError):
                existing_count_splits.add(count_split)

    # Add new items
    for item in data:
        count_split = item.get("count_split")
        if count_split is not None:
            try:
                count_split_int = int(count_split)
            except (ValueError, TypeError):
                count_split_int = count_split
            
            if count_split_int not in existing_count_splits:
                existing_data.append(item)
                existing_count_splits.add(count_split_int)

    # Save (atomic)
    _atomic_write_json(filepath, existing_data)


def _append_needs_review(filepath, items):
    """Append best-effort-but-invalid items to the needs-review list, deduped by
    count_split. Atomic; never raises (review tracking must not break a run)."""
    try:
        existing = []
        if os.path.exists(filepath):
            with open(filepath, "r", encoding="utf-8") as f:
                existing = json.load(f)
            if not isinstance(existing, list):
                existing = []
        seen = {int(it["count_split"]) for it in existing if "count_split" in it}
        for it in items:
            if int(it["count_split"]) not in seen:
                existing.append(it)
                seen.add(int(it["count_split"]))
        _atomic_write_json(filepath, existing)
    except Exception as e:  # noqa: BLE001
        app_logger.warning(f"Could not record needs-review items: {e}")


def check_and_sort_translations(SRC_SPLIT_JSON_PATH, RESULT_SPLIT_JSON_PATH):
    """
    Check missing translations and sort results
    """
    missing_count_splits = set()

    if not os.path.exists(SRC_SPLIT_JSON_PATH) or not os.path.exists(RESULT_SPLIT_JSON_PATH):
        app_logger.error("Source or result file not found")
        return missing_count_splits

    with open(SRC_SPLIT_JSON_PATH, "r", encoding="utf-8") as src_file:
        try:
            src_data = json.load(src_file)
        except json.JSONDecodeError:
            app_logger.error("Failed to load source JSON")
            return missing_count_splits

    with open(RESULT_SPLIT_JSON_PATH, "r", encoding="utf-8") as result_file:
        try:
            translated_data = json.load(result_file)
        except json.JSONDecodeError:
            app_logger.error("Failed to load translated JSON")
            return missing_count_splits

    # Get all source count_splits
    src_count_splits = set()
    src_dict = {}
    
    for item in src_data:
        if isinstance(item, dict):
            count_split = item.get("count_split")
            if count_split is not None:
                try:
                    count_split_int = int(count_split)
                    src_count_splits.add(count_split_int)
                    src_dict[count_split_int] = item
                except (ValueError, TypeError):
                    pass
    
    # Get translated count_splits
    translated_dict = {}
    for item in translated_data:
        if isinstance(item, dict):
            count_split = item.get("count_split")
            if count_split is not None:
                try:
                    count_split_int = int(count_split)
                    translated_dict[count_split_int] = item
                except (ValueError, TypeError):
                    pass
    
    # Find missing
    missing_count_splits = src_count_splits - set(translated_dict.keys())

    # Add missing translations
    if missing_count_splits:
        app_logger.warning(f"Missing translations for count_splits: {missing_count_splits}")
        
        for count_split in missing_count_splits:
            if count_split in src_dict:
                original_text = src_dict[count_split].get("value", "")
                
                # Create new entry
                new_entry = {
                    "count_split": count_split,
                    "original": original_text,
                    "translated": original_text  # Use original as translated
                }
                translated_data.append(new_entry)
    else:
        app_logger.info("No missing translations")

    # Sort by count_split
    sorted_data = sorted(translated_data, key=lambda x: int(x.get("count_split", 0)))

    # This is the post-run rewrite of the result file; the in-memory buffer is
    # now stale, so drop it (nothing reads via the buffer after this point).
    _atomic_write_json(RESULT_SPLIT_JSON_PATH, sorted_data)
    invalidate_results(RESULT_SPLIT_JSON_PATH)

    app_logger.info("Translation results sorted")
    return missing_count_splits
# pipeline/word/bilingual.py
# Bilingual mode helpers for Word translation pipeline
import re
import datetime
from lxml import etree
from config.log_config import app_logger


def set_current_target_language(lang_code):
    """Set the current target language for date conversion decisions in bilingual processing."""
    globals()['_current_target_language'] = lang_code


class DateConversionConfig:
    """Date conversion configuration for bilingual mode."""
    TARGET_LANGUAGE = 'en'  # Default to English format
    ENABLE_AUTO_DATE_CONVERSION = True  # Enable automatic date conversion


def clean_translation_brackets(text):
    """Clean up translation text by removing bracket symbols while keeping content."""
    if not text:
        return text
    cleaned_text = text.replace('《', '').replace('》', '')
    return cleaned_text


def detect_and_convert_untranslated_dates(original_text, translated_text, target_language='en'):
    """Detect untranslated dates in translation and convert to target language format."""
    current_lang = globals().get('_current_target_language', None)
    effective_target = current_lang if current_lang is not None else target_language

    if not DateConversionConfig.ENABLE_AUTO_DATE_CONVERSION or effective_target != 'en':
        return clean_translation_brackets(translated_text)

    original_dates = find_dates_in_text(original_text)

    if not original_dates:
        return clean_translation_brackets(translated_text)

    converted_text = translated_text
    conversion_count = 0
    date_conversions = {}

    for date_info in original_dates:
        date_str = date_info['date_str']
        if date_str in converted_text:
            converted_date = convert_date_to_target_format(date_str, effective_target)
            if converted_date != date_str:
                date_conversions[date_str] = converted_date
                app_logger.info(f"Prepared date conversion: '{date_str}' -> '{converted_date}'")

    sorted_dates = sorted(date_conversions.keys(), key=len, reverse=True)

    for original_date in sorted_dates:
        converted_date = date_conversions[original_date]
        occurrences = converted_text.count(original_date)
        if occurrences > 0:
            converted_text = converted_text.replace(original_date, converted_date)
            conversion_count += occurrences
            app_logger.info(f"Auto-converted {occurrences} occurrences of date: '{original_date}' -> '{converted_date}'")

    if conversion_count > 0:
        app_logger.info(f"Total {conversion_count} date instances auto-converted in text: '{original_text[:30]}...'")

    return clean_translation_brackets(converted_text)


def find_dates_in_text(text):
    """Find dates in text and return detailed information."""
    date_patterns = [
        (r'(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})', 'YYYY.M.D'),
        (r'(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})', 'M.D.YYYY'),
        (r'(\d{4})[.\-/](\d{1,2})', 'YYYY.M'),
    ]

    dates = []

    for pattern, format_type in date_patterns:
        matches = re.finditer(pattern, text)
        for match in matches:
            dates.append({
                'date_str': match.group(),
                'format_type': format_type,
                'groups': match.groups(),
                'start': match.start(),
                'end': match.end()
            })

    dates.sort(key=lambda x: x['start'])

    filtered_dates = []
    for date in dates:
        is_overlap = False
        for i, existing_date in enumerate(filtered_dates):
            if (date['start'] < existing_date['end'] and date['end'] > existing_date['start']):
                if len(date['date_str']) > len(existing_date['date_str']):
                    filtered_dates[i] = date
                is_overlap = True
                break

        if not is_overlap:
            filtered_dates.append(date)

    filtered_dates.sort(key=lambda x: x['start'])
    return filtered_dates


def convert_date_to_target_format(date_str, target_language='en'):
    """Convert date string to target language format."""
    if target_language != 'en':
        return date_str

    try:
        parsed_date = None
        is_year_month_only = False

        for sep in ['.', '-', '/']:
            if sep in date_str:
                parts = date_str.split(sep)
                if len(parts) == 3:
                    if len(parts[0]) == 4:
                        year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
                    elif len(parts[2]) == 4:
                        month, day, year = int(parts[0]), int(parts[1]), int(parts[2])
                    else:
                        continue

                    if 1 <= month <= 12 and 1 <= day <= 31 and 1900 <= year <= 2100:
                        try:
                            parsed_date = datetime.datetime(year, month, day)
                            break
                        except ValueError:
                            continue

                elif len(parts) == 2 and len(parts[0]) == 4:
                    year, month = int(parts[0]), int(parts[1])
                    if 1 <= month <= 12 and 1900 <= year <= 2100:
                        parsed_date = datetime.datetime(year, month, 1)
                        is_year_month_only = True
                        break

        if parsed_date:
            if target_language == 'en':
                if is_year_month_only:
                    return parsed_date.strftime("%B %Y")
                else:
                    return parsed_date.strftime("%B %d, %Y")

    except (ValueError, IndexError, TypeError) as e:
        app_logger.warning(f"Failed to parse date '{date_str}': {e}")
        pass

    return date_str


def create_bilingual_text(original_text, translated_text):
    """Create bilingual text format: original text + newline + translated text."""
    if not original_text:
        return clean_translation_brackets(translated_text)
    if not translated_text:
        return original_text

    original_clean = original_text.strip()
    translated_clean = translated_text.strip()

    converted_translated = detect_and_convert_untranslated_dates(
        original_clean,
        translated_clean,
        DateConversionConfig.TARGET_LANGUAGE
    )

    converted_translated = clean_translation_brackets(converted_translated)

    footnote_ref_pattern = r'\{\{FOOTNOTE_REF_\d+\}\}'
    footnote_refs = re.findall(footnote_ref_pattern, original_clean)

    if footnote_refs:
        for footnote_ref in footnote_refs:
            converted_translated = converted_translated.replace(footnote_ref, '')
        converted_translated = re.sub(r'\s+', ' ', converted_translated).strip()
        app_logger.debug(f"Removed footnote references from translation: {footnote_refs}")

    return f"{original_clean}\n{converted_translated}"


def apply_latin_font_to_run(run, namespaces, target_language=None):
    """Apply Latin font to text run for non-Chinese target languages."""
    effective_target = target_language or globals().get('_current_target_language') or DateConversionConfig.TARGET_LANGUAGE

    if effective_target and effective_target.lower() in ['zh', 'zh-cn', 'zh-tw', 'chinese']:
        return

    rPr = run.xpath('.//w:rPr', namespaces=namespaces)
    if not rPr:
        rPr_element = etree.SubElement(run, f"{{{namespaces['w']}}}rPr")
    else:
        rPr_element = rPr[0]

    existing_fonts = rPr_element.xpath('.//w:rFonts', namespaces=namespaces)
    if existing_fonts:
        rFonts = existing_fonts[0]
    else:
        rFonts = etree.SubElement(rPr_element, f"{{{namespaces['w']}}}rFonts")

    font_name = "Times New Roman"
    rFonts.set(f'{{{namespaces["w"]}}}ascii', font_name)
    rFonts.set(f'{{{namespaces["w"]}}}hAnsi', font_name)
    rFonts.set(f'{{{namespaces["w"]}}}cs', font_name)

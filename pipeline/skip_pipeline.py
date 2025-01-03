import regex as re

def is_multibyte(text):
    return bool(re.search(r'[\u4e00-\u9fff\u3040-\u30ff\u3400-\u4dbf\uFF00-\uFFEF]', text, re.UNICODE))

def should_translate(text_value):
    text_value = text_value.strip()

    # Skip empty values
    if not text_value:
        return False

    # Skip pure numbers
    if text_value.isdigit():
        return False

    # Skip percentages, units, or simple numeric strings
    if re.match(r'^\d+(\.\d+)?\s*(%|[a-zA-Z]+)?$', text_value):
        return False

    # Skip URLs or emails
    if re.match(r'^https?://|www\.', text_value) or re.match(r'^[^@]+@[^@]+\.[^@]+$', text_value):
        return False

    # Skip identifiers or codes
    if re.match(r'^[0-9-_]+$', text_value):
        return False

    # Skip placeholders
    if re.match(r'^[\{\[\<][^{}\[\]]*[\}\]\>]$', text_value):
        return False

    # Skip strings that contain only symbols, numbers, and spaces
    if re.match(r'^[\s\p{P}\p{S}0-9]+$', text_value, re.UNICODE):
        return False

    # Skip pure punctuation or operators
    if re.match(r'^[^\w\u4e00-\u9fff]+$', text_value):
        return False

    if all(char in "・〇、。！？…（）「」『』ー △" for char in text_value):
        return False
    
    # Skip dates in common formats
    if re.match(r'^\d{4}/\d{1,2}/\d{1,2}$', text_value) or re.match(r'^\w{3,9} \d{1,2}, \d{4}$', text_value):
        return False

    # Skip very short strings
    if len(text_value) < 2:
        return False

    # Skip single letters (a-Z)
    if re.match(r'^[a-zA-Z]$', text_value):
        return False

    # Otherwise, translate
    return True

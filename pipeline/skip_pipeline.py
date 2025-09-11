import regex as re

def is_multibyte(text):
    """Check if text contains multibyte characters (Chinese, Japanese, Korean, etc.)"""
    return bool(re.search(r'[\u4e00-\u9fff\u3040-\u30ff\u3400-\u4dbf\uFF00-\uFFEF]', text, re.UNICODE))

def should_translate(text_value):
    """Determine if text needs translation - Translation industry standard optimized version"""
    text_value = text_value.strip()

    # Skip empty values
    if not text_value:
        return False

    # If contains multibyte characters (Chinese, Japanese, Korean, etc.), usually needs translation
    if is_multibyte(text_value):
        return True

    # ===== Priority check for various numeric formats (avoid misclassifying as content needing translation) =====
    
    # Skip pure numbers (integers)
    if text_value.isdigit():
        return False

    # Skip pure decimals and floats (including simple decimals like 1.1, 2.5, 0.3, etc.)
    if re.match(r'^-?\d+\.\d+$', text_value):
        return False

    # Skip negative numbers  
    if re.match(r'^-\d+$', text_value):
        return False

    # Skip numbers with thousands separators
    if re.match(r'^\d{1,3}(,\d{3})*(\.\d+)?$', text_value):
        return False

    # Skip scientific notation
    if re.match(r'^-?\d+(\.\d+)?[eE][+-]?\d+$', text_value):
        return False

    # Skip percentages
    if re.match(r'^\d+(\.\d+)?\s*%$', text_value):
        return False

    # Skip version numbers and decimals (like 1.1, 2.0, 3.14.5, 10.2.3.4, etc.)
    if re.match(r'^\d+(\.\d+){1,4}$', text_value):
        return False

    # Skip simple ratios and proportions (like 1:1, 3:2, 16:9, etc.)
    if re.match(r'^\d+:\d+(\.\d+)?$', text_value):
        return False

    # Skip fractions (like 1/2, 3/4, 22/7, etc.)
    if re.match(r'^\d+/\d+$', text_value):
        return False

    # Skip coordinate-like numbers (like 1.1.1, 2.3.4.5, etc.)
    if re.match(r'^\d+(\.\d+){2,}$', text_value):
        return False

    # Skip hexadecimal, binary, octal numbers and color codes
    if re.match(r'^0x[0-9A-Fa-f]+$', text_value):        # 0xABCD
        return False
    if re.match(r'^#[0-9A-Fa-f]{3,8}$', text_value):     # Color codes #FFF, #FFFFFF
        return False
    if re.match(r'^0b[01]+$', text_value):               # 0b1010
        return False
    if re.match(r'^0o[0-7]+$', text_value):              # 0o777
        return False

    # Skip currency amounts
    if re.match(r'^[$¥€£₹₽¢]\s*\d+(\.\d{2})?$', text_value):
        return False
    if re.match(r'^\d+(\.\d{2})?\s*[$¥€£₹₽¢]$', text_value):
        return False

    # ===== Skip numeric ranges and numeric + symbol combinations =====
    
    # Skip time ranges and number ranges (8:00-18:00, 204-205, 110-111, etc.)
    numeric_range_patterns = [
        r'^\d+:\d{2}-\d+:\d{2}$',                        # 8:00-18:00, 9:30-17:30
        r'^\d+-\d+$',                                    # 204-205, 110-111, 1-10
        r'^\d+\.\d+-\d+\.\d+$',                          # 1.5-2.5, 10.2-15.8
        r'^\d+:\d{2}:\d{2}-\d+:\d{2}:\d{2}$',            # 08:30:00-17:30:00
        r'^\d+/\d+-\d+/\d+$',                            # 1/2-3/4
        r'^\d+\s*-\s*\d+$',                              # 100 - 200 (with spaces)
    ]
    
    for pattern in numeric_range_patterns:
        if re.match(pattern, text_value):
            return False

    # Skip phone numbers and extension patterns
    phone_patterns = [
        r'^\+\d{1,3}-\d{2,3}-\d{3,4}-\d{3,4}$',         # +82-32-726-2000
        r'^\(\d{3,4},?\s*\d{3,4}(-\d+)?\)$',             # (6777, 6777-1)
        r'^\d{3,4}-\d{3,4}-\d{4}$',                      # 010-1234-5678
        r'^\+\d{1,3}\s*\d{2,3}\s*\d{3,4}\s*\d{3,4}$',   # +82 32 726 2000
        r'^\d{3,4}\.\d{3,4}\.\d{4}$',                    # 010.1234.5678
    ]
    
    for pattern in phone_patterns:
        if re.match(pattern, text_value):
            return False

    # Skip room/location codes with symbols
    location_patterns = [
        r'^\d+[A-Z]?\s*\(\d+(,\s*\d+)*\)$',             # 2F (201, 202, 203)
        r'^\d+[A-Z]?\s*\([\d\s,]+\)$',                   # Various room listings
        r'^[A-Z]?\d+\s*\([\d\s,-]+\)$',                  # A1 (101, 102-105)
        r'^\d+[A-Z]-\d+[A-Z]?$',                         # 1A-5B, 2F-3F
    ]
    
    for pattern in location_patterns:
        if re.match(pattern, text_value):
            return False

    # Skip time slots with symbols
    time_slot_patterns = [
        r'^[□■◇◆○●△▲▽▼]\d+:\d{2}-\d+:\d{2}$',           # □9:00-9:50
        r'^[□■◇◆○●△▲▽▼]\s*\d+:\d{2}$',                  # □9:00
        r'^[□■◇◆○●△▲▽▼]\s*\d+$',                        # □9
        r'^\d+:\d{2}-\d+:\d{2}\s*[□■◇◆○●△▲▽▼]$',       # 9:00-9:50□
    ]
    
    for pattern in time_slot_patterns:
        if re.match(pattern, text_value):
            return False

    # Skip general numeric + common symbols combinations
    numeric_symbol_patterns = [
        r'^[\d\s\-\+\(\),\.:/]+$',                       # Only digits, spaces, and common symbols
        r'^[\d\s\-\+\(\),\.:/#]+$',                      # Include # for extensions
        r'^[\d\s\-\+\(\),\.:/×~]+$',                     # Include × and ~
        r'^[\d\s\-\+\(\),\.:/&]+$',                      # Include &
    ]
    
    # Check if text is purely numeric + symbols (no alphabetic characters)
    if not re.search(r'[a-zA-Z\u4e00-\u9fff\u3040-\u30ff\u3400-\u4dbf\uFF00-\uFFEF]', text_value):
        for pattern in numeric_symbol_patterns:
            if re.match(pattern, text_value):
                return False

    # ===== Check special date and time formats =====
    
    # Special handling: Date formats may need translation (localization requirements)
    # Note: Only complex date formats are considered for translation, simple numeric formats already excluded above
    date_patterns = [
        r'^\d{4}[.\-/]\d{1,2}[.\-/]\d{1,2}$',          # YYYY.M.D, YYYY-M-D, YYYY/M/D
        r'^\d{1,2}[.\-/]\d{1,2}[.\-/]\d{4}$',          # M.D.YYYY, M-D-YYYY, M/D/YYYY  
        r'^\d{4}[.\-/]\d{1,2}$',                        # YYYY.M, YYYY-M, YYYY/M (but exclude simple decimals)
        r'^\d{1,2}[.\-/]\d{4}$',                        # M.YYYY, M-YYYY, M/YYYY
    ]
    
    for pattern in date_patterns:
        if re.match(pattern, text_value):
            # Additional check: Ensure it's not a simple version number or decimal misclassified as date
            # If it's a simple format with two digits or less, it's likely a version number rather than a date
            parts = re.split(r'[.\-/]', text_value)
            if len(parts) == 2:
                # Two-part format needs stricter judgment
                try:
                    num1, num2 = int(parts[0]), int(parts[1])
                    # If both are single digits or simple numbers, more likely to be version numbers
                    if (num1 < 100 and num2 < 100) and not (num1 > 31 or num2 > 31):
                        # Likely a version number, don't translate
                        return False
                except ValueError:
                    pass
            return True

    # Time formats may need translation
    time_patterns = [
        r'^\d{1,2}:\d{2}(:\d{2})?(\s*(AM|PM|am|pm))?$',  # 12:30, 12:30:45, 12:30 PM
        r'^\d{1,2}时\d{1,2}分(\d{1,2}秒)?$',              # 12时30分45秒 (Chinese time format)
    ]
    
    for pattern in time_patterns:
        if re.match(pattern, text_value):
            return True

    # Skip units and measurements (extended unit patterns)
    unit_patterns = [
        r'^\d+(\.\d+)?\s*(mm|cm|dm|m|km|in|ft|yd|mi|mil)$',           # Length
        r'^\d+(\.\d+)?\s*(mg|g|kg|t|oz|lb|ton|lbs)$',                 # Weight/Mass
        r'^\d+(\.\d+)?\s*(ml|cl|dl|l|kl|fl\s?oz|gal|pt|qt|cup)$',     # Volume
        r'^\d+(\.\d+)?\s*(mm²|cm²|m²|km²|in²|ft²|yd²|mi²|ha)$',       # Area
        r'^\d+(\.\d+)?\s*(mm³|cm³|m³|km³|in³|ft³|yd³|mi³)$',          # Volume 3D
        r'^\d+(\.\d+)?\s*(b|kb|mb|gb|tb|pb|kB|MB|GB|TB|PB)$',         # Data size
        r'^\d+(\.\d+)?\s*(hz|khz|mhz|ghz|thz|Hz|KHz|MHz|GHz|THz)$',   # Frequency
        r'^\d+(\.\d+)?\s*(v|mv|kv|V|mV|kV|volt|volts)$',              # Voltage
        r'^\d+(\.\d+)?\s*(w|kw|mw|gw|W|kW|MW|GW|watt|watts)$',        # Power
        r'^\d+(\.\d+)?\s*(°c|°f|°k|°C|°F|°K|celsius|fahrenheit|kelvin)$', # Temperature
        r'^\d+(\.\d+)?\s*(px|pt|em|rem|ex|ch|vh|vw|vmin|vmax)$',      # CSS units
        r'^\d+(\.\d+)?\s*(rpm|bpm|ppm|dpi|fps)$',                     # Rates
        r'^\d+(\.\d+)?\s*(pa|kpa|mpa|gpa|psi|bar|atm|torr)$',         # Pressure
        r'^\d+(\.\d+)?\s*([a-zA-Z]+)$',                               # General number + unit pattern
    ]
    
    for pattern in unit_patterns:
        if re.match(pattern, text_value, re.IGNORECASE):
            return False

    # Skip version numbers and build numbers (more detailed version number detection)
    if re.match(r'^v?\d+(\.\d+){1,3}(-\w+)?(\+\w+)?$', text_value, re.IGNORECASE):
        return False
    if re.match(r'^(build|rev|revision)\s*\d+$', text_value, re.IGNORECASE):
        return False

    # Skip model numbers, part numbers, SKUs
    model_patterns = [
        r'^[A-Z]{1,4}\d{2,}[A-Z]?\d*$',                  # ABC123, XY456A, A12B34
        r'^[A-Z]{2,}-\d{2,}(-[A-Z0-9]+)*$',             # ABC-123, XYZ-456-A
        r'^\d{2,}[A-Z]{1,4}\d*$',                        # 123ABC, 456XY7
        r'^[A-Z0-9]{2,}-[A-Z0-9]{2,}(-[A-Z0-9]{2,})*$', # AB-12-CD
        r'^SKU[:\-\s]*[A-Z0-9]+$',                       # SKU:ABC123, SKU-XYZ456
        r'^P/N[:\-\s]*[A-Z0-9\-]+$',                     # P/N:123-ABC, P/N 456-XYZ
    ]
    
    for pattern in model_patterns:
        if re.match(pattern, text_value, re.IGNORECASE):
            return False

    # Skip serial numbers and long alphanumeric IDs
    if re.match(r'^[A-Z0-9]{6,}$', text_value) and not re.match(r'^[A-Z]+$', text_value):
        return False

    # Skip UUIDs and GUIDs
    uuid_patterns = [
        r'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$',
        r'^[0-9a-fA-F]{32}$',                            # UUID without hyphens
        r'^\{[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\}$',
    ]
    
    for pattern in uuid_patterns:
        if re.match(pattern, text_value):
            return False

    # Skip network addresses (IP, MAC, etc.)
    if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?$', text_value):  # IP:Port
        return False
    if re.match(r'^[0-9A-Fa-f:]{9,39}$', text_value):                          # IPv6
        return False

    # Skip MAC addresses
    mac_patterns = [
        r'^[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}:[0-9A-Fa-f]{2}$',
        r'^[0-9A-Fa-f]{2}-[0-9A-Fa-f]{2}-[0-9A-Fa-f]{2}-[0-9A-Fa-f]{2}-[0-9A-Fa-f]{2}-[0-9A-Fa-f]{2}$',
        r'^[0-9A-Fa-f]{12}$',                            # MAC without separators
    ]
    
    for pattern in mac_patterns:
        if re.match(pattern, text_value):
            return False

    # Skip URLs, emails, and file paths
    url_patterns = [
        r'^(https?|ftp|ftps|file|sftp)://\S+$',                      # Standard URLs with protocols
        r'^www\.\S+\.\S+',                                          # URLs starting with www
        r'^\S+\.(com|org|net|edu|gov|mil|int|co|io|me|tv|info|biz|name|pro|museum|aero|coop|[a-z]{2})\b.*$',  # Domain extensions
        r'^\S+\.(com|org|net|edu|gov|mil|int|co|io|me|tv|info|biz|name|pro|museum|aero|coop|[a-z]{2})/\S*$',   # URLs with paths
        r'^\S+\.(com|org|net|edu|gov|mil|int|co|io|me|tv|info|biz|name|pro|museum|aero|coop|[a-z]{2})\?\S*$',  # URLs with query params
        r'^\S+\.(com|org|net|edu|gov|mil|int|co|io|me|tv|info|biz|name|pro|museum|aero|coop|[a-z]{2})#\S*$',   # URLs with fragments
    ]
    
    for pattern in url_patterns:
        if re.match(pattern, text_value, re.IGNORECASE):
            return False
    
    # Skip email addresses
    if re.match(r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$', text_value):
        return False
    
    # Skip file paths
    if (re.match(r'^[A-Za-z]:\\', text_value) or                     # Windows: C:\
        re.match(r'^/[^/\s]', text_value)):                          # Unix: /usr/bin
        return False

    # Skip file names with extensions (but allow sentences with periods)
    if re.match(r'^[^/\\:*?"<>|]+\.[a-zA-Z0-9]{1,5}$', text_value):
        # Check if it's likely a filename vs a sentence
        if re.match(r'^[a-zA-Z0-9_\-\.]+\.[a-zA-Z0-9]{1,5}$', text_value):
            return False

    # Skip coordinates and geometric data
    coordinate_patterns = [
        r'^-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?$',                      # 12.34, 56.78
        r'^\(\s*-?\d+(\.\d+)?\s*,\s*-?\d+(\.\d+)?\s*\)$',            # (12.34, 56.78)
        r'^-?\d+(\.\d+)?°\s*\d+(\.\d+)?\'(\s*\d+(\.\d+)?")?\s*[NS]\s*,?\s*-?\d+(\.\d+)?°\s*\d+(\.\d+)?\'(\s*\d+(\.\d+)?")?\s*[EW]$',  # DMS
        r'^-?\d+(\.\d+)?°\s*[NS],?\s*-?\d+(\.\d+)?°\s*[EW]$',       # 12.34°N, 56.78°W
    ]
    
    for pattern in coordinate_patterns:
        if re.match(pattern, text_value, re.IGNORECASE):
            return False

    # Skip mathematical expressions and formulas
    if re.match(r'^[\d\+\-\*/\(\)\.\s=<>≤≥≠±×÷√∞]+$', text_value):
        return False

    # Skip programming/markup identifiers and codes
    if re.match(r'^[0-9\-_]+$', text_value):                         # Pure number-dash-underscore
        return False

    # Skip placeholders and template variables
    placeholder_patterns = [
        r'^[\{\[\<][^{}\[\]<>]*[\}\]\>]$',                           # {var}, [var], <var>
        r'^\$\{[^}]*\}$',                                            # ${variable}
        r'^%[A-Z_][A-Z0-9_]*%$',                                     # %VARIABLE%
        r'^{{[^}]*}}$',                                              # {{variable}}
        r'^<%[^%]*%>$',                                              # <%variable%>
        r'^@[A-Z_][A-Z0-9_]*@$',                                     # @VARIABLE@
    ]
    
    for pattern in placeholder_patterns:
        if re.match(pattern, text_value, re.IGNORECASE):
            return False

    # Skip strings with only symbols, numbers, and spaces
    if re.match(r'^[\s\p{P}\p{S}0-9]+$', text_value, re.UNICODE):
        return False

    # Skip pure punctuation, operators, and symbols
    if re.match(r'^[^\w\u4e00-\u9fff\u3040-\u30ff\u3400-\u4dbf\uFF00-\uFFEF]+$', text_value, re.UNICODE):
        return False

    # Skip single characters and very short symbol combinations
    single_char_patterns = [
        r'^[a-zA-Z]$',                                               # Single letters
        r'^[0-9]$',                                                  # Single digits  
        r'^[\(\)\[\]\{\}<>]$',                                       # Single brackets
        r'^[\.,:;!?\'""]$',                                          # Single punctuation
        r'^[+\-*/=<>≤≥≠]$',                                          # Single operators
        r'^[&@#$%^|~`\\]$',                                          # Single symbols
    ]
    
    for pattern in single_char_patterns:
        if re.match(pattern, text_value):
            return False

    # Skip Japanese/Chinese punctuation and symbols that don't need translation
    if all(char in "・〇、。！？…（）「」『』ー △ 《》±×÷≠≤≥∞∑∏∫∂√∆Ω※◆●○■□▲▼◀▶↑↓←→↔" for char in text_value):
        return False

    # Skip Roman numerals (often used as identifiers, not content)
    if re.match(r'^[IVXLCDM]+$', text_value, re.IGNORECASE) and len(text_value) <= 10:
        return False

    # Skip very short texts with low alphabetic content
    if len(text_value) <= 3:
        alpha_count = sum(1 for char in text_value if char.isalpha())
        if alpha_count == 0:
            return False
        # Allow if more than half is alphabetic
        if alpha_count / len(text_value) < 0.5:
            return False

    # Skip common technical abbreviations and codes
    tech_patterns = [
        r'^[A-Z]{2,6}\d*$',                                          # API, HTTP, USB2, WIFI6
        r'^[A-Z]+_[A-Z]+(_[A-Z]+)*$',                                # CONST_VALUE, MAX_SIZE
        r'^[A-Z]{1,3}\d{1,4}[A-Z]?$',                                # A1, AB12, ABC123D
        r'^\d{1,4}[A-Z]{1,4}$',                                      # 12A, 123ABC
        r'^[A-Z]+\d+[A-Z]*\d*$',                                     # AB123CD, A1B2
    ]
    
    for pattern in tech_patterns:
        if re.match(pattern, text_value):
            return False

    # At this point, check if we have meaningful alphabetic content
    alpha_count = sum(1 for char in text_value if char.isalpha())
    total_meaningful = sum(1 for char in text_value if char.isalnum())
    
    # Need at least 2 alphabetic characters for translation
    if alpha_count < 2:
        return False
    
    # If text is mostly alphabetic, likely needs translation
    if total_meaningful > 0 and alpha_count / total_meaningful >= 0.6:
        return True

    # For mixed content, be more selective
    if len(text_value) > 8 and alpha_count >= 3:
        return True

    # Default: don't translate unless we're confident it's natural language
    return False
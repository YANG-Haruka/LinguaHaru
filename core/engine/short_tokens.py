"""Short tokens that must not be sent to the translator.

Data tables are full of cells like "W" (withheld), "NA", "XX", "e" (estimated),
"—", "2,630" or "5 kt". An LLM given such a cell with no context "translates"
it ("W" -> "西"/"Ouest"), so callers skip these cells and leave them as-is.
"""

# Two-letter real words that do appear alone in cells (Yes/No columns) and
# should still be translated.
_TWO_LETTER_WORDS = {"no", "ok"}


def is_untranslatable_short_token(text):
    """True for text with no letters (numbers, punctuation, symbols, dashes)
    or only 1-2 ASCII letters (markers / units: "W", "NA", "XX", "e", "5 kt").
    Non-Latin letters (CJK, accented) never count as a short token."""
    s = (text or "").strip()
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return True
    if len(letters) > 2 or not all(c.isascii() for c in letters):
        return False
    word = "".join(letters)
    # Two plain letters are a code only when uppercase ("US", "NA") or next to
    # a digit ("5 kt"); "Hi", "Up", "On", "Ja" are words.
    if len(letters) == 2 and not word.isupper() and not any(c.isdigit() for c in s):
        return False
    return word.lower() not in _TWO_LETTER_WORDS

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.engine.short_tokens import is_untranslatable_short_token


@pytest.mark.parametrize("text", [
    "W", "e", "NA", "XX", "—", "--", "", "  ", "2,630", "146,000", "56,200,000",
    "(3)", "12.5%", "5 kt", "(W)", "2023", "Q1", "US", "N/A", "12.5",
])
def test_skipped(text):
    assert is_untranslatable_short_token(text)


@pytest.mark.parametrize("text", [
    "No", "OK", "Yes", "Hi", "Up", "On", "In", "Me", "Go", "Ja", "否", "有", "Total", "Chile", "Reserves", "合计", "是", "Sí", "mine production",
])
def test_translated(text):
    assert not is_untranslatable_short_token(text)

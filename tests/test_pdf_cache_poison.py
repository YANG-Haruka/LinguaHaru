"""A failed PDF paragraph (callback returns the source text) must not be cached,
and an already-poisoned cache entry (cached == source) must be re-translated."""
import pytest

pytest.importorskip("babeldoc")
from core.translators.pdf_translator import _CallbackTranslator  # noqa: E402


class _Cache:
    def __init__(self):
        self.d = {}

    def get(self, k):
        return self.d.get(k)

    def set(self, k, v):
        self.d[k] = v


def _make(cb):
    t = _CallbackTranslator("en", "zh", cb, {})
    t.cache = _Cache()
    return t


def test_failure_not_cached_then_retranslated():
    calls = []
    answers = iter(["Hello", "你好"])     # 1st call fails (echoes source), 2nd works

    def cb(text):
        calls.append(text)
        return next(answers)

    t = _make(cb)
    assert t.translate("Hello") == "Hello"
    assert "Hello" not in t.cache.d            # failure NOT cached
    assert t.translate("Hello") == "你好"       # so the next run calls the API again
    assert t.cache.d["Hello"] == "你好"
    assert t.translate("Hello") == "你好" and len(calls) == 2   # now a real cache hit


def test_poisoned_entry_is_ignored():
    t = _make(lambda text: "你好")
    t.cache.d["Hello"] = "Hello"               # poisoned by an older version
    assert t.translate("Hello") == "你好"
    assert t.cache.d["Hello"] == "你好"

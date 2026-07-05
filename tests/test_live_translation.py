# Real-time voice: the translation shown must be clean text, never the raw
# JSON wrapper ({"translated_text": ...}) the LLM backends emit.
#
# Run from the repo root:
#   python tests/test_live_translation.py
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

from core.llm.llm_wrapper import _plain_translation

CASES = [
    ('{"translated_text": "じゃあ、テストしてみるね。"}', "じゃあ、テストしてみるね。"),
    ('{"1": "Hello world"}', "Hello world"),
    ('{"translation": "Bonjour"}', "Bonjour"),
    ('```json\n{"translated_text": "こんにちは"}\n```', "こんにちは"),
    ("Plain text, no JSON at all", "Plain text, no JSON at all"),
    ('  {"translated_text": "  trimmed  "}  ', "trimmed"),
]


def main():
    ok = 0
    for raw, expected in CASES:
        got = _plain_translation(raw)
        status = "PASS" if got == expected else "FAIL"
        if got == expected:
            ok += 1
        print(f"  [{status}] {raw[:40]!r} -> {got!r}")
    assert ok == len(CASES), f"{ok}/{len(CASES)} passed"
    print(f"PASS: live translation output is clean text ({ok}/{len(CASES)})")


if __name__ == "__main__":
    main()

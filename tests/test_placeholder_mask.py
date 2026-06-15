# Tests for the PRE-LLM placeholder masking layer (core/engine/placeholder_mask.py)
# and its integration into the LLM wrapper.
#
# Standalone (no pytest): prints PASS/FAIL per check, exits non-zero on failure.
#   python tests/test_placeholder_mask.py
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

from core.engine.placeholder_mask import mask, unmask, _SENT_OPEN  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f" -> {detail}" if detail and not cond else ""))


def roundtrip(text):
    masked, mapping = mask(text)
    return unmask(masked, mapping), masked, mapping


def test_printf():
    print("printf / C-style specifiers")
    text = "Hello %s, you have %d new messages (%.2f%% full), id=%1$s, n=%05d"
    restored, masked, mapping = roundtrip(text)
    check("printf round-trips byte-identical", restored == text, repr(restored))
    check("printf tokens removed from masked", "%s" not in masked and "%d" not in masked, repr(masked))
    check("%% literal captured", any(v == "%%" for v in mapping.values()), str(mapping))
    check("positional %1$s captured", any(v == "%1$s" for v in mapping.values()), str(mapping))

    # a bare percent in prose must NOT be masked
    prose = "Save 50% today and tomorrow"
    r2, m2, _ = roundtrip(prose)
    check("bare percent in prose untouched", m2 == prose, repr(m2))


def test_shell_vars():
    print("shell / template variables")
    text = "Path is ${HOME}/bin and user is $USER ok"
    restored, masked, mapping = roundtrip(text)
    check("$ vars round-trip", restored == text, repr(restored))
    check("${HOME} captured whole", any(v == "${HOME}" for v in mapping.values()), str(mapping))
    check("$USER captured", any(v == "$USER" for v in mapping.values()), str(mapping))
    check("braced var not split into bare", "${HOME}" not in masked and "$USER" not in masked, repr(masked))


def test_single_brace():
    print("single-brace placeholders")
    text = "Welcome {name}, item {0} of {count}"
    restored, masked, mapping = roundtrip(text)
    check("single-brace round-trips", restored == text, repr(restored))
    check("three tokens masked", len(mapping) == 3, str(mapping))


def test_icu_plural():
    print("ICU plural (nested balanced braces)")
    text = "You have {count, plural, one {# item} other {# items}}"
    restored, masked, mapping = roundtrip(text)
    check("ICU plural round-trips", restored == text, repr(restored))
    check("whole ICU group masked as one token", len(mapping) == 1, str(mapping))
    check("nested braces gone from masked", "{" not in masked and "}" not in masked, repr(masked))


def test_reserved_markers_not_touched():
    print("reserved pipeline markers must NOT collide")
    text = "See {{HLINK_0}}real link{{/HLINK_0}} for {count} details"
    restored, masked, mapping = roundtrip(text)
    check("HLINK marker preserved in masked", "{{HLINK_0}}" in masked and "{{/HLINK_0}}" in masked, repr(masked))
    check("only {count} masked (one token)", len(mapping) == 1 and "{count}" in mapping.values(), str(mapping))
    check("round-trip preserves everything", restored == text, repr(restored))

    # Other double-brace markers + formula + line markers stay intact
    text2 = "{{INLINE_1}} {{FIELD: REF x}} [formula_2] line1␊line2 {x} {{FOOTNOTE_REF_3}}"
    r2, m2, map2 = roundtrip(text2)
    check("inline/field/formula/footnote untouched, line marker kept",
          "{{INLINE_1}}" in m2 and "{{FIELD: REF x}}" in m2 and "[formula_2]" in m2
          and "␊" in m2 and "{{FOOTNOTE_REF_3}}" in m2, repr(m2))
    check("only {x} masked in marker-heavy string",
          len(map2) == 1 and "{x}" in map2.values(), str(map2))
    check("marker-heavy round-trip", r2 == text2, repr(r2))


def test_unmask_robustness():
    print("unmask robustness (dropped / duplicated sentinels)")
    text = "a %s b %d c"
    masked, mapping = mask(text)
    # Drop one sentinel from the model's reply
    parts = [p for p in masked]
    # Find the two sentinels and delete the first by removing its index span.
    import re
    sentinels = re.findall(re.escape(_SENT_OPEN) + r"\d+", masked)
    dropped = masked.replace(sentinels[0], "", 1)
    restored_dropped = unmask(dropped, mapping)
    check("dropped sentinel does not crash and restores the rest",
          "%d" in restored_dropped and "%s" not in restored_dropped, repr(restored_dropped))

    # Duplicate a sentinel
    duped = masked.replace(sentinels[0], sentinels[0] + sentinels[0], 1)
    restored_duped = unmask(duped, mapping)
    check("duplicated sentinel restores twice", restored_duped.count("%s") == 2, repr(restored_duped))

    # Unknown index sentinel is left as-is (no KeyError)
    junk = masked + f"{_SENT_OPEN}999"
    restored_junk = unmask(junk, mapping)
    check("unknown sentinel index left intact", restored_junk.endswith(f"{_SENT_OPEN}999"),
          repr(restored_junk))


def test_no_token_fastpath():
    print("no-token fast path")
    text = "A perfectly ordinary sentence with no placeholders."
    masked, mapping = mask(text)
    check("masked is the identical string object semantics", masked == text and mapping == {}, repr(masked))
    check("unmask of plain text is a no-op", unmask(text, {}) == text)

    empty_ok = mask("") == ("", {}) and mask(None) == (None, {})
    check("empty / None handled", empty_ok)


def test_fake_translator_compat():
    print("[T]-prefixed fake-translation still unmasks")
    # Simulate the corpus fake translator: prefix the masked value with [T].
    text = "Hello %s and {count}"
    masked, mapping = mask(text)
    faked = "[T]" + masked            # what fake_translate would produce on a masked value
    restored = unmask(faked, mapping)
    check("[T] prefix preserved and tokens restored",
          restored == "[T]Hello %s and {count}", repr(restored))


def test_wrapper_integration():
    print("llm_wrapper mask/unmask seam")
    from core.llm import llm_wrapper as lw

    segment = '```json\n{\n    "1": "Hello %s, {count} left",\n    "2": "plain text"\n}\n```'
    masked_seg, mapping = lw._mask_segment(segment)
    check("segment JSON structure intact (outer braces kept)",
          masked_seg.strip().startswith("```json") and '"1"' in masked_seg and '"2"' in masked_seg,
          repr(masked_seg))
    check("value tokens masked, not the JSON braces",
          "%s" not in masked_seg and "{count}" not in masked_seg, repr(masked_seg))
    check("mapping is per-key", "1" in mapping and "2" not in mapping, str(mapping))

    # Model returns the masked sentinels inside a translated JSON reply.
    masked_data = json.loads(lw._strip_json_fence(masked_seg))
    reply = json.dumps({"1": "[T]" + masked_data["1"], "2": "[T]plain text"}, ensure_ascii=False)
    unmasked_reply = lw._unmask_reply(reply, mapping)
    reply_data = json.loads(unmasked_reply)
    check("reply value 1 restored byte-identical",
          reply_data["1"] == "[T]Hello %s, {count} left", repr(reply_data["1"]))
    check("reply value 2 unchanged", reply_data["2"] == "[T]plain text", repr(reply_data["2"]))

    # A segment with no tokens must pass through untouched (fast path).
    plain = '```json\n{\n    "1": "just words"\n}\n```'
    out, m = lw._mask_segment(plain)
    check("no-token segment returned unchanged", out == plain and m == {}, repr(out))


def main():
    for fn in (test_printf, test_shell_vars, test_single_brace, test_icu_plural,
               test_reserved_markers_not_touched, test_unmask_robustness,
               test_no_token_fastpath, test_fake_translator_compat,
               test_wrapper_integration):
        try:
            fn()
        except Exception:
            import traceback
            traceback.print_exc()
            FAILED.append(fn.__name__ + " (crashed)")
        print()
    print(f"{len(PASSED)} passed, {len(FAILED)} failed")
    for name in FAILED:
        print(f"  FAIL: {name}")
    sys.exit(1 if FAILED else 0)


if __name__ == "__main__":
    main()

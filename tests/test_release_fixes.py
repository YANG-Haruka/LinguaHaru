"""Assert-based tests for the 2026-06 release-review fixes: placeholder multiset,
last_try structural gating, API error classification, translation cache, subtitle
re-segmentation, windowed-VAD overlap. Pure unit tests (no network / models)."""
import os
import sys
import tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)


# --- placeholder / machine-token EXACT multiset -----------------------------
def test_placeholder_multiset_exact():
    from core.engine.translation_checker import _placeholders_preserved, _machine_tokens_preserved
    # dropped -> invalid
    assert not _placeholders_preserved("a {{F}} b", "a b")
    # extra (model invented one) -> invalid too
    assert not _placeholders_preserved("a {{F}} b", "a {{F}} {{F}} b")
    # identical multiset -> valid
    assert _placeholders_preserved("a {{F}} b", "x {{F}} y")
    # machine tokens
    assert not _machine_tokens_preserved("Hi %s", "你好")            # dropped
    assert not _machine_tokens_preserved("Hi %s", "你好 %s %s")       # extra
    assert _machine_tokens_preserved("Hi %s {count}", "%s 你好 {count}")


def test_last_try_structural_gating():
    """last_try must FAIL (source fallback) structurally-broken output, not write
    it as needs_review."""
    import json
    from core.engine import translation_checker as tc
    d = tempfile.mkdtemp()
    src = os.path.join(d, "src.json"); res = os.path.join(d, "res.json")
    fail = os.path.join(d, "fail.json"); nr = os.path.join(d, "nr.json")
    with open(src, "w", encoding="utf-8") as f:
        json.dump([{"count_split": 1, "value": "Hello %s"},
                   {"count_split": 2, "value": "World"}], f)
    orig = "```json\n" + json.dumps({"1": "Hello %s", "2": "World"}) + "\n```"
    # id1 drops %s (structural break -> FAIL); id2 fine
    trans = "```json\n" + json.dumps({"1": "你好", "2": "世界"}) + "\n```"
    tc.process_translation_results(orig, trans, src, res, fail, "en", "zh",
                                   last_try=True, needs_review_path=nr)
    tc.flush_results(res)
    with open(res, encoding="utf-8") as f:
        result = {r["count_split"]: r["translated"] for r in json.load(f)}
    failed = set()
    if os.path.exists(fail):
        with open(fail, encoding="utf-8") as f:
            failed = {x["count_split"] for x in json.load(f)}
    assert 1 in failed and 1 not in result        # broken -> failed, NOT written
    assert result.get(2) == "世界"                 # clean -> written


# --- API error classification ----------------------------------------------
def test_api_error_classification():
    from core.llm.online_translation import _classify_api_exception as C

    class E(Exception):
        def __init__(s, m, code=None): super().__init__(m); s.status_code = code
    assert C(E("x", 429)) == "rate_limit"
    assert C(E("x", 402)) == "hard"
    assert C(E("x", 503)) == "server"
    assert C(E("x", 400)) == "invalid_request"
    assert C(E("x", 422)) == "invalid_request"
    assert C(E("x", 413)) == "too_large"
    assert C(Exception("Request timed out")) == "timeout"
    assert C(Exception("Insufficient Balance")) == "hard"


# --- translation cache ------------------------------------------------------
def test_translation_cache_roundtrip_and_isolation():
    import core.engine.translation_cache as tc
    tc._conn = None
    tc._DB_PATH = os.path.join(tempfile.mkdtemp(), "tm.sqlite")
    s1 = tc.params_sig("m", "en", "zh", glossary_hash=tc.glossary_hash([["AI", "人工智能"]]))
    s2 = tc.params_sig("m", "en", "ja")
    assert s1 != s2
    tc.put_many([("Hello", "你好"), ("Same", "Same")], s1)   # 'Same'==src skipped
    assert tc.get_many(["Hello", "Same", "X"], s1) == {"Hello": "你好"}
    assert tc.get_many(["Hello"], s2) == {}                  # sig isolation
    assert tc.clear() and tc.stats()[0] == 0


def test_cache_sig_includes_context():
    import core.llm.llm_wrapper as w
    a = w._cache_sig("m", "sys", "usr", "prev", None, None, previous_text="He met Tom.")
    b = w._cache_sig("m", "sys", "usr", "prev", None, None, previous_text="She met Tom.")
    assert a != b   # same source in different context must not share a cache key


# --- subtitle re-segmentation ----------------------------------------------
def test_resegment_split_merge():
    import core.pipelines.video_translation_pipeline as v
    # long EN cue with words -> splits at punctuation
    text = "Hello there friend. This is a long sentence that should be split now."
    words, t = [], 0.0
    for wd in text.split(" "):
        words.append((t, t + 0.5, wd + " ")); t += 0.5
    out = v._resegment_cues([(0.0, 12.0, text, words)], "en")
    assert len(out) > 1 and all(s < e for s, e, _t, _w in out)
    # flicker-short merge, with a space (not "Hi.there")
    m = v._resegment_cues([(0.0, 0.5, "Hi.", None), (0.6, 1.0, "there", None)], "en")
    assert len(m) == 1 and "Hi. there" in m[0][2]
    # normal cue unchanged
    assert len(v._resegment_cues([(0.0, 3.0, "A short fine line.", None)], "en")) == 1


def test_md_sentinels_disjoint_and_validated():
    import core.pipelines.md_translation_pipeline as m
    import core.engine.placeholder_mask as pm
    from core.engine.translation_checker import _structural_intact
    assert m._MD_SENT_OPEN != pm._SENT_OPEN          # disjoint delimiters
    orig = "See " + m._md_sentinel(0) + " now"
    assert not _structural_intact(orig, "See now")    # dropped MD sentinel -> fail
    assert _structural_intact(orig, "看 " + m._md_sentinel(0) + " 现在")


def test_language_validation_keeps_machine_content():
    from core.engine.translation_checker import is_translation_valid as v
    assert v("https://example.com", "https://example.com", "en", "zh")   # URL kept
    assert v("a@b.com", "a@b.com", "en", "zh")                            # email kept
    assert v("100%", "100%", "en", "zh")                                  # no letters
    assert not v("Hello world", "hello  world", "en", "fr")               # normalized echo
    assert v("Hello", "你好", "en", "zh")                                  # real translation
    assert v("x", None, "en", "zh") in (True, False)                      # None no crash


def test_glossary_latin1_is_last():
    import inspect
    from core.engine import text_separator
    src = inspect.getsource(text_separator.load_glossary)
    enc_line = next(l for l in src.splitlines() if "encodings = [" in l)
    assert enc_line.rstrip().endswith("'latin1']"), enc_line   # latin1 must be last


def test_window_segments_no_overlap():
    import core.pipelines.video_translation_pipeline as v
    segs = v._window_segments(70000.0)
    # adjacent windows must not overlap (no duplicate transcription)
    for (a, b), (c, d) in zip(segs, segs[1:]):
        assert c >= b


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn(); print(f"  [PASS] {name}")
    print("All release-fix tests passed.")

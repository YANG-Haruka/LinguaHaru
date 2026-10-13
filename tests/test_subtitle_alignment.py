# Subtitle alignment: real captions split one sentence over 2-3 timed cues
# (NASA SVS #13768 below). Sent one cue per id, a local model merged the next
# cue's half-sentence into the current one, every later cue drifted 2-4 s and the
# emptied slot was filled with an invented "[音乐]". The SRT pipeline now sends
# one unit per sentence group and splits the translation back over the group's
# cues. These tests drive the REAL translator pipeline with a fake model that
# reproduces the merging behaviour.
#
# Run from the repo root:
#   python -m pytest tests/test_subtitle_alignment.py
import json
import os
import re
import sys
import zlib
from collections import Counter

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

import core.engine.base_translator as bt
from core.engine.translation_checker import clean_json
from core.pipelines import subtitle_formats_pipeline as fmts
from core.pipelines import subtitle_translation_pipeline as stp
from core.pipelines import subtitle_groups as sg

# NASA SVS #13768 English captions (US-government work, no copyright), verbatim.
NASA_SRT = """1
00:00:01,070 --> 00:00:05,070
[Music throughout] Immediately after the universe was born, it was too tiny

2
00:00:05,070 --> 00:00:09,070
and too hot for protons, electrons and even photons to exist.

3
00:00:09,070 --> 00:00:13,070
Within a second, it had expanded and cooled enough for

4
00:00:13,070 --> 00:00:17,070
protons and neutrons, collectively called baryons, to form.

5
00:00:17,070 --> 00:00:21,070
Almost instantly, there was a rapid burst of sound waves triggered by tiny

6
00:00:21,070 --> 00:00:25,070
variations in this particle soup. Scientists call these waves

7
00:00:25,070 --> 00:00:29,070
Baryon Acoustic Oscillations. The pressure waves traveled outward

8
00:00:29,070 --> 00:00:33,070
for 400,000 years until the universe thinned and cooled enough

9
00:00:33,070 --> 00:00:37,070
for light to travel through it. The universe became transparent

10
00:00:37,070 --> 00:00:41,070
and the waves froze in place. Over

11
00:00:41,070 --> 00:00:45,000
time, the denser regions formed by the waves encouraged more star and galaxy formation

12
00:00:45,000 --> 00:00:49,070
than other areas. The imprint of those waves expanded

13
00:00:49,070 --> 00:00:53,000
with the universe, slowly accelerating due to dark energy.

14
00:00:53,000 --> 00:00:57,070
Today, with our “snapshot” of the universe, there is a

15
00:00:57,070 --> 00:01:01,070
slight tendency for galaxies to be separated from each other by a

16
00:01:01,070 --> 00:01:05,070
distance related to that expanding-ring radius.

17
00:01:05,070 --> 00:01:09,070
Although difficult to see, it is

18
00:01:09,070 --> 00:01:13,070
detectable as a slight bump through careful surveys of many galaxies.

19
00:01:13,070 --> 00:01:18,784
[Music]
"""

_PUNCT_ZH = {".": "。", ",": "，", "!": "！", "?": "？", "[": "[", "]": "]"}


def fake_zh(text):
    """Deterministic 'translation': one CJK char per English word, CJK punctuation."""
    out = []
    for tok in re.findall(r"\{\{[^}]+\}\}|<[^>]+>|␊|[\w'-]+|[^\w\s]", text):
        if tok.startswith(("{{", "<")) or tok == "␊":
            out.append(tok)             # placeholders / markup / line marks kept, like a model
        elif tok in _PUNCT_ZH:
            out.append(_PUNCT_ZH[tok])
        elif re.match(r"[\w'-]", tok):
            out.append(chr(0x4E00 + zlib.crc32(tok.lower().encode()) % 20000))
    return "".join(out)


def _ends_sentence(text):
    return bool(re.search(r"[.!?]\W*$", text.strip()))


def merging_model(data):
    """What qwen3.5-9b did: an item that stops mid-sentence is completed with
    the next item's words up to its sentence end; a slot left empty by that is
    filled with an invented '[音乐]'. Keys are always all returned."""
    keys, rem = list(data), [data[k].strip() for k in data]
    out = {}
    for j, k in enumerate(keys):
        text = rem[j]
        if not text:
            out[k] = "[音乐]"
            continue
        if not _ends_sentence(text) and j + 1 < len(keys):
            m = re.search(r"[.!?](\s|$)", rem[j + 1])
            cut = m.end() if m else len(rem[j + 1])
            text, rem[j + 1] = text + " " + rem[j + 1][:cut], rem[j + 1][cut:].strip()
        out[k] = fake_zh(text)
    return out


def _sandbox(tmp_path, monkeypatch, model, name="nasa.srt", content=NASA_SRT):
    calls = []

    def fake_translate_text(segments, *args, **kwargs):
        data = json.loads(clean_json(segments))
        calls.append(list(data))
        return json.dumps(model(data, len(calls)), ensure_ascii=False), True, None

    monkeypatch.setattr(bt, "translate_text", fake_translate_text)
    monkeypatch.setattr(bt, "cache_store_validated", lambda *a, **k: None)
    monkeypatch.setattr(fmts, "_match_source_name_enabled", lambda: True)
    src = tmp_path / name
    src.write_text(content, encoding="utf-8")
    return str(src), calls


def _run(tmp_path, src, cls=None):
    from core.translators.subtitle_translator import SubtitlesTranslator
    cls = cls or SubtitlesTranslator
    d = {n: str(tmp_path / n) for n in ("temp", "result", "log", "history")}
    for p in d.values():
        os.makedirs(p, exist_ok=True)
    tr = cls(src, "fake-model", False, "", "en", "zh", False,
             max_token=768, max_retries=3, thread_count=1,
             glossary_path=None, temp_dir=d["temp"], result_dir=d["result"],
             session_lang="en", log_dir=d["log"], history_dir=d["history"])
    out, _missing = tr.process(os.path.splitext(src)[0], os.path.splitext(src)[1])
    with open(out, encoding="utf-8-sig") as f:
        return f.read()


def _cues(srt):
    blocks = re.findall(r"(\d+)\n(\S+ --> \S+)\n(.*?)(?=\n\n|\Z)", srt, re.S)
    return [(int(n), ts, text) for n, ts, text in blocks]


def _overlap(a, b):
    """Shared CJK chars as a fraction of the larger side."""
    a, b = Counter(re.findall(r"[一-鿿]", a)), Counter(re.findall(r"[一-鿿]", b))
    return sum((a & b).values()) / max(sum(a.values()), sum(b.values()), 1)


def _assert_aligned(srt):
    src, out = _cues(NASA_SRT), _cues(srt)
    assert [c[:2] for c in out] == [c[:2] for c in src], "cue numbers/timings changed"
    _check_pairs([(s[2], o[2]) for s, o in zip(src, out)])


def _check_pairs(pairs):
    """(source cue text, output cue text) per cue: no empty / invented / drifted cue."""
    assert len(pairs) == 19
    for n, (s_text, o_text) in enumerate(pairs, start=1):
        o_text = re.sub(r"\{[^}]*\}", "", o_text)      # ASS override tags
        assert o_text.strip(), f"cue {n} is empty"
        if sg._TAG_ONLY.match(s_text) and o_text == s_text:
            continue                    # a bare sound tag kept as-is (not sent)
        # invented sound tags: only cues whose source has one may carry one
        if "[" not in s_text:
            assert "[" not in o_text and "音乐" not in o_text, f"cue {n} fabricated: {o_text!r}"
        assert _overlap(fake_zh(s_text), o_text) >= 0.5, \
            f"cue {n} drifted: source {s_text!r} -> {o_text!r} (expected ~{fake_zh(s_text)!r})"


def test_fake_model_reproduces_the_bug_on_per_cue_ids():
    # Sanity: fed one id per cue (the old layout), the fake drifts + fabricates.
    per_cue = {str(n): t for n, _, t in _cues(NASA_SRT)}
    out = merging_model(per_cue)
    assert out["2"] == "[音乐]"
    assert _overlap(fake_zh(per_cue["1"]), out["1"]) < 0.8


def test_cues_grouped_into_sentences(tmp_path):
    src = tmp_path / "nasa.srt"
    src.write_text(NASA_SRT, encoding="utf-8")
    with open(stp.extract_srt_content_to_json(str(src), str(tmp_path)), encoding="utf-8") as f:
        units = json.load(f)
    spans = [len(u["cues"]) for u in units]
    # 1-2 | 3-4 | 5..13 (sentence ends mid-cue until 13) | 14-16 | 17-18 | [Music]
    assert spans == [2, 2, 9, 3, 2, 1], spans
    assert units[0]["start_time"] == "00:00:01,070" and units[0]["end_time"] == "00:00:09,070"
    assert units[-1]["value"] == "[Music]"


def test_merging_model_no_drift_end_to_end(tmp_path, monkeypatch):
    src, calls = _sandbox(tmp_path, monkeypatch, lambda data, _n: merging_model(data))
    srt = _run(tmp_path, src)
    _assert_aligned(srt)
    sent = [k for c in calls for k in c]
    assert len(sent) == 6, f"expected one id per sentence group, got {sent}"


def test_missing_id_is_retried_not_shifted(tmp_path, monkeypatch):
    # First call: the model folds one unit into its neighbour and drops that id
    # (what it did to cue halves). The id is retried on its own; nothing shifts.
    def model(data, n_call):
        out = merging_model(data)
        if n_call == 1 and len(out) > 1:
            out.pop(list(out)[-1])
        return out
    src, calls = _sandbox(tmp_path, monkeypatch, model)
    srt = _run(tmp_path, src)
    assert len(calls) > 3, "the incomplete batch was not retried"
    _assert_aligned(srt)


def test_fabricated_tags_dropped_on_write(tmp_path, monkeypatch):
    def model(data, _n):
        out = merging_model(data)
        return {k: (v if "[" in data[k] else "[音乐] " + v) for k, v in out.items()}
    src, _ = _sandbox(tmp_path, monkeypatch, model)
    _assert_aligned(_run(tmp_path, src))


def test_pure_fabrication_falls_back_to_source():
    assert sg.drop_fabricated_tags("The waves froze.", "[音乐]") == "The waves froze."
    assert sg.drop_fabricated_tags("The waves froze.", "波 [Music] 冻结") == "波 冻结"
    # a tag that IS in the source is kept
    assert sg.drop_fabricated_tags("[Music]", "[音乐]") == "[音乐]"
    assert sg.drop_fabricated_tags("(music)", "[音乐]") == "[音乐]"
    # non-sound brackets (translator notes, footnote refs) are not touched
    assert sg.drop_fabricated_tags("He said it.", "他说了[原文如此][1]。") == "他说了[原文如此][1]。"


def test_split_uses_sentence_anchors_and_word_boundaries():
    parts = ["and the waves froze in place. Over", "time, the denser regions formed"]
    src = sg.join_texts(parts)
    pieces = sg.split_across_cues(src, parts, "WAVES FROZE IN PLACE. OVER TIME, THE DENSER REGIONS FORMED")
    assert len(pieces) == 2 and all(pieces)
    assert " ".join(pieces).split() == "WAVES FROZE IN PLACE. OVER TIME, THE DENSER REGIONS FORMED".split()
    # never cuts inside a Latin word
    for p in pieces:
        assert p.split()[0].isupper() and p.split()[-1].rstrip(".,").isalpha()
    # a cue ending a source sentence ends exactly at the translated sentence end
    parts = ["Hello there, my good friend.", "How are you doing", "on this fine day?"]
    zh = sg.split_across_cues(sg.join_texts(parts), parts, "你好，我的好朋友。你在这美好的一天过得怎么样？")
    assert zh[0] == "你好，我的好朋友。" and "".join(zh) == "你好，我的好朋友。你在这美好的一天过得怎么样？"
    assert all(zh)
    # never inside a number, even next to its thousands separator
    parts = ["The pressure waves traveled outward", "for 400,000 years."]
    nums = sg.split_across_cues(sg.join_texts(parts), parts, "压力波向外传播了400,000年。")
    assert "400,000" in nums[0] + nums[1] and all("400,000" in p or "400" not in p for p in nums)


def test_bilingual_keeps_each_cue_original(tmp_path):
    src = tmp_path / "bi.srt"
    src.write_text(NASA_SRT, encoding="utf-8")
    sj = stp.extract_srt_content_to_json(str(src), str(tmp_path))
    with open(sj, encoding="utf-8") as f:
        units = json.load(f)
    dst = tmp_path / "dst.json"
    dst.write_text(json.dumps([{"count_src": u["count_src"], "translated": fake_zh(u["value"])}
                               for u in units], ensure_ascii=False), encoding="utf-8")
    out = stp.write_translated_content_to_srt(str(src), sj, str(dst), str(tmp_path / "r"),
                                              "en", "zh", bilingual_mode=True)
    text = open(out, encoding="utf-8").read()
    assert text.count("-->") == 19
    assert "\nand too hot for protons, electrons and even photons to exist.\n" in text


# ---- VTT / ASS / LRC: same captions, same merging model, same guarantees ----
NASA_CUES = [tuple(ts.split(" --> ")) + (text.strip(),) for _, ts, text in _cues(NASA_SRT)]


def _vtt_ts(ts):
    return ts.replace(",", ".")


def _ass_ts(ts):
    sec = sg.seconds(ts)
    return f"{int(sec // 3600)}:{int(sec % 3600 // 60):02d}:{sec % 60:05.2f}"


def _lrc_ts(ts):
    sec = sg.seconds(ts)
    return f"[{int(sec // 60):02d}:{sec % 60:05.2f}]"


def test_vtt_no_drift_settings_kept(tmp_path, monkeypatch):
    from core.translators.extra_formats_translator import VttTranslator
    stamps = [f"{_vtt_ts(s)} --> {_vtt_ts(e)} align:start position:10%" for s, e, _ in NASA_CUES]
    doc = "WEBVTT\n\n" + "".join(f"{ts}\n{t}\n\n" for ts, (_, _, t) in zip(stamps, NASA_CUES))
    src, calls = _sandbox(tmp_path, monkeypatch, lambda d, _n: merging_model(d), "nasa.vtt", doc)
    out = _run(tmp_path, src, VttTranslator)
    blocks = re.findall(r"^(\S+ --> .*)\n(.+)$", out, re.M)
    assert [b[0] for b in blocks] == stamps, "cue timing / settings changed"
    _check_pairs([(t, b[1]) for (_, _, t), b in zip(NASA_CUES, blocks)])
    assert len([k for c in calls for k in c]) == 5     # 5 groups; bare [Music] not sent


def test_vtt_markup_and_voice_cues_stay_single(tmp_path):
    from core.pipelines.subtitle_formats_pipeline import extract_vtt_content_to_json
    doc = ("WEBVTT\n\n00:01.000 --> 00:03.000\nThe waves <i>froze</i> in\n\n"
           "00:03.000 --> 00:05.000\nplace and stayed there.\n\n"
           "00:05.000 --> 00:07.000\n<v Ann>Over the years they\n\n"
           "00:07.000 --> 00:09.000\n<v Bob>moved apart.\n")
    (tmp_path / "m.vtt").write_text(doc, encoding="utf-8")
    with open(extract_vtt_content_to_json(str(tmp_path / "m.vtt"), str(tmp_path)), encoding="utf-8") as f:
        units = [u["value"] for u in json.load(f)]
    assert units == ["The waves <i>froze</i> in", "place and stayed there.",
                     "<v Ann>Over the years they", "<v Bob>moved apart."], units


def _ass_doc(lead=None, names=None):
    lead, names = lead or {}, names or {}
    return ("[Script Info]\nTitle: t\n\n[V4+ Styles]\nFormat: Name, Fontname\nStyle: Default,Arial\n\n"
            "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
            + "".join(f"Dialogue: 0,{_ass_ts(s)},{_ass_ts(e)},Default,{names.get(k, '')},0,0,0,,"
                      f"{lead.get(k, '')}{t}\n" for k, (s, e, t) in enumerate(NASA_CUES, 1)))


def test_ass_no_drift_leading_tags_kept(tmp_path, monkeypatch):
    from core.translators.extra_formats_translator import AssTranslator
    lead = {3: "{\\an8}", 4: "{\\an8\\i1}"}
    doc = _ass_doc(lead)
    src, calls = _sandbox(tmp_path, monkeypatch, lambda d, _n: merging_model(d), "nasa.ass", doc)
    out = _run(tmp_path, src, AssTranslator)
    src_lines = [ln for ln in doc.splitlines() if ln.startswith("Dialogue:")]
    out_lines = [ln for ln in out.splitlines() if ln.startswith("Dialogue:")]
    assert [ln.split(",", 9)[:9] for ln in out_lines] == [ln.split(",", 9)[:9] for ln in src_lines]
    texts = [ln.split(",", 9)[9] for ln in out_lines]
    assert texts[2].startswith("{\\an8}") and texts[3].startswith("{\\an8\\i1}"), texts[2:4]
    assert not texts[2][len("{\\an8}"):].count("{")    # nothing cut into / duplicated
    _check_pairs([(t, o) for (_, _, t), o in zip(NASA_CUES, texts)])
    assert len([k for c in calls for k in c]) == 5     # 5 groups; bare [Music] not sent


def test_ass_speaker_change_and_karaoke_not_merged(tmp_path):
    from core.pipelines.subtitle_formats_pipeline import extract_ass_content_to_json
    doc = _ass_doc(lead={5: "{\\k20}Almost {\\k30}"}, names={2: "Narrator"})
    (tmp_path / "s.ass").write_text(doc, encoding="utf-8")
    with open(extract_ass_content_to_json(str(tmp_path / "s.ass"), str(tmp_path)), encoding="utf-8") as f:
        units = [u["value"] for u in json.load(f)]
    # cue 1 / cue 2 have different speakers -> no sentence close -> one unit each
    assert units[0].startswith("[Music throughout] Immediately") and units[0].endswith("too tiny")
    assert units[1].startswith("and too hot")
    # the karaoke line is its own unit with protected tags; its run (5..13) is
    # broken, so those cues go one by one (old behaviour), never merged blindly
    assert any(u.startswith("{{ASS_0}}Almost {{ASS_1}}") for u in units)


def test_lrc_no_drift(tmp_path, monkeypatch):
    from core.translators.extra_formats_translator import LrcTranslator
    doc = ("[ti:Baryon Acoustic Oscillations]\n"
           + "".join(f"{_lrc_ts(s)}{t}\n" for s, _, t in NASA_CUES)
           + f"{_lrc_ts(NASA_CUES[-1][1])}\n")
    src, calls = _sandbox(tmp_path, monkeypatch, lambda d, _n: merging_model(d), "nasa.lrc", doc)
    out = _run(tmp_path, src, LrcTranslator)
    rows = re.findall(r"^(\[\d\d:\d\d\.\d\d\])(.+)$", out, re.M)
    assert [r[0] for r in rows] == [_lrc_ts(s) for s, _, _ in NASA_CUES]
    assert "[ti:Baryon Acoustic Oscillations]" in out
    _check_pairs([(t, r[1]) for (_, _, t), r in zip(NASA_CUES, rows)])
    assert len([k for c in calls for k in c]) == 5     # 5 groups; bare [Music] not sent


def test_lrc_pause_and_chorus_not_merged(tmp_path):
    from core.pipelines.subtitle_formats_pipeline import extract_lrc_content_to_json
    doc = ("[00:01.00]The waves froze in\n[00:03.00]\n[00:09.00]place and stayed.\n"
           "[00:12.00][01:12.00]Over time they\n[00:14.00]moved apart.\n"
           "[00:16.00]Every line a phrase\n[00:18.00]no punctuation here\n")
    (tmp_path / "p.lrc").write_text(doc, encoding="utf-8")
    with open(extract_lrc_content_to_json(str(tmp_path / "p.lrc"), str(tmp_path)), encoding="utf-8") as f:
        units = [u["value"] for u in json.load(f)]
    # empty timed line = instrumental pause; double timestamp = chorus; lyrics
    # without punctuation stay one line per unit
    assert units == ["The waves froze in", "place and stayed.", "Over time they",
                     "moved apart.", "Every line a phrase", "no punctuation here"], units

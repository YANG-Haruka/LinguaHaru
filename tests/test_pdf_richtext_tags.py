# PDF placeholder-tag leaks + number-scale errors (real-document bugs, see
# Video-Creator assets/real README §8-5 / §8-7). Deterministic: fake LLM, no
# network, no BabelDOC pipeline run. The fake replies below are the actual
# model outputs recorded in the BabelDOC caches of those runs (with the old
# "<b1>" formula placeholder now "{v1}").
import json
import os
import re
import sys
import time

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)  # prompts are loaded via relative paths

import core.translators.pdf_translator as pt  # noqa: E402
from core.engine import translation_qa  # noqa: E402

BULLET = "{v1}Total withdrawals in 2015, were 322 billion gallons per day (Bgal/d), " \
         "resulting in a 9 percent decrease since 2010"
TAG_SHAPE = re.compile(r"<\s*/?\s*b?\s*\d+\s*>")


def _translator(tmp_path, dst="zh"):
    return pt.PdfTranslator(
        str(tmp_path / "x.pdf"), model=f"fake-{time.time()}", use_online=True,
        api_key="unused", src_lang="en", dst_lang=dst, continue_mode=False,
        max_token=2048, max_retries=3, thread_count=1, glossary_path=None,
        temp_dir=str(tmp_path / "temp"), result_dir=str(tmp_path / "result"),
        session_lang="en", log_dir=str(tmp_path / "log"))


def _fake_llm(monkeypatch, replies):
    """translate_text stand-in: returns replies[i] for the i-th call (last one
    repeats) and records the segments it was sent."""
    sent = []

    def fake(segments, previous_text, *a, **kw):
        sent.append((dict(segments), previous_text))
        reply = replies[min(len(sent), len(replies)) - 1]
        return json.dumps({"1": reply}, ensure_ascii=False), True, None

    monkeypatch.setattr(pt, "translate_text", fake)
    return sent


# --- A. placeholder tags --------------------------------------------------- #

@pytest.mark.parametrize("reply, expected", [
    # es/fr/de 5/5 runs: model "closed" the lone placeholder
    ("{v1}2015 年的总取水量为 3220 亿加仑/日（Bgal/d），较 2010 年下降了 9%</b1>",
     "{v1}2015 年的总取水量为 3220 亿加仑/日（Bgal/d），较 2010 年下降了 9%"),
    # ja/ko: model dropped the bullet placeholder -> put back at the start
    ("2015 年的总取水量为 3220 亿加仑/日（Bgal/d），较 2010 年下降了 9%",
     "{v1}2015 年的总取水量为 3220 亿加仑/日（Bgal/d），较 2010 年下降了 9%"),
    # mangled leftovers + a duplicate + spacing variant
    ("{ v1 }2015 年总取水量为 3220 亿加仑/日 <1> 下降了 9%{v1}</1>",
     "{v1}2015 年总取水量为 3220 亿加仑/日 下降了 9%"),
])
def test_paragraph_never_returns_stray_tags(tmp_path, monkeypatch, reply, expected):
    _fake_llm(monkeypatch, [reply])
    out = _translator(tmp_path)._translate_paragraph(BULLET)
    assert out == expected
    assert not TAG_SHAPE.search(out)


def test_missing_mid_placeholder_retries_then_keeps_source(tmp_path, monkeypatch):
    src = "Water use {v1} grew by 4 percent"
    sent = _fake_llm(monkeypatch, ["用水量增长了 4%"])
    tr = _translator(tmp_path)
    assert tr._translate_paragraph(src) == src     # position unknown -> source
    assert len(sent) == 2                          # ... after one retry
    sent = _fake_llm(monkeypatch, ["用水量增长了 4%", "用水量 {v1} 增长了 4%"])
    assert tr._translate_paragraph(src) == "用水量 {v1} 增长了 4%"


def test_repair_is_noop_without_tags():
    assert pt._repair_placeholders("plain", "普通  文本") == "普通  文本"


def test_proofread_override_is_sanitised(tmp_path):
    tr = _translator(tmp_path)
    tr._proofread_overrides = {BULLET: "总取水量 3220 亿加仑</b1>"}
    assert tr._translate_paragraph(BULLET) == "{v1}总取水量 3220 亿加仑"


def test_callback_placeholders_give_babeldoc_real_regexes():
    """BabelDOC builds its hallucinated-placeholder filters from
    translator.get_*_placeholder(r"\\d+")[1]. With BaseTranslator's bare strings
    that was the single char "b" / "/", deleting every unallowed B and slash."""
    from babeldoc.format.pdf.document_il.midend.il_translator import (
        FormulaPlaceholder, ILTranslator)
    from babeldoc.format.pdf.document_il import il_version_1 as il

    eng = pt._CallbackTranslator("en", "de", lambda t: t, {"t": "1"}, ignore_cache=True)
    il_tr = ILTranslator.__new__(ILTranslator)
    il_tr.translate_engine = eng
    # exactly what ILTranslator.__init__ does
    il_tr._formula_placeholder_pattern = re.compile(eng.get_formular_placeholder(r"\d+")[1], re.I)
    il_tr._style_left_placeholder_pattern = re.compile(
        eng.get_rich_text_left_placeholder(r"\d+")[1], re.I)
    il_tr._style_right_placeholder_pattern = re.compile(
        eng.get_rich_text_right_placeholder(r"\d+")[1], re.I)
    assert il_tr._formula_placeholder_pattern.fullmatch("{v12}")
    assert il_tr._style_right_placeholder_pattern.fullmatch("</b3>")

    src = "{v1}Withdrawals for thermoelectric power, irrigation, and public supply"
    para = il.PdfParagraph(unicode="• Withdrawals for thermoelectric power, irrigation, "
                                   "and public supply")   # no "B", no "/"
    ph = il_tr.create_formula_placeholder(il.PdfFormula(), 1, para)
    assert isinstance(ph, FormulaPlaceholder) and ph.placeholder == "{v1}"
    inp = ILTranslator.TranslateInput(src, [ph], il.PdfStyle())
    out = "{v1}Entnahmen für Bewässerung und öffentliche Versorgung (Bgal/d)</b2>"
    comps = il_tr.parse_translate_output(inp, out)
    assert comps[0].pdf_formula is ph.formula
    text = "".join(c.pdf_same_style_unicode_characters.unicode for c in comps[1:])
    assert text == "Entnahmen für Bewässerung und öffentliche Versorgung (Bgal/d)"


# --- B. number + scale word ------------------------------------------------ #

def test_cjk_targets_receive_native_units(tmp_path, monkeypatch):
    for dst, unit in (("zh", "3220亿"), ("ja", "3220億"), ("ko", "3220억")):
        sent = _fake_llm(monkeypatch, ["{v1}" + unit + " 2015 2010 9"])
        _translator(tmp_path, dst)._translate_paragraph(BULLET)
        assert unit + " gallons" in sent[0][0]["1"], sent


def test_wrong_magnitude_is_retried(tmp_path, monkeypatch):
    sent = _fake_llm(monkeypatch, [
        "{v1}2015 年的总取水量为 322 亿加仑/日（Bgal/d），较 2010 年下降了 9%",
        "{v1}2015 年的总取水量为 3220 亿加仑/日（Bgal/d），较 2010 年下降了 9%"])
    out = _translator(tmp_path)._translate_paragraph(BULLET)
    assert len(sent) == 2 and "3220 亿" in out


def test_persistent_wrong_magnitude_ships_and_is_flagged(tmp_path, monkeypatch):
    bad = "{v1}2015 年の総汲出量は 322 バリオンガロン/日（Bgal/d）で、2010 年以降に 9 パーセント減少"
    _fake_llm(monkeypatch, [bad])
    tr = _translator(tmp_path, "ja")
    assert tr._translate_paragraph(BULLET) == bad      # not dropped to English
    tr.topts = {"params": {"qa": ["placeholders"]}}
    tr._write_qa_report()
    with open(os.path.join(tr.result_dir, "qa.json"), encoding="utf-8") as f:
        assert json.load(f)["numbers"][0]["numbers"] == ["322 billion"]


@pytest.mark.parametrize("dst_lang, text, ok", [
    ("zh", "总取水量为 322 亿加仑/日", False),          # x10 off (zh run-5)
    ("zh", "总取水量为 3220 亿加仑/日", True),
    ("ja", "322 バリオンガロン/日", False),               # "baryon" (ja 4/4)
    ("ja", "3,220億ガロン/日", True),
    ("ko", "일일 322 억 갤런", False),                  # x10 off (ko)
    ("ko", "일일 3220억 갤런", True),
    ("ko", "322 십억 갤런", True),
    ("es", "322 mil millones de galones", True),
    ("fr", "322 milliards de gallons", True),
    ("fr", "322 billions de gallons", False),          # FR billion = 10^12
    ("de", "322 Milliarden Gallonen", True),
    ("de", "322 Millionen Gallonen", False),
    ("ru", "322 миллиарда", True),                     # unsupported pair: skipped
])
def test_scale_number_rule(dst_lang, text, ok):
    bad = translation_qa.scale_number_mismatches(
        "were 322 billion gallons per day", text, "en", dst_lang)
    assert (bad == []) is ok, bad


def test_qa_flags_leftover_tags():
    items = [{"count_src": 1, "original": "{v1}Total 9 percent",
              "translated": "{v1}总计 9%</b1>"},
             {"count_src": 2, "original": "Since 2010", "translated": "自 2010 年<1>"},
             {"count_src": 3, "original": "{v1}Fine", "translated": "{v1}很好"}]
    assert translation_qa.run(["placeholders"], items)["placeholders"] == [1, 2]

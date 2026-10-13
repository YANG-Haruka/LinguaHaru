# Manga/image text erase: only the text ink is removed, tight speech-balloon
# outlines survive, flat balloons are filled (no smear), textured ground falls
# back to inpainting, and lettering stays inside the balloon.
# Deterministic synthetic images, no OCR model / GPU / LLM.
#
# Run from the repo root:
#   python -m pytest tests/test_manga_erase.py
import os
import sys

import cv2
import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core.pipelines.image_translation_pipeline as M  # noqa: E402

PAPER = (200, 225, 235)    # BGR cream page
INK = (40, 40, 40)


@pytest.fixture(autouse=True)
def _no_config(monkeypatch):
    monkeypatch.setattr(M, "_lama_enabled", lambda: False)
    monkeypatch.setattr(M, "_erase_mode", lambda: "ink")


def _glyphs(img, x0, y0, cols, rows, cs):
    """Draw char-like strokes (a little cross + bar per cell) on a cs grid."""
    for c in range(cols):
        for r in range(rows):
            x, y = x0 + c * cs, y0 + r * cs
            cv2.line(img, (x + 4, y + cs // 2), (x + cs - 6, y + cs // 2), INK, 3)
            cv2.line(img, (x + cs // 2, y + 4), (x + cs // 2, y + cs - 6), INK, 3)
            cv2.line(img, (x + 5, y + cs - 6), (x + cs - 7, y + 6), INK, 2)


def _scene(shape="rect"):
    """A page with art around a tight balloon; returns (img, region, text_mask,
    outline_mask). The OCR box pokes out over the balloon outline (the bug)."""
    img = np.full((400, 500, 3), PAPER, np.uint8)
    cv2.rectangle(img, (5, 5), (494, 394), INK, 4)                       # panel border
    for i in range(0, 500, 23):                                          # hatched art
        cv2.line(img, (i, 330), (i + 40, 394), (60, 90, 60), 2)
    outline = np.zeros(img.shape[:2], np.uint8)
    if shape == "rect":
        cv2.rectangle(outline, (150, 80), (330, 270), 255, 3)
    else:
        cv2.ellipse(outline, (240, 175), (120, 110), 0, 0, 360, 255, 3)
    img[outline > 0] = INK
    cs = 30
    before = img.copy()
    _glyphs(img, 180, 115, 4, 4, cs)                                     # 4 lines x 4 chars
    text = np.any(img != before, axis=2)
    # OCR boxes: one per row, padded so they overlap the balloon outline
    rects = [[140, 110 + r * cs - 2, 340, 110 + (r + 1) * cs + 2] for r in range(4)]
    region = {"rect": [140, 108, 340, 232], "erase_rects": rects,
              "needs_translation": True, "count_src": 1, "value": "x"}
    return img, region, text, outline > 0


@pytest.mark.parametrize("shape", ["rect", "ellipse"])
def test_tight_balloon_outline_survives_and_text_removed(shape):
    img, region, text, outline = _scene(shape)
    out = M.inpaint_page(img, [region])
    gray = cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)
    paper = cv2.cvtColor(np.uint8([[PAPER]]), cv2.COLOR_BGR2GRAY)[0, 0]
    # every outline pixel stays dark ink
    assert (gray[outline] < 80).mean() > 0.99
    # text strokes are gone (back to paper tone), nothing smeared grey
    assert np.abs(gray[text].astype(int) - paper).max() <= 12
    # outside the balloon nothing changed
    far = ~cv2.dilate(outline.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    far &= ~cv2.dilate(text.astype(np.uint8), np.ones((9, 9), np.uint8)).astype(bool)
    assert (out[far] == img[far]).all()


def test_box_mode_is_the_legacy_behaviour(monkeypatch):
    """The old erase (whole dilated OCR box + Telea) is kept behind
    image_erase_mode=box - and it is what smeared the outline."""
    monkeypatch.setattr(M, "_erase_mode", lambda: "box")
    img, region, _text, outline = _scene("rect")
    gray = cv2.cvtColor(M.inpaint_page(img, [region]), cv2.COLOR_BGR2GRAY)
    assert (gray[outline] < 80).mean() < 0.9


def test_textured_background_falls_back_to_inpaint(monkeypatch):
    rng = np.random.default_rng(1)
    img = rng.integers(60, 200, (240, 320, 3), dtype=np.uint8)            # noisy ground
    img = cv2.GaussianBlur(img, (3, 3), 0)
    _glyphs(img, 100, 90, 4, 1, 30)
    for c in range(4):                                                    # dark text
        x = 100 + c * 30
        cv2.line(img, (x + 4, 105), (x + 24, 105), (0, 0, 0), 3)
    region = {"rect": [96, 86, 224, 124], "needs_translation": True, "count_src": 1}
    calls = []
    real = M._apply_inpaint
    monkeypatch.setattr(M, "_apply_inpaint",
                        lambda im, m, dilate=True: (calls.append(dilate), real(im, m, dilate))[1])
    out = M.inpaint_page(img, [region])
    assert calls == [False]                     # local inpaint of the glyph mask only
    assert (out[:80] == img[:80]).all() and (out[140:] == img[140:]).all()
    assert cv2.cvtColor(out, cv2.COLOR_BGR2GRAY)[100:110, 104:224].min() > 30


def test_lettering_stays_inside_balloon():
    img, region, _text, outline = _scene("rect")
    (rect,) = M._render_rects(img, [region])
    # inside the 150..330 x 80..270 outline, off the stroke, not the raw OCR box
    assert rect[0] > 153 and rect[2] < 327 and rect[1] > 83 and rect[3] < 267
    pil = M.render_on_image(img, [region], {1: "这是一段中文测试译文"}, "zh")
    gray = cv2.cvtColor(cv2.cvtColor(np.array(pil), cv2.COLOR_RGB2BGR), cv2.COLOR_BGR2GRAY)
    assert (gray[outline] < 80).mean() > 0.99
    changed = np.any(np.array(pil)[..., ::-1] != img, axis=2)
    assert not changed[:78].any() and not changed[273:].any()


def test_two_regions_in_one_balloon_do_not_share_the_lettering_box():
    img, region, _t, _o = _scene("rect")
    a = dict(region, erase_rects=region["erase_rects"][:2], rect=[140, 108, 340, 170], count_src=1)
    b = dict(region, erase_rects=region["erase_rects"][2:], rect=[140, 170, 340, 232], count_src=2)
    ra, rb = M._render_rects(img, [a, b])
    assert ra[3] <= rb[1] + 4                   # stacked, not overlapping


def _tailed_balloon():
    """A tight box balloon whose outline opens at the bottom into a speech tail
    (1920s style): the interior runs on down the tail."""
    img = np.full((520, 400, 3), PAPER, np.uint8)
    cv2.rectangle(img, (5, 5), (394, 514), INK, 4)
    pts = [(150, 300), (100, 300), (100, 60), (260, 60), (260, 300), (205, 300)]
    cv2.polylines(img, [np.int32(pts)], False, INK, 3)               # open at 150..205
    for x0, x1 in ((150, 172), (205, 180)):                           # wavy tail, closed
        tail = [(int(x0 + (x1 - x0) * (y - 300) / 60 + 5 * np.sin(y / 10.0)), y)
                for y in range(300, 364, 4)]
        cv2.polylines(img, [np.int32(tail)], False, INK, 3)
    _glyphs(img, 130, 85, 3, 6, 30)                                   # 3 columns x 6 chars
    rects = [[118 + c * 30, 80, 150 + c * 30, 270] for c in range(3)]
    region = {"rect": [118, 80, 240, 270], "erase_rects": rects,
              "needs_translation": True, "count_src": 1, "value": "x"}
    return img, region


def test_lettering_does_not_run_down_the_speech_tail():
    img, region = _tailed_balloon()
    (rect,) = M._render_rects(img, [region])
    assert 100 < rect[0] and rect[2] < 260 and 60 < rect[1] and rect[3] < 300 - 4
    text = "这是一段相当长的中文测试译文要排满整个气泡才行"
    clean = M.inpaint_page(img, [region])
    pil = M.render_on_image(img, [region], {1: text}, "zh", clean_bgr=clean)
    drawn = np.any(np.array(pil)[..., ::-1] != clean, axis=2)
    assert drawn.any()
    assert not drawn[297:].any()                # nothing on/below the bottom outline
    assert not drawn[:, :103].any() and not drawn[:, 258:].any()

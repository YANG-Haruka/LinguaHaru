# Corpus tests: image OCR translation edge cases (no real OCR engine needed).
#   - low-confidence regions are not translated/erased (treated as noise)
#   - missing scores don't truncate the region list (zip-shortest bug)
#   - GIF (OpenCV can't decode) is read via the PIL fallback
#
# _run_ocr is monkeypatched so these run without invoking the OCR model.
#
# Run from the repo root:
#   python tests/test_corpus_image.py
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.corpus_common import T, check, fake_translate, run, work_dirs

WORK_DIR, TEMP_DIR, RESULT_DIR = work_dirs("image")


def _box(x1, y1, x2, y2):
    return [[x1, y1], [x2, y1], [x2, y2], [x1, y2]]


def _make_image(path, fmt=None):
    from PIL import Image
    img = Image.new("RGB", (400, 200), (255, 255, 255))
    img.save(path, format=fmt)


def test_image_confidence_filter():
    print("IMAGE: low-confidence OCR regions are skipped, high-confidence translated")
    import core.pipelines.image_translation_pipeline as ip

    src = os.path.join(WORK_DIR, "img.png")
    _make_image(src)

    # high score region + a low score (noise) region
    def fake_ocr(_file_path, _src_lang=None, _manga=False):
        return (["Real heading text", "g4rb4ge"],
                [_box(10, 10, 200, 50), _box(10, 120, 200, 160)],
                [0.97, 0.21])

    orig = ip._run_ocr
    ip._run_ocr = fake_ocr
    try:
        src_json = ip.extract_image_content_to_json(src, TEMP_DIR)
    finally:
        ip._run_ocr = orig

    with open(src_json, encoding="utf-8") as f:
        to_translate = [i["value"] for i in json.load(f)]
    check("only the confident region is queued for translation",
          to_translate == ["Real heading text"], str(to_translate))

    regions_path = os.path.join(TEMP_DIR, "img", "regions.json")
    with open(regions_path, encoding="utf-8") as f:
        regions = json.load(f)
    check("both regions recorded (low-confidence kept for the text file)",
          len(regions) == 2, str(regions))
    low = next(r for r in regions if r["value"] == "g4rb4ge")
    check("low-confidence region marked needs_translation=False",
          low["needs_translation"] is False, str(low))

    dst_json = fake_translate(src_json)
    out = ip.write_translated_content_to_image(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                               src_lang="en", dst_lang="ja")
    check("output image produced", os.path.exists(out), out)


def test_image_missing_scores_not_dropped():
    print("IMAGE: missing OCR scores don't truncate the region list")
    import core.pipelines.image_translation_pipeline as ip

    src = os.path.join(WORK_DIR, "noscore.png")
    _make_image(src)

    def fake_ocr(_file_path, _src_lang=None, _manga=False):
        # engine returned NO scores at all (empty list)
        return (["Alpha text", "Beta text"],
                [_box(10, 10, 200, 50), _box(10, 80, 200, 120)],
                [])

    orig = ip._run_ocr
    ip._run_ocr = fake_ocr
    try:
        src_json = ip.extract_image_content_to_json(src, TEMP_DIR)
    finally:
        ip._run_ocr = orig

    with open(src_json, encoding="utf-8") as f:
        vals = [i["value"] for i in json.load(f)]
    check("both regions survive despite empty scores", vals == ["Alpha text", "Beta text"],
          str(vals))


def test_image_gif_pil_fallback():
    print("IMAGE: GIF (OpenCV can't decode) read via PIL fallback")
    import core.pipelines.image_translation_pipeline as ip

    src = os.path.join(WORK_DIR, "img.gif")
    _make_image(src, fmt="GIF")

    def fake_ocr(_file_path, _src_lang=None, _manga=False):
        return (["Caption text"], [_box(10, 10, 200, 50)], [0.95])

    orig = ip._run_ocr
    ip._run_ocr = fake_ocr
    try:
        src_json = ip.extract_image_content_to_json(src, TEMP_DIR)
        dst_json = fake_translate(src_json)
        out = ip.write_translated_content_to_image(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                                   src_lang="en", dst_lang="ja")
    finally:
        ip._run_ocr = orig
    check("GIF translated image produced (no decode crash)", os.path.exists(out), out)


if __name__ == "__main__":
    run([test_image_confidence_filter, test_image_missing_scores_not_dropped,
         test_image_gif_pil_fallback])

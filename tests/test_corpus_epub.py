# Corpus tests: EPUB with 3 chapters, internal cross-links, image, CSS.
#
# Run from the repo root:
#   python tests/test_corpus_epub.py
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.corpus_common import T, check, fake_translate, run, work_dirs

WORK_DIR, TEMP_DIR, RESULT_DIR = work_dirs("epub")

CSS = "body { font-family: serif; margin: 2em; }\nh1 { color: #224488; }\n"

CHAPTER_TMPL = ('<?xml version="1.0" encoding="utf-8"?>'
                '<html xmlns="http://www.w3.org/1999/xhtml">'
                '<head><title>{title}</title>'
                '<link rel="stylesheet" type="text/css" href="style.css"/></head>'
                "<body>{body}</body></html>")


def build_epub(path):
    from PIL import Image
    img_path = os.path.join(WORK_DIR, "cover.png")
    Image.new("RGB", (40, 40), (10, 120, 230)).save(img_path)
    with open(img_path, "rb") as f:
        img_bytes = f.read()

    ch1 = CHAPTER_TMPL.format(title="Chapter One Title", body=(
        '<h1 id="c1top">The Journey Begins</h1>'
        '<p>See <a href="ch2.xhtml#c2top">the second chapter</a> for the continuation, '
        'or jump to <a href="ch3.xhtml#ending">the final scene</a> directly.</p>'
        '<p><img src="cover.png" alt="Cover artwork image"/> Caption text after image.</p>'))
    ch2 = CHAPTER_TMPL.format(title="Chapter Two Title", body=(
        '<h1 id="c2top">The Road Onward</h1>'
        '<p>Back to <a href="ch1.xhtml#c1top">the first chapter</a> any time.</p>'))
    ch3 = CHAPTER_TMPL.format(title="Chapter Three Title", body=(
        '<h1 id="ending">The Final Scene</h1>'
        "<p>Every story reaches its conclusion eventually.</p>"))

    opf = ('<?xml version="1.0"?><package xmlns="http://www.idpf.org/2007/opf" version="2.0" '
           'unique-identifier="id"><metadata/><manifest>'
           '<item id="css" href="style.css" media-type="text/css"/>'
           '<item id="img" href="cover.png" media-type="image/png"/>'
           '<item id="c1" href="ch1.xhtml" media-type="application/xhtml+xml"/>'
           '<item id="c2" href="ch2.xhtml" media-type="application/xhtml+xml"/>'
           '<item id="c3" href="ch3.xhtml" media-type="application/xhtml+xml"/>'
           '</manifest><spine><itemref idref="c1"/><itemref idref="c2"/>'
           '<itemref idref="c3"/></spine></package>')
    container = ('<?xml version="1.0"?><container version="1.0" '
                 'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles>'
                 '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
                 "</rootfiles></container>")

    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/epub+zip", compress_type=zipfile.ZIP_STORED)
        z.writestr("META-INF/container.xml", container, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("content.opf", opf, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("style.css", CSS, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("cover.png", img_bytes, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("ch1.xhtml", ch1, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("ch2.xhtml", ch2, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("ch3.xhtml", ch3, compress_type=zipfile.ZIP_DEFLATED)
    return img_bytes


def test_epub_three_chapters():
    print("EPUB: 3 chapters, cross-links, image ref, CSS byte-identical")
    from core.pipelines.epub_translation_pipeline import (
        extract_epub_content_to_json, write_translated_content_to_epub)

    src = os.path.join(WORK_DIR, "trilogy.epub")
    img_bytes = build_epub(src)

    src_json = extract_epub_content_to_json(src, TEMP_DIR)
    dst_json = fake_translate(src_json)
    out = write_translated_content_to_epub(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                           src_lang="en", dst_lang="ja")

    with zipfile.ZipFile(out) as z:
        infos = z.infolist()
        names = z.namelist()
        ch1 = z.read("ch1.xhtml").decode("utf-8")
        ch2 = z.read("ch2.xhtml").decode("utf-8")
        ch3 = z.read("ch3.xhtml").decode("utf-8")
        css = z.read("style.css").decode("utf-8")
        img = z.read("cover.png")

    # --- zip structure ---
    check("mimetype first and uncompressed",
          infos[0].filename == "mimetype" and infos[0].compress_type == zipfile.ZIP_STORED,
          f"{infos[0].filename}/{infos[0].compress_type}")
    check("all 8 members preserved", len(names) == 8 and "cover.png" in names, str(names))

    # --- per-chapter translation ---
    check("chapter 1 heading translated", T + "The Journey Begins" in ch1, ch1)
    check("chapter 2 heading translated", T + "The Road Onward" in ch2, ch2)
    check("chapter 3 heading and paragraph translated",
          T + "The Final Scene" in ch3
          and T + "Every story reaches its conclusion eventually." in ch3, ch3)

    # --- cross-links survive with fragments, link text translated in place ---
    check("ch1 forward links keep href+fragment",
          'href="ch2.xhtml#c2top"' in ch1 and 'href="ch3.xhtml#ending"' in ch1, ch1)
    check("ch1 link texts stay inside their anchors",
          ">the second chapter</a>" in ch1 and ">the final scene</a>" in ch1, ch1)
    check("ch2 backward link kept", 'href="ch1.xhtml#c1top"' in ch2, ch2)
    check("link target ids survive",
          'id="c1top"' in ch1 and 'id="c2top"' in ch2 and 'id="ending"' in ch3,
          f"{ch1}\n{ch2}\n{ch3}")
    check("no HLINK placeholders leaked",
          all("HLINK" not in c for c in (ch1, ch2, ch3)), ch1 + ch2 + ch3)

    # --- assets untouched ---
    check("CSS byte-identical", css == CSS, repr(css))
    check("image bytes identical", img == img_bytes, f"{len(img)} vs {len(img_bytes)} bytes")
    check("img element and src survive", 'src="cover.png"' in ch1, ch1)
    # The [T] prefix lands on the paragraph text before the image marker;
    # the caption is the image's tail text, still after the img element
    img_pos = ch1.find('src="cover.png"')
    cap_pos = ch1.find("Caption text after image.")
    check("img stays in its paragraph, caption text after it",
          0 <= img_pos < cap_pos, f"img={img_pos} cap={cap_pos}\n{ch1}")


if __name__ == "__main__":
    run([test_epub_three_chapters])

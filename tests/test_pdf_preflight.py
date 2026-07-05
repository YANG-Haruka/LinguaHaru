# PDF preflight guard test: encrypted and scanned/image-only PDFs must fail
# with a clear, actionable error instead of producing a silently-empty output.
#
# Fast: the guard runs before the (expensive) BabelDOC pass, so no layout
# analysis happens here.
#
# Run from the repo root:
#   python tests/test_pdf_preflight.py
import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

WORK_DIR = os.path.join(REPO_ROOT, "tests", "_roundtrip_work", "pdf_preflight")


def _make_translator(pdf_path):
    import core.translators.pdf_translator as pt
    return pt.PdfTranslator(
        pdf_path, model=f"fake-{int(time.time())}", use_online=True, api_key="unused",
        src_lang="en", dst_lang="zh", continue_mode=False, max_token=2048,
        max_retries=3, thread_count=1, glossary_path=None,
        temp_dir=os.path.join(WORK_DIR, "temp"),
        result_dir=os.path.join(WORK_DIR, "result"),
        session_lang="en", log_dir=os.path.join(WORK_DIR, "log"),
    )


def main():
    import pymupdf

    os.makedirs(WORK_DIR, exist_ok=True)
    passed = 0

    # 1) Scanned / image-only PDF: a page with no text layer.
    scanned = os.path.join(WORK_DIR, "scanned.pdf")
    doc = pymupdf.open()
    doc.new_page()  # blank page == no extractable text, like a scan
    doc.save(scanned)
    doc.close()

    try:
        _make_translator(scanned)._preflight_check()
        print("FAIL: scanned PDF did not raise")
    except RuntimeError as e:
        assert "scanned" in str(e).lower() or "no extractable text" in str(e).lower(), str(e)
        print(f"PASS: scanned PDF rejected -> {e}")
        passed += 1

    # 2) Encrypted / password-protected PDF.
    encrypted = os.path.join(WORK_DIR, "encrypted.pdf")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Secret content that needs a password.", fontsize=12)
    doc.save(encrypted, encryption=pymupdf.PDF_ENCRYPT_AES_256,
             owner_pw="owner", user_pw="secret")
    doc.close()

    try:
        _make_translator(encrypted)._preflight_check()
        print("FAIL: encrypted PDF did not raise")
    except RuntimeError as e:
        assert "password" in str(e).lower(), str(e)
        print(f"PASS: encrypted PDF rejected -> {e}")
        passed += 1

    # 3) A normal text PDF passes the guard.
    normal = os.path.join(WORK_DIR, "normal.pdf")
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "A normal paragraph with a real text layer.", fontsize=12)
    doc.save(normal)
    doc.close()

    _make_translator(normal)._preflight_check()  # must not raise
    print("PASS: normal text PDF passes the guard")
    passed += 1

    assert passed == 3, f"only {passed}/3 preflight checks passed"
    print("PASS: PDF preflight guard works (scanned + encrypted + normal)")


if __name__ == "__main__":
    main()

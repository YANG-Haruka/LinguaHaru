# Video/audio translation wiring tests (no STT model / ffmpeg needed):
#   - SenseVoice language codes are unified into one source of truth
#   - bilingual subtitle flag flows to media files
#
# Run from the repo root:
#   python tests/test_video_wiring.py
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

CHECKS = []


def check(name, cond, detail=""):
    CHECKS.append(bool(cond))
    print(f"  [{'PASS' if cond else 'FAIL'}] {name}" + (f"\n      -> {detail}" if detail and not cond else ""))


def test_sensevoice_codes_unified():
    print("VIDEO: SenseVoice supported codes derive from one map; lang mapping consistent")
    from core.pipelines.video_translation_pipeline import (
        _sensevoice_lang, _SENSEVOICE_LANG_MAP, SENSEVOICE_SUPPORTED_CODES)

    check("supported codes == map keys (no drift)",
          SENSEVOICE_SUPPORTED_CODES == set(_SENSEVOICE_LANG_MAP), str(SENSEVOICE_SUPPORTED_CODES))
    check("zh-Hant maps to zh", _sensevoice_lang("zh-Hant") == "zh")
    check("ja maps to ja", _sensevoice_lang("ja") == "ja")
    check("unsupported (de) -> auto", _sensevoice_lang("de") == "auto")
    check("cantonese yue accepted directly", _sensevoice_lang("yue") == "yue")
    check("every supported UI code resolves to a real ASR code (not auto)",
          all(_sensevoice_lang(c) != "auto" for c in SENSEVOICE_SUPPORTED_CODES),
          str({c: _sensevoice_lang(c) for c in SENSEVOICE_SUPPORTED_CODES}))


def test_video_bilingual_flag_flow():
    print("VIDEO: bilingual subtitle flag is wired for media files")
    import core.backend as b
    from core.optional_modules import MEDIA_EXTENSIONS

    for ext in (".mp4", ".mp3", ".mkv"):
        p = b.get_translator_class(ext, subtitle_bilingual_mode=True)
        check(f"{ext} -> partial carries bilingual_mode=True",
              getattr(p, "keywords", {}).get("bilingual_mode") is True, str(getattr(p, "keywords", None)))
        check(f"{ext} -> UI key is subtitle_bilingual_mode",
              b.BILINGUAL_KEY_BY_EXT.get(ext) == "subtitle_bilingual_mode",
              b.BILINGUAL_KEY_BY_EXT.get(ext))

    check("all media extensions covered in BILINGUAL_KEY_BY_EXT",
          all(b.BILINGUAL_KEY_BY_EXT.get(e) == "subtitle_bilingual_mode" for e in MEDIA_EXTENSIONS),
          str(MEDIA_EXTENSIONS))

    # VideoTranslator accepts and stores the flag
    from core.translators.video_translator import VideoTranslator
    check("VideoTranslator.__init__ accepts bilingual_mode",
          "bilingual_mode" in VideoTranslator.__init__.__code__.co_varnames)


if __name__ == "__main__":
    test_sensevoice_codes_unified()
    test_video_bilingual_flag_flow()
    print("\n" + "=" * 60)
    print(f"{sum(CHECKS)}/{len(CHECKS)} checks passed")
    if not all(CHECKS):
        sys.exit(1)

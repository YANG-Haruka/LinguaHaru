# End-to-end tests for the optional image / video translation modules.
# The LLM call is replaced with a fake translator; OCR, inpainting, text
# rendering, ffmpeg extraction and whisper transcription all run for real.
#
# Run from the repo root:
#   python tests/test_optional_modules.py
import json
import os
import sys

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)
os.environ.setdefault("LINGUAHARU_WHISPER_MODEL", "tiny")  # small download for tests

WORK_DIR = os.path.join(REPO_ROOT, "tests", "_roundtrip_work", "optional")
T = "[T]"


def install_fake_llm():
    """Replace the LLM call inside the base translator pipeline."""
    import core.engine.base_translator as bt
    from core.engine.translation_checker import clean_json

    def fake_translate_text(segments, previous_text, model, use_online, api_key,
                            system_prompt, user_prompt, previous_prompt,
                            glossary_prompt, glossary_terms=None, check_stop_callback=None,
                            **kwargs):
        data = json.loads(clean_json(segments if isinstance(segments, str)
                                     else json.dumps(segments, ensure_ascii=False)))
        reply = {k: T + v for k, v in data.items()}
        return json.dumps(reply, ensure_ascii=False), True, {
            "prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2}

    bt.translate_text = fake_translate_text


def make_translator(cls, path, **overrides):
    kwargs = dict(
        input_file_path=path, model="fake", use_online=True, api_key="x",
        src_lang="en", dst_lang="fr", continue_mode=False, max_token=2048,
        max_retries=2, thread_count=2, glossary_path=None,
        temp_dir=os.path.join(WORK_DIR, "temp"),
        result_dir=os.path.join(WORK_DIR, "result"),
        session_lang="en", log_dir=os.path.join(WORK_DIR, "log"),
    )
    kwargs.update(overrides)
    return cls(**kwargs)


def test_image():
    print("IMAGE: OCR -> translate -> render back")
    from PIL import Image, ImageDraw, ImageFont
    from core.translators.image_translator import ImageTranslator

    img_path = os.path.join(WORK_DIR, "img_test.png")
    image = Image.new("RGB", (800, 300), "white")
    draw = ImageDraw.Draw(image)
    try:
        font = ImageFont.truetype(r"C:\Windows\Fonts\arial.ttf", 40)
    except OSError:
        font = ImageFont.load_default()
    draw.text((40, 60), "Hello World", fill="black", font=font)
    draw.text((40, 160), "Quality Control Report", fill="black", font=font)
    image.save(img_path)

    translator = make_translator(ImageTranslator, img_path)
    out_path, missing = translator.process("img_test", ".png")

    print(f"  output: {out_path}")
    assert os.path.exists(out_path), "translated image missing"
    assert not missing, f"missing translations: {missing}"

    pairs_path = out_path.rsplit(".", 1)[0] + ".txt"
    assert os.path.exists(pairs_path), "companion text file missing"
    with open(pairs_path, encoding="utf-8") as f:
        pairs = f.read()
    assert T in pairs, f"no translation in pairs file: {pairs!r}"
    print("  PASS: image translated, companion text written")
    return True


def _ensure_speech_fixture():
    """Synthesize a short speech WAV via Windows TTS if not present."""
    wav_path = os.path.join(REPO_ROOT, "tests", "fixtures", "speech.wav")
    if os.path.exists(wav_path):
        return wav_path
    os.makedirs(os.path.dirname(wav_path), exist_ok=True)
    import subprocess
    script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        f"$s.SetOutputToWaveFile('{wav_path}'); "
        "$s.Speak('Hello world. This is a test of the subtitle translation system. "
        "Thank you very much.'); $s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True)
    return wav_path


def test_video():
    print("VIDEO/AUDIO: ffmpeg -> whisper -> SRT -> translate")
    from core.translators.video_translator import VideoTranslator

    wav_path = _ensure_speech_fixture()
    assert os.path.exists(wav_path), "speech.wav fixture missing"

    translator = make_translator(VideoTranslator, wav_path)
    out_path, missing = translator.process("speech", ".wav")

    print(f"  output: {out_path}")
    assert out_path.endswith(".srt"), f"expected .srt output, got {out_path}"
    assert os.path.exists(out_path), "translated srt missing"

    with open(out_path, encoding="utf-8") as f:
        srt = f.read()
    assert "-->" in srt, f"no cues in srt: {srt!r}"
    assert T in srt, f"no translation in srt: {srt!r}"

    transcript = os.path.join(WORK_DIR, "result", "speech_transcribed.srt")
    assert os.path.exists(transcript), "raw transcript copy missing"
    print(f"  transcript excerpt: {srt[:160]!r}")
    print("  PASS: audio transcribed and subtitle translated")
    return True


def main():
    os.makedirs(WORK_DIR, exist_ok=True)
    install_fake_llm()

    results = {}
    for fn in (test_image, test_video):
        try:
            results[fn.__name__] = fn()
        except Exception:
            import traceback
            traceback.print_exc()
            results[fn.__name__] = False
        print()

    for name, passed in results.items():
        print(f"{'PASS' if passed else 'FAIL'}: {name}")
    sys.exit(0 if all(results.values()) else 1)


if __name__ == "__main__":
    main()

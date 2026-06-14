# Optional module: video/audio subtitle translation.
# Requires: pip install -r requirements/video.txt, plus ffmpeg on PATH.
import os
import json
from datetime import datetime

from core.pipelines.video_translation_pipeline import transcribe_media_to_srt
from core.pipelines.subtitle_translation_pipeline import (
    extract_srt_content_to_json, write_translated_content_to_srt)
from core.engine.base_translator import DocumentTranslator


class VideoTranslator(DocumentTranslator):
    """Transcribes the audio track with faster-whisper, then feeds the result
    through the existing SRT translation pipeline. Output is a translated
    .srt file (plus the raw transcript for reference)."""

    @property
    def _generated_srt_path(self):
        # Deterministic so it also resolves when extraction is skipped
        # (continue mode); named after the media file so the SRT pipeline's
        # temp folder matches this translator's file_dir
        base = os.path.splitext(os.path.basename(self.input_file_path))[0]
        return os.path.join(self.temp_dir, f"{base}.srt")

    def extract_content_to_json(self, progress_callback=None):
        transcribe_media_to_srt(
            self.input_file_path, self.temp_dir, src_lang=self.src_lang,
            progress_callback=progress_callback, transcript_copy_dir=self.result_dir)
        return extract_srt_content_to_json(self._generated_srt_path, self.temp_dir)

    def write_translated_json_to_file(self, json_path, translated_json_path, progress_callback=None):
        self._result_srt_path = write_translated_content_to_srt(
            self._generated_srt_path, json_path, translated_json_path,
            self.result_dir, self.src_lang, self.dst_lang)

    @staticmethod
    def _translate_subtitles_enabled():
        """When the user unticks 'translate subtitles', we only transcribe."""
        try:
            with open(os.path.join("config", "system_config.json"), encoding="utf-8") as f:
                return bool(json.load(f).get("translate_subtitles", True))
        except Exception:
            return True

    def process(self, file_name, file_extension, progress_callback=None):
        if not self._translate_subtitles_enabled():
            # Transcribe-only: emit the source-language SRT, skip LLM translation.
            self.translation_start_time = datetime.now()
            transcribe_media_to_srt(
                self.input_file_path, self.temp_dir, src_lang=self.src_lang,
                progress_callback=progress_callback, transcript_copy_dir=self.result_dir)
            if progress_callback:
                progress_callback(1.0, desc=self._get_status_message("Translation completed"))
            base = os.path.splitext(os.path.basename(self.input_file_path))[0]
            self._result_srt_path = os.path.join(self.result_dir, f"{base}_transcribed.srt")
            return self._result_srt_path, {}

        _, missing_counts = super().process(file_name, file_extension, progress_callback)
        # The deliverable is the translated subtitle file, not a media file
        return self._result_srt_path, missing_counts

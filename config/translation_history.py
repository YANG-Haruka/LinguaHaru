"""
Translation History Manager Module
Manages reading and writing translation records to log/translation_summary.json
"""

import os
import json
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any
from config.log_config import app_logger


class TranslationHistoryManager:
    """Manages translation history records"""

    MAX_RECORDS = 100  # Keep only the most recent 100 records

    def __init__(self, log_dir: str = "log"):
        self.log_dir = log_dir
        self.summary_file = os.path.join(log_dir, "translation_summary.json")
        self._ensure_file_exists()

    def _ensure_file_exists(self):
        """Ensure the log directory and summary file exist"""
        os.makedirs(self.log_dir, exist_ok=True)
        if not os.path.exists(self.summary_file):
            self._write_records([])

    def _read_records(self) -> List[Dict[str, Any]]:
        """Read all records from the summary file"""
        try:
            with open(self.summary_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data if isinstance(data, list) else []
        except (json.JSONDecodeError, FileNotFoundError) as e:
            app_logger.warning(f"Error reading translation history: {e}")
            return []

    def _write_records(self, records: List[Dict[str, Any]]):
        """Write records to the summary file"""
        try:
            with open(self.summary_file, 'w', encoding='utf-8') as f:
                json.dump(records, f, ensure_ascii=False, indent=2)
        except Exception as e:
            app_logger.error(f"Error writing translation history: {e}")

    def get_all_records(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """
        Get translation records, sorted by start_time (newest first)

        Args:
            limit: Maximum number of records to return. None means all records.

        Returns:
            List of translation records
        """
        records = self._read_records()
        # Sort by start_time descending (newest first)
        records.sort(key=lambda x: x.get('start_time', ''), reverse=True)

        if limit is not None:
            return records[:limit]
        return records

    def add_record(self, record: Dict[str, Any]) -> bool:
        """
        Add a new translation record

        Args:
            record: Translation record dictionary containing:
                - id: unique identifier
                - start_time: ISO format datetime string
                - end_time: ISO format datetime string
                - duration_seconds: translation duration in seconds
                - total_tokens: tokens consumed
                - src_lang: source language code
                - src_lang_display: source language display name
                - dst_lang: target language code
                - dst_lang_display: target language display name
                - model: model name
                - use_online: whether online model was used
                - input_file: input filename
                - output_file_path: full path to output file
                - log_file_path: full path to log file
                - status: "success", "failed", or "stopped"

        Returns:
            True if record was added successfully
        """
        try:
            records = self._read_records()

            # Check if record with same ID exists (update it)
            existing_index = None
            for i, r in enumerate(records):
                if r.get('id') == record.get('id'):
                    existing_index = i
                    break

            if existing_index is not None:
                records[existing_index] = record
            else:
                records.append(record)

            # Sort by start_time descending and keep only MAX_RECORDS
            records.sort(key=lambda x: x.get('start_time', ''), reverse=True)
            records = records[:self.MAX_RECORDS]

            self._write_records(records)
            return True
        except Exception as e:
            app_logger.error(f"Error adding translation record: {e}")
            return False

    def get_record_by_id(self, record_id: str) -> Optional[Dict[str, Any]]:
        """Get a specific record by its ID"""
        records = self._read_records()
        for record in records:
            if record.get('id') == record_id:
                return record
        return None

    def delete_record(self, record_id: str) -> bool:
        """Delete a record by its ID"""
        try:
            records = self._read_records()
            records = [r for r in records if r.get('id') != record_id]
            self._write_records(records)
            return True
        except Exception as e:
            app_logger.error(f"Error deleting translation record: {e}")
            return False

    def clear_all_records(self) -> bool:
        """Clear all translation records"""
        try:
            self._write_records([])
            return True
        except Exception as e:
            app_logger.error(f"Error clearing translation records: {e}")
            return False


def create_translation_record(
    translation_id: str,
    start_time: datetime,
    end_time: datetime,
    total_tokens: int,
    src_lang: str,
    src_lang_display: str,
    dst_lang: str,
    dst_lang_display: str,
    model: str,
    use_online: bool,
    input_file: str,
    output_file_path: str,
    log_file_path: str,
    status: str
) -> Dict[str, Any]:
    """
    Create a translation record dictionary

    Args:
        translation_id: Unique identifier for the translation
        start_time: Translation start time
        end_time: Translation end time
        total_tokens: Total tokens consumed
        src_lang: Source language code
        src_lang_display: Source language display name
        dst_lang: Target language code
        dst_lang_display: Target language display name
        model: Model name used
        use_online: Whether online model was used
        input_file: Input filename (without path)
        output_file_path: Full path to the output file
        log_file_path: Full path to the log file
        status: Translation status ("success", "failed", or "stopped")

    Returns:
        Dictionary containing the translation record
    """
    duration_seconds = int((end_time - start_time).total_seconds())

    return {
        "id": translation_id,
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "duration_seconds": duration_seconds,
        "total_tokens": total_tokens,
        "src_lang": src_lang,
        "src_lang_display": src_lang_display,
        "dst_lang": dst_lang,
        "dst_lang_display": dst_lang_display,
        "model": model,
        "use_online": use_online,
        "input_file": input_file,
        "output_file_path": output_file_path,
        "log_file_path": log_file_path,
        "status": status
    }


def format_duration(seconds: int) -> str:
    """
    Format duration in seconds to human-readable string

    Args:
        seconds: Duration in seconds

    Returns:
        Formatted string like "5m 23s" or "1h 30m 45s"
    """
    if seconds < 60:
        return f"{seconds}s"

    minutes = seconds // 60
    remaining_seconds = seconds % 60

    if minutes < 60:
        if remaining_seconds > 0:
            return f"{minutes}m {remaining_seconds}s"
        return f"{minutes}m"

    hours = minutes // 60
    remaining_minutes = minutes % 60

    parts = [f"{hours}h"]
    if remaining_minutes > 0:
        parts.append(f"{remaining_minutes}m")
    if remaining_seconds > 0:
        parts.append(f"{remaining_seconds}s")

    return " ".join(parts)


def format_tokens(tokens: int) -> str:
    """
    Format token count with K suffix for thousands

    Args:
        tokens: Number of tokens

    Returns:
        Formatted string like "12.5K" or "500"
    """
    if tokens >= 1000:
        return f"{tokens / 1000:.1f}K"
    return str(tokens)

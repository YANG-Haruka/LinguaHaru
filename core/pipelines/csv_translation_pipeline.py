# pipeline/csv_translation_pipeline.py
# CSV translation: text cells go through the standard pipeline; numbers,
# codes and other non-translatable cells pass through untouched. The
# delimiter is sniffed from the file and reused on output.
import csv
import io
import json
import os

from .skip_pipeline import should_translate
from .txt_translation_pipeline import read_file_with_encoding
from core.log_config import app_logger


def _sniff_delimiter(sample):
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def extract_csv_content_to_json(file_path, temp_dir):
    content, encoding = read_file_with_encoding(file_path)
    delimiter = _sniff_delimiter(content[:4096])

    # StringIO (not splitlines) so newlines embedded in quoted fields survive
    rows = list(csv.reader(io.StringIO(content, newline=""), delimiter=delimiter))

    content_data = []
    count = 0
    for row_index, row in enumerate(rows):
        for col_index, cell in enumerate(row):
            text = cell.strip()
            if not text or not should_translate(text):
                continue
            count += 1
            content_data.append({
                "count_src": count,
                "type": "text",
                "value": cell.replace("\n", "␊").replace("\r", "␍"),
                "row": row_index,
                "col": col_index,
            })

    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join(temp_dir, filename)
    os.makedirs(temp_folder, exist_ok=True)

    # Keep the parsed rows + dialect for write-back
    with open(os.path.join(temp_folder, "csv_layout.json"), "w", encoding="utf-8") as f:
        json.dump({"rows": rows, "delimiter": delimiter, "encoding": encoding}, f,
                  ensure_ascii=False)

    json_path = os.path.join(temp_folder, "src.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(content_data, f, ensure_ascii=False, indent=4)

    app_logger.info(f"CSV: extracted {count} translatable cells "
                    f"(delimiter {delimiter!r}, encoding {encoding})")
    return json_path


def write_translated_content_to_csv(file_path, original_json_path, translated_json_path,
                                    temp_dir, result_dir, src_lang=None, dst_lang=None):
    filename = os.path.splitext(os.path.basename(file_path))[0]
    temp_folder = os.path.join(temp_dir, filename)

    with open(os.path.join(temp_folder, "csv_layout.json"), encoding="utf-8") as f:
        layout = json.load(f)
    with open(original_json_path, encoding="utf-8") as f:
        original_data = json.load(f)
    with open(translated_json_path, encoding="utf-8") as f:
        translated_data = json.load(f)

    translations = {item["count_src"]: item["translated"] for item in translated_data}

    rows = layout["rows"]
    for item in original_data:
        translated = translations.get(item["count_src"])
        if translated:
            rows[item["row"]][item["col"]] = translated.replace("␊", "\n").replace("␍", "\r")

    os.makedirs(result_dir, exist_ok=True)
    lang_suffix = f"{src_lang}2{dst_lang}" if src_lang and dst_lang else "translated"
    extension = os.path.splitext(file_path)[1].lower() or ".csv"  # also serves .tsv
    result_path = os.path.join(result_dir, f"{filename}_{lang_suffix}{extension}")

    # Plain UTF-8 (no BOM): a BOM would be glued to the first header cell
    # (e.g. "﻿id"), breaking strict CSV consumers.
    with open(result_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=layout["delimiter"])
        writer.writerows(rows)

    app_logger.info(f"Translated CSV saved to: {result_path}")
    return result_path

# Corpus tests: CSV / TSV with quoting edge cases.
#
# Run from the repo root:
#   python tests/test_corpus_csv.py
import csv
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.corpus_common import T, check, fake_translate, run, work_dirs

WORK_DIR, TEMP_DIR, RESULT_DIR = work_dirs("csv")


def test_csv_quoted_delimiters():
    print("CSV: quoted fields containing the delimiter itself")
    from core.pipelines.csv_translation_pipeline import (
        extract_csv_content_to_json, write_translated_content_to_csv)

    src = os.path.join(WORK_DIR, "quoted.csv")
    with open(src, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=",")
        writer.writerow(["Product name", "Description"])
        writer.writerow(["Steel bracket", "Strong, durable, and rust proof"])
        writer.writerow(["Copper wire", 'Conducts "very" well, cheaply'])

    src_json = extract_csv_content_to_json(src, TEMP_DIR)
    dst_json = fake_translate(src_json)
    out = write_translated_content_to_csv(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                          src_lang="en", dst_lang="ja")
    with open(out, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f, delimiter=","))

    check("row/column structure intact", len(rows) == 3 and all(len(r) == 2 for r in rows),
          str(rows))
    check("comma-bearing field translated as ONE cell",
          rows[1][1] == T + "Strong, durable, and rust proof", str(rows[1]))
    check("field with embedded quotes round-trips",
          rows[2][1] == T + 'Conducts "very" well, cheaply', str(rows[2]))


def test_csv_quoted_newlines():
    print("CSV: quoted fields containing newlines")
    from core.pipelines.csv_translation_pipeline import (
        extract_csv_content_to_json, write_translated_content_to_csv)

    src = os.path.join(WORK_DIR, "newlines.csv")
    with open(src, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter=",")
        writer.writerow(["Note title", "Body"])
        writer.writerow(["Shipping note", "First line of the note\nSecond line of the note"])
        writer.writerow(["Final note", "Closing remark text"])

    src_json = extract_csv_content_to_json(src, TEMP_DIR)
    dst_json = fake_translate(src_json)
    out = write_translated_content_to_csv(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                          src_lang="en", dst_lang="ja")
    with open(out, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f, delimiter=","))

    check("newline-bearing row not split into extra rows", len(rows) == 3, str(rows))
    check("embedded newline restored inside the translated cell",
          rows[1][1] == T + "First line of the note\nSecond line of the note", repr(rows[1]))
    check("following row unaffected", rows[2][0] == T + "Final note", str(rows[2]))


def test_tsv_quoted_fields():
    print("TSV: quoted fields containing tabs and newlines")
    from core.pipelines.csv_translation_pipeline import (
        extract_csv_content_to_json, write_translated_content_to_csv)

    src = os.path.join(WORK_DIR, "quoted.tsv")
    with open(src, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["Item name", "Remark"])
        writer.writerow(["Wooden table", "Contains a\ttab and\na newline inside"])

    src_json = extract_csv_content_to_json(src, TEMP_DIR)
    dst_json = fake_translate(src_json)
    out = write_translated_content_to_csv(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                          src_lang="en", dst_lang="ja")
    check("output keeps .tsv extension", out.endswith(".tsv"), out)
    with open(out, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f, delimiter="\t"))

    check("tab-delimited structure intact", len(rows) == 2 and all(len(r) == 2 for r in rows),
          str(rows))
    check("embedded tab and newline survive in the translated cell",
          rows[1][1] == T + "Contains a\ttab and\na newline inside", repr(rows[1]))


def test_csv_no_bom():
    print("CSV: output has no UTF-8 BOM (first cell not corrupted)")
    from core.pipelines.csv_translation_pipeline import (
        extract_csv_content_to_json, write_translated_content_to_csv)

    src = os.path.join(WORK_DIR, "header.csv")
    with open(src, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Title", "Body"])
        w.writerow(["Hello", "World text"])

    src_json = extract_csv_content_to_json(src, TEMP_DIR)
    dst_json = fake_translate(src_json)
    out = write_translated_content_to_csv(src, src_json, dst_json, TEMP_DIR, RESULT_DIR,
                                          src_lang="en", dst_lang="ja")
    raw = open(out, "rb").read()
    check("no UTF-8 BOM at file start", not raw.startswith(b"\xef\xbb\xbf"), repr(raw[:6]))
    with open(out, encoding="utf-8", newline="") as f:
        rows = list(csv.reader(f))
    check("first cell has no BOM glued to it", rows[0][0] == T + "Title", str(rows[0]))


if __name__ == "__main__":
    run([test_csv_quoted_delimiters, test_csv_quoted_newlines, test_tsv_quoted_fields,
         test_csv_no_bom])

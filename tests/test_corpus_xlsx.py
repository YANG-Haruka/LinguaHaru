# Corpus tests: XLSX structures.
#   - multiple merged regions        - cross-sheet formula
#   - date/percent/currency cells untouched, number formats preserved
#   - cell comments                  - frozen panes
#   - CJK sheet names                - long multi-line cell
#
# Run from the repo root:
#   python tests/test_corpus_xlsx.py
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tests.corpus_common import T, check, fake_translate, run, work_dirs

WORK_DIR, TEMP_DIR, RESULT_DIR = work_dirs("xlsx")

LONG_NOTE = ("First line of an unusually long cell note that goes on for a while\n"
             "second line continues the explanation with more detail\n"
             "third line wraps everything up after a considerable amount of text")


def test_xlsx_structures():
    print("XLSX: merges, cross-sheet formula, typed cells, comments, panes, CJK sheets")
    import openpyxl
    from openpyxl.comments import Comment
    from pipeline.excel_translation_pipeline import (
        extract_excel_content_to_json, write_translated_content_to_excel)

    src = os.path.join(WORK_DIR, "structures.xlsx")
    wb = openpyxl.Workbook()

    # Sheet 1: "DATA2024" is a code-like name the skip filter leaves alone,
    # so the cross-sheet formula that references it stays valid after the
    # translated workbook is saved.
    ws1 = wb.active
    ws1.title = "DATA2024"
    ws1["A1"] = "Wide merged banner title"
    ws1.merge_cells("A1:D1")
    ws1["A3"] = "Tall merged side label"
    ws1.merge_cells("A3:B5")
    ws1["E1"] = "Second merged region"
    ws1.merge_cells("E1:E3")
    ws1["B7"] = 1999.5                                   # plain number
    ws1["B8"] = datetime.datetime(2024, 3, 15)           # date
    ws1["B8"].number_format = "yyyy-mm-dd"
    ws1["B9"] = 0.375                                    # percent
    ws1["B9"].number_format = "0.00%"
    ws1["B10"] = 1234.5                                  # currency
    ws1["B10"].number_format = '"$"#,##0.00'
    ws1["A12"] = LONG_NOTE                               # long multi-line cell
    ws1["C2"] = "Cell carrying a comment"
    ws1["C2"].comment = Comment("Reviewer remark stays as a comment", "Reviewer")
    ws1.freeze_panes = "B2"

    # Sheet 2: CJK sheet name (gets translated/renamed) + cross-sheet formula
    ws2 = wb.create_sheet("売上データ")
    ws2["A1"] = "Cross sheet total label"
    ws2["B1"] = "=DATA2024!B7*2"
    ws2.freeze_panes = "A2"

    wb.save(src)

    src_json = extract_excel_content_to_json(src, TEMP_DIR, use_xlwings=False)
    dst_json = fake_translate(src_json)
    out = write_translated_content_to_excel(src, src_json, dst_json, RESULT_DIR,
                                            src_lang="ja", dst_lang="en")

    wb2 = openpyxl.load_workbook(out)
    sheets = wb2.sheetnames
    s1 = wb2["DATA2024"]

    # --- sheet names ---
    check("code-like sheet name untouched", "DATA2024" in sheets, str(sheets))
    # Excel forbids []:*?/\ in sheet names, so the fake [T] marker is
    # sanitized to -T- by design; the rename itself is what matters.
    check("CJK sheet name translated (renamed, brackets sanitized)",
          "-T-売上データ" in sheets, str(sheets))
    s2 = wb2["-T-売上データ"] if "-T-売上データ" in sheets else wb2[sheets[1]]

    # --- text content ---
    check("merged banner translated", s1["A1"].value == T + "Wide merged banner title",
          repr(s1["A1"].value))
    check("all three merged regions intact",
          sorted(str(r) for r in s1.merged_cells.ranges) == ["A1:D1", "A3:B5", "E1:E3"],
          str(list(s1.merged_cells.ranges)))
    check("other merged texts translated",
          s1["A3"].value == T + "Tall merged side label"
          and s1["E1"].value == T + "Second merged region",
          f"{s1['A3'].value!r} / {s1['E1'].value!r}")
    check("long multi-line cell translated with newlines intact",
          s1["A12"].value == T + LONG_NOTE, repr(s1["A12"].value))
    check("second sheet label translated",
          s2["A1"].value == T + "Cross sheet total label", repr(s2["A1"].value))

    # --- typed cells untouched, formats preserved ---
    check("plain number untouched", s1["B7"].value == 1999.5, repr(s1["B7"].value))
    check("date cell untouched with format preserved",
          s1["B8"].value == datetime.datetime(2024, 3, 15)
          and s1["B8"].number_format == "yyyy-mm-dd",
          f"{s1['B8'].value!r} / {s1['B8'].number_format!r}")
    check("percent cell untouched with format preserved",
          s1["B9"].value == 0.375 and s1["B9"].number_format == "0.00%",
          f"{s1['B9'].value!r} / {s1['B9'].number_format!r}")
    check("currency cell untouched with format preserved",
          s1["B10"].value == 1234.5 and s1["B10"].number_format == '"$"#,##0.00',
          f"{s1['B10'].value!r} / {s1['B10'].number_format!r}")

    # --- cross-sheet formula ---
    # The referenced sheet name is non-translatable, so the formula is still
    # valid. (Known limitation: if a referenced sheet IS renamed by
    # translation, formulas are not rewritten to the new name.)
    check("cross-sheet formula preserved verbatim",
          s2["B1"].value == "=DATA2024!B7*2", repr(s2["B1"].value))

    # --- comments / panes ---
    # Known limitation: comment text is not extracted for translation; it
    # must simply survive the round trip untouched.
    check("comment survives untouched",
          s1["C2"].comment is not None
          and "Reviewer remark stays as a comment" in s1["C2"].comment.text,
          repr(s1["C2"].comment))
    check("comment-bearing cell text translated",
          s1["C2"].value == T + "Cell carrying a comment", repr(s1["C2"].value))
    check("frozen panes preserved on both sheets",
          s1.freeze_panes == "B2" and s2.freeze_panes == "A2",
          f"{s1.freeze_panes} / {s2.freeze_panes}")


if __name__ == "__main__":
    run([test_xlsx_structures])

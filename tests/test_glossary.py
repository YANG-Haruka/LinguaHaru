# Tests for glossary CRUD (create / import / delete) added so the Web and Qt apps
# can manage multiple glossaries, plus the guards that protect base files and the
# language-code header format the translator requires.
#
# Run from the repo root:
#   python tests/test_glossary.py
import os
import sys
import csv
import tempfile

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)
os.chdir(REPO_ROOT)

for stream in (sys.stdout, sys.stderr):
    if hasattr(stream, "reconfigure"):
        stream.reconfigure(encoding="utf-8", errors="replace")

from core import backend


def test_create_and_delete():
    name = "PytestGlossCRUD"
    if name in backend.get_glossary_files():
        backend.delete_glossary(name)
    backend.create_glossary(name)
    assert name in backend.get_glossary_files()
    header, rows = backend.load_glossary(name)
    # A new glossary must start with LANGUAGE-CODE columns, not source/target,
    # or load_glossary would never match a src/dst pair and silently drop it.
    assert header == backend._DEFAULT_GLOSSARY_HEADER, header
    assert rows == []
    backend.delete_glossary(name)
    assert name not in backend.get_glossary_files()


def test_guards():
    name = "PytestGlossGuard"
    backend.create_glossary(name)
    try:
        try:
            backend.create_glossary(name)
            assert False, "duplicate create should raise"
        except FileExistsError:
            pass
        for bad in ["", "a/b", "x:y", "../escape"]:
            try:
                backend.create_glossary(bad)
                assert False, f"bad name accepted: {bad!r}"
            except ValueError:
                pass
        try:
            backend.delete_glossary("Default")
            assert False, "Default must be protected"
        except ValueError:
            pass
        try:
            backend.delete_glossary("NoSuchGlossary___")
            assert False, "missing delete should raise"
        except FileNotFoundError:
            pass
    finally:
        backend.delete_glossary(name)


def test_import_roundtrip():
    name = "PytestGlossImport"
    src = os.path.join(tempfile.gettempdir(), "pytest_glossary_import.csv")
    with open(src, "w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([["en", "zh"], ["dog", "狗"], ["cat", "猫"]])
    backend.import_glossary(name, src)
    try:
        header, rows = backend.load_glossary(name)
        assert header == ["en", "zh"], header
        assert rows == [["dog", "狗"], ["cat", "猫"]], rows
        # import must refuse an existing name
        try:
            backend.import_glossary(name, src)
            assert False, "import over existing should raise"
        except FileExistsError:
            pass
    finally:
        backend.delete_glossary(name)
        os.remove(src)


def test_default_always_present():
    assert "Default" in backend.get_glossary_files()


if __name__ == "__main__":
    test_create_and_delete()
    test_guards()
    test_import_roundtrip()
    test_default_always_present()
    print("All glossary tests passed.")

from pathlib import Path

import pytest

from dmx_docs.chunking import split_text
from dmx_docs.config import load_config
from dmx_docs.extract import _word_available, extract_file, find_libreoffice, paginate_blocks


def test_split_text_is_contiguous_and_bounded():
    text = ("Une phrase assez longue pour le test. " * 200) + "\n\n" + ("x" * 5000)
    parts = split_text(text)
    assert "".join(parts) == text
    assert all(len(p) <= 2000 for p in parts)
    assert split_text("court") == ["court"]


def test_pdf_pages(corpus):
    r = extract_file(str(corpus / "Projets" / "P1234_Nestle" / "Spec_cellule.pdf"))
    assert r.status == "ok"
    assert r.n_pages == 3
    assert [p for p, _ in r.pages] == [1, 2, 3]
    assert "MN-114" in r.pages[1][1]
    assert "préhension" in r.pages[1][1]


def test_docx_headings_and_tables(corpus):
    r = extract_file(str(corpus / "Projets" / "P1234_Nestle" / "Rapport_MES.docx"))
    assert r.status == "ok"
    text = "\n".join(t for _, t in r.pages)
    assert "# Rapport de mise en service" in text
    assert "## Problèmes rencontrés" in text
    assert "Amortisseur | AMX-220" in text


def test_scan_and_corrupt(corpus, tmp_path):
    assert extract_file(str(corpus / "Scans" / "scan.pdf")).status == "no_text"
    from conftest import make_pdf
    make_pdf(str(tmp_path / "short.pdf"), ["Remplacer les ventouses toutes les 500 h."])
    assert extract_file(str(tmp_path / "short.pdf")).status == "ok"  # short, but real text
    r = extract_file(str(corpus / "Broken" / "corrupt.pdf"))
    assert r.status == "error" and r.error


@pytest.mark.skipif(find_libreoffice() is None, reason="LibreOffice not installed")
def test_doc_conversion(corpus):
    r = extract_file(str(corpus / "Old" / "ancien_rapport.doc"), {"doc_converter": "libreoffice"})
    assert r.status == "ok", r.error
    assert "AMX-220" in "\n".join(t for _, t in r.pages)


@pytest.mark.skipif(not _word_available(), reason="Microsoft Word + pywin32 not available")
def test_doc_conversion_with_word(corpus, tmp_path):
    import pythoncom
    import win32com.client

    doc_path = str(tmp_path / "ancien_rapport.doc")
    pythoncom.CoInitialize()
    word = win32com.client.DispatchEx("Word.Application")
    try:
        word.Visible = False
        doc = word.Documents.Open(str(corpus / "Projets" / "P1234_Nestle" / "Rapport_MES.docx"), False, True)
        doc.SaveAs2(doc_path, FileFormat=0)  # wdFormatDocument (Word 97-2003)
        doc.Close(False)
    finally:
        word.Quit()
        pythoncom.CoUninitialize()
    r = extract_file(doc_path, {"doc_converter": "word"})
    assert r.status == "ok", r.error
    assert "AMX-220" in "\n".join(t for _, t in r.pages)


def test_doc_without_converter(corpus, tmp_path):
    f = tmp_path / "x.doc"
    f.write_bytes(b"whatever")
    assert extract_file(str(f), {"doc_converter": "none"}).status == "skipped"


def test_paginate_uses_page_break_markers():
    blocks = [("a" * 10, False), ("b" * 10, True), ("c" * 10, False), ("d" * 10, True)]
    assert paginate_blocks(blocks) == ["a" * 10, "b" * 10 + "\n\n" + "c" * 10, "d" * 10]
    no_markers = [("x" * 2000, False)] * 3
    assert len(paginate_blocks(no_markers, page_chars=3000)) == 3


def test_example_config_parses():
    cfg = load_config(Path(__file__).parent.parent / "config.example.toml")
    assert cfg.roots == []
    assert cfg.extensions == [".pdf", ".docx", ".doc"]
    assert cfg.is_excluded("~$doc.docx", "Z:/~$doc.docx")
    assert not cfg.is_excluded("Rapport.docx", "Z:/Rapport.docx")

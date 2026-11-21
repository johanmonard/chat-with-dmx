import datetime as dt
from pathlib import Path

import openpyxl
import pytest

from dmx_docs import excel
from dmx_docs.extract import EXTRACT_VERSION, extract_file

FIXTURES = Path(__file__).parent / "fixtures"


def book(tmp_path, fill, name="b.xlsx"):
    wb = openpyxl.Workbook()
    fill(wb)
    p = tmp_path / name
    wb.save(p)
    return str(p)


def test_sheets_are_pages_in_workbook_order(tmp_path):
    def fill(wb):
        a = wb.active
        a.title = "Pièces"
        a.append(["Référence", "Désignation", "Qté"])
        a.append(["AMX-220", "Amortisseur", 4])
        a.append([])
        a.append(["VT-9", None, 2.5])
        h = wb.create_sheet("Caché")
        h.sheet_state = "hidden"
        h["B2"] = "seul"
        wb.create_sheet("Vide")
    r = extract_file(book(tmp_path, fill))
    assert (r.status, r.n_pages) == ("ok", 3)
    assert r.pages[0] == (1, "Feuille : Pièces\nRéférence | Désignation | Qté\nAMX-220 | Amortisseur | 4\nVT-9 | | 2.5")
    assert r.pages[1] == (2, "Feuille : Caché (masquée)\n| seul")
    assert dict(r.pages).get(3, "") == ""


def test_cell_text():
    assert [excel.cell_text(v) for v in (None, True, 4.0, 2.5, 1 / 3, dt.datetime(2024, 3, 15),
                                         dt.datetime(2024, 3, 15, 14, 5), dt.date(2024, 3, 15), "a\nb")] == \
        ["", "VRAI", "4", "2.5", "0.3333333333", "2024-03-15", "2024-03-15 14:05", "2024-03-15", "a b"]


def test_formula_gives_its_stored_value_or_nothing(tmp_path):
    def fill(wb):
        wb.active.append(["Total", "=1+1"])     # openpyxl stores no calculated value
    assert extract_file(book(tmp_path, fill)).pages[0][1] == "Feuille : Sheet\nTotal"


def test_row_and_column_limits(tmp_path):
    def fill(wb):
        ws = wb.active
        ws.append(["h1", "h2", "h3"])
        for i in range(12):
            ws.append([i, "x", "y"])
    r = excel.extract_excel(book(tmp_path, fill), max_rows=5, max_columns=2)
    text = r.pages[0][1]
    assert text.splitlines()[1] == "h1 | h2" and "h3" not in text
    assert text.endswith("[feuille coupée après 5 lignes]") and "\n4 | x" not in text


def test_header_is_repeated_in_long_sheets(tmp_path):
    def fill(wb):
        ws = wb.active
        ws.append(["Référence", "Désignation"])
        for i in range(400):
            ws.append([f"REF-{i:04d}", "Vis à tête cylindrique M8x25"])
    text = extract_file(book(tmp_path, fill)).pages[0][1]
    lines = text.splitlines()
    repeats = [i for i, line in enumerate(lines) if line == "[colonnes] Référence | Désignation"]
    assert len(repeats) >= 10
    gaps = [sum(len(x) + 1 for x in lines[a + 1:b]) for a, b in zip(repeats, repeats[1:])]
    assert max(gaps) < excel.HEADER_EVERY_CHARS + 100
    assert sum(1 for line in lines if line.startswith("REF-")) == 400     # no row lost


def test_workbook_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(excel, "MAX_WORKBOOK_CHARS", 500)
    def fill(wb):
        for n in range(3):
            ws = wb.active if n == 0 else wb.create_sheet(f"S{n}")
            for i in range(40):
                ws.append([f"ligne {i} de la feuille {n}"])
    r = extract_file(book(tmp_path, fill))
    assert r.n_pages == 3 and r.pages[0][1].endswith("[classeur coupé : feuilles suivantes non lues]")
    assert dict(r.pages).get(3, "") == ""


def test_error_values_and_merged_cells(tmp_path):              # Review Focus 2
    def fill(wb):
        ws = wb.active
        ws.append(["Titre fusionné", None, None])
        ws.merge_cells("A1:C1")
        ws.append(["#DIV/0!", "#N/A", 3])
    assert extract_file(book(tmp_path, fill)).pages[0][1] == "Feuille : Sheet\nTitre fusionné\n#DIV/0! | #N/A | 3"


def test_old_xls():
    r = extract_file(str(FIXTURES / "small.xls"))
    assert r.status == "ok" and r.n_pages == 2
    assert r.pages[0][1] == "Feuille : Pièces\nRéférence | Désignation | Qté\nAMX-220 | Amortisseur | 4\n2024-03-15"


def test_damaged_workbook_is_an_error(tmp_path):
    p = tmp_path / "bad.xlsx"
    p.write_bytes(b"not a workbook")
    r = extract_file(str(p))
    assert r.status == "error" and r.error


def test_extract_version_is_unchanged():
    assert EXTRACT_VERSION == 2


def test_excel_in_a_world(tmp_path, make_cfg):
    from dmx_docs.indexer import run_index
    from dmx_docs.tools import DocTools
    root = tmp_path / "root"
    root.mkdir()
    def fill(wb):
        wb.active.title = "Pompes"
        wb.active.append(["Pompe", "Débit"])
        wb.active.append(["VTN 41", "610 l/min"])
    path = book(root, fill, "Calcul.xlsx")
    cfg = make_cfg(root, extensions=[".xlsx"])
    run_index(cfg, progress=lambda *a, **k: None)
    tools = DocTools(cfg)
    out = tools.search("VTN", mode="keyword")
    assert "Calcul.xlsx — sheet 1/1" in out and "(Excel," in out
    assert "Feuille : Pompes" in tools.read_document(path)
    assert "Excel=1" in tools.index_status()
    with pytest.raises(ValueError, match="open_document"):
        tools.view_page(path)

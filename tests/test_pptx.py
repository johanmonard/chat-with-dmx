import sys

import pytest

from conftest import make_png, make_pptx
from dmx_docs.extract import extract_file
from dmx_docs.indexer import run_index
from dmx_docs.tools import DocTools

quiet = lambda *a, **k: None  # noqa: E731


def test_pptx_text_per_slide(tmp_path):
    deck = tmp_path / "Deck.pptx"
    make_pptx(deck)
    r = extract_file(str(deck))
    assert r.status == "ok" and r.n_pages == 3
    pages = dict(r.pages)
    assert pages[1].startswith("# Paloma 4R")
    assert "Pick and place robot for biscuits" in pages[1]
    assert "Notes: Mention the washdown version." in pages[1]
    assert "Cadence | 120 ppm" in pages[2] and "Robots | 4" in pages[2]
    assert "Hygienic design" in pages[2]           # text box inside a group
    assert pages[2].count("Technical data") == 1   # the title is not repeated
    assert 3 not in pages                          # pictures only: no text


def test_broken_pptx_is_an_error_not_a_crash(tmp_path):
    bad = tmp_path / "bad.pptx"
    bad.write_bytes(b"not a zip file")  # also what an encrypted (password) .pptx looks like to python-pptx
    r = extract_file(str(bad))
    assert r.status == "error" and r.error


@pytest.fixture
def deck_tools(tmp_path, make_cfg):
    root = tmp_path / "Marketing"
    (root / "Presentations").mkdir(parents=True)
    red, blue = tmp_path / "red.png", tmp_path / "blue.png"
    make_png(red, (255, 0, 0))
    make_png(blue, (0, 0, 255))
    make_pptx(root / "Presentations" / "Deck.pptx", pictures=(red, blue))
    cfg = make_cfg(root, extensions=[".pdf", ".pptx", ".ppt"], world="marketing", profile="marketing")
    run_index(cfg, progress=quiet)
    return DocTools(cfg), root / "Presentations" / "Deck.pptx"


def test_pptx_is_searched_and_read_by_slide(deck_tools):
    t, deck = deck_tools
    assert "Deck.pptx — slide 2/3 (PowerPoint" in t.search("Hygienic", mode="keyword")
    text = t.read_document(str(deck))
    assert "3 slides" in text and "--- slide 2 ---" in text and "[no text on this page]" in text


def test_pptx_table_keeps_repeated_values_and_merged_cells(tmp_path):
    from pptx import Presentation
    from pptx.util import Inches

    deck = tmp_path / "Table.pptx"
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # Blank
    table = slide.shapes.add_table(2, 3, Inches(1), Inches(1), Inches(6), Inches(2)).table
    table.cell(0, 0).merge(table.cell(0, 1))  # header: the first two cells are merged
    table.cell(0, 0).text = "A B"
    table.cell(0, 2).text = "C"
    for c, text in enumerate(["Robots", "4", "4"]):  # equal adjacent values are data, not repeats
        table.cell(1, c).text = text
    prs.save(str(deck))
    r = extract_file(str(deck))
    assert r.status == "ok"
    text = dict(r.pages)[1]
    assert "A B | C" in text.splitlines()
    assert "Robots | 4 | 4" in text.splitlines()
    assert "|  |" not in text  # no empty cell where the merged cell was


def test_pptx_table_keeps_columns_under_a_vertical_merge(tmp_path):
    from pptx import Presentation
    from pptx.util import Inches

    deck = tmp_path / "Vertical.pptx"
    prs = Presentation()
    slide = prs.slides.add_slide(prs.slide_layouts[6])  # Blank
    table = slide.shapes.add_table(3, 2, Inches(1), Inches(1), Inches(6), Inches(3)).table
    table.cell(0, 0).merge(table.cell(1, 0))  # "Group" spans the first two rows of the first column
    table.cell(0, 0).text = "Group"
    table.cell(0, 1).text = "Speed"
    table.cell(1, 1).text = "Max"
    table.cell(2, 0).text = "Motor"
    table.cell(2, 1).text = "3 kW"
    prs.save(str(deck))
    r = extract_file(str(deck))
    assert r.status == "ok"
    lines = dict(r.pages)[1].splitlines()
    assert "Group | Speed" in lines
    assert "| Max" in lines  # Max stays in the second column: the covered cell leaves an empty slot
    assert "Motor | 3 kW" in lines


def test_ppt_without_converter_is_skipped(tmp_path, monkeypatch):
    from dmx_docs import extract
    monkeypatch.setattr(extract, "find_libreoffice", lambda configured=None: None)
    monkeypatch.setattr(extract, "_powerpoint_installed", lambda: False)
    old = tmp_path / "old.ppt"
    old.write_bytes(b"old powerpoint")
    r = extract.extract_file(str(old))
    assert r.status == "skipped" and "no .ppt converter" in r.error


def test_ppt_converter_choice(tmp_path, monkeypatch):
    from dmx_docs import extract
    calls = []
    monkeypatch.setattr(extract, "find_libreoffice", lambda configured=None: "soffice.exe")
    monkeypatch.setattr(extract, "_convert_with_libreoffice",
                        lambda soffice, path, out_dir, target="docx": calls.append(("lo", target)) or "x.pptx")
    monkeypatch.setattr(extract, "_powerpoint_installed", lambda: True)
    monkeypatch.setattr(extract, "_convert_with_powerpoint", lambda path, out_dir: calls.append(("ppt",)) or "y.pptx")
    assert extract.convert_ppt("a.ppt", str(tmp_path)) == "x.pptx"                    # auto: LibreOffice first
    assert extract.convert_ppt("a.ppt", str(tmp_path), converter="word") == "y.pptx"  # Microsoft Office
    assert calls == [("lo", "pptx"), ("ppt",)]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows semaphores")
def test_stuck_powerpoint_times_out_and_frees_the_slot(tmp_path, monkeypatch):
    import threading

    from dmx_docs import extract
    released = threading.Event()

    class FakePresentations:
        def Open(self, *args):
            released.wait(30)  # a hidden dialog: Open returns only when PowerPoint is killed
            raise RuntimeError("The RPC server is unavailable.")

    class FakeApp:
        Presentations = FakePresentations()

    monkeypatch.setattr(extract, "POWERPOINT_TIMEOUT_S", 1)
    monkeypatch.setattr(extract, "_powerpoint", lambda: FakeApp())
    monkeypatch.setattr(extract, "kill_office_automation", lambda *names: released.set())
    with pytest.raises(TimeoutError, match="within 1 s"):
        extract._convert_with_powerpoint(str(tmp_path / "stuck.ppt"), str(tmp_path))
    released.clear()
    with pytest.raises(TimeoutError, match="within 1 s"):  # not "blocked": the slot was released
        extract._convert_with_powerpoint(str(tmp_path / "stuck2.ppt"), str(tmp_path))


def _has_powerpoint():
    from dmx_docs.extract import _powerpoint_installed
    return _powerpoint_installed()


@pytest.mark.skipif(not _has_powerpoint(), reason="Microsoft PowerPoint + pywin32 not available")
def test_ppt_conversion_with_powerpoint(tmp_path):
    import pythoncom
    import win32com.client

    from dmx_docs import extract
    deck = tmp_path / "Deck.pptx"
    make_pptx(deck)
    old = tmp_path / "Deck.ppt"
    pythoncom.CoInitialize()
    app = win32com.client.DispatchEx("PowerPoint.Application")
    pres = app.Presentations.Open(str(deck), True, False, False)
    pres.SaveAs(str(old), 1)  # ppSaveAsPresentation (PowerPoint 97-2003)
    pres.Close()
    try:
        r = extract.extract_file(str(old), {"doc_converter": "word"})
        assert r.status == "ok", r.error
        assert "Hygienic design" in dict(r.pages)[2]
    finally:
        extract.kill_office_automation("POWERPNT.EXE")


def _fake_powerpoint(already_open, fail_save=False):
    """A PowerPoint that belongs to the user: alerts on, macros allowed, the .ppt maybe open."""
    import os
    log = []

    class FakePresentation:
        def SaveCopyAs(self, path, file_format):
            log.append(("SaveCopyAs", os.path.basename(path), file_format, app.DisplayAlerts, app.AutomationSecurity))
            if fail_save:
                raise RuntimeError("disk full")
            with open(path, "wb") as f:
                f.write(b"pptx")

        def SaveAs(self, path, file_format):
            log.append(("SaveAs",))  # would rename the user's presentation

        def Close(self):
            log.append(("Close",))

    class FakePresentations:
        Count = 1 if already_open else 0

        def Open(self, *args):
            log.append(("Open",) + args[1:])
            if not already_open:  # an open file is returned as it is: the collection does not grow
                self.Count += 1
            return FakePresentation()

    class FakeApp:
        DisplayAlerts = 2        # ppAlertsAll
        AutomationSecurity = 1   # msoAutomationSecurityLow
        Presentations = FakePresentations()

    app = FakeApp()
    return app, log


@pytest.mark.skipif(sys.platform != "win32", reason="Windows semaphores")
def test_powerpoint_conversion_leaves_a_presentation_the_user_has_open(tmp_path, monkeypatch):
    from dmx_docs import extract
    app, log = _fake_powerpoint(already_open=True)
    monkeypatch.setattr(extract, "_powerpoint", lambda: app)
    out = extract._convert_with_powerpoint(str(tmp_path / "Deck.ppt"), str(tmp_path))
    assert out == str(tmp_path / "converted.pptx")
    assert [entry[0] for entry in log] == ["Open", "SaveCopyAs"]  # not SaveAs, and not closed
    assert log[0][1:] == (True, False, False)                     # ReadOnly, not untitled, no window
    assert log[1] == ("SaveCopyAs", "converted.pptx", 24, 1, 3)   # quiet during the conversion
    assert (app.DisplayAlerts, app.AutomationSecurity) == (2, 1)  # and back as the user had them


@pytest.mark.skipif(sys.platform != "win32", reason="Windows semaphores")
def test_powerpoint_conversion_closes_the_presentation_it_opened(tmp_path, monkeypatch):
    from dmx_docs import extract
    app, log = _fake_powerpoint(already_open=False)
    monkeypatch.setattr(extract, "_powerpoint", lambda: app)
    extract._convert_with_powerpoint(str(tmp_path / "Deck.ppt"), str(tmp_path))
    assert [entry[0] for entry in log] == ["Open", "SaveCopyAs", "Close"]
    assert (app.DisplayAlerts, app.AutomationSecurity) == (2, 1)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows semaphores")
def test_powerpoint_settings_come_back_when_the_conversion_fails(tmp_path, monkeypatch):
    from dmx_docs import extract
    app, log = _fake_powerpoint(already_open=False, fail_save=True)
    monkeypatch.setattr(extract, "_powerpoint", lambda: app)
    with pytest.raises(RuntimeError, match="disk full"):
        extract._convert_with_powerpoint(str(tmp_path / "Deck.ppt"), str(tmp_path))
    assert [entry[0] for entry in log] == ["Open", "SaveCopyAs", "Close"]
    assert (app.DisplayAlerts, app.AutomationSecurity) == (2, 1)

from dmx_docs import onenote

NS = 'xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote"'
HIER = f"""<one:Notebook {NS}><one:Section name="Pompes" ID="s1">
<one:Page ID="p1" name="A savoir" dateTime="2022-07-12T13:50:28.000Z" lastModifiedTime="2022-10-14T11:40:50.000Z" pageLevel="1"/>
<one:Page ID="p2" name="Détails" dateTime="2022-07-13T08:00:00.000Z" lastModifiedTime="2022-07-13T09:00:00.000Z" pageLevel="2"/>
<one:Page ID="p3" name="Vide" dateTime="2023-01-01T08:00:00.000Z" lastModifiedTime="2023-01-01T08:00:00.000Z" pageLevel="1"/>
</one:Section><one:Section name="Autre" ID="s2"/></one:Notebook>"""
PAGE = f"""<one:Page {NS} ID="p1" name="A savoir">
<one:Title><one:OE><one:T><![CDATA[A savoir]]></one:T></one:OE></one:Title>
<one:Outline><one:OEChildren>
<one:OE><one:T><![CDATA[Le débit d'air <span style='font-weight:bold'>s'additionne</span> &amp; les fuites aussi]]></one:T></one:OE>
<one:OE><one:Table><one:Row><one:Cell><one:OEChildren><one:OE><one:T><![CDATA[Pompe]]></one:T></one:OE></one:OEChildren></one:Cell>
<one:Cell><one:OEChildren><one:OE><one:T><![CDATA[610 l/min]]></one:T></one:OE></one:OEChildren></one:Cell></one:Row></one:Table></one:OE>
<one:OE><one:Image><one:OCRData><one:OCRText><![CDATA[Pilotage interne ou externe ?]]></one:OCRText></one:OCRData></one:Image></one:OE>
<one:OE><one:InsertedFile preferredName="Courbe pompe.pdf"/></one:OE>
</one:OEChildren></one:Outline></one:Page>"""


def test_section_pages_and_titles():
    pages = onenote.section_pages(HIER, "Pompes")
    assert [(p.id, p.name, p.level) for p in pages] == [("p1", "A savoir", 1), ("p2", "Détails", 2), ("p3", "Vide", 1)]
    assert onenote.page_titles(pages) == ["A savoir", "A savoir > Détails", "Vide"]
    assert onenote.section_pages(HIER, "Autre") == []


def test_page_text():
    assert onenote.page_text(PAGE, "A savoir", "2022-07-12T13:50:28.000Z", "2022-10-14T11:40:50.000Z") == (
        "A savoir\nCréée le 2022-07-12, modifiée le 2022-10-14\n"
        "Le débit d'air s'additionne & les fuites aussi\nPompe | 610 l/min\n"
        "Fichiers joints : Courbe pompe.pdf\n[Texte des images]\nPilotage interne ou externe ?")


def test_page_with_pictures_only_keeps_title_and_dates():     # Review Focus 3
    xml = f'<one:Page {NS} ID="p3" name="Vide"><one:Outline><one:OE><one:Image/></one:OE></one:Outline></one:Page>'
    assert onenote.page_text(xml, "Vide", "2023-01-01T08:00:00.000Z", "2023-01-01T08:00:00.000Z") == \
        "Vide\nCréée le 2023-01-01, modifiée le 2023-01-01"


def _cell(inner):
    return f"<one:Cell><one:OEChildren><one:OE>{inner}</one:OE></one:OEChildren></one:Cell>"


def _t(x):
    return f"<one:T><![CDATA[{x}]]></one:T>"


def test_nested_table_text_appears_once():
    inner = f"<one:Table><one:Row>{_cell(_t('In1'))}{_cell(_t('In2'))}</one:Row><one:Row>{_cell(_t('In3'))}</one:Row></one:Table>"
    xml = (f'<one:Page {NS}><one:Outline><one:OE><one:Table><one:Row>{_cell(_t("Out"))}{_cell(inner)}</one:Row>'
           '</one:Table></one:OE></one:Outline></one:Page>')
    text = onenote.page_text(xml, "T", "2023-01-01T00:00:00Z", "2023-01-01T00:00:00Z")
    assert text.split("\n")[2:] == ["Out | In1 | In2 / In3"]
    for s in ("In1", "In2", "In3", "Out"):
        assert text.count(s) == 1


def test_picture_and_file_inside_table_cell():
    pic = "<one:Image><one:OCRData><one:OCRText><![CDATA[Texte de capture]]></one:OCRText></one:OCRData></one:Image>"
    f = '<one:InsertedFile preferredName="Notice.docx"/>'
    xml = (f'<one:Page {NS}><one:Outline><one:OE><one:Table><one:Row>{_cell(pic)}{_cell(f + _t("Légende"))}</one:Row>'
           '</one:Table></one:OE></one:Outline></one:Page>')
    text = onenote.page_text(xml, "T", "2023-01-01T00:00:00Z", "2023-01-01T00:00:00Z")
    assert text.endswith("Fichiers joints : Notice.docx\n[Texte des images]\nTexte de capture")
    assert "Légende" in text


def test_level_three_title_and_malformed_xml():
    def ref(i, lvl):
        return onenote.PageRef(i, i.upper(), lvl, "", "")
    pages = [ref("a", 1), ref("b", 2), ref("c", 3), ref("d", 1)]
    assert onenote.page_titles(pages) == ["A", "A > B", "A > B > C", "D"]
    import pytest
    with pytest.raises(Exception):
        onenote.page_text("<one:Page", "T", "", "")


import json
import os
import sys
from pathlib import Path

import pytest

from dmx_docs.extract import Extracted, extract_file


class FakeOneNote:
    """Lists no page for the first `late` polls, then the pages of HIER."""
    def __init__(self, late=1, fail_on_page=False):
        self.opened, self.closed, self.polls, self.late, self.fail_on_page = [], [], 0, late, fail_on_page
    def OpenHierarchy(self, path, parent, obj_id, create):
        self.opened.append(path)
        self.files = sorted(os.listdir(path))
        return "nb1"
    def GetHierarchy(self, node, scope, xml):
        self.polls += 1
        return HIER if self.polls > self.late else HIER.replace("<one:Page", "<one:Skip")
    def GetPageContent(self, page_id, xml, info):
        if self.fail_on_page:
            raise RuntimeError("boom")
        return PAGE
    def CloseNotebook(self, node):
        self.closed.append(node)


def section_file(tmp_path):
    src = tmp_path / "share" / "Pompes.one"
    src.parent.mkdir()
    src.write_bytes(b"section bytes")
    return src


def test_read_section_opens_a_copy_and_cleans_up(tmp_path):
    src, tmp_root, app = section_file(tmp_path), tmp_path / "onenote-tmp", FakeOneNote()
    before = src.stat().st_mtime
    r = onenote.read_section(str(src), str(tmp_root), app=app, poll_s=0)
    assert (r.status, r.n_pages, r.title) == ("ok", 3, "Pompes")
    assert r.pages[1][1].startswith("A savoir > Détails\nCréée le 2022-07-13")
    assert len(app.opened) == 1 and Path(app.opened[0]).parent == tmp_root
    assert str(src.parent) not in app.opened[0] and app.files == ["Pompes.one"]
    assert app.closed == ["nb1"] and list(tmp_root.iterdir()) == []
    assert src.stat().st_mtime == before and src.read_bytes() == b"section bytes"


def test_read_section_without_pages_is_empty(tmp_path):         # Review Focus 3
    app = FakeOneNote(late=10 ** 9)
    r = onenote.read_section(str(section_file(tmp_path)), str(tmp_path / "t"), app=app, poll_s=0, load_timeout_s=0.05)
    assert r.status == "empty" and app.closed == ["nb1"]


def test_read_section_closes_and_cleans_after_an_error(tmp_path):
    app, tmp_root = FakeOneNote(fail_on_page=True), tmp_path / "t"
    r = onenote.read_section(str(section_file(tmp_path)), str(tmp_root), app=app, poll_s=0)
    assert r.status == "error" and "boom" in r.error
    assert app.closed == ["nb1"] and list(tmp_root.iterdir()) == []


def test_section_limit():
    assert onenote.section_limit_s(0) == 300
    assert onenote.section_limit_s(200 * 1024 * 1024) == 540


@pytest.fixture
def pass_env(tmp_path, make_cfg, monkeypatch):
    killed, saved = [], []
    monkeypatch.setattr(onenote, "installed", lambda: True)
    monkeypatch.setattr(onenote, "running", lambda: False)
    monkeypatch.setattr(onenote, "kill_office_automation", lambda *names: killed.append(names))
    monkeypatch.setattr(onenote, "_close_left_notebooks", lambda tmp_root, app=None: 0)  # no real OneNote
    cfg = make_cfg(tmp_path / "root", world="engineering", profile="engineering")
    items = [(f"\\\\srv\\share\\S{i}.one", 10, 1.0, False) for i in range(8)]
    save = lambda path, size, mtime, result, existed: saved.append((path, result.status, result.error))
    return cfg, items, save, saved, killed


def run(cfg, items, save, read):
    onenote.run_pass(cfg, items, save, lambda *a: None, lambda: None, read=read)


def test_pass_skips_everything_without_onenote(pass_env, monkeypatch):
    cfg, items, save, saved, killed = pass_env
    monkeypatch.setattr(onenote, "installed", lambda: False)
    run(cfg, items, save, read=lambda *a: pytest.fail("must not read"))
    assert [s[1:] for s in saved] == [("skipped", onenote.NOT_INSTALLED)] * 8 and killed == []


def test_pass_saves_each_section_and_ends_the_onenote_it_started(pass_env):
    cfg, items, save, saved, killed = pass_env
    run(cfg, items, save, read=lambda path, tmp_root, limit: Extracted(status="ok", pages=[(1, "x")], n_pages=1))
    assert [s[1] for s in saved] == ["ok"] * 8 and killed == [("ONENOTE.EXE",)]


def test_pass_goes_on_after_a_timeout_and_stops_after_five_in_a_row(pass_env):
    cfg, items, save, saved, killed = pass_env
    def read(path, tmp_root, limit):
        if path.endswith("S0.one"):
            return Extracted(status="ok", pages=[(1, "x")], n_pages=1)
        raise TimeoutError()
    run(cfg, items, save, read)
    assert [s[1] for s in saved] == ["ok"] + ["error"] * 5      # S6 and S7 are left for the next run
    assert all(s[2].startswith("timeout") for s in saved[1:])


def test_pass_never_ends_a_onenote_that_was_already_open(pass_env, monkeypatch):   # Review Focus 4
    cfg, items, save, saved, killed = pass_env
    monkeypatch.setattr(onenote, "running", lambda: True)
    def read(path, tmp_root, limit):
        raise TimeoutError()
    run(cfg, items, save, read)
    assert killed == [] and len(saved) == 1 and saved[0][1] == "error"


def test_pass_removes_leftover_temporary_folders(pass_env):
    cfg, items, save, saved, killed = pass_env
    left = cfg.world_dir / "onenote-tmp" / "dead"
    left.mkdir(parents=True)
    run(cfg, items[:1], save, read=lambda *a: Extracted(status="empty"))
    assert not left.exists()


def test_extract_file_never_reads_a_section(tmp_path):
    r = extract_file(str(section_file(tmp_path)))
    assert (r.status, r.error) == ("skipped", onenote.SERVER_ONLY)


def test_index_run_reads_sections_in_the_pass_and_the_server_serves_the_index(tmp_path, make_cfg, monkeypatch):
    from dmx_docs.indexer import run_index
    from dmx_docs.tools import DocTools
    src = section_file(tmp_path)
    monkeypatch.setattr(onenote, "installed", lambda: True)
    monkeypatch.setattr(onenote, "running", lambda: True)
    monkeypatch.setattr(onenote, "_read_in_process", lambda path, tmp_root, limit: onenote.read_section(
        path, tmp_root, app=FakeOneNote(late=0), poll_s=0))
    cfg = make_cfg(src.parent, extensions=[".one"])
    run_index(cfg, progress=lambda *a, **k: None)
    tools = DocTools(cfg)
    out = tools.search("fuites", mode="keyword")
    assert "Pompes.one — page 1/3" in out and "(OneNote," in out
    src.write_bytes(b"changed by OneNote after indexing")
    monkeypatch.setattr("dmx_docs.tools.extract_file", lambda *a, **k: pytest.fail("no live read of a section"))
    text = tools.read_document(str(src))
    assert "Le débit d'air s'additionne" in text and "changed since it was indexed on" in text
    with pytest.raises(ValueError, match="open_document"):
        tools.view_page(str(src))


@pytest.mark.skipif(not os.environ.get("DMX_TEST_ONE_FILE"), reason="needs OneNote and a real .one file")
def test_real_section(tmp_path):
    src = Path(os.environ["DMX_TEST_ONE_FILE"])
    before = (src.stat().st_mtime, src.stat().st_size)
    r = onenote.read_section(str(src), str(tmp_path / "t"))
    assert r.status in ("ok", "empty") and (src.stat().st_mtime, src.stat().st_size) == before


def test_pass_closes_the_notebook_of_a_reading_that_timed_out(pass_env, monkeypatch):
    cfg, items, save, saved, killed = pass_env
    closed = []
    monkeypatch.setattr(onenote, "running", lambda: True)
    monkeypatch.setattr(onenote, "_close_left_notebooks", lambda tmp_root, app=None: closed.append(tmp_root))
    def read(path, tmp_root, limit):
        raise TimeoutError()
    run(cfg, items, save, read)
    assert closed == [str(cfg.world_dir / "onenote-tmp")] and killed == []


class FakeNotebooks:
    def __init__(self, listing):
        self.listing, self.closed = listing, []
    def GetHierarchy(self, node, scope, xml):
        assert (node, scope) == ("", 2)
        return self.listing
    def CloseNotebook(self, node):
        self.closed.append(node)


def test_close_left_notebooks_closes_only_the_temporary_ones(tmp_path):
    tmp_root = tmp_path / "onenote-tmp"
    listing = (f'<one:Notebooks {NS}>'
               f'<one:Notebook name="Mine" ID="user" path="{tmp_path / "Documents" / "Mine"}"/>'
               f'<one:Notebook name="x" ID="left" path="{tmp_root / "0123abcd"}"/>'
               f'<one:Notebook name="y" ID="near" path="{tmp_path / "onenote-tmp2" / "z"}"/>'
               '</one:Notebooks>')
    app = FakeNotebooks(listing)
    assert onenote._close_left_notebooks(str(tmp_root), app=app) == 1 and app.closed == ["left"]


# Speaks the PowerShell session's protocol: base64 JSON requests on stdin, results in files, ok/err.
FAKE_SESSION = '''
import base64, json, sys, time
print("#< CLIXML noise", flush=True)
for line in sys.stdin:
    if not line.strip():
        break
    r = json.loads(base64.b64decode(line).decode("utf-8"))
    if r["op"] == "GetHierarchy":
        with open(r["out"], "w", encoding="utf-8") as f:
            f.write(json.dumps([r["a"], r["b"], "é"]))
        print("ok", flush=True)
    elif r["op"] == "GetPageContent":
        time.sleep(30)
    elif r["op"] == "CloseNotebook":
        sys.exit("PowerShell broke")
    else:
        with open(r["err"], "w", encoding="utf-8") as f:
            f.write("Exception from HRESULT: 0x8004201B")
        print("err", flush=True)
'''


@pytest.fixture
def fake_session(tmp_path, monkeypatch):
    script = tmp_path / "bin" / "fake_session.py"
    script.parent.mkdir()
    script.write_text(FAKE_SESSION, encoding="utf-8")
    monkeypatch.setattr(onenote.PowerShellOneNote, "command", [sys.executable, str(script)])
    work = tmp_path / "work"
    work.mkdir()
    return work


def test_powershell_session_passes_arguments_as_data(fake_session):
    tricky = str(fake_session / "a'; Remove-Item x; '$(b) é")
    app = onenote.PowerShellOneNote(str(fake_session))
    try:
        assert json.loads(app.GetHierarchy(tricky, 4, "")) == [tricky, "4", "é"]
        assert json.loads(app.GetHierarchy("", 2, "")) == ["", "2", "é"]     # same process, still alive
        with pytest.raises(RuntimeError, match="0x8004201B"):
            app.OpenHierarchy("x", "", "", 0)
    finally:
        app.close()
    assert not any(tricky in c for c in onenote.PowerShellOneNote.command)
    assert os.listdir(fake_session) == []                                    # result files are removed
    assert onenote._protected(RuntimeError("OneNote OpenHierarchy: Exception from HRESULT: 0x8004201B"))


def test_powershell_session_timeout_and_end(fake_session):
    app = onenote.PowerShellOneNote(str(fake_session), timeout_s=1)
    with pytest.raises(TimeoutError):
        app.GetPageContent("p1", "", 0)
    assert app._proc is None                                                 # the stuck process is ended
    with pytest.raises(RuntimeError, match="PowerShell ended"):
        app.CloseNotebook("nb1")
    app.close()


def test_a_section_the_pass_does_not_reach_stays_in_the_index(tmp_path, make_cfg, monkeypatch):   # R8
    from dmx_docs.indexer import run_index
    from dmx_docs.tools import DocTools
    first = section_file(tmp_path)
    second = first.parent / "copie" / "Pompes.one"               # same section name: FakeOneNote lists it
    second.parent.mkdir()
    second.write_bytes(b"other section")
    monkeypatch.setattr(onenote, "installed", lambda: True)
    monkeypatch.setattr(onenote, "running", lambda: True)
    monkeypatch.setattr(onenote, "_close_left_notebooks", lambda tmp_root, app=None: 0)
    monkeypatch.setattr(onenote, "_read_in_process", lambda path, tmp_root, limit: onenote.read_section(
        path, tmp_root, app=FakeOneNote(late=0), poll_s=0))
    cfg = make_cfg(first.parent, extensions=[".one"])
    run_index(cfg, progress=lambda *a, **k: None)
    first.write_bytes(b"changed 1")
    second.write_bytes(b"changed 2")
    reached = []
    def stuck(path, tmp_root, limit):
        reached.append(path)
        raise TimeoutError()
    monkeypatch.setattr(onenote, "_read_in_process", stuck)
    run_index(cfg, progress=lambda *a, **k: None)          # the user's OneNote is open: the pass stops
    assert len(reached) == 1
    not_reached = second if reached[0] == str(first) else first
    text = DocTools(cfg).read_document(str(not_reached))
    assert "Le débit d'air s'additionne" in text and "changed since it was indexed on" in text

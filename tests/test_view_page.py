import pytest

from conftest import make_pdf
from dmx_docs.extract import pymupdf
from dmx_docs.indexer import run_index
from dmx_docs.tools import DocTools

quiet = lambda *a, **k: None  # noqa: E731


@pytest.fixture
def tools(tmp_path, make_cfg):
    root = tmp_path / "RMA"
    (root / "THOR").mkdir(parents=True)
    make_pdf(str(root / "THOR" / "Schema.pdf"), ["Page un : préhenseur.", "Page deux : convoyeur."])
    # A Word file with two embedded pictures (red, then blue).
    from docx import Document
    d = Document()
    d.add_paragraph("Rapport FAT avec photos.")
    for i, color in enumerate(((1, 0, 0), (0, 0, 1))):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 20), False)
        pix.set_rect(pix.irect, tuple(int(c * 255) for c in color))
        png = tmp_path / f"img{i}.png"
        pix.save(str(png))
        d.add_picture(str(png))
    d.save(str(root / "THOR" / "Rapport FAT.docx"))
    (root / "THOR" / "ancien.doc").write_bytes(b"old word")
    cfg = make_cfg(root)
    run_index(cfg, progress=quiet)
    return DocTools(cfg), root


def test_pdf_page_and_region(tools):
    t, root = tools
    caption, data, fmt = t.view_page(str(root / "THOR" / "Schema.pdf"), page=2)
    assert fmt == "png" and data[:4] == b"\x89PNG"
    assert "page 2/2, whole page" in caption
    w = pymupdf.Pixmap(data).width
    caption, data, _ = t.view_page(str(root / "THOR" / "Schema.pdf"), page=1, region="0,0,0.5,0.5")
    assert "region 0,0,0.5,0.5" in caption and pymupdf.Pixmap(data).width <= w
    with pytest.raises(ValueError, match="between 1 and 2"):
        t.view_page(str(root / "THOR" / "Schema.pdf"), page=3)
    with pytest.raises(ValueError, match="region must be"):
        t.view_page(str(root / "THOR" / "Schema.pdf"), region="top")


def test_docx_pictures_in_order(tools):
    t, root = tools
    for k, rgb in ((1, (255, 0, 0)), (2, (0, 0, 255))):
        caption, data, _ = t.view_page(str(root / "THOR" / "Rapport FAT.docx"), image=k)
        assert f"picture {k} of 2" in caption
        assert tuple(pymupdf.Pixmap(data).pixel(5, 5)) == rgb


def test_works_when_roots_come_from_the_database(tools, make_cfg):
    # Like the MCP server: config.toml has no roots, they are stored by the configuration page.
    t, root = tools
    fresh = DocTools(make_cfg(root, roots=[]))
    fresh.cfg.data_dir = t.cfg.data_dir
    caption, data, _ = fresh.view_page(str(root / "THOR" / "Schema.pdf"))
    assert data[:4] == b"\x89PNG"


def test_export_image_files(tools, tmp_path):
    t, root = tools
    t.cfg.export_dir = str(tmp_path / "exports")
    out = t.export_image(str(root / "THOR" / "Schema.pdf"), page=2, region="0,0,0.5,0.5")
    target = tmp_path / "exports" / "Schema_p2_zoom.png"
    assert str(target) in out and target.read_bytes()[:4] == b"\x89PNG"
    assert max(pymupdf.Pixmap(str(target)).width, pymupdf.Pixmap(str(target)).height) > 1568  # slide quality
    again = t.export_image(str(root / "THOR" / "Schema.pdf"), page=2, region="0,0,0.5,0.5")
    assert "Schema_p2_zoom_2.png" in again  # never overwrites
    out = t.export_image(str(root / "THOR" / "Rapport FAT.docx"), image=2, name="../../FAT photo bleue")
    target = tmp_path / "exports" / "FAT_photo_bleue.png"  # name sanitized, stays in the folder
    assert str(target) in out and tuple(pymupdf.Pixmap(str(target)).pixel(5, 5)) == (0, 0, 255)
    with pytest.raises(ValueError, match="only be exported"):
        t.export_image(str(root / "THOR" / "ancien.doc"))


@pytest.fixture
def launched(monkeypatch):
    from dmx_docs import tools as tools_mod
    calls = []
    monkeypatch.setattr(tools_mod, "_launch", lambda args=None, path=None, verb="open": calls.append((args, path, verb)))
    return calls, tools_mod


@pytest.mark.parametrize("viewer, expect", [
    (r"C:\Program Files\Adobe\Acrobat DC\Acrobat\Acrobat.exe", lambda a, p: a[1:3] == ["/A", "page=2"]),
    (r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
     lambda a, p: a[1].startswith("file:") and a[1].endswith("Schema.pdf#page=2")),
])
def test_open_pdf_at_page(tools, launched, monkeypatch, viewer, expect):
    t, root = tools
    calls, mod = launched
    monkeypatch.setattr(mod, "_default_app", lambda ext: viewer)
    out = t.open_document(str(root / "THOR" / "Schema.pdf"), page=2)
    assert "at page 2" in out and len(calls) == 1 and expect(calls[0][0], calls[0][1])


def test_open_word_read_only_and_unknown_viewer(tools, launched, monkeypatch):
    t, root = tools
    calls, mod = launched
    monkeypatch.setattr(mod, "_default_app", lambda ext: r"C:\Tools\SumatraPDF.exe")
    assert "go to page 2" in t.open_document(str(root / "THOR" / "Schema.pdf"), page=2)
    assert "read-only" in t.open_document(str(root / "THOR" / "Rapport FAT.docx"))
    assert calls[-1][2] == "OpenAsReadOnly"


def test_open_refuses_other_file_types(tools, launched, tmp_path):
    t, root = tools
    (root / "THOR" / "setup.exe").write_bytes(b"MZ")
    with pytest.raises(ValueError, match="Only .pdf"):
        t.open_document(str(root / "THOR" / "setup.exe"))
    with pytest.raises(ValueError, match="outside the indexed folders"):
        t.open_document(str(tmp_path / "elsewhere.pdf"))
    assert launched[0] == []


def test_refuses_doc_and_paths_outside_the_roots(tools, tmp_path):
    t, root = tools
    with pytest.raises(ValueError, match="cannot be shown"):
        t.view_page(str(root / "THOR" / "ancien.doc"))
    outside = tmp_path / "secret.pdf"
    make_pdf(str(outside), ["x"])
    with pytest.raises(ValueError, match="outside the indexed folders"):
        t.view_page(str(outside))

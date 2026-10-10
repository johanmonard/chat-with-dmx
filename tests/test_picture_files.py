import zipfile

from conftest import make_png
from dmx_docs import store
from dmx_docs.extract import docx_picture_names, extract_file, picture_title, pymupdf
from dmx_docs.indexer import run_index
from dmx_docs.tools import DocTools

quiet = lambda *a, **k: None  # noqa: E731
PICS = [".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff"]


def pictures_world(tmp_path, make_cfg):
    root = tmp_path / "Marketing"
    (root / "Pictures" / "Paloma").mkdir(parents=True)
    make_png(root / "Pictures" / "Paloma" / "Paloma_4R-tray loader.png", (10, 200, 30), size=(300, 200))
    (root / "Pictures" / "empty.jpg").write_bytes(b"")
    cfg = make_cfg(root, extensions=[".pdf"] + PICS, world="marketing", world_title="Marketing",
                   profile="marketing")
    run_index(cfg, progress=quiet)
    return cfg, root


def test_picture_title():
    assert picture_title(r"\\s\x\Paloma_4R-tray loader.png") == "Paloma 4R tray loader"
    assert picture_title("IMG.0042.JPG") == "IMG 0042"


def test_picture_file_is_a_one_page_document_without_text(tmp_path, make_cfg):
    cfg, root = pictures_world(tmp_path, make_cfg)
    r = extract_file(str(root / "Pictures" / "Paloma" / "Paloma_4R-tray loader.png"))
    assert (r.status, r.n_pages, r.pages, r.title) == ("no_text", 1, [], "Paloma 4R tray loader")
    con = store.connect(cfg.db_path)
    rows = {n: (s, p) for n, s, p in con.execute("SELECT name, status, n_pages FROM docs")}
    assert rows["Paloma_4R-tray loader.png"] == ("no_text", 1)
    assert rows["empty.jpg"][0] == "empty"
    assert con.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0


def test_picture_file_found_by_name_viewed_exported_and_read(tmp_path, make_cfg):
    cfg, root = pictures_world(tmp_path, make_cfg)
    t = DocTools(cfg)
    found = t.find_files("Paloma tray")
    assert "Paloma_4R-tray loader.png (Picture, 1 pages" in found and "not described yet" in found
    path = str(root / "Pictures" / "Paloma" / "Paloma_4R-tray loader.png")
    caption, data, fmt = t.view_page(path)
    assert caption.startswith(path + " — picture file, shown at")
    assert tuple(pymupdf.Pixmap(data).pixel(5, 5)) == (10, 200, 30)
    t.cfg.export_dir = str(tmp_path / "exports")
    out = t.export_image(path)
    target = tmp_path / "exports" / "Paloma_4R-tray_loader.png"
    assert str(target) in out and target.read_bytes() == (root / "Pictures" / "Paloma" / "Paloma_4R-tray loader.png").read_bytes()
    text = t.read_document(path)
    assert "(Picture, 1 pages" in text and "no description yet" in text and "no text layer" not in text


def test_docx_picture_names_numbering(tmp_path):
    z_path = tmp_path / "a.docx"
    with zipfile.ZipFile(z_path, "w") as z:
        for name in ("word/media/image10.png", "word/media/image2.jpeg", "word/media/image1.emf",
                     "word/media/image3.png", "word/document.xml"):
            z.writestr(name, b"x")
    with zipfile.ZipFile(z_path) as z:
        assert docx_picture_names(z) == ["word/media/image2.jpeg", "word/media/image3.png", "word/media/image10.png"]

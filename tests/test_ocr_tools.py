from conftest import make_pdf as make_text_pdf
from dmx_docs import ocr, store
from dmx_docs.indexer import run_index
from dmx_docs.tools import DocTools
from test_ocr import make_pdf

quiet = lambda *a, **k: None  # noqa: E731


def indexed(tmp_path, make_cfg):
    root = tmp_path / "Docs"
    root.mkdir()
    make_pdf(root / "scan.pdf", [("image", "x"), ("image", "y")])
    make_text_pdf(str(root / "plain.pdf"), ["Rapport ordinaire Gondolier sans scan, avec du vrai texte."])
    cfg = make_cfg(root)
    run_index(cfg, progress=quiet)
    con = store.connect(cfg.db_path)
    d = con.execute("SELECT id FROM docs WHERE name = 'scan.pdf'").fetchone()[0]
    con.isolation_level = None
    con.execute("BEGIN")
    ocr.save_page(con, d, 1, "text", "Bordereau Gondolier pour quarante colis")
    ocr.save_page(con, d, 2, "empty", "")
    con.execute("COMMIT")
    con.close()
    return cfg, root


def test_search_marks_ocr_hits(tmp_path, make_cfg):
    cfg, _ = indexed(tmp_path, make_cfg)
    lines = [l for l in DocTools(cfg).search("Gondolier", mode="keyword").splitlines() if l.startswith("[")]
    assert any("scan.pdf — page 1/2 (OCR)" in l for l in lines)
    assert any("plain.pdf" in l and "(OCR)" not in l for l in lines)


def test_read_document_says_which_pages_are_ocr(tmp_path, make_cfg):
    cfg, root = indexed(tmp_path, make_cfg)
    out = DocTools(cfg).read_document(str(root / "scan.pdf"))
    assert "recognized by OCR" in out and "page 1" in out.split("recognized by OCR")[0].splitlines()[-1]
    assert "no text layer" not in out          # the document is no longer 'no_text'
    assert "recognized by OCR" not in DocTools(cfg).read_document(str(root / "plain.pdf"))


def test_index_status_counts_ocr_pages(tmp_path, make_cfg):
    cfg, _ = indexed(tmp_path, make_cfg)
    assert "OCR: 1 pages with text, 1 without usable text (photos, drawings), 0 failed or timed out" \
        in DocTools(cfg).index_status()


def test_index_without_ocr_table_still_works(tmp_path, make_cfg):
    # An index copied from before OCR existed (Claude Desktop opens it read-only).
    cfg, root = indexed(tmp_path, make_cfg)
    con = store.connect(cfg.db_path)
    con.execute("DROP TABLE ocr_pages")
    con.commit()
    con.close()
    t = DocTools(cfg)
    assert "(OCR)" not in t.search("Gondolier", mode="keyword")
    assert "OCR: not run yet" in t.index_status()
    assert "scan.pdf" in t.read_document(str(root / "scan.pdf"))


def test_instructions_warn_about_ocr_text(tmp_path, make_cfg):
    from dmx_docs.server import GENERIC_INSTRUCTIONS, INSTRUCTIONS, MARKETING_INSTRUCTIONS
    for text in (INSTRUCTIONS, MARKETING_INSTRUCTIONS, GENERIC_INSTRUCTIONS):
        assert "(OCR)" in text and "view_page" in text

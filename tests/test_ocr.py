import os

import pytest

from dmx_docs import ocr, store
from dmx_docs.config import load_config
from dmx_docs.extract import pymupdf
from dmx_docs.indexer import run_index

quiet = lambda *a, **k: None  # noqa: E731


def _tessdata():
    for d in (os.environ.get("DMX_TESSDATA"), r"U:\DMX-RAG\tools\tessdata"):
        if d and os.path.isfile(os.path.join(d, "fra.traineddata")):
            return d
    return None


TESSDATA = _tessdata()
needs_tesseract = pytest.mark.skipif(TESSDATA is None, reason="no Tesseract language files (set DMX_TESSDATA)")


def make_pdf(path, pages):
    """pages: [("text", s) | ("image", s)]; an "image" page is the text rendered as a picture
    (no text layer), like a scan."""
    doc = pymupdf.open()
    for kind, text in pages:
        if kind == "text":
            page = doc.new_page()
            page.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text, fontsize=11)
            continue
        src = pymupdf.open()
        sp = src.new_page()
        sp.insert_textbox(pymupdf.Rect(50, 50, 550, 800), text, fontsize=16)
        pix = sp.get_pixmap(dpi=200)
        page = doc.new_page(width=sp.rect.width, height=sp.rect.height)
        page.insert_image(page.rect, pixmap=pix)
        src.close()
    doc.save(str(path))
    doc.close()


SCAN_1 = "Bordereau de livraison Gondolier pour quarante colis de biscuits"
SCAN_2 = "Rapport hebdomadaire Pelican sur l'inspection de la ligne"
MIXED_TEXT = "Texte normal du rapport Albatros avec assez de contenu pour une vraie page de texte."
MIXED_SCAN = "Annexe signee Cormoran pour la validation finale de la machine"


@pytest.fixture
def scans(tmp_path, make_cfg):
    root = tmp_path / "Docs"
    for sub in ("Scans", "Mixed", "Text"):
        (root / sub).mkdir(parents=True)
    make_pdf(root / "Scans" / "scan.pdf", [("image", SCAN_1), ("image", SCAN_2)])
    make_pdf(root / "Mixed" / "mixed.pdf", [("text", MIXED_TEXT), ("image", MIXED_SCAN)])
    make_pdf(root / "Text" / "text.pdf", [("text", MIXED_TEXT), ("text", MIXED_TEXT)])
    cfg = make_cfg(root, ocr_tessdata=TESSDATA)
    run_index(cfg, progress=quiet)
    return cfg, root


def doc_id(con, name):
    return con.execute("SELECT id FROM docs WHERE name = ?", (name,)).fetchone()[0]


def test_line_filter_keeps_prose_and_drops_drawing_noise():
    assert ocr.keep_line("Remplir le tube de graisse, ensuite enfiler l'axe pour faire sortir le surplus")
    assert ocr.keep_line("Fill the tube with grease (Ref: Mobil")
    assert ocr.keep_line("Vérifier l'état de l'axe d'entraînement")
    assert ocr.keep_line("S'assurer que l'opérateur a coupé l'alimentation")
    assert ocr.keep_line("Le sous-ensemble est monté sur le bâti")
    assert ocr.keep_line("Retirar el sí del motor")
    assert not ocr.keep_line("2 4 5 6 | 140,141 20 11#,12 /10 = LA")
    assert not ocr.keep_line("| | 8 5SF HP) AVE :")
    assert not ocr.keep_line("Paloma")  # a single word is not enough
    text = "2 4 5 6 | 140,141 20\nRemplir le tube de graisse avant montage\n= ; 322 | 101#"
    assert ocr.filter_text(text) == "Remplir le tube de graisse avant montage"


def test_ocr_settings_from_the_config(tmp_path, monkeypatch):
    monkeypatch.delenv("DMX_DOCS_WORLD", raising=False)
    p = tmp_path / "config.toml"
    p.write_text("[index]\ndata_dir = 'D'\n\n[ocr]\nlanguages = 'fra+eng'\ndpi = 200\n\n"
                 "[worlds.projects]\nprofile = 'projects'\n\n[worlds.marketing]\nprofile = 'marketing'\nocr = false\n"
                 .replace("D", (tmp_path / "data").as_posix()), encoding="utf-8")
    cfg = load_config(p)
    assert (cfg.ocr_enabled, cfg.ocr_languages, cfg.ocr_dpi) == (True, "fra+eng", 200)
    assert cfg.tessdata_dir == tmp_path / "tessdata"  # next to data_dir, like C:\dmx-rag\tessdata
    assert load_config(p, world="marketing").ocr_enabled is False


def test_candidates_scans_first_then_textless_pages_of_text_pdfs(scans):
    cfg, _ = scans
    con = store.connect(cfg.db_path)
    jobs = ocr.candidates(con, cfg.max_pdf_pages)
    by_doc = {j.doc_id: j.pages for j in jobs}
    assert jobs[0].doc_id == doc_id(con, "scan.pdf")            # fully scanned documents first
    assert by_doc[doc_id(con, "scan.pdf")] == [1, 2]
    assert by_doc[doc_id(con, "mixed.pdf")] == [2]
    assert doc_id(con, "text.pdf") not in by_doc
    con.close()


def test_pages_already_read_are_not_candidates_unless_retried_or_outdated(scans):
    cfg, _ = scans
    con = store.connect(cfg.db_path)
    mixed = doc_id(con, "mixed.pdf")
    scan = doc_id(con, "scan.pdf")
    con.execute("INSERT INTO ocr_pages(doc_id, page_no, status, chars, ocr_version) VALUES(?, 2, 'empty', 0, ?)",
                (mixed, ocr.OCR_VERSION))
    con.execute("INSERT INTO ocr_pages(doc_id, page_no, status, chars, ocr_version) VALUES(?, 1, 'timeout', 0, ?)",
                (scan, ocr.OCR_VERSION))
    con.execute("INSERT INTO ocr_pages(doc_id, page_no, status, chars, ocr_version) VALUES(?, 2, 'text', 80, ?)",
                (scan, ocr.OCR_VERSION - 1))
    con.commit()
    plain = {j.doc_id: j.pages for j in ocr.candidates(con, cfg.max_pdf_pages)}
    assert mixed not in plain                      # read, nothing usable: not again
    assert plain[scan] == [2]                      # timeout skipped, older version redone
    retried = {j.doc_id: j.pages for j in ocr.candidates(con, cfg.max_pdf_pages, retry=True)}
    assert retried[scan] == [1, 2]
    con.close()


def test_a_page_with_ocr_text_is_judged_by_its_record(scans):
    cfg, _ = scans
    con = store.connect(cfg.db_path)
    mixed = doc_id(con, "mixed.pdf")
    con.execute("INSERT INTO chunks(doc_id, page_no, seq, text) VALUES(?, 2, 1, ?)", (mixed, MIXED_SCAN))
    con.commit()

    def jobs(retry=False):
        return {j.doc_id: j.pages for j in ocr.candidates(con, cfg.max_pdf_pages, retry=retry)}

    assert mixed not in jobs()                     # OCR text of 50+ characters, no record: nothing to do
    con.execute("INSERT INTO ocr_pages(doc_id, page_no, status, chars, ocr_version) VALUES(?, 2, 'text', ?, ?)",
                (mixed, len(MIXED_SCAN), ocr.OCR_VERSION - 1))
    con.commit()
    assert jobs()[mixed] == [2]                    # read by an older OCR_VERSION: redone
    assert jobs(retry=True)[mixed] == [2]
    con.execute("UPDATE ocr_pages SET status = 'timeout', ocr_version = ? WHERE doc_id = ? AND page_no = 2",
                (ocr.OCR_VERSION, mixed))
    con.commit()
    assert mixed not in jobs()                     # timed out at this version: skipped ...
    assert jobs(retry=True)[mixed] == [2]          # ... unless retried
    con.execute("UPDATE ocr_pages SET status = 'text', ocr_version = ? WHERE doc_id = ? AND page_no = 2",
                (ocr.OCR_VERSION, mixed))
    con.commit()
    assert mixed not in jobs(retry=True)           # control: read with text at this version, never again
    con.close()


def test_big_documents_are_split_into_jobs(scans, monkeypatch):
    cfg, _ = scans
    monkeypatch.setattr(ocr, "PAGES_PER_JOB", 1)
    con = store.connect(cfg.db_path)
    jobs = ocr.candidates(con, cfg.max_pdf_pages)
    scan = doc_id(con, "scan.pdf")
    assert [j.pages for j in jobs if j.doc_id == scan] == [[1], [2]]
    con.close()


def test_reindexing_a_document_forgets_its_ocr(scans):
    cfg, _ = scans
    con = store.connect(cfg.db_path)
    scan = doc_id(con, "scan.pdf")
    con.execute("INSERT INTO ocr_pages(doc_id, page_no, status, chars, ocr_version) VALUES(?, 1, 'empty', 0, ?)",
                (scan, ocr.OCR_VERSION))
    store.delete_doc_content(con, scan)
    assert con.execute("SELECT count(*) FROM ocr_pages WHERE doc_id = ?", (scan,)).fetchone()[0] == 0
    con.close()

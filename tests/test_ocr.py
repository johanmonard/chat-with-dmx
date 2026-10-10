import os
import time
from concurrent.futures import Future, ProcessPoolExecutor, ThreadPoolExecutor
from concurrent.futures.process import BrokenProcessPool
from pathlib import Path

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


def build_scans(tmp_path, make_cfg, **kw):
    root = tmp_path / "Docs"
    for sub in ("Scans", "Mixed", "Text"):
        (root / sub).mkdir(parents=True)
    make_pdf(root / "Scans" / "scan.pdf", [("image", SCAN_1), ("image", SCAN_2)])
    make_pdf(root / "Mixed" / "mixed.pdf", [("text", MIXED_TEXT), ("image", MIXED_SCAN)])
    make_pdf(root / "Text" / "text.pdf", [("text", MIXED_TEXT), ("text", MIXED_TEXT)])
    cfg = make_cfg(root, **kw)
    run_index(cfg, progress=quiet)
    return cfg, root


@pytest.fixture
def scans(tmp_path, make_cfg):
    return build_scans(tmp_path, make_cfg, ocr_tessdata=TESSDATA)


@pytest.fixture
def fake_scans(tmp_path, make_cfg):
    """The same documents for tests whose page reader is a fake: a dummy language file stands in
    for Tesseract's, so they run without the shared language files."""
    tess = tmp_path / "tess"
    tess.mkdir()
    (tess / "fra.traineddata").write_bytes(b"x")
    return build_scans(tmp_path, make_cfg, ocr_tessdata=str(tess))


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


from dmx_docs.embeddings import run_embed
from dmx_docs.tools import DocTools


def chunk_count(cfg):
    con = store.connect(cfg.db_path)
    n = con.execute("SELECT count(*), sum(embedded) FROM chunks").fetchone()
    con.close()
    return n


def test_unavailable_ocr_says_why_and_reads_nothing(scans, tmp_path):
    cfg, _ = scans
    said = []
    cfg.ocr_enabled = False
    assert ocr.run_ocr(cfg, progress=said.append).pages == 0
    assert "disabled" in said[-1]
    cfg.ocr_enabled, cfg.ocr_tessdata = True, str(tmp_path / "empty")
    assert ocr.run_ocr(cfg, progress=said.append).pages == 0
    assert "fra.traineddata" in said[-1]


@needs_tesseract
def test_ocr_makes_scanned_pages_searchable_and_is_not_repeated(scans):
    cfg, _ = scans
    before = chunk_count(cfg)
    stats = ocr.run_ocr(cfg, progress=quiet)
    assert (stats.pages, stats.text) == (3, 3)
    tools = DocTools(cfg)
    assert "scan.pdf" in tools.search("Gondolier", mode="keyword")
    assert "mixed.pdf" in tools.search("Cormoran", mode="keyword")
    con = store.connect(cfg.db_path)
    assert con.execute("SELECT status FROM docs WHERE name = 'scan.pdf'").fetchone()[0] == "ok"
    assert con.execute("SELECT count(*) FROM ocr_pages WHERE status = 'text'").fetchone()[0] == 3
    con.close()
    after = chunk_count(cfg)
    assert after[0] > before[0]
    run_embed(cfg, progress=quiet)
    assert chunk_count(cfg)[1] == after[0]          # the new chunks get embeddings
    assert ocr.run_ocr(cfg, progress=quiet).pages == 0   # nothing read twice


@needs_tesseract
def test_text_already_on_the_page_is_kept_and_the_page_rebuilt(scans):
    cfg, root = scans
    make_pdf(root / "Mixed" / "header.pdf", [("text", MIXED_TEXT), ("image", "Page 2\n\n" + MIXED_SCAN)])
    run_index(cfg, progress=quiet)
    con = store.connect(cfg.db_path)
    d = doc_id(con, "header.pdf")
    con.execute("INSERT INTO chunks(doc_id, page_no, seq, text) VALUES(?, 2, 0, 'Page 2')", (d,))
    con.commit()
    con.close()
    ocr.run_ocr(cfg, progress=quiet)
    text = DocTools(cfg).read_document(str(root / "Mixed" / "header.pdf"), start_page=2)
    assert "Page 2\n\n" in text and "Cormoran" in text


@needs_tesseract
def test_changed_scan_is_read_again(scans):
    cfg, root = scans
    ocr.run_ocr(cfg, progress=quiet)
    make_pdf(root / "Scans" / "scan.pdf", [("image", "Nouveau bordereau Flamant pour la livraison")])
    run_index(cfg, progress=quiet)
    assert ocr.run_ocr(cfg, progress=quiet).text == 1
    tools = DocTools(cfg)
    assert "scan.pdf" in tools.search("Flamant", mode="keyword")
    assert "scan.pdf" not in tools.search("Gondolier", mode="keyword")


@needs_tesseract
def test_time_limit_stops_between_jobs_and_the_next_run_finishes(scans):
    cfg, _ = scans
    said = []
    stopped = ocr.run_ocr(cfg, max_minutes=1e-9, progress=said.append)
    assert stopped.pages == 0 and any("Time limit" in s for s in said)
    assert ocr.run_ocr(cfg, progress=quiet).pages == 3


@needs_tesseract
def test_new_ocr_version_replaces_the_old_ocr_chunks(scans, monkeypatch):
    cfg, _ = scans
    ocr.run_ocr(cfg, progress=quiet)
    count = chunk_count(cfg)[0]
    monkeypatch.setattr(ocr, "OCR_VERSION", ocr.OCR_VERSION + 1)
    assert ocr.run_ocr(cfg, progress=quiet).pages == 3
    assert chunk_count(cfg)[0] == count             # replaced, not added twice
    con = store.connect(cfg.db_path)
    assert {r[0] for r in con.execute("SELECT ocr_version FROM ocr_pages")} == {ocr.OCR_VERSION}
    con.close()


def test_cli_ocr_without_language_files(scans, capsys, tmp_path):
    cfg, _ = scans
    from dmx_docs.cli import main
    p = tmp_path / "c.toml"
    p.write_text(f"[index]\ndata_dir = '{cfg.data_dir.as_posix()}'\n\n[ocr]\ntessdata = '{(tmp_path / 'none').as_posix()}'\n",
                 encoding="utf-8")
    main(["--config", str(p), "ocr"])
    assert "OCR not available" in capsys.readouterr().out


def test_unreadable_pdf_is_an_error_for_each_of_its_pages(tmp_path):
    out = ocr.read_pages(str(tmp_path / "nope.pdf"), [1, 2], {})
    assert [(p, status) for p, status, _ in out] == [(1, "error"), (2, "error")]
    assert all(msg for _, _, msg in out)


@needs_tesseract
def test_replaced_ocr_chunks_lose_their_vectors_and_the_vector_cache_is_dropped(scans, monkeypatch):
    cfg, _ = scans
    ocr.run_ocr(cfg, progress=quiet)
    run_embed(cfg, progress=quiet)
    con = store.connect(cfg.db_path)
    version = store.get_meta(con, "vec_version")
    vectors = con.execute("SELECT count(*) FROM vectors").fetchone()[0]
    con.close()
    monkeypatch.setattr(ocr, "OCR_VERSION", ocr.OCR_VERSION + 1)
    assert ocr.run_ocr(cfg, progress=quiet).pages == 3
    con = store.connect(cfg.db_path)
    assert store.get_meta(con, "vec_version") != version
    assert con.execute("SELECT count(*) FROM vectors").fetchone()[0] == vectors - 3   # the 3 old OCR chunks
    assert con.execute("SELECT count(*) FROM chunks WHERE embedded = 0").fetchone()[0] == 3  # their replacements
    con.close()


REAL_READ_PAGES = ocr.read_pages


def crashing_read(path, pages, options):
    """Simulates a scan that makes the OCR library crash the whole worker process. The two jobs
    shake hands through flag files, so the healthy neighbour is always in flight when the worker
    dies, however long the processes take to start."""
    base = os.environ["DMX_TEST_CRASH_FLAGS"]
    started, crashing = Path(base + ".started"), Path(base + ".crashing")

    def wait_for(flag):
        for _ in range(600):
            if flag.exists():
                return
            time.sleep(0.05)

    if path.endswith("scan.pdf"):
        wait_for(started)                      # the neighbour has its job
        crashing.write_text("crash")
        os._exit(1)
    started.write_text("started")
    wait_for(crashing)                         # the other worker is about to die
    time.sleep(1)
    return REAL_READ_PAGES(path, pages, options)


def ocr_rows(cfg):
    con = store.connect(cfg.db_path)
    rows = {(r[0], r[1]): r[2] for r in con.execute(
        "SELECT d.name, o.page_no, o.status FROM ocr_pages o JOIN docs d ON d.id = o.doc_id")}
    con.close()
    return rows


def hanging_read(path, pages, options):
    """A scan that makes its worker hang for good; every other document is answered at once."""
    if path.endswith("scan.pdf"):
        time.sleep(600)
    return [(p, "empty", "") for p in pages]


@needs_tesseract
def test_stall_records_the_stuck_job_as_timeout_restarts_the_pool_and_goes_on(scans, monkeypatch):
    """Through the real spawn pool: a job queued behind a stuck one already reports running() there,
    so the run keeps at most one job per worker in flight and a stall only ever hits jobs that run."""
    cfg, _ = scans
    cfg.workers = 1                                  # scan.pdf takes the only worker
    monkeypatch.setattr(ocr, "read_pages", hanging_read)
    monkeypatch.setattr(ocr, "STALL_S", 5)           # mixed.pdf, read by the new pool, answers well within
    said = []
    stats = ocr.run_ocr(cfg, progress=said.append)
    assert len([s for s in said if "WARNING" in s]) == 1
    assert (stats.pages, stats.empty, stats.failed) == (3, 1, 2)
    assert ocr_rows(cfg) == {("scan.pdf", 1): "timeout", ("scan.pdf", 2): "timeout", ("mixed.pdf", 2): "empty"}


@needs_tesseract
def test_worker_crash_only_marks_the_culprit(scans, monkeypatch, tmp_path):
    cfg, _ = scans
    monkeypatch.setenv("DMX_TEST_CRASH_FLAGS", str(tmp_path / "flag"))
    monkeypatch.setattr(ocr, "read_pages", crashing_read)
    said = []
    stats = ocr.run_ocr(cfg, progress=said.append)
    assert (stats.pages, stats.text, stats.failed) == (3, 1, 2)
    assert ocr_rows(cfg) == {("scan.pdf", 1): "error", ("scan.pdf", 2): "error", ("mixed.pdf", 2): "text"}
    assert any("alone" in s and "scan.pdf" in s for s in said)
    assert "OCR done" in said[-2] and "2 pages failed or timed out" in said[-1]   # the run ended, with its hint
    con = store.connect(cfg.db_path)
    assert "crashed" in con.execute("SELECT error FROM ocr_pages WHERE page_no = 1").fetchone()[0]
    con.close()
    # --retry reads the culprit again, alone, and the run still ends
    assert ocr.run_ocr(cfg, retry=True, progress=quiet).failed == 2


@needs_tesseract
@pytest.mark.parametrize("workers", [1, 2])
def test_jobs_in_flight_when_a_worker_dies_are_read_again_one_by_one(scans, monkeypatch, workers):
    """When a worker dies every job in flight fails alike: the healthy ones are not errors."""
    cfg, _ = scans
    cfg.workers = workers
    pools = []

    class Pool(ThreadPoolExecutor):                  # stands in for the process pool
        def __init__(self, max_workers, dying):
            super().__init__(max_workers)
            self.dying = dying

        def submit(self, fn, *args, **kwargs):
            if not self.dying:
                return super().submit(fn, *args, **kwargs)
            fut = Future()                           # the first pool: a worker died, its jobs fail
            fut.set_exception(BrokenProcessPool("A child process terminated abruptly"))
            self._broken = "a worker died"
            return fut

    def new_pool(max_workers, mp_context, **kwargs):     # kwargs: the worker initializer, not needed by threads
        pools.append(Pool(max_workers, dying=not pools))
        return pools[-1]

    def fake_read(path, pages, options):
        if path.endswith("scan.pdf"):
            raise BrokenProcessPool("crashes alone too")
        return [(p, "text", "Texte lu sans incident") for p in pages]

    monkeypatch.setattr(ocr, "read_pages", fake_read)
    monkeypatch.setattr(ocr, "ProcessPoolExecutor", new_pool)
    said = []
    stats = ocr.run_ocr(cfg, progress=said.append)
    assert (stats.pages, stats.text, stats.failed) == (3, 1, 2)
    assert ocr_rows(cfg) == {("scan.pdf", 1): "error", ("scan.pdf", 2): "error", ("mixed.pdf", 2): "text"}
    alone = [s for s in said if "alone" in s]
    assert any("scan.pdf" in s for s in alone)
    assert any("mixed.pdf" in s for s in alone) == (workers == 2)    # in flight with the culprit, or not


@needs_tesseract
def test_a_worker_dying_while_jobs_are_submitted_does_not_abort_the_run(scans, monkeypatch):
    cfg, _ = scans
    pools = []

    class Pool(ThreadPoolExecutor):                  # the first pool is broken from its first submit on
        def submit(self, fn, *args, **kwargs):
            if len(pools) == 1:
                raise BrokenProcessPool("a worker died")
            return super().submit(fn, *args, **kwargs)

    def new_pool(max_workers, mp_context, **kwargs):
        pools.append(Pool(max_workers))
        return pools[-1]

    monkeypatch.setattr(ocr, "read_pages", lambda path, pages, options: [(p, "empty", "") for p in pages])
    monkeypatch.setattr(ocr, "ProcessPoolExecutor", new_pool)
    stats = ocr.run_ocr(cfg, progress=quiet)
    assert (stats.pages, stats.empty) == (3, 3) and len(pools) == 2


@needs_tesseract
def test_ctrl_c_during_a_solo_read_stops_promptly(scans, monkeypatch, tmp_path):
    cfg, _ = scans
    monkeypatch.setenv("DMX_TEST_CRASH_FLAGS", str(tmp_path / "flag"))
    real_wait = ocr.wait

    def interrupt_solo_wait(fs, timeout=None, **kwargs):
        if timeout == 1 and "return_when" not in kwargs:     # the 1 s steps of a solo read
            raise KeyboardInterrupt
        return real_wait(fs, timeout=timeout, **kwargs)

    with monkeypatch.context() as m:
        m.setattr(ocr, "read_pages", crashing_read)
        m.setattr(ocr, "wait", interrupt_solo_wait)
        with pytest.raises(KeyboardInterrupt):
            ocr.run_ocr(cfg, progress=quiet)
    rows = ocr_rows(cfg)
    assert not [k for k in rows if k[0] == "scan.pdf"]       # a suspect is not recorded before it is read
    assert ocr.run_ocr(cfg, progress=quiet).pages == 3 - len(rows)


@needs_tesseract
def test_ctrl_c_stops_cleanly_and_the_next_run_goes_on(scans, monkeypatch):
    cfg, _ = scans
    omp = os.environ.get("OMP_THREAD_LIMIT")
    said = []

    def interrupt(*args, **kwargs):
        raise KeyboardInterrupt

    with monkeypatch.context() as m:
        m.setattr(ocr, "wait", interrupt)
        with pytest.raises(KeyboardInterrupt):
            ocr.run_ocr(cfg, progress=said.append)
    assert "Interrupted" in said[-1]
    assert os.environ.get("OMP_THREAD_LIMIT") == omp     # set for the workers only while the pool lives
    assert ocr.run_ocr(cfg, progress=quiet).pages == 3   # index not locked, nothing half saved


# ---- memory of huge pages

def test_ocr_resolution_is_lowered_for_huge_pages():
    a4, a0 = pymupdf.Rect(0, 0, 595, 842), pymupdf.Rect(0, 0, 2384, 3370)
    assert ocr.ocr_dpi(a4, 300) == 300                        # an ordinary page keeps the configured dpi
    dpi = ocr.ocr_dpi(a0, 300)                                # ~140 million pixels at 300 dpi
    assert 150 <= dpi <= 170
    assert a0.width / 72 * dpi * a0.height / 72 * dpi <= ocr.MAX_OCR_PIXELS
    assert ocr.ocr_dpi(pymupdf.Rect(0, 0, 14400, 14400), 300) == ocr.MIN_OCR_DPI   # never below the floor
    assert ocr.ocr_dpi(a0, 72) == 72                          # nor above what was configured


def make_big_scan(path):
    """One A0-sized page: a picture (no text layer) of a few big words."""
    src = pymupdf.open()
    sp = src.new_page(width=2384, height=3370)
    sp.insert_textbox(pymupdf.Rect(100, 100, 2300, 900), "Plan general ligne Gondolier\nImplantation des convoyeurs",
                      fontsize=90)
    png = sp.get_pixmap(dpi=100, colorspace=pymupdf.csGRAY).tobytes("png")
    src.close()
    doc = pymupdf.open()
    page = doc.new_page(width=2384, height=3370)
    page.insert_image(page.rect, stream=png)
    doc.save(str(path), deflate=True)
    doc.close()


@needs_tesseract
def test_a_huge_page_is_still_read_at_a_capped_resolution(tmp_path, monkeypatch):
    big = tmp_path / "plan_A0.pdf"
    make_big_scan(big)
    used = []
    real = pymupdf.Page.get_textpage_ocr

    def spy(self, *args, **kwargs):
        used.append(kwargs["dpi"])
        return real(self, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "get_textpage_ocr", spy)
    out = ocr.read_pages(str(big), [1], {"languages": "fra", "dpi": 300, "tessdata": TESSDATA})
    assert [(p, status) for p, status, _ in out] == [(1, "text")]
    assert "Gondolier" in out[0][2] and "convoyeurs" in out[0][2]
    assert used and ocr.MIN_OCR_DPI <= used[0] < 300       # ~2 GB of memory per page at 300 dpi


# ---- Tesseract's console messages

def noisy_read(path, pages, options):
    """Like Tesseract, which writes its warnings straight to file descriptor 2."""
    os.write(2, b"Line cannot be recognized!!\n")
    return [(p, "empty", "") for p in pages]


def test_tesseract_messages_of_the_workers_go_to_a_log_file_not_the_console(fake_scans, monkeypatch, capfd):
    cfg, _ = fake_scans
    cfg.workers = 1
    monkeypatch.setattr(ocr, "read_pages", noisy_read)
    ocr.run_ocr(cfg, progress=quiet)                      # a real spawn pool: two jobs, one worker
    assert "cannot be recognized" not in capfd.readouterr().err
    log = cfg.logs_dir / "ocr-tesseract.log"
    assert log.read_text(encoding="utf-8", errors="replace").count("Line cannot be recognized!!") == 2


def test_a_job_read_alone_logs_tesseract_messages_too(fake_scans, monkeypatch, tmp_path, capfd):
    cfg, root = fake_scans
    monkeypatch.setattr(ocr, "read_pages", noisy_read)
    log = tmp_path / "somewhere" / "ocr-tesseract.log"
    out = ocr._read_alone(ocr.Job(1, str(root / "Scans" / "scan.pdf"), [1]), {}, str(log))
    assert out == [(1, "empty", "")]
    assert "cannot be recognized" not in capfd.readouterr().err
    assert "Line cannot be recognized!!" in log.read_text(encoding="utf-8", errors="replace")


def test_real_tesseract_messages_are_redirected_too(tmp_path, capfd):
    from dmx_docs.indexer import _MP

    pdf = tmp_path / "s.pdf"
    make_pdf(pdf, [("image", SCAN_1)])
    log = tmp_path / "logs" / "ocr-tesseract.log"
    pool = ProcessPoolExecutor(max_workers=1, mp_context=_MP, initializer=ocr._worker_init, initargs=(str(log),))
    try:   # a language that does not exist: Tesseract says so on file descriptor 2 and the page is an error
        out = pool.submit(ocr.read_pages, str(pdf), [1],
                          {"languages": "xxx", "dpi": 100, "tessdata": str(tmp_path)}).result(timeout=120)
    finally:
        pool.shutdown()
    assert [(p, status) for p, status, _ in out] == [(1, "error")]
    assert "Failed loading language" not in capfd.readouterr().err
    assert "Failed loading language" in log.read_text(encoding="utf-8", errors="replace")


def test_a_log_file_that_cannot_be_opened_does_not_stop_the_worker(tmp_path, capfd):
    blocked = tmp_path / "file"
    blocked.write_text("not a folder")
    saved = os.dup(2)
    try:
        ocr._worker_init(str(blocked / "ocr-tesseract.log"))   # its folder cannot be created
        os.write(2, b"goes to the null device\n")
    finally:
        os.dup2(saved, 2)
        os.close(saved)
    assert "null device" not in capfd.readouterr().err         # discarded, not shown


# ---- circuit breaker and closing summary

threads = lambda max_workers, mp_context, **kwargs: ThreadPoolExecutor(max_workers)  # noqa: E731


def failing_read(path, pages, options):
    return [(p, "error", "OSError: share not reachable") for p in pages]


def test_run_stops_when_jobs_keep_failing_and_the_rest_stays_a_candidate(fake_scans, monkeypatch):
    cfg, _ = fake_scans
    cfg.workers = 1
    monkeypatch.setattr(ocr, "PAGES_PER_JOB", 1)                # three jobs: scan p.1, scan p.2, mixed p.2
    monkeypatch.setattr(ocr, "MAX_FAILED_JOBS_IN_A_ROW", 2)
    monkeypatch.setattr(ocr, "read_pages", failing_read)
    monkeypatch.setattr(ocr, "ProcessPoolExecutor", threads)
    said = []
    stats = ocr.run_ocr(cfg, progress=said.append)
    assert (stats.pages, stats.failed) == (2, 2)
    assert ocr_rows(cfg) == {("scan.pdf", 1): "error", ("scan.pdf", 2): "error"}   # mixed.pdf: not recorded
    stop = [s for s in said if "in a row" in s]
    assert len(stop) == 1
    assert "2 jobs in a row failed (last error: OSError: share not reachable)" in stop[0]
    assert "Stopped: check the file share / language files, then run again" in stop[0]
    assert "--retry" in stop[0]
    assert not any("Time limit" in s for s in said)
    con = store.connect(cfg.db_path)
    left = {j.doc_id: j.pages for j in ocr.candidates(con, cfg.max_pdf_pages)}
    assert left == {doc_id(con, "mixed.pdf"): [2]}              # still a candidate; the failed ones wait for --retry
    con.close()


def test_a_job_that_works_resets_the_count_of_failed_jobs(fake_scans, monkeypatch):
    cfg, _ = fake_scans
    cfg.workers = 1
    monkeypatch.setattr(ocr, "PAGES_PER_JOB", 1)
    monkeypatch.setattr(ocr, "MAX_FAILED_JOBS_IN_A_ROW", 2)

    def sometimes_failing(path, pages, options):
        if path.endswith("scan.pdf") and pages == [2]:
            return [(2, "text", "Texte lu sans incident")]
        return failing_read(path, pages, options)

    monkeypatch.setattr(ocr, "read_pages", sometimes_failing)
    monkeypatch.setattr(ocr, "ProcessPoolExecutor", threads)
    said = []
    stats = ocr.run_ocr(cfg, progress=said.append)              # error, text, error: never 2 in a row
    assert (stats.pages, stats.text, stats.failed) == (3, 1, 2)
    assert not any("in a row" in s for s in said)


def test_summary_says_how_to_read_failed_pages_again(tmp_path, make_cfg, monkeypatch):
    tess = tmp_path / "tess"
    tess.mkdir()
    (tess / "fra.traineddata").write_bytes(b"x")
    monkeypatch.setattr(ocr, "read_pages", failing_read)
    monkeypatch.setattr(ocr, "ProcessPoolExecutor", threads)
    cfg, _ = build_scans(tmp_path, make_cfg, ocr_tessdata=str(tess), world="projects")
    said = []
    assert ocr.run_ocr(cfg, progress=said.append).failed == 3
    assert said[-1] == ("3 pages failed or timed out: read them again with `dmx-docs ocr --retry` "
                        "(launcher: dmx.ps1 ocr -World projects --retry)")
    assert "OCR done" in said[-2]


def test_summary_without_failed_pages_has_no_retry_hint(fake_scans, monkeypatch):
    cfg, _ = fake_scans
    monkeypatch.setattr(ocr, "read_pages", lambda path, pages, options: [(p, "empty", "") for p in pages])
    monkeypatch.setattr(ocr, "ProcessPoolExecutor", threads)
    said = []
    assert ocr.run_ocr(cfg, progress=said.append).failed == 0
    assert "OCR done" in said[-1] and not any("--retry" in s for s in said)

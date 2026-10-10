# tests/test_describe.py
import json

import pytest

from conftest import make_png, make_pptx
from dmx_docs import describe, store, vision
from dmx_docs.indexer import run_index
from dmx_docs.tools import DocTools
from fake_ollama import answer

quiet = lambda *a, **k: None  # noqa: E731
PICS = [".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff"]


@pytest.fixture
def world(tmp_path, make_cfg, fake_ollama):
    """4 documents: FR and DE manual pages sharing one picture (plus an icon each), a deck showing the
    same photo twice on slide 3, a picture file. The manual's img folder is not scanned (exclude
    pattern) but its pictures stay inside the root, so the links resolve."""
    root = tmp_path / "Source"
    (root / "img").mkdir(parents=True)
    make_png(root / "img" / "outil.png", (200, 0, 0), size=(300, 200))
    make_png(root / "img" / "icone.png", (0, 0, 0), size=(40, 20))
    for lang in ("FR", "DE"):
        (root / f"5120_Depileur_{lang}.md").write_text(
            f"# Dépileur {lang}\n\nRéglage.\n\n![Outil](img/outil.png)\n\n![](img/icone.png)\n", encoding="utf-8")
    make_png(tmp_path / "photo.png", (0, 200, 0), size=(320, 200))
    make_pptx(root / "deck.pptx", pictures=[tmp_path / "photo.png", tmp_path / "photo.png"])
    (root / "Pictures").mkdir()
    make_png(root / "Pictures" / "Paloma_4R.png", (0, 0, 200), size=(400, 300))
    cfg = make_cfg(root, extensions=[".md", ".pptx"] + PICS, exclude=["~$*", ".*", "img"],
                   vision_url=fake_ollama.url, vision_model="test-vision", vision_timeout_s=5)
    run_index(cfg, progress=quiet)
    return cfg, root


def rows(cfg, sql, *args):
    con = store.connect(cfg.db_path)
    try:
        return [tuple(r) for r in con.execute(sql, args)]   # store.connect returns sqlite3.Row objects
    finally:
        con.close()


def test_skipped_when_ollama_is_unavailable(tmp_path, make_cfg):
    (tmp_path / "r").mkdir()
    cfg = make_cfg(tmp_path / "r", vision_url="http://127.0.0.1:9", vision_model="m")
    msgs = []
    stats = describe.run_describe(cfg, progress=msgs.append)
    assert stats.documents == 0 and any("does not answer" in m for m in msgs) and any("skipped" in m for m in msgs)
    cfg.vision_enabled = False
    msgs.clear()
    describe.run_describe(cfg, progress=msgs.append)
    assert any("off for this world" in m for m in msgs)


def test_describe_adds_one_passage_per_occurrence(world, fake_ollama):
    cfg, root = world
    stats = describe.run_describe(cfg, progress=quiet)
    # distinct pictures: outil.png (shared by FR and DE), photo.png (twice in the deck), Paloma_4R.png
    # FR (model call), DE (cache), deck (call + second copy), file (call): handled one at a time in the main process
    assert len(fake_ollama.calls) == 3
    assert (stats.documents, stats.described, stats.reused, stats.small, stats.failed) == (4, 3, 2, 2, 0)
    uses = rows(cfg, "SELECT d.name, u.page_no, u.image_no, u.status, u.chunk_id FROM picture_uses u "
                     "JOIN docs d ON d.id = u.doc_id ORDER BY d.name, u.page_no, u.image_no")
    assert [(n, p, i, s) for n, p, i, s, _ in uses] == [
        ("5120_Depileur_DE.md", 1, 1, "described"), ("5120_Depileur_DE.md", 1, 2, "small"),
        ("5120_Depileur_FR.md", 1, 1, "described"), ("5120_Depileur_FR.md", 1, 2, "small"),
        ("Paloma_4R.png", 1, 1, "described"),
        ("deck.pptx", 3, 1, "described"), ("deck.pptx", 3, 2, "described")]
    text = rows(cfg, "SELECT c.text, c.embedded FROM chunks c JOIN picture_uses u ON u.chunk_id = c.id "
                     "JOIN docs d ON d.id = u.doc_id WHERE d.name = '5120_Depileur_FR.md'")[0]
    assert text[0].startswith("\n\n[Image 1 — photo] Photo d'un préhenseur") and text[1] == 0
    assert "Mots-clés : préhenseur, ventouses, gripper, suction cups" in text[0]
    pic = rows(cfg, "SELECT c.text, d.status FROM chunks c JOIN docs d ON d.id = c.doc_id WHERE d.name = 'Paloma_4R.png'")
    assert pic == [("[Image 1 — photo] Fichier : Paloma_4R.png (dossiers : Source / Pictures). "
                    "Photo d'un préhenseur à ventouses sur un robot Delta.\n"
                    "Texte visible : —\nMots-clés : préhenseur, ventouses, gripper, suction cups", "ok")]
    assert len(rows(cfg, "SELECT * FROM picture_docs")) == 4
    assert "Paloma_4R.png" in DocTools(cfg).search("ventouses", mode="keyword")


def test_same_picture_twice_in_a_document_is_described_once(world, fake_ollama):
    cfg, _ = world
    describe.run_describe(cfg, progress=quiet)
    deck = rows(cfg, "SELECT u.sha1, u.chunk_id FROM picture_uses u JOIN docs d ON d.id = u.doc_id "
                     "WHERE d.name = 'deck.pptx'")
    assert len(deck) == 2 and deck[0][0] == deck[1][0] and deck[0][1] != deck[1][1]


def test_second_run_has_nothing_to_do(world, fake_ollama):
    cfg, _ = world
    describe.run_describe(cfg, progress=quiet)
    msgs = []
    describe.run_describe(cfg, progress=msgs.append)
    assert len(fake_ollama.calls) == 3 and any("No picture to describe" in m for m in msgs)


def test_changed_document_gets_its_passages_back_from_the_cache(world, fake_ollama):
    cfg, root = world
    describe.run_describe(cfg, progress=quiet)
    md = root / "5120_Depileur_FR.md"
    md.write_text(md.read_text(encoding="utf-8") + "\nNouvelle phrase.\n", encoding="utf-8")
    run_index(cfg, progress=quiet)
    assert rows(cfg, "SELECT count(*) FROM picture_uses u JOIN docs d ON d.id = u.doc_id "
                     "WHERE d.name = '5120_Depileur_FR.md'")[0][0] == 0
    stats = describe.run_describe(cfg, progress=quiet)
    assert len(fake_ollama.calls) == 3 and stats.reused == 1 and stats.described == 0
    assert rows(cfg, "SELECT count(*) FROM picture_uses u JOIN docs d ON d.id = u.doc_id "
                     "WHERE d.name = '5120_Depileur_FR.md' AND u.status = 'described'")[0][0] == 1


def test_a_new_model_replaces_the_passages(world, fake_ollama):
    cfg, _ = world
    describe.run_describe(cfg, progress=quiet)
    old = {r[0] for r in rows(cfg, "SELECT chunk_id FROM picture_uses WHERE chunk_id IS NOT NULL")}
    fake_ollama.models.append("other-vision")
    cfg.vision_model = "other-vision"
    describe.run_describe(cfg, progress=quiet)
    new = {r[0] for r in rows(cfg, "SELECT chunk_id FROM picture_uses WHERE chunk_id IS NOT NULL")}
    assert len(fake_ollama.calls) == 6 and len(new) == len(old) and not (old & new)
    assert rows(cfg, "SELECT count(*) FROM chunks WHERE id IN (%s)" % ",".join(map(str, old)))[0][0] == 0


def test_logo_is_cached_without_a_passage(world, fake_ollama):
    cfg, _ = world
    fake_ollama.reply = lambda body: answer(kind=vision.LOGO, description="Logo Demaurex.")
    stats = describe.run_describe(cfg, progress=quiet)
    assert stats.logos == 5 and rows(cfg, "SELECT count(*) FROM picture_uses WHERE chunk_id IS NOT NULL")[0][0] == 0
    assert rows(cfg, "SELECT count(*) FROM picture_cache WHERE type = ?", vision.LOGO)[0][0] == 3


def test_invalid_answers_are_retried_then_recorded_and_retry_fixes_them(world, fake_ollama):
    cfg, _ = world
    fake_ollama.reply = lambda body: (200, {"message": {"content": "garbage"}})
    stats = describe.run_describe(cfg, progress=quiet)
    assert len(fake_ollama.calls) == 6 and stats.failed == 5        # 3 pictures x 2 tries; 5 occurrences
    assert {r[0] for r in rows(cfg, "SELECT status FROM picture_uses WHERE status NOT IN ('small')")} == {"error"}
    fake_ollama.reply = lambda body: answer()
    describe.run_describe(cfg, progress=quiet)                      # without --retry: nothing
    assert len(fake_ollama.calls) == 6
    describe.run_describe(cfg, retry=True, progress=quiet)
    assert {r[0] for r in rows(cfg, "SELECT status FROM picture_uses WHERE status NOT IN ('small')")} == {"described"}


def test_a_timeout_is_retried_once_then_recorded(world, fake_ollama):
    cfg, _ = world
    cfg.vision_timeout_s = 0.3
    fake_ollama.delay = 1
    describe.run_describe(cfg, progress=quiet)
    assert {r[0] for r in rows(cfg, "SELECT status FROM picture_uses WHERE status NOT IN ('small')")} == {"timeout"}
    assert len(fake_ollama.calls) == 6


def test_stops_after_too_many_failed_calls_in_a_row(world, fake_ollama, monkeypatch):
    cfg, _ = world
    # 1: the first handled document fails its call, so only the documents already being prepared
    # (2 workers) are saved, never all 4.
    monkeypatch.setattr(describe, "MAX_FAILED_CALLS_IN_A_ROW", 1)
    fake_ollama.reply = lambda body: (500, {"error": "boom"})
    msgs = []
    describe.run_describe(cfg, progress=msgs.append)
    assert any("in a row failed" in m for m in msgs)
    assert len(rows(cfg, "SELECT * FROM picture_docs")) < 4           # stopped before the end


def test_time_limit_stops_and_the_next_run_continues(world, fake_ollama):
    cfg, _ = world
    msgs = []
    describe.run_describe(cfg, max_minutes=1e-6, progress=msgs.append)
    assert any("Time limit" in m for m in msgs)
    describe.run_describe(cfg, progress=quiet)
    assert len(rows(cfg, "SELECT * FROM picture_docs")) == 4


def test_model_is_unloaded_at_the_end(world, fake_ollama):
    cfg, _ = world
    describe.run_describe(cfg, progress=quiet)
    assert fake_ollama.unloaded == ["test-vision"]


def test_cli_describe_skips_with_exit_code_0(tmp_path, capsys):
    from dmx_docs.cli import main

    (tmp_path / "r").mkdir()
    cfg = tmp_path / "config.toml"
    cfg.write_text(f"[index]\nroots = ['{(tmp_path / 'r').as_posix()}']\ndata_dir = '{(tmp_path / 'd').as_posix()}'\n"
                   "[vision]\nurl = 'http://127.0.0.1:9'\n", encoding="utf-8")
    main(["--config", str(cfg), "describe"])
    assert "skipped" in capsys.readouterr().out


def test_compare_writes_a_page_and_leaves_the_index_alone(world, fake_ollama, tmp_path):
    cfg, _ = world
    fake_ollama.models.append("other-vision")
    before = rows(cfg, "SELECT count(*) FROM chunks")[0][0]
    path = describe.run_compare(cfg, ["test-vision", "other-vision"], sample=3, out_dir=str(tmp_path / "out"),
                                progress=quiet)
    html = open(path, encoding="utf-8").read()
    assert "test-vision" in html and "other-vision" in html and "préhenseur à ventouses" in html  # ' is escaped
    assert html.count("<tr><td class=n>") == 3
    assert rows(cfg, "SELECT count(*) FROM chunks")[0][0] == before
    assert rows(cfg, "SELECT count(*) FROM picture_uses")[0][0] == 0
    assert rows(cfg, "SELECT count(*) FROM picture_cache")[0][0] == 0


def test_compare_reports_a_missing_model(world, fake_ollama, tmp_path):
    cfg, _ = world
    msgs = []
    path = describe.run_compare(cfg, ["test-vision", "missing:7b"], sample=2, out_dir=str(tmp_path / "o"),
                                progress=msgs.append)
    assert any("ollama pull missing:7b" in m for m in msgs)
    assert "missing:7b" in open(path, encoding="utf-8").read()


# ---------------------------------------------------------------- final review fixes

@pytest.fixture
def many(tmp_path, make_cfg, fake_ollama):
    """One manual page with 5 distinct pictures."""
    root = tmp_path / "Many"
    (root / "img").mkdir(parents=True)
    links = []
    for i in range(5):
        make_png(root / "img" / f"p{i}.png", (40 * i, 10, 10), size=(300, 200))
        links.append(f"![P{i}](img/p{i}.png)")
    (root / "page_FR.md").write_text("# Page\n\n" + "\n\n".join(links) + "\n", encoding="utf-8")
    cfg = make_cfg(root, extensions=[".md"], exclude=["~$*", ".*", "img"], vision_url=fake_ollama.url,
                   vision_model="test-vision", vision_timeout_s=5)
    run_index(cfg, progress=quiet)
    return cfg


def test_stops_inside_a_document_and_keeps_what_was_described(many, fake_ollama, monkeypatch):
    cfg = many
    monkeypatch.setattr(describe, "MAX_FAILED_CALLS_IN_A_ROW", 2)
    n = {"calls": 0}

    def reply(body):
        n["calls"] += 1
        return answer() if n["calls"] <= 2 else (500, {"error": "GPU hung"})
    fake_ollama.reply = reply
    describe.run_describe(cfg, progress=quiet)
    # 2 good calls, then 2 failed pictures x 2 tries; the 5th picture is not sent
    assert len(fake_ollama.calls) == 6
    assert rows(cfg, "SELECT count(*) FROM picture_docs")[0][0] == 0      # unfinished: still a candidate
    assert rows(cfg, "SELECT count(*) FROM picture_cache")[0][0] == 2     # what was described is kept
    fake_ollama.reply = lambda body: answer()
    describe.run_describe(cfg, progress=quiet)
    assert len(fake_ollama.calls) == 9                                      # only the 3 missing pictures
    assert rows(cfg, "SELECT count(*) FROM picture_uses WHERE status = 'described'")[0][0] == 5


def test_time_limit_is_checked_between_the_pictures_of_a_document(many, fake_ollama):
    cfg = many
    fake_ollama.delay = 1
    msgs = []
    describe.run_describe(cfg, max_minutes=0.05, progress=msgs.append)
    assert len(fake_ollama.calls) < 5 and rows(cfg, "SELECT count(*) FROM picture_docs")[0][0] == 0
    assert any("Time limit" in m for m in msgs)


def test_a_crashing_document_does_not_fail_the_ones_prepared_with_it(world, fake_ollama, monkeypatch):
    cfg, _ = world
    monkeypatch.setenv("DMX_DOCS_TEST_CRASH_SUFFIX", "5120_Depileur_DE.md")
    describe.run_describe(cfg, progress=quiet)
    assert rows(cfg, "SELECT d.name, u.status FROM picture_uses u JOIN docs d ON d.id = u.doc_id "
                     "WHERE u.page_no = 0") == [("5120_Depileur_DE.md", "error")]
    done = {r[0] for r in rows(cfg, "SELECT d.name FROM picture_docs p JOIN docs d ON d.id = p.doc_id")}
    assert done == {"5120_Depileur_FR.md", "deck.pptx", "Paloma_4R.png"}  # the crashing one is tried again next run


def test_a_hanging_document_is_recorded_as_timeout(world, fake_ollama, monkeypatch):
    cfg, _ = world
    monkeypatch.setattr(describe, "STALL_S", 6)
    monkeypatch.setenv("DMX_DOCS_TEST_HANG_SUFFIX", "deck.pptx")
    describe.run_describe(cfg, progress=quiet)
    assert rows(cfg, "SELECT d.name, u.status FROM picture_uses u JOIN docs d ON d.id = u.doc_id "
                     "WHERE u.page_no = 0") == [("deck.pptx", "timeout")]
    assert len(rows(cfg, "SELECT * FROM picture_docs")) == 4               # a hang waits for --retry


def test_an_unreadable_document_keeps_its_passages_and_is_tried_again(world, fake_ollama):
    cfg, root = world
    describe.run_describe(cfg, progress=quiet)
    fr_passages = ("SELECT count(*) FROM picture_uses u JOIN docs d ON d.id = u.doc_id "
                   "WHERE d.name = '5120_Depileur_FR.md' AND u.chunk_id IS NOT NULL")
    assert rows(cfg, fr_passages)[0][0] == 1
    fake_ollama.models.append("other-vision")
    cfg.vision_model = "other-vision"
    md = root / "5120_Depileur_FR.md"
    md.rename(root / "away.tmp")                                            # the share hiccups
    describe.run_describe(cfg, progress=quiet)
    assert rows(cfg, fr_passages)[0][0] == 1                                # the old passage is kept
    (root / "away.tmp").rename(md)
    describe.run_describe(cfg, progress=quiet)                              # tried again without --retry
    assert rows(cfg, "SELECT p.model FROM picture_docs p JOIN docs d ON d.id = p.doc_id "
                     "WHERE d.name = '5120_Depileur_FR.md'") == [("other-vision",)]


def test_stops_after_too_many_unreadable_documents(world, fake_ollama, monkeypatch):
    cfg, root = world
    monkeypatch.setattr(describe, "MAX_FAILED_DOCS_IN_A_ROW", 1)
    root.rename(root.parent / "gone")                                       # the share is gone
    msgs = []
    try:
        describe.run_describe(cfg, progress=msgs.append)
    finally:
        (root.parent / "gone").rename(root)
    assert any("could not be read" in m for m in msgs)
    assert len(rows(cfg, "SELECT * FROM picture_uses WHERE page_no = 0")) <= 2   # only those in flight
    assert rows(cfg, "SELECT count(*) FROM picture_docs")[0][0] == 0


def test_an_unexpected_error_in_a_call_does_not_stop_the_run(world, monkeypatch):
    cfg, _ = world

    def boom(*a, **k):
        raise RuntimeError("IncompleteRead")
    monkeypatch.setattr(vision, "describe_image", boom)
    stats = describe.run_describe(cfg, progress=quiet)
    assert stats.failed == 5 and len(rows(cfg, "SELECT * FROM picture_docs")) == 4


def test_a_word_file_with_only_pictures_is_described(tmp_path, make_cfg, fake_ollama):
    from docx import Document
    from docx.shared import Inches

    root = tmp_path / "W"
    root.mkdir()
    make_png(tmp_path / "p.png", (9, 9, 9), size=(300, 200))
    d = Document()
    d.add_picture(str(tmp_path / "p.png"), width=Inches(2))
    d.save(root / "cover.docx")
    cfg = make_cfg(root, extensions=[".docx"], vision_url=fake_ollama.url, vision_model="test-vision",
                   vision_timeout_s=5)
    run_index(cfg, progress=quiet)
    assert rows(cfg, "SELECT status FROM docs") == [("empty",)]
    describe.run_describe(cfg, progress=quiet)
    assert rows(cfg, "SELECT status FROM docs") == [("ok",)] and len(fake_ollama.calls) == 1


def test_describe_does_not_hide_scanned_pages_from_ocr(tmp_path, make_cfg, fake_ollama):
    from dmx_docs import ocr
    from test_ocr import make_pdf as make_ocr_pdf

    root = tmp_path / "Scans"
    root.mkdir()
    make_ocr_pdf(root / "scan.pdf", [("image", "Bordereau de livraison pour quarante colis")])
    cfg = make_cfg(root, vision_url=fake_ollama.url, vision_model="test-vision", vision_timeout_s=5)
    run_index(cfg, progress=quiet)
    describe.run_describe(cfg, progress=quiet)        # no OCR language files in the tests: the page is described
    con = store.connect(cfg.db_path)
    con.isolation_level = None
    try:
        assert rows(cfg, "SELECT count(*) FROM picture_uses WHERE status = 'described'")[0][0] == 1
        assert [j.pages for j in ocr.candidates(con, cfg.max_pdf_pages)] == [[1]]   # still an OCR candidate
        doc_id = con.execute("SELECT id FROM docs").fetchone()[0]
        con.execute("BEGIN")
        ocr.save_page(con, doc_id, 1, "text", "Bordereau de livraison pour quarante colis")
        con.execute("COMMIT")
    finally:
        con.close()
    assert rows(cfg, "SELECT count(*) FROM picture_docs")[0][0] == 0      # examined again with the OCR text

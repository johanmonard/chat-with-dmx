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
    assert pic == [("[Image 1 — photo] Fichier : Paloma_4R.png. Photo d'un préhenseur à ventouses sur un robot Delta.\n"
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

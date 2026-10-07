"""Regressions found on the first large Windows run (14,761 files on a network share)."""
import os
import sys
import time

import pytest

from conftest import make_pdf
from dmx_docs import store
from dmx_docs.extract import clean_text, extract_file
from dmx_docs.indexer import run_index

quiet = lambda *a, **k: None  # noqa: E731


def test_clean_text_collapses_huge_blank_runs_quickly():
    # Sorted PDF text can hold 150,000 spaces on one line; the old regex took minutes.
    text = "Titre" + " " * 300_000 + "fin\n   ligne 2  \t suite   \n\n\n\nligne 3"
    t = time.perf_counter()
    out = clean_text(text)
    assert time.perf_counter() - t < 1
    assert out == "Titre fin\nligne 2 suite\n\nligne 3"


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path length limit")
def test_paths_longer_than_260_characters(tmp_path):
    deep = tmp_path
    while len(str(deep)) < 300:
        deep = deep / ("dossier_de_projet_tres_long_" + str(len(str(deep))))
    os.makedirs(store.fs_path(str(deep)))
    target = str(deep / "manuel.pdf")
    make_pdf(store.fs_path(target), ["Remplacer les ventouses toutes les 500 heures."])
    assert len(target) > 260
    r = extract_file(target)
    assert r.status == "ok", r.error
    assert "ventouses" in r.pages[0][1]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows path length limit")
def test_index_finds_files_in_deep_folders(tmp_path, make_cfg):
    root = tmp_path / "root"
    deep = root
    while len(str(deep)) < 300:
        deep = deep / ("sous_dossier_avec_un_nom_long_" + str(len(str(deep))))
    os.makedirs(store.fs_path(str(deep)))
    make_pdf(store.fs_path(str(deep / "profond.pdf")), ["Document rangé très profondément."])
    cfg = make_cfg(root)
    stats = run_index(cfg, progress=quiet)
    assert stats.scan_errors == 0 and stats.by_status == {"ok": 1}


def test_new_extractor_version_reextracts_everything_once(corpus_copy, make_cfg):
    cfg = make_cfg(corpus_copy)
    first = run_index(cfg, progress=quiet)
    assert first.processed > 0
    assert run_index(cfg, progress=quiet).processed == 0  # nothing changed

    con = store.connect(cfg.db_path)
    store.set_meta(con, "extract_version", "1")  # as if indexed by the previous version
    con.commit()
    con.close()
    again = run_index(cfg, progress=quiet)
    assert again.processed == first.processed and again.unchanged == 0
    assert run_index(cfg, progress=quiet).processed == 0

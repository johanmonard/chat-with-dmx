"""Knowledge cards (knowledge.py): one Markdown card per project, written by Claude from the
documents, served by project_card and catalog, validated by `dmx-docs knowledge check`."""

import os
import time
from pathlib import Path

import pytest

from conftest import make_pdf
from dmx_docs.config import load_config
from dmx_docs.indexer import run_index
from dmx_docs.knowledge import CardError, Knowledge, Vocabulary, form_of, parse_card
from dmx_docs.tools import DocTools

CARD = """---
type: project
project: THOR
status: unreviewed
written: 2026-10-16
client: ACME Foods
years: 2019-2020
machines: [Paloma 4R, Presto]
infeed: [flowpack (chocolate bar 100 g, 4 formats), loose (biscuit)]
outfeed: [wrap-around case (24 bars)]
end_effectors: [suction cups]
speed: 120 products/min
read: 6
---
## Summary
Line for ACME Foods.
"""

VOCAB = """## forms
- flowpack: flow-pack, flow pack, sachet flowpack
- pouch: sachet, beutel
- loose: vrac, en vrac
- wrap-around case: wrap-around, wraparound
## end_effectors
- suction cups: ventouse, ventouses, suction cup
- mechanical gripper: pince, pinces, préhenseur à doigts
"""


# --- parsing ---------------------------------------------------------------------------------

def test_parse_card_fields_and_lists():
    c = parse_card(CARD, "THOR.md")
    assert (c.project, c.status, c.written, c.read) == ("THOR", "unreviewed", "2026-10-16", 6)
    assert c.values("infeed") == ["flowpack (chocolate bar 100 g, 4 formats)", "loose (biscuit)"]
    assert c.values("client") == ["ACME Foods"] and c.values("country") == []
    assert c.body.startswith("## Summary")


def test_parse_card_accepts_bom_and_crlf():
    c = parse_card("﻿" + CARD.replace("\n", "\r\n"), "THOR.md")
    assert c.values("machines") == ["Paloma 4R", "Presto"] and "\r" not in c.body


@pytest.mark.parametrize("broken, reason", [
    (CARD.replace("status: unreviewed", "status: done"), "status"),
    (CARD.replace("read: 6\n", ""), "read"),
    (CARD.replace("written: 2026-10-16", "written: yesterday"), "written"),
    ("## Summary only", "header"),
])
def test_parse_card_errors_name_the_problem(broken, reason):
    with pytest.raises(CardError, match=reason):
        parse_card(broken, "THOR.md")


def test_vocabulary_synonyms_fold_accents_and_case():
    v = Vocabulary.parse(VOCAB)
    assert v.canonical("end_effectors", "Ventouses") == "suction cups"
    assert v.canonical("end_effectors", "préhenseur à doigts") == "mechanical gripper"
    assert v.canonical("forms", "Flow-pack") == "flowpack"
    assert v.canonical("forms", "carton") is None
    assert form_of("wrap-around case (24 bars)") == "wrap-around case"


# --- the in-memory store ---------------------------------------------------------------------

def test_knowledge_reloads_changed_files_and_reports_bad_ones(tmp_path):
    proj = tmp_path / "knowledge" / "projects"
    proj.mkdir(parents=True)
    (proj / "THOR.md").write_text(CARD, encoding="utf-8")
    (proj / "ODIN.md").write_text("---\ntype: project\n", encoding="utf-8")   # caught half-written
    k = Knowledge(str(tmp_path / "knowledge"))
    assert [c.project for c in k.cards()] == ["THOR"]
    assert "ODIN.md" in k.errors()
    (proj / "ODIN.md").write_text(CARD.replace("THOR", "ODIN"), encoding="utf-8")
    os.utime(proj / "ODIN.md", (time.time() + 5, time.time() + 5))   # mtime resolution
    (proj / "THOR.md").unlink()
    assert [c.project for c in k.cards()] == ["ODIN"] and k.errors() == {}


def test_knowledge_without_folder(tmp_path):
    k = Knowledge(str(tmp_path / "missing"))
    assert not k.available and k.cards() == [] and Knowledge(None).cards() == []


# --- served by the tools ---------------------------------------------------------------------

def quiet(*_a, **_k):
    pass


@pytest.fixture
def indexed(tmp_path, make_cfg):
    """A tiny projects index (THOR, MOUSQUETAIRES\\1_Athos) with a card for each."""
    root = tmp_path / "RMA"
    for sub, name, text in ((r"THOR\0_Vente\00_Offres", "Offre THOR.pdf", "Offre Paloma 4R pour ACME."),
                            (r"MOUSQUETAIRES\1_Athos\Rapports", "FAT Athos.pdf", "FAT Presto Athos.")):
        d = root / sub
        d.mkdir(parents=True)
        make_pdf(str(d / name), [text])
    cfg = make_cfg(root)
    cfg.register_path = None
    run_index(cfg, progress=quiet)
    cards = tmp_path / "knowledge" / "projects"
    cards.mkdir(parents=True)
    (cards / "THOR.md").write_text(CARD, encoding="utf-8")
    (cards / "MOUSQUETAIRES__1_Athos.md").write_text(CARD.replace("project: THOR", "project: 1_Athos"),
                                                     encoding="utf-8")
    cfg.knowledge_path = str(tmp_path / "knowledge")
    return cfg


def test_config_knowledge_path_default_and_override(tmp_path, monkeypatch):
    monkeypatch.delenv("DMX_DOCS_WORLD", raising=False)
    cfg_file = tmp_path / "config.toml"
    base = f"[index]\ndata_dir = '{(tmp_path / 'data').as_posix()}'\n\n[worlds.projects]\nprofile = \"projects\"\n"
    cfg_file.write_text(base, encoding="utf-8")
    assert load_config(cfg_file, world="projects").knowledge_path == str(tmp_path / "knowledge")
    cfg_file.write_text(base + "knowledge = \"cards\"\n", encoding="utf-8")
    assert load_config(cfg_file, world="projects").knowledge_path == str(tmp_path / "cards")


def test_project_card_appends_the_written_card(indexed):
    out = DocTools(indexed).project_card("thor")
    assert "Written card (unreviewed, 2026-10-16, built from 6 documents):" in out
    assert out.index("Written card") > out.index("Documents by type")
    assert "Line for ACME Foods." in out


def test_project_card_finds_a_subproject_card(indexed):
    assert "Written card" in DocTools(indexed).project_card("1_Athos")


def test_project_card_without_card_or_folder(indexed):
    (Path(indexed.knowledge_path) / "projects" / "THOR.md").unlink()
    assert "No written card yet." in DocTools(indexed).project_card("THOR")
    indexed.knowledge_path = None
    assert "No written card yet." in DocTools(indexed).project_card("THOR")


def test_project_card_names_an_unreadable_card(indexed):
    (Path(indexed.knowledge_path) / "projects" / "THOR.md").write_text("---\ntype: project\n", encoding="utf-8")
    out = DocTools(indexed).project_card("THOR")
    assert "card THOR.md unreadable:" in out and "No written card yet." not in out

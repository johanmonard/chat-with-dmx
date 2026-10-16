"""Knowledge cards (knowledge.py): one Markdown card per project, written by Claude from the
documents, served by project_card and catalog, validated by `dmx-docs knowledge check`."""

import os
import re
import time
from datetime import date
from pathlib import Path

import pytest

from conftest import make_pdf
from dmx_docs import knowledge
from dmx_docs.config import load_config
from dmx_docs.indexer import run_index
from dmx_docs.knowledge import CardError, Knowledge, Vocabulary, form_of, load_vocabulary, parse_card
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
- case: carton, karton
- tray: barquette
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
    assert v.canonical("forms", "crate") is None
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
    """A tiny projects index with a card for THOR and MOUSQUETAIRES\\1_Athos. Also indexed, without
    cards: MOUSQUETAIRES\\2_Porthos, ORION, and PACIFIC\\ORION (a sub-project named like a project)."""
    root = tmp_path / "RMA"
    for sub, name, text in ((r"THOR\0_Vente\00_Offres", "Offre THOR.pdf", "Offre Paloma 4R pour ACME."),
                            (r"MOUSQUETAIRES\1_Athos\Rapports", "FAT Athos.pdf", "FAT Presto Athos."),
                            (r"MOUSQUETAIRES\2_Porthos\Rapports", "FAT Porthos.pdf", "FAT Presto Porthos."),
                            (r"ORION\0_Vente\00_Offres", "Offre ORION.pdf", "Offre Hector ORION."),
                            (r"PACIFIC\ORION\Rapports", "FAT Pacific Orion.pdf", "FAT Pacific Orion.")):
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
    assert "Written card THOR.md (unreviewed, 2026-10-16, built from 6 documents):" in out
    assert out.index("Written card") > out.index("Documents by type")
    assert "Line for ACME Foods." in out


def test_project_card_finds_a_subproject_card(indexed):
    assert "Written card" in DocTools(indexed).project_card("1_Athos")


def test_project_card_without_card_or_folder(indexed):
    (Path(indexed.knowledge_path) / "projects" / "THOR.md").unlink()
    assert "No written card yet." in DocTools(indexed).project_card("THOR")
    indexed.knowledge_path = None
    assert "No written card yet." in DocTools(indexed).project_card("THOR")


ODIN = """---
type: project
project: ODIN
status: reviewed
written: 2026-10-16
client: Globex
years: 2008
machines: [Hector]
infeed: [pouch (coffee)]
outfeed: [tray]
end_effectors: [mechanical gripper]
read: 3
---
## Summary
Coffee pouches into trays.
"""


@pytest.fixture
def catalogued(indexed):
    k = Path(indexed.knowledge_path)
    (k / "projects" / "MOUSQUETAIRES__1_Athos.md").unlink()
    (k / "projects" / "ODIN.md").write_text(ODIN, encoding="utf-8")
    (k / "vocabulary.md").write_text(VOCAB, encoding="utf-8")
    return indexed


def listed(out):
    return [line.split(" · ")[0] for line in out.splitlines() if " · " in line]


def test_catalog_lists_every_card_with_coverage(catalogued):
    out = DocTools(catalogued).catalog()
    assert "THOR · ACME Foods · 2019-2020 · Paloma 4R, Presto · " in out
    assert "flowpack (chocolate bar 100 g, 4 formats), loose (biscuit) -> wrap-around case (24 bars)" in out
    assert "ODIN · Globex · 2008 · Hector · pouch (coffee) -> tray · mechanical gripper · ? · reviewed" in out
    # ODIN is not in the index: only THOR counts, out of THOR, MOUSQUETAIRES, ORION, PACIFIC
    assert out.rstrip().splitlines()[-1] == ("Cards exist for 1 of 4 projects: for a complete answer, "
                                            "also use search / list_projects.")


@pytest.mark.parametrize("kwargs, expected", [
    (dict(infeed="Flow-pack"), ["THOR"]),
    (dict(infeed="sachet"), ["ODIN"]),
    (dict(outfeed="tray"), ["ODIN"]),
    (dict(end_effector="ventouses"), ["THOR"]),
    (dict(machine="presto"), ["THOR"]),
    (dict(client="globex"), ["ODIN"]),
    (dict(country="FR"), []),
    (dict(year_from=2015), ["THOR"]),
    (dict(year_to=2010), ["ODIN"]),
    (dict(status="reviewed"), ["ODIN"]),
    (dict(text="coffee pouches"), ["ODIN"]),
    (dict(infeed="pouches"), ["ODIN"]),                       # plurals
    (dict(outfeed="wrap-around cases"), ["THOR"]),
    (dict(end_effector="mechanical grippers"), ["ODIN"]),
    (dict(outfeed="case"), ["THOR"]),                         # a wrap-around case is a case
    (dict(outfeed="cases"), ["THOR"]),
])
def test_catalog_filters(catalogued, kwargs, expected):
    assert listed(DocTools(catalogued).catalog(**kwargs)) == expected


def test_catalog_without_cards_on_this_machine(indexed):
    indexed.knowledge_path = str(Path(indexed.knowledge_path).parent / "nowhere")
    assert DocTools(indexed).catalog() == "No knowledge cards on this machine (run pull)."


def test_catalog_lists_unreadable_cards(catalogued):
    (Path(catalogued.knowledge_path) / "projects" / "BAD.md").write_text("no header", encoding="utf-8")
    out = DocTools(catalogued).catalog()
    assert "card BAD.md unreadable:" in out and listed(out) == ["ODIN", "THOR"]


# --- dmx-docs knowledge check ----------------------------------------------------------------

@pytest.fixture
def good_card(indexed):
    """THOR.md rewritten so that it passes every rule; the only card in the folder."""
    k = Path(indexed.knowledge_path)
    (k / "projects" / "MOUSQUETAIRES__1_Athos.md").unlink()
    (k / "vocabulary.md").write_text(VOCAB, encoding="utf-8")
    pdf = os.path.join(indexed.roots[0], "THOR", "0_Vente", "00_Offres", "Offre THOR.pdf")
    head = CARD.split("## Summary")[0].replace("written: 2026-10-16", f"written: {date.today().isoformat()}")
    body = f"""## Summary
Paloma line for ACME Foods.

## A. Client and machines
- Client ACME Foods [register]
- Paloma 4R and Presto, 2019-2020 [register]

## B. The line
- Infeed: chocolate bars in flowpack, and loose biscuits [{pdf} p.1]
- Outfeed: wrap-around cases of 24 bars [{pdf} p.1]
- End effector: suction cups [{pdf} p.1]
- Speed: 120 products/min [{pdf} p.1]

## C. History
- Offer 2019 [{pdf} p.1]

## D. Key documents
- Offer: {pdf}

## Checked, not found
- SAT report: none indexed
"""
    path = k / "projects" / "THOR.md"
    path.write_text(head + body, encoding="utf-8")
    return path


@pytest.fixture
def indexed_config_file(indexed, tmp_path, monkeypatch):
    monkeypatch.delenv("DMX_DOCS_WORLD", raising=False)
    p = tmp_path / "config.toml"
    p.write_text(f"[index]\ndata_dir = '{Path(indexed.data_dir).as_posix()}'\n"
                 f"roots = ['{Path(indexed.roots[0]).as_posix()}']\n"
                 f"[embeddings]\nmodel = 'hash:256'\nquery_prefix = ''\npassage_prefix = ''\n"
                 f"[search]\nregister = ''\n", encoding="utf-8")
    return p


def issues(cfg):
    tools = DocTools(cfg)
    con = tools._con()
    try:
        return [(i.file, i.level, i.message) for i in knowledge.check(tools.knowledge, con)]
    finally:
        con.close()


def test_check_accepts_a_good_card(indexed, good_card):
    assert issues(indexed) == []


@pytest.mark.parametrize("change, level, words", [
    (lambda t: t.replace(" [register]", "", 1), "error", "no source"),
    (lambda t: t.replace("speed: 120 products/min", "speed: 90 products/min"), "error", "90 products/min"),
    (lambda t: t.replace("[suction cups]", "[magnets]"), "error", "word list"),
    (lambda t: t.replace("p.1]", "p.1] [N:\\nowhere\\x.pdf p.2]", 1), "error", "cited file not in the index"),
    (lambda t: t.replace("project: THOR", "project: NOBODY"), "error", "not in the index nor the register"),
    (lambda t: t.replace(f"written: {date.today().isoformat()}", "written: 2000-01-01"), "warning", "older than"),
])
def test_check_rules(indexed, good_card, change, level, words):
    good_card.write_text(change(good_card.read_text(encoding="utf-8")), encoding="utf-8")
    found = issues(indexed)
    assert any(lv == level and words in msg for _, lv, msg in found), found


def test_check_reports_unreadable_cards(indexed, good_card):
    good_card.write_text("no header", encoding="utf-8")
    assert issues(indexed) == [("THOR.md", "error", issues(indexed)[0][2])]
    assert "header" in issues(indexed)[0][2]


def test_cli_knowledge_check_exit_code(indexed_config_file, good_card, capsys):
    from dmx_docs import cli
    cli.main(["--config", str(indexed_config_file), "knowledge", "check"])
    assert "1 cards, 0 errors, 0 warnings" in capsys.readouterr().out
    good_card.write_text("no header", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        cli.main(["--config", str(indexed_config_file), "knowledge", "check"])
    assert e.value.code == 1 and "THOR.md: error:" in capsys.readouterr().out


# --- the README and the word list shipped in the repo ----------------------------------------

def test_readme_example_card_follows_the_rules():
    root = Path(__file__).resolve().parents[1] / "knowledge"
    text = (root / "README.md").read_text(encoding="utf-8")
    example = re.search(r"```markdown\n(---\n.*?)```", text, re.S).group(1)
    card = parse_card(example, "EXAMPLE.md")
    vocab = load_vocabulary(str(root / "vocabulary.md"))
    for kind, key in (("forms", "infeed"), ("forms", "outfeed"), ("end_effectors", "end_effectors")):
        assert card.values(key), key
        for entry in card.values(key):
            assert vocab.canonical(kind, form_of(entry)), entry


# --- review findings: card identity, check workflow, word list --------------------------------

def card_file(cfg, stem, project):
    path = Path(cfg.knowledge_path) / "projects" / f"{stem}.md"
    path.write_text(CARD.replace("project: THOR", f"project: {project}")
                    .replace("Line for ACME Foods.", f"Card body of {stem}."), encoding="utf-8")
    os.utime(path, (time.time() + 5, time.time() + 5))
    return path


@pytest.mark.parametrize("order", [("ORION", "PACIFIC__ORION"), ("PACIFIC__ORION", "ORION")])
def test_project_card_prefers_the_exact_file_over_a_subproject_card(indexed, order):
    tools = DocTools(indexed)
    first, second = order
    card_file(indexed, first, "ORION")
    tools.project_card("ORION")                       # the running server has seen the first card
    card_file(indexed, second, "ORION")
    out = tools.project_card("ORION")
    assert "Card body of ORION." in out and "Card body of PACIFIC__ORION." not in out


def test_written_card_heading_names_the_file(indexed):
    card_file(indexed, "PACIFIC__ORION", "ORION")
    assert "Written card PACIFIC__ORION.md (unreviewed" in DocTools(indexed).project_card("ORION")


def test_subproject_without_card_is_labelled_with_the_parent_card(indexed):
    card_file(indexed, "MOUSQUETAIRES", "MOUSQUETAIRES")
    out = DocTools(indexed).project_card("2_Porthos")
    assert "No written card for 2_Porthos; written card of the parent project MOUSQUETAIRES" in out


def test_check_requires_the_file_name_to_match_the_project(indexed, good_card):
    card_file(indexed, "MOUSQUETAIRES__1_Athos", "MOUSQUETAIRES")
    found = issues(indexed)
    assert any(f == "MOUSQUETAIRES__1_Athos.md" and "does not match" in m for f, _, m in found), found


def test_project_card_of_a_project_gone_from_the_index_still_shows_its_card(indexed):
    card_file(indexed, "GONE", "GONE")
    out = DocTools(indexed).project_card("GONE")
    assert "No project 'GONE' in the index" in out and "Card body of GONE." in out


def test_coverage_counts_projects_not_subproject_cards(indexed):
    card_file(indexed, "MOUSQUETAIRES__2_Porthos", "2_Porthos")   # + 1_Athos and THOR from the fixture
    assert "Cards exist for 2 of 4 projects" in DocTools(indexed).catalog()


def test_check_accepts_page_ranges(indexed, good_card):
    text = good_card.read_text(encoding="utf-8")
    good_card.write_text(text.replace("p.1]", "p.1-2]", 1).replace("p.1]", "pp. 1, 3]", 1), encoding="utf-8")
    assert issues(indexed) == []


def test_vocabulary_reports_a_word_listed_twice():
    v = Vocabulary.parse("## forms\n- pouch: sac, bag\n- bag: sac\n")
    assert sorted(v.duplicates) == ["forms: 'bag' is listed under pouch and bag",
                                    "forms: 'sac' is listed under pouch and bag"]


def test_shipped_vocabulary_has_no_duplicates():
    root = Path(__file__).resolve().parents[1] / "knowledge"
    assert load_vocabulary(str(root / "vocabulary.md")).duplicates == []


def test_check_reports_vocabulary_duplicates(indexed, good_card):
    vocab = VOCAB.replace("## end_effectors", "- bag: sachet\n## end_effectors")
    (Path(indexed.knowledge_path) / "vocabulary.md").write_text(vocab, encoding="utf-8")
    assert ("vocabulary.md", "error", "forms: 'sachet' is listed under pouch and bag") in issues(indexed)


def test_readme_example_card_passes_check(indexed):
    root = Path(__file__).resolve().parents[1] / "knowledge"
    text = (root / "README.md").read_text(encoding="utf-8")
    example = re.search(r"```markdown\n(---\n.*?)```", text, re.S).group(1)
    k = Path(indexed.knowledge_path)
    for f in (k / "projects").glob("*.md"):
        f.unlink()
    (k / "projects" / "THOR.md").write_text(example, encoding="utf-8")
    (k / "vocabulary.md").write_text((root / "vocabulary.md").read_text(encoding="utf-8"), encoding="utf-8")
    errors = [m for _, lv, m in issues(indexed) if lv == "error" and not m.startswith("cited file not in the index")]
    assert errors == []


def test_cli_check_fails_without_cards(indexed_config_file, indexed, capsys):
    from dmx_docs import cli
    for f in (Path(indexed.knowledge_path) / "projects").glob("*.md"):
        f.unlink()
    with pytest.raises(SystemExit) as e:
        cli.main(["--config", str(indexed_config_file), "knowledge", "check"])
    assert e.value.code == 1 and "no cards" in capsys.readouterr().out


def test_project_card_names_an_unreadable_card(indexed):
    (Path(indexed.knowledge_path) / "projects" / "THOR.md").write_text("---\ntype: project\n", encoding="utf-8")
    out = DocTools(indexed).project_card("THOR")
    assert "card THOR.md unreadable:" in out and "No written card yet." not in out

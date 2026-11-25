"""The caller in DocTools, the server and the CLI. Also holds the shared `acl_world` fixture
(two projects, sales and mechanical folders, one reading rule each) used by the access tests."""

import dataclasses
import os
import time
from pathlib import Path

import pytest

from conftest import make_pdf
from dmx_docs import cli
from dmx_docs.access import KEEP_ALWAYS, Access, AccessStore, Caller, Unrestricted
from dmx_docs.indexer import run_index
from dmx_docs.store import path_key
from dmx_docs.tools import DocTools

SALES = "S-1-5-21-1-2-3-1101"
MEC = "S-1-5-21-1-2-3-1102"
OTHER = "S-1-5-21-1-2-3-1199"
PROJECTS = ("THOR", "ORION")


def quiet(*_a, **_k):
    pass


@pytest.fixture
def acl_world(tmp_path, make_cfg):
    """(cfg, tools_for): THOR and ORION each hold 0_Vente\\Offre <P>.pdf ("prix 100000 CHF") and
    3_Mecanique\\Plan <P>.pdf ("préhenseur ventouses"); 0_Vente folders allow SALES, 3_Mecanique
    folders allow MEC. tools_for(*sids, admin=False) builds DocTools for that caller."""
    root = tmp_path / "RMA"
    folder_rules = {}
    for p in PROJECTS:
        for sub, name, text, rule in (("0_Vente", f"Offre {p}.pdf", f"Offre {p}: prix 100000 CHF", 1),
                                      ("3_Mecanique", f"Plan {p}.pdf", f"Plan {p}: préhenseur ventouses", 2)):
            d = root / p / sub
            d.mkdir(parents=True)
            make_pdf(str(d / name), [text])
            folder_rules[path_key(str(d))] = rule
    cfg = make_cfg(root, access="acl")
    cfg.register_path = None
    run_index(cfg, progress=quiet)
    AccessStore.write(cfg.access_path, rules={"sales": 1, "mec": 2}, folder_rules=folder_rules,
                      rule_sets={1: (frozenset({SALES}), frozenset()), 2: (frozenset({MEC}), frozenset())},
                      errors={}, names={}, version="v1")

    def tools_for(*sids, admin=False):
        return DocTools(cfg, Caller(frozenset(sids) | KEEP_ALWAYS, admin))

    return cfg, tools_for


def key(cfg, *parts):
    return path_key(str(Path(cfg.roots[0]).joinpath(*parts)))


def test_docTools_of_a_world_world_reads_everything(acl_world):
    cfg, _ = acl_world
    cfg = dataclasses.replace(cfg, access="world")
    tools = DocTools(cfg, Caller(frozenset(), False))
    assert isinstance(tools.access, Unrestricted)
    assert tools.allows(key(cfg, "THOR", "0_Vente", "Offre THOR.pdf"))
    assert DocTools(cfg).allows(key(cfg, "ORION", "3_Mecanique", "Plan ORION.pdf"))


def test_allows_follows_the_folder_rule(acl_world):
    cfg, tools_for = acl_world
    offre, plan = key(cfg, "THOR", "0_Vente", "Offre THOR.pdf"), key(cfg, "THOR", "3_Mecanique", "Plan THOR.pdf")
    mec = tools_for(MEC)
    assert mec.allows(plan) and not mec.allows(offre)
    admin = tools_for(OTHER, admin=True)
    assert admin.allows(offre) and admin.allows(plan)
    assert not tools_for(OTHER).allows(offre)
    assert not mec.allows(key(cfg, "elsewhere", "x.pdf"))  # outside the roots: the world's own rule still applies


def test_a_world_without_access_file_is_hidden_except_for_admins(acl_world):
    cfg, tools_for = acl_world
    cfg.access_path.unlink()
    offre = key(cfg, "THOR", "0_Vente", "Offre THOR.pdf")
    assert not tools_for(SALES).allows(offre)
    assert tools_for(SALES, admin=True).allows(offre)


def test_without_a_caller_the_process_identity_is_used(acl_world, monkeypatch):
    cfg, _ = acl_world
    seen = []
    monkeypatch.setattr(Caller, "from_token", classmethod(lambda c, admins: seen.append(admins) or Caller(
        frozenset({SALES}) | KEEP_ALWAYS)))
    tools = DocTools(cfg)
    assert tools.allows(key(cfg, "THOR", "0_Vente", "Offre THOR.pdf")) and seen == [cfg.access_admins]


def test_as_groups_is_refused_for_non_admins(acl_world, monkeypatch, tmp_path, capsys):
    cfg, _ = acl_world
    monkeypatch.setattr(Caller, "from_token", classmethod(lambda c, admins: Caller(frozenset({SALES}), False)))
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: cfg)
    conf = tmp_path / "config.toml"
    conf.write_text("", encoding="utf-8")
    for argv in (["search", "prix", "--as-groups", "G1"], ["status", "--as-groups", "G1"],
                 ["read", "x.pdf", "--as-groups", "G1"], ["serve", "--as-groups", "G1"]):
        with pytest.raises(SystemExit) as e:
            cli.main(["--config", str(conf), *argv])
        assert e.value.code == 2
    assert "--as-groups is for the admins listed in [access] admins." in capsys.readouterr().err


def test_as_groups_builds_a_caller_for_admins(acl_world, monkeypatch, tmp_path, capsys):
    cfg, _ = acl_world
    monkeypatch.setattr(Caller, "from_token", classmethod(lambda c, admins: Caller(frozenset(), True)))
    monkeypatch.setattr(Caller, "from_groups", classmethod(lambda c, names: Caller(
        frozenset({MEC}) | KEEP_ALWAYS, False, ",".join(names))))
    monkeypatch.setattr(cli, "load_config", lambda *a, **k: cfg)
    conf = tmp_path / "config.toml"
    conf.write_text("", encoding="utf-8")
    cli.main(["--config", str(conf), "status", "--as-groups", "G1,G2"])
    assert capsys.readouterr().out


def _embed(cfg):
    from dmx_docs.embeddings import run_embed
    assert run_embed(cfg, progress=quiet) > 0


@pytest.mark.parametrize("mode", ["keyword", "semantic", "hybrid"])
def test_search_shows_only_readable_documents(acl_world, mode):
    cfg, tools_for = acl_world
    _embed(cfg)
    out = tools_for(MEC).search("prix", mode=mode)
    assert "0_Vente" not in out and "Offre" not in out
    sales = tools_for(SALES).search("prix", mode=mode)
    assert "Offre THOR" in sales and "Offre ORION" in sales
    assert "3_Mecanique" not in sales


def _crowded_world(tmp_path, make_cfg):
    """150 sales documents that match "ventouses" much better than the 5 mechanical ones."""
    root = tmp_path / "RMA"
    folder_rules = {}
    for sub, n, rule in (("0_Vente", 150, 1), ("3_Mecanique", 5, 2)):
        d = root / "THOR" / sub
        d.mkdir(parents=True)
        for i in range(n):
            text = (f"ventouses ventouses ventouses ventouses {i}" if rule == 1 else
                    f"plan {i} garniture bride levier cadre rail axe vis écrou ventouses")
            make_pdf(str(d / f"Doc {sub} {i:03d}.pdf"), [text])
        folder_rules[path_key(str(d))] = rule
    cfg = make_cfg(root, access="acl")
    cfg.register_path = None
    run_index(cfg, progress=quiet)
    _embed(cfg)
    AccessStore.write(cfg.access_path, rules={"sales": 1, "mec": 2}, folder_rules=folder_rules,
                      rule_sets={1: (frozenset({SALES}), frozenset()), 2: (frozenset({MEC}), frozenset())},
                      errors={}, names={}, version="v1")
    return cfg


def _mec_hits(cfg, mode):
    tools = DocTools(cfg, Caller(frozenset({MEC}) | KEEP_ALWAYS, False))
    con = tools._con()
    try:
        row_mask = None
        if mode != "keyword" and tools.searcher.vectors.ensure(con):
            row_mask = tools.access.row_mask(tools.searcher.vectors, con)
        hits = tools.searcher.search(con, "ventouses", limit=10, mode=mode, allowed=tools.allows,
                                     row_mask=row_mask, folder_ok=tools.access.folder_predicate())
    finally:
        con.close()
    assert all("3_Mecanique" in h.path for h in hits)
    return hits


def test_semantic_top_k_is_taken_among_allowed_rows(tmp_path, make_cfg, monkeypatch):
    cfg = _crowded_world(tmp_path, make_cfg)
    assert len(_mec_hits(cfg, "semantic")) == 5
    out = DocTools(cfg, Caller(frozenset({MEC}) | KEEP_ALWAYS, False)).search("ventouses", mode="semantic", limit=5)
    assert out.count("3_Mecanique") >= 5 and "0_Vente" not in out
    # Without the mask the 150 closer sales rows fill the top 100: the mask is what finds the 5.
    monkeypatch.setattr(Access, "row_mask", lambda self, index, con: None)
    from dmx_docs.search import Searcher
    tools = DocTools(cfg, Caller(frozenset({MEC}) | KEEP_ALWAYS, False))
    con = tools._con()
    try:
        tools.searcher.vectors.ensure(con)
        found = tools.searcher.search(con, "ventouses", limit=10, mode="semantic", allowed=tools.allows)
    finally:
        con.close()
    assert len(found) < 5


def test_keyword_slots_are_not_used_by_hidden_documents(tmp_path, make_cfg):
    cfg = _crowded_world(tmp_path, make_cfg)
    assert len(_mec_hits(cfg, "keyword")) == 5
    tools = DocTools(cfg, Caller(frozenset({MEC}) | KEEP_ALWAYS, False))
    con = tools._con()
    try:  # without the folder predicate the 100 FTS slots go to hidden documents
        bare = tools.searcher.search(con, "ventouses", limit=10, mode="keyword", allowed=tools.allows)
    finally:
        con.close()
    assert len(bare) < 5
    assert tools.search("ventouses", mode="keyword").count("3_Mecanique") >= 5


def test_row_mask_is_none_for_admins_and_world(acl_world):
    cfg, tools_for = acl_world
    _embed(cfg)
    tools = tools_for(MEC)
    con = tools._con()
    try:
        assert tools.searcher.vectors.ensure(con)
        mask = tools.access.row_mask(tools.searcher.vectors, con)
        assert mask is not None and mask.dtype == bool and 0 < mask.sum() < len(mask)
        assert tools_for(OTHER, admin=True).access.row_mask(tools.searcher.vectors, con) is None
        assert Unrestricted().row_mask(tools.searcher.vectors, con) is None
        cfg.access_path.unlink()
        none = tools_for(MEC)
        none.access._snap = None
        assert not none.access.row_mask(tools.searcher.vectors, con).any()
    finally:
        con.close()


def _answer(fn, *args, **kwargs) -> str:
    """What a tool answers, its error included (the server passes the message on)."""
    try:
        return str(fn(*args, **kwargs))
    except Exception as e:
        return f"{type(e).__name__}: {e}"


DOCUMENT_TOOLS = (("read_document", {}), ("find_in_document", {"text": "prix"}), ("view_page", {}),
                  ("export_image", {}), ("open_document", {}))


def test_hidden_path_answers_like_a_missing_one(acl_world, monkeypatch):
    cfg, tools_for = acl_world
    import dmx_docs.tools as tools_mod
    launched = []
    monkeypatch.setattr(tools_mod, "_launch", lambda args=None, path=None, verb="open": launched.append(path))
    mec = tools_for(MEC)
    hidden = str(Path(cfg.roots[0], "THOR", "0_Vente", "Offre THOR.pdf"))
    missing = str(Path(cfg.roots[0], "THOR", "3_Mecanique", "Offre THOR.pdf"))
    for name, kwargs in DOCUMENT_TOOLS:
        got = _answer(getattr(mec, name), hidden, **kwargs)
        assert got.replace("0_Vente", "3_Mecanique") == _answer(getattr(mec, name), missing, **kwargs), name
        assert "100000" not in got
    assert launched == []
    assert "100000 CHF" in tools_for(SALES).read_document(hidden)
    # A file not indexed yet in a readable folder is still read live.
    make_pdf(str(Path(cfg.roots[0], "THOR", "3_Mecanique", "Nouveau.pdf")), ["Nouveau plan: bride"])
    assert "bride" in mec.read_document(str(Path(cfg.roots[0], "THOR", "3_Mecanique", "Nouveau.pdf")))


def test_markdown_picture_in_a_hidden_folder_answers_like_a_missing_one(acl_world):
    from conftest import make_png
    cfg, tools_for = acl_world
    make_png(Path(cfg.roots[0], "THOR", "0_Vente", "schema.png"), (200, 30, 30))
    md = Path(cfg.roots[0], "THOR", "3_Mecanique", "notes.md")
    mec = tools_for(MEC)
    md.write_text("# Notes\n\n![schema](../0_Vente/schema.png)\n", encoding="utf-8")
    hidden = _answer(mec.view_page, str(md), image=1)
    md.write_text("# Notes\n\n![schema](../3_Mecanique/schema.png)\n", encoding="utf-8")
    assert hidden.replace("0_Vente", "3_Mecanique") == _answer(mec.view_page, str(md), image=1)
    md.write_text("# Notes\n\n![schema](../0_Vente/schema.png)\n", encoding="utf-8")
    caption, data, fmt = tools_for(SALES, MEC).view_page(str(md), image=1)
    assert data and "picture 1 of 1" in caption


def test_list_folder_hides_folders_with_only_hidden_documents(acl_world):
    cfg, tools_for = acl_world
    thor = str(Path(cfg.roots[0], "THOR"))
    mec = tools_for(MEC)
    out = mec.list_folder(thor)
    assert "3_Mecanique" in out and "0_Vente" not in out
    assert "Offre" not in mec.list_folder(str(Path(cfg.roots[0], "THOR", "0_Vente")))
    assert "Plan THOR.pdf" in mec.list_folder(str(Path(cfg.roots[0], "THOR", "3_Mecanique")))
    assert "(2 indexed documents" in mec.list_folder()
    with pytest.raises(ValueError, match="No indexed folder named"):
        mec.list_folder("0_Vente")  # known by name only from hidden documents
    assert "0_Vente" in tools_for(SALES).list_folder("0_Vente")
    both = tools_for(SALES).list_folder(thor)
    assert "0_Vente" in both and "3_Mecanique" not in both
    world = DocTools(dataclasses.replace(cfg, access="world"), Caller(frozenset(), False))
    out = world.list_folder(thor)
    assert "0_Vente" in out and "3_Mecanique" in out
    assert "(4 indexed documents" in world.list_folder()


def test_find_files_and_list_projects_count_only_readable_documents(acl_world):
    cfg, tools_for = acl_world
    mec = tools_for(MEC)
    out = mec.find_files("THOR")
    assert "Plan THOR.pdf" in out and "Offre" not in out and out.startswith("1 files")
    assert mec.find_files("Offre") == "No indexed file matches 'Offre'."
    projects = mec.list_projects()
    assert "THOR" in projects and "| 1 docs |" in projects and "| 2 docs |" not in projects
    assert tools_for(OTHER).list_projects().startswith("No matching project")
    assert "| 2 docs |" in tools_for(SALES, MEC).list_projects()
    card = mec.project_card("THOR")
    assert "Offre THOR" not in card and "1 indexed documents" in card


def test_find_files_slots_are_not_used_by_hidden_documents(tmp_path, make_cfg):
    cfg = _crowded_world(tmp_path, make_cfg)
    out = DocTools(cfg, Caller(frozenset({MEC}) | KEEP_ALWAYS, False)).find_files("Doc", limit=5)
    assert out.startswith("5 files") and "0_Vente" not in out


def test_index_status_says_how_many_documents_the_caller_can_read(acl_world):
    cfg, tools_for = acl_world
    assert "You can read 2 of 4 documents of this world." in tools_for(MEC).index_status()
    assert "You can read 4 of 4 documents of this world." in tools_for(OTHER, admin=True).index_status()
    assert "Access rules not loaded" not in tools_for(MEC).index_status()
    world = DocTools(dataclasses.replace(cfg, access="world"), Caller(frozenset(), False))
    assert "You can read" not in world.index_status()


def test_world_map_counts_only_readable_documents_and_its_cache_follows_the_rules(acl_world):
    cfg, tools_for = acl_world
    mec = tools_for(MEC)
    assert ": 2 documents;" in mec.world_map()
    assert ": 4 documents;" in tools_for(OTHER, admin=True).world_map()
    assert ": 0 documents;" in tools_for(OTHER).world_map()
    folder_rules = {key(cfg, p, sub): rule for p in PROJECTS for sub, rule in (("0_Vente", 1), ("3_Mecanique", 2))}
    AccessStore.write(cfg.access_path, rules={"sales": 1, "mec": 2}, folder_rules=folder_rules,
                      rule_sets={1: (frozenset({SALES, MEC}), frozenset()), 2: (frozenset({MEC}), frozenset())},
                      errors={}, names={}, version="v2")
    mec.access._checked = None  # the next call reloads the rules
    assert ": 4 documents;" in mec.world_map()


def test_mapped_drive_path_is_judged_like_its_unc_form(acl_world, monkeypatch):
    from dmx_docs import sources
    cfg, tools_for = acl_world
    root = cfg.roots[0]
    real = sources.to_unc
    monkeypatch.setattr(sources, "to_unc", lambda p: root + p[2:] if p.upper().startswith("Q:\\") else real(p))
    for sid, readable in ((MEC, False), (SALES, True)):
        tools = tools_for(sid)
        mapped = _answer(tools.read_document, "Q:\\THOR\\0_Vente\\Offre THOR.pdf")
        assert mapped == _answer(tools.read_document, str(Path(root, "THOR", "0_Vente", "Offre THOR.pdf")))
        assert ("100000 CHF" in mapped) == readable


def test_caller_without_groups_sees_nothing_and_no_error(acl_world, monkeypatch):
    cfg, tools_for = acl_world
    import dmx_docs.tools as tools_mod
    monkeypatch.setattr(tools_mod, "_launch", lambda args=None, path=None, verb="open": None)
    nobody = tools_for()
    thor = str(Path(cfg.roots[0], "THOR"))
    for out in (nobody.search("prix"), nobody.find_files("THOR"), nobody.list_folder(), nobody.list_folder(thor),
                nobody.list_projects(), nobody.world_map(), nobody.project_card("THOR")):
        assert isinstance(out, str) and "Offre" not in out and "Plan" not in out
    assert "You can read 0 of 4 documents of this world." in nobody.index_status()
    doc = str(Path(cfg.roots[0], "THOR", "3_Mecanique", "Plan THOR.pdf"))
    for name, kwargs in DOCUMENT_TOOLS:
        assert _answer(getattr(nobody, name), doc, **kwargs).startswith("FileNotFoundError: File not")


def test_missing_access_file_hides_everything_but_for_admins(acl_world):
    cfg, tools_for = acl_world
    cfg.access_path.unlink()
    sales, admin = tools_for(SALES), tools_for(OTHER, admin=True)
    status = sales.index_status()
    assert "You can read 0 of 4 documents of this world." in status
    assert "Access rules not loaded yet: only admins can read this world." in status
    assert "No indexed file" in sales.find_files("Offre")
    assert sales.list_projects().startswith("No matching project")
    assert "(0 indexed documents" in sales.list_folder()
    offre = str(Path(cfg.roots[0], "THOR", "0_Vente", "Offre THOR.pdf"))
    assert _answer(sales.read_document, offre).startswith("FileNotFoundError")
    status = admin.index_status()
    assert "You can read 4 of 4 documents of this world." in status
    assert "Access rules not loaded yet: only admins can read this world." in status
    assert "100000 CHF" in admin.read_document(offre)
    assert "Offre THOR.pdf" in admin.find_files("Offre")


def test_machine_document_counts_cover_only_readable_documents(acl_world):
    cfg, tools_for = acl_world
    for sub, name in (("0_Vente", "Offre Paloma 4R.pdf"), ("0_Vente", "Commande Paloma 4R.pdf"),
                      ("3_Mecanique", "Plan Paloma 4R.pdf")):
        make_pdf(str(Path(cfg.roots[0], "THOR", sub, name)), [f"{name}: machine"])
    run_index(cfg, progress=quiet)
    world = DocTools(dataclasses.replace(cfg, access="world"), Caller(frozenset(), False))
    for tools, count in ((world, 3), (tools_for(OTHER, admin=True), 3), (tools_for(SALES), 2), (tools_for(MEC), 1)):
        projects, card = tools.list_projects(), tools.project_card("THOR")
        assert f"Paloma 4R ({count} docs)" in projects and f"Paloma 4R ({count} docs)" in card
        for other in {3, 2, 1} - {count}:
            assert f"({other} docs)" not in projects + card
        if tools is not world:
            assert f"Paloma 4R ({count} docs)" in tools.search("Paloma", limit=30)
    assert tools_for(OTHER).list_projects().startswith("No matching project")
    # The register's note on what the documents say keeps its counts for those who read everything only.
    con = tools_for(MEC)._con()
    try:
        con.execute("INSERT INTO register_machines(project, serial, model, doc_note) VALUES "
                    "('THOR', 'S-1', 'Presto 2R', 'the documents name Paloma 4R (3 docs), not Presto 2R')")
        con.commit()
    finally:
        con.close()
    assert "(3 docs)" not in tools_for(MEC).project_card("THOR")
    assert "Paloma 4R (3 docs), not Presto 2R" in tools_for(OTHER, admin=True).project_card("THOR")
    assert "Paloma 4R (3 docs), not Presto 2R" in world.project_card("THOR")


def test_register_notes_on_the_documents_are_for_those_who_read_everything(acl_world):
    """The register's doc_note and its year taken from the documents describe every document of
    the project, hidden ones included: a restricted caller gets the register's own values."""
    cfg, tools_for = acl_world
    con = tools_for(MEC)._con()
    try:
        con.execute("INSERT INTO register_machines(project, serial, model, client, year, reg_year, doc_note) VALUES "
                    "('THOR', 'S-1', 'Presto 2R', 'ACME Foods', 2025, 2010, "
                    "'the documents name Paloma 4R (3 docs), not Presto 2R; the register says 2010; "
                    "the project''s offers/orders/FAT/SAT are dated 2025-2026')")
        con.commit()
    finally:
        con.close()
    mec = tools_for(MEC)
    card, projects = mec.project_card("THOR"), mec.list_projects()
    assert "the documents name" not in card and "dated 2025" not in card and "\n    ⚠" not in card
    assert "S-1 · Presto 2R · 2010" in card and "2025" not in card
    assert "ACME Foods, 1 machine 2010" in projects and "2025" not in projects
    world = DocTools(dataclasses.replace(cfg, access="world"), Caller(frozenset(), False))
    for tools in (tools_for(OTHER, admin=True), world):
        card = tools.project_card("THOR")
        assert "S-1 · Presto 2R · 2025" in card and "the documents name Paloma 4R (3 docs)" in card
        assert "ACME Foods, 1 machine 2025" in tools.list_projects()


# --- knowledge cards and the machine register, per caller ------------------------------------

CARD_VOCAB = """## forms
- flowpack: flow-pack, flow pack
- case: carton, karton
## end_effectors
- suction cups: ventouse, ventouses, suction cup
"""


def _thor_card(cfg, tmp_path):
    """THOR.md citing the sales offer (client, price, infeed, speed) and the mechanical plan
    (outfeed, machine, end effector)."""
    offre = Path(cfg.roots[0], "THOR", "0_Vente", "Offre THOR.pdf")
    plan = Path(cfg.roots[0], "THOR", "3_Mecanique", "Plan THOR.pdf")
    k = tmp_path / "knowledge"
    (k / "projects").mkdir(parents=True)
    (k / "vocabulary.md").write_text(CARD_VOCAB, encoding="utf-8")
    (k / "projects" / "THOR.md").write_text(f"""---
type: project
project: THOR
status: reviewed
written: 2026-11-20
client: ACME Foods
machines: [Paloma 4R]
infeed: [flowpack (chocolate bar)]
outfeed: [case]
end_effectors: [suction cups]
speed: 120 products/min
read: 2
---
## Summary
Chocolate bars for ACME Foods.

## A. Client and machines
- Client ACME Foods, price 100000 CHF [{offre} p.1]

## B. The line
- Infeed: chocolate bars in flowpack, 120 products/min [{offre} p.1]
- Outfeed: case, Paloma 4R with ventouses [{plan} p.1]
""", encoding="utf-8")
    cfg.knowledge_path = str(k)


def test_project_card_for_a_mechanical_caller_shows_only_mechanical_lines(acl_world, tmp_path):
    cfg, tools_for = acl_world
    _thor_card(cfg, tmp_path)
    out = tools_for(MEC).project_card("THOR")
    assert "- Outfeed: case, Paloma 4R with ventouses" in out
    assert "100000" not in out and "flowpack" not in out and "Offre THOR" not in out
    assert "Chocolate bars for ACME Foods." not in out  # the summary needs every source
    assert "built from" not in out and "reviewed" not in out  # no header line
    assert "2 lines not shown with your rights." in out
    full = tools_for(SALES, MEC).project_card("THOR")
    assert "Written card THOR.md (reviewed, 2026-11-20, built from 2 documents):" in full
    assert "Chocolate bars for ACME Foods." in full and "not shown with your rights" not in full
    for tools in (tools_for(OTHER, admin=True), DocTools(dataclasses.replace(cfg, access="world"))):
        assert "price 100000 CHF" in tools.project_card("THOR")
    nobody = tools_for(OTHER)
    out = nobody.project_card("THOR")
    assert "ACME" not in out and "Paloma" not in out and "ventouses" not in out and "Card" not in out
    assert out.replace("THOR", "NOPE") == nobody.project_card("NOPE")  # as for a name that does not exist


def _card_file(tmp_path, name, text):
    path = tmp_path / "knowledge" / "projects" / name
    path.write_text(text, encoding="utf-8")
    os.utime(path, (time.time() + 5, time.time() + 5))  # mtime resolution: the card is read again


def test_hidden_project_with_a_card_answers_like_a_missing_name(acl_world, tmp_path):
    cfg, tools_for = acl_world
    _thor_card(cfg, tmp_path)
    _card_file(tmp_path, "ORION.md", "no header")
    nobody = tools_for(OTHER)
    for name in ("THOR", "ORION"):  # a card, an unparseable card file
        assert nobody.project_card(name).replace(name, "NOPE") == nobody.project_card("NOPE"), name
    _mec_reads_only_thor(cfg)
    orion = (tmp_path / "knowledge" / "projects" / "THOR.md").read_text(encoding="utf-8").replace(
        "project: THOR", "project: ORION").replace("Outfeed: case", "Outfeed: crate")
    _card_file(tmp_path, "ORION.md", orion)  # ORION's card citing THOR's plan, which MEC reads
    mec = tools_for(MEC)
    assert mec.project_card("ORION").replace("ORION", "NOPE") == mec.project_card("NOPE")
    assert "ORION" not in mec.catalog() and "crate" not in mec.catalog()
    assert "Outfeed: crate" in tools_for(OTHER, admin=True).project_card("ORION")


def test_card_without_any_citation_is_for_those_who_read_everything(acl_world, tmp_path):
    cfg, tools_for = acl_world
    _thor_card(cfg, tmp_path)
    text = (tmp_path / "knowledge" / "projects" / "THOR.md").read_text(encoding="utf-8")
    _card_file(tmp_path, "THOR.md", text.split("## A.")[0])  # header and summary only
    mec = tools_for(MEC)
    assert "Card not available with your rights." in mec.project_card("THOR")
    assert "Chocolate" not in mec.project_card("THOR") and "THOR ·" not in mec.catalog()
    assert "Cards exist for 0 of 2 projects" in mec.catalog()
    admin = tools_for(OTHER, admin=True)
    assert "Chocolate bars for ACME Foods." in admin.project_card("THOR") and "THOR ·" in admin.catalog()


def test_card_citations_that_are_neither_paths_nor_notes_are_hidden(acl_world, tmp_path):
    cfg, tools_for = acl_world
    _thor_card(cfg, tmp_path)
    offre = str(Path(cfg.roots[0], "THOR", "0_Vente", "Offre THOR.pdf")).replace("\\", "/")
    plan = str(Path(cfg.roots[0], "THOR", "3_Mecanique", "Plan THOR.pdf")).replace("\\", "/")
    text = (tmp_path / "knowledge" / "projects" / "THOR.md").read_text(encoding="utf-8").split("## A.")[0]
    _card_file(tmp_path, "THOR.md", text + f"""## A. Sources of every kind
- Bare file name [Offre ORION.pdf p.1]
- Forward slashes, sales [{offre} p.1]
- Forward slashes, mechanics [{plan} p.2]
- A correction [user, 2026-01-02]
- From the register [register]
""")
    out = tools_for(MEC).project_card("THOR")
    for shown in ("Forward slashes, mechanics", "A correction", "From the register", "2 lines not shown"):
        assert shown in out, shown
    assert "Bare file name" not in out and "Forward slashes, sales" not in out
    both = tools_for(SALES, MEC).project_card("THOR")
    assert "Forward slashes, sales" in both and "Bare file name" not in both
    assert "1 line not shown with your rights." in both  # nobody can tell where it comes from
    assert "Bare file name" in tools_for(OTHER, admin=True).project_card("THOR")


def test_catalog_shows_no_header_detail_from_a_hidden_line(acl_world, tmp_path):
    cfg, tools_for = acl_world
    _thor_card(cfg, tmp_path)
    path = tmp_path / "knowledge" / "projects" / "THOR.md"
    _card_file(tmp_path, "THOR.md", path.read_text(encoding="utf-8").replace(
        "- Outfeed: case, Paloma 4R with ventouses", "- Outfeed: case, Paloma 4R with ventouses, fed in flow-pack"))
    mec, both = tools_for(MEC), tools_for(SALES, MEC)
    out = mec.catalog(infeed="flowpack")  # backed by the visible line; "chocolate bar" only by a hidden one
    assert "THOR · " in out and "flowpack -> case" in out and "chocolate" not in out
    assert "THOR" not in mec.catalog(infeed="chocolate")
    assert "flowpack (chocolate bar)" in both.catalog() and "THOR ·" in both.catalog(infeed="chocolate")


def test_catalog_matches_only_on_visible_lines(acl_world, tmp_path):
    cfg, tools_for = acl_world
    _thor_card(cfg, tmp_path)
    mec, both = tools_for(MEC), tools_for(SALES, MEC)
    assert "THOR" not in mec.catalog(infeed="flowpack")  # backed only by a sales line
    assert "THOR ·" in both.catalog(infeed="flowpack")
    assert "THOR ·" in mec.catalog(end_effector="ventouses") and "THOR ·" in mec.catalog(outfeed="carton")
    for kwargs in ({"client": "ACME"}, {"text": "100000"}, {"text": "Chocolate"}, {"status": "reviewed"}):
        assert "THOR" not in mec.catalog(**kwargs), kwargs
        assert "THOR ·" in both.catalog(**kwargs), kwargs
    assert "THOR ·" in mec.catalog(text="ventouses") and "THOR ·" in mec.catalog(machine="Paloma")
    line = next(x for x in mec.catalog().splitlines() if x.startswith("THOR ·"))
    assert "Paloma 4R" in line and "case" in line and "suction cups" in line
    assert "flowpack" not in line and "ACME" not in line and "120" not in line and "reviewed" not in line
    full = next(x for x in both.catalog().splitlines() if x.startswith("THOR ·"))
    assert "flowpack" in full and "ACME Foods" in full and full.endswith("reviewed")
    assert "Cards exist for 1 of 2 projects" in mec.catalog()
    nobody = tools_for(OTHER).catalog()
    assert "THOR" not in nobody and "Cards exist for 0 of 0 projects" in nobody
    world = DocTools(dataclasses.replace(cfg, access="world"))
    assert "Cards exist for 1 of 2 projects" in world.catalog()
    assert "Cards exist for 1 of 2 projects" in tools_for(OTHER, admin=True).catalog()


def test_catalog_does_not_name_an_unreadable_card_of_a_hidden_project(acl_world, tmp_path):
    cfg, tools_for = acl_world
    _thor_card(cfg, tmp_path)
    (tmp_path / "knowledge" / "projects" / "ORION.md").write_text("no header", encoding="utf-8")
    assert "card ORION.md unreadable" in tools_for(MEC).catalog()
    assert "ORION" not in tools_for(OTHER).catalog()
    assert "unreadable" not in tools_for(OTHER).project_card("ORION")
    assert "unreadable: no header" not in tools_for(MEC).catalog()  # the reason may quote the card
    assert "card ORION.md unreadable: no header" in tools_for(OTHER, admin=True).catalog()


def _mec_reads_only_thor(cfg):
    """New rules: MEC reads THOR's mechanics only (ORION's mechanics are for SALES)."""
    folder_rules = {key(cfg, "THOR", "0_Vente"): 1, key(cfg, "THOR", "3_Mecanique"): 2,
                    key(cfg, "ORION", "0_Vente"): 1, key(cfg, "ORION", "3_Mecanique"): 1}
    AccessStore.write(cfg.access_path, rules={"sales": 1, "mec": 2}, folder_rules=folder_rules,
                      rule_sets={1: (frozenset({SALES}), frozenset()), 2: (frozenset({MEC}), frozenset())},
                      errors={}, names={}, version="v-thor-only")


def _register_world(cfg, tmp_path):
    """MEC reads THOR's mechanics only (ORION's mechanics are for SALES); the register lists THOR,
    ORION and GHOST (no indexed documents), all for the same client group."""
    from test_register import write_register
    _mec_reads_only_thor(cfg)
    cfg.register_path = str(tmp_path / "register.csv")
    write_register(cfg.register_path, [
        dict(project="THOR", serial="100700001", model="Paloma 4R", year="2019", client="ACME Foods",
             group="ACME", country="France"),
        dict(project="ORION", serial="100700002", model="Presto 2R", year="2015", client="ACME Snacks",
             group="ACME", country="Spain"),
        dict(project="GHOST", serial="100700003", model="Hector", year="2001", client="ACME Old",
             group="ACME", country="Italy"),
    ])


def test_register_shown_only_with_a_readable_document_of_the_project(acl_world, tmp_path):
    cfg, tools_for = acl_world
    _register_world(cfg, tmp_path)
    mec = tools_for(MEC)
    orion = mec.project_card("ORION")
    assert orion.startswith("No project 'ORION'")
    for secret in ("ACME Snacks", "Presto 2R", "Spain", "100700002"):
        assert secret not in orion
    assert "ORION" not in mec.project_card("ORIO")  # not even among the similar names
    thor = mec.project_card("THOR")
    assert "ACME Foods" in thor and "Paloma 4R" in thor
    for secret in ("ORION", "GHOST", "ACME Snacks", "ACME Old"):
        assert secret not in thor, secret
    ghost = mec.project_card("GHOST")
    assert "ACME Old" not in ghost and "Hector" not in ghost
    projects = mec.list_projects(client="ACME")
    assert "THOR" in projects and "ORION" not in projects and "GHOST" not in projects
    sales = tools_for(SALES).project_card("ORION")
    assert "ACME Snacks" in sales and "Presto 2R" in sales
    admin = tools_for(OTHER, admin=True)
    thor = admin.project_card("THOR")
    assert "ORION (Presto 2R, 2015)" in thor and "GHOST (Hector, 2001, not indexed)" in thor
    assert "GHOST" in admin.list_projects(client="ACME") and "ACME Old" in admin.project_card("GHOST")
    world = DocTools(dataclasses.replace(cfg, access="world"))
    assert "ACME Snacks" in world.project_card("ORION") and "GHOST" in world.list_projects(client="ACME")


def test_register_links_made_from_hidden_documents_are_not_used(acl_world, tmp_path):
    """Register rows linked to a project by order numbers found in its sales documents (hidden from
    MEC): MEC gets the register's own links. 100700009 is filed under GHOST (not indexed) and named
    only in THOR's sales; 100700008 is filed under THOR and named only in ORION's sales."""
    from test_register import write_register
    cfg, tools_for = acl_world
    for p, name in (("THOR", "Commande 100700009.pdf"), ("ORION", "Commande 100700008.pdf"),
                    ("ORION", "Facture 100700008.pdf")):
        make_pdf(str(Path(cfg.roots[0], p, "0_Vente", name)), ["Commande machine"])
    cfg.register_path = str(tmp_path / "register.csv")
    write_register(cfg.register_path, [
        dict(project="THOR", serial="100700001", model="Paloma 4R", year="2019", client="ACME Foods"),
        dict(project="GHOST", serial="100700009", model="Hector", year="2001", client="ACME Hidden"),
        dict(project="THOR", serial="100700008", model="Delfi", year="2018", client="ACME Foods"),
    ])
    run_index(cfg, progress=quiet)
    world = DocTools(dataclasses.replace(cfg, access="world"))
    for tools in (world, tools_for(OTHER, admin=True)):
        thor = tools.project_card("THOR")
        assert "100700009" in thor and "ACME Hidden" in thor and "100700008 Delfi -> ORION" in thor
        assert "100700008" in tools.project_card("ORION")
        assert "THOR" in tools.list_projects(client="ACME Hidden")
    mec = tools_for(MEC)
    thor = mec.project_card("THOR")
    assert "100700009" not in thor and "ACME Hidden" not in thor and "Hector" not in thor
    assert "100700008 · Delfi" in thor and "ORION" not in thor  # where the register files it
    assert "100700008" not in mec.project_card("ORION") and "Delfi" not in mec.project_card("ORION")
    assert mec.list_projects(client="ACME Hidden").startswith("No matching project")
    assert "ACME Hidden" not in mec.list_projects()
    hits = mec.search("préhenseur", client="ACME Hidden", mode="keyword")
    assert "Plan THOR" not in hits
    assert "Plan THOR" in world.search("préhenseur", client="ACME Hidden", mode="keyword")


def test_list_projects_filters_keep_the_readable_clause(acl_world):
    cfg, tools_for = acl_world
    other, mec = tools_for(OTHER), tools_for(MEC)
    for kwargs in ({}, {"collection": "%"}, {"collection": ""}, {"name": "THOR"}, {"name": "%"},
                   {"machine": "Paloma"}, {"collection": "%", "name": "O"}):
        assert other.list_projects(**kwargs).startswith("No matching project"), kwargs
    for kwargs in ({}, {"collection": "%"}, {"name": "THOR"}):
        out = mec.list_projects(**kwargs)
        assert "THOR" in out and "| 1 docs |" in out and "| 2 docs |" not in out, kwargs


def test_list_projects_lists_no_project_without_a_readable_document(acl_world, monkeypatch):
    cfg, tools_for = acl_world
    # A slip in the SQL (the readable condition lost) must still fail closed.
    monkeypatch.setattr(DocTools, "_readable_facets", lambda self, con: "")
    assert tools_for(OTHER).list_projects().startswith("No matching project")
    assert "THOR" in tools_for(MEC).list_projects()


def _model_only_in_a_hidden_document(cfg):
    """THOR names Presto 3R in two sales documents (hidden from MEC); ORION names it in its mechanics."""
    for p, sub in (("THOR", "0_Vente"), ("THOR", "0_Vente"), ("ORION", "3_Mecanique")):
        n = len(list(Path(cfg.roots[0], p, sub).glob("*Presto*")))
        make_pdf(str(Path(cfg.roots[0], p, sub, f"Variante {n} Presto 3R.pdf")), ["variante préhenseur"])
    run_index(cfg, progress=quiet)


def test_machine_filter_is_confirmed_among_readable_documents(acl_world):
    cfg, tools_for = acl_world
    _model_only_in_a_hidden_document(cfg)
    mec = tools_for(MEC)
    out = mec.list_projects(machine="Presto 3R")
    assert "ORION" in out and "THOR" not in out
    assert "THOR" not in mec.list_projects(machine="Presto")
    hits = mec.search("préhenseur", machine="Presto 3R", mode="keyword", limit=30)
    assert "ORION" in hits and "THOR" not in hits
    for tools in (tools_for(OTHER, admin=True), DocTools(dataclasses.replace(cfg, access="world"),
                                                         Caller(frozenset(), False))):
        out = tools.list_projects(machine="Presto 3R")
        assert "ORION" in out and "THOR" in out
        hits = tools.search("préhenseur", machine="Presto 3R", mode="keyword", limit=30)
        assert "Plan THOR" in hits and "Plan ORION" in hits
    sales = tools_for(SALES)
    assert "THOR" in sales.list_projects(machine="Presto 3R")
    assert "ORION" not in sales.list_projects(machine="Presto 3R")


def test_hidden_folder_answers_like_a_missing_one(acl_world):
    cfg, tools_for = acl_world
    hidden = str(Path(cfg.roots[0], "THOR", "0_Vente"))
    missing = str(Path(cfg.roots[0], "THOR", "9_Vide"))
    mec = tools_for(MEC)
    got = _answer(mec.list_folder, hidden)
    assert got.startswith("Cannot open folder") and "Offre" not in got
    assert got.replace("0_Vente", "9_Vide") == _answer(mec.list_folder, missing)
    assert _answer(tools_for().list_folder, str(Path(cfg.roots[0], "THOR"))).startswith("Cannot open folder")
    assert "3_Mecanique" in mec.list_folder(str(Path(cfg.roots[0], "THOR")))
    assert "Offre THOR.pdf" in tools_for(SALES).list_folder(hidden)
    assert "Offre THOR.pdf" in tools_for(OTHER, admin=True).list_folder(hidden)
    world = DocTools(dataclasses.replace(cfg, access="world"), Caller(frozenset(), False))
    assert "Offre THOR.pdf" in world.list_folder(hidden)


def test_world_world_search_is_unchanged(acl_world):
    cfg, _ = acl_world
    _embed(cfg)
    world = DocTools(dataclasses.replace(cfg, access="world"), Caller(frozenset(), False))
    con = world._con()
    try:
        assert world.access.row_mask(world.searcher.vectors, con) is None
    finally:
        con.close()
    expected = world.searcher.search(world._con(), "prix", limit=10, allowed=world.cfg.allows)
    out = world.search("prix")
    assert out.count("Offre") >= 2 and "Plan" not in out.split("Offre")[0]
    for h in expected:
        assert h.path in out

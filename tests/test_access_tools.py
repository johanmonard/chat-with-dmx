"""The caller in DocTools, the server and the CLI. Also holds the shared `acl_world` fixture
(two projects, sales and mechanical folders, one reading rule each) used by the access tests."""

import dataclasses
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

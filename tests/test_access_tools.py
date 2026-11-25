"""The caller in DocTools, the server and the CLI. Also holds the shared `acl_world` fixture
(two projects, sales and mechanical folders, one reading rule each) used by the access tests."""

import dataclasses
from pathlib import Path

import pytest

from conftest import make_pdf
from dmx_docs import cli
from dmx_docs.access import KEEP_ALWAYS, AccessStore, Caller, Unrestricted
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

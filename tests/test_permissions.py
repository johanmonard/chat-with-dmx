import os

import pytest
from conftest import make_pdf

from dmx_docs import cli, permissions, store
from dmx_docs.access import AccessStore
from dmx_docs.aclsurvey import Acl
from dmx_docs.indexer import run_index
from dmx_docs.permissions import refresh_permissions, write_change_report

DOMAIN = "S-1-5-21-1"
SALES, ENG, ADMINS = f"{DOMAIN}-1001", f"{DOMAIN}-1002", "S-1-5-32-544"
NAMES = {SALES: "DMX\\Sales", ENG: "DMX\\Engineering"}


def acl(*allow, deny=()):
    return Acl(frozenset(allow), frozenset(deny))


def reader(table):
    """table: folder path -> Acl, or an exception to raise (keys compared like the index does)."""
    def read(path, for_files=True):
        k = store.path_key(path)
        for p, a in table.items():
            if store.path_key(p) == k:
                if isinstance(a, Exception):
                    raise a
                return a
        raise FileNotFoundError(path)
    return read


@pytest.fixture
def world(tmp_path, make_cfg):
    root = tmp_path / "docs"
    (root / "A").mkdir(parents=True)
    (root / "B").mkdir()
    make_pdf(str(root / "A" / "a.pdf"), ["Alpha document about suction cups."])
    make_pdf(str(root / "B" / "b.pdf"), ["Beta document about conveyors."])
    cfg = make_cfg(root, world="projects", access="acl", access_domain_sid=DOMAIN)
    run_index(cfg, progress=lambda *_: None)
    out = tmp_path / "shared" / "access.sqlite3"
    return cfg, root, out


def refresh(cfg, out, table, **kw):
    return refresh_permissions(cfg, [out], read_acl=reader(table), account_name=NAMES.get, workers=2, **kw)


def key(folder):
    return store.path_key(str(folder))


def test_refresh_writes_one_rule_per_folder_and_drops_builtin_sids(world):
    cfg, root, out = world
    r = refresh(cfg, out, {root / "A": acl(SALES, ADMINS), root / "B": acl(ENG)})
    data = AccessStore(out).load()
    assert r.folders == 2 and r.rules == 2
    allow, deny = data.rules[data.folder_rule[key(root / "A")]]
    assert allow == {SALES} and deny == frozenset()
    assert data.rules[data.folder_rule[key(root / "B")]][0] == {ENG}


def test_refresh_writes_every_out_path(world, tmp_path):
    cfg, root, out = world
    local = tmp_path / "local" / "projects" / "access.sqlite3"
    refresh_permissions(cfg, [out, local], read_acl=reader({root / "A": acl(SALES), root / "B": acl(SALES)}),
                        account_name=NAMES.get, workers=1)
    assert AccessStore(out).load().version == AccessStore(local).load().version


def test_unchanged_refresh_keeps_the_version(world):
    cfg, root, out = world
    table = {root / "A": acl(SALES), root / "B": acl(ENG)}
    first = refresh(cfg, out, table)
    second = refresh(cfg, out, table)
    assert second.version == first.version and second.changed == []


def test_changed_rule_bumps_the_version_and_reports_the_folder(world, tmp_path):
    cfg, root, out = world
    first = refresh(cfg, out, {root / "A": acl(SALES), root / "B": acl(ENG)})
    second = refresh(cfg, out, {root / "A": acl(SALES, ENG), root / "B": acl(ENG)})
    assert second.version != first.version
    assert [c[0] for c in second.changed] == [str(root / "A")] and second.docs_affected == 1
    report = write_change_report(second, tmp_path / "reports").read_text(encoding="utf-8")
    assert str(root / "A") in report and "1 documents" in report


def test_unreadable_folder_keeps_its_previous_rule(world):
    cfg, root, out = world
    refresh(cfg, out, {root / "A": acl(SALES), root / "B": acl(ENG)})
    before = AccessStore(out).load()
    (root / "C").mkdir()
    make_pdf(str(root / "C" / "c.pdf"), ["Gamma document."])
    run_index(cfg, progress=lambda *_: None)
    r = refresh(cfg, out, {root / "A": PermissionError("denied"), root / "B": acl(ENG),
                           root / "C": PermissionError("denied")})
    data = AccessStore(out).load()
    assert {p for p, _ in r.unreadable} == {str(root / "A"), str(root / "C")}
    assert data.rules[data.folder_rule[key(root / "A")]] == before.rules[before.folder_rule[key(root / "A")]]
    assert key(root / "C") not in data.folder_rule


def test_dropped_domains_and_unreadable_sids(world):
    cfg, root, out = world
    refresh(cfg, out, {root / "A": acl(SALES, "S-1-5-21-9-500", "S-1-3-0", "S-1-1-0"), root / "B": acl(ENG)})
    data = AccessStore(out).load()
    assert data.rules[data.folder_rule[key(root / "A")]][0] == {SALES, "S-1-1-0"}


def test_write_is_retried_when_the_target_is_held(world, monkeypatch):
    cfg, root, out = world
    monkeypatch.setattr(permissions, "WRITE_DELAY_SECONDS", 0)
    real, calls = AccessStore.write, []

    def flaky(path, *a, **kw):
        calls.append(path)
        if len(calls) < 3:
            raise PermissionError("in use")
        return real(path, *a, **kw)

    monkeypatch.setattr(AccessStore, "write", staticmethod(flaky))
    refresh(cfg, out, {root / "A": acl(SALES), root / "B": acl(SALES)})
    assert len(calls) == 3 and AccessStore(out).load() is not None


def test_cli_refuses_a_world_without_acl_access(tmp_path, capsys):
    cfgfile = tmp_path / "config.toml"
    cfgfile.write_text(f"[index]\ndata_dir = '{(tmp_path / 'data').as_posix()}'\n\n"
                       "[worlds.projects]\nprofile = \"projects\"\n", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        cli.main(["--config", str(cfgfile), "--world", "projects", "permissions"])
    assert e.value.code == 2
    assert "World 'projects' has access = \"world\": nothing to refresh." in capsys.readouterr().out


def _config_file(tmp_path, access):
    cfgfile = tmp_path / "config.toml"
    root = tmp_path / "docs"
    (root / "A").mkdir(parents=True)
    make_pdf(str(root / "A" / "a.pdf"), ["Alpha document about suction cups."])
    cfgfile.write_text(f"[access]\ndomain_sid = '{DOMAIN}'\n\n[index]\ndata_dir = '{(tmp_path / 'data').as_posix()}'\n"
                       f"roots = ['{root.as_posix()}']\n[embeddings]\nmodel = 'hash:256'\nquery_prefix = ''\npassage_prefix = ''\n\n"
                       f"[worlds.projects]\nprofile = \"projects\"\n{access}", encoding="utf-8")
    return cfgfile, root


def _status_line(cfgfile):
    from dmx_docs.config import load_config
    from dmx_docs.tools import DocTools
    return DocTools(load_config(cfgfile, world="projects")).index_status().splitlines()[0]


def test_cli_status_prints_the_index_status(tmp_path, capsys):
    cfgfile, _ = _config_file(tmp_path, "")
    cli.main(["--config", str(cfgfile), "--world", "projects", "index"])
    capsys.readouterr()
    cli.main(["--config", str(cfgfile), "--world", "projects", "status"])
    assert _status_line(cfgfile) in capsys.readouterr().out


def test_cli_permissions_writes_outputs_and_report_without_index_status(tmp_path, capsys, monkeypatch):
    cfgfile, root = _config_file(tmp_path, 'access = "acl"\n')
    cli.main(["--config", str(cfgfile), "--world", "projects", "index"])
    capsys.readouterr()
    real = permissions.refresh_permissions
    monkeypatch.setattr(permissions, "refresh_permissions", lambda cfg, outs, workers=16: real(
        cfg, outs, read_acl=lambda p, for_files=True: acl(SALES), account_name=lambda s: s, workers=workers))
    o1, o2, rep = tmp_path / "m" / "access.sqlite3", tmp_path / "l" / "access.sqlite3", tmp_path / "rep"
    cli.main(["--config", str(cfgfile), "--world", "projects", "permissions", "--out", str(o1), "--out", str(o2),
              "--report-dir", str(rep)])
    out = capsys.readouterr().out
    assert o1.exists() and o2.exists() and list(rep.glob("changes-*.md"))
    assert "Written:" in out and _status_line(cfgfile) not in out

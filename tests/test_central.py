"""serve-all: every world in one HTTP process, each tool call answered with the request's caller.

The NTLM layer is left out (auth=False): a small middleware puts the caller named by the test header
x-test-caller where NtlmAuth puts it (scope["state"]["dmx_caller"])."""

import asyncio
import json
import logging
import re
import threading
import time
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from conftest import make_pdf
from dmx_docs import central, cli
from dmx_docs.access import KEEP_ALWAYS, AccessPool, AccessStore, Caller
from dmx_docs.config import Config, load_config
from dmx_docs.indexer import run_index
from dmx_docs.store import path_key
from dmx_docs.tools import DocTools

SALES = "S-1-5-21-1-2-3-1101"
ENG = "S-1-5-21-1-2-3-1102"
SALES_CALLER = Caller(frozenset({SALES}) | KEEP_ALWAYS, False, "DMX\\sales")
ENG_CALLER = Caller(frozenset({ENG}) | KEEP_ALWAYS, False, "DMX\\eng")
CALLERS = {"sales": SALES_CALLER, "eng": ENG_CALLER}

CONFIG = """\
[index]
data_dir = '{data}'
extensions = ['.pdf']

[embeddings]
model = "hash:256"
query_prefix = ""
passage_prefix = ""

[central]
{central}

[worlds.projects]
profile = "projects"
access = "{projects_access}"
roots = ['{projects}']
register = ""

[worlds.marketing]
profile = "marketing"
access = "acl"
roots = ['{marketing}']
"""


def quiet(*_a, **_k):
    pass


def write_config(base: Path, central_lines: str = "", projects_access: str = "acl") -> Path:
    path = base / "config.toml"
    path.write_text(CONFIG.format(data=(base / "data").as_posix(), central=central_lines,
                                  projects_access=projects_access, projects=(base / "P").as_posix(),
                                  marketing=(base / "M").as_posix()), encoding="utf-8")
    return path


@pytest.fixture
def two_worlds(tmp_path):
    """config.toml with two acl worlds. projects: A\\0_Vente\\Offre A.pdf (readable by SALES) and
    B\\3_Mecanique\\Plan B.pdf (readable by ENG). marketing: Brochures\\Paloma brochure.pdf (both)."""
    docs = {
        "projects": [("A/0_Vente", "Offre A.pdf", "Offre A: prix 100000 CHF palette", 1),
                     ("B/3_Mecanique", "Plan B.pdf", "Plan B: prix du préhenseur palette", 2)],
        "marketing": [("Brochures", "Paloma brochure.pdf", "Paloma brochure: palette robot", 3)],
    }
    roots = {"projects": tmp_path / "P", "marketing": tmp_path / "M"}
    path = write_config(tmp_path)
    for world, files in docs.items():
        folder_rules = {}
        for sub, name, text, rule in files:
            d = roots[world] / sub
            d.mkdir(parents=True)
            make_pdf(str(d / name), [text])
            folder_rules[path_key(str(d))] = rule
        cfg = load_config(path, world=world)
        run_index(cfg, progress=quiet)
        AccessStore.write(cfg.access_path, rules={}, folder_rules=folder_rules,
                          rule_sets={1: (frozenset({SALES}), frozenset()), 2: (frozenset({ENG}), frozenset()),
                                     3: (frozenset({SALES, ENG}), frozenset())},
                          errors={}, names={}, version="v1")
    return path


def inject(app):
    """Stands in for NtlmAuth: the caller named by the x-test-caller header, none without it."""
    async def wrapped(scope, receive, send):
        if scope["type"] == "http":
            state = dict(scope.get("state") or {})
            state.pop("dmx_caller", None)
            name = dict(scope.get("headers") or []).get(b"x-test-caller")
            if name:
                state["dmx_caller"] = CALLERS[name.decode()]
            scope = {**scope, "state": state}
        await app(scope, receive, send)
    return wrapped


def client_for(config_path, worlds=None):
    return TestClient(inject(central.build_app(config_path, worlds, auth=False)))


def rpc(client, world, method, params=None, caller=None):
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json",
               "MCP-Protocol-Version": "2025-06-18"}
    if caller:
        headers["x-test-caller"] = caller
    r = client.post(f"/{world}/mcp", headers=headers,
                    content=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}))
    assert r.status_code == 200, r.text
    body = r.json()
    assert "result" in body, body
    return body["result"]


def call(client, world, tool, args, caller=None):
    """(is_error, text) of one tools/call."""
    res = rpc(client, world, "tools/call", {"name": tool, "arguments": args}, caller)
    return bool(res.get("isError")), "\n".join(c.get("text", "") for c in res["content"])


def _sees(text):
    return {n for n in ("Offre A", "Plan B", "Paloma brochure") if n in text}


def test_each_world_answers_at_its_own_path(two_worlds):
    with client_for(two_worlds) as client:
        projects = {t["name"] for t in rpc(client, "projects", "tools/list", caller="sales")["tools"]}
        marketing = {t["name"] for t in rpc(client, "marketing", "tools/list", caller="sales")["tools"]}
    assert {"search", "list_projects", "project_card", "catalog", "index_status"} <= projects
    assert "search" in marketing and "list_projects" not in marketing


def test_a_search_is_answered_with_the_injected_caller(two_worlds):
    with client_for(two_worlds) as client:
        err, sales = call(client, "projects", "search", {"query": "prix palette", "mode": "keyword"}, "sales")
        assert not err and _sees(sales) == {"Offre A"}
        err, eng = call(client, "projects", "search", {"query": "prix palette", "mode": "keyword"}, "eng")
        assert not err and _sees(eng) == {"Plan B"}
        err, brochure = call(client, "marketing", "search", {"query": "palette", "mode": "keyword"}, "eng")
        assert not err and _sees(brochure) == {"Paloma brochure"}


def test_no_caller_is_refused(two_worlds):
    with client_for(two_worlds) as client:
        err, text = call(client, "projects", "search", {"query": "prix", "mode": "keyword"})
    assert err and "Not signed in." in text and "Offre" not in text


def test_a_world_without_acl_is_refused_at_start(tmp_path):
    path = write_config(tmp_path, projects_access="world")
    with pytest.raises(ValueError, match="projects"):
        central.build_app(path, auth=False)


def test_busy_when_all_workers_are_taken(two_worlds, monkeypatch):
    write_config(two_worlds.parent, "workers = 1\nbusy_seconds = 0.2")
    entered, release = threading.Event(), threading.Event()
    real_search = DocTools.search

    def blocked_search(self, *a, **k):
        entered.set()
        assert release.wait(10)
        return real_search(self, *a, **k)

    monkeypatch.setattr(DocTools, "search", blocked_search)
    with client_for(two_worlds) as client:
        first: list = []
        t = threading.Thread(target=lambda: first.append(
            call(client, "projects", "search", {"query": "prix", "mode": "keyword"}, "sales")))
        t.start()
        try:
            assert entered.wait(10)
            # the event loop is free while the worker runs the blocked call
            assert client.get("/_whoami", headers={"x-test-caller": "eng"}).status_code == 200
            started = time.monotonic()
            err, text = call(client, "marketing", "list_folder", {}, "eng")   # another world, the same pool
            waited = time.monotonic() - started
        finally:
            release.set()
            t.join(10)
        assert err and "The server is busy, try again in a minute." in text
        assert 0.2 <= waited < 5
        assert first and not first[0][0] and _sees(first[0][1]) == {"Offre A"}
        err, text = call(client, "marketing", "list_folder", {}, "eng")     # the worker is free again
        assert not err


def test_whoami_reports_the_caller(two_worlds):
    with client_for(two_worlds) as client:
        r = client.get("/_whoami", headers={"x-test-caller": "sales"})
        assert r.status_code == 200
        body = r.json()
        assert (body["caller"], body["admin"], body["groups"]) == ("DMX\\sales", False, len(SALES_CALLER.sids))
        assert body["not_served"] == {}
        assert client.get("/_whoami").status_code == 401


def test_log_line_has_world_tool_caller_and_no_arguments(two_worlds, caplog):
    caplog.set_level(logging.INFO, logger="dmx_docs.central")
    with client_for(two_worlds) as client:
        call(client, "projects", "search", {"query": "secretword prix", "mode": "keyword"}, "sales")
        call(client, "marketing", "search", {"query": "secretword", "mode": "keyword"}, "eng")
    lines = [r.getMessage() for r in caplog.records if r.name == "dmx_docs.central" and "search" in r.getMessage()]
    assert len(lines) == 2
    assert lines[0].startswith("projects search DMX\\sales ") and re.search(r"\d+\.\d+ s ok$", lines[0])
    assert lines[1].startswith("marketing search DMX\\eng ")   # the tool's name, not marketing_search
    assert not any("secretword" in r.getMessage() or "keyword" in r.getMessage() for r in caplog.records)


def test_tool_schemas_are_unchanged_in_central_mode(tmp_path):
    from mcp import Client

    from dmx_docs.server import build_server

    async def listed(server):
        async with Client(server) as client:
            return [t.model_dump() for t in (await client.list_tools()).tools]

    for profile in ("projects", "marketing", "documentation", "offers", "engineering", "none"):
        cfg = Config(roots=[str(tmp_path)], data_dir=tmp_path / "data", world=profile, world_title=profile,
                     profile=profile, embedding_model="hash:256", access="acl")
        local = asyncio.run(listed(build_server(cfg, Caller(frozenset()))))
        remote = asyncio.run(listed(build_server(cfg, central=True)))
        assert local == remote, profile
        assert all("ctx" not in json.dumps(t["input_schema"] if "input_schema" in t else t["inputSchema"])
                   for t in remote)


def test_a_central_call_without_http_request_is_not_signed_in(two_worlds):
    from mcp import Client

    from dmx_docs.server import build_server

    async def run():
        async with Client(build_server(load_config(two_worlds, world="projects"), central=True)) as client:
            return await client.call_tool("search", {"query": "prix", "mode": "keyword"})

    res = asyncio.run(run())
    assert res.is_error if hasattr(res, "is_error") else res.isError
    assert "Not signed in." in res.content[0].text


def test_the_central_fixed_caller_reads_nothing(two_worlds):
    world = central.World(load_config(two_worlds, world="projects"))
    assert world.tools.access.caller.sids == frozenset() and not world.tools.access.caller.admin
    assert _sees(world.tools.search("prix palette", mode="keyword")) == set()


def test_access_is_taken_from_the_pool_once_per_call(two_worlds, monkeypatch):
    calls = []
    real = AccessPool.for_caller
    monkeypatch.setattr(AccessPool, "for_caller", lambda self, c: calls.append(c.label) or real(self, c))
    with client_for(two_worlds) as client:
        call(client, "projects", "search", {"query": "prix palette", "mode": "keyword"}, "sales")
        call(client, "projects", "list_projects", {}, "eng")
    assert calls == ["DMX\\sales", "DMX\\eng"]


def test_central_settings_reach_the_pools_and_the_sign_in(two_worlds):
    write_config(two_worlds.parent, "workers = 2\nbusy_seconds = 7\ncaller_cache_seconds = 42")
    cfg = load_config(two_worlds, world="projects")
    assert (cfg.central_port, cfg.central_workers, cfg.central_busy_seconds,
            cfg.central_caller_cache_seconds) == (8765, 2, 7, 42)
    assert central.World(cfg).tools._pool.cache_seconds == 42
    app = central.build_app(two_worlds, auth=True)
    ntlm = [m for m in app.user_middleware if m.cls is central.NtlmAuth]
    assert len(ntlm) == 1 and ntlm[0].kwargs["cache_seconds"] == 42


def test_central_defaults(tmp_path):
    cfg = load_config(write_config(tmp_path), world="projects")
    assert (cfg.central_port, cfg.central_workers, cfg.central_busy_seconds,
            cfg.central_caller_cache_seconds, cfg.central_certfile, cfg.central_keyfile) == (
        8765, 4, 30, 300, None, None)


def test_a_world_without_enough_memory_is_not_served(two_worlds, monkeypatch):
    gb = 1024 ** 3
    monkeypatch.setattr(central, "free_memory_bytes", lambda: 2 * gb)
    monkeypatch.setattr(central, "vector_file_bytes", lambda cfg: 5 * gb if cfg.world == "marketing" else 0)
    with client_for(two_worlds) as client:
        assert client.get("/_whoami", headers={"x-test-caller": "sales"}).json()["not_served"] == {
            "marketing": "not enough memory"}
        err, status = call(client, "projects", "index_status", {}, "sales")
        assert not err and "marketing: not served: not enough memory" in status
        err, text = call(client, "marketing", "search", {"query": "palette", "mode": "keyword"}, "sales")
        assert err and "marketing: not served: not enough memory" in text and "Paloma" not in text
        err, text = call(client, "projects", "search", {"query": "prix", "mode": "keyword"}, "sales")
        assert not err and _sees(text) == {"Offre A"}


def test_vector_file_bytes_counts_the_current_vector_files(two_worlds):
    cfg = load_config(two_worlds, world="projects")
    world = central.World(cfg)
    con = world.tools._con()
    try:
        world.tools.searcher.vectors.ensure(con)   # writes this version's cache files
    finally:
        con.close()
    files = list(cfg.world_dir.glob("vec_*.npy"))
    assert files and central.vector_file_bytes(cfg) == sum(f.stat().st_size for f in files)


def test_only_the_chosen_worlds_are_served(two_worlds):
    with client_for(two_worlds, ["marketing"]) as client:
        assert client.post("/projects/mcp", json={}).status_code == 404
        assert not call(client, "marketing", "list_folder", {}, "sales")[0]


def test_cli_serve_all_runs_uvicorn_without_proxy_headers(two_worlds, monkeypatch, caplog):
    import uvicorn

    runs = []
    monkeypatch.setattr(central, "build_app", lambda path, worlds=None, auth=True: ("app", path, worlds, auth))
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: runs.append((app, kw)))
    caplog.set_level(logging.WARNING)
    cli.main(["--config", str(two_worlds), "serve-all", "--worlds", "projects,marketing"])
    (app, kw), = runs
    assert app[2] == ["projects", "marketing"] and app[3] is True
    assert kw["proxy_headers"] is False and kw["port"] == 8765 and kw["host"] == "0.0.0.0"
    assert "ssl_certfile" not in kw
    assert "HTTP without a certificate: logins cross the LAN unencrypted." in caplog.text


def test_cli_serve_all_serves_https_with_a_certificate(two_worlds, monkeypatch, caplog):
    import uvicorn

    write_config(two_worlds.parent, "port = 9443\ncertfile = 'vm.crt'\nkeyfile = 'vm.key'")
    runs = []
    monkeypatch.setattr(central, "build_app", lambda path, worlds=None, auth=True: "app")
    monkeypatch.setattr(uvicorn, "run", lambda app, **kw: runs.append(kw))
    caplog.set_level(logging.WARNING)
    cli.main(["--config", str(two_worlds), "serve-all", "--host", "127.0.0.1"])
    kw, = runs
    assert kw["ssl_certfile"] == str(two_worlds.parent / "vm.crt")
    assert kw["ssl_keyfile"] == str(two_worlds.parent / "vm.key")
    assert kw["port"] == 9443 and kw["host"] == "127.0.0.1" and kw["proxy_headers"] is False
    assert "HTTP without a certificate" not in caplog.text

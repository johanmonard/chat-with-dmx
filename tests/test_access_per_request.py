"""One DocTools serving many people: each request is answered with its own caller (CURRENT_CALLER),
never with another request's results, cached counts or "To verify" list."""

import contextvars
import dataclasses
import json
import sys
import threading

import pytest

from dmx_docs.access import CURRENT_CALLER, KEEP_ALWAYS, Access, AccessPool, AccessStore, Caller, Unrestricted
from dmx_docs.tools import DocTools
from test_access_tools import MEC, OTHER, SALES, _embed, acl_world, key  # noqa: F401  (fixture)
from test_offer_tools import (_register_submission, _thor_db, _to_verify_lines, _tools_index,  # noqa: F401
                              offers_world_linked, verifying)

SALES_CALLER = Caller(frozenset({SALES}) | KEEP_ALWAYS, False, "DMX\\sales")
MEC_CALLER = Caller(frozenset({MEC}) | KEEP_ALWAYS, False, "DMX\\mec")
NOBODY = Caller(frozenset(KEEP_ALWAYS), False, "nobody")   # the fixed caller of a shared server: reads nothing


def as_caller(caller, fn, *args, **kwargs):
    """fn(*args, **kwargs) inside a request of `caller`, as the server runs each tool call."""
    def run():
        CURRENT_CALLER.set(caller)
        return fn(*args, **kwargs)
    return contextvars.copy_context().run(run)


def _sees(out: str) -> set[str]:
    return {name for name in ("Offre THOR", "Offre ORION", "Plan THOR", "Plan ORION") if name in out}


SALES_DOCS = {"Offre THOR", "Offre ORION"}
MEC_DOCS = {"Plan THOR", "Plan ORION"}


def test_one_doctools_answers_each_request_with_its_caller(acl_world):
    cfg, _ = acl_world
    tools = DocTools(cfg, NOBODY)
    assert _sees(tools.search("THOR ORION", mode="keyword")) == set()
    offre, plan = key(cfg, "THOR", "0_Vente", "Offre THOR.pdf"), key(cfg, "THOR", "3_Mecanique", "Plan THOR.pdf")

    token = CURRENT_CALLER.set(SALES_CALLER)
    try:
        assert _sees(tools.search("THOR ORION", mode="keyword")) == SALES_DOCS
        assert tools.allows(offre) and not tools.allows(plan)
        sales_map = tools.world_map()
    finally:
        CURRENT_CALLER.reset(token)
    token = CURRENT_CALLER.set(MEC_CALLER)   # the same thread, the next request
    try:
        assert _sees(tools.search("THOR ORION", mode="keyword")) == MEC_DOCS
        assert tools.allows(plan) and not tools.allows(offre)
        mec_map = tools.world_map()       # counted for MEC, not the overview cached for SALES
    finally:
        CURRENT_CALLER.reset(token)
    assert sales_map == DocTools(cfg, SALES_CALLER).world_map()
    assert mec_map == DocTools(cfg, MEC_CALLER).world_map()
    assert sales_map != mec_map


def test_interleaved_callers_in_threads_do_not_mix(acl_world):
    cfg, _ = acl_world
    _embed(cfg)
    tools = DocTools(cfg, NOBODY)
    start = threading.Barrier(2)
    wrong: list = []

    def worker(first: int):
        start.wait()
        for i in range(20):
            caller, want = (SALES_CALLER, SALES_DOCS) if (i + first) % 2 == 0 else (MEC_CALLER, MEC_DOCS)
            got = _sees(as_caller(caller, tools.search, "THOR ORION prix préhenseur", mode="hybrid"))
            if got != want:
                wrong.append((caller.label, i, got))

    threads = [threading.Thread(target=worker, args=(n,)) for n in (0, 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert wrong == []


def test_local_mode_without_a_request_caller_is_unchanged(acl_world):
    cfg, tools_for = acl_world
    tools = tools_for(SALES)
    assert CURRENT_CALLER.get() is None
    assert isinstance(tools.access, Access) and tools.access is tools.access
    assert tools.access.caller.sids == frozenset({SALES}) | KEEP_ALWAYS
    assert _sees(tools.search("THOR ORION", mode="keyword")) == SALES_DOCS
    assert _sees(as_caller(MEC_CALLER, tools.search, "THOR ORION", mode="keyword")) == MEC_DOCS
    assert _sees(tools.search("THOR ORION", mode="keyword")) == SALES_DOCS   # a request leaves no trace


def test_a_world_world_is_unrestricted_for_request_callers(acl_world):
    cfg, _ = acl_world
    tools = DocTools(dataclasses.replace(cfg, access="world"), NOBODY)
    assert isinstance(as_caller(MEC_CALLER, lambda: tools.access), Unrestricted)
    assert _sees(as_caller(NOBODY, tools.search, "THOR ORION", mode="keyword")) == SALES_DOCS | MEC_DOCS


def test_a_request_caller_without_access_data_reads_nothing(acl_world):
    cfg, _ = acl_world
    cfg.access_path.unlink()
    tools = DocTools(cfg, NOBODY)
    assert _sees(as_caller(SALES_CALLER, tools.search, "THOR ORION", mode="keyword")) == set()
    admin = Caller(frozenset({OTHER}) | KEEP_ALWAYS, True, "DMX\\admin")
    assert _sees(as_caller(admin, tools.search, "THOR ORION", mode="keyword")) == SALES_DOCS | MEC_DOCS


# --- AccessPool ---------------------------------------------------------------------------------------

def test_pool_keeps_one_access_per_caller(acl_world):
    cfg, _ = acl_world
    pool = AccessPool(AccessStore(cfg.access_path))
    sales = pool.for_caller(SALES_CALLER)
    assert sales.caller == SALES_CALLER
    assert pool.for_caller(Caller(SALES_CALLER.sids, False, "another label")) is sales   # keyed by sids, admin
    assert pool.for_caller(MEC_CALLER) is not sales
    assert pool.for_caller(Caller(SALES_CALLER.sids, True)) is not sales


def test_pool_drops_unused_entries_and_holds_at_most_64(acl_world):
    cfg, _ = acl_world
    now = [0.0]
    pool = AccessPool(AccessStore(cfg.access_path), cache_seconds=300, clock=lambda: now[0])
    sales = pool.for_caller(SALES_CALLER)
    now[0] = 299.0
    assert pool.for_caller(SALES_CALLER) is sales
    now[0] = 299.0 + 301
    assert pool.for_caller(SALES_CALLER) is not sales      # unused for longer than cache_seconds
    first = pool.for_caller(Caller(frozenset({"S-1-5-21-1-2-3-5000"})))
    for n in range(64):
        pool.for_caller(Caller(frozenset({f"S-1-5-21-1-2-3-{6000 + n}"})))
    assert len(pool._entries) == 64
    assert pool.for_caller(Caller(frozenset({"S-1-5-21-1-2-3-5000"}))) is not first   # the oldest was dropped


@pytest.mark.skipif(sys.platform != "win32", reason="windows only")
def test_from_token_is_from_token_handle_of_the_process_token():
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    assert Caller.from_token_handle(token, []) == Caller.from_token([])


# --- offers world: "To verify" and offer_verify -------------------------------------------------------

def test_to_verify_list_is_per_caller(verifying):
    cfg, con, tools, shared = verifying
    first = _to_verify_lines(as_caller(SALES_CALLER, tools.offer_lines, machine="Paloma"))
    assert first
    again = _to_verify_lines(as_caller(SALES_CALLER, tools.offer_lines, machine="Paloma"))
    assert set(first).isdisjoint(again)                     # listed to SALES a moment ago
    assert _to_verify_lines(as_caller(MEC_CALLER, tools.offer_lines, machine="Paloma")) == first
    assert _to_verify_lines(tools.offer_lines(machine="Paloma")) == first   # local mode: its own list


def test_to_verify_lists_are_kept_for_a_bounded_number_of_callers(acl_world, monkeypatch):
    from dmx_docs import tools as tools_module
    cfg, _ = acl_world
    monkeypatch.setattr(tools_module, "LISTED_CALLERS", 2)
    tools = DocTools(cfg, NOBODY)
    sales = as_caller(SALES_CALLER, tools._to_verify)["listed"]
    assert as_caller(SALES_CALLER, tools._to_verify)["listed"] is sales
    as_caller(MEC_CALLER, tools._to_verify)
    as_caller(NOBODY, tools._to_verify)
    assert list(tools._listed) == [MEC_CALLER.sids, NOBODY.sids]   # SALES, the oldest, was dropped


def test_offer_verify_records_the_request_caller(verifying, monkeypatch):
    cfg, con, tools, shared = verifying
    monkeypatch.setenv("USERNAME", "local-user")
    out = as_caller(SALES_CALLER, tools.offer_verify, **_register_submission(con))
    assert out.startswith("Verified:")
    out = tools.offer_verify(**_register_submission(con, offer="3001234", revision="01"))
    users = sorted(json.loads(p.read_text(encoding="utf-8"))["user"] for p in shared.glob("*.json"))
    assert users == ["DMX\\sales", "local-user"], out

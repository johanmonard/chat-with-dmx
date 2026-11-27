"""Central server: open_document and export_image never act on the VM; they hand the action to the
relay running on the colleague's PC (a `DMX-RELAY {json}` marker). Local mode is unchanged."""

import base64
import contextvars
import json
import os

import pytest

from dmx_docs import tools as tools_module
from dmx_docs.access import CURRENT_CALLER, KEEP_ALWAYS, AccessStore, Caller
from dmx_docs.extract import pymupdf
from dmx_docs.store import path_key
from dmx_docs.tools import DocTools
from test_central import SALES, ENG, call, client_for, two_worlds  # noqa: F401  (fixture)
from test_offer_tools import _register_submission, offers_world_linked, _tools_index, _thor_db, verifying  # noqa: F401
from test_view_page import tools  # noqa: F401  (fixture: local DocTools on a THOR tree)

MARKER = "DMX-RELAY "


def marker(text: str) -> dict:
    assert text.startswith(MARKER) and "\n" not in text, text
    return json.loads(text[len(MARKER):])


def offre_a(config_path) -> str:
    return str(config_path.parent / "P" / "A" / "0_Vente" / "Offre A.pdf")


@pytest.fixture
def client(two_worlds):
    with client_for(two_worlds) as c:
        yield c


@pytest.fixture
def no_launch(monkeypatch):
    """Any launch of a program is recorded (and not performed)."""
    launched = []
    monkeypatch.setattr(tools_module, "_launch", lambda *a, **k: launched.append((a, k)))
    return launched


# --- central: markers ----------------------------------------------------------------------------

def test_central_export_returns_a_save_marker_with_the_bytes(two_worlds, client, tmp_path, no_launch):
    is_error, text = call(client, "projects", "export_image",
                          {"path": offre_a(two_worlds), "page": 1, "name": "../Mon image"}, caller="sales")
    assert not is_error, text
    m = marker(text)
    assert m["action"] == "save" and m["file"] == "Mon_image.png"
    assert m["what"] == "page 1" and m["source"] == offre_a(two_worlds)
    data = base64.b64decode(m["data"], validate=True)
    assert data[:4] == b"\x89PNG" and pymupdf.Pixmap(data).width > 100
    assert set(m) == {"action", "file", "what", "source", "data"}
    assert not any(tmp_path.rglob("*.png"))  # nothing written on the VM
    assert not no_launch


def test_central_export_default_name_and_region(two_worlds, client):
    _, text = call(client, "projects", "export_image",
                   {"path": offre_a(two_worlds), "region": "0,0,0.5,0.5"}, caller="sales")
    m = marker(text)
    assert m["file"] == "Offre_A_p1_zoom.png" and m["what"] == "page 1, region 0,0,0.5,0.5"


def test_central_open_returns_an_open_marker(two_worlds, client, no_launch):
    is_error, text = call(client, "projects", "open_document", {"path": offre_a(two_worlds), "page": 3},
                          caller="sales")
    assert not is_error, text
    assert marker(text) == {"action": "open", "path": offre_a(two_worlds), "ext": ".pdf", "page": 3}
    _, text = call(client, "projects", "open_document", {"path": offre_a(two_worlds)}, caller="sales")
    assert marker(text)["page"] is None
    assert not no_launch  # no program was started on the VM


def test_central_marker_for_an_unreadable_document_is_the_missing_file_error(two_worlds, client, no_launch):
    plan_b = str(two_worlds.parent / "P" / "B" / "3_Mecanique" / "Plan B.pdf")  # ENG only
    absent = plan_b.replace("Plan B", "Plan Z")
    for tool, extra in (("open_document", {}), ("export_image", {"page": 1})):
        hidden_err, hidden = call(client, "projects", tool, {"path": plan_b, **extra}, caller="sales")
        absent_err, missing = call(client, "projects", tool, {"path": absent, **extra}, caller="sales")
        assert hidden_err and absent_err and not hidden.startswith(MARKER)
        assert hidden == missing  # the central server's own error, the same as for a missing file
        ok_err, ok = call(client, "projects", tool, {"path": plan_b, **extra}, caller="eng")
        assert not ok_err and ok.startswith(MARKER)  # ENG reads it
    assert not no_launch


def test_unreadable_document_raises_the_missing_file_error_before_any_marker(two_worlds):
    from dmx_docs.config import load_config
    t = DocTools(load_config(two_worlds, world="projects"))
    plan_b = str(two_worlds.parent / "P" / "B" / "3_Mecanique" / "Plan B.pdf")
    sales = Caller(frozenset({SALES}) | KEEP_ALWAYS, False, "DMX\\sales")
    for fn, args in ((t.open_document_target, ()), (t.export_image_data, ())):
        with pytest.raises(FileNotFoundError, match="File not reachable now"):
            run_as(sales, fn, plan_b, *args)


# --- defence in depth: no launch, no write when a request caller is set --------------------------------

def run_as(caller, fn, *args, **kwargs):
    def run():
        CURRENT_CALLER.set(caller)
        return fn(*args, **kwargs)
    return contextvars.copy_context().run(run)


CALLER = Caller(frozenset({SALES}) | KEEP_ALWAYS, False, "DMX\\sales")


def test_open_document_refuses_to_launch_for_a_request_caller(tools, no_launch):
    t, root = tools
    pdf = str(root / "THOR" / "Schema.pdf")
    for page in (None, 2):
        with pytest.raises(RuntimeError, match="relay"):
            run_as(CALLER, t.open_document, pdf, page=page)
    with pytest.raises(RuntimeError, match="relay"):
        run_as(CALLER, t.open_document, str(root / "THOR" / "Rapport FAT.docx"))
    assert not no_launch
    assert "Opened" in t.open_document(pdf)  # local mode: launched as before
    assert no_launch


def test_open_document_target_checks_like_open_document(tools):
    t, root = tools
    pdf = str(root / "THOR" / "Schema.pdf")
    assert t.open_document_target(pdf, 2) == (pdf, ".pdf", 2)
    with pytest.raises(ValueError, match="Only"):
        t.open_document_target(str(root / "THOR" / "setup.exe"), None)
    with pytest.raises(FileNotFoundError, match="File not reachable now"):
        t.open_document_target(str(root / "THOR" / "absent.pdf"), None)


def test_local_export_refuses_to_write_for_a_request_caller(tools, tmp_path):
    t, root = tools
    t.cfg.export_dir = str(tmp_path / "exports")
    with pytest.raises(RuntimeError, match="relay"):
        run_as(CALLER, t.export_image, str(root / "THOR" / "Schema.pdf"), page=1)
    assert not (tmp_path / "exports").exists()
    # the data half is read-only and is what the central server uses
    data, ext, default, what = run_as(CALLER, t.export_image_data, str(root / "THOR" / "Schema.pdf"), page=1)
    assert data[:4] == b"\x89PNG" and (ext, default, what) == (".png", "Schema_p1", "page 1")
    assert not (tmp_path / "exports").exists()


def test_local_export_still_writes_the_file(tools, tmp_path):
    t, root = tools
    t.cfg.export_dir = str(tmp_path / "exports")
    out = t.export_image(str(root / "THOR" / "Schema.pdf"), page=2)
    target = tmp_path / "exports" / "Schema_p2.png"
    assert target.read_bytes()[:4] == b"\x89PNG"
    assert out.startswith(f"Saved page 2 of {root / 'THOR' / 'Schema.pdf'} to {target} (")
    assert "in the local export folder" in out


# --- central: offer_verify refuses a revision the caller cannot read ------------------------------------

def _acl_offers(verifying):
    cfg, con, _, shared = verifying
    cfg.access = "acl"
    sub = _register_submission(con)
    folder_key = os.path.dirname(path_key(sub["path"]))
    AccessStore.write(cfg.access_path, rules={}, folder_rules={folder_key: 1},
                      rule_sets={1: (frozenset({SALES}), frozenset())}, errors={}, names={}, version="v1")
    return DocTools(cfg), sub, shared


def test_offer_verify_refuses_a_revision_the_caller_cannot_read(verifying):
    t, sub, shared = _acl_offers(verifying)
    eng = Caller(frozenset({ENG}) | KEEP_ALWAYS, False, "DMX\\eng")
    out = run_as(eng, t.offer_verify, **sub)
    assert out.startswith("Not stored:") and not list(shared.glob("*.json")), out
    out = run_as(Caller(frozenset({SALES}) | KEEP_ALWAYS, False, "DMX\\sales"), t.offer_verify, **sub)
    assert out.startswith("Verified:"), out
    users = [json.loads(p.read_text(encoding="utf-8"))["user"] for p in shared.glob("*.json")]
    assert users == ["DMX\\sales"]

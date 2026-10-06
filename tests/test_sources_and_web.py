import os
import time

import pytest
from fastapi.testclient import TestClient

from dmx_docs import sources, store
from dmx_docs.indexer import run_index
from dmx_docs.tools import DocTools
from dmx_docs.web import create_app

quiet = lambda *a, **k: None  # noqa: E731


def names(cfg):
    con = store.connect(cfg.db_path)
    out = {r["name"] for r in con.execute("SELECT name FROM docs")}
    con.close()
    return out


def test_excluding_a_subfolder(corpus_copy, make_cfg):
    cfg = make_cfg(corpus_copy)
    run_index(cfg, progress=quiet)
    tools = DocTools(cfg)
    assert "maintenance_manual.pdf" in tools.search("suction cups", mode="keyword")

    con = store.connect(cfg.db_path)
    sources.set_excluded(con, str(corpus_copy / "Manuals"), True)
    con.close()
    # hidden from Claude immediately, before any rescan
    assert "maintenance_manual.pdf" not in tools.search("suction cups", mode="keyword")
    assert "Manuals" not in tools.list_folder(str(corpus_copy))
    with pytest.raises(ValueError):
        tools.read_document(str(corpus_copy / "Manuals" / "maintenance_manual.pdf"))
    assert "maintenance_manual.pdf" in names(cfg)

    stats = run_index(cfg, progress=quiet)
    assert stats.removed == 1
    assert "maintenance_manual.pdf" not in names(cfg)
    assert "Spec_cellule.pdf" in names(cfg)

    con = store.connect(cfg.db_path)
    sources.set_excluded(con, str(corpus_copy / "Manuals"), False)
    con.close()
    stats = run_index(cfg, progress=quiet)
    assert stats.new == 1 and "maintenance_manual.pdf" in names(cfg)


def test_root_management(corpus_copy, make_cfg, tmp_path):
    cfg = make_cfg(corpus_copy)
    run_index(cfg, progress=quiet)
    con = store.connect(cfg.db_path)
    with pytest.raises(ValueError, match="already covered"):
        sources.add_root(con, str(corpus_copy / "Projets"))
    with pytest.raises(ValueError, match="not found"):
        sources.add_root(con, str(tmp_path / "nope"))
    with pytest.raises(ValueError, match="cannot be excluded"):
        sources.set_excluded(con, str(corpus_copy), True)
    sources.set_excluded(con, str(corpus_copy / "Projets" / "P1234_Nestle"), True)
    sources.set_excluded(con, str(corpus_copy / "Projets"), True)  # replaces the nested exclusion
    assert [r[0] for r in con.execute("SELECT path FROM excluded_dirs")] == [str(corpus_copy / "Projets")]
    sources.remove_root(con, str(corpus_copy))
    assert con.execute("SELECT count(*) FROM excluded_dirs").fetchone()[0] == 0
    con.close()
    assert "No results" in DocTools(cfg).search("ventouses", mode="keyword")
    stats = run_index(cfg, progress=quiet)
    assert stats.removed > 0 and names(cfg) == set()


@pytest.fixture
def client(corpus_copy, make_cfg):
    cfg = make_cfg(None, roots=[])
    app = create_app(cfg, allowed_hosts={"testserver"})
    c = TestClient(app)
    c.headers["X-Dmx"] = "1"
    return c, corpus_copy


def wait_job(c):
    for _ in range(300):
        job = c.get("/api/state").json()["job"]
        if job and job["state"] != "running":
            return job
        time.sleep(0.1)
    raise AssertionError("scan did not finish")


def test_web_flow(client):
    c, root = client
    assert "dmx-docs" in c.get("/").text
    st = c.get("/api/state").json()
    assert st["roots"] == [] and "mcpServers" in st["claude_desktop"]

    r = c.post("/api/roots", json={"path": str(root)})
    assert r.status_code == 200
    assert c.post("/api/roots", json={"path": str(root / "Manuals")}).status_code == 400

    tree = c.get("/api/browse", params={"path": str(root)}).json()
    assert {d["name"] for d in tree["dirs"]} >= {"Projets", "Manuals", "Scans"}
    assert c.post("/api/exclude", json={"path": str(root / "Manuals"), "excluded": True}).json()["ok"]
    tree = c.get("/api/browse", params={"path": str(root)}).json()
    assert next(d for d in tree["dirs"] if d["name"] == "Manuals")["excluded"]

    assert c.get("/api/state").json()["needs_rescan"]
    c.post("/api/scan", json={"embed": True})
    job = wait_job(c)
    assert job["state"] == "done", job["lines"]
    st = c.get("/api/state").json()
    assert not st["needs_rescan"]
    expected = 3 if (root / "Old").exists() else 2  # Old/ holds a .doc when LibreOffice is available
    assert st["roots"][0]["documents"] == expected
    assert st["roots"][0]["excluded"] == [str(root / "Manuals")]
    assert st["stats"]["embedded"] == st["stats"]["chunks"] > 0

    probs = c.get("/api/problems").json()
    assert {os.path.basename(p["path"]) for p in probs["items"]} == {"corrupt.pdf", "scan.pdf"}
    assert c.get("/api/problems", params={"status": "error"}).json()["total"] == 1

    pats = c.post("/api/patterns", json={"patterns": ["~$*", " ", "Broken"]}).json()["patterns"]
    assert pats == ["~$*", "Broken"]


def test_web_protections(client):
    c, root = client
    assert c.post("/api/roots", json={"path": str(root)}, headers={"X-Dmx": ""}).status_code == 403
    assert c.get("/api/state", headers={"Host": "evil.example"}).status_code == 403

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell launcher")
SCRIPT = Path(__file__).parents[1] / "scripts" / "dmx.ps1"
CONFIG = "[index]\ndata_dir = 'x'\n\n[worlds.projects]\nprofile = 'projects'\n\n[worlds.marketing]\nprofile = 'marketing'\n"


def run(shared, local, *args):
    env = dict(os.environ, DMX_RAG_LOCAL=str(local))
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(shared / "app" / "scripts" / "dmx.ps1"), *args],
                          capture_output=True, text=True, env=env, timeout=180)


@pytest.fixture
def layout(tmp_path):
    """The single-index layout used before worlds, on a fake share and a fake C:\\dmx-rag."""
    shared, local = tmp_path / "share", tmp_path / "local"
    (shared / "app" / "scripts").mkdir(parents=True)
    shutil.copy(SCRIPT, shared / "app" / "scripts" / "dmx.ps1")
    (shared / "config.toml").write_text(CONFIG, encoding="utf-8")
    (shared / "data").mkdir()
    (shared / "data" / "index.sqlite3").write_bytes(b"master")
    (shared / "data" / "index.prev.sqlite3").write_bytes(b"prev")
    (shared / "register.csv").write_text("project,model\n", encoding="utf-8")
    (local / "data" / "logs").mkdir(parents=True)
    (local / "data" / "models").mkdir()
    (local / "data" / "index.sqlite3").write_bytes(b"local")
    (local / "data" / "vec_ids.v1.npy").write_bytes(b"v")
    (local / "register.csv").write_text("project,model\n", encoding="utf-8")
    return shared, local


def test_migrate_moves_the_single_index_to_projects(layout):
    shared, local = layout
    r = run(shared, local, "migrate")
    assert r.returncode == 0, r.stdout + r.stderr
    w = shared / "worlds" / "projects"
    assert (w / "index.sqlite3").read_bytes() == b"master"
    assert (w / "index.prev.sqlite3").exists() and (w / "register.csv").exists()
    assert not (shared / "data").exists() and not (shared / "register.csv").exists()
    lp = local / "data" / "projects"
    assert (lp / "index.sqlite3").read_bytes() == b"local"
    assert (lp / "vec_ids.v1.npy").exists() and (lp / "logs").is_dir() and (lp / "register.csv").exists()
    assert (local / "data" / "models").is_dir() and (local / "config.toml").exists()
    again = run(shared, local, "migrate")  # nothing left to do
    assert again.returncode == 0 and "One-time move" not in again.stdout


def test_migrate_refuses_while_the_old_layout_is_locked(layout):
    shared, local = layout
    (shared / "data" / "LOCK").write_text("DMX-WS032|someone|2026-10-09 10:00|index|123")
    r = run(shared, local, "migrate")
    assert r.returncode != 0 and "old layout" in r.stdout + r.stderr
    assert (shared / "data" / "index.sqlite3").exists() and not (shared / "worlds").exists()


def test_local_index_in_use_is_left_untouched(layout):
    shared, local = layout
    with open(local / "data" / "index.sqlite3", "rb"):  # like Claude Desktop's server
        r = run(shared, local, "migrate")
    assert r.returncode != 0 and "Quit Claude Desktop" in r.stdout + r.stderr
    assert (local / "data" / "index.sqlite3").exists() and (local / "data" / "vec_ids.v1.npy").exists()
    assert run(shared, local, "migrate").returncode == 0  # once it is closed, the rerun completes
    assert (local / "data" / "projects" / "index.sqlite3").read_bytes() == b"local"


def test_unknown_world_is_refused(layout):
    shared, local = layout
    r = run(shared, local, "unlock", "-World", "nope")
    assert r.returncode != 0 and "Configured worlds: projects, marketing" in r.stdout + r.stderr


def test_world_as_first_argument_and_per_world_lock(layout):
    shared, local = layout
    assert run(shared, local, "migrate").returncode == 0
    r = run(shared, local, "unlock", "marketing")
    assert r.returncode == 0 and "Not locked (marketing)" in r.stdout
    (shared / "worlds" / "projects" / "LOCK").write_text("DMX-WS032|someone|2026-10-09 10:00|index|1")
    r = run(shared, local, "unlock", "-World", "projects")
    assert "Locked by: DMX-WS032" in r.stdout
    assert "Not locked (marketing)" in run(shared, local, "unlock", "marketing").stdout

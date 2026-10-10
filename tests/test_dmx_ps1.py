import os
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows PowerShell launcher")
SCRIPT = Path(__file__).parents[1] / "scripts" / "dmx.ps1"
CONFIG = "[index]\ndata_dir = 'x'\n\n[worlds.projects]\nprofile = 'projects'\n\n[worlds.marketing]\nprofile = 'marketing'\n"


def run(shared, local, *args, env=None):
    env = dict(os.environ, DMX_RAG_LOCAL=str(local), **(env or {}))
    return subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(shared / "app" / "scripts" / "dmx.ps1"), *args],
                          capture_output=True, text=True, env=env, timeout=180)


def text(r):
    """stdout + stderr on one line: PowerShell wraps long error messages at the console width."""
    return (r.stdout + r.stderr).replace("\r", "").replace("\n", "")


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
    assert r.returncode != 0 and "old layout" in text(r)
    assert (shared / "data" / "index.sqlite3").exists() and not (shared / "worlds").exists()
    assert (local / "data" / "index.sqlite3").exists() and not (local / "data" / "projects").exists()


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


def test_lock_of_another_machine_is_named_and_can_be_unlocked(layout):
    shared, local = layout
    lock = shared / "data" / "LOCK"
    lock.write_text("DMX-WS032|someone|2026-10-09 10:00|index|123")
    r = run(shared, local, "migrate")
    assert r.returncode != 0 and str(lock) in text(r) and "DMX-WS032" in text(r)
    r = run(shared, local, "unlock", "-World", "projects")  # unlock does not need the move first
    assert r.returncode == 0 and "Locked by (old layout): DMX-WS032" in r.stdout and lock.exists()
    r = run(shared, local, "unlock", "-World", "projects", "-Force")
    assert r.returncode == 0 and not lock.exists()
    assert run(shared, local, "migrate").returncode == 0
    assert (shared / "worlds" / "projects" / "index.sqlite3").read_bytes() == b"master"
    assert not (shared / "worlds" / "projects" / "LOCK").exists()


def test_cut_short_run_of_this_machine_keeps_its_lock_with_the_index(layout):
    shared, local = layout
    held = os.environ["COMPUTERNAME"] + "|someone|2026-10-09 10:00|index|999999"  # no such process
    (shared / "data" / "LOCK").write_text(held)
    r = run(shared, local, "migrate")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (shared / "worlds" / "projects" / "LOCK").read_text() == held
    assert not (shared / "data").exists()
    assert (local / "data" / "projects" / "index.sqlite3").read_bytes() == b"local"
    r = run(shared, local, "unlock", "-World", "projects")
    assert "Locked by: " + held in r.stdout


def test_interrupted_shared_move_is_completed(layout):
    shared, local = layout
    w = shared / "worlds" / "projects"
    w.mkdir(parents=True)
    (shared / "data" / "index.sqlite3").rename(w / "index.sqlite3")  # the master moved, the rest did not
    r = run(shared, local, "migrate")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (w / "index.sqlite3").read_bytes() == b"master"
    assert (w / "index.prev.sqlite3").read_bytes() == b"prev" and (w / "register.csv").exists()
    assert not (shared / "data").exists() and not (shared / "register.csv").exists()


def test_interrupted_local_move_is_completed(layout):
    shared, local = layout
    lp = local / "data" / "projects"
    lp.mkdir()
    (local / "data" / "index.sqlite3").rename(lp / "index.sqlite3")  # the index moved, the rest did not
    r = run(shared, local, "migrate")
    assert r.returncode == 0, r.stdout + r.stderr
    assert (lp / "index.sqlite3").read_bytes() == b"local"
    assert (lp / "vec_ids.v1.npy").exists() and (lp / "logs").is_dir() and (lp / "register.csv").exists()
    assert not (local / "data" / "vec_ids.v1.npy").exists() and not (local / "register.csv").exists()


def test_failed_secondary_move_is_reported_and_retried(layout):
    shared, local = layout
    with open(local / "data" / "vec_ids.v1.npy", "rb"):  # a cache that cannot be renamed now
        r = run(shared, local, "migrate")
        assert r.returncode == 0, r.stdout + r.stderr
        assert "Could not move" in r.stdout and "vec_ids.v1.npy" in r.stdout
        assert (local / "data" / "projects" / "index.sqlite3").exists()
        assert (local / "data" / "vec_ids.v1.npy").exists()
    assert run(shared, local, "migrate").returncode == 0
    assert (local / "data" / "projects" / "vec_ids.v1.npy").exists()


def test_world_names_are_case_insensitive(layout):
    shared, local = layout
    r = run(shared, local, "unlock", "-World", "Projects")
    assert r.returncode == 0 and "Not locked (projects)" in r.stdout
    assert "Not locked (marketing)" in run(shared, local, "unlock", "Marketing").stdout  # as first argument
    assert (shared / "data" / "index.sqlite3").exists()  # unlock does not move anything


def test_migrate_needs_the_projects_table_in_the_config(layout):
    shared, local = layout
    without = CONFIG.replace("[worlds.projects]\nprofile = 'projects'\n\n", "")
    assert "[worlds.projects]" not in without
    (shared / "config.toml").write_text(without, encoding="utf-8")
    r = run(shared, local, "migrate")
    assert r.returncode != 0 and "[worlds.projects]" in text(r)
    assert (shared / "data" / "index.sqlite3").exists() and not (shared / "worlds").exists()
    assert (local / "data" / "index.sqlite3").exists() and not (local / "data" / "projects").exists()
    assert not (local / "config.toml").exists()


@pytest.mark.parametrize("args", [["-World", "marketing"], ["marketing"]])
def test_unlock_launcher_takes_the_world(layout, args):
    shared, local = layout
    launcher = shared / "Unlock after a crash.cmd"   # next to app\, as on the share
    shutil.copy(SCRIPT.parent / "launchers" / launcher.name, launcher)
    env = dict(os.environ, DMX_RAG_LOCAL=str(local))
    r = subprocess.run(" ".join([f'"{launcher}"', *args]), shell=True, input="n\n",
                       capture_output=True, text=True, env=env, timeout=120)
    assert "Not locked (marketing)" in r.stdout, r.stdout + r.stderr


# ---- commands that need the Python environment: a real venv with no packages, and a stub
# ---- dmx_docs.cli, so that no uv, no packages and no real index are involved.

STUB_CLI = """import os, sys
# Stands in for dmx_docs.cli: the index step ends with FAKE_DMX_EXIT, every other step succeeds.
sys.exit(int(os.environ.get("FAKE_DMX_EXIT", "0")) if "index" in sys.argv else 0)
"""
STAMP_NS = 1_700_000_000_000_000_000  # a multiple of 100 ns (the NTFS resolution)


@pytest.fixture
def ready(layout, tmp_path):
    """The migrated layout with an installed environment that Assert-Env accepts (dmx.ps1 would
    otherwise try to install one with uv.exe, which this fake share does not have)."""
    shared, local = layout
    assert run(shared, local, "migrate").returncode == 0
    master = shared / "worlds" / "projects" / "index.sqlite3"
    master.unlink()
    con = sqlite3.connect(master)  # the copy back runs an integrity check on a real database
    con.execute("CREATE TABLE t (x)")
    con.commit()
    con.close()
    app = shared / "app"
    (app / "src").mkdir()
    (app / "pyproject.toml").write_text("[project]\n", encoding="ascii")
    (app / "src" / "placeholder.txt").write_text("x", encoding="ascii")
    for stamped in (app / "pyproject.toml", app / "src" / "placeholder.txt", app / "scripts" / "dmx.ps1"):
        os.utime(stamped, ns=(STAMP_NS, STAMP_NS))
    subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(local / "venv")], check=True, capture_output=True)
    ticks = STAMP_NS // 100 + 621355968000000000  # .NET ticks of the newest file (Get-CodeStamp)
    gpu = "True" if shutil.which("nvidia-smi") else "False"
    (local / "installed.txt").write_text(f"{ticks}|gpu={gpu}", encoding="ascii")
    stub = tmp_path / "stub" / "dmx_docs"
    stub.mkdir(parents=True)
    (stub / "__init__.py").write_text("", encoding="ascii")
    (stub / "cli.py").write_text(STUB_CLI, encoding="ascii")
    return shared, local, {"PYTHONPATH": str(stub.parent)}


def test_update_that_succeeds_says_done_and_exits_zero(ready):
    shared, local, env = ready
    r = run(shared, local, "update", "-World", "projects", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Running: dmx-docs --world projects embed" in r.stdout
    out = r.stdout
    assert out.index("--world projects index") < out.index("--world projects ocr") < out.index("--world projects embed")
    assert "Done. The 'projects' index in the shared folder is up to date and unlocked." in r.stdout
    assert "Stopped" not in r.stdout
    assert not (shared / "worlds" / "projects" / "LOCK").exists()


def test_update_stopped_by_a_failing_step_does_not_report_success(ready):
    shared, local, env = ready
    r = run(shared, local, "update", "-World", "projects", env=dict(env, FAKE_DMX_EXIT="3"))
    assert r.returncode == 3, r.stdout + r.stderr          # the unattended run is judged by this
    assert "Done." not in r.stdout and "is up to date" not in r.stdout
    assert "Stopped early" in r.stdout and "exit code 3" in r.stdout and "'projects'" in r.stdout
    assert "Running: dmx-docs --world projects embed" not in r.stdout   # the next step was skipped
    # What was done so far is still saved and unlocked.
    w = shared / "worlds" / "projects"
    assert not (w / "LOCK").exists() and (w / "index.prev.sqlite3").exists()
    assert "saved" in r.stdout and "unlocked" in r.stdout


def test_pull_warns_about_a_register_left_in_the_shared_folder(ready):
    shared, local, env = ready
    first = run(shared, local, "pull", "-World", "projects", env=env)
    assert first.returncode == 0, first.stdout + first.stderr
    assert "is not used" not in first.stdout
    stale = shared / "register.csv"   # written later by the old register import, after the migration
    stale.write_text("project,model\nNEWER,x\n", encoding="utf-8")
    kept = (shared / "worlds" / "projects" / "register.csv").read_bytes()
    r = run(shared, local, "pull", "-World", "projects", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"{stale} is not used" in r.stdout
    assert r"worlds\projects\register.csv" in r.stdout and "move it" in r.stdout
    assert stale.exists() and (shared / "worlds" / "projects" / "register.csv").read_bytes() == kept  # nothing was touched


def test_language_files_are_copied_to_the_machine(ready):
    shared, local, env = ready
    td = shared / "tools" / "tessdata"
    td.mkdir(parents=True)
    (td / "fra.traineddata").write_bytes(b"model")
    r = run(shared, local, "update", "-World", "projects", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert (local / "tessdata" / "fra.traineddata").read_bytes() == b"model"
    assert "OCR language files updated from the shared folder." in r.stdout   # said on the first copy ...
    again = run(shared, local, "update", "-World", "projects", env=env)
    assert again.returncode == 0, again.stdout + again.stderr
    assert "OCR language files" not in again.stdout                           # ... and not when nothing changed
    (td / "fra.traineddata").write_bytes(b"newer model")
    changed = run(shared, local, "update", "-World", "projects", env=env)
    assert "OCR language files updated from the shared folder." in changed.stdout
    assert (local / "tessdata" / "fra.traineddata").read_bytes() == b"newer model"


def test_language_files_that_cannot_be_copied_are_a_warning_not_a_failed_step(ready):
    shared, local, env = ready
    td = shared / "tools" / "tessdata"
    td.mkdir(parents=True)
    (td / "fra.traineddata").write_bytes(b"model")
    (local / "tessdata").write_bytes(b"a file where the folder should be")   # robocopy cannot copy into it
    r = run(shared, local, "update", "-World", "projects", env=env)
    assert r.returncode == 0, r.stdout + r.stderr               # robocopy's code is not the step's exit code
    assert "WARNING: could not copy the OCR language files (robocopy exit code " in r.stdout
    assert "OCR may be unavailable." in r.stdout
    assert "Done. The 'projects' index in the shared folder is up to date and unlocked." in r.stdout
    assert "Stopped" not in r.stdout


def test_ocr_command_runs_locked_for_one_world(ready):
    shared, local, env = ready
    r = run(shared, local, "ocr", "-World", "projects", "--max-minutes", "5", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Running: dmx-docs --world projects ocr --max-minutes 5" in r.stdout
    assert not (shared / "worlds" / "projects" / "LOCK").exists()

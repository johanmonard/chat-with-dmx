# Worlds and PowerPoint Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One index per document family ("world": projects, marketing...) selected with `--world`, the Projects index migrated to the new layout, PowerPoint (.pptx/.ppt) indexed slide by slide, and an unattended "update a world" script for the GPU VM, so Marketing can be indexed and embedded on DMX-WS032.

**Architecture:** `config.toml` gains `[worlds.<name>]` tables; `load_config(path, world)` picks one and derives per-world paths (`data_dir\<world>\...`). A world's `profile` (projects / marketing / none) selects facet rules, MCP tools and server instructions. `scripts\dmx.ps1` gets `-World`, per-world lock/master/local copy, an `update` command and an automatic one-time migration of the single index to `worlds\projects`. PowerPoint text comes from python-pptx; old .ppt files are converted by LibreOffice or PowerPoint (COM) like .doc files.

**Tech Stack:** Python 3.12, SQLite/FTS5, PyMuPDF, python-docx, python-pptx (new), fastembed, MCP (mcp 2.x `MCPServer`), pywin32 (COM), Windows PowerShell 5.1, pytest.

**Spec:** `docs/superpowers/specs/2026-10-09-worlds-and-powerpoint-design.md`

## Global Constraints

- Machines install the code from the **working tree** of `U:\DMX-RAG\app` (stamp = newest file in `src`, `pyproject.toml`, `dmx.ps1`). Never edit `U:\DMX-RAG\app` before Task 9: all work happens in the worktree `D:\JOHAN\Private\Python\github\chat-with-dmx-worlds` (branch `worlds`), merged in Task 9.
- World selection: `--world`, else `$DMX_DOCS_WORLD`, else `projects` when the config has `[worlds.projects]`. Unknown world = error listing the configured worlds (config order).
- World paths: local `data_dir\<world>\index.sqlite3`, vector caches and `logs\` in `data_dir\<world>\`, `register.csv` in `data_dir\<world>\` (projects only); `models_dir` stays `data_dir\models`. Shared: `U:\DMX-RAG\worlds\<world>\` (index.sqlite3, index.prev.sqlite3, LOCK, register.csv).
- A config **without** `[worlds]` behaves exactly as today (`data_dir\index.sqlite3`, profile projects). The existing test suite must keep passing.
- The `[embeddings]` model is global and cannot be overridden per world.
- MCP server name: `dmx-docs` for projects (and single-index configs), `dmx-<world>` otherwise.
- Projects profile = today's behaviour, unchanged (facets, machines, register, `list_projects`, `project_card`, `INSTRUCTIONS`); its stored `facets_version` stays the plain number `"3"`.
- Marketing profile: facet `category` = first folder below the root (Brochures, Competition, CSI, Datasheets, Exhibition, Graphics, Pictures, Presentations, Publications, Videos); no machines/register; no `list_projects`/`project_card`; `search` offers `category=`.
- PowerPoint: one slide = one page, page label "slide"; .ppt conversion time limit 120 s (`POWERPOINT_TIMEOUT_S`); the user's own PowerPoint is never quit or killed (only processes started by automation, `/Automation` or `-Embedding` on the command line).
- `dmx.ps1` must run on Windows PowerShell 5.1: no `&&`, `||`, `?:`, `??`.
- No real client names in tests or docstrings (use fictional names).
- Commit trailer: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. The user pushes (the auto-mode classifier blocks `git push`).

Test command used below (Git Bash, from the worktree):

```bash
cd /d/JOHAN/Private/Python/github/chat-with-dmx-worlds && PYTHONPATH=src /c/dmx-rag/venv/Scripts/python.exe -m pytest <tests> -v
```

## Review Focus

- The laptop's Claude Desktop holds `C:\dmx-rag\data\index.sqlite3` during migration: expect a clear "quit Claude Desktop" message, the local files untouched, and a rerun that completes (test in Task 7).
- A corrupt or password-protected `.pptx`: expect status `error` for that file and the run to continue (test in Task 4).
- A category typed in another case or several at once (`category="brochures, DATASHEETS"`): expect both matched (test in Task 2).
- PowerPoint stuck on a `.ppt` behind an invisible dialog: expect a `TimeoutError` after the limit, the slot released, and the next file not blocked (test in Task 5).
- The existing Claude Desktop entry on the laptop (`--config C:\dmx-rag\config.toml serve`, no `--world`) after migration: expect it to serve Projects from `data\projects` (tests in Task 1 and Task 7).

---

### Task 0: Worktree and test environment

**Files:** none (environment only)

- [ ] **Step 1: Create the worktree on a local disk**

```bash
git -C /u/DMX-RAG/app worktree add /d/JOHAN/Private/Python/github/chat-with-dmx-worlds -b worlds
```
Expected: `Preparing worktree (new branch 'worlds')`.

- [ ] **Step 2: Install python-pptx into the laptop venv (used by the tests; machines get it from pyproject in Task 4)**

```bash
/u/DMX-RAG/tools/uv.exe pip install --python /c/dmx-rag/venv/Scripts/python.exe "python-pptx>=1.0"
```

- [ ] **Step 3: Baseline**

Run: `cd /d/JOHAN/Private/Python/github/chat-with-dmx-worlds && PYTHONPATH=src /c/dmx-rag/venv/Scripts/python.exe -m pytest -q`
Expected: `76 passed, 1 skipped` (LibreOffice test skipped).

---

### Task 1: Worlds in the configuration

**Files:**
- Modify: `src/dmx_docs/config.py` (whole file below)
- Modify: `src/dmx_docs/cli.py:31-69`
- Modify: `src/dmx_docs/search.py:150,156` (`self.cfg.data_dir` → `self.cfg.world_dir`)
- Create: `tests/test_worlds.py`

**Interfaces:**
- Produces: `load_config(path, world: str | None = None) -> Config`; `Config.world: str | None`, `Config.world_title: str | None`, `Config.profile: str` (`"projects" | "marketing" | "none"`); properties `Config.world_dir`, `db_path`, `models_dir`, `logs_dir`, `server_name`; `config.PROFILES`; CLI option `--world NAME`.

- [ ] **Step 1: Write the failing tests** (`tests/test_worlds.py`)

```python
import pytest

from dmx_docs.config import load_config

WORLDS = r"""
[index]
data_dir = 'DATA'
extensions = ['.pdf', '.docx', '.doc']

[worlds.projects]
title = "Projects"
profile = "projects"

[worlds.marketing]
title = "Marketing"
profile = "marketing"
extensions = ['.pdf', '.pptx', '.ppt']
roots = ['\\server\share\Marketing']
"""


@pytest.fixture
def config_file(tmp_path, monkeypatch):
    monkeypatch.delenv("DMX_DOCS_WORLD", raising=False)
    p = tmp_path / "config.toml"
    p.write_text(WORLDS.replace("DATA", (tmp_path / "data").as_posix()), encoding="utf-8")
    return p


def test_default_world_is_projects(config_file, tmp_path):
    # Claude Desktop's existing entry has no --world: it must keep serving the projects.
    cfg = load_config(config_file)
    data = tmp_path / "data"
    assert (cfg.world, cfg.world_title, cfg.profile) == ("projects", "Projects", "projects")
    assert cfg.db_path == data / "projects" / "index.sqlite3"
    assert cfg.models_dir == data / "models"  # one embedding model for all worlds
    assert cfg.register_path == str(data / "projects" / "register.csv")
    assert cfg.extensions == [".pdf", ".docx", ".doc"]
    assert cfg.server_name == "dmx-docs"


def test_marketing_world_has_its_own_folder_and_file_types(config_file, tmp_path):
    cfg = load_config(config_file, world="marketing")
    data = tmp_path / "data"
    assert (cfg.world, cfg.world_title, cfg.profile) == ("marketing", "Marketing", "marketing")
    assert cfg.db_path == data / "marketing" / "index.sqlite3"
    assert cfg.logs_dir == data / "marketing" / "logs"
    assert cfg.extensions == [".pdf", ".pptx", ".ppt"]
    assert cfg.roots == [r"\\server\share\Marketing"]
    assert cfg.register_path is None
    assert cfg.server_name == "dmx-marketing"


def test_world_from_environment(config_file, monkeypatch):
    monkeypatch.setenv("DMX_DOCS_WORLD", "marketing")
    assert load_config(config_file).world == "marketing"
    assert load_config(config_file, world="projects").world == "projects"  # the option wins


def test_unknown_world_lists_the_configured_ones(config_file):
    with pytest.raises(ValueError, match="Configured worlds .*: projects, marketing"):
        load_config(config_file, world="documentation")


def test_single_index_config_is_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("DMX_DOCS_WORLD", raising=False)
    p = tmp_path / "config.toml"
    p.write_text(f"[index]\ndata_dir = '{(tmp_path / 'data').as_posix()}'\n", encoding="utf-8")
    cfg = load_config(p)
    assert cfg.world is None and cfg.profile == "projects"
    assert cfg.db_path == tmp_path / "data" / "index.sqlite3"
    assert cfg.register_path == str(tmp_path / "register.csv")
    with pytest.raises(ValueError, match=r"no \[worlds"):
        load_config(p, world="marketing")


def test_cli_refuses_an_unknown_world(config_file, capsys):
    from dmx_docs.cli import main
    with pytest.raises(SystemExit):
        main(["--config", str(config_file), "--world", "nope", "status"])
    assert "Unknown world 'nope'" in capsys.readouterr().err


def test_vector_cache_lives_in_the_world_folder(config_file):
    from dmx_docs.search import VectorIndex
    cfg = load_config(config_file, world="marketing")
    ids, mat = VectorIndex(cfg)._cache_paths("abc")
    assert ids.parent == mat.parent == cfg.world_dir
```

- [ ] **Step 2: Run them to see them fail**

Run: `... -m pytest tests/test_worlds.py -v`
Expected: FAIL (`load_config() got an unexpected keyword argument 'world'`, no attribute `world`...).

- [ ] **Step 3: Replace `src/dmx_docs/config.py` with**

```python
"""Configuration loading (TOML)."""

from __future__ import annotations

import fnmatch
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_EXCLUDES = ["~$*", ".*", "$RECYCLE.BIN", "System Volume Information"]
WORLD_NAME = re.compile(r"[a-z0-9_-]+")
# Facet rules, MCP tools and server instructions of a world (see facets.py and server.py).
PROFILES = ("projects", "marketing", "none")


def _default_workers() -> int:
    # Extraction is mostly CPU work (sorted PDF text); one core is left for the main process.
    return max(1, min(16, (os.cpu_count() or 2) - 1))


@dataclass
class Config:
    # Root folders and excluded folders are managed on the configuration page
    # (stored in the index database, see sources.py); `roots` in config.toml
    # only seeds them on first use.
    roots: list[str]
    data_dir: Path
    excluded_dirs: list[str] = field(default_factory=list)
    extensions: list[str] = field(default_factory=lambda: [".pdf", ".docx", ".doc"])
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    max_file_mb: float = 300
    max_pdf_pages: int = 3000
    workers: int = field(default_factory=_default_workers)
    doc_converter: str = "auto"
    libreoffice_path: str | None = None

    embeddings_enabled: bool = True
    embedding_model: str = "intfloat/multilingual-e5-large"
    query_prefix: str = "query: "
    passage_prefix: str = "passage: "
    embed_batch_size: int = 1
    embed_gpu_batch_size: int = 64
    embed_threads: int | None = None
    embed_device: str = "auto"  # 'auto' (GPU if available), 'cpu' or 'cuda'

    max_read_chars: int = 40000
    thesaurus_path: str | None = None   # thesaurus.toml next to config.toml by default
    expand_synonyms: bool = False       # expand keyword searches with the thesaurus
    register_path: str | None = None    # register.csv (installed machines) next to config.toml by default
    export_dir: str | None = None  # where export_image saves files (default: ~\Claude\dmx-images)
    config_path: Path | None = None
    # A world is one family of documents with its own index (projects, marketing...), chosen
    # with --world. None: a config without [worlds] tables, one index directly in data_dir.
    world: str | None = None
    world_title: str | None = None
    profile: str = "projects"

    def __post_init__(self) -> None:
        self.extensions = [e.lower() if e.startswith(".") else "." + e.lower() for e in self.extensions]
        self.set_sources(self.roots, self.excluded_dirs, self.exclude)

    def set_sources(self, roots: list[str], excluded_dirs: list[str], patterns: list[str]) -> None:
        from .store import path_key

        self.roots = list(roots)
        self.excluded_dirs = list(excluded_dirs)
        self.exclude = list(patterns)
        self.root_keys = [path_key(r) for r in self.roots]
        self.excluded_keys = {path_key(d) for d in self.excluded_dirs}
        self._name_patterns = [p.lower() for p in self.exclude if "/" not in p]
        self._path_patterns = [p.lower() for p in self.exclude if "/" in p]

    def is_excluded_dir(self, key: str) -> bool:
        """True if the folder (path_key) or one of its parents is excluded."""
        if not self.excluded_keys:
            return False
        from .store import is_under

        return any(is_under(key, ex) for ex in self.excluded_keys)

    def allows(self, key: str) -> bool:
        """True if a path (path_key) is inside a root and not in an excluded folder."""
        from .store import is_under

        return any(is_under(key, rk) for rk in self.root_keys) and not self.is_excluded_dir(key)

    @property
    def world_dir(self) -> Path:
        """Local folder of this world's index, search cache and logs."""
        return self.data_dir / self.world if self.world else self.data_dir

    @property
    def db_path(self) -> Path:
        return self.world_dir / "index.sqlite3"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"  # one embedding model for every world

    @property
    def logs_dir(self) -> Path:
        return self.world_dir / "logs"

    @property
    def server_name(self) -> str:
        """MCP server name in Claude Desktop (dmx-docs for the project documentation)."""
        return "dmx-docs" if self.world in (None, "projects") else f"dmx-{self.world}"

    def is_excluded(self, name: str, path: str) -> bool:
        name_l = name.lower()
        if any(fnmatch.fnmatchcase(name_l, p) for p in self._name_patterns):
            return True
        if self._path_patterns:
            path_l = path.replace("\\", "/").lower()
            return any(fnmatch.fnmatchcase(path_l, p) for p in self._path_patterns)
        return False

    def extract_options(self) -> dict:
        """Plain-dict options passed to extraction worker processes."""
        return {
            "max_pdf_pages": self.max_pdf_pages,
            "doc_converter": self.doc_converter,
            "libreoffice_path": self.libreoffice_path,
        }


def load_config(path: str | os.PathLike, world: str | None = None) -> Config:
    """Read config.toml. When it has [worlds.<name>] tables, one world is used: `world`, else
    $DMX_DOCS_WORLD, else 'projects'. A world table may set title, profile, extensions, exclude,
    roots (first-run seed) and register; all other settings are shared by every world."""
    path = Path(path)
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    idx = dict(raw.get("index", {}))
    emb = raw.get("embeddings", {})
    srv = raw.get("server", {})
    worlds = raw.get("worlds") or {}

    name = world or os.environ.get("DMX_DOCS_WORLD") or None
    if worlds:
        name = name or ("projects" if "projects" in worlds else None)
        if name not in worlds:
            raise ValueError(f"Unknown world '{name or ''}'. Configured worlds in {path}: {', '.join(worlds)}")
        if not WORLD_NAME.fullmatch(name):
            raise ValueError(f"World names use lowercase letters, digits, '-' and '_' only: '{name}'")
    elif name:
        raise ValueError(f"{path} has no [worlds.<name>] tables: --world {name} cannot be used")
    w = worlds.get(name, {}) if name else {}
    for key in ("roots", "extensions", "exclude"):
        if key in w:
            idx[key] = w[key]

    roots = idx.get("roots") or []
    data_dir = Path(idx.get("data_dir", "data"))
    if not data_dir.is_absolute():
        data_dir = (path.parent / data_dir).resolve()

    kwargs: dict = {"roots": [str(r) for r in roots], "data_dir": data_dir, "config_path": path.resolve()}
    if name:
        profile = w.get("profile", "none")
        if profile not in PROFILES:
            raise ValueError(f"World '{name}': unknown profile '{profile}' (use {', '.join(PROFILES)})")
        kwargs.update(world=name, world_title=w.get("title") or name.capitalize(), profile=profile)
    for key in ("extensions", "exclude", "max_file_mb", "max_pdf_pages", "workers",
                "doc_converter", "libreoffice_path"):
        if key in idx:
            kwargs[key] = idx[key]
    mapping = {
        "enabled": "embeddings_enabled",
        "model": "embedding_model",
        "query_prefix": "query_prefix",
        "passage_prefix": "passage_prefix",
        "batch_size": "embed_batch_size",
        "gpu_batch_size": "embed_gpu_batch_size",
        "threads": "embed_threads",
        "device": "embed_device",
    }
    for key, attr in mapping.items():
        if key in emb:
            kwargs[attr] = emb[key]
    if "max_read_chars" in srv:
        kwargs["max_read_chars"] = srv["max_read_chars"]
    srch = raw.get("search", {})
    thesaurus = srch.get("thesaurus", "thesaurus.toml")
    if thesaurus:
        tp = Path(os.path.expandvars(str(thesaurus)))
        kwargs["thesaurus_path"] = str(tp if tp.is_absolute() else (path.parent / tp))
    kwargs["expand_synonyms"] = bool(srch.get("expand", False))
    if name:  # the machine register belongs to a world: data_dir\<world>\register.csv
        register = w.get("register", "register.csv" if kwargs["profile"] == "projects" else None)
        if register:
            rp = Path(os.path.expandvars(str(register)))
            kwargs["register_path"] = str(rp if rp.is_absolute() else data_dir / name / rp)
    else:
        register = srch.get("register", "register.csv")
        if register:
            rp = Path(os.path.expandvars(str(register)))
            kwargs["register_path"] = str(rp if rp.is_absolute() else (path.parent / rp))
    if srv.get("export_dir"):
        kwargs["export_dir"] = os.path.expandvars(os.path.expanduser(str(srv["export_dir"])))
    return Config(**kwargs)
```

- [ ] **Step 4: `--world` in `src/dmx_docs/cli.py`**

After the `--config` argument (line 33) add:

```python
    parser.add_argument("--world", default=None,
                        help="world (family of documents) to use, e.g. projects or marketing "
                             "(default: $DMX_DOCS_WORLD, else projects)")
```

Replace `cfg = load_config(config_path)` (line 69) with:

```python
    try:
        cfg = load_config(config_path, world=args.world)
    except ValueError as e:
        parser.error(str(e))
```

- [ ] **Step 5: Vector cache per world in `src/dmx_docs/search.py`**

In `VectorIndex._cache_paths` and `_cleanup_old_caches`, replace every `self.cfg.data_dir` with `self.cfg.world_dir` (lines 150 and 156; no other use in the file).

- [ ] **Step 6: Run the tests**

Run: `... -m pytest tests/test_worlds.py -v` → all PASS. Then `... -m pytest -q` → `83 passed, 1 skipped`.

- [ ] **Step 7: Commit**

```bash
git add src/dmx_docs/config.py src/dmx_docs/cli.py src/dmx_docs/search.py tests/test_worlds.py
git commit -m "Worlds: one index per family of documents, chosen with --world

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Facet profiles (projects, marketing, none)

**Files:**
- Modify: `src/dmx_docs/facets.py` (TABLE, COLUMNS, versions, new `compute_marketing`, `refresh`)
- Modify: `src/dmx_docs/indexer.py:343`
- Modify: `src/dmx_docs/tools.py` (`_con`, `search`, `_facets`, `index_status`)
- Create: `tests/test_profiles.py`

**Interfaces:**
- Consumes: `Config.profile`, `Config.world`, `Config.world_title` (Task 1).
- Produces: `facets.refresh(con, roots, profile="projects") -> int`; `facets.compute_marketing(path, root) -> dict`; `doc_facets.category` column; `DocTools.search(..., category: str | None = None)`; `DocTools._facets(self, con, doc_ids)` (now an instance method); `index_status()` first line `World: <title> (<name>)` when a world is set. Test fixture `marketing` in `tests/test_profiles.py` (reused by Task 3).

- [ ] **Step 1: Write the failing tests** (`tests/test_profiles.py`)

```python
import pytest

from conftest import make_pdf
from dmx_docs import facets, store
from dmx_docs.indexer import run_index
from dmx_docs.tools import DocTools

quiet = lambda *a, **k: None  # noqa: E731


@pytest.fixture
def marketing(tmp_path, make_cfg):
    root = tmp_path / "22_Marketing"
    (root / "Brochures").mkdir(parents=True)
    (root / "Presentations" / "Training").mkdir(parents=True)
    (root / "Datasheets").mkdir()
    make_pdf(str(root / "Brochures" / "Paloma brochure.pdf"), ["Paloma pick and place robot for biscuits."])
    make_pdf(str(root / "Datasheets" / "Paloma datasheet.pdf"), ["Paloma technical data: 120 ppm."])
    make_pdf(str(root / "Presentations" / "Training" / "Sales training.pdf"), ["Sales training: Paloma cadence."])
    make_pdf(str(root / "Overview.pdf"), ["Company overview: Paloma and Presto."])
    cfg = make_cfg(root, world="marketing", world_title="Marketing", profile="marketing")
    run_index(cfg, progress=quiet)
    return cfg, root


def categories(cfg):
    con = store.connect(cfg.db_path)
    rows = {r[0]: r[1] for r in con.execute(
        "SELECT d.name, f.category FROM docs d JOIN doc_facets f ON f.doc_id = d.id")}
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    con.close()
    return rows, tables


def test_marketing_category_is_the_first_folder(marketing):
    cfg, _ = marketing
    rows, tables = categories(cfg)
    assert rows == {"Paloma brochure.pdf": "Brochures", "Paloma datasheet.pdf": "Datasheets",
                    "Sales training.pdf": "Presentations", "Overview.pdf": None}
    assert "project_machines" not in tables and "register_machines" not in tables  # projects only
    assert cfg.db_path.parent.name == "marketing"


def test_search_by_category_any_case_several_values(marketing):
    cfg, _ = marketing
    out = DocTools(cfg).search("Paloma", mode="keyword", category="brochures, DATASHEETS")
    assert "Paloma brochure.pdf" in out and "Paloma datasheet.pdf" in out
    assert "Sales training.pdf" not in out and "Overview.pdf" not in out
    assert "category Brochures" in out


def test_index_status_names_the_world(marketing):
    cfg, _ = marketing
    assert DocTools(cfg).index_status().startswith("World: Marketing (marketing)")


def test_profile_change_recomputes_facets(marketing):
    cfg, _ = marketing
    con = store.connect(cfg.db_path)
    assert facets.refresh(con, cfg.roots, "marketing") == 0  # up to date
    assert facets.refresh(con, cfg.roots, "none") == 4       # other rules: recomputed
    assert con.execute("SELECT count(*) FROM doc_facets WHERE category IS NOT NULL").fetchone()[0] == 0
    con.close()


def test_projects_facets_version_is_unchanged(corpus_copy, make_cfg):
    # The existing Projects index must not recompute its facets when worlds arrive.
    cfg = make_cfg(corpus_copy)
    run_index(cfg, progress=quiet)
    con = store.connect(cfg.db_path)
    assert store.get_meta(con, "facets_version") == str(facets.FACETS_VERSION)
    con.close()
```

- [ ] **Step 2: Run them to see them fail**

Run: `... -m pytest tests/test_profiles.py -v`
Expected: FAIL (no column `category`, `refresh()` takes 2 arguments, `search()` got an unexpected keyword `category`).

- [ ] **Step 3: `src/dmx_docs/facets.py`**

Replace the version constant (line 17-18):

```python
# Bump when the rules change: every document's facets are recomputed (from its path only).
FACETS_VERSION = 3             # projects
MARKETING_FACETS_VERSION = 1
```

Directly after the end of `compute()` (before `TABLE = """`), add:

```python
def compute_marketing(path: str, root: str) -> dict:
    """Marketing world: the category is the first folder below the root (Brochures, Presentations...)."""
    rel = os.path.relpath(path, root) if root else path
    parts = [p for p in rel.replace("/", "\\").split("\\") if p and p != "."]
    if len(parts) > 1:
        return {"category": parts[0], "facet_source": "folder"}
    return {}


RULES = {"projects": compute, "marketing": compute_marketing}


def _version(profile: str) -> str:
    # Projects keeps the plain number stored before worlds existed: nothing is recomputed.
    if profile == "projects":
        return str(FACETS_VERSION)
    return f"{profile}:{MARKETING_FACETS_VERSION if profile == 'marketing' else 1}"
```

In `TABLE`, after `    subproject    TEXT` add a comma and the line `    category      TEXT` (so the column list ends `subproject TEXT,\n    category TEXT`). Replace `COLUMNS` with:

```python
COLUMNS = ("doc_id", "project", "collection", "section", "doc_type", "facet_source", "subproject", "category")
```

Replace the whole `refresh` function with:

```python
def refresh(con, roots: list[str], profile: str = "projects") -> int:
    """Compute facets for documents that have none (or were computed by older rules or another
    profile). Cheap when everything is up to date. Returns the number of documents updated."""
    from . import store

    con.executescript(TABLE)
    cols = {r[1] for r in con.execute("PRAGMA table_info(doc_facets)")}
    if "subproject" not in cols:
        con.execute("ALTER TABLE doc_facets ADD COLUMN subproject TEXT")  # tables from version 1
    if "category" not in cols:
        con.execute("ALTER TABLE doc_facets ADD COLUMN category TEXT")  # tables from before worlds
    if store.get_meta(con, "facets_version") != _version(profile):
        con.execute("DELETE FROM doc_facets")
        store.set_meta(con, "facets_version", _version(profile))
    con.execute("DELETE FROM doc_facets WHERE doc_id NOT IN (SELECT id FROM docs)")
    todo = con.execute("""SELECT d.id, d.path, d.path_key FROM docs d
                          LEFT JOIN doc_facets f ON f.doc_id = d.id WHERE f.doc_id IS NULL""").fetchall()
    root_keys = [(store.path_key(r), r) for r in roots]
    rule = RULES.get(profile, lambda path, root: {})
    rows = []
    for doc_id, path, key in todo:
        f = rule(path, root_of(key, root_keys) or "")
        rows.append((doc_id,) + tuple(f.get(c) for c in COLUMNS[1:]))
    autocommit = con.isolation_level is None
    if autocommit:
        con.execute("BEGIN")  # one transaction, not one per row
    con.executemany(f"INSERT OR REPLACE INTO doc_facets ({','.join(COLUMNS)}) "
                    f"VALUES ({','.join('?' * len(COLUMNS))})", rows)
    con.execute("COMMIT") if autocommit else con.commit()
    if profile == "projects":  # machine types and the register belong to the project documentation
        from . import machines, register
        machines.refresh(con)  # machine types per project; no-op when nothing changed
        con.executescript(register.TABLE)  # filled by register.refresh (needs the register file)
    return len(rows)
```

- [ ] **Step 4: `src/dmx_docs/indexer.py:343`**

```python
    facets.refresh(con, cfg.roots, cfg.profile)
```

- [ ] **Step 5: `src/dmx_docs/tools.py`**

In `DocTools._con`, replace the two refresh lines with:

```python
        facets.refresh(con, self.cfg.roots, self.cfg.profile)  # no-op when every document has its facets
        if self.cfg.profile == "projects":  # the machine register belongs to the project documentation
            register.refresh(con, self.cfg.register_path)  # no-op when neither the file nor the documents changed
```

In `search`, add the parameter `category: str | None = None` after `country: str | None = None`, and replace the `facet_filter = ...` statement with:

```python
        facet_filter = {"project": project, "doc_type": doc_type, "section": section, "collection": collection,
                        "machine": machine, "client": client, "country": country, "category": category}
```

Replace the head of `_facets` (from `@staticmethod` down to and including `out = {}`) with:

```python
    def _facets(self, con, doc_ids: set[int]) -> dict[int, str]:
        """One line per document: project / collection / section / type (projects) or category."""
        if not doc_ids or self.cfg.profile == "none":
            return {}
        ids = list(doc_ids)
        if self.cfg.profile == "marketing":
            return {r["doc_id"]: f"category {r['category'] or 'unknown'}" for r in con.execute(
                f"SELECT doc_id, category FROM doc_facets WHERE doc_id IN ({','.join('?' * len(ids))})", ids)}
        out = {}
```

In `index_status`, replace `lines = [f"Indexed documents: {total}",` with:

```python
        lines = ([f"World: {self.cfg.world_title} ({self.cfg.world})"] if self.cfg.world else []) + [
                 f"Indexed documents: {total}",
```

(the rest of the list literal stays as it is).

- [ ] **Step 6: Run the tests**

Run: `... -m pytest tests/test_profiles.py tests/test_facets.py tests/test_machines.py tests/test_register.py -v` → all PASS. Then the whole suite `-q` → no failure.

- [ ] **Step 7: Commit**

```bash
git add src/dmx_docs/facets.py src/dmx_docs/indexer.py src/dmx_docs/tools.py tests/test_profiles.py
git commit -m "Facet profiles: marketing category = first folder; register and machines for projects only

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: MCP server and configuration page per world

**Files:**
- Modify: `src/dmx_docs/server.py`
- Modify: `src/dmx_docs/web.py:123-129` (`claude_desktop_snippet`) and the `state()` dict
- Modify: `src/dmx_docs/web/index.html:380`
- Modify: `tests/test_profiles.py` (append)

**Interfaces:**
- Consumes: `Config.server_name`, `Config.profile`, `Config.world`, `Config.world_title`, `Config.world_dir` (Task 1); `DocTools.search(..., category=)` (Task 2).
- Produces: `server.instructions_for(cfg) -> str`, `server.MARKETING_INSTRUCTIONS`, `server.GENERIC_INSTRUCTIONS`; server tool sets: projects = today's 11 tools; marketing/none = 9 tools (no `list_projects`, `project_card`), marketing `search` has `category`.

- [ ] **Step 1: Append the failing tests to `tests/test_profiles.py`**

```python
import asyncio
import json


def test_marketing_server_tools(marketing):
    cfg, _ = marketing
    from mcp import Client

    from dmx_docs.server import build_server

    async def run():
        async with Client(build_server(cfg)) as client:
            listed = {t.name: t for t in (await client.list_tools()).tools}
            res = await client.call_tool("search", {"query": "Paloma", "mode": "keyword", "category": "Brochures"})
            return listed, res

    listed, res = asyncio.run(run())
    assert set(listed) == {"search", "find_files", "list_folder", "read_document", "find_in_document",
                           "index_status", "view_page", "open_document", "export_image"}
    props = listed["search"].inputSchema["properties"]
    assert "category" in props and "project" not in props and "machine" not in props
    assert "Paloma brochure.pdf" in res.content[0].text and "Sales training" not in res.content[0].text


def test_instructions_and_name_follow_the_profile(marketing, corpus_copy, make_cfg):
    from dmx_docs.server import INSTRUCTIONS, instructions_for
    cfg, _ = marketing
    assert cfg.server_name == "dmx-marketing"
    assert "Brochures" in instructions_for(cfg) and "0_Vente" not in instructions_for(cfg)
    assert instructions_for(make_cfg(corpus_copy)) == INSTRUCTIONS


def test_claude_desktop_snippet_names_the_world(marketing):
    from dmx_docs.web import claude_desktop_snippet
    cfg, _ = marketing
    entry = json.loads(claude_desktop_snippet(cfg))["mcpServers"]["dmx-marketing"]
    assert entry["args"][-3:] == ["--world", "marketing", "serve"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `... -m pytest tests/test_profiles.py -v`
Expected: FAIL (`cannot import name 'instructions_for'`, tool list contains `list_projects`, snippet key `dmx-docs`).

- [ ] **Step 3: `src/dmx_docs/server.py`**

After the `INSTRUCTIONS` string add:

```python
MARKETING_INSTRUCTIONS = """\
Read-only access to the company's marketing documents (Demaurex: robotic packaging lines):
brochures, datasheets, presentations (sales, trainings, company), exhibitions, competition
analyses and publications; PDF, Word and PowerPoint, mostly English and French, also German.
Each search hit shows its category = the top folder: Brochures, Competition, Datasheets,
Exhibition, Graphics, Pictures, Presentations, Publications, Videos. PowerPoint files are read
slide by slide ("slide" instead of "page").

Never answer from the first search alone. Follow this loop for every question:

1. REFORMULATE the question into 2-4 search queries: English and French terms, product names
   (Paloma, Presto, Hector, Delfi, Astor, Nestor, FeedPlacer), exact names in "double quotes".
   Filter with category= when the kind of document is known (category="Competition" for
   competitors, "Datasheets" for technical data, "Presentations" for sales decks).
2. RETRIEVE with `search` (hybrid by default; mode="keyword" for names and codes); browse a
   category with `list_folder`.
3. EVALUATE each hit: does the excerpt answer the question or only share words? Read the best
   documents with `read_document` / `find_in_document`; look at pictures with `view_page`
   (PDF pages; for PowerPoint, page = slide and image = n-th picture on it).
4. RETRY with other words or another language if coverage is thin, at most 3 rounds.
5. SYNTHESIZE in the user's language, only from what you read. Cite every fact as full path +
   page or slide. Marketing material is promotional: say so when a figure only comes from a
   brochure. State clearly what was not found.
"""

GENERIC_INSTRUCTIONS = """\
Read-only access to the company's "{title}" documents (Demaurex: robotic packaging lines).
Search with `search` (2-4 reformulated queries, several languages), check each hit by reading it
with `read_document` / `find_in_document`, look at pictures with `view_page`, browse with
`list_folder`, and answer only from what you read, citing full path + page.
"""


def instructions_for(cfg: Config) -> str:
    if cfg.profile == "projects":
        return INSTRUCTIONS
    if cfg.profile == "marketing":
        return MARKETING_INSTRUCTIONS
    return GENERIC_INSTRUCTIONS.format(title=cfg.world_title or cfg.world)
```

In `build_server`, replace `mcp = MCPServer("dmx-docs", instructions=INSTRUCTIONS)` with:

```python
    mcp = MCPServer(cfg.server_name, instructions=instructions_for(cfg))
```

Delete the `@mcp.tool(**kw)` line directly above `def search(`, above `def list_projects(` and above `def project_card(` (the functions stay; they are registered below).

Before `    return mcp` at the end of `build_server`, add:

```python
    def marketing_search(query: str, folder: str | None = None, file_type: str | None = None,
                         modified_after: str | None = None, limit: int = 10, mode: str = "hybrid",
                         category: str | None = None, expand: bool | None = None) -> str:
        """Search the marketing documents by meaning and keywords. Returns excerpts with file path
        and page (slide for PowerPoint), how each one matched (keyword, semantic or both) and its
        meaning similarity to the query (strong >= 0.86, medium 0.83-0.86, weak < 0.83 = often off
        topic). These are hints: judge by reading.

        Args:
            query: What to look for (any language; documents are mostly English and French).
                Use "double quotes" for exact phrases or names, and word* for prefix matches.
            folder: Optional folder path to restrict the search to (includes subfolders).
            file_type: Optional 'pdf', 'docx', 'doc', 'pptx' or 'ppt'.
            modified_after: Optional date (YYYY, YYYY-MM or YYYY-MM-DD) - only files modified since then.
            limit: Number of results (1-30, default 10). At most 3 excerpts per document.
            mode: 'hybrid' (default), 'keyword' (exact words, codes, names) or 'semantic' (by meaning).
            category: Optional top folder(s), comma-separated: Brochures, Competition, Datasheets,
                Exhibition, Graphics, Pictures, Presentations, Publications, Videos.
            expand: Widen the keyword part with the company thesaurus. Default: as configured.
        """
        return tools.search(query, folder=folder, file_type=file_type, modified_after=modified_after,
                            limit=limit, mode=mode, category=category, expand=expand)

    def plain_search(query: str, folder: str | None = None, file_type: str | None = None,
                     modified_after: str | None = None, limit: int = 10, mode: str = "hybrid",
                     expand: bool | None = None) -> str:
        """Search the documents by meaning and keywords. Returns excerpts with file path and page,
        how each one matched and its meaning similarity to the query (strong >= 0.86, medium
        0.83-0.86, weak < 0.83). These are hints: judge by reading.

        Args:
            query: What to look for (any language). "double quotes" for exact phrases, word* for prefixes.
            folder: Optional folder path to restrict the search to (includes subfolders).
            file_type: Optional file type, e.g. 'pdf', 'docx', 'pptx'.
            modified_after: Optional date (YYYY, YYYY-MM or YYYY-MM-DD).
            limit: Number of results (1-30, default 10).
            mode: 'hybrid' (default), 'keyword' or 'semantic'.
            expand: Widen the keyword part with the company thesaurus. Default: as configured.
        """
        return tools.search(query, folder=folder, file_type=file_type, modified_after=modified_after,
                            limit=limit, mode=mode, expand=expand)

    # Tools that depend on the world's profile.
    if cfg.profile == "projects":
        mcp.tool(**kw)(search)
        mcp.tool(**kw)(list_projects)
        mcp.tool(**kw)(project_card)
    elif cfg.profile == "marketing":
        mcp.tool(name="search", **kw)(marketing_search)
    else:
        mcp.tool(name="search", **kw)(plain_search)

```

- [ ] **Step 4: `src/dmx_docs/web.py`**

Replace `claude_desktop_snippet` with:

```python
def claude_desktop_snippet(cfg: Config) -> str:
    config_path = str(cfg.config_path) if cfg.config_path else "config.toml"
    world = ["--world", cfg.world] if cfg.world else []
    return json.dumps({"mcpServers": {cfg.server_name: {
        "command": sys.executable,
        "args": ["-m", "dmx_docs.cli", "--config", config_path] + world + ["serve"],
    }}}, indent=2)
```

In `state()`, replace `"data_dir": str(cfg.data_dir),` with:

```python
            "data_dir": str(cfg.world_dir),
            "world": cfg.world_title or "",
```

In `src/dmx_docs/web/index.html` line 380, replace

```js
    $("settings-info").textContent = `File types: ${state.extensions.join(", ")} · Index stored in ${state.data_dir}` +
```

with

```js
    $("settings-info").textContent = (state.world ? `World: ${state.world} · ` : "") +
      `File types: ${state.extensions.join(", ")} · Index stored in ${state.data_dir}` +
```

- [ ] **Step 5: Run the tests**

Run: `... -m pytest tests/test_profiles.py tests/test_index_and_tools.py tests/test_sources_and_web.py -v` → all PASS (`test_mcp_server_lists_tools` still lists the 11 projects tools).

- [ ] **Step 6: Commit**

```bash
git add src/dmx_docs/server.py src/dmx_docs/web.py src/dmx_docs/web/index.html tests/test_profiles.py
git commit -m "Server per world: dmx-<world> name, marketing instructions and search, projects tools only for projects

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: PowerPoint .pptx text

**Files:**
- Modify: `pyproject.toml` (dependency, description)
- Modify: `src/dmx_docs/extract.py` (module docstring, `extract_file`, new PowerPoint section at the end)
- Modify: `src/dmx_docs/tools.py` (`TYPE_LABEL`, `_page_label`, new `_units`, `read_document` header)
- Modify: `tests/conftest.py` (helpers `make_png`, `make_pptx`)
- Create: `tests/test_pptx.py`

**Interfaces:**
- Produces: `extract.pptx_shapes(shapes)` (generator, groups flattened), `extract.pptx_slides(path) -> (list[str], str | None)`, `extract.extract_pptx(path) -> Extracted`; `TYPE_LABEL[".pptx"] = "PowerPoint"`, `TYPE_LABEL[".ppt"] = "PowerPoint 97-2003"`; `_page_label(".pptx") == "slide"`; conftest `make_png(path, rgb, size=(40, 20))`, `make_pptx(path, pictures=())` (slide 1: title "Paloma 4R", subtitle, notes; slide 2: title "Technical data", 2x2 table Cadence/120 ppm/Robots/4, grouped text box "Hygienic design"; slide 3: the given pictures only). Fixture `deck_tools` in `tests/test_pptx.py` (reused by Task 6).

- [ ] **Step 1: Test helpers in `tests/conftest.py`** (after `make_docx`)

```python
def make_png(path, rgb, size=(40, 20)):
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, *size), False)
    pix.set_rect(pix.irect, tuple(rgb))
    pix.save(str(path))


def make_pptx(path, pictures=()):
    """Slide 1: title, subtitle, speaker notes. Slide 2: title, table, text box inside a group.
    Slide 3: the given pictures only (no text)."""
    from pptx import Presentation
    from pptx.util import Inches

    prs = Presentation()
    s1 = prs.slides.add_slide(prs.slide_layouts[0])  # Title Slide
    s1.shapes.title.text = "Paloma 4R"
    s1.placeholders[1].text = "Pick and place robot for biscuits"
    s1.notes_slide.notes_text_frame.text = "Mention the washdown version."
    s2 = prs.slides.add_slide(prs.slide_layouts[5])  # Title Only
    s2.shapes.title.text = "Technical data"
    table = s2.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(6), Inches(1)).table
    for (r, c), text in {(0, 0): "Cadence", (0, 1): "120 ppm", (1, 0): "Robots", (1, 1): "4"}.items():
        table.cell(r, c).text = text
    group = s2.shapes.add_group_shape()
    group.shapes.add_textbox(Inches(1), Inches(4), Inches(3), Inches(1)).text_frame.text = "Hygienic design"
    s3 = prs.slides.add_slide(prs.slide_layouts[6])  # Blank
    for i, picture in enumerate(pictures):
        s3.shapes.add_picture(str(picture), Inches(1 + 3 * i), Inches(1))
    prs.save(str(path))
```

- [ ] **Step 2: Write the failing tests** (`tests/test_pptx.py`)

```python
import sys

import pytest

from conftest import make_png, make_pptx
from dmx_docs.extract import extract_file
from dmx_docs.indexer import run_index
from dmx_docs.tools import DocTools

quiet = lambda *a, **k: None  # noqa: E731


def test_pptx_text_per_slide(tmp_path):
    deck = tmp_path / "Deck.pptx"
    make_pptx(deck)
    r = extract_file(str(deck))
    assert r.status == "ok" and r.n_pages == 3
    pages = dict(r.pages)
    assert pages[1].startswith("# Paloma 4R")
    assert "Pick and place robot for biscuits" in pages[1]
    assert "Notes: Mention the washdown version." in pages[1]
    assert "Cadence | 120 ppm" in pages[2] and "Robots | 4" in pages[2]
    assert "Hygienic design" in pages[2]           # text box inside a group
    assert pages[2].count("Technical data") == 1   # the title is not repeated
    assert 3 not in pages                          # pictures only: no text


def test_broken_pptx_is_an_error_not_a_crash(tmp_path):
    bad = tmp_path / "bad.pptx"
    bad.write_bytes(b"not a zip file")  # also what an encrypted (password) .pptx looks like to python-pptx
    r = extract_file(str(bad))
    assert r.status == "error" and r.error


@pytest.fixture
def deck_tools(tmp_path, make_cfg):
    root = tmp_path / "Marketing"
    (root / "Presentations").mkdir(parents=True)
    red, blue = tmp_path / "red.png", tmp_path / "blue.png"
    make_png(red, (255, 0, 0))
    make_png(blue, (0, 0, 255))
    make_pptx(root / "Presentations" / "Deck.pptx", pictures=(red, blue))
    cfg = make_cfg(root, extensions=[".pdf", ".pptx", ".ppt"], world="marketing", profile="marketing")
    run_index(cfg, progress=quiet)
    return DocTools(cfg), root / "Presentations" / "Deck.pptx"


def test_pptx_is_searched_and_read_by_slide(deck_tools):
    t, deck = deck_tools
    assert "Deck.pptx — slide 2/3 (PowerPoint" in t.search("Hygienic", mode="keyword")
    text = t.read_document(str(deck))
    assert "3 slides" in text and "--- slide 2 ---" in text and "[no text on this page]" in text
```

- [ ] **Step 3: Run them to see them fail**

Run: `... -m pytest tests/test_pptx.py -v`
Expected: FAIL (`status == "skipped"`, unsupported extension .pptx).

- [ ] **Step 4: `pyproject.toml`**

Add `"python-pptx>=1.0",` after `"python-docx>=1.1",` and change the description to `"Index file-server documentation (PDF, Word, PowerPoint) and chat with it through Claude via MCP."`.

- [ ] **Step 5: `src/dmx_docs/extract.py`**

Module docstring first line: `"""Text extraction from PDF, DOCX, DOC, PPTX and PPT files.`

In `extract_file`, after the `.doc` branch add:

```python
        if ext == ".pptx":
            return extract_pptx(fs)
```

At the end of the file add:

```python
# -------------------------------------------------------------- PowerPoint

def pptx_shapes(shapes):
    """The shapes of a slide in order, the shapes inside groups included."""
    from pptx.shapes.group import GroupShape

    for shape in shapes:
        if isinstance(shape, GroupShape):
            yield from pptx_shapes(shape.shapes)
        else:
            yield shape


def _pptx_table_text(table) -> str:
    lines = []
    for row in table.rows:
        cells: list[str] = []
        for cell in row.cells:
            t = " ".join(cell.text.split())
            if not cells or cells[-1] != t:  # merged cells repeat their text
                cells.append(t)
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def pptx_slides(path: str) -> tuple[list[str], str | None]:
    """Text of each slide: its title as a heading, the other shapes in order (groups and
    placeholders included), tables, then the speaker notes. SmartArt and charts are left out."""
    from pptx import Presentation

    prs = Presentation(path)
    try:
        title = (prs.core_properties.title or "").strip() or None
    except Exception:
        title = None
    slides = []
    for slide in prs.slides:
        parts = []
        heading = slide.shapes.title
        heading_id = heading.shape_id if heading is not None else None
        if heading is not None and heading.has_text_frame and heading.text_frame.text.strip():
            parts.append("# " + " ".join(heading.text_frame.text.split()))
        for shape in pptx_shapes(slide.shapes):
            if shape.shape_id == heading_id:
                continue
            if shape.has_text_frame:
                text = shape.text_frame.text.replace("\v", "\n").strip()  # \v = line break in a paragraph
            elif getattr(shape, "has_table", False):
                text = _pptx_table_text(shape.table)
            else:
                continue
            if text:
                parts.append(text)
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame
            if notes is not None and notes.text.strip():
                parts.append("Notes: " + notes.text.replace("\v", "\n").strip())
        slides.append(clean_text("\n\n".join(parts)))
    return slides, title


def extract_pptx(path: str) -> Extracted:
    slides, title = pptx_slides(path)
    pages = [(i + 1, t) for i, t in enumerate(slides) if t]
    if not pages:
        return Extracted(status="empty", n_pages=len(slides), title=title)
    return Extracted(status="ok", pages=pages, n_pages=len(slides), title=title)
```

- [ ] **Step 6: `src/dmx_docs/tools.py`**

```python
TYPE_LABEL = {".pdf": "PDF", ".docx": "Word", ".doc": "Word 97-2003",
              ".pptx": "PowerPoint", ".ppt": "PowerPoint 97-2003"}
SLIDE_EXTS = (".pptx", ".ppt")
```

Replace `_page_label` with:

```python
def _page_label(ext: str) -> str:
    if ext in SLIDE_EXTS:
        return "slide"
    return "page" if ext == ".pdf" else "page (approx.)"


def _units(ext: str) -> str:
    return "slides" if ext in SLIDE_EXTS else "pages"
```

In `read_document`, replace the header line

```python
        header = (f"{path} ({TYPE_LABEL.get(info.get('ext'), info.get('ext'))}, {last} pages, "
```

with

```python
        header = (f"{path} ({TYPE_LABEL.get(info.get('ext'), info.get('ext'))}, {last} {_units(info.get('ext', ''))}, "
```

- [ ] **Step 7: Run the tests**

Run: `... -m pytest tests/test_pptx.py tests/test_extract.py tests/test_index_and_tools.py -v` → all PASS.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/dmx_docs/extract.py src/dmx_docs/tools.py tests/conftest.py tests/test_pptx.py
git commit -m "PowerPoint .pptx: one slide = one page (titles, text, groups, tables, notes)

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Old PowerPoint .ppt (LibreOffice or PowerPoint)

**Files:**
- Modify: `src/dmx_docs/extract.py` (`_convert_with_libreoffice` target, `extract_file`, new converter code)
- Modify: `src/dmx_docs/indexer.py:91-114` (`_kill_automation_word` → `_kill_automation_office`) and end of the extraction phase
- Modify: `tests/test_pptx.py` (append)

**Interfaces:**
- Consumes: `extract_pptx` (Task 4), conftest `make_pptx`.
- Produces: `extract.NoConverter(ValueError)`; `extract.convert_ppt(path, out_dir, converter="auto", libreoffice_path=None) -> str` (path of the .pptx); `extract.extract_ppt(path, converter="auto", libreoffice_path=None) -> Extracted`; `extract.kill_office_automation(*exe_names)`; `extract._powerpoint_installed() -> bool`; `extract.POWERPOINT_TIMEOUT_S = 120`; `_convert_with_libreoffice(soffice, path, out_dir, target="docx")`.

- [ ] **Step 1: Append the failing tests to `tests/test_pptx.py`**

```python
def test_ppt_without_converter_is_skipped(tmp_path, monkeypatch):
    from dmx_docs import extract
    monkeypatch.setattr(extract, "find_libreoffice", lambda configured=None: None)
    monkeypatch.setattr(extract, "_powerpoint_installed", lambda: False)
    old = tmp_path / "old.ppt"
    old.write_bytes(b"old powerpoint")
    r = extract.extract_file(str(old))
    assert r.status == "skipped" and "no .ppt converter" in r.error


def test_ppt_converter_choice(tmp_path, monkeypatch):
    from dmx_docs import extract
    calls = []
    monkeypatch.setattr(extract, "find_libreoffice", lambda configured=None: "soffice.exe")
    monkeypatch.setattr(extract, "_convert_with_libreoffice",
                        lambda soffice, path, out_dir, target="docx": calls.append(("lo", target)) or "x.pptx")
    monkeypatch.setattr(extract, "_powerpoint_installed", lambda: True)
    monkeypatch.setattr(extract, "_convert_with_powerpoint", lambda path, out_dir: calls.append(("ppt",)) or "y.pptx")
    assert extract.convert_ppt("a.ppt", str(tmp_path)) == "x.pptx"                    # auto: LibreOffice first
    assert extract.convert_ppt("a.ppt", str(tmp_path), converter="word") == "y.pptx"  # Microsoft Office
    assert calls == [("lo", "pptx"), ("ppt",)]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows semaphores")
def test_stuck_powerpoint_times_out_and_frees_the_slot(tmp_path, monkeypatch):
    import threading

    from dmx_docs import extract
    released = threading.Event()

    class FakePresentations:
        def Open(self, *args):
            released.wait(30)  # a hidden dialog: Open returns only when PowerPoint is killed
            raise RuntimeError("The RPC server is unavailable.")

    class FakeApp:
        Presentations = FakePresentations()

    monkeypatch.setattr(extract, "POWERPOINT_TIMEOUT_S", 1)
    monkeypatch.setattr(extract, "_powerpoint", lambda: FakeApp())
    monkeypatch.setattr(extract, "kill_office_automation", lambda *names: released.set())
    with pytest.raises(TimeoutError, match="within 1 s"):
        extract._convert_with_powerpoint(str(tmp_path / "stuck.ppt"), str(tmp_path))
    released.clear()
    with pytest.raises(TimeoutError, match="within 1 s"):  # not "blocked": the slot was released
        extract._convert_with_powerpoint(str(tmp_path / "stuck2.ppt"), str(tmp_path))


def _has_powerpoint():
    from dmx_docs.extract import _powerpoint_installed
    return _powerpoint_installed()


@pytest.mark.skipif(not _has_powerpoint(), reason="Microsoft PowerPoint + pywin32 not available")
def test_ppt_conversion_with_powerpoint(tmp_path):
    import pythoncom
    import win32com.client

    from dmx_docs import extract
    deck = tmp_path / "Deck.pptx"
    make_pptx(deck)
    old = tmp_path / "Deck.ppt"
    pythoncom.CoInitialize()
    app = win32com.client.DispatchEx("PowerPoint.Application")
    pres = app.Presentations.Open(str(deck), True, False, False)
    pres.SaveAs(str(old), 1)  # ppSaveAsPresentation (PowerPoint 97-2003)
    pres.Close()
    try:
        r = extract.extract_file(str(old), {"doc_converter": "word"})
        assert r.status == "ok", r.error
        assert "Hygienic design" in dict(r.pages)[2]
    finally:
        extract.kill_office_automation("POWERPNT.EXE")
```

- [ ] **Step 2: Run them to see them fail**

Run: `... -m pytest tests/test_pptx.py -v`
Expected: FAIL (`module 'dmx_docs.extract' has no attribute '_powerpoint_installed'`...).

- [ ] **Step 3: `src/dmx_docs/extract.py`**

Replace `_convert_with_libreoffice` with:

```python
def _convert_with_libreoffice(soffice: str, path: str, out_dir: str, target: str = "docx") -> str:
    # A private profile per call lets several worker processes convert at once.
    profile = os.path.join(out_dir, "lo_profile")
    profile_url = "file:///" + profile.replace("\\", "/").lstrip("/")
    subprocess.run(
        [soffice, f"-env:UserInstallation={profile_url}", "--headless", "--norestore",
         "--convert-to", target, "--outdir", out_dir, path],
        check=True, capture_output=True, timeout=180,
    )
    out = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0] + "." + target)
    if not os.path.exists(out):
        raise RuntimeError("LibreOffice produced no output")
    return out
```

In `extract_file`, after the `.pptx` branch add:

```python
        if ext == ".ppt":
            return extract_ppt(path, options.get("doc_converter", "auto"), options.get("libreoffice_path"))
```

At the end of the file add:

```python
class NoConverter(ValueError):
    """No program is installed to convert an old Office format."""


def kill_office_automation(*exe_names: str) -> None:
    """End the Office instances started by automation (/Automation or -Embedding on their
    command line). The user's own Word or PowerPoint windows are left alone."""
    if sys.platform != "win32" or not exe_names:
        return
    names = " OR ".join(f"Name='{n}'" for n in exe_names)
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    f"Get-CimInstance Win32_Process -Filter \"{names}\" | "
                    "Where-Object { $_.CommandLine -match '/Automation|-Embedding' } | "
                    "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
                   capture_output=True, timeout=60)


def _powerpoint_installed() -> bool:
    if sys.platform != "win32" or not _word_available():  # _word_available: pywin32 is installed
        return False
    import winreg

    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"PowerPoint.Application\CurVer"))
        return True
    except OSError:
        return False


POWERPOINT_TIMEOUT_S = 120  # a .ppt conversion taking longer is stuck (usually an invisible dialog)
_ppt_slots = None


def _ppt_slot():
    """Machine-wide semaphore: PowerPoint runs one instance shared by every worker process."""
    global _ppt_slots
    if _ppt_slots is None:
        import win32event

        _ppt_slots = win32event.CreateSemaphore(None, 1, 1, "dmx_docs_powerpoint")
    return _ppt_slots


def _powerpoint():
    """The running PowerPoint (possibly the user's own) or a new hidden one. Never quit here:
    the indexer ends the automation instances after the run (kill_office_automation)."""
    import pythoncom
    import win32com.client

    pythoncom.CoInitialize()
    app = win32com.client.DispatchEx("PowerPoint.Application")
    try:
        app.DisplayAlerts = 1        # ppAlertsNone
        app.AutomationSecurity = 3   # msoAutomationSecurityForceDisable: no macros
    except Exception:
        pass
    return app


def _convert_with_powerpoint(path: str, out_dir: str) -> str:
    import threading

    import win32event

    out = os.path.join(out_dir, "converted.pptx")
    slot = _ppt_slot()
    if win32event.WaitForSingleObject(slot, POWERPOINT_TIMEOUT_S * 3 * 1000) != win32event.WAIT_OBJECT_0:
        raise TimeoutError("PowerPoint is blocked by another .ppt conversion")
    timed_out = threading.Event()

    def kill():
        timed_out.set()
        kill_office_automation("POWERPNT.EXE")

    watchdog = threading.Timer(POWERPOINT_TIMEOUT_S, kill)
    watchdog.start()
    try:
        app = _powerpoint()
        # ReadOnly, Untitled=False, WithWindow=False: nothing appears on screen.
        pres = app.Presentations.Open(os.path.abspath(path), True, False, False)
        try:
            pres.SaveAs(out, 24)  # ppSaveAsOpenXMLPresentation (.pptx)
        finally:
            pres.Close()
    except Exception:
        if timed_out.is_set():
            raise TimeoutError(f"PowerPoint did not convert the file within {POWERPOINT_TIMEOUT_S} s") from None
        raise
    finally:
        watchdog.cancel()
        win32event.ReleaseSemaphore(slot, 1)
    if not os.path.exists(out):
        raise RuntimeError("PowerPoint produced no output")
    return out


def convert_ppt(path: str, out_dir: str, converter: str = "auto", libreoffice_path: str | None = None) -> str:
    """Convert an old .ppt into a .pptx in out_dir and return its path. 'auto' prefers LibreOffice;
    'word' (= Microsoft Office) uses PowerPoint."""
    soffice = find_libreoffice(libreoffice_path) if converter in ("auto", "libreoffice") else None
    if soffice:
        return _convert_with_libreoffice(soffice, path, out_dir, target="pptx")
    if converter in ("auto", "word") and _powerpoint_installed():
        return _convert_with_powerpoint(path, out_dir)
    raise NoConverter("no .ppt converter (install LibreOffice or Microsoft PowerPoint + pywin32)")


def extract_ppt(path: str, converter: str = "auto", libreoffice_path: str | None = None) -> Extracted:
    with tempfile.TemporaryDirectory(prefix="dmx_ppt_") as tmp:
        try:
            converted = convert_ppt(path, tmp, converter, libreoffice_path)
        except NoConverter as e:
            return Extracted(status="skipped", error=str(e))
        return extract_pptx(converted)
```

- [ ] **Step 4: `src/dmx_docs/indexer.py`**

Change the import `from .extract import EXTRACT_VERSION, Extracted, extract_file` to
`from .extract import EXTRACT_VERSION, Extracted, extract_file, kill_office_automation`.

Replace `_kill_automation_word` with:

```python
def _kill_automation_office() -> None:
    """End the Word/PowerPoint instances started for .doc/.ppt conversion (they outlive killed
    workers; PowerPoint is never quit by the workers). The user's own windows are left alone."""
    try:
        kill_office_automation("WINWORD.EXE", "POWERPNT.EXE")
    except Exception as e:  # noqa: BLE001
        log.warning("could not stop Word/PowerPoint: %s", e)
```

In `_kill_pool`, replace `_kill_automation_word()` with `_kill_automation_office()`.

In `run_index`, right after the loop that retries crashed files alone (after `writer.save(path, size, mtime, _extract_alone(path, options, check_stop), existed)`, still inside the `try`), add at the `for` loop's indentation level:

```python
        if ".ppt" in cfg.extensions:
            _kill_automation_office()  # the hidden PowerPoint started for conversions
```

- [ ] **Step 5: Run the tests**

Run: `... -m pytest tests/test_pptx.py tests/test_extract.py tests/test_robustness.py -v` → all PASS (the PowerPoint test runs on this laptop: PowerPoint is installed).

- [ ] **Step 6: Commit**

```bash
git add src/dmx_docs/extract.py src/dmx_docs/indexer.py tests/test_pptx.py
git commit -m "Old PowerPoint .ppt: converted by LibreOffice or PowerPoint, with time limit

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Pictures of PowerPoint slides (view_page, export_image)

**Files:**
- Modify: `src/dmx_docs/extract.py` (new `PICTURE_EXTS`, `pptx_pictures`)
- Modify: `src/dmx_docs/tools.py` (`_scaled_picture`, `_docx_picture`, new `_slide_picture_raw`, `view_page`, `export_image`)
- Modify: `src/dmx_docs/server.py` (docstrings of `view_page`, `export_image`)
- Modify: `tests/test_pptx.py` (append)

**Interfaces:**
- Consumes: `pptx_shapes`, `convert_ppt` (Tasks 4-5), fixture `deck_tools` (Task 4).
- Produces: `extract.pptx_pictures(path) -> list[list[tuple[bytes, str]]]`; `tools._slide_picture_raw(fs, path, slide, image, options) -> (bytes, ext, k, total_on_slide, n_slides)`; `tools._scaled_picture(raw, ext) -> (bytes, fmt, width, height)`.

- [ ] **Step 1: Append the failing test to `tests/test_pptx.py`**

```python
def test_slide_pictures_view_and_export(deck_tools, tmp_path):
    from dmx_docs.extract import pymupdf
    t, deck = deck_tools
    caption, data, _ = t.view_page(str(deck), page=3, image=2)
    assert "picture 2 of 2 on slide 3/3" in caption
    assert tuple(pymupdf.Pixmap(data).pixel(5, 5)) == (0, 0, 255)
    with pytest.raises(ValueError, match="Slide 1 has no picture.*Slides with pictures: 3"):
        t.view_page(str(deck), page=1)
    with pytest.raises(ValueError, match="between 1 and 3"):
        t.view_page(str(deck), page=4)
    t.cfg.export_dir = str(tmp_path / "exports")
    out = t.export_image(str(deck), page=3, image=1)
    target = tmp_path / "exports" / "Deck_s3_img1.png"
    assert str(target) in out and tuple(pymupdf.Pixmap(str(target)).pixel(5, 5)) == (255, 0, 0)
```

- [ ] **Step 2: Run it to see it fail**

Run: `... -m pytest tests/test_pptx.py::test_slide_pictures_view_and_export -v`
Expected: FAIL (`Pictures of PowerPoint files cannot be shown`).

- [ ] **Step 3: `src/dmx_docs/extract.py`** (end of file)

```python
PICTURE_EXTS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff")


def pptx_pictures(path: str) -> list[list[tuple[bytes, str]]]:
    """Embedded pictures of each slide in shape order: one list of (bytes, '.png') per slide.
    Vector pictures (WMF/EMF) and linked pictures are left out: they cannot be shown."""
    from pptx import Presentation
    from pptx.shapes.picture import Picture

    slides = []
    for slide in Presentation(path).slides:
        pictures = []
        for shape in pptx_shapes(slide.shapes):
            if not isinstance(shape, Picture):
                continue
            try:
                image = shape.image
            except Exception:  # linked to an outside file, not embedded
                continue
            ext = "." + image.ext.lower()
            if ext in PICTURE_EXTS:
                pictures.append((image.blob, ext))
        slides.append(pictures)
    return slides
```

- [ ] **Step 4: `src/dmx_docs/tools.py`**

Replace `_docx_picture` with:

```python
def _scaled_picture(raw: bytes, ext: str) -> tuple[bytes, str, int, int]:
    """A picture file, scaled down for Claude when larger than VIEW_MAX_PX: (data, format, w, h)."""
    from .extract import pymupdf

    with pymupdf.open(stream=raw, filetype=ext.lstrip(".")) as img:
        p = img[0]
        zoom = min(1.0, VIEW_MAX_PX / max(p.rect.width, p.rect.height))
        pix = p.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    data, fmt = _encode(pix)
    return data, fmt, pix.width, pix.height


def _docx_picture(fs: str, path: str, image: int | None) -> tuple[str, bytes, str]:
    raw, ext, k, total = _docx_picture_raw(fs, image)
    data, fmt, w, h = _scaled_picture(raw, ext)
    caption = (f"{path} — picture {k} of {total} embedded in the Word file (document order; Word "
               f"files have no fixed pages), {w}x{h} px. Use image=N for the others.")
    return caption, data, fmt


def _slide_picture_raw(fs: str, path: str, slide: int, image: int | None,
                       options: dict) -> tuple[bytes, str, int, int, int]:
    """Original bytes of a picture on a PowerPoint slide: (data, extension, number, pictures on
    the slide, slides). Old .ppt files are converted first (LibreOffice or PowerPoint)."""
    import tempfile

    from .extract import convert_ppt, pptx_pictures

    with tempfile.TemporaryDirectory(prefix="dmx_view_") as tmp:
        src = fs
        if path.lower().endswith(".ppt"):
            src = convert_ppt(path, tmp, options.get("doc_converter", "auto"), options.get("libreoffice_path"))
        slides = pptx_pictures(src)
    n = len(slides)
    if not 1 <= slide <= n:
        raise ValueError(f"page (slide number) must be between 1 and {n}")
    pictures = slides[slide - 1]
    if not pictures:
        others = [str(i + 1) for i, p in enumerate(slides) if p]
        raise ValueError(f"Slide {slide} has no picture that can be shown. " + (
            f"Slides with pictures: {', '.join(others[:50])}" if others else "This file has no pictures."))
    k = 1 if image is None else int(image)
    if not 1 <= k <= len(pictures):
        raise ValueError(f"image must be between 1 and {len(pictures)} (pictures on slide {slide})")
    data, ext = pictures[k - 1]
    return data, ext, k, len(pictures), n
```

In `view_page`, replace the last `raise ValueError(...)` (after the `.docx` branch) with:

```python
        if ext in SLIDE_EXTS:
            raw, pic_ext, k, total, n = _slide_picture_raw(fs, path, int(page), image, self.cfg.extract_options())
            data, fmt, w, h = _scaled_picture(raw, pic_ext)
            return (f"{path} — picture {k} of {total} on slide {page}/{n}, {w}x{h} px. Use image=N for "
                    "the others (whole slides cannot be shown, only their pictures)."), data, fmt
        raise ValueError(f"Pictures of {TYPE_LABEL.get(ext, ext)} files cannot be shown "
                         "(only PDF pages and pictures in .docx and PowerPoint files).")
```

In `export_image`, replace the `else: raise ValueError(...)` branch with:

```python
        elif ext in SLIDE_EXTS:
            data, out_ext, k, total, _ = _slide_picture_raw(fs, path, int(page), image, self.cfg.extract_options())
            default, what = f"{stem}_s{int(page)}_img{k}", f"picture {k} of {total} on slide {page}"
        else:
            raise ValueError(f"Images can only be exported from PDF pages and pictures in .docx and "
                             f"PowerPoint files, not {ext}")
```

- [ ] **Step 5: `src/dmx_docs/server.py` docstrings**

In `view_page`, replace the first two docstring lines and the `page`/`image` arg lines with:

```python
        """Look at a document as an image: a PDF page as it is printed (drawings, schematics,
        layouts, photos, tables), a picture embedded in a Word .docx (photos of FAT reports...), or
        a picture on a PowerPoint slide (page = slide number, image = n-th picture on it).
```
```python
            page: PDF page number, or PowerPoint slide number (default 1).
```
```python
            image: For .docx files: number of the embedded picture (1 = first, document order).
                For PowerPoint: number of the picture on the slide (1 = first).
```

In `export_image`, replace `or a picture embedded in a .docx (original resolution)` with `or a picture embedded in a .docx or on a PowerPoint slide (original resolution)`, and the arg lines with:

```python
            page: PDF page number, or PowerPoint slide number (default 1).
```
```python
            image: For .docx files: number of the embedded picture (1 = first). For PowerPoint:
                number of the picture on the slide.
```

- [ ] **Step 6: Run the tests**

Run: `... -m pytest tests/test_pptx.py tests/test_view_page.py -v` → all PASS.

- [ ] **Step 7: Commit**

```bash
git add src/dmx_docs/extract.py src/dmx_docs/tools.py src/dmx_docs/server.py tests/test_pptx.py
git commit -m "view_page / export_image: pictures of PowerPoint slides

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: dmx.ps1 per world, update command, migration, launchers

**Files:**
- Modify: `scripts/dmx.ps1` (whole file below)
- Modify: `scripts/launchers/2 - Configuration page.cmd`, `3 - Index new and changed files.cmd`, `5 - Update Claude Desktop copy.cmd`, `Unlock after a crash.cmd`
- Create: `scripts/launchers/7 - Update a world (index + embeddings).cmd`
- Create: `tests/test_dmx_ps1.py`

**Interfaces:**
- Consumes: CLI `--world` (Task 1), server names (Task 3).
- Produces: `dmx.ps1 <command> [-World name | name] [-Force] [args]`, commands `setup web index embed update pull status search unlock migrate`; environment variable `DMX_RAG_LOCAL` (tests only) replaces `C:\dmx-rag`.

- [ ] **Step 1: Write the failing tests** (`tests/test_dmx_ps1.py`)

```python
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
```

- [ ] **Step 2: Run them to see them fail**

Run: `... -m pytest tests/test_dmx_ps1.py -v`
Expected: FAIL (`Cannot validate argument on parameter 'Command'` for `migrate`).

- [ ] **Step 3: Replace `scripts/dmx.ps1` with**

```powershell
<#
dmx-docs on several machines that share one network folder (e.g. U:\DMX-RAG).

A world is one family of documents with its own index and its own audience (projects,
marketing...). The shared folder holds the code, the settings and the master copy of each
world's index. SQLite must not run on a network share, so every command that changes an index
works on a local copy:  lock the world -> copy its master to C:\dmx-rag\data\<world> -> run ->
copy back -> unlock. One machine at a time per world; different worlds can run at the same time.

Shared folder layout (this script lives in <shared>\app\scripts):
  <shared>\app\               git clone of chat-with-dmx
  <shared>\config.toml        settings used by every machine (data_dir = 'C:\dmx-rag\data'),
                              with one [worlds.<name>] table per world
  <shared>\thesaurus.toml     search vocabulary shared by every world
  <shared>\worlds\<world>\    index.sqlite3 (master), index.prev.sqlite3 (previous), LOCK,
                              register.csv (projects: machine register)
  <shared>\models\            embedding model, copied to each machine once
  <shared>\tools\uv.exe       builds the local Python environment (no admin rights needed)
  <shared>\logs\              one log per run

Commands. All but setup and migrate work on one world: -World <name>, or the world's name as
first argument; otherwise the script asks (Enter = projects).
  setup          build/update C:\dmx-rag\venv (GPU packages on NVIDIA machines)
  web            configuration page (folders, exclusions, scans), checked in when closed
  index          scan for new/changed/deleted files
  embed [args]   compute embeddings, e.g. embed --max-minutes 300
  update         index, then embed: unattended run, e.g. on the GPU machine
  pull           refresh this machine's read-only copy for Claude Desktop (no lock)
  status         index statistics of the local copy
  search "..."   test a search on the local copy
  unlock -Force  remove a stale lock left by a machine that crashed
  migrate        one-time move from the single-index layout (every command does it first)
#>
param(
    [Parameter(Position = 0, Mandatory = $true)]
    [ValidateSet('setup', 'web', 'index', 'embed', 'update', 'pull', 'status', 'search', 'unlock', 'migrate')]
    [string]$Command,
    [string]$World,
    [switch]$Force,
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Rest
)

$ErrorActionPreference = 'Stop'
$App = Split-Path $PSScriptRoot -Parent
$Shared = Split-Path $App -Parent
$SharedConfig = Join-Path $Shared 'config.toml'
$SharedModels = Join-Path $Shared 'models'
$Logs = Join-Path $Shared 'logs'
$Uv = Join-Path $Shared 'tools\uv.exe'

# DMX_RAG_LOCAL: the tests run this script against a temporary folder instead of C:\dmx-rag.
$Local = if ($env:DMX_RAG_LOCAL) { $env:DMX_RAG_LOCAL } else { 'C:\dmx-rag' }
$LocalData = Join-Path $Local 'data'
$LocalModels = Join-Path $LocalData 'models'
$LocalConfig = Join-Path $Local 'config.toml'
$Venv = Join-Path $Local 'venv'
$Py = Join-Path $Venv 'Scripts\python.exe'
$Stamp = Join-Path $Local 'installed.txt'
$Rest = @($Rest | Where-Object { $_ })

# Set by Set-World: the world's folders on the share and on this machine.
$SharedWorld = $Master = $Lock = $LocalWorld = $LocalDb = $null
$WorldArgs = @()

# Keep uv's Python and cache on the local disk, not in a roaming profile.
$env:UV_PYTHON_INSTALL_DIR = Join-Path $Local 'python'
$env:UV_CACHE_DIR = Join-Path $Local 'uv-cache'
$env:PYTHONIOENCODING = 'utf-8'
$env:PYTHONUNBUFFERED = '1'   # live progress through the pipe
[Console]::OutputEncoding = [Text.Encoding]::UTF8

function Say($msg) { Write-Host "[dmx] $msg" -ForegroundColor Cyan }

function Invoke-Native([string]$exe, [string[]]$arguments, [switch]$AllowFail) {
    # Output goes through the pipeline so it also lands in the log (Start-Transcript only
    # records what PowerShell writes). Windows PowerShell 5.1 turns redirected stderr lines
    # into errors, hence 'Continue' and the unwrapping.
    $eap = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    try {
        & $exe @arguments 2>&1 | ForEach-Object {
            if ($_ -is [System.Management.Automation.ErrorRecord]) { $_.Exception.Message } else { "$_" }
        } | Out-Host
    } finally { $ErrorActionPreference = $eap }
    if ($LASTEXITCODE -ne 0 -and -not $AllowFail) { throw "$([IO.Path]::GetFileName($exe)) exited with code $LASTEXITCODE" }
}

function Get-CodeStamp {
    # This script is part of the stamp: a change in how it installs triggers a reinstall.
    $files = @(Get-Item (Join-Path $App 'pyproject.toml'), $PSCommandPath) + @(Get-ChildItem (Join-Path $App 'src') -Recurse -File)
    $latest = ($files | Measure-Object -Property LastWriteTimeUtc -Maximum).Maximum
    "$($latest.Ticks)|gpu=$(Test-Gpu)"
}

function Test-Gpu { [bool](Get-Command nvidia-smi -ErrorAction SilentlyContinue) }

function Install-Env {
    if (-not (Test-Path $Uv)) { throw "uv.exe not found in $(Split-Path $Uv)" }
    New-Item -ItemType Directory -Force $Local, $LocalData, (Join-Path $Local 'exports') | Out-Null  # exports: export_image
    if (-not (Test-Path $Py)) {
        Say "Creating the Python 3.12 environment in $Venv ..."
        Invoke-Native $Uv @('venv', $Venv, '--python', '3.12')
    }
    Say "Installing dmx-docs from $App ..."
    Invoke-Native $Uv @('pip', 'install', '--python', $Py, '--reinstall-package', 'dmx-docs', $App)
    if (Test-Gpu) {
        # onnxruntime-gpu >= 1.28 is built for CUDA 13 (driver R580+); 1.26 is the last CUDA 12 build.
        $smi = (nvidia-smi | Out-String)
        $cuda = if ($smi -match 'CUDA Version:\s*(\d+)') { [int]$Matches[1] } else { 0 }
        $ort = switch ($cuda) { { $_ -ge 13 } { 'onnxruntime-gpu[cuda,cudnn]' } 12 { 'onnxruntime-gpu[cuda,cudnn]==1.26.*' } default { $null } }
        if ($ort) {
            Say "NVIDIA GPU found (driver supports CUDA $cuda): installing $ort ..."
            Invoke-Native $Uv @('pip', 'uninstall', '--python', $Py, 'onnxruntime', 'fastembed') -AllowFail
            # The CPU and GPU packages share their folders (onnxruntime\, fastembed\): uninstalling
            # the CPU ones deletes files of the GPU ones, so those are always reinstalled.
            Invoke-Native $Uv @('pip', 'install', '--python', $Py, '--reinstall-package', 'onnxruntime-gpu',
                                '--reinstall-package', 'fastembed-gpu', 'fastembed-gpu>=0.8,<0.9', $ort)
            Invoke-Native $Py @('-c', "import fastembed, onnxruntime as o; p = o.get_available_providers(); print('GPU runtime check:', p); assert 'CUDAExecutionProvider' in p")
        } else {
            Say "NVIDIA GPU found but its driver is too old for CUDA 12 (nvidia-smi says CUDA $cuda): embeddings will use the CPU."
        }
    }
    Get-CodeStamp | Set-Content -Encoding ascii $Stamp
    $hasWord = Test-Path 'Registry::HKEY_CLASSES_ROOT\Word.Application\CurVer'
    $hasPpt = Test-Path 'Registry::HKEY_CLASSES_ROOT\PowerPoint.Application\CurVer'
    $hasLo = (Test-Path 'C:\Program Files\LibreOffice\program\soffice.exe') -or (Test-Path 'C:\Program Files (x86)\LibreOffice\program\soffice.exe')
    if (-not ($hasWord -or $hasLo)) { Say 'Note: neither Word nor LibreOffice is installed here, so .doc files are skipped if you index from this machine.' }
    if (-not ($hasPpt -or $hasLo)) { Say 'Note: neither PowerPoint nor LibreOffice is installed here, so .ppt files are skipped if you index from this machine.' }
    Say 'Environment ready.'
}

function Assert-Env {
    if (-not (Test-Path $Py) -or -not (Test-Path $Stamp) -or (Get-Content $Stamp) -ne (Get-CodeStamp)) { Install-Env }
}

function Sync-Models {
    # The model is 2.2 GB: copy it from the share once instead of downloading it on every machine.
    if ((Test-Path $SharedModels) -and -not (Test-Path (Join-Path $LocalModels '*'))) {
        Say 'Copying the embedding model from the shared folder (first time on this machine) ...'
        robocopy $SharedModels $LocalModels /E /NFL /NDL /NJH /NP | Out-Null
    }
}

function Publish-Models {
    if (-not (Test-Path (Join-Path $SharedModels '*')) -and (Test-Path (Join-Path $LocalModels '*'))) {
        Say 'Saving the embedding model to the shared folder for the other machines ...'
        robocopy $LocalModels $SharedModels /E /NFL /NDL /NJH /NP | Out-Null
    }
}

# ------------------------------------------------------------------ worlds

function Get-Worlds {
    # World names, in the order of the [worlds.<name>] tables of the shared config.
    $text = Get-Content $SharedConfig -Raw
    @([regex]::Matches($text, '(?m)^\s*\[worlds\.([a-z0-9_-]+)\]') | ForEach-Object { $_.Groups[1].Value })
}

function Resolve-World {
    $known = @(Get-Worlds)
    if (-not $known) { throw "No [worlds.<name>] table in $SharedConfig" }
    if (-not $script:World -and $script:Rest.Count -gt 0 -and $known -contains $script:Rest[0]) {
        $script:World = $script:Rest[0]   # e.g. "7 - Update a world.cmd" marketing
        $script:Rest = @($script:Rest | Select-Object -Skip 1)
    }
    if (-not $script:World) {
        $answer = Read-Host "World ($($known -join ', ')) [projects]"
        $script:World = if ($answer.Trim()) { $answer.Trim().ToLower() } else { 'projects' }
    }
    if ($known -notcontains $script:World) { throw "Unknown world '$($script:World)'. Configured worlds: $($known -join ', ')" }
}

function Set-World {
    $script:SharedWorld = Join-Path $Shared "worlds\$World"
    $script:Master = Join-Path $SharedWorld 'index.sqlite3'
    $script:Lock = Join-Path $SharedWorld 'LOCK'
    $script:LocalWorld = Join-Path $LocalData $World
    $script:LocalDb = Join-Path $LocalWorld 'index.sqlite3'
    $script:WorldArgs = @('--world', $World)
}

function Get-ServedWorld([string]$commandLine) {
    if ($commandLine -match '--world\s+"?([a-z0-9_-]+)') { $Matches[1] } else { 'projects' }  # no --world: the default
}

function Stop-LocalServers([string]$world) {
    # Claude Desktop's server of that world keeps its local index open; Claude Desktop restarts it by itself.
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
        Where-Object { $_.CommandLine -match 'dmx_docs\.cli' -and $_.CommandLine -match ' serve' -and
                       $_.CommandLine -match [regex]::Escape($LocalConfig) -and
                       (Get-ServedWorld $_.CommandLine) -eq $world } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Move-LegacyLayout {
    # Before worlds there was one index (<shared>\data, C:\dmx-rag\data): it becomes the
    # "projects" world. Renames only, never copies; does nothing once done.
    $oldData = Join-Path $Shared 'data'
    $oldMaster = Join-Path $oldData 'index.sqlite3'
    $projects = Join-Path $Shared 'worlds\projects'
    if ((Test-Path $oldMaster) -and -not (Test-Path (Join-Path $projects 'index.sqlite3'))) {
        $oldLock = Join-Path $oldData 'LOCK'
        if (Test-Path $oldLock) {
            throw "The index is being worked on with the old layout ($((Get-Content $oldLock -Raw).Trim())). Let that run finish (or unlock it), then run this again."
        }
        Say 'One-time move of the shared index to worlds\projects ...'
        New-Item -ItemType Directory -Force $projects | Out-Null
        Move-Item $oldMaster $projects
        $prev = Join-Path $oldData 'index.prev.sqlite3'
        if (Test-Path $prev) { Move-Item $prev $projects }
        $reg = Join-Path $Shared 'register.csv'
        if (Test-Path $reg) { Move-Item $reg $projects }
        if (-not (Get-ChildItem $oldData -Force)) { Remove-Item $oldData }
    }
    $oldLocal = Join-Path $LocalData 'index.sqlite3'
    $localProjects = Join-Path $LocalData 'projects'
    if ((Test-Path $oldLocal) -and -not (Test-Path (Join-Path $localProjects 'index.sqlite3'))) {
        Say 'One-time move of the local index to data\projects ...'
        Copy-Item $SharedConfig $LocalConfig -Force   # a restarted server must find the new layout
        New-Item -ItemType Directory -Force $localProjects | Out-Null
        $moved = $false
        for ($try = 1; $try -le 5 -and -not $moved; $try++) {
            Stop-LocalServers 'projects'
            Start-Sleep -Milliseconds 500
            try { Move-Item $oldLocal $localProjects -ErrorAction Stop; $moved = $true } catch { }
        }
        if (-not $moved) {
            throw 'The local index is in use (Claude Desktop?). Quit Claude Desktop completely and run this again. Nothing was moved on this machine.'
        }
        foreach ($name in 'index.sqlite3-wal', 'index.sqlite3-shm') {
            $p = Join-Path $LocalData $name
            if (Test-Path $p) { Move-Item $p $localProjects -Force -ErrorAction SilentlyContinue }
        }
        # Search caches are rebuilt if one cannot be moved.
        Get-ChildItem $LocalData -Filter 'vec_*' -File | ForEach-Object { Move-Item $_.FullName $localProjects -Force -ErrorAction SilentlyContinue }
        $oldLogs = Join-Path $LocalData 'logs'
        if (Test-Path $oldLogs) { Move-Item $oldLogs $localProjects -ErrorAction SilentlyContinue }
        $oldReg = Join-Path $Local 'register.csv'
        if (Test-Path $oldReg) { Move-Item $oldReg $localProjects -Force }
    }
}

# --------------------------------------------------------- lock and copies

function Get-LockInfo { if (Test-Path $Lock) { (Get-Content $Lock -Raw).Trim() } }

function Enter-Lock([string]$what) {
    New-Item -ItemType Directory -Force $SharedWorld | Out-Null
    $info = "$env:COMPUTERNAME|$env:USERNAME|$(Get-Date -Format 'yyyy-MM-dd HH:mm')|$what|$PID"
    try {
        $fs = [IO.File]::Open($Lock, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write)
        $bytes = [Text.Encoding]::ASCII.GetBytes($info)
        $fs.Write($bytes, 0, $bytes.Length); $fs.Close()
        return $false   # fresh lock: start from the master copy
    } catch [IO.IOException] {
        $held = Get-LockInfo
        $fields = if ($held) { $held.Split('|') } else { @() }
        if ($fields.Count -ge 5 -and $fields[0] -eq $env:COMPUTERNAME -and
            (Get-Process -Id ([int]$fields[4]) -ErrorAction SilentlyContinue | Where-Object ProcessName -match 'powershell')) {
            throw "'$($fields[3])' is still running on this machine for '$World' (started $($fields[2])). Wait for it to finish, or stop it first."
        }
        if ($held -and $fields[0] -eq $env:COMPUTERNAME) {
            Say "This machine already holds the lock of '$World' ($held): continuing with its local copy, which has unsaved work."
            [IO.File]::WriteAllText($Lock, $info)   # this process owns it now
            return $true
        }
        throw "The '$World' index is in use by another machine: $held`nWait for it to finish. If that machine crashed, run: dmx.ps1 unlock -World $World -Force"
    }
}

function Exit-Lock { Remove-Item $Lock -Force -ErrorAction SilentlyContinue }

function Remove-LocalDb {
    # Returns $false if the file stays in use (the old copy is then left untouched).
    for ($try = 1; $try -le 5; $try++) {
        if (-not (Test-Path $LocalDb)) { break }
        Stop-LocalServers $World
        Start-Sleep -Milliseconds 500
        try { [IO.File]::Delete($LocalDb) } catch { }
    }
    if (Test-Path $LocalDb) { return $false }
    Remove-Item "$LocalDb-wal", "$LocalDb-shm" -Force -ErrorAction SilentlyContinue
    return $true
}

function Copy-MasterToLocal {
    # Never copy over the local index in place: a copy interrupted by a reader corrupts it.
    New-Item -ItemType Directory -Force $LocalWorld | Out-Null
    $new = "$LocalDb.new"
    if (Test-Path $Master) {
        Say "Copying the '$World' index from the shared folder ..."
        Copy-Item $Master $new -Force
    }
    if (-not (Remove-LocalDb)) {
        Remove-Item $new -Force -ErrorAction SilentlyContinue
        throw "The local '$World' index is in use (Claude Desktop?). Quit Claude Desktop completely and run this again. The current local copy was left as it was."
    }
    if (Test-Path $new) { [IO.File]::Move($new, $LocalDb) }
    else { Say "No '$World' index in the shared folder yet: starting a new one." }
}

function Save-LocalToMaster {
    if (-not (Test-Path $LocalDb)) { return $true }
    Say 'Checking the local index ...'
    $check = & $Py -c @"
import sqlite3, sys
con = sqlite3.connect(sys.argv[1])
con.execute('PRAGMA wal_checkpoint(TRUNCATE)')
print(con.execute('PRAGMA quick_check').fetchone()[0])
con.close()
"@ $LocalDb
    if ($check -ne 'ok') {
        Say "The local index failed its integrity check ($check). It was NOT copied to the shared folder; the lock is kept."
        return $false
    }
    Say "Copying the '$World' index back to the shared folder ..."
    $tmp = "$Master.tmp"
    Copy-Item $LocalDb $tmp -Force
    if (Test-Path $Master) { Move-Item $Master (Join-Path $SharedWorld 'index.prev.sqlite3') -Force }
    Move-Item $tmp $Master -Force
    Copy-Item $SharedConfig $LocalConfig -Force
    return $true
}

function Invoke-Locked([string]$what, [object[]]$steps) {
    # $steps: one argument list per dmx-docs command, run in order on the same local copy.
    # Lock first: never reinstall packages under a run that is still going on this machine.
    $resumed = Enter-Lock $what
    $working = $resumed   # true once the local copy holds this run's (or an unsaved run's) work
    $ok = $false
    try {
        Assert-Env
        Sync-Models
        if (-not $resumed -or -not (Test-Path $LocalDb)) { Copy-MasterToLocal }
        $working = $true
        foreach ($dmxArgs in $steps) {
            Say "Running: dmx-docs $(($WorldArgs + $dmxArgs) -join ' ')"
            Invoke-Native $Py (@('-m', 'dmx_docs.cli', '--config', $SharedConfig) + $WorldArgs + $dmxArgs) -AllowFail
            Say "dmx-docs finished (exit code $LASTEXITCODE)."
            if ($LASTEXITCODE -ne 0) { Say 'Stopped: the next steps are skipped.'; break }
        }
        $ok = $true
    } finally {
        # Also runs after Ctrl+C: what was done so far is kept (the index is resumable).
        if (-not $working) {
            Exit-Lock; Say 'Nothing was changed; lock released.'   # failed before touching the index
        } elseif ((Test-Path $Py) -and (Save-LocalToMaster)) {
            Exit-Lock; Say "Done. The '$World' index in the shared folder is up to date and unlocked."
        }
        if ($ok -and $what -in 'embed', 'update') { Publish-Models }
    }
}

# -------------------------------------------------------------------- main

if (-not (Test-Path $SharedConfig)) { throw "Settings not found: $SharedConfig" }
$needsWorld = $Command -notin 'setup', 'migrate'
if ($needsWorld) { Resolve-World; Set-World }
New-Item -ItemType Directory -Force $Logs | Out-Null
$tag = if ($needsWorld) { "$($World)_$Command" } else { $Command }
$log = Join-Path $Logs ("{0}_{1}_{2}.log" -f (Get-Date -Format 'yyyyMMdd-HHmmss'), $env:COMPUTERNAME, $tag)
Start-Transcript -Path $log -Append | Out-Null
try {
    Move-LegacyLayout
    switch ($Command) {
        'setup' { Install-Env; Sync-Models }
        'migrate' { Say 'The layout is up to date.' }
        'web' { Invoke-Locked 'web' @(, (@('web') + $Rest)) }
        'index' { Invoke-Locked 'index' @(, (@('index') + $Rest)) }
        'embed' { Invoke-Locked 'embed' @(, (@('embed') + $Rest)) }
        'update' { Invoke-Locked 'update' @(@('index'), @('embed')) }
        'pull' {
            Assert-Env
            Sync-Models
            $held = Get-LockInfo
            if ($held -and $held.Split('|')[0] -eq $env:COMPUTERNAME) { throw "This machine holds the lock of '$World' with unsaved work ($held): run the interrupted command again first." }
            if ($held) { Say "Note: $($held.Split('|')[0]) is working on '$World' right now; you get the last saved version." }
            if (-not (Test-Path $Master)) { throw "World '$World' has no index in the shared folder yet: index it first (launcher 3 or 7)." }
            Copy-MasterToLocal
            Copy-Item $SharedConfig $LocalConfig -Force
            $th = Join-Path $Shared 'thesaurus.toml'
            if (Test-Path $th) { Copy-Item $th (Join-Path $Local 'thesaurus.toml') -Force }  # search synonyms
            $reg = Join-Path $SharedWorld 'register.csv'
            if (Test-Path $reg) { Copy-Item $reg (Join-Path $LocalWorld 'register.csv') -Force }  # machine register
            # Build the vector cache and facets now, so Claude's first question is fast.
            Say 'Preparing the search cache (about 30-60 s) ...'
            Invoke-Native $Py @('-c', "from dmx_docs.config import load_config; from dmx_docs.tools import DocTools; DocTools(load_config(r'$LocalConfig', world='$World')).search('warm-up', limit=1)") -AllowFail
            Invoke-Native $Py (@('-m', 'dmx_docs.cli', '--config', $LocalConfig) + $WorldArgs + @('status')) -AllowFail
            $name = if ($World -eq 'projects') { 'dmx-docs' } else { "dmx-$World" }
            $serverArgs = @('-m', 'dmx_docs.cli', '--config', $LocalConfig) + $WorldArgs + @('serve')
            $snippet = @{ mcpServers = @{ $name = @{ command = $Py; args = $serverArgs } } } | ConvertTo-Json -Depth 5
            Say "Claude Desktop configuration for this machine:`n$snippet"
        }
        'status' { Assert-Env; Invoke-Native $Py (@('-m', 'dmx_docs.cli', '--config', $SharedConfig) + $WorldArgs + @('status')) -AllowFail }
        'search' { Assert-Env; Invoke-Native $Py (@('-m', 'dmx_docs.cli', '--config', $SharedConfig) + $WorldArgs + @('search') + $Rest) -AllowFail }
        'unlock' {
            $held = Get-LockInfo
            if (-not $held) { Say "Not locked ($World)." }
            elseif (-not $Force) { Say "Locked by: $held`nRun again with -Force to remove the lock (only if that machine is no longer working on it)." }
            else { Exit-Lock; Say "Lock of '$World' removed (was: $held)." }
        }
    }
} finally {
    Stop-Transcript | Out-Null
}
```

- [ ] **Step 4: Launchers** (`scripts/launchers/`)

`2 - Configuration page.cmd`:
```bat
@echo off
echo Configuration page: folders to index, exclusions, scans.
echo STOP IT WITH CTRL+C IN THIS WINDOW (not the X), so the index is saved back to the shared folder.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\dmx.ps1" web %*
pause
```

`3 - Index new and changed files.cmd`:
```bat
@echo off
rem Scans the root folders of a world for new, changed and deleted files. Ctrl+C stops it; the next run continues.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\dmx.ps1" index %*
pause
```

`5 - Update Claude Desktop copy.cmd`:
```bat
@echo off
rem Copies the latest index of a world to this machine for Claude Desktop and prints the Claude Desktop configuration.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\dmx.ps1" pull %*
pause
```

`7 - Update a world (index + embeddings).cmd`:
```bat
@echo off
rem Unattended update of one world: index new and changed files, then compute embeddings.
rem From a cmd prompt: "7 - Update a world (index + embeddings).cmd" marketing   (double-click: it asks)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\dmx.ps1" update %*
pause
```

`Unlock after a crash.cmd`:
```bat
@echo off
rem Only if the machine that holds the lock is no longer working on that world's index.
set W=%~1
if "%W%"=="" set /p W=World (projects, marketing) [projects]: 
if "%W%"=="" set W=projects
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\dmx.ps1" unlock -World %W%
choice /m "Remove the lock"
if errorlevel 2 exit /b
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0app\scripts\dmx.ps1" unlock -World %W% -Force
pause
```

(`1 - Setup this machine.cmd` and `4 - Compute embeddings.cmd` are unchanged: 4 already passes `%*`.)

- [ ] **Step 5: Run the tests**

Run: `... -m pytest tests/test_dmx_ps1.py -v` → all PASS. Also a parse check with the PowerShell tool (not Git Bash, which would expand `$`):
`$e = $null; [void][System.Management.Automation.Language.Parser]::ParseFile('D:\JOHAN\Private\Python\github\chat-with-dmx-worlds\scripts\dmx.ps1', [ref]$null, [ref]$e); $e.Count` → `0`.

- [ ] **Step 6: Commit**

```bash
git add scripts/dmx.ps1 scripts/launchers tests/test_dmx_ps1.py
git commit -m "dmx.ps1: worlds (per-world lock and copies), update command, one-time migration, launcher 7

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Documentation and helper scripts

**Files:**
- Modify: `README.md` (section 6), `config.example.toml`
- Modify (outside git, on the share): `U:\DMX-RAG\register-work\import_register.py:1,19`, `U:\DMX-RAG\thesaurus-work\build_draft.py:14`, `U:\DMX-RAG\thesaurus-work\verify_testset.py:9`, `U:\DMX-RAG\6 - Import machine register.cmd`

- [ ] **Step 1: README section 6** — replace the code block of the shared folder layout and the bullet list after it with:

````markdown
Each family of documents is a **world** (projects, marketing...) with its own index, so each
can be shared with its own audience. Worlds are declared in `config.toml`
(`[worlds.<name>]`: title, profile, file types, first root folders); every command takes
`--world <name>` (default: projects).

```
U:\DMX-RAG\
  1 - Setup this machine.cmd           builds C:\dmx-rag\venv (GPU runtime on NVIDIA machines)
  2 - Configuration page.cmd           folders, exclusions, scans (stop with Ctrl+C)
  3 - Index new and changed files.cmd
  4 - Compute embeddings.cmd           from a cmd prompt you can add e.g. --max-minutes 300
  5 - Update Claude Desktop copy.cmd   local copy for Claude Desktop + the configuration to paste
  7 - Update a world (index + embeddings).cmd   unattended run, e.g. on the GPU machine
  Unlock after a crash.cmd
  config.toml      settings for all machines (data_dir = 'C:\dmx-rag\data') + [worlds.<name>]
  thesaurus.toml   search vocabulary shared by every world
  app\             this repository (git pull here to update; machines reinstall automatically)
  worlds\<name>\   index.sqlite3, index.prev.sqlite3 (previous version), LOCK, register.csv (projects)
  models\          embedding model, copied to each machine once
  tools\uv.exe     installs Python and the packages without admin rights
  logs\            one log per run
```

* Every launcher asks for the world (Enter = projects); from a cmd prompt give it as first
  argument, e.g. `"7 - Update a world (index + embeddings).cmd" marketing`.
* One machine at a time works on a world; two machines can work on two different worlds.
* Local copies are in `C:\dmx-rag\data\<world>`. Claude Desktop gets one server per world:
  `dmx-docs` for projects, `dmx-<world>` for the others (launcher 5 prints the entry).
* The launchers are in `scripts\launchers` and the logic in `scripts\dmx.ps1`. The first run
  of the new script moves the old single index (`data\`) to `worlds\projects` (renames only).
* Use UNC paths for root folders (`\\server\share\...`): drive letters can differ between machines.
* Indexing `.doc` files needs Word or LibreOffice, `.ppt` files PowerPoint or LibreOffice, on
  the machine that indexes.
* If a machine crashes during a run, run the same command again on that machine: it continues
  from its local copy. From another machine, *Unlock after a crash* releases the lock (the
  unsaved work of the crashed run is then lost).
````

In the "How it works" list, add after the Extraction bullet:
`* PowerPoint: python-pptx, one slide = one page (titles, text, tables, speaker notes); .ppt converted to .pptx first.`

- [ ] **Step 2: `config.example.toml`**

Replace the extensions comment and line with:

```toml
# File types to index. '.doc' (Word 97-2003) needs LibreOffice or Microsoft Word installed for
# conversion, '.ppt' (PowerPoint 97-2003) LibreOffice or Microsoft PowerPoint; remove them from
# the list to skip those files. A world can have its own list (see [worlds] below).
extensions = ['.pdf', '.docx', '.doc']
```

Replace the converter comment with:

```toml
# How to convert old .doc/.ppt files: 'auto' (LibreOffice, then Microsoft Office), 'libreoffice',
# 'word' (Microsoft Office: Word for .doc, PowerPoint for .ppt), or 'none'.
```

Append at the end:

```toml

# Worlds: separate indexes for separate families of documents, each shared with its own
# audience (local files in data_dir\<world>). Choose one with `dmx-docs --world <name>`
# (default: projects). Without [worlds] tables there is a single index directly in data_dir.
# profile: 'projects' (project folders, machine register), 'marketing' (category = first
# folder) or 'none'. A world may set its own extensions, exclude and roots (first-run seed).
# [worlds.projects]
# title = "Projects"
# profile = "projects"
#
# [worlds.marketing]
# title = "Marketing"
# profile = "marketing"
# extensions = ['.pdf', '.docx', '.doc', '.pptx', '.ppt']
# roots = ['\\server\share\Marketing']
```

- [ ] **Step 3: Commit (worktree)**

```bash
git add README.md config.example.toml
git commit -m "Docs: worlds, launcher 7, PowerPoint

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

- [ ] **Step 4: Helper scripts on the share (not in git; do it in Task 9 right after the merge)**

`U:\DMX-RAG\register-work\import_register.py`: docstring first line `U:\\DMX-RAG\\register.csv` → `U:\\DMX-RAG\\worlds\\projects\\register.csv`; line 19:

```python
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "worlds", "projects", "register.csv")
```

`U:\DMX-RAG\thesaurus-work\build_draft.py:14` and `verify_testset.py:9`: default DB
`r"C:\dmx-rag\data\projects\index.sqlite3"`.

`U:\DMX-RAG\6 - Import machine register.cmd`: last echo becomes
`echo Then run "5 - Update Claude Desktop copy.cmd" (world projects) so Claude sees the new register.`

---

### Task 9: Rollout

**Files:** shared `U:\DMX-RAG\config.toml`, launchers in `U:\DMX-RAG\`, the laptop's Claude Desktop config. Each step that changes something outside the worktree is confirmed with the user first.

- [ ] **Step 1: Whole suite in the worktree**

Run: `... -m pytest -q` → no failure (≈ 105 passed, LibreOffice test skipped).

- [ ] **Step 2: Nobody is working on the index**

Run: `ls /u/DMX-RAG/data` → no `LOCK`. If there is one, wait.

- [ ] **Step 3: Merge into the shared clone (machines install it on their next run)**

```bash
git -C /u/DMX-RAG/app merge --ff-only worlds
```

Then apply Task 8 Step 4 (helper scripts on the share).

- [ ] **Step 4: Shared config** (backup first: `cp /u/DMX-RAG/config.toml /u/DMX-RAG/config.toml.bak-$(date +%Y%m%d-%H%M%S)`), append:

```toml

# ------------------------------------------------------------------ worlds
# One index per family of documents: U:\DMX-RAG\worlds\<name>, C:\dmx-rag\data\<name>.
[worlds.projects]
title = "Projects"
profile = "projects"

[worlds.marketing]
title = "Marketing"
profile = "marketing"
extensions = ['.pdf', '.docx', '.doc', '.pptx', '.ppt']
roots = ['\\DMX-FS01.rotzingerag.local\Daten$\RMA_ORG\SAL\2_Groupes\22_Marketing']
```

Check: `cd /u/DMX-RAG/app && PYTHONPATH=src /c/dmx-rag/venv/Scripts/python.exe -c "from dmx_docs.config import load_config as l; print(l(r'U:\DMX-RAG\config.toml').db_path, l(r'U:\DMX-RAG\config.toml', world='marketing').roots)"`.

- [ ] **Step 5: Launchers on the share**

```bash
cp /u/DMX-RAG/app/scripts/launchers/*.cmd /u/DMX-RAG/
```

- [ ] **Step 6: Migrate and refresh Projects on the laptop** (user runs `5 - Update Claude Desktop copy.cmd`, Enter = projects, or with permission `powershell -File U:\DMX-RAG\app\scripts\dmx.ps1 pull -World projects`)

Expected: "One-time move of the shared index to worlds\projects", "One-time move of the local index to data\projects", reinstall, copy, status with the same counts as before (106,743 documents, ~1.73M chunks embedded). Then a question in Claude Desktop (existing `dmx-docs` entry, unchanged) still answers.

- [ ] **Step 7: Marketing on DMX-WS032** (the user, on the VM): double-click `U:\DMX-RAG\7 - Update a world (index + embeddings).cmd`, type `marketing`. Expected: reinstall (python-pptx), new index, about 1,100 documents, embeddings on the A4000, "Done. The 'marketing' index in the shared folder is up to date and unlocked." Check the log `U:\DMX-RAG\logs\*_DMX-WS032_marketing_update.log` for skipped `.ppt` (no converter) and errors.

- [ ] **Step 8: Marketing in Claude Desktop on the laptop**: run `5 - Update Claude Desktop copy.cmd` → `marketing`; with the user's OK, merge the printed `dmx-marketing` entry into `%APPDATA%\Claude\claude_desktop_config.json` (backup first), restart Claude Desktop, ask a test question (e.g. "Which brochures present the Paloma?").

- [ ] **Step 9: Finish**: ask the user to push (`git -C U:\DMX-RAG\app push`), then remove the worktree (`git -C /u/DMX-RAG/app worktree remove /d/JOHAN/Private/Python/github/chat-with-dmx-worlds` and `git -C /u/DMX-RAG/app branch -d worlds`), and update the deployment memory (worlds layout, launcher 7, dmx-marketing entry).

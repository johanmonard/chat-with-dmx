# World Maps Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `world_map` tool in every world that returns a hand-written map of what the world holds (and lacks) plus a live overview computed from the index, used by the skill to route questions; and a routing test that measures the gain.

**Architecture:** A new module `worldmap.py` reads `maps\<world>.md` (path from config, next to `config.toml`) and computes a live overview (folder tree with counts; for Projects the template folders aggregated across projects plus facet counts). `DocTools.world_map()` caches the overview 10 min and composes text + overview under a 12,000-character cap; `server.py` registers it for every profile and each world's instructions point to it. The skill calls it first; three map drafts ship in `maps\` and are copied to the machines by `dmx.ps1 pull`.

**Tech Stack:** Python 3.11, SQLite (existing index), MCP Python SDK (existing server), pytest, PowerShell 5.1 (`scripts/dmx.ps1`).

**Spec:** `docs/superpowers/specs/2026-10-13-world-maps-design.md`

## Refinements of the spec (found while planning)

- Claude Desktop serves from the local copy `C:\dmx-rag\config.toml`, so maps resolve to `C:\dmx-rag\maps\`. `dmx.ps1 pull` copies `U:\DMX-RAG\maps\*.md` there (like `thesaurus.toml`): an edit on U: is live after the next pull (no reindex, no skill upload, no restart since the file is read at each call).
- New CLI command `dmx-docs map` (and `dmx.ps1 map`, which reads the shared config, so maps on U: are previewed directly): prints what `world_map` returns. Used to review the maps and to feed variant B of the routing test before the code is deployed.
- Marketing's "counts per category" are the first level of the folder tree (category = top folder), not a separate line.
- Projects' template folders: rows of fewer than 5 documents are folded, and at most 25 rows are shown per section (largest first, then "+ N other folders").

## Global Constraints

- Python `>=3.11` (no backslash inside f-string expressions).
- Read-only: `world_map` never writes the index; map files are read with `encoding="utf-8-sig"`.
- Output cap: 12,000 characters total; the tree is cut first, with the line `... tree cut here (N more lines): browse with list_folder.`
- Fold threshold 5 documents; tree depth 3; cache 600 s per `DocTools` instance.
- Missing map: `No map file for this world (<path>): only the live overview below.`; no map configured: `No map file configured for this world: only the live overview below.`
- Footer: `Counts are from the index; the map text is maintained by hand and may lag behind them.`
- No real client names in repository files (tests, docstrings, maps). Project code names and folder names are allowed (they are already in the skill). The routing test set lives outside the repository in `U:\DMX-RAG\routing-work\`.
- Windows paths in files are written with the Write tool, never through Bash heredocs (backslashes get lost).
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **A map file saved by Notepad with a BOM, or in ANSI** - expected: BOM stripped (`utf-8-sig`); undecodable file reported like a missing one, never a crash. Test in Task 1 (`test_map_with_bom_and_bad_encoding`).
2. **Documents whose path is under no configured root** (root removed on the configuration page) - expected: skipped by the tree, never shown with a full UNC path as a folder. Test in Task 1 (`test_tree_ignores_paths_outside_roots`).
3. **An empty world (index built, 0 documents)** - expected: overview says 0 documents, no exception. Test in Task 2 (`test_world_map_on_empty_index`).
4. **A very long map text** (user pastes a lot) - expected: the map text is kept whole, the tree is cut, the total stays near the cap. Test in Task 1 (`test_compose_cuts_tree_not_map`).
5. **Projects facets missing for a document** (unknown project) - expected: counted in the tree under its section, project counted as unknown (not crashing on None). Covered by `test_template_folders_aggregate_across_projects` (one row with project None).

---

### Task 1: `worldmap.py` - map text, folder tree, template folders, compose

**Files:**
- Create: `src/dmx_docs/worldmap.py`
- Test: `tests/test_worldmap.py`

**Interfaces:**
- Consumes: `facets.section_of(folder: str) -> str | None`, `facets.root_of(path_key, root_keys) -> str | None`, `facets.SECTIONS` (dict, values in template order), `store.path_key(path) -> str`.
- Produces:
  - `load_map_text(map_path: str | None) -> str`
  - `folder_tree(rows: list[tuple[str, str]], roots: list[str], depth: int = 3, min_docs: int = 5) -> list[str]` (rows = `(path, path_key)`)
  - `template_folders(rows: list[tuple[str, str, str | None, str | None]], roots: list[str], min_docs: int = 5, max_rows: int = 25) -> list[str]` (rows = `(path, path_key, project, subproject)`)
  - `@dataclass Overview: head: list[str]; tree_title: str; tree: list[str]`
  - `compose(map_text: str, overview: Overview, max_chars: int = MAX_CHARS) -> str`
  - constants `MAX_CHARS = 12000`, `MIN_DOCS = 5`, `TREE_DEPTH = 3`, `CACHE_S = 600`, `FOOTER`

- [ ] **Step 1: Write the failing tests**

```python
"""World map: hand-written map text + live overview of the index (worldmap.py)."""

from dmx_docs import store, worldmap
from dmx_docs.worldmap import Overview

M = r"\\srv\Marketing"
P = r"\\srv\Projets"


def rows(paths):
    return [(p, store.path_key(p)) for p in paths]


def test_map_text_missing_or_not_configured(tmp_path):
    missing = tmp_path / "maps" / "marketing.md"
    assert worldmap.load_map_text(str(missing)) == (
        f"No map file for this world ({missing}): only the live overview below.")
    assert worldmap.load_map_text(None) == "No map file configured for this world: only the live overview below."


def test_map_with_bom_and_bad_encoding(tmp_path):
    good = tmp_path / "a.md"
    good.write_bytes("\ufeff# Marketing - map\nTrainings: Presentations\\Training Sales\n".encode("utf-8"))
    assert worldmap.load_map_text(str(good)).startswith("# Marketing - map")
    bad = tmp_path / "b.md"
    bad.write_bytes("Clôture".encode("cp1252"))  # saved as ANSI by an old editor
    assert worldmap.load_map_text(str(bad)) == f"No map file for this world ({bad}): only the live overview below."


def test_folder_tree_counts_and_folds_small_folders():
    paths = ([rf"{M}\Brochures\Marketing\a{i}.pdf" for i in range(6)]
             + [rf"{M}\Brochures\Old\b{i}.pdf" for i in range(2)]
             + [rf"{M}\Videos\v.jpg", rf"{M}\top.pdf"])
    assert worldmap.folder_tree(rows(paths), [M]) == [
        rf"{M} (10 docs)",
        "  - Brochures (8)",
        "    - Marketing (6)",
        "    - + 1 smaller folder (2 docs)",
        "  - + 1 smaller folder (1 doc)",
    ]


def test_folder_tree_stops_at_depth():
    paths = [rf"{M}\A\B\C\D\x{i}.pdf" for i in range(5)]
    assert worldmap.folder_tree(rows(paths), [M]) == [
        rf"{M} (5 docs)", "  - A (5)", "    - B (5)", "      - C (5)"]


def test_tree_ignores_paths_outside_roots():
    paths = [rf"{M}\Brochures\a{i}.pdf" for i in range(5)] + [r"\\other\share\Brochures\x.pdf"]
    assert worldmap.folder_tree(rows(paths), [M]) == [rf"{M} (5 docs)", "  - Brochures (5)"]


def test_template_folders_aggregate_across_projects():
    r = ([(rf"{P}\THOR\0_Vente\00_Offres\o{i}.pdf", "THOR") for i in range(5)]
         + [(rf"{P}\LEON\0_Vente\00_Offres\o{i}.pdf", "LEON") for i in range(5)]
         + [(rf"{P}\LEON\3_Mecanique\37_Materiel_Tiers\m{i}.pdf", "LEON") for i in range(6)]
         + [(rf"{P}\2_Hors_Garantie\ANGE\Clôture\c.pdf", "ANGE"),
            (rf"{P}\THOR\notes.pdf", "THOR"),
            (rf"{P}\X\3_Mecanique\37_Materiel_Tiers\n.pdf", None)])  # project facet unknown
    data = [(p, store.path_key(p), proj, None) for p, proj in r]
    assert worldmap.template_folders(data, [P]) == [
        "- Vente: 2 projects, 10 docs",
        "  - 0_Vente\\00_Offres: 2 projects, 10 docs",
        "- Mecanique: 2 projects, 7 docs",
        "  - 3_Mecanique\\37_Materiel_Tiers: 2 projects, 7 docs",
        "- Cloture: 1 project, 1 doc",
        "  - + 1 other folder (1 doc)",
        "- outside the template sections: 1 doc in 1 project",
    ]


def test_template_folders_cap_rows_per_section():
    r = [(rf"{P}\THOR\0_Vente\{k:02d}_Sub\o{i}.pdf", "THOR") for k in range(30) for i in range(5)]
    data = [(p, store.path_key(p), proj, None) for p, proj in r]
    lines = worldmap.template_folders(data, [P], max_rows=25)
    assert lines[0] == "- Vente: 1 project, 150 docs"
    assert len(lines) == 1 + 25 + 1
    assert lines[-1] == "  - + 5 other folders (25 docs)"


def test_compose_puts_map_first_then_overview_and_footer():
    ov = Overview(head=["## Live overview (from the index)", "Marketing (marketing): 10 documents"],
                  tree_title="Folders (documents):", tree=["x (10 docs)", "  - Brochures (8)"])
    out = worldmap.compose("# Marketing - map\nhello", ov)
    assert out.index("# Marketing - map") < out.index("## Live overview") < out.index("  - Brochures (8)")
    assert out.rstrip().endswith(worldmap.FOOTER)


def test_compose_cuts_tree_not_map():
    text = "# Map\n" + "m" * 5000
    ov = Overview(head=["## Live overview (from the index)"], tree_title="Folders (documents):",
                  tree=[f"  - folder {i} ({i})" for i in range(2000)])
    out = worldmap.compose(text, ov, max_chars=8000)
    assert text in out
    assert len(out) <= 8000
    assert "... tree cut here (" in out and "more lines): browse with list_folder." in out
    assert out.rstrip().endswith(worldmap.FOOTER)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_worldmap.py -v` (or `C:\dmx-rag\venv\Scripts\python.exe -m pytest` with `PYTHONPATH=src`)
Expected: FAIL with `ImportError: cannot import name 'worldmap'`

- [ ] **Step 3: Write the implementation**

```python
"""World map: what a world holds, where, and what it lacks.

The text is written by people in maps\\<world>.md next to config.toml (see load_config); the live
overview under it is computed from the index, so new folders show up before the text mentions them.
"""

from __future__ import annotations

import functools
import os
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from . import facets, store

MAX_CHARS = 12000
MIN_DOCS = 5          # smaller folders are folded into one line
TREE_DEPTH = 3
CACHE_S = 600         # the live overview is recomputed at most every 10 min per server
FOOTER = "Counts are from the index; the map text is maintained by hand and may lag behind them."
SECTION_ORDER = list(dict.fromkeys(facets.SECTIONS.values()))  # Vente, Finances, ... Cloture


@dataclass
class Overview:
    head: list[str]
    tree_title: str
    tree: list[str]


def _n(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def load_map_text(map_path: str | None) -> str:
    """The hand-written map, or a one-line note saying why there is none."""
    if not map_path:
        return "No map file configured for this world: only the live overview below."
    try:
        text = Path(map_path).read_text(encoding="utf-8-sig").strip()
    except (OSError, UnicodeDecodeError):
        return f"No map file for this world ({map_path}): only the live overview below."
    return text or f"No map file for this world ({map_path}): only the live overview below."


def _folders(path: str, key: str, root_keys: list[tuple[str, str]]) -> tuple[str, list[str]] | None:
    """(root, folders between the root and the file), or None for a path under no root."""
    root = facets.root_of(key, root_keys)
    if root is None:
        return None
    parts = [p for p in os.path.relpath(path, root).replace("/", "\\").split("\\") if p and p != "."]
    return root, parts[:-1]


def folder_tree(rows, roots, depth: int = TREE_DEPTH, min_docs: int = MIN_DOCS) -> list[str]:
    """Folders under each root to `depth`, with their number of documents (subfolders included);
    folders under `min_docs` documents are folded into one line per parent. rows = (path, path_key)."""
    root_keys = [(store.path_key(r), r) for r in roots]
    counts = {r: Counter() for r in roots}
    for path, key in rows:
        got = _folders(path, key, root_keys)
        if not got:
            continue
        root, folders = got
        counts[root][()] += 1
        for i in range(1, min(depth, len(folders)) + 1):
            counts[root][tuple(folders[:i])] += 1
    lines: list[str] = []
    for root in roots:
        c = counts[root]
        children = defaultdict(list)
        for k in c:
            if k:
                children[k[:-1]].append(k)
        lines.append(f"{root} ({_n(c[()], 'doc')})")
        _walk(c, children, (), min_docs, lines)
    return lines


def _walk(c, children, parent, min_docs, lines) -> None:
    kids = sorted(children.get(parent, []), key=lambda k: k[-1].lower())
    small = [k for k in kids if c[k] < min_docs]
    for k in kids:
        if c[k] >= min_docs:
            lines.append("  " * len(k) + f"- {k[-1]} ({c[k]})")
            _walk(c, children, k, min_docs, lines)
    if small:
        lines.append("  " * (len(parent) + 1)
                     + f"- + {_n(len(small), 'smaller folder')} ({_n(sum(c[k] for k in small), 'doc')})")


@functools.lru_cache(maxsize=None)
def _section(folder: str) -> str | None:
    return facets.section_of(folder)


def template_folders(rows, roots, min_docs: int = MIN_DOCS, max_rows: int = 25) -> list[str]:
    """The project template aggregated over every project: per section, its folder and the folder
    below it (0_Vente\\00_Offres) with the number of projects and documents having it.
    rows = (path, path_key, project, subproject)."""
    root_keys = [(store.path_key(r), r) for r in roots]
    docs: Counter = Counter()
    projects: dict[tuple, set] = defaultdict(set)
    section_of_key: dict[tuple, str] = {}
    outside_docs, outside_projects = 0, set()
    for path, key, project, subproject in rows:
        got = _folders(path, key, root_keys)
        if not got:
            continue
        folders = got[1]
        pid = (project, subproject)
        k = next((i for i, f in enumerate(folders) if _section(f)), None)
        if k is None:
            outside_docs += 1
            outside_projects.add(pid)
            continue
        tk = tuple(folders[k:k + 2])
        section_of_key[tk] = _section(folders[k])
        docs[tk] += 1
        projects[tk].add(pid)
    by_section = defaultdict(list)
    for tk, sec in section_of_key.items():
        by_section[sec].append(tk)
    lines: list[str] = []
    for sec in sorted(by_section, key=lambda s: SECTION_ORDER.index(s) if s in SECTION_ORDER else 99):
        keys = by_section[sec]
        n_proj = len(set().union(*(projects[k] for k in keys)))
        lines.append(f"- {sec}: {_n(n_proj, 'project')}, {_n(sum(docs[k] for k in keys), 'doc')}")
        big = sorted((k for k in keys if docs[k] >= min_docs),
                     key=lambda k: (-len(projects[k]), -docs[k], "\\".join(k).lower()))
        shown = big[:max_rows]
        for k in shown:
            name = "\\".join(k)
            lines.append(f"  - {name}: {_n(len(projects[k]), 'project')}, {_n(docs[k], 'doc')}")
        rest = [k for k in keys if k not in shown]
        if rest:
            lines.append(f"  - + {_n(len(rest), 'other folder')} ({_n(sum(docs[k] for k in rest), 'doc')})")
    if outside_docs:
        lines.append(f"- outside the template sections: {_n(outside_docs, 'doc')} "
                     f"in {_n(len(outside_projects), 'project')}")
    return lines


def compose(map_text: str, overview: Overview, max_chars: int = MAX_CHARS) -> str:
    """Map text first (kept whole), then the overview; the tree is cut to stay under max_chars."""
    top = map_text.rstrip() + "\n\n" + "\n".join(overview.head) + "\n\n" + overview.tree_title + "\n"
    bottom = "\n\n" + FOOTER + "\n"
    budget = max_chars - len(top) - len(bottom)
    out: list[str] = []
    used = 0
    for i, line in enumerate(overview.tree):
        note = f"... tree cut here ({len(overview.tree) - i} more lines): browse with list_folder."
        if used + len(line) + 1 + len(note) + 1 > budget:
            out.append(note)
            break
        out.append(line)
        used += len(line) + 1
    return top + "\n".join(out) + bottom
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_worldmap.py -v`
Expected: PASS (9 tests)

- [ ] **Step 5: Commit**

```bash
git add src/dmx_docs/worldmap.py tests/test_worldmap.py
git commit -m "worldmap: map text, folder tree, project template folders, size cap"
```

### Task 2: config `map_path`, live overview, `DocTools.world_map`

**Files:**
- Modify: `src/dmx_docs/config.py` (field `map_path` after `register_path`; resolution in `load_config` after the thesaurus block)
- Modify: `src/dmx_docs/worldmap.py` (add `live_overview`)
- Modify: `src/dmx_docs/tools.py:343-346` (`DocTools.__init__`), add `world_map` after `index_status`
- Test: `tests/test_worlds.py`, `tests/test_worldmap.py`

**Interfaces:**
- Consumes: Task 1 (`Overview`, `folder_tree`, `template_folders`, `compose`, `load_map_text`, `CACHE_S`).
- Produces: `Config.map_path: str | None`; `worldmap.live_overview(con, cfg) -> Overview`; `DocTools.world_map() -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_worlds.py`:

```python
def test_map_path_defaults_to_maps_folder_next_to_config(config_file, tmp_path):
    assert load_config(config_file, world="marketing").map_path == str(tmp_path / "maps" / "marketing.md")


def test_map_path_override_is_relative_to_config(config_file, tmp_path):
    text = config_file.read_text(encoding="utf-8").replace(
        'title = "Marketing"', "title = \"Marketing\"\nmap = 'other\\mkt.md'")
    config_file.write_text(text, encoding="utf-8")
    assert load_config(config_file, world="marketing").map_path == str(tmp_path / "other" / "mkt.md")


def test_map_path_without_worlds(tmp_path, monkeypatch):
    monkeypatch.delenv("DMX_DOCS_WORLD", raising=False)
    p = tmp_path / "config.toml"
    p.write_text("[index]\ndata_dir = 'data'\n", encoding="utf-8")
    assert load_config(p).map_path == str(tmp_path / "maps" / "default.md")
```

Append to `tests/test_worldmap.py`:

```python
import pytest

from conftest import make_pdf
from dmx_docs.indexer import run_index
from dmx_docs.tools import DocTools

quiet = lambda *a, **k: None  # noqa: E731


@pytest.fixture
def marketing(tmp_path, make_cfg):
    root = tmp_path / "22_Marketing"
    (root / "Presentations" / "Training Sales").mkdir(parents=True)
    for i in range(5):
        make_pdf(str(root / "Presentations" / "Training Sales" / f"t{i}.pdf"), [f"Training {i}: coflow"])
    make_pdf(str(root / "Datasheets.pdf"), ["Paloma technical data"])
    map_file = tmp_path / "maps" / "marketing.md"
    map_file.parent.mkdir()
    map_file.write_text("# Marketing - map\nSales trainings: Presentations\\Training Sales\n", encoding="utf-8")
    cfg = make_cfg(root, world="marketing", world_title="Marketing", profile="marketing", map_path=str(map_file))
    run_index(cfg, progress=quiet)
    return cfg, root, map_file


def test_world_map_marketing(marketing):
    cfg, root, _ = marketing
    out = DocTools(cfg).world_map()
    assert out.startswith("# Marketing - map")
    assert "Marketing (marketing): 6 documents" in out
    assert "By file type: .pdf 6" in out
    assert f"{root} (6 docs)" in out and "    - Training Sales (5)" in out


def test_world_map_rereads_the_map_but_caches_the_overview(marketing):
    cfg, _, map_file = marketing
    tools = DocTools(cfg)
    tools.world_map()
    map_file.write_text("# Marketing - map v2\n", encoding="utf-8")
    assert tools.world_map().startswith("# Marketing - map v2")
    assert tools._overview is not None


def test_world_map_without_map_file(marketing):
    cfg, _, map_file = marketing
    map_file.unlink()
    assert DocTools(cfg).world_map().startswith("No map file for this world (")


def test_world_map_projects(corpus_copy, make_cfg):
    cfg = make_cfg(corpus_copy)  # profile projects, no world, no map configured
    run_index(cfg, progress=quiet)
    out = DocTools(cfg).world_map()
    assert out.startswith("No map file configured for this world")
    assert "Projects: " in out and "By section: " in out and "By document type: " in out
    assert "Template folders across projects" in out
    assert "outside the template sections" in out  # the test corpus has no template folders


def test_world_map_on_empty_index(tmp_path, make_cfg):
    root = tmp_path / "empty"
    root.mkdir()
    cfg = make_cfg(root, world="documentation", world_title="Documentation", profile="documentation")
    run_index(cfg, progress=quiet)
    out = DocTools(cfg).world_map()
    assert "Documentation (documentation): 0 documents" in out
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_worlds.py tests/test_worldmap.py -v`
Expected: FAIL (`map_path` unknown; `DocTools` has no `world_map`)

- [ ] **Step 3: Implement**

`config.py`, in `Config` after `register_path`:

```python
    map_path: str | None = None         # world map text (maps\<world>.md next to config.toml), see worldmap.py
```

`config.py`, in `load_config` right after the thesaurus block (`kwargs["expand_synonyms"] = ...`):

```python
    # The world map (world_map tool): maps\<world>.md next to config.toml, or [worlds.<name>] map.
    mp = Path(os.path.expandvars(str(w.get("map") or f"maps/{name or 'default'}.md")))
    kwargs["map_path"] = str(mp if mp.is_absolute() else (path.parent / mp))
```

`worldmap.py`, append:

```python
def _counts(con, sql: str) -> str:
    return ", ".join(f"{k if k is not None else '(unknown)'} {n}" for k, n in con.execute(sql).fetchall()) or "none"


def live_overview(con, cfg) -> Overview:
    """Counts and folders of the world, from the index."""
    from datetime import datetime

    last = store.get_meta(con, "last_index_finished")
    when = datetime.fromtimestamp(float(last)).strftime("%Y-%m-%d") if last else "never"
    title = f"{cfg.world_title} ({cfg.world})" if cfg.world else "Index"
    if cfg.profile == "projects":
        rows = [tuple(r) for r in con.execute(
            """SELECT d.path, d.path_key, f.project, f.subproject FROM docs d
               LEFT JOIN doc_facets f ON f.doc_id = d.id""") if cfg.allows(r[1])]
    else:
        rows = [tuple(r) for r in con.execute("SELECT path, path_key FROM docs") if cfg.allows(r[1])]
    head = ["## Live overview (from the index)",
            f"{title}: {len(rows)} documents; last full index run {when}."]
    if cfg.profile == "projects":
        head += [f"Projects: {len({r[2] for r in rows if r[2]})}",
                 "By collection: " + _counts(con, "SELECT collection, count(*) n FROM doc_facets GROUP BY 1 ORDER BY n DESC"),
                 "By section: " + _counts(con, "SELECT section, count(*) n FROM doc_facets GROUP BY 1 ORDER BY n DESC"),
                 "By document type: " + _counts(con, "SELECT doc_type, count(*) n FROM doc_facets GROUP BY 1 ORDER BY n DESC")]
        tree_title = ("Template folders across projects (section folder and the folder below it; "
                      "number of projects and documents having it):")
        tree = template_folders(rows, cfg.roots)
    else:
        head.append("By file type: " + _counts(con, "SELECT ext, count(*) n FROM docs GROUP BY 1 ORDER BY n DESC"))
        if cfg.profile == "documentation":
            head.append("By language: " + _counts(
                con, "SELECT language, count(*) n FROM doc_facets GROUP BY 1 ORDER BY n DESC"))
        tree_title = "Folders (documents, subfolders included; depth 3):"
        tree = folder_tree([r[:2] for r in rows], cfg.roots)
    if store.has_table(con, "picture_uses"):
        kinds = _counts(con, """SELECT type, count(*) n FROM picture_uses WHERE status = 'described'
                                GROUP BY 1 ORDER BY n DESC""")
        if kinds != "none":
            head.append("Described pictures (search with pictures=true): " + kinds)
    return Overview(head=head, tree_title=tree_title, tree=tree)
```

`tools.py`: add `import time` at the top if absent and `from . import worldmap` next to the other package imports; in `DocTools.__init__` after `self.searcher = Searcher(cfg)`:

```python
        self._overview: tuple[float, worldmap.Overview] | None = None  # world_map's live part, cached
```

`tools.py`, new method right after `index_status`:

```python
    def world_map(self) -> str:
        now = time.monotonic()
        if self._overview is None or now - self._overview[0] > worldmap.CACHE_S:
            con = self._con()
            try:
                self._overview = (now, worldmap.live_overview(con, self.cfg))
            finally:
                con.close()
        return worldmap.compose(worldmap.load_map_text(self.cfg.map_path), self._overview[1])
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_worlds.py tests/test_worldmap.py -v`
Expected: PASS. If `test_world_map_projects` shows a project count of 0 because the corpus root itself is the project, keep the assertion on `"Projects: "` only (it is).

- [ ] **Step 5: Run the whole suite**

Run: `uv run pytest -q`
Expected: all pass (no other test builds `Config` positionally).

- [ ] **Step 6: Commit**

```bash
git add src/dmx_docs/config.py src/dmx_docs/worldmap.py src/dmx_docs/tools.py tests/test_worlds.py tests/test_worldmap.py
git commit -m "world_map in DocTools: map file from config, live overview cached 10 min"
```

### Task 3: MCP tool, instructions, CLI `map`, `dmx.ps1`

**Files:**
- Modify: `src/dmx_docs/server.py` (instruction constants; register `world_map` next to `index_status`)
- Modify: `src/dmx_docs/cli.py` (subcommand `map`)
- Modify: `scripts/dmx.ps1` (help text, `ValidateSet`, `map` command, pull copies maps)
- Test: `tests/test_worldmap.py`

**Interfaces:**
- Consumes: `DocTools.world_map() -> str` (Task 2).
- Produces: MCP tool `world_map` (no parameters) in every profile; constant `server.WORLD_MAP_LINE`; CLI `dmx-docs [--world W] map`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_worldmap.py`:

```python
import asyncio

from dmx_docs.config import Config


@pytest.mark.parametrize("profile", ["projects", "marketing", "documentation", "none"])
def test_world_map_tool_and_instructions_in_every_profile(tmp_path, profile):
    from mcp import Client

    from dmx_docs.server import WORLD_MAP_LINE, build_server, instructions_for

    cfg = Config(roots=[str(tmp_path)], data_dir=tmp_path / "data", world=profile.replace("none", "other"),
                 world_title="X", profile=profile, embedding_model="hash:256")

    async def names():
        async with Client(build_server(cfg)) as client:
            return {t.name for t in (await client.list_tools()).tools}

    assert "world_map" in asyncio.run(names())
    text = instructions_for(cfg)
    assert WORLD_MAP_LINE in text
    assert text.index(WORLD_MAP_LINE) < 1200  # near the top: some clients truncate instructions


def test_cli_map_prints_the_world_map(marketing, tmp_path, capsys):
    from dmx_docs import cli

    cfg, root, map_file = marketing
    conf = tmp_path / "config.toml"
    conf.write_text(
        f"[index]\ndata_dir = '{cfg.data_dir.as_posix()}'\n\n"
        f"[worlds.marketing]\nprofile = 'marketing'\nroots = ['{root.as_posix()}']\n"
        "[embeddings]\nmodel = 'hash:256'\nquery_prefix = ''\npassage_prefix = ''\n",
        encoding="utf-8")
    cli.main(["--config", str(conf), "--world", "marketing", "map"])
    out = capsys.readouterr().out
    assert out.startswith("# Marketing - map") and "## Live overview" in out
```

(The fixture's map file is `tmp_path\maps\marketing.md`, which is exactly the default next to this `config.toml`.)

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_worldmap.py -v -k "tool_and_instructions or cli_map"`
Expected: FAIL (`cannot import name 'WORLD_MAP_LINE'`; `invalid choice: 'map'`)

- [ ] **Step 3: Implement `server.py`**

Add above `INSTRUCTIONS`:

```python
WORLD_MAP_LINE = ("Call world_map first when you do not know where a kind of information is kept or whether "
                  "this world holds it at all: it lists what is here, where, and what is missing.")
```

Insert `{WORLD_MAP_LINE}`-equivalent text in each constant. The constants are plain strings, so concatenate: in `INSTRUCTIONS`, `MARKETING_INSTRUCTIONS` and `DOCUMENTATION_INSTRUCTIONS`, split the literal just before `Never answer from the first search alone` and join with `+ WORLD_MAP_LINE + "\n\n" +`. Example for `MARKETING_INSTRUCTIONS` (do the same for the two others at their own "Never answer" line):

```python
MARKETING_INSTRUCTIONS = """\
Read-only access to the company's marketing documents (Demaurex: robotic packaging lines):
...
slide by slide ("slide" instead of "page").

""" + WORLD_MAP_LINE + "\n\n" + """\
Never answer from the first search alone. Follow this loop for every question:
...
"""
```

For `GENERIC_INSTRUCTIONS` (which is `.format`-ed: `WORLD_MAP_LINE` has no braces, so appending is safe), append after its first line:

```python
GENERIC_INSTRUCTIONS = """\
Read-only access to the company's "{title}" documents (Demaurex: robotic packaging lines).
""" + WORLD_MAP_LINE + "\n" + """\
Search with `search` (2-4 reformulated queries, several languages), check each hit by reading it
...
"""
```

Register the tool right after `index_status` in `build_server`:

```python
    @mcp.tool(**kw)
    def world_map() -> str:
        """What this world holds and where: the kinds of documents and the folders or filters that reach
        them, what is NOT here and where to look instead (hand-written map), then live counts and the
        folder tree from the index. Call it before routing a question you are unsure about."""
        return tools.world_map()
```

- [ ] **Step 4: Implement `cli.py`**

After `sub.add_parser("status", help="show index statistics")`:

```python
    sub.add_parser("map", help="print the world map (map file + live overview), as the world_map tool returns it")
```

After the `status` branch:

```python
    elif args.command == "map":
        from .tools import DocTools
        print(DocTools(cfg).world_map())
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_worldmap.py tests/test_profiles.py tests/test_documentation.py tests/test_picture_tools.py tests/test_skill.py -v`
Expected: PASS (`test_profiles` compares `instructions_for` to `INSTRUCTIONS + "\n" + PICTURES_NOTE`; the constant now contains the line, so it still holds).

- [ ] **Step 6: Update `scripts/dmx.ps1`** (edit with the Edit tool; check backslashes after editing)

Help block, after the `status` line:

```
  map            print the world map (maps\<world>.md on the shared folder + live counts of the local copy)
```

Shared layout block, after the `thesaurus.toml` line:

```
  <shared>\maps\<world>.md    world maps (what each world holds, where, what it lacks), copied by pull
```

`ValidateSet`: add `'map'` after `'status'`.

Dispatch, after the `'status'` line:

```powershell
        'map' { Assert-Env; Invoke-Native $Py (@('-m', 'dmx_docs.cli', '--config', $SharedConfig) + $WorldArgs + @('map')) -AllowFail }
```

In `'pull'`, right after the thesaurus copy line:

```powershell
            $maps = Join-Path $Shared 'maps'
            if (Test-Path $maps) {  # world maps, read by the world_map tool next to the local config
                New-Item -ItemType Directory -Force (Join-Path $Local 'maps') | Out-Null
                Copy-Item (Join-Path $maps '*.md') (Join-Path $Local 'maps') -Force
            }
```

- [ ] **Step 7: Check the script still parses and its tests pass**

Run: `powershell -NoProfile -Command "$null = [System.Management.Automation.Language.Parser]::ParseFile('scripts\dmx.ps1', [ref]$null, [ref]$e); $e.Count"` → Expected: `0`
Run: `uv run pytest tests/test_dmx_ps1.py -q` → Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/dmx_docs/server.py src/dmx_docs/cli.py scripts/dmx.ps1 tests/test_worldmap.py
git commit -m "world_map tool in every world, instructions line, dmx-docs map, pull copies maps"
```

### Task 4: Draft the three maps (content; user review checkpoint)

**Files:**
- Create: `maps/projects.md`, `maps/marketing.md`, `maps/documentation.md` (repo root; copied to `U:\DMX-RAG\maps\` at rollout)
- Create: `maps/README.md` (three lines: what the files are, the outline, that they are copied to `U:\DMX-RAG\maps\` and edited there)

**Interfaces:**
- Consumes: the indexes on this machine (`C:\dmx-rag\data\<world>\index.sqlite3`, read-only), the MCP tools `dmx-projects`, `dmx-marketing`, `dmx-documentation` (`list_folder`, `search`, `read_document`), and `dmx-docs map` once Task 3 is merged locally.
- Produces: three Markdown files with exactly the outline below; Task 5 points to them, Task 6 feeds them to variant B.

Outline of every map (headings verbatim):

```
# <World> - map
## What this world is for
## Kinds of information
| Kind | What it is | Where | Filter | Answers questions like |
## Not in this world - look there
## Pitfalls
```

- [ ] **Step 1: Collect the facts per world** with a read-only script in the scratchpad (not the repo), run with `C:\dmx-rag\venv\Scripts\python.exe -I`: folder tree depth 3 with counts (Marketing, Documentation); Projects template folders aggregated (projects and docs per `section\subfolder`), plus the non-template folder names that occur in 5+ projects; for each main folder, 10 file names as samples. Save the output in the scratchpad.

- [ ] **Step 2: Read samples** with the MCP tools: for each kind row you will write, open 1-2 representative documents (`read_document`, first pages) to describe what the kind really contains (e.g. what a `20_Conception` folder holds; what `Training Sales\420 Speed Calculation` explains; what `Brochures\Marketing\Quotations` holds).

- [ ] **Step 3: Write `maps/projects.md`.** Rows required (one per kind, more if the facts show them): offers / reference offers (`0_Vente\00_Offres`, `doc_type="offre"`, base for a new offer: search `machine=` + `doc_type="offre"`); customer specifications (`03_Cahier_des_charges`); orders; third-party equipment (`06_`, `27_`, `37_`, `85_`, `87_`, `89_`, `90_Materiel_Tiers`, `Livraison tiers`, `Offres Tiers`; `doc_type="doc_fournisseur"`); design and development (`20_Conception`, `30_Conception`, `Developpement mec`, `Revue de conception`); modifications; electrical schematics (`23_Schemas`); layouts and drawings; bills of materials (`Nomenclatures`); software (`4_Soft`); follow-up and planning; FAT / SAT / acceptance (`56_Acceptation_machine`); commissioning (`55_MES`, `61_MES`); tests and open points; qualification IQ/OQ; transport; project manuals (`8_Documentation`, `80_USB stick Doc Project`); supplier manuals (`85_Received_DOC`); after-sales (`9_SAV`); closure (`Clôture`, `57_Cloture`); invoices. "Not here" must include: standard (generic) manual -> Documentation; product claims, sales decks, photos for slides -> Marketing; Excel and CAD files not indexed (costing sheets, 3D) -> open the folder with `list_folder` / `open_document`; register-only machines without documents. Pitfalls: old named templates, `2_Hors_Garantie`, duplicates in `80_USB stick`, OCR, project names that are ordinary words.

- [ ] **Step 4: Write `maps/marketing.md`.** Rows: Training Sales chapters one row each where 5+ files (010, 210, 212, 220, 260, 310, 320, 330, 340, 410, 420, 440, 450, 460, 710), company and group presentations (`Présentation Demaurex`, `Présentation Rotzinger`, `Group Rotzinger templates`), Paloma WD, D3 Sales Info, brochures (`Brochures`, versions FSO), offer templates and old quotations (`Brochures\Marketing\Quotations`: `offre_3_pages_a_valider` per machine/configuration - templates, not real offers -> real offers are in Projects), datasheets, exhibitions (CFIA, IBA), competition (Cavanna, others), publications, photos (`Pictures` sets per machine), graphics (visual identity, website mockups), videos (stills only: the videos themselves are not indexed). "Not here": what a client's machine reached (Projects FAT/SAT), how to maintain/operate (Documentation), the videos' content, Excel price lists, `CSI` (no indexed file). Pitfalls: promotional figures, several versions of a deck, `Training Sales` holds sub-folders with supplier quotations (e.g. denester) that are not Demaurex offers.

- [ ] **Step 5: Write `maps/documentation.md`.** Rows per chapter and the useful sub-chapters (1300 module descriptions, 1400 technical data, 2100 standard safety, 3100 functional elements, 3200 user manual, 3300 troubleshooting, 4100-4300 HMI parameters, errors and warnings, 5100 functional descriptions, 5200 tool change, 5300 Gemini installation, 6200 maintenance, 6300/6400 replacement of electrical/mechanical parts, 7200 spare parts, 7600 CE declaration), languages FR/DE/EN. "Not here - look there" (the gap the user named): **standard software documentation and standard electrical documentation are not in this world yet** (`7300_Schémas électriques`, `7100_Description de l'application` hold only placeholder sections): look in each project's `4_Soft`, `2_Electrique` (`23_Schemas`) and `8_Documentation` in Projects, and say the standard version is missing. Also: project-specific (customised) manuals -> Projects `8_Documentation`; product claims -> Marketing. Pitfalls: same section in three languages, Markdown sections read as one page, old versions excluded.

- [ ] **Step 6: Preview each map with the tool output**

Run (PowerShell, from the worktree, after copying the three files to `C:\dmx-rag\maps\` temporarily or pointing a scratch config at them): `C:\dmx-rag\venv\Scripts\python.exe -m dmx_docs.cli --config <scratch config> --world marketing map` (with `PYTHONPATH=src`). Check each output is under 12,000 characters, the map comes first, nothing is cut that matters. If a map is longer than about 6,000 characters, shorten it (the tree needs room).

- [ ] **Step 7: Commit and ask the user to review**

```bash
git add maps/
git commit -m "World maps: first drafts for projects, marketing, documentation"
```

Send the three files to the user (SendUserFile) and STOP until they have reviewed them; apply their corrections in a follow-up commit `World maps: user's corrections`.

### Task 5: Skill update

**Files:**
- Modify: `skills/dmx-research/SKILL.md` (section 0 and a new table)
- Modify: `skills/dmx-research/references/projects.md`, `references/marketing.md`, `references/documentation.md` ("What is in the index" sections)
- Test: `tests/test_skill.py` (unchanged; must pass)

**Interfaces:**
- Consumes: tool `world_map` (Task 3), the maps' content (Task 4).
- Produces: the new skill zip (`scripts/build_skill.py`).

- [ ] **Step 1: Edit `SKILL.md` section 0.** After the bullet list ending with "`project_card`. **The search filters differ per world** ...", add:

```markdown
- **Start with the map.** Call `world_map` on each world you are going to search, once per
  conversation, before routing: it says what kinds of documents the world holds, in which folders
  and with which filters, and **what it does not hold and where to look instead**. Use it to pick
  the world, the folder (`folder=`) and the filters; when the map says a kind is missing, say so in
  the answer instead of searching on and on.
```

- [ ] **Step 2: Add the cross-world table** at the end of section 1 ("Route the question"), before "**Several worlds:**":

```markdown
**Where each kind of information lives** (details and folders: `world_map`):

| Kind | World | Where |
|---|---|---|
| real offers, reference offers for a new one | Projects | `0_Vente\00_Offres`, `doc_type="offre"` + `machine=` |
| offer templates (3-page standard offers) | Marketing | `Brochures\Marketing\Quotations` |
| client specifications, orders, modifications | Projects | `0_Vente`, `doc_type="cahier_des_charges,commande,modification"` |
| third-party equipment (supplier docs and offers) | Projects | `*_Materiel_Tiers` folders, `doc_type="doc_fournisseur"` |
| design and development | Projects | `20_Conception`, `30_Conception`, `Developpement mec` |
| FAT, SAT, commissioning, open points | Projects | `5_Gestion\56_Acceptation_machine`, `6_Rapports_Tests` |
| project closure | Projects | `Clôture`, `57_Cloture`, `section="Cloture"` |
| a project's own manual | Projects | `8_Documentation` |
| standard manual: operation, safety, HMI, maintenance | Documentation | chapters 1000-7000 |
| standard software and electrical documentation | **none yet** | say so; the projects' `4_Soft`, `2_Electrique`, `8_Documentation` hold project versions |
| sales trainings by topic (end-effectors, speed calculation, coflow/counterflow...) | Marketing | `Presentations\Training Sales\<chapter>` |
| brochures, datasheets, exhibitions, competition | Marketing | `category=` |
| photos and graphics for slides | Marketing | `category="Pictures,Graphics"`, `pictures=true` |
```

- [ ] **Step 3: Shrink the references.** In each `references/<world>.md`, replace the "What is in the index" section with:

```markdown
## What is in the index

Call `world_map` (this connector) for what this world holds, where, and what it lacks; it is kept
up to date by hand and with live counts. The essentials for searching:
```

followed, for **projects.md**, by the existing facets bullet (the one starting "Every document carries **facets**") and the "What is indexed changes over time" sentence - delete the template table and the folder bullets (they are in the map); for **marketing.md**, by the existing "Each hit shows its **category**" bullet; for **documentation.md**, by the existing "Each hit shows its **category** = the manual chapter" bullet. Keep every other section unchanged.

- [ ] **Step 4: Run the skill test**

Run: `uv run pytest tests/test_skill.py -v`
Expected: PASS (it now sees `world_map` registered in every world).

- [ ] **Step 5: Build the zip and commit**

Run: `C:\dmx-rag\venv\Scripts\python.exe scripts\build_skill.py C:\Users\JOHAN~1.MON\AppData\Local\Temp\skill-check` → Expected: `Written: ...\dmx-research.zip` (the real one goes to `U:\DMX-RAG\skills` at rollout).

```bash
git add skills/dmx-research
git commit -m "Skill dmx-research: start with world_map, cross-world table of where each kind lives"
```

### Task 6: Routing test (outside the repository)

**Files (all in `U:\DMX-RAG\routing-work\`, written with the Write tool):**
- Create: `testset.json`, `score.py`, `runs-<date>\A-<id>.json`, `runs-<date>\B-<id>.json`, `results-<date>.md`

**Interfaces:**
- Consumes: the three connectors in this session (old server code: no `world_map` tool), the old skill (`git show 92f9d81:skills/dmx-research/...`), the new skill (Task 5), the map outputs from `dmx-docs map` per world (Task 3, maps from Task 4 after the user's review).
- Produces: `results-<date>.md` with the per-question comparison and the keep/fix decision.

- [ ] **Step 1: Write `testset.json`** - 18 items, real project names found with `list_projects` (pick projects whose folders actually contain the expected kind; check with `list_folder`):

```json
[{"id": "P1", "question": "...", "worlds": ["projects"],
  "expect": ["00_Offres"], "gap": false, "note": "reference offer for a Paloma 4R"}]
```

Distribution: Projects 6 (reference offers for a machine type; third-party equipment of a project; design/development documents of a project; closure documents of a project; FAT findings of a project; customised manual of a project), Marketing 5 (coflow vs counterflow training; speed calculation; offer template for a Presto; competitor Cavanna; photo for a slide of a Paloma washdown), Documentation 4 (gearbox replacement procedure; HMI error list; standard safety; technical data), gaps 3 (standard software documentation of the Gemini HMI; standard electrical schematic of a Paloma; a video of a demo). `expect` = path fragments, any of which a good answer must cite; `gap` = true when the right answer states the kind is missing.

- [ ] **Step 2: Write `score.py`**

```python
"""Score routing-test runs: python score.py <runs folder> -> prints a Markdown table."""
import json
import sys
from pathlib import Path

GAP_WORDS = ("not in", "pas dans", "missing", "manque", "absent", "n'existe pas", "no standard", "pas de version standard")


def score(item, run):
    answer = run["answer"].lower()
    reached = any(e.lower() in answer for e in item["expect"])
    gap = any(w in answer for w in GAP_WORDS) if item["gap"] else None
    searches, first = 0, None
    for step in run["trace"]:
        if step["tool"].endswith("search"):
            searches += 1
        if first is None and any(e.lower() in " ".join(step.get("paths", [])).lower() for e in item["expect"]):
            first = searches
    return reached, gap, searches, first


def main():
    folder = Path(sys.argv[1])
    items = {i["id"]: i for i in json.loads((folder.parent / "testset.json").read_text(encoding="utf-8"))}
    totals = {"A": [0, 0, 0, 0], "B": [0, 0, 0, 0]}
    print("| id | variant | reached | gap stated | searches | searches to first expected hit |")
    print("|---|---|---|---|---|---|")
    for f in sorted(folder.glob("*.json")):
        variant, iid = f.stem.split("-", 1)
        reached, gap, searches, first = score(items[iid], json.loads(f.read_text(encoding="utf-8")))
        t = totals[variant]
        t[0] += reached
        t[1] += bool(gap)
        t[2] += searches
        t[3] += 1
        print(f"| {iid} | {variant} | {'yes' if reached else 'no'} | {'-' if gap is None else ('yes' if gap else 'no')} "
              f"| {searches} | {first if first is not None else '-'} |")
    for v, (r, g, s, n) in totals.items():
        if n:
            print(f"\n{v}: reached {r}/{n}, gaps stated {g}, mean searches {s / n:.1f}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Run variant A** - one subagent per question (batches of 6 in parallel), prompt = old SKILL.md + the old reference files (from `git show 92f9d81:...`) + the question + "Do not call world_map. At the end, output ONLY a JSON object {\"answer\": <your full answer with sources>, \"trace\": [{\"tool\": <tool name>, \"args\": <args>, \"paths\": [<first 3 paths returned>]} for every tool call]}". Save each result as `runs-<date>\A-<id>.json`.

- [ ] **Step 4: Run variant B** - same, with the new SKILL.md + new references + "Result of world_map for each world:" followed by the `dmx.ps1 map` output of the three worlds (the session's servers do not have the tool yet; injecting its output is equivalent to one call per world). Save as `B-<id>.json`.

- [ ] **Step 5: Score and decide**

Run: `C:\dmx-rag\venv\Scripts\python.exe -I U:\DMX-RAG\routing-work\score.py U:\DMX-RAG\routing-work\runs-<date>`
Write `results-<date>.md`: the table, totals, and per failing B question the cause (map wrong, map missing a kind, retrieval problem). Keep the change if B reaches more expected locations and states more gaps without more searches on average. For each B failure caused by a map, correct the map (user approves the change) and rerun only those questions; at most two rounds.

- [ ] **Step 6: Report** to the user: totals A vs B, what changed in the maps, remaining failures and whether they are routing or retrieval problems.

### Task 7: Rollout (with the user)

- [ ] **Step 1:** Run the full suite `uv run pytest -q` → all pass; then finish the branch (superpowers:finishing-a-development-branch): merge into `U:\DMX-RAG\app` main and push (the user allows pushing).
- [ ] **Step 2:** Copy `maps\*.md` to `U:\DMX-RAG\maps\` (Copy-Item in PowerShell, not Bash), check `dmx.ps1 map -World marketing` prints the map.
- [ ] **Step 3:** Build the skill zip into `U:\DMX-RAG\skills`; the user uploads it and pulls each world (launcher 5) so the laptop gets the maps; restart Claude Desktop.
- [ ] **Step 4:** In Claude Desktop, ask one question per world and check that `world_map` is called first.
- [ ] **Step 5:** Update the deployment memory (`dmx-rag-deployment.md`): maps location, `map` command, routing-test results.

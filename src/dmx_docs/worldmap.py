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

MAX_CHARS = 15000     # about 4k tokens: map text + live overview
MIN_DOCS = 5          # smaller folders are folded into one line...
SMALL_WORLD = 1000    # ...except in a world this small, where every folder is shown
TREE_DEPTH = 3
SECTION_ROWS = 8      # Projects: template folders shown per section (540 projects, many old variants)
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
        text = ""
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


def template_folders(rows, roots, min_docs: int = MIN_DOCS, max_rows: int = SECTION_ROWS) -> list[str]:
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
        tree = folder_tree([r[:2] for r in rows], cfg.roots,
                           min_docs=1 if len(rows) < SMALL_WORLD else MIN_DOCS)
    if store.has_table(con, "picture_uses"):
        kinds = _counts(con, """SELECT type, count(*) n FROM picture_uses WHERE status = 'described'
                                GROUP BY 1 ORDER BY n DESC""")
        if kinds != "none":
            head.append("Described pictures (search with pictures=true): " + kinds)
    return Overview(head=head, tree_title=tree_title, tree=tree)

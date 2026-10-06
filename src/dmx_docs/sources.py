"""Root folders, excluded folders and exclusion patterns, stored in the index database.

They are edited on the configuration page. The indexer and the MCP server read
them from the database each time, so changes apply without restarting anything.
"""

from __future__ import annotations

import json
import os
import time

from . import store
from .config import Config


def _seed(con, cfg: Config) -> None:
    """On first use, import roots and patterns from config.toml."""
    if store.get_meta(con, "sources_initialized"):
        return
    for root in cfg.roots:
        con.execute("INSERT OR IGNORE INTO roots(path, path_key, added_at) VALUES(?,?,?)",
                    (root, store.path_key(root), time.time()))
    store.set_meta(con, "exclude_patterns", json.dumps(cfg.exclude))
    store.set_meta(con, "sources_initialized", 1)
    con.commit()


def refresh(cfg: Config, con=None) -> Config:
    """Load the current sources from the database into cfg."""
    own = con is None
    if own:
        if not cfg.db_path.exists() and not cfg.roots:
            return cfg
        con = store.connect(cfg.db_path)
    try:
        _seed(con, cfg)
        roots = [r["path"] for r in con.execute("SELECT path FROM roots ORDER BY path")]
        excluded = [r["path"] for r in con.execute("SELECT path FROM excluded_dirs ORDER BY path")]
        patterns = json.loads(store.get_meta(con, "exclude_patterns", "[]"))
    finally:
        if own:
            con.close()
    cfg.set_sources(roots, excluded, patterns)
    return cfg


def _changed(con) -> None:
    store.set_meta(con, "sources_changed_at", time.time())


def _clean(path: str) -> str:
    path = path.strip().strip('"').strip()
    if not path:
        raise ValueError("Empty path")
    path = os.path.normpath(path)
    if os.path.splitdrive(path)[1] in ("", "."):  # 'Z:' means the root of the drive
        path = os.path.splitdrive(path)[0] + os.sep
    return path


def add_root(con, path: str) -> str:
    path = _clean(path)
    if not os.path.isdir(path):
        raise ValueError(f"Folder not found or not reachable: {path}")
    key = store.path_key(path)
    for r in con.execute("SELECT path, path_key FROM roots"):
        if store.is_under(key, r["path_key"]):
            raise ValueError(f"{path} is already covered by the root folder {r['path']}")
        if store.is_under(r["path_key"], key):
            raise ValueError(f"{path} contains the root folder {r['path']}: remove that one first")
    con.execute("INSERT INTO roots(path, path_key, added_at) VALUES(?,?,?)", (path, key, time.time()))
    # Exclusions inside the new root stay; exclusions that were the root itself go.
    con.execute("DELETE FROM excluded_dirs WHERE path_key = ?", (key,))
    _changed(con)
    con.commit()
    return path


def remove_root(con, path: str) -> None:
    key = store.path_key(path)
    con.execute("DELETE FROM roots WHERE path_key = ?", (key,))
    for r in con.execute("SELECT path_key FROM excluded_dirs").fetchall():
        if store.is_under(r["path_key"], key):
            con.execute("DELETE FROM excluded_dirs WHERE path_key = ?", (r["path_key"],))
    _changed(con)
    con.commit()


def set_excluded(con, path: str, excluded: bool) -> None:
    path = _clean(path)
    key = store.path_key(path)
    if excluded:
        roots = [r["path_key"] for r in con.execute("SELECT path_key FROM roots")]
        if key in roots:
            raise ValueError("A root folder cannot be excluded; remove it instead")
        if not any(store.is_under(key, rk) for rk in roots):
            raise ValueError(f"{path} is not inside a root folder")
        con.execute("INSERT OR IGNORE INTO excluded_dirs(path, path_key, added_at) VALUES(?,?,?)",
                    (path, key, time.time()))
        # Exclusions below this folder are now redundant.
        for r in con.execute("SELECT path_key FROM excluded_dirs").fetchall():
            if r["path_key"] != key and store.is_under(r["path_key"], key):
                con.execute("DELETE FROM excluded_dirs WHERE path_key = ?", (r["path_key"],))
    else:
        con.execute("DELETE FROM excluded_dirs WHERE path_key = ?", (key,))
    _changed(con)
    con.commit()


def set_patterns(con, patterns: list[str]) -> list[str]:
    cleaned = [p.strip() for p in patterns if p.strip()]
    store.set_meta(con, "exclude_patterns", json.dumps(cleaned))
    _changed(con)
    con.commit()
    return cleaned

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
        _migrate_drive_letters(con)
        roots = [r["path"] for r in con.execute("SELECT path FROM roots ORDER BY path")]
        excluded = [r["path"] for r in con.execute("SELECT path FROM excluded_dirs ORDER BY path")]
        patterns = json.loads(store.get_meta(con, "exclude_patterns", "[]"))
    finally:
        if own:
            con.close()
    cfg.set_sources(roots, excluded, patterns)
    return cfg


def _migrate_drive_letters(con) -> None:
    """Roots saved as 'N:\\...' become UNC paths (on a machine where that drive is mapped).
    A root that lies inside another root becomes a ticked folder of that root."""
    for r in con.execute("SELECT path, path_key FROM roots").fetchall():
        unc = to_unc(r["path"])
        if unc == r["path"]:
            continue
        excluded = [e["path"] for e in con.execute("SELECT path, path_key FROM excluded_dirs")
                    if store.is_under(e["path_key"], r["path_key"])]
        remove_root(con, r["path"])
        try:
            add_root(con, unc)
        except ValueError:
            continue  # already covered by another root
        for ex in excluded:
            try:
                set_excluded(con, to_unc(ex), True)
            except ValueError:
                pass


def _changed(con) -> None:
    store.set_meta(con, "sources_changed_at", time.time())


def to_unc(path: str) -> str:
    """'N:\\x' -> '\\\\server\\share\\x' when N: is a mapped network drive. Drive letters
    differ between machines (and do not exist for scheduled tasks), UNC paths do not."""
    if os.name != "nt":
        return path
    drive, rest = os.path.splitdrive(path)
    if len(drive) != 2 or drive[1] != ":":
        return path
    import ctypes

    buf = ctypes.create_unicode_buffer(1024)
    size = ctypes.c_ulong(len(buf))
    if ctypes.windll.mpr.WNetGetConnectionW(drive, buf, ctypes.byref(size)) != 0:
        return path  # local drive, or not a network drive
    return os.path.normpath(buf.value.rstrip("\\") + (rest or "\\"))


def clean_path(path: str) -> str:
    path = path.strip().strip('"').strip()
    if not path:
        raise ValueError("Empty path")
    path = os.path.normpath(path)
    drive, rest = os.path.splitdrive(path)
    if drive and not rest.startswith(os.sep):
        # 'Z:' and 'Z:Projets' are relative to the drive's current folder on Windows;
        # they always mean the drive root here.
        path = os.path.normpath(drive + os.sep + ("" if rest == "." else rest))
    return to_unc(path)


def add_root(con, path: str) -> str:
    path = clean_path(path)
    if not os.path.isdir(store.fs_path(path)):
        raise ValueError(f"Folder not found or not reachable: {path}")
    key = store.path_key(path)
    for r in con.execute("SELECT path, path_key FROM roots"):
        if store.is_under(key, r["path_key"]):
            # Inside an existing root: adding it means "index it again" if it was unticked.
            cur = con.execute("DELETE FROM excluded_dirs WHERE path_key = ?", (key,))
            parent_excluded = [e["path"] for e in con.execute("SELECT path, path_key FROM excluded_dirs")
                               if store.is_under(key, e["path_key"])]
            if parent_excluded:
                raise ValueError(f"{path} is inside the unticked folder {parent_excluded[0]}: "
                                 "tick that folder in the root folder's tree instead")
            if cur.rowcount:
                _changed(con)
                con.commit()
                return path
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
    path = clean_path(path)
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

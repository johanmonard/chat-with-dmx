"""SQLite storage: documents, chunks, full-text index and embedding vectors."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS docs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    path        TEXT NOT NULL,
    path_key    TEXT NOT NULL UNIQUE,
    folder_key  TEXT NOT NULL,
    name        TEXT NOT NULL,
    ext         TEXT NOT NULL,
    size        INTEGER,
    mtime       REAL,
    status      TEXT NOT NULL,
    error       TEXT,
    n_pages     INTEGER,
    title       TEXT,
    indexed_at  REAL,
    seen_run    INTEGER
);
CREATE INDEX IF NOT EXISTS docs_folder ON docs(folder_key);

CREATE TABLE IF NOT EXISTS chunks (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    doc_id    INTEGER NOT NULL,
    page_no   INTEGER NOT NULL,
    seq       INTEGER NOT NULL,
    text      TEXT NOT NULL,
    embedded  INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(doc_id, page_no, seq);
CREATE INDEX IF NOT EXISTS chunks_todo ON chunks(id) WHERE embedded = 0;

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    text, content='chunks', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);
CREATE VIRTUAL TABLE IF NOT EXISTS docs_fts USING fts5(
    name, path, content='docs', content_rowid='id',
    tokenize='unicode61 remove_diacritics 2'
);

CREATE TABLE IF NOT EXISTS vectors (
    chunk_id  INTEGER PRIMARY KEY,
    vec       BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS roots (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    path      TEXT NOT NULL,
    path_key  TEXT NOT NULL UNIQUE,
    added_at  REAL
);
CREATE TABLE IF NOT EXISTS excluded_dirs (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    path      TEXT NOT NULL,
    path_key  TEXT NOT NULL UNIQUE,
    added_at  REAL
);

CREATE TABLE IF NOT EXISTS meta (
    key    TEXT PRIMARY KEY,
    value  TEXT
);
"""


def path_key(path: str) -> str:
    """Canonical form of a path used for lookups (case-insensitive on Windows)."""
    return os.path.normcase(os.path.normpath(os.path.abspath(path)))


def is_under(key: str, root_key: str) -> bool:
    return key == root_key or key.startswith(root_key.rstrip(os.sep) + os.sep)


def connect(db_path: str | os.PathLike, create: bool = True) -> sqlite3.Connection:
    db_path = Path(db_path)
    if not create and not db_path.exists():
        raise FileNotFoundError(f"Index not found: {db_path} (run `dmx-docs index` first)")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path, timeout=60, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=60000")
    if create:
        con.executescript(SCHEMA)
    return con


def get_meta(con: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(con: sqlite3.Connection, key: str, value) -> None:
    con.execute("INSERT INTO meta(key, value) VALUES(?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def bump_vector_version(con: sqlite3.Connection) -> None:
    set_meta(con, "vec_version", int(get_meta(con, "vec_version", "0")) + 1)


def delete_doc_content(con: sqlite3.Connection, doc_id: int) -> bool:
    """Remove a document's chunks, FTS entries and vectors. Returns True if vectors were removed."""
    rows = con.execute("SELECT id, text FROM chunks WHERE doc_id=?", (doc_id,)).fetchall()
    if not rows:
        return False
    con.executemany("INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', ?, ?)",
                    [(r["id"], r["text"]) for r in rows])
    cur = con.execute("DELETE FROM vectors WHERE chunk_id IN (SELECT id FROM chunks WHERE doc_id=?)",
                      (doc_id,))
    con.execute("DELETE FROM chunks WHERE doc_id=?", (doc_id,))
    return cur.rowcount > 0


def _fts_doc_delete(con: sqlite3.Connection, doc_id: int) -> None:
    row = con.execute("SELECT name, path FROM docs WHERE id=?", (doc_id,)).fetchone()
    if row:
        con.execute("INSERT INTO docs_fts(docs_fts, rowid, name, path) VALUES('delete', ?, ?, ?)",
                    (doc_id, row["name"], row["path"]))


def delete_doc(con: sqlite3.Connection, doc_id: int) -> bool:
    removed_vectors = delete_doc_content(con, doc_id)
    _fts_doc_delete(con, doc_id)
    con.execute("DELETE FROM docs WHERE id=?", (doc_id,))
    return removed_vectors


def upsert_doc(con: sqlite3.Connection, *, path: str, size: int, mtime: float, status: str,
               error: str | None, n_pages: int, title: str | None, indexed_at: float, run_id: int,
               chunks: list[tuple[int, int, str]]) -> tuple[int, bool]:
    """Insert or replace a document and its chunks. Returns (doc_id, removed_vectors)."""
    key = path_key(path)
    name = os.path.basename(path)
    ext = os.path.splitext(name)[1].lower()
    folder = os.path.dirname(key)
    row = con.execute("SELECT id FROM docs WHERE path_key=?", (key,)).fetchone()
    removed_vectors = False
    values = (path, folder, name, ext, size, mtime, status, error, n_pages, title, indexed_at, run_id)
    if row:
        doc_id = row["id"]
        removed_vectors = delete_doc_content(con, doc_id)
        _fts_doc_delete(con, doc_id)
        con.execute("""UPDATE docs SET path=?, folder_key=?, name=?, ext=?, size=?, mtime=?, status=?,
                       error=?, n_pages=?, title=?, indexed_at=?, seen_run=? WHERE id=?""",
                    values + (doc_id,))
    else:
        cur = con.execute("""INSERT INTO docs(path, folder_key, name, ext, size, mtime, status, error,
                             n_pages, title, indexed_at, seen_run, path_key)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""", values + (key,))
        doc_id = cur.lastrowid
    con.execute("INSERT INTO docs_fts(rowid, name, path) VALUES(?, ?, ?)", (doc_id, name, path))
    for page_no, seq, text in chunks:
        cur = con.execute("INSERT INTO chunks(doc_id, page_no, seq, text) VALUES(?,?,?,?)",
                          (doc_id, page_no, seq, text))
        con.execute("INSERT INTO chunks_fts(rowid, text) VALUES(?, ?)", (cur.lastrowid, text))
    return doc_id, removed_vectors

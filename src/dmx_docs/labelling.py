"""Engineering world: label every document with its discipline, from the text already in the index.

Runs at the end of an index run (indexer.run_index). A document is labelled when it has no label, was
re-extracted since it was labelled, or when disciplines.toml changed (then every document is labelled
again, from the stored chunks: nothing is extracted). The label is copied into doc_facets.category, the
filter of the search tool; doc_discipline keeps what decided it, and a CSV report lists every document.
"""

from __future__ import annotations

import csv
import json
import os
import time
from pathlib import Path

from . import facets, store
from .config import Config
from .disciplines import DISCIPLINES, Rules, classify

TABLE = """
CREATE TABLE IF NOT EXISTS doc_discipline (
    doc_id       INTEGER PRIMARY KEY,
    discipline   TEXT NOT NULL,
    source       TEXT NOT NULL,
    scores       TEXT NOT NULL,
    top_terms    TEXT NOT NULL,
    labelled_at  REAL NOT NULL,
    signature    TEXT NOT NULL DEFAULT ''
);
"""
# What a document was scored on: its number of chunks and the last chunk id. OCR and picture descriptions
# add chunks without extracting the document again, so a change of the signature relabels it.
SIGNATURE = "(SELECT count(*) || ':' || coalesce(max(id), 0) FROM chunks WHERE doc_id = d.id)"
BODY_CHUNKS = 3
REPORT_NAME = "engineering-classification.csv"


def _commit_many(con, sql: str, rows: list) -> None:
    autocommit = con.isolation_level is None
    if autocommit:
        con.execute("BEGIN")
    con.executemany(sql, rows)
    con.execute("COMMIT") if autocommit else con.commit()


def text_of(con, doc_id: int, path: str, key: str, root_keys: list) -> tuple[str, str]:
    """(path below its root, text of the first chunks): what is scored for a document."""
    root = facets.root_of(key, root_keys)
    rel = os.path.relpath(path, root) if root else os.path.basename(path)
    body = "\n".join(r[0] for r in con.execute(
        "SELECT text FROM chunks WHERE doc_id = ? ORDER BY page_no, seq LIMIT ?", (doc_id, BODY_CHUNKS)))
    return rel, body


def refresh(con, cfg: Config, rules: Rules, progress=print) -> dict[str, int]:
    """Label the documents that need it, copy the labels into doc_facets.category, write the report.
    Returns the number of documents per label."""
    con.executescript(TABLE)
    if "signature" not in {r[1] for r in con.execute("PRAGMA table_info(doc_discipline)")}:
        con.execute("ALTER TABLE doc_discipline ADD COLUMN signature TEXT NOT NULL DEFAULT ''")
    if rules.fingerprint == "none":
        progress("WARNING: disciplines.toml not found: every document is Unknown")
    everything = store.get_meta(con, "disciplines_fingerprint") != rules.fingerprint
    con.execute("DELETE FROM doc_discipline WHERE doc_id NOT IN (SELECT id FROM docs)")
    where = "" if everything else ("WHERE x.doc_id IS NULL OR x.labelled_at < d.indexed_at "
                                   "OR x.signature != sig")
    todo = con.execute(f"""SELECT d.id, d.path, d.path_key, d.title, {SIGNATURE} AS sig FROM docs d
                           LEFT JOIN doc_discipline x ON x.doc_id = d.id {where}""").fetchall()
    root_keys = [(store.path_key(r), r) for r in cfg.roots]
    now = time.time()
    rows = []
    for doc_id, path, key, title, sig in todo:
        rel, body = text_of(con, doc_id, path, key, root_keys)
        d = classify(rules, path, rel, title, body)
        rows.append((doc_id, d.label, d.source, json.dumps(d.scores), json.dumps(d.top_terms), now, sig))
    _commit_many(con, "INSERT OR REPLACE INTO doc_discipline(doc_id, discipline, source, scores, top_terms, "
                      "labelled_at, signature) VALUES (?,?,?,?,?,?,?)", rows)
    con.execute("UPDATE doc_facets SET category = (SELECT discipline FROM doc_discipline "
                "WHERE doc_discipline.doc_id = doc_facets.doc_id)")
    if con.in_transaction:
        con.commit()
    store.set_meta(con, "disciplines_fingerprint", rules.fingerprint)
    try:
        write_report(con, cfg.world_dir / REPORT_NAME)
    except OSError as e:
        progress(f"{REPORT_NAME} not written: {e}")
    return dict(con.execute("SELECT discipline, count(*) FROM doc_discipline GROUP BY 1").fetchall())


def write_report(con, path: Path) -> int:
    """One row per document: path, section, discipline, what decided it, the three scores and the top
    terms. UTF-8 with BOM and ';' so that Excel opens it."""
    rows = con.execute("""SELECT d.path, f.section, x.discipline, x.source, x.scores, x.top_terms
                          FROM docs d JOIN doc_discipline x ON x.doc_id = d.id
                          LEFT JOIN doc_facets f ON f.doc_id = d.id ORDER BY d.path_key""").fetchall()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        out = csv.writer(fh, delimiter=";")
        out.writerow(["path", "section", "discipline", "source"] + [d.lower() for d in DISCIPLINES] + ["top_terms"])
        for p, section, discipline, source, scores, terms in rows:
            s = json.loads(scores)
            out.writerow([p, section or "", discipline, source] + [s.get(d, 0) for d in DISCIPLINES]
                         + [", ".join(json.loads(terms))])
    return len(rows)

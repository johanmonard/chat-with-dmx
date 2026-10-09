"""Hybrid search: SQLite FTS5 keyword search + embedding similarity, fused with RRF."""

from __future__ import annotations

import logging
import os
import re
import threading
import unicodedata
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from . import store
from .config import Config

log = logging.getLogger("dmx_docs.search")

RRF_K = 60
MAX_PER_DOC = 3
BLOCK_ROWS = 131072
# Vectors are stored as float16; below this size they are kept in RAM as
# float32, which makes each query several times faster.
FLOAT32_MAX_BYTES = 8 * 1024**3

# Very common words in French, English, German and Spanish: dropped from
# keyword queries because they match almost every chunk.
STOPWORDS = set("""
le la les un une des du de d l au aux et ou en dans sur pour par avec sans ce cet cette ces
qui que quoi dont est sont été être a ont il elle ils elles on nous vous je tu se sa son ses
leur leurs mais donc ni car ne pas plus comme y quel quelle quels quelles
the a an and or of to in on for with without by is are was were be been it its this that
these those from at as not but what which who how when where do does did
der die das den dem des ein eine einer eines und oder zu im in auf für mit von ist sind
nicht wie was wer wo
el los las un una unos unas y o de del en con sin por para es son que se su sus al lo como
""".split())

_PHRASE = re.compile(r'"([^"]+)"')
_COMPOUND = re.compile(r"\w+(?:[-./_]\w+)+\*?|\w+\*?", re.UNICODE)


def build_fts_query(query: str, mode: str = "OR") -> str | None:
    """Turn free text into a safe FTS5 query.

    "quoted text" stays a phrase, codes like MN-114 or 12.345.6 become phrases,
    a trailing * means prefix search; other words are OR-ed (ranked by BM25).
    """
    terms: list[str] = []
    for phrase in _PHRASE.findall(query):
        words = re.findall(r"\w+", phrase)
        if words:
            terms.append('"' + " ".join(words) + '"')
    rest = _PHRASE.sub(" ", query)
    for tok in _COMPOUND.findall(rest):
        prefix = tok.endswith("*")
        tok = tok.rstrip("*")
        words = re.findall(r"\w+", tok)
        if not words:
            continue
        if len(words) == 1:
            w = words[0]
            if w.lower() in STOPWORDS or (len(w) < 2 and not w.isdigit()):
                continue
        term = '"' + " ".join(words) + '"'
        terms.append(term + "*" if prefix else term)
    seen = set()
    unique = [t for t in terms if not (t.lower() in seen or seen.add(t.lower()))]
    if not unique:
        return None
    return f" {mode} ".join(unique)


def fold(text: str) -> str:
    """Lowercase and strip accents, keeping string length (for snippet offsets)."""
    out = []
    for ch in text:
        base = unicodedata.normalize("NFKD", ch)
        out.append(base[0] if base else ch)
    return "".join(out).lower()


def make_snippet(text: str, query: str, size: int = 700) -> str:
    text = " ".join(text.split())
    if len(text) <= size:
        return text
    folded = fold(text)
    positions = []
    for w in re.findall(r"\w+", query):
        if len(w) > 2 and w.lower() not in STOPWORDS:
            i = folded.find(fold(w))
            if i != -1:
                positions.append(i)
    start = max(0, min(positions) - size // 3) if positions else 0
    snippet = text[start:start + size]
    return ("…" if start > 0 else "") + snippet + ("…" if start + size < len(text) else "")


def parse_date(value: str | None) -> float | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).timestamp()
        except ValueError:
            pass
    raise ValueError(f"Invalid date '{value}', use YYYY, YYYY-MM or YYYY-MM-DD")


class VectorIndex:
    """All chunk vectors in memory (memory-mapped .npy cache, rebuilt when the index changes)."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.version: str | None = None
        self.ids: np.ndarray | None = None
        self.mat: np.ndarray | None = None
        self.lock = threading.Lock()

    def _cache_paths(self, version: str):
        # One file pair per vector version: a new cache never has to replace a file that
        # another process (e.g. Claude Desktop's server) still has memory-mapped.
        d = self.cfg.data_dir
        safe = re.sub(r"[^0-9A-Za-z]", "", version)[:40] or "0"
        return d / f"vec_ids.{safe}.npy", d / f"vec_mat.{safe}.npy"

    def _cleanup_old_caches(self, keep: str) -> None:
        keep_names = {p.name for p in self._cache_paths(keep)}
        for p in list(self.cfg.data_dir.glob("vec_*.npy")) + list(self.cfg.data_dir.glob("vec_*.tmp")) + list(self.cfg.data_dir.glob("vec_cache.json")):
            if p.name not in keep_names:
                try:
                    p.unlink()
                except OSError:
                    pass  # still in use by another process: removed by a later run

    def ensure(self, con) -> bool:
        version = store.get_meta(con, "vec_version", "0")
        with self.lock:
            if version == self.version and self.mat is not None:
                return len(self.ids) > 0
            ids_p, mat_p = self._cache_paths(version)
            try:
                if ids_p.exists() and mat_p.exists():
                    self.ids = np.load(ids_p, mmap_mode="r")
                    self.mat = np.load(mat_p, mmap_mode="r")
                    self.version = version
            except (OSError, ValueError):
                pass
            if self.version != version or self.mat is None:
                self._load_from_db(con, version)
            self._cleanup_old_caches(version)
            if self.mat.nbytes * 2 <= FLOAT32_MAX_BYTES:
                self.mat = np.asarray(self.mat, dtype=np.float32)
            return len(self.ids) > 0

    def _load_from_db(self, con, version: str) -> None:
        n = con.execute("SELECT count(*) FROM vectors").fetchone()[0]
        first = con.execute("SELECT vec FROM vectors LIMIT 1").fetchone()
        dim = len(first[0]) // 2 if first else 0
        ids = np.empty(n, dtype=np.int64)
        mat = np.empty((n, dim), dtype=np.float16)
        i = 0
        for row in con.execute("SELECT chunk_id, vec FROM vectors ORDER BY chunk_id"):
            if i >= n:
                break
            ids[i] = row[0]
            mat[i] = np.frombuffer(row[1], dtype=np.float16)
            i += 1
        self.ids, self.mat, self.version = ids[:i], mat[:i], version
        ids_p, mat_p = self._cache_paths(version)
        try:
            # ids last: a cache counts as complete only when both files exist.
            for p, arr in ((mat_p, self.mat), (ids_p, self.ids)):
                tmp = p.with_name(p.stem + f".{os.getpid()}.tmp")
                with open(tmp, "wb") as f:
                    np.save(f, arr)
                os.replace(tmp, p)
        except OSError as e:
            log.warning("could not write vector cache: %s", e)

    def query(self, qvec: np.ndarray, k: int) -> list[tuple[int, float]]:
        n = len(self.ids)
        if n == 0:
            return []
        q = qvec.astype(np.float32)
        scores = np.empty(n, dtype=np.float32)
        for start in range(0, n, BLOCK_ROWS):
            block = np.asarray(self.mat[start:start + BLOCK_ROWS], dtype=np.float32)
            scores[start:start + len(block)] = block @ q
        k = min(k, n)
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return [(int(self.ids[i]), float(scores[i])) for i in top]

    def similarity(self, qvec: np.ndarray, chunk_ids: list[int]) -> dict[int, float]:
        """Cosine similarity between the query and the given chunks (vectors are normalized)."""
        if self.ids is None or len(self.ids) == 0 or not chunk_ids:
            return {}
        wanted = np.asarray(chunk_ids, dtype=self.ids.dtype)
        pos = np.searchsorted(self.ids, wanted)  # ids are sorted (loaded by chunk_id)
        pos = np.clip(pos, 0, len(self.ids) - 1)
        found = self.ids[pos] == wanted
        q = qvec.astype(np.float32)
        return {int(c): float(np.asarray(self.mat[p], dtype=np.float32) @ q)
                for c, p, ok in zip(chunk_ids, pos, found) if ok}


@dataclass
class Hit:
    chunk_id: int
    doc_id: int
    path: str
    ext: str
    mtime: float
    page_no: int
    n_pages: int
    text: str
    sources: list[str]
    score: float
    similarity: float | None = None  # cosine similarity query/chunk (None without embeddings)


class Searcher:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.vectors = VectorIndex(cfg)
        self._embedder = None
        self._embedder_lock = threading.Lock()
        self.embedder_error: str | None = None

    def embedder(self):
        with self._embedder_lock:
            if self._embedder is None and self.embedder_error is None:
                try:
                    from .embeddings import Embedder
                    self._embedder = Embedder(self.cfg)
                except Exception as e:
                    self.embedder_error = f"{type(e).__name__}: {e}"
                    log.exception("embedding model unavailable")
            return self._embedder

    @staticmethod
    def _filters(folder: str | None, file_type: str | None, modified_after: str | None,
                 facets: dict | None = None):
        clauses, params = [], []
        # Facet filters (project, collection, section, doc_type): comma-separated values,
        # case-insensitive. Documents without that facet are excluded by the filter.
        for col, value in (facets or {}).items():
            values = [v.strip() for v in str(value).split(",") if v.strip()] if value else []
            if values:
                marks = ",".join("?" * len(values))
                if col == "machine":  # documents of projects with that machine family or model
                    from .machines import confirmed_sql
                    clauses.append(
                        "d.id IN (SELECT doc_id FROM doc_facets WHERE project IN (SELECT project FROM "
                        f"project_machines pm WHERE {confirmed_sql()} AND (family COLLATE NOCASE IN ({marks}) "
                        f"OR model COLLATE NOCASE IN ({marks}))))")
                    params += values + values
                elif col == "project":  # a project name or one of its sub-projects (machines)
                    clauses.append(f"d.id IN (SELECT doc_id FROM doc_facets WHERE project COLLATE NOCASE "
                                   f"IN ({marks}) OR subproject COLLATE NOCASE IN ({marks}))")
                    params += values + values
                else:
                    clauses.append(f"d.id IN (SELECT doc_id FROM doc_facets WHERE {col} COLLATE NOCASE IN ({marks}))")
                    params += values
        if folder:
            key = store.path_key(folder)
            clauses.append("(d.path_key = ? OR substr(d.path_key, 1, ?) = ?)")
            prefix = key.rstrip(os.sep) + os.sep
            params += [key, len(prefix), prefix]
        if file_type:
            ext = "." + file_type.lower().lstrip(".")
            clauses.append("d.ext = ?")
            params.append(ext)
        ts = parse_date(modified_after)
        if ts is not None:
            clauses.append("d.mtime >= ?")
            params.append(ts)
        return clauses, params

    def keyword(self, con, query: str, limit: int, clauses, params) -> list[int]:
        fts = build_fts_query(query)
        if not fts:
            return []
        where = " AND ".join(["chunks_fts MATCH ?"] + clauses)
        sql = (f"SELECT f.rowid FROM chunks_fts f JOIN chunks c ON c.id = f.rowid "
               f"JOIN docs d ON d.id = c.doc_id WHERE {where} ORDER BY f.rank LIMIT ?")
        try:
            return [r[0] for r in con.execute(sql, [fts] + params + [limit])]
        except Exception as e:
            log.warning("FTS query failed (%s): %s", fts, e)
            return []

    def semantic(self, con, query: str, limit: int) -> tuple[list[int], np.ndarray | None]:
        """Chunk ids closest in meaning, and the query vector (None if unavailable)."""
        if not self.cfg.embeddings_enabled or not self.vectors.ensure(con):
            return [], None
        emb = self.embedder()
        if emb is None:
            return [], None
        qvec = emb.embed_query(query)
        return [cid for cid, _ in self.vectors.query(qvec, limit)], qvec

    def search(self, con, query: str, limit: int = 10, folder: str | None = None,
               file_type: str | None = None, modified_after: str | None = None,
               mode: str = "hybrid", allowed=None, facets: dict | None = None) -> list[Hit]:
        clauses, params = self._filters(folder, file_type, modified_after, facets)
        filtered = bool(clauses)
        ranked: dict[str, list[int]] = {}
        qvec = None
        if mode in ("hybrid", "keyword"):
            ranked["keyword"] = self.keyword(con, query, 100, clauses, params)
        if mode in ("hybrid", "semantic"):
            ranked["semantic"], qvec = self.semantic(con, query, 2000 if filtered else 100)

        fused: dict[int, float] = {}
        sources: dict[int, list[str]] = {}
        for name, ids in ranked.items():
            for rank, cid in enumerate(ids):
                fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank + 1)
                sources.setdefault(cid, []).append(name)
        if not fused:
            return []
        order = sorted(fused, key=fused.get, reverse=True)

        hits: list[Hit] = []
        per_doc: dict[int, int] = {}
        for start in range(0, len(order), 500):
            batch = order[start:start + 500]
            where = " AND ".join([f"c.id IN ({','.join('?' * len(batch))})"] + clauses)
            rows = {r["id"]: r for r in con.execute(
                f"""SELECT c.id, c.doc_id, c.page_no, c.text, d.path, d.path_key, d.ext, d.mtime, d.n_pages
                    FROM chunks c JOIN docs d ON d.id = c.doc_id WHERE {where}""", batch + params)}
            for cid in batch:
                r = rows.get(cid)
                if r is None or per_doc.get(r["doc_id"], 0) >= MAX_PER_DOC:
                    continue
                # Excluded or removed folders are hidden even before the next scan purges them.
                if allowed is not None and not allowed(r["path_key"]):
                    continue
                per_doc[r["doc_id"]] = per_doc.get(r["doc_id"], 0) + 1
                hits.append(Hit(cid, r["doc_id"], r["path"], r["ext"], r["mtime"], r["page_no"],
                                r["n_pages"] or 0, r["text"], sources[cid], fused[cid]))
                if len(hits) >= limit:
                    break
            if len(hits) >= limit:
                break
        if qvec is not None:  # also for hits found by keyword only
            sims = self.vectors.similarity(qvec, [h.chunk_id for h in hits])
            for h in hits:
                h.similarity = sims.get(h.chunk_id)
        return hits

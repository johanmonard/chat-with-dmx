"""Hybrid search: SQLite FTS5 keyword search + embedding similarity, fused with RRF."""

from __future__ import annotations

import json
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

    def _cache_paths(self):
        d = self.cfg.data_dir
        return d / "vec_cache.json", d / "vec_ids.npy", d / "vec_mat.npy"

    def ensure(self, con) -> bool:
        version = store.get_meta(con, "vec_version", "0")
        with self.lock:
            if version == self.version and self.mat is not None:
                return len(self.ids) > 0
            meta_p, ids_p, mat_p = self._cache_paths()
            try:
                cached = json.loads(meta_p.read_text())
                if cached.get("version") == version:
                    self.ids = np.load(ids_p, mmap_mode="r")
                    self.mat = np.load(mat_p, mmap_mode="r")
                    self.version = version
            except (OSError, ValueError):
                pass
            if self.version != version or self.mat is None:
                self._load_from_db(con, version)
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
        meta_p, ids_p, mat_p = self._cache_paths()
        try:
            for p, arr in ((ids_p, self.ids), (mat_p, self.mat)):
                tmp = p.with_suffix(".tmp.npy")
                np.save(tmp, arr)
                os.replace(tmp, p)
            meta_p.write_text(json.dumps({"version": version}))
        except OSError as e:  # e.g. cache file in use by another process on Windows
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
    def _filters(folder: str | None, file_type: str | None, modified_after: str | None):
        clauses, params = [], []
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

    def semantic(self, con, query: str, limit: int) -> list[int]:
        if not self.cfg.embeddings_enabled or not self.vectors.ensure(con):
            return []
        emb = self.embedder()
        if emb is None:
            return []
        return [cid for cid, _ in self.vectors.query(emb.embed_query(query), limit)]

    def search(self, con, query: str, limit: int = 10, folder: str | None = None,
               file_type: str | None = None, modified_after: str | None = None,
               mode: str = "hybrid", allowed=None) -> list[Hit]:
        clauses, params = self._filters(folder, file_type, modified_after)
        filtered = bool(clauses)
        ranked: dict[str, list[int]] = {}
        if mode in ("hybrid", "keyword"):
            ranked["keyword"] = self.keyword(con, query, 100, clauses, params)
        if mode in ("hybrid", "semantic"):
            ranked["semantic"] = self.semantic(con, query, 2000 if filtered else 100)

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
                    return hits
        return hits

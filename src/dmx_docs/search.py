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
from .access import readable_clause
from .config import Config

log = logging.getLogger("dmx_docs.search")

RRF_K = 60
THESAURUS_WEIGHT = 0.5  # weight of the thesaurus-expanded keyword list in the fusion
MAX_PER_DOC = 3
BLOCK_ROWS = 131072
IVF_TRAIN_ROWS = 100_000  # rows the IVF cells are trained on
IVF_ADD_ROWS = 200_000    # rows converted to float32 at a time when filling the IVF index
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


def _quote(text: str) -> str | None:
    words = re.findall(r"\w+", text)
    return '"' + " ".join(words) + '"' if words else None


def build_fts_query(query: str, mode: str = "OR", thesaurus=None, expanded: list | None = None) -> str | None:
    """Turn free text into a safe FTS5 query.

    "quoted text" stays a phrase, codes like MN-114 or 12.345.6 become phrases,
    a trailing * means prefix search; other words are OR-ed (ranked by BM25).
    With a thesaurus, a word or phrase of a known concept becomes (term OR equivalents);
    the expansions are appended to `expanded` as (term, [equivalents]).
    """
    terms: list[str] = []
    for phrase in _PHRASE.findall(query):
        q = _quote(phrase)
        if q:
            terms.append(q)
    rest = _PHRASE.sub(" ", query)
    tokens = _COMPOUND.findall(rest)
    plain = [t for t in tokens if not t.endswith("*")]
    units = dict(thesaurus.expand(plain)) if thesaurus is not None and plain else {}
    used: set[str] = set()
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        # A thesaurus phrase may span several tokens: find the unit starting here.
        match = next((u for u in units if units[u] and u not in used
                      and " ".join(tokens[i:i + len(u.split())]) == u), None)
        if match:
            used.add(match)
            group = [g for g in (_quote(t) for t in [match] + units[match]) if g]
            terms.append("(" + " OR ".join(dict.fromkeys(group)) + ")")
            if expanded is not None:
                expanded.append((match, units[match]))
            i += len(match.split())
            continue
        i += 1
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
        self.ivf = None  # faiss IVF index (worlds of ivf_min_rows+ vectors), None for exact search
        self.lock = threading.Lock()
        self._rule_rows: tuple | None = None  # ((vector version, access version), int32 array)

    def _cache_paths(self, version: str):
        # One file pair per vector version: a new cache never has to replace a file that
        # another process (e.g. Claude Desktop's server) still has memory-mapped.
        d = self.cfg.world_dir
        safe = re.sub(r"[^0-9A-Za-z]", "", version)[:40] or "0"
        return d / f"vec_ids.{safe}.npy", d / f"vec_mat.{safe}.npy"

    def _ivf_path(self, version: str):
        safe = re.sub(r"[^0-9A-Za-z]", "", version)[:40] or "0"
        return self.cfg.world_dir / f"vec_ivf.{safe}.faiss"

    @property
    def uses_ivf(self) -> bool:
        return self.ivf is not None

    def _cleanup_old_caches(self, keep: str) -> None:
        keep_names = {p.name for p in self._cache_paths(keep)} | {self._ivf_path(keep).name}
        for p in list(self.cfg.world_dir.glob("vec_*.npy")) + list(self.cfg.world_dir.glob("vec_ivf.*.faiss")) + list(self.cfg.world_dir.glob("vec_*.tmp")) + list(self.cfg.world_dir.glob("vec_cache.json")):
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
            self.ivf = None
            if len(self.ids) >= self.cfg.ivf_min_rows:
                self.ivf = self._load_ivf(version)
            if self.ivf is None and self.mat.nbytes * 2 <= FLOAT32_MAX_BYTES:
                self.mat = np.asarray(self.mat, dtype=np.float32)
            return len(self.ids) > 0

    def _load_ivf(self, version: str):
        """The faiss IVF index of the current vectors: read from its cache file, else built and saved.
        None when faiss is unavailable (exact search is used then)."""
        try:
            import faiss
        except ImportError:
            log.warning("faiss is not installed: exact vector search on a large world")
            return None
        path = self._ivf_path(version)
        n, dim = self.mat.shape
        if path.exists():
            try:
                index = faiss.read_index(str(path))
                if index.ntotal == n and index.d == dim:
                    return index
            except (OSError, RuntimeError) as e:
                log.warning("vector index file unreadable, rebuilding: %s", e)
        nlist = min(self.cfg.ivf_lists, n)
        index = faiss.IndexIVFScalarQuantizer(faiss.IndexFlatIP(dim), dim, nlist,
                                              faiss.ScalarQuantizer.QT_fp16, faiss.METRIC_INNER_PRODUCT)
        sample = np.sort(np.random.default_rng(0).choice(n, size=min(n, IVF_TRAIN_ROWS), replace=False))
        index.train(np.asarray(self.mat[sample], dtype=np.float32))
        for start in range(0, n, IVF_ADD_ROWS):
            index.add(np.asarray(self.mat[start:start + IVF_ADD_ROWS], dtype=np.float32))
        try:
            tmp = path.with_name(path.stem + f".{os.getpid()}.tmp")
            faiss.write_index(index, str(tmp))
            os.replace(tmp, path)
        except OSError as e:
            log.warning("could not write vector index file: %s", e)
        return index

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

    def rule_rows(self, con, folder_rule: dict[str, int], version: str) -> np.ndarray:
        """Rule id of each vector row's document folder (-1: no rule), cached per versions."""
        with self.lock:
            ids, vec_version = self.ids, self.version
        key = (vec_version, version)
        cached = self._rule_rows
        if cached is not None and cached[0] == key and len(cached[1]) == len(ids):
            return cached[1]
        out = np.full(len(ids), -1, dtype=np.int32)
        by_chunk = {}
        for cid, fk in con.execute("SELECT c.id, d.folder_key FROM chunks c JOIN docs d ON d.id = c.doc_id "
                                   "WHERE c.id IN (SELECT chunk_id FROM vectors)"):
            rid = folder_rule.get(fk)
            if rid is not None:
                by_chunk[cid] = rid
        if by_chunk:
            cids = np.fromiter(by_chunk.keys(), dtype=np.int64, count=len(by_chunk))
            rids = np.fromiter(by_chunk.values(), dtype=np.int32, count=len(by_chunk))
            pos = np.searchsorted(ids, cids)
            pos[pos >= len(ids)] = 0
            ok = ids[pos] == cids
            out[pos[ok]] = rids[ok]
        self._rule_rows = (key, out)
        return out

    def query(self, qvec: np.ndarray, k: int, mask: np.ndarray | None = None) -> list[tuple[int, float]]:
        n = len(self.ids)
        if n == 0:
            return []
        if mask is not None and len(mask) != n:
            # index replaced under the mask: show nothing rather than the wrong rows
            log.warning("semantic search skipped: access mask has %d rows, vector index %d", len(mask), n)
            return []
        if self.ivf is not None:
            return self._query_ivf(qvec, k, mask)
        q = qvec.astype(np.float32)
        scores = np.empty(n, dtype=np.float32)
        for start in range(0, n, BLOCK_ROWS):
            block = np.asarray(self.mat[start:start + BLOCK_ROWS], dtype=np.float32)
            scores[start:start + len(block)] = block @ q
        if mask is not None:
            scores[~mask] = -np.inf
            n = int(mask.sum())
            if n == 0:
                return []
        k = min(k, n)
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return [(int(self.ids[i]), float(scores[i])) for i in top]

    def _query_ivf(self, qvec: np.ndarray, k: int, mask: np.ndarray | None) -> list[tuple[int, float]]:
        import faiss
        n = len(self.ids)
        bitmap = None  # faiss does not own it: kept alive until the search returns
        if mask is not None:
            n = int(mask.sum())
            if n == 0:
                return []
            bitmap = np.packbits(mask, bitorder="little")
        k = min(k, n)
        params = faiss.SearchParametersIVF(sel=faiss.IDSelectorBitmap(bitmap) if bitmap is not None else None,
                                           nprobe=self.cfg.ivf_nprobe)
        scores, rows = self.ivf.search(qvec.astype(np.float32)[None, :], k, params=params)
        return [(int(self.ids[r]), float(sc)) for sc, r in zip(scores[0], rows[0]) if r != -1]

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
        self._thesaurus = None
        self._thesaurus_mtime = None
        self.last_expanded: list = []  # expansions used by the last keyword query

    def thesaurus(self):
        """The thesaurus file, reloaded when it changes (it is edited by hand)."""
        path = self.cfg.thesaurus_path
        try:
            mtime = os.path.getmtime(path) if path else None
        except OSError:
            mtime = None
        if mtime != self._thesaurus_mtime:
            from .thesaurus import Thesaurus
            try:
                self._thesaurus = Thesaurus.load(path) if mtime else None
            except Exception as e:  # noqa: BLE001 - a typo in the file must not break search
                log.warning("thesaurus not loaded: %s", e)
                self._thesaurus = None
            self._thesaurus_mtime = mtime
        return self._thesaurus

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
    def _offer_filter(col: str, values: list, marks: str, has_register: bool) -> tuple[str, list]:
        """Offers world facets that are not exact doc_facets values: machine (an element of the '|' list),
        client and country (parts of the name). Those read through the offer register (year_from, year_to,
        status) match nothing when the register has not been built."""
        offer_docs = "d.id IN (SELECT doc_id FROM doc_facets WHERE offer IN (SELECT offer_key FROM offers WHERE {}))"
        if col == "machine":  # '|Paloma|Paloma 3R|': a family or a model
            return ("d.id IN (SELECT doc_id FROM doc_facets WHERE "
                    + " OR ".join("machine LIKE ?" for _ in values) + ")", [f"%|{v}|%" for v in values])
        if col in ("client", "country"):  # part of the name (case ignored, comma = or)
            likes = [f"%{v}%" for v in values]

            def any_like(column: str) -> str:
                return "(" + " OR ".join(f"{column} LIKE ?" for _ in values) + ")"
            clause, args = f"d.id IN (SELECT doc_id FROM doc_facets WHERE {any_like(col)}", list(likes)
            if col == "country" and has_register:  # the folder's country, or the offer's in the register
                clause += f" OR offer IN (SELECT offer_key FROM offers WHERE {any_like('country')})"
                args += likes
            return clause + ")", args
        if not has_register:
            return "0", []
        if col == "status":
            return offer_docs.format(f"status COLLATE NOCASE IN ({marks})"), values
        return offer_docs.format("year >= ?" if col == "year_from" else "year <= ?"), [int(values[0])]

    @staticmethod
    def _filters(folder: str | None, file_type: str | None, modified_after: str | None,
                 facets: dict | None = None, profile: str = "projects", offer_register: bool = False,
                 con=None, readable: str | None = None):
        """SQL clauses and parameters of the filters. readable (with con): the caller's readable
        documents, among which a project's machines are confirmed for machine=."""
        clauses, params = [], []
        # Facet filters (project, collection, section, doc_type): comma-separated values,
        # case-insensitive. Documents without that facet are excluded by the filter.
        for col, value in (facets or {}).items():
            values = [v.strip() for v in str(value).split(",") if v.strip()] if value else []
            if col == "offer":  # as folders and files write it: B-250306 V1, 3002113_02, Offer - 3001234_00.pdf
                from . import offerkeys
                values = [offerkeys.offer_key(v) or v for v in values]
            if values:
                marks = ",".join("?" * len(values))
                if profile != "projects" and col in ("machine", "client", "country", "status", "year_from",
                                                     "year_to"):
                    clause, args = Searcher._offer_filter(col, values, marks, offer_register)
                    clauses.append(clause)
                    params += args
                elif col == "machine":  # documents of projects with that machine family or model
                    from .machines import filter_sql
                    cond, args = filter_sql(con, values, "project", readable=readable)
                    clauses.append(f"d.id IN (SELECT doc_id FROM doc_facets WHERE {cond})")
                    params += args
                elif profile == "projects" and col in ("client", "country"):  # projects of that client / country
                    from . import register
                    cond, args = (register.client_clause if col == "client" else register.country_clause)(str(value))
                    # a restricted caller: the register's own links, none made from hidden documents
                    own = register.own_project_sql("rm") if readable else "rm.project"
                    clauses.append("d.id IN (SELECT doc_id FROM doc_facets WHERE project IN "
                                   f"(SELECT {own} FROM register_machines rm WHERE {cond}))")
                    params += args
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

    def keyword(self, con, query: str, limit: int, clauses, params, expand: bool = False) -> list[int]:
        self.last_expanded = []
        fts = build_fts_query(query, thesaurus=self.thesaurus() if expand else None,
                              expanded=self.last_expanded)
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

    def semantic(self, con, query: str, limit: int, row_mask: np.ndarray | None = None) -> tuple[list[int], np.ndarray | None]:
        """Chunk ids closest in meaning, and the query vector (None if unavailable)."""
        if not self.cfg.embeddings_enabled or not self.vectors.ensure(con):
            return [], None
        emb = self.embedder()
        if emb is None:
            return [], None
        qvec = emb.embed_query(query)
        return [cid for cid, _ in self.vectors.query(qvec, limit, row_mask)], qvec

    def search(self, con, query: str, limit: int = 10, folder: str | None = None,
               file_type: str | None = None, modified_after: str | None = None,
               mode: str = "hybrid", allowed=None, row_mask=None, folder_ok=None, facets: dict | None = None,
               expand: bool | None = None, pictures_only: bool = False, profile: str = "projects") -> list[Hit]:
        offer_register = profile != "projects" and store.has_table(con, "offers")
        readable = readable_clause(con, folder_ok)  # hidden documents must not take keyword slots
        clauses, params = self._filters(folder, file_type, modified_after, facets, profile, offer_register,
                                        con=con, readable=readable)
        if pictures_only:  # passages written from pictures by the vision model (describe.py)
            clauses.append("c.id IN (SELECT chunk_id FROM picture_uses WHERE chunk_id IS NOT NULL)")
        filtered = bool(clauses)
        if readable:
            clauses.append(readable)
        ranked: dict[str, list[int]] = {}
        qvec = None
        self.last_expanded = []
        if mode in ("hybrid", "keyword"):
            ranked["keyword"] = self.keyword(con, query, 100, clauses, params)
            if self.cfg.expand_synonyms if expand is None else expand:
                # The user's own words keep their ranking; the thesaurus only adds a second,
                # lower-weighted list (equal-weight synonyms diluted the query in tests).
                expanded = self.keyword(con, query, 100, clauses, params, expand=True)
                if self.last_expanded:
                    ranked["thesaurus"] = expanded
        if mode in ("hybrid", "semantic"):
            ranked["semantic"], qvec = self.semantic(con, query, 2000 if filtered else 100, row_mask)

        fused: dict[int, float] = {}
        sources: dict[int, list[str]] = {}
        for name, ids in ranked.items():
            weight = THESAURUS_WEIGHT if name == "thesaurus" else 1.0
            for rank, cid in enumerate(ids):
                fused[cid] = fused.get(cid, 0.0) + weight / (RRF_K + rank + 1)
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

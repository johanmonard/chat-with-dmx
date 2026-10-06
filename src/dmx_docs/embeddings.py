"""Embedding model wrapper and the `embed` step that vectorizes chunks."""

from __future__ import annotations

import hashlib
import logging
import re
import time
import unicodedata

import numpy as np

from . import store
from .config import Config

log = logging.getLogger("dmx_docs.embeddings")

EMBED_TEXT_CHARS = 2400  # models truncate at 512 tokens anyway
FETCH_BATCH = 256
VERSION_BUMP_EVERY = 5000


def _normalize_word(w: str) -> str:
    w = unicodedata.normalize("NFKD", w.lower())
    return "".join(c for c in w if not unicodedata.combining(c))


class HashEmbedder:
    """Tiny bag-of-words embedder, for tests only (model = 'hash:<dim>')."""

    def __init__(self, dim: int = 256):
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, t in enumerate(texts):
            for w in re.findall(r"\w+", t):
                h = int.from_bytes(hashlib.md5(_normalize_word(w).encode()).digest()[:4], "little")
                out[i, h % self.dim] += 1.0
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        return out / np.maximum(norms, 1e-9)


class Embedder:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        model = cfg.embedding_model
        if model.startswith("hash:"):
            self._model = HashEmbedder(int(model.split(":", 1)[1] or 256))
            self._fast = False
        else:
            from fastembed import TextEmbedding

            cfg.models_dir.mkdir(parents=True, exist_ok=True)
            self._model = TextEmbedding(model_name=model, cache_dir=str(cfg.models_dir),
                                        threads=cfg.embed_threads)
            self._fast = True

    def _embed(self, texts: list[str]) -> np.ndarray:
        if not self._fast:
            return self._model.embed(texts)
        vecs = np.asarray(list(self._model.embed(texts, batch_size=self.cfg.embed_batch_size)),
                          dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        return vecs / np.maximum(norms, 1e-9)

    def embed_passages(self, texts: list[str]) -> np.ndarray:
        return self._embed([self.cfg.passage_prefix + t for t in texts])

    def embed_query(self, text: str) -> np.ndarray:
        return self._embed([self.cfg.query_prefix + text])[0]


def passage_text(name: str, title: str | None, text: str) -> str:
    """Text actually embedded: file name/title give the chunk its context."""
    header = name if not title or title in name else f"{name} — {title}"
    return f"{header}\n{text}"[:EMBED_TEXT_CHARS]


def run_embed(cfg: Config, max_minutes: float | None = None, reset: bool = False, progress=print) -> int:
    if not cfg.embeddings_enabled:
        progress("Embeddings are disabled in the configuration ([embeddings] enabled = false).")
        return 0
    con = store.connect(cfg.db_path)
    con.isolation_level = None
    previous = store.get_meta(con, "embedding_model")
    if previous and previous != cfg.embedding_model:
        if not reset:
            raise SystemExit(
                f"The index was embedded with '{previous}' but the configuration says "
                f"'{cfg.embedding_model}'. Run `dmx-docs embed --reset` to re-embed everything.")
    if reset:
        progress("Resetting all embeddings ...")
        con.execute("BEGIN")
        con.execute("DELETE FROM vectors")
        con.execute("UPDATE chunks SET embedded=0 WHERE embedded=1")
        store.bump_vector_version(con)
        con.execute("COMMIT")

    todo = con.execute("SELECT count(*) FROM chunks WHERE embedded=0").fetchone()[0]
    if todo == 0:
        progress("All chunks are already embedded.")
        return 0
    progress(f"Loading embedding model {cfg.embedding_model} (first time: download) ...")
    embedder = Embedder(cfg)
    store.set_meta(con, "embedding_model", cfg.embedding_model)
    progress(f"{todo} chunks to embed.")

    deadline = time.monotonic() + max_minutes * 60 if max_minutes else None
    started = time.monotonic()
    done = 0
    since_bump = 0
    last_report = 0.0
    last_id = 0
    while True:
        if deadline and time.monotonic() > deadline:
            progress("Time budget reached, stopping (run again to continue).")
            break
        rows = con.execute(
            """SELECT c.id, c.text, d.name, d.title FROM chunks c JOIN docs d ON d.id = c.doc_id
               WHERE c.embedded = 0 AND c.id > ? ORDER BY c.id LIMIT ?""",
            (last_id, FETCH_BATCH)).fetchall()
        if not rows:
            break
        last_id = rows[-1]["id"]
        vecs = embedder.embed_passages([passage_text(r["name"], r["title"], r["text"]) for r in rows])
        con.execute("BEGIN")
        con.executemany("INSERT OR REPLACE INTO vectors(chunk_id, vec) VALUES(?, ?)",
                        [(r["id"], v.astype(np.float16).tobytes()) for r, v in zip(rows, vecs)])
        con.executemany("UPDATE chunks SET embedded=1 WHERE id=?", [(r["id"],) for r in rows])
        done += len(rows)
        since_bump += len(rows)
        if since_bump >= VERSION_BUMP_EVERY:
            store.bump_vector_version(con)
            since_bump = 0
        con.execute("COMMIT")
        now = time.monotonic()
        if now - last_report > 15:
            rate = done / (now - started)
            remaining = (todo - done) / rate if rate else 0
            progress(f"  {done}/{todo} chunks ({rate:.1f}/s, ~{remaining / 3600:.1f} h remaining)")
            last_report = now
    con.execute("BEGIN")
    store.bump_vector_version(con)
    con.execute("COMMIT")
    progress(f"Embedded {done} chunks in {(time.monotonic() - started) / 60:.1f} min.")
    con.close()
    return done

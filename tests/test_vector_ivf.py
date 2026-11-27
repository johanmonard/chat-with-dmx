"""faiss IVF index for large worlds (VectorIndex), on a synthetic world."""
from __future__ import annotations

import numpy as np
import pytest

from dmx_docs import store
from dmx_docs.search import VectorIndex

N, DIM = 3000, 256  # 256: the width of the test embedder (hash:256)


def _fill(con, vecs, first_id=1):
    con.execute("DELETE FROM vectors")
    con.executemany("INSERT INTO vectors(chunk_id, vec) VALUES(?, ?)",
                    [(first_id + i, v.astype(np.float16).tobytes()) for i, v in enumerate(vecs)])
    store.bump_vector_version(con)
    con.commit()


@pytest.fixture
def ivf_world(tmp_path, make_cfg):
    rng = np.random.default_rng(1)
    vecs = rng.normal(size=(N, DIM)).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    cfg = make_cfg(tmp_path / "docs", ivf_min_rows=1000, ivf_lists=8, ivf_nprobe=8)
    con = store.connect(cfg.db_path)
    _fill(con, vecs)
    vi = VectorIndex(cfg)
    assert vi.ensure(con)
    yield cfg, con, vi, vecs
    con.close()


def _exact(vecs, q, k, mask=None):
    f16 = vecs.astype(np.float16).astype(np.float32)
    scores = f16 @ q
    if mask is not None:
        scores[~mask] = -np.inf
    return [int(i) + 1 for i in np.argsort(-scores)[:k]]


def _queries(vecs, n=5):
    rng = np.random.default_rng(2)
    q = rng.normal(size=(n, DIM)).astype(np.float32)
    return q / np.linalg.norm(q, axis=1, keepdims=True)


def test_ivf_search_matches_exact_when_every_list_is_searched(ivf_world):
    cfg, con, vi, vecs = ivf_world
    assert vi.uses_ivf and vi.mat.dtype == np.float16
    for q in _queries(vecs):
        got = vi.query(q, 10)
        assert [c for c, _ in got] == _exact(vecs, q, 10)
        assert got[0][1] == pytest.approx(float(vecs[got[0][0] - 1] @ q), abs=1e-2)


def test_ivf_respects_the_row_mask(ivf_world):
    cfg, con, vi, vecs = ivf_world
    mask = np.zeros(N, dtype=bool)
    mask[::2] = True
    for q in _queries(vecs):
        got = vi.query(q, 10, mask)
        assert len(got) == 10
        assert all(mask[c - 1] for c, _ in got)
        assert [c for c, _ in got] == _exact(vecs, q, 10, mask)


def test_ivf_with_an_empty_mask_returns_nothing(ivf_world):
    cfg, con, vi, vecs = ivf_world
    assert vi.query(_queries(vecs)[0], 10, np.zeros(N, dtype=bool)) == []


def test_ivf_index_file_is_reused_then_replaced_on_a_new_vector_version(ivf_world):
    cfg, con, vi, vecs = ivf_world
    files = list(cfg.world_dir.glob("vec_ivf.*.faiss"))
    assert len(files) == 1
    mtime = files[0].stat().st_mtime_ns
    other = VectorIndex(cfg)
    assert other.ensure(con) and other.uses_ivf
    assert files[0].stat().st_mtime_ns == mtime
    assert [c for c, _ in other.query(_queries(vecs)[0], 5)] == _exact(vecs, _queries(vecs)[0], 5)
    _fill(con, vecs[:2500])
    fresh = VectorIndex(cfg)
    assert fresh.ensure(con)
    new_files = list(cfg.world_dir.glob("vec_ivf.*.faiss"))
    assert len(new_files) == 1 and new_files[0] != files[0]
    assert len(fresh.ids) == 2500


def test_small_world_keeps_exact_search(tmp_path, make_cfg):
    rng = np.random.default_rng(3)
    vecs = rng.normal(size=(50, DIM)).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    cfg = make_cfg(tmp_path / "docs", ivf_min_rows=1000, ivf_lists=8, ivf_nprobe=8)
    con = store.connect(cfg.db_path)
    _fill(con, vecs)
    vi = VectorIndex(cfg)
    assert vi.ensure(con)
    assert not vi.uses_ivf
    assert not list(cfg.world_dir.glob("vec_ivf.*"))
    assert [c for c, _ in vi.query(vecs[0], 3)][0] == 1
    con.close()


def test_ivf_ignores_a_corrupt_or_stale_index_file_and_rebuilds_it(ivf_world):
    import faiss
    cfg, con, vi, vecs = ivf_world
    path = next(cfg.world_dir.glob("vec_ivf.*.faiss"))
    q = _queries(vecs)[0]
    path.write_bytes(b"not a faiss file")
    broken = VectorIndex(cfg)
    assert broken.ensure(con) and broken.uses_ivf
    assert faiss.read_index(str(path)).ntotal == N  # replaced by a good file
    assert [c for c, _ in broken.query(q, 10)] == _exact(vecs, q, 10)
    # a readable file with the wrong number of rows is stale too
    stale = faiss.read_index(str(path))
    stale.remove_ids(faiss.IDSelectorRange(0, 100))
    faiss.write_index(stale, str(path))
    again = VectorIndex(cfg)
    assert again.ensure(con) and again.ivf.ntotal == N
    assert faiss.read_index(str(path)).ntotal == N
    assert [c for c, _ in again.query(q, 10)] == _exact(vecs, q, 10)


def test_ivf_search_path_with_no_readable_rows_is_empty(ivf_world):
    from dmx_docs.search import Searcher
    cfg, con, vi, vecs = ivf_world
    searcher = Searcher(cfg)
    none = np.zeros(N, dtype=bool)
    assert searcher.semantic(con, "pompe", 10, none)[0] == []
    assert searcher.search(con, "pompe", mode="semantic", row_mask=none) == []
    assert searcher.vectors.uses_ivf
    assert len(searcher.semantic(con, "pompe", 10)[0]) == 10  # not masked: results


def test_ivf_similarity_reads_the_float16_rows(ivf_world):
    cfg, con, vi, vecs = ivf_world
    assert vi.uses_ivf and vi.mat.dtype == np.float16
    q = _queries(vecs)[0]
    ids = [1, 7, 2999, 3000, 99999]  # the last one does not exist
    got = vi.similarity(q, ids)
    assert set(got) == {1, 7, 2999, 3000}
    for c, sim in got.items():
        assert sim == pytest.approx(float(vecs[c - 1] @ q), abs=1e-2)
    assert vi.mat.dtype == np.float16  # still not converted by similarity()


class _NoSearch:
    """A faiss index stand-in that must never be searched."""
    def __init__(self, ntotal):
        self.ntotal = ntotal

    def search(self, *a, **kw):
        raise AssertionError("faiss search must not be called")


def test_ivf_mask_or_index_of_another_size_returns_nothing_without_searching(ivf_world):
    from dataclasses import replace
    cfg, con, vi, vecs = ivf_world
    q = _queries(vecs)[0]
    vi._snap = replace(vi._snap, ivf=_NoSearch(N))
    assert vi.query(q, 10, np.ones(N - 1, dtype=bool)) == []  # mask of another length
    vi._snap = replace(vi._snap, ivf=_NoSearch(N - 5))
    assert vi.query(q, 10, np.ones(N, dtype=bool)) == []  # index of another size than the ids
    assert vi.query(q, 10) == []


def test_ivf_query_in_progress_keeps_its_own_snapshot(ivf_world):
    cfg, con, vi, vecs = ivf_world
    q = _queries(vecs)[0]
    before = [c for c, _ in vi.query(q, 10)]
    snap = vi._snap
    _fill(con, vecs[:2500])
    assert vi.ensure(con)  # reload replaces the snapshot as a whole
    assert vi._snap is not snap and len(vi.ids) == 2500
    assert len(snap.ids) == N and snap.ivf.ntotal == N  # the old one stays consistent
    assert [c for c, _ in vi.query(q, 10)] == _exact(vecs[:2500], q, 10)
    assert before == _exact(vecs, q, 10)


@pytest.mark.parametrize("error", [ImportError("no faiss"), OSError("DLL load failed")])
def test_without_usable_faiss_a_large_world_keeps_float16_and_the_block_search(tmp_path, make_cfg, monkeypatch, caplog, error):
    import builtins
    import logging

    real_import = builtins.__import__

    def failing_import(name, *args, **kwargs):
        if name == "faiss":
            raise error
        return real_import(name, *args, **kwargs)

    rng = np.random.default_rng(1)
    vecs = rng.normal(size=(N, DIM)).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    cfg = make_cfg(tmp_path / "docs", ivf_min_rows=1000, ivf_lists=8, ivf_nprobe=8)
    con = store.connect(cfg.db_path)
    _fill(con, vecs)
    monkeypatch.setattr(builtins, "__import__", failing_import)
    caplog.set_level(logging.WARNING)
    vi = VectorIndex(cfg)
    assert vi.ensure(con)
    monkeypatch.undo()
    assert not vi.uses_ivf
    assert vi.mat.dtype == np.float16            # N >= ivf_min_rows: never converted to float32
    assert "faiss is not usable" in caplog.text
    for q in _queries(vecs):
        assert [c for c, _ in vi.query(q, 10)] == _exact(vecs, q, 10)   # block search, same answer
    con.close()

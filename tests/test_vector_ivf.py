"""faiss IVF index for large worlds (VectorIndex), on a synthetic world."""
from __future__ import annotations

import numpy as np
import pytest

from dmx_docs import store
from dmx_docs.search import VectorIndex

N, DIM = 3000, 32


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

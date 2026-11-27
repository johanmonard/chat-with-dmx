from dmx_docs.embeddings import Embedder, shared_model


def test_two_worlds_share_one_model(make_cfg, tmp_path):
    a = make_cfg(tmp_path, embedding_model="hash:64")
    b = make_cfg(tmp_path, embedding_model="hash:64", query_prefix="q: ")
    ea, eb = Embedder(a), Embedder(b)
    assert ea._model is eb._model
    assert ea.cfg.query_prefix == "" and eb.cfg.query_prefix == "q: "  # prefixes stay per Embedder


def test_a_different_model_is_loaded_separately(make_cfg, tmp_path):
    a = Embedder(make_cfg(tmp_path, embedding_model="hash:64"))
    b = Embedder(make_cfg(tmp_path, embedding_model="hash:32"))
    assert a._model is not b._model


def test_shared_model_builds_once_per_key():
    calls = []

    def factory():
        calls.append(1)
        return object()

    key = ("test-shared-model-once",)
    assert shared_model(key, factory) is shared_model(key, factory)
    assert len(calls) == 1

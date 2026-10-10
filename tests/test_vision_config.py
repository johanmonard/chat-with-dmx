from dmx_docs import store
from dmx_docs.config import load_config


def write_config(tmp_path, text):
    p = tmp_path / "config.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_vision_defaults(tmp_path):
    cfg = load_config(write_config(tmp_path, "[index]\nroots = []\n"))
    assert cfg.vision_enabled is True
    assert cfg.vision_url == "http://127.0.0.1:11434"
    assert cfg.vision_model == "gemma4:12b"
    assert (cfg.vision_timeout_s, cfg.vision_min_side, cfg.vision_pdf_picture_share,
            cfg.vision_parallel_requests) == (180, 150, 0.15, 1)


def test_vision_settings_and_world_switch(tmp_path):
    p = write_config(tmp_path, """
[vision]
url = 'http://ws032:11434'
model = 'qwen3-vl:8b'
timeout_s = 60
min_side = 200
pdf_picture_share = 0.3
parallel_requests = 2

[worlds.projects]
profile = "projects"
vision = false

[worlds.marketing]
profile = "marketing"
""")
    projects, marketing = load_config(p, world="projects"), load_config(p, world="marketing")
    assert projects.vision_enabled is False and marketing.vision_enabled is True
    assert (marketing.vision_url, marketing.vision_model, marketing.vision_timeout_s, marketing.vision_min_side,
            marketing.vision_pdf_picture_share, marketing.vision_parallel_requests) == (
        "http://ws032:11434", "qwen3-vl:8b", 60, 200, 0.3, 2)


def test_delete_doc_content_removes_picture_rows(tmp_path):
    con = store.connect(tmp_path / "i.sqlite3")
    doc_id, _ = store.upsert_doc(con, path=str(tmp_path / "a.md"), size=1, mtime=1.0, status="ok", error=None,
                                 n_pages=1, title=None, indexed_at=1.0, run_id=1, chunks=[(1, 0, "texte")])
    con.execute("INSERT INTO picture_docs(doc_id, model, version, pictures, done_at) VALUES(?, 'm', 1, 1, 1)", (doc_id,))
    con.execute("INSERT INTO picture_uses(doc_id, page_no, image_no, sha1, status, type, chunk_id) "
                "VALUES(?, 1, 1, 'x', 'described', 'photo', NULL)", (doc_id,))
    store.delete_doc_content(con, doc_id)
    assert con.execute("SELECT count(*) FROM picture_docs").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM picture_uses").fetchone()[0] == 0


def test_has_table_and_delete_chunks(tmp_path):
    con = store.connect(tmp_path / "i.sqlite3")
    assert store.has_table(con, "picture_uses") and not store.has_table(con, "nope")
    doc_id, _ = store.upsert_doc(con, path=str(tmp_path / "a.md"), size=1, mtime=1.0, status="ok", error=None,
                                 n_pages=1, title=None, indexed_at=1.0, run_id=1,
                                 chunks=[(1, 0, "alpha"), (1, 1, "beta")])
    ids = [r[0] for r in con.execute("SELECT id FROM chunks ORDER BY id")]
    con.execute("INSERT INTO vectors(chunk_id, vec) VALUES(?, x'00')", (ids[1],))
    assert store.delete_chunks(con, [ids[1]]) is True
    assert [r[0] for r in con.execute("SELECT text FROM chunks")] == ["alpha"]
    assert con.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'beta'").fetchone()[0] == 0
    assert store.delete_chunks(con, []) is False

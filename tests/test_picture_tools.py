# tests/test_picture_tools.py
import asyncio

from conftest import make_png, make_pptx
from dmx_docs import describe, store, vision
from dmx_docs.extract import pymupdf
from dmx_docs.indexer import run_index
from dmx_docs.pictures import Prepared
from dmx_docs.tools import DocTools

quiet = lambda *a, **k: None  # noqa: E731


def add_passages(cfg, name, occurrences):
    """occurrences: [(page, image, description)] saved as described pictures of document `name`."""
    con = store.connect(cfg.db_path)
    con.isolation_level = None
    doc_id = con.execute("SELECT id FROM docs WHERE name = ?", (name,)).fetchone()[0]
    job = describe.job_for(con, doc_id)
    prepared = [Prepared(p, i, "ready", sha1=f"{name}{p}{i}") for p, i, _ in occurrences]
    described = {f"{name}{p}{i}": vision.Description("photo", d, "", ["robot"], seconds=1.0) for p, i, d in occurrences}
    con.execute("BEGIN")
    describe.save_document(con, cfg, job, prepared, described)
    con.execute("COMMIT")
    con.close()


def setup(tmp_path, make_cfg):
    root = tmp_path / "Docs"
    root.mkdir()
    (root / "page_FR.md").write_text("# Dépileur\n\nRéglage du dépileur Zorglub.\n\n![Outil](x.png)\n", encoding="utf-8")
    doc = pymupdf.open()
    for text in ("Brochure Zorglub page un", "Brochure page deux"):
        doc.new_page().insert_text((50, 72), text)
    doc.save(root / "brochure.pdf")
    make_png(root / "p.png", (1, 2, 3), size=(300, 200))
    make_pptx(root / "deck.pptx", pictures=[root / "p.png"])
    cfg = make_cfg(root, extensions=[".md", ".pdf", ".pptx"])
    run_index(cfg, progress=quiet)
    add_passages(cfg, "page_FR.md", [(1, 1, "Main tenant un outil Zorglub en bois.")])
    add_passages(cfg, "brochure.pdf", [(2, 0, "Ligne Zorglub complète avec robots Delta.")])
    add_passages(cfg, "deck.pptx", [(3, 1, "Robot Zorglub vu de face.")])
    return cfg, root


def test_search_labels_picture_hits(tmp_path, make_cfg):
    cfg, _ = setup(tmp_path, make_cfg)
    out = DocTools(cfg).search("Zorglub", mode="keyword", limit=20)
    assert "page 1/1 — picture 1 (view_page image=1)" in out
    assert "page 2/2 — whole-page picture (view_page page=2)" in out
    assert "slide 3/3 — picture 1 (view_page page=3 image=1)" in out


def test_pictures_only_search(tmp_path, make_cfg):
    cfg, _ = setup(tmp_path, make_cfg)
    out = DocTools(cfg).search("Zorglub", mode="keyword", limit=20, pictures=True)
    hits = [l for l in out.splitlines() if l.startswith("[")]
    assert len(hits) == 3 and all("picture" in h for h in hits)
    assert "Réglage du dépileur" not in out and "Brochure Zorglub page un" not in out


def test_read_document_shows_descriptions_with_a_note(tmp_path, make_cfg):
    cfg, root = setup(tmp_path, make_cfg)
    text = DocTools(cfg).read_document(str(root / "page_FR.md"))
    assert "[Image 1 — photo] Main tenant un outil Zorglub en bois." in text
    assert "written by a vision model" in text
    plain = DocTools(cfg).read_document(str(root / "brochure.pdf"), 1, 1)
    assert "vision model" not in plain                     # page 1 has no picture passage


def test_index_status_counts_pictures(tmp_path, make_cfg):
    cfg, _ = setup(tmp_path, make_cfg)
    status = DocTools(cfg).index_status()
    assert "Pictures: 3 described (photo 3), 0 too small, 0 logos, 0 failed; model gemma4:12b" in status


def test_tools_work_on_an_index_without_picture_tables(tmp_path, make_cfg):
    cfg, root = setup(tmp_path, make_cfg)
    con = store.connect(cfg.db_path)
    for t in ("picture_uses", "picture_docs", "picture_cache"):
        con.execute(f"DROP TABLE {t}")
    con.commit()
    con.close()
    t = DocTools(cfg)
    assert "Zorglub" in t.search("Zorglub", mode="keyword")
    assert "No picture descriptions in this index yet" in t.search("Zorglub", pictures=True)
    assert "Réglage du dépileur" in t.read_document(str(root / "page_FR.md"))
    assert "Pictures: not described yet" in t.index_status()


def test_every_search_tool_has_the_pictures_option_and_the_note(tmp_path, make_cfg):
    from mcp import Client

    from dmx_docs.server import PICTURES_NOTE, build_server, instructions_for

    cfg, _ = setup(tmp_path, make_cfg)
    for profile in ("projects", "marketing", "documentation", "none"):
        cfg.profile = profile

        async def run():
            async with Client(build_server(cfg)) as client:
                return {t.name: t for t in (await client.list_tools()).tools}

        tool = asyncio.run(run())["search"]
        props = (getattr(tool, "input_schema", None) or tool.inputSchema)["properties"]
        assert "pictures" in props, profile
        assert PICTURES_NOTE in instructions_for(cfg)
    assert "pictures=true" in PICTURES_NOTE and "view_page" in PICTURES_NOTE

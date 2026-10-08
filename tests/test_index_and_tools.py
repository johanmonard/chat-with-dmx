import asyncio
import os
import time

import pytest

from dmx_docs import store
from dmx_docs.embeddings import run_embed
from dmx_docs.indexer import run_index
from dmx_docs.search import build_fts_query
from dmx_docs.tools import DocTools

quiet = lambda *a, **k: None  # noqa: E731


@pytest.fixture
def indexed(corpus_copy, make_cfg):
    cfg = make_cfg(corpus_copy)
    run_index(cfg, progress=quiet)
    return cfg, corpus_copy


def statuses(cfg):
    con = store.connect(cfg.db_path)
    rows = {r["name"]: r["status"] for r in con.execute("SELECT name, status FROM docs")}
    con.close()
    return rows


def test_index_statuses(indexed):
    cfg, root = indexed
    st = statuses(cfg)
    assert st["Spec_cellule.pdf"] == "ok"
    assert st["Rapport_MES.docx"] == "ok"
    assert st["scan.pdf"] == "no_text"
    assert st["corrupt.pdf"] == "error"
    assert "~$Rapport_MES.docx" not in st and "budget.xlsx" not in st


def test_incremental_update_and_delete(indexed):
    cfg, root = indexed
    stats = run_index(cfg, progress=quiet)
    assert stats.processed == 0 and stats.unchanged == stats.seen

    manual = root / "Manuals" / "maintenance_manual.pdf"
    from conftest import make_pdf
    make_pdf(str(manual), ["Nouvelle version : remplacer les ventouses toutes les 800 heures."])
    os.utime(manual, (time.time() + 10, time.time() + 10))
    (root / "Scans" / "scan.pdf").unlink()
    stats = run_index(cfg, progress=quiet)
    assert stats.updated == 1 and stats.deleted == 1
    tools = DocTools(cfg)
    assert "800 heures" in tools.read_document(str(manual))
    assert "scan.pdf" not in statuses(cfg)


def test_unreachable_root_keeps_documents(indexed, tmp_path):
    cfg, root = indexed
    moved = tmp_path / "moved_away"
    root.rename(moved)  # like a network drive that is not mapped
    stats = run_index(cfg, progress=quiet)
    assert stats.deleted == 0
    assert "Spec_cellule.pdf" in statuses(cfg)


def test_fts_query_building():
    assert build_fts_query('la note MN-114') == '"note" OR "MN 114"'
    assert build_fts_query('"doigts souples" préhens*') == '"doigts souples" OR "préhens"*'
    assert build_fts_query("le la de") is None


def test_keyword_search_ignores_accents_and_finds_codes(indexed):
    cfg, root = indexed
    tools = DocTools(cfg)
    out = tools.search("prehension", mode="keyword")
    assert "Spec_cellule.pdf" in out and "page 2/3" in out
    out = tools.search("MN-114", mode="keyword")
    assert "Spec_cellule.pdf" in out
    out = tools.search("AMX-220", mode="keyword")
    assert "Rapport_MES.docx" in out
    assert "No results" in tools.search("zzzzzz", mode="keyword")


def test_search_filters(indexed):
    cfg, root = indexed
    tools = DocTools(cfg)
    out = tools.search("ventouses", folder=str(root / "Manuals"), mode="keyword")
    assert "Spec_cellule" not in out
    out = tools.search("ventouses", file_type="pdf", mode="keyword")
    assert "Spec_cellule.pdf" in out
    assert "No results" in tools.search("ventouses", modified_after="2999", mode="keyword")
    with pytest.raises(ValueError):
        tools.search("x", folder="/somewhere/else")


def test_semantic_search_after_embed(indexed):
    cfg, root = indexed
    tools = DocTools(cfg)
    assert "no embeddings yet" in tools.search("Saugnäpfe Betriebsstunden")
    assert run_embed(cfg, progress=quiet) > 0
    out = tools.search("Saugnäpfe Betriebsstunden", mode="semantic")
    assert "maintenance_manual.pdf" in out
    out = tools.search("préhenseur doigts souples")
    assert "keyword+semantic" in out
    # re-indexing a changed file drops its vectors and the cache follows
    assert run_embed(cfg, progress=quiet) == 0


def test_find_files_and_list_folder(indexed):
    cfg, root = indexed
    tools = DocTools(cfg)
    out = tools.find_files("P1234")
    assert "Spec_cellule.pdf" in out and "Rapport_MES.docx" in out
    assert "Spec_cellule.pdf" in tools.find_files("spec cell")
    out = tools.list_folder()
    assert "reachable" in out
    out = tools.list_folder(str(root))
    assert "[folder] Projets" in out
    assert "budget.xlsx" in out and "not indexed (file type)" in out
    assert "~$" not in out


def test_read_and_find_in_document(indexed):
    cfg, root = indexed
    tools = DocTools(cfg)
    spec = str(root / "Projets" / "P1234_Nestle" / "Spec_cellule.pdf")
    out = tools.read_document(spec, start_page=2, end_page=2)
    assert "--- page 2 ---" in out and "MN-114" in out and "--- page 3" not in out
    cfg.max_read_chars = 300
    out = tools.read_document(spec)
    assert "Continue with start_page=" in out
    out = tools.find_in_document(spec, "prehenseur")
    assert "[page 2]" in out and "2 occurrences" in out
    with pytest.raises(ValueError):
        tools.read_document("/etc/passwd")


def test_read_new_file_live(indexed):
    cfg, root = indexed
    from conftest import make_pdf
    new = root / "Manuals" / "added_later.pdf"
    make_pdf(str(new), ["Document ajouté après l'indexation."])
    out = DocTools(cfg).read_document(str(new))
    assert "live extraction" in out and "ajouté" in out


def test_mcp_server_lists_tools(indexed):
    cfg, _ = indexed
    from dmx_docs.server import build_server
    server = build_server(cfg)
    try:
        from mcp import Client
    except ImportError:
        pytest.skip("in-process client requires mcp >= 2")

    async def run():
        async with Client(server) as client:
            names = {t.name for t in (await client.list_tools()).tools}
            res = await client.call_tool("search", {"query": "MN-114", "mode": "keyword"})
            return names, res

    names, res = asyncio.run(run())
    assert names == {"search", "find_files", "list_folder", "read_document", "find_in_document", "index_status",
                     "list_projects"}
    assert "Spec_cellule.pdf" in res.content[0].text


def crashing_extract(path, options=None):
    """Simulates a file that makes the PDF library crash the whole worker process."""
    if path.endswith("Spec_cellule.pdf"):
        os._exit(1)
    from dmx_docs.extract import extract_file
    return extract_file(path, options)


def test_worker_crash_only_marks_the_culprit(corpus_copy, make_cfg, monkeypatch):
    import dmx_docs.indexer as indexer
    monkeypatch.setattr(indexer, "extract_file", crashing_extract)
    cfg = make_cfg(corpus_copy)
    run_index(cfg, progress=quiet)
    st = statuses(cfg)
    assert st["Spec_cellule.pdf"] == "error"
    assert st["Rapport_MES.docx"] == "ok"
    assert st["maintenance_manual.pdf"] == "ok"

import pytest

from conftest import make_pdf
from dmx_docs import facets, store
from dmx_docs.indexer import run_index
from dmx_docs.tools import DocTools

quiet = lambda *a, **k: None  # noqa: E731


@pytest.fixture
def marketing(tmp_path, make_cfg):
    root = tmp_path / "22_Marketing"
    (root / "Brochures").mkdir(parents=True)
    (root / "Presentations" / "Training").mkdir(parents=True)
    (root / "Datasheets").mkdir()
    make_pdf(str(root / "Brochures" / "Paloma brochure.pdf"), ["Paloma pick and place robot for biscuits."])
    make_pdf(str(root / "Datasheets" / "Paloma datasheet.pdf"), ["Paloma technical data: 120 ppm."])
    make_pdf(str(root / "Presentations" / "Training" / "Sales training.pdf"), ["Sales training: Paloma cadence."])
    make_pdf(str(root / "Overview.pdf"), ["Company overview: Paloma and Presto."])
    cfg = make_cfg(root, world="marketing", world_title="Marketing", profile="marketing")
    run_index(cfg, progress=quiet)
    return cfg, root


def categories(cfg):
    con = store.connect(cfg.db_path)
    rows = {r[0]: r[1] for r in con.execute(
        "SELECT d.name, f.category FROM docs d JOIN doc_facets f ON f.doc_id = d.id")}
    tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    con.close()
    return rows, tables


def test_marketing_category_is_the_first_folder(marketing):
    cfg, _ = marketing
    rows, tables = categories(cfg)
    assert rows == {"Paloma brochure.pdf": "Brochures", "Paloma datasheet.pdf": "Datasheets",
                    "Sales training.pdf": "Presentations", "Overview.pdf": None}
    assert "project_machines" not in tables and "register_machines" not in tables  # projects only
    assert cfg.db_path.parent.name == "marketing"


def test_marketing_category_needs_a_root_and_a_folder_below_it():
    root = r"\\srv\Marketing"
    assert facets.compute_marketing(root + r"\Brochures\a.pdf", root) == {"category": "Brochures", "facet_source": "folder"}
    assert facets.compute_marketing(root + r"\a.pdf", root) == {}  # directly in the root
    # A document under no configured root: the whole path must not turn into a category (the server name).
    assert facets.compute_marketing(r"\\DMX-FS01\Old\a.pdf", "") == {}


def test_search_by_category_any_case_several_values(marketing):
    cfg, _ = marketing
    out = DocTools(cfg).search("Paloma", mode="keyword", category="brochures, DATASHEETS")
    assert "Paloma brochure.pdf" in out and "Paloma datasheet.pdf" in out
    assert "Sales training.pdf" not in out and "Overview.pdf" not in out
    assert "category Brochures" in out


def test_index_status_names_the_world(marketing):
    cfg, _ = marketing
    assert DocTools(cfg).index_status().startswith("World: Marketing (marketing)")


def test_profile_change_recomputes_facets(marketing):
    cfg, _ = marketing
    con = store.connect(cfg.db_path)
    assert facets.refresh(con, cfg.roots, "marketing") == 0  # up to date
    assert facets.refresh(con, cfg.roots, "none") == 4       # other rules: recomputed
    assert con.execute("SELECT count(*) FROM doc_facets WHERE category IS NOT NULL").fetchone()[0] == 0
    con.close()


def test_projects_facets_version_is_unchanged(corpus_copy, make_cfg):
    # The existing Projects index must not recompute its facets when worlds arrive.
    cfg = make_cfg(corpus_copy)
    run_index(cfg, progress=quiet)
    con = store.connect(cfg.db_path)
    assert store.get_meta(con, "facets_version") == str(facets.FACETS_VERSION)
    con.close()


import asyncio
import json


def test_marketing_server_tools(marketing):
    cfg, _ = marketing
    from mcp import Client

    from dmx_docs.server import build_server

    async def run():
        async with Client(build_server(cfg)) as client:
            listed = {t.name: t for t in (await client.list_tools()).tools}
            res = await client.call_tool("search", {"query": "Paloma", "mode": "keyword", "category": "Brochures"})
            return listed, res

    listed, res = asyncio.run(run())
    assert set(listed) == {"search", "find_files", "list_folder", "read_document", "find_in_document",
                           "index_status", "view_page", "open_document", "export_image"}
    search_tool = listed["search"]  # mcp 2 names it input_schema, mcp 1 inputSchema
    props = (getattr(search_tool, "input_schema", None) or search_tool.inputSchema)["properties"]
    assert "category" in props and "project" not in props and "machine" not in props
    assert "Paloma brochure.pdf" in res.content[0].text and "Sales training" not in res.content[0].text


def test_instructions_and_name_follow_the_profile(marketing, corpus_copy, make_cfg):
    from dmx_docs.server import INSTRUCTIONS, instructions_for
    cfg, _ = marketing
    assert cfg.server_name == "dmx-marketing"
    assert "Brochures" in instructions_for(cfg) and "0_Vente" not in instructions_for(cfg)
    assert instructions_for(make_cfg(corpus_copy)) == INSTRUCTIONS


def test_claude_desktop_snippet_names_the_world(marketing):
    from dmx_docs.web import claude_desktop_snippet
    cfg, _ = marketing
    entry = json.loads(claude_desktop_snippet(cfg))["mcpServers"]["dmx-marketing"]
    assert entry["args"][-3:] == ["--world", "marketing", "serve"]

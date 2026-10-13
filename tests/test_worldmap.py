"""World map: hand-written map text + live overview of the index (worldmap.py)."""

from dmx_docs import store, worldmap
from dmx_docs.worldmap import Overview

M = r"\\srv\Marketing"
P = r"\\srv\Projets"


def rows(paths):
    return [(p, store.path_key(p)) for p in paths]


def test_map_text_missing_or_not_configured(tmp_path):
    missing = tmp_path / "maps" / "marketing.md"
    assert worldmap.load_map_text(str(missing)) == (
        f"No map file for this world ({missing}): only the live overview below.")
    assert worldmap.load_map_text(None) == "No map file configured for this world: only the live overview below."


def test_map_with_bom_and_bad_encoding(tmp_path):
    good = tmp_path / "a.md"
    good.write_bytes("\ufeff# Marketing - map\nTrainings: Presentations\\Training Sales\n".encode("utf-8"))
    assert worldmap.load_map_text(str(good)).startswith("# Marketing - map")
    bad = tmp_path / "b.md"
    bad.write_bytes("Clôture".encode("cp1252"))  # saved as ANSI by an old editor
    assert worldmap.load_map_text(str(bad)) == f"No map file for this world ({bad}): only the live overview below."


def test_folder_tree_counts_and_folds_small_folders():
    paths = ([rf"{M}\Brochures\Marketing\a{i}.pdf" for i in range(6)]
             + [rf"{M}\Brochures\Old\b{i}.pdf" for i in range(2)]
             + [rf"{M}\Videos\v.jpg", rf"{M}\top.pdf"])
    assert worldmap.folder_tree(rows(paths), [M]) == [
        rf"{M} (10 docs)",
        "  - Brochures (8)",
        "    - Marketing (6)",
        "    - + 1 smaller folder (2 docs)",
        "  - + 1 smaller folder (1 doc)",
    ]


def test_folder_tree_stops_at_depth():
    paths = [rf"{M}\A\B\C\D\x{i}.pdf" for i in range(5)]
    assert worldmap.folder_tree(rows(paths), [M]) == [
        rf"{M} (5 docs)", "  - A (5)", "    - B (5)", "      - C (5)"]


def test_tree_ignores_paths_outside_roots():
    paths = [rf"{M}\Brochures\a{i}.pdf" for i in range(5)] + [r"\\other\share\Brochures\x.pdf"]
    assert worldmap.folder_tree(rows(paths), [M]) == [rf"{M} (5 docs)", "  - Brochures (5)"]


def test_template_folders_aggregate_across_projects():
    r = ([(rf"{P}\THOR\0_Vente\00_Offres\o{i}.pdf", "THOR") for i in range(5)]
         + [(rf"{P}\LEON\0_Vente\00_Offres\o{i}.pdf", "LEON") for i in range(5)]
         + [(rf"{P}\LEON\3_Mecanique\37_Materiel_Tiers\m{i}.pdf", "LEON") for i in range(6)]
         + [(rf"{P}\2_Hors_Garantie\ANGE\Clôture\c.pdf", "ANGE"),
            (rf"{P}\THOR\notes.pdf", "THOR"),
            (rf"{P}\X\3_Mecanique\37_Materiel_Tiers\n.pdf", None)])  # project facet unknown
    data = [(p, store.path_key(p), proj, None) for p, proj in r]
    assert worldmap.template_folders(data, [P]) == [
        "- Vente: 2 projects, 10 docs",
        "  - 0_Vente\\00_Offres: 2 projects, 10 docs",
        "- Mecanique: 2 projects, 7 docs",
        "  - 3_Mecanique\\37_Materiel_Tiers: 2 projects, 7 docs",
        "- Cloture: 1 project, 1 doc",
        "  - + 1 other folder (1 doc)",
        "- outside the template sections: 1 doc in 1 project",
    ]


def test_template_folders_cap_rows_per_section():
    r = [(rf"{P}\THOR\0_Vente\{k:02d}_Sub\o{i}.pdf", "THOR") for k in range(30) for i in range(5)]
    data = [(p, store.path_key(p), proj, None) for p, proj in r]
    lines = worldmap.template_folders(data, [P], max_rows=25)
    assert lines[0] == "- Vente: 1 project, 150 docs"
    assert len(lines) == 1 + 25 + 1
    assert lines[-1] == "  - + 5 other folders (25 docs)"


def test_compose_puts_map_first_then_overview_and_footer():
    ov = Overview(head=["## Live overview (from the index)", "Marketing (marketing): 10 documents"],
                  tree_title="Folders (documents):", tree=["x (10 docs)", "  - Brochures (8)"])
    out = worldmap.compose("# Marketing - map\nhello", ov)
    assert out.index("# Marketing - map") < out.index("## Live overview") < out.index("  - Brochures (8)")
    assert out.rstrip().endswith(worldmap.FOOTER)


def test_compose_cuts_tree_not_map():
    text = "# Map\n" + "m" * 5000
    ov = Overview(head=["## Live overview (from the index)"], tree_title="Folders (documents):",
                  tree=[f"  - folder {i} ({i})" for i in range(2000)])
    out = worldmap.compose(text, ov, max_chars=8000)
    assert text in out
    assert len(out) <= 8000
    assert "... tree cut here (" in out and "more lines): browse with list_folder." in out
    assert out.rstrip().endswith(worldmap.FOOTER)


# --- DocTools.world_map on real (small) indexes -------------------------------------------------

import pytest  # noqa: E402

from conftest import make_pdf  # noqa: E402
from dmx_docs.indexer import run_index  # noqa: E402
from dmx_docs.tools import DocTools  # noqa: E402

quiet = lambda *a, **k: None  # noqa: E731


@pytest.fixture
def marketing(tmp_path, make_cfg):
    root = tmp_path / "22_Marketing"
    (root / "Presentations" / "Training Sales").mkdir(parents=True)
    for i in range(5):
        make_pdf(str(root / "Presentations" / "Training Sales" / f"t{i}.pdf"), [f"Training {i}: coflow"])
    make_pdf(str(root / "Datasheets.pdf"), ["Paloma technical data"])
    map_file = tmp_path / "maps" / "marketing.md"
    map_file.parent.mkdir()
    map_file.write_text("# Marketing - map\nSales trainings: Presentations\\Training Sales\n", encoding="utf-8")
    cfg = make_cfg(root, world="marketing", world_title="Marketing", profile="marketing", map_path=str(map_file))
    run_index(cfg, progress=quiet)
    return cfg, root, map_file


def test_world_map_marketing(marketing):
    cfg, root, _ = marketing
    out = DocTools(cfg).world_map()
    assert out.startswith("# Marketing - map")
    assert "Marketing (marketing): 6 documents" in out
    assert "By file type: .pdf 6" in out
    assert f"{root} (6 docs)" in out and "    - Training Sales (5)" in out


def test_world_map_rereads_the_map_but_caches_the_overview(marketing):
    cfg, _, map_file = marketing
    tools = DocTools(cfg)
    tools.world_map()
    map_file.write_text("# Marketing - map v2\n", encoding="utf-8")
    assert tools.world_map().startswith("# Marketing - map v2")
    assert tools._overview is not None


def test_world_map_without_map_file(marketing):
    cfg, _, map_file = marketing
    map_file.unlink()
    assert DocTools(cfg).world_map().startswith("No map file for this world (")


def test_world_map_projects(corpus_copy, make_cfg):
    cfg = make_cfg(corpus_copy)  # profile projects, no world, no map configured
    run_index(cfg, progress=quiet)
    out = DocTools(cfg).world_map()
    assert out.startswith("No map file configured for this world")
    assert "Projects: " in out and "By section: " in out and "By document type: " in out
    assert "Template folders across projects" in out
    assert "outside the template sections" in out  # the test corpus has no template folders


def test_world_map_on_empty_index(tmp_path, make_cfg):
    root = tmp_path / "empty"
    root.mkdir()
    cfg = make_cfg(root, world="documentation", world_title="Documentation", profile="documentation")
    run_index(cfg, progress=quiet)
    out = DocTools(cfg).world_map()
    assert "Documentation (documentation): 0 documents" in out


# --- MCP tool, instructions, CLI ---------------------------------------------------------------

import asyncio  # noqa: E402

from dmx_docs.config import Config  # noqa: E402


@pytest.mark.parametrize("profile", ["projects", "marketing", "documentation", "none"])
def test_world_map_tool_and_instructions_in_every_profile(tmp_path, profile):
    from mcp import Client

    from dmx_docs.server import WORLD_MAP_LINE, build_server, instructions_for

    cfg = Config(roots=[str(tmp_path)], data_dir=tmp_path / "data", world=profile.replace("none", "other"),
                 world_title="X", profile=profile, embedding_model="hash:256")

    async def names():
        async with Client(build_server(cfg)) as client:
            return {t.name for t in (await client.list_tools()).tools}

    assert "world_map" in asyncio.run(names())
    text = instructions_for(cfg)
    assert WORLD_MAP_LINE in text
    assert text.index(WORLD_MAP_LINE) < 1200  # near the top: some clients truncate instructions


def test_cli_map_prints_the_world_map(marketing, tmp_path, capsys):
    from dmx_docs import cli

    cfg, root, map_file = marketing
    conf = tmp_path / "config.toml"
    conf.write_text(
        f"[index]\ndata_dir = '{cfg.data_dir.as_posix()}'\n\n"
        f"[worlds.marketing]\nprofile = 'marketing'\nroots = ['{root.as_posix()}']\n"
        "[embeddings]\nmodel = 'hash:256'\nquery_prefix = ''\npassage_prefix = ''\n",
        encoding="utf-8")
    cli.main(["--config", str(conf), "--world", "marketing", "map"])
    out = capsys.readouterr().out
    assert out.startswith("# Marketing - map") and "## Live overview" in out

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

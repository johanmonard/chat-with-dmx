import pytest

from conftest import make_pdf
from dmx_docs import machines, store
from dmx_docs.indexer import run_index
from dmx_docs.tools import DocTools

quiet = lambda *a, **k: None  # noqa: E731


@pytest.mark.parametrize("text, expected", [
    ("Paloma 10R - 120005849-03 -- THOR", {("Paloma", "Paloma 10R")}),
    ("offre pour une PALOMA D2 4R", {("Paloma", "Paloma 4R")}),
    ("ligne HECTOR PM 7R et Presto 2R", {("Hector", "Hector 7R"), ("Presto", "Presto 2R")}),
    ("palomaSQ8R", {("Paloma", "Paloma 8R SQ")}),
    ("Paloma 4SQ = Paloma 4 robots", {("Paloma", "Paloma 4R SQ"), ("Paloma", "")}),
    ("Astor 5 2R", {("Astor", "Astor 2R")}),
    ("description du robot Paloma D3", {("Paloma", "")}),
    ("Palomares et prestation", set()),  # words that only start like a machine name
])
def test_models_in(text, expected):
    assert machines.models_in(text) == expected


def test_machine_facet_filter_and_listing(tmp_path, make_cfg):
    root = tmp_path / "RMA"
    docs = {
        ("THOR", r"2_Electrique\23_Schemas", "Paloma 7R - schema.pdf"): "Schéma électrique cellule 1.",
        ("THOR", r"0_Vente\00_Offres", "Offre THOR.pdf"): "Offre pour deux Paloma 7R avec outils squeeze and spread.",
        ("THOR", r"0_Vente\03_Cahier_des_charges", "CDC.pdf"): "Cahier des charges : 2x Paloma 7R. Voir aussi Presto 2R du projet X.",
        ("SPACE", r"0_Vente\00_Offres", "Offre SPACE.pdf"): "Offre Presto 2R avec squeeze and spread.",
        ("SPACE", r"5_Gestion\56_Acceptation_machine", "FAT SPACE.pdf"): "FAT de la Presto 2R.",
    }
    for (proj, sub, name), text in docs.items():
        d = root / proj / sub
        d.mkdir(parents=True, exist_ok=True)
        make_pdf(str(d / name), [text])
    cfg = make_cfg(root)
    run_index(cfg, progress=quiet)
    tools = DocTools(cfg)

    listing = tools.list_projects(machine="Paloma")
    assert "THOR" in listing and "SPACE" not in listing and "Paloma 7R (3 docs)" in listing
    con = store.connect(cfg.db_path)
    assert machines.describe(con, "THOR") == "Paloma 7R (3 docs)"  # the single Presto reference is not kept
    con.close()
    out = tools.search("squeeze and spread", machine="Paloma", mode="keyword")
    assert "Offre THOR" in out and "SPACE" not in out
    assert "[Paloma 7R (3 docs)]" in out
    presto = tools.search("squeeze and spread", machine="Presto 2R", mode="keyword")
    assert "SPACE" in presto and "THOR" not in presto

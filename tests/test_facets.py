import os

import pytest

from conftest import make_pdf
from dmx_docs import facets, store
from dmx_docs.indexer import run_index
from dmx_docs.tools import DocTools

R = r"\\srv\Daten$\RMA_PROJETS"
quiet = lambda *a, **k: None  # noqa: E731


def f(rel, root=R):
    return facets.compute(os.path.join(root, rel), root)


@pytest.mark.parametrize("rel, project, collection, section, doc_type, source", [
    # current template
    (r"THOR\5_Gestion\56_Acceptation_machine\FAT DEMAUREX 221024.pdf", "THOR", None, "Gestion", "fat", "name"),
    (r"THOR\5_Gestion\56_Acceptation_machine\Protocole.pdf", "THOR", None, "Gestion", "reception", "folder"),
    (r"YAKUMA\0_Vente\03_Cahier_des_charges\CDC client.pdf", "YAKUMA", None, "Vente", "cahier_des_charges", "folder"),
    (r"ANGE\8_Documentation\Documentation_ANGE_FR_V00.pdf", "ANGE", None, "Documentation", "manuel", "section"),
    (r"ANGE\2_Electrique\23_Schemas\Paloma 4R.pdf", "ANGE", None, "Electrique", "schema_electrique", "folder"),
    # older named template, inside a collection
    (r"2_Hors_Garantie\DAHU\Gestion\SAT\130311_Dahu review on site.doc", "DAHU", "2_Hors_Garantie", "Gestion", "sat", "folder"),
    (r"2_Hors_Garantie\ACTE\Cahier des charges\Exigences.docx", "ACTE", "2_Hors_Garantie", "Vente", "cahier_des_charges", "folder"),
    (r"2_Hors_Garantie\IRIS 6\Gestion\Shipping Doc\Documents.pdf", "IRIS 6", "2_Hors_Garantie", "Gestion", "transport", "folder"),
    (r"2_Hors_Garantie\BEV_2\Clôture\Bilan.pdf", "BEV_2", "2_Hors_Garantie", "Cloture", None, None),
    # ad hoc project inside a collection: project still found, type from the file name
    (r"2_Hors_Garantie\AUTONOX\Dox import Mars 2020\Offre AUTONOX.pdf", "AUTONOX", "2_Hors_Garantie", None, "offre", "name"),
])
def test_compute(rel, project, collection, section, doc_type, source):
    got = f(rel)
    assert (got["project"], got["collection"], got["section"], got["doc_type"], got["facet_source"]) == \
        (project, collection, section, doc_type, source)


def test_root_that_is_a_project_folder():
    got = facets.compute(R + r"\THOR\5_Gestion\x.pdf", R + r"\THOR")
    assert got["project"] == "THOR" and got["section"] == "Gestion"


def test_facet_filters_and_project_list(tmp_path, make_cfg):
    root = tmp_path / "RMA"
    for proj, sub, name, text in [
        ("THOR", r"5_Gestion\56_Acceptation_machine", "FAT THOR.pdf", "Cadence mesurée en FAT : 120 produits par minute."),
        ("THOR", r"0_Vente\00_Offres", "Offre THOR.pdf", "Cadence offerte : 140 produits par minute."),
        ("ANGE", r"5_Gestion\56_Acceptation_machine", "FAT ANGE.pdf", "Cadence mesurée en FAT : 90 produits par minute."),
    ]:
        d = root / proj / sub
        d.mkdir(parents=True, exist_ok=True)
        make_pdf(str(d / name), [text])
    cfg = make_cfg(root)
    run_index(cfg, progress=quiet)
    tools = DocTools(cfg)

    out = tools.search("cadence produits par minute", project="thor", doc_type="fat", mode="keyword")
    assert "FAT THOR.pdf" in out and "Offre THOR" not in out and "ANGE" not in out
    assert "project THOR · section Gestion · type fat" in out
    assert "filtered by project=thor, doc_type=fat" in out
    both = tools.search("cadence", doc_type="fat,offre", mode="keyword")
    assert "FAT THOR" in both and "Offre THOR" in both and "FAT ANGE" in both

    listing = tools.list_projects()
    assert "- ANGE | current | 1 docs" in listing and "- THOR | current | 2 docs" in listing


def test_facets_follow_rule_changes(tmp_path, make_cfg, monkeypatch):
    root = tmp_path / "RMA"
    (root / "THOR" / "9_SAV").mkdir(parents=True)
    make_pdf(str(root / "THOR" / "9_SAV" / "Intervention.pdf"), ["Remplacement du moteur."])
    cfg = make_cfg(root)
    run_index(cfg, progress=quiet)
    con = store.connect(cfg.db_path)
    assert con.execute("SELECT doc_type FROM doc_facets").fetchone()[0] == "sav"
    monkeypatch.setattr(facets, "FACETS_VERSION", facets.FACETS_VERSION + 1)
    monkeypatch.setitem(facets.SECTION_DEFAULT, "SAV", "service")
    assert facets.refresh(con, cfg.roots) == 1
    assert con.execute("SELECT doc_type FROM doc_facets").fetchone()[0] == "service"
    con.close()

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

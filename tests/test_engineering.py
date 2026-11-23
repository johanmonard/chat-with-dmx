import pytest

from conftest import make_pdf
from dmx_docs import store
from dmx_docs.config import load_config
from dmx_docs.indexer import run_index
from dmx_docs.tools import DocTools

quiet = lambda *a, **k: None  # noqa: E731


def write_config(tmp_path, folder, extra=""):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "[index]\ndata_dir = 'data'\n[worlds.engineering]\ntitle = \"Engineering\"\n"
        f"profile = \"engineering\"\nextensions = ['.pdf']\nroots = ['{folder}']\n{extra}\n",
        encoding="utf-8")
    return cfg


@pytest.fixture
def eng(tmp_path):
    mech = tmp_path / "22_Mec"
    (mech / "224 - Pneumatique").mkdir(parents=True)
    (mech / "229 - Archives" / "Old").mkdir(parents=True)
    make_pdf(str(mech / "224 - Pneumatique" / "Pompes.pdf"), ["Pompe à vide: 610 l/min."])
    make_pdf(str(mech / "229 - Archives" / "Old" / "Ancien.pdf"), ["Ancienne pompe à vide."])
    make_pdf(str(mech / "Racine.pdf"), ["Pompe à vide, document à la racine."])
    cfg = load_config(write_config(tmp_path, mech), world="engineering")
    run_index(cfg, progress=quiet)
    return cfg, mech


def facet_rows(cfg):
    con = store.connect(cfg.db_path)
    rows = {r[0]: (r[1], r[2], r[3]) for r in con.execute(
        "SELECT d.name, f.category, f.section, f.facet_source FROM docs d JOIN doc_facets f ON f.doc_id = d.id")}
    con.close()
    return rows


def test_sections_come_from_the_first_folder_below_the_root(eng):
    cfg, mech = eng
    assert cfg.roots == [str(mech)]
    assert facet_rows(cfg) == {   # no disciplines.toml in this world: every document is Unknown
        "Pompes.pdf": ("Unknown", "224 - Pneumatique", "folder"),
        "Ancien.pdf": ("Unknown", "229 - Archives", "folder"),
        "Racine.pdf": ("Unknown", None, "folder")}


def test_search_filters_by_section_and_shows_the_discipline_line(eng):
    cfg, _ = eng
    out = DocTools(cfg).search("pompe", mode="keyword", section="224 - pneumatique")
    assert "Pompes.pdf" in out and "Ancien.pdf" not in out and "Racine.pdf" not in out
    assert "discipline Unknown · section 224 - Pneumatique" in out
    assert "discipline Unknown · section (none)" in DocTools(cfg).search("racine", mode="keyword")


def test_a_groups_table_is_refused(tmp_path):
    cfg = write_config(tmp_path, tmp_path, extra="[worlds.engineering.groups]\nMechanical = []\n")
    with pytest.raises(ValueError, match="groups are no longer used"):
        load_config(cfg, world="engineering")


def test_index_status_and_world_map(eng):
    cfg, _ = eng
    assert DocTools(cfg).index_status().startswith("World: Engineering (engineering)")
    overview = DocTools(cfg).world_map()
    assert "By section:" in overview and "By discipline:" in overview


def test_facets_version_of_the_profile(eng):
    cfg, _ = eng
    con = store.connect(cfg.db_path)
    assert store.get_meta(con, "facets_version") == "engineering:2"
    con.close()

import pytest

from conftest import make_pdf
from dmx_docs import facets, sources, store
from dmx_docs.config import load_config
from dmx_docs.indexer import run_index
from dmx_docs.tools import DocTools

quiet = lambda *a, **k: None  # noqa: E731


def write_config(tmp_path, groups_toml, extra=""):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "[index]\ndata_dir = 'data'\n[worlds.engineering]\ntitle = \"Engineering\"\n"
        f"profile = \"engineering\"\nextensions = ['.pdf']\n{extra}\n[worlds.engineering.groups]\n{groups_toml}\n",
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
    cfg = load_config(write_config(tmp_path, f"Mechanical = ['{mech}']\nElectrical = []\nSoftware = []"),
                      world="engineering")
    run_index(cfg, progress=quiet)
    return cfg, mech


def facet_rows(cfg):
    con = store.connect(cfg.db_path)
    rows = {r[0]: (r[1], r[2], r[3]) for r in con.execute(
        "SELECT d.name, f.category, f.section, f.facet_source FROM docs d JOIN doc_facets f ON f.doc_id = d.id")}
    con.close()
    return rows


def test_groups_come_from_the_configuration(eng):
    cfg, mech = eng
    assert cfg.groups == {"Mechanical": [str(mech)], "Electrical": [], "Software": []}
    assert cfg.roots == [str(mech)]
    assert facet_rows(cfg) == {
        "Pompes.pdf": ("Mechanical", "224 - Pneumatique", "config"),
        "Ancien.pdf": ("Mechanical", "229 - Archives", "config"),
        "Racine.pdf": ("Mechanical", None, "config")}


def test_search_filters_by_group_and_section(eng):
    cfg, _ = eng
    out = DocTools(cfg).search("pompe", mode="keyword", category="mechanical", section="224 - pneumatique")
    assert "Pompes.pdf" in out and "Ancien.pdf" not in out and "Racine.pdf" not in out
    assert "group Mechanical · section 224 - Pneumatique" in out
    assert "group Mechanical · section (none)" in DocTools(cfg).search("racine", mode="keyword")


@pytest.mark.parametrize("groups", [
    "Mechanical = ['{a}', '{a}']",                    # twice in one group
    "Mechanical = ['{a}']\nElectrical = ['{a}']",     # in two groups
    "Mechanical = ['{a}']\nElectrical = ['{a}\\\\sub']",  # one inside the other
])
def test_bad_groups_are_refused(tmp_path, groups):
    a = tmp_path / "A"
    (a / "sub").mkdir(parents=True)
    with pytest.raises(ValueError, match="group"):
        load_config(write_config(tmp_path, groups.format(a=a)), world="engineering")


def test_roots_key_is_refused_for_this_profile(tmp_path):
    with pytest.raises(ValueError, match="groups"):
        load_config(write_config(tmp_path, "Mechanical = []", extra=f"roots = ['{tmp_path}']"), world="engineering")


def test_a_new_group_folder_becomes_a_root(eng, tmp_path):
    cfg, mech = eng
    soft = tmp_path / "23_Soft"
    soft.mkdir()
    make_pdf(str(soft / "Automate.pdf"), ["Programme automate."])
    cfg2 = load_config(write_config(tmp_path, f"Mechanical = ['{mech}']\nSoftware = ['{soft}']"), world="engineering")
    run_index(cfg2, progress=quiet)
    rows = facet_rows(cfg2)
    assert rows["Automate.pdf"] == ("Software", None, "config")
    assert rows["Pompes.pdf"][0] == "Mechanical"       # recomputed with the new table, still right


def test_a_root_in_no_group_stops_the_run(eng, tmp_path):
    cfg, mech = eng
    stray = tmp_path / "Stray"
    stray.mkdir()
    con = store.connect(cfg.db_path)
    sources.add_root(con, str(stray))
    con.commit()
    con.close()
    with pytest.raises(sources.GroupError, match="Stray"):
        run_index(cfg, progress=quiet)


def test_group_folder_spelling_does_not_make_a_second_root(eng, tmp_path):   # Review Focus 1
    cfg, mech = eng
    other = str(mech).upper() + "\\"
    cfg2 = load_config(write_config(tmp_path, f"Mechanical = ['{other}']"), world="engineering")
    run_index(cfg2, progress=quiet)
    con = store.connect(cfg2.db_path)
    assert con.execute("SELECT count(*) FROM roots").fetchone()[0] == 1
    con.close()
    assert facet_rows(cfg2)["Pompes.pdf"][0] == "Mechanical"


def test_changed_groups_table_recomputes_facets(eng):
    cfg, mech = eng
    con = store.connect(cfg.db_path)
    assert facets.refresh(con, cfg.roots, "engineering", cfg.groups) == 0
    assert facets.refresh(con, cfg.roots, "engineering", {"Meca": [str(mech)]}) == 3
    assert con.execute("SELECT DISTINCT category FROM doc_facets").fetchall()[0][0] == "Meca"
    con.close()


def test_index_status_and_world_map(eng):
    cfg, _ = eng
    assert DocTools(cfg).index_status().startswith("World: Engineering (engineering)")
    assert "By group: Mechanical 3" in DocTools(cfg).world_map()

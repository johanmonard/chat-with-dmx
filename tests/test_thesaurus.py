from conftest import make_pdf
from dmx_docs.indexer import run_index
from dmx_docs.search import build_fts_query
from dmx_docs.thesaurus import Thesaurus
from dmx_docs.tools import DocTools

quiet = lambda *a, **k: None  # noqa: E731

TOML = """
[prehenseur]
fr = ["préhenseur", "pince"]
en = ["gripper", "end effector"]
de = ["Greifer"]

[arret_urgence]
fr = ["arrêt d'urgence", "AU"]
en = ["emergency stop"]
de = ["Not-Halt"]

[faux_ami]
fr = ["casse"]
en = ["breakage"]
status = "no"
"""


def test_expansion_of_words_and_phrases(tmp_path):
    p = tmp_path / "thesaurus.toml"
    p.write_text(TOML, encoding="utf-8")
    th = Thesaurus.load(p)
    expanded = []
    q = build_fts_query("réglage du Préhenseur et arrêt d'urgence casse", thesaurus=th, expanded=expanded)
    assert '("Préhenseur" OR "pince" OR "gripper" OR "end effector" OR "Greifer")' in q
    assert '("arrêt d urgence" OR "emergency stop" OR "Not Halt")' in q  # AU is never added
    assert '("AU" OR "arrêt d urgence"' in build_fts_query("AU", thesaurus=th)  # but triggers when typed
    assert '"casse"' in q and "breakage" not in q  # status "no" is ignored
    assert [t for t, _ in expanded] == ["Préhenseur", "arrêt d urgence"]
    assert build_fts_query("préhenseur", thesaurus=None) == '"préhenseur"'


def test_search_with_expansion(tmp_path, make_cfg):
    root = tmp_path / "RMA"
    (root / "P").mkdir(parents=True)
    make_pdf(str(root / "P" / "manual.pdf"), ["Replace the gripper suction cups every 500 hours."])
    (tmp_path / "thesaurus.toml").write_text(TOML, encoding="utf-8")
    cfg = make_cfg(root, thesaurus_path=str(tmp_path / "thesaurus.toml"))
    run_index(cfg, progress=quiet)
    tools = DocTools(cfg)
    assert "No results" in tools.search("préhenseur", mode="keyword")  # off by default
    out = tools.search("préhenseur", mode="keyword", expand=True)
    assert "manual.pdf" in out and "widened with the thesaurus: préhenseur → pince, gripper" in out

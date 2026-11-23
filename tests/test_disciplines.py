import pytest

from dmx_docs import disciplines
from dmx_docs.disciplines import RulesError, classify, load_rules, normalize, score_text

RULES = """
[[folder]]
pattern = "*/doc_tech/soft/*"
discipline = "Software"

[[folder]]
pattern = "*/doc_tech/*"
discipline = "Mechanical"

[words.Mechanical]
strong = ["préhenseur", "pneumatique"]
weak = ["montage", "vis"]

[words.Electrical]
strong = ["schéma électrique", "variateur"]
weak = ["relais"]

[words.Software]
strong = ["twincat"]
weak = ["version"]
"""


def make_rules(tmp_path, text=RULES):
    p = tmp_path / "disciplines.toml"
    p.write_text(text, encoding="utf-8")
    return load_rules(str(p))


def test_normalize():
    assert normalize("Préhenseur  PNEUMATIQUE-2") == "prehenseur pneumatique 2"


def test_a_word_matches_whole_words_only(tmp_path):                      # Review Focus 4
    rules = make_rules(tmp_path)
    assert score_text(rules, "visage", "")[0]["Mechanical"] == 0
    assert score_text(rules, "vis M8", "")[0]["Mechanical"] == 3         # weak 1 x head 3 x count 1


def test_accents_and_case_do_not_matter(tmp_path):                       # Review Focus 4
    rules = make_rules(tmp_path)
    a, b, c = (score_text(rules, t, "")[0] for t in ("PREHENSEUR", "Préhenseur", "prehenseur"))
    assert a == b == c and a["Mechanical"] == 9


def test_head_counts_three_times_the_body_and_counts_are_capped(tmp_path):
    rules = make_rules(tmp_path)
    assert score_text(rules, "pneumatique", "")[0]["Mechanical"] == 9
    assert score_text(rules, "", "pneumatique")[0]["Mechanical"] == 3
    assert score_text(rules, "", "pneumatique " * 20)[0]["Mechanical"] == 15


def test_phrase_entry(tmp_path):
    rules = make_rules(tmp_path)
    assert score_text(rules, "", "le schéma électrique du poste")[0]["Electrical"] == 3
    assert score_text(rules, "", "schéma de principe électrique")[0]["Electrical"] == 0


def test_decision_unknown_below_min_score(tmp_path):
    rules = make_rules(tmp_path)
    d = classify(rules, "c:/x/a.pdf", "a.pdf", None, "un peu de relais")
    assert (d.label, d.source) == ("Unknown", "none")


def test_decision_single_discipline_above_top_share(tmp_path):
    rules = make_rules(tmp_path)
    d = classify(rules, "c:/x/a.pdf", "a.pdf", None, "préhenseur pneumatique montage")
    assert (d.label, d.source) == ("Mechanical", "words")
    assert d.scores == {"Mechanical": 7, "Electrical": 0, "Software": 0}
    assert set(d.top_terms[:2]) == {"prehenseur", "pneumatique"}


def test_decision_mixed_when_two_disciplines_are_clear(tmp_path):
    rules = make_rules(tmp_path)
    d = classify(rules, "c:/x/a.pdf", "a.pdf", None, "préhenseur pneumatique variateur schéma électrique")
    assert d.scores["Mechanical"] == 6 and d.scores["Electrical"] == 6
    assert d.label == "Mixed"


def test_decision_top_discipline_when_no_second_is_clear(tmp_path):
    rules = make_rules(tmp_path)
    d = classify(rules, "c:/x/a.pdf", "a.pdf", None,
                 "préhenseur pneumatique préhenseur variateur twincat montage montage")
    # Mechanical: préhenseur x2 -> 6, pneumatique -> 3, montage x2 -> 2 = 11; variateur 3; twincat 3
    assert d.scores == {"Mechanical": 11, "Electrical": 3, "Software": 3}
    assert d.label == "Mechanical"


def test_a_folder_rule_wins_over_words(tmp_path):
    rules = make_rules(tmp_path)
    body = "préhenseur pneumatique " * 5
    d = classify(rules, "C:/Share/Doc_Tech/Soft/a.pdf", "Doc_Tech/Soft/a.pdf", None, body)
    assert (d.label, d.source) == ("Software", "rule")
    d2 = classify(rules, "C:/Share/Doc_Tech/Other/a.pdf", "Doc_Tech/Other/a.pdf", None, "twincat")
    assert (d2.label, d2.source) == ("Mechanical", "rule")           # first matching rule wins, in file order


def test_missing_file_gives_empty_rules():                              # Review Focus 1
    for path in (None, "does/not/exist.toml"):
        rules = load_rules(path)
        assert rules.fingerprint == "none"
        assert classify(rules, "c:/x/a.pdf", "a.pdf", "Préhenseur", "pneumatique").label == "Unknown"


@pytest.mark.parametrize("text", [
    "this is = not toml [",
    '[[folder]]\npattern = "*/a/*"\ndiscipline = "Chemistry"\n',
    '[words.Chemistry]\nstrong = ["acide"]\n',
    "[scoring]\nmixed_share = 1.5\n",
])
def test_bad_rules_raise_rules_error(tmp_path, text):                   # Review Focus 1
    with pytest.raises(RulesError, match="disciplines.toml"):
        make_rules(tmp_path, text)


def test_the_example_rules_file_loads_and_classifies():
    from pathlib import Path

    rules = load_rules(str(Path(__file__).parent.parent / "disciplines.example.toml"))
    assert rules.folders == []                                      # the folder rules are the user's to write
    assert all(len(rules.words[d]["strong"]) >= 30 for d in disciplines.DISCIPLINES)
    samples = {
        "Mechanical": "Réglage du préhenseur à ventouses et du vérin pneumatique",
        "Electrical": "Schéma électrique de l'armoire: variateur, câblage, relais de sécurité",
        "Software": "Version du firmware TwinCAT et paramètres de la recette Gemini",
    }
    for label, text in samples.items():
        assert classify(rules, "c:/x/a.pdf", "a.pdf", None, text).label == label, text


def test_fingerprint_changes_with_the_file(tmp_path):
    a = make_rules(tmp_path)
    b = make_rules(tmp_path, RULES + "\n# changed\n")
    assert a.fingerprint != b.fingerprint and len(a.fingerprint) == 12
    assert disciplines.LABELS == ("Mechanical", "Electrical", "Software", "Mixed", "Unknown")

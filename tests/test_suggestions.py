import csv

from conftest import make_pdf
from dmx_docs import store
from dmx_docs.config import load_config
from dmx_docs.indexer import run_index
from dmx_docs.tools import DocTools

RULES = """
[[folder]]
pattern = "*/eng/mm/*"
discipline = "Mechanical"
[[folder]]
pattern = "*/eng/ee/*"
discipline = "Electrical"
[[folder]]
pattern = "*/eng/ss/*"
discipline = "Software"

[words.Mechanical]
strong = ["pneumatique", "prehenseur"]
[words.Electrical]
strong = ["variateur"]
[words.Software]
strong = ["twincat"]
"""


def add(root, folder, name, text):
    (root / folder).mkdir(parents=True, exist_ok=True)
    make_pdf(str(root / folder / name), [text])


def make_world(tmp_path, rules=RULES):
    root = tmp_path / "Eng"
    for f, n in (("f1", 2), ("f2", 2), ("f3", 2)):                      # Mechanical: 6 documents in 3 folders
        for i in range(n):
            lower = " lowcount" if f in ("f1", "f2") else ""
            add(root, f"mm/{f}", f"d{i}.pdf", f"pneumatique partage avec sertissage rivetage{lower}")
    for i in range(6):                                                   # Mechanical: 6 documents in ONE folder
        add(root, "mm/f4", f"d{i}.pdf", "pneumatique partage avec hapax")
    for f in ("f1", "f2", "f3"):
        for i in range(2):
            add(root, f"ee/{f}", f"d{i}.pdf", "variateur partage avec bornier")
            add(root, f"ss/{f}", f"d{i}.pdf", "twincat avec compilation")
    config = tmp_path / "config.toml"
    config.write_text("[index]\ndata_dir = 'data'\n[worlds.engineering]\ntitle = \"Engineering\"\n"
                      f"profile = \"engineering\"\nextensions = ['.pdf']\nroots = ['{root}']\n", encoding="utf-8")
    rules_file = tmp_path / "disciplines.toml"
    rules_file.write_text(rules, encoding="utf-8")
    return load_config(config, world="engineering"), root, rules_file


def rows_of(cfg):
    raw = (cfg.world_dir / "keyword-suggestions.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    lines = list(csv.reader(raw.decode("utf-8-sig").splitlines(), delimiter=";"))
    assert lines[0] == ["discipline", "word", "docs_in", "docs_out", "lift", "example_1", "example_2", "example_3"]
    return lines[1:]


def words(cfg):
    return {(r[0], r[1]) for r in rows_of(cfg)}


def test_a_discriminating_word_is_suggested_with_its_stats(tmp_path):
    cfg, _, _ = make_world(tmp_path)
    run_index(cfg, progress=lambda *a: None)
    row = next(r for r in rows_of(cfg) if r[1] == "sertissage")
    assert row[0] == "Mechanical" and row[2] == "6" and row[3] == "0" and float(row[4]) >= 3
    assert all(row[5:8]) and ("Electrical", "bornier") in words(cfg) and ("Software", "compilation") in words(cfg)


def test_words_in_the_lists_or_stopwords_are_never_suggested(tmp_path):     # Review Focus 5
    cfg, _, _ = make_world(tmp_path)
    run_index(cfg, progress=lambda *a: None)
    found = {w for _, w in words(cfg)}
    assert "pneumatique" not in found and "variateur" not in found and "avec" not in found
    assert not any("pneumatique" in w or "variateur" in w for w in found)  # no phrase holding a listed word


def test_a_word_seen_in_one_folder_only_is_not_suggested(tmp_path):         # Review Focus 5
    cfg, _, _ = make_world(tmp_path)
    run_index(cfg, progress=lambda *a: None)
    assert "hapax" not in {w for _, w in words(cfg)}


def test_a_word_below_min_docs_or_below_lift_is_not_suggested(tmp_path):
    cfg, _, _ = make_world(tmp_path)
    run_index(cfg, progress=lambda *a: None)
    found = {w for _, w in words(cfg)}
    assert "lowcount" not in found          # 4 documents
    assert "partage" not in found           # as frequent in Electrical as in Mechanical


def test_mixed_unknown_and_weak_documents_are_not_reference(tmp_path):
    cfg, root, _ = make_world(tmp_path)
    for f in ("f1", "f2", "f3"):
        for i in range(2):
            add(root, f"xx/{f}", f"d{i}.pdf", "pneumatique " * 5 + "variateur " * 5 + "mixword")       # Mixed
            add(root, f"zz/{f}", f"d{i}.pdf", "pneumatique " * 5 + "variateur " + "weakword")          # share < 0.85
            add(root, f"ww/{f}", f"d{i}.pdf", "pneumatique " * 5 + "prehenseur " * 5 + "refword")      # reference
    run_index(cfg, progress=lambda *a: None)
    found = {w for _, w in words(cfg)}
    assert "mixword" not in found and "weakword" not in found
    assert ("Mechanical", "refword") in words(cfg)                       # decided by words, share 1.0, score 30
    assert ("Mechanical", "sertissage") in words(cfg)                    # decided by a folder rule


def test_csv_row_cap(tmp_path):
    cfg, _, _ = make_world(tmp_path, rules=RULES + "\n[suggestions]\nmax_rows = 1\n")
    run_index(cfg, progress=lambda *a: None)
    mech = [r[1] for r in rows_of(cfg) if r[0] == "Mechanical"]
    assert mech == ["rivetage"]             # same lift as sertissage: alphabetical order decides


def test_reminder_lines(tmp_path):
    cfg, _, rules_file = make_world(tmp_path)
    path = cfg.world_dir / "keyword-suggestions.csv"
    lines: list[str] = []
    run_index(cfg, progress=lines.append)
    n = len(rows_of(cfg))
    assert n == 5          # sertissage, rivetage, "sertissage rivetage", bornier, compilation
    first = (f"Keyword suggestions: {n} waiting for your review ({n} new since the last run) in {path}. "
             "Nothing is added to disciplines.toml until you approve it.")
    assert lines[-1] == first or first in lines
    lines.clear()
    run_index(cfg, progress=lines.append)
    assert f"Keyword suggestions: {n} waiting for your review (0 new since the last run) in {path}. " \
           "Nothing is added to disciplines.toml until you approve it." in lines
    rules_file.write_text(RULES.replace('strong = ["pneumatique", "prehenseur"]',
                                        'strong = ["pneumatique", "prehenseur", "sertissage"]'), encoding="utf-8")
    lines.clear()
    run_index(cfg, progress=lines.append)
    # sertissage joins the list: it and the phrase that holds it are no longer proposed
    assert f"Keyword suggestions: {n - 2} waiting for your review (0 new since the last run) in {path}. " \
           "Nothing is added to disciplines.toml until you approve it." in lines


def test_no_suggestions_says_so(tmp_path):
    cfg, root, _ = make_world(tmp_path)
    for p in root.rglob("*.pdf"):
        p.unlink()
    add(root, "mm/f1", "only.pdf", "pneumatique")
    lines: list[str] = []
    run_index(cfg, progress=lines.append)
    assert "Keyword suggestions: none." in lines
    assert "Keyword suggestions waiting: 0" in DocTools(cfg).index_status()


def test_index_status_shows_the_waiting_count(tmp_path):
    cfg, _, _ = make_world(tmp_path)
    run_index(cfg, progress=lambda *a: None)
    assert f"Keyword suggestions waiting: {len(rows_of(cfg))}" in DocTools(cfg).index_status()


def test_nothing_is_written_to_the_rules_file(tmp_path):
    cfg, _, rules_file = make_world(tmp_path)
    before = rules_file.read_bytes()
    run_index(cfg, progress=lambda *a: None)
    assert rules_file.read_bytes() == before
    con = store.connect(cfg.db_path)
    assert store.get_meta(con, "suggestions_waiting") == str(len(rows_of(cfg)))
    con.close()

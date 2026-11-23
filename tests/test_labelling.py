import csv
import os

import pytest

from conftest import make_pdf
from dmx_docs import store
from dmx_docs.config import load_config
from dmx_docs.disciplines import RulesError
from dmx_docs.indexer import run_index
from dmx_docs.tools import DocTools

quiet = lambda *a, **k: None  # noqa: E731

RULES = """
[words.Mechanical]
strong = ["pneumatique", "prehenseur"]
[words.Electrical]
strong = ["variateur", "cablage"]
[words.Software]
strong = ["twincat"]
"""


def make_world(tmp_path, rules=RULES):
    root = tmp_path / "Eng"
    for folder in ("Meca", "Elec", "Misc", "Pneumatique", "Divers"):
        (root / folder).mkdir(parents=True)
    make_pdf(str(root / "Meca" / "pompes.pdf"), ["pneumatique prehenseur vide"])
    make_pdf(str(root / "Elec" / "armoire.pdf"), ["variateur cablage armoire"])
    make_pdf(str(root / "Misc" / "notes.pdf"), ["bonjour compte rendu de reunion"])
    make_pdf(str(root / "Pneumatique" / "scan.pdf"), [""])          # no text at all
    make_pdf(str(root / "Divers" / "vide.pdf"), [""])
    config = tmp_path / "config.toml"
    config.write_text("[index]\ndata_dir = 'data'\n[worlds.engineering]\ntitle = \"Engineering\"\n"
                      f"profile = \"engineering\"\nextensions = ['.pdf']\nroots = ['{root}']\n", encoding="utf-8")
    rules_file = tmp_path / "disciplines.toml"
    if rules is not None:
        rules_file.write_text(rules, encoding="utf-8")
    return load_config(config, world="engineering"), root, rules_file


def labels(cfg):
    con = store.connect(cfg.db_path)
    rows = {r[0]: r[1] for r in con.execute(
        "SELECT d.name, f.category FROM docs d JOIN doc_facets f ON f.doc_id = d.id")}
    con.close()
    return rows


def test_documents_get_a_discipline_and_the_filter_works(tmp_path):
    cfg, _, _ = make_world(tmp_path)
    run_index(cfg, progress=quiet)
    assert labels(cfg) == {"pompes.pdf": "Mechanical", "armoire.pdf": "Electrical", "notes.pdf": "Unknown",
                           "scan.pdf": "Mechanical", "vide.pdf": "Unknown"}     # Review Focus 2
    out = DocTools(cfg).search("pneumatique", mode="keyword", category="mechanical")
    assert "pompes.pdf" in out and "armoire.pdf" not in out
    assert "discipline Mechanical" in out


def test_editing_the_rules_relabels_without_extracting(tmp_path, monkeypatch):     # Review Focus 3
    cfg, _, rules_file = make_world(tmp_path)
    run_index(cfg, progress=quiet)
    con = store.connect(cfg.db_path)
    before = dict(con.execute("SELECT name, indexed_at FROM docs").fetchall())
    con.close()
    rules_file.write_text(RULES + '\n[[folder]]\npattern = "*/misc/*"\ndiscipline = "Software"\n', encoding="utf-8")
    monkeypatch.setattr("dmx_docs.indexer.extract_file", lambda *a, **k: pytest.fail("must not extract"))
    run_index(cfg, progress=quiet)
    assert labels(cfg)["notes.pdf"] == "Software"
    con = store.connect(cfg.db_path)
    assert dict(con.execute("SELECT name, indexed_at FROM docs").fetchall()) == before
    assert con.execute("SELECT source FROM doc_discipline d JOIN docs x ON x.id = d.doc_id "
                       "WHERE x.name = 'notes.pdf'").fetchone()[0] == "rule"
    con.close()


def test_a_changed_document_is_relabelled_and_an_unchanged_one_is_not_rescored(tmp_path):   # Review Focus 3
    cfg, root, _ = make_world(tmp_path)
    run_index(cfg, progress=quiet)
    con = store.connect(cfg.db_path)
    stamp = con.execute("SELECT labelled_at FROM doc_discipline d JOIN docs x ON x.id = d.doc_id "
                        "WHERE x.name = 'armoire.pdf'").fetchone()[0]
    con.close()
    target = root / "Meca" / "pompes.pdf"
    make_pdf(str(target), ["twincat twincat"])
    os.utime(target, (target.stat().st_atime + 100, target.stat().st_mtime + 100))
    run_index(cfg, progress=quiet)
    assert labels(cfg)["pompes.pdf"] == "Software"
    con = store.connect(cfg.db_path)
    assert con.execute("SELECT labelled_at FROM doc_discipline d JOIN docs x ON x.id = d.doc_id "
                       "WHERE x.name = 'armoire.pdf'").fetchone()[0] == stamp
    con.close()


def test_missing_rules_file_labels_everything_unknown_with_one_warning(tmp_path):     # Review Focus 1
    cfg, _, _ = make_world(tmp_path, rules=None)
    lines: list[str] = []
    run_index(cfg, progress=lines.append)
    assert set(labels(cfg).values()) == {"Unknown"}
    assert sum("disciplines.toml not found" in line for line in lines) == 1


def test_bad_rules_stop_the_run_before_extraction(tmp_path, monkeypatch):            # Review Focus 1
    cfg, _, _ = make_world(tmp_path, rules="this is = not toml [")
    monkeypatch.setattr("dmx_docs.indexer.extract_file", lambda *a, **k: pytest.fail("must not extract"))
    with pytest.raises(RulesError, match="disciplines.toml"):
        run_index(cfg, progress=quiet)


def test_the_cli_reports_bad_rules_in_one_line(tmp_path, capsys):
    from dmx_docs.cli import main
    make_world(tmp_path, rules="this is = not toml [")
    with pytest.raises(SystemExit) as exit_info:
        main(["--config", str(tmp_path / "config.toml"), "--world", "engineering", "index"])
    assert exit_info.value.code == 2
    assert "Not indexed:" in capsys.readouterr().out


def add_chunk(cfg, name, text):
    """What OCR or the picture descriptions do: new chunks for a document, without extracting it again."""
    con = store.connect(cfg.db_path)
    doc_id = con.execute("SELECT id FROM docs WHERE name = ?", (name,)).fetchone()[0]
    seq = con.execute("SELECT coalesce(max(seq), -1) + 1 FROM chunks WHERE doc_id = ?", (doc_id,)).fetchone()[0]
    con.execute("INSERT INTO chunks(doc_id, page_no, seq, text) VALUES (?, 1, ?, ?)", (doc_id, seq, text))
    con.commit()
    con.close()


def test_text_added_after_extraction_is_scored(tmp_path):                  # final review, Important 1
    from dmx_docs import labelling
    from dmx_docs.disciplines import load_rules
    cfg, _, _ = make_world(tmp_path)
    run_index(cfg, progress=quiet)
    assert labels(cfg)["vide.pdf"] == "Unknown"
    con = store.connect(cfg.db_path)
    stamp = con.execute("SELECT labelled_at FROM doc_discipline d JOIN docs x ON x.id = d.doc_id "
                        "WHERE x.name = 'armoire.pdf'").fetchone()[0]
    con.close()
    add_chunk(cfg, "vide.pdf", "pneumatique prehenseur")
    con = store.connect(cfg.db_path)
    labelling.refresh(con, cfg, load_rules(cfg.disciplines_path), quiet)
    con.close()
    assert labels(cfg)["vide.pdf"] == "Mechanical"
    con = store.connect(cfg.db_path)
    assert con.execute("SELECT labelled_at FROM doc_discipline d JOIN docs x ON x.id = d.doc_id "
                       "WHERE x.name = 'armoire.pdf'").fetchone()[0] == stamp      # the others are not rescored
    con.close()


def test_ocr_and_describe_end_by_relabelling_an_engineering_world(tmp_path, monkeypatch):
    from dmx_docs import indexer
    cfg, _, _ = make_world(tmp_path)
    run_index(cfg, progress=quiet)
    add_chunk(cfg, "vide.pdf", "pneumatique prehenseur")
    lines: list[str] = []
    indexer.refresh_disciplines(cfg, lines.append)
    assert labels(cfg)["vide.pdf"] == "Mechanical"
    assert any(line.startswith("Keyword suggestions:") for line in lines)


def test_classification_report(tmp_path):
    cfg, _, rules_file = make_world(tmp_path)
    rules_file.write_text(RULES + '\n[[folder]]\npattern = "*/misc/*"\ndiscipline = "Software"\n', encoding="utf-8")
    run_index(cfg, progress=quiet)
    raw = (cfg.world_dir / "engineering-classification.csv").read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(raw.decode("utf-8-sig").splitlines(), delimiter=";"))
    assert rows[0] == ["path", "section", "discipline", "source", "mechanical", "electrical", "software",
                       "top_terms"]
    by_name = {os.path.basename(r[0]): r for r in rows[1:]}
    assert len(rows) == 6 and by_name["notes.pdf"][2:4] == ["Software", "rule"]
    assert by_name["pompes.pdf"][1] == "Meca" and by_name["pompes.pdf"][3] == "words"
    assert int(by_name["pompes.pdf"][4]) > 0 and "pneumatique" in by_name["pompes.pdf"][7]


def test_label_counts_are_reported(tmp_path):
    from dmx_docs import labelling
    cfg, _, _ = make_world(tmp_path)
    run_index(cfg, progress=quiet)
    con = store.connect(cfg.db_path)
    from dmx_docs.disciplines import load_rules
    counts = labelling.refresh(con, cfg, load_rules(cfg.disciplines_path), quiet)    # nothing left to label
    con.close()
    assert counts == {"Mechanical": 2, "Electrical": 1, "Unknown": 2}

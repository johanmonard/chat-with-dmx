import json
import os
from datetime import date, datetime

import pytest

from dmx_docs import cli, offers, prices
from offers_corpus import (VOCABULARY, build_garbled_corpus, build_offers_corpus, build_versions_corpus,
                           closed_port_url, indexed, make_projects_db, quiet, world)


@pytest.fixture(scope="session")
def _offers_index(tmp_path_factory):
    """The offers tree indexed, once per session."""
    return indexed(tmp_path_factory, "offers", build_offers_corpus)


@pytest.fixture(scope="session")
def _garbled_index(tmp_path_factory):
    return indexed(tmp_path_factory, "second", build_garbled_corpus)  # "garbled" only in one path


@pytest.fixture(scope="session")
def _versions_index(tmp_path_factory):
    return indexed(tmp_path_factory, "versions", build_versions_corpus)


@pytest.fixture
def versions_world(_versions_index, tmp_path):
    """Archive versions in sibling folders and offer-like files of suppliers; the register built."""
    cfg, con = world(_versions_index, tmp_path)
    yield cfg, con
    con.close()


@pytest.fixture
def offers_world(_offers_index, tmp_path):
    """The offers tree indexed, then the register built (model unavailable: closed port)."""
    cfg, con = world(_offers_index, tmp_path)
    yield cfg, con
    con.close()


@pytest.fixture
def offers_world_garbled(_garbled_index, tmp_path):
    """One readable offer and one whose price page the rules cannot read; the register is not built yet."""
    cfg, con = world(_garbled_index, tmp_path, run=False)
    yield cfg, con
    con.close()


def test_register(offers_world):
    cfg, con = offers_world
    o = con.execute("SELECT * FROM offers WHERE offer_key='3001234'").fetchone()
    assert (o["client"], o["first_date"], o["last_date"], o["year"], o["offer_type"]) == \
           ("ACME Foods", "2020-02-10", "2020-05-14", 2020, "firm")
    assert (o["title"], o["status"], o["category"], o["number_written"]) == ("Line 5", "unknown", "client", "3001234")
    revs = con.execute("SELECT revision, variant, translation_of IS NOT NULL t, confidence, date, date_source, pages, "
                       "totals, read_by FROM offer_revisions WHERE offer_key='3001234' ORDER BY rev_order, id").fetchall()
    assert [(r[0], r[1], r[2]) for r in revs] == [("00", None, 0), ("01", None, 0), ("01", None, 1),
                                                  ("02", "Vandelay version", 0), ("03", "Vandelay", 0),
                                                  ("03", "Wonka", 0)]
    assert revs[0]["confidence"] == "high" and revs[0]["read_by"] == "rules"
    assert (revs[0]["date"], revs[0]["date_source"], revs[0]["pages"]) == ("2020-02-10", "document", "2")
    assert json.loads(revs[4]["totals"]) == {"CHF": 200000.0} and json.loads(revs[5]["totals"]) == {"CHF": 180000.0}
    lines = con.execute("SELECT kind, amounts, page, l.read_by FROM offer_lines l JOIN offer_revisions r "
                        "ON r.id=l.revision_id WHERE r.offer_key='3001234' AND r.revision='00' "
                        "ORDER BY position").fetchall()
    assert [l[0] for l in lines] == ["machine", "option", "service", "discount"]
    assert json.loads(lines[0][1]) == {"CHF": 180000.0} and lines[0]["page"] == 2 and lines[0]["read_by"] == "rules"
    rate = con.execute("SELECT base, quote, rate, date_written FROM offer_rates t JOIN offer_revisions r "
                       "ON r.id=t.revision_id WHERE r.offer_key='3001234' AND r.revision='00'").fetchone()
    assert tuple(rate) == ("EUR", "CHF", 1.2, "November 1st, 2011")


def test_translation_lines_not_counted(offers_world):
    _, con = offers_world
    n = con.execute("SELECT count(*) FROM offer_lines l JOIN offer_revisions r ON r.id=l.revision_id "
                    "WHERE r.translation_of IS NOT NULL").fetchone()[0]
    assert n == 0
    de = con.execute("SELECT path, translation_of FROM offer_revisions WHERE translation_of IS NOT NULL").fetchone()
    first = con.execute("SELECT path FROM offer_revisions WHERE id=?", (de[1],)).fetchone()
    assert de[0].endswith("_01_DE.pdf") and first[0].endswith("3001234_01.pdf")


def test_archive_offer(offers_world):
    _, con = offers_world
    o = con.execute("SELECT * FROM offers WHERE offer_key='B-250306'").fetchone()
    assert (o["category"], o["country"], o["country_source"], o["offer_type"]) == ("archive", "Germany", "folder", "budget")
    assert (o["client"], o["first_date"], o["year"]) == ("Dupont Biscuits", "2006-03-25", 2006)
    r = con.execute("SELECT revision, totals, pages FROM offer_revisions WHERE offer_key='B-250306'").fetchone()
    assert r[0] == "V1" and json.loads(r[1]) == {"CHF": 461000.0, "EUR": 450000.0} and r[2] == "2-3"


def test_unread_revision_is_listed(offers_world):
    _, con = offers_world
    r = con.execute("SELECT read_by, confidence, note, date_source FROM offer_revisions WHERE offer_key='0904023'").fetchone()
    assert (r[0], r[1]) == ("none", "none") and "no price section" in r[2]
    assert r[3] == "file"  # no printed date, no readable History.xls
    o = con.execute("SELECT country, country_source FROM offers WHERE offer_key='0904023'").fetchone()
    assert tuple(o) == ("United Kingdom", "document")


def test_offer_folder_without_a_number_is_not_in_the_register(offers_world):
    _, con = offers_world
    assert con.execute("SELECT count(*) FROM offers WHERE folder LIKE '%Line 7%'").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM offers").fetchone()[0] == 8


def test_unread_document_before_a_priced_one_in_a_revision(offers_world):
    _, con = offers_world
    revs = con.execute("SELECT path, read_by, translation_of, date, date_source FROM offer_revisions "
                       "WHERE offer_key='3002222' ORDER BY id").fetchall()
    assert [r["read_by"] for r in revs] == ["none", "rules"] and revs[0]["path"].endswith("annex.pdf")
    assert revs[1]["translation_of"] is None and (revs[1]["date"], revs[1]["date_source"]) == ("2019-05-05", "document")
    assert revs[0]["date_source"] == "file"
    o = con.execute("SELECT first_date, last_date, year FROM offers WHERE offer_key='3002222'").fetchone()
    assert tuple(o) == ("2019-05-05", "2019-05-05", 2019)  # the annex's file date does not move the year


def test_documents_the_index_could_not_read_are_listed(offers_world):
    _, con = offers_world
    scan = con.execute("SELECT read_by, confidence, note, date_source FROM offer_revisions "
                       "WHERE offer_key='3003333'").fetchone()
    assert tuple(scan) == ("none", "none", "no text (status no_text)", "file")
    broken = con.execute("SELECT revision, note FROM offer_revisions WHERE offer_key='3004444' ORDER BY rev_order").fetchall()
    assert broken[0]["revision"] == "00" and broken[0]["note"].startswith("file error: ")
    assert (broken[1]["revision"], broken[1]["note"]) == ("01", "empty file")
    assert con.execute("SELECT count(*) FROM offers WHERE offer_key IN ('3003333', '3004444')").fetchone()[0] == 2


def test_the_folder_with_the_offer_documents_owns_the_number(offers_world):
    _, con = offers_world
    o = con.execute("SELECT client, folder, duplicate_of FROM offers WHERE offer_key='3006666'").fetchone()
    assert o["client"] == "Globex" and o["folder"].endswith(r"Globex\Offres\3006666")
    assert o["duplicate_of"].endswith("3006666 - specs")


def test_history_label_is_not_a_date():
    pattern = offers._label_pattern("02")
    assert pattern.search("Offre envoyée le 02.03.2009") is None and offers._label_pattern("03").search(
        "Offre envoyée le 02.03.2009") is None
    assert pattern.search("Offre V2") and pattern.search("Révision 02 envoyée") and pattern.search("02 - Vandelay")


def test_duplicate_key_in_two_client_folders(offers_world):
    _, con = offers_world
    o = con.execute("SELECT client, duplicate_of FROM offers WHERE offer_key='3001234'").fetchone()
    assert o["client"] == "ACME Foods" and "Globex" in (o["duplicate_of"] or "")
    assert con.execute("SELECT count(*) FROM offer_revisions WHERE path LIKE '%Globex%' "
                       "AND offer_key='3001234'").fetchone()[0] == 0


def test_rebuild_is_idempotent(offers_world):
    cfg, con = offers_world
    counts = [con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
              for t in ("offers", "offer_revisions", "offer_lines", "offer_rates")]
    stats = offers.run_offers(cfg, progress=quiet)
    assert [con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            for t in ("offers", "offer_revisions", "offer_lines", "offer_rates")] == counts
    assert (stats.offers, stats.revisions, stats.lines) == tuple(counts[:3])
    assert "offers" in stats.summary()


def test_history_and_quotation_list_fill_dates_and_countries(offers_world, monkeypatch):
    cfg, con = offers_world
    seen = []

    def history(path):
        seen.append(path)
        return [("2009-01-29", "CHL", "Première offre"), ("2009-03-02", "CHL", "Offre V2")]

    def quotations(path):
        seen.append(path)
        return [{"offer": "0904099", "country": "Frankreich", "author": "CGA", "status": "Running"},
                {"offer": "0904023", "country": "Atlantis", "author": "CGA", "status": "Running"}]

    monkeypatch.setattr(offers, "read_history", history)
    monkeypatch.setattr(offers, "read_quotation_list", quotations)
    offers.run_offers(cfg, progress=quiet)
    assert any(p.endswith(r"0904099\History\History.xls") for p in seen)
    assert sum(p.endswith(r"ACME Foods\Suivi\QuotationList.xls") for p in seen) == 1  # read once per client folder
    r = con.execute("SELECT date, date_source FROM offer_revisions WHERE offer_key='0904099'").fetchone()
    assert tuple(r) == ("2009-01-29", "history")
    o = con.execute("SELECT country, country_source, author FROM offers WHERE offer_key='0904099'").fetchone()
    assert tuple(o) == ("France", "quotation_list", "CHL")
    o = con.execute("SELECT country, country_source FROM offers WHERE offer_key='0904023'").fetchone()
    assert tuple(o) == ("United Kingdom", "document")  # an unknown country name is not stored: the next source decides


def test_model_unavailable_keeps_rules_lines(offers_world_garbled):
    cfg, con = offers_world_garbled
    stats = offers.run_offers(cfg, progress=quiet)  # vision_url points to a closed port
    assert stats.model_unavailable and stats.by_rules > 0
    r = con.execute("SELECT read_by, note FROM offer_revisions WHERE path LIKE '%garbled%'").fetchone()
    assert r[0] == "none" and "model unavailable" in r[1]
    assert "unavailable" in stats.summary()


def _price_answer(body):
    content = json.dumps({"lines": [{"name": "Paloma 4R cell", "text": "Paloma 4R cell", "quantity": None,
                                     "amounts": {"CHF": 180000}},
                                    {"name": "Option vision", "text": "Option vision", "amounts": {"CHF": 25000}},
                                    {"name": "Training", "text": "Training", "amounts": {"CHF": 4000}}],
                          "totals": {"CHF": 209000}, "rates": []})
    return 200, {"model": "x", "message": {"role": "assistant", "content": content}, "done": True}


def test_model_cache(fake_ollama, offers_world_garbled):
    cfg, con = offers_world_garbled
    fake_ollama.reply = _price_answer
    cfg.vision_url = fake_ollama.url
    stats = offers.run_offers(cfg, progress=quiet)
    n = len(fake_ollama.calls)
    assert n == 1 and stats.by_model == 1 and not stats.model_unavailable
    r = con.execute("SELECT read_by, confidence, sum_ok FROM offer_revisions WHERE path LIKE '%garbled%'").fetchone()
    assert tuple(r) == ("model", "medium", 1)
    kinds = [l[0] for l in con.execute("SELECT l.kind FROM offer_lines l JOIN offer_revisions r ON r.id=l.revision_id "
                                       "WHERE r.path LIKE '%garbled%' ORDER BY position")]
    assert kinds == ["machine", "option", "service"]
    offers.run_offers(cfg, progress=quiet)
    assert len(fake_ollama.calls) == n  # second run: from the cache
    cfg.vision_url = closed_port_url()  # the cache also serves when the model is gone
    stats = offers.run_offers(cfg, progress=quiet)
    assert stats.by_model == 1 and not stats.model_unavailable
    con.execute("UPDATE offer_model_cache SET answer = '{broken'")  # an unreadable entry is asked again
    con.commit()
    cfg.vision_url = fake_ollama.url
    stats = offers.run_offers(cfg, progress=quiet)
    assert stats.by_model == 1 and len(fake_ollama.calls) == n + 1


def test_model_cache_key_has_a_version(fake_ollama, offers_world_garbled, monkeypatch):
    cfg, _ = offers_world_garbled
    fake_ollama.reply = _price_answer
    cfg.vision_url = fake_ollama.url
    offers.run_offers(cfg, progress=quiet)
    monkeypatch.setattr(offers, "MODEL_CACHE_VERSION", offers.MODEL_CACHE_VERSION + 1)
    offers.run_offers(cfg, progress=quiet)
    assert len(fake_ollama.calls) == 2


def test_old_machine_name_from_the_word_list(offers_world):
    _, con = offers_world
    l = con.execute("SELECT model, family FROM offer_lines l JOIN offer_revisions r ON r.id=l.revision_id "
                    "WHERE r.offer_key='0904099' AND kind='machine'").fetchone()
    assert (l[0], l[1]) == ("Paloma 2R", "Paloma")


def test_machine_aliases(tmp_path):
    p = tmp_path / "vocabulary.md"
    p.write_text("## forms\n- box: etui\n" + VOCABULARY, encoding="utf-8")
    aliases = offers.machine_aliases(str(p))
    assert aliases[0] == ("paloma d2 top loader with two delta robots", "Paloma 2R")
    assert ("paloma d2 top loader", "Paloma 2R") in aliases and all(m == "Paloma 2R" for _, m in aliases)
    assert offers.machine_aliases(str(tmp_path / "missing.md")) == []


def test_history_and_quotation_rows():
    assert offers.history_rows([["Date", "Rédacteur", "Action", "TODO"], [39842.0, "CHL", "Première offre", ""],
                                ["", "", "", ""]]) == [("2009-01-29", "CHL", "Première offre")]
    rows = offers.quotation_rows([["Country", "City"], ["Final Customer"],
                                  ["France", "Lyon", "Mise en boîte", 39960.0, "Paloma FP2", "0905020", "", "", "CGA",
                                   "Running", "ACME"]])
    assert rows[0]["offer"] == "0905020" and rows[0]["date"] == "2009-05-27" and rows[0]["status"] == "Running"
    assert rows[0]["country"] == "France" and rows[0]["final_customer"] is None


def test_history_dates_before_1990_or_empty_are_no_dates():
    # Empty or zero date cells come out of xlrd as 1899-12-31 / 1900-01-01, or as serials 0 and 1.
    rows = [["Date", "Rédacteur", "Action"], ["1899-12-31", "CHL", "a"], ["1900-01-01", "CHL", "b"], [0.0, "CHL", "c"],
            [1.0, "CHL", "d"], [2958000.0, "CHL", "e"], ["", "CHL", "f"], [date(1985, 3, 1), "CHL", "g"],
            [39842.0, "CHL", "Première offre"]]
    assert offers.history_rows(rows) == [("2009-01-29", "CHL", "Première offre")]
    assert offers.quotation_rows([[], [], ["France", "Lyon", "Line", "1900-01-01", "", 905021.0]])[0]["date"] is None
    assert offers._mtime_date({"mtime": datetime(1985, 1, 1).timestamp()}) is None  # a file date of an old copy
    assert offers._mtime_date({"mtime": datetime(2009, 1, 29, 12).timestamp()}) == "2009-01-29"


def test_a_history_without_a_real_date_falls_back_to_the_file_date(offers_world, monkeypatch):
    cfg, con = offers_world

    def sheet(path, sheet="Quotations"):
        if not path.endswith("History.xls"):
            raise ValueError("not a workbook")
        return [["Date", "Rédacteur", "Action"], ["1900-01-01", "CHL", "Première offre"], [0.0, "CHL", "Offre V2"]]

    monkeypatch.setattr(offers, "_sheet_rows", sheet)
    offers.run_offers(cfg, progress=quiet)
    r = con.execute("SELECT date, date_source FROM offer_revisions WHERE offer_key='0904099'").fetchone()
    assert r["date_source"] == "file" and r["date"] > "1990"
    assert con.execute("SELECT count(*) FROM offers WHERE year < 1990").fetchone()[0] == 0


def test_quotation_rows_details():
    rows = offers.quotation_rows([[], [], ["France", "", "", "", "", "", "", "", "", "", ""],  # too few cells
                                  ["Suisse", "Bern", "Line", "27.05.2009", "", 905021.0, "", "", "CGA", "Ordered", ""]])
    assert len(rows) == 1
    assert rows[0]["offer"] == "0905021" and rows[0]["date"] == "2009-05-27"  # a number cell lost its leading 0


@pytest.fixture
def marketing_cfg_file(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text(f"[index]\ndata_dir = '{(tmp_path / 'data').as_posix()}'\n\n[worlds.marketing]\nprofile = 'marketing'\n",
                 encoding="utf-8")
    return p


def test_cli_other_profile_is_a_noop(capsys, marketing_cfg_file):
    cli.main(["--config", str(marketing_cfg_file), "--world", "marketing", "offers"])
    assert "Not an offers world" in capsys.readouterr().out


def test_run_offers_refuses_another_profile(tmp_path, make_cfg):
    with pytest.raises(ValueError):
        offers.run_offers(make_cfg(tmp_path, profile="projects"), progress=quiet)


# --- Won / not won, corrections, review --------------------------------------------------------------

def test_number_forms():
    token, rx = offers.number_forms("3003929")
    assert (token, rx.pattern) == ("3003929", r"(?<!\d)3003929(?!\d)")
    token, rx = offers.number_forms("B-250306")
    assert (token, rx.pattern) == ("250306", r"(?<![A-Za-z0-9])B-?250306(?!\d)")
    token, rx = offers.number_forms("1106033-A")
    assert (token, rx.pattern) == ("1106033", r"(?<!\d)1106033-?A(?![A-Za-z])")
    assert rx.search("Offre 1106033A") and not rx.search("Offre 1106033-AB")


def test_won_by_number_in_a_project(offers_world, tmp_path):
    _, con = offers_world
    # The project's machine (a Paloma, in a file name) is a family of the offer's lines: confirmed.
    pdb = make_projects_db(tmp_path, {("THOR", r"THOR\0_Vente\00_Offres\Offre Paloma 2R.pdf"):
                                      "Notre offre 3001234 du 14.05.2020"})
    assert offers.link_projects(con, pdb, today=date(2026, 10, 16)) == 1
    o = con.execute("SELECT status, project, status_evidence FROM offers WHERE offer_key='3001234'").fetchone()
    assert (o[0], o[1]) == ("won", "THOR") and o[2].endswith("Offre Paloma 2R.pdf p.1")
    o = con.execute("SELECT status, status_source FROM offers WHERE offer_key='3002222'").fetchone()
    assert tuple(o) == ("no_order_found", "age")


def _register_client(pdb, project, client):
    import sqlite3
    pcon = sqlite3.connect(pdb)
    pcon.execute("INSERT INTO register_machines(project, client) VALUES (?, ?)", (project, client))
    pcon.commit()
    pcon.close()


def test_a_mention_in_a_project_of_another_client_and_machine_is_unconfirmed(offers_world, tmp_path):
    _, con = offers_world
    pdb = make_projects_db(tmp_path, {("THOR", r"THOR\0_Vente\00_Offres\Offre Presto 2R.pdf"):
                                      "Comme l'offre 3001234 faite à un autre client"})
    _register_client(pdb, "THOR", "Globex Snacks")
    assert offers.link_projects(con, pdb, today=date(2026, 10, 16)) == 0
    o = con.execute("SELECT status, status_source, project, status_evidence FROM offers "
                    "WHERE offer_key='3001234'").fetchone()
    assert tuple(o[:3]) == ("won_unconfirmed", "projects_world", "THOR") and o[3].endswith("Offre Presto 2R.pdf p.1")
    rows = _review_rows(con, tmp_path)
    flagged = [r for r in rows if r.startswith("3001234;") and ";unconfirmed project link;" in r]
    assert len(flagged) == 1 and "THOR" in flagged[0]
    _register_client(pdb, "THOR", "ACME Foods SA")  # the project's client is the offer's: confirmed
    assert offers.link_projects(con, pdb, today=date(2026, 10, 16)) == 1
    assert con.execute("SELECT status FROM offers WHERE offer_key='3001234'").fetchone()[0] == "won"
    assert not [r for r in _review_rows(con, tmp_path) if ";unconfirmed project link;" in r]


def test_archive_number_needs_its_prefix(offers_world, tmp_path):
    _, con = offers_world
    pdb = make_projects_db(tmp_path, {("ANGE", r"ANGE\5_Gestion\PV.pdf"): "Réunion du 250306 à Lausanne"})
    offers.link_projects(con, pdb, today=date(2026, 10, 16))
    assert con.execute("SELECT status FROM offers WHERE offer_key='B-250306'").fetchone()[0] == "no_order_found"


def test_archive_number_in_a_project_path(offers_world, tmp_path):
    _, con = offers_world
    pdb = make_projects_db(tmp_path, {("ANGE", r"ANGE\0_Vente\B-250306 V1\Contrat.pdf"): "Contrat de vente"})
    assert offers.link_projects(con, pdb, today=date(2026, 10, 16)) == 1
    o = con.execute("SELECT status, status_source, project, status_evidence FROM offers "
                    "WHERE offer_key='B-250306'").fetchone()
    assert tuple(o[:3]) == ("won", "projects_world", "ANGE") and o[3].endswith(r"B-250306 V1\Contrat.pdf")


def test_several_projects(offers_world, tmp_path):
    _, con = offers_world
    pdb = make_projects_db(tmp_path, {("THOR", r"THOR\0_Vente\Offre Paloma 2R.pdf"): "Offre 3001234",
                                      ("THOR", r"THOR\5_Gestion\PV.pdf"): "Selon offre 3001234",
                                      ("ANGE", r"ANGE\5_Gestion\PV.pdf"): "Comme l'offre 3001234"})
    assert offers.link_projects(con, pdb, today=date(2026, 10, 16)) == 1
    o = con.execute("SELECT status, project, other_projects, status_evidence FROM offers "
                    "WHERE offer_key='3001234'").fetchone()
    assert tuple(o[:3]) == ("won", "THOR", "ANGE") and o[3].count(" p.1") == 3
    offers.write_review(con, str(tmp_path / "review.csv"))
    rows = (tmp_path / "review.csv").read_text(encoding="utf-8-sig").splitlines()
    assert any(r.startswith("3001234;") and ";several projects;" in r and "ANGE" in r for r in rows)


def test_open_and_unknown(offers_world, tmp_path):
    _, con = offers_world
    offers.link_projects(con, make_projects_db(tmp_path, {}), today=date(2021, 1, 1))
    assert con.execute("SELECT status FROM offers WHERE offer_key='3001234'").fetchone()[0] == "open"
    offers.link_projects(con, None)
    assert con.execute("SELECT status, status_source FROM offers WHERE offer_key='3001234'").fetchone()[:] == ("unknown", "none")
    offers.link_projects(con, str(tmp_path / "missing.sqlite3"))
    o = con.execute("SELECT status, status_source, status_evidence FROM offers WHERE offer_key='3001234'").fetchone()
    assert tuple(o[:2]) == ("unknown", "none") and "not found" in o[2]


def test_quotation_list_status(offers_world, tmp_path, monkeypatch):
    cfg, con = offers_world
    monkeypatch.setattr(offers, "read_quotation_list", lambda path: [
        {"offer": "0904099", "country": None, "author": None, "status": "Ordered"},
        {"offer": "0904023", "country": None, "author": None, "status": "Cancelled"},
        {"offer": "3002222", "country": None, "author": None, "status": "Running"}])
    pdb = make_projects_db(tmp_path, {})
    offers.run_offers(cfg, projects_db=pdb, progress=quiet)
    rows = dict((r[0], (r[1], r[2], r[3])) for r in con.execute(
        "SELECT offer_key, status, status_source, project FROM offers WHERE offer_key IN ('0904099', '0904023', '3002222')"))
    assert rows == {"0904099": ("won", "quotation_list", None), "0904023": ("cancelled", "quotation_list", None),
                    "3002222": ("no_order_found", "age", None)}


def _corrections(tmp_path, *rows, sep=";"):
    p = tmp_path / "corrections.csv"
    head = sep.join(("offer", "revision", "line", "field", "value", "note", "who", "date"))
    p.write_text("\n".join([head, *rows]) + "\n", encoding="utf-8")
    return str(p)


def test_corrections(offers_world, tmp_path):
    _, con = offers_world
    p = tmp_path / "corrections.csv"
    p.write_text("offer;revision;line;field;value;note;who;date\n"
                 "3001234;-;-;status;lost;lost to a competitor;JM;2026-10-16\n"
                 "3001234;00;1;model;Paloma 3R;;JM;2026-10-16\n"
                 "3001234;00;2;amount:EUR;24000;;JM;2026-10-16\n"
                 "9999999;-;-;status;won;;JM;2026-10-16\n", encoding="utf-8-sig")
    msgs = offers.apply_corrections(con, str(p))
    assert msgs == ["correction for offer 9999999: no such offer"]
    assert con.execute("SELECT status FROM offers WHERE offer_key='3001234'").fetchone()[0] == "lost"
    line2 = con.execute("SELECT amounts, l.read_by FROM offer_lines l JOIN offer_revisions r ON r.id=l.revision_id "
                        "WHERE r.offer_key='3001234' AND r.revision='00' AND position=2").fetchone()
    assert json.loads(line2[0]) == {"CHF": 25000.0, "EUR": 24000.0} and line2[1] == "correction"
    line1 = con.execute("SELECT model, family, l.read_by FROM offer_lines l JOIN offer_revisions r "
                        "ON r.id=l.revision_id WHERE r.offer_key='3001234' AND r.revision='00' AND position=1").fetchone()
    assert tuple(line1) == ("Paloma 3R", "Paloma", "correction")
    kept = con.execute("SELECT offer, revision, line, field, value, note, who, date, applied, message "
                       "FROM offer_corrections ORDER BY rowid").fetchall()
    assert [tuple(r)[:9] for r in kept][0] == ("3001234", None, None, "status", "lost", "lost to a competitor", "JM",
                                              "2026-10-16", 1)
    assert [r["applied"] for r in kept] == [1, 1, 1, 0] and kept[3]["message"].endswith("no such offer")


def test_correction_targets_and_values(offers_world, tmp_path):
    _, con = offers_world
    path = _corrections(
        tmp_path,
        "3001234,,,colour,red,,JM,2026-10-16",       # unknown field for an offer
        "3001234,07,,date,2020-08-01,,JM,2026-10-16",  # no such revision
        "3001234,0,9,kind,option,,JM,2026-10-16",     # no such line (revision 0 = 00: Excel drops the zero)
        "3001234,00,3,quantity,two,,JM,2026-10-16",   # not a number
        "3001234,-,-,status,maybe,,JM,2026-10-16",    # not a status
        "3001234,02,,ignore,yes,,JM,2026-10-16",
        "3001234,01,,date,2020-03-05,,JM,2026-10-16",
        "3001234,00,4,delete,yes,,JM,2026-10-16",
        "3001234,00,3,amount:CHF,,,JM,2026-10-16",    # empty value: the currency is removed
        "904023,-,-,country,Ireland,,JM,2026-10-16",  # 0904023 as Excel writes it
        sep=",")
    msgs = offers.apply_corrections(con, path)
    assert msgs == ["correction for offer 3001234: unknown field 'colour'",
                    "correction for offer 3001234: no revision '07'",
                    "correction for offer 3001234: no line 9 in revision '00'",
                    "correction for offer 3001234: quantity 'two' is not a number",
                    "correction for offer 3001234: unknown status 'maybe'"]
    assert con.execute("SELECT ignored FROM offer_revisions WHERE offer_key='3001234' AND revision='02'").fetchone()[0] == 1
    assert {tuple(r) for r in con.execute("SELECT date, date_source FROM offer_revisions WHERE offer_key='3001234' "
                                          "AND revision='01'")} == {("2020-03-05", "correction")}
    lines = con.execute("SELECT position, amounts FROM offer_lines l JOIN offer_revisions r ON r.id=l.revision_id "
                        "WHERE r.offer_key='3001234' AND r.revision='00' ORDER BY position").fetchall()
    assert [l[0] for l in lines] == [1, 2, 3] and json.loads(lines[2][1]) == {}
    assert con.execute("SELECT country, country_source FROM offers WHERE offer_key='0904023'").fetchone()[:] == \
           ("Ireland", "correction")
    assert offers.apply_corrections(con, None) == [] and offers.apply_corrections(con, str(tmp_path / "none.csv")) == []


def test_corrections_survive_a_rebuild(offers_world, tmp_path):
    cfg, con = offers_world
    cfg.corrections_path = _corrections(tmp_path, "3001234;-;-;status;lost;lost to a competitor;JM;2026-10-16",
                                        "3001234;-;-;project;THOR;;JM;2026-10-16")
    offers.run_offers(cfg, progress=quiet)
    stats = offers.run_offers(cfg, progress=quiet)
    o = con.execute("SELECT status, status_source, project FROM offers WHERE offer_key='3001234'").fetchone()
    assert tuple(o) == ("lost", "correction", "THOR") and stats.stale_corrections == 0
    assert con.execute("SELECT count(*) FROM offer_corrections").fetchone()[0] == 2
    cfg.corrections_path = _corrections(tmp_path, "9999999;-;-;status;won;;JM;2026-10-16")
    messages = []
    stats = offers.run_offers(cfg, progress=messages.append)
    assert stats.stale_corrections == 1 and "correction for offer 9999999: no such offer" in messages
    (tmp_path / "corrections.csv").write_text("who;what\nJM;x\n", encoding="utf-8")  # not a corrections file
    messages = []
    stats = offers.run_offers(cfg, progress=messages.append)
    assert stats.stale_corrections == 0 and any("not read: missing columns offer, field" in m for m in messages)


def test_review(offers_world, tmp_path):
    _, con = offers_world
    n = offers.write_review(con, str(tmp_path / "review.csv"))
    rows = (tmp_path / "review.csv").read_text(encoding="utf-8-sig").splitlines()
    assert rows[0] == "offer;revision;client;issue;detail;path;page" and n == len(rows) - 1
    assert any(";no price section;" in r and r.startswith("0904023;") for r in rows)
    assert any(";duplicate offer number;" in r for r in rows)
    assert any(";unknown offer folder;" in r and "Line 7 proposal" in r for r in rows)


def test_review_other_issues(offers_world, tmp_path):
    _, con = offers_world
    con.execute("UPDATE offer_lines SET model = NULL, family = NULL, text = '=cmd|x; type X\nwith robots' "
                "WHERE kind = 'machine' AND revision_id = (SELECT id FROM offer_revisions WHERE offer_key='0904099')")
    con.execute("UPDATE offer_revisions SET sum_ok = 0, confidence = 'low', note = 'sum check failed; model unavailable' "
                "WHERE offer_key = '3002222' AND read_by = 'rules'")
    con.execute("UPDATE offer_revisions SET sum_ok = 0, confidence = 'low', note = 'sum check failed' "
                "WHERE offer_key = '3006666'")
    con.execute("UPDATE offer_revisions SET read_by = 'model', sum_ok = NULL, confidence = 'low' "
                "WHERE offer_key = 'B-250306'")
    con.execute("UPDATE offer_revisions SET sum_ok = 0, confidence = 'low' WHERE offer_key = '3001234' AND revision = '02'")
    offers.apply_corrections(con, _corrections(tmp_path, "9999999;-;-;status;won;;JM;2026-10-16",
                                               "3001234;02;-;ignore;yes;;JM;2026-10-16"))
    offers.write_review(con, str(tmp_path / "review.csv"))
    rows = (tmp_path / "review.csv").read_text(encoding="utf-8-sig").splitlines()

    def issue(name, start=""):
        return [r for r in rows if f";{name};" in r and r.startswith(start)]

    machine = issue("unknown machine name", "0904099;V1;")
    assert len(machine) == 1 and ";'=cmd|x, type X with robots;" in machine[0] and machine[0].endswith(";2")
    assert issue("stale correction", "9999999;") and issue("no offer document", "3007777;")
    assert "model unavailable" in issue("no price section", "0904023;")[0]  # the closed port: the model was not asked
    assert "sum check failed | model unavailable | low confidence | read by rules" in issue("model unavailable",
                                                                                         "3002222;00;")[0]
    assert issue("sum mismatch", "3006666;00;")
    assert issue("low confidence", "B-250306;V1;") and not [r for r in rows if r.startswith("3001234;02;")]  # ignored
    assert issue("unread document", "3003333;") and len(issue("unread document", "3004444;")) == 2
    assert all(len(r.split(";")) == 7 for r in rows)
    assert not issue("model unavailable", "0904023;")  # one issue per revision document


def test_run_offers_links_and_writes_the_review(offers_world, tmp_path):
    cfg, con = offers_world
    pdb = make_projects_db(tmp_path, {("THOR", r"THOR\0_Vente\Offre Paloma 2R.pdf"): "Offre 3001234"})
    stats = offers.run_offers(cfg, projects_db=pdb, review=True, progress=quiet)
    assert con.execute("SELECT status FROM offers WHERE offer_key='3001234'").fetchone()[0] == "won"
    assert stats.won == 1 and "1 won" in stats.summary()
    assert (cfg.world_dir / "review.csv").read_text(encoding="utf-8-sig").startswith("offer;revision;client;issue;")


def test_review_not_written_keeps_the_run(offers_world, monkeypatch):
    cfg, _ = offers_world

    def locked(con, path):
        raise PermissionError(13, "Permission denied", path)

    monkeypatch.setattr(offers, "write_review", locked)  # review.csv open in Excel
    messages = []
    stats = offers.run_offers(cfg, review=True, progress=messages.append)
    assert stats.offers == 8 and any(m.startswith("review.csv not written: ") for m in messages)


def test_link_projects_relative_and_unc_paths(offers_world, tmp_path, monkeypatch):
    _, con = offers_world
    pdb = make_projects_db(tmp_path, {("THOR", r"THOR\0_Vente\Offre Paloma 2R.pdf"): "Offre 3001234"})
    monkeypatch.chdir(tmp_path)
    assert offers.link_projects(con, os.path.relpath(pdb)) == 1
    drive, rest = os.path.splitdrive(pdb)
    unc = r"\\localhost" + "\\" + drive[0] + "$" + rest  # the drive's administrative share
    if not os.path.isfile(unc):
        pytest.skip("no administrative share on this machine")
    offers.link_projects(con, None)
    assert offers.link_projects(con, unc) == 1


def test_corrections_name_a_variant(offers_world, tmp_path):
    _, con = offers_world
    msgs = offers.apply_corrections(con, _corrections(tmp_path, "3001234;03/vandelay;1;Amount:chf;170000;;JM;2026-10-16",
                                                      "3001234;03;1;kind;option;;JM;2026-10-16"))
    assert msgs == ["correction for offer 3001234: revision '03' has variants Vandelay, Wonka: write 03/<variant>"]
    lines = dict(con.execute("SELECT r.variant, l.amounts FROM offer_lines l JOIN offer_revisions r "
                             "ON r.id = l.revision_id WHERE r.offer_key='3001234' AND r.revision='03' AND position=1"))
    assert json.loads(lines["Vandelay"]) == {"CHF": 170000.0} and json.loads(lines["Wonka"]) == {"CHF": 160000.0}
    assert con.execute("SELECT revision FROM offer_corrections WHERE applied").fetchone()[0] == "03/vandelay"


def test_ansi_corrections_file(offers_world, tmp_path):
    _, con = offers_world
    p = tmp_path / "corrections.csv"
    p.write_text("offer;revision;line;field;value;note;who;date\n"
                 "3001234;-;-;title;Ligne 5 améliorée;révisé;JM;2026-10-16\n", encoding="cp1252")
    assert offers.apply_corrections(con, str(p)) == []
    assert con.execute("SELECT title FROM offers WHERE offer_key='3001234'").fetchone()[0] == "Ligne 5 améliorée"


def test_date_correction_on_an_offer_without_date(offers_world, tmp_path):
    _, con = offers_world
    con.execute("UPDATE offer_revisions SET date = NULL, date_source = NULL WHERE offer_key = '3003333'")
    con.execute("UPDATE offers SET first_date = NULL, last_date = NULL, year = NULL, status = 'unknown', "
                "status_source = 'none', status_evidence = 'no date' WHERE offer_key = '3003333'")
    assert offers.apply_corrections(con, _corrections(tmp_path, "3003333;00;-;date;01.02.2019;;JM;2026-10-16")) == []
    o = con.execute("SELECT status, status_source, year FROM offers WHERE offer_key='3003333'").fetchone()
    assert tuple(o) == ("no_order_found", "age", 2019)


# --- Final fix wave: archive versions (R30), offer-like files (R29) ------------------------------------

def _review_rows(con, tmp_path):
    offers.write_review(con, str(tmp_path / "review.csv"))
    return (tmp_path / "review.csv").read_text(encoding="utf-8-sig").splitlines()


def test_archive_versions_in_sibling_folders_are_one_offer(versions_world, tmp_path):
    _, con = versions_world
    o = con.execute("SELECT folder, duplicate_of, last_date FROM offers WHERE offer_key='B-250306'").fetchone()
    assert o["duplicate_of"] is None and o["folder"].endswith(r"Dupont Biscuits\B-250306 V2")
    assert o["last_date"] == "2006-05-02"
    revs = con.execute("SELECT revision, path FROM offer_revisions WHERE offer_key='B-250306' "
                       "ORDER BY rev_order").fetchall()
    assert [r["revision"] for r in revs] == ["V1", "V2"] and revs[1]["path"].endswith("Dupont B-250306 V2.pdf")
    assert not [r for r in _review_rows(con, tmp_path) if ";duplicate offer number;" in r]


def test_only_the_files_carrying_the_number_are_read_in_a_revision(versions_world, tmp_path):
    _, con = versions_world
    revs = con.execute("SELECT revision, variant, path FROM offer_revisions WHERE offer_key='3002113'").fetchall()
    assert [(r["revision"], r["variant"]) for r in revs] == [("02", None)]
    assert revs[0]["path"].endswith("Angebot - 3002113_02.pdf")  # the supplier's quotation and the transport quote are not
    rows = _review_rows(con, tmp_path)
    assert not [r for r in rows if r.startswith("3002113;") and ";offer document without number;" in r]
    # Without a document carrying the number, the offer document is read and listed for review.
    r = con.execute("SELECT path, read_by FROM offer_revisions WHERE offer_key='3002114'").fetchone()
    assert r["path"].endswith(r"3002114\00\Devis.pdf") and r["read_by"] == "rules"
    flagged = [r for r in rows if r.startswith("3002114;00;") and ";offer document without number;" in r]
    assert len(flagged) == 1 and "Devis.pdf" in flagged[0]


# --- Final fix wave: model time limit, prompt cut ---------------------------------------------------------

def test_model_time_limit_still_writes_the_register(fake_ollama, offers_world_garbled, tmp_path):
    cfg, con = offers_world_garbled
    fake_ollama.reply = _price_answer
    cfg.vision_url = fake_ollama.url
    messages = []
    stats = offers.run_offers(cfg, max_minutes=1e-9, progress=messages.append)
    assert fake_ollama.calls == [] and stats.offers == 2 and stats.time_limited
    assert "time limit" in stats.summary() and any("Time limit" in m for m in messages)
    r = con.execute("SELECT read_by, note FROM offer_revisions WHERE path LIKE '%garbled%'").fetchone()
    assert r[0] == "none" and "model not asked: time limit" in r[1]
    flagged = [r for r in _review_rows(con, tmp_path) if r.startswith("3005555;") and ";model not asked;" in r]
    assert len(flagged) == 1
    stats = offers.run_offers(cfg, progress=quiet)  # without a limit: the model reads it
    assert stats.by_model == 1 and len(fake_ollama.calls) == 1 and not stats.time_limited


def test_cli_offers_max_minutes(tmp_path, monkeypatch, capsys):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f"[index]\ndata_dir = '{(tmp_path / 'data').as_posix()}'\n\n[worlds.offers]\nprofile = 'offers'\n",
                        encoding="utf-8")
    seen = {}

    def run(cfg, projects_db=None, review=False, max_minutes=None, progress=print):
        seen["max_minutes"] = max_minutes
        return offers.OfferStats()

    monkeypatch.setattr(offers, "run_offers", run)
    cli.main(["--config", str(cfg_file), "--world", "offers", "offers", "--max-minutes", "30"])
    assert seen["max_minutes"] == 30.0 and "Done in" in capsys.readouterr().out


def test_prompt_cut_is_its_own_review_issue(offers_world, tmp_path):
    _, con = offers_world
    con.execute("UPDATE offer_revisions SET sum_ok = 0, confidence = 'low', note = 'sum check failed; model error: "
                "prompt cut: 8100 tokens read of a 8192 token window' WHERE offer_key = '3006666'")
    rows = [r for r in _review_rows(con, tmp_path) if r.startswith("3006666;00;")]
    assert len(rows) == 1 and ";prompt too long for the model;" in rows[0] and "prompt cut" in rows[0]


# --- fixes from the 30-offer spot check (2026-10-19) -----------------------------------------------------

def _doc(path, id, revision="01", ext=None, status="ok", doc_type="offer", mtime=0):
    name = os.path.basename(path)
    return {"id": id, "path": path, "name": name, "ext": ext or os.path.splitext(name)[1].lower(), "status": status,
            "doc_type": doc_type, "revision": revision, "mtime": mtime, "error": None}


REV = r"C:\Clients\ACME Foods\Offres\3001234 - Line 5\01"


def test_mails_internal_sheets_and_old_copies_lose_to_the_quotation():
    docs = [_doc(REV + r"\3001234_01_Prices summary.docx", 1), _doc(REV + r"\3001234_01_Summary for Sales Nego.docx", 2),
            _doc(REV + r"\3001234_01_Layout & Price.pdf", 7, mtime=5), _doc(REV + r"\3001234_01_Quotation.pdf", 3, mtime=9),
            _doc(REV + r"\3001234_01_Quotation.docx", 4), _doc(REV + r"\ACME - Offre 3001234-01.msg", 5),
            _doc(REV + r"\OLD\3001234_01_Quotation.pdf", 6), _doc(REV + r"\Copie de 3001234_01_Quotation.pdf", 8),
            _doc(REV + r"\Copie (2) de 3001234_01_Quotation.pdf", 9)]
    got = sorted(offers._candidates(docs, "3001234"), key=offers._doc_order)
    assert [d["id"] for d in got] == [3, 7]  # the quotation first, then the other customer document
    only = [_doc(REV + r"\3001234_01_Prices summary.docx", 1), _doc(REV + r"\ACME - Offre 3001234-01.msg", 5)]
    assert [d["id"] for d in offers._candidates(only, "3001234")] == [1]
    mail = [_doc(REV + r"\ACME - Offre 3001234-01.msg", 5)]
    assert [d["id"] for d in offers._candidates(mail, "3001234")] == [5]  # listed, never read for prices
    assert offers._twins(docs) == {3: docs[4]}  # the Word document of the PDF, read when the PDF is not


class _NoModel:
    def read(self, pages):
        return None, "model unavailable"


def _pages_db(texts):
    import sqlite3
    con = sqlite3.connect(":memory:")
    con.executescript("CREATE TABLE chunks (id INTEGER PRIMARY KEY, doc_id INTEGER, page_no INTEGER, seq INTEGER, "
                      "text TEXT); CREATE TABLE picture_uses (doc_id INTEGER, chunk_id INTEGER); "
                      "CREATE TABLE ocr_pages (doc_id INTEGER, page_no INTEGER, status TEXT);")
    for doc_id, pages in texts.items():
        for p, text in pages.items():
            con.execute("INSERT INTO chunks(doc_id, page_no, seq, text) VALUES (?, ?, 0, ?)", (doc_id, p, text))
    return con


def test_a_scanned_pdf_gives_way_to_its_word_document_and_mails_are_no_price_documents():
    pdf, word = _doc(REV + r"\3001234_01.pdf", 1), _doc(REV + r"\3001234_01.doc", 2)
    con = _pages_db({1: {1: "Offre budgétaire n° 3001234 Version 1\nACME Foods", 2: "PRIX\nEquipement CHF\nMise en service CHF"},
                     2: {1: "ACME Foods", 2: "Pos | Désignation | Qté | Total CHF\n1 | Cellule Paloma 4R | 1 | 300'000.00\n"
                                            "2 | Mise en service | 1 | 20'000.00\n| Total | | CHF | 320'000.00"},
                     3: {1: "From: ACME\nSent: 12.03.2020\nOur last price for the Paloma 4R is CHF 280'000.-, "
                            "installation CHF 18'000.-, total price CHF 298'000.-"}})
    r = offers._read(con, pdf, _NoModel(), [], twin=word)
    assert r.doc["id"] == 2 and r.read_by == "rules" and r.section.sum_ok is True and r.budget is True
    mail = offers._read(con, _doc(REV + r"\ACME - Offre 3001234-01.msg", 3), _NoModel(), [])
    assert mail.section is None and mail.read_by == "none" and mail.notes == ["mail: not a price document"]
    assert (mail.date, mail.date_source) == ("2020-03-12", "mail")


def test_a_word_twin_with_nothing_priced_never_beats_a_priced_pdf():
    pdf, word = _doc(REV + r"\3001234_01.pdf", 1), _doc(REV + r"\3001234_01.doc", 2)
    pdf_page = ("PRIX\nEquipement EUR 101'500.-- CHF 120'000.--\nJours supplémentaires CHF 1'100.- par jour\n"
                "Prix total net HT CHF 125'000.--")
    word_page = ("# PRIX\nEquipement | (EUR | 101'500.--) | CHF | 120'000.--\n"
                 "Un Paloma 4R incluant 1 jour de FAT (Jours additionnels 1100 CHF/Jours) |\n"
                 "Emballage, transport et assurance | Inclus\nMise en service | Inclus\n")
    con = _pages_db({1: {1: "ACME Foods", 2: pdf_page}, 2: {1: "ACME Foods", 2: word_page}})
    r = offers._read(con, pdf, _NoModel(), [], twin=word)
    assert r.doc["id"] == 1 and r.read_by == "rules" and r.section.sum_ok is False  # the twin: no total, not better
    assert r.notes == ["sum check failed", "model unavailable"]  # the model is still asked
    assert prices.amounts_in("(Jours additionnels 1100 CHF/Jours)") == []  # a day rate, also in the plural
    twin = prices.parse_section({2: word_page})
    assert twin.lines[0].amounts == {"EUR": 101500.0, "CHF": 120000.0}  # currency and amount in separate cells
    only_included = prices.parse_section({2: "PRIX\nEmballage, transport et assurance € Inclus\nMise en service € Inclus\n"
                                             "Une note de crédit de CHF 500.- sera émise pour les 4 heures de formation "
                                             "données lors de la mise en service."})
    assert only_included is None  # nothing priced and no total: no price section (R38)


def test_a_model_answer_cached_for_the_earlier_page_choice_still_serves():
    from types import SimpleNamespace
    from dmx_docs import prices
    pages = {2: "1x Paloma 7R CHF 680,000.-\n1 Output conveyor CHF 68,000.-",
             3: "Packaging CHF 9,700.-\nTOTAL PRICE, net, without taxes, CHF 757,700.-"}
    assert prices.price_pages(pages) == [2, 3] and prices.price_pages(pages, legacy=True) == [3]
    con = _pages_db({})
    offers._create(con)
    cfg = SimpleNamespace(vision_model="test-vision", vision_url=closed_port_url(), vision_timeout_s=10)
    model = offers._Model(con, cfg, quiet)
    answer = prices.PriceSection([prices.PriceLine(1, "Packaging", "service", None, {"CHF": 9700.0}, 3)],
                                 {"CHF": 757700.0}, [], [3], False)
    con.execute("INSERT INTO offer_model_cache VALUES (?, ?)", (model._key(pages, legacy=True), offers._dump(answer)))
    section, problem = model.read(pages)
    assert problem is None and section.lines[0].amounts == {"CHF": 9700.0} and not model.checked


def test_country_skips_the_sellers_address():
    assert offers._country_in("ACME Foods\nBOSCH PACKAGING TECHNOLOGY SA ♦ Chemin des Mésanges 3 ♦ 1032 Romanel-sur-"
                              "Lausanne ♦ Switzerland") is None
    assert offers._country_in("Pays\nCh. des Mésanges 3, CH-1032 Romanel Page 1 sur 11\nSwitzerland 12/03/09") is None
    assert offers._country_in("ACME Foods Laisves 12 Vilnius Lithuania | Why Demaurex SA? Over 30 years") == "Lithuania"
    assert offers._country_in("Globex GmbH\nBerlin\nGermany\nDemaurex SA, Route 1, 1030 Romanel, Switzerland") == "Germany"


def test_a_swiss_clients_address_is_not_the_seller():
    assert offers._country_in("ACME Foods SA\nRue du Lac 1, CH-1000 Lausanne\nTel. +41 21 000 00 00\nSwitzerland") \
        == "Switzerland"


def test_documents_marked_as_not_sent_lose_to_the_offer():
    docs = [_doc(REV + r"\Pas envoyé 3001234_01.doc", 1), _doc(REV + r"\3001234_01 not sent.pdf", 2),
            _doc(REV + r"\3001234_01 annulé.pdf", 3), _doc(REV + r"\3001234_01.pdf", 4)]
    assert [d["id"] for d in offers._candidates(docs, "3001234")] == [4]


def test_country_from_the_last_revision_first():
    from types import SimpleNamespace as R
    revisions = [(None, 0, R(pages={1: "Supplier quote to Demaurex SA, Vufflens-la-Ville\nGlobex AG\nZürich, Switzerland"},
                             other={})),
                 ("Q06", 7, R(pages={1: "ACME Foods Vilnius Lithuania"}, other={}))]
    assert offers._document_country(revisions) == "Lithuania"


# --- fixes from the fresh 30-offer spot check (2026-10-25) --------------------------------------------------------

def test_word_twin_whose_name_differs_only_by_separators():
    pdf, word = _doc(REV + r"\F-3001234_01-L5.pdf", 1), _doc(REV + r"\F-3001234 01 - L5.doc", 2)
    other = _doc(REV + r"\F-3001234 01 - L6.doc", 3)
    assert offers._twins([pdf, word, other]) == {1: word}
    assert [d["id"] for d in offers._candidates([pdf, word, other], "3001234")] == [1, 3]


def test_the_price_document_is_read_before_a_drawing():
    docs = [_doc(REV + r"\3001234-sticks .pdf", 1, mtime=1), _doc(REV + r"\Layout & Price - 3001234_01.docx", 2, mtime=9)]
    assert [d["id"] for d in sorted(docs, key=offers._doc_order)] == [2, 1]


def test_a_price_page_without_amounts_is_named():
    pdf = _doc(REV + r"\3001234_01.pdf", 1)
    con = _pages_db({1: {1: "ACME Foods", 13: "9.6. Price, net, without taxes, EXW Romanel (INCOTERMS 2000)\n"
                                              "Three robotic cells\nInstallation and setup\nNot included"}})
    r = offers._read(con, pdf, _NoModel(), [])
    assert r.section is None and r.notes[0] == "no price section: price page 13 has no amounts in its text"


def test_cached_model_lines_are_classified_again():
    from types import SimpleNamespace
    pages = {1: "garbled 400000 10000 1 Case erector ACME 98000"}
    con = _pages_db({})
    offers._create(con)
    cfg = SimpleNamespace(vision_model="test-vision", vision_url=closed_port_url(), vision_timeout_s=10)
    model = offers._Model(con, cfg, quiet)
    answer = prices.PriceSection([prices.PriceLine(1, "Emballage", "other", None, {"CHF": 10000.0}, 1),
                                  prices.PriceLine(2, "1 Case erector ACME - hotmelt - 20 cpm", "other", None,
                                                   {"CHF": 98000.0}, 1),
                                  prices.PriceLine(3, "Spare parts", "other", None, {"CHF": 400000.0}, 1)],
                                 {}, [], [1], None)
    con.execute("INSERT INTO offer_model_cache VALUES (?, ?)", (model._key(pages), offers._dump(answer)))
    section, _ = model.read(pages)
    assert [l.kind for l in section.lines] == ["service", "machine", "other"]


def test_country_skips_the_sellers_trademark_line_and_reads_provinces():
    assert offers._country_in("Globex Europe Limited\nBOSCH is a registered trademark of Robert Bosch GmbH Germany") is None
    assert offers._country_in("ACME LLC - Plant 12 Maple Road Brookville X1Y 2Z3 Quebec CA | Why Demaurex SA?") == "Canada"
    assert offers._country_in("Globex Industrial str. 4 Northern district Russian Federation") == "Russia"


def test_a_word_twin_with_a_checked_sum_beats_a_pdf_without_total():
    pdf, word = (_doc(REV + r"\Globex _ACME_ 3001234 V1.pdf", 1, mtime=5), _doc(REV + r"\Globex (ACME) 3001234 V1.doc", 2,
                                                                               mtime=9))
    con = _pages_db({1: {1: "ACME Foods", 2: "PRIX\nEquipement CHF 300'000.--\nMise en service CHF 20'000.--\n"
                                            "Prix total, net, hors taxe\n320'000.--"},
                     2: {1: "ACME Foods", 2: "PRIX\nEquipement CHF 300'000.--\nMise en service CHF 20'000.--\n"
                                            "Prix total, net, hors taxe CHF 320'000.--",
                         9: "Validité | Cette offre budgétaire vous est fournie afin de servir de base de discussion."}})
    r = offers._read(con, pdf, _NoModel(), [], twin=word)
    assert r.doc["id"] == 2 and r.section.sum_ok is True and r.budget is True and r.notes == []
    con.execute("UPDATE chunks SET text = 'PRIX\nEquipement CHF 300''000.--\nMise en service CHF 20''000.--\n"
                "Prix total, net, hors taxe CHF 320''000.--' WHERE doc_id = 1 AND page_no = 2")
    r = offers._read(con, pdf, _NoModel(), [], twin=word)
    assert r.doc["id"] == 1 and r.section.sum_ok is True and r.budget is False  # the PDF is checked: kept, and its own
    # wording decides


def test_a_word_twin_with_other_amounts_never_replaces_the_pdf():  # R46
    pdf, word = _doc(REV + r"\3001234_00_Quotation.pdf", 1, mtime=5), _doc(REV + r"\3001234_00_Quotation.docx", 2, mtime=9)
    con = _pages_db({1: {1: "ACME Foods", 2: "PRIX\n5x Paloma 4R CHF 3'300'000.--\nMise en service CHF 100'000.--"},
                     2: {1: "ACME Foods", 2: "PRIX\n2x Paloma 4R CHF 3'340'000.--\nMise en service CHF 100'000.--\n"
                                            "Prix total, net, hors taxe CHF 3'440'000.--"}})
    r = offers._read(con, pdf, _NoModel(), [], twin=word)
    assert r.doc["id"] == 1 and "Word twin differs from the PDF" in r.notes
    assert r.section.lines[0].amounts == {"CHF": 3300000.0}


def test_a_word_twin_reading_fewer_priced_lines_never_replaces_the_pdf():
    pdf, word = _doc(REV + r"\3001234_00.pdf", 1), _doc(REV + r"\3001234_00.doc", 2)
    con = _pages_db({1: {2: "PRIX\nCellule Paloma 4R CHF 300'000.--\nOption A vision CHF 20'000.--\nOption B CHF 10'000.--"},
                     2: {2: "PRIX\nCellule Paloma 4R CHF 300'000.--\nPrix total, net, hors taxe CHF 300'000.--"}})
    r = offers._read(con, pdf, _NoModel(), [], twin=word)
    assert r.doc["id"] == 1 and "Word twin reads fewer priced lines than the PDF" in r.notes


def test_a_pdf_without_amounts_gives_way_to_a_later_word_twin_with_a_note():
    pdf, word = _doc(REV + r"\3001234_00.pdf", 1, mtime=5), _doc(REV + r"\3001234_00.doc", 2, mtime=9)
    con = _pages_db({1: {2: "9.6. Price, net, without taxes\nThree robotic cells\nInstallation"},
                     2: {2: "PRIX\nCellule Paloma 4R CHF 300'000.--\nPrix total, net, hors taxe CHF 300'000.--"}})
    r = offers._read(con, pdf, _NoModel(), [], twin=word)
    assert r.doc["id"] == 2 and r.notes == ["read from the Word twin, saved after the PDF"]


def test_a_formal_variant_decides_the_offer_type_for_itself():  # R45
    from types import SimpleNamespace as R
    budget, formal = R(budget=True, formal=False), R(budget=False, formal=True)
    assert offers._offer_type([budget, formal]) == "firm"
    assert offers._offer_type([budget, R(budget=False, formal=False)]) == "budget"


def test_the_country_of_a_pdf_whose_word_twin_was_read():
    pdf, word = _doc(REV + r"\3001234_01.pdf", 1), _doc(REV + r"\3001234_01.doc", 2)
    con = _pages_db({1: {1: "ACME Foods\nRue du Port 1\n75000 Paris\nFrance", 2: "PRIX\nEquipement CHF 300'000.--"},
                     2: {1: "Offre 3001234", 2: "PRIX\nEquipement CHF 300'000.--\nPrix total, net, hors taxe CHF 300'000.--"}})
    r = offers._read(con, pdf, _NoModel(), [], twin=word)
    assert r.doc["id"] == 2 and offers._document_country([("01", 0, r)]) == "France"


def test_a_copy_without_prices_is_not_named_as_prices():
    docs = [_doc(REV + r"\Globex 3001234 V4 sans prix.pdf", 1, mtime=1), _doc(REV + r"\Globex 3001234 V4.doc", 2, mtime=9),
            _doc(REV + r"\Prix 3001234 V4.pdf", 3, mtime=5)]
    assert [d["id"] for d in sorted(docs, key=offers._doc_order)] == [3, 1, 2]

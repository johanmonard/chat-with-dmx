import os
from pathlib import Path

import pytest

from conftest import make_pdf
from dmx_docs import facets, offerkeys, store
from dmx_docs.config import load_config
from dmx_docs.indexer import run_index

R = r"\\srv\Daten$\RMA_ORG\SAL\Clients"
quiet = lambda *a, **k: None  # noqa: E731


def f(rel):
    return facets.compute_offers(os.path.join(R, rel), R)


@pytest.mark.parametrize("name, key", [
    ("3002113 - Line 5 Lyon (small)", "3002113"), ("3001227 (ATLAS)", "3001227"), ("0909009", "0909009"),
    ("1106033-A", "1106033-A"), ("518528328917 - CPL03 - Intervention", "518528328917"),
    ("B-250306 V1", "B-250306"), ("F-1103018 V1", "1103018"), ("ACME Foods B-1110021 V1.pdf", "1110021"),
    ("Angebot - 3002113_02.pdf", "3002113"), ("Offre_5034569_01.pdf", "5034569"),
    ("3006904-CPL01-Q01_.pdf", "3006904"),  # the letter after the dash is not a suffix when more follows
    ("PRJ-2014-02259 Mars France Steinburg (Barres glacées)", "PRJ-2014-02259"),
    ("PRJ-2016-04753_Cerealis Portugal", "PRJ-2016-04753"), ("PRJ-2014-02259-Q01.pdf", "PRJ-2014-02259"),
    ("Layout PRJ-2014-02259-Q01.pdf", "PRJ-2014-02259"), ("PRJ-2013- 05150 (Sanitarium, Australia)", "PRJ-2013-05150"), ("prj-2013-03403", "PRJ-2013-03403"),
    ("NNNNNNN-DESCRIPTION", None), ("YYMMNNN", None), ("Specification", None), ("Paloma 2R WD _JLT", None),
])
def test_offer_key(name, key):
    assert offerkeys.offer_key(name) == key


@pytest.mark.parametrize("name, rev", [
    ("00", ("00", None)), ("02", ("02", None)), ("V3", ("V3", None)), ("Q01", ("Q01", None)),
    ("04 - Vandelay version", ("04", "Vandelay version")), ("10 - offre formelle Rotzinger", ("10", "offre formelle Rotzinger")),
    ("History", None), ("Specification", None), ("Layout", None), ("Mails", None),
])
def test_revision_of(name, rev):
    assert offerkeys.revision_of(name) == rev


def test_revision_order():
    labels = ["V2", "00", "10", "V1", "02"]
    assert sorted(labels, key=offerkeys.revision_order)[-1] in ("10", "V2")  # V-labels and numbered labels never mix in one offer
    assert sorted(["00", "10", "02"], key=offerkeys.revision_order) == ["00", "02", "10"]
    assert sorted(["V2", "V10", "V1"], key=offerkeys.revision_order) == ["V1", "V2", "V10"]


@pytest.mark.parametrize("name, country", [
    ("Allemagne", "Germany"), ("Deutschland", "Germany"), ("Germany", "Germany"), ("UK", "United Kingdom"),
    ("TURQUIE", "Turkey"), ("Pays-Bas", "Netherlands"), ("Österreich", "Austria"), ("Dupont Biscuits", None),
    ("CYPRUS", "Cyprus"), ("Chypre", "Cyprus"), ("Iran", "Iran"), ("Koweit", "Kuwait"), ("Venezuela", "Venezuela"),
])
def test_country_en(name, country):
    assert offerkeys.country_en(name) == country


def test_client_offer_revision():
    got = f(r"ACME Foods\Offres\3002113 - Line 5\02\Angebot - 3002113_02.pdf")
    assert (got["category"], got["client"], got["site"], got["offer"], got["revision"], got["doc_type"]) == \
           ("client", "ACME Foods", None, "3002113", "02", "offer")


def test_project_numbered_offers_of_2013_2017():
    got = f(r"Mars\Offres\PRJ-2014-02259 Mars France Steinburg (Barres glacées)\Q01\PRJ-2014-02259-Q01.pdf")
    assert (got["client"], got["site"], got["offer"], got["revision"], got["doc_type"]) == \
           ("Mars", None, "PRJ-2014-02259", "Q01", "offer")
    # the number twice, under an OLD folder: the revision is the folder under the inner one
    got = f(r"PANT\Offres\OLD\PRJ-2016-04753_Cerealis Portugal\PRJ-2016-04753\Q07\PRJ-2016-04753-Q07-Signed.pdf")
    assert (got["client"], got["site"], got["offer"], got["revision"]) == ("PANT", None, "PRJ-2016-04753", "Q07")
    # a supplier quote filed inside the project folder belongs to it
    assert f(r"Mars\Offres\PRJ-2014-02259 Mars\Q01\Quote 5024692.pdf")["offer"] == "PRJ-2014-02259"
    # the revision from the file name when no folder carries it
    assert f(r"Codan\Offres\PRJ-2015-00954 Codan\PRJ-2015-00954-Q02.pdf")["revision"] == "Q02"


def test_site_and_kinds():
    assert f(r"ACME Foods\ACME Polska\Offres\3006464\00\x.pdf")["site"] == "ACME Polska"
    assert f(r"ACME Foods\Offres\3002113\02\Mail\FW Projekt.msg")["doc_type"] == "mail"
    assert f(r"ACME Foods\Offres\0904023\Specification\Cahier.pdf")["doc_type"] == "specification"
    assert f(r"ACME Foods\Offres\0904023\V1\ACME - Solution_Technique_B-0904023 V1.pdf")["doc_type"] == "technical_solution"
    assert f(r"ACME Foods\Offres\3002113\02\Besuchsbericht ACME.docx")["doc_type"] == "visit_report"
    assert f(r"ACME Foods\Offres\3002113\02\Layout\General view.pdf")["doc_type"] == "layout"


def test_archive():
    got = f(r"01_Historique\PROSPECT\Allemagne\Dupont Biscuits\B-250306 V1\Dupont B-250306 V1.doc")
    assert (got["category"], got["country"], got["client"], got["offer"], got["revision"]) == \
           ("archive", "Germany", "Dupont Biscuits", "B-250306", "V1")
    assert got["doc_type"] == "offer"  # a file in the offer folder whose name carries the offer key


def test_archive_client_after_a_country_folder_the_table_does_not_know():
    got = f(r"01_Historique\PROSPECT\Scandinavia\Dupont Biscuits\B-250306 V1\Dupont B-250306 V1.doc")
    assert (got["category"], got["country"], got["client"], got["offer"]) == \
           ("archive", None, "Dupont Biscuits", "B-250306")


def test_partners_and_unknown_client():
    got = f(r"07_Offres_Wyzo\Globex\Offres\3001264 - WYZO\00\Offer.pdf")
    assert (got["category"], got["partner"], got["client"], got["offer"]) == ("partner", "Wyzo", "Globex", "3001264")
    got = f(r"06_Offres IPSO\3006904 - CPL01 - MES\Q01\3006904-CPL01-Q01_.pdf")
    assert (got["category"], got["partner"], got["client"], got["offer"], got["revision"]) == \
           ("partner", "IPSO", None, "3006904", "Q01")
    got = f(r"08_Offres client inconnu\Paloma 2R WD _JLT\00\PALOMA 2R WD_01_Layout & Price.pdf")
    assert (got["category"], got["client"], got["machine"]) == ("unknown_client", None, "|Paloma|Paloma 2R|")


def test_other_categories():
    assert f(r"09_MarketStudy\PL\Study.pdf")["category"] == "market_study"
    assert f(r"03_Accord de confidentialité\ACME NDA.pdf")["category"] == "nda"
    assert f(r"04_Infos pratiques\Conditions de vente Swissmem\CG.pdf")["category"] == "sales_info"


def test_no_category_without_a_root_or_a_folder():
    assert facets.compute_offers(r"\\srv\Old\a.pdf", "") == {}
    assert facets.compute_offers(os.path.join(R, "a.pdf"), R) == {}


def test_corrections_path_only_for_the_offers_profile(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[index]\ndata_dir = 'data'\n[worlds.offers]\nprofile = 'offers'\n"
                        "[worlds.projects]\nprofile = 'projects'\n", encoding="utf-8")
    assert load_config(cfg_file, world="offers").corrections_path == str(tmp_path / "data" / "offers" / "corrections.csv")
    assert load_config(cfg_file, world="projects").corrections_path is None
    cfg_file.write_text("[index]\ndata_dir = 'data'\n[worlds.offers]\nprofile = 'offers'\n"
                        "corrections = 'C:/elsewhere/c.csv'\n", encoding="utf-8")
    assert load_config(cfg_file, world="offers").corrections_path == str(Path("C:/elsewhere/c.csv"))


def test_index_stores_offer_facets_and_recomputes_on_profile_change(tmp_path, make_cfg):
    root = tmp_path / "Clients"
    folder = root / "ACME Foods" / "Offres" / "3002113 - Line 5" / "02"
    folder.mkdir(parents=True)
    make_pdf(str(folder / "Angebot - 3002113_02.pdf"), ["Paloma 3R packaging line."])
    cfg = make_cfg(root, profile="offers", world="offers", extensions=[".pdf"])
    run_index(cfg, progress=quiet)
    con = store.connect(cfg.db_path)
    query = "SELECT client, offer, revision, doc_type, category, machine FROM doc_facets"
    assert store.get_meta(con, "facets_version") == "offers:4"
    assert [tuple(r) for r in con.execute(query)] == [("ACME Foods", "3002113", "02", "offer", "client", None)]
    # Another profile's rules, then the offers rules again: the rows are recomputed.
    store.set_meta(con, "facets_version", "marketing:1")
    assert facets.refresh(con, cfg.roots, "offers") == 1
    assert store.get_meta(con, "facets_version") == "offers:4"
    row = tuple(con.execute("SELECT client, offer FROM doc_facets").fetchone())
    assert row == ("ACME Foods", "3002113")
    assert facets.refresh(con, cfg.roots, "offers") == 0
    con.close()


def test_refresh_adds_the_offer_columns_to_an_older_table(tmp_path):
    con = store.connect(tmp_path / "old.sqlite3")
    con.executescript("CREATE TABLE doc_facets (doc_id INTEGER PRIMARY KEY, project TEXT, collection TEXT, "
                      "section TEXT, doc_type TEXT, facet_source TEXT, subproject TEXT, category TEXT, language TEXT)")
    facets.refresh(con, [], "offers")
    cols = {r[1] for r in con.execute("PRAGMA table_info(doc_facets)")}
    assert {"client", "site", "country", "partner", "offer", "revision", "machine"} <= cols
    con.close()


def test_file_name_revision_only_for_the_offers_own_file():
    # A number at the end of another document's name is not a revision of the offer.
    for rel in (r"ACME Foods\Offres\0904023\Specification\Cahier_03.pdf", r"ACME Foods\Offres\3002113\Mail\Mail_15.msg",
                r"ACME Foods\Offres\3002113\Layout\Plan_20.pdf"):
        assert f(rel)["revision"] is None
    got = f(r"ACME Foods\Offres\3002113\Angebot - 3002113_02.pdf")
    assert (got["offer"], got["revision"], got["doc_type"]) == ("3002113", "02", "offer")


def test_no_kind_evidence_means_no_facet_source():
    assert f(r"ACME Foods\Offres\3002113\02\Notes.pdf")["facet_source"] is None
    assert f(r"ACME Foods\Offres\3002113\02\Angebot.pdf")["facet_source"] == "name"
    assert f(r"ACME Foods\Offres\3002113\Specification\Notes.pdf")["facet_source"] == "folder"


def test_empty_corrections_setting_disables_the_path(tmp_path):
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("[index]\ndata_dir = 'data'\n[worlds.offers]\nprofile = 'offers'\ncorrections = ''\n",
                        encoding="utf-8")
    assert load_config(cfg_file, world="offers").corrections_path is None


@pytest.mark.parametrize("rel, revision", [
    (r"01_Historique\PROSPECT\SUISSE\ACME\Line 4\B 250001 V1\B-250001 V1.pdf", "V1"),  # the folder name lost its dash
    (r"01_Historique\PROSPECT\France\Dupont Biscuits\250002\B-250002 V2.doc", "V2"),  # a bare-number folder
    (r"01_Historique\PROSPECT\France\Dupont Biscuits\B-250003 - Madeleine\B-250003 V4\B-250003 V4.pdf", "V4"),
])
def test_the_offers_own_file_is_an_offer_wherever_it_lies(rel, revision):
    got = f(rel)
    assert (got["doc_type"], got["revision"]) == ("offer", revision)


def test_other_files_naming_the_offer_stay_other():
    assert f(r"01_Historique\PROSPECT\France\Dupont Biscuits\250002\Notes B-250002.pdf")["doc_type"] == "other"
    assert f(r"01_Historique\PROSPECT\France\Dupont Biscuits\250002\Layouts\B-250002 V2.pdf")["doc_type"] == "layout"


def test_a_version_after_a_dash_in_the_file_name_is_the_revision():
    got = f(r"01_Historique\PROSPECT\Italie\Globex\250004\B-250004-V2.doc")
    assert (got["offer"], got["revision"], got["doc_type"]) == ("B-250004", "V2", "offer")
    assert f(r"ACME Foods\Offres\0904023\0904023-V1.PDF")["revision"] == "V1"


def test_unkeyed_offer_documents_are_listed(tmp_path):
    import sqlite3
    from dmx_docs import offers

    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    con.executescript("CREATE TABLE docs(id INTEGER PRIMARY KEY, path TEXT, ext TEXT);"
                      "CREATE TABLE doc_facets(doc_id INTEGER, category TEXT, client TEXT, offer TEXT, doc_type TEXT);")
    for i, (path, ext, cat, offer, kind) in enumerate([
            ("a/x.pdf", ".pdf", "client", None, "offer"), ("a/y.msg", ".msg", "archive", None, "offer"),
            ("a/z.pdf", ".pdf", "client", "3001234", "offer"), ("a/w.pdf", ".pdf", "client", None, "layout"),
            ("a/n.pdf", ".pdf", "nda", None, "offer")], 1):
        con.execute("INSERT INTO docs VALUES(?,?,?)", (i, path, ext))
        con.execute("INSERT INTO doc_facets VALUES(?,?,?,?,?)", (i, cat, "ACME", offer, kind))
    n, text = offers.write_unkeyed(con, str(tmp_path / "unkeyed.csv"))
    assert n == 2 and "client .pdf 1" in text and "archive .msg 1" in text
    assert (tmp_path / "unkeyed.csv").read_text(encoding="utf-8-sig").count("\n") == 3

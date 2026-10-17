"""The fictional Clients\\ tree of the offers world tests, its index and a tiny projects world
(shared by test_offers.py and test_offer_tools.py)."""

import os
import shutil
import socket
import time

from conftest import make_pdf
from dmx_docs import offers, store
from dmx_docs.config import Config
from dmx_docs.indexer import run_index

quiet = lambda *a, **k: None  # noqa: E731

EN_ITEMS = ("3. PRICE\nA Paloma 2R Top Loader cell including: robots, frame, HMI CHF {machine}\n"
            "Option: vision system CHF 25,000.00\nTraining (2 days) CHF 4,000.00\nDiscount -CHF {discount}\n"
            "(Additional days: 1100 CHF/Day)\nPRICE, net, without taxes, CHF {total}\n"
            "Only prices in Swiss Francs are valid (CHF). As indication, actual rate (November 1st, 2011) "
            "1 Euro = 1.20 CHF.")  # the PDF wraps this line after '=' (R19)
DE_ITEMS = ("3. PREIS\nEine Paloma 2R Top Loader Zelle einschliesslich: Roboter, Rahmen, HMI CHF {machine}\n"
            "Option: Visionsystem CHF 25,000.00\nSchulung (2 Tage) CHF 4,000.00\nRabatt -CHF {discount}\n"
            "PREIS, netto, ohne Steuern, CHF {total}")
DE_ARCHIVE = {
    2: "PREIS\n2x Paloma 3R einschliesslich jeweils:\n- 3 Delta Roboter\n(EUR 400 000.-)\nCHF 410 000.-\n"
       "Installation und Inbetriebnahme\n(EUR 50 000.-)\nCHF 51 000.-\n",
    3: "PREIS, netto, ohne Steuern,\n(EUR 450 000.-)\nCHF 461 000.-\n"
       "Nur Preise in Schweizer Franken (CHF) sind gültig. Richtwert ist der tatsächliche Tageskurs ist 1.00 EUR = 1,0244 CHF"}
GARBLED = "Price\nPaloma 4R cell Option vision Training\n180 000 25 000 4 000\nCHF CHF CHF\nTotal 209 000"
VOCABULARY = "## machines\n- Paloma 2R: Paloma D2 Top Loader with two Delta robots, Paloma D2 Top Loader\n"


def en(machine="180,000.00", discount="9,000.00", total="200,000.00"):
    return EN_ITEMS.format(machine=machine, discount=discount, total=total)


def pdf(root, rel, pages, mtime=None):
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    make_pdf(str(path), pages)
    if mtime:
        os.utime(path, (mtime, mtime))


def build_offers_corpus(root):
    """Clients\\ tree of the register tests (fictional clients)."""
    t0 = time.mktime((2021, 1, 1, 12, 0, 0, 0, 0, -1))
    line5 = r"ACME Foods\Offres\3001234 - Line 5"
    pdf(root, line5 + r"\00\Offer - 3001234_00.pdf", ["Lausanne, 10.02.2020\nOffer 3001234", en()])
    pdf(root, line5 + r"\01\Offer - 3001234_01.pdf",
        ["Lausanne, 03.03.2020\nOffer 3001234", en(discount="19,000.00", total="190,000.00")], t0)
    pdf(root, line5 + r"\01\Offer - 3001234_01_DE.pdf",  # the same offer in German: a translation
        ["Lausanne, 03.03.2020\nAngebot 3001234",
         DE_ITEMS.format(machine="180,000.00", discount="19,000.00", total="190,000.00")], t0 + 60)
    pdf(root, line5 + r"\02 - Vandelay version\Offer - 3001234_02.pdf",
        ["Lausanne, 20.04.2020\nOffer 3001234", en(discount="14,000.00", total="195,000.00")])
    pdf(root, line5 + r"\03\Offer - 3001234_03 Vandelay.pdf",
        ["Lausanne, 14.05.2020\nOffer 3001234", en()], t0)
    pdf(root, line5 + r"\03\Offer - 3001234_03 Wonka.pdf",
        ["Lausanne, 14.05.2020\nOffer 3001234", en(machine="160,000.00", total="180,000.00")], t0 + 60)
    pdf(root, r"ACME Foods\Offres\0904023\V1\ACME B-0904023 V1.pdf",
        ["ACME Foods Ltd\n12 Main Street\nLeeds\nUnited Kingdom\nTechnical description of the packaging cell"])
    pdf(root, r"ACME Foods\Offres\0904099\V1\ACME B-0904099 V1.pdf",
        ["Offer for a Paloma D2 Top Loader with two Delta robots",
         "PRICE\nPaloma D2 Top Loader cell CHF 150,000.00\nInstallation and commissioning CHF 10,000.00\n"
         "PRICE, net, without taxes, CHF 160,000.00"])
    pdf(root, r"ACME Foods\Offres\Line 7 proposal\00\Proposal.pdf", ["Proposal for line 7"])
    pdf(root, r"01_Historique\PROSPECT\Allemagne\Dupont Biscuits\B-250306 V1\Dupont B-250306 V1.pdf",
        ["Lausanne, 25.03.2006\nRichtpreis-Angebot B-250306", DE_ARCHIVE[2], DE_ARCHIVE[3]])
    pdf(root, r"Globex\Offres\3001234 - copy\00\Offer - 3001234_00.pdf",
        ["Lausanne, 10.02.2020\nOffer 3001234", en()])
    # An unread annex (older file, recent date of its own) next to the priced offer of the same revision.
    pdf(root, r"ACME Foods\Offres\3002222 - Line 9\00\Offer - 3002222_00 annex.pdf",
        ["Annex: technical data of the cell"], t0)
    pdf(root, r"ACME Foods\Offres\3002222 - Line 9\00\Offer - 3002222_00.pdf",
        ["Lausanne, 05.05.2019\nOffer 3002222", en()])
    # Offer documents the index could not read: a scan before OCR, a damaged file, a 0-byte file.
    pdf(root, r"ACME Foods\Offres\3003333 - Scan\00\Offer - 3003333_00.pdf", [""])
    (root / r"ACME Foods\Offres\3004444 - Broken\00").mkdir(parents=True)
    (root / r"ACME Foods\Offres\3004444 - Broken\00\Offer - 3004444_00.pdf").write_bytes(b"%PDF-1.4 not a pdf")
    (root / r"ACME Foods\Offres\3004444 - Broken\01").mkdir(parents=True)
    (root / r"ACME Foods\Offres\3004444 - Broken\01\Offer - 3004444_01.pdf").write_bytes(b"")
    # More documents in a folder without offer documents than in the folder that has the offer.
    pdf(root, r"ACME Foods\Offres\3006666 - specs\Specification\Spec A.pdf", ["Specification A"])
    pdf(root, r"ACME Foods\Offres\3006666 - specs\Specification\Spec B.pdf", ["Specification B"])
    pdf(root, r"Globex\Offres\3006666\00\Offer - 3006666_00.pdf", ["Lausanne, 01.07.2021\nOffer 3006666", en()])
    # An offer folder without the offer itself (only a specification): not in the register, in the review.
    pdf(root, r"ACME Foods\Offres\3007777 - Line 3\Specification\Spec C.pdf", ["Specification C"])
    # Workbooks that cannot be read (not real .xls files): the run logs them and goes on.
    (root / r"ACME Foods\Offres\0904099\History").mkdir(parents=True)
    (root / r"ACME Foods\Offres\0904099\History\History.xls").write_bytes(b"not a workbook")
    (root / r"ACME Foods\Suivi").mkdir(parents=True)
    (root / r"ACME Foods\Suivi\QuotationList.xls").write_bytes(b"not a workbook")


def build_garbled_corpus(root):
    pdf(root, r"ACME Foods\Offres\3001234 - Line 5\00\Offer - 3001234_00.pdf",
        ["Lausanne, 10.02.2020\nOffer 3001234", en()])
    pdf(root, r"ACME Foods\Offres\3005555 - garbled\00\Offer - 3005555_00.pdf",
        ["Lausanne, 01.06.2021\nOffer 3005555", GARBLED])


def build_versions_corpus(root):
    """Archive versions in sibling folders (R30) and other offer-like files in a revision (R29)."""
    client = r"01_Historique\PROSPECT\Allemagne\Dupont Biscuits"
    pdf(root, client + r"\B-250306 V1\Dupont B-250306 V1.pdf",
        ["Lausanne, 25.03.2006\nAngebot B-250306", DE_ARCHIVE[2], DE_ARCHIVE[3]])
    pdf(root, client + r"\B-250306 V2\Dupont B-250306 V2.pdf",
        ["Lausanne, 02.05.2006\nAngebot B-250306", DE_ARCHIVE[2], DE_ARCHIVE[3]])
    line = r"ACME Foods\Offres\3002113 - Line 5\02"
    pdf(root, line + r"\Angebot - 3002113_02.pdf", ["Lausanne, 03.03.2020\nAngebot 3002113", en()])
    pdf(root, line + r"\Quotation conveyor supplier.pdf",  # a supplier's quotation kept with the offer
        ["Quotation 4711", "PRICE\nInfeed conveyor CHF 12,000.00\nOutfeed conveyor CHF 8,000.00\n"
                           "PRICE, net, without taxes, CHF 20,000.00"])
    pdf(root, line + r"\Devis transport.pdf",
        ["Devis", "PRIX\nTransport par camion CHF 3,000.00\nPrix total HT CHF 3,000.00"])
    pdf(root, r"ACME Foods\Offres\3002114\00\Devis.pdf", ["Lausanne, 01.02.2021\nOffre", en()])  # no number in the name


def closed_port_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


def offers_cfg(root, data_dir, knowledge, **kw):
    defaults = dict(roots=[str(root)], data_dir=data_dir, workers=2, embedding_model="hash:256", query_prefix="",
                    passage_prefix="", profile="offers", world="offers", extensions=[".pdf"],
                    knowledge_path=str(knowledge), vision_url=closed_port_url(), vision_model="test-vision",
                    vision_timeout_s=30)
    defaults.update(kw)
    return Config(**defaults)


def indexed(tmp_path_factory, name, build):
    """An indexed corpus: (root, data_dir, knowledge folder)."""
    base = tmp_path_factory.mktemp(name)
    root = base / "Clients"
    build(root)
    knowledge = base / "knowledge"
    knowledge.mkdir()
    (knowledge / "vocabulary.md").write_text(VOCABULARY, encoding="utf-8")
    cfg = offers_cfg(root, base / "data", knowledge)
    run_index(cfg, progress=quiet)
    return root, base / "data", knowledge


def world(index, tmp_path, run=True, projects_db=None):
    """A copy of an indexed corpus, its register built when run (linked to projects_db): (cfg, con)."""
    root, data, knowledge = index
    shutil.copytree(data, tmp_path / "data")
    cfg = offers_cfg(root, tmp_path / "data", knowledge)
    if run:
        offers.run_offers(cfg, projects_db=projects_db, progress=quiet)
    return cfg, store.connect(cfg.db_path)


def make_projects_db(tmp_path, texts):
    """A tiny projects world indexed: {(project, path below the root starting with the project): page 1 text}."""
    root = tmp_path / "Projets"
    root.mkdir(exist_ok=True)
    for (_, rel), text in texts.items():
        pdf(root, rel, [text])
    cfg = Config(roots=[str(root)], data_dir=tmp_path / "projects_data", workers=2, embedding_model="hash:256",
                 query_prefix="", passage_prefix="", profile="projects", world="projects", vision_enabled=False,
                 ocr_enabled=False)
    run_index(cfg, progress=quiet)
    con = store.connect(cfg.db_path)
    try:  # the projects profile derives the project from the first folder
        assert {r[0] for r in con.execute("SELECT project FROM doc_facets")} == {p for p, _ in texts}
    finally:
        con.close()
    return str(cfg.db_path)

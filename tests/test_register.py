import csv

from conftest import make_pdf
from dmx_docs import register, store
from dmx_docs.indexer import run_index
from dmx_docs.tools import DocTools

FIELDS = ["project", "path", "order_no", "serial", "model", "year", "robots", "cells", "controller", "camera",
          "client", "country", "site", "agent", "maintenance", "order_date", "status", "notes", "source",
          "group", "industry", "city"]


def quiet(*_a, **_k):
    pass


def write_register(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})


def test_fold_and_numbers():
    assert register.fold("ACME – Biscuitès") == "acme biscuites"
    assert register.numbers("DL COTILLON 0100710291.pdf", "120005849-01") == {"100710291", "120005849"}
    assert register._base("DUSCHMURTZ_OLD") == register._base("Duschmurtz")


def test_register_links_projects_and_filters(tmp_path, make_cfg):
    root = tmp_path / "RMA"
    docs = {
        ("THOR", r"0_Vente\00_Offres", "Offre THOR.pdf"): "Offre pour deux Paloma 7R. Cadence 120 produits par minute.",
        ("2_Hors_Garantie", r"COTILLON\Cahier des charges\DL", "DL COTILLON 0100710291.pdf"): "Demande de lancement Presto.",
        ("2_Hors_Garantie", r"VIEUX\Gestion", "FAT 0100710500.pdf"): "FAT Presto cadence 60 produits par minute.",
        ("2_Hors_Garantie", r"VIEUX\Gestion", "Rapport 0100710500.pdf"): "Rapport cadence.",
        ("SPACE", r"0_Vente\00_Offres", "Offre SPACE.pdf"): "Offre Presto 2R. Cadence 80 produits par minute.",
    }
    for (proj, sub, name), text in docs.items():
        d = root / proj / sub
        d.mkdir(parents=True, exist_ok=True)
        make_pdf(str(d / name), [text])
    cfg = make_cfg(root)
    cfg.register_path = str(tmp_path / "register.csv")
    write_register(cfg.register_path, [
        dict(project="THOR", path=r"N:\THOR", order_no="120005849", serial="120005849-01", model="Paloma 7R",
             year="2023", robots="7", client="ACME BISCUITS NORD", group="ACME", country="France",
             maintenance="Actif", order_date="2023-12-19"),
        dict(project="", path=r"N:\2_Hors_Garantie\COTILLON", serial="100710291", model="Presto", year="2003",
             client="POLDER BAKERY BV", country="Netherlands"),
        dict(project="OLD NAME", serial="0100710500", model="Presto 2R", year="2009", client="ACME BISCUITS SUD",
             group="ACME", country="France"),
        dict(project="GHOST", path=r"N:\3_Archives\GHOST", serial="100700001", model="Paloma 2R", year="2001",
             client="GLOBEX USA", country="United States"),
    ])
    run_index(cfg, progress=quiet)
    tools = DocTools(cfg)

    con = tools._con()
    linked = {r[0]: r[1] for r in con.execute("SELECT name, project || ':' || matched_by FROM register_machines")}
    con.close()
    assert linked["THOR"] == "THOR:path"            # by folder
    assert linked["COTILLON"] == "COTILLON:path"    # name from the folder
    assert linked["OLD NAME"] == "VIEUX:order"      # by the serial number in its file names
    assert linked["GHOST"] is None                  # kept, documents not indexed

    card = tools.project_card("thor")
    assert "Client: ACME BISCUITS NORD" in card and "120005849-01 · Paloma 7R · 2023 · 7 robots" in card
    assert "Offre THOR.pdf" in card and "VIEUX" in card  # newest offer; same client group's other project
    assert "no indexed documents" in tools.project_card("GHOST")

    listing = tools.list_projects(client="acme")
    assert "THOR" in listing and "VIEUX" in listing and "SPACE" not in listing and "COTILLON" not in listing
    assert "GHOST" not in listing
    usa = tools.list_projects(country="United States")
    assert "GHOST | Paloma 2R | GLOBEX USA" in usa and "0 indexed projects" in usa
    assert "THOR" not in tools.list_projects(year_to=2010)
    assert "COTILLON" in tools.list_projects(machine="Presto", collection="2_Hors")

    hits = tools.search("cadence", client="Acme", mode="keyword")
    assert "Offre THOR" in hits and "FAT 0100710500" in hits and "SPACE" not in hits
    assert "for ACME BISCUITS NORD" in hits
    assert "SPACE" not in tools.search("cadence", country="France", mode="keyword")


def test_register_reloads_when_file_changes(tmp_path, make_cfg):
    root = tmp_path / "RMA"
    (root / "THOR").mkdir(parents=True)
    make_pdf(str(root / "THOR" / "Offre.pdf"), ["Offre."])
    cfg = make_cfg(root)
    cfg.register_path = str(tmp_path / "register.csv")
    run_index(cfg, progress=quiet)
    tools = DocTools(cfg)
    assert "not in the register" in tools.list_projects()  # no file yet
    write_register(cfg.register_path, [dict(project="THOR", model="Paloma 4R", client="ACME FOODS")])
    assert "ACME FOODS" in tools.list_projects()
    con = store.connect(cfg.db_path)
    assert register.refresh(con, cfg.register_path) is False  # unchanged: no reload
    con.close()

"""Facets derived from file paths: project, collection, section and document type.

Project folders follow one of two templates:
  current:  0_Vente, 1_Finances, 2_Electrique, ... 9_SAV (with numbered subfolders)
  older:    Cahier des charges, Electrique, Mécanique, Gestion, Rapports, ... Clôture
and a few are ad hoc. Facets are hints for filtering and display: each records where it
came from ('folder' = template folder, 'name' = file or folder name keyword), and documents
without a facet must never disappear from unfiltered searches.
"""

from __future__ import annotations

import os
import re
import unicodedata

# Bump when the rules change: every document's facets are recomputed (from its path only).
FACETS_VERSION = 1


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.lower())
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[\s_\-]+", " ", s).strip()


# Section of the project tree: normalized folder name (without the "N_" prefix) -> section.
SECTIONS = {
    "vente": "Vente", "cahier des charges": "Vente", "offres": "Vente",
    "finances": "Finances", "facturation": "Finances",
    "electrique": "Electrique",
    "mecanique": "Mecanique",
    "soft": "Soft", "software": "Soft", "logiciel": "Soft",
    "gestion": "Gestion",
    "rapports tests": "Rapports_Tests", "rapports": "Rapports_Tests",
    "photos videos": "Photos_Videos",
    "documentation": "Documentation",
    "sav": "SAV",
    "cloture": "Cloture",
}
_SECTION_PREFIX = re.compile(r"^\d{1,2}[ _]")

# Document types. Folder rules use normalized folder names (prefix numbers removed);
# name rules use whole words of the normalized file name. First match wins, so the
# specific types come first.
DOC_TYPES = [
    # type,               folder names,                                   file-name words/phrases
    ("fat",               ["fat", "reception usine"],                                             ["fat", "factory acceptance", "reception usine"]),
    ("sat",               ["sat", "reception site"],                                             ["sat", "site acceptance", "reception site"]),
    ("reception",         ["acceptation machine", "reception"],           ["pv de reception", "proces verbal", "protocole de reception"]),
    ("mise_en_service",   ["mes", "mise en service", "rapports interventions"],
                                                                          ["mise en service", "commissioning", "rapport d intervention", "inbetriebnahme"]),
    ("open_points",       ["open points lists", "opl"],                   ["opl", "open points", "points ouverts", "punch list"]),
    ("tests",             ["tests internes", "tests preliminaires", "protocole de tests", "tests", "essais"],
                                                                          ["test", "tests", "essai", "essais"]),
    ("offre",             ["offres", "offre", "prix final"],                       ["offre", "offer", "quotation", "devis", "angebot", "price summary"]),
    ("commande",          ["commandes", "commande", "commandes complementaires"],     ["commande", "bon de commande", "purchase order", "bestellung", "order confirmation"]),
    ("cahier_des_charges", ["cahier des charges", "formulaire validation"],
                                                                          ["cahier des charges", "cdc", "specification", "specifications", "urs", "lastenheft", "pflichtenheft"]),
    ("facture",           ["facturation", "garanties bancaires"],         ["facture", "invoice", "rechnung"]),
    ("modification",      ["modifications"],                              ["modification", "modifications", "note de modification"]),
    ("schema_electrique", ["schemas", "schemas electriques", "schema", "eplan"],                           ["schema electrique", "schemas electriques", "wiring", "electrical diagram"]),
    ("layout",            ["layouts", "implantation"],                    ["layout", "implantation"]),
    ("plan",              ["dessins", "plans"],                           ["plan", "drawing", "zeichnung"]),
    ("pieces_detachees",  ["pieces detachees", "spare parts"],            ["pieces detachees", "spare parts", "ersatzteil", "ersatzteile"]),
    ("manuel",            ["documentation indesign", "usb stick doc project", "markdown"],
                                                                          ["manuel", "manual", "notice", "betriebsanleitung", "bedienungsanleitung", "user manual"]),
    ("doc_fournisseur",   ["materiel tiers", "received doc", "third party", "oem doc"],
                                                                          ["datasheet", "data sheet", "fiche technique"]),
    ("suivi",             ["suivi", "correspondance e mails"],            ["compte rendu", "cr", "minutes", "reunion", "meeting", "pv"]),
    ("planning",          ["planning"],                                   ["planning", "gantt"]),
    ("transport",         ["transport", "supply chain", "shipping doc", "shipping", "expedition"],                  ["transport", "packing list", "colisage"]),
    ("nomenclature",      ["bom internal use", "nomenclature"],           ["bom", "nomenclature"]),
]

# Default type when only the section is known.
SECTION_DEFAULT = {"Electrique": "doc_electrique", "Mecanique": "doc_mecanique", "Documentation": "manuel",
                   "SAV": "sav", "Finances": "facture", "Soft": "soft"}

_CONTAINER = re.compile(r"^\d{1,2}_")  # 2_Hors_Garantie, 3_Archives, 9_Projets_Internes...


def section_of(folder: str) -> str | None:
    return SECTIONS.get(_SECTION_PREFIX.sub("", _norm(folder)).strip())


def _words(s: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", _norm(s)) + " "


def _folder_key(folder: str) -> str:
    return re.sub(r"^[0-9 ]+", "", _norm(folder)).strip()  # "56_Acceptation_machine" -> "acceptation machine"


def compute(path: str, root: str) -> dict:
    """Facets of one document, from its path relative to the root folder it is under."""
    rel = os.path.relpath(path, root) if root else path
    parts = [p for p in rel.replace("/", "\\").split("\\") if p and p != "."]
    folders, name = parts[:-1], parts[-1] if parts else ""
    out = {"project": None, "collection": None, "section": None, "doc_type": None, "facet_source": None}

    # Project = the folder right above the first template section; else the first folder
    # (the second one under a numbered container such as 2_Hors_Garantie).
    idx = next((i for i, f in enumerate(folders) if section_of(f)), None)
    if idx is not None:
        proj_i = idx - 1
    elif folders and _CONTAINER.match(folders[0]) and len(folders) > 1:
        proj_i = 1
    else:
        proj_i = 0 if folders else -1
    if proj_i >= 0:
        out["project"] = folders[proj_i]
        out["collection"] = folders[proj_i - 1] if proj_i >= 1 else None
    elif root:
        out["project"] = os.path.basename(root.rstrip("\\/"))  # the root itself is a project folder
    if idx is not None:
        out["section"] = section_of(folders[idx])

    below = folders[idx + 1:] if idx is not None else folders[proj_i + 1:]
    keys = [_folder_key(f) for f in below] + ([_folder_key(folders[idx])] if idx is not None else [])
    words = _words(os.path.splitext(name)[0])
    for doc_type, folder_names, name_words in DOC_TYPES:  # specific file-name evidence first
        if doc_type in ("fat", "sat") and any(f" {w} " in words for w in name_words):
            out["doc_type"], out["facet_source"] = doc_type, "name"
            return out
    for key in reversed(keys):  # deepest folder first
        for doc_type, folder_names, _ in DOC_TYPES:
            if key in folder_names:
                out["doc_type"], out["facet_source"] = doc_type, "folder"
                return out
    for doc_type, _, name_words in DOC_TYPES:
        if any(f" {w} " in words for w in name_words):
            out["doc_type"], out["facet_source"] = doc_type, "name"
            return out
    if out["section"] in SECTION_DEFAULT:
        out["doc_type"], out["facet_source"] = SECTION_DEFAULT[out["section"]], "section"
    return out


TABLE = """
CREATE TABLE IF NOT EXISTS doc_facets (
    doc_id        INTEGER PRIMARY KEY,
    project       TEXT,
    collection    TEXT,
    section       TEXT,
    doc_type      TEXT,
    facet_source  TEXT
);
CREATE INDEX IF NOT EXISTS doc_facets_project ON doc_facets(project COLLATE NOCASE);
"""


def refresh(con, roots: list[str]) -> int:
    """Compute facets for documents that have none (or were computed by older rules).
    Cheap when everything is up to date. Returns the number of documents updated."""
    from . import store

    con.executescript(TABLE)
    if store.get_meta(con, "facets_version") != str(FACETS_VERSION):
        con.execute("DELETE FROM doc_facets")
        store.set_meta(con, "facets_version", FACETS_VERSION)
    con.execute("DELETE FROM doc_facets WHERE doc_id NOT IN (SELECT id FROM docs)")
    todo = con.execute("""SELECT d.id, d.path, d.path_key FROM docs d
                          LEFT JOIN doc_facets f ON f.doc_id = d.id WHERE f.doc_id IS NULL""").fetchall()
    root_keys = [(store.path_key(r), r) for r in roots]
    rows = []
    for doc_id, path, key in todo:
        f = compute(path, root_of(key, root_keys) or "")
        rows.append((doc_id, f["project"], f["collection"], f["section"], f["doc_type"], f["facet_source"]))
    autocommit = con.isolation_level is None
    if autocommit:
        con.execute("BEGIN")  # one transaction, not one per row
    con.executemany("INSERT OR REPLACE INTO doc_facets VALUES (?,?,?,?,?,?)", rows)
    con.execute("COMMIT") if autocommit else con.commit()
    return len(rows)


def root_of(path_key: str, root_keys: list[tuple[str, str]]) -> str | None:
    """The root folder (original spelling) a document key is under; root_keys = [(key, path)]."""
    best = None
    for key, root in root_keys:
        if path_key == key or path_key.startswith(key.rstrip(os.sep) + os.sep):
            if best is None or len(key) > len(best[0]):
                best = (key, root)
    return best[1] if best else None

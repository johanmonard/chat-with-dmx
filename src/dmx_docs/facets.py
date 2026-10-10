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
FACETS_VERSION = 3             # projects
MARKETING_FACETS_VERSION = 1


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
    ("reception",         ["acceptation machine", "reception", "acceptation", "acceptation fat handover sat", "handover"],
                                                                          ["pv de reception", "proces verbal", "protocole de reception", "handover"]),
    ("liberation",        ["liberation interne", "liberation avant livraison", "liberation", "internal release"],
                                                                          ["liberation interne", "liberation avant livraison"]),
    ("qualification",     ["iq oq", "iq oq pq", "qualification"],         ["iq oq", "iq oq pq", "qualification"]),
    ("securite",          ["analyse securite", "analyse de securite", "securite", "securit", "analyse de risques", "marquage ce", "ce"],
                                                                          ["analyse de risque", "analyse de risques", "analyse securite", "declaration ce", "marquage ce", "risk assessment"]),
    ("mise_en_service",   ["mes", "mise en service", "rapports interventions", "documents mes"],
                                                                          ["mise en service", "commissioning", "rapport d intervention", "inbetriebnahme"]),
    ("open_points",       ["open points lists", "opl"],                   ["opl", "open points", "points ouverts", "punch list"]),
    ("tests",             ["tests internes", "tests preliminaires", "protocole de tests", "tests", "essais",
                           "mesure de bruit", "mesures de bruit"],
                                                                          ["test", "tests", "essai", "essais", "mesure de bruit", "mesures de bruit"]),
    ("checklist",         ["check lists", "checklists", "check list", "checklist"], ["check list", "checklist"]),
    ("qualite",           ["qg", "qg process", "qg processus", "qa", "quality gates", "qualite", "qmm", "qg1", "qg2", "qg3", "qg4", "qg5"],
                                                                          ["quality gate", "qg"]),
    ("offre",             ["offres", "offre", "prix final"],                       ["offre", "offer", "quotation", "devis", "angebot", "price summary"]),
    ("commande",          ["commandes", "commande", "commandes complementaires"],     ["commande", "bon de commande", "purchase order", "bestellung", "order confirmation"]),
    ("cahier_des_charges", ["cahier des charges", "formulaire validation", "specifications", "specification"],
                                                                          ["cahier des charges", "cdc", "specification", "specifications", "urs", "lastenheft", "pflichtenheft"]),
    ("facture",           ["facturation", "garanties bancaires"],         ["facture", "invoice", "rechnung"]),
    ("modification",      ["modifications"],                              ["modification", "modifications", "note de modification"]),
    ("schema_electrique", ["schemas", "schemas electriques", "schema", "eplan"],                           ["schema electrique", "schemas electriques", "wiring", "electrical diagram"]),
    ("layout",            ["layouts", "layout", "implantation"],          ["layout", "implantation"]),
    ("plan",              ["dessins", "plans", "mechanical drawings", "drawings"], ["plan", "drawing", "zeichnung"]),
    ("pieces_detachees",  ["pieces detachees", "spare parts"],            ["pieces detachees", "spare parts", "ersatzteil", "ersatzteile"]),
    ("manuel",            ["documentation indesign", "usb stick doc project", "markdown"],
                                                                          ["manuel", "manual", "notice", "betriebsanleitung", "bedienungsanleitung", "user manual"]),
    ("sav",               ["sav", "service apres vente"],                 ["sav"]),
    ("doc_fournisseur",   ["materiel tiers", "received doc", "third party", "oem doc", "suppliers", "fournisseurs"],
                                                                          ["datasheet", "data sheet", "fiche technique"]),
    ("suivi",             ["suivi", "seances", "pv seances"],             ["compte rendu", "cr", "minutes", "reunion", "meeting", "pv", "pv seance", "rapport de seance"]),
    ("correspondance",    ["correspondance e mails", "correspondance", "fax", "e mails", "emails", "courrier"],
                                                                          ["fax", "lettre", "courrier", "e mail"]),
    ("planning",          ["planning"],                                   ["planning", "gantt"]),
    ("transport",         ["transport", "supply chain", "shipping doc", "shipping", "expedition"],                  ["transport", "packing list", "colisage"]),
    ("nomenclature",      ["bom internal use", "nomenclature"],           ["bom", "nomenclature"]),
]

# Default type when only the section is known.
SECTION_DEFAULT = {"Electrique": "doc_electrique", "Mecanique": "doc_mecanique", "Documentation": "manuel",
                   "SAV": "sav", "Finances": "facture", "Soft": "soft", "Rapports_Tests": "rapport",
                   "Cloture": "cloture"}

_CONTAINER = re.compile(r"^\d{1,2}_")  # 2_Hors_Garantie, 3_Archives, 9_Projets_Internes...


def section_of(folder: str) -> str | None:
    return SECTIONS.get(_SECTION_PREFIX.sub("", _norm(folder)).strip())


def _is_container(folder: str) -> bool:
    """A numbered folder grouping projects (not a numbered template section like 5_Gestion)."""
    return bool(_CONTAINER.match(folder)) and not section_of(folder)


def _words(s: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", _norm(s)) + " "


def _folder_key(folder: str) -> str:
    return re.sub(r"^[0-9 ]+", "", _norm(folder)).strip()  # "56_Acceptation_machine" -> "acceptation machine"


def compute(path: str, root: str) -> dict:
    """Facets of one document, from its path relative to the root folder it is under."""
    rel = os.path.relpath(path, root) if root else path
    parts = [p for p in rel.replace("/", "\\").split("\\") if p and p != "."]
    folders, name = parts[:-1], parts[-1] if parts else ""
    out = {"project": None, "subproject": None, "collection": None, "section": None,
           "doc_type": None, "facet_source": None}

    # Collection = numbered container folders (2_Hors_Garantie...); project = the first folder
    # below them; sub-project = a level between the project and its template sections
    # (multi-machine projects: MOUSQUETAIRES\1_Athos\Rapports, PACIFIC\08_03_02\Documentation).
    i = 0
    while i < len(folders) - 1 and _is_container(folders[i]):
        i += 1
    idx = next((k for k in range(i, len(folders)) if section_of(folders[k])), None)
    if i < len(folders) and not section_of(folders[i]):
        out["project"] = folders[i]
        out["collection"] = folders[i - 1] if i >= 1 else None
        if idx is not None and idx - 1 > i:
            out["subproject"] = folders[idx - 1]
    elif root:
        out["project"] = os.path.basename(root.rstrip("\\/"))  # the root itself is a project folder
    if idx is not None:
        out["section"] = section_of(folders[idx])

    start = i + 1 if out["project"] == (folders[i] if i < len(folders) else None) else i
    keys = [_folder_key(f) for f in folders[start:]]
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


def compute_marketing(path: str, root: str) -> dict:
    """Marketing world: the category is the first folder below the root (Brochures, Presentations...).
    A document under no configured root (root empty) has no category: the whole path would make the
    server name one."""
    if not root:
        return {}
    rel = os.path.relpath(path, root)
    parts = [p for p in rel.replace("/", "\\").split("\\") if p and p != "."]
    if len(parts) > 1:
        return {"category": parts[0], "facet_source": "folder"}
    return {}


_LANGUAGE = re.compile(r"_(FR|DE|EN|ES|IT)(?=_|$)")


def compute_documentation(path: str, root: str) -> dict:
    """Machine manual: the category is the chapter folder (1000_Introduction, 6000_Entretien...)
    and the language comes from the file name (..._FR_V00.md)."""
    out = compute_marketing(path, root)
    m = _LANGUAGE.search(os.path.splitext(os.path.basename(path))[0])
    if m:
        out["language"] = m.group(1)
    return out


RULES = {"projects": compute, "marketing": compute_marketing, "documentation": compute_documentation}
PROFILE_VERSIONS = {"marketing": MARKETING_FACETS_VERSION, "documentation": 1}


def _version(profile: str) -> str:
    # Projects keeps the plain number stored before worlds existed: nothing is recomputed.
    if profile == "projects":
        return str(FACETS_VERSION)
    return f"{profile}:{PROFILE_VERSIONS.get(profile, 1)}"


TABLE = """
CREATE TABLE IF NOT EXISTS doc_facets (
    doc_id        INTEGER PRIMARY KEY,
    project       TEXT,
    collection    TEXT,
    section       TEXT,
    doc_type      TEXT,
    facet_source  TEXT,
    subproject    TEXT,
    category      TEXT,
    language      TEXT
);
CREATE INDEX IF NOT EXISTS doc_facets_project ON doc_facets(project COLLATE NOCASE);
"""
COLUMNS = ("doc_id", "project", "collection", "section", "doc_type", "facet_source", "subproject", "category",
           "language")


def refresh(con, roots: list[str], profile: str = "projects") -> int:
    """Compute facets for documents that have none (or were computed by older rules or another
    profile). Cheap when everything is up to date. Returns the number of documents updated."""
    from . import store

    con.executescript(TABLE)
    cols = {r[1] for r in con.execute("PRAGMA table_info(doc_facets)")}
    if "subproject" not in cols:
        con.execute("ALTER TABLE doc_facets ADD COLUMN subproject TEXT")  # tables from version 1
    if "category" not in cols:
        con.execute("ALTER TABLE doc_facets ADD COLUMN category TEXT")  # tables from before worlds
    if "language" not in cols:
        con.execute("ALTER TABLE doc_facets ADD COLUMN language TEXT")  # tables from before documentation
    if store.get_meta(con, "facets_version") != _version(profile):
        con.execute("DELETE FROM doc_facets")
        store.set_meta(con, "facets_version", _version(profile))
    con.execute("DELETE FROM doc_facets WHERE doc_id NOT IN (SELECT id FROM docs)")
    todo = con.execute("""SELECT d.id, d.path, d.path_key FROM docs d
                          LEFT JOIN doc_facets f ON f.doc_id = d.id WHERE f.doc_id IS NULL""").fetchall()
    root_keys = [(store.path_key(r), r) for r in roots]
    rule = RULES.get(profile, lambda path, root: {})
    rows = []
    for doc_id, path, key in todo:
        f = rule(path, root_of(key, root_keys) or "")
        rows.append((doc_id,) + tuple(f.get(c) for c in COLUMNS[1:]))
    autocommit = con.isolation_level is None
    if autocommit:
        con.execute("BEGIN")  # one transaction, not one per row
    con.executemany(f"INSERT OR REPLACE INTO doc_facets ({','.join(COLUMNS)}) "
                    f"VALUES ({','.join('?' * len(COLUMNS))})", rows)
    con.execute("COMMIT") if autocommit else con.commit()
    if profile == "projects":  # machine types and the register belong to the project documentation
        from . import machines, register
        machines.refresh(con)  # machine types per project; no-op when nothing changed
        con.executescript(register.TABLE)  # filled by register.refresh (needs the register file)
    return len(rows)


def root_of(path_key: str, root_keys: list[tuple[str, str]]) -> str | None:
    """The root folder (original spelling) a document key is under; root_keys = [(key, path)]."""
    best = None
    for key, root in root_keys:
        if path_key == key or path_key.startswith(key.rstrip(os.sep) + os.sep):
            if best is None or len(key) > len(best[0]):
                best = (key, root)
    return best[1] if best else None

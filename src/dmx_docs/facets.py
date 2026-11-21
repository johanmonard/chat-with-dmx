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


# Upper case only: French words in file names ("Description_de_l'application_FR") are not tags.
_LANGUAGE = re.compile(r"_(FR|DE|EN|ES|IT)(?=_|$)")


def compute_documentation(path: str, root: str) -> dict:
    """Machine manual: the category is the chapter folder (1000_Introduction, 6000_Entretien...)
    and the language comes from the file name (..._FR_V00.md)."""
    out = compute_marketing(path, root)
    tags = _LANGUAGE.findall(os.path.splitext(os.path.basename(path))[0])
    if tags:
        out["language"] = tags[-1]  # the language tag comes last (..._DE_FR_V00: a FR translation)
    return out


# Offers world: kinds of document by folder name (folded) and by file-name words, first match wins.
_OFFER_FOLDER_KINDS = {"specification": "specification", "specifications": "specification",
                       "cahier des charges": "specification", "mail": "mail", "mails": "mail",
                       "layout": "layout", "layouts": "layout", "history": "history"}
_OFFER_NAME_KINDS = [
    ("offer",              ["angebot", "offre", "offer", "quotation", "devis"]),
    ("technical_solution", ["solution technique"]),
    ("visit_report",       ["besuchsbericht", "visit report", "rapport de visite"]),
    ("proforma",           ["proforma"]),
]
_OFFER_PARTNERS = {"06 offres ipso": "IPSO", "07 offres wyzo": "Wyzo"}
_ARCHIVE_ANCHORS = ("prospect", "prospects", "client", "clients")
_VERSION_LABEL = re.compile(r"(?<![A-Za-z0-9])V(\d+)(?!\w)", re.I)
_NAME_REVISION = re.compile(r"(?:[ _-]V(?P<v>\d+)|_(?P<n>\d{2})|[ _-](?P<q>Q\d{2}))$", re.I)  # 'B-250306 V2', '3002113_02', 'PRJ-2014-02259-Q01'
# A file named as the offer only: its number and a version ('B-250306 V2', 'F-0903029 V1', '3001234_02').
_OWN_FILE = re.compile(r"\W*(?:(?:[BFE][- ]?)?\d{6,7}|PRJ[-_ ]*\d{4}[-_ ]*\d{5})(?:[ _-]*(?:V\d+|Q\d{2}|\d{2}))?\W*", re.I)


def _offer_structural(folder: str) -> bool:
    """A folder that is neither a client nor a site: 'Offres', a revision, a kind of document."""
    from . import offerkeys, register

    return (register.fold(folder) in ("offres", "old") or offerkeys.revision_of(folder) is not None
            or register.fold(folder) in _OFFER_FOLDER_KINDS)


def compute_offers(path: str, root: str) -> dict:
    """Offers world, from the path below the root: category (client, archive, partner, unknown_client,
    market_study, nda, sales_info), client, site, country (archive only), partner, offer key, revision
    label, document kind and machines. facet_source is 'name' when the kind comes from the file name,
    else 'folder'. A document under no configured root, or directly in it, has no facets."""
    from . import machines, offerkeys, register

    if not root:
        return {}
    rel = os.path.relpath(path, root)
    parts = [p for p in rel.replace("/", "\\").split("\\") if p and p != "."]
    if len(parts) < 2:
        return {}
    folders, name = parts[:-1], parts[-1]
    stem = os.path.splitext(name)[0]
    category = offerkeys.SPECIAL_FOLDED.get(register.fold(folders[0]), "client")
    out = {"category": category, "client": None, "site": None, "country": None, "partner": None,
           "offer": None, "revision": None, "doc_type": None, "machine": None, "facet_source": "folder"}
    found = machines.models_in("|".join(parts))
    names = sorted({fam for fam, _ in found} | {model for _, model in found if model})
    out["machine"] = "|" + "|".join(names) + "|" if names else None
    if category in ("market_study", "nda", "sales_info"):
        out["doc_type"] = category
        return out

    # Offer: the first folder below the top one that carries an offer number, else the file name.
    offer_idx = next((i for i in range(1, len(folders)) if offerkeys.offer_key(folders[i])), None)
    key = offerkeys.offer_key(folders[offer_idx] if offer_idx is not None else name)
    out["offer"] = key
    limit = offer_idx if offer_idx is not None else len(folders)

    if category == "archive":
        # 01_Historique\PROSPECT\<country>\<client>\<offer>: the client is the folder under the country, also
        # under a country folder the table does not know ('Scandinavia'): the country stays None then.
        anchor = next((i for i in range(1, limit) if offerkeys.country_en(folders[i])), None)
        if anchor is not None:
            out["country"] = offerkeys.country_en(folders[anchor])
        else:
            top = next((i for i in range(1, limit) if register.fold(folders[i]) in _ARCHIVE_ANCHORS), None)
            anchor = top + 1 if top is not None else None  # the country folder
        if anchor is not None and anchor + 1 < limit and not _offer_structural(folders[anchor + 1]):
            out["client"] = folders[anchor + 1]
    elif category != "unknown_client":
        out["partner"] = _OFFER_PARTNERS.get(register.fold(folders[0])) if category == "partner" else None
        chain = [f for f in folders[0 if category == "client" else 1:limit] if not _offer_structural(f)]
        out["client"] = chain[0] if chain else None
        out["site"] = chain[1] if len(chain) > 1 else None

    if key is None and category == "archive" and out["client"]:  # numbered by the date of its folder: one offer per client
        idx = offerkeys.archive_offer_index(folders, folders.index(out["client"]) + 1)
        if idx is not None:
            offer_idx, key = idx, f"{offerkeys.archive_token(folders[idx])}@{out['client']}"
            out["offer"] = key

    # Revision: the folder under the offer folder, else a version in the offer folder name or file name.
    rev = None
    first = offer_idx + 1 if offer_idx is not None else 1
    while offer_idx is not None and first < len(folders) and offerkeys.offer_key(folders[first]) == key:
        first += 1  # 'PRJ-2016-04753_Cerealis Portugal\PRJ-2016-04753\Q08': the same number twice
    candidates = folders[first:first + 1] if offer_idx is not None else folders[1:]
    for folder in candidates:
        r = offerkeys.revision_of(folder)
        if r:
            rev = r[0]
            break
    if rev is None and offer_idx is not None:
        m = _VERSION_LABEL.search(folders[offer_idx])
        if m is None and key and "@" in key and offer_idx + 1 < len(folders) \
                and offerkeys.archive_token(folders[offer_idx + 1]) == key.split("@")[0]:
            m = _VERSION_LABEL.search(folders[offer_idx + 1])  # 'E-2007_10_31 - Cordons bleus\E-2007_10_31 V1'
        rev = f"V{m.group(1)}" if m else None
    if rev is None and key and offerkeys.offer_key(stem) == key:  # the offer's own file: Angebot - 3002113_02.pdf
        m = _NAME_REVISION.search(stem)
        rev = (f"V{m['v']}" if m['v'] else m['n'] or m['q'].upper()) if m else None
    out["revision"] = rev

    # Kind of document: the deepest kind folder, then words of the file name, then the extension.
    for folder in reversed(folders[1:]):
        kind = _OFFER_FOLDER_KINDS.get(register.fold(folder))
        if kind:
            out["doc_type"] = kind
            return out
    words = " " + register.fold(stem) + " "
    kind = next((k for k, phrases in _OFFER_NAME_KINDS if any(f" {p} " in words for p in phrases)), None)
    if kind is None and name.lower().endswith(".msg"):
        kind = "mail"
    if kind is None:
        directly_in_offer = offer_idx == len(folders) - 1 or offerkeys.revision_of(folders[-1]) is not None
        # The offer's own file ('B-250306 V2.pdf'), also in a folder not read as the offer's: 'B 250306 V1', '250306'.
        own_file = bool(key and _OWN_FILE.fullmatch(stem) and offerkeys.offer_key(stem) == key)
        kind = "offer" if key and ((directly_in_offer and key.lower() in stem.lower()) or own_file) else "other"
    out["doc_type"] = kind
    out["facet_source"] = None if kind == "other" else "name"  # no evidence for the kind: None, as in the projects rules
    return out


def groups_fingerprint(groups: dict[str, list[str]]) -> str:
    """12 hex characters naming a groups table: when it changes, the facets are recomputed."""
    import hashlib

    from . import store

    pairs = sorted((g, store.path_key(f)) for g, folders in groups.items() for f in folders)
    return hashlib.sha1(repr(pairs).encode("utf-8")).hexdigest()[:12]


def compute_engineering(path: str, root: str, groups: dict[str, list[str]]) -> dict:
    """Engineering world: the category is the group whose folder holds the document (only the
    configuration names a group), the section the first folder below the group folder. A document in
    no group folder has no facets."""
    from . import store

    key = store.path_key(path)
    for group, folders in groups.items():
        for folder in folders:
            fk = store.path_key(folder)
            if store.is_under(key, fk):
                parts = [p for p in os.path.relpath(path, folder).replace("/", "\\").split("\\")
                         if p and p != "."]
                section = parts[0] if len(parts) > 1 else None  # the first folder below the group folder
                return {"category": group, "section": section, "facet_source": "config"}
    return {}


RULES = {"projects": compute, "marketing": compute_marketing, "documentation": compute_documentation,
         "offers": compute_offers}
# offers 2: the archive client by its position under the country folder; 3: the offer's own file wherever it lies;
# 4: a version after a dash in the file name ('B-250306-V2.doc'); 5: PRJ-YYYY-NNNNN project numbers;
# 6: archive offers numbered by a date in the folder name ('<date>@<client>').
PROFILE_VERSIONS = {"marketing": MARKETING_FACETS_VERSION, "documentation": 1, "offers": 6}


def _version(profile: str, groups: dict | None = None) -> str:
    # Projects keeps the plain number stored before worlds existed: nothing is recomputed.
    if profile == "projects":
        return str(FACETS_VERSION)
    if profile == "engineering":  # the groups table is part of the rules
        return f"engineering:1:{groups_fingerprint(groups or {})}"
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
    language      TEXT,
    client        TEXT,
    site          TEXT,
    country       TEXT,
    partner       TEXT,
    offer         TEXT,
    revision      TEXT,
    machine       TEXT
);
CREATE INDEX IF NOT EXISTS doc_facets_project ON doc_facets(project COLLATE NOCASE);
"""
COLUMNS = ("doc_id", "project", "collection", "section", "doc_type", "facet_source", "subproject", "category",
           "language", "client", "site", "country", "partner", "offer", "revision", "machine")
# Columns added after the first tables were created (ALTER TABLE on an older database).
_ADDED_COLUMNS = ("subproject", "category", "language", "client", "site", "country", "partner", "offer",
                  "revision", "machine")


def refresh(con, roots: list[str], profile: str = "projects", groups: dict | None = None) -> int:
    """Compute facets for documents that have none (or were computed by older rules or another
    profile, or another groups table). Cheap when everything is up to date. Returns the number of
    documents updated."""
    from . import store

    con.executescript(TABLE)
    cols = {r[1] for r in con.execute("PRAGMA table_info(doc_facets)")}
    for column in _ADDED_COLUMNS:  # tables from before sub-projects, worlds, documentation and offers
        if column not in cols:
            con.execute(f"ALTER TABLE doc_facets ADD COLUMN {column} TEXT")
    version = _version(profile, groups)
    if store.get_meta(con, "facets_version") != version:
        con.execute("DELETE FROM doc_facets")
        store.set_meta(con, "facets_version", version)
    con.execute("DELETE FROM doc_facets WHERE doc_id NOT IN (SELECT id FROM docs)")
    todo = con.execute("""SELECT d.id, d.path, d.path_key FROM docs d
                          LEFT JOIN doc_facets f ON f.doc_id = d.id WHERE f.doc_id IS NULL""").fetchall()
    root_keys = [(store.path_key(r), r) for r in roots]
    if profile == "engineering":
        def rule(path, root):
            return compute_engineering(path, root, groups or {})
    else:
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

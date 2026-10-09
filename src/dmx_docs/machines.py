"""Machine types per project (Paloma 4R, Presto 2R, Hector PM 7R...), found in file names and
in the first pages of the documents that describe the machine (offers, specifications, orders,
acceptance, commissioning, layouts, schematics).

Evidence is counted per document: a model mentioned in many documents of a project, or in a
file name, is most likely a machine of that project; a single mention may be a comparison or a
reference to another project, so it is kept but not used for filtering.
"""

from __future__ import annotations

import re

MACHINES_VERSION = 2

FAMILIES = {"paloma": "Paloma", "palom": "Paloma", "palomas": "Paloma", "presto": "Presto",
            "hector": "Hector", "delfi": "Delfi", "astor": "Astor", "nestor": "Nestor",
            "feedplacer": "FeedPlacer", "feed placer": "FeedPlacer"}
_FAMILY = re.compile(r"(?<![a-z])(palomas?|palom|presto|hector|delfi|astor|nestor|feed ?placer)(?=[^a-z]|$|sq)", re.I)
# What may follow the family name before the robot count: "PM", "D2", "SQ", a generation digit.
_ROBOTS = re.compile(r"[\s_\-]*((?:(?:pm|d\d|v\d|sq|\d)[\s_\-]*){0,2}?)(\d{1,2})\s*(r|sq)(?![a-z])", re.I)

# Document types whose first pages describe the project's own machine.
EVIDENCE_TYPES = ("offre", "commande", "cahier_des_charges", "fat", "sat", "reception", "mise_en_service",
                  "liberation", "qualification", "layout", "schema_electrique", "suivi", "securite")
EVIDENCE_PAGES = 3
MIN_DOCS = 2  # or one file name


def models_in(text: str) -> set[tuple[str, str]]:
    """{(family, model)} mentioned in a text; model is '' when no robot count follows."""
    found = set()
    for m in _FAMILY.finditer(text):
        family = FAMILIES[m.group(1).lower().replace("  ", " ")]
        r = _ROBOTS.match(text, m.end())
        if r and int(r.group(2)) > 0:
            sq = "sq" in (r.group(1) + r.group(3)).lower()
            found.add((family, f"{family} {int(r.group(2))}R" + (" SQ" if sq else "")))
        else:
            found.add((family, ""))
    return found


TABLE = """
CREATE TABLE IF NOT EXISTS project_machines (
    project    TEXT NOT NULL,
    family     TEXT NOT NULL,
    model      TEXT NOT NULL,   -- '' = family only
    docs       INTEGER NOT NULL, -- documents mentioning it
    name_docs  INTEGER NOT NULL, -- of which in the file name or path
    confirmed  INTEGER NOT NULL, -- enough evidence to describe and filter the project
    PRIMARY KEY (project, family, model)
);
"""
MIN_SHARE = 0.10  # of the evidence of the project's main machine: below, a passing reference


def refresh(con) -> bool:
    """Recompute machine types when documents or rules changed. Returns True if recomputed."""
    from . import store

    con.executescript(TABLE)
    if "confirmed" not in {r[1] for r in con.execute("PRAGMA table_info(project_machines)")}:
        con.executescript("DROP TABLE project_machines;" + TABLE)  # table of version 1
    n, last = con.execute("SELECT count(*), max(indexed_at) FROM docs").fetchone()
    state = f"{MACHINES_VERSION}:{n}:{last}"
    if store.get_meta(con, "machines_state") == state:
        return False
    evidence: dict[tuple[str, str, str], list[int]] = {}  # (project, family, model) -> [docs, name_docs]
    per_doc: dict[int, set] = {}
    projects: dict[int, str] = {}
    for doc_id, path, project in con.execute(
            "SELECT d.id, d.path, f.project FROM docs d JOIN doc_facets f ON f.doc_id = d.id "
            "WHERE f.project IS NOT NULL"):
        projects[doc_id] = project
        rel = path.split(project, 1)[-1]  # below the project folder: not the project name itself
        found = models_in(rel)
        if found:
            per_doc[doc_id] = {(fam, mod, True) for fam, mod in found}
    marks = ",".join("?" * len(EVIDENCE_TYPES))
    for doc_id, text in con.execute(
            f"""SELECT c.doc_id, c.text FROM chunks c JOIN doc_facets f ON f.doc_id = c.doc_id
                WHERE f.doc_type IN ({marks}) AND c.page_no <= ?""", EVIDENCE_TYPES + (EVIDENCE_PAGES,)):
        found = models_in(text)
        if found:
            s = per_doc.setdefault(doc_id, set())
            s.update((fam, mod, False) for fam, mod in found)
    for doc_id, items in per_doc.items():
        project = projects.get(doc_id)
        if project is None:
            continue
        seen: dict[tuple[str, str], bool] = {}
        for fam, mod, in_name in items:
            for key in ((fam, mod), (fam, "")):  # a model also counts for its family
                seen[key] = seen.get(key, False) or in_name
        for (fam, mod), in_name in seen.items():
            e = evidence.setdefault((project, fam, mod), [0, 0])
            e[0] += 1
            e[1] += in_name
    top: dict[str, int] = {}
    for (p, f, m), (d, nd) in evidence.items():
        top[p] = max(top.get(p, 0), d)
    rows = [(p, f, m, d, nd, int((d >= MIN_DOCS or nd >= 1) and d >= MIN_SHARE * top[p]))
            for (p, f, m), (d, nd) in evidence.items()]
    autocommit = con.isolation_level is None
    if autocommit:
        con.execute("BEGIN")
    con.execute("DELETE FROM project_machines")
    con.executemany("INSERT INTO project_machines VALUES (?,?,?,?,?,?)", rows)
    store.set_meta(con, "machines_state", state)
    con.execute("COMMIT") if autocommit else con.commit()
    return True


def confirmed_sql(alias: str = "pm") -> str:
    return f"{alias}.confirmed = 1"


def describe(con, project: str, limit: int = 4) -> str:
    """'Paloma 11R (14 docs), Paloma 7R (3 docs)' - confirmed models first, families if no model."""
    rows = con.execute(
        f"""SELECT family, model, docs FROM project_machines pm WHERE project = ? AND {confirmed_sql()}
            ORDER BY model = '', docs DESC""", (project,)).fetchall()
    models = [r for r in rows if r[1]]
    families_with_model = {r[0] for r in models}
    parts = [f"{m} ({d} docs)" for _, m, d in models[:limit]]
    parts += [f"{f} ({d} docs)" for f, m, d in rows if not m and f not in families_with_model]
    return ", ".join(parts)

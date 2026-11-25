"""Machine types per project (Paloma 4R, Presto 2R, Hector PM 7R...), found in file names and
in the first pages of the documents that describe the machine (offers, specifications, orders,
acceptance, commissioning, layouts, schematics).

Evidence is counted per document: a model mentioned in many documents of a project, or in a
file name, is most likely a machine of that project; a single mention may be a comparison or a
reference to another project, so it is kept but not used for filtering.
"""

from __future__ import annotations

import re

MACHINES_VERSION = 3  # 3: per-document evidence kept in doc_machines

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
CREATE TABLE IF NOT EXISTS doc_machines (
    doc_id     INTEGER NOT NULL,
    project    TEXT NOT NULL,
    family     TEXT NOT NULL,
    model      TEXT NOT NULL,   -- '' = family (a model also counts for its family)
    in_name    INTEGER NOT NULL, -- found in the file name or path
    PRIMARY KEY (doc_id, family, model)
);
CREATE INDEX IF NOT EXISTS doc_machines_project ON doc_machines(project);
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
    doc_rows = []  # per document, so that counts can be made for one caller's readable documents
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
            doc_rows.append((doc_id, project, fam, mod, int(in_name)))
    rows = _confirm(evidence)
    autocommit = con.isolation_level is None
    if autocommit:
        con.execute("BEGIN")
    con.execute("DELETE FROM project_machines")
    con.executemany("INSERT INTO project_machines VALUES (?,?,?,?,?,?)", rows)
    con.execute("DELETE FROM doc_machines")
    con.executemany("INSERT INTO doc_machines VALUES (?,?,?,?,?)", doc_rows)
    store.set_meta(con, "machines_state", state)
    con.execute("COMMIT") if autocommit else con.commit()
    return True


def _confirm(evidence: dict[tuple[str, str, str], list[int]]) -> list[tuple]:
    """(project, family, model, docs, name_docs, confirmed) from {(project, family, model): [docs, name_docs]}."""
    top: dict[str, int] = {}
    for (p, f, m), (d, nd) in evidence.items():
        top[p] = max(top.get(p, 0), d)
    return [(p, f, m, d, nd, int((d >= MIN_DOCS or nd >= 1) and d >= MIN_SHARE * top[p]))
            for (p, f, m), (d, nd) in evidence.items()]


def confirmed_sql(alias: str = "pm") -> str:
    return f"{alias}.confirmed = 1"


def _readable_evidence(con, readable: str, project: str | None = None) -> list[tuple]:
    """_confirm rows counted among the documents `readable` (a SQL condition on docs d) keeps;
    nothing for an index from before per-document evidence (fail closed)."""
    if not con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'doc_machines'").fetchone():
        return []
    where, args = (" AND dm.project = ?", [project]) if project is not None else ("", [])
    return _confirm({(p, f, m): [d, nd] for p, f, m, d, nd in con.execute(
        f"""SELECT dm.project, dm.family, dm.model, count(*), sum(dm.in_name) FROM doc_machines dm
            JOIN docs d ON d.id = dm.doc_id WHERE {readable}{where} GROUP BY 1, 2, 3""", args)})


def filter_sql(con, values: list[str], column: str = "project", readable: str | None = None) -> tuple[str, list]:
    """SQL condition on `column` (a project name) keeping the projects with one of these machine
    families or models, confirmed by the documents or by the machine register. With `readable` (a SQL
    condition on docs d: one caller's readable documents) the documents' evidence is counted and
    confirmed among those documents only."""
    from .register import own_project_sql

    marks = ",".join("?" * len(values))
    # For one caller (readable): the register's own project links, none made from order numbers
    # found in documents that caller may not read.
    own = "project" if readable is None else own_project_sql()
    register = (f"{column} IN (SELECT {own} FROM register_machines WHERE family_ok = 1 AND "
                f"(family COLLATE NOCASE IN ({marks}) OR (model_ok = 1 AND model COLLATE NOCASE IN ({marks}))))")
    if readable is None:
        return (f"({column} IN (SELECT project FROM project_machines pm WHERE {confirmed_sql()} AND "
                f"(family COLLATE NOCASE IN ({marks}) OR model COLLATE NOCASE IN ({marks}))) OR {register})",
                values * 4)
    wanted = {v.casefold() for v in values}
    projects = sorted({p for p, f, m, _d, _nd, ok in _readable_evidence(con, readable)
                       if ok and (f.casefold() in wanted or (m and m.casefold() in wanted))})
    return f"({column} IN ({','.join('?' * len(projects))}) OR {register})", projects + values * 2


def describe(con, project: str, limit: int = 4, readable: str | None = None) -> str:
    """'Paloma 11R (14 docs), Paloma 7R (3 docs)' - confirmed models first, families if no model.
    readable: SQL condition on docs d keeping one caller's readable documents; the evidence is then
    counted and confirmed among those documents only (None: every document, as always)."""
    if readable is None:
        rows = con.execute(
            f"""SELECT family, model, docs FROM project_machines pm WHERE project = ? AND {confirmed_sql()}
                ORDER BY model = '', docs DESC""", (project,)).fetchall()
    else:
        rows = sorted(((f, m, d) for _, f, m, d, _, ok in _readable_evidence(con, readable, project) if ok),
                      key=lambda r: (r[1] == "", -r[2]))
    models = [r for r in rows if r[1]]
    families_with_model = {r[0] for r in models}
    parts = [f"{m} ({d} docs)" for _, m, d in models[:limit]]
    parts += [f"{f} ({d} docs)" for f, m, d in rows if not m and f not in families_with_model]
    return ", ".join(parts)

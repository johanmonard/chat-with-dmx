"""Project register: the installed base (one row per delivered machine, from the company's machine
list exported to register.csv) linked to the indexed projects.

A machine is linked to a project by its folder (Path), else by its project name, else by its
order/serial number when that number appears in the project's documents. Machines that match
no indexed project are kept: the register also answers "which machines did we deliver to X"
for projects whose documents are not indexed.
"""

from __future__ import annotations

import csv
import os
import re
import unicodedata

REGISTER_VERSION = 2

TABLE = """
CREATE TABLE IF NOT EXISTS register_machines (
    id          INTEGER PRIMARY KEY,
    project     TEXT,      -- indexed project (doc_facets.project), NULL if none matches
    matched_by  TEXT,      -- path, name, order
    name        TEXT,      -- project name in the register (or its folder name)
    path        TEXT,
    order_no    TEXT,
    serial      TEXT,
    model       TEXT,
    family      TEXT,
    year        INTEGER,
    robots      INTEGER,
    cells       TEXT,
    controller  TEXT,
    camera      TEXT,
    client      TEXT,
    grp         TEXT,      -- client's group
    industry    TEXT,
    city        TEXT,
    country     TEXT,
    site        TEXT,
    agent       TEXT,
    maintenance TEXT,
    order_date  TEXT,
    status      TEXT,
    notes       TEXT,
    source      TEXT,
    client_key  TEXT,      -- folded client + group, for client= filters
    reg_project TEXT,      -- project the register itself points to, when the documents say otherwise
    reg_year    INTEGER,   -- year in the register, when the documents say otherwise
    family_ok   INTEGER NOT NULL DEFAULT 1,  -- 0: the documents name another machine family
    model_ok    INTEGER NOT NULL DEFAULT 1,  -- 0: the documents name another robot count
    doc_note    TEXT       -- what the documents say instead (they are more reliable than the register)
);
CREATE INDEX IF NOT EXISTS register_project ON register_machines(project);
"""
FIELDS = ("name", "path", "order_no", "serial", "model", "family", "year", "robots", "cells", "controller",
          "camera", "client", "grp", "industry", "city", "country", "site", "agent", "maintenance",
          "order_date", "status", "notes", "source")

_NUM = re.compile(r"(?<!\d)0?(1[02]0\d{6})(?!\d)")  # 120005849, 0100710291 (orders / serial numbers)
_SUFFIX = re.compile(r"(?:[\s_\-]*(?:old|bis|backup|doc))+$")
NUM_TYPES = ("offre", "commande", "cahier_des_charges", "fat", "sat", "reception", "mise_en_service",
             "liberation", "facture", "suivi", "cloture")


def fold(s: str | None) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode().lower()
    return " ".join(re.sub(r"[^a-z0-9]+", " ", s).split())


def _key(s: str | None) -> str:
    return fold(s).replace(" ", "")


def _base(s: str | None) -> str:
    return _key(_SUFFIX.sub("", fold(s)))


def _int(v: str) -> int | None:
    m = re.search(r"\d+", v or "")
    return int(m.group()) if m else None


def _family(model: str) -> str:
    from .machines import _FAMILY, FAMILIES

    m = _FAMILY.search(model or "")
    if m:
        return FAMILIES[m.group(1).lower()]
    return (model or "").split(" ")[0].title() if model else ""


def numbers(*values: str) -> set[str]:
    return {m.group(1) for v in values for m in _NUM.finditer(v or "")}


def load(path: str) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def refresh(con, path: str | None) -> bool:
    """Reload the register and relink it to the projects when the file or the documents changed."""
    from . import store

    con.executescript(TABLE)
    if "doc_note" not in {r[1] for r in con.execute("PRAGMA table_info(register_machines)")}:
        con.executescript("DROP TABLE register_machines;" + TABLE)  # table of version 1
    if path and os.path.isfile(path):
        st = os.stat(path)
        file_state = f"{st.st_mtime_ns}:{st.st_size}"
    else:
        file_state = "none"
    n, last = con.execute("SELECT count(*), max(indexed_at) FROM docs").fetchone()
    state = f"{REGISTER_VERSION}:{file_state}:{n}:{last}:{store.get_meta(con, 'machines_state')}"
    if store.get_meta(con, "register_state") == state:
        return False
    rows = load(path) if file_state != "none" else []
    projects = [p for (p,) in con.execute("SELECT DISTINCT project FROM doc_facets WHERE project IS NOT NULL")]
    by_key = {_key(p): p for p in projects}
    by_base: dict[str, str | None] = {}
    for p in projects:
        b = _base(p)
        by_base[b] = p if b not in by_base else None  # ambiguous: not used
    num_docs = _number_evidence(con) if rows else {}
    doc_models = _doc_models(con) if rows else {}
    doc_years = _doc_years(con) if rows else {}

    # When the register and the documents disagree, the documents win (the register was typed
    # by hand from several sources): the register value is kept as a note.
    out = []
    for r in rows:
        folder = (r.get("path") or "").rstrip("\\/").replace("/", "\\").split("\\")[-1]
        name = r.get("project") or folder
        project, how = None, None
        for value, method in ((folder, "path"), (r.get("project"), "name")):
            if value and not project:
                project = by_key.get(_key(value)) or by_base.get(_base(value))
                how = method if project else None
        nums = numbers(r.get("order_no"), r.get("serial"))
        reg_project = None
        owner = _by_number(nums, num_docs, strong=bool(project))
        if owner and project and owner != project and not any(project in num_docs.get(n, {}) for n in nums):
            reg_project, project, how = project, owner, "order"  # its number is in that project's files
        elif not project:
            project, how = owner, "order" if owner else None
        model = r.get("model", "")
        family = _family(model)
        notes, family_ok, model_ok = [], 1, 1
        if reg_project:
            notes.append(f"the register files it under {reg_project}; its number is in {project}'s documents")
        found = doc_models.get(project, {})
        if family in KNOWN_FAMILIES and found:
            families = {f for f, _ in found}
            with_model = {f for f, m in found if m}
            text = ", ".join([f"{m or f} ({d} docs)" for (f, m), d in sorted(found.items(), key=lambda kv: -kv[1])
                              if m or f not in with_model][:3])
            if family not in families:
                family_ok = model_ok = 0
                notes.append(f"the documents name {text}, not {model}")
            elif re.search(r"\d+R", model) and any(f == family and m for f, m in found) and not any(
                    m == model or m.startswith(model + " ") for _, m in found):
                model_ok = 0
                notes.append(f"the documents name {text}, not {model}")
        order_date = r.get("order_date") or ""
        reg_year = year = int(order_date[:4]) if re.match(r"\d{4}", order_date) else _int(r.get("year"))
        span = doc_years.get(project)
        if year and span and not (span[0] - 2 <= year <= span[1] + 1):
            notes.append(f"the register says {year}; the project's offers/orders/FAT/SAT are dated {span[0]}-{span[1]}")
            year = span[0]
        else:
            reg_year = None
        client, grp = r.get("client", ""), r.get("group", "")
        out.append((project, how, name, r.get("path", ""), r.get("order_no", ""), r.get("serial", ""),
                    model, family, year, _int(r.get("robots")),
                    r.get("cells", ""), r.get("controller", ""), r.get("camera", ""), client, grp,
                    r.get("industry", ""), r.get("city", ""), r.get("country", ""), r.get("site", ""),
                    r.get("agent", ""), r.get("maintenance", ""), order_date, r.get("status", ""),
                    r.get("notes", ""), r.get("source", ""), f" {fold(client)} | {fold(grp)} ",
                    reg_project, reg_year, family_ok, model_ok, "; ".join(notes)))
    autocommit = con.isolation_level is None
    if autocommit:
        con.execute("BEGIN")
    con.execute("DELETE FROM register_machines")
    cols = ("project", "matched_by") + FIELDS + ("client_key", "reg_project", "reg_year", "family_ok", "model_ok",
                                                "doc_note")
    con.executemany(f"INSERT INTO register_machines ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})", out)
    store.set_meta(con, "register_state", state)
    con.execute("COMMIT") if autocommit else con.commit()
    return True


def _number_evidence(con) -> dict[str, dict[str, list[int]]]:
    """number -> project -> [documents mentioning it, of which in the file name or path]."""
    ev: dict[str, dict[str, list[int]]] = {}
    seen: dict[int, set[str]] = {}
    proj: dict[int, str] = {}
    for doc_id, path, project in con.execute(
            "SELECT d.id, d.path, f.project FROM docs d JOIN doc_facets f ON f.doc_id = d.id "
            "WHERE f.project IS NOT NULL"):
        proj[doc_id] = project
        for n in numbers(path):
            seen.setdefault(doc_id, set()).add(n)
            ev.setdefault(n, {}).setdefault(project, [0, 0])[1] += 1
    marks = ",".join("?" * len(NUM_TYPES))
    for doc_id, text in con.execute(
            f"""SELECT c.doc_id, c.text FROM chunks c JOIN doc_facets f ON f.doc_id = c.doc_id
                WHERE f.doc_type IN ({marks}) AND c.page_no <= 2""", NUM_TYPES):
        seen.setdefault(doc_id, set()).update(numbers(text))
    for doc_id, nums in seen.items():
        for n in nums:
            ev.setdefault(n, {}).setdefault(proj[doc_id], [0, 0])[0] += 1
    return ev


KNOWN_FAMILIES = ("Paloma", "Presto", "Hector", "Delfi", "Astor", "Nestor", "FeedPlacer")
OWN_TYPES = ("offre", "commande", "cahier_des_charges", "fat", "sat", "reception", "mise_en_service", "liberation")


def _doc_models(con) -> dict[str, dict[tuple[str, str], int]]:
    """project -> {(family, model): documents} for the machines confirmed by the documents."""
    out: dict[str, dict] = {}
    for p, f, m, d in con.execute("SELECT project, family, model, docs FROM project_machines WHERE confirmed = 1"):
        out.setdefault(p, {})[(f, m)] = d
    return out


def _doc_years(con) -> dict[str, tuple[int, int]]:
    """project -> (first, last) year of its own offers/orders/FAT/SAT files (10% trimmed each side)."""
    from datetime import datetime

    years: dict[str, list[int]] = {}
    for p, t in con.execute(
            f"""SELECT f.project, d.mtime FROM docs d JOIN doc_facets f ON f.doc_id = d.id
                WHERE f.project IS NOT NULL AND d.mtime IS NOT NULL
                AND f.doc_type IN ({','.join('?' * len(OWN_TYPES))})""", OWN_TYPES):
        years.setdefault(p, []).append(datetime.fromtimestamp(t).year)
    out = {}
    for p, ys in years.items():
        if len(ys) >= 3:
            ys.sort()
            out[p] = (ys[len(ys) // 10], ys[-1 - len(ys) // 10])
    return out


def _by_number(nums: set[str], ev: dict, strong: bool = False) -> str | None:
    """The project whose documents carry these numbers - only if clearly its own. strong: enough
    evidence to overrule the register's own link (several file names or documents)."""
    score: dict[str, list[int]] = {}
    for n in nums:
        for p, (docs, named) in ev.get(n, {}).items():
            s = score.setdefault(p, [0, 0])
            s[0] += docs
            s[1] += named
    if not score:
        return None
    total = sum(s[0] for s in score.values())
    best, (docs, named) = max(score.items(), key=lambda kv: kv[1][0])
    enough = (named >= 2 or docs >= 4) if strong else (docs >= 2 or named >= 1)
    if enough and docs >= 0.6 * total:
        return best
    return None


# ------------------------------------------------------------------ queries
def client_clause(value: str, alias: str = "rm") -> tuple[str, list[str]]:
    """SQL condition for client= (client or group, any part of the name; comma = OR)."""
    values = [fold(v) for v in value.split(",") if fold(v)]
    if not values:
        return "1", []
    return "(" + " OR ".join(f"{alias}.client_key LIKE ?" for _ in values) + ")", [f"% {v} %" for v in values]


def country_clause(value: str, alias: str = "rm") -> tuple[str, list[str]]:
    values = [v.strip() for v in value.split(",") if v.strip()]
    return f"{alias}.country COLLATE NOCASE IN ({','.join('?' * len(values))})", values


def summary(con, project: str) -> str:
    """'ACME FOODS (France), 3 machines 2023' - one line for lists and search hits."""
    n, y0, y1 = con.execute("SELECT count(*), min(year), max(year) FROM register_machines WHERE project = ?",
                            (project,)).fetchone()
    if not n:
        return ""
    clients = con.execute("""SELECT client, max(country) FROM register_machines WHERE project = ? AND client != ''
                             GROUP BY client ORDER BY count(*) DESC""", (project,)).fetchall()
    if not clients:
        clients = [("client ?", con.execute("SELECT max(country) FROM register_machines WHERE project = ?",
                                            (project,)).fetchone()[0])]
    who = " / ".join(f"{c}{f' ({k})' if k else ''}" for c, k in clients[:2]) + (" ..." if len(clients) > 2 else "")
    years = "" if not y0 else f" {y0}" if y0 == y1 else f" {y0}-{y1}"
    return f"{who}, {n} machine{'s' if n > 1 else ''}{years}"

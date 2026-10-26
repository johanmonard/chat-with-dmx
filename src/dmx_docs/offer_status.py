"""Status, corrections and review list of the offer register (the end of `dmx-docs offers`).

- link_projects: an offer is won when its number is found in the projects world's documents (text or
  path, read from that world's local index, read-only) and a path, the project's client or its machine
  confirms it, won_unconfirmed when only the text of another client's project with another machine
  names it; else QuotationList.xls says Ordered or Cancelled; else it is open when its last revision is
  younger than OPEN_YEARS, else no order was found.
- apply_corrections: the user's corrections.csv, applied after every rebuild (they beat the documents);
  each row is kept in offer_corrections with whether it applied.
- write_review: review.csv, the rows a person should check (Excel: UTF-8 with BOM, ';').
"""

from __future__ import annotations

import csv
import io
import json
import logging
import os
import re
import sqlite3
import urllib.parse
from datetime import date
from pathlib import Path

from . import facets, machines, offerkeys, prices
from .register import fold

log = logging.getLogger(__name__)

OPEN_YEARS = 2  # no order found and younger than this: open
MAX_EVIDENCE = 10  # documents named in status_evidence
STATUSES = ("won", "won_unconfirmed", "no_order_found", "open", "unknown", "lost", "cancelled")
KINDS = ("machine", "option", "service", "discount", "other")
OFFER_TYPES = ("firm", "budget")
OFFER_FIELDS = ("client", "country", "status", "project", "offer_type", "title")
CORRECTIONS_TABLE = """CREATE TABLE offer_corrections (
    offer TEXT, revision TEXT, line INTEGER, field TEXT, value TEXT, note TEXT, who TEXT, date TEXT,
    applied INTEGER, message TEXT
)"""
REVIEW_COLUMNS = ("offer", "revision", "client", "issue", "detail", "path", "page")
# Notes of offer documents the index could not read (offers._read).
_UNREAD_NOTES = ("file error", "no text", "empty file", "not read")
_NO_DATE = "no date"  # status_evidence of an offer whose age is unknown
_FORMULA_START = ("=", "+", "-", "@", "\t", "\r")  # a cell Excel may run as a formula
_YES, _NO = ("yes", "y", "1", "true", "oui", "ja"), ("no", "n", "0", "false", "non", "nein", "")


def _years_before(day: date, years: int) -> date:
    try:
        return day.replace(year=day.year - years)
    except ValueError:  # 29 February
        return day.replace(year=day.year - years, day=28)


def _commit_if_own(con, own: bool) -> None:
    if own:  # called alone; inside run_offers' transaction the caller commits
        con.commit()


# --- Won / no order found / open -------------------------------------------------------------------

def number_forms(offer_key: str) -> tuple[str, re.Pattern]:
    """The full-text token to look for and the pattern that must match in the text or path:
    '3003929' -> ('3003929', 3003929 not inside a longer number); 'B-250306' -> ('250306', B-250306 or
    B250306: the bare six digits are dates); '1106033-A' -> ('1106033', 1106033-A or 1106033A)."""
    m = re.fullmatch(r"([BFE])-(\d{6})", offer_key)
    if m:
        return m.group(2), re.compile(rf"(?<![A-Za-z0-9]){m.group(1)}-?{m.group(2)}(?!\d)")
    m = re.fullmatch(r"(\d+)-([A-Z])", offer_key)
    if m:
        return m.group(1), re.compile(rf"(?<!\d){m.group(1)}-?{m.group(2)}(?![A-Za-z])")
    return offer_key, re.compile(rf"(?<!\d){re.escape(offer_key)}(?!\d)")


def _read_only_uri(path: str) -> str:
    """SQLite URI opening a database read-only, for a relative, local or UNC path (Python's sqlite refuses
    file://server/share/...: a UNC path is written file:////server/share/...)."""
    full = os.path.abspath(path)
    if full.startswith("\\\\"):
        return "file:" + urllib.parse.quote("//" + full.replace("\\", "/")) + "?mode=ro"
    return Path(full).as_uri() + "?mode=ro"


def _evidence(projects_db: str, keys: list[str]) -> dict[str, dict[str, set]]:
    """offer key -> project -> {(path, page or None)}: the pages and paths of the projects world that
    carry the number (found by full text, confirmed by number_forms' pattern)."""
    con = sqlite3.connect(_read_only_uri(projects_db), uri=True)
    try:
        found: dict[str, dict[str, set]] = {}
        for key in keys:
            token, pattern = number_forms(key)
            match = '"' + token.replace('"', '""') + '"'
            hits = [(path, page, project) for path, page, project, text in con.execute(
                "SELECT d.path, c.page_no, f.project, c.text FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
                "JOIN docs d ON d.id = c.doc_id JOIN doc_facets f ON f.doc_id = d.id "
                "WHERE chunks_fts MATCH ? AND f.project IS NOT NULL", (match,)) if pattern.search(text)]
            hits += [(path, None, project) for path, project in con.execute(
                "SELECT d.path, f.project FROM docs_fts JOIN docs d ON d.id = docs_fts.rowid "
                "JOIN doc_facets f ON f.doc_id = d.id WHERE docs_fts MATCH ? AND f.project IS NOT NULL", (match,))
                if pattern.search(path)]
            for path, page, project in hits:
                found.setdefault(key, {}).setdefault(project, set()).add((path, page))
        return found
    finally:
        con.close()


def _project_facts(projects_db: str) -> tuple[dict[str, set], dict[str, set]]:
    """project -> folded clients of the machine register, project -> confirmed machine families, from the
    projects world's index (empty when it has no such table)."""
    con = sqlite3.connect(_read_only_uri(projects_db), uri=True)
    try:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        clients: dict[str, set] = {}
        families: dict[str, set] = {}
        if "register_machines" in tables:
            for project, client in con.execute("SELECT project, client FROM register_machines "
                                               "WHERE project IS NOT NULL AND client IS NOT NULL"):
                clients.setdefault(project, set()).add(fold(client))
        if "project_machines" in tables:
            for project, family in con.execute("SELECT project, family FROM project_machines WHERE confirmed = 1"):
                families.setdefault(project, set()).add(family)
        return clients, families
    finally:
        con.close()


def _same_client(a: str, b: str) -> bool:
    """Folded names, one inside the other ('acme foods' and 'acme foods sa'); 3 characters at least."""
    return len(a) >= 3 and len(b) >= 3 and (a in b or b in a)


def _confirmed(hits: set, project: str, client: str | None, families: set[str], facts) -> bool:
    """A project's evidence confirms the order when the number is in a path (a document or folder name),
    or the project's register client is the offer's client, or its machine family is one of the offer's
    lines. A mention in the text alone may be a comparison with another client's offer."""
    clients, machines_of = facts
    return (any(page is None for _, page in hits)
            or any(_same_client(fold(client), c) for c in clients.get(project, ()))
            or bool(families & machines_of.get(project, set())))


def _won(by_project: dict[str, set], confirmed: set[str]) -> tuple:
    """(status, source, project, evidence, other projects): won when a project confirms it, else
    won_unconfirmed; the project is the confirming one with most evidence (ties: by name)."""
    ranked = sorted(by_project, key=lambda p: (p not in confirmed, -len(by_project[p]), p))
    items = [f"{path} p.{page}" if page else path
             for p in ranked for path, page in sorted(by_project[p], key=lambda e: (e[0], e[1] or 0))]
    more = len(items) - MAX_EVIDENCE
    text = "; ".join(items[:MAX_EVIDENCE]) + (f"; +{more} more" if more > 0 else "")
    status = "won" if confirmed else "won_unconfirmed"
    return status, "projects_world", ranked[0], text, "; ".join(ranked[1:]) or None


def _by_register_or_age(quotation_status: str | None, last_date: str | None, today: date) -> tuple:
    q = (quotation_status or "").strip().lower()
    if q == "ordered":
        return "won", "quotation_list", None, "QuotationList.xls: Ordered", None
    if q == "cancelled":
        return "cancelled", "quotation_list", None, "QuotationList.xls: Cancelled", None
    if not last_date:
        return "unknown", "none", None, _NO_DATE, None
    young = date.fromisoformat(last_date) >= _years_before(today, OPEN_YEARS)
    return "open" if young else "no_order_found", "age", None, None, None


def link_projects(con, projects_db: str | None, today: date | None = None) -> int:
    """Set every offer's status, status_source, project, status_evidence and other_projects; returns the
    number of offers marked won (won_unconfirmed not counted). Without a readable projects index every
    status is 'unknown' and status_evidence says why."""
    today = today or date.today()
    own = not con.in_transaction
    rows = con.execute("SELECT offer_key, last_date, quotation_status, client FROM offers").fetchall()
    families: dict[str, set] = {}  # offer key -> machine families of its lines
    for key, family in con.execute("SELECT DISTINCT r.offer_key, l.family FROM offer_lines l JOIN offer_revisions r "
                                   "ON r.id = l.revision_id WHERE l.family IS NOT NULL"):
        families.setdefault(key, set()).add(family)
    found, facts, why = {}, ({}, {}), None
    if not projects_db:
        why = "no projects world index"
    elif not os.path.isfile(projects_db):
        why = f"projects world index not found: {projects_db}"
    else:
        try:
            found = _evidence(projects_db, [r[0] for r in rows])
            facts = _project_facts(projects_db)
        except (sqlite3.Error, ValueError, OSError) as e:  # an index from before the facets, a locked file
            why = f"projects world index unreadable: {e}"
    if why:
        log.warning("offer status unknown: %s", why)
    updates = []
    for key, last_date, quotation_status, client in rows:
        if why:
            status = ("unknown", "none", None, why, None)
        elif key in found:
            confirmed = {p for p, hits in found[key].items()
                         if _confirmed(hits, p, client, families.get(key, set()), facts)}
            status = _won(found[key], confirmed)
        else:
            status = _by_register_or_age(quotation_status, last_date, today)
        updates.append(status + (key,))
    con.executemany("UPDATE offers SET status=?, status_source=?, project=?, status_evidence=?, other_projects=? "
                    "WHERE offer_key=?", updates)
    _commit_if_own(con, own)
    return sum(u[0] == "won" for u in updates)


# --- Corrections ----------------------------------------------------------------------------------

def _value(v: str | None) -> str | None:
    v = (v or "").strip()
    return None if v in ("", "-") else v


def _offer_of(text: str) -> str:
    """Offer key of the correction's offer cell; Excel writes 0904023 as 904023."""
    if text.isdigit() and len(text) < 7:
        text = text.zfill(7)
    return offerkeys.offer_key(text) or text


def _revision_of(text: str | None) -> str | None:
    """'0' -> '00' (Excel drops the zero), 'v1' -> 'V1'; a variant after '/' is kept as written ('03/Vandelay')."""
    if not text:
        return None
    label, slash, variant = text.partition("/")
    label = label.strip()
    label = label.zfill(2) if label.isdigit() and len(label) < 2 else label.upper()
    return label + (f"/{variant.strip()}" if slash else "")


def _date_of(text: str) -> str | None:
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        m = re.fullmatch(r"(\d{1,2})[./](\d{1,2})[./](\d{4})", text)
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))).isoformat() if m else None
        except ValueError:
            return None


def _read_corrections(path: str) -> list[dict]:
    """Rows of corrections.csv (UTF-8 with or without BOM, else ANSI cp1252 as Excel saves it; ';' or ','
    separated) as dicts of stripped cells; empty rows left out. Raises ValueError when a needed column is missing."""
    try:
        text = Path(path).read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = Path(path).read_text(encoding="cp1252")
    head = text.split("\n", 1)[0]
    reader = csv.reader(io.StringIO(text), delimiter=";" if head.count(";") >= head.count(",") else ",")
    header = [h.strip().lower() for h in next(reader, [])]
    missing = [c for c in ("offer", "field") if c not in header]
    if missing:
        raise ValueError(f"missing columns {', '.join(missing)}")
    return [{h: (cells[i].strip() if i < len(cells) else "") for i, h in enumerate(header)}
            for cells in reader if any(c.strip() for c in cells)]


def _refresh_dates(con, offer: str) -> None:
    """The offer's first/last date and year from its revisions that are not ignored (as offers._build_offer:
    file dates only when no revision has another date); an age status follows the new last date."""
    revs = con.execute("SELECT date, date_source FROM offer_revisions WHERE offer_key = ? AND date IS NOT NULL "
                       "AND NOT ifnull(ignored, 0)", (offer,)).fetchall()
    dates = sorted([d for d, src in revs if src != "file"] or [d for d, _ in revs])
    last = dates[-1] if dates else None
    con.execute("UPDATE offers SET first_date = ?, last_date = ?, year = ? WHERE offer_key = ?",
                (dates[0] if dates else None, last, int(last[:4]) if last else None, offer))
    row = con.execute("SELECT status_source, status_evidence, quotation_status FROM offers WHERE offer_key = ?",
                      (offer,)).fetchone()
    if row[0] == "age" or (row[0] == "none" and row[1] == _NO_DATE):  # no project evidence: the age decides
        status, source, _, evidence, _ = _by_register_or_age(row[2], last, date.today())
        con.execute("UPDATE offers SET status = ?, status_source = ?, status_evidence = ? WHERE offer_key = ?",
                    (status, source, evidence, offer))


def _correct_offer(con, offer: str, field: str, value: str | None) -> str | None:
    if field not in OFFER_FIELDS:
        return f"unknown field '{field}'"
    if field == "status":
        value = (value or "").lower()
        if value not in STATUSES:
            return f"unknown status '{value}'"
        con.execute("UPDATE offers SET status = ?, status_source = 'correction' WHERE offer_key = ?", (value, offer))
    elif field == "country":
        con.execute("UPDATE offers SET country = ?, country_source = 'correction' WHERE offer_key = ?",
                    (offerkeys.country_en(value or "") or value, offer))
    elif field == "offer_type" and (value or "").lower() not in OFFER_TYPES:
        return f"unknown offer type '{value}'"
    else:
        con.execute(f"UPDATE offers SET {field} = ? WHERE offer_key = ?",
                    (value.lower() if field == "offer_type" else value, offer))
    return None


def _correct_revision(con, offer: str, ids: list[int], field: str, value: str | None) -> str | None:
    marks = ",".join("?" * len(ids))
    if field == "date":
        when = _date_of(value or "")
        if not when:
            return f"date '{value or ''}' is not a date"
        con.execute(f"UPDATE offer_revisions SET date = ?, date_source = 'correction' WHERE id IN ({marks})",
                    (when, *ids))
    elif field == "variant":
        con.execute(f"UPDATE offer_revisions SET variant = ? WHERE id IN ({marks})", (value, *ids))
    elif field == "ignore":
        flag = (value or "").lower()
        if flag not in _YES + _NO:
            return f"ignore '{value}' must be yes or no"
        con.execute(f"UPDATE offer_revisions SET ignored = ? WHERE id IN ({marks})", (int(flag in _YES), *ids))
    else:
        return f"unknown field '{field}'"
    _refresh_dates(con, offer)
    return None


def _correct_line(con, line_id: int, amounts: str | None, field: str, value: str | None) -> str | None:
    if field == "delete":
        flag = (value or "").lower()
        if flag not in _YES + _NO:
            return f"delete '{value}' must be yes or no"
        if flag in _YES:
            con.execute("DELETE FROM offer_lines WHERE id = ?", (line_id,))
        return None
    if field == "text":
        sets = {"text": value}
    elif field == "kind":
        if (value or "").lower() not in KINDS:
            return f"unknown kind '{value}'"
        sets = {"kind": value.lower()}
    elif field == "model":
        families = sorted(f for f, _ in machines.models_in(value or ""))
        sets = {"model": value, "family": families[0] if families else None}
    elif field == "quantity":
        if value is not None and not value.isdigit():
            return f"quantity '{value}' is not a number"
        sets = {"quantity": int(value) if value else None}
    elif re.fullmatch(r"amount:[A-Z]{3}", field):
        currency, current = field[7:].upper(), json.loads(amounts or "{}")
        if value is None:
            current.pop(currency, None)
        else:
            number = prices.parse_number(value)
            if number is None:
                return f"amount '{value}' is not a number"
            current[currency] = number
        sets = {"amounts": json.dumps(current)}
    else:
        return f"unknown field '{field}'"
    con.execute(f"UPDATE offer_lines SET {', '.join(f'{k} = ?' for k in sets)}, read_by = 'correction' WHERE id = ?",
                (*sets.values(), line_id))
    return None


def _revision_ids(con, offer: str, revision: str) -> tuple[list[int], str | None]:
    """Rows of a revision ('03') or of one of its variants ('03/Vandelay', any case), with their translations;
    a bare label is refused when the revision has several variants."""
    label, slash, variant = revision.partition("/")
    rows = con.execute("SELECT id, variant, translation_of FROM offer_revisions WHERE offer_key = ? AND revision = ? "
                       "ORDER BY id", (offer, label)).fetchall()
    if slash:
        main = {r[0] for r in rows if (r[1] or "").casefold() == variant.casefold()}
        rows = [r for r in rows if r[0] in main or r[2] in main]
    if not rows:
        return [], f"no revision '{revision}'"
    variants = list(dict.fromkeys(r[1] for r in rows if r[1]))
    if not slash and len(variants) > 1:
        return [], f"revision '{label}' has variants {', '.join(variants)}: write {label}/<variant>"
    return [r[0] for r in rows], None


def _correct(con, offer: str, revision: str | None, line: str | None, field: str, value: str | None) -> str | None:
    """Apply one correction; the problem when it cannot be applied."""
    if not con.execute("SELECT 1 FROM offers WHERE offer_key = ?", (offer,)).fetchone():
        return "no such offer"
    if revision is None:
        return _correct_offer(con, offer, field, value) if line is None else f"line {line} needs a revision"
    ids, problem = _revision_ids(con, offer, revision)
    if problem:
        return problem
    if line is None:
        return _correct_revision(con, offer, ids, field, value)
    if not line.isdigit():
        return f"line '{line}' is not a number"
    lines = con.execute(f"SELECT id, amounts FROM offer_lines WHERE position = ? AND revision_id IN "
                        f"({','.join('?' * len(ids))})", (int(line), *ids)).fetchall()
    if not lines:
        return f"no line {line} in revision '{revision}'"
    for line_id, amounts in lines:  # variants of the revision: each one's line
        problem = _correct_line(con, line_id, amounts, field, value)
        if problem:
            return problem
    return None


def apply_corrections(con, path: str | None) -> list[str]:
    """Apply corrections.csv (columns offer, revision, line, field, value, note, who, date; '-' or empty
    revision and line = the offer itself) to the register; every row is kept in offer_corrections.
    Returns a message per correction that could not be applied (no exception for a bad row)."""
    own = not con.in_transaction
    con.execute("DROP TABLE IF EXISTS offer_corrections")
    con.execute(CORRECTIONS_TABLE)
    if not path or not os.path.isfile(path):
        _commit_if_own(con, own)
        return []
    try:
        rows = _read_corrections(path)
    except (OSError, UnicodeDecodeError, ValueError, csv.Error) as e:
        _commit_if_own(con, own)
        return [f"corrections file {path} not read: {e}"]
    messages = []
    for row in rows:
        offer = _offer_of(row.get("offer", ""))
        revision, line = _revision_of(_value(row.get("revision"))), _value(row.get("line"))
        field = row.get("field", "").strip().lower()
        field = "amount:" + field[7:].upper() if field.startswith("amount:") else field
        value = row.get("value", "").strip() or None
        problem = _correct(con, offer, revision, line, field, value)
        message = f"correction for offer {offer}: {problem}" if problem else None
        if message:
            messages.append(message)
        con.execute("INSERT INTO offer_corrections(offer, revision, line, field, value, note, who, date, applied, "
                    "message) VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (offer, revision, int(line) if line and line.isdigit() else line, field, value,
                     row.get("note") or None, row.get("who") or None, row.get("date") or None,
                     int(problem is None), message))
    _commit_if_own(con, own)
    return messages


# --- Review list ----------------------------------------------------------------------------------

def _revision_issue(r) -> str | None:
    """The one issue of a revision, from its note (offers._read), sum check and confidence."""
    note = r["note"] or ""
    parts = note.split("; ")
    if note.startswith(_UNREAD_NOTES):
        return "unread document"
    if any(p.startswith("model not asked") for p in parts):
        return "model not asked"  # the run's time limit: the next run reads it
    if any(p.startswith("no price section") for p in parts):  # also '...: price page 13 has no amounts in its text'
        return "no price section"  # the note says when the model could not be asked
    if any(p.startswith("model error: prompt cut") for p in parts):
        return "prompt too long for the model"  # running again does not help: a larger window or fewer pages
    if any(p.startswith(("model unavailable", "model error")) for p in parts):
        return "model unavailable"  # the rules' lines did not add up and the model was not asked: run again
    if r["sum_ok"] == 0:
        return "sum mismatch"
    return "low confidence" if r["confidence"] == "low" else None


def _revision_issues(con) -> list[tuple]:
    rows = []
    for r in con.execute("""SELECT r.offer_key, r.revision, o.client, r.path, r.pages, r.note, r.confidence, r.sum_ok,
                                   r.totals, r.read_by
                            FROM offer_revisions r LEFT JOIN offers o ON o.offer_key = r.offer_key
                            WHERE NOT ifnull(r.ignored, 0)"""):
        issue = _revision_issue(r)
        if issue:
            reasons = [p for p in (r["note"] or "").split("; ") if p]  # every reason; the issue is the first that applies
            if r["sum_ok"] == 0 and "sum check failed" not in reasons:
                reasons.append("sum check failed")
            if r["confidence"] == "low":
                reasons.append("low confidence")
            reasons += [f"read by {r['read_by']}"] + ([f"printed totals {r['totals']}"] if r["totals"] else [])
            detail = " | ".join(reasons)
            rows.append((r["offer_key"], r["revision"], r["client"], issue, detail, r["path"], r["pages"]))
    return rows


def _offer_issues(con) -> list[tuple]:
    rows = []
    for o in con.execute("SELECT offer_key, client, folder, duplicate_of, project, other_projects, status_evidence, "
                         "status FROM offers WHERE duplicate_of IS NOT NULL OR other_projects IS NOT NULL "
                         "OR status = 'won_unconfirmed'"):
        if o["status"] == "won_unconfirmed":
            rows.append((o["offer_key"], None, o["client"], "unconfirmed project link",
                         f"number only in the text of {o['project']} (not in a path; the project's client and "
                         f"machine are not the offer's): {o['status_evidence']}", o["folder"], None))
        if o["duplicate_of"]:
            rows.append((o["offer_key"], None, o["client"], "duplicate offer number", f"also in {o['duplicate_of']}",
                         o["folder"], None))
        if o["other_projects"]:
            rows.append((o["offer_key"], None, o["client"], "several projects",
                         f"linked to {o['project']}, also found in {o['other_projects']}: {o['status_evidence']}",
                         o["folder"], None))
    for l in con.execute("""SELECT r.offer_key, r.revision, o.client, l.text, r.path, l.page FROM offer_lines l
                            JOIN offer_revisions r ON r.id = l.revision_id
                            LEFT JOIN offers o ON o.offer_key = r.offer_key
                            WHERE l.kind = 'machine' AND l.model IS NULL AND l.family IS NULL
                              AND NOT ifnull(r.ignored, 0)"""):
        rows.append((l[0], l[1], l[2], "unknown machine name", l[3], l[4], l[5]))
    for r in con.execute("""SELECT r.offer_key, r.revision, o.client, r.path, r.pages FROM offer_revisions r
                            LEFT JOIN offers o ON o.offer_key = r.offer_key WHERE NOT ifnull(r.ignored, 0)"""):
        if offerkeys.offer_key(os.path.basename(r[3] or "")) != r[0]:  # read because none of the revision carries it
            rows.append((r[0], r[1], r[2], "offer document without number",
                         "no offer number in the file name: check that it is our offer", r[3], r[4]))
    if con.execute("SELECT 1 FROM sqlite_master WHERE name = 'offer_corrections'").fetchone():
        for c in con.execute("""SELECT c.offer, c.revision, o.client, c.message, c.field, c.value, c.who, c.date
                                FROM offer_corrections c LEFT JOIN offers o ON o.offer_key = c.offer
                                WHERE NOT c.applied"""):
            detail = " ".join(filter(None, (f"{c[3]} ({c[4]} = {c[5] or ''})", c[6], c[7])))
            rows.append((c[0], c[1], c[2], "stale correction", detail, None, None))
    return rows


def _documents(n: int) -> str:
    return f"{n} document" + ("s" if n != 1 else "")


def _folder_issues(con) -> list[tuple]:
    """Offer numbers whose folder holds no offer document, and offer folders without a number."""
    rows = []
    by_key: dict[str, list] = {}
    for key, client, path, doc_type in con.execute(
            "SELECT f.offer, f.client, d.path, f.doc_type FROM doc_facets f JOIN docs d ON d.id = f.doc_id "
            "WHERE f.offer IS NOT NULL AND f.offer NOT IN (SELECT offer_key FROM offers) ORDER BY d.path"):
        by_key.setdefault(key, []).append((client, path, doc_type))
    for key, docs in by_key.items():
        if any(t == "offer" for _, _, t in docs):
            continue  # the register will list it: built before this index update
        kinds = sorted({t or "other" for _, _, t in docs})
        try:
            folder = os.path.commonpath([os.path.dirname(p) for _, p, _ in docs])
        except ValueError:  # folders on different drives or roots
            folder = os.path.dirname(docs[0][1])
        rows.append((key, None, docs[0][0], "no offer document",
                     f"{_documents(len(docs))}, none is an offer ({', '.join(kinds)})", folder, None))

    root_keys = [(key, p) for p, key in con.execute("SELECT path, path_key FROM roots")]
    folders: dict[str, list] = {}  # folder -> [client, documents, has a number]
    for path, path_key, client, category, key in con.execute(
            "SELECT d.path, d.path_key, f.client, f.category, f.offer FROM docs d JOIN doc_facets f ON f.doc_id = d.id "
            "WHERE f.category IN ('client', 'archive', 'partner', 'unknown_client')"):
        root = facets.root_of(path_key, root_keys)
        if not root:
            continue
        parts = os.path.relpath(path, root).split(os.sep)[:-1]
        i = next((i for i in range(1, len(parts)) if fold(parts[i - 1]) == "offres"
                  or (i == 1 and category == "unknown_client")), None)
        if i is None:
            continue
        entry = folders.setdefault(os.path.join(root, *parts[:i + 1]), [client, 0, False])
        entry[1] += 1
        entry[2] = entry[2] or key is not None
    for folder, (client, n, numbered) in folders.items():
        if not numbered:
            rows.append((None, None, client, "unknown offer folder",
                         f"{_documents(n)}, no offer number in the folder name", folder, None))
    return rows


def _cell(v) -> str:
    """A review cell: ';' and line breaks become ',' and a space; text Excel would run as a formula
    (document text, paths) starts with a quote."""
    text = str(v) if v is not None else ""
    text = "'" + text if text.startswith(_FORMULA_START) else text
    return re.sub(r"[\r\n]+", " ", text).replace(";", ",")


def write_review(con, path: str, loaded=None) -> int:
    """review.csv (UTF-8 with BOM, ';'-separated, for Excel): one row per thing to check, with the offer,
    revision, client, issue, detail, path and page. Returns the number of rows."""
    rows = _revision_issues(con) + _offer_issues(con) + _folder_issues(con)
    if loaded is not None:  # verified records against the register's reading (verified.py)
        from . import verified

        rows += verified.review_rows(con, loaded)
    rows.sort(key=lambda r: tuple(_cell(v) for v in (r[0], r[1], r[3], r[5])))
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        out = csv.writer(f, delimiter=";")
        out.writerow(REVIEW_COLUMNS)
        out.writerows([_cell(v) for v in r] for r in rows)
    return len(rows)

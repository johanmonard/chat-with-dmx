"""The tools of the offers world over the offer register (offers.py, offer_status.py).

- list_offers: one line per offer, newest first.
- offer_card: everything the register knows about one offer.
- offer_lines: the rows of a price chart (';'-separated), one per priced line.
- offer_stats: offers and revisions counted by one or two keys (';'-separated).

Each takes a connection to the world's index and returns text. Every list ends with the coverage line:
how many offers matched, how many have readable prices, which were left out and why. Amounts are shown
per currency exactly as stored, never converted. Translations have no lines and never count; revisions
ignored by a correction are left out as if they did not exist.
"""

from __future__ import annotations

import itertools
import json
import re
from collections import Counter

from . import offerkeys, store
from .offer_status import KINDS, OFFER_TYPES, STATUSES
from .register import fold

EMPTY_REGISTER = ("The offer register is empty on this index: run the offers step (dmx-docs offers, "
                  "or launcher 7 for the offers world).")
LINE_COLUMNS = ("date", "offer", "revision", "variant", "kind", "client", "country", "status", "quantity", "amount",
                "unit_price", "currency", "line", "path", "page", "confidence")
DEFAULT_KIND_NOTE = "kind machine (default with machine=)"
UNCHECKED = "{} offers in scope have no readable prices and could not be checked for this machine"
GROUP_KEYS = ("year", "country", "client", "machine", "status", "author", "partner", "offer_type", "category")
WON_NOTE = "Final negotiated price: see the project's 0_Vente in the projects world."
UNCONFIRMED_NOTE = ("Won unconfirmed: the number is only in the text of a project whose client and machine are not "
                    "the offer's (a comparison?): check the evidence before counting it as won.")
MAX_LEFT_OUT = 30  # offers named in the "Left out:" line
MAX_DOCUMENTS = 50  # other documents listed in offer_card
# A ';'-table cell Excel may run as a formula; '-' is kept: negative amounts must stay numbers.
_FORMULA_START = ("=", "+", "@", "\t", "\r")


def _ready(con) -> bool:
    return all(store.has_table(con, t) for t in ("offers", "offer_revisions", "offer_lines"))


# --- Values and filters ---------------------------------------------------------------------------

def _cell(v) -> str:
    """A ';'-table cell: ';' and line breaks become ',' and a space; text Excel would run as a formula
    starts with a quote."""
    text = "" if v is None else str(v)
    text = "'" + text if text.startswith(_FORMULA_START) else text
    return re.sub(r"[\r\n]+", " ", text).replace(";", ",")


def _num(v) -> str:
    """A number for a ';'-table: 180000, 1250.5, -9000."""
    if v is None:
        return ""
    v = float(v)
    return str(int(v)) if v.is_integer() else f"{v:.2f}"


def _money(currency: str, v: float) -> str:
    """'CHF 180,000' (cents only when there are some)."""
    return f"{currency} {v:,.0f}" if float(v).is_integer() else f"{currency} {v:,.2f}"


def _amounts(text: str | None) -> dict[str, float]:
    try:
        return json.loads(text or "{}") or {}
    except ValueError:
        return {}


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def _matcher(query: str | None):
    """Case- and accent-insensitive substring match of any comma-separated value, or None (no filter)."""
    wanted = [w for w in (fold(v) for v in str(query or "").split(",")) if w]
    if not wanted:
        return None

    def match(*texts) -> bool:
        return any(w in fold(t) for t in texts if t for w in wanted)
    return match


def _choices(value: str | None, allowed, name: str) -> set[str] | None:
    """Comma-separated exact values (any case), checked against the allowed ones; None = no filter."""
    values = {v.strip().lower() for v in str(value or "").split(",") if v.strip()}
    unknown = sorted(values - set(allowed)) if allowed else []
    if unknown:
        raise ValueError(f"unknown {name} {', '.join(unknown)}: use {', '.join(allowed)}")
    return values or None


def _year(day: str | None) -> int | None:
    return int(day[:4]) if day else None


def _in_years(year: int, year_from, year_to) -> bool:
    return (not year_from or year >= int(year_from)) and (not year_to or year <= int(year_to))


# --- The register in memory -----------------------------------------------------------------------

class _Offer:
    """One offer with its revisions (translations excluded) and their priced lines."""

    def __init__(self, row, revisions: list, lines: dict[int, list], facet_machines: set[str]):
        self.row, self.key = row, row["offer_key"]
        self.revisions = revisions  # rev_order, then document order
        self._lines = lines
        self.facet_machines = facet_machines  # machine names in the paths of the offer's documents

    def lines(self, revision) -> list:
        return self._lines.get(revision["id"], [])

    @property
    def usable(self) -> list:
        return [r for r in self.revisions if not r["ignored"]]

    @property
    def last(self) -> list:
        """The last revision not ignored: every variant of it."""
        usable = self.usable
        return [r for r in usable if r["rev_order"] == usable[-1]["rev_order"]] if usable else []

    @property
    def readable(self) -> bool:
        return any(self.lines(r) for r in self.usable)

    def all_lines(self) -> list:
        return [l for r in self.revisions for l in self.lines(r)]


def _allowed(allows, path: str | None) -> bool:
    """True when no folder rule is given, or the path is inside the indexed, not excluded folders."""
    return allows is None or (bool(path) and allows(store.path_key(path)))


def _load(con, allows=None) -> list[_Offer]:
    """The register; with `allows` (Config.allows), revisions in folders excluded since the register was
    built are dropped, and the offers left without any revision (or whose folder is excluded)."""
    revisions: dict[str, list] = {}
    dropped: set[str] = set()
    for r in con.execute("SELECT * FROM offer_revisions WHERE translation_of IS NULL "
                         "ORDER BY offer_key, rev_order, id"):
        if _allowed(allows, r["path"]):
            revisions.setdefault(r["offer_key"], []).append(r)
        else:
            dropped.add(r["offer_key"])
    lines: dict[int, list] = {}
    for l in con.execute("SELECT * FROM offer_lines ORDER BY revision_id, position, id"):
        lines.setdefault(l["revision_id"], []).append(l)
    machines: dict[str, set] = {}
    for offer, names, path in con.execute("SELECT f.offer, f.machine, d.path FROM doc_facets f JOIN docs d "
                                          "ON d.id = f.doc_id WHERE f.offer IS NOT NULL AND f.machine IS NOT NULL"):
        if _allowed(allows, path):
            machines.setdefault(offer, set()).update(n for n in names.split("|") if n)
    out = []
    for o in con.execute("SELECT * FROM offers ORDER BY offer_key"):
        key = o["offer_key"]
        if key in revisions or (key not in dropped and _allowed(allows, o["folder"])):
            out.append(_Offer(o, revisions.get(key, []), lines, machines.get(key, set())))
    return out


def _offers(con, client=None, country=None, machine=None, statuses=None, types=None, categories=None,
            text=None, allows=None) -> list[_Offer]:
    """The offers passing the filters: client, country, machine (family or model of a line, or in the
    documents' paths) and text are substrings (case and accents ignored, comma = or); the sets are exact."""
    by_client, by_country, by_machine, by_text = map(_matcher, (client, country, machine, text))
    out = []
    for o in _load(con, allows):
        r = o.row
        if ((by_client and not by_client(r["client"])) or (by_country and not by_country(r["country"]))
                or (statuses and r["status"] not in statuses) or (types and r["offer_type"] not in types)
                or (categories and r["category"] not in categories)):
            continue
        if by_machine and not (any(by_machine(l["model"], l["family"]) for l in o.all_lines())
                               or by_machine(*o.facet_machines)):
            continue
        if by_text and not by_text(o.key, r["title"], r["client"], r["site"], r["folder"],
                                   *(l["text"] for l in o.all_lines())):
            continue
        out.append(o)
    return out


def _named(names: list[str]) -> str:
    more = len(names) - MAX_LEFT_OUT
    return ", ".join(names[:MAX_LEFT_OUT]) + (f", +{more} more" if more > 0 else "")


class _Coverage:
    """Offers matched by the filters, and what was left out with its reason: whole offers ('3001234'),
    or parts of an offer that has other points ('3001234/03 Wonka', '3001234/03 Wonka line 2').
    With a machine filter, `unchecked` names the offers in scope that have no readable prices: whether
    they quote that machine cannot be told. `notes` are added to the coverage line."""

    def __init__(self):
        self.matched: list[_Offer] = []
        self.left: list[tuple[str, str]] = []
        self.unchecked: list[str] = []
        self.notes: list[str] = []

    def leave(self, name: str, reason: str) -> None:
        self.left.append((name, reason))

    def lines(self) -> list[str]:
        counts = Counter(why for _, why in self.left)
        reasons = ", ".join(f"{n} {why}" for why, n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))
        clauses = [UNCHECKED.format(len(self.unchecked))] if self.unchecked else []
        out = ["; ".join([f"Coverage: {len(self.matched)} offers matched; {sum(o.readable for o in self.matched)} "
                          f"with readable prices; left out: {len(self.left)} ({reasons or 'none'})"]
                         + clauses + self.notes)]
        if self.left:
            out.append("Left out: " + _named([f"{name} ({why})" for name, why in self.left]))
        if self.unchecked:
            out.append("Not checked for this machine: " + _named(self.unchecked))
        return out


def _unchecked(o: _Offer, year_from, year_to) -> bool:
    """An offer without readable prices that the year filter does not exclude (its year may be unknown)."""
    year = o.row["year"]
    return not o.readable and (year is None or _in_years(year, year_from, year_to))


# --- list_offers ----------------------------------------------------------------------------------

def _machine_lines(o: _Offer) -> list[str]:
    """'2x Paloma 3R' for each machine line of the last revision (model, else family, else its text)."""
    names = []
    for r in o.last:
        for l in o.lines(r):
            if l["kind"] == "machine":
                name = l["model"] or l["family"] or " ".join((l["text"] or "").split())[:40]
                names.append(f"{l['quantity']}x {name}" if (l["quantity"] or 0) > 1 else name)
    return list(dict.fromkeys(names))


def _totals(r) -> str:
    return ", ".join(_money(c, v) for c, v in _amounts(r["totals"]).items())


def _list_line(o: _Offer) -> str:
    r = o.row
    priced = [(rev, _totals(rev)) for rev in o.last if _totals(rev)]
    if len(priced) > 1:  # variants side by side, never summed
        total = "total " + " / ".join(f"{t} ({rev['variant'] or rev['revision']})" for rev, t in priced)
    else:
        total = f"total {priced[0][1]}" if priced else "no total read"
    status = (r["status"] or "unknown") + (f" -> {r['project']}" if r["project"] else "")
    # The source of the last revision: its priced documents (every variant), else every document of it.
    source = [rev for rev in o.last if o.lines(rev)] or o.last
    return " · ".join((o.key, (r["client"] or "client ?") + (f" / {r['site']}" if r["site"] else ""),
                       r["country"] or "country ?", f"{r['first_date'] or '?'}..{r['last_date'] or '?'}",
                       _plural(len({rev["rev_order"] for rev in o.usable}), "revision"),
                       ", ".join(_machine_lines(o)) or "machine ?", total, status,
                       " / ".join(rev["path"] + (f" p.{rev['pages']}" if rev["pages"] else "") for rev in source)
                       or "no revision"))


def list_offers(con, client: str | None = None, country: str | None = None, machine: str | None = None,
                status: str | None = None, year_from: int | None = None, year_to: int | None = None,
                text: str | None = None, limit: int = 100, allows=None) -> str:
    """One line per offer, newest first, ending with the last revision's document and pages; then the
    coverage line. `allows` (Config.allows) hides the folders excluded since the register was built."""
    statuses = _choices(status, STATUSES, "status")
    if not _ready(con):
        return EMPTY_REGISTER
    found = _offers(con, client=client, country=country, machine=machine, statuses=statuses, text=text,
                    allows=allows)
    cov, shown = _Coverage(), []
    for o in sorted(found, key=lambda o: (o.row["last_date"] or "", o.key), reverse=True):
        if year_from or year_to:
            if o.row["year"] is None:
                cov.matched.append(o)
                cov.leave(o.key, "no date")
                continue
            if not _in_years(o.row["year"], year_from, year_to):
                continue
        cov.matched.append(o)
        shown.append(o)
    limit = max(1, int(limit))
    for o in shown[limit:]:
        cov.leave(o.key, "over the limit")
    out = [_list_line(o) for o in shown[:limit]] or ["No offer matches these filters."]
    if len(shown) > limit:
        out.append(f"... {len(shown) - limit} more offers (raise limit=)")
    return "\n".join(out + cov.lines())


# --- offer_card -----------------------------------------------------------------------------------

def _card_line(l) -> str:
    parts = [f"{l['position']}. {' '.join((l['text'] or '').split())}", l["kind"], l["model"] or l["family"],
             f"qty {l['quantity']}" if l["quantity"] else None]
    parts += [_money(c, v) for c, v in _amounts(l["amounts"]).items()]
    page = f" (p.{l['page']})" if l["page"] else ""
    return "  " + " · ".join(p for p in parts if p) + page + (" (corrected)" if l["read_by"] == "correction" else "")


def _revision_line(r) -> str:
    label = (r["revision"] or "?") + (f" ({r['variant']})" if r["variant"] else "")
    bits = [label, f"{r['date']} ({r['date_source']})" if r["date"] else "date ?",
            r["path"] + (f" p.{r['pages']}" if r["pages"] else ""),
            f"totals {_totals(r)}" if _totals(r) else "no total read",
            f"confidence {r['confidence']}", f"read by {r['read_by']}", r["note"],
            "ignored by correction" if r["ignored"] else None]
    return "- " + " · ".join(b for b in bits if b)


def _correction_line(c) -> str:
    target = c["revision"] or "offer"
    target += f" line {c['line']}" if c["line"] is not None else ""
    by = ", ".join(v for v in (c["who"], c["date"]) if v)
    text = f"- {target} {c['field']} = {c['value'] or ''}" + (f" ({by})" if by else "")
    return text + (f": {c['note']}" if c["note"] else "") + (f" -> {c['message']}" if c["message"] else "")


def _facts(o, allows=None) -> list[str]:
    """The offer's own lines of offer_card: number, client, folder, dates, status and its evidence."""
    def opt(text: str, value) -> str:
        return text.format(value) if value else ""

    key = o["offer_key"]
    others = "; ".join(f for f in (o["duplicate_of"] or "").split("; ") if f and _allowed(allows, f))
    out = [f"Offer {key}" + opt(" - {}", o["title"])
           + (f" (written {o['number_written']})" if o["number_written"] not in (None, key) else ""),
           f"Client: {o['client'] or '?'}" + opt(" / {}", o["site"]) + f" · country {o['country'] or '?'}"
           + opt(" (from {})", o["country_source"]) + f" · category {o['category'] or '?'}"
           + opt(" · partner {}", o["partner"]),
           f"Folder: {o['folder']}" + opt(" (the number is also used in {})", others),
           f"Dates: {o['first_date'] or '?'}..{o['last_date'] or '?'} · type {o['offer_type'] or '?'}"
           + opt(" · author {}", o["author"]),
           f"Offer status {o['status'] or 'unknown'}" + opt(" -> {}", o["project"])
           + opt(" (from {})", o["status_source"]) + opt("; also found in {}", o["other_projects"]),
           opt("Status evidence: {}", o["status_evidence"]), opt("QuotationList.xls: {}", o["quotation_status"]),
           WON_NOTE if o["status"] == "won" else UNCONFIRMED_NOTE if o["status"] == "won_unconfirmed" else ""]
    return [line for line in out if line]


def offer_card(con, offer: str, allows=None) -> str:
    """Everything the register knows about one offer: facts, status and evidence, each revision with its
    lines, rates and translations, the corrections, and the offer's other documents. `allows`
    (Config.allows) hides the folders excluded since the register was built."""
    asked = (offer or "").strip()
    if not _ready(con):
        return EMPTY_REGISTER
    key = offerkeys.offer_key(asked) or asked
    o = con.execute("SELECT * FROM offers WHERE offer_key = ? COLLATE NOCASE", (key,)).fetchone()
    every = con.execute("SELECT * FROM offer_revisions WHERE offer_key = ? ORDER BY rev_order, id",
                        (o["offer_key"] if o else key,)).fetchall()
    revisions = [r for r in every if _allowed(allows, r["path"])]
    if o is None or (every and not revisions) or (not every and not _allowed(allows, o["folder"])):
        docs = [r[0] for r in con.execute("SELECT d.path FROM doc_facets f JOIN docs d ON d.id = f.doc_id "
                                          "WHERE f.offer = ? COLLATE NOCASE ORDER BY d.path", (key,))
                if _allowed(allows, r[0])][:10]
        return (f"No offer {asked} in the register (find offers with list_offers)."
                + (f" Indexed documents carrying this number, none of them an offer document: {'; '.join(docs)}"
                   if docs else ""))
    key = o["offer_key"]
    out = _facts(o, allows)
    ids = [r["id"] for r in revisions]
    marks = ",".join("?" * len(ids)) or "NULL"
    lines: dict[int, list] = {}
    for l in con.execute(f"SELECT * FROM offer_lines WHERE revision_id IN ({marks}) ORDER BY position, id", ids):
        lines.setdefault(l["revision_id"], []).append(l)
    rates: dict[int, list] = {}
    if store.has_table(con, "offer_rates"):
        for t in con.execute(f"SELECT * FROM offer_rates WHERE revision_id IN ({marks})", ids):
            rates.setdefault(t["revision_id"], []).append(t)
    out.append("\nRevisions (oldest first):")
    for r in (r for r in revisions if r["translation_of"] is None):
        out.append(_revision_line(r))
        out += [_card_line(l) for l in lines.get(r["id"], [])]
        out += [f"  rate: 1 {t['base']} = {t['rate']:g} {t['quote']}"
                + (f" ({t['date_written']})" if t["date_written"] else "") for t in rates.get(r["id"], [])]
        out += [f"  translation: {t['path']} (same totals, no lines of its own)"
                for t in revisions if t["translation_of"] == r["id"]]

    if store.has_table(con, "offer_corrections"):
        corrections = con.execute("SELECT * FROM offer_corrections WHERE offer = ? ORDER BY rowid", (key,)).fetchall()
        for title, applied in (("Corrections applied:", 1), ("Corrections not applied:", 0)):
            chosen = [_correction_line(c) for c in corrections if c["applied"] == applied]
            if chosen:
                out += ["\n" + title] + chosen

    docs = con.execute(f"""SELECT d.path, f.doc_type FROM doc_facets f JOIN docs d ON d.id = f.doc_id
                           WHERE f.offer = ? AND d.id NOT IN (SELECT doc_id FROM offer_revisions
                           WHERE offer_key = ? AND doc_id IS NOT NULL) ORDER BY f.doc_type, d.path""",
                       (key, key)).fetchall()
    docs = [d for d in docs if _allowed(allows, d["path"])]
    if docs:
        out.append("\nOther documents of the offer:")
        out += [f"- {d['doc_type'] or 'other'}: {d['path']}" for d in docs[:MAX_DOCUMENTS]]
        if len(docs) > MAX_DOCUMENTS:
            out.append(f"... {len(docs) - MAX_DOCUMENTS} more (list_folder {o['folder']})")
    return "\n".join(out)


# --- offer_lines ----------------------------------------------------------------------------------

def _part(o: _Offer, r) -> str:
    """'3001234/03 Wonka': a revision document of an offer."""
    return f"{o.key}/{r['revision'] or '?'}" + (f" {r['variant']}" if r["variant"] else "")


def _parts_left_out(o: _Offer, chosen: list, undated: list, wanted, cur: str, last: bool,
                    year_from, year_to) -> list[tuple[str, str]]:
    """What an offer that has points leaves out of the chart: an ignored revision that would have been
    chosen, a variant without a date (year filter), not read or without any amount in `cur`, a line
    without an amount in `cur`."""
    out = []
    after = o.last[0]["rev_order"]
    for r in o.revisions:
        in_years = not (year_from or year_to) or (r["date"] and _in_years(_year(r["date"]), year_from, year_to))
        if r["ignored"] and (not last or r["rev_order"] >= after) and in_years:
            out.append((_part(o, r), "ignored by correction"))
    out += [(_part(o, r), "no date") for r in undated]
    for r in chosen:
        if not o.lines(r):
            out.append((_part(o, r), "no readable prices"))
            continue
        mine = [l for l in o.lines(r) if wanted(l)]
        missing = [l for l in mine if cur not in _amounts(l["amounts"])]
        if mine and len(missing) == len(mine):
            out.append((_part(o, r), f"no {cur} amount"))
        else:
            out += [(f"{_part(o, r)} line {l['position']}", f"no {cur} amount") for l in missing]
    return out


def offer_lines(con, machine: str | None = None, kind: str | None = None, client: str | None = None,
                country: str | None = None, status: str | None = None, offer_type: str | None = "firm",
                category: str | None = "client,archive", year_from: int | None = None, year_to: int | None = None,
                currency: str = "CHF", revisions: str = "last", limit: int = 500, allows=None) -> str:
    """The rows of a price chart: one per priced line with an amount in `currency` (never converted), of
    the last revision of each offer (every variant of it) or of every revision; then the coverage line,
    which names whole offers left out and the parts left out of the offers shown (a variant not read,
    without a date or without an amount in `currency`, a line without that amount, an ignored revision).
    With `machine` and no `kind` (None), only machine lines (kind machine), said in the coverage line; an
    empty kind is every kind. With `machine`, the offers in scope without readable prices are named: they
    could not be checked. `allows` (Config.allows) hides the folders excluded since the register was built."""
    if revisions not in ("last", "all"):
        raise ValueError("revisions must be 'last' or 'all'")
    by_machine = _matcher(machine)
    default_kind = by_machine is not None and kind is None
    kinds = {"machine"} if default_kind else _choices(kind, KINDS, "kind")
    statuses = _choices(status, STATUSES, "status")
    types = _choices(offer_type, OFFER_TYPES, "offer_type")
    categories = _choices(category, None, "category")
    cur = (currency or "CHF").strip().upper()
    if not _ready(con):
        return EMPTY_REGISTER

    def wanted(l) -> bool:
        return (by_machine is None or by_machine(l["model"], l["family"])) and (not kinds or l["kind"] in kinds)

    last = revisions == "last"
    cov, rows = _Coverage(), []
    if default_kind:
        cov.notes.append(DEFAULT_KIND_NOTE)
    for o in _offers(con, client=client, country=country, statuses=statuses, allows=allows):
        facet_match = by_machine and (not kinds or default_kind) and by_machine(*o.facet_machines)
        if (by_machine or kinds) and not (any(wanted(l) for l in o.all_lines()) or facet_match):
            in_scope = ((not types or o.row["offer_type"] in types)
                        and (not categories or o.row["category"] in categories))
            if by_machine and in_scope and _unchecked(o, year_from, year_to):
                cov.unchecked.append(o.key)
            continue
        if not o.revisions:
            cov.matched.append(o)
            cov.leave(o.key, "no revision")
            continue
        chosen, undated = o.last if last else o.usable, []
        if year_from or year_to:
            pool = chosen or o.revisions
            dated = [r for r in pool if r["date"]]
            within = {r["id"] for r in dated if _in_years(_year(r["date"]), year_from, year_to)}
            if not within and len(dated) == len(pool):
                continue  # dated, outside the years asked
            undated = [r for r in chosen if not r["date"]]
            chosen = [r for r in chosen if r["id"] in within]
        cov.matched.append(o)
        picked = [(r, l) for r in chosen for l in o.lines(r) if wanted(l)]
        priced = [(r, l, _amounts(l["amounts"])[cur]) for r, l in picked if cur in _amounts(l["amounts"])]
        if types and o.row["offer_type"] not in types:
            reason = o.row["offer_type"] or "no offer type"
        elif categories and o.row["category"] not in categories:
            reason = f"category {o.row['category']}"
        elif not o.usable:
            reason = "ignored by correction"
        elif not chosen:
            reason = "no date"
        elif not any(o.lines(r) for r in chosen):  # an older revision may be read: say which one is not
            reason = ("no readable prices" if not o.readable
                      else "last revision not read" if last else "revisions of these years not read")
        elif not picked:
            reason = "no matching line" + (" in the last revision" if last else "")
        elif not priced:
            reason = f"no {cur} amount"
        else:
            reason = None
        if reason:
            cov.leave(o.key, reason)
            continue
        for name, why in _parts_left_out(o, chosen, undated, wanted, cur, last, year_from, year_to):
            cov.leave(name, why)
        for r, l, amount in priced:
            q = l["quantity"]
            rows.append(((r["date"] or "9999", o.key, r["rev_order"], r["id"], l["position"]),
                         [r["date"], o.key, r["revision"], r["variant"], l["kind"], o.row["client"], o.row["country"],
                          o.row["status"], _num(q), _num(amount), _num(round(amount / q, 2)) if q else "", cur,
                          " ".join((l["text"] or "").split()), r["path"], _num(l["page"]), l["confidence"]]))
    rows.sort(key=lambda kr: kr[0])
    limit = max(1, int(limit))
    out = [";".join(LINE_COLUMNS)] + [";".join(_cell(v) for v in cells) for _, cells in rows[:limit]]
    if len(rows) > limit:
        out.append(f"... {len(rows) - limit} more rows (raise limit=)")
    return "\n".join(out + cov.lines())


# --- offer_stats ----------------------------------------------------------------------------------

def _group_values(o: _Offer, key: str) -> list[str]:
    if key == "machine":  # the machines of the last revision: an offer with several counts under each
        names = [l["model"] or l["family"] for r in o.last for l in o.lines(r)
                 if l["kind"] == "machine" and (l["model"] or l["family"])]
        return list(dict.fromkeys(names)) or ["unknown"]
    value = o.row[key]
    return [str(value)] if value not in (None, "") else ["unknown"]


def offer_stats(con, group_by: str, client: str | None = None, country: str | None = None,
                machine: str | None = None, status: str | None = None, offer_type: str | None = None,
                category: str | None = None, year_from: int | None = None, year_to: int | None = None,
                allows=None) -> str:
    """Offers and revisions counted by one or two keys (every offer, all types and categories unless
    filtered), sorted by keys; then the coverage line. With `machine`, the offers in scope without readable
    prices are named: they could not be checked. `allows` (Config.allows) hides the folders excluded since
    the register was built."""
    keys = [k.strip().lower() for k in str(group_by or "").split(",") if k.strip()]
    if not 1 <= len(keys) <= 2 or any(k not in GROUP_KEYS for k in keys):
        raise ValueError(f"group_by must be one or two of {', '.join(GROUP_KEYS)} (comma-separated)")
    statuses = _choices(status, STATUSES, "status")
    types = _choices(offer_type, OFFER_TYPES, "offer_type")
    categories = _choices(category, None, "category")
    if not _ready(con):
        return EMPTY_REGISTER
    cov, offers, revisions = _Coverage(), Counter(), Counter()
    found = _offers(con, client=client, country=country, machine=machine, statuses=statuses, types=types,
                    categories=categories, allows=allows)
    if _matcher(machine):  # the offers the machine filter could not judge
        matched = {o.key for o in found}
        cov.unchecked = [o.key for o in _offers(con, client=client, country=country, statuses=statuses, types=types,
                                                categories=categories, allows=allows)
                         if o.key not in matched and _unchecked(o, year_from, year_to)]
    for o in found:
        if year_from or year_to:
            if o.row["year"] is None:
                cov.matched.append(o)
                cov.leave(o.key, "no date")
                continue
            if not _in_years(o.row["year"], year_from, year_to):
                continue
        cov.matched.append(o)
        n = len({r["rev_order"] for r in o.usable})
        for combo in itertools.product(*(_group_values(o, k) for k in keys)):
            offers[combo] += 1
            revisions[combo] += n
    out = [";".join(keys + ["offers", "revisions"])]
    for combo in sorted(offers, key=lambda c: tuple((v == "unknown", v) for v in c)):
        out.append(";".join([_cell(v) for v in combo] + [str(offers[combo]), str(revisions[combo])]))
    if "machine" in keys:
        out.append("(machines of the last revision: an offer with several machines is counted under each)")
    return "\n".join(out + cov.lines())

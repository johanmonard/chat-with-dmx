"""Verified offer records: what the agent saw on an offer revision's price pages, kept as one JSON file per
record in a shared folder.

The server checks a submission (check) before storing it: every number must be printed on the page and the
whole revision must be covered. Records are never written into the register tables: the register tools put
them in place of the register's own reading when they read it (overlay), so a revoke or an expiry needs no
rebuild. The precedence is corrections.csv > verified record > register reading.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from datetime import datetime
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from . import store
from .config import Config

STATES = ("verified", "disputed", "revoked")
CHECKED_ON = ("image", "text", "image only")


@dataclass
class Record:
    offer: str
    revision: str
    variant: str | None
    path: str
    size: int | None
    mtime: float | None
    pages: list[int] = field(default_factory=list)
    not_price_pages: dict[int, str] = field(default_factory=dict)
    lines: list[dict] = field(default_factory=list)
    not_prices: list[dict] = field(default_factory=list)
    totals: dict[str, float] = field(default_factory=dict)
    facts: dict[str, str | None] = field(default_factory=dict)
    checked_on: str = "image"
    checked_by: str = ""
    checked_at: str = ""
    user: str = ""
    note: str = ""
    state: str = "verified"
    file: str = ""

    @property
    def identity(self) -> tuple[str, str, str]:
        return identity(self.offer, self.revision, self.variant)


@dataclass
class Loaded:
    records: dict[tuple, Record] = field(default_factory=dict)
    expired: list[Record] = field(default_factory=list)
    damaged: list[str] = field(default_factory=list)
    source: str = "none"   # "shared" | "mirror" | "none"
    note: str | None = None
    disputed: list[Record] = field(default_factory=list)
    revoked: int = 0
    stale: list = field(default_factory=list)   # filled by overlay


def identity(offer: str, revision: str, variant: str | None) -> tuple[str, str, str]:
    """The key of a revision: offer upper-cased, revision, variant ('' for none, '-' or blank; a revision
    written '-' or '?' is the one without a label)."""
    v, rev = (variant or "").strip(), (revision or "").strip()
    return ((offer or "").strip().upper(), "" if rev in ("-", "?") else rev, "" if v in ("", "-") else v)


def write(folder: str, record: Record) -> str:
    """Write the record as <16 hex>.json (name from a hash, never from tool input), via <name>.tmp and a
    rename. Returns the file name."""
    directory = Path(folder)
    directory.mkdir(parents=True, exist_ok=True)
    seed = "|".join(identity(record.offer, record.revision, record.variant)) + record.checked_at + record.user \
        + secrets.token_hex(8)
    name = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16] + ".json"
    record.file = name
    data = asdict(record)
    data["not_price_pages"] = {str(k): v for k, v in record.not_price_pages.items()}
    tmp = directory / (name + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, directory / name)
    return name


def _read(path: Path) -> Record:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("not an object")
    known = {f.name for f in fields(Record)}
    data = {k: v for k, v in data.items() if k in known}
    skipped = data.get("not_price_pages") or {}
    if not isinstance(skipped, dict):
        raise ValueError("not_price_pages")
    data["not_price_pages"] = {int(k): v for k, v in skipped.items()}
    rec = Record(**data)
    _validate(rec)
    rec.file = path.name
    return rec


def _number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and abs(v) != float("inf")


def _validate(rec: Record) -> None:
    """A file in the shared folder is data from outside: every field the tools use has its type checked, so
    a hand-made or damaged record is listed as damaged and never breaks a register tool."""
    texts = (rec.offer, rec.revision, rec.path, rec.checked_on, rec.checked_by, rec.checked_at, rec.user, rec.note)
    if rec.state not in STATES or not rec.offer or not all(isinstance(t, str) for t in texts):
        raise ValueError("not a record")
    if rec.variant is not None and not isinstance(rec.variant, str):
        raise ValueError("variant")
    if not all(v is None or _number(v) for v in (rec.size, rec.mtime)):
        raise ValueError("fingerprint")
    if not isinstance(rec.pages, list) or not all(type(p) is int for p in rec.pages):
        raise ValueError("pages")
    if not isinstance(rec.totals, dict) or not all(isinstance(k, str) and _number(v) for k, v in rec.totals.items()):
        raise ValueError("totals")
    if not isinstance(rec.facts, dict) or not all(v is None or isinstance(v, str) for v in rec.facts.values()):
        raise ValueError("facts")
    if not isinstance(rec.lines, list) or not isinstance(rec.not_prices, list):
        raise ValueError("lines")
    for l in rec.lines:
        amounts = l.get("amounts") if isinstance(l, dict) else None
        if (not isinstance(amounts, dict) or not all(isinstance(k, str) and _number(v) for k, v in amounts.items())
                or not (l.get("quantity") is None or _number(l["quantity"]))
                or not (l.get("page") is None or type(l["page"]) is int)
                or not all(l.get(k) is None or isinstance(l.get(k), str) for k in ("text", "kind", "model"))):
            raise ValueError("line")
    if not all(isinstance(n, dict) for n in rec.not_prices):
        raise ValueError("not_prices")


_cache: dict[tuple, tuple[list[Record], list[str]]] = {}


def _scan(folder: Path) -> tuple[list[Record], list[str]] | None:
    """Every record of a folder and the names of damaged files; None when the folder cannot be read."""
    try:
        files = sorted(p for p in folder.iterdir() if p.suffix == ".json" and p.is_file())
        stamp = (str(folder), tuple(p.name for p in files), max((p.stat().st_mtime_ns for p in files), default=0))
    except OSError:
        return None
    if stamp in _cache:
        return _cache[stamp]
    records, damaged = [], []
    for p in files:
        try:
            records.append(_read(p))
        except OSError:
            return None   # the share dropped while reading: unreachable, not damaged (and not cached)
        except (ValueError, TypeError, KeyError, AttributeError):
            damaged.append(p.name)
    if len(_cache) > 20:
        _cache.clear()
    _cache[stamp] = (records, damaged)
    return records, damaged


def load(cfg: Config, con, allows=None) -> Loaded:
    """The current record per revision: the shared folder, else the pull's mirror (said in `note`).
    `allows` (default cfg.allows) says which documents the caller may see."""
    allows = allows or cfg.allows
    out = Loaded()
    scanned = None
    if cfg.verified_dir:
        scanned = _scan(Path(cfg.verified_dir))
        if scanned is not None:
            out.source = "shared"
    if scanned is None and cfg.verified_mirror:
        scanned = _scan(Path(cfg.verified_mirror))
        if scanned is not None:
            out.source = "mirror"
            if cfg.verified_dir:
                out.note = f"shared folder not reachable ({cfg.verified_dir}): verified records of the last pull"
    if scanned is None:
        return out
    records, out.damaged = scanned
    latest: dict[tuple, Record] = {}
    for rec in sorted(records, key=lambda r: (r.checked_at, r.file)):
        latest[rec.identity] = rec
    docs = {}
    for key, rec in latest.items():
        if rec.state == "revoked":
            out.revoked += 1
        elif rec.state == "disputed":
            out.disputed.append(rec)
        elif allows(store.path_key(rec.path)) and _fingerprint_ok(con, rec, docs):
            out.records[key] = rec
        else:
            out.expired.append(rec)
    return out


def _fingerprint_ok(con, rec: Record, cache: dict) -> bool:
    """The document is still the one that was looked at (size and mtime to the second)."""
    key = store.path_key(rec.path)
    if key not in cache:
        cache[key] = con.execute("SELECT size, mtime FROM docs WHERE path_key = ?", (key,)).fetchone()
    row = cache[key]
    if row is None:
        return False
    same_size = rec.size is None or row["size"] == rec.size
    same_time = rec.mtime is None or row["mtime"] is not None and round(row["mtime"]) == round(rec.mtime)
    return same_size and same_time


# --- The server's check of a submission -----------------------------------------------------------

MAX_LINES, MAX_TEXT, MAX_PAGES, MAX_NOTE = 200, 300, 30, 500
_FORMULA_START = ("=", "+", "-", "@")


@dataclass
class Submission:
    offer: str
    revision: str
    variant: str | None = None
    path: str = ""
    pages: list[int] = field(default_factory=list)
    lines: list[dict] = field(default_factory=list)
    totals: dict = field(default_factory=dict)
    date: str | None = None
    country: str | None = None
    offer_type: str | None = None
    checked_on: str = "image"
    checked_by: str = ""
    not_prices: list[dict] = field(default_factory=list)
    not_price_pages: dict = field(default_factory=dict)
    note: str = ""
    checked_at: str = ""
    user: str = ""


@dataclass
class Verdict:
    ok: bool
    record: Record | None = None
    problems: list[str] = field(default_factory=list)


def parse_pages(text) -> list[int]:
    """'4-5' -> [4, 5]; '1,3' -> [1, 3]; a list of numbers is kept."""
    if isinstance(text, (list, tuple)):
        return sorted({int(p) for p in text})
    out: set[int] = set()
    for part in str(text or "").replace(" ", "").split(","):
        if not part:
            continue
        a, _, b = part.partition("-")
        lo, hi = int(a), int(b or a)
        if hi < lo or hi - lo > 200:
            raise ValueError(f"bad page range {part}")
        out.update(range(lo, hi + 1))
    return sorted(out)


def _clean(text, limit: int = MAX_TEXT) -> str:
    """A text field: stripped, cut, and a leading = + - @ quoted so that a table never runs it as a formula."""
    t = " ".join(str(text or "").split())[:limit]
    return "'" + t if t.startswith(_FORMULA_START) else t


def _stamp(given: str) -> str:
    """The record's time: now, unless a valid past ISO time is given (the seeding of old checks); a time in
    the future would win over every later record, so it is never kept."""
    now = datetime.now().replace(microsecond=0)
    try:
        when = datetime.fromisoformat(str(given))
    except ValueError:
        return now.isoformat()
    return when.replace(tzinfo=None, microsecond=0).isoformat() if when.replace(tzinfo=None) <= now else now.isoformat()


def _amount(v) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return round(f, 2) if f == f and abs(f) != float("inf") else None


def _money(currency: str, v: float) -> str:
    text = f"{v:,.0f}" if float(v).is_integer() else f"{v:,.2f}"
    return f"{currency} {text}".replace(",", "'")


def _find(con, cfg: Config, sub: Submission, allows=None):
    """(revision row, document row, problems): the register's revision of the submission and the document
    whose pages were looked at."""
    ident = identity(sub.offer, sub.revision, sub.variant)
    rev = None
    for r in con.execute("SELECT * FROM offer_revisions WHERE offer_key = ? COLLATE NOCASE AND ignored = 0 "
                         "AND translation_of IS NULL ORDER BY id", (ident[0],)):
        if identity(r["offer_key"], r["revision"] or "", r["variant"]) == ident:
            rev = r
            break
    if rev is None:
        return None, None, [f"offer {sub.offer} revision {sub.revision}"
                            + (f" variant {sub.variant}" if sub.variant else "")
                            + " is not in the register (list_offers / offer_card show the revisions)"]
    key = store.path_key(sub.path) if sub.path else None
    if not key or not (allows or cfg.allows)(key):
        return rev, None, [f"{sub.path or 'no path'}: not inside the indexed folders"]
    doc = con.execute("SELECT * FROM docs WHERE path_key = ?", (key,)).fetchone()
    offer = con.execute("SELECT folder FROM offers WHERE offer_key = ?", (rev["offer_key"],)).fetchone()
    folder = store.path_key(offer["folder"]) if offer and offer["folder"] else None
    if doc is None or not (doc["id"] == rev["doc_id"] or (folder and store.is_under(key, folder))):
        return rev, None, [f"{sub.path}: not the revision's document nor a document of the offer's folder"]
    return rev, doc, []


def check(con, cfg: Config, sub: Submission, allows=None) -> Verdict:
    """The submission accepted as a verified record, or the problems the agent can act on."""
    from . import offers, prices

    problems: list[str] = []
    if len(sub.lines) > MAX_LINES:
        problems.append(f"{len(sub.lines)} lines: at most {MAX_LINES} per call")
    if len(sub.pages) > MAX_PAGES:
        problems.append(f"{len(sub.pages)} pages: at most {MAX_PAGES} per call")
    if not sub.lines:
        problems.append("no line submitted")
    if problems:
        return Verdict(False, None, problems)
    rev, doc, found = _find(con, cfg, sub, allows)
    if found:
        return Verdict(False, None, found)

    pages = sorted(set(sub.pages))
    skipped = {int(p): _clean(why, MAX_NOTE) for p, why in (sub.not_price_pages or {}).items()}
    last_page = doc["n_pages"] or max(pages + list(skipped), default=0)
    for p in pages + list(skipped):
        if not 1 <= p <= max(last_page, 1):
            problems.append(f"page {p} is not a page of {os.path.basename(doc['path'])} ({last_page} pages)")
    register_pages = parse_pages(rev["pages"])
    for p in register_pages:
        if p not in pages and p not in skipped:
            problems.append(f"page {p} is a price page of this revision: submit its lines or give it in "
                            "not_price_pages with the reason")
    if register_pages and not set(register_pages) & set(pages):
        problems.append("none of the revision's price pages is among the submitted pages: not_price_pages "
                        "cannot stand for all of them")
    if problems:
        return Verdict(False, None, problems)
    text = offers._pages(con, doc["id"])
    printed = {p: prices._printed_numbers(text.get(p, "")) for p in pages}
    blind = {p for p in pages if not printed[p]}

    lines, seen = [], {p: set() for p in pages}
    for i, l in enumerate(sub.lines, 1):
        if not isinstance(l, dict):
            problems.append(f"line {i}: not an object")
            continue
        page, qty, amounts = l.get("page"), l.get("quantity"), {}
        for cur, v in (l.get("amounts") or {}).items():
            a = _amount(v)
            if a is None:
                problems.append(f"line {i}: {cur} amount {v!r} is not a number")
            else:
                code = str(cur).strip().upper()
                if re.fullmatch(r"[A-Z]{3}", code):
                    amounts[code] = a
                else:
                    problems.append(f"line {i}: '{cur}' is not a currency code (CHF, EUR...)")
        if qty is not None and (isinstance(qty, bool) or _amount(qty) is None):
            problems.append(f"line {i}: quantity {qty!r} is not a number")
            continue
        if page not in pages:
            problems.append(f"line {i}: page {page} is not among the submitted pages {pages}")
            continue
        if page not in blind:
            for cur, a in amounts.items():
                if round(abs(a), 2) not in printed[page]:
                    problems.append(f"page {page}: {_money(cur, a)} is not printed there")
            if qty is not None and round(abs(float(qty)), 2) not in printed[page]:
                problems.append(f"page {page}: quantity {qty} is not printed there")
        qty = None if qty is None else int(qty) if float(qty).is_integer() else float(qty)
        seen[page].update(round(a, 2) for a in amounts.values())
        lines.append({"text": _clean(l.get("text")), "kind": _clean(l.get("kind"), 40),
                      "model": _clean(l.get("model"), 60) or None, "quantity": qty, "amounts": amounts, "page": page})
    not_prices = []
    for n in sub.not_prices or []:
        a = _amount(n.get("value")) if isinstance(n, dict) else None
        if a is None or n.get("page") not in pages:
            problems.append(f"not_prices entry {n!r}: needs a page among the submitted ones and a numeric value")
            continue
        not_prices.append({"page": n["page"], "value": a, "reason": _clean(n.get("reason"), MAX_NOTE)})
        seen[n["page"]].add(a)
    totals = {}
    for cur, v in (sub.totals or {}).items():
        a = _amount(v)
        if a is None:
            problems.append(f"total {cur} {v!r} is not a number")
            continue
        if not re.fullmatch(r"[A-Z]{3}", str(cur).strip().upper()):
            problems.append(f"total '{cur}' is not a currency code")
            continue
        totals[str(cur).strip().upper()] = a
        if len(blind) < len(pages) and not any(round(abs(a), 2) in printed[p] for p in pages if p not in blind):
            problems.append(f"{_money(str(cur).upper(), a)} (total) is not printed on the submitted pages")

    # The whole revision: every amount the register read on these pages is in the submission.
    missing: dict[int, list[str]] = {}
    for l in con.execute("SELECT * FROM offer_lines WHERE revision_id = ? ORDER BY position", (rev["id"],)):
        if l["page"] not in pages:
            continue
        try:
            reg = json.loads(l["amounts"] or "{}")
        except ValueError:
            reg = {}
        for cur, v in reg.items():
            a = round(float(v), 2)
            if not any(a in s or -a in s for s in seen.values()):
                missing.setdefault(l["page"], []).append(_money(cur, a))
    for p, amounts in sorted(missing.items()):
        problems.append(f"page {p} holds register amounts ({', '.join(amounts)}) missing from the submission: "
                        "add the line or list it in not_prices")
    if problems:
        return Verdict(False, None, problems)

    sums: dict[str, float] = {}
    for l in lines:
        for cur, a in l["amounts"].items():
            sums[cur] = sums.get(cur, 0) + a
    note = _clean(sub.note, MAX_NOTE)
    if totals:
        gaps = [f"lines {_money(c, round(sums.get(c, 0), 2))}, printed total {_money(c, t)}"
                for c, t in totals.items() if abs(sums.get(c, 0) - t) > 0.5]
        note = " · ".join(x for x in (note, "; ".join(gaps) if gaps else "lines add up to the printed total") if x)
    checked_on = "image only" if blind else sub.checked_on if sub.checked_on in ("image", "text") else "image"
    facts = {"date": sub.date, "country": sub.country, "offer_type": sub.offer_type}
    record = Record(offer=rev["offer_key"], revision=rev["revision"] or sub.revision, variant=rev["variant"],
                    path=doc["path"], size=doc["size"], mtime=doc["mtime"], pages=pages, not_price_pages=skipped,
                    lines=lines, not_prices=not_prices, totals=totals,
                    facts={k: (_clean(v, 80) if v else None) for k, v in facts.items()}, checked_on=checked_on,
                    checked_by=_clean(sub.checked_by, 80), checked_at=_stamp(sub.checked_at), user=_clean(sub.user, 80),
                    note=note)
    return Verdict(True, record, [])


def dispute(con, cfg: Config, sub: Submission, reason: str, state: str = "disputed", allows=None) -> Record:
    """A record that says the agent could not confirm the register's reading (state disputed), or takes a
    verified one back (state revoked); no grounding. Raises ValueError when the revision or the document is
    not what the register holds."""
    rev, doc, found = _find(con, cfg, sub, allows)
    if found:
        raise ValueError("; ".join(found))
    return Record(offer=rev["offer_key"], revision=rev["revision"] or sub.revision, variant=rev["variant"],
                  path=doc["path"], size=doc["size"], mtime=doc["mtime"], pages=sorted(set(sub.pages)),
                  checked_on=sub.checked_on if sub.checked_on in CHECKED_ON else "image",
                  checked_by=_clean(sub.checked_by, 80), checked_at=_stamp(sub.checked_at), user=_clean(sub.user, 80),
                  note=_clean(reason, MAX_NOTE), state=state)


def revoke(con, cfg: Config, sub: Submission, reason: str, allows=None) -> Record:
    """A record that takes the revision's verified record back: the register's reading stands again."""
    return dispute(con, cfg, sub, reason, state="revoked", allows=allows)


# --- Overlay: verified values in place of the register's, corrections first ----------------------------

_DAY = re.compile(r"\d{4}-\d{2}-\d{2}")


def _norm(text) -> str:
    return " ".join(str(text or "").split()).casefold()


def _same_text(a, b) -> bool:
    a, b = _norm(a), _norm(b)
    return bool(a and b) and (a == b or a in b or b in a)


def _label_matches(label: str | None, r) -> bool:
    """A correction's revision ('03' or '03/Vandelay') names this revision row."""
    if not label:
        return False
    base, slash, variant = label.partition("/")
    return base == (r["revision"] or "") and (not slash or variant.casefold() == (r["variant"] or "").casefold())


def _apply_correction(line: dict, field: str, value: str | None) -> bool:
    """One line correction applied to a verified line (False: the field cannot be applied here)."""
    from . import machines, prices

    if field == "text":
        line["text"] = value
    elif field == "kind":
        line["kind"] = (value or "").lower()
    elif field == "model":
        families = sorted(f for f, _ in machines.models_in(value or ""))
        line["model"], line["family"] = value, families[0] if families else None
    elif field == "quantity":
        line["quantity"] = int(value) if value and value.isdigit() else None
    elif re.fullmatch(r"amount:[A-Z]{3}", field):
        amounts = json.loads(line["amounts"] or "{}")
        number = prices.parse_number(value) if value is not None else None
        if value is None:
            amounts.pop(field[7:], None)
        elif number is None:
            return False
        else:
            amounts[field[7:]] = number
        line["amounts"] = json.dumps(amounts)
    else:
        return False
    line["read_by"] = "correction"
    return True


def _verified_lines(r, rec: Record) -> list[dict]:
    from . import prices

    out = []
    for pos, l in enumerate(rec.lines, 1):
        model = l.get("model") or None
        family, found = prices._machine(model or l.get("text") or "") if (l.get("kind") == "machine" or model) \
            else (None, None)
        out.append({"id": -(r["id"] * 1000 + pos), "revision_id": r["id"], "position": pos, "text": l.get("text"),
                    "kind": l.get("kind") or "other", "family": family, "model": model or found,
                    "quantity": l.get("quantity"), "amounts": json.dumps(l.get("amounts") or {}),
                    "page": l.get("page"), "read_by": "verified", "confidence": "verified"})
    return out


def _pages_text(nums: list[int]) -> str:
    if nums and nums == list(range(nums[0], nums[-1] + 1)):
        return str(nums[0]) if len(nums) == 1 else f"{nums[0]}-{nums[-1]}"
    return ",".join(map(str, nums))


def overlay(con, offers: list, loaded: Loaded) -> None:
    """Put the verified records in place of the register's reading, in memory. `offers` are offer_tools._Offer
    (revisions and lines are plain dicts). Every revision gets `verification` (verified | expired | disputed |
    none) and `record`; a verified one gets the record's lines, totals, pages, path and date, and, when it is
    the offer's last revision, its country and offer type. Corrections come first: an applied offer-level
    correction keeps its value over the record's fact; a line correction is applied to the verified line with
    the same text, else listed in `loaded.stale`."""
    from . import offerkeys
    from .offer_status import OFFER_TYPES

    loaded.stale = []
    expired = {r.identity: r for r in loaded.expired}
    disputed = {r.identity: r for r in loaded.disputed}
    by_offer: dict[str, list] = {}
    if store.has_table(con, "offer_corrections"):
        for c in con.execute("SELECT offer, revision, line, field, value FROM offer_corrections WHERE applied = 1 "
                             "ORDER BY rowid"):
            by_offer.setdefault(c["offer"], []).append(c)
    for o in offers:
        corrections = by_offer.get(o.key, [])
        fixed = {c["field"] for c in corrections if c["revision"] is None and c["line"] is None}
        last_ids = {r["id"] for r in o.last}
        dated = False
        for r in o.revisions:
            ident = identity(o.key, r["revision"] or "", r["variant"])
            rec = loaded.records.get(ident)
            r["record"], r["verified_on"] = rec, rec.checked_on if rec else "no"
            r["verification"] = ("verified" if rec else "disputed" if ident in disputed
                                 else "expired" if ident in expired else "none")
            if ident in disputed:
                r["record"] = disputed[ident]
            elif ident in expired and not rec:
                r["record"] = expired[ident]
            if not rec:
                continue
            register = o.lines(r)
            lines = _verified_lines(r, rec)
            for c in (c for c in corrections if c["line"] is not None and _label_matches(c["revision"], r)):
                reg = next((l for l in register if l["position"] == int(c["line"])), None)
                target = next((l for l in lines if reg and _same_text(reg["text"], l["text"])), None)
                if target is not None and c["field"] == "delete":
                    if (c["value"] or "").lower() in ("yes", "y", "1", "true", "oui", "ja"):
                        lines.remove(target)
                    continue
                if target is None or not _apply_correction(target, c["field"], c["value"]):
                    loaded.stale.append((o.key, c["revision"], c["line"], c["field"], c["value"],
                                         "the verified line at this position is not the line corrected"
                                         if target is None else "the field cannot be applied to the verified line"))
            o._lines[r["id"]] = lines
            r["totals"] = json.dumps(rec.totals)
            r["pages"] = _pages_text(rec.pages)
            r["path"], r["read_by"], r["confidence"], r["sum_ok"] = rec.path, "verified", "verified", 1
            r["note"] = rec.note or None
            day = (rec.facts or {}).get("date")
            if day and _DAY.fullmatch(day) and r["date_source"] != "correction":
                r["date"], r["date_source"], dated = day, "verified", True
            if r["id"] in last_ids:
                country = (rec.facts or {}).get("country")
                if country and "country" not in fixed:
                    o.row["country"], o.row["country_source"] = offerkeys.country_en(country) or country, "verified"
                kind = ((rec.facts or {}).get("offer_type") or "").lower()
                if kind in OFFER_TYPES and "offer_type" not in fixed:
                    o.row["offer_type"] = kind
        if dated:
            days = sorted(r["date"] for r in o.usable if r["date"])
            if days:
                o.row["first_date"], o.row["last_date"], o.row["year"] = days[0], days[-1], int(days[-1][:4])


def stale_corrections(con, loaded: Loaded) -> list[tuple]:
    """(offer, revision, line, field, value, reason) of the line corrections a verified record could not take
    over: the review list of the rebuild shows them."""
    from . import offer_tools

    overlay(con, offer_tools._load(con), loaded)
    return list(loaded.stale)


def tally(revisions) -> tuple[int, int, int, int]:
    """(verified, not verified, of which expired, of which disputed) over revision dicts that went through overlay."""
    states = [r.get("verification", "none") for r in revisions]
    verified_n = states.count("verified")
    return verified_n, len(states) - verified_n, states.count("expired"), states.count("disputed")


def mark(r) -> str:
    """The text after a revision in offer_card."""
    state, rec = r.get("verification", "none"), r.get("record")
    if state == "verified":
        return f"verified ({rec.checked_on}, {rec.checked_at[:10]}, {rec.checked_by})"
    if state == "disputed":
        return f"verification disputed: {rec.note}"
    return "not verified (record expired: the document changed)" if state == "expired" else "not verified"


def review_rows(con, loaded: Loaded) -> list[tuple]:
    """Rows for review.csv: where a verified record differs from the register's own reading (the parser
    comparison the rebuild keeps), and the line corrections no record could take over."""
    rows = []
    for rec in loaded.records.values():
        found = None
        for r in con.execute("SELECT r.*, o.client FROM offer_revisions r LEFT JOIN offers o ON o.offer_key = r.offer_key "
                             "WHERE r.offer_key = ? COLLATE NOCASE AND r.translation_of IS NULL", (rec.offer,)):
            if identity(r["offer_key"], r["revision"] or "", r["variant"]) == rec.identity:
                found = r
                break
        if found is None:
            continue

        def shown(amounts_per_line) -> str:
            return " | ".join(sorted(f"{c} {a:g}" for amounts in amounts_per_line for c, a in amounts.items()))

        mine = shown(json.loads(l["amounts"] or "{}") for l in
                     con.execute("SELECT amounts FROM offer_lines WHERE revision_id = ?", (found["id"],)))
        theirs = shown(l["amounts"] for l in rec.lines)
        if mine != theirs:
            rows.append((rec.offer, rec.revision, found["client"], "verified differs",
                         f"register: {mine or 'no amount'}; verified: {theirs or 'no amount'}",
                         rec.path, ",".join(map(str, rec.pages))))
    for offer, revision, line, field, value, why in stale_corrections(con, loaded):
        client = con.execute("SELECT client FROM offers WHERE offer_key = ?", (offer,)).fetchone()
        rows.append((offer, revision, client[0] if client else None, "stale correction",
                     f"line {line} {field} = {value or ''}: {why}", None, None))
    return rows


# --- Seed from the spot checks ---------------------------------------------------------------------------

SEED_BY = "spot check"
_REVISION = re.compile(r"^\s*(?P<rev>[^\s/(]+)\s*(?:/\s*(?P<variant>[^(]*?))?\s*(?:\(.*\))?\s*$")
_TOTAL = re.compile(r"([A-Z]{3})\s*(-?[\d'’., ]+\d)")
_CHECKED_ON = {"page image": "image", "extracted text": "text"}


def _parse_revision(text: str) -> tuple[str, str | None]:
    """'02 / A (2 variants)' -> ('02', 'A'); '-' -> ('-', None); '03 (2 variants)' -> ('03', None)."""
    m = _REVISION.match(text or "")
    if not m:
        return (text or "").strip(), None
    return m["rev"], (m["variant"] or "").strip() or None


def _submission_from_spot_check(batch: dict, result: dict, stamp: str) -> Submission:
    from . import prices

    revision, variant = _parse_revision(batch.get("Last revision") or "")
    lines = []
    for l in batch.get("lines") or []:
        lines.append({"text": l.get("Line text (register)"), "kind": l.get("Kind"), "model": l.get("Model"),
                      "quantity": l.get("Qty"), "page": l.get("Page"),
                      "amounts": {c: l[c] for c in ("CHF", "EUR", "USD", "GBP") if l.get(c) is not None}})
    totals = {}
    for cur, number in _TOTAL.findall(batch.get("Printed totals") or ""):
        value = prices.parse_number(number)
        if value is not None:
            totals[cur] = value
    day, _, source = (batch.get("Date (source)") or "").partition(" ")
    pages = [] if not batch.get("Pages") else parse_pages(batch["Pages"])
    return Submission(offer=str(batch["Offer"]), revision=revision, variant=variant, path=batch.get("Price document") or "",
                      pages=pages, lines=lines, totals=totals, date=day if source == "(document)" else None,
                      country=batch.get("Country"), offer_type=batch.get("Offer type"),
                      checked_on=_CHECKED_ON.get(result.get("checked_on"), "text"), checked_by=SEED_BY, checked_at=stamp)


def seed_from_spot_checks(con, cfg: Config, folders: list[str], dry_run: bool = False) -> list[str]:
    """Verified records from the spot checks: in each folder the pairs batch<n>.json (the register's values as
    they stood when checked) and result<n>.json (the checker's verdicts). An offer is seeded when its lines,
    amounts and facts were all right (the values the checker saw, not today's register); every submission goes
    through the same server check. A later folder's verdict on an offer replaces an earlier one. One line per
    offer: seeded, already seeded, skipped (<verdicts>) or refused: <problems>."""
    judged: dict[str, tuple[dict, dict, str]] = {}
    for folder in folders:
        n = 1
        while (Path(folder) / f"batch{n}.json").is_file() and (Path(folder) / f"result{n}.json").is_file():
            batch_file, result_file = Path(folder) / f"batch{n}.json", Path(folder) / f"result{n}.json"
            batches = json.loads(batch_file.read_text(encoding="utf-8"))
            stamp = datetime.fromtimestamp(result_file.stat().st_mtime).replace(microsecond=0).isoformat()
            results = {str(r.get("Offer")): r for r in json.loads(result_file.read_text(encoding="utf-8"))}
            for b in batches:
                if str(b.get("Offer")) in results:
                    judged[str(b["Offer"])] = (b, results[str(b["Offer"])], stamp)
            n += 1
    current = load(cfg, con).records if cfg.verified_dir else {}
    out = []
    for offer, (batch, result, stamp) in sorted(judged.items()):
        bad = [f"{k.removesuffix('_ok')} {result.get(k)}" for k in ("lines_ok", "amounts_ok", "facts_ok")
               if result.get(k) != "✓"]
        if bad:
            out.append(f"{offer}: skipped ({', '.join(bad)})")
            continue
        try:
            sub = _submission_from_spot_check(batch, result, stamp)
        except (ValueError, TypeError, KeyError) as e:
            out.append(f"{offer}: refused: unreadable batch entry ({e})")
            continue
        verdict = check(con, cfg, sub)
        if not verdict.ok:
            out.append(f"{offer}: refused: " + "; ".join(verdict.problems))
            continue
        rec = verdict.record
        old = current.get(rec.identity)
        if old and old.checked_by == SEED_BY and old.checked_at == rec.checked_at and old.lines == rec.lines:
            out.append(f"{offer}: already seeded")
            continue
        if not dry_run:
            write(cfg.verified_dir, rec)
        out.append(f"{offer}: seeded" + (" (dry run)" if dry_run else ""))
    seeded = sum(": seeded" in line for line in out)
    out.append(f"{len(judged)} offers judged, {seeded} {'would be ' if dry_run else ''}seeded, "
               f"{sum(': skipped' in line for line in out)} skipped, {sum(': refused' in line for line in out)} refused")
    return out

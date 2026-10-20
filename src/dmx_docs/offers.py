"""The offer register of the offers world (`dmx-docs offers`): one row per offer, its revisions and
their priced lines, rebuilt from the index.

Each offer's revisions come from its offer documents (doc_facets doc_type 'offer'); their price
sections are read by rules (prices.parse_section), else by the local model (prices.parse_with_model,
answers cached by text). Dates come from the document, else History.xls, else the file; the country
from the archive folder, else the client's QuotationList.xls, else page 1 of the offer. Amounts are
stored per currency exactly as printed, never converted.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta

from . import facets, knowledge, machines, offerkeys, prices, sources, store, vision
from .config import Config
from .offer_status import apply_corrections, link_projects, number_forms, write_review  # noqa: F401 - offers.*
from .register import fold

log = logging.getLogger(__name__)

TABLES = """
CREATE TABLE IF NOT EXISTS offers (
    offer_key TEXT PRIMARY KEY, number_written TEXT, client TEXT, site TEXT, country TEXT, country_source TEXT,
    partner TEXT, category TEXT, title TEXT, folder TEXT, first_date TEXT, last_date TEXT, year INTEGER,
    author TEXT, offer_type TEXT, status TEXT, status_source TEXT, project TEXT, status_evidence TEXT,
    duplicate_of TEXT, quotation_status TEXT, other_projects TEXT
);
CREATE TABLE IF NOT EXISTS offer_revisions (
    id INTEGER PRIMARY KEY, offer_key TEXT, revision TEXT, variant TEXT, rev_order INTEGER, date TEXT,
    date_source TEXT, doc_id INTEGER, path TEXT, pages TEXT, totals TEXT, sum_ok INTEGER, read_by TEXT,
    confidence TEXT, note TEXT, translation_of INTEGER, ignored INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS offer_revisions_offer ON offer_revisions(offer_key);
CREATE TABLE IF NOT EXISTS offer_lines (
    id INTEGER PRIMARY KEY, revision_id INTEGER, position INTEGER, text TEXT, kind TEXT, family TEXT, model TEXT,
    quantity INTEGER, amounts TEXT, page INTEGER, read_by TEXT, confidence TEXT
);
CREATE INDEX IF NOT EXISTS offer_lines_revision ON offer_lines(revision_id);
CREATE TABLE IF NOT EXISTS offer_rates (
    revision_id INTEGER, base TEXT, quote TEXT, rate REAL, date_written TEXT
);
CREATE INDEX IF NOT EXISTS offer_rates_revision ON offer_rates(revision_id);
CREATE TABLE IF NOT EXISTS offer_model_cache (
    text_hash TEXT PRIMARY KEY, answer TEXT
)
"""
REBUILT = ("offers", "offer_revisions", "offer_lines", "offer_rates")  # offer_model_cache is kept
PROGRESS_EVERY = 200  # offers
# Bump when parse_with_model reads answers differently: every cached model answer is asked again.
MODEL_CACHE_VERSION = 1
# Offer documents the index could not read: the revision is listed with this note.
_STATUS_NOTES = {"no_text": "no text (status no_text)", "empty": "empty file"}

# Sheet "Quotations" of QuotationList.xls, from its third row.
QUOTATION_KEYS = ("country", "city", "description", "date", "machine", "offer", "final_customer", "final_country",
                  "author", "status", "company")
_EXCEL_EPOCH = date(1899, 12, 30)  # serial dates of the 1900 date system
_REVISION_MARK = re.compile(r"^[\s_.\-]*(?:V\d+|Q\d{2}|\d{2})(?![A-Za-z0-9])", re.I)
_WORD = re.compile(r"[^\W\d_]+(?:[-'][^\W\d_]+)*")


@dataclass
class OfferStats:
    offers: int = 0
    revisions: int = 0
    lines: int = 0
    by_rules: int = 0
    by_model: int = 0
    unread: int = 0
    model_unavailable: bool = False
    time_limited: bool = False
    won: int = 0
    stale_corrections: int = 0

    def summary(self) -> str:
        text = (f"{self.offers} offers, {self.revisions} revisions ({self.by_rules} read by rules, {self.by_model} "
                f"by the model, {self.unread} without readable prices), {self.lines} priced lines, {self.won} won")
        if self.stale_corrections:
            text += f"; {self.stale_corrections} corrections not applied (see above)"
        if self.model_unavailable:
            text += "; the model was unavailable: run again when Ollama answers to read the rest"
        if self.time_limited:
            text += "; the time limit stopped the model: run again to read the rest"
        return text


# --- History.xls and QuotationList.xls -------------------------------------------------------------

def _cell_text(v) -> str | None:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    s = str(v).strip() if v is not None else ""
    return s or None


def _cell_date(v) -> str | None:
    """YYYY-MM-DD of an Excel serial date (39842.0), a date or a written date ('29.01.2009', '2009-01-29');
    None for an empty or zero cell and a date prices.plausible_date refuses (before 1990, far ahead)."""
    if isinstance(v, datetime):
        v = v.date()
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        v = _EXCEL_EPOCH + timedelta(days=int(v)) if 1 <= v < 2958466 else None
    if isinstance(v, date):
        return v.isoformat() if prices.plausible_date(v.isoformat()) else None
    found = prices.offer_date({1: v}) if isinstance(v, str) else None
    return found[0] if found else None


def _offer_cell(v) -> str | None:
    """Offer key of a cell; a number cell has lost the leading zero of 0905020."""
    if isinstance(v, (int, float)) and not isinstance(v, bool) and float(v).is_integer():
        v = str(int(v)).zfill(7)
    text = _cell_text(v)
    return offerkeys.offer_key(text) if text else None


def history_rows(rows: list[list]) -> list[tuple[str, str, str]]:
    """(YYYY-MM-DD, author, action) of the cell values of sheet Quotations of History.xls; the header
    row and rows without a date are left out."""
    out = []
    for row in rows[1:]:
        cells = list(row) + [None] * 3
        when = _cell_date(cells[0])
        if when:
            out.append((when, _cell_text(cells[1]) or "", _cell_text(cells[2]) or ""))
    return out


def quotation_rows(rows: list[list]) -> list[dict]:
    """Rows of sheet Quotations of QuotationList.xls from the third one, as dicts (QUOTATION_KEYS); rows
    with fewer than 3 filled cells are left out, dates are YYYY-MM-DD and offer numbers offer keys."""
    out = []
    for row in rows[2:]:
        cells = (list(row) + [None] * len(QUOTATION_KEYS))[:len(QUOTATION_KEYS)]
        if sum(_cell_text(c) is not None for c in cells) < 3:
            continue
        d = {k: _cell_text(c) for k, c in zip(QUOTATION_KEYS, cells)}
        d["date"], d["offer"] = _cell_date(cells[3]), _offer_cell(cells[5])
        out.append(d)
    return out


def _sheet_rows(path: str, sheet: str = "Quotations") -> list[list]:
    """Cell values of one sheet of an .xls workbook; date cells become YYYY-MM-DD (either date system)."""
    import xlrd  # only this step reads workbooks

    book = xlrd.open_workbook(path, on_demand=True)
    try:
        sh = book.sheet_by_name(sheet)
        return [[xlrd.xldate_as_datetime(c.value, book.datemode).date().isoformat()
                 if c.ctype == xlrd.XL_CELL_DATE else c.value for c in sh.row(i)] for i in range(sh.nrows)]
    finally:
        book.release_resources()


def read_history(path: str) -> list[tuple[str, str, str]]:
    return history_rows(_sheet_rows(path))


def read_quotation_list(path: str) -> list[dict]:
    return quotation_rows(_sheet_rows(path))


def _workbook(reader, path: str):
    """A workbook's rows, [] when it is missing or cannot be read (logged)."""
    if not os.path.isfile(store.fs_path(path)):
        return []
    try:
        return reader(store.fs_path(path))
    except Exception as e:  # noqa: BLE001 - xlrd missing, damaged or locked file, other sheet names
        log.warning("cannot read %s: %s: %s", path, type(e).__name__, e)
        return []


def machine_aliases(vocabulary_path: str) -> list[tuple[str, str]]:
    """(folded alias, model) of the `## machines` section of the word list, longest alias first."""
    words = knowledge.load_vocabulary(vocabulary_path).kinds.get("machines", {})
    return sorted(words.items(), key=lambda kv: (-len(kv[0]), kv[0]))


# --- Reading one offer document -------------------------------------------------------------------

def _pages(con, doc_id: int) -> dict[int, str]:
    """Indexed text per page, without the passages written from pictures."""
    pages: dict[int, str] = {}
    for page_no, text in con.execute(
            "SELECT page_no, text FROM chunks WHERE doc_id = ? AND id NOT IN (SELECT chunk_id FROM picture_uses "
            "WHERE doc_id = ? AND chunk_id IS NOT NULL) ORDER BY page_no, seq", (doc_id, doc_id)):
        pages[page_no] = pages.get(page_no, "") + text
    return pages


def _dump(section: prices.PriceSection | None) -> str:
    return json.dumps(asdict(section) if section else None)


def _load(answer: str) -> prices.PriceSection | None:
    d = json.loads(answer)
    if not d:
        return None
    return prices.PriceSection([prices.PriceLine(**l) for l in d["lines"]], d["totals"],
                               [prices.Rate(**r) for r in d["rates"]], d["pages"], d["sum_ok"])


class _Model:
    """The local model for the price pages the rules cannot read: answers cached by the text sent, Ollama
    checked once per run and only when the cache has no answer. After the deadline (time.perf_counter()) the
    model is not asked any more; cached answers still serve."""

    def __init__(self, con, cfg: Config, progress, deadline: float | None = None):
        self.con, self.cfg, self.progress, self.deadline = con, cfg, progress, deadline
        self.why: str | None = None
        self.checked = self.unavailable = self.time_limited = False

    def _key(self, pages: dict[int, str]) -> str:
        # The pages parse_with_model sends: the rules' price pages, else the first ones.
        nums = prices.price_pages(pages) or [p for p in sorted(pages) if p <= prices.MODEL_PAGES]
        sent = "\n\n".join(f"[Page {p}]\n{pages[p]}" for p in nums)
        text = f"{MODEL_CACHE_VERSION}\n{self.cfg.vision_model}\n{prices.MODEL_ROLE}\n{prices.MODEL_PROMPT}\n{sent}"
        return hashlib.sha1(text.encode("utf-8")).hexdigest()

    def read(self, pages: dict[int, str]) -> tuple[prices.PriceSection | None, str | None]:
        """(section or None, problem or None)."""
        key = self._key(pages)
        row = self.con.execute("SELECT answer FROM offer_model_cache WHERE text_hash = ?", (key,)).fetchone()
        if row:
            try:  # cached answers are cleaned like new ones (amounts of 0, day rates)
                return prices.clean_model(_load(row[0]), pages), None
            except (ValueError, KeyError, TypeError, AttributeError) as e:  # damaged entry: asked again
                log.warning("unreadable cached model answer %s, asked again: %s", key, e)
        if self.deadline is not None and time.perf_counter() > self.deadline:
            if not self.time_limited:
                self.time_limited = True
                self.progress("Time limit reached: the model is not asked for the remaining price pages "
                              "(the register is still written; run again to read them).")
            return None, "model not asked: time limit"
        if not self.checked:
            self.checked = True
            self.why = vision.unavailable(self.cfg.vision_url, self.cfg.vision_model)
            if self.why:
                self.progress(f"Model unavailable, price pages the rules cannot read stay unread: {self.why}")
                log.warning("model unavailable: %s", self.why)
        if self.why:
            self.unavailable = True
            return None, "model unavailable"
        try:
            section = prices.parse_with_model(self.cfg.vision_url, self.cfg.vision_model, pages,
                                              self.cfg.vision_timeout_s)
        except vision.VisionError as e:
            log.warning("model error: %s", e)
            return None, f"model error: {e}"
        self.con.execute("INSERT OR REPLACE INTO offer_model_cache(text_hash, answer) VALUES(?, ?)",
                         (key, _dump(section)))  # autocommit: kept even when the run stops
        return section, None


@dataclass
class _Read:
    doc: sqlite3.Row
    pages: dict[int, str]
    section: prices.PriceSection | None = None
    read_by: str = "none"
    confidence: str = "none"
    notes: list[str] = field(default_factory=list)
    date: str | None = None
    date_source: str | None = None
    variant: str | None = None
    twin: _Read | None = None  # the revision's document this one translates
    budget: bool = False  # budget wording (prices.offer_type), in this document or its PDF/Word twin


def _confidence(read_by: str, sum_ok: bool | None) -> str:
    if read_by == "rules":
        return "high" if sum_ok else "medium" if sum_ok is None else "low"
    return "medium" if sum_ok else "low"


def _alias_model(text: str, aliases: list[tuple[str, str]]) -> str | None:
    folded = f" {fold(text)} "
    return next((model for alias, model in aliases if f" {alias} " in folded), None)


def _score(section: prices.PriceSection | None) -> int:
    """How well the rules read a section: none 0, sum check failed 1, no total 2, sum checked 3."""
    return 0 if section is None else {False: 1, None: 2, True: 3}[section.sum_ok]


def _read(con, doc, model: _Model, aliases, twin=None) -> _Read:
    """One document read. A mail is never a price document (its date is the mail's). A PDF the rules cannot read
    (a scan whose OCR lost the numbers, a garbled text layer) gives way to its Word twin (same name) when the rules
    read the twin better."""
    if doc["status"] != "ok":  # listed, not read: a scan before OCR, a damaged or empty file
        note = (f"file error: {doc['error'] or 'unknown'}" if doc["status"] == "error"
                else _STATUS_NOTES.get(doc["status"], f"not read (status {doc['status']})"))
        return _Read(doc, {}, notes=[note])
    pages = _pages(con, doc["id"])
    budget = prices.offer_type(pages, doc["name"]) == "budget"
    if doc["ext"] == ".msg":
        r = _Read(doc, pages, notes=["mail: not a price document"], budget=budget)
        found = prices.offer_date(pages)
        if found:
            r.date, r.date_source = found[0], "mail"
        return r
    section, other = prices.parse_section(pages), pages
    if twin is not None and _score(section) < 2:
        twin_pages = _pages(con, twin["id"])
        twin_section = prices.parse_section(twin_pages)
        budget |= prices.offer_type(twin_pages, twin["name"]) == "budget"
        if _score(twin_section) > _score(section):
            doc, pages, section, other = twin, twin_pages, twin_section, pages
    r = _Read(doc, pages, budget=budget)
    read_by = "rules"
    if r.pages and (section is None or section.sum_ok is False):
        answer, problem = model.read(r.pages)
        if problem:
            r.notes.append(problem)
        if answer is not None and (section is None or answer.sum_ok):
            section, read_by = answer, "model"
    found = prices.offer_date(r.pages) or prices.offer_date(other)
    if found:
        r.date, r.date_source = found[0], "document"
    if section is None:
        r.notes.insert(0, "no price section")
        return r
    r.section, r.read_by = section, read_by
    r.confidence = _confidence(read_by, section.sum_ok)
    if section.sum_ok is False:
        r.notes.insert(0, "sum check failed")
    ocr = {p for (p,) in con.execute("SELECT page_no FROM ocr_pages WHERE doc_id = ? AND status = 'text'",
                                     (doc["id"],))}
    if r.confidence == "high" and ocr & set(section.pages):
        r.confidence = "medium"  # recognised text: a digit may be wrong
    for line in section.lines:
        if line.kind == "machine" and not line.model:  # an old machine name from the word list
            line.model = _alias_model(line.text, aliases)
            if line.model and not line.family:
                line.family = next(iter(sorted(f for f, _ in machines.models_in(line.model))), None)
    return r


# --- One offer ------------------------------------------------------------------------------------

def _offer_folder(path: str, rel_parts: list[str], root: str) -> str:
    """The first folder below the top one that carries an offer number (as facets.compute_offers),
    else the document's folder."""
    folders = rel_parts[:-1]
    idx = next((i for i in range(1, len(folders)) if offerkeys.offer_key(folders[i])), None)
    return os.path.join(root, *folders[:idx + 1]) if idx is not None else os.path.dirname(path)


def _client_folder(rel_parts: list[str], root: str, client: str | None) -> str | None:
    if not client or client not in rel_parts[:-1]:
        return None
    return os.path.join(root, *rel_parts[:rel_parts.index(client) + 1])


def _merge_versions(folders: dict[str, list]) -> dict[str, list]:
    """Sibling folders whose names differ only by a version label ('B-250306 V1', 'B-250306 V2') hold the
    versions of one offer: their documents go to the folder of the last version (each keeps the label of
    its own folder as its revision)."""
    versions: dict[tuple[str, str], list[tuple[int, str]]] = {}
    for folder in folders:
        name = os.path.basename(folder)
        m = facets._VERSION_LABEL.search(name)
        if m:
            base = fold(name[:m.start()] + " " + name[m.end():])
            versions.setdefault((os.path.dirname(folder), base), []).append((int(m.group(1)), folder))
    for found in versions.values():
        if len(found) > 1:
            found.sort()
            last = found[-1][1]
            for _, folder in found[:-1]:
                folders[last] += folders.pop(folder)
            folders[last].sort(key=lambda d: d["path"])
    return folders


# Names of the documents sent to the client ('Quotation', 'Offre', 'Angebot'...): read first in a revision.
_OFFER_NAMED = re.compile(r"\b(?:offers?|offres?|angebot|quotation|proposal|proposition|devis)\b")
# Documents that are not what was sent: internal costing and negotiation sheets, drafts, old copies (folded names).
_NOT_SENT = re.compile(r"\b(?:nego\w*|calcul\w*|kalkulation|interne?|internal|prix de revient|costs?|kosten"
                       r"|prices? summary|summary for sales|old|draft|brouillon|entwurf|en cours|copie de|copy of|kopie)\b")
_OLD_FOLDERS = {"old", "alt", "archive", "archiv", "ancien", "anciens", "draft", "temp", "tmp", "brouillon", "entwurf"}


def _stem(d) -> str:
    return fold(os.path.splitext(d["name"])[0])


def _sent_rank(d) -> int:
    """0 a document sent to the client, 1 an internal sheet, a draft or an old copy, 2 a mail."""
    if d["ext"] == ".msg":
        return 2
    folders = {fold(f) for f in os.path.dirname(d["path"]).split(os.sep)[-2:]}
    return 1 if _NOT_SENT.search(_stem(d)) or folders & _OLD_FOLDERS else 0


def _doc_order(d) -> tuple:
    """The documents of a revision: those named as the offer first ('..._Quotation.pdf'), then by file date."""
    return not _OFFER_NAMED.search(_stem(d)), d["mtime"] or 0, d["path"]


def _twins(docs: list) -> dict:
    """{id of a readable PDF: the readable .doc(x) offer document with the same name}."""
    words = {os.path.splitext(d["path"].lower())[0]: d for d in docs
             if d["doc_type"] == "offer" and d["ext"] in (".doc", ".docx") and d["status"] == "ok"}
    return {d["id"]: words[stem] for d in docs if d["doc_type"] == "offer" and d["ext"] == ".pdf"
            and d["status"] == "ok" and (stem := os.path.splitext(d["path"].lower())[0]) in words}


def _candidates(docs: list, key: str) -> list:
    """Offer documents (also those the index could not read); of a readable .pdf and a .doc(x) with the
    same name, the PDF (its Word twin is read when the PDF is not: _twins). In each revision, the documents whose
    file name carries the offer number; the other offer documents (a supplier's quotation, a transport quote) only
    when none does. Then the documents sent to the client: an internal sheet (costing, negotiation, prices
    summary), a draft or an old copy only when nothing else is left, a mail only when it is the only document
    (it is listed, never read for prices)."""
    docs = [d for d in docs if d["doc_type"] == "offer"]
    pdfs = {os.path.splitext(d["path"].lower())[0] for d in docs if d["ext"] == ".pdf" and d["status"] == "ok"}
    docs = [d for d in docs
            if d["ext"] not in (".doc", ".docx") or os.path.splitext(d["path"].lower())[0] not in pdfs]
    numbered = {d["revision"] for d in docs if offerkeys.offer_key(d["name"]) == key}
    docs = [d for d in docs if d["revision"] not in numbered or offerkeys.offer_key(d["name"]) == key]
    best: dict = {}
    for d in docs:
        best[d["revision"]] = min(best.get(d["revision"], 2), _sent_rank(d))
    return [d for d in docs if _sent_rank(d) == best[d["revision"]]]


def _name_variant(stem: str, key: str) -> str | None:
    """'Offer - 3001234_03 Vandelay' -> 'Vandelay': the file name after the offer number and revision."""
    i = stem.lower().find(key.lower())
    if i < 0:
        return None
    rest = _REVISION_MARK.sub("", stem[i + len(key):])
    return rest.strip(" _.-") or None


def _label_pattern(label: str | None) -> re.Pattern | None:
    """A History action naming the revision: '02', 'V2', 'rev 2', 'Version 02'..."""
    m = re.fullmatch(r"[VQ]?(\d+)", label or "", re.I)
    if not m:
        return None
    alt = rf"{re.escape(label)}|(?:v|rev\.?|r[eé]vision|version|indice)\s*0*{int(m.group(1))}"
    # Never part of a date: "le 02.03.2009" names neither 02 nor 03.
    return re.compile(rf"(?<![A-Za-z0-9])(?<!\d[./])(?:{alt})(?!\d)(?![./]\d)", re.I)


# The seller's letterhead, address and footer (Demaurex, Bosch Packaging, Rotzinger; Romanel, Vufflens, Ecublens).
_SELLER = re.compile(r"demaurex|bosch\s+packaging|rotzinger|m[ée]sanges|romanel|vufflens|ecublens|\bCH-\d{4}\b|\+41",
                     re.I)


def _first_country(text: str) -> str | None:
    words = _WORD.findall(text)
    for i in range(len(words)):
        for n in (3, 2, 1):
            phrase = " ".join(words[i:i + n])
            if i + n > len(words) or not phrase[:1].isupper():
                continue
            country = offerkeys.country_en(phrase)
            if country and (len(phrase) > 3 or phrase.isupper()):
                return country
    return None


def _country_in(text: str) -> str | None:
    """English name of the first country named on a page (one to three words, starting with a capital;
    abbreviations of 3 letters or fewer only in capitals). The seller's own address is skipped: a cell (between
    '|' or '♦') naming the seller or its address, and Switzerland in the seller's row or the row after it
    ('Ch. des Mésanges 3, CH-1032 Romanel' + 'Switzerland 27/05/09')."""
    after_seller = False
    for row in text.split("\n"):
        seller_row = False
        for cell in re.split(r"[|♦]", row):
            if _SELLER.search(cell):
                seller_row = True
                continue
            country = _first_country(cell)
            if country and not (country == "Switzerland" and (seller_row or after_seller)):
                return country
        after_seller = seller_row
    return None


def _document_country(revisions) -> str | None:
    """The country named on page 1 of the offer's documents, the last revision first."""
    return next((c for _, _, r in reversed(revisions) if (c := _country_in(r.pages.get(1, "")))), None)


def _mtime_date(doc) -> str | None:
    day = datetime.fromtimestamp(doc["mtime"]).date().isoformat() if doc["mtime"] else None
    return day if day and prices.plausible_date(day) else None


@dataclass
class _Context:
    model: _Model
    aliases: list[tuple[str, str]]
    root_keys: list[tuple[str, str]]
    histories: dict[str, list] = field(default_factory=dict)    # offer folder -> History.xls rows
    quotations: dict[str, dict] = field(default_factory=dict)   # client folder -> offer key -> QuotationList row

    def history(self, offer_folder: str) -> list[tuple[str, str, str]]:
        if offer_folder not in self.histories:
            path = os.path.join(offer_folder, "History", "History.xls")
            self.histories[offer_folder] = _workbook(read_history, path)
        return self.histories[offer_folder]

    def quotation(self, doc, key: str) -> dict | None:
        """The QuotationList.xls row of this offer number in the document's client folder."""
        root = facets.root_of(doc["path_key"], self.root_keys)
        folder = _client_folder(os.path.relpath(doc["path"], root).split(os.sep), root, doc["client"]) if root else None
        if not folder:
            return None
        if folder not in self.quotations:
            rows = _workbook(read_quotation_list, os.path.join(folder, "Suivi", "QuotationList.xls"))
            self.quotations[folder] = {}
            for q in rows:
                self.quotations[folder].setdefault(q["offer"], q)
        return self.quotations[folder].get(key)


@dataclass
class _Offer:
    row: dict
    revisions: list[tuple[str | None, int, _Read]]  # (label, rev_order, read)


def _revision(con, key: str, owner: str, docs: list, order: int, history, ctx: _Context,
              twins: dict | None = None) -> list[_Read]:
    """The documents of one revision, those named as the offer first, then the oldest file first (_doc_order):
    translations, variants and dates. twins: {PDF id: its Word twin} (_twins)."""
    reads = [_read(con, d, ctx.model, ctx.aliases, twin=(twins or {}).get(d["id"]))
             for d in sorted(docs, key=_doc_order)]
    kept: list[_Read] = []
    for r in reads:  # a later document with the same totals translates an earlier one: no lines of its own
        totals = r.section.totals if r.section else None
        r.twin = next((k for k in kept if totals and k.section and k.section.totals == totals), None)
        if r.twin is None:
            kept.append(r)
    pattern = _label_pattern(docs[0]["revision"])
    from_history = history[0][0] if history and order == 0 else next(
        (when for when, _, action in history if pattern and pattern.search(action)), None)
    for r in reads:
        folder = os.path.dirname(r.doc["path"])
        folder_rev = offerkeys.revision_of(os.path.basename(folder)) if folder != owner else None
        r.variant = folder_rev[1] if folder_rev else None
        if r.variant is None and len(kept) > 1 and r.twin is None:  # variants side by side: the file names tell
            r.variant = _name_variant(os.path.splitext(r.doc["name"])[0], key)
        if r.date is None and from_history:
            r.date, r.date_source = from_history, "history"
        if r.date is None:
            r.date = _mtime_date(r.doc)
            r.date_source = "file" if r.date else None
    return reads


def _build_offer(con, key: str, owner: str, docs: list, others: list[str], ctx: _Context) -> _Offer | None:
    cands = _candidates(docs, key)
    if not cands:
        return None
    by_label: dict[str | None, list] = {}
    for d in cands:
        by_label.setdefault(d["revision"], []).append(d)
    labels = sorted(by_label, key=lambda lb: offerkeys.revision_order(lb or ""))
    history = ctx.history(owner)
    revisions = [(label, order, r) for order, label in enumerate(labels)
                 for r in _revision(con, key, owner, by_label[label], order, history, ctx, _twins(docs))]
    last = [r for label, _, r in revisions if label == labels[-1]]
    offer_type = "budget" if any(r.budget for r in last) else "firm"

    first = docs[0]
    # File dates only when no revision has a printed or History date: an annex copied later moves nothing.
    dated = [r.date for _, _, r in revisions if r.date and r.date_source in ("document", "history")]
    dates = sorted(dated or [r.date for _, _, r in revisions if r.date])
    quote = ctx.quotation(first, key)
    country, country_source = first["country"], "folder" if first["country"] else None
    if country is None and quote and offerkeys.country_en(quote.get("country") or ""):
        country, country_source = offerkeys.country_en(quote["country"]), "quotation_list"
    if country is None:
        country = _document_country(revisions)
        country_source = "document" if country else None
    for _, _, r in revisions:
        r.pages = {}  # the text is no longer needed
    name = os.path.basename(owner)
    own_folder = offerkeys.offer_key(name) == key
    row = {"offer_key": key, "number_written": name.split(" - ", 1)[0].strip() if own_folder else key,
           "client": first["client"], "site": first["site"], "country": country, "country_source": country_source,
           "partner": first["partner"], "category": first["category"],
           "title": name.split(" - ", 1)[1].strip() or None if own_folder and " - " in name else None,
           "folder": owner, "first_date": dates[0] if dates else None, "last_date": dates[-1] if dates else None,
           "year": int(dates[-1][:4]) if dates else None,
           "author": (history[0][1] or None) if history else (quote or {}).get("author"),
           "offer_type": offer_type, "status": "unknown", "status_source": "none", "project": None,
           "status_evidence": None, "duplicate_of": "; ".join(others) or None,
           "quotation_status": (quote or {}).get("status"), "other_projects": None}
    return _Offer(row, revisions)


# --- The run --------------------------------------------------------------------------------------

def _pages_text(nums: list[int]) -> str:
    """[4, 5] -> '4-5', [2] -> '2', [1, 3] -> '1,3'."""
    if nums and nums == list(range(nums[0], nums[-1] + 1)):
        return str(nums[0]) if len(nums) == 1 else f"{nums[0]}-{nums[-1]}"
    return ",".join(map(str, nums))


def _create(con) -> None:
    for stmt in TABLES.split(";"):  # one by one: executescript would commit the open transaction
        if stmt.strip():
            con.execute(stmt)


def _write(con, built: list[_Offer]) -> None:
    _create(con)
    for o in built:
        con.execute(f"INSERT INTO offers ({','.join(o.row)}) VALUES ({','.join('?' * len(o.row))})",
                    tuple(o.row.values()))
        ids: dict[int, int] = {}
        for label, order, r in o.revisions:
            s = r.section
            cur = con.execute(
                "INSERT INTO offer_revisions(offer_key, revision, variant, rev_order, date, date_source, doc_id, "
                "path, pages, totals, sum_ok, read_by, confidence, note, translation_of) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (o.row["offer_key"], label, r.variant, order, r.date, r.date_source, r.doc["id"], r.doc["path"],
                 _pages_text(s.pages) if s else None, json.dumps(s.totals) if s and s.totals else None,
                 None if s is None or s.sum_ok is None else int(s.sum_ok), r.read_by, r.confidence,
                 "; ".join(r.notes) or None, ids[id(r.twin)] if r.twin else None))
            ids[id(r)] = cur.lastrowid
            if s is None or r.twin:
                continue
            con.executemany(
                "INSERT INTO offer_lines(revision_id, position, text, kind, family, model, quantity, amounts, page, "
                "read_by, confidence) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                [(cur.lastrowid, l.position, l.text, l.kind, l.family, l.model, l.quantity, json.dumps(l.amounts),
                  l.page, r.read_by, r.confidence) for l in s.lines])
            con.executemany("INSERT INTO offer_rates(revision_id, base, quote, rate, date_written) VALUES(?,?,?,?,?)",
                            [(cur.lastrowid, t.base, t.quote, t.rate, t.date_written) for t in s.rates])


def run_offers(cfg: Config, projects_db: str | None = None, review: bool = False, max_minutes: float | None = None,
               progress=print) -> OfferStats:
    """Rebuild the register from the index: every table but the model cache, then the statuses from the
    projects world's index (projects_db) and the user's corrections, in one transaction at the end (the
    documents and the model are read first). review=True also writes <world>\\review.csv. After max_minutes
    the model is not asked any more (note 'model not asked: time limit'); the register is still written."""
    deadline = time.perf_counter() + max_minutes * 60 if max_minutes else None
    if cfg.profile != "offers":
        raise ValueError(f"World '{cfg.world}' has profile '{cfg.profile}': the offer register needs 'offers'")
    if not cfg.db_path.exists():
        raise FileNotFoundError(f"Index not found: {cfg.db_path} (run `dmx-docs index` first)")
    con = store.connect(cfg.db_path)
    con.isolation_level = None  # explicit transaction for the rebuild; cache rows commit one by one
    try:
        sources.refresh(cfg, con)
        facets.refresh(con, cfg.roots, "offers")
        _create(con)  # the model cache is read and written while the documents are read
        root_keys = [(store.path_key(r), r) for r in cfg.roots]
        groups: dict[str, dict[str, list]] = {}  # offer key -> offer folder -> documents
        for d in con.execute("""SELECT d.id, d.path, d.path_key, d.name, d.ext, d.mtime, d.status, d.error, f.category,
                                       f.client, f.site, f.country, f.partner, f.offer, f.revision, f.doc_type
                                FROM docs d JOIN doc_facets f ON f.doc_id = d.id
                                WHERE f.offer IS NOT NULL ORDER BY d.path""").fetchall():
            root = facets.root_of(d["path_key"], root_keys)
            if not root:
                continue
            folder = _offer_folder(d["path"], os.path.relpath(d["path"], root).split(os.sep), root)
            groups.setdefault(d["offer"], {}).setdefault(folder, []).append(d)

        vocabulary = os.path.join(cfg.knowledge_path, "vocabulary.md") if cfg.knowledge_path else ""
        model = _Model(con, cfg, progress, deadline)
        ctx = _Context(model, machine_aliases(vocabulary), root_keys)
        progress(f"Reading the offer documents of {len(groups)} offer numbers...")
        built = []
        for i, (key, folders) in enumerate(sorted(groups.items()), 1):
            folders = _merge_versions(folders)
            # The folder with most documents of the number owns it (ties: path order), among the folders that
            # hold offer documents when there are any; the others are listed.
            owner = min(folders, key=lambda f: (not any(d["doc_type"] == "offer" for d in folders[f]),
                                                -len(folders[f]), f))
            offer = _build_offer(con, key, owner, folders[owner], sorted(f for f in folders if f != owner), ctx)
            if offer:
                built.append(offer)
            if i % PROGRESS_EVERY == 0:
                progress(f"  {i}/{len(groups)} offer numbers read")

        con.execute("BEGIN IMMEDIATE")
        try:
            for table in REBUILT:
                con.execute(f"DROP TABLE IF EXISTS {table}")
            _write(con, built)
            link_projects(con, projects_db)
            messages = apply_corrections(con, cfg.corrections_path)  # after every rebuild: they beat the documents
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        for message in messages:
            progress(message)
            log.warning(message)
        if review:
            path = cfg.world_dir / "review.csv"
            try:
                progress(f"Review list: {write_review(con, str(path))} rows in {path}")
            except OSError as e:  # open in Excel
                progress(f"review.csv not written: {e}")
                log.warning("review.csv not written: %s", e)

        stats = OfferStats(model_unavailable=model.unavailable, time_limited=model.time_limited)
        stats.stale_corrections = con.execute("SELECT count(*) FROM offer_corrections "
                                              "WHERE NOT applied").fetchone()[0]
        stats.won = con.execute("SELECT count(*) FROM offers WHERE status = 'won'").fetchone()[0]  # corrections included
        stats.offers = con.execute("SELECT count(*) FROM offers").fetchone()[0]
        stats.lines = con.execute("SELECT count(*) FROM offer_lines").fetchone()[0]
        by = dict(con.execute("SELECT read_by, count(*) FROM offer_revisions GROUP BY read_by").fetchall())
        stats.revisions = sum(by.values())
        stats.by_rules, stats.by_model, stats.unread = by.get("rules", 0), by.get("model", 0), by.get("none", 0)
        log.info("offer register: %s", stats.summary())
        return stats
    finally:
        con.close()

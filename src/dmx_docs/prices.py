"""Price sections of sales offers, read by rules from the indexed page text ({page_no: text}).

A price section is a run of priced lines (machine, options, services, discounts) closed by a
net total, amounts per currency exactly as printed (CHF first and EUR in brackets is common).
Precision before coverage: a section the rules cannot read cleanly comes back as None or with
sum_ok False, never as plausible-looking wrong numbers.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import machines, vision


@dataclass
class PriceLine:
    position: int  # 1-based, document order
    text: str
    kind: str  # machine | option | service | discount | other
    quantity: int | None
    amounts: dict[str, float]
    page: int
    family: str | None = None
    model: str | None = None


@dataclass
class Rate:
    base: str
    quote: str
    rate: float
    date_written: str | None


@dataclass
class PriceSection:
    lines: list[PriceLine]
    totals: dict[str, float]
    rates: list[Rate]
    pages: list[int]
    sum_ok: bool | None  # None = no total printed (and nothing unreadable)


MAX_PAGES = 3
MODEL_PAGES = 6  # pages sent to the model when the rules found no price section
MIN_AMOUNT = 50  # a smaller printed value is a section number or a rate, never a price (0 is kept: "included")
FIRST_DATE = date(1990, 1, 1)  # an earlier offer date is an empty date cell or a misread

_SP = " " + chr(0xA0) + chr(0x202F)  # space, no-break space, narrow no-break space
_DASH = "-" + chr(0x2013) + chr(0x2212)  # hyphen, en dash, minus
_CURRENCIES = {"€": "EUR", "$": "USD", "US$": "USD", "£": "GBP", "Fr.": "CHF", "SFr.": "CHF", "SFr": "CHF"}
_CUR = r"(?<![A-Za-z])(?:CHF|EUR|USD|GBP|US\$|SFr\.?|Fr\.|€|\$|£)(?![A-Za-z])"
# Thousands grouped by space, apostrophe, dot or comma, or plain digits; up to 2 decimals; not part
# of a date or a longer number ("Fr. 12.05.2020").
_NUM = rf"(?:\d{{1,3}}(?:[{_SP}'’.,]\d{{3}})+(?:[.,]\d{{1,2}})?|\d+(?:[.,]\d{{1,2}})?)(?![.,/]?\d)"
_END = rf"(?:\.?[{_DASH}]{{1,2}}(?!\d))?"  # "410 000.-", "410 000.--"
_AMOUNT = re.compile(
    rf"(?P<n1>[{_DASH}])?(?P<c1>{_CUR})[{_SP}]*(?P<n2>[{_DASH}])?[{_SP}]*(?P<v1>{_NUM}){_END}"
    rf"|(?P<n3>[{_DASH}])?(?P<v2>{_NUM}){_END}[{_SP}]*(?P<c2>{_CUR})(?![{_SP}]*[{_DASH}]?[{_SP}]*\d)")
_CUR_TOKEN = re.compile(_CUR)
# "Paloma 4R 1 150 000 €": a quantity may be fused with a space-grouped amount written number first,
# when what follows the 1-2 digit lead is itself an amount of two 3-digit groups or more.
_FUSED = re.compile(rf"\d{{1,2}}[{_SP}]\d{{3}}[{_SP}]\d{{3}}")
_PER = re.compile(rf"[{_SP}]*(?:/[{_SP}]*(?:tag|day|jour|h|hour|stunde|heure)\b"
                  rf"|(?:per|par|pro)[{_SP}]+(?:day|jour|tag|hour|heure|stunde)\b)", re.I)
# Exchange rates: "1.00 EUR = 1,0244 CHF", "1 Euro = 1.20 CHF", "1 EUR = CHF 1.08", "1 € = 1.08 CHF".
_RCUR = r"(?:EUR|Euros?|€uros?|USD|CHF|GBP|US\$|€|\$|£)(?![A-Za-z])"  # "1 €uro = 1.20 CHF" is printed too
_RNUM = r"\d+(?:[.,]\d+)?"
_RATE = re.compile(rf"(?<![\d.,])(?P<b>{_RNUM})\s*(?P<bc>{_RCUR})\s*=\s*"
                   rf"(?:(?P<qc1>{_RCUR})\s*(?P<q1>{_RNUM})|(?P<q2>{_RNUM})\s*(?P<qc2>{_RCUR}))", re.I)

_PRICE_WORD = re.compile(r"\b(?:price|prix|preis|total|montant|betrag)", re.I)
# Total labels: a net total ("PREIS, netto, ohne Steuern", "Gesamtpreis netto", "Prix total HT") is
# strong; a short label with a total word and no digits ("TOTAL", "Prix total", "Gesamtbetrag") is weaker.
# Either only counts when amounts are on the line or directly follow it.
_NET_TOTAL = re.compile(r"(?=.*(?:price|prix|preis|total|betrag|montant))"
                        r"(?=.*(?:\bnet\b|\bnetto|\bHT\b|hors taxes|ohne steuern|without taxes))", re.I)
_TOTAL_WORD = re.compile(r"\b(?:sub-?total|sous-total|total|totalpreis|totalbetrag"
                         r"|gesamt|gesamtpreis|gesamtbetrag|gesamtsumme|zwischensumme|summe)\b", re.I)
_SUBTOTAL = re.compile(r"\b(?:sub-?total|sous-total|zwischensumme)\b", re.I)  # never the total, never a line
_NUMBERING = re.compile(r"^\W*\d+(?:\.\d+)*\.?\s+")
_HEADING = re.compile(r"(?:\d+(?:\.\d+)*\.?\s*)?(?:prix|prices?|preise?)(?:\s*/\s*(?:prix|prices?|preise?))*", re.I)
_RUNNING = re.compile(r"(?=.*\b(?:offer|offre|angebot))(?=.*\d{5})", re.I)  # "Offer 3001234 - ACME - Paloma 4R"
_PAGE_NO = re.compile(r"^\s*(?:page|seite)\s+\d+(?:\s*(?:/|of|von|sur|de)\s*\d+)?\s*$", re.I)
_DESCRIPTION = re.compile(r"[-•*–]|(?:including|inkl|einschliesslich|einschließlich|y compris|comprenant)\b",
                          re.I)
_QUANTITY = re.compile(r"^\s*(\d+)\s*[x×](?=\s|[^\W\d_])")  # "2x Paloma", "2 x Paloma", "2xPaloma"
# A page header repeated on each page: an offer number and a date ("ACME 3001234 - 02 04.11.2024").
_OFFER_NO = re.compile(r"(?<!\d)\d{7}(?!\d)|(?<![A-Za-z0-9])[BFE]-\d{6,7}(?!\d)")
_NUM_DATE = re.compile(r"(?<![\d.])(?:\d{1,2}\.\d{1,2}\.\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})(?!\d)")
# A numbered section heading ("2. Proposal", "2. Proposition Equipement"), short and without amounts.
_SECTION = re.compile(r"^\d+(?:\.\d+)*\.?\s*(?:proposal|proposition|angebot|offer|offre|equipment|équipement"
                      r"|equipement|ausrüstung|prix|price|preis)\b", re.I)
# The company's footer and letterhead: name, group, address with a CH- postcode, web site, phone.
_STREET = (r"(?:\b(?:route|rue|chemin|avenue|place|via|street|road)\b|\b(?:rte|ch|av)\."
           r"|(?:strasse|straße|weg|gasse|platz)\b)")
_FOOTER = re.compile(
    r"^\W*(?:demaurex(?:\s+sa)?|(?:an?\s+|member\s+of\s+the\s+)?rotzinger(?:\s+(?:ag|sa|group|gruppe|groupe))?"
    r"(?:\s+company)?)\W*$"
    r"|a\s+bosch\s+packaging\s+technology\s+company|\bwww\.\w"
    r"|\b(?:tel|tél|phone|fax)\b\.?\s*:?\s*\+?\s*\(?\d[\d\s()./-]{5,}"
    rf"|\bCH-\d{{4}}\b.*{_STREET}|{_STREET}.*\bCH-\d{{4}}\b", re.I)
# A delivered price: an Incoterm with 'Incoterms' or a place ('CIP Berlin (INCOTERMS 2010)', 'DAP Madrid').
# CIP followed by cleaning words is clean-in-place, not an Incoterm ("Option CIP Cleaning station").
_INCOTERM_CODES = (rf"(?:EXW|FCA|FAS|FOB|CFR|CIF|CPT|DAP|DPU|DAT|DDP|DDU"
                   rf"|CIP(?![{_SP}]+(?i:clean|cleaning|reinigung|nettoyage)\b))")
_INCOTERM = re.compile(rf"(?<![\w-]){_INCOTERM_CODES}(?![\w-])")
_INCOTERM_PLACE = re.compile(rf"(?<![\w-]){_INCOTERM_CODES}[{_SP}]+[A-ZÀ-Ý]")
_INCOTERMS_WORD = re.compile(r"\bincoterms?\b", re.I)
# The introduction of the offered items ("In this quotation, we kindly offer you:"), also of options
# ("The following options are also possible:"): left out of the item's label.
_INTRO = re.compile(r"we kindly offer you|nous avons le plaisir de vous propos|wir freuen uns|nous vous proposons"
                    r"|we are pleased to offer|following options? (?:is|are) (?:also )?possible"
                    r"|les options suivantes|l.option suivante|folgenden? optionen?", re.I)

_DISCOUNT = re.compile(r"\b(?:remise|rabais|rabatt|discount|nachlass)", re.I)
_OPTION = re.compile(r"\boption", re.I)
_SERVICE = re.compile(r"\b(?:installation|inbetriebnahme|mise en service|commissioning|training|formation|schulung"
                      r"|travel|reise|déplacement|transport|documentation|sat|fat)\b", re.I)
_MACHINE = re.compile(r"\b(?:cells?|cellules?|zellen?|lines?|lignes?|linien?|robot\w*)\b", re.I)

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6, "juillet": 7,
    "août": 8, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
    "januar": 1, "jänner": 1, "februar": 2, "märz": 3, "maerz": 3, "juni": 6, "juli": 7, "oktober": 10,
    "dezember": 12,
}
_MONTH = "|".join(sorted(map(re.escape, _MONTHS), key=len, reverse=True))
_DATE = re.compile(
    rf"\b(?:(?P<d1>\d{{1,2}})[./](?P<m1>\d{{1,2}})[./](?P<y1>\d{{4}})"
    rf"|(?P<y2>\d{{4}})-(?P<m2>\d{{2}})-(?P<d2>\d{{2}})"
    rf"|(?P<d3>\d{{1,2}})(?:\.|er|st|nd|rd|th)?\s+(?P<m3>{_MONTH})\s+(?P<y3>\d{{4}})"
    rf"|(?P<m4>{_MONTH})\s+(?P<d4>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y4>\d{{4}}))(?!\d)", re.I)
# Sentence ends: a line break, or . ! ? after a non-digit ("1. November 2011" is not an end).
_SENTENCE_END = re.compile(r"\n|(?<=[^\d\s][.!?])\s+")
_RATE_WORDS = re.compile(r"tageskurs|wechselkurs|taux de change|cours de change|exchange rate|rate of exchange", re.I)
# Looser hints, only for a date in parentheses: "actual rate (November 1st, 2011)", "cours du jour (12.03.2019)".
_RATE_HINT = re.compile(r"\brates?\b|\bcours\b|kurs|\btaux\b|\bexchange\b", re.I)
_BUDGET = re.compile(r"\b(?:budget|budgetary|richtpreis|prix indicatif|indicative|estimation)", re.I)


def parse_number(s: str) -> float | None:
    """'410 200.-', "1'230'000", '1.230.000,00', '-9 000.-', '12,5' -> float; None if not a number."""
    t = s.strip()
    neg = bool(t) and t[0] in _DASH
    t = re.sub(rf"\.?[{_DASH}]+$", "", t.lstrip(_DASH + " "))
    t = re.sub(r"[\s'’‘]", "", t)
    if not re.fullmatch(r"\d+(?:[.,]\d+)*", t):
        return None
    seps = re.findall(r"[.,]", t)
    if seps:
        last = t.rfind(seps[-1])
        whole, frac = t[:last], t[last + 1:]
        if len(set(seps)) == 1 and (len(seps) > 1 or (len(frac) == 3 and whole != "0")):
            whole, frac = t, ""  # only thousands separators
        groups = re.split(r"[.,]", whole)
        if len(groups) > 1 and (any(len(g) != 3 for g in groups[1:]) or len(groups[0]) > 3):
            return None
        t = "".join(groups) + ("." + frac if frac else "")
    v = float(t)
    return -v if neg else v


def _currency(token: str) -> str:
    t = token.upper()
    return "EUR" if t.startswith(("EURO", "€URO")) else _CURRENCIES.get(token, t)


def _blank_rates(text: str) -> str:
    """Exchange rates replaced by spaces, same length and line breaks kept: a rate wrapped after '='
    ('1 Euro =' then '1.20 CHF.') never leaves an amount on a line of its own."""
    return _RATE.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)


def _amount_spans(text: str) -> list[tuple[int, int, str, float, bool]]:
    """(start, end, currency, value, ambiguous) of the amounts in a text; exchange and day rates left out.

    ambiguous: written number first after text on the same line, a 1-2 digit lead then two 3-digit
    groups or more ("Qty 2 150 000,00 EUR" may be 2 x 150 000; "Option 50 000 EUR" is not ambiguous)."""
    text = _blank_rates(text)  # same length: spans stay valid
    found = []
    for m in _AMOUNT.finditer(text):
        if _PER.match(text, m.end()):
            continue  # a rate per day or hour, not a price
        raw = m.group("v1") or m.group("v2")
        value = parse_number(raw)
        if value is None or 0 < value < MIN_AMOUNT:
            continue  # a section number or a rate ('USD 2.1'), not a price
        before = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
        ambiguous = bool(m.group("v2") and _FUSED.match(raw) and re.search(r"\w", before))
        neg = m.group("n1") or m.group("n2") or m.group("n3")
        found.append((m.start(), m.end(), _currency(m.group("c1") or m.group("c2")), -value if neg else value,
                      ambiguous))
    return found


def amounts_in(text: str) -> list[tuple[str, float]]:
    """(currency, value) in text order; rates per day/hour and exchange rates are not amounts."""
    return [(c, v) for _, _, c, v, _ in _amount_spans(text)]


def _split(line: str) -> tuple[list[tuple[str, float, bool]], str, str]:
    """(amounts, label, words) of a line. label: the line, or its text without the amounts when it has
    some; words: the text without amounts and currency tokens, stripped of punctuation (empty = amounts only)."""
    spans = _amount_spans(line)
    rest = line
    for start, end, *_ in reversed(spans):
        rest = rest[:start] + " " + rest[end:]
    rest = _CUR_TOKEN.sub(" ", rest)
    words = rest.strip("()-–:,. \t")
    label = " ".join(re.sub(r"\(\s*\)", "", rest).split()).rstrip(" :,-–") if spans else line.strip()
    return [(c, v, a) for _, _, c, v, a in spans], label, words


def _total_strength(label: str) -> int:
    """2 = net total label, 1 = short total label without digits ('TOTAL', 'Prix total'), 0 = not a total.
    `label` is a line without its amounts; it is a total only when amounts are on it or directly follow.
    A subtotal is never a total."""
    if _SUBTOTAL.search(label):
        return 0
    if _NET_TOTAL.search(label):
        return 2
    w = _NUMBERING.sub("", label)
    return 1 if _TOTAL_WORD.search(w) and not re.search(r"\d", w) and len(w.split()) <= 5 else 0


def _has_total(text: str) -> bool:
    """A page holds a total label with amounts on its line or on the amount-only line right after it."""
    rows = [_split(line) for line in _blank_rates(text).split("\n") if line.strip()]
    for i, (amts, label, words) in enumerate(rows):
        if words and _total_strength(label):
            if amts or (i + 1 < len(rows) and rows[i + 1][0] and not rows[i + 1][2]):
                return True
    return False


def price_pages(pages: dict[int, str]) -> list[int]:
    """From a page with a price word and 2+ amounts through the page holding the total (at most 3
    consecutive pages). The first such run that ends in a total wins, else the first candidate page alone."""
    first: list[int] = []
    for p in sorted(pages):
        text = pages[p]
        if not (_PRICE_WORD.search(text) and len(amounts_in(text)) >= 2):
            continue
        for k in range(MAX_PAGES):
            if p + k not in pages:
                break
            if _has_total(pages[p + k]):
                return list(range(p, p + k + 1))
        first = first or [p]
    return first


def _machine(text: str) -> tuple[str | None, str | None]:
    """Most specific (family, model): the longest model, else the family alone (model None)."""
    found = machines.models_in(text)
    models = sorted((m for _, m in found if m), key=lambda m: (-len(m), m))
    if models:
        return next(f for f, m in found if m == models[0]), models[0]
    families = sorted(f for f, _ in found)
    return (families[0], None) if families else (None, None)


def classify(text: str, amounts: dict[str, float]) -> str:
    """machine | option | service | discount | other. `text` is the item's label; its first line
    (the item's own name) decides, so a description never turns a machine into a service. A service word
    before the machine name wins ('Training for the Paloma 3R operators'), after it the machine does
    ('Paloma 3R cell including installation')."""
    first = text.split("\n", 1)[0]
    if any(v < 0 for v in amounts.values()) or _DISCOUNT.search(first):
        return "discount"
    if _OPTION.search(first):
        return "option"
    family, service = machines._FAMILY.search(first), _SERVICE.search(first)  # _FAMILY: what models_in finds
    if family and not (service and service.start() < family.start()):
        return "machine"
    if service:
        return "service"
    if _MACHINE.search(first):
        return "machine"
    return "other"


def _dates(text: str):
    """(YYYY-MM-DD, as written, start, end) of each valid date in a text."""
    for m in _DATE.finditer(text):
        g = m.groupdict()
        i = next(i for i in "1234" if g[f"y{i}"])
        month = g[f"m{i}"]
        month = int(month) if month.isdigit() else _MONTHS[month.lower()]
        try:
            d = date(int(g[f"y{i}"]), month, int(g[f"d{i}"]))
        except ValueError:
            continue
        yield d.isoformat(), m.group(), m.start(), m.end()


def _date_in(text: str) -> tuple[str, str] | None:
    """(YYYY-MM-DD, as written) of the first valid date in a text."""
    return next(((iso, written) for iso, written, _, _ in _dates(text)), None)


def _rates(text: str) -> list[Rate]:
    """Printed exchange rates; the date is the text of the nearest preceding parentheses when it holds one."""
    rates = []
    for m in _RATE.finditer(text):
        base = float(m.group("b").replace(",", "."))
        quote = float((m.group("q1") or m.group("q2")).replace(",", "."))
        if not base:
            continue
        before = re.findall(r"\(([^()]*)\)", text[max(0, m.start() - 150):m.start()])
        when = _date_in(before[-1]) if before else None
        rate = Rate(_currency(m.group("bc")), _currency(m.group("qc1") or m.group("qc2")), round(quote / base, 6),
                    when[1] if when else None)
        if rate not in rates:
            rates.append(rate)
    return rates


@dataclass
class _Block:
    label: list[str]  # the item's name and the lines before its amounts
    amounts: dict[str, float] = field(default_factory=dict)
    page: int | None = None
    ambiguous: bool = False
    inline: bool = False  # the last label line carried the amounts itself
    description: list[str] = field(default_factory=list)  # lines after its amounts ("including travel")


def _is_heading(words: str, line: str, following: str | None) -> bool:
    """'PRICE', '3. PRIX', 'Prix / Price', an all-caps line without digits ('PRICES AND OPTIONS'), a short
    numbered section heading followed by a label line that is no description ('2. Proposal' + 'In this
    quotation...', but not '2. Equipment upgrade' + '- new grippers'), or a price intro ending with ':' and
    followed by text, not amounts ('The price for the equipment described in this offer is:' but not
    'Installation price:' + 'CHF 25,000'). `following` is the next line when it has text. Never a total or
    a subtotal."""
    text_follows = following is not None
    if _total_strength(line) or _SUBTOTAL.search(line):
        return False
    if _HEADING.fullmatch(words):
        return True
    if (_SECTION.match(line) and len(line.split()) <= 6 and text_follows and not _is_description(following)
            and not machines.models_in(line) and not _QUANTITY.match(_NUMBERING.sub("", line))):
        return True
    if line.endswith(":") and text_follows and _PRICE_WORD.search(line) and not machines.models_in(line):
        return True
    return words == words.upper() and any(c.isalpha() for c in words) and not re.search(r"\d", words)


def _is_description(line: str) -> bool:
    return line[:1].islower() or bool(_DESCRIPTION.match(line))


def _is_page_header(line: str) -> bool:
    """A running header: an offer number and a date on one line ('ACME 3001234 - 02 04.11.2024')."""
    return bool(_OFFER_NO.search(line) and _NUM_DATE.search(line))


def _item_name(line: str) -> str:
    """The line with a space after a quantity glued to the name ('2xPaloma 3R' -> '2x Paloma 3R'), so that
    the machine is found in it."""
    return re.sub(r"^(\s*\d+\s*[x×])(?=[^\W\d_])", r"\1 ", line)


def _has_item(line: str) -> bool:
    """A quantity ('2x') or a machine family on the line."""
    return bool(_QUANTITY.match(line) or machines.models_in(_item_name(line)))


def _names_kind(line: str) -> bool:
    """A discount, option or service word: the line names an item of its own ('Training:')."""
    return bool(_DISCOUNT.search(line) or _OPTION.search(line) or _SERVICE.search(line))


def _is_intro(line: str, following: str | None) -> bool:
    """An introduction of the items, with a label line after it: an intro phrase ('In this quotation, we
    kindly offer you:', 'The following options are also possible:'), or a line ending with ':' right above an
    item with a quantity or a machine ('Gerne unterbreiten wir Ihnen das folgende Angebot:' + '2x Presto 2R').
    Never a line with a quantity or a machine itself; a line ending with ':' that names a kind ('Training:',
    'Option N°2 au choix:') is the item."""
    if following is None or _has_item(line):
        return False
    if _INTRO.search(line):
        return True
    return line.endswith(":") and not _names_kind(line) and _has_item(following)


def _is_incoterm(line: str) -> bool:
    """An Incoterm code with 'Incoterms' or a place, or at the start of the line; never in a description."""
    if _is_description(line) or not _INCOTERM.search(line):
        return False
    return bool(_INCOTERMS_WORD.search(line) or _INCOTERM_PLACE.search(line)
                or re.match(rf"^\W*(?:\d+(?:\.\d+)*\.?\s+)?{_INCOTERM_CODES}(?![\w-])", line))


def _delivered(label: list[str]) -> bool:
    """A delivered price ('CIP Berlin (INCOTERMS 2010) EUR ... CHF ...'): its last label line holds the
    Incoterm, or 'Incoterms' under a line that holds the code ('FCA Ecublens' + '(Incoterms 2010)'), and no
    label line names an item (quantity, machine, kind word): '1x Paloma 3R, livré DAP Lyon' and 'Transport
    and insurance CIP Berlin' are ordinary lines that state their delivery term."""
    if not label or any(_has_item(line) or _names_kind(line) for line in label if not _is_description(line)):
        return False
    if _is_incoterm(label[-1]):
        return True
    return (len(label) > 1 and bool(_INCOTERMS_WORD.search(label[-1])) and not _is_description(label[-2])
            and bool(_INCOTERM.search(label[-2])))


def _item(label: list[str]) -> tuple[list[str], bool]:
    """(the block's label from its item line on, True when a left-out introduction announces options).

    Introductions and Incoterm captions above the item are left out ('2. Proposal' and the page header are
    no label lines). The item line is the first line with a quantity or a machine family, else the first
    line; never a description line, and never past a line that names a kind of its own ('Option dismantling
    of the hood' before 'Access to the Delfi ...', 'Commissioning' before 'For the 2 Delfi cells'). Kind,
    quantity, family and model come from it."""
    lines, option_intro = [], False
    for i, line in enumerate(label):
        following = label[i + 1] if i + 1 < len(label) else None
        if _is_intro(line, following):
            option_intro |= bool(_OPTION.search(line))  # "The following options are also possible:"
        elif not (following is not None and _is_incoterm(line) and not _has_item(line) and not _names_kind(line)):
            lines.append(line)  # else the Incoterm caption of the total above
    start = 0
    for i, line in enumerate(lines):
        if _is_description(line) or line.startswith("("):
            continue
        if _has_item(line) or _names_kind(line):
            start = i  # a kind word: an item name of its own
            break
    return lines[start:], option_intro


def _blocks(pages: dict[int, str], nums: list[int]) -> list[_Block]:
    """The section's text cut into blocks: label lines, then the amounts that follow them."""
    blocks: list[_Block] = []
    label: list[str] = []
    block: _Block | None = None
    prev_amounts = describing = False

    def close():
        nonlocal block, label
        if block and block.amounts:
            blocks.append(block)
        block, label = None, []

    def add(amts, page: int, inline: bool = False):
        nonlocal block
        for c, v, ambiguous in amts:
            if block is None:
                block = _Block(label, inline=inline)
            elif c in block.amounts:  # a second value in the same currency starts a new block
                close()
                block = _Block([])
            block.amounts[c] = v
            block.page = block.page or page
            block.ambiguous |= ambiguous

    rows = []  # (page, line, amounts, label, words) without blank lines, running headers and page numbers
    for p in nums:
        first = True
        for line in _blank_rates(pages[p]).split("\n"):  # a rate wrapped over two lines is no line
            stripped = line.strip()
            if not stripped:
                continue
            amts, text, words = _split(line)
            # Header and footer shapes inside a description stay: '- see www.example.com for specs'.
            letterhead = ((not _is_description(stripped) or stripped.lower().startswith("www."))
                          and (_is_page_header(stripped) or _FOOTER.search(stripped)))
            if not amts and (_PAGE_NO.match(stripped) or (first and _RUNNING.search(stripped)) or letterhead):
                first = False
                continue  # running header, page number or the company's footer
            first = False
            rows.append((p, stripped, amts, text, words))

    for i, (p, stripped, amts, text, words) in enumerate(rows):
        following = rows[i + 1][1] if i + 1 < len(rows) and rows[i + 1][4] else None
        if not amts and (not words or _is_heading(words, stripped, following)):
            if words and prev_amounts:
                close()
                prev_amounts = False
            describing = False
            continue
        if words:
            just_closed = prev_amounts
            if prev_amounts:
                close()
            if not amts and (just_closed or describing) and blocks and _is_description(stripped):
                blocks[-1].description.append(stripped)  # the previous item's description
                describing, prev_amounts = True, False
                continue
            describing = False
            if label and _total_strength(label[-1]):
                label.pop()  # a total-like sentence without amounts: "Alle Preise verstehen sich netto ab Werk."
            label.append(text)
            if amts:
                add(amts, p, inline=True)
                close()
        else:
            add(amts, p)
        prev_amounts = bool(amts)
    close()
    return blocks


def _bracketed(text: str) -> set[str]:
    """Currencies whose every amount in the text is in brackets: conversions printed beside the main
    price ('(EUR 400 000.-)' above 'CHF 410 000.-')."""
    inside, outside = set(), set()
    for start, end, currency, _, _ in _amount_spans(text):
        line_end = text.find("\n", end)
        before = text[text.rfind("\n", 0, start) + 1:start]
        after = text[end:len(text) if line_end < 0 else line_end]
        opened = before.rfind("(") > before.rfind(")")
        closed = ")" in after and ("(" not in after or after.index(")") < after.index("("))
        (inside if opened and closed else outside).add(currency)
    return inside - outside


def _sums_match(lines: list[PriceLine], totals: dict[str, float], text: str) -> bool:
    """The lines add up to the printed total in every currency. The main currency (the first total not in
    brackets in `text`, else the first total) allows one unit of rounding per line, at most 5: a small
    line or a misread digit is never lost in it. A conversion currency, converted and rounded line by line,
    allows max(5, 0.05 %)."""
    conversions = _bracketed(text)
    main = next((c for c in totals if c not in conversions), next(iter(totals), None))

    def tolerance(currency: str, total: float) -> float:
        return min(5, len(lines)) if currency == main else max(5, 0.0005 * abs(total))
    return all(abs(sum(l.amounts.get(c, 0.0) for l in lines) - t) <= tolerance(c, t) for c, t in totals.items())


def parse_section(pages: dict[int, str]) -> PriceSection | None:
    """The offer's price section: lines, totals per currency, sum check, exchange rates. None when not found.

    sum_ok is False (unreadable) when amounts have no label, a line with amounts mentions a total
    without being a total label, priced lines other than options follow the total (options listed after
    it are extras: kept, out of the sum, R35), or (without a total) an amount may hold a fused quantity.
    A delivered price ('CIP Berlin (INCOTERMS 2010) ...') is the total when none is printed; after a
    printed total it is left out when it reaches that total, else it is a late line. Kind, quantity,
    family and model come from the item line (_item); the line's text starts there."""
    nums = price_pages(pages)
    if not nums:
        return None
    blocks = _blocks(pages, nums)
    if not blocks:
        return None
    total_block, strength = None, 0  # the printed total, known before the lines: a delivered price is compared
    for b in blocks:
        s = _total_strength(b.label[-1]) if b.label else 0
        if s > strength:  # the first net total, else the first short total label
            total_block, strength = b, s
    printed = dict(total_block.amounts) if total_block else {}
    lines: list[PriceLine] = []
    totals: dict[str, float] = {}
    delivered: dict[str, float] = {}
    total_at, delivered_at, unreadable, ambiguous = 0, 0, False, False
    for b in blocks:
        if b is total_block:
            totals, total_at = dict(b.amounts), len(lines)
            continue
        if b.label and _total_strength(b.label[-1]):
            continue
        if b.label and _SUBTOTAL.search(b.label[-1]):
            continue  # a subtotal: the lines already hold its amounts
        if _delivered(b.label):
            if not printed:  # the total
                if not delivered:
                    delivered, delivered_at = dict(b.amounts), len(lines)
                continue
            if any(c in printed and v >= printed[c] for c, v in b.amounts.items()):
                continue  # the printed total delivered (transport and insurance added)
            # else less than the printed total: an ordinary (late) line
        if not b.label or (b.inline and _TOTAL_WORD.search(b.label[-1])):
            unreadable = True  # amounts without a label, or "Total 4 cells CHF ...": never an ordinary line
            continue
        item, option_intro = _item(b.label)
        text = " ".join(" ".join(item + b.description).split())
        q = _QUANTITY.match(item[0])
        family, model = _machine(_item_name(item[0]))
        kind = classify(_item_name("\n".join(item)), b.amounts)
        ambiguous |= b.ambiguous
        lines.append(PriceLine(len(lines) + 1, text, "option" if option_intro and kind != "discount" else kind,
                               int(q.group(1)) if q else None, dict(b.amounts), b.page, family, model))
    text = "\n".join(pages[p] for p in nums)
    if totals:
        late = lines[total_at:]  # R13: a late line makes the sum unchecked, unless it is an option (R35)
        sum_ok = (_sums_match(lines[:total_at], totals, text) and not unreadable
                  and all(l.kind == "option" for l in late))
    elif delivered:  # the delivered price is the total; it may follow or precede the lines
        totals = delivered
        counted = lines[:delivered_at] + [l for l in lines[delivered_at:] if l.kind != "option"]
        sum_ok = _sums_match(counted, totals, text) and not unreadable
    else:
        sum_ok = False if unreadable or ambiguous else None
    rates = _rates(text)
    return PriceSection(lines, totals, rates, nums, sum_ok)


def plausible_date(iso: str) -> bool:
    """An offer date: from 1990-01-01 to one year after today (an empty Excel date cell reads 1899-12-31 or
    1900-01-01; a validity date may lie ahead)."""
    return FIRST_DATE <= date.fromisoformat(iso) <= date.today() + timedelta(days=365)


def offer_date(pages: dict[int, str]) -> tuple[str, str] | None:
    """(YYYY-MM-DD, as written) of the first plausible date (plausible_date) on pages 1-2 outside an
    exchange-rate sentence.

    A date in parentheses is a rate date also when its sentence hints at a rate ('actual rate',
    'cours du jour') or the next line holds the rate (a rate sentence wrapped over two lines)."""
    for p in (1, 2):
        rows = pages.get(p, "").split("\n")
        for i, row in enumerate(rows):
            rate_next = i + 1 < len(rows) and _RATE.search(rows[i + 1])
            for sentence in _SENTENCE_END.split(row):
                if _RATE.search(sentence) or _RATE_WORDS.search(sentence):
                    continue
                for iso, written, start, end in _dates(sentence):
                    if not plausible_date(iso):
                        continue
                    in_parens = sentence.rfind("(", 0, start) > sentence.rfind(")", 0, start) and ")" in sentence[end:]
                    if in_parens and (rate_next or _RATE_HINT.search(sentence)):
                        continue
                    return iso, written
    return None


def offer_type(pages: dict[int, str], name: str) -> str:
    """'budget' when pages 1-2 or the file name say budget / Richtpreis / indicative..., else 'firm'."""
    text = "\n".join(pages.get(p, "") for p in (1, 2)) + "\n" + name
    return "budget" if _BUDGET.search(text) else "firm"


MODEL_ROLE = "You read the price section of a sales offer for industrial packaging machines."
# The instructions come after the pages' text: a prompt cut by the model's window loses its start, not them.
MODEL_PROMPT = """The text above is the offer's pages; it may be garbled (tables flattened, columns mixed). \
Answer only with a JSON object with exactly these keys:
{"lines": [{"name": "...", "text": "...", "quantity": null, "amounts": {"CHF": 0.0}}],
 "totals": {"CHF": 0.0}, "rates": [{"base": "EUR", "quote": "CHF", "rate": 1.0, "date": null}]}
Rules:
- "lines": one entry per priced line (machine, option, service, discount) in document order; "name" is \
the item's name as printed (its first line), "text" is the name with its short description if any.
- "amounts": every amount of the line as printed, with its currency code (CHF, EUR, USD...); several \
currencies on one line give several entries. Numbers only, no thousands separators. A discount is negative.
- No line for totals or subtotals, for rates per day or per hour, or for items priced "on request".
- "quantity": only when the text states it (2x, 2 pcs), else null.
- "totals": the net total per currency as printed, empty when none is printed.
- "rates": the exchange rates printed in the text (1 EUR = 1.20 CHF gives base EUR, quote CHF, rate 1.2), \
"date" as written, or null.
- Never convert currencies, never compute anything, never invent a line or an amount.
- A page marked [Page N] may be followed by lines of that page: add "page": N to a line only when you are \
sure of its page."""


def _model_amount(v) -> float | None:
    """A number, or a numeric string read like the rules read amounts; None for anything else."""
    if isinstance(v, bool):
        return None
    if isinstance(v, str):
        v = parse_number(v)
    return float(v) if isinstance(v, (int, float)) and math.isfinite(v) else None


def _model_currency(k) -> str | None:
    c = _currency(k.strip()) if isinstance(k, str) else ""
    return c if re.fullmatch(r"[A-Z]{3}", c) else None


def _model_amounts(d) -> dict[str, float]:
    out: dict[str, float] = {}
    for k, v in (d.items() if isinstance(d, dict) else ()):
        cur, value = _model_currency(k), _model_amount(v)
        if cur and value is not None:
            out[cur] = value
    return out


def _model_quantity(v) -> int | None:
    v = parse_number(v) if isinstance(v, str) else v
    ok = isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
    return int(v) if ok and v > 0 and v == int(v) else None


def _model_rate(r) -> Rate | None:
    if not isinstance(r, dict):
        return None
    base, quote = _model_currency(r.get("base")), _model_currency(r.get("quote"))
    try:
        rate = float(str(r.get("rate")).replace(",", "."))
    except ValueError:
        return None
    when = r.get("date")
    if not (base and quote and base != quote and math.isfinite(rate) and rate > 0):
        return None
    return Rate(base, quote, round(rate, 6), when.strip() or None if isinstance(when, str) else None)


def _printed_numbers(text: str) -> set[float]:
    """The absolute values of the numbers printed in a text (amounts, quantities, rates...)."""
    values = (parse_number(m.group()) for m in re.finditer(_NUM, text))
    return {round(abs(v), 2) for v in values if v is not None}


def parse_with_model(url: str, model: str, pages: dict[int, str], timeout: float) -> PriceSection | None:
    """The price section read by the local model, for pages the rules cannot split.

    The pages sent are the rules' price pages, else pages 1-6. The model's amounts, totals and rates are
    cleaned like printed ones; kind, family and model come from the same rules as parse_section, on the
    item's name only, and sum_ok is recomputed. sum_ok is False when an amount or a total is not printed
    in the pages sent (a model that computes or invents), so a wrong answer never looks checked. None when
    the answer has no line. Raises vision.VisionError (timeout=True for a timeout, also a cut prompt)."""
    nums = price_pages(pages) or [p for p in sorted(pages) if p <= MODEL_PAGES]
    if not nums:
        return None
    sent = "\n\n".join(f"[Page {p}]\n{pages[p]}" for p in nums)
    answer = vision.chat_json(url, model, f"{MODEL_ROLE}\n\n{sent}\n\n{MODEL_PROMPT}", timeout)
    raw = answer.get("lines")
    if raw and not isinstance(raw, list):
        raise vision.VisionError("answer without a list of lines: " + str(raw)[:200])
    lines: list[PriceLine] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        name, text = item.get("name"), item.get("text")
        text = text if isinstance(text, str) and text.strip() else name  # no description: the name is the text
        if not isinstance(text, str):
            continue
        label = " ".join(text.split())
        name = " ".join(name.split()) if isinstance(name, str) and name.strip() else text.strip().split("\n")[0]
        amounts = _model_amounts(item.get("amounts"))
        if not label or not amounts:
            continue
        page = item.get("page")
        family, model_name = _machine(_item_name(name))
        lines.append(PriceLine(len(lines) + 1, label, classify(_item_name(name), amounts),
                               _model_quantity(item.get("quantity")),
                               amounts, page if type(page) is int and page in nums else nums[0],
                               family, model_name))
    if not lines:
        return None
    totals = _model_amounts(answer.get("totals"))
    rates: list[Rate] = []
    for r in answer.get("rates") if isinstance(answer.get("rates"), list) else []:
        rate = _model_rate(r)
        if rate and rate not in rates:
            rates.append(rate)
    printed = _printed_numbers(sent)
    values = [v for l in lines for v in l.amounts.values()] + list(totals.values())
    if any(round(abs(v), 2) not in printed for v in values):
        sum_ok = False
    else:
        sum_ok = _sums_match(lines, totals, sent) if totals else None
    return PriceSection(lines, totals, rates, nums, sum_ok)

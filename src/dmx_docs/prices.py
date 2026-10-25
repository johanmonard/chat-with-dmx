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
# 'Euro', 'EURO', '€uro' before or after the number are EUR too ("Equipement : €uro 410'000.-- (CHF 500'000.--)").
_CUR = r"(?<![A-Za-z])(?:CHF|EUR|USD|GBP|US\$|SFr\.?|Fr\.|€uros?|€UROS?|(?i:euros?)|€|\$|£)(?![A-Za-z])"
# After the number, a euro word is a sentence's word ('for an amount of 4'000.- Euros') or the scrambled text of a
# garbled PDF ('264'500.-- €uro price'), not a price column: only before the number.
_CUR_AFTER = _CUR.replace("|€uros?|€UROS?|(?i:euros?)", "")
# Thousands grouped by space, apostrophe, dot or comma, or plain digits; up to 2 decimals; not part
# of a date or a longer number ("Fr. 12.05.2020").
_NUM = rf"(?:\d{{1,3}}(?:[{_SP}'’.,]\d{{3}})+(?:[.,]\d{{1,2}})?|\d+(?:[.,]\d{{1,2}})?)(?![.,/]?\d)"
_END = rf"(?:\.?[{_DASH}]{{1,2}}(?!\d))?"  # "410 000.-", "410 000.--"
_AMOUNT = re.compile(
    rf"(?P<n1>[{_DASH}])?(?P<c1>{_CUR})[{_SP}]*(?P<n2>[{_DASH}])?[{_SP}]*(?P<v1>{_NUM}){_END}"
    rf"|(?P<n3>[{_DASH}])?(?P<v2>{_NUM}){_END}[{_SP}]*(?P<c2>{_CUR_AFTER})(?![{_SP}]*[{_DASH}]?[{_SP}]*\d)")
_CUR_TOKEN = re.compile(_CUR)
# "Paloma 4R 1 150 000 €": a quantity may be fused with a space-grouped amount written number first,
# when what follows the 1-2 digit lead is itself an amount of two 3-digit groups or more.
_FUSED = re.compile(rf"\d{{1,2}}[{_SP}]\d{{3}}[{_SP}]\d{{3}}")
# A rate per day or hour after the amount, also on the next line ('CHF 1'100' + 'pro Zusatztag möglich'), also 'per
# additional day', 'par jour supplémentaire'.
_PER = re.compile(rf"[{_SP}]*\n?[{_SP}]*(?:/[{_SP}]*(?:tage?|days?|jours?|h|hours?|stunden?|heures?)\b"
                  rf"|(?:per|par|pro)[{_SP}]+(?:(?:additional|extra|further|zusätzliche[nrm]?|weitere[nrm]?)[{_SP}]+)?"
                  rf"(?:day|jour|tag|zusatztag|mehrtag|hour|heure|stunde|zusatzstunde)\b)", re.I)
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
# A description line: a bullet (also the Wingdings bullets of Word documents, U+F0xx) or an including-word.
_DESCRIPTION = re.compile(r"[->»•*–▪●◦►➢✓-]|(?:including|inkl|einschliesslich|einschließlich|y compris"
                          r"|comprenant)\b", re.I)
# An item name opening its description on the same line: 'A Delfi cell including: Four Delta Robots ...'.
_INCLUDING_COLON = re.compile(r"\b(?:including|incl\.?|incluant|comprenant|compos[ée]e? de|[ée]quip[ée]e? de|equipped with"
                              r"|einschlie(?:ss|ß)lich|bestehend aus|ausgestattet mit)"
                              r"(?:\s+(?:each|jeweils|chacune?))?\s*:", re.I)
_NUMBER_WORD = (r"(?:one|two|three|four|five|six|seven|eight|nine|ten|un|une|deux|trois|quatre|cinq|sept|huit"
                r"|neuf|dix|ein|eine|zwei|drei|vier|fünf|sechs|sieben|acht|neun|zehn)")
# "2x Paloma", "2 x Paloma", "2xPaloma", "Four (4) Presto": group 1 or group 2 is the quantity.
_QUANTITY = re.compile(rf"^\s*(?:(\d+)\s*[x×](?=\s|[^\W\d_])|(?i:{_NUMBER_WORD})\s*\((\d+)\)(?=\s))")
# A count before a name ('1 Wyzo Sidebot', '2 Infeed belt conveyor'): an item's name when it carries the amount.
_BARE_COUNT = re.compile(r"^\s*\d{1,3}\s+[^\W\d_]")
# Included items ('Equipement € Inclus', 'Emballage, Transport et Assurance Inclus', 'Included in the equipment
# price'): a line without amounts; not a sentence ('... sont inclus.'). Not included items are no lines.
_INCL = r"(?:inclus|incluse?s?|included|inbegriffen|inklusive|compris|enthalten)"
_INCLUDED_START = re.compile(rf"^\W*{_INCL}\b", re.I)
_INCLUDED_END = re.compile(rf"(?:(?:{_CUR}|[|:])[{_SP}]*(?i:{_INCL})|[{_SP}](?:Inclus|Included|Inbegriffen|Inklusive"
                           rf"|INCLUS|INCLUDED))[{_SP}]*$")
_NOT_INCLUDED = re.compile(r"(?i:non|not|nicht|pas)\s+(?i:inclus|included|inbegriffen|compris|enthalten)\b")
_MD_HEADING = re.compile(r"^#{1,6}\s+")  # a heading of a Word document's text ('## Prix, net, hors taxe')
# A page header repeated on each page: an offer number and a date ("ACME 3001234 - 02 04.11.2024").
_OFFER_NO = re.compile(r"(?<!\d)\d{7}(?!\d)|(?<![A-Za-z0-9])[BFE]-\d{6,7}(?!\d)")
_NUM_DATE = re.compile(r"(?<![\d.])(?:\d{1,2}\.\d{1,2}\.\d{4}|\d{1,2}/\d{1,2}/\d{4}|\d{4}-\d{2}-\d{2})(?!\d)")
# A numbered section heading ("2. Proposal", "2. Proposition Equipement"), short and without amounts.
_SECTION = re.compile(r"^\d+(?:\.\d+)*\.?\s*(?:proposal|proposition|angebot|vorschlag|offer|offre|equipment|équipement"
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
# The introduction of the offered items ("In this quotation, we kindly offer you:") and of options
# ("The following options are also possible:"): left out of the item's label.
_INTRO = re.compile(r"we kindly offer you|nous avons le plaisir de vous propos|wir freuen uns|nous vous proposons"
                    r"|we are pleased to offer|wir schlagen ihnen", re.I)
_OPTION_INTRO = re.compile(r"following options? (?:is|are) (?:also )?possible|les options suivantes|l.option suivante"
                           r"|folgenden? optionen?", re.I)
# An item line that is an option by its first word ("Option: ...", "Optional ...", "En option ..."): with
# an option introduction, the only lines after the net total that are extras out of the sum (R35).
_VARIANT = re.compile(r"^\W*(?:\d+(?:\.\d+)*\.?\s+)?(?:variante?|alternative)\b", re.I)
_OPTION_START = re.compile(r"^\W*(?:\d+(?:\.\d+)*\.?\s+)?"
                           r"(?:options?|optional|optionen|en\s+option|variante\s+option)\b", re.I)

_DISCOUNT = re.compile(r"\b(?:remise|rabais|rabatt|discount|nachlass|d[ée]duction|abzug|gutschrift)", re.I)
_OPTION = re.compile(r"\boption", re.I)
_SERVICE = re.compile(r"\b(?:installation|inbetriebnahme|mise en service|commissioning|training|formation|schulung"
                      r"|travel|reise|déplacement|transport|transportation|documentation|sat|fat|montage|monteur"
                      r"|supervision|intervention|on[- ]site|I&C|engineering|ingénierie|ingenierie|project management"
                      r"|projektmanagement|projektleitung|gestion de projet)(?![\w&])", re.I)
# Machine words: a cell or line, and the equipment of an offer ('Equipement (selon §6.2)', conveyors, case packers).
_MACHINE = re.compile(r"\b(?:cells?|cellules?|zellen?|lines?|lignes?|linien?|robot\w*|equipment|[ée]quipements?"
                      r"|ausrüstung|lieferumfang|conveyors?|convoyeurs?|förder\w*|case ?packers?|encaisseuses?"
                      r"|erect(?:ors?|ing)|formeuses?|fermeuses?|closers?|denesters?|dépileurs?|sidebots?"
                      r"|cartoners?|cartonneuses?|encartonneuses?|[ée]tuyeuses?|kartonierer|kartoniermaschinen?)\b", re.I)
# Packaging as the item itself ('Packaging', 'Emballage', 'Packaging for ACME equipment'): crating, a service.
_PACKING = re.compile(r"^\W*(?:\d+\s*[x×]?\s*)?(?:packaging|emballage|verpackung)\b"
                      r"(?!\s+(?:cells?|lines?|machines?|robots?|systems?|modules?)\b)", re.I)

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
    rf"|(?P<d3>\d{{1,2}})(?:\.|er|st|nd|rd|th)?(?:\s+of)?\s+(?P<m3>{_MONTH})\s+(?P<y3>\d{{4}})"
    rf"|(?P<m4>{_MONTH})\s+(?P<d4>\d{{1,2}})(?:st|nd|rd|th)?,?\s+(?P<y4>\d{{4}}))(?!\d)", re.I)
# Sentence ends: a line break, or . ! ? after a non-digit ("1. November 2011" is not an end).
_SENTENCE_END = re.compile(r"\n|(?<=[^\d\s][.!?])\s+")
_RATE_WORDS = re.compile(r"tageskurs|wechselkurs|taux de change|cours de change|exchange rate|rate of exchange", re.I)
# Looser hints, only for a date in parentheses: "actual rate (November 1st, 2011)", "cours du jour (12.03.2019)".
_RATE_HINT = re.compile(r"\brates?\b|\bcours\b|kurs|\btaux\b|\bexchange\b", re.I)
# Budget wording on pages 1-3 or in the file name ('Offre budgétaire', 'Budgetangebot', 'Price indication'). Not
# 'indicative' or 'estimation' alone: 'prices in EUR are indicative', 'the working time is an estimation'.
_BUDGET_PHRASE = re.compile(r"\b(?:budget(?:ary)?[- ]?(?:offer|quotation|proposal|angebot|price)"
                            r"|offres? budg[ée]taires?|prix budg[ée]taires?|richtpreis|richtangebot|preisindikation"
                            r"|price indication|indication de prix)", re.I)
_ESTIMATION = (r"|prix sont (?:une )?estimation|prices? (?:is|are) an estimation|offre estimation|estimation offer"
               r"|estimation of\b[^\n]{0,80}?(?:CHF|EUR|USD|€)")
_BUDGET = re.compile(r"\b(?:budget|budg[ée]taire|richtpreis|richtangebot|preisindikation|price indication"
                     r"|indication de prix|prix indicatifs?|offre indicative|indicative (?:offer|price|quotation|proposal)"
                     r"|preisangaben?\b(?!\s+in\b)|kostenvoranschlag|oferta presupuestaria|oferta indicativa"
                     r"|offerta indicativa|preventivo (?:indicativo|di massima)"
                     + _ESTIMATION + ")", re.I)
# The offer calling itself budget or non-binding, on any page (its validity section: 'This budgetary proposal is
# intended as a base for discussion', 'Cette offre budgétaire vous est fournie', 'Dieses Budgetangebot', 'Es ist als
# Budget zu verstehen', 'Unverbindliches Angebot', '... and is therefore not binding'). Not 'brochures are not binding',
# 'Lieferzeit: unverbindlich', nor another offer ('following this budgetary proposal 3001233', _budget_said).
_BUDGET_OWN = re.compile(
    r"\b(?:this|the present)\s+budget(?:ary)?\s+(?:offer|quotation|proposal|price)s?\b"
    r"|validity of the budgetary (?:offer|quotation|proposal)"
    r"|\b(?:cette|la pr[ée]sente)\s+offre\s+budg[ée]taire|validit[ée] de l.offre budg[ée]taire"
    r"|\b(?:dieses|diese|vorliegende[sn]?)\s+(?:budgetangebot|richtangebot|kostenvoranschlag)\b"
    r"|g[üu]ltigkeit des budgetangebot|als budget zu verstehen|\bunverbindliche[sn]?\s+angebot"
    r"|\b(?:offers?|proposals?|quotations?|prices?)\s+(?:is|are)\s+(?:therefore\s+)?(?:non[- ]binding|not binding)"
    r"|oferta presupuestaria|presupuesto (?:indicativo|orientativo)|offerta (?:di )?budget", re.I)
# A document calling itself formal on page 1 or in its name ('Formal Quotation', 'Offre formelle', 'Verbindliches
# Angebot'), not a sentence asking for one ('a formal proposal is required', 'avant l'établissement d'une offre formelle').
_FORMAL = re.compile(r"(?<!\ba )(?<!\ban )(?<!une )(?<!eine )"
                     r"\b(?:formal\s+(?:quotation|proposal|offer)|offre\s+formelle|formelles\s+angebot"
                     r"|verbindliches\s+angebot|oferta\s+formal|offerta\s+formale)\b", re.I)
# A date cited in the letter (a visit, a meeting, the client's request or letter) is not the offer's date.
_CITED_DATE = re.compile(r"(?:visite?s?|besuch\w*|r[ée]union|meeting|rencontre|entretien|anfrage|demande|request"
                         r"|inquiry|enquiry|lettre|letter|schreiben|courrier)\b[^.\n]{0,50}?"
                         r"\b(?:du|vom|of|dated|from|on|am|le)\s*$", re.I)
# A budget offer cited by another offer ('remplace l'offre budgétaire n° ...', 'par rapport à l'offre budgétaire
# B-...', 'following our budgetary offer'): words before the hit, or another offer's number right after it.
_CITED_BEFORE = re.compile(r"(?:remplac\w*|par rapport [àa]|d[ée]fini\w* dans|replac\w*|following|further to"
                           r"|with reference to|suite [àa]|r[ée]f[ée]rence [àa]|gem[äa]ss|bezugnehmend auf|ersetzt)"
                           r"\W+(?:[\w'’]+\W+){0,3}$", re.I)
_NUMBER_AFTER = re.compile(r"[\w]*\W{1,4}(?:[^\W\d]+\W{1,4})?(?:n[°o]s?\.?\s*|nr\.?\s*|#\s*)?(?:[BFE][- ]?)?(\d{6,7})(?!\d)",
                           re.I)


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


def _blank_rates(text: str, per_day: bool = True) -> str:
    """Exchange rates replaced by spaces, same length and line breaks kept: a rate wrapped after '='
    ('1 Euro =' then '1.20 CHF.') never leaves an amount on a line of its own. per_day: also the amounts of rates per
    day or hour, also when the 'per day' words are on the next line ('CHF 1'100' + 'pro Zusatztag möglich.')."""
    text = _RATE.sub(lambda m: re.sub(r"[^\n]", " ", m.group()), text)
    if per_day:
        source = text
        text = _AMOUNT.sub(lambda m: " " * len(m.group()) if _PER.match(source, m.end()) else m.group(), source)
    return text


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


def _rate_values(text: str) -> set[float]:
    """Values printed as rates per day or hour ('1100 CHF/Day')."""
    text = _blank_rates(text, per_day=False)
    return {v for m in _AMOUNT.finditer(text) if _PER.match(text, m.end())
            if (v := parse_number(m.group("v1") or m.group("v2"))) is not None}


# Tables of Word documents ('Pos | Désignation | Qté | Prix/unit. CHF | Total CHF'): the currency is in the header
# of the price column, the rows hold bare numbers. A row's amount is its last cell, printed as a price ("12'000.00",
# "600.00"); position, quantity and unit price cells are left out.
_PRICE_HEADER = re.compile(r"\b(?:prix|price|preis|total|amount|montant|betrag|summe)", re.I)
_CUR_CELL = re.compile(r"(?<![A-Za-z])(?:CHF|EUR|USD|GBP|US\$|€uros?|euros?|€|\$|£|SFr\.?|Fr\.)(?![A-Za-z])", re.I)
_CELL_PRICE = re.compile(rf"[{_DASH}]?[{_SP}]*(?:\d{{1,3}}(?:['’ .,]\d{{3}})+(?:[.,]\d{{2}})?|\d+[.,]\d{{2}})"
                         rf"{_END}")
_CUR_SPLIT = re.compile(rf"({_CUR_CELL.pattern})[{_SP}]*\|[{_SP}]*(?=[{_DASH}]?[{_SP}]*\d)", re.I)


def _tables(text: str) -> str:
    """The text with each priced row of a table under a currency header written as '<text> <amount> <currency>'.
    A row with as many cells as the header takes the last column of each currency ('Total CHF | Total €uros':
    both; 'Prix/unit. CHF | Total CHF': the total); any other row its last cell in the header's last currency."""
    out, currency, columns, width = [], None, {}, 0
    tiers: dict[int, str] = {}  # a tiered table: column -> tier label ('≥ 10 machines'), in `currency`
    for line in text.split("\n"):
        cells = [c.strip() for c in line.split("|")] if "|" in line else []
        if tiers and not cells:
            tiers, currency = {}, None
        if not tiers and cells and (tier := _tier_header(cells)):
            currency, tiers = tier
            out.append("PRICES")  # the header row: a price heading, no item's text
            continue
        if tiers:
            prices_at = [i for i in tiers if i < len(cells) and _CELL_PRICE.fullmatch(cells[i])]
            if not prices_at:  # a second header row ('| 0% | 10% | 20%'): part of the tier labels
                for i in tiers:
                    if i < len(cells) and cells[i]:
                        tiers[i] = f"{tiers[i]} {cells[i]}"
                out.append("")
                continue
            name = " ".join(c for i, c in enumerate(cells) if c and i not in tiers)
            out.append("\n".join(f"{name} ({tiers[i]}) {currency} {cells[i]}" for i in prices_at))
            continue
        if (not cells and line.strip() and not line.rstrip().endswith(":")
                and (len(line.split()) > 6 or line.rstrip().endswith("."))):
            currency, columns, width = None, {}, 0  # a sentence: the table has ended (a label 'Engineering :' has not)
        if cells:
            head = [(i, c) for i, c in enumerate(cells)
                    if _PRICE_HEADER.search(c) and _CUR_CELL.search(c) and len(c.split()) <= 4]
            if head and not _AMOUNT.search(line):
                columns = {_currency(_CUR_CELL.findall(c)[-1]): i for i, c in head}  # the last column per currency
                currency, width = _currency(_CUR_CELL.findall(head[-1][1])[-1]), len(cells)
                out.append("")  # the header row is no item's text
                continue
            filled = [c for c in cells if c]
            if currency and filled and _CELL_PRICE.fullmatch(filled[-1]) and not _amount_spans(line):
                words = [c for c in filled[:-1] if not _CELL_PRICE.fullmatch(c) and parse_number(c) is None
                         and not _CUR_CELL.fullmatch(c)]
                priced = [f"{cur} {cells[i]}" for cur, i in columns.items() if _CELL_PRICE.fullmatch(cells[i])] \
                    if len(cells) == width else []
                line = " ".join(words + (priced or [currency, filled[-1]]))
            else:  # a currency and its amount in two cells: 'Equipement | (EUR | 101'500.--) | CHF | 120'000.--'
                line = _CUR_SPLIT.sub(lambda m: f"{_currency(m.group(1))} ", line)
        out.append(line)
    return "\n".join(out)


def _tier_header(cells: list[str]) -> tuple[str, dict[int, str]] | None:
    """(currency, {column: tier label}) of the header of a tiered price table ('All prices are in CHF * | < 10 machines |
    ≥ 10 machines | ≥ 20 machines'): one cell names prices and a currency, two or more others are tier labels with a
    number and neither a currency nor a price word. A row of such a table gives a line per tier (no total: the tiers
    are alternatives)."""
    head = [c for c in cells if _PRICE_HEADER.search(c) and _CUR_CELL.search(c) and len(c.split()) <= 8]
    labels = {i: c for i, c in enumerate(cells)
              if c and re.search(r"\d", c) and not _CUR_CELL.search(c) and not _PRICE_HEADER.search(c)}
    if len(head) != 1 or len(labels) < 2 or cells.index(head[0]) in labels:
        return None
    return _currency(_CUR_CELL.findall(head[0])[-1]), labels


def _prepared(pages: dict[int, str]) -> dict[int, str]:
    return {p: _tables(t) for p, t in pages.items()}


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


def _prose(line: str) -> bool:
    """A sentence quoting a price ('A credit note of CHF 600.- , that is 4 hours, will be issued with this order ...'):
    12 words or more, and 4 words or more after its last amount. A priced line ends with its amounts."""
    spans = _amount_spans(line)
    return (bool(spans) and len(line.split()) >= 12
            and len(re.findall(r"[^\W\d_]{2,}", line[spans[-1][1]:])) >= 4)


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


def _included_line(line: str, following_amounts_only: bool = False) -> str | None:
    """'in' for an included item's line ('Equipement € Inclus', 'Inclus dans le prix de l'équipement'), 'out' for a
    not included one ('Not included', 'Verpackung ... Nicht inbegriffen'), else None. Never a sentence ending with a
    period ('... sont inclus.', 'inbegriffen.'), never a line whose amounts follow on the next line."""
    line = line.strip()
    if not line or following_amounts_only or _is_description(line):
        return None
    short = len(line.split()) <= 12
    m = _NOT_INCLUDED.search(line)
    if m and short and not line[m.end():].strip(" .;"):
        lead = line[:m.start()]
        if not lead.strip() or line[m.start()].isupper() or re.search(rf"(?:{_CUR}|[|:])[{_SP}]*$", lead):
            return "out"
    if not line.endswith((".", ":")) and _INCLUDED_END.search(line):
        return "in"
    if _INCLUDED_START.match(line) and line[:1].isupper() and short and not line.endswith(":"):
        return "in"
    return None


def _has_included(text: str) -> bool:
    return any(_included_line(line) == "in" for line in text.split("\n"))


def price_pages(pages: dict[int, str], legacy: bool = False) -> list[int]:
    """From a page with a price word and 2+ amounts (or 1 amount and an included item: 'Equipement € Inclus',
    'Total € 150'000') through the page holding the total (at most 3 consecutive pages). The first such run that
    ends in a total wins, else the first candidate page alone. A run that ends in a total also takes the pages
    before it that hold 2+ amounts and no total (items listed under 'Proposal' without a price word), within the
    3 pages, and the next page when options are announced after the total ('The following options are also
    possible:' at the end of the total page or at the top of the next one).
    legacy=True: the choice before 2026-10-19 (no table, included item or extension rule), only to find the model's
    answers cached for it."""
    if not legacy:
        pages = _prepared(pages)
    first: list[int] = []
    for p in sorted(pages):
        text = pages[p]
        found = amounts_in(text)
        if not (_PRICE_WORD.search(text) and (len(found) >= 2 or (not legacy and found and _has_included(text)))):
            continue
        for k in range(MAX_PAGES):
            if p + k not in pages:
                break
            if _has_total(pages[p + k]):
                run = list(range(p, p + k + 1))
                return run if legacy else _extended(pages, run)
        first = first or [p]
    return first


# A price section's title: '9.6. Price, net, without taxes, EXW ...', '10.1. Prix, net, hors taxe', '9.4. BUDGETARY PRICE'.
_PRICE_TITLE = re.compile(r"^\W*(?:\d+(?:\.\d+)*\.?\s*)?(?:(?:(?:budgetary\s+)?prices?|prix|preise?)\b.{0,40}?"
                          r"(?:\bnet\b|\bnetto\b|hors\s+taxes?|without\s+taxes|ohne\s+steuern)"
                          r"|budgetary\s+prices?\b|prix\s+budg[ée]taires?\b|richtpreise?\b)", re.I | re.M)


def pages_without_amounts(pages: dict[int, str]) -> list[int]:
    """Pages holding a price section's title and no amount at all: the numbers are missing from the text (a table
    the OCR or the PDF's text layer lost), so the section cannot be read from the text; the page image can."""
    return [p for p in sorted(pages) if _PRICE_TITLE.search(pages[p]) and not amounts_in(_tables(pages[p]))]


def _extended(pages: dict[int, str], run: list[int]) -> list[int]:
    while (len(run) < MAX_PAGES and run[0] - 1 in pages and len(amounts_in(pages[run[0] - 1])) >= 2
           and not _has_total(pages[run[0] - 1])):
        run.insert(0, run[0] - 1)
    last, nxt = pages[run[-1]], run[-1] + 1
    spans = _amount_spans(last)
    after = last[spans[-1][1]:] if spans else ""
    if nxt in pages and amounts_in(pages[nxt]):
        head = _amount_spans(pages[nxt])
        before = pages[nxt][:head[0][0]] if head else pages[nxt]
        if _OPTION_INTRO.search(after) or _OPTION_INTRO.search(before):
            run.append(nxt)
    return run


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
    ('Paloma 3R cell including installation'). An option word wins unless a machine or a service word comes
    before it ('Option: Paloma 2R second cell' but '1x Paloma 3R cell with optional vision', 'Installation
    incl. options')."""
    first = text.split("\n", 1)[0]
    if any(v < 0 for v in amounts.values()) or _DISCOUNT.search(first):
        return "discount"
    family, service = machines._FAMILY.search(first), _SERVICE.search(first)  # _FAMILY: what models_in finds
    option = _OPTION.search(first)
    if option and len(first.split()) > 8 and len(first[:option.start()].split()) > 3:
        option = None  # an option word deep in a long description ('... optional extended-range head')
    if option and not any(m and m.start() < option.start() for m in (family, service)):
        return "option"
    if family and not (service and service.start() < family.start()):
        return "machine"
    if service or _PACKING.match(first):  # 'Packaging', 'Emballage' alone: crating
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
    inline: bool = False  # the last label line carried the amounts (or 'Inclus') itself
    description: list[str] = field(default_factory=list)  # lines after its amounts ("including travel")
    included: bool = False  # an included item: no amounts ('Emballage, transport et assurance Inclus')
    name_line: str | None = None  # the machine named after a generic price ('Equipment CHF ...' + '1 Paloma 2R cell:')


def _is_heading(words: str, line: str, following: str | None) -> bool:
    """'PRICE', '3. PRIX', 'Prix / Price', an all-caps line without digits ('PRICES AND OPTIONS'), a short
    numbered section heading followed by a label line that is no description ('2. Proposal' + 'In this
    quotation...', but not '2. Equipment upgrade' + '- new grippers'), or a price intro ending with ':' and
    followed by text, not amounts ('The price for the equipment described in this offer is:' but not
    'Installation price:' + 'CHF 25,000'). `following` is the next line when it has text. Never a total or
    a subtotal, never one or two words naming a kind ('SAT', 'TRAINING')."""
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
    if len(words.split()) <= 2 and _names_kind(words):
        return False  # 'SAT', 'TRAINING', 'OPTION': an item's name in capitals
    caps = _NUMBERING.sub("", words)  # '9. SERVICES INCLUS'
    return caps == caps.upper() and any(c.isalpha() for c in caps) and not re.search(r"\d", caps)


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
    Never a line with a quantity or a machine itself; a line that names a kind ('Training:', 'Option N°2 au
    choix:', 'Nous vous proposons la formation suivante:') is the item, unless it introduces options."""
    if following is None or _has_item(line):
        return False
    if _OPTION_INTRO.search(line) or (_INTRO.search(line) and _OPTION.search(line)):
        return True  # "We are pleased to offer the following options:"
    if _names_kind(line):
        return False
    return bool(_INTRO.search(line)) or (line.endswith(":") and _has_item(following))


def _is_incoterm(line: str) -> bool:
    """An Incoterm code with 'Incoterms' or a place, or at the start of the line; never in a description."""
    if _is_description(line) or not _INCOTERM.search(line):
        return False
    return bool(_INCOTERMS_WORD.search(line) or _INCOTERM_PLACE.search(line)
                or re.match(rf"^\W*(?:\d+(?:\.\d+)*\.?\s+)?{_INCOTERM_CODES}(?![\w-])", line))


def _delivered(label: list[str]) -> bool:
    """A delivered price ('CIP Berlin (INCOTERMS 2010) EUR ... CHF ...'): its last label line holds the
    Incoterm, or 'Incoterms' under a line that holds the code ('FCA Ecublens' + '(Incoterms 2010)'). No label
    line has a quantity or a machine ('1x Paloma 3R, livré DAP Lyon' is a line that states its delivery
    term), and no other label line names a kind ('Transport and insurance' above 'CIP Berlin ...'); the
    Incoterm line itself may, after the code only ('Prix rendu DAP Lyon, transport et assurance compris'
    but not 'Transport and insurance CIP Berlin')."""
    if not label:
        return False
    if _is_incoterm(label[-1]):
        at = len(label) - 1
    elif (len(label) > 1 and _INCOTERMS_WORD.search(label[-1]) and not _is_description(label[-2])
          and _INCOTERM.search(label[-2])):
        at = len(label) - 2
    else:
        return False
    code = _INCOTERM.search(label[at]).start()
    if any(m.start() < code for rx in (_DISCOUNT, _OPTION, _SERVICE) for m in rx.finditer(label[at])):
        return False
    return not any(_has_item(line) or (i != at and _names_kind(line))
                   for i, line in enumerate(label) if not _is_description(line))


@dataclass
class _Item:
    lines: list[str]  # the label from the item line on: the line's text
    option_intro: bool  # a left-out introduction announces options
    kind_line: str  # the line the kind comes from
    name: str  # the line quantity, family and model come from


def _names_item(line: str) -> bool:
    """The line can be an item's name: a quantity, a count or a machine first ('1 Globex Sidebot', '2 Infeed belt
    conveyor'), a variant, or a kind or machine word (among the first three words of a long line)."""
    w = _item_name(line)
    if _has_item(w) or _starts_item(w) or _BARE_COUNT.match(w) or _VARIANT.match(w) or _PACKING.match(w):
        return True
    words = _NUMBERING.sub("", w).split()
    head = w if len(words) <= 8 else " ".join(words[:3])
    return bool(_names_kind(head) or _MACHINE.search(head))


def _kind_first(line: str) -> bool:
    """A kind word among the first two words ('Installation and setup of one line', 'Special deduction:')."""
    return _names_kind(" ".join(_NUMBERING.sub("", _item_name(line)).split()[:2]))


def _item(label: list[str], inline: bool = False) -> _Item:
    """The block's item.

    Introductions and Incoterm captions above the item are left out ('2. Proposal' and the page header are
    no label lines). When the last label line carries the amounts and names an item ('2 Infeed belt conveyor
    CHF ...'), it is the item line: the lines above it are the previous item's description or a paragraph
    ('Zusätzliche Tage sind auf Anfrage ...'), unless the line right above it names a kind first and ends
    no sentence ('Installation and commissioning of the' + 'Paloma cells at customer site CHF ...'). Else the
    item line is the first line with a quantity, a machine family or a kind word, else
    the first line; never a description line, never a line that only mentions a machine below a kind line.
    - A quantity or a machine line: kind, quantity, family and model come from it.
    - A service, option or discount line ('Commissioning' + 'For the Delfi cells', 'Option' + 'Paloma 2R
      second cell'): it keeps its kind; quantity, family and model may come from the next line naming one.
    - A variant line ('Variante avec option nettoyage' + '1x Paloma 4R'): the kind, quantity and model come
      from the item named below it.
    The line's text starts at the item line: nothing below it is dropped."""
    lines, option_intro = [], False
    for i, line in enumerate(label):
        following = label[i + 1] if i + 1 < len(label) else None
        if _is_intro(line, following):
            option_intro |= bool(_OPTION.search(line))  # "The following options are also possible:"
        elif not (following is not None and _is_incoterm(line) and not _has_item(line) and not _names_kind(line)):
            lines.append(line)  # else the Incoterm caption of the total above
    named = [i for i, line in enumerate(lines) if not _is_description(line) and not line.startswith("(")]
    start, last = None, len(lines) - 1
    if inline and last in named and _names_item(lines[last]):
        start = last
        above = lines[last - 1] if last - 1 in named else ""
        if above and not re.search(r"[.!?]\s*$", above) and (_kind_first(above) or _VARIANT.match(above)):
            start = last - 1
    if start is None:
        start = next((i for i in named
                      if _has_item(lines[i]) or _names_kind(lines[i]) or _VARIANT.match(lines[i])), 0)
    kind_line = name = lines[start]
    if not _has_item(lines[start]):
        below = [i for i in named if i > start]
        if _VARIANT.match(lines[start]):  # the variant of an item named below it
            kind_line = name = next((lines[i] for i in below if _starts_item(lines[i])), kind_line)
        else:  # a kind line keeps its kind; the machine may be named below it
            name = next((lines[i] for i in below if _has_item(lines[i])), name)
    return _Item(lines[start:], option_intro, kind_line, name)


def _starts_item(line: str) -> bool:
    """An item name: a quantity first, or a machine family among the first three words ('1x Paloma 4R',
    'A Paloma D2 cell', 'Equipement 1 Presto DC'), not a sentence that mentions one ('Access to the Delfi')."""
    if _QUANTITY.match(line):
        return True
    m = machines._FAMILY.search(_item_name(line))
    return bool(m) and len(_item_name(line)[:m.start()].split()) <= 2


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

    rows = []  # (page, line, amounts, label, words, Word heading) without blank lines, running headers, page numbers
    for p in nums:
        first = True
        for line in _blank_rates(pages[p]).split("\n"):  # a rate wrapped over two lines is no line
            stripped = line.strip()
            if not stripped:
                continue
            md = bool(_MD_HEADING.match(stripped))
            if md:
                line = stripped = _MD_HEADING.sub("", stripped)
            amts, text, words = _split(line)
            if amts and _prose(line):  # a price quoted in a sentence: no priced line
                amts, text = [], stripped
            # Header and footer shapes inside a description stay: '- see www.example.com for specs'.
            letterhead = ((not _is_description(stripped) or stripped.lower().startswith("www."))
                          and (_is_page_header(stripped) or _FOOTER.search(stripped)))
            if not amts and (_PAGE_NO.match(stripped) or (first and _RUNNING.search(stripped)) or letterhead):
                first = False
                continue  # running header, page number or the company's footer
            first = False
            rows.append((p, stripped, amts, text, words, md))

    for i, (p, stripped, amts, text, words, md) in enumerate(rows):
        nxt = rows[i + 1] if i + 1 < len(rows) else None
        following = nxt[1] if nxt and nxt[4] else None
        if not amts and (not words or _is_heading(words, stripped, following)):
            if words and prev_amounts:
                close()
                prev_amounts = False
            elif words and block is None:
                label = []  # the text before a heading belongs to no item ('9.2. Services ...' before '10. PRIX')
            describing = False
            continue
        if words:
            just_closed = prev_amounts
            if prev_amounts:
                close()
            if md and not amts:
                label = []  # a Word heading starts an item's label ('## Emballage, transport et assurance')
            if not amts and (just_closed or describing) and blocks and (
                    _is_description(stripped) or (describing and stripped.startswith("("))):
                blocks[-1].description.append(stripped)  # the previous item's description
                describing, prev_amounts = True, False
                continue
            if (not amts and blocks and (just_closed or (describing and blocks[-1].name_line))
                    and not any(_has_item(x) for x in blocks[-1].label) and _starts_item(stripped)
                    and ((stripped.endswith(":") and nxt and _is_description(nxt[1])) or _INCLUDING_COLON.search(stripped))):
                # 'Equipment CHF 320'000' then '1 Paloma 2R Top Loader cell including each :' and its bullets, or a
                # Word table cell with its description glued: 'A Delfi Feed Placer cell including: Four Delta Robots ...'
                blocks[-1].description.append(stripped)
                blocks[-1].name_line = blocks[-1].name_line or stripped
                describing, prev_amounts = True, False
                continue
            describing = False
            if label and _total_strength(label[-1]):
                if amts and label[-1].endswith((",", "-")):  # 'PRICE, net, without taxes,' + 'FCA ... CHF ...'
                    text = label.pop() + " " + text
                elif _NUMBERING.match(label.pop()):  # '7.2. Prix du système de convoyeurs, net, hors taxe': a
                    label = []  # heading; the text before it belongs to no item (a footnote of the section above)
                # else a total-like sentence without amounts: "Alle Preise verstehen sich netto ab Werk."
            if not amts:
                inc = _included_line(stripped, following_amounts_only=bool(nxt and nxt[2] and not nxt[4]))
                if inc == "out":  # 'Packaging, transport and insurance: Not included': no line
                    label, prev_amounts = [], False
                    continue
                if inc == "in":
                    named = bool(label or _INCLUDED_END.search(stripped))  # else a note: 'Included in the price'
                    if named:
                        blocks.append(_Block(label + [text], page=p, included=True,
                                             inline=bool(_INCLUDED_END.search(stripped))))
                    label, prev_amounts = [], named
                    continue
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
    # A total currency no line carries is the total's conversion ('EUR 347,000.- CHF 357,000.-' under CHF lines):
    # nothing to check in it. When no line carries any total currency, nothing is checked: False.
    checked = {c: t for c, t in totals.items() if any(c in l.amounts for l in lines)}
    return bool(checked) and all(abs(sum(l.amounts.get(c, 0.0) for l in lines) - t) <= tolerance(c, t)
                                 for c, t in checked.items())


def parse_section(pages: dict[int, str]) -> PriceSection | None:
    """The offer's price section: lines, totals per currency, sum check, exchange rates. None when not found.

    sum_ok is False (unreadable) when amounts have no label, a line with amounts mentions a total
    without being a total label, priced lines other than extras follow the total (options listed after it,
    _OPTION_START or an option introduction, R35; deductions, R36; included items: kept, out of the sum), or
    (without a total) an amount may hold a fused quantity. sum_ok is None when no line before the total carries
    an amount (every item 'Inclus': nothing to check).
    A delivered price ('CIP Berlin (INCOTERMS 2010) ...') is the total when none is printed; after a
    printed total it is left out when it reaches that total, else it is a late line. Kind, quantity,
    family and model come from the item line (_item); the line's text starts there. Included items are lines
    without amounts."""
    pages = _prepared(pages)
    nums = price_pages(pages)
    if not nums:
        return None
    blocks = _blocks(pages, nums)
    if not blocks:
        return None
    total_block, strength = None, 0  # the printed total, known before the lines: a delivered price is compared
    for b in blocks:
        s = _total_strength(b.label[-1]) if b.label and not b.included else 0
        if s > strength:  # the first net total, else the first short total label
            total_block, strength = b, s
    printed = dict(total_block.amounts) if total_block else {}
    # Without a printed total, the largest delivered price is the total; the others are ordinary lines.
    candidates = [b for b in blocks if not printed and b.label and not b.included and not _total_strength(b.label[-1])
                  and not _SUBTOTAL.search(b.label[-1]) and _delivered(b.label)]
    ref = next(iter(candidates[0].amounts)) if candidates else None
    delivered_block = max(candidates, key=lambda b: b.amounts.get(ref, -math.inf)) if candidates else None
    lines: list[PriceLine] = []
    extras: list[bool] = []  # per line: may follow the total, out of the sum (options R35, deductions R36, included)
    totals: dict[str, float] = {}
    delivered: dict[str, float] = {}
    total_at, delivered_at, unreadable, ambiguous, options_follow = 0, 0, False, False, False
    for b in blocks:
        if b is total_block:
            totals, total_at = dict(b.amounts), len(lines)
            continue
        if not b.included:
            if b.label and _total_strength(b.label[-1]):
                continue
            if b.label and _SUBTOTAL.search(b.label[-1]):
                continue  # a subtotal: the lines already hold its amounts
            if b is delivered_block:  # the total
                delivered, delivered_at = dict(b.amounts), len(lines)
                continue
            if printed and _delivered(b.label) and any(c in printed and v >= printed[c] for c, v in b.amounts.items()):
                continue  # the printed total delivered (transport and insurance added); a smaller one is a late line
            if not b.label or (b.inline and _TOTAL_WORD.search(b.label[-1])):
                unreadable = True  # amounts without a label, or "Total 4 cells CHF ...": never an ordinary line
                continue
        item = _item(b.label, b.inline)
        text = " ".join(" ".join(item.lines + b.description).split())
        name = item.name
        if b.name_line and not _has_item(name):  # 'Equipment' priced, the machine named after it
            name = b.name_line
        q = _QUANTITY.match(name)
        family, model = _machine(_item_name(name))
        kind = classify(_item_name(item.kind_line), b.amounts)
        # After the total, an option introduction announces every line that follows it (R35).
        options_follow |= item.option_intro and bool(totals)
        intro = (item.option_intro or options_follow) and kind != "discount"
        ambiguous |= b.ambiguous
        lines.append(PriceLine(len(lines) + 1, text, "option" if intro else kind,
                               int(q.group(1) or q.group(2)) if q else None, dict(b.amounts), b.page, family, model))
        # R35: an extra after the total is announced by an option introduction, or its item line starts with
        # an option word, and no quantity, machine or service word is on it or named below it. R36: a deduction
        # after the total is taken from it. An included item carries no amount.
        extras.append(intro or b.included or kind == "discount"
                      or (kind == "option" and bool(_OPTION_START.match(item.kind_line))
                          and not any(_has_item(x) or _SERVICE.search(x) for x in (item.kind_line, item.name))))
    text = "\n".join(pages[p] for p in nums)
    if totals:  # R13: a late line makes the sum unchecked, unless it is an extra (R35, R36)
        counted = lines[:total_at]
        sum_ok = (False if unreadable or not all(extras[total_at:])
                  else None if not any(l.amounts for l in counted) else _sums_match(counted, totals, text))
    elif delivered:  # the delivered price is the total; it may follow or precede the lines
        totals = delivered
        counted = lines if not delivered_at else lines[:delivered_at] + [  # extras after it: out of the sum
            l for l, extra in zip(lines[delivered_at:], extras[delivered_at:]) if not extra]
        sum_ok = (False if unreadable else None if not any(l.amounts for l in counted)
                  else _sums_match(counted, totals, text))
    else:
        if not any(l.amounts for l in lines):
            return None  # only included items, no total: nothing priced, no price section (R38)
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
                    if _CITED_DATE.search(sentence[max(0, start - 80):start]):
                        continue  # 'votre visite dans nos locaux du 21.07.2003', 'Ihre Anfrage vom ...'
                    return iso, written
    return None


def _budget_said(text: str, rx: re.Pattern, own: str | None) -> bool:
    """A budget word that is not a citation of another offer (_CITED_BEFORE, another offer number after it)."""
    for m in rx.finditer(text):
        if _CITED_BEFORE.search(text[max(0, m.start() - 60):m.start()]):
            continue
        number = _NUMBER_AFTER.match(text, m.end())
        if number and own and number.group(1) != own:
            continue
        return True
    return False


def offer_type(pages: dict[int, str], name: str, key: str | None = None) -> str:
    """'budget' when page 1 or the file name say budget / budgétaire / Richtpreis / price indication / 'les prix sont
    une estimation'..., or page 2 does, or page 3 in a budget phrase ('This budgetary proposal', not 'Budget for flow
    wrapper' in a firm offer), else 'firm'. A budget offer cited by another offer ('remplace l'offre budgétaire
    n° ...') does not count. The offer calling itself budget or non-binding on any page (_BUDGET_OWN: its validity
    section) is budget. A formal offer (F- number) is budget only by page 1 or its file name."""
    own = re.search(r"\d{6,7}", key or name)
    own = own.group() if own else None
    formal = bool(re.match(r"\W*F[- ]?\d", key or "") or re.search(r"(?<![A-Za-z0-9])F-\d{6,7}", name))
    first = pages.get(1, "") + "\n" + name
    if _budget_said(first, _BUDGET, own) or _budget_said(first, _BUDGET_OWN, own):
        return "budget"
    if formal:
        return "firm"
    # A formal cover keeps a budget template's validity text out (R45): 'Formal Quotation' + 'This budgetary proposal...'.
    own_wording = not _FORMAL.search(first) and any(_budget_said(t, _BUDGET_OWN, own) for p, t in pages.items() if p > 1)
    return "budget" if (_budget_said(pages.get(2, ""), _BUDGET, own)
                        or _budget_said(pages.get(3, ""), _BUDGET_PHRASE, own) or own_wording) else "firm"


def is_formal(pages: dict[int, str], name: str, key: str | None = None) -> bool:
    """The document calls itself formal: an F- number, or 'Formal Quotation', 'Offre formelle', 'Verbindliches Angebot'
    on page 1 or in its file name (R45)."""
    return bool(re.match(r"\W*F[- ]?\d", key or "") or re.search(r"(?<![A-Za-z0-9])F-\d{6,7}", name)
                or _FORMAL.search(pages.get(1, "") + "\n" + name))


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
    return clean_model(PriceSection(lines, totals, rates, nums, None), pages, checked=False)


def classify_again(section: PriceSection | None) -> PriceSection | None:
    """A cached model answer's lines of kind 'other' classified again with today's rules (the kinds were stored when
    the answer was cached), on the item's name: the text up to its first separator ('1 Case erector ACME - hotmelt'
    -> '1 Case erector ACME'). Family and model are filled when the name gives them; other kinds are kept."""
    for line in section.lines if section else []:
        if line.kind != "other":
            continue
        name = _item_name(re.split(r"\s+[-–•|]\s+|:|\(|\|", line.text, maxsplit=1)[0])
        line.kind = classify(name, line.amounts)
        if line.family is None:
            line.family, line.model = _machine(name)
    return section


def _model_sum(lines: list[PriceLine], totals: dict[str, float], sent: str) -> bool | None:
    """False when an amount or a total is not printed in the pages sent (R16), else the sum check."""
    printed = _printed_numbers(sent)
    values = [v for l in lines for v in l.amounts.values()] + list(totals.values())
    if any(round(abs(v), 2) not in printed for v in values):
        return False
    return _sums_match(lines, totals, sent) if totals else None


def clean_model(section: PriceSection | None, pages: dict[int, str], checked: bool = True) -> PriceSection | None:
    """A model's section (also a cached one) without what is no price: amounts of 0 (the model's 'no price', never
    printed as such), values printed only as rates per day or hour ('1100 CHF/Day') and totals not printed in the
    pages sent (the model's own sum, R40: no total rather than a total nobody printed). A line left without amounts
    is dropped, positions follow; None when no line is left. sum_ok is computed again when something was dropped
    (always when checked is False)."""
    if section is None:
        return None
    sent = "\n\n".join(f"[Page {p}]\n{pages[p]}" for p in section.pages if p in pages)
    rates = _rate_values(sent) - {v for _, v in amounts_in(sent)}

    def price(v: float) -> bool:
        return v != 0 and abs(v) not in rates

    lines, changed = [], False
    for l in section.lines:
        amounts = {c: v for c, v in l.amounts.items() if price(v)}
        changed |= amounts != l.amounts
        if amounts:
            lines.append(PriceLine(len(lines) + 1, l.text, l.kind, l.quantity, amounts, l.page, l.family, l.model))
    printed = _printed_numbers(sent)  # a total not printed is the model's own sum: no total (R40)
    totals = {c: v for c, v in section.totals.items() if price(v) and round(abs(v), 2) in printed}
    changed |= totals != section.totals
    if not lines:
        return None
    sum_ok = _model_sum(lines, totals, sent) if changed or not checked else section.sum_ok
    return PriceSection(lines, totals, section.rates, section.pages, sum_ok)

"""Offer numbers, revision labels and country names found in the folders and files of the offers
tree (sales\\Clients). Pure functions on names: no file access.

An offer number is written many ways (`3002113 - Line 5`, `B-250306 V1`, `Angebot - 3002113_02.pdf`);
offer_key gives one canonical string so that every way of writing it matches.
"""

from __future__ import annotations

import re

from .register import fold

# Top folders of the tree that are not clients (keys folded: accents and case removed).
SPECIAL = {"01_historique": "archive", "03_accord de confidentialite": "nda", "04_infos pratiques": "sales_info",
           "06_offres ipso": "partner", "07_offres_wyzo": "partner", "08_offres client inconnu": "unknown_client",
           "09_marketstudy": "market_study"}
# The same keys under register.fold (punctuation and case-insensitive): what the lookups use.
SPECIAL_FOLDED = {fold(k): v for k, v in SPECIAL.items()}

_EXTENSION = re.compile(r"\.[A-Za-z0-9]{2,5}$")
# Archive numbers: B-250306 (offer), F-... and E-... prefixes, six digits, anywhere in the name.
_ARCHIVE = re.compile(r"(?<![A-Za-z0-9])([BFE])-(\d{6})(?!\d)")
# 7-digit numbers (0909009), 12-digit numbers (IPSO); a `-A` suffix only when the letter ends the word.
_NUMBER = re.compile(r"(?<!\d)(\d{7}|\d{12})(?!\d)(-[A-Z](?=$|[\s_.)]))?")


def offer_key(name: str) -> str | None:
    """Canonical key of an offer folder or file name, None when the name carries no offer number."""
    stem = _EXTENSION.sub("", name.strip())
    m = _ARCHIVE.search(stem)
    if m:
        return f"{m.group(1)}-{m.group(2)}"
    m = _NUMBER.search(stem)
    if m:
        return m.group(1) + (m.group(2) or "")
    return None


_REVISION_PATTERNS = (re.compile(r"(\d{2})(?:\s*-\s*(.+))?"), re.compile(r"(V\d+)", re.I), re.compile(r"(Q\d{2})", re.I))


def revision_of(name: str) -> tuple[str, str | None] | None:
    """(label, variant) of a revision folder name (`02`, `V3`, `Q01`, `04 - Vandelay version`), else None."""
    name = name.strip()
    for pattern in _REVISION_PATTERNS:
        m = pattern.fullmatch(name)
        if m:
            variant = m.group(2).strip() if pattern.groups > 1 and m.group(2) else None
            return m.group(1).upper(), variant
    return None


def revision_order(label: str) -> tuple[int, int, str]:
    """Sort key of a revision label: a later revision is larger (V and Q labels sort after numbers: the
    kinds do not mix within one offer)."""
    m = re.fullmatch(r"(\d+)", label)
    if m:
        return (0, int(m.group(1)), label)
    m = re.fullmatch(r"V(\d+)", label, re.I)
    if m:
        return (1, int(m.group(1)), label)
    m = re.fullmatch(r"Q(\d+)", label, re.I)
    if m:
        return (2, int(m.group(1)), label)
    return (-1, 0, label)


# English name -> names in French, German and English, and common abbreviations.
_COUNTRIES = {
    "Albania": ["Albanie", "Albanien"],
    "Algeria": ["Algerie", "Algerien"],
    "Argentina": ["Argentine", "Argentinien"],
    "Australia": ["Australie", "Australien"],
    "Austria": ["Autriche", "Osterreich", "Oesterreich"],
    "Belgium": ["Belgique", "Belgien"],
    "Brazil": ["Bresil", "Brasilien"],
    "Bulgaria": ["Bulgarie", "Bulgarien"],
    "Canada": ["Kanada", "Quebec", "Ontario", "British Columbia", "Alberta"],  # provinces in addresses
    "Chile": ["Chili"],
    "China": ["Chine"],
    "Colombia": ["Colombie", "Kolumbien"],
    "Croatia": ["Croatie", "Kroatien"],
    "Cyprus": ["Chypre", "Zypern"],
    "Czech Republic": ["Republique tcheque", "Tchequie", "Tschechien", "Tschechische Republik", "Czechia"],
    "Denmark": ["Danemark", "Danmark"],
    "Egypt": ["Egypte", "Agypten", "Aegypten"],
    "Estonia": ["Estonie", "Estland"],
    "Finland": ["Finlande", "Finnland"],
    "France": ["Frankreich"],
    "Germany": ["Allemagne", "Deutschland"],
    "Greece": ["Grece", "Griechenland"],
    "Hungary": ["Hongrie", "Ungarn"],
    "India": ["Inde", "Indien"],
    "Indonesia": ["Indonesie", "Indonesien"],
    "Iran": [],
    "Ireland": ["Irlande", "Irland"],
    "Israel": [],
    "Italy": ["Italie", "Italien"],
    "Japan": ["Japon"],
    "Jordan": ["Jordanie", "Jordanien"],
    "Kazakhstan": ["Kazakhstan", "Kasachstan"],
    "Kuwait": ["Koweit"],
    "Latvia": ["Lettonie", "Lettland"],
    "Lebanon": ["Liban", "Libanon"],
    "Lithuania": ["Lituanie", "Litauen"],
    "Luxembourg": ["Luxemburg"],
    "Malaysia": ["Malaisie"],
    "Mexico": ["Mexique", "Mexiko"],
    "Morocco": ["Maroc", "Marokko"],
    "Netherlands": ["Pays-Bas", "Pays Bas", "Niederlande", "Holland", "Hollande", "The Netherlands"],
    "New Zealand": ["Nouvelle-Zelande", "Neuseeland"],
    "Norway": ["Norvege", "Norwegen"],
    "Peru": ["Perou"],
    "Poland": ["Pologne", "Polen", "Polska"],
    "Portugal": [],
    "Romania": ["Roumanie", "Rumanien", "Rumaenien"],
    "Russia": ["Russie", "Russland", "Russian Federation", "Federation de Russie"],
    "Saudi Arabia": ["Arabie saoudite", "Saudi-Arabien", "Saudiarabien", "KSA"],
    "Serbia": ["Serbie", "Serbien"],
    "Singapore": ["Singapour", "Singapur"],
    "Slovakia": ["Slovaquie", "Slowakei"],
    "Slovenia": ["Slovenie", "Slowenien"],
    "South Africa": ["Afrique du Sud", "Sudafrika", "Suedafrika"],
    "South Korea": ["Coree du Sud", "Sudkorea", "Suedkorea", "Korea"],
    "Spain": ["Espagne", "Spanien"],
    "Sweden": ["Suede", "Schweden"],
    "Switzerland": ["Suisse", "Schweiz", "Svizzera"],
    "Thailand": ["Thailande"],
    "Tunisia": ["Tunisie", "Tunesien"],
    "Turkey": ["Turquie", "Turkei", "Tuerkei", "Turkiye"],
    "Ukraine": [],
    "United Arab Emirates": ["Emirats arabes unis", "Vereinigte Arabische Emirate", "UAE"],
    "United Kingdom": ["Royaume-Uni", "Royaume Uni", "Vereinigtes Konigreich", "Grossbritannien",
                       "Angleterre", "England", "Great Britain", "UK", "GB"],
    "United States": ["Etats-Unis", "Etats Unis", "Vereinigte Staaten", "USA", "US", "United States of America"],
    "Venezuela": [],
    "Vietnam": ["Viet Nam"],
}
_COUNTRY_BY_NAME = {fold(alias): english for english, aliases in _COUNTRIES.items()
                    for alias in [english, *aliases]}


def country_en(name: str) -> str | None:
    """English name of a country written in French, German or English (a folder name or a word)."""
    return _COUNTRY_BY_NAME.get(fold(name))

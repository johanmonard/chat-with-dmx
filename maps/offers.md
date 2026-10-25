# Offers - map

## What this world is for
Every offer sent to a client, won or not, with all its revisions, from the 1999 archive to today:
the offer documents, technical solutions, specifications, Outlook mails, layouts, visit reports,
partner offers, market studies, NDAs and sales conditions (PDF, Word, PowerPoint, mails; French,
English, German). For sales and management only: do not pass its content on. Use it for what was
quoted, to whom, when, at which price and with what result - not for what was built (Projects world).

Two parts: the **documents** (`search`, like the other worlds) and the **offer register**, one row
per priced line of every offer revision, read from the offer documents (amounts per currency exactly
as printed, source path and page), with each offer's status and the user's corrections. Prices,
counts and histories come from the register, never from search (search returns only the best excerpts).

## The register and its four tools
| Tool | Use it for |
|---|---|
| `offer_lines` | the rows of a price chart or table: one per priced line with its kind, in one currency (CHF by default), last revision of each offer unless `revisions="all"`; with `machine=` only machine lines unless `kind=` is given |
| `list_offers` | one line per offer, newest first: client, dates, revisions, machine lines, total, status; a client's history |
| `offer_card` | everything about one offer: revisions, lines, rates, status and its evidence, corrections, other documents |
| `offer_stats` | counts of offers and revisions by `group_by=` one or two of year, country, client, machine, status, author, partner, offer_type, category |

Each ends with a **coverage line** (offers matched, with readable prices, left out and why, then the
offers named): show it under every chart or count. With `machine=` it also names the offers without
readable prices, which could not be checked for that machine. Currencies are never converted.

## Kinds of information
| Kind | Where | Filter | Answers questions like |
|---|---|---|---|
| Offers and revisions | `<client>\Offres\<offer>\<revision>` (00, 01, Q01, V1...), variants as extra folders | `doc_type="offer"` + `client=`, `offer=`, `revision=` | "the offer of client X, all revisions" |
| Technical solutions | beside the offer, `Solution_Technique_...` | `doc_type="technical_solution"` | "how was the line described" |
| Specifications | `Specification`, `Cahier des charges` | `doc_type="specification"` | "what did the client ask" |
| Mails | `Mail(s)` (`.msg`: subject, from, date, body; attachments by name only) | `doc_type="mail"` | "what was negotiated, objections" |
| Layouts | `Layout(s)` (PDF only; `.dwg` not indexed) | `doc_type="layout"` | "the proposed layout" |
| Visit reports | client visit reports | `doc_type="visit_report"` | "what we saw on site" |
| Archive offers by country | `01_Historique\PROSPECT\<country>\<client>\<offer>`, numbers `B-`, `F-`, `E-` + 6 digits | `category="archive"` + `country=` | "offers to Germany before 2010" |
| Partner offers | `06_Offres IPSO`, `07_Offres_Wyzo\<client>` | `category="partner"` or `partner=` | "offers made through a partner" |
| Unknown client | `08_Offres client inconnu` | `category="unknown_client"` | |
| Market studies | `09_MarketStudy\<country>` | `category="market_study"` | "market study of country X" |
| NDAs | `03_Accord de confidentialité` | `category="nda"` | "is there an NDA with X" |
| Sales conditions | `04_Infos pratiques` (conditions, insurance, bank, register) | `category="sales_info"` | "standard sales conditions" |

`search` also filters by `machine=`, `year_from=` / `year_to=` (the offer's last revision) and
`status=` (won, won_unconfirmed, no_order_found, open, unknown, lost, cancelled) - these leave out documents
whose facet is unknown: search again without them when results are thin.

## Not in this world - look there
- Costing sheets (`.xls`, QPC `.xlsm`, "prix de revient"), CAD (`.dwg`), other Excel files: not
  indexed; browse with `list_folder`, open with `open_document`. Cost prices and margins are not read.
- The **final negotiated price** of a won offer and its follow-up (orders, FAT, SAT) -> Projects
  world, the project's `0_Vente`. Offers filed in project folders are not read here: a won offer's
  prices stop at its last revision in the sales folder.
- Contact lists and mailing labels (`02_Liste adresse client`): personal data, not indexed.
- Product claims, templates (standard 3-page offers) -> Marketing world.

## Pitfalls
- **Scope differs between offers**: "Paloma 3R" may include grippers, vision, conveyors, installation
  or not. Show the line text with each price; never compare totals of different scopes as the same.
- **Budget vs firm**: "Richtpreis", "budget", "budgétaire", "price indication", "Preisangabe" offers, and offers that call
  themselves budgetary or non-binding in their validity section, are budget prices. Charts use the
  offers of the client and archive folders; budget offers are included and marked (`offer_type`
  column: mark them on the chart); filter `offer_type="firm"` to exclude them. Partner offers (prices
  may be to the partner) are counted in the coverage line and added with `category=`.
- **Variants and revisions**: variants (two totals for one revision, e.g. per supplier) are separate
  points, never summed; translations of an offer are one offer. "Last revision" is the default.
- **Status**: "no_order_found" is not "lost" (the project may not be indexed, or quote another
  revision); "open" = no order found and younger than 2 years; `won` names the project.
  `won_unconfirmed`: the number is only in the text of a project of another client and machine (a
  comparison?): show it apart from won and check its evidence in `offer_card`.
- **Currencies**: only CHF is binding in the offers read; an offer without the chosen currency is
  left out and named. Never convert, not even with a rate printed in the offer.
- Old archive offers are scanned: OCR text ("(OCR)"), check amounts with `view_page`.
- The register is machine-read: confidence `low` or `medium` means check the page; the user's
  corrections (`offer_card` shows them) beat the documents.
- The offer number is the key (`3001234`, `B-250306`, `1106033-A`); client names are written
  differently over the years: search `client=` by part of the name.

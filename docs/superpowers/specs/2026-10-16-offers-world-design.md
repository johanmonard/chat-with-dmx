# Offers world: every offer sent to clients, with a register of priced lines

Date: 2026-10-16. Status: approved by the user on 2026-10-16.

## Goal

A fourth world, **Offers**, next to Projects, Marketing and Documentation, built from the sales
folder `O:\SAL\Clients` (= `\\DMX-FS01.rotzingerag.local\Daten$\RMA_ORG\SAL\Clients`). It holds
every offer Demaurex sent, won or not, with all its revisions, from the 1999 archive to today.

The questions it must answer (the user's examples):

- "Give me a chart of Paloma 3R prices over the years."
- "What is the history of the offers submitted to client C?"
- "Chart the number of offers per year, per country, etc."

These are **counting and comparing** questions over every offer, not "find the passage"
questions. Search returns the 10-20 best excerpts and would silently miss offers, so a chart
built from search would be wrong without saying so. The world therefore has two parts:

1. **A search index** like the other worlds (offers, specifications, mails, visit reports...).
2. **An offer register**: one row per *priced line* of every offer revision, read from the offer
   documents, with its source page. Charts and histories are built from the register.

Success: the three example questions are answered in Claude Desktop with a chart or a table, the
coverage is stated ("412 offers with a Paloma 3R line; 371 in CHF; 41 left out: listed below"),
and every point can be traced to a file and page. A spot check of 30 random offers by the user
finds the register's lines, amounts and currencies matching the documents.

### Why a separate world

Offers carry prices and lost deals: they are for **sales and management only**. Isolation must
be real (the MCP server runs on each user's PC against a local copy of the index, so whoever has
the file can read all of it), hence a separate world with its own folder and an AD-group
permission on it, as foreseen in the worlds design (2026-10-09).

### Goals, non-goals, anti-goals

Goals:
- Search over all offer documents, filtered by client, country, offer, year, machine, kind.
- A register precise down to each priced line (each machine, cell, option, service, discount
  is quoted separately in an offer, and the user wants that detail), in every currency printed.
- Won / not won per offer, linked to the project when won.
- Charts and histories that always state what they cover and what they leave out.

Non-goals (this spec):
- Cost prices and margins. The costing sheets (`.xls`, QPC `.xlsm`, "prix de revient") are not
  read. The user asked for the detail of the quoted prices.
- Replacing a CRM, forecasting, writing new offers.
- Currency conversion at outside rates.
- Indexing Excel and CAD files (as in the other worlds: browsable with `list_folder`, opened
  with `open_document`).

Anti-goals (would make it a failure even if it works):
- A chart that silently misses offers or silently converts currencies.
- A price shown without its source document and page.
- Comparing a "Paloma 3R" line that includes grippers, vision and conveyors with one that does
  not as if they were the same thing, without showing the line text.
- Anything from the offers world reaching people outside sales and management (through the
  projects world, the shared knowledge folder, the maps or the skill). The projects world keeps
  what it indexes today, including the won projects' offers and final prices
  (`0_Vente\00_Offres`, `01_Prix_final`): the user's decision, nothing moves.

## What is in `O:\SAL\Clients`

Inventory of 2026-10-16. These are lower bounds: 632 folders of the client part could not be
read, and the share became unreachable at the end of the walk, so the builder should redo it.

- **At least 79,116 files, 249 GB.** By count: `.jpg` 10,811, `.pdf` 10,535, `.msg` 9,115,
  `.doc` 8,087, `.xls` 6,945, `.dwg` 6,799, `.docx` 3,183. Most of the volume is video, CAD and
  zip files, none of which are indexed.
- **Archive** `01_Historique`: 35,846 files, of which 13,802 are of the indexed types.
- **Client folders**: 296 clients have files (the other ~620 have only the empty template or
  nothing), 816 offer folders, 42,011 files, of which 16,987 are of the indexed types.
- **Special folders**: 1,259 files, of which 589 are of the indexed types.
- So roughly **31,000 documents to index**, about 9,000 of them mails. That is about a third of the
  projects world's 106,743 documents.

- **~920 top-level folders**, one per client (`ACME Foods`, `Globex`...), sometimes with sites
  below (`ACME Foods\ACME Polska`).
- **Per client**: `Offres\<offer folder>\...` and `Suivi\` (`Followup.xls`, `QuotationList.xls`).
- **Offer folder names**: a number, optionally ` - description` or ` (CODE NAME)`:
  `3002113 - Line 5 (small)`, `3001227 (ATLAS)`, `1106033-A`, `0909009`,
  `518528328917 - CPL03 - ...` (IPSO). Templates: `NNNNNNN-DESCRIPTION`, `YYMMNNN`.
- **Revisions inside an offer**: newer offers `00`, `01`, `02`... (sometimes
  `04 - Vandelay version`, `10 - offre formelle Rotzinger`), or `Q01`; older offers
  `V1`, `V2`... next to `History\` (`History.xls`: date, author, action) and `Specification\`.
- **Per revision**: the offer itself (`ACME Foods B-1110021 V1.pdf/.doc`,
  `Angebot - 3002113_02.pdf/.docx`, sometimes a separate `Solution_Technique_...`), the costing
  sheet, `Layout(s)\` (`.dwg`, `.pdf`), `Mail(s)\` (`.msg`), visit reports, product photos.
- **Special top folders**:
  - `01_Historique\PROSPECT\<country>\<client>\<offer>`: the archive, 42 country folders,
    ~530 offers numbered `B-`, `F-` or `E-` + six digits (`B-250306 V1`), with their own
    subfolders (`Cahier des charges`, proformas...); already ~34,000 files. Included (the
    long price history is part of the point).
  - `01_Historique\3_Modèles`: old templates.
  - `02_Liste adresse client`: contact lists and mailing labels (personal data).
  - `03_Accord de confidentialité` (NDAs), `04_Infos pratiques` (sales conditions Swissmem,
    insurance, bank, commercial register, logo), `09_MarketStudy\<country>`.
  - `06_Offres IPSO`, `07_Offres_Wyzo\<client>`: offers made through partners.
  - `08_Offres client inconnu`: offers without a named client.
  - `- Client modèle`: the folder template.
- **File types** (sample of the archive): `.doc`, `.jpg`, `.msg`, `.xls`, `.pdf`, `.dwg`,
  `.dxf`, `.zip`, `.tif`, `.wmv`, `.pptx`...
- **Structured lists are almost empty**: `Suivi\QuotationList.xls` has the right columns
  (country, city, description, date, machine, offer #, final customer, author, status), but only
  17 of 379 are filled, 148 rows, nearly all from 2009. The register must be read from the
  offer documents themselves; these rows are a secondary source.
- **Prices**: on the offer's price page, one amount per line, CHF first with the other currency
  beside it, a net total, often the exchange rate and its date. Examples (fictional client and
  amounts, real layout):
  - `ACME Foods\Offres\3002113 - Line 5 (small)\02\Angebot - 3002113_02.pdf` p.4-5:
    "2x Paloma 3R einschliesslich jeweils: ... CHF 410 000.- (EUR 400 000.-)", other lines,
    "PREIS, netto, ohne Steuern, CHF 461 000.- (EUR 450 000.-)", "Nur Preise in Schweizer
    Franken (CHF) sind gültig".
  - `Globex\Offres\1110021\V1\Globex B-1110021 V1.pdf` p.4: "PRICE, net, without taxes,
    CHF", "Only prices in Swiss Francs are valid (CHF)... actual rate (<date>)
    1 Euro = <rate> CHF".

## The world

```toml
[worlds.offers]
title = "Offers"
profile = "offers"
extensions = ['.pdf', '.docx', '.doc', '.pptx', '.ppt', '.msg']
vision = false
roots = ['\\DMX-FS01.rotzingerag.local\Daten$\RMA_ORG\SAL\Clients']
exclude = ['~$*', '.*', '$RECYCLE.BIN', 'Thumbs.db', '- Client modèle', 'NNNNNNN-DESCRIPTION',
           'YYMMNNN', '3_Modèles', '02_Liste adresse client']
```

- Server `dmx-offers`, local copy `C:\dmx-rag\data\offers\`, master `worlds\offers\` on the
  share, launcher 7 as for the other worlds (index -> ocr -> **offers** -> embed), run on
  DMX-WS032 (A4000: embeddings, and Ollama for the register step). WS032 also needs a pulled
  copy of the projects world for the won/not-won links. The laptops only pull the result.
- `02_Liste adresse client` is excluded: contact lists are personal data, not offers.
- Picture descriptions are off (`vision = false`) to begin with; picture files are not indexed.
  The local model is still used by the register step (below).
- Old `.doc` / `.ppt` go through the existing conversion; scanned PDFs through OCR.

### Outlook mails (`.msg`)

New extractor (one more file type for every world that lists it): subject, from, to, date and
body as one document of one page, header lines first (`Subject:`, `From:`, `Date:`), then the
body. Attachments are listed by name only (not extracted): they are usually also saved next to
the mail. Mails hold the client's real requirements and the negotiation, so they are searchable,
but they are **not** a source for the register (their prices are quotes of quotes).

## Profile `offers`: facets from the path

Computed from the path below the root, stored in `doc_facets` (new columns as needed).

| Facet | Rule | Examples |
|---|---|---|
| `category` | `client` for ordinary client folders; otherwise from the top folder: `archive` (01_Historique), `partner` (06_, 07_), `unknown_client` (08_), `market_study` (09_), `nda` (03_), `sales_info` (04_) | |
| `client` | the client folder; in the archive the folder under the country; in `07_Offres_Wyzo` the folder under it; in `06_Offres IPSO` the partner name; in `08_` none | `ACME Foods`, `Globex` |
| `site` | a folder between the client and `Offres` | `ACME Polska` |
| `country` | archive: the country folder (normalised to English); otherwise from the register's client/country, the offer's address page (register step) or `QuotationList` | `Germany` |
| `partner` | `IPSO`, `Wyzo` | |
| `offer` | the offer key (below) of the first offer folder in the path | `3002113`, `B-250306` |
| `revision` | the folder under the offer folder when it is a revision (`00`, `01`, `V1`, `Q01`, `04 - ...`), else from the file name suffix (`_02`, ` V1`) | `02` |
| `doc_kind` | `offer`, `technical_solution`, `specification`, `mail`, `layout`, `visit_report`, `history`, `proforma`, `market_study`, `nda`, `sales_info`, `other`: from the folder (`Specification`, `Spécifications`, `Mail(s)`, `Layout(s)`, `History`, `Cahier des charges`) then the file name (`Angebot`, `Offre`, `Offer`, `Quotation`, `Solution_Technique`, `Besuchsbericht`, `visit report`, `Proforma`) | |
| `machine` | machine families and models in the path and file name, with `machines.models_in` | `Paloma 3R` |

**Offer key**: the number as one canonical string so that every way of writing it matches:
digits of a 7-digit (`0909009`, `3002113`), 12-digit (IPSO) or archive number, the `B-`/`F-`/`E-`
prefix kept for archive numbers (`B-250306`), suffixes like `-A` kept (`1106033-A`), revision
suffixes (` V1`, `_02`, `/01`) removed. `B-1110021 V1` (a newer offer written with the `B-`
prefix) has key `1110021`. The exact rules are left to the builder, tested on the real folder
names.

## The offer register

### What it holds

Four tables in the offers world's index, rebuilt by the `offers` step:

- **`offers`**: one row per offer key: client, site, country, partner, category, title (from the
  folder description), folder, first and last revision date, author (when known), **status**,
  linked project, status evidence.
- **`offer_revisions`**: one row per revision: revision label, date, the document read for the
  prices (doc_id, page), totals (one per currency), exchange rates, how it was read, confidence,
  sum check result. Revisions without a readable price page are still listed (no lines).
- **`offer_lines`**: one row per priced line: revision, position, **line text as written**,
  kind (`machine`, `option`, `service`, `discount`, `other`), machine family and model when the
  line is a machine or cell (`Paloma`, `Paloma 3R`), quantity, **amounts in every currency
  printed** (CHF 410,000 and EUR 400,000 = two amounts on one line), unit price per currency
  (amount / quantity, only when the quantity is explicit), page, how it was read, confidence.
- **`offer_rates`**: rates printed in the revision (`1 EUR = 1.08 CHF`, date as written).

### Which document and page

In each revision folder, the offer document is chosen by `doc_kind = offer` and the offer
number in its name; when a `.pdf` and a `.doc(x)` of the same name exist, the PDF is read (same
content, page numbers that match what people see).

A revision folder can hold several priced documents (designer's call, to check in the review):
- **Translations** (`Angebot - 3002113_02.pdf` and `..._02_UK.docx`): same offer number and the
  same totals in every currency -> one priced document; the others are recorded as translations
  and their lines are not counted.
- **Variants** (different totals: "variant A / B", `05 - Wonka version` next to
  `04 - Vandelay version`): each is kept as a variant of the offer, with a `variant`
  label from the folder or file name; variants are never summed. "Last revision" = the highest
  revision label; when it has variants, each variant is one point, marked as such.
- **Copies issued by the group** (`10 - offre formelle Rotzinger`): treated as a revision like
  any other; the issuing company is kept when the document names it.

Each offer also gets an **offer type**: `firm` or `budget` (words "budget", "Richtpreis",
"prix indicatif", "indicative", "estimation" on the first pages or in the name), default `firm`.

The price page is found by its words
("PRICE", "PRIX", "PREIS", "net", "netto", "without taxes", "ohne Steuern", "hors taxes") and
amounts with a currency. The step reads the text already in the index (extracted or OCR), it
does not reopen the files.

### How lines are read

1. **Rules first**: a parser splits the price page into lines (text, then one or more
   `<currency> <amount>` values, amounts like `410 000.-`, `410'000`, `410,000.00`, currency
   before or after, in brackets for the second currency). A line passes when the amounts parse
   and the lines add up to the printed total in each currency (within rounding).
2. **Local model when rules fail**: the page text goes to the local model already used for
   picture descriptions (Ollama, `[vision] url/model`), asked for the same structure as JSON;
   the result passes the same checks. The page never leaves the machine.
3. **Confidence**: `high` = sum check passed; `medium` = parsed but no total to check against,
   or the model was needed and its result passed; `low` = sums do not match, amounts unclear,
   OCR text. Each row says how it was read (`rules`, `model`, `correction`).
4. **Machine model** of a line: `machines.models_in` on the line text, then the word list
   (`knowledge\vocabulary.md` gets a `## machines` section for old names, e.g. how
   "Paloma D2 Top Loader with two Delta robots" maps to a model). Unknown names are kept as
   written and reported, never guessed.
5. **Date** of a revision: the date printed in the offer, else `History.xls`, else the file
   date; the source of the date is kept. The number is not used for dates (the archive's six
   digits are not consistent).

### Status: won, not won, open

- **Won**: the offer key, in any of its written forms (`3003929`, `B-1110021 V1`,
  `F-1103018 V1`), appears in the projects world's documents (projects' own sales folders such
  as `THOR\7_Photos_Videos\70_Dossier_vente\3003929 - Line 2`, offers quoted in project
  documents such as "Offre F-1103018 V1" in an ANGE document). The project is linked and the
  evidence (file, page) kept; the register's client and machine help confirm the match. Read from
  the projects world's **local index** on the machine that runs the step (read-only).
- **No order found**: no project quotes it and the last revision is more than 2 years old
  ("not won" is not claimed: the project may not be indexed or may quote another revision).
- **Open**: younger than 2 years, no order found.
- **Offers filed in project folders are not read as revisions** (decision 2026-10-16: the
  projects world is left as it is). A won offer's prices therefore stop at its last revision in
  `O:\SAL\Clients`; `offer_card` says so and points to the linked project's `0_Vente` for the
  final negotiated price. Adding them is a possible later step.
- **2009 `QuotationList` rows** give a status (Running, Ordered, Cancelled) where they exist.
- **User corrections** override everything (`lost`, `won` + project, `cancelled`...).

### Corrections and review

- Corrections live in `worlds\offers\corrections.csv` on the share (restricted folder),
  mirrored to `C:\dmx-rag\data\offers\` by pull: one row per correction (offer, revision, line
  position or `-`, field, value, note, who, date). They are applied after every rebuild; a
  correction whose target no longer exists is reported, not dropped.
- `dmx-docs --world offers offers --review` writes `review.csv`: low-confidence revisions,
  failed sum checks, unknown machine names, offers with no price page, ambiguous project links.
- Precedence: the user's corrections beat the documents, the documents beat the register (as for
  the machine register and the knowledge cards).

## Tools of `dmx-offers`

The common tools (`search`, `read_document`, `find_in_document`, `view_page`, `export_image`,
`open_document`, `list_folder`, `find_files`, `index_status`, `world_map`), with `search`
filters `client`, `country`, `offer`, `doc_kind`, `category`, `machine`, `year_from`, `year_to`,
`status`. New:

- **`list_offers(client, country, machine, status, year_from, year_to, text)`**: one line per
  offer, newest first: key, client, country, date range, revisions, main machine lines, total of
  the last revision (all currencies), status (+ project). Ends with coverage.
- **`offer_card(offer)`**: everything about one offer: revisions with dates and totals, the lines
  of each revision, rates, status and evidence, the documents (offer, specification, mails,
  layouts), corrections applied.
- **`offer_lines(machine, kind, client, country, status, year_from, year_to, currency='CHF',
  revisions='last')`**: the rows for a price chart, one per line: date, offer, revision, client,
  country, status, quantity, amount and unit price in the chosen currency, line text, source
  page, confidence. `revisions='all'` gives every revision.
- **`offer_stats(group_by, ...same filters)`**: counts of offers (and revisions) grouped by
  `year`, `country`, `client`, `machine`, `status`, `author`, `partner`; one or two keys.

Rules every one of them follows:

- **Coverage line, always**: how many offers matched, how many have readable prices, how many
  were left out and why (no amount in that currency, low confidence when filtered out, no date).
- **Currencies are never converted.** A chart is in one currency (CHF by default: the offers read
  say only CHF is binding); an offer without that currency is left out and named.
- **Default: last revision of each offer** for trends and counts; every revision for the history
  of a client or an offer.
- **Default scope of price trends**: the offers of the client folders and the archive, firm and
  budget. Budget offers are included and marked: each chart row carries an `offer_type` column so
  charts can mark budget points; `offer_type="firm"` excludes them (user's decision, 2026-10-19,
  after the first real run; the designer's first call left them out). Partner offers
  (`06_Offres IPSO`, `07_Offres_Wyzo`, whose prices may be to the partner) are left out by default,
  counted in the coverage line, and included with `category=`. Counts (`offer_stats`) include every
  offer and can be grouped by `offer_type` and `category`.
- Every row carries its source file and page, so Claude can cite each point and the user can
  open it.

Charts are drawn by Claude in Claude Desktop from these rows; the server returns data, not
pictures. For a price chart, won and not-won offers are distinguished (e.g. filled and hollow
points) and the line text is available for each point, so different scopes are visible.

## Map, skill and the projects world

- `maps\offers.md`: what is here, where, kinds of information, what is missing, pitfalls (scope
  differences, revisions, currencies, old offers read by OCR).
- `skills\dmx-research\references\offers.md` + routing in `SKILL.md`: for price trends, client
  histories and counts use `offer_lines` / `list_offers` / `offer_stats`, then verify in the
  documents; for one offer `offer_card`. These files describe the world, they contain no offer
  data, so the skill stays shareable.
- `maps\projects.md` "Not in this world": "All revisions of the offers, offers not won and the
  sales history per client -> Offers world (sales and management only)". The projects world
  itself gets no data from the offers world.

## Access

- `worlds\offers\` gets an NTFS permission for one AD group (sales and management). The shared
  folder `U:\DMX-RAG` is today on the user's personal share, so the offers world needs a team
  share (or at least its `worlds\offers` folder on one) before anyone else uses it; until then it
  is used by the user only.
- Only machines of people in that group pull `offers` (`dmx.ps1 pull -World offers` fails with
  "no access" for others, without touching other worlds).
- The machine that builds the world (WS032) runs under an account in the group.
- No offer data in `knowledge\`, the maps, the skill, tests or logs shared with everyone. Tests
  use fictional clients (ACME...), as everywhere.

## Failures

- A revision with no readable price page: listed in `offer_revisions` with no lines and the
  reason; counted in the coverage line.
- Ollama not running: the rules-only lines are kept, the others marked `not read (model
  unavailable)`; the next run retries them.
- Projects world index missing on the machine: status stays `unknown` with that reason.
- An offer folder that does not match any known pattern: offer key `unknown`, listed in
  `review.csv`.
- A corrupt or password-protected file: recorded as an error for that file, the run continues.

## Testing

With fictional names and small generated documents:

- Facets: one test per folder pattern (client, site, archive, partner, unknown client,
  templates excluded), offer key normalisation, revision detection.
- `.msg` extraction.
- Price page detection and the line parser: CHF/EUR/USD, both orders, brackets, `410 000.-`,
  `410'000`, `1,230,000.00`, discount lines, missing total, sum check.
- Model fallback with the fake Ollama already used by the tests.
- Status linking against a small projects index; corrections applied, stale correction
  reported.
- Tools: coverage line, currency filter, last-revision default, `offer_stats` grouping.
- Real run: index the world on WS032, then the user's spot check of 30 random offers and the
  three example questions in Claude Desktop.

## Left to the builder

- Exact offer-key and revision rules, from a fresh walk of the folder.
- Table and column names, how the register step is split into functions.
- Prompt and JSON schema for the local model.
- The 2-year "open" threshold (to confirm with the user if sales cycles are longer).

# Offers World Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A fourth world `offers` over `O:\SAL\Clients`: search over offer documents and mails, plus an offer register (one row per priced line of every offer revision, every currency printed, won / no order found / open) served by four tools that feed charts.

**Architecture:** A new profile `offers` derives client / site / country / partner / offer / revision / document kind from the path (`offerkeys.py` + `facets.compute_offers`). `.msg` mails become one-page documents. A new step `dmx-docs offers` (after index and OCR, before embed) rebuilds the register tables inside the world's index from the indexed page text: `prices.py` parses price sections with rules, the local Ollama model reads the pages the rules cannot, `offers.py` chooses documents, dates, countries, statuses (from the projects world's local index) and applies the user's corrections. `offer_tools.py` formats `list_offers`, `offer_card`, `offer_lines`, `offer_stats`; `server.py` registers them for the offers profile.

**Tech Stack:** Python 3.11, SQLite (existing index), MCP Python SDK, pytest, PowerShell 5.1 (`dmx.ps1`); new dependencies `olefile` (BSD, `.msg`) and `xlrd>=2.0` (BSD, `History.xls` / `QuotationList.xls`).

**Spec:** `docs/superpowers/specs/2026-10-16-offers-world-design.md`

## Global Constraints

- World `offers`, profile `offers`, title `Offers`, server name `dmx-offers` (from the existing `Config.server_name` rule).
- Root: `\\DMX-FS01.rotzingerag.local\Daten$\RMA_ORG\SAL\Clients`. Extensions `['.pdf', '.docx', '.doc', '.pptx', '.ppt', '.msg']`, `vision = false`. Exclude patterns: `'~$*', '.*', '$RECYCLE.BIN', 'Thumbs.db', '- Client modèle', 'NNNNNNN-DESCRIPTION', 'YYMMNNN', '3_Modèles', '02_Liste adresse client'`.
- `vision = false` turns off picture descriptions only; the register step still uses `vision_url` / `vision_model` for price pages.
- Currencies are never converted. Amounts are stored per currency exactly as printed (`{"CHF": 410000.0, "EUR": 400000.0}`).
- Charts default to currency `CHF`, the last revision of each offer, `offer_type = firm`, categories `client,archive`. Everything left out is counted and named in the coverage line.
- Precedence: user corrections > documents > register sources (`QuotationList`, `History.xls`).
- Status values: `won`, `no_order_found`, `open`, `unknown`, plus corrected values `lost`, `cancelled`. `open` = no order found and last revision younger than `OPEN_YEARS = 2` years.
- Offers in project folders are not read as revisions: a won offer's `offer_card` says "Final negotiated price: see the project's 0_Vente in the projects world."
- Nothing from the offers world is written to the projects world, the shared `knowledge\`, maps or skill. Real client names never appear in tests, docstrings or examples (use ACME, Globex, Polder, Dupont Biscuits...).
- Tool output in English. Every row printed by a tool carries its source `path` and `page`.
- Keep each file's existing line endings; edit existing files with the Edit tool, never through Python or shell heredocs (they flip CRLF/LF and eat backslashes).
- New tools are read-only (`**kw` with the `READ_ONLY` annotations, like the others) and registered only for `profile == "offers"`.
- Coverage line format (every tool that lists offers or lines): `Coverage: {n} offers matched; {m} with readable prices; left out: {k} ({reasons})` where reasons are `count reason` pairs joined by `, ` (e.g. `12 no CHF amount, 3 low confidence, 2 no date`), and then one line `Left out: <offer> (<reason>), ...` listing at most 30 offers.

## Review Focus

- A price section whose extraction order puts the second currency first and each amount on its own line (`(EUR 400 000.-)` then `CHF 410 200.-`, as in real German offers) must attach both amounts to the label above them - test in Task 3.
- Swiss and European number formats (`410 200.-`, `410'200.-`, `1’230’000`, `1.230.000,00`, `1,230,000.00`, `-9 000.-`) must parse to the same floats - test in Task 3.
- Day/hour rates and "on request" amounts (`CHF 1 100.-/Tag`, `1100 CHF/Day`, `par jour`) must never become lines nor break the sum check - test in Task 3.
- Archive numbers (`B-250306`) look like dates: status linking must require the letter prefix in the project text, never the bare six digits - test in Task 6.
- The same offer key found in two client folders (client renamed, subsidiary) must not merge two clients' prices silently: the first folder is kept, the duplicate is listed in `review.csv` - test in Task 5.

---

## File Structure

- Create `src/dmx_docs/offerkeys.py` - offer key, revision label/order, special top folders, country names.
- Modify `src/dmx_docs/facets.py` - `compute_offers`, new `doc_facets` columns.
- Modify `src/dmx_docs/config.py` - profile `offers`, `corrections_path`.
- Modify `src/dmx_docs/extract.py` - `.msg` extraction.
- Create `src/dmx_docs/prices.py` - price section parsing (rules), dates, offer type.
- Modify `src/dmx_docs/vision.py` - `chat_json` (text-only JSON call to Ollama).
- Create `src/dmx_docs/offers.py` - register tables, `run_offers`, linking, corrections, review.
- Create `src/dmx_docs/offer_tools.py` - the four tools' queries and text output.
- Modify `src/dmx_docs/search.py` - plain facet columns for non-projects profiles, offer-register filters.
- Modify `src/dmx_docs/tools.py`, `src/dmx_docs/server.py`, `src/dmx_docs/cli.py`.
- Modify `scripts/dmx.ps1` - `offers` step, corrections/review copies, access message.
- Create `maps/offers.md`, `skills/dmx-research/references/offers.md`; modify `skills/dmx-research/SKILL.md`, `maps/projects.md`, `README.md`, `config.example.toml`, `pyproject.toml`.
- Tests: create `tests/test_offer_facets.py`, `tests/test_msg.py`, `tests/test_prices.py`, `tests/test_offers.py`, `tests/test_offer_tools.py`; modify `tests/test_dmx_ps1.py`, `tests/test_skill.py` (only if it needs the new world listed).

---

### Task 1: Profile `offers` and path facets

**Files:**
- Create: `src/dmx_docs/offerkeys.py`
- Modify: `src/dmx_docs/config.py` (`PROFILES`, `corrections_path`), `src/dmx_docs/facets.py` (`compute_offers`, `RULES`, `PROFILE_VERSIONS`, `TABLE`/`COLUMNS`, `refresh` column migration), `config.example.toml`
- Test: `tests/test_offer_facets.py`

**Interfaces:**
- Produces:
  - `offerkeys.offer_key(name: str) -> str | None` - canonical key of an offer folder or file name.
  - `offerkeys.revision_of(name: str) -> tuple[str, str | None] | None` - `(label, variant)` of a revision folder name, `None` if the name is not a revision.
  - `offerkeys.revision_order(label: str) -> tuple[int, int, str]` - sort key (later revision = larger).
  - `offerkeys.SPECIAL = {"01_historique": "archive", "03_accord de confidentialite": "nda", "04_infos pratiques": "sales_info", "06_offres ipso": "partner", "07_offres_wyzo": "partner", "08_offres client inconnu": "unknown_client", "09_marketstudy": "market_study"}` (keys accent- and case-folded with `register.fold`).
  - `offerkeys.country_en(name: str) -> str | None` - French/German/English country folder or word -> English name (`"Allemagne"`, `"Deutschland"`, `"Germany"` -> `"Germany"`; `"UK"` -> `"United Kingdom"`; `"TURQUIE"` -> `"Turkey"`). A table of the ~60 countries Demaurex sells to, with FR/DE/EN names and common abbreviations.
  - `facets.compute_offers(path: str, root: str) -> dict` with keys `category`, `client`, `site`, `country`, `partner`, `offer`, `revision`, `doc_type`, `machine`, `facet_source`.
  - `doc_facets` gains columns `client, site, country, partner, offer, revision, machine` (TEXT). `machine` is stored as `|Paloma|Paloma 3R|` (families and models from `machines.models_in`, pipes around each) so a filter is `machine LIKE '%|<value>|%'`.
  - `Config.corrections_path: str | None` - `data_dir\<world>\corrections.csv` when the profile is `offers`, else `None`; `[worlds.offers] corrections = <path>` overrides.

- [ ] **Step 1: Write the failing tests**

```python
R = r"\\srv\Daten$\RMA_ORG\SAL\Clients"
def f(rel): return facets.compute_offers(os.path.join(R, rel), R)

@pytest.mark.parametrize("name, key", [
    ("3002113 - Line 5 Lyon (small)", "3002113"), ("3001227 (ATLAS)", "3001227"), ("0909009", "0909009"),
    ("1106033-A", "1106033-A"), ("518528328917 - CPL03 - Intervention", "518528328917"),
    ("B-250306 V1", "B-250306"), ("F-1103018 V1", "1103018"), ("ACME Foods B-1110021 V1.pdf", "1110021"),
    ("Angebot - 3002113_02.pdf", "3002113"), ("Offre_5034569_01.pdf", "5034569"),
    ("NNNNNNN-DESCRIPTION", None), ("YYMMNNN", None), ("Specification", None), ("Paloma 2R WD _JLT", None),
])
def test_offer_key(name, key):
    assert offerkeys.offer_key(name) == key

@pytest.mark.parametrize("name, rev", [
    ("00", ("00", None)), ("02", ("02", None)), ("V3", ("V3", None)), ("Q01", ("Q01", None)),
    ("04 - Vandelay version", ("04", "Vandelay version")), ("10 - offre formelle Rotzinger", ("10", "offre formelle Rotzinger")),
    ("History", None), ("Specification", None), ("Layout", None), ("Mails", None),
])
def test_revision_of(name, rev):
    assert offerkeys.revision_of(name) == rev

def test_revision_order():
    labels = ["V2", "00", "10", "V1", "02"]
    assert sorted(labels, key=offerkeys.revision_order)[-1] in ("10", "V2")  # V-labels and numbered labels never mix in one offer
    assert sorted(["00", "10", "02"], key=offerkeys.revision_order) == ["00", "02", "10"]
    assert sorted(["V2", "V10", "V1"], key=offerkeys.revision_order) == ["V1", "V2", "V10"]

def test_client_offer_revision():
    got = f(r"ACME Foods\Offres\3002113 - Line 5\02\Angebot - 3002113_02.pdf")
    assert (got["category"], got["client"], got["site"], got["offer"], got["revision"], got["doc_type"]) == \
           ("client", "ACME Foods", None, "3002113", "02", "offer")

def test_site_and_kinds():
    assert f(r"ACME Foods\ACME Polska\Offres\3006464\00\x.pdf")["site"] == "ACME Polska"
    assert f(r"ACME Foods\Offres\3002113\02\Mail\FW Projekt.msg")["doc_type"] == "mail"
    assert f(r"ACME Foods\Offres\0904023\Specification\Cahier.pdf")["doc_type"] == "specification"
    assert f(r"ACME Foods\Offres\0904023\V1\ACME - Solution_Technique_B-0904023 V1.pdf")["doc_type"] == "technical_solution"
    assert f(r"ACME Foods\Offres\3002113\02\Besuchsbericht ACME.docx")["doc_type"] == "visit_report"
    assert f(r"ACME Foods\Offres\3002113\02\Layout\General view.pdf")["doc_type"] == "layout"

def test_archive():
    got = f(r"01_Historique\PROSPECT\Allemagne\Dupont Biscuits\B-250306 V1\Dupont B-250306 V1.doc")
    assert (got["category"], got["country"], got["client"], got["offer"], got["revision"]) == \
           ("archive", "Germany", "Dupont Biscuits", "B-250306", "V1")

def test_partners_and_unknown_client():
    got = f(r"07_Offres_Wyzo\Globex\Offres\3001264 - WYZO\00\Offer.pdf")
    assert (got["category"], got["partner"], got["client"], got["offer"]) == ("partner", "Wyzo", "Globex", "3001264")
    got = f(r"06_Offres IPSO\3006904 - CPL01 - MES\Q01\3006904-CPL01-Q01_.pdf")
    assert (got["category"], got["partner"], got["client"], got["offer"], got["revision"]) == \
           ("partner", "IPSO", None, "3006904", "Q01")
    got = f(r"08_Offres client inconnu\Paloma 2R WD _JLT\00\PALOMA 2R WD_01_Layout & Price.pdf")
    assert (got["category"], got["client"], got["machine"]) == ("unknown_client", None, "|Paloma|Paloma 2R|")

def test_other_categories():
    assert f(r"09_MarketStudy\PL\Study.pdf")["category"] == "market_study"
    assert f(r"03_Accord de confidentialité\ACME NDA.pdf")["category"] == "nda"
    assert f(r"04_Infos pratiques\Conditions de vente Swissmem\CG.pdf")["category"] == "sales_info"
```

Plus one index-level test: index a tiny tree under `tmp_path / "Clients"` with `make_cfg(root, profile="offers", world="offers", extensions=[".pdf"])`, run `facets.refresh(con, cfg.roots, "offers")`, and assert a row of `doc_facets` has `client = 'ACME Foods'`, `offer = '3002113'`; and that a `facets_version` change from `"marketing:1"` to `"offers:1"` recomputes the rows.

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_offer_facets.py -v`
Expected: FAIL (`ModuleNotFoundError: dmx_docs.offerkeys`)

- [ ] **Step 3: Implement `offerkeys.py`, `facets.compute_offers` and the config changes**

Key rules (from the real folder names in the spec): strip the extension; archive form `^[BFE]-(\d{6})(?!\d)` -> `"<letter>-<6 digits>"`; any other `(?<!\d)(\d{7}|\d{12})(?!\d)` -> those digits, plus `-<letter>` when directly followed by `-[A-Z](?![a-z])` (`1106033-A`); a `B-`/`F-` prefix before 7 digits is dropped. Revision: `^(\d{2})(?:\s*-\s*(.+))?$`, `^(V\d+)$`, `^(Q\d{2})$` (case-insensitive, label upper-cased). `doc_type` order: folder (`specification`, `spécifications`, `cahier des charges` -> `specification`; `mail`, `mails` -> `mail`; `layout`, `layouts` -> `layout`; `history` -> `history`) then file name words (`angebot`, `offre`, `offer`, `quotation`, `devis` -> `offer`; `solution_technique`, `solution technique` -> `technical_solution`; `besuchsbericht`, `visit report`, `rapport de visite` -> `visit_report`; `proforma` -> `proforma`), then `.msg` -> `mail`, then for a file directly in a revision folder whose name contains the offer key -> `offer`, else `other`. `facet_source` is `folder` or `name` as in the projects rules. Partner names: `06_Offres IPSO` -> `IPSO`, `07_Offres_Wyzo` -> `Wyzo`. Add `"offers"` to `PROFILES`; `RULES["offers"] = compute_offers`; `PROFILE_VERSIONS["offers"] = 1`; add the seven columns to `TABLE`, `COLUMNS` and the `ALTER TABLE` migration list in `refresh`. Add a commented `[worlds.offers]` block to `config.example.toml` with the values in Global Constraints.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_offer_facets.py tests/test_facets.py tests/test_worlds.py -v`
Expected: PASS (existing facet and world tests unchanged)

- [ ] **Step 5: Commit**

```bash
git add src/dmx_docs/offerkeys.py src/dmx_docs/facets.py src/dmx_docs/config.py config.example.toml tests/test_offer_facets.py
git commit -m "Offers world: profile and path facets (client, offer, revision, kind, archive, partners)"
```

---

### Task 2: Outlook mails (`.msg`)

**Files:**
- Modify: `src/dmx_docs/extract.py` (`extract_file` dispatch, new functions), `pyproject.toml` (`olefile>=0.47`)
- Test: `tests/test_msg.py`

**Interfaces:**
- Produces:
  - `extract.msg_fields(streams: dict[str, bytes]) -> dict` - from the OLE streams of a `.msg` (stream name -> bytes): `{"subject", "sender", "to", "date", "body", "attachments": [names]}`; missing fields are `""` / `[]`.
  - `extract.extract_msg(path: str) -> Extracted` - one page: `Subject: …\nFrom: …\nTo: …\nDate: YYYY-MM-DD HH:MM\nAttachments: a.pdf, b.xlsx\n\n<body>` through `clean_text`; `title` = subject; empty body and subject -> `status="empty"`.

- [ ] **Step 1: Write the failing tests**

Build the stream dict by hand (no Outlook needed):

```python
def u(s): return s.encode("utf-16-le")
def systime(dt):  # FILETIME: 100 ns since 1601-01-01
    return int((dt - datetime(1601, 1, 1)).total_seconds() * 10**7).to_bytes(8, "little")

def props(*entries):  # top-level __properties_version1.0: 32-byte header, then 16-byte entries
    out = bytes(32)
    for tag, value in entries:
        out += tag.to_bytes(4, "little") + (6).to_bytes(4, "little") + value
    return out

STREAMS = {
    "__substg1.0_0037001F": u("RE: Offer 3001234 - Line 5"),
    "__substg1.0_0C1A001F": u("Jane Doe"),
    "__substg1.0_0E04001F": u("sales@acme.example"),
    "__substg1.0_1000001F": u("Dear John,\r\nplease quote two Paloma 3R.\r\n"),
    "__properties_version1.0": props((0x00390040, systime(datetime(2021, 9, 15, 14, 30)))),
    "__attach_version1.0_#00000000/__substg1.0_3707001F": u("Layout_3001234.pdf"),
}

def test_msg_fields():
    m = extract.msg_fields(STREAMS)
    assert m["subject"] == "RE: Offer 3001234 - Line 5" and m["sender"] == "Jane Doe"
    assert m["date"] == "2021-09-15 14:30" and "two Paloma 3R" in m["body"]
    assert m["attachments"] == ["Layout_3001234.pdf"]

def test_msg_fields_ansi_and_missing():
    m = extract.msg_fields({"__substg1.0_0037001E": "Offre ACME".encode("cp1252"),
                            "__substg1.0_1000001E": "Prix demandé".encode("cp1252")})
    assert (m["subject"], m["body"], m["date"], m["attachments"]) == ("Offre ACME", "Prix demandé", "", [])
```

Plus `test_extract_msg_dispatch`: monkeypatch `extract._read_msg_streams` (the olefile wrapper, see Step 3) to return `STREAMS`, write a non-empty dummy `x.msg`, and assert `extract_file(path)` gives `status == "ok"`, one page starting with `"Subject: RE: Offer 3001234 - Line 5\nFrom: Jane Doe"`; and that `_read_msg_streams` raising `OSError` gives `status == "error"`.

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_msg.py -v`
Expected: FAIL (`AttributeError: module 'dmx_docs.extract' has no attribute 'msg_fields'`)

- [ ] **Step 3: Implement `msg_fields`, `_read_msg_streams(path) -> dict[str, bytes]` (olefile: every stream of the top level and of `__attach_version1.0_#*` storages, names joined with `/`) and `extract_msg`; add `.msg` to `extract_file`; add `olefile>=0.47` to `pyproject.toml`**

Property tags: subject `0037`, sender name `0C1A`, display to `0E04`, body `1000`, attachment long file name `3707` (fallback `3704`); suffix `001F` = UTF-16-LE, `001E` = ANSI (cp1252). Date = `PR_CLIENT_SUBMIT_TIME 0x0039` (type `0040`) from `__properties_version1.0`, else `PR_MESSAGE_DELIVERY_TIME 0x0E06`. Embedded messages (`__substg1.0_3701000D`) are not read.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_msg.py tests/test_extract.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dmx_docs/extract.py pyproject.toml tests/test_msg.py
git commit -m "Extract Outlook .msg mails (subject, sender, date, body, attachment names) with olefile"
```

---

### Task 3: Price sections by rules

**Files:**
- Create: `src/dmx_docs/prices.py`
- Test: `tests/test_prices.py`

**Interfaces:**
- Produces:
  - `@dataclass PriceLine: position: int, text: str, kind: str, quantity: int | None, amounts: dict[str, float], page: int, family: str | None = None, model: str | None = None`
  - `@dataclass Rate: base: str, quote: str, rate: float, date_written: str | None`
  - `@dataclass PriceSection: lines: list[PriceLine], totals: dict[str, float], rates: list[Rate], pages: list[int], sum_ok: bool | None` (`None` = no total printed)
  - `parse_number(s: str) -> float | None`
  - `amounts_in(text: str) -> list[tuple[str, float]]` - `(currency, value)` in text order; skips rates (`/Tag`, `/day`, `/jour`, `per day`, `par jour`, `pro Tag`, `/h`, `/hour`, `/Stunde`, `/heure`).
  - `price_pages(pages: dict[int, str]) -> list[int]` - consecutive pages (at most 3) from the first page with a price word (`price`, `prix`, `preis`, `total`, `montant`, `betrag`) and at least 2 amounts, through the page holding the net total.
  - `parse_section(pages: dict[int, str]) -> PriceSection | None`
  - `classify(text: str, amounts: dict[str, float]) -> str` - `machine | option | service | discount | other`.
  - `offer_date(pages: dict[int, str]) -> tuple[str, str] | None` - `(YYYY-MM-DD, as written)` of the first date on pages 1-2 that is not inside a rate sentence; formats `23.06.2025`, `23/06/2025`, `2025-06-23`, `15 mars 2012`, `1. November 2011`, `March 12th, 2019`, `1er novembre 2011` (FR/DE/EN month names).
  - `offer_type(pages: dict[int, str], name: str) -> str` - `budget` when pages 1-2 or the name contain `budget`, `budgetary`, `richtpreis`, `prix indicatif`, `indicative`, `estimation`; else `firm`.
  - Currency codes: `CHF`, `EUR`, `USD`, `GBP`; `€` -> `EUR`, `$`/`US$` -> `USD`, `£` -> `GBP`, `Fr.`/`SFr.` -> `CHF`.

- [ ] **Step 1: Write the failing tests**

```python
DE = {4: "PREIS\n2x Paloma 3R einschliesslich jeweils:\n- 3 Delta Roboter\n(EUR 400 000.-)\nCHF 410 000.-\n"
         "Installation und Inbetriebnahme\n(EUR 50 000.-)\nCHF 51 000.-\n"
         "Zusätzliche Tage sind auf Anfrage zum Preis von CHF 1 100.-/Tag\n",
      5: "PREIS, netto, ohne Steuern,\n(EUR 450 000.-)\nCHF 461 000.-\n"
         "Nur Preise in Schweizer Franken (CHF) sind gültig. Richtwert ist der tatsächliche Tageskurs ist 1.00 EUR = 1,0650 CHF"}
EN = {4: "3. PRICE\nA Paloma 2R Top Loader cell including: robots, frame, HMI CHF 180,000.00\n"
         "Option: vision system CHF 25,000.00\nTraining (2 days) CHF 4,000.00\nDiscount -CHF 9,000.00\n"
         "(Additional days: 1100 CHF/Day)\nPRICE, net, without taxes, CHF 200,000.00\n"
         "Only prices in Swiss Francs are valid (CHF). As indication, actual rate (March 12th, 2019) 1 Euro = 1.08 CHF."}

@pytest.mark.parametrize("s, v", [("410 200.-", 410000.0), ("410'200.-", 410000.0), ("1’230’000", 1230000.0),
    ("1.230.000,00", 1230000.0), ("1,230,000.00", 1230000.0), ("-9 000.-", -9000.0), ("25,000.00", 25000.0),
    ("1 100.-", 1100.0), ("12,5", 12.5), ("abc", None)])
def test_parse_number(s, v):
    assert prices.parse_number(s) == v

def test_rates_are_not_amounts():
    assert prices.amounts_in("Zusätzliche Tage zum Preis von CHF 1 100.-/Tag") == []
    assert prices.amounts_in("(Additional days: 1100 CHF/Day)") == []
    assert prices.amounts_in("(EUR 50 000.-) CHF 51 000.-") == [("EUR", 50000.0), ("CHF", 51000.0)]

def test_german_section_second_currency_first():
    s = prices.parse_section(DE)
    assert s.pages == [4, 5] and s.totals == {"CHF": 461000.0, "EUR": 450000.0} and s.sum_ok is True
    first = s.lines[0]
    assert first.amounts == {"CHF": 410000.0, "EUR": 400000.0} and first.quantity == 2
    assert (first.kind, first.family, first.model) == ("machine", "Paloma", "Paloma 3R")
    assert first.text.startswith("2x Paloma 3R einschliesslich") and "3 Delta Roboter" in first.text
    assert s.lines[1].kind == "service" and len(s.lines) == 2
    assert s.rates == [prices.Rate("EUR", "CHF", 1.065, None)]

def test_english_section_options_discount_rate():
    s = prices.parse_section(EN)
    assert [l.kind for l in s.lines] == ["machine", "option", "service", "discount"]
    assert s.lines[3].amounts == {"CHF": -9000.0} and s.sum_ok is True and s.totals == {"CHF": 200000.0}
    assert s.rates == [prices.Rate("EUR", "CHF", 1.08, "March 12th, 2019")]

def test_sum_mismatch_and_no_total():
    bad = {1: EN[4].replace("CHF 4,000.00", "CHF 5,000.00")}
    assert prices.parse_section(bad).sum_ok is False
    no_total = {1: "PRICE\nPaloma 4R cell CHF 300,000.00\nOption: second infeed CHF 40,000.00\n"}
    s = prices.parse_section(no_total)
    assert s.totals == {} and s.sum_ok is None and len(s.lines) == 2

def test_no_price_section():
    assert prices.parse_section({1: "Technical description of the Paloma 2R", 2: "Layout"}) is None

def test_offer_date_and_type():
    assert prices.offer_date({1: "Vufflens-la-Ville, 23.06.2025\nOffer 3001234"}) == ("2025-06-23", "23.06.2025")
    assert prices.offer_date({1: "Lausanne, le 15 mars 2012"}) == ("2012-03-15", "15 mars 2012")
    assert prices.offer_date(EN) is None  # only date is inside the rate sentence, and not on pages 1-2
    assert prices.offer_type({1: "Budgetary offer for a Paloma 4R"}, "x.pdf") == "budget"
    assert prices.offer_type({1: "Offer"}, "ACME Richtpreis.pdf") == "budget"
    assert prices.offer_type({1: "Offer"}, "x.pdf") == "firm"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_prices.py -v`
Expected: FAIL (`ModuleNotFoundError: dmx_docs.prices`)

- [ ] **Step 3: Implement `prices.py`**

Section algorithm (the tests fix the rest):

```
lines = text of price_pages, split on "\n", each tagged with its page
blocks = []; label = []
for each line:
    amts = amounts_in(line)
    words = line with its amounts and currency tokens removed, stripped of "()-:,."
    if words is only a section heading ("PRICE", "3. PRICE", "PREIS", "PRIX", "Prix / Price"): skip the line
    if words and not amts: if the previous line had amounts, close the block; label.append(line)
    elif words and amts: label.append(words); close block with amts          # "Option: vision CHF 25,000.00"
    elif amts: add amts to the open block (opened by the last label)        # "(EUR …)" then "CHF …" lines
close the last block. A block whose label matches the total words
(price|prix|preis|total) + (net|netto|hors taxes|ohne steuern|without taxes) gives `totals`;
a block without amounts is dropped (headings). The heading "PRICE"/"PREIS" alone is not a line.
```

A block's amounts are merged per currency (one value per currency; a second value in the same currency starts a new block). Quantity: `^\s*(\d+)\s*[x×]\s` on the label. `sum_ok`: per currency present in `totals`, `abs(sum(lines) - total) <= max(5, 0.002 * total)`. Model/family: `machines.models_in(label)`, the most specific model, family alone when no model. `classify`: discount if any amount < 0 or words `remise|rabais|rabatt|discount|nachlass`; option if `option|optional|en option`; service if `installation|inbetriebnahme|mise en service|commissioning|training|formation|schulung|travel|reise|déplacement|transport|emballage|verpackung|packing|documentation|sat|fat`; machine if a model/family is found or `cell|cellule|zelle|line|ligne|linie|robot`; else other. Rates: `(\d+[.,]?\d*)\s*(EUR|Euro|USD|CHF)\s*=\s*(\d+[.,]\d+)\s*(CHF|EUR|USD)`, date written = the text inside the nearest preceding parentheses when it holds a date.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_prices.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dmx_docs/prices.py tests/test_prices.py
git commit -m "Price sections of offers by rules: lines, quantities, kinds, totals per currency, sum check, rates, dates"
```

---

### Task 4: Local model for pages the rules cannot read

**Files:**
- Modify: `src/dmx_docs/vision.py` (`chat_json`), `src/dmx_docs/prices.py` (`parse_with_model`, `MODEL_PROMPT`)
- Test: `tests/test_prices.py` (new tests), uses the existing `fake_ollama` fixture

**Interfaces:**
- Consumes: `prices.PriceSection`, `PriceLine`, `classify`, `machines.models_in` (Task 3).
- Produces:
  - `vision.chat_json(url: str, model: str, prompt: str, timeout: float) -> dict` - one `/api/chat` call with `"format": "json"`, `"stream": false`, `"options": {"temperature": 0, "num_predict": 2048}`; raises `VisionError` (existing class) on HTTP error, timeout or invalid JSON.
  - `prices.parse_with_model(url: str, model: str, pages: dict[int, str], timeout: float) -> PriceSection | None` - sends the price pages' text (or pages 1-6 when `price_pages` found none) with `MODEL_PROMPT`; expects `{"lines": [{"text", "quantity", "amounts": {"CHF": 1.0}}], "totals": {...}, "rates": [{"base", "quote", "rate", "date"}]}`; kinds and models are computed by `classify` / `models_in`, never taken from the model; `sum_ok` is recomputed. Returns `None` when the answer has no lines.

- [ ] **Step 1: Write the failing tests**

```python
def test_model_reads_a_page(fake_ollama):
    fake_ollama.reply = lambda body: (200, {"message": {"content": json.dumps({
        "lines": [{"text": "Paloma 4R cell", "quantity": 1, "amounts": {"CHF": 300000}},
                  {"text": "Installation", "quantity": None, "amounts": {"CHF": 20000}}],
        "totals": {"CHF": 320000}, "rates": []})}, "done": True})
    s = prices.parse_with_model(fake_ollama.url, "test-vision", {3: "garbled table text"}, timeout=10)
    assert [l.kind for l in s.lines] == ["machine", "service"] and s.sum_ok is True
    assert s.lines[0].model == "Paloma 4R" and s.pages == [3]
    assert fake_ollama.calls[0]["format"] == "json" and "garbled table text" in fake_ollama.calls[0]["messages"][-1]["content"]

def test_model_bad_json_raises(fake_ollama):
    fake_ollama.reply = lambda body: (200, {"message": {"content": "not json"}, "done": True})
    with pytest.raises(vision.VisionError):
        prices.parse_with_model(fake_ollama.url, "test-vision", {1: "x"}, timeout=10)
```

(`fake_ollama.url` is the fixture's base URL.)

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_prices.py -k model -v`
Expected: FAIL (`AttributeError: ... 'parse_with_model'`)

- [ ] **Step 3: Implement `chat_json` (reuse `vision._post`) and `parse_with_model`**

`MODEL_PROMPT` (English): the page text of a sales offer's price section; return JSON with exactly those keys; one entry per priced line in document order; every amount as printed with its currency code; negative for discounts; no line for totals, per-day or per-hour rates, or "on request"; `quantity` only when the text states it (`2x`, `2 pcs`), else null; never convert currencies.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_prices.py tests/test_vision.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dmx_docs/vision.py src/dmx_docs/prices.py tests/test_prices.py
git commit -m "Read price pages the rules cannot split with the local model (JSON), same checks"
```

---

### Task 5: The register: `dmx-docs offers`

**Files:**
- Create: `src/dmx_docs/offers.py`
- Modify: `src/dmx_docs/cli.py` (`offers` command), `pyproject.toml` (`xlrd>=2.0`)
- Test: `tests/test_offers.py`

**Interfaces:**
- Consumes: `doc_facets` offer columns (Task 1), `prices.*` (Tasks 3-4), `vision.unavailable(url, model)` (existing).
- Produces:
  - `offers.TABLES` (SQL, created by `run_offers`):
    - `offers(offer_key TEXT PRIMARY KEY, number_written TEXT, client TEXT, site TEXT, country TEXT, country_source TEXT, partner TEXT, category TEXT, title TEXT, folder TEXT, first_date TEXT, last_date TEXT, year INTEGER, author TEXT, offer_type TEXT, status TEXT, status_source TEXT, project TEXT, status_evidence TEXT, duplicate_of TEXT)`
    - `offer_revisions(id INTEGER PRIMARY KEY, offer_key TEXT, revision TEXT, variant TEXT, rev_order INTEGER, date TEXT, date_source TEXT, doc_id INTEGER, path TEXT, pages TEXT, totals TEXT, sum_ok INTEGER, read_by TEXT, confidence TEXT, note TEXT, translation_of INTEGER)` (`totals` JSON `{"CHF": 461000.0}`, `pages` like `"4-5"`, `read_by` `rules|model|correction|none`, `confidence` `high|medium|low|none`)
    - `offer_lines(id INTEGER PRIMARY KEY, revision_id INTEGER, position INTEGER, text TEXT, kind TEXT, family TEXT, model TEXT, quantity INTEGER, amounts TEXT, page INTEGER, read_by TEXT, confidence TEXT)`
    - `offer_rates(revision_id INTEGER, base TEXT, quote TEXT, rate REAL, date_written TEXT)`
    - `offer_model_cache(text_hash TEXT PRIMARY KEY, answer TEXT)` - model answers by SHA-1 of the sent text, kept across rebuilds.
  - `@dataclass OfferStats: offers: int, revisions: int, lines: int, by_rules: int, by_model: int, unread: int, model_unavailable: bool` with `summary() -> str`.
  - `offers.run_offers(cfg: Config, projects_db: str | None = None, review: bool = False, progress=print) -> OfferStats` - rebuilds every table except the cache inside one transaction; then `link_projects`, `apply_corrections` and (if `review`) `write_review` from Task 6.
  - `offers.history_rows(rows: list[list]) -> list[tuple[str, str, str]]` - `(YYYY-MM-DD, author, action)` from the cell values of sheet `Quotations` of `History.xls` (header row skipped, Excel serial dates such as `39842.0` -> `2009-01-29`, blank rows skipped); `offers.read_history(path: str)` = xlrd + `history_rows`.
  - `offers.quotation_rows(rows: list[list]) -> list[dict]` - rows of sheet `Quotations` of `QuotationList.xls` from the third row, keys `country, city, description, date, machine, offer, final_customer, final_country, author, status, company`, rows with fewer than 3 filled cells skipped, dates as `YYYY-MM-DD`, offer numbers through `offerkeys.offer_key`; `offers.read_quotation_list(path: str)` = xlrd + `quotation_rows`.
  - `offers.machine_aliases(vocabulary_path: str) -> list[tuple[str, str]]` - `(folded alias, model)` from the `## machines` section of `knowledge\vocabulary.md` (parsed with the existing `knowledge.Vocabulary`), longest alias first; a machine-like line with no model from `models_in` gets the model of the first alias contained in its folded text.
  - CLI: `dmx-docs offers [--review] [--projects-db PATH]`. Default projects index: `load_config(cfg.config_path, world="projects").db_path` when the config has a projects world; a world whose profile is not `offers` prints `Not an offers world: nothing to do.` and exits 0 (so `dmx.ps1 update` can run the step for every world).

- [ ] **Step 1: Write the failing tests**

Fixture `offers_corpus(tmp_path)` builds `Clients\` with `make_pdf` (fictional names):
- `ACME Foods\Offres\3001234 - Line 5\00\Offer - 3001234_00.pdf`: page 1 `"Lausanne, 10.02.2020\nOffer 3001234"`, page 2 = `EN[4]` with total `CHF 200,000.00`.
- `...\3001234 - Line 5\01\Offer - 3001234_01.pdf` (page 1 `"14.05.2020"`, total CHF 190,000 after a discount of 19,000) and `...\01\Offer - 3001234_01_DE.pdf` with the same amounts in German wording (a translation).
- `...\3001234 - Line 5\02 - Vandelay version\Offer - 3001234_02.pdf` (a named revision folder: `variant = "Vandelay version"`).
- `...\3001234 - Line 5\03\Offer - 3001234_03 Vandelay.pdf` and `...\03\Offer - 3001234_03 Wonka.pdf` with different totals (two variants of the last revision; variant label = file-name suffix after the offer number and revision).

"Last revision" = the highest revision label only; each variant inside it is one point (`revisions="all"` shows the others).
- `ACME Foods\Offres\0904023\V1\ACME B-0904023 V1.pdf` with no price words (unread revision).
- `ACME Foods\Offres\0904099\V1\ACME B-0904099 V1.pdf`: page 1 `"Offer for a Paloma D2 Top Loader with two Delta robots"`, page 2 a price section whose machine line is `"Paloma D2 Top Loader cell CHF 150,000.00"`; a `knowledge\vocabulary.md` next to the test config with `## machines\n- Paloma 2R: Paloma D2 Top Loader with two Delta robots, Paloma D2 Top Loader` (old-name mapping).
- `ACME Foods\Offres\Line 7 proposal\00\Proposal.pdf` (offer folder without a number).

`.xls` files cannot be generated without Excel, so `History.xls` / `QuotationList.xls` are tested through their row parsers (`history_rows`, `quotation_rows`, below) with Python lists; `read_history` / `read_quotation_list` are thin `xlrd` wrappers around them.
- `01_Historique\PROSPECT\Allemagne\Dupont Biscuits\B-250306 V1\Dupont B-250306 V1.pdf` with `DE` pages (budget wording "Richtpreis" on page 1).
- `Globex\Offres\3001234 - copy\00\Offer - 3001234_00.pdf` (same key in another client folder).

```python
def test_register(offers_world):  # indexed with run_index, then run_offers(cfg, projects_db=None)
    con = store.connect(cfg.db_path)
    o = con.execute("SELECT * FROM offers WHERE offer_key='3001234'").fetchone()
    assert (o["client"], o["first_date"], o["last_date"], o["year"], o["offer_type"]) == \
           ("ACME Foods", "2020-02-10", "2020-05-14", 2020, "firm")
    revs = con.execute("SELECT revision, variant, translation_of IS NOT NULL t, confidence FROM offer_revisions "
                       "WHERE offer_key='3001234' ORDER BY rev_order, id").fetchall()
    assert [(r[0], r[1], r[2]) for r in revs] == [("00", None, 0), ("01", None, 0), ("01", None, 1),
                                                  ("02", "Vandelay version", 0), ("03", "Vandelay", 0), ("03", "Wonka", 0)]
    assert revs[0]["confidence"] == "high"
    lines = con.execute("SELECT kind, amounts FROM offer_lines l JOIN offer_revisions r ON r.id=l.revision_id "
                        "WHERE r.offer_key='3001234' AND r.revision='00' ORDER BY position").fetchall()
    assert [l[0] for l in lines] == ["machine", "option", "service", "discount"]
    assert json.loads(lines[0][1]) == {"CHF": 180000.0}

def test_translation_lines_not_counted(offers_world):
    n = con.execute("SELECT count(*) FROM offer_lines l JOIN offer_revisions r ON r.id=l.revision_id "
                    "WHERE r.translation_of IS NOT NULL").fetchone()[0]
    assert n == 0

def test_archive_offer(offers_world):
    o = con.execute("SELECT * FROM offers WHERE offer_key='B-250306'").fetchone()
    assert (o["category"], o["country"], o["country_source"], o["offer_type"]) == ("archive", "Germany", "folder", "budget")

def test_unread_revision_is_listed(offers_world):
    r = con.execute("SELECT read_by, confidence, note FROM offer_revisions WHERE offer_key='0904023'").fetchone()
    assert (r[0], r[1]) == ("none", "none") and "no price section" in r[2]

def test_duplicate_key_in_two_client_folders(offers_world):
    o = con.execute("SELECT client, duplicate_of FROM offers WHERE offer_key='3001234'").fetchone()
    assert o["client"] == "ACME Foods" and "Globex" in (o["duplicate_of"] or "")
    assert con.execute("SELECT count(*) FROM offer_revisions WHERE path LIKE '%Globex%'").fetchone()[0] == 0

def test_model_unavailable_keeps_rules_lines(offers_world_garbled):  # one revision whose price page the rules cannot read
    stats = offers.run_offers(cfg)  # vision_url points to a closed port
    assert stats.model_unavailable and stats.by_rules > 0
    r = con.execute("SELECT read_by, note FROM offer_revisions WHERE path LIKE '%garbled%'").fetchone()
    assert r[0] == "none" and "model unavailable" in r[1]

def test_model_cache(fake_ollama, offers_world_garbled):  # second run sends nothing
    offers.run_offers(cfg_with(fake_ollama)); n = len(fake_ollama.calls)
    offers.run_offers(cfg_with(fake_ollama)); assert len(fake_ollama.calls) == n

def test_old_machine_name_from_the_word_list(offers_world):
    l = con.execute("SELECT model, family FROM offer_lines l JOIN offer_revisions r ON r.id=l.revision_id "
                    "WHERE r.offer_key='0904099' AND kind='machine'").fetchone()
    assert (l[0], l[1]) == ("Paloma 2R", "Paloma")

def test_history_and_quotation_rows():
    assert offers.history_rows([["Date", "Rédacteur", "Action", "TODO"], [39842.0, "CHL", "Première offre", ""],
                                ["", "", "", ""]]) == [("2009-01-29", "CHL", "Première offre")]
    rows = offers.quotation_rows([["Country", "City"], ["Final Customer"],
                                  ["France", "Lyon", "Mise en boîte", 39960.0, "Paloma FP2", "0905020", "", "", "CGA", "Running", "ACME"]])
    assert rows[0]["offer"] == "0905020" and rows[0]["date"] == "2009-05-27" and rows[0]["status"] == "Running"

def test_cli_other_profile_is_a_noop(capsys, marketing_cfg_file):
    cli.main(["--config", str(marketing_cfg_file), "--world", "marketing", "offers"])
    assert "Not an offers world" in capsys.readouterr().out
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_offers.py -v`
Expected: FAIL (`ModuleNotFoundError: dmx_docs.offers`)

- [ ] **Step 3: Implement `offers.py` and the CLI command**

Per offer key (from `doc_facets.offer`, ordered by path): the first folder (shortest path) owns the key, others go to `duplicate_of` (`"; "`-joined folder paths) and are skipped. Per revision: candidate documents = `doc_type='offer'` docs of that offer and revision with status `ok`; same file stem in `.pdf` and `.doc(x)` -> the PDF. Page text = concatenation of the doc's chunks per page (`ORDER BY page_no, seq`), excluding chunks listed in `picture_uses`. Read each candidate with `prices.parse_section`; when it returns `None` or `sum_ok is False`, try `parse_with_model` (cache first; skip when `vision.unavailable()` returned a message, and set `note = "model unavailable"`). Confidence: rules + `sum_ok` -> `high`; model + `sum_ok`, or rules with no total -> `medium`; else `low`; OCR pages in the section (`ocr_pages`) cap it at `medium`. Two candidates of one revision with identical `totals` -> the later one gets `translation_of` = the first one's id and no lines. Different totals -> both kept; the variant label comes from the revision folder, else the file name's suffix after the offer number. Revision date: `prices.offer_date` of the document, else `History.xls` (first row date for the first revision, matching action text otherwise), else the file's `mtime` (`date_source` = `document | history | file`). Offer: `first_date`/`last_date`/`year` (year of `last_date`) from its revisions; `title` from the offer folder name after ` - `; `offer_type` = `budget` if the last revision's document says so; country: `doc_facets.country` (archive folder, `country_source='folder'`), else a `QuotationList.xls` row of that client with the same offer number (`quotation_list`), else `offerkeys.country_en` on the first country word of the offer's page 1 (`document`), else `None`. Status is left `unknown` here (Task 6). `xlrd` is imported lazily inside `read_history` / `read_quotation_list`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_offers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dmx_docs/offers.py src/dmx_docs/cli.py pyproject.toml tests/test_offers.py
git commit -m "Offer register: revisions, variants, translations, priced lines, dates, countries (dmx-docs offers)"
```

---

### Task 6: Won / not won, corrections, review list

**Files:**
- Modify: `src/dmx_docs/offers.py`
- Test: `tests/test_offers.py` (new tests)

**Interfaces:**
- Consumes: the register tables (Task 5); a projects index file (`docs`, `chunks`, `chunks_fts`, `docs_fts`, `doc_facets`).
- Produces:
  - `offers.number_forms(offer_key: str) -> tuple[str, re.Pattern]` - the FTS token to look for and the regex that must match in the text: `"3003929"` -> (`3003929`, `(?<!\d)3003929(?!\d)`); `"B-250306"` -> (`250306`, `(?<![A-Za-z0-9])B-?250306(?!\d)`); `"1106033-A"` -> (`1106033`, `(?<!\d)1106033-?A(?![A-Za-z])`).
  - `offers.link_projects(con, projects_db: str | None, today: date | None = None) -> int` - sets `status`, `status_source` (`projects_world | quotation_list | age | none`), `project`, `status_evidence` (`<path> p.<n>`); returns the number of offers marked `won`. Opens the projects index read-only (`file:...?mode=ro`, `uri=True`). Two or more projects -> `status='won'`, `project` = the project with most evidence, `status_evidence` lists all, flagged in review.
  - `offers.apply_corrections(con, path: str | None) -> list[str]` - returns messages for corrections whose target does not exist. CSV columns `offer,revision,line,field,value,note,who,date` (UTF-8 with or without BOM, `;` or `,` separated - sniffed). Fields: on offers `client, country, status, project, offer_type, title`; on revisions `date, variant, ignore` (`ignore=yes` removes the revision from charts); on lines `text, kind, model, quantity, delete` and `amount:<CUR>` (empty value removes that currency). Corrected rows get `read_by='correction'`; a table `offer_corrections(offer, revision, line, field, value, note, who, date, applied INTEGER, message TEXT)` keeps every row for `offer_card`.
  - `offers.write_review(con, path: str) -> int` - `review.csv` (UTF-8 with BOM, `;`-separated, for Excel) with columns `offer;revision;client;issue;detail;path;page`; issues: `low confidence`, `sum mismatch`, `no price section`, `model unavailable`, `unknown machine name` (a `machine` line with neither model nor family), `duplicate offer number`, `several projects`, `stale correction`, `unknown offer folder` (a folder directly under `Offres` or under a special top folder whose name gives no offer key, e.g. `Line 7 proposal`). Returns the row count.

- [ ] **Step 1: Write the failing tests**

```python
def make_projects_db(tmp_path, texts):  # {(project, rel_path): page_text}
    ...  # index a tiny projects tree with make_cfg(..., profile="projects") + run_index; return db_path

def test_won_by_number_in_a_project(offers_world, tmp_path):
    pdb = make_projects_db(tmp_path, {("THOR", r"THOR\0_Vente\00_Offres\Offre.pdf"): "Notre offre 3001234 du 14.05.2020"})
    assert offers.link_projects(con, pdb, today=date(2026, 10, 16)) == 1
    o = con.execute("SELECT status, project, status_evidence FROM offers WHERE offer_key='3001234'").fetchone()
    assert (o[0], o[1]) == ("won", "THOR") and o[2].endswith("Offre.pdf p.1")

def test_archive_number_needs_its_prefix(offers_world, tmp_path):
    pdb = make_projects_db(tmp_path, {("ANGE", r"ANGE\5_Gestion\PV.pdf"): "Réunion du 250306 à Lausanne"})
    offers.link_projects(con, pdb, today=date(2026, 10, 16))
    assert con.execute("SELECT status FROM offers WHERE offer_key='B-250306'").fetchone()[0] == "no_order_found"

def test_open_and_unknown(offers_world, tmp_path):
    offers.link_projects(con, make_projects_db(tmp_path, {}), today=date(2021, 1, 1))
    assert con.execute("SELECT status FROM offers WHERE offer_key='3001234'").fetchone()[0] == "open"
    offers.link_projects(con, None)
    assert con.execute("SELECT status, status_source FROM offers WHERE offer_key='3001234'").fetchone()[:] == ("unknown", "none")

def test_corrections(offers_world, tmp_path):
    p = tmp_path / "corrections.csv"
    p.write_text("offer;revision;line;field;value;note;who;date\n"
                 "3001234;-;-;status;lost;lost to a competitor;JM;2026-10-16\n"
                 "3001234;00;1;model;Paloma 3R;;JM;2026-10-16\n"
                 "3001234;00;2;amount:EUR;24000;;JM;2026-10-16\n"
                 "9999999;-;-;status;won;;JM;2026-10-16\n", encoding="utf-8-sig")
    msgs = offers.apply_corrections(con, str(p))
    assert msgs == ["correction for offer 9999999: no such offer"]
    assert con.execute("SELECT status FROM offers WHERE offer_key='3001234'").fetchone()[0] == "lost"
    line2 = con.execute("SELECT amounts, read_by FROM offer_lines l JOIN offer_revisions r ON r.id=l.revision_id "
                        "WHERE r.offer_key='3001234' AND r.revision='00' AND position=2").fetchone()
    assert json.loads(line2[0]) == {"CHF": 25000.0, "EUR": 24000.0} and line2[1] == "correction"

def test_corrections_survive_a_rebuild(offers_world, tmp_path):  # run_offers twice with cfg.corrections_path set
    ...  # status stays 'lost' after the second run

def test_review(offers_world, tmp_path):
    n = offers.write_review(con, str(tmp_path / "review.csv"))
    rows = (tmp_path / "review.csv").read_text(encoding="utf-8-sig").splitlines()
    assert rows[0] == "offer;revision;client;issue;detail;path;page" and n == len(rows) - 1
    assert any(";no price section;" in r and r.startswith("0904023;") for r in rows)
    assert any(";duplicate offer number;" in r for r in rows)
    assert any(";unknown offer folder;" in r and "Line 7 proposal" in r for r in rows)
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_offers.py -k "won or prefix or open or correction or review" -v`
Expected: FAIL (`AttributeError: ... 'link_projects'`)

- [ ] **Step 3: Implement `number_forms`, `link_projects`, `apply_corrections`, `write_review`; call them at the end of `run_offers` (corrections from `cfg.corrections_path`, review to `data_dir\<world>\review.csv` when `review=True`)**

Linking: for each offer, FTS `MATCH '"<token>"'` on `chunks_fts` and `docs_fts` of the projects index, confirm each hit with the regex on the chunk text or the doc path, map the doc to `doc_facets.project`. No evidence: a `QuotationList` status (`Ordered` -> `won` without project, `Cancelled` -> `cancelled`, `Running` -> keep looking) else `open` when `last_date` is within `OPEN_YEARS` of `today`, else `no_order_found`. `projects_db=None` or file missing -> `unknown`, `status_source='none'`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_offers.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/dmx_docs/offers.py tests/test_offers.py
git commit -m "Offers: won/no order found/open from the projects world, user corrections, review list"
```

---

### Task 7: Tools of `dmx-offers`

**Files:**
- Create: `src/dmx_docs/offer_tools.py`
- Modify: `src/dmx_docs/search.py` (`_filters` / `search` get `profile`), `src/dmx_docs/tools.py` (`DocTools` methods, `_facets` line for offers, `search` filters), `src/dmx_docs/server.py` (`OFFERS_INSTRUCTIONS`, `offers_search`, four tools, registration)
- Test: `tests/test_offer_tools.py`

**Interfaces:**
- Consumes: register tables (Tasks 5-6).
- Produces (each takes `con` and returns text; `DocTools` wraps them with `self._con()`):
  - `list_offers(con, client=None, country=None, machine=None, status=None, year_from=None, year_to=None, text=None, limit=100) -> str` - newest first, one line: `<key> · <client>[ / site] · <country> · <first_date>..<last_date> · <n> revisions · <machine lines of the last revision> · total <CHF …, EUR …> · <status>[ -> <project>]`, then the coverage line.
  - `offer_card(con, offer: str) -> str` - offer facts and status evidence; per revision (oldest first): label, variant, date (+ source), document path and pages, totals, confidence, read_by, then its lines `  <position>. <text> · <kind> · <model> · qty <q> · CHF … · EUR … (p.<page>)`; rates; translations; corrections applied (with who/date) and stale ones; for `won`, the line `Final negotiated price: see the project's 0_Vente in the projects world.`; the other documents of the offer (specification, mails, layouts) by `doc_facets`.
  - `offer_lines(con, machine=None, kind=None, client=None, country=None, status=None, offer_type="firm", category="client,archive", year_from=None, year_to=None, currency="CHF", revisions="last", limit=500) -> str` - a `;`-separated table, header `date;offer;revision;variant;client;country;status;quantity;amount;unit_price;currency;line;path;page;confidence`, then the coverage line. `unit_price` only when `quantity` is set. Lines without an amount in `currency` are left out (reason `no <CUR> amount`); revisions with `ignore=yes` are left out (`ignored by correction`).
  - `offer_stats(con, group_by: str, client=None, country=None, machine=None, status=None, offer_type=None, category=None, year_from=None, year_to=None) -> str` - `group_by` is one or two of `year, country, client, machine, status, author, partner, offer_type, category` (comma-separated); `;`-table `<keys>;offers;revisions`, sorted by keys; offers with an unknown key value counted under `unknown`.
  - `DocTools.search` gains `offer`, `revision`, `partner`, `year_from`, `year_to`, `status` (passed in `facet_filter`, `None` for the other profiles), and passes `profile=self.cfg.profile` to `Searcher.search`.
  - `Searcher._filters(..., profile: str = "projects")`: when `profile != "projects"`, `client`, `country`, `machine` are plain `doc_facets` columns (`machine` via `LIKE '%|v|%'`), and `country` also matches docs whose `offer` has `offers.country` in the values; new facet keys `offer`, `revision`, `partner`, `year_from`, `year_to`, `status` (the last three through the `offers` table on `doc_facets.offer`).

- [ ] **Step 1: Write the failing tests**

```python
def test_offer_lines_chart_rows(offers_world_linked):
    out = offer_tools.offer_lines(con, machine="Paloma")
    rows = [r for r in out.splitlines() if r and not r.startswith(("date;", "Coverage", "Left out"))]
    assert all(r.split(";")[10] == "CHF" for r in rows)
    assert out.splitlines()[0].startswith("date;offer;revision;variant;client")
    assert "Coverage:" in out and "budget" in out  # B-250306 left out as budget

def test_last_revision_default_and_all(offers_world_linked):
    last = offer_tools.offer_lines(con, client="ACME")
    every = offer_tools.offer_lines(con, client="ACME", revisions="all")
    assert ";03;Vandelay;" in last and ";03;Wonka;" in last and ";02;" not in last and ";00;" not in last
    assert ";00;" in every and ";02;Vandelay version;" in every

def test_currency_never_converted(offers_world_linked):
    out = offer_tools.offer_lines(con, currency="EUR", offer_type="firm,budget")
    assert "B-250306" in out and "no EUR amount" in out  # the EN offer has CHF only: left out, named

def test_offer_stats(offers_world_linked):
    out = offer_tools.offer_stats(con, "year,country")
    assert out.splitlines()[0] == "year;country;offers;revisions" and any(l.startswith("2020;") for l in out.splitlines())
    with pytest.raises(ValueError):
        offer_tools.offer_stats(con, "colour")

def test_offer_card_and_list(offers_world_linked):
    card = offer_tools.offer_card(con, "3001234")
    assert "Vandelay version" in card and "(p.2)" in card and "translation" in card.lower()
    assert "status won -> THOR" in card and "Final negotiated price" in card
    assert offer_tools.offer_card(con, "1234567").startswith("No offer 1234567")
    lst = offer_tools.list_offers(con, client="acme")
    assert lst.splitlines()[0].startswith("3001234 · ACME Foods") and "Coverage:" in lst

def test_offers_server_tools(offers_cfg):  # same in-process Client pattern as test_index_and_tools.py (skips without mcp >= 2)
    names = tool_names(server.build_server(offers_cfg))
    assert {"list_offers", "offer_card", "offer_lines", "offer_stats", "search", "world_map"} <= names
    assert "list_projects" not in names and "catalog" not in names

def test_offers_search_filters(offers_world_linked):
    out = DocTools(cfg).search("Paloma", client="ACME Foods", year_from=2020)
    assert "ACME Foods" in out and "Dupont" not in out
    assert "offer 3001234 rev 00" in out  # facet line of an offers hit
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_offer_tools.py -v`
Expected: FAIL (`ModuleNotFoundError: dmx_docs.offer_tools`)

- [ ] **Step 3: Implement `offer_tools.py`, the search/tools/server changes**

`_facets` line for offers: `client <client>[ / <site>] · offer <key> rev <revision>[ (<variant>)] · <doc_type> · <country>[ · partner <p>]`. `OFFERS_INSTRUCTIONS`: what the world holds (every offer since 1999, revisions, specifications, mails; sales and management only), the register and when to use each tool (charts and trends -> `offer_lines`; counts -> `offer_stats`; a client's history -> `list_offers(client=)` then `offer_card`), the rules (one currency per chart, never convert, show the coverage line, show line text so scopes are visible, mark won vs not won, cite path and page, verify a surprising value with `view_page`), and the same reformulate / retrieve / evaluate / retry / synthesize loop as the other worlds. Tool docstrings list the parameters as above.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests -v`
Expected: PASS (whole suite: search filter changes must not alter projects/marketing/documentation behaviour)

- [ ] **Step 5: Commit**

```bash
git add src/dmx_docs/offer_tools.py src/dmx_docs/search.py src/dmx_docs/tools.py src/dmx_docs/server.py tests/test_offer_tools.py
git commit -m "dmx-offers tools: list_offers, offer_card, offer_lines, offer_stats, offers search filters"
```

---

### Task 8: Launcher script

**Files:**
- Modify: `scripts/dmx.ps1`
- Test: `tests/test_dmx_ps1.py`

**Interfaces:**
- `update` runs `index`, `ocr`, `offers`, `describe`, `embed` for every world (the `offers` step is a no-op elsewhere, Task 5).
- New command `offers [args]` = `Invoke-Locked 'offers' @(, (@('offers') + $Rest))` (e.g. `dmx.ps1 offers -World offers --review`).
- `Copy-MasterToLocal`: also copies `<shared>\worlds\<world>\corrections.csv` to `<local>\data\<world>\` when present. `Save-LocalToMaster`: also copies `<local>\data\<world>\review.csv` back when present.
- `pull` and every locked command: when `Test-Path` on `<shared>\worlds\<world>` fails with access denied, stop with `No access to the '<world>' world in the shared folder (ask for the AD group that can read it). Other worlds are not affected.` and exit code 5.

- [ ] **Step 1: Write the failing tests** (extend the `layout` fixture's config with `[worlds.offers]\nprofile = 'offers'`; use the existing fake-Python hook the other tests use to record the dmx-docs calls)

```python
def test_update_runs_the_offers_step(layout): ...      # recorded steps: index, ocr, offers, describe, embed
def test_corrections_down_review_up(layout): ...      # corrections.csv appears locally before the run; review.csv written by the fake step is copied back
def test_no_access_message(layout): ...               # deny read on worlds\offers with icacls; pull -World offers -> exit 5 and the message; pull -World projects still works
```

- [ ] **Step 2: Run them to verify they fail**

Run: `pytest tests/test_dmx_ps1.py -v`
Expected: the three new tests FAIL

- [ ] **Step 3: Implement the script changes (Edit tool only; keep the file's line endings)**

- [ ] **Step 4: Run the tests to verify they pass**

Run: `pytest tests/test_dmx_ps1.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add scripts/dmx.ps1 tests/test_dmx_ps1.py
git commit -m "dmx.ps1: offers step in update, corrections down / review up, clear no-access message"
```

---

### Task 9: Map, skill, docs

**Files:**
- Create: `maps/offers.md`, `skills/dmx-research/references/offers.md`
- Modify: `skills/dmx-research/SKILL.md` (routing), `maps/projects.md` ("Not in this world" line), `knowledge/vocabulary.md` (empty `## machines` section), `README.md` (worlds list, launcher 7, corrections/review files, access), `config.example.toml` (already has the block from Task 1)
- Test: `tests/test_skill.py`, `tests/test_worldmap.py` (existing checks)

- [ ] **Step 1: Write `maps/offers.md`** in the format of `maps/marketing.md`: what the world is for; kinds of information table (offers and revisions, technical solutions, specifications, mails, layouts, visit reports, archive offers by country, partner offers, market studies, NDAs, sales conditions) with folder, filter and example question; the register and its four tools; not in this world (costing sheets, CAD, Excel -> `list_folder` / `open_document`; final negotiated price of won offers -> projects world `0_Vente`; contact lists: not indexed); pitfalls (scope differs between offers: read the line text; budget vs firm; variants; currencies never converted; OCR'd archive offers; coverage line).
- [ ] **Step 2: Write `references/offers.md` and the SKILL.md routing line**: price trend / history / counts questions -> `offer_lines` / `list_offers` / `offer_stats` first, then `offer_card` and the documents for the points that matter; always state the coverage line under a chart. No offer data in these files.
- [ ] **Step 3: Add to `maps/projects.md` under "Not in this world"**: `- All revisions of the offers, offers not won and the sales history per client -> Offers world (sales and management only).` Add an empty `## machines` section to `knowledge/vocabulary.md` with one comment line explaining the format (`- <model>: <old name>, <old name>`); the entries are filled with the user after the first `review.csv` (its `unknown machine name` rows).
- [ ] **Step 4: Run the checks**

Run: `pytest tests/test_skill.py tests/test_worldmap.py -v`
Expected: PASS (every tool the skill shows exists on the offers server)

- [ ] **Step 5: Commit**

```bash
git add maps/offers.md maps/projects.md skills/dmx-research knowledge/vocabulary.md README.md config.example.toml
git commit -m "Offers world: map, skill reference and routing, README"
```

---

## Rollout (after the branch is merged; done with the user, not by a task)

1. Team share folder for `worlds\offers` with the AD group's permission (user / IT).
2. `[worlds.offers]` table in the shared `config.toml` (Write tool, then check the bytes: UNC backslashes).
3. On WS032: `pull` projects, Ollama running, then `7 - Update a world (index + embeddings).cmd offers`; then `dmx.ps1 offers -World offers --review`.
4. The user's spot check of 30 random offers against `review.csv` and `offer_card`; corrections in `corrections.csv`.
5. Laptop: `pull -World offers`, `dmx-offers` entry through `Set Claude Desktop connectors.cmd`, new skill zip; the three example questions in Claude Desktop.

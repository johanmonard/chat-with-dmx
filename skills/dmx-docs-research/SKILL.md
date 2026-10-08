---
name: dmx-docs-research
description: Agentic research workflow for the Demaurex project documentation exposed by the dmx-docs MCP tools (search, list_projects, find_files, list_folder, read_document, find_in_document, view_page, open_document). Use it for EVERY question answered from the company documents - projects (THOR, YAKUMA, ANGE...), machines (Paloma, Presto, Hector...), offers, specifications, FAT/SAT, manuals, schematics, SAV - and whenever the dmx-docs tools are available and the question could be answered from internal documents.
---

# Researching the Demaurex documentation (dmx-docs)

Never answer from the raw top results of a single search. Run this loop:
**reformulate → retrieve → evaluate → (retry, max 3 rounds) → synthesize with sources.**

## 0. What is in the index

- PDF and Word files of the project folders on `\\DMX-FS01.rotzingerag.local\Daten$\RMA_PROJETS`
  (= drive N:), mostly French; also English, German, Spanish. Excel, images and CAD are not indexed.
- One folder per project, code-named (THOR, YAKUMA, ANGE, BOULE, MANOLO, INIESTA...). Current
  projects sit directly under `RMA_PROJETS`; finished ones may be grouped in **collections** such
  as `2_Hors_Garantie` (out of warranty). **What is indexed changes over time**: check with
  `list_projects` (or `index_status`) which projects and collections are actually in the index
  before concluding anything from an absence of results. If a project or collection is not
  listed, say "not in the index" - never invent another reason.
- Every document carries **facets**, shown under each search hit and usable as filters:
  `project`, `collection`, `section` (Vente, Finances, Electrique, Mecanique, Soft, Gestion,
  Rapports_Tests, Photos_Videos, Documentation, SAV, Cloture) and `doc_type` (offre, commande,
  cahier_des_charges, facture, modification, schema_electrique, layout, plan, nomenclature,
  pieces_detachees, manuel, doc_fournisseur, fat, sat, reception, mise_en_service, open_points,
  tests, suivi, planning, transport, sav, doc_electrique, doc_mecanique, soft).
  "type X" = from the template folder (reliable); "type X (from name)" = guessed from the file
  name; "(from section)" = only the section is known; "type unknown" = not classified.
- Current projects use this template (older projects use named folders instead: `Cahier des
  charges`, `Electrique`, `Mécanique`, `Gestion`, `Rapports`, `Documentation`, `SAV`, `Clôture`,
  `Facturation`; their deeper folders are less regular, so their doc_type is often guessed or unknown):

| Folder | Holds | Typical subfolders |
|---|---|---|
| `0_Vente` | offers, customer specifications, orders | `00_Offres`, `02_Commandes`, `03_Cahier_des_charges`, `04_Formulaire_validation`, `06_Materiel_Tiers` |
| `1_Finances` | invoices, payments | `11_Facturation` |
| `2_Electrique` | electrical design, schematics (long, mostly labels) | `20_Conception`, `21_Modifications`, `23_Schemas` |
| `3_Mecanique` | mechanical design, layouts, third-party equipment | `30_Conception`, `31_Modifications`, `33_Layouts`, `37_Materiel_Tiers` |
| `4_Soft` | software | |
| `5_Gestion` | project follow-up, meetings, planning, acceptance, transport | `51_Suivi`, `52_Planning`, `54_Transport`, `55_MES`, `56_Acceptation_machine` (FAT/SAT protocols) |
| `6_Rapports_Tests` | internal tests, commissioning reports, open points | `60_Tests_Internes`, `61_MES`, `604_Open_points_lists` |
| `8_Documentation` | machine manuals (FR/EN/DE), supplier docs | `80_USB stick Doc Project` (copy of the manual), `85_Received_DOC` |
| `9_SAV` | after-sales service | |

- Folders named `Anciennes versions` / `old` are not indexed: the index holds current versions.

## 1. Reformulate

Turn the question into **2-4 search queries** before the first search:

- **Precise French technical terms first**, then synonyms and the English/German equivalent.
  Vocabulary seen in the documents:
  - gripping: préhenseur, ventouse(s), pince, doigts souples, préhension, tilt, vide/venturi — gripper, suction cup
  - speed: cadence, débit, produits par minute (ppm), coups/min, rattrapage — throughput, rate
  - acceptance: FAT (réception usine), SAT (réception site), MES / mise en service, protocole de réception, OPL / open points list, réserves
  - machine parts: convoyeur (à bande, de boîtes), collateur, infeed, dépileur/denester, formeuse, fermeuse, encolleuse, flowpack/flow-pack, étui, carton, barquette, blister, francomat, XTS, vision/caméra
  - robots/machines: Paloma (nR), Presto, Hector, Delfi, Astor, Nestor — the number before R is the number of robots
  - documents: offre, cahier des charges (CDC), spécification, note de modification, rapport d'intervention, compte rendu, manuel, schéma électrique, nomenclature/BOM, pièces détachées
- **Exact identifiers in "double quotes"**: order numbers (`"120006116"`), part numbers (`"R911347583"`), codes (`"MN-114"`). Use `mode="keyword"` for these.
- **Decompose** multi-part or multi-project questions: one query per part ("cadence THOR" and "cadence YAKUMA", not "cadence THOR et YAKUMA").
- **Name a project?** Check its exact name with `list_projects name="..."`, then filter with
  `project="THOR"` (several: `project="THOR,YAKUMA"`). Many project names are ordinary words
  (ANGE, BOULE, LEON, VENUS, SPACE): without the filter, a keyword search mixes the project with the word.
  For a client or machine, use `find_files` (names often contain them) or a search without filter.
- **Kind of document known?** Filter with `doc_type=` (FAT findings → `fat,reception`; client
  requirement → `cahier_des_charges`; price/scope offered → `offre`; commissioning → `mise_en_service,sat`;
  how the machine works → `manuel`). Or with `section=` for a whole area (e.g. `SAV`).
- **Facet filters refine, they never replace the unfiltered search**: documents whose facet is
  unknown (mostly old projects) are left out by a filter. If a filtered search is thin, run it
  again without `doc_type`/`section` (keep `project`).
- Recent information only? Use `modified_after="2025"`. Current vs. out-of-warranty projects:
  `collection="2_Hors_Garantie"`.

## 2. Retrieve

Run the queries with `search` (default `mode="hybrid"`, `limit` 10; up to 20 for broad questions).
Each hit looks like:

```
[3] \\...\THOR\5_Gestion\56_Acceptation_machine\FAT DEMAUREX 221024.pdf — page 7/8 (PDF, modified 2024-10-28) [keyword+semantic, similarity 0.87 strong]
    project THOR · section Gestion · type fat (from name)
    excerpt...
```

## 3. Evaluate (before using anything)

For each promising hit, judge:

| Signal | Meaning |
|---|---|
| `keyword+semantic` | found by both methods: most reliable |
| similarity **strong** (≥ 0.86) | usually on topic |
| similarity **medium** (0.83-0.86) | possibly relevant: read the passage to confirm |
| similarity **weak** (< 0.83) | often off topic, even if words match |
| note "all matches are weak" | the documents probably do not cover the question as phrased |
| facet line (project, type) | is it the right project and the right kind of document? |

The similarity labels are **hints, not verdicts**: short keyword queries score lower than full
sentences (a relevant FAT page can show 0.83 for "problèmes cadence vision"). Always judge by
reading the excerpt; to get more meaningful scores, phrase semantic queries as full sentences.

Then check, out loud for yourself:
1. **Relevance**: does the excerpt answer the question, or only share words with it (e.g. "cadence" in a generic manual page vs. the project's measured rate)?
2. **Coverage**: is every part of the question answered by at least one relevant source?
3. **Right scope**: right project, right machine, right document type (a supplier manual does not tell what was agreed with the client; an offer does not tell what was measured at the FAT).
4. **Consistency**: do sources disagree? Compare dates/versions: offer < specification < modification < FAT < SAT < SAV.
5. **Depth**: excerpts are a few hundred characters. **Always open the 1-3 best documents with `read_document`** around the matching page (start_page = page - 1) before answering; use `find_in_document` to find all mentions of a term in a long file, and `list_folder` to see neighbouring documents (later versions, related reports).
6. **Look when it is visual**: `read_document` gives text only. For drawings, layouts, schematics, photos (grippers, products, FAT pictures), tables whose extracted text is garbled, or when the user wants to see something, use `view_page path page` (PDF) - then `region="x0,y0,x1,y1"` (fractions of the page) to zoom on small text such as schematic labels or title blocks. For `.docx`, `view_page path image=N` returns the embedded pictures in document order. It reads the live file (needs the file server); `.doc` files cannot be viewed. Describe what you see and cite the page.

## 4. Retry (max 3 search rounds in total)

If evaluation finds missing parts, weak or off-topic results, or contradictions to resolve, change the approach - do not repeat the same query:

| Problem | Next attempt |
|---|---|
| no or weak results | synonyms, broader term, English/German wording, `mode="semantic"` with a full-sentence description |
| too many generic hits (manuals, schematics, other projects) | add `project=`, `doc_type=` or `section=`; `folder=` for a precise subfolder; `modified_after` |
| filtered search thin | drop `doc_type`/`section` (keep `project`): old projects are often unclassified |
| an exact code/name not found | `mode="keyword"`, partial code with `*`, `find_files` on the name |
| one part of the question unanswered | a dedicated query for that sub-question only |
| answer probably in a specific document type | `doc_type=` (FAT → `fat,reception`, client requirement → `cahier_des_charges`, open issues → `open_points,suivi`, commissioning → `mise_en_service,sat`) |
| question about projects themselves (which, how many, when) | `list_projects` (by name or collection), then search per project |
| "which projects ..." (aggregation) | search results are never exhaustive: after the broad searches, list the candidate projects with `list_projects` and check the remaining likely ones one by one (`project=` filter); say how many projects were checked and that the list may be incomplete |
| contradiction between sources | find the most recent version, look for modification notes or later reports |

Stop when the evaluation is satisfied, or after the third round: then answer with the best evidence and say what is missing.

## 5. Synthesize

- Answer in the language of the question, starting with the direct answer.
- Use **only** what you read in the documents. General engineering knowledge may be added only if clearly labeled as not coming from the documents.
- **Cite every fact**: full path + page, e.g. `\\DMX-FS01.rotzingerag.local\Daten$\RMA_PROJETS\THOR\5_Gestion\56_Acceptation_machine\FAT DEMAUREX 221024 (OPL St Michel).pdf (p. 7)`. Word page numbers are approximate - say "p. ~3".
- When versions disagree, give the most recent one and mention the older value with its source.
- End with a short **Limites / Limitations** line when relevant: what was not found, what is uncertain (medium matches, single source, old document), and which document or person could confirm it.

Answer skeleton:

```
<direct answer, 1-3 sentences>

<details: facts with their source after each one>

Sources : <list of the documents used, path + pages>
Limites : <what is missing or uncertain - omit if nothing>
```

## Opening the original for the user

If the user asks to open, show or see the original file ("ouvre-moi la FAT de THOR page 7"),
call `open_document path page`: it opens the file on their PC in its usual application (PDF
at the page when the viewer allows it, Word read-only). Never use it to read a document
yourself, and do not open files the user did not ask for.

## Example

Question: « Quels problèmes ont été relevés lors de la FAT de THOR, et sont-ils résolus ? »

1. Reformulate: `list_projects name="THOR"` (exact name); `search "problèmes relevés lors de la FAT" project="THOR" doc_type="fat,reception"`; `search "points ouverts OPL réserves" project="THOR"`; `search "levée des réserves mise en service sur site" project="THOR" doc_type="sat,mise_en_service,suivi"`.
2. Retrieve → FAT protocol (type fat) strong; an open-points list (type open_points) medium.
3. Evaluate → FAT problems covered; resolution status not covered yet. Read the FAT protocol pages around the hits.
4. Retry (round 2) → same query without `doc_type`, `list_folder` on the project's report folder → later report found, read it.
5. Synthesize → list of FAT issues with page citations, status of each from the later report, and what remains unconfirmed.

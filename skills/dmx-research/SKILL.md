---
name: dmx-research
description: Research method for the Demaurex company documents behind the DMX connectors - dmx-projects (project folders - offers, specifications, FAT/SAT, manuals, schematics, after-sales; formerly called dmx-docs), dmx-marketing (brochures, datasheets, presentations, competition, exhibitions, picture files) and dmx-documentation (the standard machine manual in FR/DE/EN). Use it for EVERY question that internal documents can answer - projects (THOR, YAKUMA...), machines (Paloma, Presto, Hector...), clients, product claims, how a machine works or is maintained, pictures for a presentation - with whichever of these connectors are enabled, one or several.
---

# DMX research

Never answer from the raw top results of a single search. Run this loop in every world you use:
**route → reformulate → retrieve → evaluate → (retry, max 3 rounds) → synthesize with sources.**

## 0. Which worlds are available

Each connector is one **world**, a separate index of documents with its own audience:

| Connector (tool prefix) | World | Holds | Read before searching it |
|---|---|---|---|
| `dmx-projects` (older name `dmx-docs`) | Projects | one folder per client project: offers, specifications, orders, electrical and mechanical design, FAT/SAT, commissioning, project manuals, after-sales | `references/projects.md` |
| `dmx-marketing` | Marketing | brochures, datasheets, sales and training presentations, competition analyses, exhibitions, publications, picture files (photos, graphics) | `references/marketing.md` |
| `dmx-documentation` | Documentation | the STANDARD machine manual (Paloma, Presto, Hector, Delfi, Astor, Nestor, conveyors, vision, HMI), chapters 0000-7000, in French, German and English | `references/documentation.md` |

- **Look at the tools you actually have.** Their names start with the connector, e.g.
  `dmx-marketing` → its `search`. A connector that is not enabled has no tools: never pretend to
  have searched it.
- **Work with what is enabled.** If the question needs a world whose connector is off, answer
  from the enabled ones and say plainly what is missing and what it would add ("the Marketing
  connector is not enabled: the brochure figures are not checked - enable dmx-marketing for them").
- Every world has `search`, `find_files`, `list_folder`, `read_document`, `find_in_document`,
  `view_page`, `export_image`, `open_document`, `index_status` and `world_map`; Projects also has
  `list_projects` and `project_card`. **The search filters differ per world**: read the world's
  reference file (table above) before your first search in it.
- **Start with the map.** Call `world_map` on each world you are going to search, once per
  conversation, before routing: it says what kinds of documents the world holds, in which folders
  and with which filters, and **what it does not hold and where to look instead**. Use it to pick
  the world, the folder (`folder=`) and the filters; when the map says a kind is missing, say so in
  the answer instead of searching on and on.

## 1. Route the question

| The question is about... | Ask |
|---|---|
| a client project: what was offered, agreed, ordered, built, measured (FAT/SAT), delivered, fixed (SAV); a client, a country, a period | Projects |
| what we say about a product: features, performance claims, datasheet values, references, sales decks, trainings, competitors, exhibitions | Marketing |
| how the standard machine works, safety, operation, HMI screens, maintenance intervals and procedures, spare parts of the standard design | Documentation |
| a project's own, customised manual | Projects (`8_Documentation` of that project), then Documentation for the standard part |
| a photo, schema or screenshot to show or to put on a slide | Marketing first (picture files, brochures, decks), then Documentation, then Projects |
| "promised vs delivered vs standard", a comparison across sources | every relevant world, one after the other |

**Where each kind of information lives** (details and folders: `world_map`):

| Kind | World | Where |
|---|---|---|
| real offers, reference offers for a new one | Projects | `0_Vente\00_Offres`, `doc_type="offre"` + `machine=` |
| offer templates (3-page standard offers) | Marketing | `Brochures\Marketing\Quotations` |
| client specifications, orders, modifications | Projects | `0_Vente`, `doc_type="cahier_des_charges,commande,modification"` |
| third-party equipment (supplier docs and offers) | Projects | `*_Materiel_Tiers` folders, `doc_type="doc_fournisseur"` |
| design and development | Projects | `20_Conception`, `30_Conception`, `Developpement mec` |
| FAT, SAT, commissioning, open points | Projects | `5_Gestion\56_Acceptation_machine`, `6_Rapports_Tests` |
| project closure | Projects | `Clôture`, `57_Cloture`, `section="Cloture"` |
| a project's own manual, software specification | Projects | `8_Documentation`, `4_Soft\42_Documents` |
| standard manual: operation, safety, HMI, maintenance | Documentation | chapters 1000-7000 |
| standard software and electrical documentation | **none yet** | say so; the projects' `4_Soft`, `2_Electrique`, `8_Documentation` hold project versions |
| sales trainings by topic (end-effectors, speed calculation, coflow/counterflow...) | Marketing | `Presentations\Training Sales\<chapter>` |
| brochures, datasheets, exhibitions, competition | Marketing | `category=` |
| photos and graphics for slides | Marketing | `category="Pictures,Graphics"`, `pictures=true` |

**Several worlds:** search each one separately (their filters differ), keep the results apart,
then compare. When they disagree:
- for a given project, the **project documents win** (offer < specification < modification < FAT <
  SAT < SAV, the most recent first);
- Marketing material is promotional: present its figures as claims ("according to the brochure");
- the Documentation manual is the generic reference: a project may differ from it.
Say which world each fact comes from.

## 2. Reformulate

Turn the question into **2-4 search queries** per world before the first search:
- precise **French technical terms** first, then synonyms and the English/German equivalents
  (préhenseur / ventouse / pince - gripper; cadence / débit / produits par minute - throughput;
  FAT / réception usine; SAT / mise en service; dépileur - denester...). Each reference file has
  the vocabulary of its world;
- **exact identifiers in "double quotes"** (order numbers, part numbers, codes) with `mode="keyword"`;
- **one query per part** of a multi-part question;
- the world's **filters** when the scope is known (project, machine, document type, category,
  chapter, language - see the reference file). Filters leave out documents whose facet is unknown:
  if a filtered search is thin, run it again without the filter.

## 3. Retrieve and evaluate

`search` returns excerpts with the full path, the page (or slide), how each matched
(`keyword`, `semantic`, both) and a meaning similarity (**strong** ≥ 0.86, **medium** 0.83-0.86,
**weak** < 0.83 = often off topic). These are hints: judge by reading.

1. **Relevance**: does the excerpt answer the question, or only share words with it?
2. **Coverage**: is every part of the question answered by a relevant source?
3. **Right scope**: right world, project, machine, kind of document (a brochure does not tell what
   a client's machine reached at its FAT; a supplier manual does not tell what was agreed).
4. **Consistency**: compare dates and versions when sources disagree.
5. **Depth**: excerpts are short. **Open the 1-3 best documents with `read_document`** around the
   matching page; use `find_in_document` for every mention of a term in a long file and
   `list_folder` for neighbouring documents (later versions, related reports).
6. **Look when it is visual** (drawings, layouts, schematics, photos, screens, garbled tables):
   `view_page` - see section 5.

## 4. Retry (max 3 search rounds per world)

| Problem | Next attempt |
|---|---|
| no or weak results | synonyms, broader term, another language, `mode="semantic"` with a full-sentence description |
| too many generic hits | add the world's filters, `folder=` for a precise folder, `modified_after=` |
| filtered search thin | drop the filter (old or unclassified documents are left out by it) |
| an exact code or name not found | `mode="keyword"`, partial code with `*`, `find_files` on the name |
| one part of the question unanswered | a dedicated query for that part only, maybe in another world |
| contradiction | find the most recent version, modification notes, later reports |

Then answer with the best evidence and say what is missing.

## 5. Pictures

The pictures of the indexed documents (photos, schemas, screenshots, picture pages of brochures,
picture files) are **described by a vision model**, and each description is a passage of its
document. In search results they are marked `picture N (view_page image=N)`,
`picture N (view_page page=S image=N)` (PowerPoint slide S) or `whole-page picture (view_page page=P)`.

- **To find a picture**, search with `pictures=true`: only picture descriptions are returned
  ("photo Paloma préhenseur ventouses", "capture d'écran recette paramètres").
- **Open it** with `view_page` using exactly the page/image given in the hit, before showing it,
  describing it or quoting anything from it. The descriptions are machine-written: **never quote
  a number, label or code from a description without checking the picture.**
- `read_document` shows the descriptions after the text of each page (`[Image 3 — photo] ...`,
  `[Page entière — schéma] ...`): use them to choose which picture to open.
- **To reuse a picture** (slides, report, email): `export_image` with the same path/page/image and
  a meaningful `name=`; it writes the original picture (or a sharp rendering of a PDF page,
  `region=` to frame part of it) to the user's export folder `C:\dmx-rag\exports` and returns the
  file path. If your workspace cannot see that folder, ask the user to add it. Cite the source
  document and page of each picture.
- For a Demaurex presentation, combine with the `demaurex` skill (corporate template).

Hits marked **(OCR)** come from scanned pages read by OCR: the text may contain recognition errors
- check codes, numbers and names with `view_page` before quoting them.

## 6. Synthesize

- Answer in the language of the question, starting with the direct answer.
- Use **only** what you read. General knowledge may be added only if clearly labelled as such.
- **Cite every fact**: world, full path and page (slide for PowerPoint, "p. ~3" for Word), e.g.
  `Projects: \\DMX-FS01...\THOR\5_Gestion\56_Acceptation_machine\FAT DEMAUREX 221024.pdf (p. 7)`.
- When sources disagree, give the most recent or the most authoritative one (section 1) and
  mention the other with its source.
- End with **Limites / Limitations** when relevant: what was not found, what is uncertain, which
  world was not available.

```
<direct answer, 1-3 sentences>

<details: facts with their source (world, path, page) after each one>

Sources : <documents used, by world>
Limites : <missing or uncertain - omit if nothing>
```

If the user asks to open or see the original file, call `open_document` (it opens the file on
their PC in its usual application); never use it to read a document yourself.

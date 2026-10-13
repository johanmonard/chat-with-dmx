# World maps: what each world holds, where, and what it lacks

Date: 2026-10-13. Status: design approved by the user in conversation; written spec to review.

## Goal

Give Claude, before it searches, an understanding of **what kinds of information each world
holds, where they are, how to reach them, and what is missing** (with where to look instead),
so that it routes questions better:

1. **Routing**: go straight to the right world, folder, category or document type (reference
   offers in the projects' `0_Vente\00_Offres`, sales trainings in Marketing's
   `Presentations\Training Sales\<chapter>`) instead of broad searches with generic hits.
2. **Honest gaps**: say "not in this world, look there" (standard software and electrical
   documentation are not in Documentation yet; each project holds its own in `4_Soft`,
   `2_Electrique`, `8_Documentation`) instead of a thin answer.
3. **Right kind of source**: a brochure claim is not a FAT measurement, an offer template is not
   a real offer.

Out of scope: how well a document ranks once in the right place (ranking, OCR, thesaurus), and
filling the real content gaps.

Today only Projects has such a map (`skills/dmx-research/references/projects.md`: template
folders, sections, doc_types). Marketing and Documentation list their top folders only, nothing
states what a world lacks, and the knowledge lives only in the skill (absent from clients without
it; a change needs a new zip upload).

## What the worlds hold (measured 2026-10-13, local indexes)

- **Projects** (106,745 docs): sections Documentation 42.7k, Gestion 18.7k, Electrique 9.4k,
  Vente 9.2k, Mecanique 8.8k, Rapports_Tests 5.6k, Soft 5.4k, SAV 3.8k, Finances 2.1k, Cloture 69.
  Top doc_types: manuel 22k, doc_fournisseur 13.7k, plan 7.8k, cahier_des_charges 6.4k; offre 880.
  Folders the map must name: third-party equipment (`06_`, `27_`, `37_`, `85_`, `87_`, `89_`,
  `90_Materiel_Tiers`, `Livraison tiers`, `Offres Tiers`), development and design (`20_Conception`,
  `30_Conception`, `Developpement mec`, `Revue de conception`, `Documents développement 2012`),
  closure (`Clôture`, `57_Cloture`, `5. Clôture`, `Clôture de projet`), qualification
  (`IQ OQ reference`), bills of materials (`Nomenclatures`, `Dessins pour nomenclature`).
- **Marketing** (2,911 docs, 1,446 in Presentations): `Presentations\Training Sales` holds 1,287
  files in numbered chapters (010 Généralités, 210 D3 Platform, 220 Demain, 260 Paloma SQ,
  310 End-effectors, 320 GD33, 330 Gemini, 340 Vision, 410 Efficiency, 420 Speed Calculation,
  440 Coflow vs Counterflow, 450 Denesting, 460 UX, 710 PACD agent training); `Brochures\Marketing\Quotations`
  (280: offer templates `offre_3_pages_a_valider` per machine/configuration, old quotations);
  `Exhibition` (CFIA Rennes 2019-2022, IBA Munich 2018); `Pictures` (photo sets per machine);
  `Graphics` (visual identity, website mockups); `Videos` (stills of demo videos per project);
  `Competition` (mostly Cavanna); `Datasheets` (21); `Publications` (52). Folder `CSI` holds no
  indexed file.
- **Documentation** (241 docs, 218 Markdown sections): the standard manual, chapters 1000-7000
  with sub-chapters (1300 module descriptions 48, 6200 maintenance 40, 3100 functional elements 33,
  5100 functional descriptions 29); `7300_Schémas électriques`, `7400_Schéma pneumatique`,
  `7100_Description de l'application`, `7200_Pièces de rechange` hold 3 files each (placeholders
  per language). Standard software and electrical documentation are absent.

## Design

### 1. Map files

One Markdown file per world, written by people, read by the server:

- Location: `maps\<world>.md` next to `config.toml` (shared `U:\DMX-RAG\maps\`), resolved like
  `thesaurus.toml`. A world may override it with `map = '...'` in its `[worlds.<name>]` table
  (relative to `config.toml`). Without worlds (single index): `maps\default.md`.
- Read at each `world_map` call (small file), so an edit is live without restarting `serve`,
  reindexing or uploading a skill.
- Fixed outline (the tool does not parse it, the outline is for writers and readers):

```
# <World> - map
## What this world is for          one paragraph: audience, source share, languages
## Kinds of information            table: kind | what it is | where (folder / category) | filter | answers questions like
## Not in this world - look there  bullets: missing kind -> other world / folder, or "nowhere yet"
## Pitfalls                        versions, old templates, OCR, promotional figures, duplicates
```

- The repository ships the three first drafts in `maps\` at the repo root (copied to
  `U:\DMX-RAG\maps\` at rollout, then edited there). They contain no real client names
  (project code names and folder names are fine, as in the existing references).

### 2. Tool `world_map`

Available in every world (all profiles), read-only, no arguments:

```
world_map() -> str
```

Returns, in this order:

1. The map file's text, or the line "No map file for this world (<path>): only the live overview
   below." when it is missing or unreadable.
2. **Live overview** from the index:
   - world title, number of documents, last full index run (same values as `index_status`);
   - the folder tree under each root to depth 3 (projects: depth 1 = the projects are listed as a
     count, not one by one; see below), each folder with its number of indexed documents,
     folders under 5 documents folded into "+ N smaller folders (M docs)";
   - per profile: Projects = counts per collection, section and doc_type (from `doc_facets`) and
     the number of projects; Marketing = counts per category and file type; Documentation =
     counts per chapter and language; also the picture-description counts by kind when present.
   - Projects' folder tree is aggregated across projects: the template folders (`0_Vente\00_Offres`,
     `37_Materiel_Tiers`...) with the number of projects and documents having each, so the size
     stays small (about 100 lines) despite 540 projects.
3. A closing line: "Counts are from the index; the map text is maintained by hand and may lag
   behind them."

Size cap about 12,000 characters (the tree is cut first, with a note). Computed by one pass over
`docs.path` (+ `doc_facets` for Projects); cached per `serve` process for 10 min.

Code: a new module `src/dmx_docs/worldmap.py` (`load_map_text(cfg)`, `live_overview(store, cfg)`,
`world_map(store, cfg)`); `DocTools.world_map()` calls it; `server.py` registers the tool for every
profile; `config.py` gains `map_path`.

### 3. Server instructions

Each world's instructions get one line near the top: "Call world_map first when you do not know
where a kind of information is kept or whether this world holds it at all; it lists what is here,
where, and what is missing." Instructions are truncated in some clients, so the line goes before
the long search loop.

### 4. Skill

- `SKILL.md`: section 0 gains "Call `world_map` on each world you will search, once per
  conversation, before routing" and a short cross-world table **where each kind of information
  lives** (project offers, third-party docs, design/development, closure, standard manual by
  chapter, sales trainings by topic, brochures/datasheets, photos, software and electrical
  documentation - with the gap) - kept short, the maps hold the details.
- `references/*.md`: the "What is in the index" sections shrink to two lines pointing to
  `world_map`; filters, vocabulary, aggregation and examples stay.
- `tests/test_skill.py` already checks every tool call in the skill against the servers:
  `world_map` must be registered in each world.
- New zip with `scripts/build_skill.py`; the user uploads it.

### 5. Drafting the maps

I draft each map from the index: folder tree and counts, file names per folder, doc_types,
picture kinds, and text samples of the main folders (read with the world's tools). The user
reviews and corrects, especially the gaps and the meaning of folders I cannot infer.

## Routing test (measures the gain)

A test set of 18 questions, 6 per world plus cross-world ones, in
`U:\DMX-RAG\routing-work\testset.json` (outside the repository: it uses real project names).
Each item: question, expected world(s), expected location (path fragments or facet values that a
good answer must cite, any of them), and whether the expected answer includes a stated gap.
Mix: reference offers for a machine type, third-party equipment of a project, design documents,
closure, a sales training topic (coflow/counterflow, speed calculation), datasheet vs FAT figure,
standard maintenance procedure, standard software / electrical documentation (gap), photo for a
slide, a customised project manual.

Run: each question twice, by a research agent with access to the three connectors - **A** with
the current skill and no `world_map` tool, **B** with the new skill and the tool. Measured per run:

| Metric | How |
|---|---|
| reached the expected location | any expected fragment among the cited sources |
| gap stated when expected | answer says the kind is missing and where to look instead |
| wrong kind of source | e.g. brochure figure given as measured value (manual check) |
| search calls until the first expected hit | from the tool-call log |

Result in `routing-work\results-<date>.md`: per question A vs B, totals. The change is kept if B
reaches more expected locations and states more gaps without more search calls on average; if a
question fails in B, its map is corrected and the test re-run (at most twice). A script
`routing-work\score.py` scores the logs; the runs themselves are agent sessions, not CI tests.

## Tests (repository)

- `tests/test_worldmap.py`: map present (text first, then live part), map missing (note + live
  part), `map =` override relative to `config.toml`, folding of small folders, size cap, Projects
  aggregation by template folder on a small fixture index with facets, Marketing/Documentation
  counts per category/chapter and language.
- `tests/test_worlds.py` / `test_profiles.py`: `world_map` registered in every profile; the
  instructions contain the `world_map` line.
- `tests/test_skill.py`: unchanged, passes with the new calls.

## Rollout

1. Merge; machines reinstall on their next launcher run.
2. Copy `maps\*.md` to `U:\DMX-RAG\maps\`; the user reviews and edits them there.
3. Rebuild and upload the skill zip; restart Claude Desktop.
4. Run the routing test (A before the skill upload, or with the old zip) and report.

## Decisions

- Map text in editable files on the shared folder, combined with live counts by a tool (user's
  choice over skill-only references), so it helps every client and stays current.
- Claude drafts the maps, the user reviews (user's choice).
- "Reference offers" = the real offers in each project's `0_Vente\00_Offres` (doc_type `offre`),
  used as a base for new offers (user's answer); Marketing's offer templates are a separate kind.

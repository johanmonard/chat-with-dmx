# Knowledge cards: pre-digested context, starting with one card per project

Date: 2026-10-15. Status: design approved by the user in conversation; written spec to review.

## Goal

Today Claude researches every question from scratch (`search`, `find_files`, `list_folder`,
`read_document`) and nothing found in earlier chats is kept. The long-term vision is a
self-improving knowledge base:

1. **Pre-digested context**: catalogs Claude reads before searching, starting with the projects.
2. **Catalogs for any topic**: every heavy study ends up as a catalog, so the work is never redone.
3. **A save command at the end of a chat** (`/extract-data-to-the-knowledge-database`) that
   extracts what was found and stores it.
4. **Everything linked**: products, clients, machines and studies link to the projects they come
   from, forming a graph rather than separate lists.

The vision is split into sub-projects, each with its own spec, plan and implementation:

| # | Sub-project | Status |
|---|-------------|--------|
| 1 | Knowledge store + one card per project + `catalog` tool + minimal routing | **this spec** |
| 2 | Save command at the end of a chat, writing to a pending area, with review | later |
| 3 | Other kinds of entries (clients, machine types, topic studies) linked to the project cards | later |

Success for sub-project 1: questions like "which projects had a flowpack infeed?", "which lines
use mechanical grippers?" or "what did we build for client X?" are answered from the cards in one
or two calls, with citations, and verified in the documents when it matters.

Out of scope here: writing from Claude Desktop (sub-project 2), entries other than projects
(sub-project 3), search ranking.

## Principles

- **Documents beat everything.** A card is a fast pointer, not the truth: every fact cites its
  source, and where a card, the register and a document disagree, the document wins (as for the
  register today).
- **"Checked, not found" is explicit.** A card says what was looked for and not found, so a
  missing fact is never read as "the project doesn't have it".
- **Coverage is always stated.** Any list built from cards says how many projects have a card, so
  an incomplete catalog is never mistaken for a complete answer.
- **Readable files.** Cards are plain Markdown that the user can read and fix in any editor.

## The card file

One file per project: `knowledge\projects\<PROJECT>.md`, named exactly as `list_projects` names
the project; a sub-project uses a double underscore (`MOUSQUETAIRES__1_Athos.md`).

```markdown
---
type: project
project: THOR
status: unreviewed
written: 2026-10-16
client: ACME Foods
country: FR
years: 2019-2020
machines: [Paloma 4R, Presto]
infeed: [flowpack (chocolate bar 100 g), loose (biscuit)]
outfeed: [wrap-around case (24 bars), tray]
end_effectors: [suction cups]
speed: 120 products/min
read: 6
---
## Summary
Two lines: what the line does, for whom, when.

## A. Client and machines
- Client ACME Foods, Lyon (FR) [register] - see [[client/ACME Foods]]
- Paloma 4R, serial 1234, 2019 [register] [N:\...\THOR\0_Vente\Offre_THOR_v3.pdf p.2] - [[machine/Paloma 4R]]

## B. The line
- Infeed: chocolate bars 100 g in flowpack, 4 formats [...\Cahier des charges.pdf p.5]
- Outfeed: wrap-around cases of 24 [...]
- Speed: 120 products/min [...]
- End effector: suction cups ("ventouses"), 4 per tool [...\FAT_THOR.pdf p.12]
- Layout: ... [...]

## C. History
- Offer v3 2019-03, order 2019-05, FAT 2020-01, SAT 2020-04 [...]
- Issues at SAT: ... [...]
- After-sales: ... [...]

## D. Key documents
- Offer: N:\...\Offre_THOR_v3.pdf
- Specification, FAT, SAT, commissioning, manual: ...

## Checked, not found
- Speed measured at SAT: not in the SAT report (checked FAT and SAT)
```

### Header fields

| Field | Content | Required |
|-------|---------|----------|
| `type` | `project` (other kinds come with sub-project 3) | yes |
| `project` | project name as in the index / register | yes |
| `status` | `unreviewed` or `reviewed` | yes |
| `written` | date the card was written or last refreshed (YYYY-MM-DD) | yes |
| `client`, `country` | from the register, or the documents when they disagree | no |
| `years` | `YYYY` or `YYYY-YYYY` (order to SAT) | no |
| `machines` | list of machine models | no |
| `infeed`, `outfeed` | list of `form (product)`; the form comes from the word list | no |
| `end_effectors` | list from the word list | no |
| `speed` | free text with unit | no |
| `read` | number of documents the card was built from | yes |

The header is a flat `key: value` block; lists are written `[a, b]`. It is parsed by a small
parser in `knowledge.py` (no YAML dependency). Unknown fields are kept and ignored by the tools.

### Body rules

- Sections in this order: Summary, A. Client and machines, B. The line, C. History,
  D. Key documents, Checked, not found.
- Every line of A, B and C carries at least one source: `[register]` or `[<full path> p.<n>]`
  (`slide <n>` for PowerPoint, no page for a one-page or Word source).
- Every header value appears in the body with its source.
- Language: English, like the maps and the tool output; original French terms in quotes where
  they matter ("ventouses", "barquette").
- Links `[[client/<name>]]`, `[[machine/<model>]]`, `[[project/<name>]]` name other entries. In
  sub-project 1 they are names only; a link without a file is allowed.

### Word list

`knowledge\vocabulary.md` holds the allowed values, each with synonyms (French, German, English):

- **forms** (for infeed and outfeed): loose, flowpack, pouch, tray, box, case, wrap-around, ...
- **end effectors**: suction cups, mechanical gripper, needles, ...

Format: one line per value, `- <value>: <synonym>, <synonym>, ...` under a `## forms` or
`## end_effectors` heading. The first version is drafted with the first sample cards and settled
during the sample review.

## Writing the cards

Cards are written by Claude in Claude Code sessions, from the project documents through the
dmx-projects tools plus the register. The procedure and the format are kept in
`knowledge\README.md` on the shared folder (one place, for people and for every session).

For each project:

1. **Start from what is known**: `project_card` (register facts, document counts, newest offer,
   specification, FAT, SAT...).
2. **Read, within a budget** of about 8 documents: offer, specification, FAT/SAT reports,
   commissioning report; targeted `find_in_document` for the end effector ("ventouse",
   "préhenseur", "pince"), speed ("cadence") and the infeed/outfeed terms; `view_page` when the
   text comes from OCR or a drawing.
3. **Write the card** to `\\DMX-FS01\Users$\Johan.Monard\DMX-RAG\knowledge\projects\` with
   `status: unreviewed`.
4. **Check it** with `dmx-docs knowledge check` (below) and fix it before the next project.

Order of work:

- **Sample**: the user picks 10-20 projects they know well (old and new, Paloma, Presto and
  others). Claude writes those cards, the user reviews them, and the word list and the README
  rules are adjusted. Sample cards the user approves get `status: reviewed`.
- **Then the rest**, published directly as `unreviewed`: current projects first, then
  `2_Hors_Garantie` from newest to oldest, about 20 projects per session. The projects still to do
  are the ones without a file.
- The user corrects any card by editing its file (and may set `status: reviewed`).

## Serving the cards

- **Location**: shared master in `U:\DMX-RAG\knowledge\`. `dmx.ps1 pull` copies it (all `*.md`,
  subfolders included) to `C:\dmx-rag\knowledge\`, like the maps. Config: `knowledge\` next to
  config.toml by default, or `[worlds.<name>] knowledge = <path>`. Only the projects world serves
  project cards for now.
- **`knowledge.py`** loads the cards and the word list and keeps them in memory; on each call it
  re-reads only the files whose modification time changed, so an edited card shows up without
  restarting Claude Desktop.
- **`project_card(project)`**: unchanged output, then the written card under it -
  "Written card (unreviewed, 2026-10-16, built from 6 documents):" and the card body; or
  "No written card yet."
- **New tool `catalog`** (projects world): filters `infeed`, `outfeed`, `end_effector`,
  `machine`, `client`, `country`, `year_from`, `year_to`, `status`, `text` (free text in the card).
  `infeed`/`outfeed`/`end_effector` match the word list value or any of its synonyms
  ("sachet" -> pouch, "ventouse" -> suction cups); the other filters are case-insensitive
  substring matches (`year_*` on `years`). One line per card:
  `project · client · years · machines · infeed -> outfeed · end effector · speed · status`.
  The last line always gives coverage: "Cards exist for 34 of 540 projects: for a complete
  answer, also use search / list_projects." The tool is named `catalog`, not `project_catalog`,
  so that sub-project 3 can add `kind=` without a new tool.
- **Routing (minimal)**: the projects server instructions, `maps\projects.md` and the
  `dmx-research` skill (`references/projects.md`) get one rule: for "which projects..." and "what
  did we build for..." questions, call `catalog` / `project_card` first, then verify in the
  documents. New skill zip built with `scripts/build_skill.py`.

## `dmx-docs knowledge check`

A CLI command run by the card-writing sessions (and by the user at will). It reports, per card:

- header missing or unreadable, required field missing, `status` not `unreviewed`/`reviewed`;
- a line in A, B or C without a source;
- a header value that does not appear in the body;
- a form or end effector not in the word list;
- a cited path not in the index;
- a project not in the index nor the register;
- **stale**: the card's `written` date is older than the newest indexed document of its project
  (the card should be refreshed).

Exit code 0 when no card has an error (stale is a warning).

## Failures

- An unreadable card (broken header, bad field) is skipped by the tools, which say
  "card THOR.md unreadable: <reason>"; `check` lists it. One bad card never breaks a tool.
- No knowledge folder on the machine: `catalog` answers "No knowledge cards on this machine
  (run pull)"; `project_card` works as today.
- A card whose project no longer exists is still served; `check` flags it.
- Values outside the word list are accepted when serving (tolerant) but rejected by `check`
  (strict for the writing sessions).

## Testing

With fictional names (ACME...), as in the existing tests:

- `knowledge.py`: header parsing, lists, reload on change, synonyms, unreadable card, missing
  folder.
- `catalog` and `project_card` against a small test index: each filter, coverage line, card
  appended / "No written card yet".
- `check`: one test per rule, stale warning, exit code.
- `test_skill.py` already checks that every tool the skill shows exists, so it covers `catalog`.
- Real run: 2-3 sample cards on the laptop, then a "which projects..." question in Claude Desktop.

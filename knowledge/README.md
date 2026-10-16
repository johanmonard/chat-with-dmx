# Knowledge cards

Pre-digested context for Claude: one card per project, read through `project_card` (the card is
shown under the register facts) and `catalog` (one line per card, with filters). Cards are
written by Claude in Claude Code sessions from the project documents and the machine register.
A card is a fast pointer, not the truth: every fact cites its source, and **where a card and a
document disagree, the document wins**.

Layout of this folder (shared master `U:\DMX-RAG\knowledge\`, mirrored to each machine by
`dmx.ps1 pull`):

- `projects\<PROJECT>.md` - one card per project, named exactly as `list_projects` names it,
  with `project: <PROJECT>`. A sub-project's card is `<PARENT>__<SUB>.md` with
  `project: <SUB>` (`MOUSQUETAIRES__1_Athos.md`, `project: 1_Athos`); `check` refuses a file
  name that does not match `project:`. A project's card is found by its file name first, so
  `ORION.md` wins over a sub-project card `PACIFIC__ORION.md`.
- `vocabulary.md` - the word list: allowed infeed/outfeed forms and end effectors, with synonyms.
- `README.md` - this file: the format and the procedure.

## Example card

```markdown
---
type: project
project: THOR
status: unreviewed
written: 2026-10-16
client: ACME Foods
country: France
years: 2019-2020
machines: [Paloma 4R, Presto]
infeed: [flowpack (chocolate bar 100 g, 4 formats), loose (biscuit)]
outfeed: [wrap-around case (24 bars), tray]
end_effectors: [suction cups]
speed: 120 products/min
read: 6
---
## Summary
Paloma 4R line packing chocolate bars in flowpack and loose biscuits into wrap-around cases and
trays for ACME Foods (France), ordered 2019, accepted on site 2020.

## A. Client and machines
- Client ACME Foods, Lyon, France [register] - [[client/ACME Foods]]
- Paloma 4R, serial 1234, ordered 2019 [register] [N:\THOR\0_Vente\00_Offres\Offre THOR v3.pdf p.2] - [[machine/Paloma 4R]]
- Presto for the trays [N:\THOR\0_Vente\00_Offres\Offre THOR v3.pdf p.3] - [[machine/Presto]]

## B. The line
- Infeed: chocolate bars 100 g in flowpack, 4 formats [N:\THOR\0_Vente\Cahier des charges THOR.pdf p.5]
- Infeed: loose biscuits on a second belt [N:\THOR\0_Vente\Cahier des charges THOR.pdf p.6]
- Outfeed: wrap-around cases of 24 bars; trays of biscuits [N:\THOR\0_Vente\Cahier des charges THOR.pdf p.7]
- Speed: 120 products/min [N:\THOR\0_Vente\00_Offres\Offre THOR v3.pdf p.4]
- End effector: suction cups ("ventouses"), 4 per tool [N:\THOR\5_Gestion\FAT THOR.pdf p.12]
- Layout: two Paloma cells in line, Presto at the end [N:\THOR\0_Vente\00_Offres\Offre THOR v3.pdf p.5]

## C. History
- Project 2019-2020, from the offer to the SAT [N:\THOR\0_Vente\00_Offres\Offre THOR v3.pdf p.1] [N:\THOR\5_Gestion\SAT THOR.pdf p.1]
- Offer v3 2019-03, order 2019-05 [N:\THOR\0_Vente\00_Offres\Offre THOR v3.pdf p.1]
- FAT 2020-01: 3 open points, closed before shipping [N:\THOR\5_Gestion\FAT THOR.pdf p.2]
- SAT 2020-04 [N:\THOR\5_Gestion\SAT THOR.pdf p.1]

## D. Key documents
- Offer: N:\THOR\0_Vente\00_Offres\Offre THOR v3.pdf
- Specification: N:\THOR\0_Vente\Cahier des charges THOR.pdf
- FAT: N:\THOR\5_Gestion\FAT THOR.pdf
- SAT: N:\THOR\5_Gestion\SAT THOR.pdf

## Checked, not found
- Speed measured at SAT: not in the SAT report (checked FAT and SAT)
- After-sales history: no 9_SAV folder
```

## Header fields

| Field | Content | Required |
|-------|---------|----------|
| `type` | `project` | yes |
| `project` | project name as in `list_projects` / the register; for a sub-project card, the sub-project name | yes |
| `status` | `unreviewed` (written by Claude) or `reviewed` (checked by a person) | yes |
| `written` | date the card was written or last refreshed, YYYY-MM-DD | yes |
| `client`, `country` | from the register, or the documents when they disagree | no |
| `years` | `YYYY` or `YYYY-YYYY` (order to SAT) | no |
| `machines` | list of machine models | no |
| `infeed`, `outfeed` | list of `form (product)`: what goes in, what comes out; the form comes from `vocabulary.md` | no |
| `end_effectors` | list from `vocabulary.md` | no |
| `speed` | free text with unit | no |
| `read` | number of documents the card was built from | yes |

Lists are written `[a, b]`; commas inside parentheses stay in their item. Leave a field out
rather than guess: the "Checked, not found" section says what was looked for.

## Body rules

- Sections in this order: Summary, A. Client and machines, B. The line, C. History,
  D. Key documents, Checked, not found.
- Every `- ` line of A, B and C carries at least one source: `[register]` or
  `[<full path> p.<n>]` (`slide <n>` for PowerPoint; no page for a Word file). Use the full path
  as the tools show it (N:\... or \\server\...), never an abbreviation.
- Every header value appears in the body, with its source (for infeed, outfeed and end effectors
  the form or one of its synonyms).
- English, with original French terms in quotes where they matter ("ventouses", "barquette").
- Links: `[[client/<name>]]`, `[[machine/<model>]]`, `[[project/<name>]]`. A link without a file
  is allowed (client and machine entries come later).
- "Checked, not found" is required when something was looked for and not found, so that a
  missing fact is never read as "the project doesn't have it".

## Writing a card (procedure for Claude Code sessions)

1. **Start from what is known**: `project_card project="<PROJECT>"` (register facts, document
   counts, the newest offer, specification, FAT, SAT, commissioning report).
2. **Read, within a budget of about 8 documents**: offer, specification, FAT/SAT reports,
   commissioning report. Use `find_in_document` for targeted lookups: the end effector
   ("ventouse", "préhenseur", "pince"), the speed ("cadence"), the infeed and outfeed terms
   ("flowpack", "vrac", "barquette", "carton", "caisse"). Use `view_page` when the text comes
   from OCR or a drawing.
3. **Write the card** to `\\DMX-FS01\Users$\Johan.Monard\DMX-RAG\knowledge\projects\<PROJECT>.md`
   with `status: unreviewed`.
4. **Check it** on the shared folder, with the shared config (its data_dir is the local index,
   its knowledge folder is the shared one):

   `C:\dmx-rag\venv\Scripts\python.exe -m dmx_docs.cli --config \\DMX-FS01\Users$\Johan.Monard\DMX-RAG\config.toml --world projects knowledge check`

   Fix every error before the next project. It fails when it finds no card at all (wrong
   folder). A warning "older than the newest document" means the card should be refreshed.

## Order of work

1. **Sample**: 10-20 projects the user knows well (old and new, Paloma, Presto and others). The
   user reviews them; the word list and these rules are adjusted. Approved cards get
   `status: reviewed`.
2. **Then the rest**, published directly as `unreviewed`: current projects first, then
   `2_Hors_Garantie` from newest to oldest, about 20 projects per session. The projects still to
   do are the ones without a file.
3. Anyone may correct a card by editing its file (and set `status: reviewed`).

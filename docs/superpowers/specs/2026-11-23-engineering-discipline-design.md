# Engineering world: a discipline label per document instead of configured groups

Date: 2026-11-23. Status: written from the chat of that day, waiting for the user's approval.
Changes `2026-11-21-engineering-world-design.md` (merged at 1217758, nothing indexed yet).

## Intent

The three groups (Mechanical, Electrical, Software) were folder lists written by the user. The
server's folders do not follow the three disciplines (`4_Shared` is mixed), so the user dropped
them. The world is one list of folders, and every document gets a **discipline** decided during
indexing: `Mechanical`, `Electrical`, `Software`, `Mixed` (several disciplines clearly present)
or `Unknown` (nothing points either way).

## Goals

1. `search(category="Mechanical")` filters by discipline (`category` is kept as the filter name:
   no server signature changes); every hit shows `discipline <D> · section <S>`.
2. The discipline comes, in this order, from: (a) a **folder rule written by the user**;
   (b) **keyword scoring** of the folder names, file name, title and the start of the text;
   (c) otherwise `Unknown`. No model pass in this version.
3. The user can check the result before trusting it: a report file lists every document with its
   label, scores, top terms and which rule decided.
4. Editing the rules or the word lists recomputes the labels on the next index run, without
   re-extracting any document.

## Non-goals

* A model (gemma4) pass for undecided documents: added later only if the spot-check of the
  keyword result disappoints.
* Several labels per document, or a score exposed to Claude (Mixed covers it).
* Changing any other world.
* Folder groups: removed.

## Design

### 1. Configuration

The world table is an ordinary one again: `roots = [...]` (here the Mechanical folder
`\\DMX-FS01.rotzingerag.local\Daten$\RMA_ORG\END\2_Groupes\22_Mecanique`), no `[...groups]`
table. The `groups` code of the previous design (`Config.groups`, `_read_groups`,
`sources.GroupError` / `sync_group_roots`, the group fingerprint, the engineering clause of
`facets.compute_engineering`) is removed; `roots` is accepted for this profile like for Marketing.

Rules live in a file next to `config.toml`: `disciplines.toml` (shared folder
`U:\DMX-RAG\disciplines.toml`, copied to `C:\dmx-rag\disciplines.toml` by the pull like
`thesaurus.toml`; `[worlds.engineering] disciplines = "..."` overrides the path):

```toml
# Folder rules written by the user: first match wins; path pattern with / as separator,
# case-insensitive, * wildcard, matched against the document's full path.
[[folder]]
pattern = "*/Doc_Tech/Soft/*"
discipline = "Software"

[words.Mechanical]          # French and English words, case and accent insensitive
strong = ["préhenseur", "pneumatique", "tôlerie", "usinage", "convoyeur", "gripper", "vérin"]
weak   = ["montage", "vis", "plan", "assembly"]
[words.Electrical]
strong = ["schéma électrique", "automate", "variateur", "câblage", "eplan", "wiring", "plc"]
weak   = ["tension", "relais"]
[words.Software]
strong = ["twincat", "gemini", "recette", "firmware", "api", "git", "code source", "hmi"]
weak   = ["version", "paramètre", "log"]

[scoring]
min_score = 6        # below: Unknown
mixed_share = 0.30   # a second discipline with at least this share of the total, with the top
                     # one below 0.70: Mixed
```

The word lists above are examples only; the builder seeds fuller lists from the French and
English vocabulary of the indexed Mechanical documents and the user reviews them. The folder
rules start empty: the user writes them.

### 2. Scoring

For each document, score per discipline = sum over its words of `weight × count`, with
`strong = 3`, `weak = 1`, counts capped at 5 per word so one repeated term cannot dominate.
Text scored = path below the root + file name + title (counted at weight ×3) + the first three
chunks of the document. Words match on whole words after lowercasing and removing accents;
multi-word entries match as phrases.

Decision: a matching folder rule wins (`source = rule`). Otherwise `total < min_score` ->
`Unknown`; top share `>= 0.70` -> that discipline; else the top two, if the second has share
`>= mixed_share` -> `Mixed`; else the top one. Thresholds are the user's to tune in the file.

### 3. Where it runs

After `facets.refresh`, `run_index` calls `disciplines.refresh(con, cfg)` for the `engineering`
profile: it reads the chunks of documents without a label (or whose rules fingerprint changed),
writes `doc_facets.category = <discipline>` and, in a small table `doc_discipline(doc_id,
discipline, source, scores, top_terms)`, what decided. The fingerprint (a hash of the rules file
content) is stored in `meta`; a changed file relabels every document of the world from the
stored chunks, with no extraction. `section` stays the first folder below the root.

The report `engineering-classification.csv` (next to the index, copied to the shared folder by
the push like `review.csv` of the offers world) has one row per document: path, section,
discipline, source (rule / words), the three scores, top terms.

### 4. Tools and text

`ENGINEERING_INSTRUCTIONS`, the `engineering_search` docstring, the skill page
`references/engineering.md` and `maps\engineering.md` say "discipline" instead of "group", say
that the label is automatic and approximate (a hit's label is a hint, not a fact), and that
`Mixed` and `Unknown` exist. The hit line is `discipline <D> · section <S>`.

## Constraints

* `EXTRACT_VERSION` stays 2; the other worlds are untouched.
* The shared config and `U:\DMX-RAG\app` currently hold the groups version: they are updated
  together (backup first). Nothing is indexed with the old version, so no migration is needed.
* Tests use made-up text and folder names.

## Approaches considered

* Rules and keywords only; a model reading each document; both together (chosen by the user:
  rules and keywords first, the model pass added later only for the documents the first stage
  cannot decide, if the spot-check calls for it).

## Acceptance

Index the Mechanical folder, read `engineering-classification.csv` with the user: a sample of
about 40 documents spread over sections, count of Unknown and Mixed, and the word lists adjusted
until the user is satisfied. Only then the world is used.

## Left to the builder

* The exact word lists and their first seeding.
* Reading the first three chunks efficiently for ~2,000 documents.
* The `doc_discipline` table's migration for an older database (create if missing).

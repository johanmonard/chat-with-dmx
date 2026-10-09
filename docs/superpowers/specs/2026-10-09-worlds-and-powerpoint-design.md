# Worlds (separate indexes per document family) and PowerPoint support

Date: 2026-10-09. Status: design approved in chat, spec under review.

## Goal

Today dmx-docs has one index: the project documentation (N:\RMA_PROJETS). The company also
wants to search other, unrelated document families ("worlds"), each shared with its own
audience, and still be able to study all of them together:

* **Projects**: the current index.
* **Marketing**: `O:\SAL\2_Groupes\22_Marketing` (= `\\DMX-FS01.rotzingerag.local\Daten$\RMA_ORG\SAL\2_Groupes\22_Marketing`),
  the first new world.
* **Documentation** (machine documentation): later, same mechanism.

Isolation must be real, not a filter: the MCP server runs on each user's PC against a local
copy of the index, so whoever has an index file can read all of it. Each world therefore gets
its own index file in its own folder, and access is granted later by folder permissions
(AD groups) on the share. Cross-world search works because every world uses the same
embedding model.

This spec covers **sub-project A** only. The whole effort is split in three:

| | Scope | Result |
|---|---|---|
| **A (this spec)** | Per-world layout, config, profiles, scripts, migration of Projects; PowerPoint (.pptx, .ppt) | Marketing text can be indexed and embedded on DMX-WS032 and tried in Claude Desktop |
| B | Picture descriptions: a `describe` step with a local vision model (Ollama + Qwen2.5-VL 7B on the A4000) turning photos, slide/brochure pictures and later scanned PDFs into searchable text | Pictures become findable |
| C | One MCP server over every world the user can read (merged ranking, results tagged by world), one skill per world plus a cross-world skill | Cross-world studies |

## Shared folder layout

```
U:\DMX-RAG\
  app\  tools\  models\  logs\  skills\    shared by all worlds (code, uv, embedding model, run logs, skill zips)
  config.toml                              settings for all machines + one [worlds.<name>] table per world
  thesaurus.toml                           shared vocabulary (useful to every world)
  worlds\
    projects\     index.sqlite3, index.prev.sqlite3, LOCK, register.csv
    marketing\    index.sqlite3, index.prev.sqlite3, LOCK
```

Local copy on each machine (`C:\dmx-rag`):

```
C:\dmx-rag\
  venv\  python\  uv-cache\  exports\      unchanged
  config.toml  thesaurus.toml              copies of the shared files
  data\
    models\                                unchanged (embedding model, shared by all worlds)
    projects\     index.sqlite3, vec_ids.*.npy, vec_mat.*.npy, logs\, register.csv
    marketing\    index.sqlite3, vec_*.npy, logs\
```

Later, access control = NTFS permissions on `worlds\<name>` (and moving `U:\DMX-RAG` from the
user's personal share to a team share). Not part of A.

## Configuration

`config.toml` keeps its current sections (`[index]`, `[embeddings]`, `[server]`, `[search]`),
which apply to every world. New tables, one per world:

```toml
[worlds.projects]
title = "Projects"
profile = "projects"        # facet rules and tools of the project documentation
# extensions default to [index] extensions ('.pdf', '.docx', '.doc')

[worlds.marketing]
title = "Marketing"
profile = "marketing"
extensions = ['.pdf', '.docx', '.doc', '.pptx', '.ppt']
# seed only (afterwards the configuration page manages them); UNC, not O:, because the
# drive letter may not be mapped on the VM
roots = ['\\DMX-FS01.rotzingerag.local\Daten$\RMA_ORG\SAL\2_Groupes\22_Marketing']
```

A world table may override `extensions`, `exclude` and `roots` (seed). The `[embeddings]`
model is global on purpose and cannot be overridden per world (vectors of all worlds must be
comparable for cross-world search in C).

**Selecting the world.** `dmx-docs --config ... --world <name> <command>`; default: the
environment variable `DMX_DOCS_WORLD`, else `projects` when the config has
`[worlds.projects]`. `load_config(path, world=None)` applies the same default, so the
existing Claude Desktop entry on the laptop (`--config C:\dmx-rag\config.toml serve`) and the
helper scripts keep working unchanged. An unknown world name is an error listing the
configured worlds.

**Paths of a world.** With a world `w`: `db_path = data_dir\w\index.sqlite3`; vector caches
and logs in `data_dir\w\`; `register_path` defaults to `data_dir\w\register.csv`;
`models_dir` stays `data_dir\models`. A config **without** `[worlds]` keeps today's
single-index behaviour (`data_dir\index.sqlite3`), so tests and other installations are not
affected.

Root folders, excluded folders and patterns are already stored inside each index database
(`sources.py`), so every world naturally has its own; the seed comes from the world table.

## Profiles

A world's `profile` decides its facet rules, the MCP tools offered and the server
instructions. `Config` gains `world`, `world_title` and `profile` (default `"projects"` in
single-index mode).

* **projects**: exactly today's behaviour: project / sub-project / collection / section /
  doc_type facets from the folder templates, machine types per project (`machines.py`), the
  machine register (`register.py`), tools `list_projects` and `project_card`, the current
  `INSTRUCTIONS` text.
* **marketing**: one facet, `category` = the first folder below the root (Brochures,
  Competition, CSI, Datasheets, Exhibition, Graphics, Pictures, Presentations, Publications,
  Videos), stored in a new `doc_facets.category` column. No machine-type refresh, no register,
  no `list_projects` / `project_card`. `search` offers `category=` instead of
  project/section/doc_type/collection/machine/client/country. Short instructions: what the
  world contains (brochures, presentations, datasheets, exhibitions, competition, publications;
  languages), the same reformulate / retrieve / evaluate / retry / cite loop as Projects,
  and `list_folder` to browse categories.
* **none**: no facets (default for a new world until it gets its own rules).

Facet computation dispatches on the profile; the stored `facets_version` becomes
`"<profile>:<version>"` so a profile change or rule change recomputes them. `DocTools._con`
calls `machines`/`register` refreshes only for `projects`. `index_status` names the world.

The server name stays `dmx-docs` for Projects; other worlds are served as `dmx-<world>`
(e.g. `dmx-marketing`), so until C the user can add Marketing to Claude Desktop as a second
entry: `--config C:\dmx-rag\config.toml --world marketing serve`.

## PowerPoint

New dependency: `python-pptx`.

* **.pptx extraction** (`extract_pptx`): one slide = one page (`n_pages` = slide count).
  Per slide, in shape order: title, text of every shape including grouped shapes and
  placeholders, tables (one row per line, cells joined with ` | `), then the speaker notes
  prefixed `Notes:`. Hidden slides are included. SmartArt and chart text are out of scope
  for now. Text goes through `clean_text` and the normal chunking.
* **.ppt** (PowerPoint 97-2003): converted to .pptx in a temporary folder, then extracted
  as above. Converter follows the existing `doc_converter` setting: `auto` = LibreOffice if
  installed, else PowerPoint via COM. PowerPoint conversion uses the same hang-proofing as
  Word: time limit per file (2 min), limited parallel conversions, stall watchdog, and the
  instance is killed on a hang. If the user already has PowerPoint open on that machine, the
  converter never quits it (it only closes the presentation it opened). Without any
  converter, .ppt files are recorded as skipped with a clear reason (like .doc today).
* **Reading**: `read_document` / `find_in_document` work per slide; the page label for
  .pptx/.ppt is "slide".
* **Pictures**: `view_page` and `export_image` on a .pptx/.ppt return the pictures of a
  slide: `page` = slide number, `image` = n-th picture on that slide (shape order, groups
  included), original resolution for export, scaled like docx pictures for viewing. A slide
  without pictures gives an error listing the slides that have some. Whole slides are not
  rendered as images (would need PowerPoint/LibreOffice at question time). For .ppt, the
  file is converted on the fly first.

## Scripts (`scripts\dmx.ps1` and launchers)

* `dmx.ps1 <command> [-World <name>]`. Without `-World`, the script asks
  (`World (projects, marketing) [projects]:`, the list read from `[worlds.*]` in the shared
  config; Enter = projects). Unknown names are refused.
* Everything that was global becomes per world: master `worlds\<w>\index.sqlite3`, previous
  copy, `LOCK`, local copy `C:\dmx-rag\data\<w>\`, the Python calls get `--world <w>`, log
  names `<date>_<machine>_<world>_<command>.log`. Two machines can therefore work on two
  different worlds at the same time; one world is still locked to one machine at a time.
* `Stop-LocalServers` only stops the Claude Desktop servers of the world being replaced.
* `pull` copies config, thesaurus and the world's `register.csv` (if any), warms that world's
  search cache and prints the Claude Desktop entry for that world.
* New command **`update`**: index then embed under one lock (one copy down, one copy back).
  Sub-project B will insert `describe` between the two.
* Launchers in `U:\DMX-RAG` (sources in `app\scripts\launchers`, copied to the share root):
  1 Setup (no world), 2 Configuration page, 3 Index, 4 Embeddings, 5 Update Claude Desktop
  copy, 6 Import machine register (writes `worlds\projects\register.csv`), Unlock after a
  crash: each passes its arguments through, so `-World marketing` works and otherwise the
  script asks. New **`7 - Update a world (index + embeddings).cmd`**: the unattended VM
  script, e.g. `7 - Update a world (index + embeddings).cmd marketing`.

### One-time migration (automatic, in dmx.ps1)

Runs at the start of every command, does nothing once done; renames only, no copies.

* **Share**: if `data\index.sqlite3` exists and `worlds\projects\index.sqlite3` does not:
  refuse if `data\LOCK` exists (a run of the old layout is in progress: finish it first);
  otherwise move `data\index.sqlite3`, `data\index.prev.sqlite3` and `register.csv` into
  `worlds\projects\`, then remove the empty `data\`.
* **Machine**: if `C:\dmx-rag\data\index.sqlite3` exists and `data\projects\index.sqlite3`
  does not: stop the local Claude Desktop server, move `index.sqlite3` (and `-wal`/`-shm`),
  `vec_*.npy` and `logs\` into `data\projects\`, and `C:\dmx-rag\register.csv` to
  `data\projects\register.csv`. If a file stays in use: stop with "quit Claude Desktop
  completely and run this again", nothing half-moved.
* The shared `config.toml` gets the `[worlds.projects]` and `[worlds.marketing]` tables
  (edited once by hand during rollout, backup kept).

### Helper scripts

Default paths updated: `register-work\import_register.py` writes
`worlds\projects\register.csv`; `thesaurus-work\build_draft.py` and `verify_testset.py`
default to `C:\dmx-rag\data\projects\index.sqlite3`; the scripts that call `load_config`
need no change (default world). README: shared-folder section, worlds, launcher 7.

## Error handling

* World given but missing from config: error listing the configured worlds (CLI and dmx.ps1).
* World index not on the share yet (first run of a new world): `index` / `web` / `update`
  start a new one, as today for an empty share; `pull` says there is nothing to copy yet.
* Corrupt or unreadable .pptx/.ppt: recorded as error for that file, the run continues
  (existing behaviour of `extract_file`).
* Migration refused while an old-layout lock exists; never partially applied.

## Tests

* Config: world selection (flag, env var, default `projects`), unknown world error, per-world
  paths, per-world extension override, single-index mode unchanged.
* Facets: marketing `category` from the first folder; profile switch recomputes facets;
  projects facets unchanged (existing tests).
* Server: projects tool list unchanged; marketing has no `list_projects`/`project_card` and
  offers `category=`.
* PowerPoint: .pptx generated in the test with python-pptx (title, text box, group, table,
  notes, picture): text per slide, slide label, `view_page` picture by slide/image, slide
  without picture error. `.ppt` without converter: skipped with reason.
* Existing suite passes.
* Manual rollout check: migration on the laptop then `status -World projects` (same numbers
  as before) and Claude Desktop still answering; then `7 - Update a world ... marketing` on
  DMX-WS032, `status -World marketing`, a few searches, and a Claude Desktop test with the
  `dmx-marketing` entry.

## Out of scope (later)

Picture descriptions and OCR (B); multi-world server and skills (C); Documentation world and
its facet rules; Excel files; indexing .pptx in Projects (possible by adding the extension to
`[worlds.projects]`); folder permissions / team share.

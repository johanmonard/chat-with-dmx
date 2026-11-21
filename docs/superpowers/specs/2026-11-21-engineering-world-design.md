# Engineering world, with Excel and OneNote readers

Date: 2026-11-21. Status: approved by the user in chat on 2026-11-21.

## Intent

A fifth world, **Engineering**, next to Projects, Offers, Marketing and Documentation: the working
knowledge of the engineering groups (methods, calculation tools, templates, reference documents,
notebooks handed over by engineers), so that it can be searched from Claude like the other worlds.

It has three groups: **Mechanical**, **Electrical**, **Software**. They are one world, not three,
because the audience is the same and questions cross the groups; the group is a filter inside the
world.

The first version covers Mechanical only, with one folder:
`O:\END\2_Groupes\22_Mecanique` = `\\DMX-FS01.rotzingerag.local\Daten$\RMA_ORG\END\2_Groupes\22_Mecanique`.

Much of what matters in that folder is in file types no world reads today, so this work also adds
readers for **Excel** and **OneNote** and switches on PowerPoint, mails, Markdown, text and picture
files for this world.

## What is in the folder (measured 2026-11-21)

3,104 files, 20.9 GB, in 11 numbered folders (`220 - Administratif` ... `230 - Documents de
references`). `230` holds 2,070 files and `229 - Archives` 753.

| Kind | Files | In the first version |
|---|---|---|
| PDF | 219 | yes (existing reader, OCR) |
| Word .docx / .doc | 115 / 107 | yes (existing) |
| Markdown | 131 | yes (existing) |
| PowerPoint .pptx / .ppt | 20 / 8 | yes (existing, slide by slide) |
| Outlook .msg | 8 | yes (existing) |
| Pictures .png / .jpg / .gif | 577 / 439 / 7 | yes (existing; described by the vision step) |
| Excel .xlsx / .xlsm / .xls | 109 / 17 / 32 | yes, **new reader** |
| OneNote .one | 86 (18 of them in `OneNote_RecycleBin`, left out) | yes, **new reader** |
| Text .txt | 41 | yes, **new reader** (small) |
| SolidWorks, STEP, DWG | about 650 | no |
| Video, zip, exe, other | about 400 | no |

Three Excel files are over 10 MB (105 MB, 28 MB, 12 MB). The OneNote files total about 560 MB,
the largest 208 MB.

## Goals

1. A world `engineering` that is indexed, embedded and served like the others (`dmx-engineering`).
2. Every hit shows its **group** and its **section** (the first folder below the group folder, for
   example `224 - Pneumatique`); both can be used as search filters.
3. Which folders belong to which group is **written by the user in the configuration**. Nothing
   derives a group from a folder name.
4. Excel workbooks are searchable sheet by sheet.
5. OneNote sections are searchable page by page, with page title and dates, including the text
   OneNote has recognised inside pictures.
6. The full run is launched by the user on the indexing VM, as for the other worlds. Nothing is
   indexed from the laptop.

## Non-goals (first version)

* Electrical and Software folders. They are added to the configuration when the user names them;
  no code change is needed for that.
* SolidWorks, CAD, video, archives (.zip), OneNote table-of-contents files (.onetoc2).
* Showing an Excel sheet or a OneNote page as an image (`view_page`, `export_image`). The original
  is opened with `open_document`.
* Describing the pictures inside OneNote pages or Excel workbooks with the vision model. OneNote's
  own picture text is taken; pictures without text in them stay unsearchable for now.
* Reading files attached inside OneNote pages (only their names are kept).
* Excel in the other worlds. The reader is general, but each world decides its own file types and
  no other world's list changes here.
* Knowledge cards, registers or any Engineering-specific tool beyond search and reading.

## Anti-goals

* A file on the server is changed by indexing. OneNote writes into every notebook it opens
  (measured: new modification date, a new `Open Notebook.onetoc2`), so the OneNote reader must
  never open an original.
* A folder ends up in a group, or in the index, that the user did not list.
* The run hangs or dies on one bad workbook or notebook.
* The other worlds re-extract their documents because of this change.

## Constraints

* Same deployment as the other worlds: code in the repository, installed from `U:\DMX-RAG\app`;
  shared `config.toml`; index built on the VM under the world's LOCK; SQLite never on `U:`.
* `EXTRACT_VERSION` stays 2. Adding readers for new extensions does not change the text of any
  document already indexed; bumping it would re-extract and re-embed about 180,000 documents.
* OneNote can only be read where OneNote desktop (Office) is installed. On a machine without it,
  `.one` files get status `skipped` with a clear reason and are picked up later by
  `index --retry-errors`, exactly like `.doc` files without a converter today.
* Tests use made-up names and content, never real client, project or people names.
* Windows paths in files are written with the Write tool, not through a shell heredoc.

## Approaches considered

### One world or three

* **One world with three groups (chosen by the user).** Same audience, one connector, one map, one
  index run; cross-group questions in one search.
* Three worlds. Only useful if a group had to be hidden from some engineers or needed its own
  reading rules. Neither applies.

### Excel

* **Read the cells directly (chosen).** One sheet = one page, each row one line of text. No Excel
  needed on the VM, fast, good for search.
* Convert to PDF with Excel, then read the PDF. Keeps the look, but wide tables split across pages
  and rows break; slow; worse search.

### OneNote

Tested on 2026-11-21 on copies of two real sections.

* **A. Ask OneNote itself for each page (chosen).** 3 and 5 pages returned with title, created and
  modified date, clean text in reading order, tables, and OneNote's own picture text (29,500
  characters in one section, more than its typed text). 3 to 5 s per section after about 5 s of
  loading. Needs OneNote desktop on the VM.
* B. Read the `.one` file with a Python library (pyOneNote 0.0.2). Rejected: text comes back as hex
  strings and wrongly decoded bytes, 288,000 characters where the real text is 8,600; every old
  version of every paragraph is mixed in; no pages, no picture text.
* C. Export each section to PDF once and index the PDFs. Rejected: hits point to a copy, pages no
  longer match OneNote pages, and the shared team notebook goes stale.

## Design

### 1. The world and its groups

Shared `config.toml`:

```toml
[worlds.engineering]
title = "Engineering"
profile = "engineering"
extensions = ['.pdf', '.docx', '.doc', '.pptx', '.ppt', '.msg', '.md', '.txt',
              '.xlsx', '.xlsm', '.xls', '.one',
              '.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tif', '.tiff']
exclude = ['~$*', '.*', '$RECYCLE.BIN', 'System Volume Information', 'Thumbs.db', 'OneNote_RecycleBin']
vision = true

# The user decides which folders belong to each group. A group without folders is simply empty.
[worlds.engineering.groups]
Mechanical = ['\\DMX-FS01.rotzingerag.local\Daten$\RMA_ORG\END\2_Groupes\22_Mecanique']
Electrical = []
Software = []
```

Rules:

* New profile `engineering` in `PROFILES`. Only this profile reads `groups`.
* For this profile the group folders **are** the roots. `roots` is not written in the world table.
  At the start of an index run the stored roots are brought in line with the group folders: a group
  folder that is not yet a root is added. A stored root that lies in no group folder stops the run
  with a message naming it; the run does not guess a group and does not drop the root by itself.
* Two group folders may not contain one another, and a folder may be in one group only; the
  configuration is refused otherwise.
* Facets (`facets.py`, new rule `compute_engineering`): `category` = the group whose folder
  contains the document; `section` = the first folder below that group folder; documents directly
  in the group folder have no section. `facet_source` = `config`. The facets version of this
  profile includes a fingerprint of the groups table, so a change of the table recomputes the
  facets on the next run or server start.
* Search: `category=` filters by group (as in Marketing), `section=` by section. Both are shown on
  every hit.
* Server instructions for this world, a world map `maps\engineering.md`, and an `engineering`
  reference page in the `dmx-research` skill, following what the Marketing world has.
  `index_status`, `list_folder`, `find_files`, `read_document`, `find_in_document`, `view_page`,
  `export_image`, `open_document` and `world_map` are registered as in Marketing.
* `229 - Archives` is indexed like any other section; it is recognisable by its section name. No
  folder name is given a special meaning in code.

### 2. Excel reader (`.xlsx`, `.xlsm`, `.xls`)

* `.xlsx` and `.xlsm` with `openpyxl` (new dependency) in read-only mode, values only: a formula
  gives its last calculated value, or nothing when the workbook stores none. `.xls` with `xlrd`
  (already a dependency).
* One sheet = one page, in workbook order, hidden sheets included. Chart sheets and empty sheets
  give an empty page (page numbers stay equal to sheet positions).
* Page text: a first line `Feuille : <sheet name>` (` (masquée)` added for a hidden sheet), then
  one line per row that has at least one value, cells joined by ` | `, empty cells between values
  kept as empty fields, trailing empty cells dropped. Dates as `YYYY-MM-DD` (with time when it is
  not midnight), whole numbers without decimals, other numbers with at most 10 significant digits.
* Limits, so that a database dump does not flood the index: at most 5,000 rows with values and 200
  columns per sheet, and 2,000,000 characters per workbook. A sheet that is cut ends with the line
  `[feuille coupée après 5000 lignes]`; a workbook that is cut stops reading further sheets and
  says so on the last page. The row and column limits are settings in `[index]`
  (`max_sheet_rows`, `max_sheet_columns`).
* The header of a sheet (its first row with values) is repeated at the top of every chunk made from
  that sheet, after the `Feuille :` line, so that a chunk from row 3,000 still says what its
  columns are. How chunking carries this is left to the builder.
* Document title: the workbook's title property when set, else none (as for other types).
* Password-protected or damaged workbooks: status `error` with the reason.
* Labels: type `Excel` / `Excel 97-2003`; the page word in hits is `sheet`.
* `view_page` and `export_image` answer that Excel sheets cannot be shown and point to
  `open_document`.

### 3. OneNote reader (`.one`)

One section file = one document. One OneNote page = one page of the document, in the section's
page order.

Reading one section:

1. Copy the `.one` file into a new, empty folder under the world's local data folder
   (`C:\dmx-rag\data\engineering\onenote-tmp\<random>`). Only this copy is ever given to OneNote.
2. `OneNote.Application` (COM, `pywin32`): `OpenHierarchy(<that folder>, "", id, cftNone)`. The
   folder is opened as a notebook because a notebook can be closed again by script; a section
   opened by itself cannot (measured).
3. OneNote loads in the background: ask `GetHierarchy(id, hsPages)` every 2 s until the section is
   listed and two answers in a row give the same pages, at most 120 s. A section that still lists
   no page after that is a document with status `empty`.
4. For each page `GetPageContent(pageId, xml, piBasic)`.
5. `CloseNotebook(id)` and delete the temporary folder, also after an error.

Page text, built from the page XML by a function that needs no OneNote (tested on its own):

```
<page title>
Créée le 2022-07-12, modifiée le 2022-10-14
<text of the page in reading order; a table row as cells joined by " | ">
Fichiers joints : <names of inserted files>
[Texte des images]
<text OneNote recognised in the pictures of the page>
```

The lines `Fichiers joints` and `[Texte des images]` appear only when there is something to put
under them. Inline formatting tags in the text are removed. A sub-page keeps its own page; its
title is written as `<parent title> > <title>`.

Where it runs:

* **Only in the index run, never in the server.** `read_document` and `find_in_document` re-read a
  file themselves when it changed since indexing or was not read; for `.one` they do not. They
  serve the indexed pages and add one line: changed since it was indexed on <date>, or not read yet
  (with the stored reason). The MCP server therefore never copies a notebook, never drives the
  user's OneNote and never ends it.
* **In its own pass, not in the worker pool.** The pool's limits (`STALL_S` 600 s, `SOLO_TIMEOUT_S`
  300 s in `indexer.py`) are shorter than a large section needs and are shared by every world, so
  they stay as they are. After the pool has finished the other files, the run reads the `.one`
  files one after another in the main process. This also makes "one OneNote job at a time" true
  without a cross-process slot.

Robustness:

* A time limit per section (5 minutes plus 1 minute per 50 MB). When it passes, the OneNote process
  is ended with the existing `kill_office_automation` pattern and the document gets status `error`
  (`timeout`); the pass continues with the next section. After 5 timeouts in a row the pass stops
  and the remaining sections stay unread for the next run.
* OneNote is ended at the end of the pass only if the pass started it; a OneNote that was already
  running is left open (its test notebook is closed).
* OneNote not installed: status `skipped` with that reason. A section protected by a password:
  status `error` with that reason.
* Startup cleanup of the pass: leftover folders in `onenote-tmp` from a killed run are deleted.
* `open_document` on a `.one` opens the original in the user's OneNote, as a double-click would.
  OneNote then writes into the file, so the next index run reads that section again (a few
  seconds). This is the user's own action and is accepted; the indexing itself still never opens
  an original.
* Labels: type `OneNote`; the page word in hits is `page`.
* `view_page` and `export_image` answer that OneNote pages cannot be shown and point to
  `open_document`.

Document title: the section name (the file name without `.one`).

### 4. Text files (`.txt`)

One page. Decoded as UTF-8 (with or without BOM), else UTF-16 when it has a BOM, else Windows-1252.
Files over 2 MB are cut at 2 MB with a last line saying so. Type label `Text`.

### 5. What does not change

* The other four worlds: their extension lists, their indexes, their tools.
* `EXTRACT_VERSION`, the embedding model, the chunk size.
* The OCR, describe and embed steps: they run on this world as they do on Marketing (PDF pages
  without text are OCRed; picture files and pictures in PDF, Word, PowerPoint and Markdown are
  described).

## Tests

* `test_excel.py`: workbooks made in the test (openpyxl; a small `.xls` fixture): sheet order and
  names, hidden sheet, formula with and without stored value, dates and numbers, empty rows, row
  and column limits with the cut line, workbook limit, damaged file -> `error`.
* `test_onenote.py`: the page-XML-to-text function on hand-written XML (title, dates, outline
  order, table, picture text, inserted file, sub-page); the reader with a fake OneNote object:
  copy is opened and never the original, waiting for late pages, close and cleanup after an error,
  timeout -> `error`, OneNote missing -> `skipped`; `read_document` on a changed `.one` serves the
  indexed pages with the note and does not call the reader. One test on a real `.one` runs only where OneNote
  is installed and a fixture path is given by an environment variable.
* `test_profiles.py` / `test_facets.py`: groups from the configuration, group and section of a
  path, a document directly in the group folder, nested or duplicate group folders refused, a root
  outside every group stops the run, a changed groups table recomputes the facets.
* `test_worlds.py`, `test_index_and_tools.py`: an `engineering` world indexed from a small folder
  with one file of each new type; `category=` and `section=` filters; `index_status` shows the new
  types; `view_page` on Excel and OneNote gives the explained refusal.
* `test_skill.py`: the tool calls shown in the new skill page are valid.
* The whole existing suite still passes; a test asserts `EXTRACT_VERSION == 2`.

## Rollout

Prepared here, launched by the user:

1. Code on a branch, merged to main when the tests pass, `U:\DMX-RAG\app` brought to main.
2. Shared `config.toml`: backup, then the `[worlds.engineering]` tables above. `maps\engineering.md`
   written and reviewed by the user. Skill zip rebuilt.
3. On the VM (user): start OneNote once by hand so that its first-run dialogs are done; then
   launcher 7 with world `engineering` (index, OCR, describe, embed). Expected size: about 1,900
   documents; picture descriptions about 2 hours.
4. Check `index_status` (documents by status and by type, as today) and the run log, which names
   every file in `error` or `skipped` with its reason. `index_status` itself is not extended.
5. Laptop: pull the world, add the `dmx-engineering` connector with Claude Desktop closed, upload
   the new skill zip.
6. Acceptance: five questions chosen by the user, at least one answered from an Excel sheet and one
   from a OneNote page.

## Open points for the user

1. **OneNote desktop on the VM.** Answered by the user on 2026-11-21: yes, the Office OneNote is
   installed on the machine that will run the index.
2. **Private content in the handed-over notebooks.** The notebooks of one engineer include sections
   named `Perso` and `Les prix`. Answered by the user on 2026-11-21: they are included.
3. **Audience.** As for the other worlds, access is decided later by permissions on
   `worlds\engineering`; until then the index is only on the machines the user copies it to.

## Calls made by the writer (not decided by the user)

* Group = `category`, first folder below the group folder = `section`.
* Group folders are the roots; a root outside every group stops the run.
* Excel: values only (no formulas), hidden sheets included, limits of 5,000 rows, 200 columns and
  2,000,000 characters, header row repeated in each chunk.
* OneNote: picture text taken from OneNote, pictures not described by the vision model, attached
  files listed by name only, 120 s loading limit, time limit per section.
* OneNote is read only during the index run, in its own pass after the other files; the server
  serves the indexed pages of a changed notebook with a note instead of reading it again.
* `open_document` opens the original notebook in the user's OneNote, although OneNote then writes
  into it.
* `.msg`, `.md`, `.txt` and picture files switched on for this world; `.log` files not.
* `229 - Archives` indexed like any other section, without a special mark.
* No `view_page` for Excel and OneNote in this version.

## Left to the builder

* How the Excel header row reaches each chunk.
* Where the OneNote pass and its time limit live (a new module, or next to the OCR and describe
  steps), and whether it is a part of `index` or a step of its own in launcher 7.
* The exact wording of the server instructions and of the skill page, in the style of the
  Marketing ones.
* Whether the script that writes the Claude Desktop connector entries needs a change for a fifth
  world.

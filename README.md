# chat-with-dmx

Chat with the documentation stored on the file servers (PDF and Word files) using
**Claude Desktop** (or Claude Code) and your normal Claude subscription.

```
 Configuration page (browser) ── folders to index, excluded subfolders, scans, status
        │
 File servers (Z:\, Y:\ ...)                         Claude Desktop
        │                                                  │  asks questions, calls tools
        ▼                                                  ▼
 dmx-docs index ──► SQLite index ◄──── dmx-docs serve (MCP server, runs on your PC)
 (text, pages,      (keyword index      tools: search · find_files · list_folder
  file metadata)     + embeddings)             read_document · find_in_document · index_status
 dmx-docs embed ──►  (by-meaning search)
```

* **Indexing** runs on your PC: it reads every PDF/Word file under the root folders,
  extracts the text page by page and stores it in a local SQLite database with a
  keyword index (accent-insensitive). `embed` adds a multilingual embedding per text
  chunk so that search also works *by meaning* (French, English, German, Spanish...).
* **Chatting** happens in Claude Desktop. Claude doesn't see the whole document base at once.
  It researches the question with the tools: it searches, opens the relevant documents,
  reads the pages around the matches, explores neighbouring folders, and answers
  with the file path and page of each source.
* Everything except the conversation with Claude stays on your PC. The extracts Claude
  reads are sent to Claude as part of the chat.

> **Privacy:** on a Pro/Max plan, check *Claude → Settings → Privacy* and turn off the
> option that allows your chats to be used to improve Claude. Chats are then kept
> 30 days and not used for training.

## 1. Install (once)

1. Install **Python 3.12** from <https://www.python.org/downloads/windows/>. In the
   installer, tick **"Add python.exe to PATH"**.
2. Get this repository, e.g. into `C:\dmx\chat-with-dmx` (git clone or *Code → Download ZIP*).
3. Open **PowerShell** in that folder and run:

   ```powershell
   py -3.12 -m venv .venv
   .venv\Scripts\pip install -e .
   ```

4. Optional, for old `.doc` files (Word 97-2003): they are converted automatically with
   **LibreOffice** if it is installed (<https://www.libreoffice.org>), otherwise with
   **Microsoft Word** if it is installed. Without either, `.doc` files are just skipped.

## 2. Configure

```powershell
copy config.example.toml config.toml
notepad config.toml
```

In `config.toml`, set `data_dir`: where the index is stored, on a **local disk** (SSD if
possible), e.g. `'C:\dmx-docs-data'`. Plan for roughly 1–2× the amount of extracted text,
plus about 4 KB per chunk for embeddings. Everything else can stay as it is.

## 3. Choose folders and scan: the configuration page

Double-click `scripts\start_config_page.bat` (or run `.venv\Scripts\dmx-docs web`). The page
opens in your browser at <http://127.0.0.1:8765> and is only reachable from this computer.

* **Add a root folder**: type its path (`Z:\Projets`, `\\server\share\Docs`) or use *Browse…*.
  Start with **one representative folder** for the pilot, check the answers, then add the rest.
* **Exclude subfolders**: expand the root folder and **untick** the subfolders you don't want,
  at any depth. Root folder with A, B, C inside, B unticked → only A and C are indexed.
  Unticked folders are hidden from Claude immediately, and their already-indexed content
  is removed at the next scan.
* **Scan for changes**: indexes new, modified and deleted files, then computes embeddings
  (search by meaning). Live progress is shown. *Stop* keeps what is done, and the next scan
  continues from there. The scan runs index and embeddings only: scanned PDF pages become
  searchable with `dmx-docs ocr` (launcher 7 includes it, see section 6).
* **Files that could not be read** lists corrupt and password-protected files, scans
  without text, and skipped files, each with the reason.
* **Exclusion patterns** apply everywhere, by name (e.g. `~$*`, `Archives`) or by path
  (e.g. `*/old/*`).
* **Connect Claude Desktop** shows the exact configuration to paste (see step 4).

The page and the command line share the same settings. Changes apply to Claude
without restarting anything.

### Same thing from the command line

```powershell
.venv\Scripts\dmx-docs index
```

* The first run reads every file, which can take hours for a large share. It prints progress
  every 15 s. You can **stop it at any time (Ctrl+C) and run it again**: it continues where
  it stopped.
* Later runs only process new or modified files and remove deleted ones, so they are quick.
* Files that could not be read (corrupt, password-protected, scanned PDFs without text)
  are counted in the summary. Details are in `data_dir\logs`. `--retry-errors` tries them again.
  The scanned PDFs without text are read by `dmx-docs ocr` (OCR, see the Reference below).

Then compute the embeddings for by-meaning search:

```powershell
.venv\Scripts\dmx-docs embed
```

* The first time, it downloads the multilingual model `intfloat/multilingual-e5-large`
  (~2.2 GB) into `data_dir\models`.
* This is the slowest step on a PC without a graphics card. Run a short test first to see
  the speed on your machine: `dmx-docs embed --max-minutes 10` prints the rate and the
  estimated remaining time. Then let it run overnight. It is also resumable.
* **Keyword search works without embeddings**, so you can start chatting as soon as `index`
  is done. Search gets better as embeddings are added.

Check from the command line:

```powershell
.venv\Scripts\dmx-docs status
.venv\Scripts\dmx-docs search "préhenseur ventouses cadence"
.venv\Scripts\dmx-docs read "Z:\Projets\P1234\Spec.pdf" --start-page 3
```

## 4. Connect Claude Desktop

1. Install **Claude Desktop** (<https://claude.ai/download>) and sign in with your account.
2. Open *Settings → Developer → Edit Config*. This opens `claude_desktop_config.json`.
   Paste the block shown in the **Connect Claude Desktop** section of the configuration page,
   which already has the right paths. It looks like this:

   ```json
   {
     "mcpServers": {
       "dmx-docs": {
         "command": "C:\\dmx\\chat-with-dmx\\.venv\\Scripts\\dmx-docs.exe",
         "args": ["--config", "C:\\dmx\\chat-with-dmx\\config.toml", "serve"]
       }
     }
   }
   ```

   (In JSON, every backslash is written twice.)
3. Quit Claude Desktop completely (also from the system tray) and start it again. The
   *dmx-docs* tools appear under the tools (🔨/⚙) icon of the chat box.
4. Ask a question, for example:
   *"Quel préhenseur a été utilisé sur la cellule du projet P1234 et pourquoi a-t-il été changé ?"*
   Claude asks permission the first time it uses each tool. You can allow them permanently.

**Claude Code** (alternative): from the repository folder, run

```powershell
claude mcp add dmx-docs -- C:\dmx\chat-with-dmx\.venv\Scripts\dmx-docs.exe --config C:\dmx\chat-with-dmx\config.toml serve
```

### How Claude researches (agentic retrieval)

Claude does not answer from the first search results. It follows a loop:
**reformulate** the question into precise queries (French terms, synonyms, other languages,
one query per sub-question, restricted to the project folder) → **search** → **evaluate**
each excerpt (relevance, coverage, contradictions, then read the best documents in full) →
**retry** with another angle if something is missing (3 rounds at most) → **answer** with a
source (path + page) for every fact and the limits of what was found.

* The short version is built into the MCP server (`INSTRUCTIONS` in `src/dmx_docs/server.py`),
  so every client follows it without installing anything.
* The detailed playbook is the skill `skills/dmx-docs-research/SKILL.md` (domain vocabulary,
  project folder map, relevance rubric, retry strategies, answer template).
  * Claude Desktop: *Settings → Capabilities → Skills → Upload skill*, choose
    `U:\DMX-RAG\skills\dmx-docs-research.zip`.
  * Claude Code: copy the folder to `%USERPROFILE%\.claude\skills\`.
* Each search hit shows its **meaning similarity** to the query, calibrated for
  multilingual-e5-large on these documents: *strong* ≥ 0.86 (on topic), *medium* 0.83–0.86
  (check by reading), *weak* < 0.83 (often off topic; short queries score lower, so these are hints). When all hits are weak, the result
  says so.

### Facets: project, collection, section, document type

Each document gets facets derived from its path (no re-indexing needed; computed in a second):
**project** (the folder above the project template, at any depth), **collection** (a numbered
folder grouping projects, e.g. `2_Hors_Garantie`), **section** (Vente, Electrique, Gestion…,
from the current `0_Vente…9_SAV` template or the older named one) and **document type** (offre,
cahier_des_charges, fat, sat, mise_en_service, manuel, schema_electrique…, from the template
folders, else from file-name keywords, else from the section). Search results show them;
`search` accepts `project=`, `doc_type=`, `section=`, `collection=` filters; `list_projects`
lists projects. Filters leave out documents whose facet is unknown, so Claude repeats a thin
filtered search without them. Rules are in `src/dmx_docs/facets.py`; bump `FACETS_VERSION`
after changing them and every document is reclassified.

### Thesaurus (synonyms, translations, abbreviations)

`thesaurus.toml` (next to `config.toml`, i.e. `U:\DMX-RAG\thesaurus.toml`) groups equivalent
terms per concept in FR/EN/DE/ES/IT (préhenseur = gripper = Greifer, mise en service =
commissioning = Inbetriebnahme = MES...). When enabled, a keyword search for one term also finds
the others; the result says which terms were added. Semantic search does not need it; it helps
with jargon, abbreviations and exact cross-language terms.

* The draft is generated by `thesaurus-work\build_draft.py`: concepts written from domain
  knowledge, every term checked against the index (number of passages), inflected forms added.
  Review it by hand: remove wrong terms, add jargon, set `status = "ok"`, or `"no"` to disable.
* Enable it with `[search] expand = true` in `config.toml` once reviewed (Claude can also ask for
  it per search with `expand=true`). The file is reloaded automatically when it changes;
  `5 - Update Claude Desktop copy` copies it next to the local index.

### Tips for good answers

* Ask precise questions and mention project numbers, clients or machine types when you
  know them.
* Ask Claude to *"read the documents"* or *"check in the full document"* if an answer
  seems based on excerpts only.
* Sources are cited as `path (p. N)`. For Word files, page numbers are approximate.
* Prepare 20–30 real questions with known answers and use them to judge the quality
  whenever something changes (more folders, another embedding model...).

## 5. Keep the index up to date

`scripts\update_index.bat` runs `index` and then `embed` (5 h at most). Schedule it every night
with the **Windows Task Scheduler** (*Create Basic Task → Daily → Start a program →*
`C:\dmx\chat-with-dmx\scripts\update_index.bat`). It does not read scanned pages: run
`dmx-docs ocr` (before `embed`) for those, or use launcher 7 of section 6, which does index,
OCR and embeddings.

> Mapped drives (`Z:\`) exist only in your logged-on session. If the task must run while you
> are logged off, use UNC paths (`\\server\share\...`) in `roots`.

## 6. Several machines sharing one folder (e.g. `U:\DMX-RAG`)

To index or embed from whichever machine is available (a GPU VM for embeddings, another one
overnight...), keep everything in a shared folder and use the launchers in it.
The index itself never runs on the share (SQLite is not safe there): each run takes a lock,
copies the index to `C:\dmx-rag` on the machine, works on it and copies it back.
Only one machine at a time works on a given world's index.

Each family of documents is a **world** (projects, marketing...) with its own index, so each
can be shared with its own audience. Worlds are declared in `config.toml`
(`[worlds.<name>]`: title, profile, file types, first root folders); every command takes
`--world <name>` (default: projects). Without `[worlds]` tables the command line still works
with a single index, but the shared-folder launchers (`dmx.ps1`) need at least a
`[worlds.projects]` table.

```
U:\DMX-RAG\
  1 - Setup this machine.cmd           builds C:\dmx-rag\venv (GPU runtime on NVIDIA machines)
  2 - Configuration page.cmd           folders, exclusions, scans (stop with Ctrl+C)
  3 - Index new and changed files.cmd
  4 - Compute embeddings.cmd           from a cmd prompt you can add e.g. --max-minutes 300
  5 - Update Claude Desktop copy.cmd   local copy for Claude Desktop + the configuration to paste
  7 - Update a world (index + embeddings).cmd   unattended: index, OCR of scanned pages, embeddings
  Unlock after a crash.cmd
  config.toml      settings for all machines (data_dir = 'C:\dmx-rag\data') + [worlds.<name>]
  thesaurus.toml   search vocabulary shared by every world
  app\             this repository (git pull here to update; machines reinstall automatically)
  worlds\<name>\   index.sqlite3, index.prev.sqlite3 (previous version), LOCK, register.csv (projects)
  models\          embedding model, copied to each machine once
  tools\uv.exe     installs Python and the packages without admin rights
  tools\tessdata   OCR language files, copied to each machine
  logs\            one log per run
```

* Every launcher asks for the world (Enter = projects); from a cmd prompt give it as first
  argument, e.g. `"7 - Update a world (index + embeddings).cmd" marketing`.
* One machine at a time works on a world; two machines can work on two different worlds.
* Local copies are in `C:\dmx-rag\data\<world>`. Claude Desktop gets one server per world:
  `dmx-docs` for projects, `dmx-<world>` for the others (launcher 5 prints the entry).
* The launchers are in `scripts\launchers` and the logic in `scripts\dmx.ps1`. The first run
  of the new script moves the old single index (`data\`) to `worlds\projects` (renames only).
* Use UNC paths for root folders (`\\server\share\...`): drive letters can differ between machines.
* Indexing `.doc` files needs Word or LibreOffice, `.ppt` files PowerPoint or LibreOffice, on
  the machine that indexes.
* If a machine crashes during a run, run the same command again on that machine: it continues
  from its local copy. From another machine, *Unlock after a crash* releases the lock (the
  unsaved work of the crashed run is then lost).

## Reference

| Command | What it does |
|---|---|
| `dmx-docs web [--port 8765]` | Configuration page: folders, exclusions, scans, status |
| `dmx-docs index [--retry-errors]` | Crawl roots, extract text, update the keyword index |
| `dmx-docs ocr [--max-minutes N] [--retry]` | Read scanned PDF pages (OCR) and add their text; run before embed |
| `dmx-docs embed [--max-minutes N] [--reset]` | Add embeddings to new chunks (`--reset` after changing model) |
| `dmx-docs status` | Counts per status/type, embedding coverage |
| `dmx-docs search "..." [--mode keyword\|semantic\|hybrid] [--folder ...]` | Test a search |
| `dmx-docs read PATH [--start-page N]` | Show a document as Claude sees it |
| `dmx-docs serve` | MCP server (started by Claude Desktop, not by hand) |

All commands take `--config path\to\config.toml` (default: `config.toml` in the current
folder, or the `DMX_DOCS_CONFIG` environment variable).

**How it works**

* Extraction: PyMuPDF (PDF, page by page), python-docx (paragraphs, headings, tables;
  pages follow Word's last saved page breaks when available), `.doc` converted to `.docx`.
  Extraction runs in several processes. A file that crashes a worker is retried alone and
  marked as an error.
* PowerPoint: python-pptx, one slide = one page (titles, text, tables, speaker notes); .ppt converted to .pptx first.
  PowerPoint files are indexed only when `.pptx` and/or `.ppt` are in `extensions`
  (see `[worlds.marketing]` in config.example.toml).
* OCR: PDF pages with (almost) no text are read by Tesseract (built into PyMuPDF, French model by default) in a separate `ocr` step; the text is appended as extra chunks marked "(OCR)" in search results, without touching existing chunks or embeddings.
* Chunks: ~1,200–2,000 characters, never spanning two pages, so every hit has a page number.
* Keyword search: SQLite FTS5 with BM25 ranking, case- and accent-insensitive. Codes like
  `MN-114` are matched as phrases.
* Semantic search: `multilingual-e5-large` via fastembed (ONNX, CPU). Vectors are kept in RAM
  for fast search, about 0.1 s per million chunks.
* Hybrid: both result lists are fused with Reciprocal Rank Fusion, with at most 3 excerpts
  per document.
* The tools are read-only and refuse paths outside the configured roots.

**Development**

```bash
pip install -e ".[test]"
pytest
```

Note: PyMuPDF is AGPL-licensed, which is fine for internal use. Review the license
before distributing this software outside the company.

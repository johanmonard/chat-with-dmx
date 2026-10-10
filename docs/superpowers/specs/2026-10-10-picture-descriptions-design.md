# Picture descriptions with a local vision model (sub-project B2)

Date: 2026-10-10. Status: approved by the user (sections 1-4 in conversation, then the written spec).

## Goal

Make what pictures show usable, in the Documentation and Marketing worlds first:

1. **Find pictures**: search by what a picture shows ("photo of the denester adjustment tool",
   "Paloma with suction-cup gripper") to show it or export it for answers and presentations.
2. **Answer from pictures**: facts that exist only in images (schemas, layouts, screenshots,
   image-only brochure pages, exhibition posters).
3. **Help Claude pick pictures**: when Claude reads a document, a description next to each
   picture tells it which one to open with `view_page`.

A vision model running in Ollama on DMX-WS032 (RTX A4000, 16 GB) writes one description per
picture. The descriptions are added to the index as extra passages (like OCR text in B1), so
keyword search, semantic search, `read_document` and the copy to the other machines work
unchanged. Projects is out of scope for this round: it is switched off for that world until
the speed is measured (see Rollout).

## Size of the work (measured 2026-10-10)

| World | Pictures | Distinct | Notes |
|---|---|---|---|
| Documentation | 1,281 Markdown picture links | 484 files (450 PNG, 33 JPG, 1 BMP) | FR/DE/EN pages share pictures; 74 under 150 px (icons); median 600x206 px |
| Marketing, PDF | 633 PDF, 6,411 pages | about 4,070 picture pages (3,992 with 15%+ pictures, plus pages without text) | brochures: most pages have photos |
| Marketing, PowerPoint | 176 .pptx: 5,925 pictures of 150 px+ | 3,005 | 103 .ppt not measured (about 1,500 more, by proportion) |
| Marketing, Word | 102 .docx: 904 pictures of 150 px+ | 316 | |
| Marketing, picture files | about 2,040 | not measured | some are also inside the decks and brochures |

Speed estimate on the A4000: 4-8 s per picture for an 8-12B model, so Documentation (about
410 pictures after the size filter) takes 30-50 min; Marketing (about 11,000 distinct pictures)
12-24 h, i.e. two or three nights with `--max-minutes` (the step resumes where it stopped).
`parallel_requests` (measured with the comparison) may shorten this.

## Design

### What is described

A **picture occurrence** is (document, page, picture number), numbered exactly as `view_page`
and `export_image` number them today, so every description points to something Claude can open:

| Format | Occurrences | Page | Picture number |
|---|---|---|---|
| Markdown (.md) | each picture link that resolves (`resolve_md_link`, inside the indexed folders) | 1 | N of the `[Image N]` marker |
| Word (.docx) | each picture in `word/media` (same order and types as `_docx_picture_raw`) | 1 (Word has no fixed pages) | N |
| PowerPoint (.pptx, .ppt) | each picture of each slide (`pptx_pictures`; .ppt converted first as in indexing) | slide | N on that slide |
| PDF | a whole page, rendered, when it is a picture page (below) | page | none (whole page) |

| Picture file (.jpg, .jpeg, .png, .bmp, .gif, .tif, .tiff) | the file itself | 1 | 1 |

Old Word `.doc` files are left out: their pictures cannot be shown either.

**Picture files as documents** (decided after the design discussion). Marketing holds about
2,040 standalone picture files (Presentations 863, Graphics 547, Pictures 386, Brochures 247,
Videos 156, Exhibition 24; mostly JPG, then BMP and PNG), the best source of photos for
presentations. A world indexes them when its `extensions` list them (Marketing in the rollout;
not Documentation, whose pictures are reached through the Markdown pages; not Projects yet).
Design files (.ai, .psd, .svg, .eps) stay out.

* `extract.py`: a picture file is a one-page document with status `no_text`, no chunks and the
  file name (separators as spaces) as title. It is found by name with `find_files` at once, and
  by content once described.
* `describe` adds its passage like any other (with `Fichier : <name>` and the two parent folder
  names in the passage and in the context) and sets the status to `ok`, as OCR does for scans.
* Files over 100 MB are not read (status `error`, "too large").
* `view_page` returns the picture (scaled), `export_image` copies the original file,
  `open_document` opens it in the default viewer; type label "Picture".

**PDF picture pages.** A page within `max_pdf_pages` is described when:

* it has a text layer (its non-OCR chunks hold at least 50 characters) and raster images of at
  least 150 px cover at least `pdf_picture_share` (15%) of the page: brochure photos, diagrams; or
* it has no text layer and OCR found less than **600 characters** on it (`ocr_pages` status
  `empty`, or `text` with fewer than 600 characters), or OCR is off for the world: posters,
  photo pages, drawings. A scanned letter or form (more OCR text) is left to OCR.

**Skipped** (recorded, so they are not examined again):

* pictures smaller than `min_side` (150 px) on their longer side: icons, bullets (status `small`);
* pictures the model classifies as `logo ou décoration`: kept in the cache, no passage (status `logo`).

**Duplicates** are described once: the description cache is keyed by the SHA-1 of the picture
bytes (for PDF pages, of the rendered image), the model and `DESCRIBE_VERSION`. A picture shared
by the FR, DE and EN versions of a manual page, or a photo reused across decks, costs one call.

### Context sent with each picture

The model gets the picture and a short context, used only to name what it sees:

* always: document title (or file name) and the world's category facet when known;
* Markdown: the caption (alt text), the nearest heading above the picture and the paragraph just
  before it (at most 400 characters);
* PowerPoint: the slide's indexed text (at most 800 characters);
* PDF: the page's indexed text (at most 800 characters);
* Word: the title only (the format does not tell where a picture sits in the text);
* picture file: the file name and its two parent folder names.

### Prompt and output

Ollama `/api/chat`, `stream: false`, `think: false`, `options.temperature: 0`, with a JSON schema
in `format` (structured output), so the shape never drifts:

```json
{"type": "photo | schéma | capture d'écran | dessin technique | graphique | tableau | page de texte | logo ou décoration",
 "description": "French; 2-5 sentences for a photo; up to ~10 lines for a schema, table, chart or screenshot",
 "texte_visible": "text read in the picture, copied as written, at most 500 characters, empty if none",
 "mots_cles": ["5 to 12 keywords, French and English"]}
```

The prompt (French) states: a picture from the documentation of Demaurex, a maker of robotic
packaging lines; the context; describe only what is visible; use the context only to name
machines and parts; write « semble être » when unsure; copy numbers only when legible; no
promotional adjectives; for schemas, tables, charts and screenshots list the elements, labels,
values, flows, buttons and tabs.

Images are sent as JPEG (photos) or PNG (screens, drawings, transparency) scaled to 1024 px on the
longer side; whole PDF pages are rendered at 1400 px on the longer side so that brochure text
stays legible. Field lengths are capped after parsing (description 1,000 characters, visible text
500, keywords 12), so that one occurrence is always one passage (`MAX_CHARS` 2,000).

### Passage text

One chunk per described occurrence, appended to the document after its existing chunks of that
page (`\n\n` first, so the page text stays rebuildable from its chunks, as in OCR):

```
[Image 3 — photo] Main tenant un outil de réglage en bois inséré entre les deux courroies du dépileur…
Texte visible : —
Mots-clés : dépileur, outil de réglage, courroies, denester, adjustment tool, belts
```

For a whole PDF page the marker is `[Page entière — <type>]`. Existing chunks and vectors are
never touched; only the new chunks need embedding (the `embed` step does it).

### Storage (store.py)

```sql
CREATE TABLE IF NOT EXISTS picture_cache (      -- one description per distinct picture
    sha1 TEXT NOT NULL, model TEXT NOT NULL, version INTEGER NOT NULL,
    type TEXT, description TEXT, visible_text TEXT, keywords TEXT, seconds REAL, created_at REAL,
    PRIMARY KEY (sha1, model, version));
CREATE TABLE IF NOT EXISTS picture_docs (       -- documents whose pictures were all handled
    doc_id INTEGER PRIMARY KEY, model TEXT NOT NULL, version INTEGER NOT NULL,
    pictures INTEGER, done_at REAL);
CREATE TABLE IF NOT EXISTS picture_uses (       -- one row per occurrence
    doc_id INTEGER NOT NULL, page_no INTEGER NOT NULL, image_no INTEGER NOT NULL,  -- 0 = whole page
    sha1 TEXT, status TEXT NOT NULL,             -- described | small | logo | error | timeout
    chunk_id INTEGER, error TEXT, done_at REAL,
    PRIMARY KEY (doc_id, page_no, image_no));
```

`store.delete_doc_content` also deletes the document's `picture_uses` and `picture_docs` rows
(its chunks are deleted anyway), so a changed document is examined again; its unchanged pictures
come back from `picture_cache` without a model call. A document is a candidate when it is
`.md`, `.docx`, `.pptx`, `.ppt`, `.pdf` or a picture file with status `ok` or `no_text` and has no
`picture_docs` row for the configured model and `DESCRIBE_VERSION`, or (with `--retry`) has
occurrences with status `error` or `timeout`. A model change or a `DESCRIBE_VERSION` bump
re-describes everything, replacing the old passages (their chunk ids are in `picture_uses`).

### New step `describe` (between `ocr` and `embed`)

* CLI: `dmx-docs [--world W] describe [--max-minutes N] [--retry]`. New module `describe.py`
  (candidates, preparation, Ollama calls, saving) and `vision.py` (the small Ollama client:
  `available()`, `chat()`, `unload()`, using `urllib`, no new dependency).
* **Before starting**: Ollama must answer at `[vision] url` and list the model (`/api/tags`).
  Otherwise the step prints why and how to fix it (`ollama pull <model>`) and exits with code 0,
  so `update` continues on machines without Ollama. Off when `[vision] enabled = false` or the
  world has `vision = false`.
* **Preparation in worker processes** (the indexer's spawn pool, `workers` capped at 4): open one
  document, list its occurrences, apply the size and PDF-page rules, compute SHA-1s, scale the
  pictures, build the context; return small prepared pictures. At most one document per worker
  in flight; hang and crash protection as in OCR (a document that hangs for 10 min or crashes the
  pool is recorded as `timeout` / `error`).
* **Model calls in the main process**, `parallel_requests` at a time (default 1), cache first.
  Each call has `timeout_s` (180 s); invalid JSON or a timeout is retried once, then the
  occurrence is recorded as `error` / `timeout`. After 20 failed calls in a row (Ollama gone)
  the step stops, saving what is done.
* **Saving**: one transaction per document (occurrence rows, chunks, `picture_docs` row), so
  Ctrl+C, the time limit or a crash lose at most the document in progress; the next run continues.
* Progress every 15 s: pictures done / to do, pictures per minute, time left. Summary at the end;
  failures go to the world's log with the document path.
* At the end the model is unloaded (`keep_alive: 0`), so the `embed` step has the GPU memory.

### Choosing the model: `describe --compare`

`dmx-docs [--world W] describe --compare gemma4:12b,qwen3-vl:8b [--sample 25]` takes a fixed
random sample of occurrences of the world (mixed formats), describes each with every listed
model and writes `describe-compare-<world>-<date>.html` to the logs folder: thumbnail, context,
each model's output and seconds. It never writes to the index. Run on WS032 for Documentation and
Marketing; the user picks the model, which goes into `[vision] model`.

### Configuration

```toml
[vision]
enabled = true
url = 'http://127.0.0.1:11434'   # the Ollama on this machine; pictures are sent only there
model = 'gemma4:12b'             # set after the comparison
timeout_s = 180
min_side = 150
pdf_picture_share = 0.15
parallel_requests = 1

[worlds.projects]
vision = false                   # until the Projects size and speed are known
```

### Tools and server

* **Search**: picture passages are labelled in results: `page 4/12, picture 3 (view_page page=4
  image=3)`, `page 1/1, picture 3 (view_page image=3)` for Markdown, `page 7/20, whole-page
  picture (view_page page=7)` for PDF. New parameter `pictures: bool = False` on `search` (all
  profiles): only picture passages (`c.id IN (SELECT chunk_id FROM picture_uses)`).
* **read_document**: the descriptions are part of the page text (they are chunks of the page);
  when the document has picture passages, the header says they were written by a vision model
  and that a picture should be viewed before quoting a precise value from its description.
* **index_status**: pictures described / small / logo / failed, by type, and the model.
* **Server instructions** (Projects, Marketing, Documentation, generic): picture hits, the
  `pictures=true` search for finding pictures, and the rule to view a picture before quoting a
  precise value from a description.

### dmx.ps1 and launchers

* New verb `describe` (`dmx.ps1 describe -World W [--max-minutes N] [--retry]`) under the world's
  lock; `update` becomes index → ocr → describe → embed. Launcher 7's description mentions it.
* Nothing to install by the script: Ollama and the model are installed by hand on WS032 (done:
  Ollama; to do: `ollama pull` of the chosen model).

## Error handling summary

| Situation | Behaviour |
|---|---|
| Ollama not running / model missing / vision off | step skipped with the reason, exit 0 |
| Picture unreadable (corrupt, unknown format) | occurrence `error`, others continue |
| Markdown link broken or outside the indexed folders | not an occurrence (as in `view_page`) |
| Model timeout or invalid JSON | one retry, then `timeout` / `error`; `--retry` later |
| 20 failed calls in a row | stop, save, message; next run continues |
| Document hangs or crashes a worker | `timeout` / `error` for that document, pool replaced |
| Document changed later | its occurrences are redone at the next run, cache reused |

## Testing

Unit tests with a fake Ollama (a local HTTP server in a thread that answers `/api/tags`,
`/api/chat` with canned JSON and counts calls):

* occurrences per format and their numbering match `view_page`; size, logo and PDF-page rules;
* picture files: indexed as `no_text` one-page documents, found by name, `ok` with a passage
  after `describe`; `view_page`, `export_image` and the 100 MB limit;
* a picture shared by two documents is described once (one call); a changed document gets its
  passages back from the cache without a call;
* passages: marker, one chunk per occurrence, appended after the page text, embedded later;
* search labels and `pictures=True`; `read_document` header; `index_status` counts;
* skip when Ollama is unreachable or the model is missing (exit 0, message); stop after 20
  failures; `--retry`; `--max-minutes`; `--compare` writes the HTML and leaves the index alone;
* `delete_doc_content` removes picture rows; a model change replaces the passages.

Real checks: the comparison on WS032, the Documentation run, then test questions in Claude
Desktop (find a picture, answer from a schema, read a page with pictures).

## Rollout

1. Merge; set `[vision]` in the shared config, `vision = false` on `[worlds.projects]`, and add
   `.jpg .jpeg .png .bmp .gif .tif .tiff` to `[worlds.marketing] extensions`.
2. On WS032: `ollama pull gemma4:12b` and `ollama pull qwen3-vl:8b`; run the comparison for
   Documentation and Marketing; the user picks the model; remove the other one.
3. On WS032: launcher 7 → `documentation`, then `marketing`.
4. On the laptop: pull both worlds; test questions.
5. Later: measure Projects (pictures per format, time) before switching it on.

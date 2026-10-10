# OCR of scanned and image-only PDF pages (sub-project B1)

Date: 2026-10-10. Status: approved; amended by the implementation plan (chunk ids in ocr_pages, crash handling).

## Goal

Make the text of scanned and image-only PDF pages searchable, in every world, without
recomputing what is already indexed and embedded.

Sub-project B (picture analysis) is split in two:

* **B1 (this spec): OCR.** Text of pages that have no text layer.
* **B2 (later, own spec): picture descriptions** with a vision model through Ollama on
  DMX-WS032 (photos, schemas and slide pictures). Ollama is now installed on WS032.

Size of the work, measured on 2026-10-10:

| World | Fully scanned PDFs | Pages without text inside PDFs that have text | Total pages |
|---|---|---|---|
| Marketing | 251 files, 589 pages (228 files have 1-2 pages: exhibition flyers) | not measured (small) | ~600 |
| Projects | 32,149 files, 101,310 pages | 32,162 pages in 3,510 files (scanned signature pages, annexes) | ~133,000 |

## Engine (decided, spike-verified)

PyMuPDF (already a dependency, 1.28.2) contains the Tesseract engine:
`page.get_textpage_ocr(language=..., dpi=300, full=True, tessdata=...)`. Only the language
files are needed: `tessdata_best` from github.com/tesseract-ocr, already downloaded to
`U:\DMX-RAG\tools\tessdata` (fra 3.8 MB, eng 14.7 MB, deu 8.2 MB, spa 12.9 MB). No program to
install, no admin rights, runs on any machine's CPU.

Spike results (single thread per process, as in the workers, `OMP_THREAD_LIMIT=1`):

* `fra`: 2.8 - 5.3 s per page; `fra+eng`: about twice as slow for no visible gain (English,
  German and Spanish text came out correctly with `fra`). Default: `fra`, configurable.
* Typed pages (order forms, spec sheets, letters) are read well, accents included.
* Scanned mechanical drawings produce mostly noise; a line filter keeps the useful notes
  (e.g. "Remplir le tube de graisse ... Mobilgrease FM102") and drops the noise lines.
* Estimate on DMX-WS032 (16 workers, ~3.5 s/page): Marketing 2-3 min, Projects ~8 h (one night).

## Design

### Which pages

A page is an OCR candidate when it is a page of a `.pdf` document (status `ok` or `no_text`),
within `max_pdf_pages`, whose existing chunks hold fewer than **50 characters** in total
(no chunk at all, or only a stray header or page number), and which has no row yet in
`ocr_pages` for the current `OCR_VERSION`. Fully scanned documents (`no_text`) are processed
first, then pages inside `ok` documents.

### New step `ocr` (between `index` and `embed`)

* CLI: `dmx-docs [--world W] ocr [--max-minutes N]`. Resumable like `embed`: Ctrl+C or the time
  limit stops after committing what is done; the next run continues.
* Work is done per document in a process pool (`workers` from the config): open the PDF once,
  OCR its candidate pages at `dpi` (300) with `languages` (`fra`), return the text per page.
  `OMP_THREAD_LIMIT=1` is set before the pool starts so that 16 workers do not oversubscribe
  the CPU.
* Hang protection like the indexer: if no document finishes for 10 minutes, the pool is
  restarted and the pending documents' pages are recorded with status `timeout` (not retried
  automatically; `--retry` retries `timeout` and `error` pages).
* **Line filter** (noise from drawings and photos): keep a line when it has at least 2 word-like
  tokens (letters only, length >= 2, containing a vowel; trailing punctuation allowed) and
  word-like tokens are at least half of its tokens. Page text = kept lines joined by newlines,
  cleaned with `extract.clean_text`. A page whose kept text is shorter than 20 characters is
  recorded as `empty` (photo, drawing without notes) and gets no chunk.
* **Storage, appended, never replacing:**
  * New chunks for the page via `chunking.split_text`, with `seq` continuing after the page's
    existing chunks; when the page already had text, the OCR text is prefixed with a blank line
    so that concatenating a page's chunks still rebuilds it (read_document relies on this).
  * Each `ocr_pages` row keeps the ids of the chunks it added (`chunk_ids`), so a page read again by a newer `OCR_VERSION` replaces exactly its own OCR chunks; no column is added to `chunks` (an index copied from before this feature keeps working unchanged in Claude Desktop).
  * New table `ocr_pages(doc_id, page_no, status, chars, ocr_version, done_at)`, primary key
    `(doc_id, page_no)`; status `text` | `empty` | `timeout` | `error`.
  * A `no_text` document that gets at least one `text` page becomes `ok` (error cleared).
  * Existing chunks and vectors are untouched: the next `embed` only embeds the new chunks.
* `OCR_VERSION = 1`. A higher version makes pages recorded with an older version candidates
  again; their old OCR chunks (and vectors) are deleted first.

### Interaction with indexing

* When a document is re-extracted (file changed, extractor version), `store.delete_doc_content`
  also deletes its `ocr_pages` rows, so its pages are OCR'd again by the next `ocr` run. A
  re-extracted fully scanned document goes back to `no_text` until then.
* The indexer is otherwise unchanged: OCR never runs inside `index`.

### Configuration and deployment

* `[ocr]` table in config.toml (global; a world may override `enabled`):
  `enabled = true`, `languages = 'fra'`, `dpi = 300`,
  `tessdata` (default: `<data_dir parent>\tessdata`, i.e. `C:\dmx-rag\tessdata`).
* `scripts\dmx.ps1`:
  * copies `<shared>\tools\tessdata` to `C:\dmx-rag\tessdata` when missing or different
    (like the embedding model);
  * new command `ocr [args]` (locked, per world);
  * `update` becomes `index`, `ocr`, `embed` (stops at the first failing step, as today).
* If OCR is disabled or the tessdata folder is missing, `ocr` prints why and exits 0, so
  `update` still runs `embed`.

### What users and Claude see

* Search hits from OCR chunks are marked `(OCR)` after the page reference.
* `read_document` on a document with OCR pages adds a header note: "pages N, M: text
  recognized by OCR (may contain recognition errors)" and no longer says "would need OCR" for
  pages that have OCR text.
* `index_status` shows `OCR: X pages with text, Y without usable text (photos, drawings),
  Z failed`.
* Server instructions for every profile mention that `(OCR)` text can contain recognition
  errors: check codes and numbers with `view_page`.

## Error handling

* Unreadable or password-protected PDF: the document's candidate pages are recorded `error`.
* Network share unreachable during a run: pages of that document are recorded `error`
  (retried with `--retry`).
* Tesseract crash in a worker: the jobs hit by the crash are read again one by one in a single-worker pool; only a job that crashes or hangs alone is recorded `error`/`timeout` (`ocr --retry` reads those again).

## Tests

Test PDFs are generated: a page rendered from text into a picture and inserted as an image
(no text layer), a PDF mixing a text page and such an image page, and a "drawing" page with
noise. Tests needing Tesseract use `DMX_TESSDATA` or `U:\DMX-RAG\tools\tessdata` and are
skipped when neither exists.

* candidate selection: image page selected, text page not, page with < 50 chars selected,
  page already in `ocr_pages` not;
* run: words of the image page found by keyword search, chunk flagged `ocr`, doc `no_text`
  becomes `ok`, `(OCR)` label in search, read_document note, embed embeds the new chunks;
* second run processes 0 pages; file change → OCR redone; `--max-minutes` stops and resumes;
* line filter unit tests (prose kept, noise dropped, mixed line);
* disabled / no tessdata → message, exit 0;
* dmx.ps1: `update` runs index, ocr, embed in order (temp-folder test with the stub CLI,
  as the existing ones); tessdata copy.

## Out of scope

Picture descriptions (B2); OCR of pictures inside .docx/.pptx (B2 describes them); images as
documents (.jpg/.png files); choosing pages with a vision model (hybrid, later if needed);
tables layout reconstruction.

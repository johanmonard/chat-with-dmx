# Picture descriptions (B2) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Describe the pictures of the Documentation and Marketing worlds with a local vision model (Ollama on DMX-WS032) and make the descriptions searchable, readable and usable to pick pictures; index standalone picture files as documents.

**Architecture:** A new step `describe` (between `ocr` and `embed`) finds documents not yet handled, prepares their picture occurrences in worker processes (`pictures.py`, numbered exactly like `view_page`), describes each distinct picture once through Ollama (`vision.py`, cached by SHA-1 + model + version) and appends one passage (chunk) per occurrence, like OCR. Tools label picture hits, filter on them (`pictures=True`), and say that the passages are machine-written.

**Tech Stack:** Python 3.12, PyMuPDF, python-pptx, sqlite3/FTS5, `urllib` (no new dependency), Ollama HTTP API (`/api/tags`, `/api/chat` with JSON-schema `format`, `/api/generate` to unload), Windows PowerShell 5.1 for `dmx.ps1`, pytest.

**Spec:** `docs/superpowers/specs/2026-10-10-picture-descriptions-design.md`

## Global Constraints

- No new Python dependency: HTTP with `urllib.request`; images with PyMuPDF.
- Ollama URL default `http://127.0.0.1:11434`; pictures are sent only to `[vision] url`.
- Descriptions in French; keywords French and English; prompt text in French as in Task 3.
- `DESCRIBE_VERSION = 1`; cache key (sha1 of the original picture bytes, or of the rendered PDF page, model, version).
- One chunk per described occurrence, at most `chunking.MAX_CHARS` (2,000) characters: description ≤ 1,000, visible text ≤ 500, keywords ≤ 12.
- Occurrence numbering identical to `view_page` / `export_image`: Markdown image N, Word image N (`word/media` natural order, `PICTURE_EXTS`), PowerPoint slide S image N (`pptx_pictures`), PDF whole page (image 0), picture file image 1.
- Size rules: `min_side` 150 px; `pdf_picture_share` 0.15; PDF page without text layer is a picture page when OCR found < 600 characters; picture files over 100 MB are not read.
- Pictures sent at 1,024 px on the longer side; whole PDF pages rendered at 1,400 px.
- `describe` exits 0 when skipped (vision off, Ollama down, model missing) so `update` continues.
- `[worlds.projects] vision = false` in the rollout; Projects is out of scope.
- All files in this worktree use LF line endings: keep them (never `sed -i` in Git Bash on this machine, write Windows paths with an editor, not a shell heredoc).
- Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Run tests with `C:\dmx-rag\venv\Scripts\python.exe -m pytest` from the worktree root (PYTHONPATH=src), e.g. `PYTHONPATH=src /c/dmx-rag/venv/Scripts/python.exe -m pytest tests/test_vision.py -q`.

## Review Focus

1. A Markdown page of the manual is edited and re-indexed: its passages come back from the cache with no model call (Task 5 test `test_changed_document_gets_its_passages_back_from_the_cache`).
2. Ollama answers slowly (huge picture) or with garbage: one retry, then `timeout`/`error`, the run continues and `--retry` fixes it (Task 5 tests `test_a_timeout_is_retried_once_then_recorded`, `test_invalid_answers_are_retried_then_recorded_and_retry_fixes_them`).
3. Odd picture encodings (CMYK JPEG, palette PNG with transparency, GIF) are scaled without crashing, and a corrupt one is an `error` occurrence, not a failed document (Task 4 tests `test_scale_picture_handles_cmyk_and_transparency`, `test_corrupt_picture_file_is_an_error_occurrence`).
4. An index copied to the laptop before this change (no picture tables): `search`, `read_document`, `index_status` keep working and `pictures=True` says there are no descriptions yet (Task 7 test `test_tools_work_on_an_index_without_picture_tables`).
5. The same picture twice in one document (a logo or photo repeated in a deck): one model call, two passages (Task 5 test `test_same_picture_twice_in_a_document_is_described_once`).

---

## File Structure

| File | Responsibility |
|---|---|
| `src/dmx_docs/config.py` (modify) | `[vision]` settings, world `vision = false` |
| `src/dmx_docs/store.py` (modify) | picture tables, `has_table`, `delete_chunks`, picture rows removed with a document's content |
| `src/dmx_docs/ocr.py` (modify) | use `store.delete_chunks` |
| `src/dmx_docs/extract.py` (modify) | picture files as one-page `no_text` documents; `docx_picture_names` shared with tools |
| `src/dmx_docs/vision.py` (create) | Ollama client: prompt, JSON schema, `unavailable`, `describe_image`, `unload`, `passage_text` |
| `src/dmx_docs/pictures.py` (create) | occurrences of one document, scaled and with context (runs in worker processes) |
| `src/dmx_docs/describe.py` (create) | the step: candidates, jobs, model calls with cache and retry, saving, pool, compare page |
| `src/dmx_docs/cli.py` (modify) | `describe` command |
| `src/dmx_docs/search.py` (modify) | `pictures_only` filter |
| `src/dmx_docs/tools.py` (modify) | picture files in view/export/find/read; picture labels; `pictures` param; notes; status |
| `src/dmx_docs/server.py` (modify) | `pictures` param on every search tool, docstrings, instructions |
| `scripts/dmx.ps1`, launcher 7 (modify) | `describe` verb, `update` = index → ocr → describe → embed |
| `README.md`, `config.example.toml` (modify) | how to install Ollama/model, settings, commands |
| `tests/fake_ollama.py` (create) | local HTTP server answering like Ollama |
| `tests/test_vision_config.py`, `tests/test_picture_files.py`, `tests/test_vision.py`, `tests/test_pictures.py`, `tests/test_describe.py`, `tests/test_picture_tools.py` (create) | tests per task |

---

### Task 1: Settings and storage

**Files:**
- Modify: `src/dmx_docs/config.py` (Config fields after `ocr_tessdata`; `load_config` after the OCR block)
- Modify: `src/dmx_docs/store.py` (SCHEMA, new `has_table`, `delete_chunks`, `delete_doc_content`)
- Modify: `src/dmx_docs/ocr.py` (`_delete_chunks` → `store.delete_chunks`)
- Test: `tests/test_vision_config.py`

**Interfaces:**
- Produces: `Config.vision_enabled: bool`, `vision_url: str`, `vision_model: str`, `vision_timeout_s: float`, `vision_min_side: int`, `vision_pdf_picture_share: float`, `vision_parallel_requests: int`; tables `picture_cache`, `picture_docs`, `picture_uses` (with a `type` column, added to the spec's table to count types in `index_status`); `store.has_table(con, name) -> bool`; `store.delete_chunks(con, ids: list[int]) -> bool` (True if vectors were removed).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_vision_config.py
from dmx_docs import store
from dmx_docs.config import load_config


def write_config(tmp_path, text):
    p = tmp_path / "config.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_vision_defaults(tmp_path):
    cfg = load_config(write_config(tmp_path, "[index]\nroots = []\n"))
    assert cfg.vision_enabled is True
    assert cfg.vision_url == "http://127.0.0.1:11434"
    assert cfg.vision_model == "gemma4:12b"
    assert (cfg.vision_timeout_s, cfg.vision_min_side, cfg.vision_pdf_picture_share,
            cfg.vision_parallel_requests) == (180, 150, 0.15, 1)


def test_vision_settings_and_world_switch(tmp_path):
    p = write_config(tmp_path, """
[vision]
url = 'http://ws032:11434'
model = 'qwen3-vl:8b'
timeout_s = 60
min_side = 200
pdf_picture_share = 0.3
parallel_requests = 2

[worlds.projects]
profile = "projects"
vision = false

[worlds.marketing]
profile = "marketing"
""")
    projects, marketing = load_config(p, world="projects"), load_config(p, world="marketing")
    assert projects.vision_enabled is False and marketing.vision_enabled is True
    assert (marketing.vision_url, marketing.vision_model, marketing.vision_timeout_s, marketing.vision_min_side,
            marketing.vision_pdf_picture_share, marketing.vision_parallel_requests) == (
        "http://ws032:11434", "qwen3-vl:8b", 60, 200, 0.3, 2)


def test_delete_doc_content_removes_picture_rows(tmp_path):
    con = store.connect(tmp_path / "i.sqlite3")
    doc_id, _ = store.upsert_doc(con, path=str(tmp_path / "a.md"), size=1, mtime=1.0, status="ok", error=None,
                                 n_pages=1, title=None, indexed_at=1.0, run_id=1, chunks=[(1, 0, "texte")])
    con.execute("INSERT INTO picture_docs(doc_id, model, version, pictures, done_at) VALUES(?, 'm', 1, 1, 1)", (doc_id,))
    con.execute("INSERT INTO picture_uses(doc_id, page_no, image_no, sha1, status, type, chunk_id) "
                "VALUES(?, 1, 1, 'x', 'described', 'photo', NULL)", (doc_id,))
    store.delete_doc_content(con, doc_id)
    assert con.execute("SELECT count(*) FROM picture_docs").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM picture_uses").fetchone()[0] == 0


def test_has_table_and_delete_chunks(tmp_path):
    con = store.connect(tmp_path / "i.sqlite3")
    assert store.has_table(con, "picture_uses") and not store.has_table(con, "nope")
    doc_id, _ = store.upsert_doc(con, path=str(tmp_path / "a.md"), size=1, mtime=1.0, status="ok", error=None,
                                 n_pages=1, title=None, indexed_at=1.0, run_id=1,
                                 chunks=[(1, 0, "alpha"), (1, 1, "beta")])
    ids = [r[0] for r in con.execute("SELECT id FROM chunks ORDER BY id")]
    con.execute("INSERT INTO vectors(chunk_id, vec) VALUES(?, x'00')", (ids[1],))
    assert store.delete_chunks(con, [ids[1]]) is True
    assert [r[0] for r in con.execute("SELECT text FROM chunks")] == ["alpha"]
    assert con.execute("SELECT count(*) FROM chunks_fts WHERE chunks_fts MATCH 'beta'").fetchone()[0] == 0
    assert store.delete_chunks(con, []) is False
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src /c/dmx-rag/venv/Scripts/python.exe -m pytest tests/test_vision_config.py -q`
Expected: FAIL (`AttributeError: 'Config' object has no attribute 'vision_enabled'`, no table `picture_docs`).

- [ ] **Step 3: Implement**

In `config.py`, after `ocr_tessdata: str | None = None`:

```python
    # Picture descriptions by a local vision model (describe.py, vision.py).
    vision_enabled: bool = True
    vision_url: str = "http://127.0.0.1:11434"   # the Ollama on this machine: pictures are sent only there
    vision_model: str = "gemma4:12b"
    vision_timeout_s: float = 180
    vision_min_side: int = 150                   # smaller pictures (icons, bullets) are not described
    vision_pdf_picture_share: float = 0.15       # a PDF page with text is described when pictures cover this share
    vision_parallel_requests: int = 1
```

In `load_config`, after the `if name and "ocr" in w:` block (before `return Config(**kwargs)`):

```python
    vis = raw.get("vision", {})
    for key, attr in (("enabled", "vision_enabled"), ("url", "vision_url"), ("model", "vision_model"),
                      ("timeout_s", "vision_timeout_s"), ("min_side", "vision_min_side"),
                      ("pdf_picture_share", "vision_pdf_picture_share"),
                      ("parallel_requests", "vision_parallel_requests")):
        if key in vis:
            kwargs[attr] = vis[key]
    if name and "vision" in w:  # a world can switch picture descriptions off
        kwargs["vision_enabled"] = bool(w["vision"])
```

Also update the `load_config` docstring sentence "A world table may set title, profile, extensions, exclude, roots (first-run seed) and register" → "... roots (first-run seed), register, ocr and vision".

In `store.py`, append to `SCHEMA` (inside the string, after the `ocr_pages` table):

```sql
CREATE TABLE IF NOT EXISTS picture_cache (
    sha1          TEXT NOT NULL,
    model         TEXT NOT NULL,
    version       INTEGER NOT NULL,
    type          TEXT,
    description   TEXT,
    visible_text  TEXT,
    keywords      TEXT,
    seconds       REAL,
    created_at    REAL,
    PRIMARY KEY (sha1, model, version)
);
CREATE TABLE IF NOT EXISTS picture_docs (
    doc_id    INTEGER PRIMARY KEY,
    model     TEXT NOT NULL,
    version   INTEGER NOT NULL,
    pictures  INTEGER,
    done_at   REAL
);
CREATE TABLE IF NOT EXISTS picture_uses (
    doc_id    INTEGER NOT NULL,
    page_no   INTEGER NOT NULL,
    image_no  INTEGER NOT NULL,
    sha1      TEXT,
    status    TEXT NOT NULL,
    type      TEXT,
    chunk_id  INTEGER,
    error     TEXT,
    done_at   REAL,
    PRIMARY KEY (doc_id, page_no, image_no)
);
CREATE INDEX IF NOT EXISTS picture_uses_chunk ON picture_uses(chunk_id);
```

Add after `set_meta`:

```python
def has_table(con: sqlite3.Connection, name: str) -> bool:
    """True if the index has this table (copies made by older versions may not)."""
    return con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (name,)).fetchone() is not None


def delete_chunks(con: sqlite3.Connection, ids: list[int]) -> bool:
    """Remove chunks (with their FTS entries and vectors). True if vectors were removed."""
    if not ids:
        return False
    marks = ",".join("?" * len(ids))
    rows = con.execute(f"SELECT id, text FROM chunks WHERE id IN ({marks})", ids).fetchall()
    con.executemany("INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', ?, ?)",
                    [(r[0], r[1]) for r in rows])
    cur = con.execute(f"DELETE FROM vectors WHERE chunk_id IN ({marks})", ids)
    con.execute(f"DELETE FROM chunks WHERE id IN ({marks})", ids)
    return cur.rowcount > 0
```

In `delete_doc_content`, after the `ocr_pages` line:

```python
    con.execute("DELETE FROM picture_uses WHERE doc_id=?", (doc_id,))  # described again after re-extraction
    con.execute("DELETE FROM picture_docs WHERE doc_id=?", (doc_id,))  # (from the cache: no model call)
```

In `ocr.py`, delete the function `_delete_chunks` and replace its one use in `save_page`:

```python
    removed = store.delete_chunks(con, [int(i) for i in (old[0] or "").split(",") if i]) if old else False
```

Then `grep -rn "_delete_chunks" src tests` must return nothing; if a test used `ocr._delete_chunks`, change it to `store.delete_chunks`.

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src /c/dmx-rag/venv/Scripts/python.exe -m pytest tests/test_vision_config.py tests/test_ocr.py tests/test_ocr_tools.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dmx_docs/config.py src/dmx_docs/store.py src/dmx_docs/ocr.py tests/test_vision_config.py
git commit -m "Picture descriptions: [vision] settings and picture tables

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Picture files as documents

**Files:**
- Modify: `src/dmx_docs/extract.py` (dispatch in `extract_file`; new `picture_title`, `extract_picture_file`, `docx_picture_names`)
- Modify: `src/dmx_docs/tools.py` (`TYPE_LABEL`, `_page_label`, `_docx_picture_raw`, `view_page`, `export_image`, `find_files`, `read_document`)
- Test: `tests/test_picture_files.py`

**Interfaces:**
- Consumes: `PICTURE_EXTS` (extract.py, existing).
- Produces: `extract.picture_title(path) -> str`; `extract.extract_picture_file(path) -> Extracted` (status `no_text`, n_pages 1, no pages); `extract.docx_picture_names(z: zipfile.ZipFile) -> list[str]` (the `view_page` numbering); `tools.TYPE_LABEL[ext] == "Picture"` for `PICTURE_EXTS`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_picture_files.py
import zipfile

from conftest import make_png
from dmx_docs import store
from dmx_docs.extract import docx_picture_names, extract_file, picture_title, pymupdf
from dmx_docs.indexer import run_index
from dmx_docs.tools import DocTools

quiet = lambda *a, **k: None  # noqa: E731
PICS = [".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff"]


def pictures_world(tmp_path, make_cfg):
    root = tmp_path / "Marketing"
    (root / "Pictures" / "Paloma").mkdir(parents=True)
    make_png(root / "Pictures" / "Paloma" / "Paloma_4R-tray loader.png", (10, 200, 30), size=(300, 200))
    (root / "Pictures" / "empty.jpg").write_bytes(b"")
    cfg = make_cfg(root, extensions=[".pdf"] + PICS, world="marketing", world_title="Marketing",
                   profile="marketing")
    run_index(cfg, progress=quiet)
    return cfg, root


def test_picture_title():
    assert picture_title(r"\\s\x\Paloma_4R-tray loader.png") == "Paloma 4R tray loader"
    assert picture_title("IMG.0042.JPG") == "IMG 0042"


def test_picture_file_is_a_one_page_document_without_text(tmp_path, make_cfg):
    cfg, root = pictures_world(tmp_path, make_cfg)
    r = extract_file(str(root / "Pictures" / "Paloma" / "Paloma_4R-tray loader.png"))
    assert (r.status, r.n_pages, r.pages, r.title) == ("no_text", 1, [], "Paloma 4R tray loader")
    con = store.connect(cfg.db_path)
    rows = {n: (s, p) for n, s, p in con.execute("SELECT name, status, n_pages FROM docs")}
    assert rows["Paloma_4R-tray loader.png"] == ("no_text", 1)
    assert rows["empty.jpg"][0] == "empty"
    assert con.execute("SELECT count(*) FROM chunks").fetchone()[0] == 0


def test_picture_file_found_by_name_viewed_exported_and_read(tmp_path, make_cfg):
    cfg, root = pictures_world(tmp_path, make_cfg)
    t = DocTools(cfg)
    found = t.find_files("Paloma tray")
    assert "Paloma_4R-tray loader.png (Picture, 1 pages" in found and "not described yet" in found
    path = str(root / "Pictures" / "Paloma" / "Paloma_4R-tray loader.png")
    caption, data, fmt = t.view_page(path)
    assert caption.startswith(path + " — picture file, shown at")
    assert tuple(pymupdf.Pixmap(data).pixel(5, 5)) == (10, 200, 30)
    t.cfg.export_dir = str(tmp_path / "exports")
    out = t.export_image(path)
    target = tmp_path / "exports" / "Paloma_4R-tray_loader.png"
    assert str(target) in out and target.read_bytes() == (root / "Pictures" / "Paloma" / "Paloma_4R-tray loader.png").read_bytes()
    text = t.read_document(path)
    assert "(Picture, 1 pages" in text and "no description yet" in text and "no text layer" not in text


def test_docx_picture_names_numbering(tmp_path):
    z_path = tmp_path / "a.docx"
    with zipfile.ZipFile(z_path, "w") as z:
        for name in ("word/media/image10.png", "word/media/image2.jpeg", "word/media/image1.emf",
                     "word/media/image3.png", "word/document.xml"):
            z.writestr(name, b"x")
    with zipfile.ZipFile(z_path) as z:
        assert docx_picture_names(z) == ["word/media/image2.jpeg", "word/media/image3.png", "word/media/image10.png"]
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src /c/dmx-rag/venv/Scripts/python.exe -m pytest tests/test_picture_files.py -q`
Expected: FAIL (`ImportError: cannot import name 'docx_picture_names'`).

- [ ] **Step 3: Implement in `extract.py`**

In `extract_file`, after the `.md` branch:

```python
        if ext in PICTURE_EXTS:
            return extract_picture_file(path)
```

`PICTURE_EXTS` is defined further down the module (line ~633); the name is resolved at call time, so the order is fine.

After `pptx_pictures` (before the Markdown section), add:

```python
def docx_picture_names(z) -> list[str]:
    """Pictures of a .docx (an open ZipFile) that can be shown, in the numbering of view_page's image=N:
    word/media files in natural order (image2 before image10), vector drawings (EMF/WMF) left out."""
    media = [n for n in z.namelist() if n.startswith("word/media/")]
    media.sort(key=lambda n: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", n)])
    return [n for n in media if os.path.splitext(n)[1].lower() in PICTURE_EXTS]


# ------------------------------------------------------------ picture files

def picture_title(path: str) -> str:
    """A picture file's name as words: 'Paloma_4R-tray loader.png' -> 'Paloma 4R tray loader'."""
    stem = os.path.splitext(os.path.basename(path))[0]
    return re.sub(r"[_\-.\s]+", " ", stem).strip() or stem


def extract_picture_file(path: str) -> Extracted:
    """A picture file is a one-page document without text: found by its name at once, by its content
    once the `describe` step has added its description (which also makes it 'ok')."""
    return Extracted(status="no_text", n_pages=1, title=picture_title(path),
                     error="picture file: no description yet (describe step)")
```

- [ ] **Step 4: Implement in `tools.py`**

Imports: `from .extract import PICTURE_EXTS, docx_picture_names, extract_file`.

After `TYPE_LABEL = {...}`:

```python
TYPE_LABEL.update(dict.fromkeys(PICTURE_EXTS, "Picture"))
```

`_page_label`:

```python
def _page_label(ext: str) -> str:
    if ext in SLIDE_EXTS:
        return "slide"
    return "page" if ext in (".pdf", ".md") or ext in PICTURE_EXTS else "page (approx.)"
```

`_docx_picture_raw` uses the shared numbering:

```python
    with zipfile.ZipFile(fs) as z:
        media = [n for n in z.namelist() if n.startswith("word/media/")]
        shown = docx_picture_names(z)
        if not shown:
```

(remove the old `media.sort(...)` and `shown = [...]` lines; the rest of the function is unchanged).

`view_page`, before the final `raise ValueError(...)`:

```python
        if ext in PICTURE_EXTS:
            with open(fs, "rb") as f:
                data, fmt, w, h = _scaled_picture(f.read(), ext)
            return f"{path} — picture file, shown at {w}x{h} px.", data, fmt
```

and update that final error text: `"(only PDF pages, pictures in .docx, PowerPoint and Markdown files, and picture files)."`

`export_image`, before the final `else:`:

```python
        elif ext in PICTURE_EXTS:
            with open(fs, "rb") as f:
                data = f.read()  # the original file
            out_ext, default, what = ext, stem, "picture file"
```

and update that `else` error text the same way.

`find_files`, the `extra` line:

```python
            extra = ("" if r["status"] == "ok" else ", not described yet" if r["ext"] in PICTURE_EXTS
                     and r["status"] == "no_text" else f", status: {r['status']}")
```

`read_document`, replace the `no_text` note:

```python
        if info.get("status") == "no_text" and info.get("ext") in PICTURE_EXTS:
            header += "\nNote: a picture file with no description yet — look at it with view_page."
        elif info.get("status") == "no_text":
            header += "\nNote: this PDF has (almost) no text layer — probably a scan; content may be missing."
```

Also: `view_page` and `export_image` in `server.py` — append to their first docstring sentence ", or a picture file (JPG, PNG...)" (wording only; no test).

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=src /c/dmx-rag/venv/Scripts/python.exe -m pytest tests/test_picture_files.py tests/test_tools.py tests/test_documentation.py -q`
Expected: PASS (`test_tools.py` covers the existing .docx pictures numbering).

- [ ] **Step 6: Commit**

```bash
git add src/dmx_docs/extract.py src/dmx_docs/tools.py src/dmx_docs/server.py tests/test_picture_files.py
git commit -m "Picture files (JPG, PNG...) as one-page documents: found by name, viewable, exportable

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Ollama client (`vision.py`) and a fake Ollama for tests

**Files:**
- Create: `src/dmx_docs/vision.py`
- Create: `tests/fake_ollama.py`
- Modify: `tests/conftest.py` (fixture `fake_ollama`)
- Test: `tests/test_vision.py`

**Interfaces:**
- Produces (vision.py): `DESCRIBE_VERSION = 1`; `TYPES: tuple[str, ...]`; `LOGO = "logo ou décoration"`; `SCHEMA: dict`; `SYSTEM_PROMPT: str`; `class VisionError(Exception)` with attribute `timeout: bool`; `@dataclass Description(type: str, description: str, visible_text: str, keywords: list[str], seconds: float = 0.0)`; `unavailable(url: str, model: str) -> str | None`; `parse(content: str) -> Description`; `describe_image(url: str, model: str, image: bytes, context: str, timeout: float) -> Description` (raises `VisionError`); `unload(url: str, model: str) -> None`; `passage_text(desc: Description, marker: str, file_name: str | None = None) -> str`.
- Produces (tests): `FakeOllama(models=("test-vision",))` with attributes `url`, `models: list[str]`, `calls: list[dict]` (bodies of `/api/chat`), `reply` (callable `body -> (status, dict | str)`), `delay: float`, `unloaded: list[str]`; `default_reply(body)`; `answer(type, description, visible="", keywords=...)` builds a 200 reply; fixture `fake_ollama` in conftest yields a started `FakeOllama`.

- [ ] **Step 1: Write the fake Ollama**

```python
# tests/fake_ollama.py
"""A local HTTP server that answers like Ollama, for tests: /api/tags lists `models`, /api/chat
answers `reply(body)`, /api/generate (unload) is recorded. `calls` keeps every /api/chat body."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def answer(kind="photo", description="Photo d'un préhenseur à ventouses sur un robot Delta.", visible="",
           keywords=("préhenseur", "ventouses", "gripper", "suction cups")):
    content = json.dumps({"type": kind, "description": description, "texte_visible": visible,
                          "mots_cles": list(keywords)}, ensure_ascii=False)
    return 200, {"model": "x", "message": {"role": "assistant", "content": content}, "done": True}


def default_reply(body):
    return answer()


class FakeOllama:
    def __init__(self, models=("test-vision",)):
        self.models = list(models)
        self.calls: list[dict] = []
        self.unloaded: list[str] = []
        self.reply = default_reply
        self.delay = 0.0
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, status, payload):
                data = (payload if isinstance(payload, str) else json.dumps(payload)).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == "/api/tags":
                    self._send(200, {"models": [{"name": m} for m in fake.models]})
                else:
                    self._send(404, {"error": "not found"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
                if self.path == "/api/generate":
                    fake.unloaded.append(body.get("model"))
                    self._send(200, {"done": True})
                    return
                fake.calls.append(body)
                if fake.delay:
                    time.sleep(fake.delay)
                status, payload = fake.reply(body)
                try:
                    self._send(status, payload)
                except OSError:  # the client gave up (timeout test)
                    pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
```

Append to `tests/conftest.py`:

```python
@pytest.fixture
def fake_ollama():
    from fake_ollama import FakeOllama

    with FakeOllama() as server:
        yield server
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_vision.py
import base64
import json
import socket

import pytest

from dmx_docs import vision
from fake_ollama import answer


def free_port_url():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return f"http://127.0.0.1:{port}"


def test_unavailable_reasons(fake_ollama):
    assert "does not answer" in vision.unavailable(free_port_url(), "test-vision")
    assert "ollama pull other:7b" in vision.unavailable(fake_ollama.url, "other:7b")
    assert vision.unavailable(fake_ollama.url, "test-vision") is None
    fake_ollama.models = ["gemma4:latest"]
    assert vision.unavailable(fake_ollama.url, "gemma4") is None   # 'gemma4' means 'gemma4:latest'


def test_describe_image_request_and_answer(fake_ollama):
    d = vision.describe_image(fake_ollama.url, "test-vision", b"PNGDATA", "Document : Manuel", timeout=10)
    assert (d.type, d.description) == ("photo", "Photo d'un préhenseur à ventouses sur un robot Delta.")
    assert d.keywords == ["préhenseur", "ventouses", "gripper", "suction cups"] and d.seconds >= 0
    body = fake_ollama.calls[0]
    assert body["model"] == "test-vision" and body["stream"] is False and body["think"] is False
    assert body["format"] == vision.SCHEMA and body["options"]["temperature"] == 0
    assert body["messages"][0] == {"role": "system", "content": vision.SYSTEM_PROMPT}
    user = body["messages"][1]
    assert "Document : Manuel" in user["content"] and user["images"] == [base64.b64encode(b"PNGDATA").decode()]


def test_model_without_thinking_switch_is_asked_again_without_it(fake_ollama):
    def reply(body):
        if "think" in body:
            return 400, {"error": '"test-vision" does not support thinking'}
        return answer()
    fake_ollama.reply = reply
    d = vision.describe_image(fake_ollama.url, "test-vision", b"x", "c", timeout=10)
    assert d.type == "photo" and len(fake_ollama.calls) == 2 and "think" not in fake_ollama.calls[1]


def test_invalid_answers_and_http_errors_raise(fake_ollama):
    fake_ollama.reply = lambda body: (200, {"message": {"content": "pas du JSON"}})
    with pytest.raises(vision.VisionError, match="invalid JSON"):
        vision.describe_image(fake_ollama.url, "test-vision", b"x", "c", timeout=10)
    fake_ollama.reply = lambda body: answer(description="  ")
    with pytest.raises(vision.VisionError, match="without a description"):
        vision.describe_image(fake_ollama.url, "test-vision", b"x", "c", timeout=10)
    fake_ollama.reply = lambda body: (500, {"error": "model crashed"})
    with pytest.raises(vision.VisionError, match="HTTP 500") as e:
        vision.describe_image(fake_ollama.url, "test-vision", b"x", "c", timeout=10)
    assert e.value.timeout is False


def test_timeout_raises_a_timeout_error(fake_ollama):
    fake_ollama.delay = 2
    with pytest.raises(vision.VisionError) as e:
        vision.describe_image(fake_ollama.url, "test-vision", b"x", "c", timeout=0.5)
    assert e.value.timeout is True


def test_parse_caps_and_cleans():
    content = json.dumps({"type": "capture d'écran", "description": "mot " * 400, "texte_visible": "V" * 900,
                          "mots_cles": ["Dépileur", "dépileur", " denester ", ""] + [f"k{i}" for i in range(20)]})
    d = vision.parse(content)
    assert d.type == "capture d'écran"
    assert len(d.description) <= vision.MAX_DESCRIPTION and d.description.endswith("…")
    assert len(d.visible_text) == vision.MAX_VISIBLE
    assert d.keywords[:2] == ["Dépileur", "denester"] and len(d.keywords) == vision.MAX_KEYWORDS
    assert vision.parse(json.dumps({"type": "", "description": "x", "texte_visible": "", "mots_cles": []})).type == "image"


def test_passage_text():
    d = vision.Description("photo", "Main tenant un outil.", "", ["dépileur", "denester"])
    assert vision.passage_text(d, "Image 3") == (
        "[Image 3 — photo] Main tenant un outil.\nTexte visible : —\nMots-clés : dépileur, denester")
    assert vision.passage_text(d, "Image 1", file_name="Outil.jpg").startswith(
        "[Image 1 — photo] Fichier : Outil.jpg. Main tenant un outil.")


def test_unload_is_quiet(fake_ollama):
    vision.unload(fake_ollama.url, "test-vision")
    assert fake_ollama.unloaded == ["test-vision"]
    vision.unload(free_port_url(), "test-vision")   # no exception when Ollama is gone
```

- [ ] **Step 3: Run them to see them fail**

Run: `PYTHONPATH=src;tests /c/dmx-rag/venv/Scripts/python.exe -m pytest tests/test_vision.py -q` (the `tests` folder is already on `sys.path` through pytest's rootdir conftest; `PYTHONPATH=src` is enough).
Expected: FAIL (`ModuleNotFoundError: No module named 'dmx_docs.vision'`).

- [ ] **Step 4: Implement `src/dmx_docs/vision.py`**

```python
"""The local vision model (Ollama): prompt, answer format and HTTP calls.

Pictures go only to the configured Ollama ([vision] url, this machine by default). The model
answers with a fixed JSON structure (Ollama structured output), checked and capped by `parse`.
"""

from __future__ import annotations

import base64
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

DESCRIBE_VERSION = 1   # bump when the prompt or the passage format changes: every picture is described again
TYPES = ("photo", "schéma", "capture d'écran", "dessin technique", "graphique", "tableau",
         "page de texte", "logo ou décoration")
LOGO = "logo ou décoration"
MAX_DESCRIPTION = 1000
MAX_VISIBLE = 500
MAX_KEYWORDS = 12
MAX_KEYWORD_CHARS = 40

SCHEMA = {
    "type": "object",
    "properties": {
        "type": {"type": "string", "enum": list(TYPES)},
        "description": {"type": "string"},
        "texte_visible": {"type": "string"},
        "mots_cles": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["type", "description", "texte_visible", "mots_cles"],
}

SYSTEM_PROMPT = """Tu décris des images pour le moteur de recherche documentaire de Demaurex, fabricant suisse de \
lignes d'emballage robotisées (robots Delta, préhenseurs, convoyeurs, dépileurs, formeuses de boîtes, \
interfaces opérateur). Réponds uniquement en JSON selon le schéma imposé.
Règles :
- Décris seulement ce qui est visible dans l'image.
- Le contexte sert uniquement à nommer correctement les machines et les pièces : n'en recopie pas le \
contenu et n'invente rien qui n'est pas visible.
- Si tu n'es pas sûr, écris « semble être ».
- Ne recopie un nombre ou un texte que s'il est lisible.
- Pas d'adjectifs publicitaires.
Champs :
- type : la nature de l'image.
- description : en français. Pour une photo, 2 à 5 phrases (quoi, quelles parties, quelle action ou \
quel état). Pour un schéma, un dessin technique, un tableau, un graphique ou une capture d'écran : les \
éléments, libellés, valeurs, flux, boutons et onglets, jusqu'à 10 lignes.
- texte_visible : le texte lisible dans l'image, recopié tel quel (500 caractères au plus), vide s'il \
n'y en a pas.
- mots_cles : 5 à 12 mots-clés utiles pour la recherche, en français et en anglais."""


class VisionError(Exception):
    """A call that gave no usable description: timeout, HTTP error, Ollama gone, invalid answer."""

    def __init__(self, message: str, timeout: bool = False):
        super().__init__(message)
        self.timeout = timeout


@dataclass
class Description:
    type: str
    description: str
    visible_text: str
    keywords: list[str] = field(default_factory=list)
    seconds: float = 0.0


def _post(url: str, body: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def unavailable(url: str, model: str) -> str | None:
    """Why the model cannot be used now (Ollama not answering, model not installed), or None."""
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/api/tags", timeout=10) as r:
            names = {m.get("name") for m in json.loads(r.read().decode("utf-8")).get("models", [])}
    except Exception as e:  # noqa: BLE001 - not running, refused, wrong address
        return (f"Ollama does not answer at {url} ({type(e).__name__}: {e}). Start Ollama on this "
                "machine, or set [vision] url.")
    if model not in names and f"{model}:latest" not in names:
        return f"The vision model {model} is not installed in Ollama: run `ollama pull {model}` on this machine."
    return None


def _cut(text: str, limit: int) -> str:
    text = " ".join(text.split()) if "\n" not in text else text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    return (cut[:cut.rfind(" ")] if " " in cut[limit // 2:] else cut).rstrip() + "…"


def parse(content: str) -> Description:
    """The model's JSON answer, checked and capped so that one occurrence is one passage."""
    try:
        d = json.loads(content)
    except ValueError:
        raise VisionError("invalid JSON answer: " + str(content)[:200]) from None
    if not isinstance(d, dict) or not isinstance(d.get("description"), str) or not d["description"].strip():
        raise VisionError("answer without a description")
    kind = str(d.get("type") or "").strip()[:30] or "image"
    keywords, seen = [], set()
    for k in d.get("mots_cles") or []:
        k = " ".join(str(k).split())[:MAX_KEYWORD_CHARS]
        if k and k.lower() not in seen:
            seen.add(k.lower())
            keywords.append(k)
    visible = str(d.get("texte_visible") or "").strip()[:MAX_VISIBLE]
    return Description(kind, _cut(d["description"], MAX_DESCRIPTION), visible, keywords[:MAX_KEYWORDS])


def describe_image(url: str, model: str, image: bytes, context: str, timeout: float) -> Description:
    """One picture described by the model. Raises VisionError (timeout=True for a timeout)."""
    body = {"model": model, "stream": False, "think": False, "format": SCHEMA, "keep_alive": "10m",
            "options": {"temperature": 0, "num_ctx": 8192},
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": f"Contexte :\n{context}\n\nDécris l'image.",
                          "images": [base64.b64encode(image).decode("ascii")]}]}
    endpoint = url.rstrip("/") + "/api/chat"
    started = time.perf_counter()
    try:
        try:
            out = _post(endpoint, body, timeout)
        except urllib.error.HTTPError as e:
            detail = e.read().decode("utf-8", "replace")
            if e.code != 400 or "think" not in detail.lower():
                raise VisionError(f"HTTP {e.code}: {detail[:200]}") from None
            body.pop("think")  # a model without a thinking switch
            out = _post(endpoint, body, timeout)
    except VisionError:
        raise
    except urllib.error.HTTPError as e:
        raise VisionError(f"HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}") from None
    except TimeoutError:
        raise VisionError(f"no answer within {timeout:.0f} s", timeout=True) from None
    except urllib.error.URLError as e:
        if isinstance(e.reason, TimeoutError):
            raise VisionError(f"no answer within {timeout:.0f} s", timeout=True) from None
        raise VisionError(f"Ollama not reachable at {url}: {e.reason}") from None
    except (OSError, ValueError) as e:
        raise VisionError(f"{type(e).__name__}: {e}"[:200]) from None
    desc = parse(((out or {}).get("message") or {}).get("content") or "")
    desc.seconds = time.perf_counter() - started
    return desc


def unload(url: str, model: str) -> None:
    """Free the GPU memory now (the embedding step needs it); errors are ignored."""
    try:
        _post(url.rstrip("/") + "/api/generate", {"model": model, "keep_alive": 0}, 60)
    except Exception:  # noqa: BLE001
        pass


def passage_text(desc: Description, marker: str, file_name: str | None = None) -> str:
    """The searchable passage of one picture occurrence ('[Image 3 — photo] ...')."""
    first = f"[{marker} — {desc.type}] " + (f"Fichier : {file_name}. " if file_name else "") + desc.description
    lines = [first, "Texte visible : " + (desc.visible_text or "—")]
    if desc.keywords:
        lines.append("Mots-clés : " + ", ".join(desc.keywords))
    return "\n".join(lines)
```

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=src /c/dmx-rag/venv/Scripts/python.exe -m pytest tests/test_vision.py -q`
Expected: PASS. (If `test_parse_caps_and_cleans` fails on the `…` assertion, check `_cut`: a 1,600-character description must come back ≤ 1,000 characters ending with `…`.)

- [ ] **Step 6: Commit**

```bash
git add src/dmx_docs/vision.py tests/fake_ollama.py tests/conftest.py tests/test_vision.py
git commit -m "vision.py: Ollama client with a fixed JSON answer, French prompt, passage text

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Picture occurrences of one document (`pictures.py`)

**Files:**
- Create: `src/dmx_docs/pictures.py`
- Test: `tests/test_pictures.py`

**Interfaces:**
- Consumes: `extract.md_pictures`, `extract.pptx_pictures`, `extract.convert_ppt`, `extract.docx_picture_names`, `extract.PICTURE_EXTS`, `extract.pymupdf`, `tools.resolve_md_link`, `store.fs_path`, `config.Config`.
- Produces: `@dataclass Prepared(page_no: int, image_no: int, status: str, sha1: str | None = None, image: bytes | None = None, context: str = "", error: str | None = None)` with status `ready | small | error`; `image_no` 0 = whole PDF page; `page_no` 0 with `image_no` 0 = the whole document failed. `prepare_document(job: dict, options: dict) -> list[Prepared]`. `scale_picture(raw: bytes, max_px: int) -> tuple[bytes, int, int]` (encoded image, original width, height). `md_context(text: str, k: int) -> str`. Constants `PICTURE_PX = 1024`, `PAGE_PX = 1400`, `TEXT_PAGE_CHARS = 600`, `MIN_PAGE_CHARS = 50`, `MAX_PICTURE_BYTES = 100 * 2**20`.
- Job dict (built by Task 5 `describe.job_for`): `{"doc_id": int, "path": str, "ext": str, "title": str | None, "category": str | None, "pages": {page_no: indexed text without picture passages}, "native_chars": {page_no: characters of non-OCR, non-picture chunks}, "ocr_chars": {page_no: characters found by OCR}}`.
- Options dict (Task 5 `describe.prep_options(cfg)`): `{"roots": list[str], "excluded_dirs": list[str], "min_side": int, "pdf_picture_share": float, "max_pdf_pages": int, "doc_converter": str, "libreoffice_path": str | None}`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_pictures.py
import hashlib

from conftest import make_png, make_pptx
from dmx_docs.extract import pymupdf
from dmx_docs.pictures import Prepared, md_context, prepare_document, scale_picture

OPTIONS = {"min_side": 150, "pdf_picture_share": 0.15, "max_pdf_pages": 3000, "doc_converter": "auto",
           "libreoffice_path": None, "excluded_dirs": []}


def job(path, ext, pages=None, native=None, ocr=None, title="Titre", category="Cat"):
    return {"doc_id": 1, "path": str(path), "ext": ext, "title": title, "category": category,
            "pages": pages or {}, "native_chars": native or {}, "ocr_chars": ocr or {}}


def opts(root):
    return dict(OPTIONS, roots=[str(root)])


def by_key(prepared):
    return {(p.page_no, p.image_no): p for p in prepared}


def test_markdown_occurrences_keep_the_marker_numbers(tmp_path):
    (tmp_path / "img").mkdir()
    make_png(tmp_path / "img" / "big.png", (255, 0, 0), size=(300, 200))
    make_png(tmp_path / "img" / "icon.png", (0, 0, 0), size=(40, 20))
    md = tmp_path / "p_FR.md"
    md.write_text("# Manuel\n## Réglage du dépileur\n\nIntroduire l'outil entre les courroies.\n\n"
                  "![🖼 Outil de réglage](img/big.png)\n\n![](missing.png)\n\n![](img/icon.png)\n", encoding="utf-8")
    out = by_key(prepare_document(job(md, ".md"), opts(tmp_path)))
    assert set(out) == {(1, 1), (1, 3)}                      # the broken link 2 is not an occurrence
    first = out[(1, 1)]
    assert first.status == "ready" and first.sha1 == hashlib.sha1((tmp_path / "img" / "big.png").read_bytes()).hexdigest()
    assert "Légende : Outil de réglage" in first.context and "Section : Réglage du dépileur" in first.context
    assert "Introduire l'outil entre les courroies." in first.context and "Document : Titre" in first.context
    assert pymupdf.Pixmap(first.image).width == 300
    assert out[(1, 3)].status == "small" and out[(1, 3)].image is None


def test_markdown_link_outside_the_roots_is_not_an_occurrence(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    make_png(tmp_path / "secret.png", (0, 255, 0), size=(300, 200))
    md = root / "p.md"
    md.write_text(f"![x]({(tmp_path / 'secret.png').as_posix()})\n", encoding="utf-8")
    assert prepare_document(job(md, ".md"), opts(root)) == []


def test_md_context():
    text = "# T\n## Section A\npara 1\n\n## Section B\n\nLe texte avant.\n\n![a](x.png)\n\n![b](y.png)"
    assert md_context(text, 1) == "Section : Section B\nTexte juste avant l'image : Le texte avant."
    assert md_context(text, 2).startswith("Section : Section B")


def test_word_pictures_in_view_page_order(tmp_path):
    from docx import Document
    from docx.shared import Inches

    make_png(tmp_path / "a.png", (255, 0, 0), size=(300, 200))
    make_png(tmp_path / "b.png", (0, 0, 255), size=(40, 20))
    d = Document()
    d.add_paragraph("Rapport")
    d.add_picture(str(tmp_path / "a.png"), width=Inches(2))
    d.add_picture(str(tmp_path / "b.png"), width=Inches(1))
    d.save(tmp_path / "r.docx")
    out = by_key(prepare_document(job(tmp_path / "r.docx", ".docx"), opts(tmp_path)))
    assert out[(1, 1)].status == "ready" and out[(1, 2)].status == "small"
    assert out[(1, 1)].context == "Document : Titre\nCatégorie : Cat"


def test_powerpoint_pictures_per_slide_with_slide_text(tmp_path):
    make_png(tmp_path / "a.png", (255, 0, 0), size=(300, 200))
    make_png(tmp_path / "b.png", (0, 0, 255), size=(40, 20))
    make_pptx(tmp_path / "d.pptx", pictures=[tmp_path / "a.png", tmp_path / "b.png"])
    out = by_key(prepare_document(job(tmp_path / "d.pptx", ".pptx", pages={3: "Ligne Paloma"}), opts(tmp_path)))
    assert set(out) == {(3, 1), (3, 2)}
    assert out[(3, 1)].status == "ready" and "Texte de la diapositive : Ligne Paloma" in out[(3, 1)].context
    assert out[(3, 2)].status == "small"


def make_picture_pdf(path):
    """p1: text only; p2: text + a picture over half the page; p3: picture only (no text);
    p4: picture only, a scanned letter (OCR found a lot of text)."""
    doc = pymupdf.open()
    photo = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 400, 300), False)
    photo.set_rect(photo.irect, (200, 100, 50))
    for i in range(4):
        page = doc.new_page()
        if i in (0, 1):
            page.insert_textbox(pymupdf.Rect(50, 50, 550, 200), "Brochure Paloma " * 20, fontsize=11)
        if i == 1:
            page.insert_image(pymupdf.Rect(50, 250, 550, 750), pixmap=photo)
        if i in (2, 3):
            page.insert_image(page.rect, pixmap=photo)
    doc.save(path)


def test_pdf_picture_pages(tmp_path):
    make_picture_pdf(tmp_path / "b.pdf")
    j = job(tmp_path / "b.pdf", ".pdf", pages={2: "Brochure Paloma", 3: ""},
            native={1: 400, 2: 400}, ocr={3: 0, 4: 1500})
    out = by_key(prepare_document(j, opts(tmp_path)))
    assert set(out) == {(2, 0), (3, 0)}
    assert out[(2, 0)].status == "ready" and abs(max(scale_picture(out[(2, 0)].image, 5000)[1:]) - 1400) <= 1
    assert "Texte de la page : Brochure Paloma" in out[(2, 0)].context
    no_ocr = by_key(prepare_document(job(tmp_path / "b.pdf", ".pdf", native={1: 400, 2: 400}), opts(tmp_path)))
    assert set(no_ocr) == {(2, 0), (3, 0), (4, 0)}          # OCR off: every page without text is a picture page


def test_picture_file_occurrence(tmp_path):
    (tmp_path / "Pictures" / "Paloma").mkdir(parents=True)
    f = tmp_path / "Pictures" / "Paloma" / "P4R.png"
    make_png(f, (1, 2, 3), size=(300, 200))
    [p] = prepare_document(job(f, ".png", title="P4R", category="Pictures"), opts(tmp_path))
    assert (p.page_no, p.image_no, p.status) == (1, 1, "ready")
    assert p.context == "Fichier : P4R\nDossiers : Pictures / Paloma\nCatégorie : Pictures"


def test_corrupt_picture_file_is_an_error_occurrence(tmp_path):
    f = tmp_path / "bad.jpg"
    f.write_bytes(b"\xff\xd8 not a jpeg")
    [p] = prepare_document(job(f, ".jpg"), opts(tmp_path))
    assert (p.page_no, p.image_no, p.status) == (1, 1, "error") and p.error


def test_unreadable_document_is_one_document_error(tmp_path):
    [p] = prepare_document(job(tmp_path / "gone.pdf", ".pdf"), opts(tmp_path))
    assert (p.page_no, p.image_no, p.status) == (0, 0, "error")


def test_scale_picture_handles_cmyk_and_transparency(tmp_path):
    big = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 3000, 2000), False)
    data, w, h = scale_picture(big.tobytes("png"), 1024)
    assert (w, h) == (3000, 2000) and max(pymupdf.Pixmap(data).width, pymupdf.Pixmap(data).height) == 1024
    cmyk = pymupdf.Pixmap(pymupdf.csCMYK, pymupdf.IRect(0, 0, 200, 200), False)
    assert scale_picture(cmyk.tobytes("jpg"), 1024)[1:] == (200, 200)
    alpha = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 100), True)
    data, _, _ = scale_picture(alpha.tobytes("png"), 1024)
    assert data[:4] == b"\x89PNG"
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src /c/dmx-rag/venv/Scripts/python.exe -m pytest tests/test_pictures.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'dmx_docs.pictures'`).

- [ ] **Step 3: Implement `src/dmx_docs/pictures.py`**

```python
"""Picture occurrences of one document, numbered exactly like view_page / export_image, scaled
for the vision model and with a short context. Runs in worker processes (describe.py): plain
arguments and results only, no database access.

  Markdown     each picture link that resolves inside the indexed folders: page 1, image N of [Image N]
  Word .docx   each picture of word/media (docx_picture_names): page 1, image N
  PowerPoint   each picture of each slide (pptx_pictures; .ppt converted first): slide S, image N
  PDF          a whole page, rendered, when it is a picture page (is_picture_page): page P, image 0
  picture file the file itself: page 1, image 1
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .extract import PICTURE_EXTS, convert_ppt, docx_picture_names, md_pictures, pptx_pictures, pymupdf
from .store import fs_path

PICTURE_PX = 1024      # pictures sent to the model: longer side
PAGE_PX = 1400         # whole PDF pages: longer side (brochure text stays legible)
MIN_PAGE_CHARS = 50    # a PDF page with at least this much non-OCR text has a text layer
TEXT_PAGE_CHARS = 600  # a page without text layer is a text scan (left to OCR) from this much OCR text
MAX_PICTURE_BYTES = 100 * 2**20
CONTEXT_CHARS = 800
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\(((?:[^()]|\([^()]*\))*)\)")   # as extract._MD_IMAGE


@dataclass
class Prepared:
    page_no: int
    image_no: int              # 0: the whole PDF page (or, with page_no 0, the whole document failed)
    status: str                # ready | small | error
    sha1: str | None = None    # of the original picture bytes (of the rendered page for PDF pages)
    image: bytes | None = None  # scaled, ready for the model
    context: str = ""
    error: str | None = None


def scale_picture(raw: bytes, max_px: int) -> tuple[bytes, int, int]:
    """A picture as JPEG (PNG when it has transparency), at most max_px on its longer side, and its
    original width and height. CMYK and other colour spaces are converted to RGB."""
    pix = pymupdf.Pixmap(raw)
    w, h = pix.width, pix.height
    if pix.colorspace is None or pix.colorspace.n not in (1, 3):
        pix = pymupdf.Pixmap(pymupdf.csRGB, pix)
    if max(w, h) > max_px:
        zoom = max_px / max(w, h)
        pix = pymupdf.Pixmap(pix, max(1, round(w * zoom)), max(1, round(h * zoom)), None)
    if pix.alpha:
        return pix.tobytes("png"), w, h
    return pix.tobytes("jpg", jpg_quality=90), w, h


def _clip(text: str, limit: int = CONTEXT_CHARS) -> str:
    return " ".join(text.split())[:limit]


def _base_context(job: dict) -> list[str]:
    lines = [f"Document : {job.get('title') or os.path.basename(job['path'])}"]
    if job.get("category"):
        lines.append(f"Catégorie : {job['category']}")
    return lines


def md_context(text: str, k: int) -> str:
    """Nearest heading above the k-th picture of a Markdown text, and the paragraph just before it."""
    matches = list(_MD_IMAGE.finditer(text))
    before = text[:matches[k - 1].start()] if 0 < k <= len(matches) else text
    heading = ""
    for line in before.splitlines():
        if line.lstrip().startswith("#") and re.search(r"[^\W\d_]", line):
            heading = line.strip().strip("#").strip()
    blocks = [b.strip() for b in re.split(r"\n\s*\n", _MD_IMAGE.sub("", before)) if b.strip()]
    blocks = [b for b in blocks if not b.lstrip().startswith("#")]
    para = " ".join(blocks[-1].split())[-400:] if blocks else ""
    return f"Section : {heading}\nTexte juste avant l'image : {para}"


def _read(path: str) -> bytes:
    fs = fs_path(path)
    if os.path.getsize(fs) > MAX_PICTURE_BYTES:
        raise ValueError(f"too large (over {MAX_PICTURE_BYTES // 2**20} MB)")
    with open(fs, "rb") as f:
        return f.read()


def _occurrence(page_no: int, image_no: int, load, context: str, options: dict) -> Prepared:
    """One picture; load() returns its original bytes. Unreadable: an 'error' occurrence."""
    try:
        raw = load()
        image, w, h = scale_picture(raw, PICTURE_PX)
        if max(w, h) < options["min_side"]:
            return Prepared(page_no, image_no, "small")
        return Prepared(page_no, image_no, "ready", sha1=hashlib.sha1(raw).hexdigest(), image=image, context=context)
    except Exception as e:  # noqa: BLE001 - corrupt or unknown picture format
        return Prepared(page_no, image_no, "error", error=f"{type(e).__name__}: {e}"[:300])


def _markdown(job: dict, options: dict) -> list[Prepared]:
    from .config import Config
    from .tools import resolve_md_link

    sources = Config(roots=options["roots"], data_dir=Path("."), excluded_dirs=options["excluded_dirs"])
    with open(fs_path(job["path"]), "rb") as f:
        text = f.read().decode("utf-8-sig", errors="replace")
    out = []
    for k, (caption, target) in enumerate(md_pictures(text), 1):
        found = resolve_md_link(target, job["path"], sources.roots, allows=sources.allows)
        if found is None or os.path.splitext(found)[1].lower() not in PICTURE_EXTS:
            continue  # broken, outside the indexed folders or not a picture: not an occurrence (as in view_page)
        context = "\n".join(_base_context(job) + [f"Légende : {caption or '—'}", md_context(text, k)])
        out.append(_occurrence(1, k, lambda p=found: _read(p), context, options))
    return out


def _word(job: dict, options: dict) -> list[Prepared]:
    import zipfile

    context = "\n".join(_base_context(job))
    with zipfile.ZipFile(fs_path(job["path"])) as z:
        return [_occurrence(1, k, lambda n=name: z.read(n), context, options)
                for k, name in enumerate(docx_picture_names(z), 1)]


def _slides(job: dict, options: dict) -> list[Prepared]:
    with tempfile.TemporaryDirectory(prefix="dmx_describe_") as tmp:
        src = fs_path(job["path"])
        if job["ext"] == ".ppt":
            src = convert_ppt(job["path"], tmp, options.get("doc_converter", "auto"), options.get("libreoffice_path"))
        slides = pptx_pictures(src)
    out = []
    for s, pictures in enumerate(slides, 1):
        context = "\n".join(_base_context(job) + [f"Texte de la diapositive : {_clip(job['pages'].get(s, ''))}"])
        for k, (data, _ext) in enumerate(pictures, 1):
            out.append(_occurrence(s, k, lambda d=data: d, context, options))
    return out


def picture_share(page, min_side: int) -> float:
    """Share of the page covered by raster pictures of at least min_side pixels (capped at 1)."""
    area = abs(page.rect) or 1.0
    covered = sum(abs(pymupdf.Rect(info["bbox"]) & page.rect) for info in page.get_image_info()
                  if max(info["width"], info["height"]) >= min_side)
    return min(1.0, covered / area)


def is_picture_page(page, native_chars: int, ocr_chars: int | None, options: dict) -> bool:
    """With a text layer: pictures cover at least pdf_picture_share of the page (brochure photos,
    diagrams). Without: OCR found less than TEXT_PAGE_CHARS (posters, photos, drawings; a scanned
    letter or form is left to OCR); OCR not run or off counts as no OCR text."""
    if native_chars >= MIN_PAGE_CHARS:
        return picture_share(page, options["min_side"]) >= options["pdf_picture_share"]
    return (ocr_chars or 0) < TEXT_PAGE_CHARS


def _pdf(job: dict, options: dict) -> list[Prepared]:
    out = []
    with pymupdf.open(fs_path(job["path"])) as doc:
        if doc.needs_pass:
            raise ValueError("password-protected PDF")
        for i in range(min(doc.page_count, options["max_pdf_pages"])):
            p = i + 1
            page = doc[i]
            try:
                if not is_picture_page(page, job["native_chars"].get(p, 0), job["ocr_chars"].get(p), options):
                    continue
                zoom = PAGE_PX / max(page.rect.width, page.rect.height)
                data = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("jpg", jpg_quality=90)
            except Exception as e:  # noqa: BLE001 - a broken page
                out.append(Prepared(p, 0, "error", error=f"{type(e).__name__}: {e}"[:300]))
                continue
            context = "\n".join(_base_context(job) + [f"Texte de la page : {_clip(job['pages'].get(p, ''))}"])
            out.append(Prepared(p, 0, "ready", sha1=hashlib.sha1(data).hexdigest(), image=data, context=context))
    return out


def _picture_file(job: dict, options: dict) -> list[Prepared]:
    parts = os.path.normpath(job["path"]).split(os.sep)
    lines = [f"Fichier : {job.get('title') or parts[-1]}", "Dossiers : " + " / ".join(parts[-3:-1])]
    if job.get("category"):
        lines.append(f"Catégorie : {job['category']}")
    return [_occurrence(1, 1, lambda: _read(job["path"]), "\n".join(lines), options)]


def prepare_document(job: dict, options: dict) -> list[Prepared]:
    """All picture occurrences of one document. A document that cannot be opened gives one
    Prepared(0, 0, 'error')."""
    ext = job["ext"]
    try:
        if ext == ".md":
            return _markdown(job, options)
        if ext == ".docx":
            return _word(job, options)
        if ext in (".pptx", ".ppt"):
            return _slides(job, options)
        if ext == ".pdf":
            return _pdf(job, options)
        if ext in PICTURE_EXTS:
            return _picture_file(job, options)
    except Exception as e:  # noqa: BLE001 - unreadable file or share not reachable
        return [Prepared(0, 0, "error", error=f"{type(e).__name__}: {e}"[:300])]
    return []
```

Note on `_occurrence` for picture files: `_read` raising "too large" yields an `error` occurrence (1, 1), as the spec's error table says.

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src /c/dmx-rag/venv/Scripts/python.exe -m pytest tests/test_pictures.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dmx_docs/pictures.py tests/test_pictures.py
git commit -m "pictures.py: picture occurrences of a document, numbered like view_page, with context

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: The `describe` step

**Files:**
- Create: `src/dmx_docs/describe.py`
- Modify: `src/dmx_docs/cli.py` (command `describe`; module docstring)
- Test: `tests/test_describe.py`

**Interfaces:**
- Consumes: `vision.*` (Task 3), `pictures.prepare_document`, `pictures.Prepared` (Task 4), `store.delete_chunks`, `store.has_table`, `store.bump_vector_version` (Task 1), `chunking.split_text` is NOT used (one chunk per occurrence), `indexer._MP`, `indexer._terminate_pool`.
- Produces: `DOC_EXTS`; `unavailable(cfg) -> str | None`; `prep_options(cfg) -> dict`; `candidates(con, cfg, retry=False) -> list[int]`; `job_for(con, doc_id) -> dict`; `marker(ext, page_no, image_no) -> str`; `save_document(con, cfg, job, prepared: list[Prepared], described: dict[str, vision.Description | vision.VisionError]) -> bool` (returns True if vectors were removed; writes the cache rows for new descriptions, the occurrences, the passages and the `picture_docs` row; caller owns the transaction); `@dataclass DescribeStats(documents, described, reused, small, logos, failed)` with `summary()`; `run_describe(cfg, max_minutes=None, retry=False, progress=print) -> DescribeStats`; module constants `PROGRESS_EVERY_S = 15`, `STALL_S = 600`, `MAX_FAILED_CALLS_IN_A_ROW = 20`, `MAX_PREP_WORKERS = 4`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_describe.py
import json

import pytest

from conftest import make_png, make_pptx
from dmx_docs import describe, store, vision
from dmx_docs.indexer import run_index
from dmx_docs.tools import DocTools
from fake_ollama import answer

quiet = lambda *a, **k: None  # noqa: E731
PICS = [".jpg", ".jpeg", ".png", ".bmp", ".gif", ".tif", ".tiff"]


@pytest.fixture
def world(tmp_path, make_cfg, fake_ollama):
    """4 documents: FR and DE manual pages sharing one picture (plus an icon each), a deck showing the
    same photo twice on slide 3, a picture file. The manual's img folder is not scanned (exclude
    pattern) but its pictures stay inside the root, so the links resolve."""
    root = tmp_path / "Source"
    (root / "img").mkdir(parents=True)
    make_png(root / "img" / "outil.png", (200, 0, 0), size=(300, 200))
    make_png(root / "img" / "icone.png", (0, 0, 0), size=(40, 20))
    for lang in ("FR", "DE"):
        (root / f"5120_Depileur_{lang}.md").write_text(
            f"# Dépileur {lang}\n\nRéglage.\n\n![Outil](img/outil.png)\n\n![](img/icone.png)\n", encoding="utf-8")
    make_png(tmp_path / "photo.png", (0, 200, 0), size=(320, 200))
    make_pptx(root / "deck.pptx", pictures=[tmp_path / "photo.png", tmp_path / "photo.png"])
    (root / "Pictures").mkdir()
    make_png(root / "Pictures" / "Paloma_4R.png", (0, 0, 200), size=(400, 300))
    cfg = make_cfg(root, extensions=[".md", ".pptx"] + PICS, exclude=["~$*", ".*", "img"],
                   vision_url=fake_ollama.url, vision_model="test-vision", vision_timeout_s=5)
    run_index(cfg, progress=quiet)
    return cfg, root


def rows(cfg, sql, *args):
    con = store.connect(cfg.db_path)
    try:
        return con.execute(sql, args).fetchall()
    finally:
        con.close()


def test_skipped_when_ollama_is_unavailable(tmp_path, make_cfg):
    (tmp_path / "r").mkdir()
    cfg = make_cfg(tmp_path / "r", vision_url="http://127.0.0.1:9", vision_model="m")
    msgs = []
    stats = describe.run_describe(cfg, progress=msgs.append)
    assert stats.documents == 0 and any("does not answer" in m for m in msgs) and any("skipped" in m for m in msgs)
    cfg.vision_enabled = False
    msgs.clear()
    describe.run_describe(cfg, progress=msgs.append)
    assert any("off for this world" in m for m in msgs)


def test_describe_adds_one_passage_per_occurrence(world, fake_ollama):
    cfg, root = world
    stats = describe.run_describe(cfg, progress=quiet)
    # distinct pictures: outil.png (shared by FR and DE), photo.png (twice in the deck), Paloma_4R.png
    # FR (model call), DE (cache), deck (call + second copy), file (call): handled one at a time in the main process
    assert len(fake_ollama.calls) == 3
    assert (stats.documents, stats.described, stats.reused, stats.small, stats.failed) == (4, 3, 2, 2, 0)
    uses = rows(cfg, "SELECT d.name, u.page_no, u.image_no, u.status, u.chunk_id FROM picture_uses u "
                     "JOIN docs d ON d.id = u.doc_id ORDER BY d.name, u.page_no, u.image_no")
    assert [(n, p, i, s) for n, p, i, s, _ in uses] == [
        ("5120_Depileur_DE.md", 1, 1, "described"), ("5120_Depileur_DE.md", 1, 2, "small"),
        ("5120_Depileur_FR.md", 1, 1, "described"), ("5120_Depileur_FR.md", 1, 2, "small"),
        ("Paloma_4R.png", 1, 1, "described"),
        ("deck.pptx", 3, 1, "described"), ("deck.pptx", 3, 2, "described")]
    text = rows(cfg, "SELECT c.text, c.embedded FROM chunks c JOIN picture_uses u ON u.chunk_id = c.id "
                     "JOIN docs d ON d.id = u.doc_id WHERE d.name = '5120_Depileur_FR.md'")[0]
    assert text[0].startswith("\n\n[Image 1 — photo] Photo d'un préhenseur") and text[1] == 0
    assert "Mots-clés : préhenseur, ventouses, gripper, suction cups" in text[0]
    pic = rows(cfg, "SELECT c.text, d.status FROM chunks c JOIN docs d ON d.id = c.doc_id WHERE d.name = 'Paloma_4R.png'")
    assert pic == [("[Image 1 — photo] Fichier : Paloma_4R.png. Photo d'un préhenseur à ventouses sur un robot Delta.\n"
                    "Texte visible : —\nMots-clés : préhenseur, ventouses, gripper, suction cups", "ok")]
    assert len(rows(cfg, "SELECT * FROM picture_docs")) == 4
    assert "Paloma_4R.png" in DocTools(cfg).search("ventouses", mode="keyword")


def test_same_picture_twice_in_a_document_is_described_once(world, fake_ollama):
    cfg, _ = world
    describe.run_describe(cfg, progress=quiet)
    deck = rows(cfg, "SELECT u.sha1, u.chunk_id FROM picture_uses u JOIN docs d ON d.id = u.doc_id "
                     "WHERE d.name = 'deck.pptx'")
    assert len(deck) == 2 and deck[0][0] == deck[1][0] and deck[0][1] != deck[1][1]


def test_second_run_has_nothing_to_do(world, fake_ollama):
    cfg, _ = world
    describe.run_describe(cfg, progress=quiet)
    msgs = []
    describe.run_describe(cfg, progress=msgs.append)
    assert len(fake_ollama.calls) == 3 and any("No picture to describe" in m for m in msgs)


def test_changed_document_gets_its_passages_back_from_the_cache(world, fake_ollama):
    cfg, root = world
    describe.run_describe(cfg, progress=quiet)
    md = root / "5120_Depileur_FR.md"
    md.write_text(md.read_text(encoding="utf-8") + "\nNouvelle phrase.\n", encoding="utf-8")
    run_index(cfg, progress=quiet)
    assert rows(cfg, "SELECT count(*) FROM picture_uses u JOIN docs d ON d.id = u.doc_id "
                     "WHERE d.name = '5120_Depileur_FR.md'")[0][0] == 0
    stats = describe.run_describe(cfg, progress=quiet)
    assert len(fake_ollama.calls) == 3 and stats.reused == 1 and stats.described == 0
    assert rows(cfg, "SELECT count(*) FROM picture_uses u JOIN docs d ON d.id = u.doc_id "
                     "WHERE d.name = '5120_Depileur_FR.md' AND u.status = 'described'")[0][0] == 1


def test_a_new_model_replaces_the_passages(world, fake_ollama):
    cfg, _ = world
    describe.run_describe(cfg, progress=quiet)
    old = {r[0] for r in rows(cfg, "SELECT chunk_id FROM picture_uses WHERE chunk_id IS NOT NULL")}
    fake_ollama.models.append("other-vision")
    cfg.vision_model = "other-vision"
    describe.run_describe(cfg, progress=quiet)
    new = {r[0] for r in rows(cfg, "SELECT chunk_id FROM picture_uses WHERE chunk_id IS NOT NULL")}
    assert len(fake_ollama.calls) == 6 and len(new) == len(old) and not (old & new)
    assert rows(cfg, "SELECT count(*) FROM chunks WHERE id IN (%s)" % ",".join(map(str, old)))[0][0] == 0


def test_logo_is_cached_without_a_passage(world, fake_ollama):
    cfg, _ = world
    fake_ollama.reply = lambda body: answer(kind=vision.LOGO, description="Logo Demaurex.")
    stats = describe.run_describe(cfg, progress=quiet)
    assert stats.logos == 5 and rows(cfg, "SELECT count(*) FROM picture_uses WHERE chunk_id IS NOT NULL")[0][0] == 0
    assert rows(cfg, "SELECT count(*) FROM picture_cache WHERE type = ?", vision.LOGO)[0][0] == 3


def test_invalid_answers_are_retried_then_recorded_and_retry_fixes_them(world, fake_ollama):
    cfg, _ = world
    fake_ollama.reply = lambda body: (200, {"message": {"content": "garbage"}})
    stats = describe.run_describe(cfg, progress=quiet)
    assert len(fake_ollama.calls) == 6 and stats.failed == 5        # 3 pictures x 2 tries; 5 occurrences
    assert {r[0] for r in rows(cfg, "SELECT status FROM picture_uses WHERE status NOT IN ('small')")} == {"error"}
    fake_ollama.reply = lambda body: answer()
    describe.run_describe(cfg, progress=quiet)                      # without --retry: nothing
    assert len(fake_ollama.calls) == 6
    describe.run_describe(cfg, retry=True, progress=quiet)
    assert {r[0] for r in rows(cfg, "SELECT status FROM picture_uses WHERE status NOT IN ('small')")} == {"described"}


def test_a_timeout_is_retried_once_then_recorded(world, fake_ollama):
    cfg, _ = world
    cfg.vision_timeout_s = 0.3
    fake_ollama.delay = 1
    describe.run_describe(cfg, progress=quiet)
    assert {r[0] for r in rows(cfg, "SELECT status FROM picture_uses WHERE status NOT IN ('small')")} == {"timeout"}
    assert len(fake_ollama.calls) == 6


def test_stops_after_too_many_failed_calls_in_a_row(world, fake_ollama, monkeypatch):
    cfg, _ = world
    monkeypatch.setattr(describe, "MAX_FAILED_CALLS_IN_A_ROW", 2)
    fake_ollama.reply = lambda body: (500, {"error": "boom"})
    msgs = []
    describe.run_describe(cfg, progress=msgs.append)
    assert any("in a row failed" in m for m in msgs)
    assert len(rows(cfg, "SELECT * FROM picture_docs")) < 4           # stopped before the end


def test_time_limit_stops_and_the_next_run_continues(world, fake_ollama):
    cfg, _ = world
    msgs = []
    describe.run_describe(cfg, max_minutes=1e-6, progress=msgs.append)
    assert any("Time limit" in m for m in msgs)
    describe.run_describe(cfg, progress=quiet)
    assert len(rows(cfg, "SELECT * FROM picture_docs")) == 4


def test_model_is_unloaded_at_the_end(world, fake_ollama):
    cfg, _ = world
    describe.run_describe(cfg, progress=quiet)
    assert fake_ollama.unloaded == ["test-vision"]


def test_cli_describe_skips_with_exit_code_0(tmp_path, capsys):
    from dmx_docs.cli import main

    (tmp_path / "r").mkdir()
    cfg = tmp_path / "config.toml"
    cfg.write_text(f"[index]\nroots = ['{(tmp_path / 'r').as_posix()}']\ndata_dir = '{(tmp_path / 'd').as_posix()}'\n"
                   "[vision]\nurl = 'http://127.0.0.1:9'\n", encoding="utf-8")
    main(["--config", str(cfg), "describe"])
    assert "skipped" in capsys.readouterr().out
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src /c/dmx-rag/venv/Scripts/python.exe -m pytest tests/test_describe.py -q`
Expected: FAIL (`ModuleNotFoundError: No module named 'dmx_docs.describe'`).

- [ ] **Step 3: Implement `src/dmx_docs/describe.py`**

```python
"""Picture descriptions by a local vision model (sub-project B2).

A separate step between `ocr` and `embed`. It finds documents whose pictures were not handled
with the configured model and DESCRIBE_VERSION, lists their picture occurrences in worker
processes (pictures.py), describes each distinct picture once through Ollama (vision.py; cached
by SHA-1 in picture_cache) and APPENDS one passage (chunk) per occurrence, like OCR: existing
chunks and vectors are never touched, only the new chunks need embedding. Occurrences are
recorded in picture_uses, finished documents in picture_docs (store.py), which makes the step
resumable and lets the tools label picture passages.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass

from . import store, vision
from .extract import PICTURE_EXTS
from .pictures import Prepared, prepare_document

log = logging.getLogger("dmx_docs.describe")

DOC_EXTS = (".md", ".docx", ".pptx", ".ppt", ".pdf") + PICTURE_EXTS
PROGRESS_EVERY_S = 15
STALL_S = 600                 # a document whose pictures are not ready after this long is stuck
MAX_FAILED_CALLS_IN_A_ROW = 20
MAX_PREP_WORKERS = 4          # preparing pictures is light; the GPU is the bottleneck
PAGE_TEXT_CHARS = 2000        # indexed text per page passed to the workers (context uses 800)


def unavailable(cfg) -> str | None:
    """Why pictures cannot be described now, or None."""
    if not cfg.vision_enabled:
        return "Picture descriptions are off for this world ([vision] enabled = false or the world's vision = false)."
    return vision.unavailable(cfg.vision_url, cfg.vision_model)


def prep_options(cfg) -> dict:
    return {"roots": list(cfg.roots), "excluded_dirs": list(cfg.excluded_dirs), "min_side": cfg.vision_min_side,
            "pdf_picture_share": cfg.vision_pdf_picture_share, "max_pdf_pages": cfg.max_pdf_pages,
            "doc_converter": cfg.doc_converter, "libreoffice_path": cfg.libreoffice_path}


def candidates(con, cfg, retry: bool = False) -> list[int]:
    """Documents to handle: never handled with this model and DESCRIBE_VERSION, or (retry) with
    occurrences that failed or timed out."""
    marks = ",".join("?" * len(DOC_EXTS))
    ids = [r[0] for r in con.execute(
        f"""SELECT d.id FROM docs d WHERE d.ext IN ({marks}) AND d.status IN ('ok', 'no_text')
            AND NOT EXISTS (SELECT 1 FROM picture_docs p WHERE p.doc_id = d.id AND p.model = ? AND p.version = ?)
            ORDER BY d.id""", (*DOC_EXTS, cfg.vision_model, vision.DESCRIBE_VERSION))]
    if retry:
        seen = set(ids)
        ids += [r[0] for r in con.execute(
            "SELECT DISTINCT doc_id FROM picture_uses WHERE status IN ('error', 'timeout') ORDER BY doc_id")
            if r[0] not in seen]
    return ids


def job_for(con, doc_id: int) -> dict | None:
    """What a worker needs to prepare one document (no database access in the workers); None if
    the document is gone."""
    d = con.execute("SELECT id, path, ext, title FROM docs WHERE id = ?", (doc_id,)).fetchone()
    if d is None:
        return None
    category = None
    try:
        row = con.execute("SELECT category FROM doc_facets WHERE doc_id = ?", (doc_id,)).fetchone()
        category = row[0] if row else None
    except sqlite3.OperationalError:  # no facets table (yet), or one from before the category column
        pass
    ocr_ids, ocr_chars = set(), {}
    for page, chars, ids in con.execute("SELECT page_no, chars, chunk_ids FROM ocr_pages WHERE doc_id = ?", (doc_id,)):
        ocr_chars[page] = chars or 0
        ocr_ids.update(int(i) for i in (ids or "").split(",") if i)
    picture_ids = {r[0] for r in con.execute(
        "SELECT chunk_id FROM picture_uses WHERE doc_id = ? AND chunk_id IS NOT NULL", (doc_id,))}
    pages: dict[int, str] = {}
    native: dict[int, int] = {}
    for cid, page, text in con.execute("SELECT id, page_no, text FROM chunks WHERE doc_id = ? ORDER BY page_no, seq",
                                       (doc_id,)):
        if cid in picture_ids:
            continue
        if len(pages.get(page, "")) < PAGE_TEXT_CHARS:
            pages[page] = (pages.get(page, "") + " " + text).strip()[:PAGE_TEXT_CHARS]
        if cid not in ocr_ids:
            native[page] = native.get(page, 0) + len(text)
    return {"doc_id": d[0], "path": d[1], "ext": d[2], "title": d[3], "category": category,
            "pages": pages, "native_chars": native, "ocr_chars": ocr_chars}


def marker(image_no: int) -> str:
    """Start of a passage: 'Image 3' (the [Image 3] marker of a Markdown page, the 3rd picture of a
    Word file or of a slide) or 'Page entière' (a whole PDF page)."""
    return "Page entière" if image_no == 0 else f"Image {image_no}"


def _cached(con, cfg, sha1s) -> dict[str, vision.Description]:
    sha1s = list(sha1s)
    out = {}
    for i in range(0, len(sha1s), 500):
        part = sha1s[i:i + 500]
        for sha1, kind, text, visible, keywords in con.execute(
                f"""SELECT sha1, type, description, visible_text, keywords FROM picture_cache
                    WHERE model = ? AND version = ? AND sha1 IN ({','.join('?' * len(part))})""",
                (cfg.vision_model, vision.DESCRIBE_VERSION, *part)):
            out[sha1] = vision.Description(kind, text, visible or "", json.loads(keywords or "[]"))
    return out


def save_document(con, cfg, job: dict, prepared: list[Prepared], described: dict) -> bool:
    """Record one document: new cache rows, its occurrences and their passages (replacing the
    passages of an earlier run), and its picture_docs row. `described` maps sha1 to a Description
    or to the VisionError of its last try. The caller owns the transaction. True if vectors were removed."""
    doc_id = job["doc_id"]
    now = time.time()
    for sha1, d in described.items():
        if isinstance(d, vision.Description) and d.seconds:  # new from the model (cached ones have 0 s)
            con.execute("""INSERT OR REPLACE INTO picture_cache(sha1, model, version, type, description, visible_text,
                           keywords, seconds, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (sha1, cfg.vision_model, vision.DESCRIBE_VERSION, d.type, d.description, d.visible_text,
                         json.dumps(d.keywords, ensure_ascii=False), d.seconds, now))
    old = [r[0] for r in con.execute("SELECT chunk_id FROM picture_uses WHERE doc_id = ? AND chunk_id IS NOT NULL",
                                     (doc_id,))]
    removed = store.delete_chunks(con, old)
    con.execute("DELETE FROM picture_uses WHERE doc_id = ?", (doc_id,))
    file_name = os.path.basename(job["path"]) if job["ext"] in PICTURE_EXTS else None
    added = False
    for p in prepared:
        status, kind, chunk_id, error = p.status, None, None, p.error
        if p.status == "ready":
            d = described.get(p.sha1)
            if isinstance(d, vision.Description):
                kind = d.type
                if d.type == vision.LOGO:
                    status = "logo"
                else:
                    status = "described"
                    count, last_seq = con.execute("SELECT count(*), max(seq) FROM chunks WHERE doc_id = ? AND page_no = ?",
                                                  (doc_id, p.page_no)).fetchone()
                    text = ("\n\n" if count else "") + vision.passage_text(d, marker(p.image_no), file_name)
                    cur = con.execute("INSERT INTO chunks(doc_id, page_no, seq, text) VALUES(?, ?, ?, ?)",
                                      (doc_id, p.page_no, (last_seq + 1) if count else 0, text))
                    con.execute("INSERT INTO chunks_fts(rowid, text) VALUES(?, ?)", (cur.lastrowid, text))
                    chunk_id = cur.lastrowid
                    added = True
            else:
                status = "timeout" if getattr(d, "timeout", False) else "error"
                error = str(d) if d is not None else "not described"
        con.execute("""INSERT OR REPLACE INTO picture_uses(doc_id, page_no, image_no, sha1, status, type, chunk_id,
                       error, done_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (doc_id, p.page_no, p.image_no, p.sha1, status, kind, chunk_id, error, now))
    if added:
        con.execute("UPDATE docs SET status = 'ok', error = NULL WHERE id = ? AND status = 'no_text'", (doc_id,))
    con.execute("INSERT OR REPLACE INTO picture_docs(doc_id, model, version, pictures, done_at) VALUES(?, ?, ?, ?, ?)",
                (doc_id, cfg.vision_model, vision.DESCRIBE_VERSION, len(prepared), now))
    return removed


@dataclass
class DescribeStats:
    documents: int = 0
    described: int = 0   # model calls that gave a description
    reused: int = 0      # occurrences served from the cache (or shared with another occurrence)
    small: int = 0
    logos: int = 0
    failed: int = 0

    def summary(self) -> str:
        return (f"{self.documents} documents: {self.described} pictures described by the model, {self.reused} reused "
                f"from earlier descriptions, {self.small} too small, {self.logos} logos/decoration, {self.failed} failed")


def _call(cfg, image: bytes, context: str) -> vision.Description:
    """One description, tried twice (a timeout or an invalid answer is often a one-off)."""
    try:
        return vision.describe_image(cfg.vision_url, cfg.vision_model, image, context, cfg.vision_timeout_s)
    except vision.VisionError:
        return vision.describe_image(cfg.vision_url, cfg.vision_model, image, context, cfg.vision_timeout_s)


def run_describe(cfg, max_minutes: float | None = None, retry: bool = False, progress=print) -> DescribeStats:
    from .indexer import _MP, _terminate_pool

    stats = DescribeStats()
    why = unavailable(cfg)
    if why:
        progress(why)
        progress("Picture descriptions skipped.")
        return stats
    con = store.connect(cfg.db_path)
    con.isolation_level = None  # explicit transactions, one per document
    queue = candidates(con, cfg, retry)
    if not queue:
        progress("No picture to describe.")
        con.close()
        return stats
    total = len(queue)
    workers = max(1, min(MAX_PREP_WORKERS, int(cfg.workers)))
    progress(f"{total} documents to examine for pictures ({workers} workers, model {cfg.vision_model}).")
    options = prep_options(cfg)
    deadline = time.perf_counter() + max_minutes * 60 if max_minutes else None
    calls = ThreadPoolExecutor(max_workers=max(1, int(cfg.vision_parallel_requests)))
    failed_in_a_row = 0
    last_error = ""

    def new_pool() -> ProcessPoolExecutor:
        return ProcessPoolExecutor(max_workers=workers, mp_context=_MP)

    def save(job: dict, prepared: list[Prepared], described: dict) -> None:
        con.execute("BEGIN IMMEDIATE")
        try:
            if save_document(con, cfg, job, prepared, described):
                store.bump_vector_version(con)
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        stats.documents += 1

    def handle(job: dict, prepared: list[Prepared]) -> None:
        nonlocal failed_in_a_row, last_error
        ready = [p for p in prepared if p.status == "ready"]
        described: dict = _cached(con, cfg, {p.sha1 for p in ready})
        todo = {}
        for p in ready:
            if p.sha1 not in described and p.sha1 not in todo:
                todo[p.sha1] = p
        futures = {sha1: calls.submit(_call, cfg, p.image, p.context) for sha1, p in todo.items()}
        for sha1, fut in futures.items():
            try:
                described[sha1] = fut.result()
                stats.described += 1
                failed_in_a_row = 0
            except vision.VisionError as e:
                described[sha1] = e
                failed_in_a_row += 1
                last_error = str(e)
                log.info("describe failed: %s (%s)", job["path"], e)
        sent: set[str] = set()   # the first occurrence of each picture sent to the model
        for p in prepared:
            if p.status == "small":
                stats.small += 1
            elif p.status == "error":
                stats.failed += 1
                log.info("picture error: %s p.%d image %d (%s)", job["path"], p.page_no, p.image_no, p.error)
            else:
                d = described.get(p.sha1)
                if not isinstance(d, vision.Description):
                    stats.failed += 1
                elif d.type == vision.LOGO:
                    stats.logos += 1
                elif p.sha1 in futures and p.sha1 not in sent:
                    sent.add(p.sha1)
                else:
                    stats.reused += 1   # from the cache, or a second occurrence of a picture of this document
        save(job, prepared, described)

    def too_many_failures() -> bool:
        return failed_in_a_row >= MAX_FAILED_CALLS_IN_A_ROW

    def doc_failed(job: dict, status: str, message: str) -> None:
        """The whole document failed (worker crash, hang): one occurrence (0, 0); --retry redoes it."""
        save(job, [Prepared(0, 0, status, error=message)], {})
        stats.failed += 1

    executor = new_pool()
    pending: dict = {}   # future -> (job, submitted at)
    started = last_progress = time.perf_counter()
    try:
        while queue or pending:
            while (queue and len(pending) < workers and not too_many_failures()
                   and not (deadline and time.perf_counter() > deadline)):
                job = job_for(con, queue.pop(0))
                if job is None:
                    continue  # deleted since the list was made
                try:
                    fut = executor.submit(prepare_document, job, options)
                except BrokenProcessPool:
                    _terminate_pool(executor)
                    executor = new_pool()
                    fut = executor.submit(prepare_document, job, options)
                pending[fut] = (job, time.perf_counter())
            if not pending:
                break  # time limit reached, or stopped after too many failed calls
            done, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
            broken = False
            for fut in done:
                job, _ = pending.pop(fut)
                try:
                    prepared = fut.result()
                except BrokenProcessPool:
                    broken = True
                    doc_failed(job, "error", "picture preparation crashed (this document or one prepared at the same time)")
                    continue
                except Exception as e:  # noqa: BLE001
                    prepared = [Prepared(0, 0, "error", error=f"{type(e).__name__}: {e}"[:300])]
                handle(job, prepared)
            now = time.perf_counter()
            stuck = [f for f, (_, t) in pending.items() if not f.done() and now - t > STALL_S]
            if broken or stuck:
                for f in list(pending):
                    job, _ = pending.pop(f)
                    if f in stuck:
                        doc_failed(job, "timeout", f"pictures not ready within {STALL_S // 60} min")
                    else:
                        doc_failed(job, "error", "picture preparation crashed (this document or one prepared "
                                                 "at the same time)")
                _terminate_pool(executor)
                executor = new_pool()
            if now - last_progress >= PROGRESS_EVERY_S:
                rate = stats.documents / max(now - started, 1e-6)
                progress(f"  {stats.documents}/{total} documents, {stats.described} pictures described "
                         f"({stats.described / max((now - started) / 60, 1e-6):.0f}/min), "
                         f"~{(total - stats.documents) / max(rate, 1e-6) / 3600:.1f} h remaining")
                last_progress = now
    except KeyboardInterrupt:
        progress("Interrupted - the documents handled so far are saved.")
        raise
    finally:
        _terminate_pool(executor)
        calls.shutdown(wait=False, cancel_futures=True)
        con.close()
        vision.unload(cfg.vision_url, cfg.vision_model)
    if too_many_failures():
        progress(f"{MAX_FAILED_CALLS_IN_A_ROW} calls in a row failed (last error: {last_error}). Stopped: check "
                 "Ollama, then run again; the failed pictures can be described again with --retry.")
    elif queue:
        progress(f"Time limit reached: {len(queue)} documents left for the next run.")
    progress(f"Pictures done in {(time.perf_counter() - started) / 60:.1f} min: {stats.summary()}")
    if stats.failed:
        launcher = f"dmx.ps1 describe -World {cfg.world} --retry" if cfg.world else "dmx.ps1 describe --retry"
        progress(f"{stats.failed} pictures failed: describe them again with `dmx-docs describe --retry` "
                 f"(launcher: {launcher})")
    return stats
```

- [ ] **Step 4: Add the CLI command**

In `cli.py`, module docstring: `"""Command line: dmx-docs web | index | ocr | describe | embed | status | search | read | serve."""`. After the `ocr` parser:

```python
    p = sub.add_parser("describe", help="describe pictures with the local vision model (Ollama); run before embed")
    p.add_argument("--max-minutes", type=float, help="stop after this many minutes (resume later)")
    p.add_argument("--retry", action="store_true", help="describe again the pictures that failed or timed out")
```

After the `ocr` branch:

```python
    elif args.command == "describe":
        from .describe import run_describe
        _setup_logging(cfg, "describe")
        try:
            run_describe(cfg, max_minutes=args.max_minutes, retry=args.retry)
        except KeyboardInterrupt:
            print("Stopped. Run `dmx-docs describe` again to continue.")
            sys.exit(130)
```

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=src /c/dmx-rag/venv/Scripts/python.exe -m pytest tests/test_describe.py -q`
Expected: PASS. Then the whole suite: `PYTHONPATH=src /c/dmx-rag/venv/Scripts/python.exe -m pytest -q` → all pass (1 skip without LibreOffice).

- [ ] **Step 6: Commit**

```bash
git add src/dmx_docs/describe.py src/dmx_docs/cli.py tests/test_describe.py
git commit -m "describe step: picture passages from the local vision model, cached, resumable

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Model comparison page (`describe --compare`)

**Files:**
- Modify: `src/dmx_docs/describe.py` (new `run_compare`, `_compare_html`)
- Modify: `src/dmx_docs/cli.py` (`--compare`, `--sample`, `--out`)
- Test: `tests/test_describe.py` (two tests appended)

**Interfaces:**
- Consumes: `candidates`, `job_for`, `prep_options`, `prepare_document`, `vision.unavailable`, `vision.describe_image`, `vision.unload`, `pictures.scale_picture`.
- Produces: `run_compare(cfg, models: list[str], sample: int = 25, out_dir: str | None = None, progress=print) -> str | None` (path of the HTML page, None if nothing to compare). Never writes to the index.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_describe.py`)

```python
def test_compare_writes_a_page_and_leaves_the_index_alone(world, fake_ollama, tmp_path):
    cfg, _ = world
    fake_ollama.models.append("other-vision")
    before = rows(cfg, "SELECT count(*) FROM chunks")[0][0]
    path = describe.run_compare(cfg, ["test-vision", "other-vision"], sample=3, out_dir=str(tmp_path / "out"),
                                progress=quiet)
    html = open(path, encoding="utf-8").read()
    assert "test-vision" in html and "other-vision" in html and "Photo d'un préhenseur" in html
    assert html.count("<tr><td class=n>") == 3
    assert rows(cfg, "SELECT count(*) FROM chunks")[0][0] == before
    assert rows(cfg, "SELECT count(*) FROM picture_uses")[0][0] == 0
    assert rows(cfg, "SELECT count(*) FROM picture_cache")[0][0] == 0


def test_compare_reports_a_missing_model(world, fake_ollama, tmp_path):
    cfg, _ = world
    msgs = []
    path = describe.run_compare(cfg, ["test-vision", "missing:7b"], sample=2, out_dir=str(tmp_path / "o"),
                                progress=msgs.append)
    assert any("ollama pull missing:7b" in m for m in msgs)
    assert "missing:7b" in open(path, encoding="utf-8").read()
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src /c/dmx-rag/venv/Scripts/python.exe -m pytest tests/test_describe.py -k compare -q`
Expected: FAIL (`AttributeError: module 'dmx_docs.describe' has no attribute 'run_compare'`).

- [ ] **Step 3: Implement** (append to `describe.py`; add `import base64, html, random, statistics` to the imports)

```python
def run_compare(cfg, models: list[str], sample: int = 25, out_dir: str | None = None, progress=print) -> str | None:
    """Describe a fixed sample of the world's pictures with each model and write a side-by-side HTML
    page (thumbnail, context, each model's answer and seconds). Read-only for the index."""
    from .pictures import scale_picture

    con = store.connect(cfg.db_path)
    try:
        ids = [r[0] for r in con.execute(
            f"SELECT id FROM docs WHERE ext IN ({','.join('?' * len(DOC_EXTS))}) AND status IN ('ok', 'no_text')",
            DOC_EXTS)]
        random.Random(7).shuffle(ids)
        options, items, seen = prep_options(cfg), [], set()
        for doc_id in ids:
            if len(items) >= sample:
                break
            job = job_for(con, doc_id)
            ready = [p for p in prepare_document(job, options) if p.status == "ready" and p.sha1 not in seen]
            for p in random.Random(doc_id).sample(ready, min(2, len(ready))):   # at most 2 per document
                seen.add(p.sha1)
                items.append((job["path"], p))
    finally:
        con.close()
    items = items[:sample]
    if not items:
        progress("No picture to compare in this world.")
        return None
    progress(f"{len(items)} pictures sampled; models: {', '.join(models)}.")
    results: dict[str, list] = {}
    for model in models:
        why = vision.unavailable(cfg.vision_url, model)
        if why:
            progress(why)
            results[model] = [why] * len(items)
            continue
        results[model] = []
        for i, (path, p) in enumerate(items, 1):
            try:
                d = vision.describe_image(cfg.vision_url, model, p.image, p.context, cfg.vision_timeout_s)
                results[model].append(d)
                progress(f"  {model} {i}/{len(items)} {d.seconds:.1f} s  {d.type}: {d.description[:80]}")
            except vision.VisionError as e:
                results[model].append(str(e))
                progress(f"  {model} {i}/{len(items)} failed: {e}")
        vision.unload(cfg.vision_url, model)
    out_dir = out_dir or str(cfg.logs_dir)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"describe-compare-{cfg.world or 'index'}-{time.strftime('%Y%m%d-%H%M')}.html")
    thumbs = [base64.b64encode(scale_picture(p.image, 300)[0]).decode("ascii") for _, p in items]
    with open(path, "w", encoding="utf-8") as f:
        f.write(_compare_html(cfg, models, items, thumbs, results))
    progress(f"Written: {path}")
    return path


def _compare_html(cfg, models, items, thumbs, results) -> str:
    e = html.escape
    summary = []
    for m in models:
        secs = [r.seconds for r in results[m] if isinstance(r, vision.Description)]
        failed = sum(not isinstance(r, vision.Description) for r in results[m])
        if secs:
            med = statistics.median(secs)
            summary.append(f"<li><b>{e(m)}</b>: median {med:.1f} s per picture, {failed} failed; "
                           f"1,000 pictures ≈ {1000 * med / 3600:.1f} h</li>")
        else:
            summary.append(f"<li><b>{e(m)}</b>: no description, {failed} failed</li>")
    rows = []
    for i, ((path, p), thumb) in enumerate(zip(items, thumbs)):
        mime = "image/png" if base64.b64decode(thumb)[:4] == b"\x89PNG" else "image/jpeg"
        where = f"page {p.page_no}" + (f", image {p.image_no}" if p.image_no else " (whole page)")
        cells = [f"<td class=pic><img src='data:{mime};base64,{thumb}'><div class=src>{e(path)} — {where}</div>"
                 f"<details><summary>context</summary><pre>{e(p.context)}</pre></details></td>"]
        for m in models:
            r = results[m][i]
            if isinstance(r, vision.Description):
                cells.append(f"<td><b>{e(r.type)}</b> · {r.seconds:.1f} s<p>{e(r.description)}</p>"
                             f"<p class=vis>Texte visible : {e(r.visible_text or '—')}</p>"
                             f"<p class=kw>{e(', '.join(r.keywords))}</p></td>")
            else:
                cells.append(f"<td class=err>{e(str(r))}</td>")
        rows.append(f"<tr><td class=n>{i + 1}</td>{''.join(cells)}</tr>")
    head = "".join(f"<th>{e(m)}</th>" for m in models)
    return f"""<!doctype html><html><head><meta charset=utf-8><title>Vision models</title><style>
body{{font:14px Segoe UI,Arial,sans-serif;margin:16px;background:#fff;color:#222}}
table{{border-collapse:collapse;width:100%}} td,th{{border:1px solid #ccc;vertical-align:top;padding:8px}}
th{{background:#f3f3f3;position:sticky;top:0}} td.pic{{width:300px}} td.pic img{{max-width:300px;max-height:260px}}
.src{{font-size:11px;color:#666;word-break:break-all}} pre{{white-space:pre-wrap;font-size:11px}}
.vis{{color:#555}} .kw{{color:#06c;font-size:12px}} .err{{color:#b00}} td.n{{width:24px}}
</style></head><body><h1>Vision models — {e(cfg.world_title or cfg.world or 'index')} — {time.strftime('%Y-%m-%d %H:%M')}</h1>
<ul>{''.join(summary)}</ul><table><tr><th>#</th><th>picture</th>{head}</tr>{''.join(rows)}</table></body></html>"""
```

In `cli.py`, on the `describe` parser:

```python
    p.add_argument("--compare", metavar="MODELS",
                   help="comma-separated Ollama models: describe a sample with each and write a side-by-side "
                        "HTML page (the index is not changed)")
    p.add_argument("--sample", type=int, default=25, help="pictures in the --compare sample (default 25)")
    p.add_argument("--out", help="folder for the --compare page (default: the world's logs folder)")
```

and in the `describe` branch, before `run_describe`:

```python
        if args.compare:
            from .describe import run_compare
            run_compare(cfg, [m.strip() for m in args.compare.split(",") if m.strip()], sample=args.sample,
                        out_dir=args.out)
            return
```

- [ ] **Step 4: Run the tests**

Run: `PYTHONPATH=src /c/dmx-rag/venv/Scripts/python.exe -m pytest tests/test_describe.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dmx_docs/describe.py src/dmx_docs/cli.py tests/test_describe.py
git commit -m "describe --compare: side-by-side page of vision models on a sample, index untouched

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Picture passages in the tools and the server

**Files:**
- Modify: `src/dmx_docs/search.py` (`search(..., pictures_only=False)`)
- Modify: `src/dmx_docs/tools.py` (`_picture_chunks`, `_picture_label`, `search(..., pictures=False)`, `_pages` info, `read_document` note, `index_status`)
- Modify: `src/dmx_docs/server.py` (`pictures` on the 4 search functions, `PICTURES_NOTE` in `instructions_for`)
- Test: `tests/test_picture_tools.py`

**Interfaces:**
- Consumes: `describe.save_document`, `describe.job_for` (Task 5), `pictures.Prepared` (Task 4), `vision.Description` (Task 3), `store.has_table` (Task 1).
- Produces: `Searcher.search(..., pictures_only: bool = False)`; `DocTools.search(..., pictures: bool = False)`; `server.PICTURES_NOTE: str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_picture_tools.py
import asyncio

from conftest import make_png, make_pptx
from dmx_docs import describe, store, vision
from dmx_docs.extract import pymupdf
from dmx_docs.indexer import run_index
from dmx_docs.pictures import Prepared
from dmx_docs.tools import DocTools

quiet = lambda *a, **k: None  # noqa: E731


def add_passages(cfg, name, occurrences):
    """occurrences: [(page, image, description)] saved as described pictures of document `name`."""
    con = store.connect(cfg.db_path)
    con.isolation_level = None
    doc_id = con.execute("SELECT id FROM docs WHERE name = ?", (name,)).fetchone()[0]
    job = describe.job_for(con, doc_id)
    prepared = [Prepared(p, i, "ready", sha1=f"{name}{p}{i}") for p, i, _ in occurrences]
    described = {f"{name}{p}{i}": vision.Description("photo", d, "", ["robot"], seconds=1.0) for p, i, d in occurrences}
    con.execute("BEGIN")
    describe.save_document(con, cfg, job, prepared, described)
    con.execute("COMMIT")
    con.close()


def setup(tmp_path, make_cfg):
    root = tmp_path / "Docs"
    root.mkdir()
    (root / "page_FR.md").write_text("# Dépileur\n\nRéglage du dépileur Zorglub.\n\n![Outil](x.png)\n", encoding="utf-8")
    doc = pymupdf.open()
    for text in ("Brochure Zorglub page un", "Brochure page deux"):
        doc.new_page().insert_text((50, 72), text)
    doc.save(root / "brochure.pdf")
    make_png(root / "p.png", (1, 2, 3), size=(300, 200))
    make_pptx(root / "deck.pptx", pictures=[root / "p.png"])
    cfg = make_cfg(root, extensions=[".md", ".pdf", ".pptx"])
    run_index(cfg, progress=quiet)
    add_passages(cfg, "page_FR.md", [(1, 1, "Main tenant un outil Zorglub en bois.")])
    add_passages(cfg, "brochure.pdf", [(2, 0, "Ligne Zorglub complète avec robots Delta.")])
    add_passages(cfg, "deck.pptx", [(3, 1, "Robot Zorglub vu de face.")])
    return cfg, root


def test_search_labels_picture_hits(tmp_path, make_cfg):
    cfg, _ = setup(tmp_path, make_cfg)
    out = DocTools(cfg).search("Zorglub", mode="keyword", limit=20)
    assert "page 1/1 — picture 1 (view_page image=1)" in out
    assert "page 2/2 — whole-page picture (view_page page=2)" in out
    assert "slide 3/3 — picture 1 (view_page page=3 image=1)" in out


def test_pictures_only_search(tmp_path, make_cfg):
    cfg, _ = setup(tmp_path, make_cfg)
    out = DocTools(cfg).search("Zorglub", mode="keyword", limit=20, pictures=True)
    hits = [l for l in out.splitlines() if l.startswith("[")]
    assert len(hits) == 3 and all("picture" in h for h in hits)
    assert "Réglage du dépileur" not in out and "Brochure Zorglub page un" not in out


def test_read_document_shows_descriptions_with_a_note(tmp_path, make_cfg):
    cfg, root = setup(tmp_path, make_cfg)
    text = DocTools(cfg).read_document(str(root / "page_FR.md"))
    assert "[Image 1 — photo] Main tenant un outil Zorglub en bois." in text
    assert "written by a vision model" in text
    plain = DocTools(cfg).read_document(str(root / "brochure.pdf"), 1, 1)
    assert "vision model" not in plain                     # page 1 has no picture passage


def test_index_status_counts_pictures(tmp_path, make_cfg):
    cfg, _ = setup(tmp_path, make_cfg)
    status = DocTools(cfg).index_status()
    assert "Pictures: 3 described (photo 3), 0 too small, 0 logos, 0 failed; model gemma4:12b" in status


def test_tools_work_on_an_index_without_picture_tables(tmp_path, make_cfg):
    cfg, root = setup(tmp_path, make_cfg)
    con = store.connect(cfg.db_path)
    for t in ("picture_uses", "picture_docs", "picture_cache"):
        con.execute(f"DROP TABLE {t}")
    con.commit()
    con.close()
    t = DocTools(cfg)
    assert "Zorglub" in t.search("Zorglub", mode="keyword")
    assert "No picture descriptions in this index yet" in t.search("Zorglub", pictures=True)
    assert "Réglage du dépileur" in t.read_document(str(root / "page_FR.md"))
    assert "Pictures: not described yet" in t.index_status()


def test_every_search_tool_has_the_pictures_option_and_the_note(tmp_path, make_cfg):
    from mcp import Client

    from dmx_docs.server import PICTURES_NOTE, build_server, instructions_for

    cfg, _ = setup(tmp_path, make_cfg)
    for profile in ("projects", "marketing", "documentation", "none"):
        cfg.profile = profile

        async def run():
            async with Client(build_server(cfg)) as client:
                return {t.name: t for t in (await client.list_tools()).tools}

        tool = asyncio.run(run())["search"]
        props = (getattr(tool, "input_schema", None) or tool.inputSchema)["properties"]
        assert "pictures" in props, profile
        assert PICTURES_NOTE in instructions_for(cfg)
    assert "pictures=true" in PICTURES_NOTE and "view_page" in PICTURES_NOTE
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src /c/dmx-rag/venv/Scripts/python.exe -m pytest tests/test_picture_tools.py -q`
Expected: FAIL (`TypeError: ... unexpected keyword argument 'pictures'`).

- [ ] **Step 3: `search.py`**

Signature: `def search(self, con, query: str, limit: int = 10, folder: str | None = None, file_type: str | None = None, modified_after: str | None = None, mode: str = "hybrid", allowed=None, facets: dict | None = None, expand: bool | None = None, pictures_only: bool = False) -> list[Hit]:`

After `clauses, params = self._filters(folder, file_type, modified_after, facets)`:

```python
        if pictures_only:  # passages written from pictures by the vision model (describe.py)
            clauses.append("c.id IN (SELECT chunk_id FROM picture_uses WHERE chunk_id IS NOT NULL)")
```

(`keyword()` and the final row query both alias `chunks` as `c`, so the clause works in both; `filtered` becomes True, so the semantic list is widened to 2,000 as for facet filters.)

- [ ] **Step 4: `tools.py`**

Helpers after `_ocr_text_pages`:

```python
def _picture_chunks(con, chunk_ids) -> dict[int, tuple[int, int]]:
    """chunk_id -> (page, picture number; 0 = whole page) of passages written from pictures."""
    ids = list(chunk_ids)
    if not ids or not store.has_table(con, "picture_uses"):
        return {}
    return {r[0]: (r[1], r[2]) for r in con.execute(
        f"SELECT chunk_id, page_no, image_no FROM picture_uses WHERE chunk_id IN ({','.join('?' * len(ids))})", ids)}


def _picture_label(ext: str, page: int, image: int) -> str:
    if image == 0:
        return f" — whole-page picture (view_page page={page})"
    where = f"page={page} image={image}" if ext in SLIDE_EXTS else f"image={image}"
    return f" — picture {image} (view_page {where})"


def _picture_pages(con, doc_id: int) -> list[int]:
    if not store.has_table(con, "picture_uses"):
        return []
    return [r[0] for r in con.execute(
        "SELECT DISTINCT page_no FROM picture_uses WHERE doc_id = ? AND chunk_id IS NOT NULL ORDER BY page_no", (doc_id,))]
```

`DocTools.search`: add parameter `pictures: bool = False` (last). After `con = self._con()` and inside the `try`, before the searcher call:

```python
            if pictures and not store.has_table(con, "picture_uses"):
                return "No picture descriptions in this index yet (the describe step has not run on it)."
```

pass `pictures_only=bool(pictures)` to `self.searcher.search(...)`, and after `ocr_pages = ...`:

```python
            picture_hits = _picture_chunks(con, [h.chunk_id for h in hits])
```

In the hit line:

```python
            ocr_mark = " (OCR)" if (h.doc_id, h.page_no) in ocr_pages else ""
            pic = picture_hits.get(h.chunk_id)
            pic_mark = _picture_label(h.ext, *pic) if pic else ""
            lines.append(f"[{i}] {h.path} — {_page_label(h.ext)} {h.page_no}/{h.n_pages}{pic_mark}{ocr_mark} "
                         f"({TYPE_LABEL.get(h.ext, h.ext)}, modified {_date(h.mtime)}) [{match}]")
```

When `pictures` is set and there are no hits, the existing "No results" message applies; add `"(pictures only)"` to `notes` when `pictures` is True so the user sees the filter: `if pictures: notes.append("(only passages written from pictures)")`.

`_pages` (indexed branch): `info.update(..., picture_pages=_picture_pages(con, doc["id"]))`.

`read_document`, after the OCR note:

```python
        pic_shown = [p for p in info.get("picture_pages", ()) if start_page <= p <= shown_until]
        if pic_shown:
            header += ("\nNote: passages starting with [Image N — type] or [Page entière — type] were written by a "
                       "vision model from the pictures: look at the picture with view_page before quoting a "
                       "precise value (number, label, code) from one.")
```

`index_status`: inside the `try`, add

```python
            pictures = None
            if store.has_table(con, "picture_uses"):
                pictures = {
                    "status": dict(con.execute("SELECT status, count(*) FROM picture_uses GROUP BY status").fetchall()),
                    "types": con.execute("""SELECT type, count(*) n FROM picture_uses WHERE status = 'described'
                                            GROUP BY type ORDER BY n DESC""").fetchall(),
                    "models": [r[0] for r in con.execute("SELECT DISTINCT model FROM picture_docs ORDER BY model")]}
```

and after the OCR line is inserted:

```python
        if pictures and pictures["status"]:
            s = pictures["status"]
            types = ", ".join(f"{t} {n}" for t, n in pictures["types"])
            line = (f"Pictures: {s.get('described', 0)} described" + (f" ({types})" if types else "")
                    + f", {s.get('small', 0)} too small, {s.get('logo', 0)} logos, "
                    f"{s.get('error', 0) + s.get('timeout', 0)} failed; model {', '.join(pictures['models']) or '?'}")
        else:
            line = "Pictures: not described yet"
        lines.insert(lines.index(next(l for l in lines if l.startswith("OCR:"))) + 1, line)
```

- [ ] **Step 5: `server.py`**

Add before `instructions_for`:

```python
PICTURES_NOTE = """\
Passages marked "picture N" or "whole-page picture" in search results were written by a vision model
from the pictures (photos, schemas, screenshots, picture pages). Use them to find and choose pictures
- open one with view_page (the page/image given in the hit), save it for a presentation with
export_image - and to answer questions about what pictures show. To find a picture, search with
pictures=true. Look at the picture with view_page before quoting a precise value (number, label,
code) from a description.
"""
```

`instructions_for`: compute `base` as today, then `return base + "\n" + PICTURES_NOTE`.

Each of `search`, `marketing_search`, `plain_search`, `documentation_search` gets a last parameter `pictures: bool = False`, passed as `pictures=pictures` to `tools.search`, and this docstring line before the facet note / at the end of Args:

```
            pictures: True to search only the descriptions of pictures (photos, schemas, screenshots,
                picture pages), e.g. to find a picture to show or export.
```

- [ ] **Step 6: Run the tests**

Run: `PYTHONPATH=src /c/dmx-rag/venv/Scripts/python.exe -m pytest tests/test_picture_tools.py tests/test_ocr_tools.py tests/test_tools.py tests/test_server.py tests/test_documentation.py -q`
Expected: PASS. (If an existing server test compares instructions text exactly, update it to expect the appended `PICTURES_NOTE`.)

- [ ] **Step 7: Commit**

```bash
git add src/dmx_docs/search.py src/dmx_docs/tools.py src/dmx_docs/server.py tests/test_picture_tools.py
git commit -m "Picture passages in search (labels, pictures=true), read_document note, index_status, instructions

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: `dmx.ps1`, launcher and documentation

**Files:**
- Modify: `scripts/dmx.ps1` (doc comment, `ValidateSet`, `switch`)
- Modify: `scripts/launchers/7 - Update a world (index + embeddings).cmd` (rem text)
- Modify: `tests/test_dmx_ps1.py`
- Modify: `README.md`, `config.example.toml`

**Interfaces:**
- Consumes: CLI `describe` (Task 5/6).
- Produces: `dmx.ps1 describe -World W [args]`; `update` runs `index`, `ocr`, `describe`, `embed`.

- [ ] **Step 1: Write the failing tests** (edit/append in `tests/test_dmx_ps1.py`)

In `test_update_that_succeeds_says_done_and_exits_zero`, replace the order assertion with:

```python
    assert (out.index("--world projects index") < out.index("--world projects ocr")
            < out.index("--world projects describe") < out.index("--world projects embed"))
```

Append:

```python
def test_describe_command_runs_locked_for_one_world(ready):
    shared, local, env = ready
    r = run(shared, local, "describe", "-World", "projects", "--max-minutes", "5", env=env)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "Running: dmx-docs --world projects describe --max-minutes 5" in r.stdout
    assert not (shared / "worlds" / "projects" / "LOCK").exists()
```

- [ ] **Step 2: Run them to see them fail**

Run: `PYTHONPATH=src /c/dmx-rag/venv/Scripts/python.exe -m pytest tests/test_dmx_ps1.py -q`
Expected: FAIL (`describe` not in ValidateSet; no describe step in update).

- [ ] **Step 3: Implement `dmx.ps1`**

Doc comment lines:

```
  ocr [args]     read scanned PDF pages (OCR), e.g. ocr --max-minutes 300 or ocr --retry
  describe [args] describe pictures with the local vision model (Ollama), e.g. describe --max-minutes 600,
                 describe --retry, or describe --compare gemma4:12b,qwen3-vl:8b (side-by-side page, index unchanged)
  update         index, then OCR, then picture descriptions, then embed: unattended run, e.g. on the GPU machine
                 (a failing step stops the run, is named in the last message and becomes the exit code)
```

`ValidateSet('setup', 'web', 'index', 'embed', 'ocr', 'describe', 'update', 'pull', 'status', 'search', 'unlock', 'migrate')`

In the `switch`:

```powershell
        'describe' { Invoke-Locked 'describe' @(, (@('describe') + $Rest)) }
        'update' { Invoke-Locked 'update' @(@('index'), @('ocr'), @('describe'), @('embed')) }
```

Launcher 7, second line:

```
rem Unattended update of one world: index new and changed files, read scanned pages (OCR), describe pictures (Ollama, when installed), then compute embeddings.
```

- [ ] **Step 4: Documentation**

`config.example.toml`, after the `[ocr]` section:

```toml
# Picture descriptions by a local vision model (dmx-docs describe; part of `update`). Needs Ollama
# on the machine that runs the step (https://ollama.com) and the model: `ollama pull gemma4:12b`.
# Without Ollama the step is skipped with a message. Pictures are sent only to this url.
# [vision]
# enabled = true
# url = 'http://127.0.0.1:11434'
# model = 'gemma4:12b'
# timeout_s = 180
# min_side = 150            # smaller pictures (icons) are not described
# pdf_picture_share = 0.15  # a PDF page with text is described when pictures cover this share of it
# parallel_requests = 1
```

and in the commented worlds examples: `vision = false` under the projects example with the comment `# picture descriptions off for this world`, and the marketing example's extensions extended with `'.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tif', '.tiff'` with the comment `# picture files become one-page documents, described by the describe step`.

`README.md`, in "How it works", a bullet after the OCR one:

```markdown
- **Picture descriptions** (`dmx-docs describe`, part of `update`): a local vision model in
  [Ollama](https://ollama.com) describes each picture once (Markdown pictures, pictures in Word
  and PowerPoint files, PDF pages that are mostly pictures, picture files) in French with French
  and English keywords. Each description becomes a passage of its document, marked
  `picture N` / `whole-page picture` in search results; `search(pictures=true)` searches only them.
  Install Ollama and `ollama pull gemma4:12b` on the GPU machine; `dmx-docs describe --compare
  a,b` writes a side-by-side page to choose a model. Without Ollama the step is skipped.
```

- [ ] **Step 5: Run the whole suite**

Run: `PYTHONPATH=src /c/dmx-rag/venv/Scripts/python.exe -m pytest -q`
Expected: all pass (1 skipped without LibreOffice).

- [ ] **Step 6: Commit**

```bash
git add scripts/dmx.ps1 "scripts/launchers/7 - Update a world (index + embeddings).cmd" tests/test_dmx_ps1.py README.md config.example.toml
git commit -m "dmx.ps1 describe; update = index, OCR, picture descriptions, embeddings; docs

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## After the tasks (rollout, with the user's OK for each external step)

1. Final review of the whole branch; fix Important findings.
2. Shared config (backup first, written with the Write tool + Python, bytes checked): `[vision]` with the model the user picked from the comparison; `vision = false` in `[worlds.projects]`; picture extensions added to `[worlds.marketing] extensions`.
3. Merge `vision` into `U:\DMX-RAG\app` main (no LOCK present), push.
4. WS032: launcher 7 → `documentation`, then `marketing` (the Marketing describe step needs two or three nights: `dmx.ps1 describe -World marketing --max-minutes 600`).
5. Laptop: `dmx.ps1 pull -World documentation` / `marketing`; test questions in Claude Desktop.

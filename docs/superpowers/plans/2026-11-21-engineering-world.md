# Engineering World Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A fifth world `engineering` whose groups (Mechanical, Electrical, Software) are folder lists written in the configuration, with new readers for Excel, OneNote and text files.

**Architecture:** A new profile `engineering` reads `[worlds.engineering.groups]`; the group folders are the world's roots and give each document its `category` (group) and `section` (first folder below). Excel and text are ordinary extractors called by `extract_file` in the worker pool. OneNote is different: `.one` files are held back from the pool and read one by one in a separate pass at the end of the index run, through OneNote desktop (COM), always on a temporary copy; the MCP server never reads a `.one` itself.

**Tech Stack:** Python 3.11, SQLite (existing `store.py`), `openpyxl` (new), `xlrd` (present), `pywin32` COM for OneNote (present), pytest.

**Spec:** `docs/superpowers/specs/2026-11-21-engineering-world-design.md`

## Global Constraints

* `EXTRACT_VERSION` stays `2` (`src/dmx_docs/extract.py`). No change may make another world re-extract.
* The other four worlds keep their extension lists, tools and facets. `STALL_S` and `SOLO_TIMEOUT_S` in `indexer.py` are not changed.
* A `.one` file on a root is never opened by OneNote from code: only a copy under `<world_dir>\onenote-tmp\<random>` is.
* No group is ever derived from a folder name: only from `cfg.groups`.
* Tests use made-up names and content only (ACME, P1234...); never a real client, project or person.
* Chunks stay contiguous slices of the page text (`chunking.py` is not changed): `read_document` rebuilds a page by concatenating its chunks.
* Files that contain Windows paths are written with the Write/Edit tools, never through a shell heredoc.
* Run tests from the worktree root with:
  `$env:PYTHONPATH='src'; C:\dmx-rag\venv\Scripts\python.exe -m pytest <file> -q`
* Texts written into page text are French, as in the spec: `Feuille :`, `(masquée)`, `[colonnes]`, `[feuille coupée après N lignes]`, `Créée le`, `modifiée le`, `Fichiers joints :`, `[Texte des images]`.

## Review Focus

1. A group folder written with a drive letter, a trailing backslash or another letter case than the stored root (`O:\END\...\22_Mecanique\`) is the same folder as its UNC form: no second root, no "root in no group" stop. (Task 1)
2. An Excel cell holding an error value (`#DIV/0!`, `#N/A`), a merged cell range, or a workbook written without sheet dimensions: read as text, no crash, no lost rows. (Task 3)
3. A OneNote page with pictures or ink only (no text node), and a section with no page at all: the first is a page with its title and dates, the second a document with status `empty`; neither is an error. (Task 4 and 5)
4. OneNote already running with the user's own notebooks when the pass starts: it is not ended, and only the temporary notebook is closed. (Task 5)
5. A `.txt` that is not text (binary content, NUL bytes) or is empty: status `empty` or a readable page, never an exception out of `extract_file`. (Task 2)

---

## File structure

| File | Responsibility |
|---|---|
| `src/dmx_docs/config.py` (modify) | profile `engineering`, `groups`, sheet limits |
| `src/dmx_docs/sources.py` (modify) | bring stored roots in line with the group folders |
| `src/dmx_docs/facets.py` (modify) | group and section of a document, versioned by the groups table |
| `src/dmx_docs/excel.py` (create) | workbook -> one page of text per sheet |
| `src/dmx_docs/onenote.py` (create) | page XML -> text; reading a section through OneNote; the pass |
| `src/dmx_docs/extract.py` (modify) | dispatch `.txt`, Excel, `.one`; `extract_txt` |
| `src/dmx_docs/indexer.py` (modify) | hold `.one` files back, run the OneNote pass, sync roots |
| `src/dmx_docs/tools.py` (modify) | labels, hit line, no live read of `.one`, refusals in `view_page` / `export_image` |
| `src/dmx_docs/server.py` (modify) | `engineering_search`, instructions |
| `src/dmx_docs/worldmap.py` (modify) | counts by group and section |
| `skills/dmx-research/references/engineering.md` (create), `SKILL.md`, `tests/test_skill.py` (modify) | skill page |
| `tests/test_engineering.py`, `tests/test_excel.py`, `tests/test_onenote.py`, `tests/test_txt.py` (create) | tests |

---

### Task 0: Commit the design and this plan

- [ ] **Step 1:** `git add docs/superpowers/specs/2026-11-21-engineering-world-design.md docs/superpowers/plans/2026-11-21-engineering-world.md` and commit: `Engineering world: design and plan`.

---

### Task 1: Profile, groups, roots and facets

**Files:**
- Modify: `src/dmx_docs/config.py`, `src/dmx_docs/sources.py`, `src/dmx_docs/facets.py`, `src/dmx_docs/indexer.py:222-224,379`, `src/dmx_docs/tools.py:356,471-486`, `src/dmx_docs/server.py`, `src/dmx_docs/worldmap.py`
- Test: `tests/test_engineering.py`

**Interfaces:**
- Produces:
  - `config.PROFILES` contains `"engineering"`.
  - `Config.groups: dict[str, list[str]]` (default `{}`), `Config.max_sheet_rows: int = 5000`, `Config.max_sheet_columns: int = 200`; both limits are added to `Config.extract_options()` under the same names.
  - `load_config`: for profile `engineering`, `groups` comes from `[worlds.<name>.groups]`, each folder passed through `sources.clean_path`; `roots` seeds from all group folders in table order. Raises `ValueError` when the world table also has `roots` (message: `World '<name>': write the folders in [worlds.<name>.groups], not in roots`), when a folder is listed twice in one or two groups (message names the folder and says `is in more than one group`), or when one group folder lies inside another (message names both and says `group folders may not contain one another`); compare with `store.path_key` / `store.is_under`. `[index] max_sheet_rows` / `max_sheet_columns` are read like `max_pdf_pages`.
  - `class sources.GroupError(ValueError)`; `sources.sync_group_roots(con, cfg: Config) -> list[str]`: adds every group folder that is not a stored root (returns the added ones); raises `GroupError` naming each stored root that is neither a group folder nor inside one. Never removes a root.
  - `facets.groups_fingerprint(groups: dict[str, list[str]]) -> str` (12 hex characters, from the sorted `(group, path_key)` pairs).
  - `facets.compute_engineering(path: str, root: str, groups: dict[str, list[str]]) -> dict`: `{"category": <group>, "section": <first folder below the group folder or None>, "facet_source": "config"}`; `{}` when the path is in no group folder.
  - `facets.refresh(con, roots, profile="projects", groups: dict | None = None) -> int`; the stored `facets_version` of this profile is `f"engineering:1:{groups_fingerprint(groups)}"`.

- [ ] **Step 1: Write the failing tests** in `tests/test_engineering.py`

```python
import pytest

from conftest import make_pdf
from dmx_docs import facets, sources, store
from dmx_docs.config import load_config
from dmx_docs.indexer import run_index
from dmx_docs.tools import DocTools

quiet = lambda *a, **k: None  # noqa: E731


def write_config(tmp_path, groups_toml, extra=""):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        "[index]\ndata_dir = 'data'\n[worlds.engineering]\ntitle = \"Engineering\"\n"
        f"profile = \"engineering\"\nextensions = ['.pdf']\n{extra}\n[worlds.engineering.groups]\n{groups_toml}\n",
        encoding="utf-8")
    return cfg


@pytest.fixture
def eng(tmp_path):
    mech = tmp_path / "22_Mec"
    (mech / "224 - Pneumatique").mkdir(parents=True)
    (mech / "229 - Archives" / "Old").mkdir(parents=True)
    make_pdf(str(mech / "224 - Pneumatique" / "Pompes.pdf"), ["Pompe à vide: 610 l/min."])
    make_pdf(str(mech / "229 - Archives" / "Old" / "Ancien.pdf"), ["Ancienne pompe à vide."])
    make_pdf(str(mech / "Racine.pdf"), ["Pompe à vide, document à la racine."])
    cfg = load_config(write_config(tmp_path, f"Mechanical = ['{mech}']\nElectrical = []\nSoftware = []"),
                      world="engineering")
    run_index(cfg, progress=quiet)
    return cfg, mech


def facet_rows(cfg):
    con = store.connect(cfg.db_path)
    rows = {r[0]: (r[1], r[2], r[3]) for r in con.execute(
        "SELECT d.name, f.category, f.section, f.facet_source FROM docs d JOIN doc_facets f ON f.doc_id = d.id")}
    con.close()
    return rows


def test_groups_come_from_the_configuration(eng):
    cfg, mech = eng
    assert cfg.groups == {"Mechanical": [str(mech)], "Electrical": [], "Software": []}
    assert cfg.roots == [str(mech)]
    assert facet_rows(cfg) == {
        "Pompes.pdf": ("Mechanical", "224 - Pneumatique", "config"),
        "Ancien.pdf": ("Mechanical", "229 - Archives", "config"),
        "Racine.pdf": ("Mechanical", None, "config")}


def test_search_filters_by_group_and_section(eng):
    cfg, _ = eng
    out = DocTools(cfg).search("pompe", mode="keyword", category="mechanical", section="224 - pneumatique")
    assert "Pompes.pdf" in out and "Ancien.pdf" not in out and "Racine.pdf" not in out
    assert "group Mechanical · section 224 - Pneumatique" in out
    assert "group Mechanical · section (none)" in DocTools(cfg).search("racine", mode="keyword")


@pytest.mark.parametrize("groups", [
    "Mechanical = ['{a}', '{a}']",                    # twice in one group
    "Mechanical = ['{a}']\nElectrical = ['{a}']",     # in two groups
    "Mechanical = ['{a}']\nElectrical = ['{a}\\\\sub']",  # one inside the other
])
def test_bad_groups_are_refused(tmp_path, groups):
    a = tmp_path / "A"
    (a / "sub").mkdir(parents=True)
    with pytest.raises(ValueError, match="group"):
        load_config(write_config(tmp_path, groups.format(a=a)), world="engineering")


def test_roots_key_is_refused_for_this_profile(tmp_path):
    with pytest.raises(ValueError, match="groups"):
        load_config(write_config(tmp_path, "Mechanical = []", extra=f"roots = ['{tmp_path}']"), world="engineering")


def test_a_new_group_folder_becomes_a_root(eng, tmp_path):
    cfg, mech = eng
    soft = tmp_path / "23_Soft"
    soft.mkdir()
    make_pdf(str(soft / "Automate.pdf"), ["Programme automate."])
    cfg2 = load_config(write_config(tmp_path, f"Mechanical = ['{mech}']\nSoftware = ['{soft}']"), world="engineering")
    run_index(cfg2, progress=quiet)
    rows = facet_rows(cfg2)
    assert rows["Automate.pdf"] == ("Software", None, "config")
    assert rows["Pompes.pdf"][0] == "Mechanical"       # recomputed with the new table, still right


def test_a_root_in_no_group_stops_the_run(eng, tmp_path):
    cfg, mech = eng
    stray = tmp_path / "Stray"
    stray.mkdir()
    con = store.connect(cfg.db_path)
    sources.add_root(con, str(stray))
    con.commit()
    con.close()
    with pytest.raises(sources.GroupError, match="Stray"):
        run_index(cfg, progress=quiet)


def test_group_folder_spelling_does_not_make_a_second_root(eng, tmp_path):   # Review Focus 1
    cfg, mech = eng
    other = str(mech).upper() + "\\"
    cfg2 = load_config(write_config(tmp_path, f"Mechanical = ['{other}']"), world="engineering")
    run_index(cfg2, progress=quiet)
    con = store.connect(cfg2.db_path)
    assert con.execute("SELECT count(*) FROM roots").fetchone()[0] == 1
    con.close()
    assert facet_rows(cfg2)["Pompes.pdf"][0] == "Mechanical"


def test_changed_groups_table_recomputes_facets(eng):
    cfg, mech = eng
    con = store.connect(cfg.db_path)
    assert facets.refresh(con, cfg.roots, "engineering", cfg.groups) == 0
    assert facets.refresh(con, cfg.roots, "engineering", {"Meca": [str(mech)]}) == 3
    assert con.execute("SELECT DISTINCT category FROM doc_facets").fetchall()[0][0] == "Meca"
    con.close()


def test_index_status_and_world_map(eng):
    cfg, _ = eng
    assert DocTools(cfg).index_status().startswith("World: Engineering (engineering)")
    assert "By group: Mechanical 3" in DocTools(cfg).world_map()
```

- [ ] **Step 2: Run** `tests/test_engineering.py`. Expected: FAIL (unknown profile 'engineering').

- [ ] **Step 3: Implement** the Interfaces block above, plus:
  - `indexer.run_index`: after `sources.refresh(cfg, con)`, when `cfg.profile == "engineering"`: `sources.sync_group_roots(con, cfg)` then `sources.refresh(cfg, con)` again; `GroupError` is not caught there. Both `facets.refresh` callers (`indexer.py:379`, `tools.py:356`) pass `cfg.groups`.
  - `cli`'s `index` command prints a `GroupError` as one line and exits with code 2 (follow how it reports other `ValueError`s).
  - `tools._facets`: for `engineering`, the line is `group <category or 'unknown'> · section <section or '(none)'>`.
  - `server.py`: `engineering_search(query, folder, file_type, modified_after, limit, mode, category, section, expand, pictures)` registered as `search` for this profile, in the style of `marketing_search`; `category` documented as "Optional group(s), comma-separated: Mechanical, Electrical, Software" and `section` as "Optional section(s): the first folder below the group folder, e.g. \"224 - Pneumatique\"". `ENGINEERING_INSTRUCTIONS` in the style of `MARKETING_INSTRUCTIONS`: what the world holds, group and section on every hit, Excel hits are sheets and OneNote hits are pages with title and dates, neither can be shown with `view_page` (use `open_document`), `world_map` first.
  - `worldmap.live_overview`: for `engineering`, two extra head lines `By group: ...` and `By section: ...` built with `_counts` on `doc_facets.category` and `doc_facets.section`.

- [ ] **Step 4: Run** `tests/test_engineering.py tests/test_profiles.py tests/test_worlds.py tests/test_facets.py tests/test_worldmap.py`. Expected: PASS.

- [ ] **Step 5: Commit** `Engineering world: profile, groups from the configuration, group and section facets`.

---

### Task 2: Text files

**Files:**
- Modify: `src/dmx_docs/extract.py` (dispatch in `extract_file`, new `extract_txt`), `src/dmx_docs/tools.py:21-24,291-294`
- Test: `tests/test_txt.py`

**Interfaces:**
- Produces: `extract.extract_txt(path: str) -> Extracted`; `extract.TXT_MAX_BYTES = 2 * 1024 * 1024`; `tools.TYPE_LABEL[".txt"] == "Text"`; `_page_label(".txt") == "page"`.

- [ ] **Step 1: Write the failing tests**

```python
from dmx_docs.extract import TXT_MAX_BYTES, extract_file


def test_txt_encodings(tmp_path):
    for name, data in {"utf8.txt": "Réglage du préhenseur".encode("utf-8"),
                       "bom.txt": "\ufeffRéglage du préhenseur".encode("utf-8"),
                       "utf16.txt": "Réglage du préhenseur".encode("utf-16"),
                       "cp1252.txt": "Réglage du préhenseur".encode("cp1252")}.items():
        p = tmp_path / name
        p.write_bytes(data)
        r = extract_file(str(p))
        assert (r.status, r.n_pages, r.pages) == ("ok", 1, [(1, "Réglage du préhenseur")]), name


def test_txt_is_cut_at_the_limit(tmp_path):
    p = tmp_path / "big.txt"
    p.write_bytes(b"ligne\n" * (TXT_MAX_BYTES // 6 + 1000))
    text = extract_file(str(p)).pages[0][1]
    assert len(text) < TXT_MAX_BYTES + 100 and text.endswith("[fichier coupé après 2 Mo]")


def test_txt_that_is_not_text(tmp_path):                       # Review Focus 5
    blank = tmp_path / "blank.txt"
    blank.write_bytes(b" \r\n\t")
    assert extract_file(str(blank)).status == "empty"
    binary = tmp_path / "binary.txt"
    binary.write_bytes(bytes(range(256)) * 40)
    assert extract_file(str(binary)).status in ("ok", "empty")   # no exception, no 'error'
```

- [ ] **Step 2: Run** `tests/test_txt.py`. Expected: FAIL (`TXT_MAX_BYTES` missing).
- [ ] **Step 3: Implement** `extract_txt`: read at most `TXT_MAX_BYTES + 1` bytes; decode UTF-8 (BOM accepted), else UTF-16 when the data starts with a UTF-16 BOM, else `cp1252` with `errors="replace"`; `clean_text`; append the cut line when the file was longer; status `empty` when nothing is left.
- [ ] **Step 4: Run** `tests/test_txt.py tests/test_extract.py`. Expected: PASS.
- [ ] **Step 5: Commit** `Text files (.txt) are read as one page`.

---

### Task 3: Excel reader

**Files:**
- Create: `src/dmx_docs/excel.py`, `tests/test_excel.py`, `tests/fixtures/small.xls`
- Modify: `pyproject.toml` (`"openpyxl>=3.1"`), `src/dmx_docs/extract.py` (dispatch), `src/dmx_docs/tools.py:21-24,291-294,633-700`

**Interfaces:**
- Consumes: `options["max_sheet_rows"]`, `options["max_sheet_columns"]` from Task 1 (defaults 5000 and 200 when absent).
- Produces:
  - `excel.extract_excel(path: str, max_rows: int = 5000, max_columns: int = 200) -> Extracted`
  - `excel.MAX_WORKBOOK_CHARS = 2_000_000`, `excel.HEADER_EVERY_CHARS = 1000`, `excel.HEADER_MAX_CHARS = 300`
  - `excel.cell_text(value) -> str`
  - `extract_file` sends `.xlsx`, `.xlsm`, `.xls` to it (import inside the function, as the module imports `Extracted` and `clean_text` from `extract`).
  - `TYPE_LABEL`: `.xlsx` and `.xlsm` -> `"Excel"`, `.xls` -> `"Excel 97-2003"`; `_page_label` -> `"sheet"`.
  - `view_page` and `export_image` on these types raise `ValueError("Excel sheets cannot be shown as an image: open the workbook with open_document.")`.

Page text of a sheet, exactly:

```
Feuille : <name>            (" (masquée)" appended when the sheet is hidden)
<row>                       cells joined by " | ", trailing empty cells dropped, rows without a value skipped
...
[colonnes] <header row>     written again before a row once at least HEADER_EVERY_CHARS characters of rows
<row>                       were written since the header or its last repeat; header cut to HEADER_MAX_CHARS
...
[feuille coupée après 5000 lignes]     only when rows with values remain after max_rows of them
```

The header row is the first row with a value. A sheet without any value, and a chart sheet, gives the page text `""` (the page number still counts). When the workbook passes `MAX_WORKBOOK_CHARS`, the current page ends with `[classeur coupé : feuilles suivantes non lues]` and the remaining sheets give `""`. `n_pages` is the number of sheets. Status `ok` when a page has text, else `empty`. Every page goes through `clean_text` (so an empty field reads `a | | c`).

`cell_text`: `None` -> `""`; `bool` -> `VRAI` / `FAUX`; `datetime` at midnight and `date` -> `YYYY-MM-DD`; other `datetime` -> `YYYY-MM-DD HH:MM`; a float with an integer value -> without decimals; other floats -> `format(v, ".10g")`; anything else -> `str(v)` with line breaks replaced by a space.

- [ ] **Step 1: Make the fixture** `tests/fixtures/small.xls` once (Excel 97-2003 format; `xlrd` cannot write): with Excel or LibreOffice, two sheets, `Pièces` holding `Référence | Désignation | Qté` / `AMX-220 | Amortisseur | 4` and a date cell `2024-03-15` in A3, and an empty sheet `Vide`. Commit the file.

- [ ] **Step 2: Write the failing tests** in `tests/test_excel.py`

```python
import datetime as dt
from pathlib import Path

import openpyxl
import pytest

from dmx_docs import excel
from dmx_docs.extract import EXTRACT_VERSION, extract_file

FIXTURES = Path(__file__).parent / "fixtures"


def book(tmp_path, fill, name="b.xlsx"):
    wb = openpyxl.Workbook()
    fill(wb)
    p = tmp_path / name
    wb.save(p)
    return str(p)


def test_sheets_are_pages_in_workbook_order(tmp_path):
    def fill(wb):
        a = wb.active
        a.title = "Pièces"
        a.append(["Référence", "Désignation", "Qté"])
        a.append(["AMX-220", "Amortisseur", 4])
        a.append([])
        a.append(["VT-9", None, 2.5])
        h = wb.create_sheet("Caché")
        h.sheet_state = "hidden"
        h["B2"] = "seul"
        wb.create_sheet("Vide")
    r = extract_file(book(tmp_path, fill))
    assert (r.status, r.n_pages) == ("ok", 3)
    assert r.pages[0] == (1, "Feuille : Pièces\nRéférence | Désignation | Qté\nAMX-220 | Amortisseur | 4\nVT-9 | | 2.5")
    assert r.pages[1] == (2, "Feuille : Caché (masquée)\n| seul")
    assert dict(r.pages).get(3, "") == ""


def test_cell_text():
    assert [excel.cell_text(v) for v in (None, True, 4.0, 2.5, 1 / 3, dt.datetime(2024, 3, 15),
                                         dt.datetime(2024, 3, 15, 14, 5), dt.date(2024, 3, 15), "a\nb")] == \
        ["", "VRAI", "4", "2.5", "0.3333333333", "2024-03-15", "2024-03-15 14:05", "2024-03-15", "a b"]


def test_formula_gives_its_stored_value_or_nothing(tmp_path):
    def fill(wb):
        wb.active.append(["Total", "=1+1"])     # openpyxl stores no calculated value
    assert extract_file(book(tmp_path, fill)).pages[0][1] == "Feuille : Sheet\nTotal"


def test_row_and_column_limits(tmp_path):
    def fill(wb):
        ws = wb.active
        ws.append(["h1", "h2", "h3"])
        for i in range(12):
            ws.append([i, "x", "y"])
    r = excel.extract_excel(book(tmp_path, fill), max_rows=5, max_columns=2)
    text = r.pages[0][1]
    assert text.splitlines()[1] == "h1 | h2" and "h3" not in text
    assert text.endswith("[feuille coupée après 5 lignes]") and "\n4 | x" not in text


def test_header_is_repeated_in_long_sheets(tmp_path):
    def fill(wb):
        ws = wb.active
        ws.append(["Référence", "Désignation"])
        for i in range(400):
            ws.append([f"REF-{i:04d}", "Vis à tête cylindrique M8x25"])
    text = extract_file(book(tmp_path, fill)).pages[0][1]
    lines = text.splitlines()
    repeats = [i for i, line in enumerate(lines) if line == "[colonnes] Référence | Désignation"]
    assert len(repeats) >= 10
    gaps = [sum(len(x) + 1 for x in lines[a + 1:b]) for a, b in zip(repeats, repeats[1:])]
    assert max(gaps) < excel.HEADER_EVERY_CHARS + 100
    assert sum(1 for line in lines if line.startswith("REF-")) == 400     # no row lost


def test_workbook_limit(tmp_path, monkeypatch):
    monkeypatch.setattr(excel, "MAX_WORKBOOK_CHARS", 500)
    def fill(wb):
        for n in range(3):
            ws = wb.active if n == 0 else wb.create_sheet(f"S{n}")
            for i in range(40):
                ws.append([f"ligne {i} de la feuille {n}"])
    r = extract_file(book(tmp_path, fill))
    assert r.n_pages == 3 and r.pages[0][1].endswith("[classeur coupé : feuilles suivantes non lues]")
    assert dict(r.pages).get(3, "") == ""


def test_error_values_and_merged_cells(tmp_path):              # Review Focus 2
    def fill(wb):
        ws = wb.active
        ws.append(["Titre fusionné", None, None])
        ws.merge_cells("A1:C1")
        ws.append(["#DIV/0!", "#N/A", 3])
    assert extract_file(book(tmp_path, fill)).pages[0][1] == "Feuille : Sheet\nTitre fusionné\n#DIV/0! | #N/A | 3"


def test_old_xls():
    r = extract_file(str(FIXTURES / "small.xls"))
    assert r.status == "ok" and r.n_pages == 2
    assert r.pages[0][1] == "Feuille : Pièces\nRéférence | Désignation | Qté\nAMX-220 | Amortisseur | 4\n2024-03-15"


def test_damaged_workbook_is_an_error(tmp_path):
    p = tmp_path / "bad.xlsx"
    p.write_bytes(b"not a workbook")
    r = extract_file(str(p))
    assert r.status == "error" and r.error


def test_extract_version_is_unchanged():
    assert EXTRACT_VERSION == 2
```

- [ ] **Step 3: Run** `tests/test_excel.py`. Expected: FAIL (no module `dmx_docs.excel`). Install the new dependency first: `C:\dmx-rag\venv\Scripts\python.exe -m pip install "openpyxl>=3.1"`.

- [ ] **Step 4: Implement.** `.xlsx`/`.xlsm`: `openpyxl.load_workbook(path, read_only=True, data_only=True)`, iterate `wb.worksheets` plus chart sheets in `wb.sheetnames` order, `ws.iter_rows(values_only=True, max_col=max_columns)`; call `ws.reset_dimensions()` before iterating so a workbook without dimensions is read fully; close the workbook in a `finally`. `.xls`: `xlrd.open_workbook(path, on_demand=True)`, dates through `xlrd.xldate_as_datetime(value, book.datemode)` for cells of type `XL_CELL_DATE`, error cells through `xlrd.error_text_from_code`, hidden when `sheet.visibility != 0`. One shared function builds the page text from an iterator of rows of already converted strings. Add the `view_page` / `export_image` refusals and the labels.

- [ ] **Step 5: Add a tools test** to `tests/test_excel.py` and run the file again:

```python
def test_excel_in_a_world(tmp_path, make_cfg):
    from dmx_docs.indexer import run_index
    from dmx_docs.tools import DocTools
    root = tmp_path / "root"
    root.mkdir()
    def fill(wb):
        wb.active.title = "Pompes"
        wb.active.append(["Pompe", "Débit"])
        wb.active.append(["VTN 41", "610 l/min"])
    path = book(root, fill, "Calcul.xlsx")
    cfg = make_cfg(root, extensions=[".xlsx"])
    run_index(cfg, progress=lambda *a, **k: None)
    tools = DocTools(cfg)
    out = tools.search("VTN", mode="keyword")
    assert "Calcul.xlsx — sheet 1/1" in out and "(Excel," in out
    assert "Feuille : Pompes" in tools.read_document(path)
    assert "Excel=1" in tools.index_status()
    with pytest.raises(ValueError, match="open_document"):
        tools.view_page(path)
```

- [ ] **Step 6: Run** `tests/test_excel.py tests/test_extract.py tests/test_view_page.py`. Expected: PASS.
- [ ] **Step 7: Commit** `Excel workbooks are read sheet by sheet (.xlsx, .xlsm, .xls)`.

---

### Task 4: OneNote page text (no OneNote needed)

**Files:**
- Create: `src/dmx_docs/onenote.py`, `tests/test_onenote.py`

**Interfaces:**
- Produces:
  - `@dataclass PageRef: id: str; name: str; level: int; created: str; modified: str` (dates as given by OneNote, ISO with `Z`)
  - `onenote.section_pages(hierarchy_xml: str, section_name: str | None = None) -> list[PageRef]`: the `Page` elements of the `Section` named `section_name` (the first `Section` when `None`), in document order.
  - `onenote.page_titles(pages: list[PageRef]) -> list[str]`: the title of each page; a page of level 2 or 3 is `"<title of the nearest earlier page of a lower level> > <its name>"`.
  - `onenote.page_text(page_xml: str, title: str, created: str, modified: str) -> str`

Both XML documents use the namespace `http://schemas.microsoft.com/office/onenote/2013/onenote`; match elements by local name so that other schema years work too. In a page, text is in `one:T` elements (CDATA holding HTML fragments: strip tags, unescape entities); a table is `one:Table` > `one:Row` > `one:Cell`, written as one line per row with cells joined by ` | `; a picture's recognised text is `one:Image/one:OCRData/one:OCRText`; an attached file is `one:InsertedFile` with attribute `preferredName`. The page's own `one:Title` text is skipped (the title line already has it). Text inside a table is not written a second time outside it.

Output, exactly (dates cut to `YYYY-MM-DD`; the last two blocks only when not empty; the result goes through `clean_text`):

```
<title>
Créée le <created>, modifiée le <modified>
<text and table rows in document order>
Fichiers joints : <name>, <name>
[Texte des images]
<picture texts, one block per picture>
```

- [ ] **Step 1: Write the failing tests**

```python
from dmx_docs import onenote

NS = 'xmlns:one="http://schemas.microsoft.com/office/onenote/2013/onenote"'
HIER = f"""<one:Notebook {NS}><one:Section name="Pompes" ID="s1">
<one:Page ID="p1" name="A savoir" dateTime="2022-07-12T13:50:28.000Z" lastModifiedTime="2022-10-14T11:40:50.000Z" pageLevel="1"/>
<one:Page ID="p2" name="Détails" dateTime="2022-07-13T08:00:00.000Z" lastModifiedTime="2022-07-13T09:00:00.000Z" pageLevel="2"/>
<one:Page ID="p3" name="Vide" dateTime="2023-01-01T08:00:00.000Z" lastModifiedTime="2023-01-01T08:00:00.000Z" pageLevel="1"/>
</one:Section><one:Section name="Autre" ID="s2"/></one:Notebook>"""
PAGE = f"""<one:Page {NS} ID="p1" name="A savoir">
<one:Title><one:OE><one:T><![CDATA[A savoir]]></one:T></one:OE></one:Title>
<one:Outline><one:OEChildren>
<one:OE><one:T><![CDATA[Le débit d'air <span style='font-weight:bold'>s'additionne</span> &amp; les fuites aussi]]></one:T></one:OE>
<one:OE><one:Table><one:Row><one:Cell><one:OEChildren><one:OE><one:T><![CDATA[Pompe]]></one:T></one:OE></one:OEChildren></one:Cell>
<one:Cell><one:OEChildren><one:OE><one:T><![CDATA[610 l/min]]></one:T></one:OE></one:OEChildren></one:Cell></one:Row></one:Table></one:OE>
<one:OE><one:Image><one:OCRData><one:OCRText><![CDATA[Pilotage interne ou externe ?]]></one:OCRText></one:OCRData></one:Image></one:OE>
<one:OE><one:InsertedFile preferredName="Courbe pompe.pdf"/></one:OE>
</one:OEChildren></one:Outline></one:Page>"""


def test_section_pages_and_titles():
    pages = onenote.section_pages(HIER, "Pompes")
    assert [(p.id, p.name, p.level) for p in pages] == [("p1", "A savoir", 1), ("p2", "Détails", 2), ("p3", "Vide", 1)]
    assert onenote.page_titles(pages) == ["A savoir", "A savoir > Détails", "Vide"]
    assert onenote.section_pages(HIER, "Autre") == []


def test_page_text():
    assert onenote.page_text(PAGE, "A savoir", "2022-07-12T13:50:28.000Z", "2022-10-14T11:40:50.000Z") == (
        "A savoir\nCréée le 2022-07-12, modifiée le 2022-10-14\n"
        "Le débit d'air s'additionne & les fuites aussi\nPompe | 610 l/min\n"
        "Fichiers joints : Courbe pompe.pdf\n[Texte des images]\nPilotage interne ou externe ?")


def test_page_with_pictures_only_keeps_title_and_dates():     # Review Focus 3
    xml = f'<one:Page {NS} ID="p3" name="Vide"><one:Outline><one:OE><one:Image/></one:OE></one:Outline></one:Page>'
    assert onenote.page_text(xml, "Vide", "2023-01-01T08:00:00.000Z", "2023-01-01T08:00:00.000Z") == \
        "Vide\nCréée le 2023-01-01, modifiée le 2023-01-01"
```

- [ ] **Step 2: Run** `tests/test_onenote.py`. Expected: FAIL (no module).
- [ ] **Step 3: Implement** with `xml.etree.ElementTree` and `html.unescape`.
- [ ] **Step 4: Run** `tests/test_onenote.py`. Expected: PASS.
- [ ] **Step 5: Commit** `OneNote: page text from the page XML`.

---

### Task 5: Reading a section through OneNote, and the pass in the index run

**Files:**
- Modify: `src/dmx_docs/onenote.py`, `src/dmx_docs/extract.py` (dispatch), `src/dmx_docs/indexer.py:304-346`, `src/dmx_docs/tools.py:21-24,291-294,633-700,1137-1169`
- Test: `tests/test_onenote.py`

**Interfaces:**
- Consumes: Task 4's `section_pages`, `page_titles`, `page_text`.
- Produces:
  - `onenote.LOAD_TIMEOUT_S = 120`, `onenote.POLL_S = 2`, `onenote.MAX_TIMEOUTS_IN_ROW = 5`, `onenote.NOT_INSTALLED = "OneNote desktop is not installed on this machine"`, `onenote.SERVER_ONLY = "OneNote sections are read by the index run only"`.
  - `onenote.installed() -> bool` (the ProgID `OneNote.Application` is registered; `False` off Windows).
  - `onenote.running() -> bool` (a process `ONENOTE.EXE` exists).
  - `onenote.section_limit_s(size_bytes: int) -> float` = `300 + 60 * size_bytes / (50 * 1024 * 1024)`.
  - `onenote.read_section(path: str, tmp_root: str, app=None, load_timeout_s: float = LOAD_TIMEOUT_S, poll_s: float = POLL_S) -> Extracted`. `app` is an object with the four OneNote methods below (a fake in tests); `None` creates `win32com.client.Dispatch("OneNote.Application")` after `pythoncom.CoInitialize()`.
  - `onenote.run_pass(cfg, items: list[tuple[str, int, float, bool]], save, progress, check_stop, read=None) -> None`, where `items` are `(path, size, mtime, existed)` and `save` is `_Writer.save`. `read(path, tmp_root, limit_s) -> Extracted` defaults to a function that runs `read_section` in a fresh one-worker spawn `ProcessPoolExecutor` and raises `TimeoutError` after `limit_s`.
  - `extract_file` on `.one` returns `Extracted(status="skipped", error=onenote.SERVER_ONLY)`.
  - `TYPE_LABEL[".one"] == "OneNote"`, `_page_label(".one") == "page"`.

`read_section`, in this order:
1. `tmp = <tmp_root>\<uuid4 hex>`; create it; copy `path` into it (`shutil.copy2`, source through `fs_path`).
2. `nb = app.OpenHierarchy(tmp, "", "", 0)` (with pywin32 late binding the out parameter comes back as the return value; a fake returns the id). The path given is `tmp`, never `path`.
3. Poll `app.GetHierarchy(nb, 4, "")` every `poll_s`: stop when the section named like the file (without `.one`) is listed and two polls in a row return the same list of page ids, or at `load_timeout_s`. A section still without a page -> `Extracted(status="empty", n_pages=0, title=<section name>)`.
4. For page number `n` (1-based): `app.GetPageContent(page.id, "", 0)` -> `page_text(...)` with the title from `page_titles`.
5. `finally`: `app.CloseNotebook(nb)` when a notebook was opened (errors of the close are logged, not raised), then `shutil.rmtree(tmp, ignore_errors=True)`.
6. A COM error whose text says the section is password protected -> `Extracted(status="error", error="OneNote section protected by a password")`; any other exception -> `status="error"` with `f"{type(e).__name__}: {e}"[:500]`.
Result: `status="ok"`, `pages=[(n, text), ...]`, `n_pages=len(pages)`, `title=<section name>`.

`run_pass`:
* nothing to do when `items` is empty; otherwise delete every folder left in `<cfg.world_dir>\onenote-tmp`.
* not `installed()` -> every item saved as `Extracted(status="skipped", error=NOT_INSTALLED)`, one `progress` line, return.
* `was_running = running()`. For each item: `check_stop()`, `progress("  OneNote: <path>")`, `read(path, tmp_root, section_limit_s(size))`, `save(path, size, mtime, result, existed)`.
* on `TimeoutError`: save `Extracted(status="error", error="timeout: OneNote did not finish in <N> s")`; when `not was_running`, `kill_office_automation("ONENOTE.EXE")`; when `was_running`, stop the pass with a `progress` warning that the user's OneNote is open and the remaining sections are left for the next run (they are not saved, so the next run takes them again).
* `MAX_TIMEOUTS_IN_ROW` timeouts in a row: stop the pass the same way.
* at the end, when `not was_running`: `kill_office_automation("ONENOTE.EXE")`.

`indexer.run_index`: in the scan loop, after the unchanged check and the size check, a path ending in `.one` is appended to a list instead of being submitted to the pool. After the `crashed` retries and before the `.ppt` cleanup: `onenote.run_pass(cfg, held, writer.save, progress, check_stop)`.

`tools._pages`: when the indexed document's extension is `.one`, the index is served whether or not the file changed: status `ok` / `no_text` / `empty` -> the indexed pages, with `info["note"] = "changed since it was indexed on <YYYY-MM-DD>: these are the indexed pages"` when size or date differ; any other status -> `ValueError(f"Not read yet: {path} ({doc['error']})")`. A `.one` that is not in the index -> `ValueError("Not indexed yet: ...")`. `extract_file` is never called for a `.one` here. `read_document` and `find_in_document` print `info["note"]` on its own line under their header line. `view_page` / `export_image` on `.one` raise `ValueError("OneNote pages cannot be shown as an image: open the section with open_document.")`.

- [ ] **Step 1: Write the failing tests** (appended to `tests/test_onenote.py`; `HIER` and `PAGE` from Task 4)

```python
import os
from pathlib import Path

import pytest

from dmx_docs.extract import Extracted, extract_file


class FakeOneNote:
    """Lists no page for the first `late` polls, then the pages of HIER."""
    def __init__(self, late=1, fail_on_page=False):
        self.opened, self.closed, self.polls, self.late, self.fail_on_page = [], [], 0, late, fail_on_page
    def OpenHierarchy(self, path, parent, obj_id, create):
        self.opened.append(path)
        self.files = sorted(os.listdir(path))
        return "nb1"
    def GetHierarchy(self, node, scope, xml):
        self.polls += 1
        return HIER if self.polls > self.late else HIER.replace("<one:Page", "<one:Skip")
    def GetPageContent(self, page_id, xml, info):
        if self.fail_on_page:
            raise RuntimeError("boom")
        return PAGE
    def CloseNotebook(self, node):
        self.closed.append(node)


def section_file(tmp_path):
    src = tmp_path / "share" / "Pompes.one"
    src.parent.mkdir()
    src.write_bytes(b"section bytes")
    return src


def test_read_section_opens_a_copy_and_cleans_up(tmp_path):
    src, tmp_root, app = section_file(tmp_path), tmp_path / "onenote-tmp", FakeOneNote()
    before = src.stat().st_mtime
    r = onenote.read_section(str(src), str(tmp_root), app=app, poll_s=0)
    assert (r.status, r.n_pages, r.title) == ("ok", 3, "Pompes")
    assert r.pages[1][1].startswith("A savoir > Détails\nCréée le 2022-07-13")
    assert len(app.opened) == 1 and Path(app.opened[0]).parent == tmp_root
    assert str(src.parent) not in app.opened[0] and app.files == ["Pompes.one"]
    assert app.closed == ["nb1"] and list(tmp_root.iterdir()) == []
    assert src.stat().st_mtime == before and src.read_bytes() == b"section bytes"


def test_read_section_without_pages_is_empty(tmp_path):         # Review Focus 3
    app = FakeOneNote(late=10 ** 9)
    r = onenote.read_section(str(section_file(tmp_path)), str(tmp_path / "t"), app=app, poll_s=0, load_timeout_s=0.05)
    assert r.status == "empty" and app.closed == ["nb1"]


def test_read_section_closes_and_cleans_after_an_error(tmp_path):
    app, tmp_root = FakeOneNote(fail_on_page=True), tmp_path / "t"
    r = onenote.read_section(str(section_file(tmp_path)), str(tmp_root), app=app, poll_s=0)
    assert r.status == "error" and "boom" in r.error
    assert app.closed == ["nb1"] and list(tmp_root.iterdir()) == []


def test_section_limit():
    assert onenote.section_limit_s(0) == 300
    assert onenote.section_limit_s(200 * 1024 * 1024) == 540


@pytest.fixture
def pass_env(tmp_path, make_cfg, monkeypatch):
    killed, saved = [], []
    monkeypatch.setattr(onenote, "installed", lambda: True)
    monkeypatch.setattr(onenote, "running", lambda: False)
    monkeypatch.setattr(onenote, "kill_office_automation", lambda *names: killed.append(names))
    cfg = make_cfg(tmp_path / "root", world="engineering", profile="engineering")
    items = [(f"\\\\srv\\share\\S{i}.one", 10, 1.0, False) for i in range(8)]
    save = lambda path, size, mtime, result, existed: saved.append((path, result.status, result.error))
    return cfg, items, save, saved, killed


def run(cfg, items, save, read):
    onenote.run_pass(cfg, items, save, lambda *a: None, lambda: None, read=read)


def test_pass_skips_everything_without_onenote(pass_env, monkeypatch):
    cfg, items, save, saved, killed = pass_env
    monkeypatch.setattr(onenote, "installed", lambda: False)
    run(cfg, items, save, read=lambda *a: pytest.fail("must not read"))
    assert [s[1:] for s in saved] == [("skipped", onenote.NOT_INSTALLED)] * 8 and killed == []


def test_pass_saves_each_section_and_ends_the_onenote_it_started(pass_env):
    cfg, items, save, saved, killed = pass_env
    run(cfg, items, save, read=lambda path, tmp_root, limit: Extracted(status="ok", pages=[(1, "x")], n_pages=1))
    assert [s[1] for s in saved] == ["ok"] * 8 and killed == [("ONENOTE.EXE",)]


def test_pass_goes_on_after_a_timeout_and_stops_after_five_in_a_row(pass_env):
    cfg, items, save, saved, killed = pass_env
    def read(path, tmp_root, limit):
        if path.endswith("S0.one"):
            return Extracted(status="ok", pages=[(1, "x")], n_pages=1)
        raise TimeoutError()
    run(cfg, items, save, read)
    assert [s[1] for s in saved] == ["ok"] + ["error"] * 5      # S6 and S7 are left for the next run
    assert all(s[2].startswith("timeout") for s in saved[1:])


def test_pass_never_ends_a_onenote_that_was_already_open(pass_env, monkeypatch):   # Review Focus 4
    cfg, items, save, saved, killed = pass_env
    monkeypatch.setattr(onenote, "running", lambda: True)
    def read(path, tmp_root, limit):
        raise TimeoutError()
    run(cfg, items, save, read)
    assert killed == [] and len(saved) == 1 and saved[0][1] == "error"


def test_pass_removes_leftover_temporary_folders(pass_env):
    cfg, items, save, saved, killed = pass_env
    left = cfg.world_dir / "onenote-tmp" / "dead"
    left.mkdir(parents=True)
    run(cfg, items[:1], save, read=lambda *a: Extracted(status="empty"))
    assert not left.exists()


def test_extract_file_never_reads_a_section(tmp_path):
    r = extract_file(str(section_file(tmp_path)))
    assert (r.status, r.error) == ("skipped", onenote.SERVER_ONLY)


def test_index_run_reads_sections_in_the_pass_and_the_server_serves_the_index(tmp_path, make_cfg, monkeypatch):
    from dmx_docs.indexer import run_index
    from dmx_docs.tools import DocTools
    src = section_file(tmp_path)
    monkeypatch.setattr(onenote, "installed", lambda: True)
    monkeypatch.setattr(onenote, "running", lambda: True)
    monkeypatch.setattr(onenote, "_read_in_process", lambda path, tmp_root, limit: onenote.read_section(
        path, tmp_root, app=FakeOneNote(late=0), poll_s=0))
    cfg = make_cfg(src.parent, extensions=[".one"])
    run_index(cfg, progress=lambda *a, **k: None)
    tools = DocTools(cfg)
    out = tools.search("fuites", mode="keyword")
    assert "Pompes.one — page 1/3" in out and "(OneNote," in out
    src.write_bytes(b"changed by OneNote after indexing")
    monkeypatch.setattr("dmx_docs.tools.extract_file", lambda *a, **k: pytest.fail("no live read of a section"))
    text = tools.read_document(str(src))
    assert "Le débit d'air s'additionne" in text and "changed since it was indexed on" in text
    with pytest.raises(ValueError, match="open_document"):
        tools.view_page(str(src))


@pytest.mark.skipif(not os.environ.get("DMX_TEST_ONE_FILE"), reason="needs OneNote and a real .one file")
def test_real_section(tmp_path):
    src = Path(os.environ["DMX_TEST_ONE_FILE"])
    before = (src.stat().st_mtime, src.stat().st_size)
    r = onenote.read_section(str(src), str(tmp_path / "t"))
    assert r.status in ("ok", "empty") and (src.stat().st_mtime, src.stat().st_size) == before
```

`_read_in_process(path, tmp_root, limit_s)` is the default of `run_pass`'s `read` (looked up at call time, so the test can replace it).

- [ ] **Step 2: Run** `tests/test_onenote.py`. Expected: FAIL (`read_section` missing).
- [ ] **Step 3: Implement** as specified above. `kill_office_automation` is imported into `onenote` from `extract` (read it first: it ends only the instances started for automation).
- [ ] **Step 4: Run** `tests/test_onenote.py tests/test_index_and_tools.py tests/test_robustness.py`. Expected: PASS.
- [ ] **Step 5: Real check on this machine** (OneNote is installed here; uses a temporary copy made by hand, not the share): copy one small `.one` from the Mechanical folder to a scratch folder, then run `tests/test_onenote.py::test_real_section` with `$env:DMX_TEST_ONE_FILE` set to that copy. Expected: PASS, and no notebook left open in OneNote.
- [ ] **Step 6: Commit** `OneNote sections are read page by page, in their own pass of the index run`.

---

### Task 6: Skill page, example configuration, whole suite

**Files:**
- Create: `skills/dmx-research/references/engineering.md`
- Modify: `skills/dmx-research/SKILL.md`, `tests/test_skill.py:12-13,75`, `config.example.toml`

**Interfaces:**
- Consumes: the `search` tool of Task 1 (`category`, `section`), the labels of Tasks 2, 3 and 5.

- [ ] **Step 1:** In `tests/test_skill.py` add `"engineering": "references/engineering.md"` to `WORLD_FILES` and `"dmx-engineering"` to the server names. Run it. Expected: FAIL (file missing).
- [ ] **Step 2:** Write `references/engineering.md` in the form of `references/marketing.md`: what the world holds (methods, calculation tools, templates, reference documents, handed-over notebooks), the groups and that only Mechanical is filled, how to filter (`category="Mechanical"`, `section="224 - Pneumatique"`), how to read an Excel hit (sheet, `[colonnes]` lines repeat the header, values only, sheets cut after 5,000 rows) and a OneNote hit (page title, dates, `[Texte des images]` is OneNote's own recognition: check a number before quoting it), that neither can be shown with `view_page`, and that archives are recognisable by their section name. Add the world to the list in `SKILL.md`. Every tool call shown must be valid for the server (the test checks it).
- [ ] **Step 3:** `config.example.toml` has no `[worlds]` tables today. Add at its end a commented-out block (every line starting with `# `, so that loading the example is unchanged) holding the `[worlds.engineering]` and `[worlds.engineering.groups]` tables of the spec, section "1. The world and its groups", with the group folders replaced by `'\\server\share\Mecanique'` and empty lists, and one comment line saying that the user writes which folders belong to each group.
- [ ] **Step 4: Run the whole suite:** `$env:PYTHONPATH='src'; C:\dmx-rag\venv\Scripts\python.exe -m pytest -q`. Expected: all pass (tests needing LibreOffice or Ollama skip as before).
- [ ] **Step 5: Commit** `Engineering world: skill page and example configuration`.

---

### Task 7: Rollout (shared folder; each step with the user's go-ahead)

Not code. Done after the branch is merged to main; nothing here runs an index.

- [ ] **Step 1:** Merge to main and bring `U:\DMX-RAG\app` (`\\DMX-FS01\Users$\Johan.Monard\DMX-RAG\app`) to main, when no run is going on any machine.
- [ ] **Step 2:** Back up the shared `config.toml` as `config.toml.bak-<YYYYMMDD-HHMMSS>`, then add the two tables with the Edit tool; read the file back and check the UNC path still starts with two backslashes.
- [ ] **Step 3:** Write `maps\engineering.md` (what is in each numbered folder of `22_Mecanique`, what is not read: SolidWorks, video, archives .zip) and give it to the user to review.
- [ ] **Step 4:** Build the skill zip with `scripts\build_skill.py`.
- [ ] **Step 5 (user, on the VM):** start OneNote once by hand; launcher 7, world `engineering`.
- [ ] **Step 6:** With the user: `index_status` and the run log (files in `error` / `skipped` with their reasons); laptop pull; `dmx-engineering` connector with Claude Desktop closed; upload the skill zip; the five acceptance questions.

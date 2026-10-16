# Knowledge Cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Serve hand-written (Claude-written) Markdown cards, one per project, through `project_card` and a new `catalog` tool, and check them with `dmx-docs knowledge check`.

**Architecture:** A new module `knowledge.py` parses `knowledge\projects\*.md` and `knowledge\vocabulary.md` (next to config.toml), keeps them in memory and re-reads changed files on each call. `DocTools` uses it in `project_card` and a new `catalog` method; `knowledge.check` validates cards against the index. `dmx.ps1 pull` mirrors the shared knowledge folder to the machine. Routing text (server instructions, skill, map) points to `catalog` first.

**Tech Stack:** Python 3.11, SQLite (existing index), MCP Python SDK (existing server), pytest, PowerShell 5.1 (`dmx.ps1`).

**Spec:** `docs/superpowers/specs/2026-10-15-knowledge-cards-design.md`

## Global Constraints

- No new dependency: the card header is parsed by our own small parser (no YAML library).
- Tool output and card text in English; real client names never appear in tests, docstrings or examples (use ACME, Globex, Polder...).
- New tools are read-only (`**kw` with `READ_ONLY` annotations, like the others) and registered only for `profile == "projects"`.
- Accent/case-insensitive matching uses `register.fold` (already used for client names).
- Keep each file's existing line endings; edit existing files with the Edit tool, never by rewriting them through Python or shell heredocs (they flip CRLF/LF and eat backslashes).
- Card location: `knowledge\` next to config.toml by default, `[worlds.<name>] knowledge = <path>` overrides (relative paths resolve against the config folder).
- Required header fields: `type`, `project`, `status` (`unreviewed` | `reviewed`), `written` (YYYY-MM-DD), `read` (integer). Optional: `client`, `country`, `years` (`YYYY` or `YYYY-YYYY`), `machines`, `infeed`, `outfeed`, `end_effectors`, `speed`. Unknown fields are kept and ignored.
- Body section order: `## Summary`, `## A. Client and machines`, `## B. The line`, `## C. History`, `## D. Key documents`, `## Checked, not found`.
- Exact texts: `No written card yet.`; `Written card ({status}, {written}, built from {read} documents):`; `card {file} unreadable: {reason}`; `No knowledge cards on this machine (run pull).`; coverage line `Cards exist for {n} of {m} projects: for a complete answer, also use search / list_projects.`

## Review Focus

- A card saved by Notepad (UTF-8 with BOM, CRLF line ends) must parse like any other - test in Task 1.
- List items with commas inside parentheses (`[flowpack (bar 100 g, 4 formats), tray]`) must stay one item - test in Task 1.
- A card caught half-written (broken header) is reported unreadable, then served once the file is complete, without a restart - test in Task 1.
- `project_card` for a sub-project (`1_Athos`, file `MOUSQUETAIRES__1_Athos.md`) and for a name in another case (`thor`) finds the card - test in Task 2.
- Filter words with accents, plurals or another language (`ventouses`, `Flow-pack`, `sachet`) match the word-list value - test in Task 3.

---

## File Structure

- Create `src/dmx_docs/knowledge.py` - card parsing, word list, in-memory store with reload, `check`.
- Modify `src/dmx_docs/config.py` - `knowledge_path` field and its loading.
- Modify `src/dmx_docs/tools.py` - `DocTools.knowledge`, card appended in `project_card`, new `catalog`.
- Modify `src/dmx_docs/server.py` - `catalog` tool, instructions line.
- Modify `src/dmx_docs/cli.py` - `knowledge check` command.
- Modify `scripts/dmx.ps1` - pull mirrors `<shared>\knowledge`.
- Create `knowledge/README.md`, `knowledge/vocabulary.md` - format + procedure, first word list (copied to `U:\DMX-RAG\knowledge` at deploy).
- Modify `skills/dmx-research/references/projects.md`, `maps/projects.md` - routing.
- Create `tests/test_knowledge.py`; modify `tests/test_index_and_tools.py`, `tests/test_dmx_ps1.py`.

---

### Task 1: `knowledge.py` - parse cards and word list, keep them in memory

**Files:**
- Create: `src/dmx_docs/knowledge.py`
- Test: `tests/test_knowledge.py`

**Interfaces:**
- Produces:
  - `class CardError(ValueError)`
  - `@dataclass Card: file: str` (file name, e.g. `THOR.md`), `fields: dict[str, str | list[str]]`, `body: str`; properties `project -> str`, `status -> str`, `written -> str`, `read -> int`, and `values(key) -> list[str]` (list field as is, scalar as `[value]`, missing as `[]`).
  - `parse_card(text: str, file: str) -> Card` - raises `CardError(reason)` for: no `---` header block, a header line without `key:`, missing required field, `status` not `unreviewed`/`reviewed`, `written` not `YYYY-MM-DD`, `read` not an integer.
  - `form_of(entry: str) -> str` - `"flowpack (bar 100 g)"` -> `"flowpack"`.
  - `@dataclass Vocabulary: kinds: dict[str, dict[str, str]]` (kind -> folded word or synonym -> value); classmethod `Vocabulary.parse(text: str) -> Vocabulary`; `canonical(kind: str, word: str) -> str | None`; `load_vocabulary(path: str) -> Vocabulary` (missing file -> empty).
  - `class Knowledge(root: str | None)`: `available -> bool` (root folder exists); `cards() -> list[Card]` (sorted by project); `errors() -> dict[str, str]` (file -> reason); `vocabulary -> Vocabulary`; `card(name: str) -> Card | None`. Every public call first re-reads files of `root\projects\*.md` whose mtime changed, drops deleted ones, and reloads `root\vocabulary.md` when its mtime changed.

- [ ] **Step 1: Write the failing tests** in `tests/test_knowledge.py`

```python
CARD = """---
type: project
project: THOR
status: unreviewed
written: 2026-10-16
client: ACME Foods
years: 2019-2020
machines: [Paloma 4R, Presto]
infeed: [flowpack (chocolate bar 100 g, 4 formats), loose (biscuit)]
outfeed: [wrap-around case (24 bars)]
end_effectors: [suction cups]
speed: 120 products/min
read: 6
---
## Summary
Line for ACME Foods.
"""
VOCAB = """## forms
- flowpack: flow-pack, flow pack, sachet flowpack
- pouch: sachet, beutel
- loose: vrac, en vrac
- wrap-around case: wrap-around, wraparound
## end_effectors
- suction cups: ventouse, ventouses, suction cup
- mechanical gripper: pince, pinces, préhenseur à doigts
"""

def test_parse_card_fields_and_lists():
    c = parse_card(CARD, "THOR.md")
    assert (c.project, c.status, c.written, c.read) == ("THOR", "unreviewed", "2026-10-16", 6)
    assert c.values("infeed") == ["flowpack (chocolate bar 100 g, 4 formats)", "loose (biscuit)"]
    assert c.values("client") == ["ACME Foods"] and c.values("country") == []
    assert c.body.startswith("## Summary")

def test_parse_card_accepts_bom_and_crlf():
    c = parse_card("\ufeff" + CARD.replace("\n", "\r\n"), "THOR.md")
    assert c.values("machines") == ["Paloma 4R", "Presto"] and "\r" not in c.body

@pytest.mark.parametrize("broken, reason", [
    (CARD.replace("status: unreviewed", "status: done"), "status"),
    (CARD.replace("read: 6\n", ""), "read"),
    (CARD.replace("written: 2026-10-16", "written: yesterday"), "written"),
    ("## Summary only", "header"),
])
def test_parse_card_errors_name_the_problem(broken, reason):
    with pytest.raises(CardError, match=reason):
        parse_card(broken, "THOR.md")

def test_vocabulary_synonyms_fold_accents_and_case():
    v = Vocabulary.parse(VOCAB)  # helper used by load_vocabulary
    assert v.canonical("end_effectors", "Ventouses") == "suction cups"
    assert v.canonical("end_effectors", "préhenseur à doigts") == "mechanical gripper"
    assert v.canonical("forms", "Flow-pack") == "flowpack"
    assert v.canonical("forms", "carton") is None
    assert form_of("wrap-around case (24 bars)") == "wrap-around case"

def test_knowledge_reloads_changed_files_and_reports_bad_ones(tmp_path):
    proj = tmp_path / "knowledge" / "projects"; proj.mkdir(parents=True)
    (proj / "THOR.md").write_text(CARD, encoding="utf-8")
    (proj / "ODIN.md").write_text("---\ntype: project\n", encoding="utf-8")   # caught half-written
    k = Knowledge(str(tmp_path / "knowledge"))
    assert [c.project for c in k.cards()] == ["THOR"]
    assert "ODIN.md" in k.errors()
    (proj / "ODIN.md").write_text(CARD.replace("THOR", "ODIN"), encoding="utf-8")
    os.utime(proj / "ODIN.md", (time.time() + 5, time.time() + 5))   # mtime resolution
    (proj / "THOR.md").unlink()
    assert [c.project for c in k.cards()] == ["ODIN"] and k.errors() == {}

def test_knowledge_without_folder(tmp_path):
    k = Knowledge(str(tmp_path / "missing"))
    assert not k.available and k.cards() == [] and Knowledge(None).cards() == []
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_knowledge.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'dmx_docs.knowledge'` (import at top: `from dmx_docs.knowledge import Card, CardError, Knowledge, Vocabulary, form_of, parse_card`).

- [ ] **Step 3: Implement `src/dmx_docs/knowledge.py`**

Module docstring in the style of `worldmap.py` (what the cards are, where they live, that documents beat cards). Read files with `encoding="utf-8-sig"` and normalise `\r\n` to `\n`. Header list values: strip the brackets and split on commas at parenthesis depth 0. `Vocabulary.parse(text)`: under `## <kind>` headings, each `- <value>: <syn>, <syn>` line maps `register.fold(value)` and `register.fold(syn)` of each synonym to `value`; `canonical` folds its argument the same way. `Knowledge.card(name)`: match `register.fold(name)` against the folded `project` field, the folded file stem, and the folded part of the stem after `__` (sub-projects).

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_knowledge.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dmx_docs/knowledge.py tests/test_knowledge.py
git commit -m "knowledge.py: parse project cards and the word list, reload changed files"
```

---

### Task 2: config + `project_card` shows the written card

**Files:**
- Modify: `src/dmx_docs/config.py` (field next to `map_path`; loading next to the world-map lines ~211)
- Modify: `src/dmx_docs/tools.py` (`DocTools.__init__` ~344, `project_card` ~693-810)
- Test: `tests/test_knowledge.py`

**Interfaces:**
- Consumes: `Knowledge`, `Card` from Task 1.
- Produces: `Config.knowledge_path: str | None = None`; `DocTools.knowledge: Knowledge` (built from `cfg.knowledge_path`); `DocTools._project_names(con) -> set[str]` (distinct `doc_facets.project` plus `subproject`) used by Task 3 and Task 4.

- [ ] **Step 1: Write the failing tests**

Add a module fixture `indexed(tmp_path, make_cfg)` that builds a tiny projects index the way `tests/test_register.py::test_register_links_projects_and_filters` does (projects `THOR` and `MOUSQUETAIRES\1_Athos`, one PDF each via `make_pdf`, `run_index(cfg, progress=quiet)`), sets `cfg.knowledge_path = str(tmp_path / "knowledge")` and writes `THOR.md` (CARD) and `MOUSQUETAIRES__1_Athos.md` (CARD with `project: 1_Athos`).

```python
def test_config_knowledge_path_default_and_override(tmp_path):
    cfg_file = tmp_path / "config.toml"
    base = f'data_dir = "{(tmp_path / "data").as_posix()}"\n[worlds.projects]\nroots = ["{tmp_path.as_posix()}"]\n'
    cfg_file.write_text(base, encoding="utf-8")
    assert load_config(cfg_file, world="projects").knowledge_path == str(tmp_path / "knowledge")
    cfg_file.write_text(base + 'knowledge = "cards"\n', encoding="utf-8")
    assert load_config(cfg_file, world="projects").knowledge_path == str(tmp_path / "cards")
    # (adapt the minimal TOML to whatever tests/test_worlds.py uses to load a [worlds.projects] config)

def test_project_card_appends_the_written_card(indexed):
    out = DocTools(indexed).project_card("thor")
    assert "Written card (unreviewed, 2026-10-16, built from 6 documents):" in out
    assert out.index("Written card") > out.index("Documents by type")
    assert "Line for ACME Foods." in out

def test_project_card_finds_a_subproject_card(indexed):
    assert "Written card" in DocTools(indexed).project_card("1_Athos")

def test_project_card_without_card_or_folder(indexed):
    (Path(indexed.knowledge_path) / "projects" / "THOR.md").unlink()
    assert DocTools(indexed).project_card("THOR").rstrip().endswith("No written card yet.")
    indexed.knowledge_path = None
    assert "No written card yet." in DocTools(indexed).project_card("THOR")

def test_project_card_names_an_unreadable_card(indexed):
    (Path(indexed.knowledge_path) / "projects" / "THOR.md").write_text("---\ntype: project\n", encoding="utf-8")
    assert "card THOR.md unreadable:" in DocTools(indexed).project_card("THOR")
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_knowledge.py -k "config or project_card" -v`
Expected: FAIL (`Config` has no `knowledge_path`; no "Written card" in the output).

- [ ] **Step 3: Implement**

`config.py`: `knowledge_path: str | None = None  # knowledge cards (knowledge\ next to config.toml), see knowledge.py`; in `load_config`, `w.get("knowledge") or "knowledge"`, resolved like `map` (absolute kept, relative against `path.parent`). `tools.py`: `self.knowledge = knowledge.Knowledge(cfg.knowledge_path)`; in `project_card`, after the "Other projects for the same client" block and before the closing register note, append a blank line and one of: the "Written card ..." line plus `card.body`; `card <file> unreadable: <reason>` when an error file's stem folds to the asked name; `No written card yet.`

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_knowledge.py tests/test_register.py -v`
Expected: all PASS (existing `project_card` tests unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/dmx_docs/config.py src/dmx_docs/tools.py tests/test_knowledge.py
git commit -m "project_card: show the project's written knowledge card"
```

---

### Task 3: `catalog` tool

**Files:**
- Modify: `src/dmx_docs/tools.py` (new method after `project_card`)
- Modify: `src/dmx_docs/server.py` (tool function next to `project_card`; registration in the `profile == "projects"` block; one routing sentence in `INSTRUCTIONS` step 1)
- Modify: `tests/test_index_and_tools.py:171` (expected tool names gain `"catalog"`)
- Test: `tests/test_knowledge.py`

**Interfaces:**
- Consumes: `DocTools.knowledge`, `DocTools._project_names(con)` (Task 2), `Vocabulary.canonical`, `form_of` (Task 1).
- Produces: `DocTools.catalog(infeed: str | None = None, outfeed: str | None = None, end_effector: str | None = None, machine: str | None = None, client: str | None = None, country: str | None = None, year_from: int | None = None, year_to: int | None = None, status: str | None = None, text: str | None = None, limit: int = 100) -> str`; MCP tool `catalog` with the same parameters.

- [ ] **Step 1: Write the failing tests** (reuse the `indexed` fixture; add `vocabulary.md` = VOCAB and a second card `ODIN.md`: client Globex, years 2008, infeed `[pouch (coffee)]`, end_effectors `[mechanical gripper]`, status reviewed)

```python
def test_catalog_lists_every_card_with_coverage(indexed):
    out = DocTools(indexed).catalog()
    assert "THOR · ACME Foods · 2019-2020 · Paloma 4R, Presto · " in out
    assert "flowpack (chocolate bar 100 g, 4 formats), loose (biscuit) -> wrap-around case (24 bars)" in out
    assert out.rstrip().splitlines()[-1].startswith("Cards exist for ")

@pytest.mark.parametrize("kwargs, expected", [
    (dict(infeed="Flow-pack"), ["THOR"]),
    (dict(infeed="sachet"), ["ODIN"]),
    (dict(end_effector="ventouses"), ["THOR"]),
    (dict(machine="presto"), ["THOR"]),
    (dict(client="globex"), ["ODIN"]),
    (dict(year_from=2015), ["THOR"]),
    (dict(year_to=2010), ["ODIN"]),
    (dict(status="reviewed"), ["ODIN"]),
    (dict(text="acme foods"), ["THOR"]),
])
def test_catalog_filters(indexed, kwargs, expected):
    out = DocTools(indexed).catalog(**kwargs)
    listed = [line.split(" · ")[0] for line in out.splitlines() if " · " in line]
    assert listed == expected

def test_catalog_without_cards_on_this_machine(indexed):
    indexed.knowledge_path = str(Path(indexed.knowledge_path).parent / "nowhere")
    assert DocTools(indexed).catalog() == "No knowledge cards on this machine (run pull)."

def test_catalog_lists_unreadable_cards(indexed):
    (Path(indexed.knowledge_path) / "projects" / "BAD.md").write_text("no header", encoding="utf-8")
    assert "card BAD.md unreadable:" in DocTools(indexed).catalog()
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_knowledge.py -k catalog -v`
Expected: FAIL with `AttributeError: 'DocTools' object has no attribute 'catalog'`.

- [ ] **Step 3: Implement `DocTools.catalog` and the tool**

Matching: for `infeed`/`outfeed`/`end_effector`, take `canonical(kind, query)`; if found, keep cards with an entry whose `form_of(...)` has the same canonical value; if not in the word list, keep cards where `register.fold(query)` is a substring of a folded entry. `machine`/`client`/`country`/`status`/`text`: folded substring of the folded field (or body + header for `text`). Years: parse `years` into (first, last); keep when the range overlaps the filter; a card without `years` is left out when a year filter is given. One line per card, fields joined by ` · `, missing values as `?`, infeed and outfeed joined by `, ` around ` -> `. After the lines: unreadable cards (`card X unreadable: reason`, one per line), then the coverage line with `n = len(cards)` and `m = len(_project_names(con))`. Server docstring: say it lists the written cards (what goes in, what comes out, end effector, speed, client, years, machines), that values come from Claude-written cards with sources and the documents win, and that the coverage line tells how complete it is; describe each filter in one line like `list_projects`. Instructions: in step 1 after the `list_projects(machine=...)` sentence add "For 'which projects ...' and 'what did we build for ...' questions, start with `catalog` (written project cards: infeed, outfeed, end effector, speed), then check in the documents; mind its coverage line."

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_knowledge.py tests/test_index_and_tools.py tests/test_profiles.py -v`
Expected: all PASS (marketing/documentation tool sets unchanged).

- [ ] **Step 5: Commit**

```bash
git add src/dmx_docs/tools.py src/dmx_docs/server.py tests/test_knowledge.py tests/test_index_and_tools.py
git commit -m "catalog tool: filter the written project cards, with coverage"
```

---

### Task 4: `dmx-docs knowledge check`

**Files:**
- Modify: `src/dmx_docs/knowledge.py` (add `check`)
- Modify: `src/dmx_docs/cli.py` (sub-command `knowledge` with positional `action` choices `["check"]`; dispatch next to `map`)
- Test: `tests/test_knowledge.py`

**Interfaces:**
- Consumes: `Knowledge`, `Card`, `form_of` (Task 1); `DocTools._con()`, `DocTools._project_names(con)` (Task 2); register table `register_machines` (name/project columns).
- Produces: `@dataclass Issue: file: str, level: str ("error" | "warning"), message: str`; `check(k: Knowledge, con) -> list[Issue]`; CLI prints `<file>: <level>: <message>` per issue, then `N cards, E errors, W warnings`, exit code 1 when any error.

- [ ] **Step 1: Write the failing tests** (reuse `indexed`. Fixture `good_card(indexed) -> Path`: rewrites `THOR.md` as CARD's header plus a body with all six sections where `## A.` has `- Client ACME Foods [register]` and `- Paloma 4R and Presto, 2019-2020 [register]`, `## B.` has one line per infeed/outfeed/end effector/speed value ending with `[<real indexed THOR PDF path> p.1]`, and returns the path; it also removes `MOUSQUETAIRES__1_Athos.md` and sets `written` to today so nothing is stale. Fixture `indexed_config_file(indexed, tmp_path) -> Path`: a config.toml whose roots, data_dir and knowledge point at `indexed`'s.)

```python
def issues(cfg):
    tools = DocTools(cfg)
    con = tools._con()
    try:
        return [(i.file, i.level, i.message) for i in knowledge.check(tools.knowledge, con)]
    finally:
        con.close()

def test_check_accepts_a_good_card(indexed, good_card):
    assert issues(indexed) == []

@pytest.mark.parametrize("change, level, words", [
    (lambda t: t.replace(" [register]", "", 1), "error", "no source"),
    (lambda t: t.replace("speed: 120 products/min", "speed: 90 products/min"), "error", "90 products/min"),
    (lambda t: t.replace("[suction cups]", "[magnets]"), "error", "word list"),
    (lambda t: t.replace("p.1]", "p.1] [N:\\nowhere\\x.pdf p.2]"), "error", "cited file not in the index"),
    (lambda t: t.replace("project: THOR", "project: NOBODY"), "error", "not in the index nor the register"),
    (lambda t: t.replace("written: 2026-10-16", "written: 2000-01-01"), "warning", "older than"),
])
def test_check_rules(indexed, good_card, change, level, words):
    good_card.write_text(change(good_card.read_text(encoding="utf-8")), encoding="utf-8")
    found = issues(indexed)
    assert any(lv == level and words in msg for _, lv, msg in found), found

def test_check_reports_unreadable_cards(indexed, good_card):
    good_card.write_text("no header", encoding="utf-8")
    assert issues(indexed)[0][1] == "error"

def test_cli_knowledge_check_exit_code(indexed_config_file, good_card, capsys):
    from dmx_docs import cli
    cli.main(["--config", str(indexed_config_file), "knowledge", "check"])
    assert "1 cards, 0 errors, 0 warnings" in capsys.readouterr().out
    good_card.write_text("no header", encoding="utf-8")
    with pytest.raises(SystemExit) as e:
        cli.main(["--config", str(indexed_config_file), "knowledge", "check"])
    assert e.value.code == 1 and "THOR.md: error:" in capsys.readouterr().out
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_knowledge.py -k check -v`
Expected: FAIL with `AttributeError: module 'dmx_docs.knowledge' has no attribute 'check'`.

- [ ] **Step 3: Implement `check` and the CLI command**

Rules, in this order per card: unreadable (from `k.errors()`); every `- ` line under `## A.`, `## B.`, `## C.` has a source - a `[...]` that is not part of `[[...]]`; every header value of `client`, `country`, `years`, `machines`, `end_effectors`, `speed` appears folded in the folded body, and for `infeed`/`outfeed` the form or one of its synonyms does; every infeed/outfeed form and end effector has a `canonical` value; every cited source that contains `\` (strip a trailing ` p.<n>` or ` slide <n>`) has its `store.path_key` in `docs` (message `cited file not in the index: <path>`); the project is in `_project_names(con)` or in `register_machines.name`/`project` (folded); warning when `written` is older than the newest `docs.mtime` of the project's documents (`doc_facets.project` or `subproject`). The message of the stale warning: `written <date> is older than the newest document (<date>): refresh the card`.

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_knowledge.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add src/dmx_docs/knowledge.py src/dmx_docs/cli.py tests/test_knowledge.py
git commit -m "dmx-docs knowledge check: validate the cards against the index and the word list"
```

---

### Task 5: pull mirrors the knowledge folder

**Files:**
- Modify: `scripts/dmx.ps1` (pull branch, after the maps copy ~488; help text line ~15)
- Test: `tests/test_dmx_ps1.py`

**Interfaces:**
- Consumes: the default `knowledge_path` from Task 2 (`<local config dir>\knowledge`).

- [ ] **Step 1: Write the failing test**

```python
def test_pull_mirrors_the_knowledge_cards(ready):
    shared, local, env = ready
    cards = shared / "knowledge" / "projects"
    cards.mkdir(parents=True)
    (cards / "THOR.md").write_text("card", encoding="utf-8")
    (cards / "ODIN.md").write_text("card", encoding="utf-8")
    (shared / "knowledge" / "vocabulary.md").write_text("words", encoding="utf-8")
    assert run(shared, local, "pull", "-World", "projects", env=env).returncode == 0
    assert (local / "knowledge" / "projects" / "THOR.md").exists()
    assert (local / "knowledge" / "vocabulary.md").read_text(encoding="utf-8") == "words"
    (cards / "ODIN.md").unlink()                      # a card removed in the shared folder ...
    assert run(shared, local, "pull", "-World", "projects", env=env).returncode == 0
    assert not (local / "knowledge" / "projects" / "ODIN.md").exists()   # ... is removed here too
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_dmx_ps1.py -k knowledge -v`
Expected: FAIL on the first `exists()` assertion.

- [ ] **Step 3: Implement**

In the pull branch: when `<shared>\knowledge` exists, remove `<local>\knowledge` (if present) and `Copy-Item -Recurse` the shared folder there; comment "knowledge cards (catalog / project_card), mirrored so removed cards disappear here too". Add the folder to the help text list of shared files.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest tests/test_dmx_ps1.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/dmx.ps1 tests/test_dmx_ps1.py
git commit -m "pull: mirror the shared knowledge cards to the machine"
```

---

### Task 6: README, word list, routing in skill and map

**Files:**
- Create: `knowledge/README.md`, `knowledge/vocabulary.md`
- Modify: `skills/dmx-research/references/projects.md` ("Filters and project tools" and "Aggregation" sections), `maps/projects.md`
- Test: `tests/test_knowledge.py`, existing `tests/test_skill.py`

**Interfaces:**
- Consumes: `parse_card`, `load_vocabulary`, `form_of` (Task 1); `catalog` parameters (Task 3).

- [ ] **Step 1: Write the failing test**

```python
def test_readme_example_card_follows_the_rules():
    root = Path(__file__).resolve().parents[1] / "knowledge"
    text = (root / "README.md").read_text(encoding="utf-8")
    example = re.search(r"```markdown\n(---\n.*?)```", text, re.S).group(1)
    card = parse_card(example, "EXAMPLE.md")
    vocab = load_vocabulary(str(root / "vocabulary.md"))
    for kind, key in (("forms", "infeed"), ("forms", "outfeed"), ("end_effectors", "end_effectors")):
        for entry in card.values(key):
            assert vocab.canonical(kind, form_of(entry)), entry
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_knowledge.py -k readme -v`
Expected: FAIL with `FileNotFoundError` (no `knowledge/README.md`).

- [ ] **Step 3: Write the files**

`knowledge/README.md`: purpose (one paragraph, documents win), the full example card (fictional ACME project, from the spec), the header field table, the body rules, the word-list rule, the writing procedure (4 steps with the ~8 document budget and the search terms "ventouse", "préhenseur", "pince", "cadence"), the work order (sample, then current projects, then `2_Hors_Garantie` newest first, ~20 per session), and "run `dmx-docs --world projects knowledge check` after each card". `knowledge/vocabulary.md`: first word list - forms: loose, flowpack, pouch, tray, box, case, wrap-around case, bottle, can, cup, blister, bag; end_effectors: suction cups, mechanical gripper, needles, magnets, fork, bernoulli; each with French/German/English synonyms. `references/projects.md`: in "Filters and project tools" a bullet "**Written project cards?** `catalog infeed=\"flowpack\"`, `catalog end_effector=\"suction cups\" year_from=2015`..." (what it holds, coverage line, verify in documents); `project_card` bullet mentions the written card at its end; "Aggregation" starts with `catalog` before `list_projects`. `maps/projects.md`: one line under the tools section pointing to `catalog` / written cards.

- [ ] **Step 4: Run to verify they pass**

Run: `pytest tests/test_knowledge.py tests/test_skill.py -v`
Expected: all PASS (`test_skill` checks the `catalog` calls in the skill against the real server schema).

- [ ] **Step 5: Commit**

```bash
git add knowledge skills/dmx-research/references/projects.md maps/projects.md tests/test_knowledge.py
git commit -m "Knowledge cards: README with the procedure, first word list, routing in skill and map"
```

---

### Task 7: full test run, deploy, first real cards

**Files:** none in the repo (deployment only).

- [ ] **Step 1: Full test suite**

Run: `pytest -q`
Expected: all pass (same skips as before).

- [ ] **Step 2: Merge and deploy** (after the user approves the branch)

Merge into main; push `U:\DMX-RAG\app` main (allowed since 2026-10-10). Copy `knowledge\README.md` and `knowledge\vocabulary.md` to `\\DMX-FS01\Users$\Johan.Monard\DMX-RAG\knowledge\` (create `projects\`), and `maps\projects.md` to `...\DMX-RAG\maps\`. Build the skill zip with `scripts/build_skill.py`; the user uploads it in Claude Desktop.

- [ ] **Step 3: Real run**

Write 2-3 cards for projects the user names, with the README procedure; run `dmx-docs --world projects knowledge check` until clean; `pull` on the laptop; in Claude Desktop ask a "which projects ..." question and check that `catalog` is called first and its coverage line is quoted.

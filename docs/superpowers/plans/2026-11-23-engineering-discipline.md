# Engineering Discipline Label Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the configured folder groups of the `engineering` world by an automatic discipline label per document (Mechanical, Electrical, Software, Mixed, Unknown) decided at indexing from user-written folder rules, then keyword scoring, with a keyword-suggestions report and a reminder after every run.

**Architecture:** The group code of the first Engineering build is removed (roots are ordinary again; `section` = first folder below the root). A pure module scores text against word lists read from `disciplines.toml`; a DB step labels documents from their stored chunks (no re-extraction), writes `doc_facets.category`, a `doc_discipline` table and a classification CSV; a second step proposes keywords from confidently labelled documents. Both steps run at the end of `run_index` for the `engineering` profile.

**Tech Stack:** Python 3.11 (`tomllib`, `unicodedata`, `fnmatch`, `csv`, `sqlite3`), pytest.

**Spec:** `docs/superpowers/specs/2026-11-23-engineering-discipline-design.md` (changes `2026-11-21-engineering-world-design.md`)

## Global Constraints

* `EXTRACT_VERSION` stays `2`; the other four worlds, their extensions, tools and facets do not change.
* Labels are exactly `Mechanical`, `Electrical`, `Software`, `Mixed`, `Unknown`; the filter name stays `category` (no server signature change); the hit line is `discipline <D> · section <S>`.
* Decision order: (a) folder rule written by the user, first match wins; (b) keyword scoring; (c) `Unknown`. Folder rules start empty; no folder -> discipline mapping is ever built into the code.
* Scoring weights: `strong = 3`, `weak = 1`; title, file name and path below the root count ×3; the first three chunks count ×1; per-word count capped at 5 (head and body each). Defaults `min_score = 6`, `mixed_share = 0.30`, `top_share = 0.70`.
* Editing `disciplines.toml` relabels every document of the world from the stored chunks on the next run, without extracting anything.
* Keyword suggestions are proposals only: nothing is ever written to `disciplines.toml` by code.
* Reports are UTF-8 with BOM, `;`-separated (as `review.csv` of the offers world), written in `cfg.world_dir`.
* The reminder after every run reads exactly: `Keyword suggestions: <N> waiting for your review (<M> new since the last run) in <path>. Nothing is added to disciplines.toml until you approve it.` and, with no rows, `Keyword suggestions: none.`
* Tests use made-up folder names and text only. Files containing Windows paths are edited with the Edit/Write tools, not shell heredocs.
* Test command (from the worktree root, PowerShell): `$env:PYTHONPATH='src'; C:\dmx-rag\venv\Scripts\python.exe -m pytest <files> -q -p no:cacheprovider`. Never run the whole suite from a task (the controller does, excluding `tests/test_dmx_ps1.py`, which hangs here).

## Review Focus

1. `disciplines.toml` missing -> every document `Unknown` with one warning line; malformed TOML or an unknown discipline name in a folder rule -> the index run stops before extraction with a one-line message, exit code 2. (Tasks 2, 3)
2. A document without chunks (image only, empty, error) is still labelled from its path and title; with nothing to score it is `Unknown`. (Task 3)
3. A document re-extracted with different text is relabelled on the next run; an unchanged one is not rescored. (Task 3)
4. Accents and case: `Préhenseur`, `PREHENSEUR` and `prehenseur` are the same word; a word never matches inside a longer word (`vis` does not match `visage`). (Task 2)
5. A word list entry that is also a suggestion candidate is never suggested; a word seen in one folder only is never suggested. (Task 4)

---

## File structure

| File | Responsibility |
|---|---|
| `src/dmx_docs/disciplines.py` (create) | rules file, normalisation, scoring, decision; no database |
| `src/dmx_docs/labelling.py` (create) | database step: label documents, `doc_discipline`, classification report |
| `src/dmx_docs/suggestions.py` (create) | keyword candidates, report, reminder lines, `meta` bookkeeping |
| `src/dmx_docs/config.py`, `sources.py`, `facets.py`, `indexer.py`, `cli.py`, `tools.py`, `server.py`, `worldmap.py` (modify) | remove groups; wire the steps; texts |
| `disciplines.example.toml` (create) | seeded word lists, empty folder rules; the shared file is a copy |
| `scripts/dmx.ps1`, `skills/dmx-research/references/engineering.md`, `README.md`, `config.example.toml` (modify) | copy rules down and reports up; documentation |
| `tests/test_engineering.py` (rewrite), `tests/test_disciplines.py`, `tests/test_labelling.py`, `tests/test_suggestions.py` (create) | tests |

---

### Task 1: Remove the groups

**Files:**
- Modify: `src/dmx_docs/config.py` (`Config.groups`, `_read_groups`, the engineering block of `load_config`), `src/dmx_docs/sources.py` (`GroupError`, `sync_group_roots`), `src/dmx_docs/facets.py` (`groups_fingerprint`, `compute_engineering`, `_version`, `refresh`), `src/dmx_docs/indexer.py:225-231,400`, `src/dmx_docs/cli.py:117-131`, `src/dmx_docs/tools.py:362,477-490`, `src/dmx_docs/worldmap.py:196-200`
- Test: `tests/test_engineering.py` (rewrite)

**Interfaces:**
- Produces:
  - `config.load_config` accepts `roots` for the `engineering` profile like for Marketing; a `groups` key in the world table raises `ValueError(f"World '{name}': groups are no longer used; write the folders in roots")`.
  - `facets.compute_engineering(path: str, root: str) -> dict` returns `{"section": <first folder below root, None for a file directly in the root>, "facet_source": "folder"}`; `{}` only when `root` is empty (a document under no configured root). `category` is never set here.
  - `facets.refresh(con, roots, profile="projects") -> int` (the `groups` parameter is removed); the stored version of the profile is `"engineering:2"`.
  - `Config.groups`, `sources.GroupError`, `sources.sync_group_roots`, `facets.groups_fingerprint` no longer exist.
  - `DocTools._facets` line for the profile: `discipline <category or 'Unknown'> · section <section or '(none)'>`; `world_map` head lines `By discipline: ...` (from `category`) and `By section: ...`.

- [ ] **Step 1: Rewrite `tests/test_engineering.py`.** Keep its `write_config` and `facet_rows` helpers (the config now has `roots = ['<folder>']` in the world table and no groups table). Tests, all with made-up folders (`22_Mec` with `224 - Pneumatique\Pompes.pdf`, `229 - Archives\Old\Ancien.pdf`, `Racine.pdf`):
  - `test_sections_come_from_the_first_folder_below_the_root`: after `run_index`, `facet_rows(cfg)` maps `Pompes.pdf -> (None, "224 - Pneumatique", "folder")`, `Ancien.pdf -> (None, "229 - Archives", "folder")`, `Racine.pdf -> (None, None, "folder")` (tuple = category, section, facet_source).
  - `test_search_filters_by_section_and_shows_the_discipline_line`: `search("pompe", mode="keyword", section="224 - pneumatique")` contains `Pompes.pdf`, not `Ancien.pdf`, and the text `discipline Unknown · section 224 - Pneumatique`.
  - `test_a_groups_table_is_refused`: `load_config` on a config with `[worlds.engineering.groups]` raises `ValueError` matching `groups are no longer used`.
  - `test_index_status_and_world_map`: `index_status()` starts with `World: Engineering (engineering)`; `world_map()` contains `By section:` and `By discipline:`.
  - `test_facets_version_of_the_profile`: after the run, `store.get_meta(con, "facets_version") == "engineering:2"`.
- [ ] **Step 2: Run** `tests/test_engineering.py`. Expected: FAIL.
- [ ] **Step 3: Implement** the Interfaces block. In `run_index` delete the `if cfg.profile == "engineering"` sync block; in `cli.py` delete the `GroupError` import and handler (Task 3 adds the rules-error handler); the two `facets.refresh` callers lose the `groups` argument.
- [ ] **Step 4: Run** `tests/test_engineering.py tests/test_profiles.py tests/test_worlds.py tests/test_facets.py tests/test_worldmap.py tests/test_sources_and_web.py`. Expected: PASS.
- [ ] **Step 5: Commit** `Engineering world: no configured groups; roots are ordinary again`.

---

### Task 2: Rules and scoring (pure)

**Files:**
- Create: `src/dmx_docs/disciplines.py`, `tests/test_disciplines.py`

**Interfaces:**
- Produces:
  - `DISCIPLINES = ("Mechanical", "Electrical", "Software")`; `LABELS = DISCIPLINES + ("Mixed", "Unknown")`
  - `class RulesError(ValueError)`
  - `@dataclass class Rules`: `folders: list[tuple[str, str]]` (lowercased `/`-pattern, label), `words: dict[str, dict[str, list[str]]]` (discipline -> `{"strong": [...], "weak": [...]}`, entries already normalised), `min_score: int = 6`, `mixed_share: float = 0.30`, `top_share: float = 0.70`, `suggestions: dict` (keys `min_docs=5`, `min_sections=2`, `min_lift=3.0`, `max_rows=200`), `fingerprint: str`
  - `load_rules(path: str | None) -> Rules`: `None` or a missing file -> empty `Rules` with fingerprint `"none"`; otherwise the TOML of the spec section 1 (`[[folder]] pattern/discipline`, `[words.<D>] strong/weak`, `[scoring]`, `[suggestions]`); fingerprint = first 12 hex characters of the SHA-1 of the file bytes. Raises `RulesError` (message names the file) on invalid TOML, a discipline outside `DISCIPLINES + ("Mixed", "Unknown")` in a folder rule, a `[words.X]` table with `X` not in `DISCIPLINES`, or `mixed_share`/`top_share` outside (0, 1).
  - `normalize(text: str) -> str`: lowercase, accents removed (NFKD, combining marks dropped), every run of non-alphanumeric characters replaced by one space, stripped.
  - `@dataclass class Decision`: `label: str`, `source: str` (`"rule"`, `"words"` or `"none"`), `scores: dict[str, int]` (all three disciplines), `top_terms: list[str]` (up to 5 words, highest weighted contribution first)
  - `classify(rules: Rules, full_path: str, rel_path: str, title: str | None, body: str) -> Decision`: `full_path` is matched against the folder rules (lowercased, `\` -> `/`, `fnmatch.fnmatchcase`); `head = rel_path + " " + (title or "")` (the file name is part of `rel_path`); `body` is the text of the first three chunks.
  - `score_text(rules: Rules, head: str, body: str) -> tuple[dict[str, int], dict[str, int]]`: per-discipline scores and per-word weighted contributions (key `"<discipline>:<word>"`).
  - Scoring: a word entry matches as a whole word or phrase in `" " + normalize(text) + " "` (so `vis` never matches inside `visage`); `score += weight * (3 * min(count_head, 5) + min(count_body, 5))`.
  - Decision: rule match -> label of the rule, `source="rule"`. Else `total = sum(scores)`; `total < min_score` -> `Unknown`, source `"none"`. Else shares; top share `>= top_share` -> that discipline; else second share `>= mixed_share` -> `Mixed`; else the top discipline. Ties in score are broken in the order of `DISCIPLINES`.

- [ ] **Step 1: Write the failing tests** in `tests/test_disciplines.py` (rules written to `tmp_path / "disciplines.toml"` with a small made-up word list, e.g. Mechanical strong `["préhenseur", "pneumatique"]` weak `["montage"]`; Electrical strong `["schéma électrique", "variateur"]`; Software strong `["twincat"]` weak `["version"]`):
  - `test_normalize`: `normalize("Préhenseur  PNEUMATIQUE-2") == "prehenseur pneumatique 2"`.
  - `test_a_word_matches_whole_words_only` (Review Focus 4): with Mechanical weak `["vis"]`, `score_text(rules, "visage", "")` gives 0 for Mechanical and `score_text(rules, "vis M8", "")` gives 3 (weak 1 × head 3 × count 1).
  - `test_accents_and_case_do_not_matter` (Review Focus 4): `"PREHENSEUR"`, `"Préhenseur"` and `"prehenseur"` give the same scores.
  - `test_head_counts_three_times_the_body_and_counts_are_capped`: `"pneumatique"` in head -> 9, in body -> 3, 20 times in body -> 15 (cap 5 × 3).
  - `test_phrase_entry`: `"schéma électrique"` matches the phrase, not the two words apart.
  - `test_decision_unknown_below_min_score`, `test_decision_single_discipline_above_top_share`, `test_decision_mixed_when_two_disciplines_are_clear` (scores 9 and 6 -> shares 0.60 / 0.40 -> `Mixed`), `test_decision_top_discipline_when_no_second_is_clear` (scores 9, 3, 3: top share 0.60 is under `top_share`, second share 0.20 is under `mixed_share` -> `Mechanical`).
  - `test_a_folder_rule_wins_over_words`: a rule `*/doc_tech/soft/*` -> `Software` wins on a path full of mechanical words, `source == "rule"`; first matching rule wins.
  - `test_missing_file_gives_empty_rules`: `load_rules(None)` and `load_rules(str(tmp_path / "absent.toml"))` give `fingerprint == "none"` and every document `Unknown` (Review Focus 1).
  - `test_bad_rules_raise_rules_error` (Review Focus 1), parametrised: invalid TOML text; `discipline = "Chemistry"` in a folder rule; a `[words.Chemistry]` table; `mixed_share = 1.5`. Each raises `RulesError` whose message contains `disciplines.toml`.
  - `test_fingerprint_changes_with_the_file`.
- [ ] **Step 2: Run** `tests/test_disciplines.py`. Expected: FAIL (no module).
- [ ] **Step 3: Implement** `disciplines.py` per the Interfaces block.
- [ ] **Step 4: Run** `tests/test_disciplines.py`. Expected: PASS.
- [ ] **Step 5: Commit** `Engineering: discipline rules and keyword scoring`.

---

### Task 3: Label the documents, classification report

**Files:**
- Create: `src/dmx_docs/labelling.py`, `tests/test_labelling.py`
- Modify: `src/dmx_docs/config.py` (`Config.disciplines_path`, read in `load_config`), `src/dmx_docs/indexer.py` (load rules at the start, call the step at the end), `src/dmx_docs/cli.py` (rules error), `src/dmx_docs/tools.py` (nothing else: the hit line was done in Task 1)

**Interfaces:**
- Consumes: `disciplines.load_rules`, `classify`, `Rules`, `RulesError` (Task 2); `facets.refresh` leaves `doc_facets.category` empty (Task 1).
- Produces:
  - `Config.disciplines_path: str | None`; `load_config` sets it for the `engineering` profile from `[worlds.<name>] disciplines` (relative = next to `config.toml`), default `disciplines.toml` next to `config.toml`.
  - `labelling.TABLE` (the `CREATE TABLE IF NOT EXISTS doc_discipline(doc_id INTEGER PRIMARY KEY, discipline TEXT NOT NULL, source TEXT NOT NULL, scores TEXT NOT NULL, top_terms TEXT NOT NULL, labelled_at REAL NOT NULL)` script)
  - `labelling.refresh(con, cfg: Config, rules: Rules, progress=print) -> dict[str, int]`: labels documents that have no `doc_discipline` row, whose `labelled_at < docs.indexed_at`, or all documents when `meta["disciplines_fingerprint"] != rules.fingerprint`; text = `docs.title` and the first three chunks (`ORDER BY page_no, seq LIMIT 3`) of the document, `rel_path` = path below the root it lies under (`facets.root_of`); then, for every document, copies `doc_discipline.discipline` into `doc_facets.category`; stores the fingerprint; writes `cfg.world_dir / "engineering-classification.csv"`; returns counts per label. When `rules.fingerprint == "none"`, calls `progress("WARNING: disciplines.toml not found: every document is Unknown")` once.
  - `labelling.write_report(con, path: Path) -> int`: columns `path;section;discipline;source;mechanical;electrical;software;top_terms` (scores as integers, `top_terms` joined with `, `), one row per document, UTF-8 BOM, `;`-separated.
  - `indexer.run_index`: for `cfg.profile == "engineering"` loads `rules = disciplines.load_rules(cfg.disciplines_path)` right after `sources.refresh(cfg, con)` (a `RulesError` closes the connection and propagates) and calls `labelling.refresh(con, cfg, rules, progress)` right after `facets.refresh(...)`, inside the `try` that keeps the run alive on a failure of the step (a failure is logged and warned, the run still completes, as for the OneNote pass).
  - `cli.py` index command prints `Not indexed: <message>` and exits with code 2 on `RulesError`.

- [ ] **Step 1: Write the failing tests** in `tests/test_labelling.py` (an engineering world built as in `tests/test_engineering.py`: `config.toml` with `roots`, a `disciplines.toml` next to it with a small word list and no folder rules; PDFs made with `conftest.make_pdf`):
  - `test_documents_get_a_discipline_and_the_filter_works`: three PDFs (mechanical words, electrical words, no keywords) -> `category` `Mechanical`, `Electrical`, `Unknown` in `doc_facets`; `search(..., category="mechanical")` returns only the first; the hit line shows `discipline Mechanical`.
  - `test_editing_the_rules_relabels_without_extracting` (Review Focus 3): after a first run, rewrite `disciplines.toml` (add a folder rule that makes one document `Software`), run again with `extract_file` patched to fail the test if called: the label changes, `docs.indexed_at` is unchanged.
  - `test_a_changed_document_is_relabelled_and_an_unchanged_one_is_not_rescored` (Review Focus 3): rewrite one PDF with other words (new mtime/size), run: its label changes; `doc_discipline.labelled_at` of the untouched document is unchanged.
  - `test_a_document_without_text_is_labelled_from_its_path` (Review Focus 2): a `.png` or empty PDF in a folder named `Pneumatique` gets `Mechanical` if the word list has it, else `Unknown`; no exception.
  - `test_missing_rules_file_labels_everything_unknown_with_one_warning` (Review Focus 1): collect `progress` lines; exactly one contains `disciplines.toml not found`.
  - `test_bad_rules_stop_the_run_before_extraction` (Review Focus 1): invalid TOML -> `run_index` raises `RulesError` and `extract_file` is never called; the cli path (`main(["--config", ..., "--world", "engineering", "index"])` if the cli exposes `main(argv)`; otherwise assert on `RulesError` only) prints `Not indexed:` and exits with code 2.
  - `test_classification_report`: the CSV starts with the BOM, has the header above, one row per document, and the `source` column shows `rule` for the rule-decided document.
  - `test_label_counts_are_reported`: `labelling.refresh` returns `{"Mechanical": 1, "Electrical": 1, "Unknown": 1}`-shaped counts (missing labels absent or zero).
- [ ] **Step 2: Run** `tests/test_labelling.py`. Expected: FAIL.
- [ ] **Step 3: Implement** per the Interfaces block (one transaction per refresh; `labelled_at = time.time()`).
- [ ] **Step 4: Run** `tests/test_labelling.py tests/test_engineering.py tests/test_index_and_tools.py tests/test_robustness.py`. Expected: PASS.
- [ ] **Step 5: Commit** `Engineering: documents get a discipline label at indexing`.

---

### Task 4: Keyword suggestions and the reminder

**Files:**
- Create: `src/dmx_docs/suggestions.py`, `tests/test_suggestions.py`
- Modify: `src/dmx_docs/indexer.py` (call after `labelling.refresh`), `src/dmx_docs/tools.py` (`index_status`)

**Interfaces:**
- Consumes: `Rules` (words, `suggestions` settings, fingerprint), `doc_discipline` rows and `normalize` (Tasks 2-3).
- Produces:
  - `suggestions.STOPWORDS: frozenset[str]` (about 150 French and English function words, normalised)
  - `@dataclass class Suggestion`: `discipline: str`, `word: str`, `docs_in: int`, `docs_out: int`, `lift: float`, `examples: list[str]` (three paths)
  - `suggestions.candidates(con, cfg, rules) -> list[Suggestion]`:
    - Reference documents: `doc_discipline.source == "rule"` (any of the three disciplines) plus documents with `source == "words"`, discipline one of the three, top share `>= 0.85` and total score `>= 3 * min_score`. `Mixed` and `Unknown` are never reference.
    - Text per reference document = the text `labelling` scored (path below root, title, first three chunks); candidates = single words and two-word phrases from `normalize(text).split()`, length `>= 4` characters (phrase: each word, ignoring stop words only at the phrase ends), not digits only, not in `STOPWORDS`, not in any list of `rules.words`.
    - The spec's rule "names inside a file that appear in a single folder are dropped" is implemented by the `min_sections` test below (distinct parent folders), not by detecting capitals.
    - For a discipline D: `df_in` = reference documents of D containing it, `df_out` = reference documents of the other disciplines; `lift = (df_in / N_in) / ((df_out + 1) / (N_out + 1))`. Kept when `df_in >= min_docs`, the documents containing it have at least `min_sections` distinct parent folders, and `lift >= min_lift`. A word whose best discipline is not D is not listed under D. At most `max_rows` per discipline, sorted by `lift` descending then word.
  - `suggestions.refresh(con, cfg, rules, progress=print) -> list[Suggestion]`: computes the candidates, writes `cfg.world_dir / "keyword-suggestions.csv"` (columns `discipline;word;docs_in;docs_out;lift;example_1;example_2;example_3`, UTF-8 BOM, `;`), reads `meta["suggestions_prev"]` (JSON list of `"<discipline>|<word>"`) to count the new ones, stores the current list there and the total in `meta["suggestions_waiting"]`, then calls `progress` with the reminder line of the Global Constraints (`<path>` = the CSV path).
  - `index_status` (engineering profile only) gains the line `Keyword suggestions waiting: <N>` (0 when the meta is absent), inserted after the `Roots:` line.

- [ ] **Step 1: Write the failing tests** in `tests/test_suggestions.py` (a DB built through `run_index` on made-up PDFs, or `doc_discipline` rows written directly with chunks, whichever keeps the test short; ≥ 8 reference documents per discipline spread over ≥ 3 folders):
  - `test_a_discriminating_word_is_suggested_with_its_stats`: a word (`sertissage`) present in 6 Mechanical reference documents in 3 folders and in none of the others appears under `Mechanical` with `docs_in == 6`, `docs_out == 0`, `lift >= 3`, three example paths.
  - `test_words_already_in_the_lists_or_stopwords_are_never_suggested` (Review Focus 5).
  - `test_a_word_seen_in_one_folder_only_is_not_suggested` (Review Focus 5): present in 6 documents of the same folder.
  - `test_a_word_below_min_docs_or_below_lift_is_not_suggested`: present in 4 documents; and a word equally frequent in two disciplines.
  - `test_mixed_unknown_and_weak_documents_are_not_reference`: a word only in `Mixed`/`Unknown` documents, and only in documents decided by words with share `< 0.85`, is not suggested; a word in rule-decided documents is.
  - `test_csv_content_and_row_cap`: header, BOM, `max_rows = 2` keeps the two best per discipline.
  - `test_reminder_lines`: first run with 3 suggestions -> `progress` receives `Keyword suggestions: 3 waiting for your review (3 new since the last run) in <path>. Nothing is added to disciplines.toml until you approve it.`; second run unchanged -> `(0 new since the last run)` and still the reminder; after the word is added to the list (rules fingerprint changes) -> fewer rows; no rows -> `Keyword suggestions: none.`
  - `test_index_status_shows_the_waiting_count`: `Keyword suggestions waiting: 3` after the first run.
  - `test_nothing_is_written_to_the_rules_file`: `disciplines.toml` bytes are identical before and after the run.
- [ ] **Step 2: Run** `tests/test_suggestions.py`. Expected: FAIL.
- [ ] **Step 3: Implement** per the Interfaces block. In `run_index` call `suggestions.refresh(con, cfg, rules, progress)` right after `labelling.refresh(...)` in the same guarded block; the reminder is the last thing the step prints.
- [ ] **Step 4: Run** `tests/test_suggestions.py tests/test_labelling.py tests/test_engineering.py`. Expected: PASS.
- [ ] **Step 5: Commit** `Engineering: keyword suggestions report and reminder after every run`.

---

### Task 5: Seeded rules, texts, scripts

**Files:**
- Create: `disciplines.example.toml`
- Modify: `src/dmx_docs/server.py` (`ENGINEERING_INSTRUCTIONS` ~l.116-130, `engineering_search` docstring ~l.537-550), `scripts/dmx.ps1` (pull and push), `skills/dmx-research/references/engineering.md`, `README.md`, `config.example.toml`, `tests/test_skill.py` (only if a name list changes)
- Test: `tests/test_disciplines.py` (one added test), `tests/test_skill.py`

**Interfaces:**
- Consumes: the rules format (Task 2), reports and hit line (Tasks 3-4).
- Produces:
  - `disciplines.example.toml`: no `[[folder]]` entries (a commented example only), `[words.Mechanical|Electrical|Software]` with about 40 `strong` and 20 `weak` French and English entries each, written from knowledge of robotic packaging lines (Mechanical: grippers, suction cups, pneumatics, vacuum, conveyors, sheet metal, machining, assembly, tolerances, SolidWorks, PDM...; Electrical: schematics, wiring, drives, servo, PLC, safety relays, EPLAN, cabinets, cables, sensors...; Software: TwinCAT, Gemini HMI, recipes, firmware, PLC code, Ethernet/Sercos, git, versions, parameters, vision software, API...), `[scoring] min_score = 6, mixed_share = 0.30, top_share = 0.70`, `[suggestions] min_docs = 5, min_sections = 2, min_lift = 3.0, max_rows = 200`. A header comment says the lists are a first draft for the user to review.
  - `dmx.ps1`: `pull` copies `<shared>\disciplines.toml` to the local config folder next to `thesaurus.toml` when it exists; `Save-LocalToMaster` copies `engineering-classification.csv` and `keyword-suggestions.csv` from the local world folder to the shared world folder when they exist (same pattern as `review.csv`); the `Copy-MasterToLocal` cleanup removes stale local copies of both reports like it does for `review.csv`.
  - Texts: every use of "group" in the Engineering instructions, docstring, skill page, README row and `config.example.toml` block becomes "discipline" (automatic and approximate: a hint, not a fact; values `Mechanical`, `Electrical`, `Software`, `Mixed`, `Unknown`); the config example shows `roots = [...]` and `disciplines = "disciplines.toml"` instead of the groups table; the skill page and instructions tell Claude to search with `category=` as a hint and again without it when results are thin, and that suggestions and the classification report exist only on the indexing side.

- [ ] **Step 1: Write the failing tests.** In `tests/test_disciplines.py`: `test_the_example_rules_file_loads_and_classifies`: `load_rules("disciplines.example.toml")` (repository root, found from `Path(__file__).parent.parent`) loads without error, has at least 30 `strong` words per discipline, no folder rules, and `classify(...)` gives `Mechanical` for `"Réglage du préhenseur à ventouses et du vérin pneumatique"`, `Electrical` for `"Schéma électrique de l'armoire: variateur, câblage, relais de sécurité"`, `Software` for `"Version du firmware TwinCAT et paramètres de la recette Gemini"`. In `tests/test_skill.py` nothing new unless a call on the skill page changes; run it.
- [ ] **Step 2: Run** `tests/test_disciplines.py::test_the_example_rules_file_loads_and_classifies`. Expected: FAIL (file missing).
- [ ] **Step 3: Write** the example file, the texts and the script changes as listed. Check `server.py` instructions and the skill page no longer contain "group" in the engineering sense: `Select-String` over those files for `group` and read each hit.
- [ ] **Step 4: Run** `tests/test_disciplines.py tests/test_skill.py tests/test_worlds.py -q`, and `C:\dmx-rag\venv\Scripts\python.exe -c "import tomllib; tomllib.load(open('config.example.toml','rb'))"`. Expected: PASS.
- [ ] **Step 5: Commit** `Engineering: example discipline word lists, texts and scripts follow the discipline label`.

---

### Task 6: Rollout (shared folder; each step with the user's go-ahead; not a code task)

- [ ] **Step 1:** Merge to `main` (fast-forward) and bring `U:\DMX-RAG\app` to `main`, when no run is going on any machine.
- [ ] **Step 2:** Back up the shared `config.toml` (`config.toml.bak-<stamp>`), replace the `[worlds.engineering.groups]` table by `roots = ['\\DMX-FS01.rotzingerag.local\Daten$\RMA_ORG\END\2_Groupes\22_Mecanique']` and `disciplines = "disciplines.toml"` in `[worlds.engineering]`; read the file back and check the UNC path starts with two backslashes; load every world from it.
- [ ] **Step 3:** Copy `disciplines.example.toml` to `U:\DMX-RAG\disciplines.toml`. Update `U:\DMX-RAG\maps\engineering.md` (group -> discipline) and rebuild the skill zip (backup first).
- [ ] **Step 4 (user, on the VM):** start OneNote once by hand, close it, then launcher 7 with the world `engineering`.
- [ ] **Step 5 (with the user):** read `engineering-classification.csv` (about 40 documents spread over sections, counts of `Unknown` and `Mixed`) and `keyword-suggestions.csv`; adjust `disciplines.toml` until the user is satisfied; the next run relabels without re-extracting.

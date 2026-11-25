# Per-Person Access Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** In worlds with `access = "acl"` (projects first), every tool answers a caller only from documents whose folder's file-server reading rule lets that caller read, with the rules copied from NTFS into a small `access.sqlite3` refreshed daily.

**Architecture:** New module `access.py` holds the rule model (SID filtering, allow/deny evaluation), the `access.sqlite3` store, the `Caller` and a per-caller `Access` view (`can_read(path_key)`, `allowed_rules`, vector mask). New module `permissions.py` refreshes `access.sqlite3` from the index's folders using `aclsurvey.read_windows_acl`. `DocTools(cfg, caller)` replaces every `self.cfg.allows` use with `self.allows`, which also asks `Access`; `VectorIndex.query` takes a row mask so semantic top-k is taken among allowed rows. Cards are filtered line by line in `knowledge.py`.

**Tech Stack:** Python 3.11, sqlite3, numpy, pywin32 (`win32security`), pytest; PowerShell 5.1 (`scripts/dmx.ps1`).

**Spec:** `docs/superpowers/specs/2026-11-25-per-person-access-design.md` (read it first; this plan does not repeat its reasons).

## Global Constraints

- Worlds without `access = "acl"` behave exactly as today: the existing suite passes unchanged (except the pre-existing failure `tests/test_ocr.py::test_stall_records_the_stuck_job_as_timeout_restarts_the_pool_and_goes_on`, which fails on main too).
- Fail closed: a document whose folder has no rule, or a world with `access = "acl"` and no `access.sqlite3`, is hidden from every caller except admins.
- A caller may read a rule when one of their SIDs is in `allow` and none is in `deny`.
- Dropped SIDs (never allow, never deny): `S-1-3-0`, `S-1-3-4`, every `S-1-5-32-*`, `S-1-5-18`, `S-1-5-19`, `S-1-5-20`, and every `S-1-5-21-*` whose domain prefix (all but the last sub-authority) differs from the AD domain prefix. `S-1-1-0` (Everyone) and `S-1-5-11` (Authenticated Users) are kept and every caller carries them.
- A hidden path answers exactly like a path that is not indexed; no tool output names a hidden document (title, path, snippet, page, picture, count).
- Only world totals reveal that hidden documents exist: `index_status` line `You can read N of M documents of this world.`
- `access.sqlite3` lives next to `index.sqlite3` (local: `<data_dir>\<world>\access.sqlite3`; master: `<shared>\worlds\<world>\access.sqlite3`); written as `access.sqlite3.new` then `os.replace`.
- The server re-reads `access.sqlite3` when its `acl_version` meta changes, checked at most once per 60 s.
- Never commit real client names in tests or docstrings (use ACME, THOR... as the existing tests do). Windows paths in files are written with the Edit/Write tools, not shell heredocs (backslashes get eaten).

## Review Focus

1. A path given with a mapped drive (`N:\...`) or other letter case must get the same decision as its UNC form → test in Task 5 (`test_mapped_drive_path_is_judged_like_its_unc_form`).
2. A caller with no matching group at all gets empty answers, "not found", and `You can read 0 of M documents`, never an exception → Task 5 (`test_caller_without_groups_sees_nothing_and_no_error`).
3. `access.sqlite3` replaced while a server process holds an `Access` → next call after the 60 s check uses the new rules; a call during the swap (file briefly missing) keeps the previous rules instead of failing → Task 1 (`test_reload_keeps_previous_rules_when_the_file_is_briefly_missing`).
4. A refresh run under an account that cannot read some folders keeps those folders' previous rules, and new folders it cannot read stay without rule → Task 2 (`test_unreadable_folder_keeps_its_previous_rule`).
5. A restricted caller still gets `limit` semantic hits when allowed documents exist beyond the unfiltered top-100 → Task 4 (`test_semantic_top_k_is_taken_among_allowed_rows`).

---

### Task 1: Rule model, access store, caller and per-caller view (`access.py`)

**Files:**
- Create: `src/dmx_docs/access.py`
- Test: `tests/test_access.py`

**Interfaces:**
- Consumes: `aclsurvey.Acl` (fields `allow`, `deny`: frozensets of SID strings).
- Produces:
  - `KEEP_ALWAYS = {"S-1-1-0", "S-1-5-11"}`
  - `def usable_sids(sids: Iterable[str], domain_prefix: str) -> frozenset[str]` — applies the Dropped-SIDs constraint.
  - `def may_read(caller_sids: frozenset[str], allow: frozenset[str], deny: frozenset[str]) -> bool`
  - `class AccessStore` — `__init__(self, path: Path)`; `@staticmethod def write(path: Path, rules: dict[str, int], folder_rules: dict[str, int | None], rule_sets: dict[int, tuple[frozenset, frozenset]], errors: dict[str, str], names: dict[str, str], version: str) -> None` (writes `<path>.new` then replaces); `def load(self) -> "AccessData | None"` (None if the file is missing).
  - `@dataclass(frozen=True) class AccessData: version: str; rules: dict[int, tuple[frozenset[str], frozenset[str]]]; folder_rule: dict[str, int]` (folder_key -> rule id; folders without rule absent).
  - Schema of `access.sqlite3`: `acl_rules(id INTEGER PRIMARY KEY, hash TEXT UNIQUE, allow TEXT, deny TEXT)` (JSON lists), `folder_acl(folder_key TEXT PRIMARY KEY, rule_id INTEGER, read_at REAL, error TEXT)`, `acl_names(sid TEXT PRIMARY KEY, name TEXT)`, `meta(key TEXT PRIMARY KEY, value TEXT)` with key `acl_version`.
  - `@dataclass(frozen=True) class Caller: sids: frozenset[str]; admin: bool = False; label: str = ""`; `@classmethod def from_token(cls, admins: list[str]) -> "Caller"` (process token: user SID + `TokenGroups` without `SE_GROUP_USE_FOR_DENY_ONLY`, plus `KEEP_ALWAYS`; `admin` when any SID resolves to a name in `admins`, case-insensitive); `@classmethod def from_groups(cls, names: list[str]) -> "Caller"` (names resolved with `LookupAccountName`, plus `KEEP_ALWAYS`, `admin=False`; unknown name → `ValueError` naming it).
  - `class Access` — `__init__(self, store: AccessStore, caller: Caller, clock=time.monotonic)`; `def can_read(self, path_key: str) -> bool` (rule of `os.path.dirname(path_key)`; admin → True; no data or no rule → False); `def allowed_rules(self) -> frozenset[int]`; `@property def version(self) -> str` (`""` without data). Reload: at most once per 60 s via `clock`, only when `acl_version` differs; if `load()` raises or returns None while data was already loaded, keep the old data.
  - `class Unrestricted` with the same three members (`can_read` always True, `allowed_rules` empty, `version = "world"`) for `access = "world"` worlds.

- [ ] **Step 1: Write the failing tests** in `tests/test_access.py`:
  - `test_usable_sids_drops_machine_relative_and_foreign_local_sids`: `usable_sids({"S-1-3-0","S-1-3-4","S-1-5-32-544","S-1-5-18","S-1-5-19","S-1-5-20","S-1-5-21-9-9-9-500","S-1-5-21-1-2-3-1105","S-1-1-0","S-1-5-11"}, "S-1-5-21-1-2-3") == {"S-1-5-21-1-2-3-1105","S-1-1-0","S-1-5-11"}`.
  - `test_deny_wins_and_one_allowed_group_is_enough`: `may_read({A,B}, {A}, set())` True; `may_read({A,B}, {A}, {B})` False; `may_read({C}, {A}, set())` False.
  - `test_store_round_trip`: write 2 rules and 3 folders (one with rule None + error) then `load()` returns `folder_rule` with 2 entries and the given `version`.
  - `test_access_hides_folders_without_rule_except_for_admins`.
  - `test_reload_keeps_previous_rules_when_the_file_is_briefly_missing`: fake clock; load v1; delete the file; advance 61 s; `can_read` still answers from v1; write v2 changing the rule; advance 61 s; answer follows v2; advance only 10 s after a further change → still v2.
  - `test_unrestricted_reads_everything`.
  - `test_from_token_has_the_user_and_everyone` (`skipif` not win32): `Caller.from_token([])` contains `S-1-1-0` and at least one `S-1-5-21-` SID.
- [ ] **Step 2: Run** `PYTHONPATH=src C:\dmx-rag\venv\Scripts\python.exe -m pytest tests/test_access.py -q` — Expected: FAIL (module missing).
- [ ] **Step 3: Implement `src/dmx_docs/access.py`** as in Interfaces. Rule ids are assigned by the writer; `hash` = sha1 of the sorted JSON of (allow, deny).
- [ ] **Step 4: Run** the same command — Expected: PASS.
- [ ] **Step 5: Commit** `git add src/dmx_docs/access.py tests/test_access.py; git commit -m "Access: rule model, access.sqlite3 store, caller and per-caller view"`

### Task 2: Permissions refresh (`permissions.py`, CLI, dmx.ps1)

**Files:**
- Create: `src/dmx_docs/permissions.py`
- Modify: `src/dmx_docs/config.py` (fields + load_config), `src/dmx_docs/cli.py` (subcommand), `scripts/dmx.ps1` (command `permissions`, `update` step, `pull` copy), `config.example.toml` (documented `[access]` table and `access = "acl"` example)
- Create: `scripts/launchers/8 - Refresh permissions.cmd`
- Test: `tests/test_permissions.py`, `tests/test_worlds.py` (config), `tests/test_dmx_ps1.py` (only if the existing layout fixtures make it a few lines; it hangs on the laptop, so run it on the VM or skip locally)

**Interfaces:**
- Consumes: `aclsurvey.read_windows_acl(path, for_files=True) -> Acl`, `aclsurvey.windows_account_name(sid) -> str`, `access.AccessStore`, `access.usable_sids`.
- Produces:
  - `Config` fields: `access: str = "world"` (per world, `"world"` or `"acl"`, other values → `ValueError` in `load_config`), `access_admins: list[str]` (from shared `[access] admins`), `access_domain_sid: str | None` (from `[access] domain_sid`); property `access_path -> Path` = `world_dir / "access.sqlite3"`.
  - `def domain_prefix(cfg) -> str` in `permissions.py`: `cfg.access_domain_sid`, else the running account's SID without its last sub-authority.
  - `@dataclass class RefreshResult: folders: int; rules: int; changed: list[tuple[str, int | None, int | None]]; unreadable: list[tuple[str, str]]; docs_affected: int; version: str`
  - `def refresh_permissions(cfg, out_paths: list[Path], read_acl=aclsurvey.read_windows_acl, account_name=aclsurvey.windows_account_name, workers: int = 16) -> RefreshResult` — folders = distinct `folder_key`/folder of `docs.path` in the local index (read-only connection); previous data from the first existing `out_paths` file; for each folder read the rule, map through `usable_sids`; read failure → previous rule kept (or no rule if none) and listed in `unreadable`; `version` = new value only if any folder's rule changed (else the previous one); writes every path in `out_paths` (master first, then local).
  - `def write_change_report(result: RefreshResult, out_dir: Path) -> Path` → `changes-<YYYYMMDD-HHMM>.md`.
  - CLI: `dmx-docs permissions [--out PATH ...] [--workers N]`; refuses with exit code 2 and the message `World '<w>' has access = "world": nothing to refresh.` when `cfg.access != "acl"`. Default `--out` = `cfg.access_path`.
  - dmx.ps1: `permissions` command (needs world access check like `index`; no LOCK) runs the CLI with `--out <SharedWorld>\access.sqlite3 --out <LocalWorld>\access.sqlite3` and writes the report to `<SharedWorld>\acl-survey`; `update` ends with the `permissions` step when the world's config has `access = "acl"`; `pull` copies `<SharedWorld>\access.sqlite3` to the local world folder via `.new` + rename (as it does for the index). Add `permissions` to the comment block's command list and to `ValidateSet`.
  - New launcher `scripts/launchers/8 - Refresh permissions.cmd`: runs `dmx.ps1 permissions -World <w>` for every world whose config has `access = "acl"` (no prompt, unattended, exit code = first failure), written like launcher 7. It is what the VM's daily scheduled task runs; the README gives the `schtasks /Create` line (daily 05:00, run as the VM account, whether logged on or not).

- [ ] **Step 1: Write the failing tests** in `tests/test_permissions.py` with an index made by `run_index` over a temp tree and a fake `read_acl` (pattern of `tests/test_aclsurvey.py::fake_reader`):
  - `test_refresh_writes_one_rule_per_folder_and_drops_builtin_sids`: folder `A` allows `{SALES, "S-1-5-32-544"}` → stored rule allow == `{SALES}`.
  - `test_unchanged_refresh_keeps_the_version` and `test_changed_rule_bumps_the_version_and_reports_the_folder` (report contains the folder path and `1 documents`).
  - `test_unreadable_folder_keeps_its_previous_rule`: second refresh with folder `A` raising `PermissionError` → rule of `A` unchanged, `A` in `unreadable`; a new folder raising → absent from `folder_rule`.
  - `test_cli_refuses_a_world_without_acl_access`.
  - in `tests/test_worlds.py`: `test_access_mode_and_admins_are_read_from_the_config` (`[access] admins`, `[worlds.projects] access = "acl"`, a bad value raises).
- [ ] **Step 2: Run** `... -m pytest tests/test_permissions.py tests/test_worlds.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** `permissions.py`, the config fields, the CLI subcommand, the dmx.ps1 changes and `config.example.toml`.
- [ ] **Step 4: Run** the same command — Expected: PASS. Then `powershell -NoProfile -Command "[ScriptBlock]::Create((Get-Content scripts\dmx.ps1 -Raw)) | Out-Null"` — Expected: no parse error.
- [ ] **Step 5: Commit** `"Permissions refresh: access.sqlite3 from the folders' file-server rules"`

### Task 3: Caller in DocTools, server and CLI (`--as-groups`, admins)

**Files:**
- Modify: `src/dmx_docs/tools.py` (`DocTools.__init__`, new `allows`), `src/dmx_docs/server.py` (`build_server`, `serve`), `src/dmx_docs/cli.py` (`--as-groups` on `serve`, `search`, `read`, `status`)
- Test: `tests/test_access_tools.py` (new; shared fixture used by Tasks 4-6)

**Interfaces:**
- Consumes: `access.Caller`, `access.Access`, `access.Unrestricted`, `access.AccessStore`, `Config.access`, `Config.access_path`, `Config.access_admins`.
- Produces:
  - `DocTools.__init__(self, cfg: Config, caller: Caller | None = None)`; `self.access` = `Unrestricted()` when `cfg.access == "world"`, else `Access(AccessStore(cfg.access_path), caller or Caller.from_token(cfg.access_admins))`.
  - `def allows(self, key: str) -> bool` = `self.cfg.allows(key) and self.access.can_read(key)`.
  - `build_server(cfg: Config, caller: Caller | None = None)`, `serve(cfg, caller=None)`.
  - CLI `--as-groups "G1,G2"`: refused (exit 2, `--as-groups is for the admins listed in [access] admins.`) unless `Caller.from_token(cfg.access_admins).admin`; then `Caller.from_groups(...)`.
  - Test fixture `acl_world(tmp_path, make_cfg)` in `tests/test_access_tools.py`: index of projects `THOR` and `ORION`, each with `0_Vente\Offre <P>.pdf` (text contains "prix 100000 CHF") and `3_Mecanique\Plan <P>.pdf` (text contains "préhenseur ventouses"); `access.sqlite3` written with `AccessStore.write` so that `0_Vente` folders allow `SALES` and `3_Mecanique` folders allow `MEC`; returns `(cfg, tools_for)` where `tools_for(*sids, admin=False)` builds `DocTools(cfg, Caller(frozenset(sids) | KEEP_ALWAYS, admin))`. `cfg.access = "acl"`, `embedding_model="hash:256"` as in conftest.

- [ ] **Step 1: Write the failing tests:** `test_docTools_of_a_world_world_reads_everything` (cfg.access "world", any caller), `test_allows_follows_the_folder_rule` (MEC caller: Plan yes, Offre no; admin: both), `test_as_groups_is_refused_for_non_admins` (monkeypatch `Caller.from_token` to a non-admin).
- [ ] **Step 2: Run** `... -m pytest tests/test_access_tools.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** as in Interfaces.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `"DocTools, server and CLI take a caller; --as-groups for admins"`

### Task 4: Search filtered by rule, with a vector mask

**Files:**
- Modify: `src/dmx_docs/search.py` (`VectorIndex`, `Searcher.semantic`, `Searcher.search`), `src/dmx_docs/access.py` (`Access.row_mask`, `Unrestricted.row_mask` returning None), `src/dmx_docs/tools.py` (`search` passes `allowed=self.allows` and `row_mask=self.access.row_mask(...)`)
- Test: `tests/test_access_tools.py`

**Interfaces:**
- Consumes: `DocTools.allows`, `Access.allowed_rules()`, `Access.version`, `AccessData.folder_rule`.
- Produces:
  - `VectorIndex.query(self, qvec, k, mask: np.ndarray | None = None)` — scores only rows where `mask` is True (others `-inf`), top-k among them.
  - `VectorIndex.rule_rows(self, con, folder_rule: dict[str, int], version: str) -> np.ndarray` — int32 rule id per row (`-1` = no rule), built from `vectors.chunk_id -> chunks.doc_id -> docs.folder_key`, cached per `(vec version, access version)`.
  - `Searcher.search(..., allowed=None, row_mask=None)`; `Searcher.semantic(con, query, limit, row_mask=None)`.
  - `Access.row_mask(self, index: VectorIndex, con) -> np.ndarray | None` — `None` for admins and `Unrestricted`; else `np.isin(rule_rows, list(allowed_rules()))`, cached for the last 4 `(versions, allowed set)` keys.
- [ ] **Step 1: Write the failing tests:**
  - `test_search_shows_only_readable_documents`: MEC caller searching "prix" (keyword, semantic, hybrid) gets no `0_Vente` path in the output; SALES caller gets both Offres.
  - `test_semantic_top_k_is_taken_among_allowed_rows`: build 150 documents in a SALES folder and 5 in a MEC folder with similar text; MEC caller `search(mode="semantic", limit=5)` returns 5 MEC hits.
  - `test_world_world_search_is_unchanged` (same output as before the change for `access="world"`).
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement** as in Interfaces; keyword results keep using `allowed=self.allows`.
- [ ] **Step 4: Run** `tests/test_access_tools.py` and `tests/test_index_and_tools.py` — Expected: PASS.
- [ ] **Step 5: Commit** `"Search: hidden documents filtered, semantic top-k among the caller's rows"`

### Task 5: Every document and listing tool uses `self.allows`

**Files:**
- Modify: `src/dmx_docs/tools.py` (all `self.cfg.allows` → `self.allows`; `_resolve`; `find_files`; `list_folder`; `list_projects`; `index_status`; offer tools' `allows=`), `src/dmx_docs/worldmap.py` (`live_overview(con, cfg, allows=None)`), `src/dmx_docs/verified.py` (call sites take an `allows` argument where they now use `cfg.allows`), `src/dmx_docs/pictures.py` only if it runs in the server path
- Test: `tests/test_access_tools.py`

**Interfaces:**
- Consumes: `DocTools.allows`.
- Produces:
  - `_resolve(path)`: when `cfg.allows(key)` but not `self.access.can_read(key)`, the path is treated as a file that does not exist: every tool that receives it (`read_document`, `find_in_document`, `view_page`, `export_image`, `open_document`) returns exactly what it returns today for a non-existent file inside a readable indexed folder. Find that wording per tool and reproduce it (no new message that would distinguish the two cases). Access is decided by the file's folder, so a non-indexed file in a readable folder keeps being read live, as today.
  - `list_folder`: entries (files and subfolders) shown only if `self.allows` holds for the file or for at least one indexed document under the subfolder.
  - `list_projects` and `world_map`'s live overview count only documents for which `self.allows` holds; the world_map overview cache key includes the caller's allowed set.
  - `index_status` adds, for `access = "acl"` worlds, the line `You can read N of M documents of this world.` (M = all docs; admins see N = M) and, when `access.sqlite3` is missing, `Access rules not loaded yet: only admins can read this world.`
- [ ] **Step 1: Write the failing tests:**
  - `test_hidden_path_answers_like_a_missing_one`: for each of `read_document`, `find_in_document`, `view_page`, `export_image`, `open_document` (with `_launch` monkeypatched): output for the hidden `THOR\0_Vente\Offre THOR.pdf` (MEC caller) equals the output for the non-existent `THOR\3_Mecanique\Offre THOR.pdf`, after replacing `0_Vente` by `3_Mecanique` in the first.
  - `test_list_folder_hides_folders_with_only_hidden_documents` (MEC: `THOR` lists `3_Mecanique`, not `0_Vente`).
  - `test_find_files_and_list_projects_count_only_readable_documents`.
  - `test_index_status_says_how_many_documents_the_caller_can_read` (`You can read 2 of 4 documents of this world.`).
  - `test_mapped_drive_path_is_judged_like_its_unc_form` (use `sources.clean_path` monkeypatched mapping as existing drive-letter tests do; same decision both ways).
  - `test_caller_without_groups_sees_nothing_and_no_error` (every tool above returns a string; `You can read 0 of 4`).
  - `test_missing_access_file_hides_everything_but_for_admins`.
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement** as in Interfaces (grep `cfg.allows` in tools.py, worldmap.py, verified.py, offer_tools call sites).
- [ ] **Step 4: Run** `tests/test_access_tools.py tests/test_index_and_tools.py tests/test_worldmap.py tests/test_offer_tools.py tests/test_verified.py` — Expected: PASS.
- [ ] **Step 5: Commit** `"Document and listing tools answer only from the caller's readable documents"`

### Task 6: Project cards filtered per line, register per project

**Files:**
- Modify: `src/dmx_docs/knowledge.py` (new function), `src/dmx_docs/tools.py` (`_written_card`, `catalog`, `project_card`'s register part)
- Test: `tests/test_knowledge.py`, `tests/test_access_tools.py`

**Interfaces:**
- Consumes: `knowledge.SOURCE`, `knowledge.SOURCED_SECTIONS`, `knowledge.Card`, `knowledge.Vocabulary.canonical`, `DocTools.allows`, `sources.clean_path`, `store.path_key`.
- Produces:
  - `def visible_card(card: Card, can_read_source: Callable[[str], bool]) -> tuple[str, int, bool]` in `knowledge.py` → `(body_to_show, hidden_line_count, whole)`. A source with a backslash is a document path (strip the page suffix with the regex used in `check`); any other source (`register`, `user, 2026-...`) is judged by `can_read_source` too (the caller decides). Sourced lines (`- ` lines of sections A/B/C) are kept only if all their sources are readable. `whole` = every source of the card readable. When not `whole`: the Summary section (any `## ` section not in `SOURCED_SECTIONS`) is removed and the text ends with `k lines not shown with your rights.` (only if k > 0).
  - In `DocTools`, the `can_read_source` for a card of project P: document path → `self.allows(path_key)`; non-document source → the caller can read at least one indexed document of P (`doc_facets.project` or `subproject` = P).
  - `_written_card`: header line (status, written, read count) only when `whole`; body from `visible_card`; when nothing is left (no visible line and not whole) → `Card not available with your rights.`
  - `catalog`: for a card that is not `whole`, a header filter value (infeed, outfeed, end_effector, machine, client, country, speed) matches only if one of its vocabulary synonyms (as in `check`) appears in the visible body; `text` matches the visible body only; the line printed for the project shows only those header values backed by the visible body; `years`/`status` filters do not match a card that is not `whole`.
  - Machine register (in `project_card` and the register columns of `list_projects`): shown for project P only if the caller can read at least one indexed document of P.
- [ ] **Step 1: Write the failing tests:**
  - `tests/test_knowledge.py::test_visible_card_keeps_lines_whose_sources_are_all_readable` (3 lines: Vente-only, Mec-only, Vente+Mec; readable = Mec → 1 line kept, hidden 2, whole False, Summary removed, ends with `2 lines not shown with your rights.`).
  - `test_visible_card_whole_when_every_source_is_readable` (body unchanged).
  - `tests/test_access_tools.py::test_project_card_for_a_mechanical_caller_shows_only_mechanical_lines` (card for THOR citing both PDFs; MEC caller sees the Mecanique line, not the Vente line, no header line).
  - `test_catalog_matches_only_on_visible_lines` (infeed value backed only by a Vente line → no match for MEC, match for SALES+MEC).
  - `test_register_shown_only_with_a_readable_document_of_the_project` (caller with no rights on ORION: no register text for ORION).
- [ ] **Step 2: Run** — Expected: FAIL.
- [ ] **Step 3: Implement** as in Interfaces.
- [ ] **Step 4: Run** `tests/test_knowledge.py tests/test_access_tools.py` — Expected: PASS.
- [ ] **Step 5: Commit** `"Knowledge cards filtered per line; machine register per readable project"`

### Task 7: Full suite, rollout notes

**Files:**
- Modify: `README.md` (section "Per-person access": `[access]`, `access = "acl"`, `permissions` command and daily task, `--as-groups`, the local-copy warning from spec §6), `maps/projects.md` only if it states that everything is readable (check)
- Test: whole suite

- [ ] **Step 1: Run** `PYTHONPATH=src C:\dmx-rag\venv\Scripts\python.exe -m pytest -q --ignore=tests/test_dmx_ps1.py -p no:cacheprovider` (≈18 min, run in background) — Expected: all pass except the pre-existing OCR stall test.
- [ ] **Step 2: Laptop check (as the user, admin):** `dmx-docs --config C:\dmx-rag\config.toml --world projects permissions --out <scratch>\access.sqlite3` on a config copy with `access = "acl"`; compare its rule count and folder count with the 2026-11-25 survey (`worlds\projects\acl-survey\acl-projects-20261125-1110.md`: 39,486 folders; rule count lower than 75 because BUILTIN/SYSTEM/local SIDs are dropped). Expected: same folder count minus the 74 deleted folders; report written.
- [ ] **Step 3: `--as-groups` check:** `dmx-docs ... --as-groups DMX_Project_Mec_R_GA search "prix offre"` — Expected: no path containing `\0_Vente\` or `\1_Finances\`.
- [ ] **Step 4: Update README; commit** `"README: per-person access"`.
- [ ] **Step 5:** Hand rollout steps 3-4 of spec §8 to the user (VM run, scheduled task, share restriction): they are not code.

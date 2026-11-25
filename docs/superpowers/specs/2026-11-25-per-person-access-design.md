# Per-person access: each user sees only what the file server lets them read

Date: 2026-11-25. Status: written from the chat of that day; builder check done; waiting for the user's approval.
Builds on the read-only survey `dmx-docs acl-survey` (aclsurvey.py, commit 68fc8aa).

## Intent

Employees will use the worlds through one server on the company VM, which runs under an account
that can read every indexed folder. Without a filter, anyone allowed into the projects world would
read Finances and Vente of every project. The user wants each person to get answers only from the
documents **they could open themselves on the file server**, decided by the existing AD groups,
with no second list of rights to maintain.

The survey (2026-11-25, run as the user) showed:

* documentation, marketing and offers: one reading rule each (offers: two folders add a named
  person; no one in the Clients group is excluded). A world-level group is enough there.
* projects: 75 rules, in practice one AD group per section folder of the current projects
  (`DMX_Project_Vente_R_GA` for `0_Vente`, `_Fin_` for `1_Finances`, `_Elect_`, `_Mec_`, `_Soft_`,
  `_Gest_`, `_Rap_`, `_Photos_`, `_Doc_`, `_SAV_`), one group for all of `2_Hors_Garantie`, and
  about 50 exceptions (moved folders carrying two groups, a named person, deleted accounts).

## Goals

1. Every document carries the reading rule of its folder, copied from the file server's access
   lists (approach A below). Every tool answers a user only from documents whose rule lets them
   read; nothing else of a hidden document shows (no title, path, snippet, page, picture, count).
2. The rules follow the file server: each index run reads the rules of new folders, and a
   **permissions refresh** re-reads every folder's rule once a day without re-indexing
   (≈15 min for the projects world over the network, measured by the survey from the laptop).
3. Things built across documents follow one principle: **a summary is shown only to someone who
   can read all its sources** (details in section 5).
4. The same code works in two modes:
   * **server mode** (later, central server on the VM): the caller's groups come from the
     server's Windows authentication;
   * **local mode** (today, Claude Desktop on a PC): the caller is the Windows user running it.
     Used to test the filter before the server exists, with an admin option to act as given
     groups (section 6).
5. Fail closed: a document whose folder has no known rule (unreadable access list, folder not
   refreshed yet) is hidden from everyone but the admins listed in the config.

## Non-goals

* The central server itself (HTTP transport, authentication, the per-PC connector, the publish
  step): a separate design. This design defines only what the server must hand over: the caller's
  list of group SIDs.
* Files carrying permissions of their own (different from their folder). The survey did not
  measure them yet (`acl-survey --files`, to run on the VM); if they matter, a later version
  reads file-level rules the same way.
* Share-level permissions of the file server (only its administrators can read them).
* Write rights, auditing who asked what (can be added in the server design).
* Telling a user that hidden documents exist for their question (it would leak that a Finances
  document mentions a word). Only world totals are shown (section 5).

## Approaches (chosen: A, with a daily permissions refresh)

* **A. Copy the permissions at indexing (chosen).** Each folder's reading rule is stored in the
  index; at question time the server compares the rule with the caller's groups. Exact (includes
  the exceptions), fast, follows IT changes automatically. Cost: a change takes effect at the
  next refresh (daily, or on demand for an urgent removal).
* B. Live check on the file server per answer (the VM acts as the user). Always current, but
  needs Kerberos delegation (a sensitive setting IT must allow), is slow (hundreds of files tested
  per search), and counts/catalog would still need A. Rejected.
* C. Fixed section-to-group table in the config. Simple, but misses the exceptions and drifts as
  soon as IT changes a folder. Rejected.

## Design

### 1. What a rule is (reuse of aclsurvey.py)

A rule = (set of SIDs allowed to read the files of a folder, set of SIDs denied), as computed by
`aclsurvey.read_windows_acl(folder, for_files=True)`: entries inherited by files, with a read bit
(`FILE_READ_DATA`, `GENERIC_READ`, `GENERIC_ALL`). Write-only entries do not count.

Special SIDs (my call, from the builder check): every SID that means something only on the
machine where it is checked is **dropped**, because on the VM or a PC it would name that machine's
admins, not the file server's: `CREATOR OWNER` (S-1-3-0), `OWNER RIGHTS` (S-1-3-4), all
`BUILTIN\*` (S-1-5-32-*, e.g. Administrators, present in every rule of the survey),
`SYSTEM`/`LOCAL SERVICE`/`NETWORK SERVICE` (S-1-5-18/19/20), and local accounts of the file
server (an `S-1-5-21-*` SID whose domain part is not the AD domain's). Otherwise any employee who
is local admin on their PC would read everything. Nobody loses rights they really have: domain
groups such as `DMX_SiteAdmin_GA` stay ordinary readers, and the app's admins come only from
`[access] admins`. `Everyone` (S-1-1-0) and `Authenticated Users` (S-1-5-11) are kept and match
every caller. Token groups marked "deny only" in the caller's token are not used as allows.

A caller may read a rule when **one of their SIDs is allowed and none is denied**. Windows
evaluates entries in order; "deny always wins" is the same or stricter, never looser.

### 2. Storage (a small separate file per world: access.sqlite3)

My call, from the builder check: the rules do **not** go into `index.sqlite3`. A refresh through
the master would copy ~10 GB twice, hold the world LOCK long, and fail on the VM while the server
has the local index open. Instead each world has `access.sqlite3` (a few MB) next to its index:
master `worlds\<world>\access.sqlite3`, local copy beside the local index. The refresh writes a new
file and swaps it in (`.new` then rename, as the pull does for the index); the server re-opens it
when its `acl_version` changes (checked at most once a minute), so an urgent removal takes effect
within a minute of an on-demand refresh, without restarting the server. Joins with `docs` are made
in Python (folder_key -> rule id dictionary, ~40k entries for projects), not in SQL across files.

```
acl_rules(id INTEGER PRIMARY KEY, hash TEXT UNIQUE, allow TEXT, deny TEXT)  -- JSON lists of SIDs
folder_acl(folder_key TEXT PRIMARY KEY, rule_id INTEGER, read_at REAL, error TEXT)
acl_names(sid TEXT PRIMARY KEY, name TEXT)                                   -- for reports only
```

`docs.folder_key` already exists: a document's rule is `folder_acl.rule_id` of its folder. Rules
are never deleted while a folder uses them; unused rules are removed by the refresh.
Meta `acl_version` changes whenever any folder's rule changes (the server reloads its masks).

### 3. Filling it

* **Index runs do not read rules.** A document in a new folder has no rule until the next
  refresh, so it is hidden from all but the admins (fail closed) for at most a day. Launcher 7
  (`update`) ends with a `permissions` step for the worlds with `access = "acl"`, so a full run
  publishes its new folders at once when it runs on the VM.
* **Permissions refresh**: new command `dmx-docs permissions` (`dmx.ps1 permissions -World X`).
  It does not take the index LOCK (it only reads the index's folder list, from the local copy
  refreshed by the last pull/run) and writes only `access.sqlite3`, master then local. Re-reads the rule of every
  folder holding documents, in parallel (16 threads, as the survey), updates `folder_acl`,
  bumps `acl_version` if anything changed, writes a short change report
  (`worlds\<world>\acl-survey\changes-<date>.md`: folders whose rule changed, documents affected).
  A folder never read successfully has no rule (error stored): its documents are hidden.
  Launcher 7 (`update`) runs it after `index`. A Windows scheduled task on the VM runs it daily
  for the worlds with `access = "acl"`.
* The daily run uses the VM account, which can read every access list. A run under another
  account (e.g. the user's, from launcher 7 on another machine) is allowed: folders whose list it
  cannot read keep their previous rule (they are not cleared), and the report names them. (In the
  2026-11-25 survey, the 74 + 10 unreadable folders were all deleted folders, "path not found",
  not access refusals.)

### 4. Applying it in the tools

A `Caller` object (SIDs, `admin` flag) is passed to `DocTools`; from it a cached set
`allowed_rules` (rule ids the caller may read) is computed once per caller and `acl_version`.

* **Keyword search (FTS) and every SQL path** (`search`, `find_files`, `list_folder`,
  `list_projects`, `read_document`, `find_in_document`, `view_page`, `export_image`,
  `open_document`, `catalog`, `project_card`, `world_map`, `index_status`): one shared clause
  `docs.folder_key IN (SELECT folder_key FROM folder_acl WHERE rule_id IN (<allowed>))`, applied
  where these tools already apply `cfg.allows` (excluded folders). A path the caller may not read
  answers exactly like a path that is not in the index ("not found"), so its existence is not
  revealed.
* **Semantic search**: `VectorIndex` keeps, beside `ids`, an array `rule_of_row` (rule id of each
  vector's document, built from `vectors -> chunks -> docs -> folder_acl`, rebuilt when
  `vec_version` or `acl_version` changes). The query computes scores only where
  `np.isin(rule_of_row, allowed)` (mask cached per allowed-set). Top-k is taken among allowed rows
  only, so a restricted user still gets k results.
* **Hybrid fusion, similarity labels, OCR passages, picture descriptions**: all are chunks of a
  document and follow it.
* **Offers register tools** (offers world): the register rows carry their source document; rows
  whose sources the caller cannot read are left out and counted in the coverage line as "not
  readable for you" only when the world has `access = "acl"` (the offers world stays at world
  level for now, see section 7).

### 5. Things built across documents

| Item | Rule |
|---|---|
| Project knowledge card (`project_card`, `catalog`) | **filtered per line** (user's decision, 2026-11-25): each cited line of the card (sections A/B/C) is shown only if the caller can read every source that line cites. The Summary paragraph and the header fields (no citations of their own) are shown only if the caller can read every source of the whole card. A card with lines left out ends with "k lines not shown with your rights". `catalog` filters on the visible lines only, so a project matches only through what the caller may see. |
| Card citations that are not documents (`[register]`, `[user, date]` corrections) | readable when the caller can read at least one document of the project (same rule as the machine register) |
| Machine register (from the installed-base Excel, no file rights) | shown for a project when the caller can read **at least one document of that project** |
| World map (`maps\<world>.md`, hand written) | shown to everyone allowed into the world; the live overview part is computed on the caller's documents only |
| Counts (`index_status`, `list_projects`, `world_map` overview, coverage lines) | computed on the caller's documents; `index_status` adds one line "you can read N of M documents of this world" |
| Thesaurus, vocabulary, skill | general, shown to all |

### 6. Who the caller is

* **Server mode** (future server design): the server authenticates the Windows user and hands a
  `Caller(sids=<the user's SID + all group SIDs from their logon token>)`. The token already
  includes nested groups.
* **Local mode** (`serve` over stdio, today): the caller is the process's own Windows token
  (`GetTokenInformation(TokenGroups)`), so an employee running a local copy would see only what
  they can read. **This is not a protection**: whoever has a local copy can open the SQLite file
  directly. Local mode is for testing; the projects master on the shared folder must be readable
  only by the admins and the VM account once employees use the server (section 8).
* **Admin test option**: `--as-groups "DMX_Project_Mec_R_GA,DMX_Project_Doc_R_GA"` (CLI `search`,
  `read`, `status`, and `serve` for Claude Desktop) replaces the caller's SIDs by those groups
  (names resolved to SIDs), so the user can check what a mechanical engineer would see.
  Allowed only for SIDs listed as admins in the config.
* **Admins** (`[access] admins = ["ROTZINGERAG\\Johan.Monard", ...]`): see every document,
  including those without a known rule.

### 7. Configuration

```toml
[access]
admins = ["ROTZINGERAG\\<admin account>"]   # see everything, may use --as-groups

[worlds.projects]
access = "acl"        # per-document rules from the file server
```

Default `access = "world"`: no per-document filter (today's behaviour); who may use the world is
decided by the world-level group in the server design. Projects starts with `"acl"`; any world can
switch later (switching on fills `folder_acl` at the next index or permissions run; until then
the world is admin-only, by the fail-closed rule).

### 8. Rollout order

1. Code + tests; `permissions` run on the laptop for projects (as the user) and compare with the
   survey.
2. Local test in Claude Desktop with `--as-groups` (e.g. only `DMX_Project_Mec_R_GA`): searches
   on prices and Finances must return nothing from `0_Vente`/`1_Finances`; cards hidden as
   specified.
3. On the VM, under its account: `acl-survey --files` (measure file-level exceptions) and the first
   `permissions` run; daily scheduled task.
4. With the central server: restrict `worlds\projects` on the shared folder to admins + VM account.

## Tests (to the builder)

Fake access lists (as in tests/test_aclsurvey.py) and a small indexed corpus with two projects and
sections readable by different fake groups: each tool, called as each group, shows exactly the
expected documents; a hidden path answers like a missing one; semantic top-k returns k allowed
hits; cards follow the "all sources" rule; a folder without a rule is hidden except for admins;
a refresh that changes a rule changes the answers without re-indexing; `access = "world"` worlds
behave exactly as today (the existing suite passes unchanged).

## Left to the builder

* Exact placement of the shared filter clause in tools.py (follow the `cfg.allows` call sites).
* Mask caching details in VectorIndex (memory: one bool array per distinct allowed-set, LRU of a
  few).
* Wording of the "not available with your rights" lines.
* Scheduled task creation script on the VM (or a launcher), consistent with launcher 7.

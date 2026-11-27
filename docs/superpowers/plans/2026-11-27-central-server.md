# Central Server Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** One `dmx-docs serve-all` process on the VM serves every world over HTTP to colleagues'
Claude Desktop. Each person reaches it through a small PowerShell relay extension, signs in with NTLM, and
is answered with their own AD rights.

**Architecture:**
- Each world keeps its `build_server` MCP server. It is mounted at `/<world>/mcp` in one
  Starlette/uvicorn app (stateless streamable HTTP, JSON responses).
- An NTLM middleware turns each request's Windows login into a `Caller`. The tools read that caller per
  request.
- One embedding model is shared by all worlds. Large worlds search a faiss IVF fp16 index.
- On each PC, a PowerShell relay packaged as an MCPB extension pipes Claude Desktop's stdio to the VM. It
  carries out `open_document` / `export_image` locally.

**Tech Stack:**
- Python 3.12, mcp 2.3 (`MCPServer`), Starlette/uvicorn (present), pywin32 SSPI (present),
  faiss-cpu ≥ 1.16 (new);
- Windows PowerShell 5.1;
- MCPB packaging.

**Spec:** `docs/superpowers/specs/2026-11-27-central-server-design.md`

## Global Constraints

- **Fail closed.**
  - A request without a completed NTLM login gets HTTP 401 and no tool runs.
  - A caller with no access data reads nothing.
- **`serve-all` only serves worlds with `access = "acl"`.** It refuses to start a world with
  `access = "world"` and names it.
- **Sign-in is NTLM only.**
  - The server sends `WWW-Authenticate: NTLM`, never `Negotiate`.
  - The relay forces NTLM through a `System.Net.CredentialCache` entry of type `"NTLM"` with
    `[System.Net.CredentialCache]::DefaultNetworkCredentials`.
- **Tool names, parameters and texts stay identical** to `build_server` today, except the central
  `open_document` / `export_image` results that the relay rewrites (Task 6).
- **Vector index:**
  - `faiss.IndexIVFScalarQuantizer`, 2048 lists, `QT_fp16`, `METRIC_INNER_PRODUCT`;
  - `nprobe` 64;
  - used for worlds with ≥ 200,000 vectors; smaller worlds keep the exact search.
  - Settings: `[search] ivf_min_rows`, `ivf_lists`, `ivf_nprobe`.
- **Server defaults:**
  - port 8765;
  - 4 concurrent tool calls; a call waiting more than 30 s gets "The server is busy, try again in a
    minute.";
  - caller cache 300 s.
  - Settings: `[central] port`, `workers`, `busy_seconds`, `caller_cache_seconds`.
- **Logs:** world, tool, caller label, duration. Never the question or the arguments.
- **Admin endpoints** (`/_admin/...`) are accepted only from 127.0.0.1 / ::1.
- **The relay runs on stock Windows PowerShell 5.1**, with no modules to install and UTF-8 in both
  directions.
- **Test commands:**
  - Python tests: `PYTHONPATH=src C:/dmx-rag/venv/Scripts/python.exe -m pytest <files> -q -p no:cacheprovider`.
  - Write Windows paths with the Edit/Write tools, not heredocs.
  - Commit messages end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. **Requests without a completed login get 401 and never reach a tool.** This covers a missing header,
   a garbage token, and a Type-3 message sent on another connection than its Type-1. Task 4 owns it.
2. **Interleaved requests from two users each get their own results.** No cached `Access`, overview or
   "To verify" list crosses between them. Task 3 owns it.
3. **A world paused during a call:** the running call finishes, new calls get the "being updated"
   message, and the other worlds keep answering. Task 7 owns it.
4. **French text round-trips through the relay unchanged** (é, à, ç, «»), in both directions. Task 8
   owns it.
5. **A faiss index file left from an older vector version** is ignored and replaced. A caller with zero
   readable rows gets an empty semantic result, not an error. Task 2 owns it.

---

### Task 1: One embedding model per process

**Files:**
- Modify: `src/dmx_docs/embeddings.py` (`Embedder.__init__`)
- Test: `tests/test_embeddings_shared.py`

**Interfaces:**
- Produces:
  - `embeddings.shared_model(key: tuple, factory: Callable[[], object]) -> object`, a process-wide cache
    guarded by a lock;
  - `Embedder` takes its model through it, keyed by
    `(cfg.embedding_model, device, str(cfg.models_dir), cfg.embed_threads)`.
- Prefixes (`query_prefix`, `passage_prefix`) stay per `Embedder`.

- [ ] **Step 1: Write the failing tests**

```python
def test_two_worlds_share_one_model(make_cfg):
    a, b = make_cfg(embedding_model="hash:64"), make_cfg(embedding_model="hash:64")
    assert Embedder(a)._model is Embedder(b)._model

def test_a_different_model_is_loaded_separately(make_cfg):
    assert Embedder(make_cfg(embedding_model="hash:64"))._model is not Embedder(make_cfg(embedding_model="hash:32"))._model
```

(Use whatever `make_cfg` override mechanism conftest offers. If it takes no keyword arguments, build the
Config with `dataclasses.replace`.)

- [ ] **Step 2: Run them, expect FAIL.**
- [ ] **Step 3: Implement `shared_model`, and route both the CPU and the GPU model creation and the
  `HashEmbedder` through it.**
- [ ] **Step 4: Run `tests/test_embeddings_shared.py` and `tests/test_search*.py`. Expect PASS.**
- [ ] **Step 5: Commit** "Embeddings: one model per process, shared by the worlds".

### Task 2: faiss IVF index for large worlds

**Files:**
- Modify: `pyproject.toml` (add `"faiss-cpu>=1.16"`)
- Modify: `src/dmx_docs/config.py` (`ivf_min_rows=200_000`, `ivf_lists=2048`, `ivf_nprobe=64` under
  `[search]`)
- Modify: `src/dmx_docs/search.py` (`VectorIndex`)
- Test: `tests/test_vector_ivf.py`

**Interfaces:**
- Produces:
  - `VectorIndex.query(qvec, k, mask)` keeps its signature and results contract.
  - `VectorIndex.uses_ivf: bool`.
  - Cache file `vec_ivf.<safe version>.faiss` next to `vec_mat.*.npy`, removed by
    `_cleanup_old_caches` like the others.
- An IVF world keeps `self.mat` as the float16 memory map, never converted to float32. `similarity()`
  reads its rows from it.

- [ ] **Step 0: Install faiss-cpu into the test venv:**
  `C:/dmx-rag/venv/Scripts/python.exe -m pip install "faiss-cpu>=1.16"`, or `uv pip install` if the venv
  has no pip. Check it with `python -c "import faiss"`.
- [ ] **Step 1: Write the failing tests.** Use a synthetic world: 3,000 normalized random vectors in a
  real index built through the existing test helpers, with `ivf_min_rows=1000`, `ivf_lists=8` and
  `ivf_nprobe=8`. With `nprobe` equal to the list count the search is exact.

```python
def test_ivf_search_matches_exact_when_every_list_is_searched(ivf_world): ...
    # top-10 chunk ids of query() equal the float32 brute-force top-10 for 5 queries
def test_ivf_respects_the_row_mask(ivf_world): ...
    # mask with every other row False: no returned id maps to a False row; k results still returned
def test_ivf_with_an_empty_mask_returns_nothing(ivf_world): ...
    # mask all False -> []
def test_ivf_index_file_is_reused_then_replaced_on_a_new_vector_version(ivf_world): ...
    # second VectorIndex.ensure: same file mtime; after vec_version changes: new file, old one removed
def test_small_world_keeps_exact_search(make_cfg): ...
    # below ivf_min_rows: uses_ivf is False and no vec_ivf file is written
```

- [ ] **Step 2: Run them, expect FAIL.**
- [ ] **Step 3: Implement.**
  - In `ensure()`: when `len(ids) >= cfg.ivf_min_rows`, load `vec_ivf.<ver>.faiss` if it exists.
    Otherwise train on a seeded sample of at most 100,000 rows (float32), add the rows in blocks of
    200,000, and write the file to `.tmp` before `os.replace`.
  - Skip the float32 conversion for IVF worlds.
  - `query()`:
    - pass the mask as `faiss.IDSelectorBitmap(np.packbits(mask, bitorder="little"))` in
      `faiss.SearchParametersIVF(sel=..., nprobe=cfg.ivf_nprobe)`;
    - map faiss row numbers to `self.ids`;
    - drop `-1` results.
- [ ] **Step 4: Run `tests/test_vector_ivf.py tests/test_access_tools.py tests/test_search*.py`. Expect
  PASS.**
- [ ] **Step 5: Commit** "Search: faiss IVF fp16 index for worlds of 200,000+ vectors".

### Task 3: Per-request caller in DocTools

**Files:**
- Modify: `src/dmx_docs/access.py` (add `CURRENT_CALLER`, `AccessPool`, `Caller.from_token_handle`)
- Modify: `src/dmx_docs/tools.py` (`DocTools.access` becomes a property; `_listed` per caller;
  `offer_verify` user)
- Test: `tests/test_access_per_request.py`

**Interfaces:**
- Produces:
  - `access.CURRENT_CALLER: contextvars.ContextVar[Caller | None]` (default `None`);
  - `access.AccessPool(store: AccessStore, cache_seconds: float = 300, clock=time.monotonic)` with
    `.for_caller(caller: Caller) -> Access`, cached by `(caller.sids, caller.admin)`, at most 64
    entries;
  - `Caller.from_token_handle(token, admins: list[str]) -> Caller`. `from_token` becomes a call to it
    with the process token.
- `DocTools.access`:
  - when `CURRENT_CALLER.get()` is set: `pool.for_caller(that caller)`;
  - otherwise: the fixed `Access` built as today (local mode).
  - Worlds with `access = "world"` still return `Unrestricted()`.
- `DocTools._listed` becomes per caller, keyed by `caller.sids`.
- `offer_verify` records `caller.label` when a request caller is set, else `USERNAME` (as today).

- [ ] **Step 1: Write the failing tests.** Reuse the fixtures of `tests/test_access_tools.py`: world,
  rules, two `Caller`s (SALES, ENG).

```python
def test_one_doctools_answers_each_request_with_its_caller(acl_world): ...
    # tools = DocTools(cfg, caller=None-not-used); with CURRENT_CALLER set to SALES -> search sees only A docs;
    # set to ENG in the same thread afterwards -> only B docs
def test_interleaved_callers_in_threads_do_not_mix(acl_world): ...
    # 2 threads x 20 alternating searches via contextvars.copy_context().run: every result set matches its caller
def test_to_verify_list_is_per_caller(offers_acl_world): ...
    # _listed entries made under SALES are not seen under ENG
def test_offer_verify_records_the_request_caller(offers_acl_world): ...
def test_local_mode_without_a_request_caller_is_unchanged(acl_world): ...
```

- [ ] **Step 2: Run them, expect FAIL.**
- [ ] **Step 3: Implement as in Interfaces.** Every cache already keyed by the allowed set (world_map
  overview, row masks) stays keyed that way. Check there is no other per-instance cache of
  caller-dependent results in `tools.py`.
- [ ] **Step 4: Run `tests/test_access_per_request.py tests/test_access_tools.py tests/test_access.py
  tests/test_verified.py`. Expect PASS.**
- [ ] **Step 5: Commit** "Access: caller per request (CURRENT_CALLER, AccessPool)".

### Task 4: NTLM sign-in middleware

**Files:**
- Create: `src/dmx_docs/ntlm.py`
- Test: `tests/test_ntlm.py` (skipped unless `sys.platform == "win32"`)

**Interfaces:**
- Produces:
  - `ntlm.NtlmAuth(app, make_caller: Callable[[object], Caller], cache_seconds: float = 300)`, an ASGI
    middleware.
- For every HTTP request whose path is not `/_admin/...`:
  - no `Authorization: NTLM` header → 401 with `WWW-Authenticate: NTLM`;
  - Type-1 → 401 with `WWW-Authenticate: NTLM <challenge>`. The pending `sspi.ServerAuth("NTLM")`
    context is kept per `(client host, client port)` for at most 30 s;
  - Type-3 → the context completes; `QuerySecurityContextToken` (or the pywin32 equivalent) gives the
    token, and `make_caller(token)` the caller. It is stored in `scope["state"]["dmx_caller"]` and the
    app is called;
  - any failure → 401.
- Callers are cached by user SID for `cache_seconds` (so are their group lookups). A pending context is
  never matched to a different connection.

- [ ] **Step 1: Write the failing tests.** Wrap a tiny ASGI app that echoes
  `scope["state"]["dmx_caller"].label`, and drive it with `httpx.AsyncClient`/`starlette.testclient`.
  Client side: `sspi.ClientAuth("NTLM")` in the test process, so it is the test machine's own login.
  Use a fixed client address per simulated connection.

```python
def test_no_header_gets_401_ntlm(): ...           # WWW-Authenticate == "NTLM", app not called
def test_full_handshake_reaches_the_app_as_me(): ... # label == current DOMAIN\user, from_token_handle used
def test_garbage_token_gets_401(): ...
def test_type3_on_another_connection_gets_401(): ...  # Type-1 from port 1111, Type-3 from port 2222
def test_admin_paths_bypass_ntlm(): ...           # /_admin/x reaches the app without a caller
```

- [ ] **Step 2: Run them, expect FAIL.**
- [ ] **Step 3: Implement `NtlmAuth`.** `make_caller` will be
  `lambda tok: Caller.from_token_handle(tok, cfg.access_admins)` from Task 3.
- [ ] **Step 4: Run `tests/test_ntlm.py`. Expect PASS.**
- [ ] **Step 5: Commit** "Central server: NTLM sign-in middleware".

### Task 5: `serve-all`: every world in one HTTP process

**Files:**
- Create: `src/dmx_docs/central.py`
- Modify: `src/dmx_docs/cli.py` (sub-command `serve-all`)
- Modify: `src/dmx_docs/config.py` (`[central]` settings: `port=8765`, `workers=4`, `busy_seconds=30`,
  `caller_cache_seconds=300`)
- Modify: `src/dmx_docs/server.py` (`build_server(cfg, caller=None, central: bool = False)`; tools read
  the request caller)
- Test: `tests/test_central.py`

**Interfaces:**
- Consumes:
  - `NtlmAuth` (Task 4);
  - `CURRENT_CALLER` / `AccessPool` (Task 3);
  - the shared model (Task 1).
- Produces:
  - `central.build_app(config_path: Path, worlds: list[str] | None = None, auth=True) -> Starlette`:
    - mounts each world's `build_server(cfg, central=True).streamable_http_app(json_response=True,
      stateless_http=True)` at `/<world>`, with one combined lifespan running every session manager;
    - adds `GET /_whoami`, which answers `{"caller": label, "admin": bool, "groups": n}` (behind the
      NTLM layer);
    - wraps everything in `NtlmAuth` when `auth`. `auth=False` is used only by tests, which then set
      `scope["state"]["dmx_caller"]` themselves.
  - `cli serve-all [--host 0.0.0.0] [--port N] [--worlds a,b]` runs uvicorn. When `[central] certfile`
    and `keyfile` are set, it passes them as `ssl_certfile` / `ssl_keyfile` and serves HTTPS. Otherwise it
    serves HTTP and logs one warning line at start: "HTTP without a certificate: logins cross the LAN
    unencrypted."
- In central mode, each tool:
  - reads the caller from the MCP request context (`ctx.request_context.request.state.dmx_caller`) and
    runs under `CURRENT_CALLER` set to it;
  - with no caller, raises "Not signed in.";
  - runs under an `anyio.CapacityLimiter(workers)`. Waiting longer than `busy_seconds` raises
    `"The server is busy, try again in a minute."`.
- At start-up:
  - a world whose `access != "acl"` is refused (the process exits with a message naming it);
  - before loading a world's vectors, if the vector files' size plus 1 GB exceeds the free physical
    memory (`GlobalMemoryStatusEx`), that world is skipped, logged, and listed by `/_whoami` and
    `index_status` as "not served: not enough memory".

- [ ] **Step 1: Write the failing tests.** Use `auth=False`, a test middleware injecting a `Caller`, two
  small acl worlds and the MCP JSON-RPC over `starlette.testclient`.

```python
def test_each_world_answers_at_its_own_path(two_worlds): ...      # tools/list on /projects/mcp and /marketing/mcp
def test_a_search_is_answered_with_the_injected_caller(two_worlds): ...  # SALES sees A only, ENG sees B only
def test_no_caller_is_refused(two_worlds): ...                     # tool call error "Not signed in."
def test_a_world_without_acl_is_refused_at_start(tmp_path): ...    # build_app raises naming the world
def test_busy_when_all_workers_are_taken(two_worlds, monkeypatch): ...  # workers=1, busy_seconds=0.2, one blocked call
def test_whoami_reports_the_caller(two_worlds): ...
def test_log_line_has_world_tool_caller_and_no_arguments(two_worlds, caplog): ...
```

- [ ] **Step 2: Run them, expect FAIL.**
- [ ] **Step 3: Implement as in Interfaces.**
  - Wrap the tool functions once in `build_server` (one decorator applied to each, when `central`).
    Do not edit the ~20 tool bodies.
  - The wrapper's `ctx` parameter must not appear in the tool schemas. Check with
    `test_tool_schemas_are_unchanged_in_central_mode`, which compares `tools/list` between local and
    central.
- [ ] **Step 4: Run `tests/test_central.py tests/test_server*.py tests/test_skill.py`. Expect PASS.**
- [ ] **Step 5: Commit** "Central server: serve-all, one HTTP process for every world".

### Task 6: `open_document` and `export_image` carried out by the relay

**Files:**
- Modify: `src/dmx_docs/tools.py`:
  - split `export_image` into `export_image_data(...) -> tuple[bytes, str, str, str]`, returning
    `(data, out_ext, default_name, what)`, and the local save;
  - add `open_document_target(path, page) -> tuple[str, str, int | None]`, returning
    `(path, ext, page)` after the same checks.
- Modify: `src/dmx_docs/server.py` (central versions of the two tools)
- Test: `tests/test_central_relay_actions.py`

**Interfaces:**
- Produces the relay marker. The tool's text result starts with `DMX-RELAY ` followed by one JSON object
  on the same line:
  - export: `{"action": "save", "file": "<name><ext>", "what": "<what>", "source": "<path>",
    "data": "<base64>"}`;
  - open: `{"action": "open", "path": "<path>", "ext": "<.pdf|.docx|...>", "page": <int|null>}`.
- The local (non-central) tools are unchanged.
- The `name` argument still sets `file`, through `_safe_name`.
- All access checks (`_resolve`, `_is_file`) run on the VM before any marker is produced.

- [ ] **Step 1: Write the failing tests.**

```python
def test_central_export_returns_a_save_marker_with_the_bytes(...): ...  # base64 decodes to the rendered PNG/JPG
def test_central_open_returns_an_open_marker(...): ...
def test_central_marker_for_an_unreadable_document_is_the_missing_file_error(...): ...
def test_local_export_still_writes_the_file(...): ...
```

- [ ] **Step 2: Run them, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run `tests/test_central_relay_actions.py tests/test_view_export*.py tests/test_open*.py`
  (whichever exist). Expect PASS.**
- [ ] **Step 5: Commit** "Central server: open_document and export_image handed to the relay".

### Task 7: Pause and reopen one world during its update

**Files:**
- Modify: `src/dmx_docs/central.py`:
  - `POST /_admin/<world>/pause` and `POST /_admin/<world>/resume`, loopback only (403 otherwise);
  - pause waits at most 120 s for running calls of that world, then releases its files: drops the
    `VectorIndex` arrays and the faiss index, closes nothing else (connections are per call);
  - it answers `{"paused": true, "running": n}`.
- Modify: `src/dmx_docs/search.py` (`VectorIndex.release()`)
- Modify: `scripts/dmx.ps1`:
  - `Stop-LocalServers` must not match `serve-all`;
  - `pull`: when `[central] port` is set in the config and `http://127.0.0.1:<port>/_admin/<world>/pause`
    answers, pause before `Copy-MasterToLocal` and resume after the warm-up, also on failure (in
    `finally`).
- Test: `tests/test_central.py` (pause cases), `tests/test_dmx_ps1.py` (pull with a stub admin server)

**Interfaces:**
- A paused world's tools raise `"This world is being updated, try again in a few minutes."`.

- [ ] **Step 1: Write the failing tests.**

```python
def test_pause_lets_the_running_call_finish_then_refuses_new_ones(two_worlds): ...
def test_other_worlds_answer_while_one_is_paused(two_worlds): ...
def test_resume_reloads_the_world(two_worlds): ...
def test_admin_from_another_address_is_refused(two_worlds): ...      # client ("10.0.0.5", ...) -> 403
# test_dmx_ps1.py:
def test_pull_pauses_and_resumes_the_central_world(ready): ...       # stub HTTP server records pause, resume in order
def test_pull_resumes_even_when_the_copy_fails(ready): ...
def test_stop_local_servers_leaves_serve_all_running(ready): ...     # a fake python 'serve-all' process survives
```

- [ ] **Step 2: Run them, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run `tests/test_central.py` and `tests/test_dmx_ps1.py -k "pull or central"`. Expect PASS.**
- [ ] **Step 5: Commit** "Central server: a world pauses for its nightly update, the others keep answering".

### Task 8: The relay extension

**Files:**
- Create: `relay/dmx-relay.ps1`
- Create: `relay/manifest.template.json`
- Create: `scripts/build_extensions.py`, writing `dist/dmx-<world>.mcpb`: a zip with forward-slash entry
  names, built with Python `zipfile`.
- Test: `tests/test_relay.py` (Windows only)

**Interfaces:**
- `dmx-relay.ps1 -Url <http(s)://host:port/<world>/mcp> [-ExportDir C:\dmx-rag\exports] [-OpenWith <cmd>] [-WhoAmI]`
- Main loop:
  - reads one JSON-RPC message per stdin line (UTF-8);
  - POSTs it with `Content-Type: application/json`, `Accept: application/json, text/event-stream`, and
    NTLM forced (Global Constraints);
  - writes the response body as one stdout line (UTF-8, no BOM);
  - for a notification (no `id`, HTTP 202), writes nothing.
- Failures become JSON-RPC errors with the request's `id` and these plain messages:
  - unreachable: "The dmx server (<host>) cannot be reached. Check the network or ask IT.";
  - 401: "Your Windows login was not accepted by the dmx server.";
  - other HTTP errors: "dmx server error <code>.".
- A tool result whose first text starts with `DMX-RELAY `:
  - `save`: written to `ExportDir`, with the same unique-name rule as `export_image` today (`_2`,
    `_3`...). The text is replaced with the local `export_image` sentence (same wording as
    `tools.export_image`).
  - `open`: opened with the same rules as `tools._open_file`:
    - Acrobat `/A page=N`;
    - Word `OpenAsReadOnly`;
    - otherwise the default application, with the page hint.

    The text is replaced with `Opened <path> <how>.`. `-OpenWith` replaces the launcher in tests.
- `-WhoAmI`: one `GET` of `/_whoami` on the same host, printed.
- The manifest:
  - names the extension `dmx-<world>`;
  - its server is `powershell.exe -NoProfile -ExecutionPolicy Bypass -File ${__dirname}/server/dmx-relay.ps1 -Url ${user_config.server_url}/<world>/mcp`;
  - `user_config.server_url` defaults to `http://dmx-ws-vm01:8765`.
  - Check the current MCPB manifest version and field names against the MCPB repository before writing
    it.

- [ ] **Step 1: Write the failing tests.** A stub HTTP server runs in a thread (`http.server`, no
  auth), with the relay started through `subprocess` with pipes.

```python
def test_request_and_response_pass_through_unchanged(stub): ...
def test_french_text_round_trips(stub): ...                 # "préhenseur à ventouses « ç »" both ways
def test_notification_writes_nothing(stub): ...
def test_unreachable_server_gives_a_jsonrpc_error_with_the_id(): ...
def test_401_gives_the_login_message(stub_401): ...
def test_save_marker_writes_the_file_and_rewrites_the_text(stub, tmp_path): ...
def test_open_marker_calls_the_launcher(stub, tmp_path): ...  # -OpenWith a script that records its arguments
def test_build_extensions_writes_one_mcpb_per_world(tmp_path): ...  # zip has manifest.json + server/dmx-relay.ps1, forward slashes
```

- [ ] **Step 2: Run them, expect FAIL.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run `tests/test_relay.py`. Expect PASS.**
- [ ] **Step 5: Commit** "Relay: PowerShell MCPB extension to the central server".

### Task 9: Running it on the VM

**Files:**
- Modify: `scripts/dmx.ps1`:
  - command `serve-all`: `Assert-Env`, then `python -m dmx_docs.cli --config $LocalConfig serve-all`,
    with `DMX_DOCS_DATA_DIR` set as for the other commands;
  - command `check-ntlm`: runs the relay with `-WhoAmI` against `http://<this machine>:<port>`.
- Create: `scripts/launchers/9 - Central server.cmd` (like launcher 8)
- Modify: `README.md`, new section "Central server (VM)", in this order:
  1. machine environment variable `DMX_RAG_LOCAL=J:\chatwithdmx`;
  2. a scheduled task "at startup" running launcher 9 as ROTZINGERAG\dmx.datahub;
  3. firewall rule
     `New-NetFirewallRule -DisplayName "dmx central" -Direction Inbound -Protocol TCP -LocalPort 8765 -Action Allow -Profile Domain`;
  4. nightly pull per world (launcher 5 unattended) and permissions (launcher 8);
  5. the five-minute NTLM test: from a PC run `dmx-relay.ps1 -WhoAmI -Url http://dmx-ws-vm01:8765/projects/mcp`,
     where the expected output is your `ROTZINGERAG\name` and a group count;
  6. building and installing the extensions (`python scripts/build_extensions.py`).
- Test: `tests/test_dmx_ps1.py`

- [ ] **Step 1: Write the failing test.** `test_serve_all_runs_the_cli_with_the_local_config(ready)`
  uses the stub CLI, which logs `serve-all`. The argv contains `--config <local>\config.toml` and
  `serve-all`.
- [ ] **Step 2: Run it, expect FAIL.**
- [ ] **Step 3: Implement the two commands, the launcher and the README section.**
- [ ] **Step 4: Run `tests/test_dmx_ps1.py`. Expect PASS.** Then run the full suite.
- [ ] **Step 5: Commit** "Central server: dmx.ps1 serve-all, launcher 9, README for the VM".

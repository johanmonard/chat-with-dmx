# Central server on the VM, reached through a Claude Desktop extension

Status: draft for approval, 2026-11-27.

## Intent

Colleagues use the dmx worlds (projects, offers, marketing, documentation, engineering) from Claude Desktop
on their own Windows PCs (Claude Team plan, like Johan). Each person reads only what their AD rights let
them read on the file server. That rule is implemented today: access.sqlite3, Caller, readable_docs.

Today every PC runs its own servers (`dmx-docs --world X serve`, one per world, started by Claude Desktop)
on its own copy of every index. That has two problems:
- the person's PC holds every document's text, so the filtering protects nothing;
- each PC needs about 21 GB and a copy every night.

The central server fixes both. Indexes and text stay on the VM dmx-ws-vm01. Each PC only runs a small relay.

## Goals

- One server process on the VM serves every world to every colleague at the same time.
- Each request is answered as the person who asked. Identity is proven by their Windows login (Kerberos,
  or NTLM as a fallback), never by a name the PC states.
- Fast answers: a search on the VM about as fast as on Johan's laptop today, about 1-2 s.
- Fits the VM: 16 GB RAM (IT target; 8 GB today) and 50 GB on Z: (J:) for the working copies.
- The tools, their names and the skill (dmx-research) stay the same. Each world keeps its own connector
  name (dmx-projects, dmx-offers, ...).
- Updating one world at night does not stop the others.

## Non-goals

- No internet exposure. The shared-connector route (Anthropic's cloud plus OAuth with Entra ID) was not
  chosen.
- No web interface for colleagues. Johan's configuration page stays as it is.
- No change to indexing. WS032 keeps indexing, and the master stays on Y:
  (`\\DMX-FS01\RAG_ClaudeAI`, option 1 of 2026-11-27).

## Anti-goals (failures even if it works)

- **The server answers someone it could not identify.** No identity means no documents, never "everything".
- **One person's results or cached state reaches another person.**
- **Document text reaches a PC other than in answers to that person's own questions.**
- **A colleague has to install Python or edit JSON files.**

## Constraints

- The VM has 4 vCPU (old Xeon E5-2630 v4), 8 GB RAM today (16 GB requested), C: full, and J: 50 GB.
  Its local working folder is set with `DMX_RAG_LOCAL=J:\chatwithdmx`, done on 2026-11-27 (commit 6a434f7).
- SQLite must stay on a local disk. Measured: searching directly on Y: takes 9-45 s, against 1.8 s locally.
- The service account dmx.datahub reads every AD group and is the only account allowed on Y:.
- The PCs have Windows PowerShell 5.1 and .NET Framework. Python is not assumed.
- MCP SDK 2.3 (MCPServer) is already used. Its streamable HTTP transport must be used in JSON response
  mode, with no long-lived stream, so a simple relay is enough.

## Approaches considered

1. **Shared connector through the Team organisation.** The best experience: nothing to install.
   - Anthropic's cloud connects to the server, so the VM must be reachable from the internet on port 443,
     with OAuth sign-in through the company's Entra ID.
   - Rejected by Johan: it opens the VM to the internet.
2. **Claude Desktop extension on each PC, relaying to the VM over the company network. Chosen.**
   - Nothing is open to the internet. Identity comes from the Windows login.
   - Costs: something to install on each PC (IT can deploy it), and it works in Claude Desktop only.
3. **Keep the local servers on each PC.** Rejected: the documents would stay on every PC, so the
   per-person access protects nothing.

## Design

### On the VM: one server for all worlds

A new command, `dmx-docs serve-all --http <address:port>`, run under dmx.datahub as a service that starts
with the VM.

- **One process for every world** listed in the config, each world at its own address:
  `/projects/mcp`, `/offers/mcp`, ... Each world keeps its own server name, instructions and tools, the
  same as `build_server` today.
- **One embedding model in memory**, shared by every world. Today each world process loads its own
  (2.2 GB each).
- **One set of vectors and one search engine per world**, shared by all callers.
- **Per-request identity.** The HTTP layer runs the Windows Negotiate handshake: SSPI through pywin32,
  already a dependency. From the security context it gets the caller's token. A `Caller` is built from
  that token exactly as `Caller.from_token` builds one from the process token:
  - the token's own groups, which include nested AD groups;
  - `usable_sids`;
  - admin by `access_admins`.

  With no valid identity, the reply is HTTP 401 and no tool runs. Callers are cached by user SID for a few
  minutes so the group lookups are not redone on every request. A cached caller is never shared between
  two users.
- **Each tool call runs with that request's caller.** Today the caller is fixed when `DocTools` is built.
  It must become a per-request value, and every cache keyed on what the caller may read must stay keyed
  that way: the `world_map` overview, `readable_docs`, the vector row mask.
- **Logging.** Each call logs world, tool, the caller's name and duration, not the question text (as
  today). The offers world's `offer_verify` writes into the shared folder as dmx.datahub and records the
  caller's name in the record.
- **Updates.**
  - The VM's nightly pull of one world asks the server to pause that world. New calls to it get "this
    world is being updated, try again in a few minutes".
  - The pull waits for running calls to finish, swaps the files (the in-place mode of 6a434f7 when Z: is
    short of space), then tells the server to reopen the world.
  - The other worlds keep answering.
  - The pause and reopen commands are only accepted from the VM itself (127.0.0.1).
- **Encryption.**
  - HTTPS when IT provides a certificate for dmx-ws-vm01.
  - Until then, HTTP on the company network. Answers then cross the LAN unencrypted. That is a decision
    for IT; the server supports both.

### Who reaches which world

- **Every world served by the VM checks access document by document** (`access = "acl"`). The VM reads
  everything as dmx.datahub, so a world left at `access = "world"` would show everything to anyone who
  installs its extension. `serve-all` therefore refuses to start a world that is not `acl`.
- The offers world is therefore limited to the people who can read `O:\SAL\Clients` on the file server
  (DMX_SAL_Clients_R_GA and the named persons). That matches "sales and management only".
- My call, following "AD decides" rather than a separate group list per world.

### Tools that act on the user's PC

- `open_document` and `export_image` act on the person's own PC today. On the VM they would open windows
  or write files there.
- In central mode the server returns, instead of acting:
  - for `open_document`, the document's file-server path;
  - for `export_image`, the picture's bytes and file name.
- The relay acts on the PC, under the person's own login:
  - it opens the path, which the person can only open if the file server lets them;
  - it saves the picture in their local exports folder (`C:\dmx-rag\exports`, as today).
- What Claude sees stays the same, so the tools and the skill do not change. My call.

### Who keeps local servers

- Colleagues never get a local copy. They cannot read Y: anyway, so they use the extension only.
- Johan's laptop and WS032 keep the local mode (indexing needs local copies). `build_server` with a
  fixed caller therefore stays.
- Claude Code users can run the same relay as a local (stdio) MCP server. My call.

### Load

- The first version targets up to about 20 colleagues, with a few searches at the same time.
- Tool calls run in a small worker pool (4, one per vCPU). A call that waits more than 30 s gets "the
  server is busy, try again".
- The 1-2 s goal is for one search at a time. My call; to revisit with real usage logs.

### On each PC: the relay extension

- **Packaging.** One Claude Desktop extension (MCPB) per world: dmx-projects, dmx-offers, ...
  - Built from one template by a build script.
  - Each person installs the worlds they need; IT can push them.
  - The connector names stay the same as today, so the skill still works.
- **The relay** is a PowerShell 5.1 script started by Claude Desktop, with no Python.
  - It reads MCP messages from Claude Desktop on its input.
  - It posts each one to `http(s)://dmx-ws-vm01:<port>/<world>/mcp` with the Windows login
    (`-UseDefaultCredentials`: Kerberos, NTLM as fallback).
  - It writes the reply back. It keeps the session id the server returns.
- **Failures** come back as an MCP error with a short plain message, never as silence:
  - VM unreachable;
  - 401 (no identity);
  - world being updated.
- The VM address is part of the extension. IT can change it at install time.

### Memory budget (16 GB)

| Item | Size |
|---|---|
| Model, once | 2.2 GB |
| Projects vectors | 3.8 GB (compact form) or 7.6 GB (float32) |
| Offers vectors | 1.1 GB or 2.3 GB |
| Other worlds | 0.3 GB |
| Windows and server | about 3 GB |

- With compact vectors the total is about 10.5 GB.
- Measured on 2026-11-27: compact vectors expanded block by block in numpy take 2.6-4 s per search,
  against 0.26 s for float32. Compact vectors therefore need a library that compares them directly:
  faiss-cpu (download pending Johan's OK), to be measured first.
- If that fails, float32 needs about 24 GB of RAM.
- Until the RAM arrives (8 GB today), the server opens only the worlds that fit, in the config's order,
  and names the ones it left out in its log and in `index_status`. My call.

## What IT has to provide

1. **16 GB of RAM** on dmx-ws-vm01.
2. **An SPN for the service URL**: `setspn -S HTTP/dmx-ws-vm01.rotzingerag.local ROTZINGERAG\dmx.datahub`,
   plus the short name. **This is a launch prerequisite.**
   - Without it, a client asks for a ticket for the VM's computer account. The service, running as
     dmx.datahub, cannot read that ticket, so sign-in fails instead of falling back to NTLM.
   - The service must run as dmx.datahub, not as the computer, because `view_page` and `read_document`
     open the original files on the file server.
   - The plan includes a check command that tells "no SPN" apart from other sign-in failures.
3. **The firewall port** of the service open on the VM to the company network.
4. **A certificate for HTTPS**, or written acceptance of HTTP on the LAN.
5. **Deployment of the extensions**, or permission for colleagues to install them themselves.

## Left to the builder

- **HTTP stack.** The MCP SDK's streamable HTTP app mounted per world in one Starlette/uvicorn app
  (uvicorn is already a dependency), and how the Negotiate handshake is wired in as middleware.
- **Making the caller per request**: for example a context variable read by `DocTools`, or one light
  `DocTools` per caller around shared search engines.
- **Caller cache lifetime.** Default 5 minutes.
- **Pause and reopen protocol details**, and the matching change in `dmx.ps1 pull`.
- **Relay details**: encoding (UTF-8 both ways in PowerShell 5.1) and timeouts.
- **Tests.** The server half with a fake caller; the Negotiate layer against the machine's own login;
  the relay against a local test server, as test_dmx_ps1 does.

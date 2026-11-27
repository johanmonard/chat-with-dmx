"""serve-all: every world in one HTTP process (the central server on the VM).

Each world's MCP server (build_server, central mode) is mounted at /<world>/mcp (stateless streamable
HTTP, JSON answers). The NTLM layer (ntlm.py) signs every request in and hands over its Caller; each tool
call then runs in a worker thread of one pool shared by every world, as that caller.

Fail closed: a tool call without a signed-in caller is refused ("Not signed in."), and a world's tools
are built with a caller that reads nothing, never with the service account's identity. Only worlds with
access = "acl" are served.

Logs: one line per tool call with world, tool, caller, duration and outcome; never the arguments. The
MCP library's own log of a failing tool (its error text and traceback, which can hold a path or words
asked for) is silenced.
"""

from __future__ import annotations

import ctypes
import functools
import inspect
import logging
import os
import sqlite3
import time
import tomllib
import typing
from contextlib import AsyncExitStack, asynccontextmanager, closing
from pathlib import Path

import anyio
import anyio.to_thread
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

try:  # mcp >= 2
    from mcp.server.mcpserver import Context
    from mcp.server.mcpserver.exceptions import ToolError
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import Context
    from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings

from . import sources, store
from .access import Caller
from .config import Config, load_config
from .ntlm import NtlmAuth
from .tools import DocTools

log = logging.getLogger("dmx_docs.central")

NOT_SIGNED_IN = "Not signed in."
BUSY = "The server is busy, try again in a minute."
NO_MEMORY = "not enough memory"
UPDATING = "This world is being updated, try again in a few minutes."
PAUSE_WAIT_SECONDS = 120.0   # how long a pause waits for the running calls of the world
LOOPBACK = ("127.0.0.1", "::1")
HTTP_WARNING = "HTTP without a certificate: logins cross the LAN unencrypted."
GB = 1024 ** 3

# The fixed caller of every central DocTools: no SID at all, so no reading rule lets it read anything
# (not even rules naming Everyone). Only used if a tool body ever ran outside a request.
NO_RIGHTS = Caller(frozenset(), False, "nobody")

# The DNS-rebinding check of the MCP transport only accepts Host: localhost; colleagues reach the VM by
# its name. Every request is signed in by NTLM before it gets here.
NO_HOST_CHECK = TransportSecuritySettings(enable_dns_rebinding_protection=False)


MCP_SERVER_LOGGER = "mcp.server.mcpserver.server"


class _NoToolDetails(logging.Filter):
    """On the MCP library's server logger: drops its records about a failing tool ("Tool %r failed: %r",
    "Tool %r raised an unexpected exception" with the traceback), whose text can carry the call's
    arguments, and strips the traceback from its other records. World.call logs each call's outcome."""

    def filter(self, record: logging.LogRecord) -> bool:
        if str(record.msg).startswith("Tool "):
            return False
        record.exc_info, record.exc_text = None, None
        return True


def quiet_mcp_tool_logs() -> None:
    logger = logging.getLogger(MCP_SERVER_LOGGER)
    if not any(isinstance(f, _NoToolDetails) for f in logger.filters):
        logger.addFilter(_NoToolDetails())


class Busy(ToolError):
    """Every worker stayed taken for busy_seconds."""


class Gate:
    """The worker pool shared by every world: at most `workers` tool calls run at once, each in a worker
    thread (the tools are synchronous: on the event loop they would stop every other request). A call
    that waits longer than busy_seconds for a worker is refused as busy."""

    def __init__(self, workers: int, busy_seconds: float):
        self.workers = max(1, int(workers))
        self.busy_seconds = busy_seconds
        self._slots = anyio.CapacityLimiter(self.workers)    # held from the start to the end of a call
        self._threads = anyio.CapacityLimiter(self.workers)  # the threads running them (never waited on)

    async def run(self, fn):
        try:
            with anyio.fail_after(self.busy_seconds):
                await self._slots.acquire()
        except TimeoutError:
            raise Busy(BUSY) from None
        try:
            # Not abandoned when the request goes away: the slot is held until the thread is done.
            return await anyio.to_thread.run_sync(fn, limiter=self._threads)
        finally:
            self._slots.release()


def request_caller(ctx) -> Caller | None:
    """The caller the sign-in layer put on the HTTP request of this tool call (None: no request, or no
    signed-in caller)."""
    try:
        request = ctx.request_context.request
    except (AttributeError, LookupError, ValueError):
        return None
    caller = getattr(getattr(request, "state", None), "dmx_caller", None)
    return caller if isinstance(caller, Caller) else None


class World:
    """One world of the central server: its tools (built with NO_RIGHTS), the shared pool, and why it is
    not served (refusal: every tool call answers with it)."""

    def __init__(self, cfg: Config, gate: Gate | None = None, not_served: dict[str, str] | None = None,
                 refusal: str | None = None):
        self.cfg = cfg
        self.name = cfg.world or "docs"
        self.gate = gate or Gate(cfg.central_workers, cfg.central_busy_seconds)
        self.not_served = not_served if not_served is not None else {}  # world -> reason, every world's
        self.refusal = refusal
        self.paused = False   # the nightly update is replacing the world's files
        self.running = 0      # calls of this world past the checks, not yet finished (event loop only)
        self.tools = DocTools(cfg, NO_RIGHTS)

    def load_vectors(self) -> None:
        """Load the world's vectors now (start-up) rather than at its first search."""
        if not self.cfg.embeddings_enabled:
            return
        try:
            con = self.tools._con()
            try:
                self.tools.searcher.vectors.ensure(con)
            finally:
                con.close()
        except Exception as e:  # loaded at the first search instead
            log.warning("World %s: vectors not loaded at start: %s", self.name, type(e).__name__)

    async def pause(self, wait: float | None = None) -> int:
        """Stop answering: new calls are refused at once, the running ones get up to `wait` seconds
        (PAUSE_WAIT_SECONDS) to finish, then the vector files are released so the update can replace them.
        Returns how many calls were still running. No await between a call's check of `paused` and its
        count in `running`, so a call is either refused or waited for."""
        self.paused = True
        deadline = time.monotonic() + (PAUSE_WAIT_SECONDS if wait is None else wait)
        while self.running > 0 and time.monotonic() < deadline:
            await anyio.sleep(0.05)
        await anyio.to_thread.run_sync(self.tools.searcher.vectors.release)
        log.info("World %s paused (%d calls still running)", self.name, self.running)
        return self.running

    async def resume(self) -> None:
        """Answer again, from the files the update put in place (vectors loaded now, as at start-up)."""
        if self.refusal is None:
            await anyio.to_thread.run_sync(self.load_vectors)
        self.paused = False
        log.info("World %s resumed", self.name)

    def registrar(self, mcp):
        """mcp.tool, with each tool function wrapped by `wrap` before it is registered."""
        def tool(*args, **opts):
            register = mcp.tool(*args, **opts)
            return lambda fn: register(self.wrap(fn, opts.get("name") or fn.__name__))
        return tool

    def wrap(self, fn, name: str):
        """An async tool with fn's name, text, parameters and result type, plus a `ctx` parameter that MCP
        fills with the request context (left out of the tool's schema). `name`: the tool's name, for the log."""
        hints = typing.get_type_hints(fn)
        sig = inspect.signature(fn)
        params = [p.replace(annotation=hints.get(p.name, p.annotation)) for p in sig.parameters.values()]
        params.append(inspect.Parameter("ctx", inspect.Parameter.KEYWORD_ONLY, annotation=Context))

        async def tool(ctx, **kwargs):
            return await self.call(name, fn, kwargs, ctx)

        tool.__name__, tool.__qualname__, tool.__doc__, tool.__module__ = (
            fn.__name__, fn.__qualname__, fn.__doc__, fn.__module__)
        tool.__signature__ = sig.replace(parameters=params,
                                         return_annotation=hints.get("return", sig.return_annotation))
        tool.__annotations__ = {**hints, "ctx": Context}
        return tool

    async def call(self, name: str, fn, kwargs: dict, ctx):
        started = time.monotonic()
        caller = request_caller(ctx)
        outcome = "error"
        try:
            if caller is None:
                outcome = "not signed in"
                raise ToolError(NOT_SIGNED_IN)
            if self.refusal:
                outcome = "not served"
                raise ToolError(self.refusal)
            if self.paused:
                outcome = "paused"
                raise ToolError(UPDATING)
            self.running += 1
            try:
                result = await self.gate.run(functools.partial(self._run, caller, fn, kwargs))
            except Busy:
                outcome = "busy"
                raise
            except Exception as e:  # the type only: the text can hold the arguments
                outcome = f"error {type(e).__name__}"
                raise
            finally:
                self.running -= 1
            if name == "index_status" and self.not_served and isinstance(result, str):
                result = result + "\n\n" + not_served_text(self.not_served)
            outcome = "ok"
            return result
        finally:
            log.info("%s %s %s %.2f s %s", self.name, name, caller.label if caller else "-",
                     time.monotonic() - started, outcome)

    def _run(self, caller: Caller, fn, kwargs: dict):
        """In the worker thread: the tool body, as the request's caller."""
        with self.tools.as_caller(caller):
            return fn(**kwargs)


def not_served_text(not_served: dict[str, str]) -> str:
    return "Worlds of this server not served now:\n" + "\n".join(
        f"- {world}: not served: {reason}" for world, reason in sorted(not_served.items()))


def free_memory_bytes() -> int | None:
    """Free physical memory (GlobalMemoryStatusEx); None when it cannot be read."""
    if os.name != "nt":
        return None

    class MemoryStatusEx(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]

    status = MemoryStatusEx()
    status.dwLength = ctypes.sizeof(MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None
    return int(status.ullAvailPhys)


def vector_file_bytes(cfg: Config) -> int:
    """Size of the world's vector files for its current vector version (the .npy cache and the faiss
    index file); without a cache yet, the size of the float16 vectors and ids it will be made of."""
    if not cfg.embeddings_enabled or not cfg.db_path.exists():
        return 0
    from .search import VectorIndex

    try:
        with closing(store.connect(cfg.db_path, create=False)) as con:
            version = store.get_meta(con, "vec_version", "0")
            index = VectorIndex(cfg)
            ids_p, mat_p = index._cache_paths(version)
            if ids_p.exists() and mat_p.exists():
                return sum(p.stat().st_size for p in (ids_p, mat_p, index._ivf_path(version)) if p.exists())
            n = con.execute("SELECT count(*) FROM vectors").fetchone()[0]
            first = con.execute("SELECT vec FROM vectors LIMIT 1").fetchone()
    except (sqlite3.Error, OSError):
        return 0
    return n * ((len(first[0]) if first else 0) + 8)


def world_names(config_path: Path) -> list[str]:
    with open(config_path, "rb") as f:
        names = list((tomllib.load(f).get("worlds") or {}))
    if not names:
        raise ValueError(f"{config_path} has no [worlds.<name>] tables: serve-all serves worlds")
    return names


def _caller_from_token(admins: list[str], token) -> Caller:
    return Caller.from_token_handle(token, admins)


def build_app(config_path: Path, worlds: list[str] | None = None, auth: bool = True) -> Starlette:
    """Every world of the config (or those named) at /<world>/mcp, and GET /_whoami, behind the NTLM
    sign-in when `auth` (auth=False: tests, which put the caller in the request state themselves).
    Raises ValueError, naming them, when a world does not have access = "acl"."""
    config_path = Path(config_path)
    quiet_mcp_tool_logs()
    cfgs = [load_config(config_path, world=name) for name in (worlds or world_names(config_path))]
    refused = [cfg.world for cfg in cfgs if cfg.access != "acl"]
    if refused:
        raise ValueError(f"serve-all serves only worlds with access = \"acl\" (each person sees what the "
                         f"file server lets them read); not \"acl\": {', '.join(refused)}")
    first = cfgs[0]
    gate = Gate(first.central_workers, first.central_busy_seconds)
    not_served: dict[str, str] = {}
    servers, routes, served = [], [], {}
    from .server import build_server

    for cfg in cfgs:
        sources.refresh(cfg)  # root/excluded folders are managed on the configuration page
        need, free = vector_file_bytes(cfg) + GB, free_memory_bytes()
        refusal = None
        if free is not None and need > free:
            not_served[cfg.world] = NO_MEMORY
            refusal = f"{cfg.world}: not served: {NO_MEMORY} on the server."
            log.warning("World %s not served: not enough memory (vectors and 1 GB margin %.1f GB, free %.1f GB)",
                        cfg.world, need / GB, free / GB)
        world = World(cfg, gate, not_served, refusal)
        if refusal is None:
            world.load_vectors()
        mcp = build_server(cfg, central=True, world=world)
        routes.append(Mount(f"/{cfg.world}", app=mcp.streamable_http_app(
            json_response=True, stateless_http=True, transport_security=NO_HOST_CHECK)))
        servers.append(mcp)
        served[cfg.world] = world
    log.info("Serving %s (%d workers, busy after %g s)%s", ", ".join(f"/{c.world}/mcp" for c in cfgs),
             gate.workers, gate.busy_seconds, f"; not served: {', '.join(not_served)}" if not_served else "")

    async def whoami(request):
        caller = getattr(request.state, "dmx_caller", None)
        if not isinstance(caller, Caller):
            return JSONResponse({"error": NOT_SIGNED_IN}, status_code=401)
        return JSONResponse({"caller": caller.label, "admin": caller.admin, "groups": len(caller.sids),
                             "not_served": dict(not_served)})

    async def admin(request):
        """POST /_admin/<world>/pause | resume: the nightly update (dmx.ps1 pull) on this machine only."""
        if not request.client or request.client.host not in LOOPBACK:
            return JSONResponse({"error": "Forbidden"}, status_code=403)
        world = served.get(request.path_params["world"])
        if world is None:
            return JSONResponse({"error": "Unknown world"}, status_code=404)
        if request.url.path.endswith("/pause"):
            return JSONResponse({"paused": True, "running": await world.pause()})
        await world.resume()
        return JSONResponse({"paused": False})

    @asynccontextmanager
    async def lifespan(_app):
        async with AsyncExitStack() as stack:
            for mcp in servers:
                await stack.enter_async_context(mcp.session_manager.run())
            yield

    middleware = []
    if auth:
        middleware.append(Middleware(NtlmAuth, make_caller=functools.partial(_caller_from_token, first.access_admins),
                                     cache_seconds=first.central_caller_cache_seconds,
                                     domain_sid=first.access_domain_sid))
    app = Starlette(routes=[Route("/_whoami", whoami, methods=["GET"]),
                           Route("/_admin/{world}/pause", admin, methods=["POST"], name="pause"),
                           Route("/_admin/{world}/resume", admin, methods=["POST"], name="resume"), *routes], middleware=middleware,
                    lifespan=lifespan)
    app.state.worlds = served  # world name -> World
    return app

"""Configuration web page: root folders, excluded folders, scans and index status.

Runs locally (http://127.0.0.1:8765 by default) for a single user.
"""

from __future__ import annotations

import collections
import json
import os
import string
import sys
import threading
import time
from datetime import datetime
from importlib import resources
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from . import sources, store
from .config import Config

PROBLEM_STATUSES = ("error", "skipped", "no_text")


class Job:
    def __init__(self, kind: str):
        self.kind = kind
        self.started = time.time()
        self.finished: float | None = None
        self.state = "running"  # running | done | stopped | failed
        self.summary = ""
        self.lines: collections.deque[str] = collections.deque(maxlen=2000)
        self.stop_requested = False

    def log(self, msg: str) -> None:
        self.lines.append(f"{datetime.now():%H:%M:%S} {msg}")

    def as_dict(self) -> dict:
        return {"kind": self.kind, "state": self.state, "started": self.started,
                "finished": self.finished, "summary": self.summary,
                "stop_requested": self.stop_requested, "lines": list(self.lines)}


class JobRunner:
    """Runs one scan at a time in a background thread."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.job: Job | None = None
        self.lock = threading.Lock()

    def running(self) -> bool:
        return self.job is not None and self.job.state == "running"

    def start_scan(self, embed: bool, retry_errors: bool) -> Job:
        with self.lock:
            if self.running():
                raise HTTPException(409, "A scan is already running")
            job = self.job = Job("scan")
        threading.Thread(target=self._scan, args=(job, embed, retry_errors), daemon=True).start()
        return job

    def _scan(self, job: Job, embed: bool, retry_errors: bool) -> None:
        from .embeddings import run_embed
        from .indexer import Cancelled, run_index

        stop = lambda: job.stop_requested  # noqa: E731
        try:
            job.log("Scanning for new, changed and deleted files ...")
            stats = run_index(self.cfg, retry_errors=retry_errors, progress=job.log, should_stop=stop)
            job.summary = stats.summary()
            job.log("Scan finished: " + job.summary)
            if embed and self.cfg.embeddings_enabled and not job.stop_requested:
                job.log("Computing embeddings (search by meaning) ...")
                run_embed(self.cfg, progress=job.log, should_stop=stop)
            job.state = "stopped" if job.stop_requested else "done"
        except Cancelled:
            job.log("Stopped. Progress is saved; the next scan continues from there.")
            job.state = "stopped"
        except BaseException as e:  # noqa: BLE001 - report anything to the page
            job.log(f"ERROR: {type(e).__name__}: {e}")
            job.state = "failed"
        finally:
            job.finished = time.time()

    def stop(self) -> None:
        if self.running():
            self.job.stop_requested = True
            self.job.log("Stop requested, finishing files in progress ...")


class PathBody(BaseModel):
    path: str


class ExcludeBody(BaseModel):
    path: str
    excluded: bool


class PatternsBody(BaseModel):
    patterns: list[str]


class ScanBody(BaseModel):
    embed: bool = True
    retry_errors: bool = False


def _drives() -> list[str]:
    if sys.platform == "win32":
        # Probing each letter with os.path.exists blocks ~20 s on a disconnected mapped drive.
        import ctypes
        mask = ctypes.windll.kernel32.GetLogicalDrives()
        return [f"{d}:\\" for i, d in enumerate(string.ascii_uppercase) if mask >> i & 1]
    return ["/", str(Path.home())]


def claude_desktop_snippet(cfg: Config) -> str:
    config_path = str(cfg.config_path) if cfg.config_path else "config.toml"
    return json.dumps({"mcpServers": {"dmx-docs": {
        "command": sys.executable,
        "args": ["-m", "dmx_docs.cli", "--config", config_path, "serve"],
    }}}, indent=2)


def create_app(cfg: Config, allowed_hosts: set[str] | None = None) -> FastAPI:
    store.connect(cfg.db_path).close()  # create the database if needed
    sources.refresh(cfg)
    runner = JobRunner(cfg)
    app = FastAPI(title="dmx-docs", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.runner = runner

    @app.middleware("http")
    async def local_only(request: Request, call_next):
        # Protects the local page against other websites open in the browser:
        # DNS-rebinding (Host check) and cross-site form posts (custom header).
        host = (request.headers.get("host") or "").split(":")[0]
        if allowed_hosts is not None and host not in allowed_hosts:
            return JSONResponse({"detail": "forbidden host"}, status_code=403)
        if request.method != "GET" and request.headers.get("x-dmx") != "1":
            return JSONResponse({"detail": "missing header"}, status_code=403)
        return await call_next(request)

    def con():
        c = store.connect(cfg.db_path)
        sources.refresh(cfg, c)
        return c

    def bad_request(e: Exception):
        raise HTTPException(400, str(e))

    @app.get("/", response_class=HTMLResponse)
    def index():
        return resources.files("dmx_docs").joinpath("web/index.html").read_text(encoding="utf-8")

    @app.get("/api/state")
    def state():
        c = con()
        try:
            roots = []
            for root, rk in zip(cfg.roots, cfg.root_keys):
                prefix = rk.rstrip(os.sep) + os.sep
                n = c.execute("SELECT count(*) FROM docs WHERE status='ok' AND "
                              "(path_key = ? OR substr(path_key, 1, ?) = ?)",
                              (rk, len(prefix), prefix)).fetchone()[0]
                excluded = [d for d, k in zip(cfg.excluded_dirs, map(store.path_key, cfg.excluded_dirs))
                            if store.is_under(k, rk)]
                roots.append({"path": root, "reachable": os.path.isdir(root), "documents": n,
                              "excluded": excluded})
            by_status = {r[0]: r[1] for r in c.execute("SELECT status, count(*) FROM docs GROUP BY status")}
            chunks, embedded = c.execute("SELECT count(*), coalesce(sum(embedded), 0) FROM chunks").fetchone()
            last = store.get_meta(c, "last_index_finished")
            changed = store.get_meta(c, "sources_changed_at")
        finally:
            c.close()
        return {
            "roots": roots,
            "patterns": cfg.exclude,
            "extensions": cfg.extensions,
            "data_dir": str(cfg.data_dir),
            "config_path": str(cfg.config_path or ""),
            "embeddings_enabled": cfg.embeddings_enabled,
            "embedding_model": cfg.embedding_model,
            "stats": {"by_status": by_status, "documents": sum(by_status.values()),
                      "chunks": chunks, "embedded": embedded,
                      "last_scan": float(last) if last else None},
            "needs_rescan": bool(changed and (not last or float(changed) > float(last))),
            "job": runner.job.as_dict() if runner.job else None,
            "claude_desktop": claude_desktop_snippet(cfg),
        }

    @app.get("/api/browse")
    def browse(path: str | None = None):
        if not path:
            return {"path": None, "parent": None,
                    "dirs": [{"name": d, "path": d, "excluded": False} for d in _drives()]}
        path = sources.clean_path(path)  # 'Z:' must list Z:\, not the drive's current folder
        try:
            with os.scandir(path) as it:
                names = sorted((e.name, e.path) for e in it if e.is_dir() and not e.name.startswith((".", "$")))
        except OSError as e:
            raise HTTPException(400, f"Cannot open {path}: {e}")
        con().close()  # refresh exclusions
        dirs = []
        for name, p in names:
            key = store.path_key(p)
            dirs.append({"name": name, "path": p, "excluded": key in cfg.excluded_keys,
                         "pattern_excluded": cfg.is_excluded(name, p)})
        parent = os.path.dirname(path.rstrip("\\/")) or None
        if parent == path:
            parent = None
        return {"path": path, "parent": parent, "dirs": dirs}

    @app.post("/api/roots")
    def add_root(body: PathBody):
        c = con()
        try:
            return {"path": sources.add_root(c, body.path)}
        except ValueError as e:
            bad_request(e)
        finally:
            c.close()

    @app.post("/api/roots/remove")
    def remove_root(body: PathBody):
        c = con()
        try:
            sources.remove_root(c, body.path)
        finally:
            c.close()
        return {"ok": True}

    @app.post("/api/exclude")
    def exclude(body: ExcludeBody):
        c = con()
        try:
            sources.set_excluded(c, body.path, body.excluded)
        except ValueError as e:
            bad_request(e)
        finally:
            c.close()
        return {"ok": True}

    @app.post("/api/patterns")
    def patterns(body: PatternsBody):
        c = con()
        try:
            return {"patterns": sources.set_patterns(c, body.patterns)}
        finally:
            c.close()

    @app.post("/api/scan")
    def scan(body: ScanBody):
        return runner.start_scan(body.embed, body.retry_errors).as_dict()

    @app.post("/api/scan/stop")
    def stop():
        runner.stop()
        return {"ok": True}

    @app.get("/api/problems")
    def problems(status: str | None = None, offset: int = 0, limit: int = 100):
        statuses = [status] if status in PROBLEM_STATUSES else list(PROBLEM_STATUSES)
        marks = ",".join("?" * len(statuses))
        c = con()
        try:
            total = c.execute(f"SELECT count(*) FROM docs WHERE status IN ({marks})", statuses).fetchone()[0]
            rows = c.execute(f"SELECT path, status, error, mtime FROM docs WHERE status IN ({marks}) "
                             "ORDER BY path LIMIT ? OFFSET ?", statuses + [limit, offset]).fetchall()
        finally:
            c.close()
        return {"total": total, "items": [dict(r) for r in rows]}

    return app


def run_web(cfg: Config, host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    import uvicorn

    allowed = {"127.0.0.1", "localhost", "::1", "[::1]"} if host in ("127.0.0.1", "localhost") else None
    app = create_app(cfg, allowed_hosts=allowed)
    url = f"http://{'127.0.0.1' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}"
    print(f"Configuration page: {url}  (Ctrl+C to stop)")
    if open_browser:
        import webbrowser
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    uvicorn.run(app, host=host, port=port, log_level="warning")

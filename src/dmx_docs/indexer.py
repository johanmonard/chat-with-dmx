"""Crawl the root folders and keep the index in sync with the files.

The run is incremental and resumable: unchanged files (same size and
modification time) are skipped, results are committed in batches, and an
interrupted run simply continues where it stopped the next time.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field

from . import sources, store
from .chunking import make_chunks
from .config import Config
from .extract import EXTRACT_VERSION, Extracted, extract_file

log = logging.getLogger("dmx_docs.indexer")

# "spawn" everywhere (the Windows default): forking from the web app's
# background thread can deadlock on Linux.
_MP = multiprocessing.get_context("spawn")

COMMIT_EVERY = 200
PROGRESS_EVERY_S = 15


class Cancelled(Exception):
    """Raised when a scan is stopped from the configuration page."""


@dataclass
class Stats:
    seen: int = 0
    unchanged: int = 0
    processed: int = 0
    new: int = 0
    updated: int = 0
    deleted: int = 0
    removed: int = 0
    by_status: dict = field(default_factory=dict)
    scan_errors: int = 0

    def summary(self) -> str:
        st = ", ".join(f"{k}={v}" for k, v in sorted(self.by_status.items()))
        return (f"files seen={self.seen} unchanged={self.unchanged} processed={self.processed} "
                f"(new={self.new} updated={self.updated}) deleted={self.deleted} "
                f"removed_by_config={self.removed} "
                f"scan_errors={self.scan_errors} [{st}]")


def scan(cfg: Config, root: str, stats: Stats):
    """Yield (path, size, mtime) for indexable files under root."""
    stack = [root]
    exts = set(cfg.extensions)
    while stack:
        folder = stack.pop()
        try:
            with os.scandir(store.fs_path(folder)) as it:
                entries = list(it)
        except OSError as e:
            stats.scan_errors += 1
            log.warning("cannot list %s: %s", folder, e)
            continue
        for entry in entries:
            path = os.path.join(folder, entry.name)  # without the long-path prefix
            try:
                if cfg.is_excluded(entry.name, path):
                    continue
                if entry.is_dir(follow_symlinks=False):
                    if store.path_key(path) not in cfg.excluded_keys:
                        stack.append(path)
                elif entry.is_file(follow_symlinks=False):
                    if os.path.splitext(entry.name)[1].lower() in exts:
                        st = entry.stat(follow_symlinks=False)
                        yield path, st.st_size, st.st_mtime
            except OSError as e:
                stats.scan_errors += 1
                log.warning("cannot stat %s: %s", path, e)


class _Writer:
    def __init__(self, con, run_id: int, stats: Stats):
        self.con = con
        self.run_id = run_id
        self.stats = stats
        self.pending_ops = 0
        self.vectors_removed = False
        con.execute("BEGIN")

    def tick(self) -> None:
        self.pending_ops += 1
        if self.pending_ops >= COMMIT_EVERY:
            self.commit()

    def commit(self) -> None:
        if self.vectors_removed:
            store.bump_vector_version(self.con)
            self.vectors_removed = False
        self.con.execute("COMMIT")
        self.con.execute("BEGIN")
        self.pending_ops = 0

    def mark_seen(self, doc_id: int) -> None:
        self.con.execute("UPDATE docs SET seen_run=? WHERE id=?", (self.run_id, doc_id))
        self.tick()

    def save(self, path: str, size: int, mtime: float, result: Extracted, existed: bool) -> None:
        chunks = make_chunks(result.pages) if result.status in ("ok", "no_text") else []
        _, removed = store.upsert_doc(
            self.con, path=path, size=size, mtime=mtime, status=result.status, error=result.error,
            n_pages=result.n_pages, title=result.title, indexed_at=time.time(), run_id=self.run_id,
            chunks=chunks)
        self.vectors_removed |= removed
        s = self.stats
        s.processed += 1
        s.updated += existed
        s.new += not existed
        s.by_status[result.status] = s.by_status.get(result.status, 0) + 1
        if result.status in ("error", "skipped"):
            log.info("%s: %s (%s)", result.status, path, result.error)
        self.tick()


def run_index(cfg: Config, retry_errors: bool = False, progress=print, should_stop=None) -> Stats:
    con = store.connect(cfg.db_path)
    sources.refresh(cfg, con)
    con.isolation_level = None  # explicit transactions
    run_id = int(store.get_meta(con, "last_run", "0")) + 1
    store.set_meta(con, "last_run", run_id)
    # A new extractor version re-extracts every document indexed before it was
    # installed. Kept as a timestamp, so an interrupted re-extraction resumes.
    if store.get_meta(con, "extract_version") != str(EXTRACT_VERSION):
        store.set_meta(con, "extract_version", EXTRACT_VERSION)
        store.set_meta(con, "reextract_before", time.time())
    reextract_before = float(store.get_meta(con, "reextract_before", "0"))
    stats = Stats()
    writer = _Writer(con, run_id, stats)
    max_bytes = cfg.max_file_mb * 1024 * 1024
    options = cfg.extract_options()
    workers = max(1, int(cfg.workers))
    retry_statuses = {"error", "skipped"} if retry_errors else set()

    pending: dict = {}
    crashed: list[tuple[str, int, float, bool]] = []
    last_progress = time.monotonic()
    started = time.monotonic()

    def report(force: bool = False) -> None:
        nonlocal last_progress
        now = time.monotonic()
        if force or now - last_progress >= PROGRESS_EVERY_S:
            rate = stats.processed / max(now - started, 1e-6)
            progress(f"  {stats.seen} files seen, {stats.processed} extracted "
                     f"({rate:.1f}/s), {stats.unchanged} unchanged, in progress: {len(pending)}")
            last_progress = now

    def check_stop() -> None:
        if should_stop is not None and should_stop():
            raise Cancelled()

    def collect(done) -> None:
        for fut in done:
            path, size, mtime, existed = pending.pop(fut)
            try:
                result = fut.result()
            except BrokenProcessPool:
                crashed.append((path, size, mtime, existed))
                continue
            except Exception as e:
                result = Extracted(status="error", error=f"{type(e).__name__}: {e}"[:500])
            writer.save(path, size, mtime, result, existed)

    if not cfg.roots:
        progress("No root folder configured: add one on the configuration page.")
    executor = ProcessPoolExecutor(max_workers=workers, mp_context=_MP)
    try:
        for root in cfg.roots:
            if not os.path.isdir(root):
                progress(f"WARNING: root not reachable, skipped: {root}")
                continue
            progress(f"Scanning {root} ...")
            for path, size, mtime in scan(cfg, root, stats):
                check_stop()
                stats.seen += 1
                row = con.execute("SELECT id, size, mtime, status, indexed_at FROM docs WHERE path_key=?",
                                  (store.path_key(path),)).fetchone()
                if (row and row["size"] == size and abs((row["mtime"] or 0) - mtime) < 0.01
                        and row["status"] not in retry_statuses
                        and (row["indexed_at"] or 0) >= reextract_before):
                    stats.unchanged += 1
                    writer.mark_seen(row["id"])
                    report()
                    continue
                if size > max_bytes:
                    writer.save(path, size, mtime, Extracted(
                        status="skipped", error=f"file larger than {cfg.max_file_mb} MB"), bool(row))
                    continue
                try:
                    fut = executor.submit(extract_file, path, options)
                except BrokenProcessPool:
                    executor = ProcessPoolExecutor(max_workers=workers, mp_context=_MP)
                    fut = executor.submit(extract_file, path, options)
                pending[fut] = (path, size, mtime, bool(row))
                if len(pending) >= workers * 4:
                    done, _ = wait(pending, return_when=FIRST_COMPLETED)
                    collect(done)
                    if crashed and getattr(executor, "_broken", False):
                        collect(list(pending))  # the remaining futures fail the same way
                        executor.shutdown(wait=False, cancel_futures=True)
                        executor = ProcessPoolExecutor(max_workers=workers, mp_context=_MP)
                report()
        while pending:
            check_stop()
            done, _ = wait(pending, return_when=FIRST_COMPLETED, timeout=1)
            collect(done)
            report()
    except (KeyboardInterrupt, Cancelled):
        # Keep what was extracted so far; the next run resumes from there.
        progress("Interrupted - saving progress ...")
        executor.shutdown(wait=False, cancel_futures=True)
        writer.commit()
        con.execute("COMMIT")
        con.close()
        raise
    finally:
        executor.shutdown(wait=True, cancel_futures=True)

    # A worker crashed (e.g. a malformed file crashing the PDF library): retry
    # those files one at a time so only the culprit is marked as an error.
    for path, size, mtime, existed in crashed:
        with ProcessPoolExecutor(max_workers=1, mp_context=_MP) as solo:
            try:
                result = solo.submit(extract_file, path, options).result()
            except Exception as e:
                result = Extracted(status="error", error=f"extraction crashed: {type(e).__name__}")
        writer.save(path, size, mtime, result, existed)

    # Remove documents of root folders that were removed or of excluded folders.
    for r in con.execute("SELECT id, path_key FROM docs").fetchall():
        if not cfg.allows(r["path_key"]):
            writer.vectors_removed |= store.delete_doc(con, r["id"])
            stats.removed += 1
            writer.tick()

    # Remove documents that disappeared, only under roots that were reachable.
    for root in cfg.roots:
        if not os.path.isdir(root):
            continue
        root_key = store.path_key(root)
        gone = [r["id"] for r in con.execute(
            "SELECT id, path_key FROM docs WHERE seen_run < ?", (run_id,))
            if store.is_under(r["path_key"], root_key)]
        for doc_id in gone:
            writer.vectors_removed |= store.delete_doc(con, doc_id)
            stats.deleted += 1
            writer.tick()

    store.set_meta(con, "last_index_finished", time.time())
    writer.commit()
    con.execute("COMMIT")
    report(force=True)
    con.close()
    return stats

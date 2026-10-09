"""Crawl the root folders and keep the index in sync with the files.

The run is incremental and resumable: unchanged files (same size and
modification time) are skipped, results are committed in batches, and an
interrupted run simply continues where it stopped the next time.
"""

from __future__ import annotations

import logging
import multiprocessing
import os
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass, field

from . import extract, facets, sources, store
from .chunking import make_chunks
from .config import Config
from .extract import EXTRACT_VERSION, Extracted, extract_file, kill_office_automation

log = logging.getLogger("dmx_docs.indexer")

# "spawn" everywhere (the Windows default): forking from the web app's
# background thread can deadlock on Linux.
_MP = multiprocessing.get_context("spawn")

COMMIT_EVERY = 200
PROGRESS_EVERY_S = 15
STALL_S = 600         # no file finished for this long: the running ones are stuck
SOLO_TIMEOUT_S = 300  # per-file limit when stuck or crashed files are retried alone


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


def _kill_automation_office() -> None:
    """End the Word/PowerPoint instances started for .doc/.ppt conversion (they outlive killed
    workers; PowerPoint is never quit by the workers). The user's own windows are left alone."""
    try:
        kill_office_automation("WINWORD.EXE", "POWERPNT.EXE")
    except Exception as e:  # noqa: BLE001
        log.warning("could not stop Word/PowerPoint: %s", e)


def _kill_automation_office_after_run() -> None:
    """End-of-run cleanup of the hidden Word/PowerPoint instances. Word is ended at once. PowerPoint
    is a single instance shared by every process of the machine, and a conversion in another
    process (the MCP server) may be using it right now: it is ended only while this process holds
    the PowerPoint slot, which a conversion holds for its whole duration. If the slot stays taken
    for POWERPOINT_TIMEOUT_S, a conversion is running and PowerPoint is left alone."""
    try:
        kill_office_automation("WINWORD.EXE")
    except Exception as e:  # noqa: BLE001
        log.warning("could not stop Word: %s", e)
    if sys.platform != "win32":
        return
    try:
        import win32event
    except ImportError:  # no pywin32: this program cannot have started a PowerPoint
        return
    opened_here = extract._ppt_slots is None  # a handle this process opened only for the cleanup is closed again
    try:
        slot = extract._ppt_slot()
        try:
            if win32event.WaitForSingleObject(slot, int(extract.POWERPOINT_TIMEOUT_S * 1000)) != win32event.WAIT_OBJECT_0:
                log.warning("PowerPoint was left running: a .ppt conversion in another process still "
                            "holds it after %s s", extract.POWERPOINT_TIMEOUT_S)
                return
            try:
                kill_office_automation("POWERPNT.EXE")
            finally:
                win32event.ReleaseSemaphore(slot, 1)
        finally:
            if opened_here:
                # The semaphore lives as long as any process has a handle on it: one kept by this
                # long-lived process would hold the count of a worker killed mid-conversion at zero.
                extract._close_ppt_slot()
    except Exception as e:  # noqa: BLE001
        log.warning("could not stop PowerPoint: %s", e)


def _kill_pool(executor: ProcessPoolExecutor) -> None:
    """Stop a pool whose workers may be stuck: shutdown(wait=True) would wait forever."""
    for proc in list((getattr(executor, "_processes", None) or {}).values()):
        try:
            proc.terminate()
        except Exception:  # noqa: BLE001
            pass
    executor.shutdown(wait=False, cancel_futures=True)
    _kill_automation_office()


def _shutdown(executor: ProcessPoolExecutor) -> None:
    executor.shutdown(wait=True, cancel_futures=True)


def _extract_alone(path: str, options: dict, check_stop) -> Extracted:
    """Extract one file in its own process, giving up after SOLO_TIMEOUT_S."""
    solo = ProcessPoolExecutor(max_workers=1, mp_context=_MP)
    fut = solo.submit(extract_file, path, options)
    deadline = time.monotonic() + SOLO_TIMEOUT_S
    try:
        while True:
            done, _ = wait([fut], timeout=1)  # short steps: Ctrl+C stays responsive
            if done:
                try:
                    return fut.result()
                except Exception as e:  # noqa: BLE001
                    return Extracted(status="error", error=f"extraction crashed: {type(e).__name__}")
            check_stop()
            if time.monotonic() > deadline:
                return Extracted(status="error",
                                 error=f"extraction did not finish within {SOLO_TIMEOUT_S // 60} min (skipped)")
    finally:
        if fut.done():
            _shutdown(solo)
        else:
            _kill_pool(solo)


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

    def new_pool(n: int = workers) -> ProcessPoolExecutor:
        return ProcessPoolExecutor(max_workers=n, mp_context=_MP)

    last_done = time.monotonic()

    def drain(until_below: int | None) -> None:
        """Collect finished files until fewer than `until_below` are pending (None: all).
        Waits in 1 s steps so that Ctrl+C and the Stop button work, and restarts the
        workers when none of them has finished a file for STALL_S."""
        nonlocal executor, last_done
        while pending and (until_below is None or len(pending) >= until_below):
            done, _ = wait(pending, return_when=FIRST_COMPLETED, timeout=1)
            if done:
                collect(done)
                last_done = time.monotonic()
                if crashed and getattr(executor, "_broken", False):
                    collect(list(pending))  # the remaining futures fail the same way
                    _kill_pool(executor)
                    executor = new_pool()
            elif time.monotonic() - last_done > STALL_S:
                stuck = [p for p, *_ in list(pending.values())[:workers]]
                progress(f"WARNING: no file finished for {STALL_S // 60} min. Restarting the workers; "
                         f"the {len(pending)} pending files are retried one by one "
                         f"({SOLO_TIMEOUT_S // 60} min max each). Probably stuck: " + "; ".join(stuck))
                log.warning("stall, probably stuck: %s", stuck)
                crashed.extend(pending.values())
                pending.clear()
                _kill_pool(executor)
                executor = new_pool()
                last_done = time.monotonic()
            check_stop()
            report()

    if not cfg.roots:
        progress("No root folder configured: add one on the configuration page.")
    executor = new_pool()
    try:
        for root in cfg.roots:
            if not os.path.isdir(store.fs_path(root)):
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
                    executor = new_pool()
                    fut = executor.submit(extract_file, path, options)
                pending[fut] = (path, size, mtime, bool(row))
                drain(until_below=workers * 4)
                report()
        drain(until_below=None)
        _shutdown(executor)

        # Files whose worker crashed (e.g. a malformed file crashing the PDF library) or
        # got stuck: retry them one at a time so only the culprit is marked as an error.
        for path, size, mtime, existed in crashed:
            progress(f"  retrying alone: {path}")
            writer.save(path, size, mtime, _extract_alone(path, options, check_stop), existed)
        if ".ppt" in cfg.extensions:
            _kill_automation_office_after_run()  # the hidden Word/PowerPoint started for conversions
    except (KeyboardInterrupt, Cancelled):
        # Keep what was extracted so far; the next run resumes from there.
        progress("Interrupted - saving progress ...")
        _kill_pool(executor)
        writer.commit()
        con.execute("COMMIT")
        con.close()
        raise

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
    facets.refresh(con, cfg.roots, cfg.profile)
    report(force=True)
    con.close()
    return stats

"""OCR of PDF pages that have no text layer (scans, image-only pages).

A separate step between `index` and `embed`: it finds PDF pages whose extracted text is
(almost) empty, reads them with the Tesseract engine built into PyMuPDF and APPENDS the
recognized text as new chunks. Existing chunks and their vectors are never touched, so only
the new chunks need embedding. Every page read is recorded in `ocr_pages` (store.py), which
makes the step resumable and lets the tools label OCR text.
"""

from __future__ import annotations

import logging
import os
import re
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass

from . import store
from .chunking import split_text

log = logging.getLogger("dmx_docs.ocr")

OCR_VERSION = 1          # bump when OCR output changes: pages read by an older version are redone
MIN_PAGE_CHARS = 50      # a PDF page with less extracted text is an OCR candidate
MIN_OCR_CHARS = 20       # less recognized text: 'empty' (photo, drawing without notes)
PAGES_PER_JOB = 20       # pages of one document read by one worker call
STALL_S = 600            # no job finished for this long: the running ones are stuck
PROGRESS_EVERY_S = 15
MAX_OCR_PIXELS = 40_000_000  # a page rendered for OCR needs ~12 bytes per pixel (~2.3 GB for an A0 at 300 dpi)
MIN_OCR_DPI = 100            # a huge page is read at a lower resolution, but not below this
MAX_FAILED_JOBS_IN_A_ROW = 50  # that many jobs with only errors in a row: the file share is probably gone
TESSERACT_LOG = "ocr-tesseract.log"  # the console messages of Tesseract in the workers (in the logs folder)

_LEAD = "(«\"'“‘["        # punctuation before a word
_TAIL = ".,;:!?)»\"'”’]"  # punctuation after a word
_WORD = re.compile(r"^[^\W\d_]+(?:['’\-][^\W\d_]+)*$")  # letters, joined by an apostrophe or a hyphen
_VOWEL = re.compile(r"[aeiouyàâäéèêëîïôöûùüÿœæáíóúAEIOUYÀÂÄÉÈÊËÎÏÔÖÛÙÜŒÆÁÍÓÚ]")


def _word(token: str) -> str | None:
    """The word of a token, without the punctuation around it, or None."""
    core = token.lstrip(_LEAD).rstrip(_TAIL)
    return core if _WORD.match(core) and _VOWEL.search(core) else None


def keep_line(line: str) -> bool:
    """A line of real text, not drawing or photo noise: at least 2 words of 2+ letters, and words
    making up at least half of the line's tokens. A word is letters, possibly joined by an
    apostrophe or a hyphen (l'axe, sous-ensemble), with a vowel. A single letter (a, y) is a word
    for the half of the tokens, but not one of the 2 words."""
    tokens = line.split()
    words = [w for w in map(_word, tokens) if w]
    long_words = [w for w in words if sum(c.isalpha() for c in w) >= 2]
    return len(long_words) >= 2 and len(words) * 2 >= len(tokens)


def filter_text(text: str) -> str:
    from .extract import clean_text

    return clean_text("\n".join(line for line in text.splitlines() if keep_line(line)))


@dataclass
class Job:
    doc_id: int
    path: str
    pages: list[int]


def candidates(con, max_pages: int, retry: bool = False) -> list[Job]:
    """PDF pages to read: fewer than MIN_PAGE_CHARS of extracted text, or read by an older
    OCR_VERSION, or (retry) failed before. Fully scanned documents first; jobs of at most
    PAGES_PER_JOB pages of one document."""
    chars: dict[tuple[int, int], int] = {}
    for d, p, n in con.execute(
            """SELECT c.doc_id, c.page_no, sum(length(c.text)) FROM chunks c JOIN docs d ON d.id = c.doc_id
               WHERE d.ext = '.pdf' AND d.status IN ('ok', 'no_text') GROUP BY c.doc_id, c.page_no"""):
        chars[(d, p)] = n
    redo = {"timeout", "error"} if retry else set()
    known, done = set(), set()
    for d, p, status, version in con.execute("SELECT doc_id, page_no, status, ocr_version FROM ocr_pages"):
        known.add((d, p))
        if version == OCR_VERSION and status not in redo:
            done.add((d, p))
    jobs: list[Job] = []
    for d, path, n_pages in con.execute(
            """SELECT id, path, n_pages FROM docs WHERE ext = '.pdf' AND status IN ('ok', 'no_text')
               ORDER BY status = 'ok', id"""):
        pages = [p for p in range(1, min(n_pages or 0, max_pages) + 1)
                 if (d, p) not in done and ((d, p) in known or chars.get((d, p), 0) < MIN_PAGE_CHARS)]
        for i in range(0, len(pages), PAGES_PER_JOB):
            jobs.append(Job(d, path, pages[i:i + PAGES_PER_JOB]))
    return jobs


def ocr_dpi(rect, dpi: int) -> int:
    """The resolution to render a page of this size at: the configured one, lowered for a huge page
    (a drawing) so that it has at most MAX_OCR_PIXELS pixels, but not below MIN_OCR_DPI."""
    pixels = (rect.width / 72 * dpi) * (rect.height / 72 * dpi)
    if pixels <= MAX_OCR_PIXELS:
        return dpi
    return min(dpi, max(MIN_OCR_DPI, int(dpi * (MAX_OCR_PIXELS / pixels) ** 0.5)))


def read_pages(path: str, pages: list[int], options: dict) -> list[tuple[int, str, str]]:
    """Read pages of one PDF with Tesseract: [(page_no, 'text'|'empty'|'error', text or error)].
    Runs in a worker process: plain arguments and results only."""
    from .extract import pymupdf

    try:
        doc = pymupdf.open(store.fs_path(path))
    except Exception as e:  # noqa: BLE001 - unreadable file or share not reachable
        return [(p, "error", f"{type(e).__name__}: {e}"[:300]) for p in pages]
    out = []
    with doc:
        if doc.needs_pass:
            return [(p, "error", "password-protected PDF") for p in pages]
        for p in pages:
            try:
                page = doc[p - 1]
                tp = page.get_textpage_ocr(language=options["languages"], dpi=ocr_dpi(page.rect, options["dpi"]),
                                           full=True, tessdata=options["tessdata"])
                text = filter_text(page.get_text("text", textpage=tp))
                out.append((p, "text" if len(text) >= MIN_OCR_CHARS else "empty", text))
            except Exception as e:  # noqa: BLE001
                out.append((p, "error", f"{type(e).__name__}: {e}"[:300]))
    return out


def _delete_chunks(con, ids: list[int]) -> bool:
    """Remove chunks (with their FTS entries and vectors). True if vectors were removed."""
    if not ids:
        return False
    marks = ",".join("?" * len(ids))
    rows = con.execute(f"SELECT id, text FROM chunks WHERE id IN ({marks})", ids).fetchall()
    con.executemany("INSERT INTO chunks_fts(chunks_fts, rowid, text) VALUES('delete', ?, ?)",
                    [(r[0], r[1]) for r in rows])
    cur = con.execute(f"DELETE FROM vectors WHERE chunk_id IN ({marks})", ids)
    con.execute(f"DELETE FROM chunks WHERE id IN ({marks})", ids)
    return cur.rowcount > 0


def save_page(con, doc_id: int, page_no: int, status: str, text: str) -> bool:
    """Record one page read by OCR and append its text as chunks. Replaces only the chunks a
    previous OCR of this page added. Returns True if vectors were removed."""
    old = con.execute("SELECT chunk_ids FROM ocr_pages WHERE doc_id = ? AND page_no = ?",
                      (doc_id, page_no)).fetchone()
    removed = _delete_chunks(con, [int(i) for i in (old[0] or "").split(",") if i]) if old else False
    ids: list[int] = []
    if status == "text":
        count, last_seq = con.execute("SELECT count(*), max(seq) FROM chunks WHERE doc_id = ? AND page_no = ?",
                                      (doc_id, page_no)).fetchone()
        body = ("\n\n" if count else "") + text  # keeps the page rebuildable from its chunks
        for k, part in enumerate(split_text(body)):
            cur = con.execute("INSERT INTO chunks(doc_id, page_no, seq, text) VALUES(?, ?, ?, ?)",
                              (doc_id, page_no, (last_seq + 1 if count else 0) + k, part))
            con.execute("INSERT INTO chunks_fts(rowid, text) VALUES(?, ?)", (cur.lastrowid, part))
            ids.append(cur.lastrowid)
        con.execute("UPDATE docs SET status = 'ok', error = NULL WHERE id = ? AND status = 'no_text'", (doc_id,))
    con.execute("""INSERT OR REPLACE INTO ocr_pages(doc_id, page_no, status, chars, ocr_version, done_at,
                   chunk_ids, error) VALUES(?, ?, ?, ?, ?, ?, ?, ?)""",
                (doc_id, page_no, status, len(text) if status == "text" else 0, OCR_VERSION, time.time(),
                 ",".join(map(str, ids)) or None, text if status in ("error", "timeout") else None))
    return removed


def unavailable(cfg) -> str | None:
    """Why OCR cannot run for this configuration, or None."""
    if not cfg.ocr_enabled:
        return "OCR is disabled for this world ([ocr] enabled = false or the world's ocr = false)."
    missing = [f"{lang}.traineddata" for lang in cfg.ocr_languages.split("+")
               if not (cfg.tessdata_dir / f"{lang}.traineddata").is_file()]
    if missing:
        return f"OCR not available: {', '.join(missing)} not found in {cfg.tessdata_dir}."
    return None


@dataclass
class OcrStats:
    pages: int = 0
    text: int = 0
    empty: int = 0
    failed: int = 0

    def summary(self) -> str:
        return (f"{self.pages} pages read: {self.text} with text, {self.empty} without usable text "
                f"(photos, drawings), {self.failed} failed")


def _worker_init(log_path: str) -> None:
    """Runs first in every OCR worker process (a module-level function: spawn pickles it by name).
    Tesseract writes warnings ('Line cannot be recognized!!') to file descriptor 2, tens of thousands
    of them on a run over drawings: they go to a log file instead of the console and the shared log.
    If that file cannot be opened they are discarded."""
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        target = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND)
    except OSError:
        try:
            target = os.open(os.devnull, os.O_WRONLY)
        except OSError:
            return
    try:
        os.dup2(target, 2)
    except OSError:
        pass
    finally:
        os.close(target)


def _failed(job: Job, status: str, message: str) -> list[tuple[int, str, str]]:
    return [(p, status, message) for p in job.pages]


def _read_alone(job: Job, options: dict, log_path: str) -> list[tuple[int, str, str]]:
    """Read a job in a process of its own, giving up after STALL_S. A job that was in flight when a
    worker crashed is recorded as an error only if it crashes (or hangs) alone too."""
    from .indexer import _MP, _terminate_pool

    solo = ProcessPoolExecutor(max_workers=1, mp_context=_MP, initializer=_worker_init, initargs=(log_path,))
    try:
        try:
            fut = solo.submit(read_pages, job.path, job.pages, options)
        except BrokenProcessPool:
            return _failed(job, "error", "OCR worker crashed")
        deadline = time.perf_counter() + STALL_S
        while True:
            done, _ = wait([fut], timeout=1)  # short steps: Ctrl+C stays responsive
            if done:
                try:
                    return fut.result()
                except BrokenProcessPool:
                    return _failed(job, "error", "OCR worker crashed")
                except Exception as e:  # noqa: BLE001
                    return _failed(job, "error", f"{type(e).__name__}: {e}"[:300])
            if time.perf_counter() > deadline:
                return _failed(job, "timeout", f"no result within {STALL_S // 60} min")
    finally:
        _terminate_pool(solo)


def run_ocr(cfg, max_minutes: float | None = None, retry: bool = False, progress=print) -> OcrStats:
    from .indexer import _MP, _terminate_pool

    stats = OcrStats()
    why = unavailable(cfg)
    if why:
        progress(why)
        return stats
    con = store.connect(cfg.db_path)
    con.isolation_level = None  # explicit transactions, one per job
    queue = candidates(con, cfg.max_pdf_pages, retry)
    total = sum(len(j.pages) for j in queue)
    if not queue:
        progress("No page needs OCR.")
        con.close()
        return stats
    workers = max(1, int(cfg.workers))
    progress(f"{total} pages to read in {len({j.doc_id for j in queue})} documents "
             f"({workers} workers, language {cfg.ocr_languages}, {cfg.ocr_dpi} dpi).")
    omp_before = os.environ.get("OMP_THREAD_LIMIT")
    os.environ["OMP_THREAD_LIMIT"] = "1"  # one Tesseract thread per worker process
    options = {"languages": cfg.ocr_languages, "dpi": cfg.ocr_dpi, "tessdata": str(cfg.tessdata_dir)}
    # perf_counter, not monotonic: on Windows monotonic ticks every 15.6 ms, too coarse for a tiny limit
    deadline = time.perf_counter() + max_minutes * 60 if max_minutes else None

    log_path = str(cfg.logs_dir / TESSERACT_LOG)

    def new_pool() -> ProcessPoolExecutor:
        return ProcessPoolExecutor(max_workers=workers, mp_context=_MP,
                                   initializer=_worker_init, initargs=(log_path,))

    def save(job: Job, results) -> None:
        con.execute("BEGIN IMMEDIATE")  # the write lock at once: waits for another writer (busy_timeout)
        try:
            removed = False
            for page_no, status, text in results:
                removed |= save_page(con, job.doc_id, page_no, status, text)
                stats.pages += 1
                if status == "text":
                    stats.text += 1
                elif status == "empty":
                    stats.empty += 1
                else:
                    stats.failed += 1
                    log.info("OCR %s: %s p.%d (%s)", status, job.path, page_no, text)
            if removed:
                store.bump_vector_version(con)
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise

    executor = new_pool()
    pending: dict = {}
    suspects: list[Job] = []  # in flight when a worker crashed: any of them may be the culprit
    started = last_progress = last_done = time.perf_counter()
    failed_in_a_row = 0  # jobs in a row whose pages ALL came back as errors (the share is gone?)
    last_error = ""

    def collect(fut) -> None:
        nonlocal failed_in_a_row, last_error
        job = pending.pop(fut)
        try:
            results = fut.result()
        except BrokenProcessPool:
            suspects.append(job)  # the culprit and its healthy neighbours fail alike: read each alone later
            return
        except Exception as e:  # noqa: BLE001
            results = _failed(job, "error", f"{type(e).__name__}: {e}"[:300])
        save(job, results)
        if results and all(status == "error" for _, status, _ in results):
            failed_in_a_row += 1
            last_error = results[-1][2]
        else:
            failed_in_a_row = 0

    def too_many_failures() -> bool:
        return failed_in_a_row >= MAX_FAILED_JOBS_IN_A_ROW

    def replace_broken_pool() -> None:
        nonlocal executor
        done, left = wait(list(pending), timeout=30)  # the other jobs of a broken pool fail the same way
        for fut in done:
            collect(fut)
        for fut in left:
            suspects.append(pending.pop(fut))
        _terminate_pool(executor)
        executor = new_pool()

    try:
        while queue or pending:
            if getattr(executor, "_broken", False):
                replace_broken_pool()
            # At most one job per worker: every pending job is really running, none waits behind another.
            # After too many failed jobs in a row nothing new is submitted: what is in flight is recorded.
            while (queue and len(pending) < workers and not too_many_failures()
                   and not (deadline and time.perf_counter() > deadline)):
                job = queue.pop(0)
                try:
                    fut = executor.submit(read_pages, job.path, job.pages, options)
                except BrokenProcessPool:  # a worker died just now
                    replace_broken_pool()
                    fut = executor.submit(read_pages, job.path, job.pages, options)
                pending[fut] = job
            if not pending:
                break  # time limit reached, or stopped after too many failed jobs
            done, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
            for fut in done:
                collect(fut)
            if done:
                last_done = time.perf_counter()
            elif time.perf_counter() - last_done > STALL_S:
                progress(f"WARNING: no OCR job finished for {STALL_S // 60} min; restarting the workers. "
                         "Probably stuck: " + "; ".join(j.path for j in pending.values()))
                for job in pending.values():
                    save(job, _failed(job, "timeout", f"no result within {STALL_S // 60} min"))
                pending.clear()
                _terminate_pool(executor)
                executor = new_pool()
                last_done = time.perf_counter()
            now = time.perf_counter()
            if now - last_progress >= PROGRESS_EVERY_S:
                rate = stats.pages / max(now - started, 1e-6)
                progress(f"  {stats.pages}/{total} pages ({rate:.1f}/s, "
                         f"~{(total - stats.pages) / max(rate, 1e-6) / 3600:.1f} h remaining)")
                last_progress = now
        if suspects and not too_many_failures():
            progress(f"A worker crashed: the {len(suspects)} jobs it may have been reading are read again "
                     f"one by one ({STALL_S // 60} min max each).")
            log.warning("worker crash, read again alone: %s", "; ".join(j.path for j in suspects))
        while suspects and not too_many_failures() and not (deadline and time.perf_counter() > deadline):
            job = suspects.pop(0)
            progress(f"  reading alone: {job.path}")
            save(job, _read_alone(job, options, log_path))
    except KeyboardInterrupt:
        progress("Interrupted - the pages read so far are saved.")
        raise
    finally:
        _terminate_pool(executor)
        con.close()
        if omp_before is None:
            os.environ.pop("OMP_THREAD_LIMIT", None)
        else:
            os.environ["OMP_THREAD_LIMIT"] = omp_before
    left = queue + suspects
    if left and too_many_failures():
        progress(f"{MAX_FAILED_JOBS_IN_A_ROW} jobs in a row failed (last error: {last_error}). Stopped: check the "
                 "file share / language files, then run again; the failed pages can be read again with --retry.")
    elif left:
        progress(f"Time limit reached: {sum(len(j.pages) for j in left)} pages left for the next run.")
    progress(f"OCR done in {(time.perf_counter() - started) / 60:.1f} min: {stats.summary()}")
    if stats.failed:
        launcher = f"dmx.ps1 ocr -World {cfg.world} --retry" if cfg.world else "dmx.ps1 ocr --retry"
        progress(f"{stats.failed} pages failed or timed out: read them again with `dmx-docs ocr --retry` "
                 f"(launcher: {launcher})")
    return stats

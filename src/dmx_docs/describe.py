"""Picture descriptions by a local vision model (sub-project B2).

A separate step between `ocr` and `embed`. It finds documents whose pictures were not handled
with the configured model and DESCRIBE_VERSION, lists their picture occurrences in worker
processes (pictures.py), describes each distinct picture once through Ollama (vision.py; cached
by SHA-1 in picture_cache) and APPENDS one passage (chunk) per occurrence, like OCR: existing
chunks and vectors are never touched, only the new chunks need embedding. Occurrences are
recorded in picture_uses, finished documents in picture_docs (store.py), which makes the step
resumable and lets the tools label picture passages.
"""

from __future__ import annotations

import base64
import html
import json
import logging
import os
import random
import sqlite3
import statistics
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, ThreadPoolExecutor, wait
from concurrent.futures.process import BrokenProcessPool
from dataclasses import dataclass

from . import store, vision
from .extract import PICTURE_EXTS
from .pictures import Prepared, prepare_document

log = logging.getLogger("dmx_docs.describe")

DOC_EXTS = (".md", ".docx", ".pptx", ".ppt", ".pdf") + PICTURE_EXTS
PROGRESS_EVERY_S = 15
STALL_S = 600                 # a document whose pictures are not ready after this long is stuck
MAX_FAILED_CALLS_IN_A_ROW = 20
MAX_PREP_WORKERS = 4          # preparing pictures is light; the GPU is the bottleneck
PAGE_TEXT_CHARS = 2000        # indexed text per page passed to the workers (context uses 800)


def unavailable(cfg) -> str | None:
    """Why pictures cannot be described now, or None."""
    if not cfg.vision_enabled:
        return "Picture descriptions are off for this world ([vision] enabled = false or the world's vision = false)."
    return vision.unavailable(cfg.vision_url, cfg.vision_model)


def prep_options(cfg) -> dict:
    return {"roots": list(cfg.roots), "excluded_dirs": list(cfg.excluded_dirs), "min_side": cfg.vision_min_side,
            "pdf_picture_share": cfg.vision_pdf_picture_share, "max_pdf_pages": cfg.max_pdf_pages,
            "doc_converter": cfg.doc_converter, "libreoffice_path": cfg.libreoffice_path}


def candidates(con, cfg, retry: bool = False) -> list[int]:
    """Documents to handle: never handled with this model and DESCRIBE_VERSION, or (retry) with
    occurrences that failed or timed out."""
    marks = ",".join("?" * len(DOC_EXTS))
    ids = [r[0] for r in con.execute(
        f"""SELECT d.id FROM docs d WHERE d.ext IN ({marks}) AND d.status IN ('ok', 'no_text')
            AND NOT EXISTS (SELECT 1 FROM picture_docs p WHERE p.doc_id = d.id AND p.model = ? AND p.version = ?)
            ORDER BY d.id""", (*DOC_EXTS, cfg.vision_model, vision.DESCRIBE_VERSION))]
    if retry:
        seen = set(ids)
        ids += [r[0] for r in con.execute(
            "SELECT DISTINCT doc_id FROM picture_uses WHERE status IN ('error', 'timeout') ORDER BY doc_id")
            if r[0] not in seen]
    return ids


def job_for(con, doc_id: int) -> dict | None:
    """What a worker needs to prepare one document (no database access in the workers); None if
    the document is gone."""
    d = con.execute("SELECT id, path, ext, title FROM docs WHERE id = ?", (doc_id,)).fetchone()
    if d is None:
        return None
    category = None
    try:
        row = con.execute("SELECT category FROM doc_facets WHERE doc_id = ?", (doc_id,)).fetchone()
        category = row[0] if row else None
    except sqlite3.OperationalError:  # no facets table (yet), or one from before the category column
        pass
    ocr_ids, ocr_chars = set(), {}
    for page, chars, ids in con.execute("SELECT page_no, chars, chunk_ids FROM ocr_pages WHERE doc_id = ?", (doc_id,)):
        ocr_chars[page] = chars or 0
        ocr_ids.update(int(i) for i in (ids or "").split(",") if i)
    picture_ids = {r[0] for r in con.execute(
        "SELECT chunk_id FROM picture_uses WHERE doc_id = ? AND chunk_id IS NOT NULL", (doc_id,))}
    pages: dict[int, str] = {}
    native: dict[int, int] = {}
    for cid, page, text in con.execute("SELECT id, page_no, text FROM chunks WHERE doc_id = ? ORDER BY page_no, seq",
                                       (doc_id,)):
        if cid in picture_ids:
            continue
        if len(pages.get(page, "")) < PAGE_TEXT_CHARS:
            pages[page] = (pages.get(page, "") + " " + text).strip()[:PAGE_TEXT_CHARS]
        if cid not in ocr_ids:
            native[page] = native.get(page, 0) + len(text)
    return {"doc_id": d[0], "path": d[1], "ext": d[2], "title": d[3], "category": category,
            "pages": pages, "native_chars": native, "ocr_chars": ocr_chars}


def marker(image_no: int) -> str:
    """Start of a passage: 'Image 3' (the [Image 3] marker of a Markdown page, the 3rd picture of a
    Word file or of a slide) or 'Page entière' (a whole PDF page)."""
    return "Page entière" if image_no == 0 else f"Image {image_no}"


def _cached(con, cfg, sha1s) -> dict[str, vision.Description]:
    sha1s = list(sha1s)
    out = {}
    for i in range(0, len(sha1s), 500):
        part = sha1s[i:i + 500]
        for sha1, kind, text, visible, keywords in con.execute(
                f"""SELECT sha1, type, description, visible_text, keywords FROM picture_cache
                    WHERE model = ? AND version = ? AND sha1 IN ({','.join('?' * len(part))})""",
                (cfg.vision_model, vision.DESCRIBE_VERSION, *part)):
            out[sha1] = vision.Description(kind, text, visible or "", json.loads(keywords or "[]"))
    return out


def save_document(con, cfg, job: dict, prepared: list[Prepared], described: dict) -> bool:
    """Record one document: new cache rows, its occurrences and their passages (replacing the
    passages of an earlier run), and its picture_docs row. `described` maps sha1 to a Description
    or to the VisionError of its last try. The caller owns the transaction. True if vectors were removed."""
    doc_id = job["doc_id"]
    now = time.time()
    for sha1, d in described.items():
        if isinstance(d, vision.Description) and d.seconds:  # new from the model (cached ones have 0 s)
            con.execute("""INSERT OR REPLACE INTO picture_cache(sha1, model, version, type, description, visible_text,
                           keywords, seconds, created_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (sha1, cfg.vision_model, vision.DESCRIBE_VERSION, d.type, d.description, d.visible_text,
                         json.dumps(d.keywords, ensure_ascii=False), d.seconds, now))
    old = [r[0] for r in con.execute("SELECT chunk_id FROM picture_uses WHERE doc_id = ? AND chunk_id IS NOT NULL",
                                     (doc_id,))]
    removed = store.delete_chunks(con, old)
    con.execute("DELETE FROM picture_uses WHERE doc_id = ?", (doc_id,))
    file_name = os.path.basename(job["path"]) if job["ext"] in PICTURE_EXTS else None
    added = False
    for p in prepared:
        status, kind, chunk_id, error = p.status, None, None, p.error
        if p.status == "ready":
            d = described.get(p.sha1)
            if isinstance(d, vision.Description):
                kind = d.type
                if d.type == vision.LOGO:
                    status = "logo"
                else:
                    status = "described"
                    count, last_seq = con.execute("SELECT count(*), max(seq) FROM chunks WHERE doc_id = ? AND page_no = ?",
                                                  (doc_id, p.page_no)).fetchone()
                    text = ("\n\n" if count else "") + vision.passage_text(d, marker(p.image_no), file_name)
                    cur = con.execute("INSERT INTO chunks(doc_id, page_no, seq, text) VALUES(?, ?, ?, ?)",
                                      (doc_id, p.page_no, (last_seq + 1) if count else 0, text))
                    con.execute("INSERT INTO chunks_fts(rowid, text) VALUES(?, ?)", (cur.lastrowid, text))
                    chunk_id = cur.lastrowid
                    added = True
            else:
                status = "timeout" if getattr(d, "timeout", False) else "error"
                error = str(d) if d is not None else "not described"
        con.execute("""INSERT OR REPLACE INTO picture_uses(doc_id, page_no, image_no, sha1, status, type, chunk_id,
                       error, done_at) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (doc_id, p.page_no, p.image_no, p.sha1, status, kind, chunk_id, error, now))
    if added:
        con.execute("UPDATE docs SET status = 'ok', error = NULL WHERE id = ? AND status = 'no_text'", (doc_id,))
    con.execute("INSERT OR REPLACE INTO picture_docs(doc_id, model, version, pictures, done_at) VALUES(?, ?, ?, ?, ?)",
                (doc_id, cfg.vision_model, vision.DESCRIBE_VERSION, len(prepared), now))
    return removed


@dataclass
class DescribeStats:
    documents: int = 0
    described: int = 0   # model calls that gave a description
    reused: int = 0      # occurrences served from the cache (or shared with another occurrence)
    small: int = 0
    logos: int = 0
    failed: int = 0

    def summary(self) -> str:
        return (f"{self.documents} documents: {self.described} pictures described by the model, {self.reused} reused "
                f"from earlier descriptions, {self.small} too small, {self.logos} logos/decoration, {self.failed} failed")


def _call(cfg, image: bytes, context: str) -> vision.Description:
    """One description, tried twice (a timeout or an invalid answer is often a one-off)."""
    try:
        return vision.describe_image(cfg.vision_url, cfg.vision_model, image, context, cfg.vision_timeout_s)
    except vision.VisionError:
        return vision.describe_image(cfg.vision_url, cfg.vision_model, image, context, cfg.vision_timeout_s)


def run_describe(cfg, max_minutes: float | None = None, retry: bool = False, progress=print) -> DescribeStats:
    from .indexer import _MP, _terminate_pool

    stats = DescribeStats()
    why = unavailable(cfg)
    if why:
        progress(why)
        progress("Picture descriptions skipped.")
        return stats
    con = store.connect(cfg.db_path)
    con.isolation_level = None  # explicit transactions, one per document
    queue = candidates(con, cfg, retry)
    if not queue:
        progress("No picture to describe.")
        con.close()
        return stats
    total = len(queue)
    workers = max(1, min(MAX_PREP_WORKERS, int(cfg.workers)))
    progress(f"{total} documents to examine for pictures ({workers} workers, model {cfg.vision_model}).")
    options = prep_options(cfg)
    deadline = time.perf_counter() + max_minutes * 60 if max_minutes else None
    calls = ThreadPoolExecutor(max_workers=max(1, int(cfg.vision_parallel_requests)))
    failed_in_a_row = 0
    last_error = ""
    # Pictures that failed in this run are not sent again for the next documents that use them (the FR, DE
    # and EN pages of the manual share theirs: a slow picture would cost two timeouts per page); --retry does.
    failed_here: dict[str, vision.VisionError] = {}

    def new_pool() -> ProcessPoolExecutor:
        return ProcessPoolExecutor(max_workers=workers, mp_context=_MP)

    def save(job: dict, prepared: list[Prepared], described: dict) -> None:
        con.execute("BEGIN IMMEDIATE")
        try:
            if save_document(con, cfg, job, prepared, described):
                store.bump_vector_version(con)
            con.execute("COMMIT")
        except BaseException:
            con.execute("ROLLBACK")
            raise
        stats.documents += 1

    def handle(job: dict, prepared: list[Prepared]) -> None:
        nonlocal failed_in_a_row, last_error
        ready = [p for p in prepared if p.status == "ready"]
        described: dict = _cached(con, cfg, {p.sha1 for p in ready})
        todo = {}
        for p in ready:
            if p.sha1 in failed_here:
                described[p.sha1] = failed_here[p.sha1]
            elif p.sha1 not in described and p.sha1 not in todo:
                todo[p.sha1] = p
        futures = {sha1: calls.submit(_call, cfg, p.image, p.context) for sha1, p in todo.items()}
        for sha1, fut in futures.items():
            try:
                described[sha1] = fut.result()
                stats.described += 1
                failed_in_a_row = 0
            except vision.VisionError as e:
                described[sha1] = failed_here[sha1] = e
                failed_in_a_row += 1
                last_error = str(e)
                log.info("describe failed: %s (%s)", job["path"], e)
        sent: set[str] = set()   # the first occurrence of each picture sent to the model
        for p in prepared:
            if p.status == "small":
                stats.small += 1
            elif p.status == "error":
                stats.failed += 1
                log.info("picture error: %s p.%d image %d (%s)", job["path"], p.page_no, p.image_no, p.error)
            else:
                d = described.get(p.sha1)
                if not isinstance(d, vision.Description):
                    stats.failed += 1
                elif d.type == vision.LOGO:
                    stats.logos += 1
                elif p.sha1 in futures and p.sha1 not in sent:
                    sent.add(p.sha1)
                else:
                    stats.reused += 1   # from the cache, or a second occurrence of a picture of this document
        save(job, prepared, described)

    def too_many_failures() -> bool:
        return failed_in_a_row >= MAX_FAILED_CALLS_IN_A_ROW

    def doc_failed(job: dict, status: str, message: str) -> None:
        """The whole document failed (worker crash, hang): one occurrence (0, 0); --retry redoes it."""
        save(job, [Prepared(0, 0, status, error=message)], {})
        stats.failed += 1

    executor = new_pool()
    pending: dict = {}   # future -> (job, submitted at)
    started = last_progress = time.perf_counter()
    try:
        while queue or pending:
            while (queue and len(pending) < workers and not too_many_failures()
                   and not (deadline and time.perf_counter() > deadline)):
                job = job_for(con, queue.pop(0))
                if job is None:
                    continue  # deleted since the list was made
                try:
                    fut = executor.submit(prepare_document, job, options)
                except BrokenProcessPool:
                    _terminate_pool(executor)
                    executor = new_pool()
                    fut = executor.submit(prepare_document, job, options)
                pending[fut] = (job, time.perf_counter())
            if not pending:
                break  # time limit reached, or stopped after too many failed calls
            done, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
            broken = False
            for fut in done:
                job, _ = pending.pop(fut)
                try:
                    prepared = fut.result()
                except BrokenProcessPool:
                    broken = True
                    doc_failed(job, "error", "picture preparation crashed (this document or one prepared at the same time)")
                    continue
                except Exception as e:  # noqa: BLE001
                    prepared = [Prepared(0, 0, "error", error=f"{type(e).__name__}: {e}"[:300])]
                handle(job, prepared)
            now = time.perf_counter()
            stuck = [f for f, (_, t) in pending.items() if not f.done() and now - t > STALL_S]
            if broken or stuck:
                for f in list(pending):
                    job, _ = pending.pop(f)
                    if f in stuck:
                        doc_failed(job, "timeout", f"pictures not ready within {STALL_S // 60} min")
                    else:
                        doc_failed(job, "error", "picture preparation crashed (this document or one prepared "
                                                 "at the same time)")
                _terminate_pool(executor)
                executor = new_pool()
            if now - last_progress >= PROGRESS_EVERY_S:
                rate = stats.documents / max(now - started, 1e-6)
                progress(f"  {stats.documents}/{total} documents, {stats.described} pictures described "
                         f"({stats.described / max((now - started) / 60, 1e-6):.0f}/min), "
                         f"~{(total - stats.documents) / max(rate, 1e-6) / 3600:.1f} h remaining")
                last_progress = now
    except KeyboardInterrupt:
        progress("Interrupted - the documents handled so far are saved.")
        raise
    finally:
        _terminate_pool(executor)
        calls.shutdown(wait=False, cancel_futures=True)
        con.close()
        vision.unload(cfg.vision_url, cfg.vision_model)
    if too_many_failures():
        progress(f"{MAX_FAILED_CALLS_IN_A_ROW} calls in a row failed (last error: {last_error}). Stopped: check "
                 "Ollama, then run again; the failed pictures can be described again with --retry.")
    elif queue:
        progress(f"Time limit reached: {len(queue)} documents left for the next run.")
    progress(f"Pictures done in {(time.perf_counter() - started) / 60:.1f} min: {stats.summary()}")
    if stats.failed:
        launcher = f"dmx.ps1 describe -World {cfg.world} --retry" if cfg.world else "dmx.ps1 describe --retry"
        progress(f"{stats.failed} pictures failed: describe them again with `dmx-docs describe --retry` "
                 f"(launcher: {launcher})")
    return stats


def run_compare(cfg, models: list[str], sample: int = 25, out_dir: str | None = None, progress=print) -> str | None:
    """Describe a fixed sample of the world's pictures with each model and write a side-by-side HTML
    page (thumbnail, context, each model's answer and seconds). Read-only for the index."""
    from .pictures import scale_picture

    con = store.connect(cfg.db_path)
    try:
        ids = [r[0] for r in con.execute(
            f"SELECT id FROM docs WHERE ext IN ({','.join('?' * len(DOC_EXTS))}) AND status IN ('ok', 'no_text')",
            DOC_EXTS)]
        random.Random(7).shuffle(ids)
        options, items, seen = prep_options(cfg), [], set()
        for doc_id in ids:
            if len(items) >= sample:
                break
            job = job_for(con, doc_id)
            if job is None:
                continue
            ready = [p for p in prepare_document(job, options) if p.status == "ready" and p.sha1 not in seen]
            unique = list({p.sha1: p for p in ready}.values())
            for p in random.Random(doc_id).sample(unique, min(2, len(unique))):   # at most 2 per document
                seen.add(p.sha1)
                items.append((job["path"], p))
    finally:
        con.close()
    items = items[:sample]
    if not items:
        progress("No picture to compare in this world.")
        return None
    progress(f"{len(items)} pictures sampled; models: {', '.join(models)}.")
    results: dict[str, list] = {}
    for model in models:
        why = vision.unavailable(cfg.vision_url, model)
        if why:
            progress(why)
            results[model] = [why] * len(items)
            continue
        results[model] = []
        for i, (path, p) in enumerate(items, 1):
            try:
                d = vision.describe_image(cfg.vision_url, model, p.image, p.context, cfg.vision_timeout_s)
                results[model].append(d)
                progress(f"  {model} {i}/{len(items)} {d.seconds:.1f} s  {d.type}: {d.description[:80]}")
            except vision.VisionError as e:
                results[model].append(str(e))
                progress(f"  {model} {i}/{len(items)} failed: {e}")
        vision.unload(cfg.vision_url, model)
    out_dir = out_dir or str(cfg.logs_dir)
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"describe-compare-{cfg.world or 'index'}-{time.strftime('%Y%m%d-%H%M')}.html")
    thumbs = [base64.b64encode(scale_picture(p.image, 300)[0]).decode("ascii") for _, p in items]
    with open(path, "w", encoding="utf-8") as f:
        f.write(_compare_html(cfg, models, items, thumbs, results))
    progress(f"Written: {path}")
    return path


def _compare_html(cfg, models, items, thumbs, results) -> str:
    e = html.escape
    summary = []
    for m in models:
        secs = [r.seconds for r in results[m] if isinstance(r, vision.Description)]
        failed = sum(not isinstance(r, vision.Description) for r in results[m])
        if secs:
            med = statistics.median(secs)
            summary.append(f"<li><b>{e(m)}</b>: median {med:.1f} s per picture, {failed} failed; "
                           f"1,000 pictures ≈ {1000 * med / 3600:.1f} h</li>")
        else:
            summary.append(f"<li><b>{e(m)}</b>: no description, {failed} failed</li>")
    rows = []
    for i, ((path, p), thumb) in enumerate(zip(items, thumbs)):
        mime = "image/png" if base64.b64decode(thumb)[:4] == b"\x89PNG" else "image/jpeg"
        where = f"page {p.page_no}" + (f", image {p.image_no}" if p.image_no else " (whole page)")
        cells = [f"<td class=pic><img src='data:{mime};base64,{thumb}'><div class=src>{e(path)} — {where}</div>"
                 f"<details><summary>context</summary><pre>{e(p.context)}</pre></details></td>"]
        for m in models:
            r = results[m][i]
            if isinstance(r, vision.Description):
                cells.append(f"<td><b>{e(r.type)}</b> · {r.seconds:.1f} s<p>{e(r.description)}</p>"
                             f"<p class=vis>Texte visible : {e(r.visible_text or '—')}</p>"
                             f"<p class=kw>{e(', '.join(r.keywords))}</p></td>")
            else:
                cells.append(f"<td class=err>{e(str(r))}</td>")
        rows.append(f"<tr><td class=n>{i + 1}</td>{''.join(cells)}</tr>")
    head = "".join(f"<th>{e(m)}</th>" for m in models)
    return f"""<!doctype html><html><head><meta charset=utf-8><title>Vision models</title><style>
body{{font:14px Segoe UI,Arial,sans-serif;margin:16px;background:#fff;color:#222}}
table{{border-collapse:collapse;width:100%}} td,th{{border:1px solid #ccc;vertical-align:top;padding:8px}}
th{{background:#f3f3f3;position:sticky;top:0}} td.pic{{width:300px}} td.pic img{{max-width:300px;max-height:260px}}
.src{{font-size:11px;color:#666;word-break:break-all}} pre{{white-space:pre-wrap;font-size:11px}}
.vis{{color:#555}} .kw{{color:#06c;font-size:12px}} .err{{color:#b00}} td.n{{width:24px}}
</style></head><body><h1>Vision models — {e(cfg.world_title or cfg.world or 'index')} — {time.strftime('%Y-%m-%d %H:%M')}</h1>
<ul>{''.join(summary)}</ul><table><tr><th>#</th><th>picture</th>{head}</tr>{''.join(rows)}</table></body></html>"""

"""OneNote sections (.one): page lists and page text from OneNote's hierarchy XML and page XML,
and the reading of a section through OneNote desktop (COM) in its own pass of the index run.

OneNote writes into every file it opens, so it is only ever given a copy, in a temporary folder
opened as a notebook (a folder can be closed again by script, a section file alone cannot).
The MCP server never reads a section: it serves the index.
XML elements are matched by local name so that any schema year works.
"""
from __future__ import annotations

import html
import logging
import multiprocessing
import os
import re
import shutil
import subprocess
import sys
import time
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ProcessPoolExecutor, wait
from dataclasses import dataclass

from .extract import Extracted, clean_text, kill_office_automation
from .store import fs_path

log = logging.getLogger("dmx_docs.onenote")

LOAD_TIMEOUT_S = 120     # OneNote loads a section in the background: wait at most this long for its pages
POLL_S = 2
MAX_TIMEOUTS_IN_ROW = 5  # this many sections in a row not finished: OneNote is stuck, stop the pass
NOT_INSTALLED = "OneNote desktop is not installed on this machine"
SERVER_ONLY = "OneNote sections are read by the index run only"
_HS_PAGES = 4            # HierarchyScope.hsPages
_SECTION_LOCKED = (0x8004201B, 0x8004201B - (1 << 32))  # hrSectionEncryptedAndLocked, unsigned and signed

_TAG = re.compile(r"<[^>]*>")
_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)


@dataclass
class PageRef:
    id: str
    name: str
    level: int
    created: str
    modified: str


def _local(el: ET.Element) -> str:
    tag = el.tag
    return tag.rsplit("}", 1)[-1] if isinstance(tag, str) else ""


def section_pages(hierarchy_xml: str, section_name: str | None = None) -> list[PageRef]:
    root = ET.fromstring(hierarchy_xml)
    sections = [e for e in root.iter() if _local(e) == "Section"]
    chosen = None
    for s in sections:
        if section_name is None or s.get("name") == section_name:
            chosen = s
            break
    if chosen is None:
        return []
    pages = []
    for e in chosen.iter():
        if _local(e) == "Page":
            try:
                level = int(e.get("pageLevel") or 1)
            except ValueError:
                level = 1
            pages.append(PageRef(e.get("ID", ""), e.get("name", ""), level,
                                 e.get("dateTime", ""), e.get("lastModifiedTime", "")))
    return pages


def page_titles(pages: list[PageRef]) -> list[str]:
    titles: list[str] = []
    parents: list[tuple[int, str]] = []  # (level, computed title) of earlier pages
    for p in pages:
        title = p.name
        if p.level >= 2:
            for lvl, full in reversed(parents):
                if lvl < p.level:
                    title = f"{full} > {p.name}"
                    break
        titles.append(title)
        parents.append((p.level, title))
    return titles


def _plain(fragment: str) -> str:
    text = _BR.sub("\n", fragment or "")
    text = _TAG.sub("", text)
    return html.unescape(text).replace("\xa0", " ").strip()


def page_text(page_xml: str, title: str, created: str, modified: str) -> str:
    root = ET.fromstring(page_xml)
    lines: list[str] = []
    files: list[str] = []
    pictures: list[str] = []

    def table_rows(table: ET.Element) -> list[str]:
        rows = []
        for row in table:
            if _local(row) != "Row":
                continue
            cells = []
            for c in row:
                if _local(c) == "Cell":
                    parts: list[str] = []
                    collect(c, parts, True)
                    cells.append(" ".join(parts))
            if any(cells):
                rows.append(" | ".join(cells))
        return rows

    def collect(el: ET.Element, out: list[str], nested: bool) -> None:
        for child in el:
            name = _local(child)
            if name == "Title":
                continue
            if name == "Table":
                rows = table_rows(child)
                if rows:
                    if nested:
                        out.append(" / ".join(rows))
                    else:
                        out.extend(rows)
            elif name == "T":
                t = _plain(child.text or "")
                if t:
                    out.append(t)
            elif name == "OCRText":
                t = _plain(child.text or "")
                if t:
                    pictures.append(t)
            elif name == "InsertedFile":
                n = child.get("preferredName")
                if n:
                    files.append(n)
            else:
                collect(child, out, nested)

    collect(root, lines, False)
    out = [title, f"Créée le {created[:10]}, modifiée le {modified[:10]}"]
    out.extend(lines)
    if files:
        out.append("Fichiers joints : " + ", ".join(files))
    if pictures:
        out.append("[Texte des images]")
        out.extend(pictures)
    return clean_text("\n".join(out))


# ------------------------------------------------------------ OneNote itself

def installed() -> bool:
    """The ProgID OneNote.Application is registered (OneNote desktop)."""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        winreg.CloseKey(winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, "OneNote.Application"))
        return True
    except OSError:
        return False


def running() -> bool:
    """A OneNote process exists (the user's own, or one started for automation)."""
    if sys.platform != "win32":
        return False
    out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq ONENOTE.EXE", "/NH"],
                         capture_output=True, text=True, timeout=60).stdout
    return "onenote.exe" in out.lower()


def section_limit_s(size_bytes: int) -> float:
    """Time allowed for one section: 5 min, plus 1 min per 50 MB."""
    return 300 + 60 * size_bytes / (50 * 1024 * 1024)


def _wait_for_pages(app, nb: str, name: str, load_timeout_s: float, poll_s: float) -> list[PageRef]:
    """Pages of the section once OneNote lists the same ones twice in a row; whatever it lists at
    the time limit (a section still without a page is empty)."""
    deadline = time.monotonic() + load_timeout_s
    last = None
    while True:
        pages = section_pages(app.GetHierarchy(nb, _HS_PAGES, ""), name)
        ids = [p.id for p in pages]
        if (ids and ids == last) or time.monotonic() >= deadline:
            return pages
        last = ids
        time.sleep(poll_s)


def _protected(e: Exception) -> bool:
    return ("password" in str(e).lower()
            or bool(getattr(e, "args", None)) and e.args[0] in _SECTION_LOCKED)


def read_section(path: str, tmp_root: str, app=None, load_timeout_s: float = LOAD_TIMEOUT_S,
                 poll_s: float = POLL_S) -> Extracted:
    """Read a section page by page through OneNote, from a copy in a new folder under tmp_root."""
    name = os.path.splitext(os.path.basename(path))[0]
    tmp = os.path.join(tmp_root, uuid.uuid4().hex)
    nb = None
    try:
        os.makedirs(tmp)
        shutil.copy2(fs_path(path), os.path.join(tmp, os.path.basename(path)))
        if app is None:
            import pythoncom
            import win32com.client

            pythoncom.CoInitialize()
            app = win32com.client.Dispatch("OneNote.Application")
        nb = app.OpenHierarchy(tmp, "", "", 0)  # the copy's folder, never `path`
        pages = _wait_for_pages(app, nb, name, load_timeout_s, poll_s)
        if not pages:
            return Extracted(status="empty", n_pages=0, title=name)
        out = []
        for n, (page, title) in enumerate(zip(pages, page_titles(pages)), 1):
            out.append((n, page_text(app.GetPageContent(page.id, "", 0), title, page.created, page.modified)))
        return Extracted(status="ok", pages=out, n_pages=len(out), title=name)
    except Exception as e:  # noqa: BLE001
        if _protected(e):
            return Extracted(status="error", error="OneNote section protected by a password")
        return Extracted(status="error", error=f"{type(e).__name__}: {e}"[:500])
    finally:
        if nb is not None:
            try:
                app.CloseNotebook(nb)
            except Exception as e:  # noqa: BLE001
                log.warning("could not close the OneNote notebook %s: %s", tmp, e)
        shutil.rmtree(tmp, ignore_errors=True)


def _read_in_process(path: str, tmp_root: str, limit_s: float) -> Extracted:
    """read_section in a fresh process; TimeoutError after limit_s (the process is then ended)."""
    pool = ProcessPoolExecutor(max_workers=1, mp_context=multiprocessing.get_context("spawn"))
    fut = pool.submit(read_section, path, tmp_root)
    deadline = time.monotonic() + limit_s
    try:
        while not fut.done():
            wait([fut], timeout=1)  # short steps: Ctrl+C stays responsive
            if not fut.done() and time.monotonic() > deadline:
                raise TimeoutError(f"OneNote did not finish {path} in {limit_s:.0f} s")
        try:
            return fut.result()
        except Exception as e:  # noqa: BLE001  (the worker process died)
            return Extracted(status="error", error=f"OneNote reading crashed: {type(e).__name__}")
    finally:
        if fut.done():
            pool.shutdown(wait=True)
        else:
            for proc in list((getattr(pool, "_processes", None) or {}).values()):
                try:
                    proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
            pool.shutdown(wait=False, cancel_futures=True)


def _end_automation_onenote() -> None:
    try:
        kill_office_automation("ONENOTE.EXE")
    except Exception as e:  # noqa: BLE001
        log.warning("could not stop OneNote: %s", e)


def run_pass(cfg, items: list[tuple[str, int, float, bool]], save, progress, check_stop, read=None) -> None:
    """Read the sections `items` ((path, size, mtime, existed)) one at a time and save each result.
    Sections left when the pass stops early are not saved: the next run takes them again."""
    if not items:
        return
    tmp_root = str(cfg.world_dir / "onenote-tmp")
    os.makedirs(tmp_root, exist_ok=True)
    for entry in os.scandir(tmp_root):  # left by a run that was killed
        if entry.is_dir(follow_symlinks=False):
            shutil.rmtree(entry.path, ignore_errors=True)
    if not installed():
        for path, size, mtime, existed in items:
            save(path, size, mtime, Extracted(status="skipped", error=NOT_INSTALLED), existed)
        progress(f"  OneNote: {len(items)} sections skipped ({NOT_INSTALLED})")
        return
    read = read or _read_in_process  # looked up at call time
    was_running = running()
    timeouts = 0
    try:
        for i, (path, size, mtime, existed) in enumerate(items):
            check_stop()
            progress(f"  OneNote: {path}")
            limit = section_limit_s(size)
            try:
                result = read(path, tmp_root, limit)
            except TimeoutError:
                timeouts += 1
                save(path, size, mtime, Extracted(
                    status="error", error=f"timeout: OneNote did not finish in {limit:.0f} s"), existed)
                left = len(items) - i - 1
                if was_running:
                    progress(f"WARNING: OneNote did not finish {path}. OneNote was already open (the user's "
                             f"own), so it is left alone; the {left} remaining sections are left for the "
                             "next run.")
                    return
                _end_automation_onenote()
                if timeouts >= MAX_TIMEOUTS_IN_ROW:
                    progress(f"WARNING: {timeouts} OneNote sections in a row did not finish; the {left} "
                             "remaining sections are left for the next run.")
                    return
                continue
            timeouts = 0
            save(path, size, mtime, result, existed)
    finally:
        if not was_running:
            _end_automation_onenote()

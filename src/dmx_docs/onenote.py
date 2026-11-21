"""OneNote sections (.one): page lists and page text from OneNote's hierarchy XML and page XML,
and the reading of a section through OneNote desktop (COM) in its own pass of the index run.

OneNote writes into every file it opens, so it is only ever given a copy, in a temporary folder
opened as a notebook (a folder can be closed again by script, a section file alone cannot).
OneNote is driven through Windows PowerShell, not pywin32: with a 32-bit Office, whose type library
is registered for 32-bit callers only, OneNote refuses pywin32's calls from a 64-bit Python
("Library not registered") while PowerShell's COM binding works.
The MCP server never reads a section: it serves the index.
XML elements are matched by local name so that any schema year works.
"""
from __future__ import annotations

import base64
import html
import json
import logging
import multiprocessing
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
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
CALL_TIMEOUT_S = 120     # one PowerShell call to OneNote
CLOSE_TIMEOUT_S = 60     # closing the notebooks left by a reading that was ended
_HS_NOTEBOOKS = 2        # HierarchyScope.hsNotebooks
_HS_PAGES = 4            # HierarchyScope.hsPages
_SECTION_LOCKED = "8004201b"  # hrSectionEncryptedAndLocked

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


# OneNote's session, in one PowerShell process: OneNote loads a notebook in the background only
# while a client holds it, and quits when the last one lets go (measured: one powershell.exe per call
# left the section unloaded after 120 s). Fixed text, never built from arguments: each request is a
# line of base64 (ASCII) of a UTF-8 JSON object on stdin; the result or the error goes to a UTF-8 file
# named in the request (stdout would depend on the code page); stdout only answers "ok" or "err".
_PS_SESSION = r"""
$ErrorActionPreference = 'Stop'
$utf8 = New-Object System.Text.UTF8Encoding $false
$on = New-Object -ComObject OneNote.Application
while ($true) {
    $line = [Console]::In.ReadLine()
    if ([string]::IsNullOrEmpty($line)) { break }
    $r = [Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($line)) | ConvertFrom-Json
    try {
        $x = ''
        switch ($r.op) {
            'OpenHierarchy'  { $on.OpenHierarchy($r.a, '', [ref]$x, [int]$r.b) }
            'GetHierarchy'   { $on.GetHierarchy($r.a, [int]$r.b, [ref]$x) }
            'GetPageContent' { $on.GetPageContent($r.a, [ref]$x, [int]$r.b) }
            'CloseNotebook'  { $on.CloseNotebook($r.a) }
            default          { throw "unknown operation $($r.op)" }
        }
        [IO.File]::WriteAllText($r.out, [string]$x, $utf8)
        [Console]::Out.WriteLine('ok')
    } catch {
        [IO.File]::WriteAllText($r.err, $_.Exception.Message, $utf8)
        [Console]::Out.WriteLine('err')
    }
    [Console]::Out.Flush()
}
"""
_PS_ENCODED = base64.b64encode(_PS_SESSION.encode("utf-16-le")).decode("ascii")


def _read_utf8(path: str) -> str:
    try:
        with open(path, encoding="utf-8-sig") as f:
            return f.read()
    except OSError:
        return ""


class PowerShellOneNote:
    """The four OneNote methods used here, served by one `powershell.exe` that holds OneNote until
    `close()`. `work_dir` holds the result files of the calls (each removed after its call)."""

    command = ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", _PS_ENCODED]

    def __init__(self, work_dir: str, timeout_s: float = CALL_TIMEOUT_S):
        self.work_dir, self.timeout_s = work_dir, timeout_s
        self._proc = None
        self._lines: queue.Queue | None = None  # output of the current process only

    @staticmethod
    def _pump(stream, lines: queue.Queue) -> None:
        for raw in stream:
            lines.put(raw.decode("utf-8", "replace").strip())
        lines.put(None)

    def _call(self, op: str, a: str, b: int | str = "") -> str:
        if self._proc is None:
            self._proc = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                          stderr=subprocess.STDOUT,
                                          creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            # A new queue per process: a killed process's last lines and end marker must not be read
            # as the answers of the next one.
            self._lines = queue.Queue()
            threading.Thread(target=self._pump, args=(self._proc.stdout, self._lines), daemon=True).start()
        base = os.path.join(self.work_dir, uuid.uuid4().hex)
        out, err = base + ".out", base + ".err"
        request = json.dumps({"op": op, "a": a, "b": str(b), "out": out, "err": err}).encode("utf-8")
        noise: list[str] = []  # anything else PowerShell printed (its errors)
        try:
            try:
                self._proc.stdin.write(base64.b64encode(request) + b"\n")
                self._proc.stdin.flush()
            except OSError:
                pass  # PowerShell has ended: its output says why
            deadline = time.monotonic() + self.timeout_s
            while True:
                try:
                    line = self._lines.get(timeout=max(0.0, deadline - time.monotonic()))
                except queue.Empty:
                    self.close(kill=True)
                    raise TimeoutError(f"OneNote {op} did not answer in {self.timeout_s:.0f} s") from None
                if line == "ok":
                    return _read_utf8(out)
                if line == "err":
                    raise RuntimeError(f"OneNote {op}: {_read_utf8(err).strip()}")
                if line is None:
                    self.close()
                    raise RuntimeError(f"OneNote {op}: PowerShell ended: " + " ".join(noise)[-400:])
                if line:
                    noise.append(line)
        finally:
            for f in (out, err):
                try:
                    os.remove(f)
                except OSError:
                    pass

    def close(self, kill: bool = False) -> None:
        """End the PowerShell process, which lets go of OneNote (kill: it is stuck in a call)."""
        proc, self._proc = self._proc, None
        if proc is None:
            return
        try:
            if kill:
                proc.kill()
            else:
                proc.stdin.close()  # the end of the input ends its loop
            proc.wait(timeout=30)
        except Exception:  # noqa: BLE001
            proc.kill()

    def OpenHierarchy(self, path: str, parent: str, obj_id: str, create: int) -> str:  # parent: always ""
        return self._call("OpenHierarchy", path, create)

    def GetHierarchy(self, node: str, scope: int, xml: str) -> str:
        return self._call("GetHierarchy", node, scope)

    def GetPageContent(self, page_id: str, xml: str, info: int) -> str:
        return self._call("GetPageContent", page_id, info)

    def CloseNotebook(self, node: str) -> None:
        self._call("CloseNotebook", node)


def _protected(e: Exception) -> bool:
    text = str(e).lower()
    return "password" in text or _SECTION_LOCKED in text


def read_section(path: str, tmp_root: str, app=None, load_timeout_s: float = LOAD_TIMEOUT_S,
                 poll_s: float = POLL_S) -> Extracted:
    """Read a section page by page through OneNote, from a copy in a new folder under tmp_root."""
    name = os.path.splitext(os.path.basename(path))[0]
    tmp = os.path.join(tmp_root, uuid.uuid4().hex)
    nb = None
    own = app is None
    stuck = False
    try:
        os.makedirs(tmp)
        shutil.copy2(fs_path(path), os.path.join(tmp, os.path.basename(path)))
        if own:
            app = PowerShellOneNote(tmp_root)
        nb = app.OpenHierarchy(tmp, "", "", 0)  # the copy's folder, never `path`
        pages = _wait_for_pages(app, nb, name, load_timeout_s, poll_s)
        if not pages:
            return Extracted(status="empty", n_pages=0, title=name)
        out = []
        for n, (page, title) in enumerate(zip(pages, page_titles(pages)), 1):
            out.append((n, page_text(app.GetPageContent(page.id, "", 0), title, page.created, page.modified)))
        return Extracted(status="ok", pages=out, n_pages=len(out), title=name)
    except TimeoutError:
        # OneNote is stuck: a timeout of the section for run_pass (counted, OneNote dealt with there),
        # not an error of this section. Closing the notebook would wait on the same stuck OneNote.
        stuck = True
        raise
    except Exception as e:  # noqa: BLE001
        if _protected(e):
            return Extracted(status="error", error="OneNote section protected by a password")
        return Extracted(status="error", error=f"{type(e).__name__}: {e}"[:500])
    finally:
        if nb is not None and not stuck:
            try:
                app.CloseNotebook(nb)
            except Exception as e:  # noqa: BLE001
                log.warning("could not close the OneNote notebook %s: %s", tmp, e)
        if own and app is not None:
            app.close()
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
                raise TimeoutError(f"OneNote did not finish in {limit_s:.0f} s")
        try:
            return fut.result()
        except TimeoutError:  # a OneNote call did not answer (read_section re-raises it)
            raise
        except Exception as e:  # noqa: BLE001  (the worker process died)
            return Extracted(status="error", error=f"OneNote reading crashed: {type(e).__name__}")
    finally:
        if fut.done():
            pool.shutdown(wait=True)
        else:
            for proc in list((getattr(pool, "_processes", None) or {}).values()):
                try:
                    if sys.platform == "win32":  # with its powershell.exe call, which would wait on OneNote
                        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                                       capture_output=True, timeout=60)
                    proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
            pool.shutdown(wait=False, cancel_futures=True)


def _close_left_notebooks(tmp_root: str, app=None) -> int:
    """Best effort: close the notebooks OneNote still has open from tmp_root (left by a reading that
    was ended), so that they do not stay in the user's list of notebooks. Returns how many."""
    root = os.path.normcase(os.path.abspath(tmp_root)).rstrip("\\/")
    closed = 0
    own = app is None
    try:
        if own:
            app = PowerShellOneNote(tmp_root, timeout_s=CLOSE_TIMEOUT_S)
        for e in ET.fromstring(app.GetHierarchy("", _HS_NOTEBOOKS, "")).iter():
            nb_path = os.path.normcase(os.path.abspath(e.get("path") or "")) if _local(e) == "Notebook" else ""
            if nb_path.startswith(root + os.sep):
                try:
                    app.CloseNotebook(e.get("ID", ""))
                    closed += 1
                except Exception as ex:  # noqa: BLE001
                    log.warning("could not close the OneNote notebook %s: %s", nb_path, ex)
    except Exception as ex:  # noqa: BLE001
        log.warning("could not list the notebooks open in OneNote: %s", ex)
    finally:
        if own and app is not None:
            app.close()
    return closed


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
    left_over = list(os.scandir(tmp_root))  # left by a run that was killed
    if left_over and installed():
        _close_left_notebooks(tmp_root)  # OneNote keeps their files open
    for entry in left_over:
        if entry.is_dir(follow_symlinks=False):
            shutil.rmtree(entry.path, ignore_errors=True)
        else:
            try:
                os.remove(entry.path)
            except OSError:
                pass
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
            except TimeoutError as e:  # the section's limit, or one OneNote call that did not answer
                timeouts += 1
                save(path, size, mtime, Extracted(
                    status="error", error=f"timeout: {str(e) or f'OneNote did not finish in {limit:.0f} s'}"), existed)
                left = len(items) - i - 1
                _close_left_notebooks(tmp_root)  # the ended reading could not close its notebook
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

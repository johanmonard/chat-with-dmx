"""Tool logic exposed to Claude (kept separate from MCP wiring so it is easy to test).

Every tool is read-only and limited to the configured root folders.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime

from . import facets, knowledge, machines, offer_tools, onenote, register, sources, store, verified, worldmap
from .access import CURRENT_CALLER, Access, AccessPool, AccessStore, Caller, Unrestricted, readable_clause
from .config import Config
from .extract import PICTURE_EXTS, docx_picture_names, extract_file
from .search import Searcher, build_fts_query, fold, make_snippet

TYPE_LABEL = {".pdf": "PDF", ".docx": "Word", ".doc": "Word 97-2003",
              ".pptx": "PowerPoint", ".ppt": "PowerPoint 97-2003", ".md": "Markdown", ".txt": "Text",
              ".xlsx": "Excel", ".xlsm": "Excel", ".xls": "Excel 97-2003", ".one": "OneNote"}
TYPE_LABEL.update(dict.fromkeys(PICTURE_EXTS, "Picture"))
SLIDE_EXTS = (".pptx", ".ppt")
EXCEL_EXTS = (".xlsx", ".xlsm", ".xls")
EXCEL_NO_IMAGE = "Excel sheets cannot be shown as an image: open the workbook with open_document."
ONENOTE_NO_IMAGE = "OneNote pages cannot be shown as an image: open the section with open_document."

# Query/passage cosine similarity with multilingual-e5-large, calibrated on the indexed
# documents (short and long queries): the best hit of on-topic queries scores 0.84-0.89,
# of off-topic ones 0.77-0.83; a relevant passage that is not the best hit can score 0.83.
CALIBRATED_MODEL = "intfloat/multilingual-e5-large"
SIM_STRONG = 0.86
SIM_MEDIUM = 0.83

LISTED_CALLERS = 256  # offers world: callers whose "To verify" list is remembered


_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


def _nocase(text: str) -> str:
    """`text` folded as SQLite's COLLATE NOCASE compares it (ASCII letters only)."""
    return text.translate(_ASCII_LOWER)


def _strength(sim: float) -> str:
    return "strong" if sim >= SIM_STRONG else "medium" if sim >= SIM_MEDIUM else "weak"


VIEW_MAX_PX = 1568          # long edge sent to Claude (larger images are downscaled anyway)
VIEW_MAX_DPI = 300          # zoomed regions are rendered at up to this resolution
VIEW_MAX_BYTES = 1_500_000  # above this, PNG is replaced by JPEG


def _encode(pix) -> tuple[bytes, str]:
    data = pix.tobytes("png")  # sharp for drawings, schematics and text
    if len(data) > VIEW_MAX_BYTES:
        data = pix.tobytes("jpeg", jpg_quality=80)  # photos
        return data, "jpeg"
    return data, "png"


def _parse_region(region: str | None):
    if not region:
        return None
    try:
        x0, y0, x1, y1 = (float(v) for v in region.replace(";", ",").split(","))
    except ValueError:
        raise ValueError("region must be 'x0,y0,x1,y1' as fractions of the page, e.g. '0.5,0,1,0.5' "
                         "for the top-right quarter") from None
    x0, x1 = sorted((min(max(x0, 0.0), 1.0), min(max(x1, 0.0), 1.0)))
    y0, y1 = sorted((min(max(y0, 0.0), 1.0), min(max(y1, 0.0), 1.0)))
    if x1 - x0 < 0.02 or y1 - y0 < 0.02:
        raise ValueError("region is too small")
    return x0, y0, x1, y1


def _render_pdf_page(fs: str, path: str, page: int, region: str | None,
                     max_px: int = VIEW_MAX_PX) -> tuple[str, bytes, str]:
    from .extract import pymupdf

    frac = _parse_region(region)
    with pymupdf.open(fs) as doc:
        n = doc.page_count
        if not 1 <= page <= n:
            raise ValueError(f"page must be between 1 and {n}")
        p = doc[page - 1]
        r = p.rect
        clip = r if frac is None else pymupdf.Rect(r.x0 + frac[0] * r.width, r.y0 + frac[1] * r.height,
                                                   r.x0 + frac[2] * r.width, r.y0 + frac[3] * r.height)
        zoom = min(max_px / max(clip.width, clip.height), VIEW_MAX_DPI / 72)
        pix = p.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=False)
    data, fmt = _encode(pix)
    where = "whole page" if frac is None else f"region {region}"
    caption = (f"{path} — page {page}/{n}, {where}, {pix.width}x{pix.height} px ({round(zoom * 72)} dpi). "
               "To read small details, call again with region='x0,y0,x1,y1' (fractions of the page, "
               "e.g. '0,0,0.5,0.5' = top-left quarter).")
    return caption, data, fmt


def _default_app(ext: str) -> str | None:
    """Executable registered for a file extension on Windows (None if unknown)."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    size = wintypes.DWORD(1024)
    buf = ctypes.create_unicode_buffer(size.value)
    ASSOCSTR_EXECUTABLE = 2
    if ctypes.windll.shlwapi.AssocQueryStringW(0, ASSOCSTR_EXECUTABLE, ext, None, buf, ctypes.byref(size)) != 0:
        return None
    return buf.value or None


def _launch(args=None, path: str | None = None, verb: str = "open") -> None:
    """Start a program, or open a file with its associated application (replaced in tests)."""
    if args:
        subprocess.Popen(args, close_fds=True)
    elif sys.platform == "win32":
        os.startfile(path, verb)
    else:  # pragma: no cover
        subprocess.Popen(["xdg-open", path])


def _open_file(path: str, ext: str, page: int | None) -> str:
    if ext == ".pdf" and page and page > 1:
        app = _default_app(".pdf") or ""
        name = os.path.basename(app).lower()
        if name in ("acrobat.exe", "acrord32.exe", "acrord64.exe"):
            _launch([app, "/A", f"page={int(page)}", path])
            return f"at page {page}"
        if name in ("msedge.exe", "chrome.exe", "firefox.exe"):
            url = "file:" + urllib.request.pathname2url(path) + f"#page={int(page)}"
            _launch([app, url])
            return f"at page {page}"
        _launch(path=path)
        return f"(the default PDF viewer cannot jump to a page: go to page {page})"
    if ext in (".docx", ".doc"):
        try:
            _launch(path=path, verb="OpenAsReadOnly")  # verb registered by Microsoft Word
            return "read-only" + (f" (Word cannot jump to a page: go to page ~{page})" if page else "")
        except OSError:
            pass
    _launch(path=path)
    return "" if not page or page == 1 else f"(go to page {page})"


EXPORT_MAX_PX = 2400  # long edge of exported PDF renderings (sharp on a full-screen slide)


def default_export_dir(cfg: Config) -> str:
    """Next to the local index (C:\\dmx-rag\\exports with the shared-folder setup): a local
    folder the user can grant to a Claude Desktop workspace to build slides from."""
    return os.path.join(os.path.dirname(str(cfg.data_dir).rstrip("\\/")), "exports")


def _safe_name(s: str, limit: int = 60) -> str:
    s = re.sub(r"[^\w\-]+", "_", s, flags=re.UNICODE).strip("_")
    return s[:limit] or "image"


def _docx_picture_raw(fs: str, image: int | None) -> tuple[bytes, str, int, int]:
    """Original bytes of a picture embedded in a .docx: (data, extension, number, total)."""
    import zipfile

    with zipfile.ZipFile(fs) as z:
        media = [n for n in z.namelist() if n.startswith("word/media/")]
        shown = docx_picture_names(z)
        if not shown:
            raise ValueError("No picture that can be shown in this file"
                             + (f" ({len(media)} vector drawings only)" if media else ""))
        k = 1 if image is None else int(image)
        if not 1 <= k <= len(shown):
            raise ValueError(f"image must be between 1 and {len(shown)}")
        return z.read(shown[k - 1]), os.path.splitext(shown[k - 1])[1].lower(), k, len(shown)


def _scaled_picture(raw: bytes, ext: str) -> tuple[bytes, str, int, int]:
    """A picture file, scaled down for Claude when larger than VIEW_MAX_PX: (data, format, w, h)."""
    from .extract import pymupdf

    with pymupdf.open(stream=raw, filetype=ext.lstrip(".")) as img:
        p = img[0]
        zoom = min(1.0, VIEW_MAX_PX / max(p.rect.width, p.rect.height))
        pix = p.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    data, fmt = _encode(pix)
    return data, fmt, pix.width, pix.height


def _docx_picture(fs: str, path: str, image: int | None) -> tuple[str, bytes, str]:
    raw, ext, k, total = _docx_picture_raw(fs, image)
    data, fmt, w, h = _scaled_picture(raw, ext)
    caption = (f"{path} — picture {k} of {total} embedded in the Word file (document order; Word "
               f"files have no fixed pages), {w}x{h} px. Use image=N for the others.")
    return caption, data, fmt


def _slide_picture_raw(fs: str, path: str, slide: int, image: int | None,
                       options: dict) -> tuple[bytes, str, int, int, int]:
    """Original bytes of a picture on a PowerPoint slide: (data, extension, number, pictures on
    the slide, slides). Old .ppt files are converted first (LibreOffice or PowerPoint)."""
    import tempfile

    from .extract import convert_ppt, pptx_pictures

    with tempfile.TemporaryDirectory(prefix="dmx_view_") as tmp:
        src = fs
        if path.lower().endswith(".ppt"):
            src = convert_ppt(path, tmp, options.get("doc_converter", "auto"), options.get("libreoffice_path"))
        slides = pptx_pictures(src)
    n = len(slides)
    if not 1 <= slide <= n:
        raise ValueError(f"page (slide number) must be between 1 and {n}")
    pictures = slides[slide - 1]
    if not pictures:
        others = [str(i + 1) for i, p in enumerate(slides) if p]
        raise ValueError(f"Slide {slide} has no picture that can be shown. " + (
            f"Slides with pictures: {', '.join(others[:50])}" if others else "This file has no pictures."))
    k = 1 if image is None else int(image)
    if not 1 <= k <= len(pictures):
        raise ValueError(f"image must be between 1 and {len(pictures)} (pictures on slide {slide})")
    data, ext = pictures[k - 1]
    return data, ext, k, len(pictures), n


def _md_link_candidates(target: str, md_path: str, roots: list[str]) -> list[str]:
    """Where a Markdown picture link may point to, most likely first. The machine manual uses
    absolute drive paths (O:/ASA/.../Source/...): through this PC's drive mapping, then rebuilt
    from the indexed root sharing the longest folder tail with the link (at least 2 folder names
    when the root has them; drives and server spellings differ between PCs)."""
    import urllib.parse

    t = urllib.parse.unquote(target.strip()).replace("/", "\\")
    if re.match(r"^[A-Za-z]:\\", t):
        candidates = [sources.to_unc(t)]
        low = t.lower()
        rebased = []
        for root in roots:
            parts = [p for p in root.rstrip("\\").split("\\") if p]
            for n in range(min(3, len(parts)), min(2, len(parts)) - 1, -1):
                tail = "\\" + "\\".join(parts[-n:]).lower() + "\\"
                i = low.find(tail)
                if i >= 0:
                    rebased.append((n, root.rstrip("\\") + "\\" + t[i + len(tail):]))
                    break
        return candidates + [path for _, path in sorted(rebased, key=lambda x: -x[0])]
    if t.startswith("\\\\"):
        return [t]
    return [os.path.normpath(os.path.join(os.path.dirname(md_path), t))]


def resolve_md_link(target: str, md_path: str, roots: list[str], allows=None) -> str | None:
    """The file a Markdown picture link points to, or None: the first candidate that is inside
    the indexed folders (checked before touching the file system, so a link never makes this PC
    contact another server) and exists."""
    for c in _md_link_candidates(target, md_path, roots):
        if (allows is None or allows(store.path_key(c))) and os.path.isfile(store.fs_path(c)):
            return c
    return None


def _md_picture_raw(fs: str, path: str, image: int | None, cfg: Config,
                    allows=None) -> tuple[bytes, str, int, int, str]:
    """Original bytes of the image-th picture linked from a Markdown file:
    (data, extension, number, pictures in the file, caption). `allows` (default cfg.allows) says
    which pictures the caller may see: one it may not is answered as one that does not exist."""
    from .extract import PICTURE_EXTS, md_pictures

    with open(fs, "rb") as f:
        pictures = md_pictures(f.read().decode("utf-8-sig", errors="replace"))
    if not pictures:
        raise ValueError("No picture in this Markdown file")
    k = 1 if image is None else int(image)
    if not 1 <= k <= len(pictures):
        raise ValueError(f"image must be between 1 and {len(pictures)}")
    caption, target = pictures[k - 1]
    found = resolve_md_link(target, path, cfg.roots, allows=allows or cfg.allows)
    if found is None:  # "outside" is the world's own rule, so a hidden picture reads "not found"
        candidates = _md_link_candidates(target, path, cfg.roots)
        if candidates and not any(cfg.allows(store.path_key(c)) for c in candidates):
            raise ValueError(f"Picture {k} is outside the indexed folders: {target}")
        raise FileNotFoundError(f"Picture {k} of {path} not found: {target}")
    ext = os.path.splitext(found)[1].lower()
    if ext not in PICTURE_EXTS:
        raise ValueError(f"Picture {k} cannot be shown ({ext or 'no extension'}): {found}")
    with open(store.fs_path(found), "rb") as f:
        return f.read(), ext, k, len(pictures), caption


def _date(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "?"


def _size(n: int | None) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def _page_label(ext: str) -> str:
    if ext in SLIDE_EXTS:
        return "slide"
    if ext in EXCEL_EXTS:
        return "sheet"
    return "page" if ext in (".pdf", ".md", ".txt", ".one") or ext in PICTURE_EXTS else "page (approx.)"


def _units(ext: str) -> str:
    return "slides" if ext in SLIDE_EXTS else "sheets" if ext in EXCEL_EXTS else "pages"


def _has_ocr_table(con) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ocr_pages'").fetchone() is not None


def _ocr_text_pages(con, doc_ids) -> set[tuple[int, int]]:
    """(doc_id, page_no) of pages whose text comes from OCR. Empty for indexes without OCR."""
    ids = list(doc_ids)
    if not ids or not _has_ocr_table(con):
        return set()
    return {(r[0], r[1]) for r in con.execute(
        f"SELECT doc_id, page_no FROM ocr_pages WHERE status = 'text' AND doc_id IN ({','.join('?' * len(ids))})", ids)}


def _picture_chunks(con, chunk_ids) -> dict[int, tuple[int, int]]:
    """chunk_id -> (page, picture number; 0 = whole page) of passages written from pictures."""
    ids = list(chunk_ids)
    if not ids or not store.has_table(con, "picture_uses"):
        return {}
    return {r[0]: (r[1], r[2]) for r in con.execute(
        f"SELECT chunk_id, page_no, image_no FROM picture_uses WHERE chunk_id IN ({','.join('?' * len(ids))})", ids)}


def _has_descriptions(con) -> bool:
    """True if the describe step added at least one passage (the tables alone exist in every index
    opened by this version)."""
    return store.has_table(con, "picture_uses") and con.execute(
        "SELECT 1 FROM picture_uses WHERE chunk_id IS NOT NULL LIMIT 1").fetchone() is not None


def _picture_label(ext: str, page: int, image: int) -> str:
    if image == 0:
        return f" — whole-page picture (view_page page={page})"
    where = f"page={page} image={image}" if ext in SLIDE_EXTS else f"image={image}"
    return f" — picture {image} (view_page {where})"


def _picture_pages(con, doc_id: int) -> list[int]:
    if not store.has_table(con, "picture_uses"):
        return []
    return [r[0] for r in con.execute(
        "SELECT DISTINCT page_no FROM picture_uses WHERE doc_id = ? AND chunk_id IS NOT NULL ORDER BY page_no", (doc_id,))]


class DocTools:
    def __init__(self, cfg: Config, caller: Caller | None = None):
        self.cfg = cfg
        # Per-person access: a world with access = "world" shows everything to everybody. The fixed
        # Access (the given caller, else the process's identity) answers when no request caller is
        # set (a local server); a central server sets CURRENT_CALLER and gets that caller's Access.
        if cfg.access == "world":
            self._local_access, self._pool = Unrestricted(), None
        else:
            store_ = AccessStore(cfg.access_path)
            self._local_access = Access(store_, caller or Caller.from_token(cfg.access_admins))
            self._pool = AccessPool(store_)
        self.searcher = Searcher(cfg)
        # world_map's live part, cached: (time, token of the readable folders it was counted for, overview)
        self._overview: tuple[float, object, worldmap.Overview] | None = None
        self.knowledge = knowledge.Knowledge(cfg.knowledge_path)  # written project cards
        # offers world: revisions listed under "To verify", per caller (sids, None in local mode) ->
        # (identity -> time): what one person was shown never hides a revision from another.
        self._listed: dict[frozenset | None, dict[tuple, float]] = {}
        self._listed_lock = threading.Lock()

    @property
    def access(self) -> Access | Unrestricted:
        """What the caller of this request may read: the request's caller (CURRENT_CALLER) when one
        is set, else the fixed caller of a local server."""
        caller = CURRENT_CALLER.get()
        if caller is None or self._pool is None:
            return self._local_access
        return self._pool.for_caller(caller)

    def allows(self, key: str) -> bool:
        """Is the document (or folder) with this path key inside the world and readable by the caller?"""
        return self.cfg.allows(key) and self.access.can_read(key)

    def _readable_sql(self, con, column: str = "d.folder_key") -> str | None:
        """SQL condition on `column` (a folder key) keeping the caller's readable documents;
        None when the caller reads everything (world access, admins)."""
        return readable_clause(con, self.access.folder_predicate(), column)

    def _readable_ids(self, con) -> str | None:
        """Name of a TEMP table holding the ids of the documents the caller may read (None when the
        caller reads everything). Filled from one scan of docs, once per connection and snapshot of
        the rules: a subquery in each statement would ask the predicate again for every document,
        each time the statement runs."""
        pred = self.access.folder_predicate()
        if pred is None:
            return None
        token = getattr(pred, "token", None)
        con.execute("CREATE TEMP TABLE IF NOT EXISTS readable_docs(id INTEGER PRIMARY KEY)")
        con.execute("CREATE TEMP TABLE IF NOT EXISTS readable_docs_token(token TEXT)")
        row = con.execute("SELECT token FROM temp.readable_docs_token").fetchone()
        if token is None or row is None or row[0] != token:
            con.execute("DELETE FROM temp.readable_docs")
            con.execute("DELETE FROM temp.readable_docs_token")
            cond = readable_clause(con, pred, "folder_key")
            con.execute(f"INSERT INTO temp.readable_docs(id) SELECT id FROM docs WHERE {cond}")
            if token is not None:
                con.execute("INSERT INTO temp.readable_docs_token(token) VALUES (?)", (token,))
            # No commit: the TEMP rows are this connection's own (closing it drops them), and a
            # commit here could end someone else's pending writes at an unexpected point.
        return "temp.readable_docs"

    def _readable_facets(self, con) -> str:
        """' AND doc_id IN <readable ids>' keeping the doc_facets rows of readable documents; '' when
        the caller reads everything (the query is then unchanged)."""
        table = self._readable_ids(con)
        return f" AND doc_id IN {table}" if table else ""

    @property
    def _restricted(self) -> bool:
        """Does the caller read less than the whole world (access = "acl" and not an admin)?"""
        return self.access.folder_predicate() is not None

    def _project_reader(self, con):
        """Function project name -> can the caller read at least one indexed document of that project
        or sub-project? The machine register and the card citations that are not documents
        ([register], [user, date]) are shown on that condition. Always True for those who read everything.
        Every name is collected with one query over the caller's readable documents."""
        if not self._restricted:
            return lambda name: True
        table = self._readable_ids(con)
        readable: set[str] = set()  # names folded as SQLite's NOCASE folds them (ASCII letters only)
        for project, subproject, pk in con.execute(
                "SELECT f.project, f.subproject, d.path_key FROM doc_facets f JOIN docs d ON d.id = f.doc_id "
                f"WHERE f.doc_id IN {table} AND (f.project IS NOT NULL OR f.subproject IS NOT NULL)"):
            names = [_nocase(n) for n in (project, subproject) if n and _nocase(n) not in readable]
            if names and self.allows(pk):  # checked again one by one, as in list_projects' safety net
                readable.update(names)

        def can_read(name: str | None) -> bool:
            # no name: no indexed document at all, none the caller can read
            return bool(name) and _nocase(name) in readable
        return can_read

    def _card_viewer(self, con, project_ok=None):
        """Function card -> (body, sourced lines left out, whole) as the caller may see it: nothing
        of the card of a project the caller reads no document of; else a cited document - an indexed
        one, exactly - by its own rights, a note ([register], [user, date]) by those on the project,
        anything else not at all (text after a file name, a file not indexed...)."""
        if not self._restricted:
            return lambda card: (card.body, 0, True)
        project_ok = project_ok or self._project_reader(con)
        indexed: dict[str, bool] = {}  # path key -> is it an indexed document (one lookup per key)

        def document_ok(source: str) -> bool:
            if not knowledge.DOCUMENT_SOURCE.match(source) or not knowledge.single_path(source):
                return False
            try:
                key = store.path_key(sources.clean_path(source))
            except ValueError:
                return False
            if key not in indexed:
                indexed[key] = con.execute("SELECT 1 FROM docs WHERE path_key = ?", (key,)).fetchone() is not None
            return indexed[key] and self.allows(key)

        def view(card):
            if not project_ok(card.project):
                return "", 0, False

            def can_read(source: str) -> bool:
                if knowledge.NOTE_SOURCE.fullmatch(source):
                    return True  # the caller reads a document of the card's project (checked above)
                return document_ok(source)  # neither a note nor an indexed document: not readable
            return knowledge.visible_card(card, can_read)
        return view

    def _is_file(self, path: str, key: str) -> bool:
        """Does the file exist for this caller? A document the caller may not read does not, so
        every tool answers for it exactly as for a missing file (and never reads it live)."""
        return self.access.can_read(key) and os.path.isfile(store.fs_path(path))

    def _con(self):
        con = store.connect(self.cfg.db_path, create=False)
        # Root/excluded folders may have changed on the configuration page.
        sources.refresh(self.cfg, con)
        facets.refresh(con, self.cfg.roots, self.cfg.profile)  # no-op when every document has its facets
        if self.cfg.profile == "projects":  # the machine register belongs to the project documentation
            register.refresh(con, self.cfg.register_path)  # no-op when neither the file nor the documents changed
        return con

    def _resolve(self, path: str) -> tuple[str, str]:
        """(path, key) of a path inside the world. Whether the caller may read it is not said
        here: the tools treat a document the caller may not read as a missing file (_is_file)."""
        path = sources.clean_path(path)  # also turns a mapped drive (N:\...) into the UNC path
        key = store.path_key(path)
        if not self.cfg.allows(key):
            raise ValueError(f"'{path}' is outside the indexed folders (roots: {', '.join(self.cfg.roots)}; "
                             "some subfolders may be excluded)")
        return path, key

    def _resolve_folder(self, con, folder: str) -> tuple[str, str]:
        """Like _resolve, but also accepts a folder given by name only ("MAURIUS") or relative to a
        root ("RMA_PROJETS\\2_Hors_Garantie\\X", "2_Hors_Garantie\\X"): the shallowest indexed
        folder with that name."""
        try:
            return self._resolve(folder)
        except ValueError:
            if re.match(r"^([a-zA-Z]:|[\\/]{2})", folder.strip()):
                raise  # a full path outside the indexed folders
        rel = folder.strip().strip("\\/").replace("/", "\\")
        readable = self._readable_sql(con, "folder_key")  # a name known only from hidden documents is unknown
        readable = f" AND {readable}" if readable else ""
        for root in self.cfg.roots:
            base = root.rstrip("\\/").split("\\")[-1]
            tail = rel[len(base):].lstrip("\\") if rel.lower() == base.lower() or rel.lower().startswith(
                base.lower() + "\\") else rel
            cand = os.path.join(root, tail) if tail else root
            key = store.path_key(cand)
            if self.cfg.allows(key) and con.execute(
                    f"SELECT 1 FROM docs WHERE substr(path_key, 1, ?) = ?{readable} LIMIT 1",
                    (len(key) + 1, key.rstrip(os.sep) + os.sep)).fetchone():
                return cand, key
        needle = os.sep + os.path.normcase(rel) + os.sep
        row = con.execute(f"SELECT path, path_key FROM docs WHERE instr(path_key, ?) > 0{readable} "
                          "ORDER BY instr(path_key, ?) LIMIT 1", (needle, needle)).fetchone()
        if row:
            end = row["path_key"].find(needle) + len(needle) - 1
            path = row["path"][:end] if len(row["path"]) == len(row["path_key"]) else row["path_key"][:end]
            return self._resolve(path)
        raise ValueError(f"No indexed folder named '{folder}'. Give a full path from a search result, "
                         "or a project name from list_projects.")

    # ------------------------------------------------------------ search
    def search(self, query: str, folder: str | None = None, file_type: str | None = None,
               modified_after: str | None = None, limit: int = 10, mode: str = "hybrid",
               project: str | None = None, doc_type: str | None = None, section: str | None = None,
               collection: str | None = None, machine: str | None = None, expand: bool | None = None,
               client: str | None = None, country: str | None = None, category: str | None = None,
               language: str | None = None, pictures: bool = False, offer: str | None = None,
               revision: str | None = None, partner: str | None = None, year_from: int | None = None,
               year_to: int | None = None, status: str | None = None) -> str:
        if mode not in ("hybrid", "keyword", "semantic"):
            raise ValueError("mode must be 'hybrid', 'keyword' or 'semantic'")
        limit = max(1, min(int(limit), 30))
        offers = self.cfg.profile == "offers"  # the filters of the offers world, None for the others
        facet_filter = {"project": project, "doc_type": doc_type, "section": section, "collection": collection,
                        "machine": machine, "client": client, "country": country, "category": category,
                        "language": language, "offer": offer if offers else None,
                        "revision": revision if offers else None, "partner": partner if offers else None,
                        "year_from": year_from if offers else None, "year_to": year_to if offers else None,
                        "status": status if offers else None}
        con = self._con()
        try:
            if pictures and not _has_descriptions(con):
                return "No picture descriptions in this index yet (the describe step has not run on it)."
            if folder:
                folder, _ = self._resolve_folder(con, folder)
            row_mask = None
            if mode != "keyword" and self.cfg.embeddings_enabled and self.searcher.vectors.ensure(con):
                row_mask = self.access.row_mask(self.searcher.vectors, con)
            hits = self.searcher.search(con, query, limit=limit, folder=folder, file_type=file_type,
                                        modified_after=modified_after, mode=mode,
                                        allowed=self.allows, row_mask=row_mask,
                                        folder_ok=self.access.folder_predicate(), facets=facet_filter, expand=expand,
                                        pictures_only=bool(pictures), profile=self.cfg.profile)
            doc_facets = self._facets(con, {h.doc_id for h in hits})
            ocr_pages = _ocr_text_pages(con, {h.doc_id for h in hits})
            picture_hits = _picture_chunks(con, [h.chunk_id for h in hits])
        finally:
            con.close()
        notes = ["(only passages written from pictures)"] if pictures else []
        if self.searcher.last_expanded:
            notes.append("(keyword search widened with the thesaurus: " + "; ".join(
                f"{term} → {', '.join(eq[:6])}" for term, eq in self.searcher.last_expanded) + ")")
        if any(facet_filter.values()):
            notes.append("(filtered by " + ", ".join(f"{k}={v}" for k, v in facet_filter.items() if v)
                         + ": documents whose facet is unknown are not included - search again without "
                         "the filter if results are thin)")
        if mode != "keyword" and self.searcher.embedder_error:
            notes.append(f"(semantic search unavailable: {self.searcher.embedder_error}; keyword results only)")
        elif mode != "keyword" and self.searcher.vectors.ids is not None and len(self.searcher.vectors.ids) == 0:
            notes.append("(no embeddings yet - keyword results only; run `dmx-docs embed`)")
        if not hits:
            return "\n".join(notes + [f"No results for: {query}. Try other words, synonyms or "
                                      "another language (documents are mostly French)."])
        calibrated = self.cfg.embedding_model == CALIBRATED_MODEL
        sims = [h.similarity for h in hits if h.similarity is not None]
        if calibrated and sims and max(sims) < SIM_MEDIUM:
            notes.append(f"(all matches are weak - best meaning similarity {max(sims):.2f}: the documents "
                         "probably do not cover this as phrased; try other terms or another angle)")
        lines = notes + [f"{len(hits)} results for: {query}\n"]
        for i, h in enumerate(hits, 1):
            match = "+".join(h.sources)
            if h.similarity is not None:
                match += f", similarity {h.similarity:.2f}"
                if calibrated:
                    match += " " + _strength(h.similarity)
            ocr_mark = " (OCR)" if (h.doc_id, h.page_no) in ocr_pages else ""
            pic = picture_hits.get(h.chunk_id)
            pic_mark = _picture_label(h.ext, *pic) if pic else ""
            lines.append(f"[{i}] {h.path} — {_page_label(h.ext)} {h.page_no}/{h.n_pages}{pic_mark}{ocr_mark} "
                         f"({TYPE_LABEL.get(h.ext, h.ext)}, modified {_date(h.mtime)}) [{match}]")
            if h.doc_id in doc_facets:
                lines.append("    " + doc_facets[h.doc_id])
            lines.append("    " + make_snippet(h.text, query))
            lines.append("")
        return "\n".join(lines).rstrip()

    def _facets(self, con, doc_ids: set[int]) -> dict[int, str]:
        """One line per document: project / collection / section / type (projects), category
        (marketing), discipline and section (engineering), category and language (documentation), client / offer / type / country (offers)."""
        if not doc_ids or self.cfg.profile == "none":
            return {}
        ids = list(doc_ids)
        if self.cfg.profile == "offers":
            return self._offer_facets(con, ids)
        if self.cfg.profile in ("marketing", "documentation", "engineering"):
            out = {}
            for r in con.execute(f"SELECT * FROM doc_facets WHERE doc_id IN ({','.join('?' * len(ids))})", ids):
                if self.cfg.profile == "engineering":
                    out[r["doc_id"]] = f"discipline {r['category'] or 'Unknown'} · section {r['section'] or '(none)'}"
                    continue
                line = f"category {r['category'] or 'unknown'}"
                if self.cfg.profile == "documentation":
                    line += f" · language {r['language'] or 'unknown'}"
                out[r["doc_id"]] = line
            return out
        out = {}
        machine_cache: dict[str, str] = {}
        readable = self._readable_sql(con)  # machine evidence counted among the caller's documents
        own = register.own_project_sql() if self._restricted else "project"  # no link from hidden documents
        for r in con.execute(f"SELECT * FROM doc_facets WHERE doc_id IN ({','.join('?' * len(ids))})", ids):
            project = r["project"] and (r["project"] + (f" / {r['subproject']}" if r["subproject"] else ""))
            if r["project"] and r["project"] not in machine_cache:
                machine_cache[r["project"]] = machines.describe(con, r["project"], limit=2, readable=readable)
                client = con.execute(f"SELECT client FROM register_machines WHERE {own} = ? AND client != '' "
                                     "GROUP BY client ORDER BY count(*) DESC LIMIT 1", (r["project"],)).fetchone()
                machine_cache["client:" + r["project"]] = client[0] if client else ""
            if r["project"] and machine_cache[r["project"]]:
                project += f" [{machine_cache[r['project']]}]"
            if r["project"] and machine_cache["client:" + r["project"]]:
                project += f" for {machine_cache['client:' + r['project']]}"
            parts = [f"project {project}" if project else None,
                     f"({r['collection']})" if r["collection"] else None,
                     f"section {r['section']}" if r["section"] else None,
                     f"type {r['doc_type']}" + ("" if r["facet_source"] == "folder" else f" (from {r['facet_source']})")
                     if r["doc_type"] else "type unknown"]
            out[r["doc_id"]] = " · ".join(p for p in parts if p)
        return out

    @staticmethod
    def _offer_facets(con, ids: list[int]) -> dict[int, str]:
        """'client ACME Foods · offer 3001234 rev 03 (Vandelay) · offer · Germany · partner Initech': the variant
        (or translation) and, when the folder has none, the country come from the offer register."""
        marks = ",".join("?" * len(ids))
        variants, countries = {}, {}
        if store.has_table(con, "offer_revisions"):
            for doc_id, variant, twin in con.execute(f"SELECT doc_id, variant, translation_of FROM offer_revisions "
                                                     f"WHERE doc_id IN ({marks})", ids):
                label = ", ".join(v for v in (variant, "translation" if twin else None) if v)
                if label:
                    variants[doc_id] = label
        if store.has_table(con, "offers"):
            countries = dict(con.execute(f"SELECT offer_key, country FROM offers WHERE offer_key IN "
                                         f"(SELECT offer FROM doc_facets WHERE doc_id IN ({marks}))", ids).fetchall())
        out = {}
        for r in con.execute(f"SELECT * FROM doc_facets WHERE doc_id IN ({marks})", ids):
            who = (f"client {r['client']}" + (f" / {r['site']}" if r["site"] else "") if r["client"]
                   else f"category {r['category'] or 'unknown'}")
            offer = (f"offer {r['offer']}" + (f" rev {r['revision']}" if r["revision"] else "")
                     + (f" ({variants[r['doc_id']]})" if r["doc_id"] in variants else "")) if r["offer"] else None
            parts = [who, offer, r["doc_type"] or "type unknown", r["country"] or countries.get(r["offer"]),
                     f"partner {r['partner']}" if r["partner"] else None]
            out[r["doc_id"]] = " · ".join(p for p in parts if p)
        return out

    # ---------------------------------------------------------- offers world
    # The register keeps rows of folders excluded since it was built: self.allows hides them (after
    # _con() has loaded the current root and excluded folders).
    def _to_verify(self) -> dict:
        """The settings of the bounded "To verify" list: off when no shared folder is configured."""
        caller = CURRENT_CALLER.get()
        who = None if caller is None else caller.sids
        with self._listed_lock:
            listed = self._listed.get(who)
            if listed is None:
                while len(self._listed) >= LISTED_CALLERS:  # bounded: the oldest caller's list goes first
                    del self._listed[next(iter(self._listed))]
                listed = self._listed[who] = {}
        return {"verify_limit": self.cfg.verify_per_request if self.cfg.verified_dir else 0, "listed": listed}

    def _verified(self, con) -> verified.Loaded:
        return verified.load(self.cfg, con, self.allows)

    def offer_health(self, **filters) -> str:
        con = self._con()
        try:
            return offer_tools.offer_health(con, self._verified(con), **filters, allows=self.allows)
        finally:
            con.close()

    def offer_verify(self, offer: str, revision: str, path: str, pages: str, lines: list | None = None,
                     checked_on: str = "image", checked_by: str = "", variant: str | None = None,
                     totals: dict | None = None, date: str | None = None, country: str | None = None,
                     offer_type: str | None = None, not_prices: list | None = None,
                     not_price_pages: dict | None = None, note: str | None = None, dispute: bool = False,
                     revoke: bool = False, reason: str | None = None) -> str:
        """Store what the agent saw on one revision's price pages as a verified record (shared at once), after
        the server's check; or dispute / revoke the revision's record. The answer says what was stored or what
        the agent has to fix. Writes only into the shared folder of the offers world."""
        if not self.cfg.verified_dir:
            return "Verification is not configured: set verified in [worlds.offers] of config.toml. Nothing was stored."
        folder = self.cfg.verified_dir
        if not os.path.isdir(folder):
            return f"The shared folder {folder} cannot be reached (VPN?): nothing was stored."
        if (dispute or revoke) and not (reason or "").strip():
            return "Not stored: dispute and revoke need a reason."
        caller = CURRENT_CALLER.get()  # the person of the request; a local server's own account
        user = caller.label if caller is not None else os.environ.get("USERNAME", "")
        try:
            sub = verified.Submission(
                offer=offer, revision=revision, variant=variant, path=path, pages=verified.parse_pages(pages),
                lines=list(lines or []), totals=dict(totals or {}), date=date, country=country,
                offer_type=offer_type, checked_on=checked_on, checked_by=checked_by,
                not_prices=list(not_prices or []), not_price_pages=dict(not_price_pages or {}), note=note or "",
                checked_at=datetime.now().replace(microsecond=0).isoformat(), user=user)
        except (ValueError, TypeError) as e:
            return f"Not stored: {e}"
        con = self._con()
        try:
            label = f"offer {offer} rev {revision}" + (f" variant {variant}" if variant else "")
            if dispute or revoke:
                if revoke and verified.identity(offer, revision, variant) not in self._verified(con).records:
                    return f"Not stored: {label} has no current verified record to revoke."
                try:
                    act = verified.revoke if revoke else verified.dispute
                    record = act(con, self.cfg, sub, reason, allows=self.allows)
                except ValueError as e:
                    return f"Not stored: {e}"
                verb = "revoked" if revoke else "disputed"
            else:
                verdict = verified.check(con, self.cfg, sub, allows=self.allows)
                if not verdict.ok:
                    return ("Not stored:\n" + "\n".join(f"- {p}" for p in verdict.problems)
                            + "\nRe-read the pages and call again, or call with dispute=true and the reason.")
                record, verb = verdict.record, "verified"
            try:
                verified.write(folder, record)
            except OSError as e:
                return f"The shared folder {folder} cannot be written ({e}): nothing was stored."
            if verb != "verified":
                return f"Recorded as {verb}: {label}. Shared with every user."
            totals_text = ", ".join(f"{c} {v:,.2f}" for c, v in record.totals.items()) or "no total"
            return (f"Verified: {label}: {len(record.lines)} lines, totals {totals_text}, checked on "
                    f"{record.checked_on}; shared with every user.")
        finally:
            con.close()

    def list_offers(self, **filters) -> str:
        con = self._con()
        try:
            return offer_tools.list_offers(con, **filters, allows=self.allows, loaded=self._verified(con))
        finally:
            con.close()

    def offer_card(self, offer: str) -> str:
        con = self._con()
        try:
            return offer_tools.offer_card(con, offer, allows=self.allows, loaded=self._verified(con))
        finally:
            con.close()

    def offer_lines(self, **filters) -> str:
        con = self._con()
        try:
            return offer_tools.offer_lines(con, **filters, allows=self.allows, loaded=self._verified(con),
                                           **self._to_verify())
        finally:
            con.close()

    def offer_stats(self, group_by: str, **filters) -> str:
        con = self._con()
        try:
            return offer_tools.offer_stats(con, group_by, **filters, allows=self.allows,
                                           loaded=self._verified(con), **self._to_verify())
        finally:
            con.close()

    # ----------------------------------------------------------- view_page
    def view_page(self, path: str, page: int = 1, region: str | None = None,
                  image: int | None = None) -> tuple[str, bytes, str]:
        """Render a PDF page (or a region of it) or return a picture embedded in a .docx or on a
        PowerPoint slide.
        Returns (caption, image bytes, format). Reads the live file: needs the network share."""
        self._con().close()  # loads the root/excluded folders that _resolve checks against
        path, key = self._resolve(path)
        fs = store.fs_path(path)
        if not self._is_file(path, key):
            raise FileNotFoundError(f"File not reachable now: {path} (viewing pages needs access to the file server)")
        ext = os.path.splitext(path)[1].lower()
        if ext in EXCEL_EXTS:
            raise ValueError(EXCEL_NO_IMAGE)
        if ext == ".one":
            raise ValueError(ONENOTE_NO_IMAGE)
        if ext == ".pdf":
            return _render_pdf_page(fs, path, int(page), region)
        if ext == ".docx":
            return _docx_picture(fs, path, image)
        if ext in SLIDE_EXTS:
            raw, pic_ext, k, total, n = _slide_picture_raw(fs, path, int(page), image, self.cfg.extract_options())
            data, fmt, w, h = _scaled_picture(raw, pic_ext)
            return (f"{path} — picture {k} of {total} on slide {page}/{n}, {w}x{h} px. Use image=N for "
                    "the others (whole slides cannot be shown, only their pictures)."), data, fmt
        if ext == ".md":
            raw, pic_ext, k, total, caption = _md_picture_raw(fs, path, image, self.cfg, self.allows)
            data, fmt, w, h = _scaled_picture(raw, pic_ext)
            label = f' "{caption}"' if caption else ""
            return (f"{path} — picture {k} of {total}{label} (the [Image {k}] marker in the text), "
                    f"{w}x{h} px. Use image=N for the others."), data, fmt
        if ext in PICTURE_EXTS:
            with open(fs, "rb") as f:
                data, fmt, w, h = _scaled_picture(f.read(), ext)
            return f"{path} — picture file, shown at {w}x{h} px.", data, fmt
        raise ValueError(f"Pictures of {TYPE_LABEL.get(ext, ext)} files cannot be shown (only PDF pages, "
                         "pictures in .docx, PowerPoint and Markdown files, and picture files).")

    # -------------------------------------------------------- export_image
    def export_image(self, path: str, page: int = 1, region: str | None = None,
                     image: int | None = None, name: str | None = None) -> str:
        """Save a PDF page/region rendering or a .docx or PowerPoint picture as an image file in the export
        folder (default C:\\dmx-rag\\exports), e.g. to put it on slides."""
        self._con().close()  # loads the root/excluded folders that _resolve checks against
        path, key = self._resolve(path)
        fs = store.fs_path(path)
        if not self._is_file(path, key):
            raise FileNotFoundError(f"File not reachable now: {path} (needs access to the file server)")
        ext = os.path.splitext(path)[1].lower()
        stem = _safe_name(os.path.splitext(os.path.basename(path))[0], 50)
        if ext in EXCEL_EXTS:
            raise ValueError(EXCEL_NO_IMAGE)
        if ext == ".one":
            raise ValueError(ONENOTE_NO_IMAGE)
        if ext == ".pdf":
            _, data, fmt = _render_pdf_page(fs, path, int(page), region, max_px=EXPORT_MAX_PX)
            default = f"{stem}_p{int(page)}" + ("_zoom" if region else "")
            out_ext = "." + ("jpg" if fmt == "jpeg" else fmt)
            what = f"page {page}" + (f", region {region}" if region else "")
        elif ext == ".docx":
            data, out_ext, k, total = _docx_picture_raw(fs, image)  # original resolution
            default, what = f"{stem}_img{k}", f"picture {k} of {total}"
        elif ext in SLIDE_EXTS:
            data, out_ext, k, total, _ = _slide_picture_raw(fs, path, int(page), image, self.cfg.extract_options())
            default, what = f"{stem}_s{int(page)}_img{k}", f"picture {k} of {total} on slide {page}"
        elif ext == ".md":
            data, out_ext, k, total, _ = _md_picture_raw(fs, path, image, self.cfg, self.allows)  # original file
            default, what = f"{stem}_img{k}", f"picture {k} of {total}"
        elif ext in PICTURE_EXTS:
            with open(fs, "rb") as f:
                data = f.read()  # the original file
            out_ext, default, what = ext, stem, "picture file"
        else:
            raise ValueError(f"Images can only be exported from PDF pages, pictures in .docx, "
                             f"PowerPoint and Markdown files, and picture files, not {ext}")
        folder = self.cfg.export_dir or default_export_dir(self.cfg)
        os.makedirs(folder, exist_ok=True)
        base = _safe_name(name) if name else default
        target = os.path.join(folder, base + out_ext)
        n = 2
        while os.path.exists(target):
            target = os.path.join(folder, f"{base}_{n}{out_ext}")
            n += 1
        with open(target, "wb") as f:
            f.write(data)
        return (f"Saved {what} of {path} to {target} ({len(data) // 1024} KB), in the local "
                f"export folder {folder} on the user's PC. If you work in a sandboxed workspace that "
                "cannot see this folder, ask the user to add it to the workspace.")

    # ------------------------------------------------------- open_document
    def open_document(self, path: str, page: int | None = None) -> str:
        """Open a document on this PC in its usual application (for the user, not for Claude)."""
        self._con().close()  # loads the root/excluded folders that _resolve checks against
        path, key = self._resolve(path)
        ext = os.path.splitext(path)[1].lower()
        if ext not in TYPE_LABEL:  # never launch anything but an indexed document type
            raise ValueError(f"Only {', '.join(TYPE_LABEL)} files can be opened")
        if not self._is_file(path, key):
            raise FileNotFoundError(f"File not reachable now: {path}")
        how = _open_file(path, ext, page)
        return f"Opened {path} {how}."

    # ------------------------------------------------------- list_projects
    def list_projects(self, name: str | None = None, collection: str | None = None,
                      machine: str | None = None, limit: int = 300, client: str | None = None,
                      country: str | None = None, year_from: int | None = None, year_to: int | None = None) -> str:
        limit = max(1, min(int(limit), 1000))
        con = self._con()
        try:
            where, having, params = self._readable_facets(con), "", []  # counts of readable documents only
            if collection:
                where += " AND coalesce(f.collection, '') LIKE ?"
                params.append(f"%{collection}%")
            if machine:  # a family ("Paloma") or a model ("Paloma 4R"), comma-separated
                values = [v.strip() for v in machine.split(",") if v.strip()]
                cond, args = machines.filter_sql(con, values, "f.project", readable=self._readable_sql(con))
                where += " AND " + cond
                params += args
            restricted = self._restricted
            reg_cond, reg_params = self._register_filter(client, country, year_from, year_to, own=restricted)
            if reg_cond:  # a restricted caller: the register's own links, none made from hidden documents
                own = register.own_project_sql("rm") if restricted else "rm.project"
                where += f" AND f.project IN (SELECT {own} FROM register_machines rm WHERE {reg_cond})"
                params += reg_params
            if name:  # match the project or any of its sub-projects, but list the whole project
                having = " HAVING f.project LIKE ? OR coalesce(group_concat(f.subproject), '') LIKE ?"
                params += [f"%{name}%", f"%{name}%"]
            sql = f"""SELECT f.project, f.collection, count(*) n, max(d.mtime) last,
                             group_concat(DISTINCT f.section) sections,
                             group_concat(DISTINCT f.subproject) subprojects
                      FROM doc_facets f JOIN docs d ON d.id = f.doc_id
                      WHERE f.project IS NOT NULL{where}
                      GROUP BY f.project, f.collection{having}
                      ORDER BY f.collection IS NOT NULL, f.project LIMIT ?"""
            rows = con.execute(sql, params + [limit]).fetchall()
            if rows and self.access.folder_predicate() is not None:
                # Safety net, as in find_files: a listed project has a document the caller may read,
                # whatever happened to the SQL above.
                listed = sorted({r["project"] for r in rows})
                seen = {(p, c) for p, c, k in con.execute(
                    f"""SELECT f.project, f.collection, d.path_key FROM doc_facets f JOIN docs d ON d.id = f.doc_id
                        WHERE f.project IN ({','.join('?' * len(listed))})""", listed) if self.allows(k)}
                rows = [r for r in rows if (r["project"], r["collection"]) in seen]
            readable = self._readable_sql(con)  # machine evidence counted among the caller's documents
            machine_text = {r["project"]: machines.describe(con, r["project"], readable=readable) for r in rows}
            # The register's years where the documents disagree come from every document, hidden ones too.
            client_text = {r["project"]: register.summary(con, r["project"], own=restricted) for r in rows}
            unindexed = []
            # Delivered machines whose documents are not indexed: a restricted caller reads no document
            # of theirs, so the register is not shown for them.
            if (reg_cond or (machine and not collection)) and not restricted:
                cond, args = reg_cond or "1", list(reg_params)
                if machine:
                    values = [v.strip() for v in machine.split(",") if v.strip()]
                    marks = ",".join("?" * len(values))
                    cond += f" AND (rm.family COLLATE NOCASE IN ({marks}) OR rm.model COLLATE NOCASE IN ({marks}))"
                    args += values + values
                if name:
                    cond += " AND rm.name LIKE ?"
                    args.append(f"%{name}%")
                unindexed = con.execute(
                    f"""SELECT coalesce(nullif(rm.name, ''), '(no name)') name, group_concat(DISTINCT rm.model) models,
                               max(rm.client) client, max(rm.country) country, min(rm.year) y0, max(rm.year) y1,
                               count(*) n FROM register_machines rm WHERE rm.project IS NULL AND {cond}
                        GROUP BY 1 ORDER BY 1 LIMIT ?""", args + [limit]).fetchall()
        finally:
            con.close()
        if not rows and not unindexed:
            return "No matching project in the index or in the machine register."
        lines = [f"{len(rows)} indexed projects (project | collection | indexed documents | last modified | "
                 "machines found in the documents | client, machines delivered and year from the register | "
                 "sections):"]
        for r in rows:
            subs = f" | sub-projects: {', '.join(sorted(r['subprojects'].split(',')))}" if r["subprojects"] else ""
            lines.append(f"- {r['project']} | {r['collection'] or 'current'} | {r['n']} docs | {_date(r['last'])} | "
                         f"{machine_text[r['project']] or 'machine unknown'} | "
                         f"{client_text[r['project']] or 'not in the register'} | {r['sections'] or '-'}{subs}")
        if unindexed:
            lines.append(f"\n{len(unindexed)} more in the machine register, without indexed documents "
                         "(name | models | client | country | year | machines):")
            for u in unindexed:
                years = "?" if not u["y0"] else str(u["y0"]) if u["y0"] == u["y1"] else f"{u['y0']}-{u['y1']}"
                lines.append(f"- {u['name']} | {u['models'] or 'model ?'} | {u['client'] or 'client ?'} | "
                             f"{u['country'] or '?'} | {years} | {u['n']}")
        if machine:
            lines.append("(machine types come from the machine register and from file names, offers, "
                         "specifications, FAT/SAT...)")
        if reg_cond:
            lines.append("(client, country and year come from the machine register: projects missing from it "
                         "are not listed - check with search if needed)")
        return "\n".join(lines)

    @staticmethod
    def _register_filter(client, country, year_from, year_to, own: bool = False) -> tuple[str, list]:
        """own: filter on the register's own years, not those taken from the project's documents."""
        conds, params = [], []
        year = "coalesce(rm.reg_year, rm.year)" if own else "rm.year"
        if client:
            c, p = register.client_clause(client)
            conds.append(c)
            params += p
        if country:
            c, p = register.country_clause(country)
            conds.append(c)
            params += p
        if year_from:
            conds.append(f"{year} >= ?")
            params.append(int(year_from))
        if year_to:
            conds.append(f"{year} <= ?")
            params.append(int(year_to))
        return " AND ".join(conds), params

    # -------------------------------------------------------- project_card
    def project_card(self, project: str) -> str:
        """Everything known about one project: client, delivered machines, order numbers, documents."""
        con = self._con()
        try:
            readable = self._readable_facets(con)  # a project with no readable document is not indexed
            hit = con.execute("SELECT project FROM doc_facets WHERE (project = ? COLLATE NOCASE "
                              f"OR subproject = ? COLLATE NOCASE){readable} LIMIT 1", (project, project)).fetchone()
            name = hit[0] if hit else None
            # The register is shown for a project the caller reads at least one document of; for a
            # restricted caller by the register's own links (own), none made from order numbers found in
            # documents the caller may not read.
            restricted, project_ok = self._restricted, self._project_reader(con)
            own = register.own_project_sql() if restricted else "project"
            reg = con.execute(
                f"SELECT *, {own} AS own FROM register_machines WHERE {own} = ? ORDER BY order_no, serial" if name
                else f"SELECT *, {own} AS own FROM register_machines WHERE name = ? COLLATE NOCASE "
                     "ORDER BY order_no, serial", (name or project,)).fetchall()
            reg = [r for r in reg if project_ok(r["own"])]
            if not name and not reg:
                if restricted:
                    names = {r[0] for r in con.execute(
                        f"SELECT DISTINCT project FROM doc_facets WHERE project LIKE ?{readable}", (f"%{project}%",))}
                    names |= {n for n, p in con.execute(f"SELECT DISTINCT name, {own} FROM register_machines "
                                                        "WHERE name LIKE ?", (f"%{project}%",)) if project_ok(p)}
                    like = [(n,) for n in sorted(names)[:10]]
                else:
                    like = con.execute(f"""SELECT DISTINCT project FROM doc_facets WHERE project LIKE ?{readable} UNION
                                          SELECT DISTINCT name FROM register_machines WHERE name LIKE ? LIMIT 10""",
                                       (f"%{project}%", f"%{project}%")).fetchall()
                written = self._written_card(con, project)  # a card may outlive its project's documents
                return (f"No project '{project}' in the index or the machine register."
                        + (f" Similar names: {', '.join(r[0] for r in like)}" if like else "")
                        # a hidden project's card answers like no card, as for a name that does not exist
                        + ("" if written in ("No written card yet.", self.NOT_AVAILABLE) else "\n\n" + written))
            lines = []
            if name:
                n, first, last, coll = con.execute(
                    f"""SELECT count(*), min(d.mtime), max(d.mtime), max(f.collection) FROM doc_facets f
                       JOIN docs d ON d.id = f.doc_id WHERE f.project = ?{readable}""", (name,)).fetchone()
                subs = [r[0] for r in con.execute("SELECT DISTINCT subproject FROM doc_facets WHERE project = ? "
                                                  f"AND subproject IS NOT NULL{readable} ORDER BY 1", (name,))]
                lines.append(f"Project {name} ({coll or 'current'}): {n} indexed documents, files dated "
                             f"{_date(first)} to {_date(last)}" + (f"; sub-projects {', '.join(subs)}" if subs else ""))
            else:
                lines.append(f"Project {reg[0]['name']}: in the machine register, no indexed documents.")
            if reg:
                clients = {}
                for r in reg:
                    if r["client"]:
                        clients.setdefault(r["client"], r)
                for c, r in clients.items():
                    extra = ", ".join(v for v in (f"group {r['grp']}" if r["grp"] and r["grp"].upper() != c.upper()
                                                  else "", r["industry"], r["city"], r["country"], r["site"]) if v)
                    lines.append(f"Client: {c}" + (f" ({extra})" if extra else ""))
                if not clients:
                    lines.append("Client: unknown in the register")
                agents = {r["agent"] for r in reg if r["agent"]}
                if agents:
                    lines.append(f"Through agent/integrator: {', '.join(sorted(agents))}")
            if name:
                found = machines.describe(con, name, limit=6, readable=self._readable_sql(con))
                if found:
                    lines.append(f"\nMachines named in the documents: {found}")
            if reg:
                lines.append(f"\nMachines delivered (machine register, {len(reg)}; where it disagrees with the "
                             "documents, the documents are more reliable - see ⚠):")
                for r in reg:
                    bits = [r["serial"] or r["order_no"] or "no serial", r["model"] or "model ?"]
                    # What the documents say instead (doc_note, the year from their dates) was read
                    # from every document of the project: only for those who read them all.
                    year = (r["reg_year"] or r["year"]) if restricted else r["year"]
                    if year:
                        bits.append(str(year))
                    if r["robots"]:
                        bits.append(f"{r['robots']} robots")
                    if r["controller"]:
                        bits.append(f"controller {r['controller']}")
                    if r["camera"]:
                        bits.append(f"camera {r['camera']}")
                    if r["maintenance"]:
                        bits.append(f"maintenance contract {r['maintenance']}")
                    if r["order_date"]:
                        bits.append(f"ordered {r['order_date']}")
                    if r["status"]:
                        bits.append(r["status"])
                    if r["notes"]:
                        bits.append(r["notes"])
                    note = None if restricted else r["doc_note"]
                    lines.append("- " + " · ".join(bits) + (f"\n    ⚠ {note}" if note else ""))
            if name:
                # Moved by order numbers found in documents: for a restricted caller they stay where the
                # register files them (here), so there is nothing to list.
                moved = [] if restricted else con.execute("SELECT serial, order_no, model, project FROM "
                                                          "register_machines WHERE reg_project = ?", (name,)).fetchall()
                if moved:
                    lines.append("\nFiled under this project in the register, but their numbers are in another "
                                 "project's documents (counted there): " + "; ".join(
                                     f"{m['serial'] or m['order_no']} {m['model'] or ''} -> {m['project']}"
                                     for m in moved))
                evidence = {}
                for (path,) in con.execute("SELECT d.path FROM docs d JOIN doc_facets f ON f.doc_id = d.id "
                                           f"WHERE f.project = ?{readable}", (name,)):
                    for num in register.numbers(path):
                        evidence[num] = evidence.get(num, 0) + 1
                known = {n for r in reg for n in register.numbers(r["order_no"], r["serial"])}
                if evidence or known:
                    nums = sorted(set(evidence) | known, key=lambda k: -evidence.get(k, 0))[:8]
                    lines.append("Order / serial numbers: " + ", ".join(
                        f"{k} ({evidence[k]} file names)" if k in evidence else f"{k} (register)" for k in nums))
                lines.append("\nDocuments by type:")
                for dt, cnt, newest in con.execute(
                        f"""SELECT coalesce(f.doc_type, 'unknown'), count(*), max(d.mtime) FROM doc_facets f
                           JOIN docs d ON d.id = f.doc_id WHERE f.project = ?{readable} GROUP BY 1 ORDER BY 2 DESC""",
                        (name,)):
                    lines.append(f"- {dt}: {cnt} (newest {_date(newest)})")
                lines.append("\nKey documents (newest of each kind):")
                for dt in ("offre", "commande", "cahier_des_charges", "fat", "sat", "mise_en_service",
                           "reception", "cloture"):
                    r = con.execute(f"""SELECT d.path, d.mtime FROM doc_facets f JOIN docs d ON d.id = f.doc_id
                                       WHERE f.project = ? AND f.doc_type = ?{readable} ORDER BY d.mtime DESC LIMIT 1""",
                                    (name, dt)).fetchone()
                    if r:
                        lines.append(f"- {dt}: {r['path']} ({_date(r['mtime'])})")
            clients = sorted({r["client"] for r in reg if r["client"]})
            groups = sorted({r["grp"] for r in reg if r["grp"]})
            if clients:
                year = "coalesce(reg_year, year)" if restricted else "year"
                others = [o for o in con.execute(
                    f"""SELECT coalesce({own}, name) p, group_concat(DISTINCT model), min({year}),
                               max({own} IS NOT NULL), max({own}) FROM register_machines
                        WHERE (client IN ({','.join('?' * len(clients))})
                        OR grp IN ({','.join('?' * len(groups)) or "''"}))
                        AND coalesce({own}, name) != ? GROUP BY 1 ORDER BY 3 DESC""",
                    clients + groups + [name or reg[0]["name"]]) if project_ok(o[4])][:25]
                if others:
                    lines.append("\nOther projects for the same client or group: " + "; ".join(
                        f"{p} ({m or 'model ?'}, {y or '?'}{'' if idx else ', not indexed'})"
                        for p, m, y, idx, _ in others))
            lines.append("\n" + self._written_card(con, project, name))
        finally:
            con.close()
        lines.append("\n(client, machines delivered and years come from the company's machine register, a "
                     "hand-made list: when it disagrees with the documents, trust the documents; the rest "
                     "comes from the indexed documents)")
        return "\n".join(lines)

    NOT_AVAILABLE = "Card not available with your rights."

    def _written_card(self, con, asked: str, parent: str | None = None) -> str:
        """The knowledge card of the asked project, else (labelled) the card of its parent project,
        as the caller may see it: the header line only when every source of the card is readable."""
        project_ok = self._project_reader(con)
        view = self._card_viewer(con, project_ok)

        def show(card, label: str = "") -> str:
            body, _hidden, whole = view(card)
            if whole:
                return (f"{label}Written card {card.file} ({card.status}, {card.written}, built from {card.read} "
                        f"documents):\n{body}")
            return f"{label}Written card {card.file}:\n{body}" if body else self.NOT_AVAILABLE

        card = self.knowledge.card(asked)
        if card:
            return show(card)
        for file, reason in self.knowledge.errors().items():
            stem = knowledge.stem_of(file)
            if register.fold(asked) in (register.fold(stem), register.fold(stem.split("__")[-1])):
                return self._unreadable_card(file, reason, project_ok)
        if parent and register.fold(parent) != register.fold(asked):
            card = self.knowledge.card(parent)
            if card:
                return show(card, f"No written card for {asked}; written card of the parent project {parent}:\n")
        return "No written card yet."

    def _unreadable_card(self, file: str, reason: str, project_ok) -> str:
        """A card file that cannot be parsed: nobody can tell where its text comes from, so its
        reason (which may quote it) is for those who read everything; its name for those who read
        its project; for the others it is not available."""
        if not self._restricted:
            return f"card {file} unreadable: {reason}"
        if project_ok(knowledge.stem_of(file).split("__")[-1]):
            return f"card {file} unreadable."
        return self.NOT_AVAILABLE

    def _project_names(self, con) -> set[str]:
        """Indexed project and sub-project names."""
        return {r[0] for r in con.execute("SELECT project FROM doc_facets WHERE project IS NOT NULL UNION "
                                          "SELECT subproject FROM doc_facets WHERE subproject IS NOT NULL")}

    # --------------------------------------------------------- catalog
    def catalog(self, infeed: str | None = None, outfeed: str | None = None, end_effector: str | None = None,
                machine: str | None = None, client: str | None = None, country: str | None = None,
                year_from: int | None = None, year_to: int | None = None, status: str | None = None,
                text: str | None = None, limit: int = 100) -> str:
        """One line per written project card matching the filters, then the coverage."""
        k = self.knowledge
        if not k.available:
            return "No knowledge cards on this machine (run pull)."
        vocab = k.vocabulary
        fold = register.fold

        def entry_match(kind: str, entries: list[str], query: str) -> bool:
            want = vocab.canonical(kind, query)
            if want:  # the same value, or a narrower one ending with it: a wrap-around case is a case
                got = [vocab.canonical(kind, knowledge.form_of(e)) or "" for e in entries]
                return any(g == want or fold(g).endswith(" " + fold(want)) for g in got)
            return any(fold(query) in fold(e) for e in entries)

        def years(card) -> tuple[int, int] | None:
            got = [int(y) for y in re.findall(r"\d{4}", " ".join(card.values("years")))]
            return (min(got), max(got)) if got else None

        con = self._con()
        try:
            project_ok = self._project_reader(con)
            view = self._card_viewer(con, project_ok)
            cards = k.cards()
            # Each card as the caller may see it: (visible body without the closing note, whole).
            seen = {}
            for c in cards:
                body, hidden, whole = view(c)
                seen[c.file] = (body.rsplit("\n\n", 1)[0] if body and not whole else body, whole)

            def visible(card) -> bool:
                body, whole = seen[card.file]
                return whole or bool(body)

            def values(card, key: str) -> list[str]:
                """The header values the caller may see: all of them for a whole card, else those
                backed by its visible lines (by the rule of knowledge check) - for a word-list field
                its word only, since the detail in parentheses may come from a hidden line."""
                body, whole = seen[card.file]
                if whole:
                    return card.values(key)
                folded = fold(body)
                words = key in ("infeed", "outfeed", "end_effectors")
                return [knowledge.form_of(v) if words else v for v in card.values(key)
                        if knowledge.backs(vocab, key, v, folded)]

            def keep(card) -> bool:
                if not visible(card):
                    return False
                body, whole = seen[card.file]
                for kind, key, query in (("forms", "infeed", infeed), ("forms", "outfeed", outfeed),
                                         ("end_effectors", "end_effectors", end_effector)):
                    if query and not entry_match(kind, values(card, key), query):
                        return False
                for key, query in (("machines", machine), ("client", client), ("country", country)):
                    if query and not any(fold(query) in fold(v) for v in values(card, key)):
                        return False
                if status and (not whole or fold(status) != fold(card.status)):  # "reviewed" is inside "unreviewed"
                    return False
                if text and fold(text) not in fold(" ".join(" ".join(card.values(f)) for f in card.fields)
                                                  + " " + card.body if whole else body):
                    return False
                if year_from or year_to:
                    span = years(card) if whole else None
                    if not span or (year_from and span[1] < year_from) or (year_to and span[0] > year_to):
                        return False
                return True

            def show(values: list[str]) -> str:
                return ", ".join(values) or "?"

            lines = [" · ".join((c.project, show(values(c, "client")), show(values(c, "years")),
                                 show(values(c, "machines")),
                                 f"{show(values(c, 'infeed'))} -> {show(values(c, 'outfeed'))}",
                                 show(values(c, "end_effectors")), show(values(c, "speed")),
                                 c.status if seen[c.file][1] else "not all shown with your rights"))
                     for c in cards if keep(c)]
            out = lines[:max(1, int(limit))] or ["No card matches these filters."]
            if len(lines) > len(out):
                out.append(f"... {len(lines) - len(out)} more (raise limit=)")
            unreadable = (self._unreadable_card(file, reason, project_ok) for file, reason in k.errors().items())
            out += [u for u in unreadable if u != self.NOT_AVAILABLE]
            # Coverage among the projects the caller can see, by the cards the caller can see.
            readable = self._readable_facets(con)
            projects = {p for (p,) in con.execute(
                f"SELECT DISTINCT project FROM doc_facets WHERE project IS NOT NULL{readable}") if project_ok(p)}
            covered = set()  # indexed projects with a card of their own or of one of their sub-projects
            for c in cards:
                if not visible(c):
                    continue
                hit = con.execute("SELECT project FROM doc_facets WHERE (project = ? COLLATE NOCASE "
                                  f"OR subproject = ? COLLATE NOCASE){readable} LIMIT 1",
                                  (c.project, c.project)).fetchone()
                if hit and hit[0] in projects:
                    covered.add(hit[0])
        finally:
            con.close()
        out.append(f"Cards exist for {len(covered)} of {len(projects)} projects: for a complete answer, "
                   "also use search / list_projects.")
        return "\n".join(out)

    # --------------------------------------------------------- find_files
    def find_files(self, name: str, folder: str | None = None, limit: int = 30) -> str:
        limit = max(1, min(int(limit), 100))
        fts = build_fts_query(name.replace("*", " "), mode="AND")
        if not fts:
            return "Please give at least one word of the file or folder name."
        fts = " AND ".join(t + "*" if not t.endswith("*") else t for t in fts.split(" AND "))
        clauses, params = [], []
        con = self._con()
        try:
            if folder:
                _, key = self._resolve_folder(con, folder)
                prefix = key.rstrip(os.sep) + os.sep
                clauses.append("substr(d.path_key, 1, ?) = ?")
                params += [len(prefix), prefix]
            readable = self._readable_sql(con)  # hidden documents must not take the LIMIT's slots
            if readable:
                clauses.append(readable)
            where = " AND ".join(["docs_fts MATCH ?"] + clauses)
            rows = [r for r in con.execute(
                f"""SELECT d.path, d.path_key, d.ext, d.size, d.mtime, d.n_pages, d.status FROM docs_fts f
                    JOIN docs d ON d.id = f.rowid WHERE {where} ORDER BY f.rank LIMIT ?""",
                [fts] + params + [limit * 3]) if self.allows(r["path_key"])][:limit]
        finally:
            con.close()
        if not rows:
            return f"No indexed file matches '{name}'."
        lines = [f"{len(rows)} files matching '{name}':"]
        for r in rows:
            extra = ("" if r["status"] == "ok" else ", not described yet" if r["ext"] in PICTURE_EXTS
                     and r["status"] == "no_text" else f", status: {r['status']}")
            lines.append(f"- {r['path']} ({TYPE_LABEL.get(r['ext'], r['ext'])}, {r['n_pages'] or 0} pages, "
                         f"{_size(r['size'])}, modified {_date(r['mtime'])}{extra})")
        return "\n".join(lines)

    # -------------------------------------------------------- list_folder
    def list_folder(self, path: str | None = None, limit: int = 200) -> str:
        limit = max(1, min(int(limit), 1000))
        con = self._con()
        try:
            if not path:
                if not self.cfg.roots:
                    return "No root folder is configured yet."
                lines = ["Indexed root folders:"]
                readable = self._readable_sql(con, "folder_key")  # the caller's documents only
                readable = f" AND {readable}" if readable else ""
                for root, rk in zip(self.cfg.roots, self.cfg.root_keys):
                    prefix = rk.rstrip(os.sep) + os.sep
                    n = con.execute(f"SELECT count(*) FROM docs WHERE substr(path_key, 1, ?) = ?{readable}",
                                    (len(prefix), prefix)).fetchone()[0]
                    state = "reachable" if os.path.isdir(root) else "NOT reachable"
                    lines.append(f"- {root} ({n} indexed documents, {state})")
                if self.cfg.excluded_dirs:
                    lines.append("Excluded folders: " + ", ".join(self.cfg.excluded_dirs))
                return "\n".join(lines)
            path, key = self._resolve_folder(con, path)
            indexed = {r["path_key"]: r for r in con.execute(
                "SELECT path_key, status, n_pages FROM docs WHERE folder_key = ?", (key,))
                if self.allows(r["path_key"])}
            # A restricted caller sees a folder only if it holds at least one document they may read:
            # any other folder, there or not, is answered as a missing one (its disk is not looked at),
            # and a subfolder is listed only if it holds such a document.
            shown_folders = None
            readable = self._readable_sql(con, "folder_key")
            if readable:
                prefix = key.rstrip(os.sep) + os.sep
                under = [fk for (fk,) in con.execute(
                    f"SELECT DISTINCT folder_key FROM docs WHERE (folder_key = ? OR substr(folder_key, 1, ?) = ?) "
                    f"AND {readable}", (key, len(prefix), prefix))]
                if not under:
                    return f"Cannot open folder {path}: folder not found."
                shown_folders = {prefix + fk[len(prefix):].split(os.sep)[0] for fk in under if fk != key}
        finally:
            con.close()

        try:
            with os.scandir(path) as it:
                entries = sorted(it, key=lambda e: (not e.is_dir(), e.name.lower()))
        except OSError as e:
            if not indexed:
                return f"Cannot open folder {path}: {e}"
            lines = [f"{path} (folder not reachable right now; indexed files only):"]
            for k, r in sorted(indexed.items()):
                lines.append(f"- {os.path.basename(k)} ({r['n_pages']} pages, {r['status']})")
            return "\n".join(lines)

        folders, files = [], []
        for e in entries:
            if self.cfg.is_excluded(e.name, e.path):
                continue
            try:
                if e.is_dir():
                    sub = store.path_key(e.path)
                    if not self.cfg.is_excluded_dir(sub) and (shown_folders is None or sub in shown_folders):
                        folders.append(f"[folder] {e.name}")
                    continue
                if not self.allows(store.path_key(e.path)):
                    continue
                st = e.stat()
            except OSError:
                continue
            r = indexed.get(store.path_key(e.path))
            if r is not None:
                info = f"indexed, {r['n_pages'] or 0} pages" if r["status"] == "ok" else f"status: {r['status']}"
            else:
                ext = os.path.splitext(e.name)[1].lower()
                info = "not indexed yet" if ext in self.cfg.extensions else "not indexed (file type)"
            files.append(f"{e.name} ({_size(st.st_size)}, modified {_date(st.st_mtime)}, {info})")
        items = folders + files
        lines = [f"{path}: {len(folders)} folders, {len(files)} files"]
        lines += ["- " + x for x in items[:limit]]
        if len(items) > limit:
            lines.append(f"... {len(items) - limit} more (increase limit)")
        return "\n".join(lines)

    # ------------------------------------------------------ document text
    def _pages(self, path: str, key: str) -> tuple[dict[int, str], int, dict]:
        """Return ({page_no: text}, n_pages, info). Uses the index, or extracts live if needed.
        A OneNote section is only ever served from the index (only the index run reads sections).
        A document the caller may not read is answered as one that is neither indexed nor on disk."""
        section = os.path.splitext(path)[1].lower() == ".one"
        readable = self.access.can_read(key)
        con = self._con()
        try:
            doc = con.execute("SELECT * FROM docs WHERE path_key = ?", (key,)).fetchone() if readable else None
            info = {"source": "index"}
            fresh = False
            if doc is None and section:
                raise ValueError(f"Not indexed yet: {path} ({onenote.SERVER_ONLY})")
            if doc is not None:
                try:
                    st = os.stat(store.fs_path(path))
                    fresh = st.st_size == doc["size"] and abs(st.st_mtime - (doc["mtime"] or 0)) < 0.01
                except OSError:
                    fresh = True  # file not reachable: serve the indexed copy
                if section and doc["status"] not in ("ok", "no_text", "empty"):
                    raise ValueError(f"Not read yet: {path} ({doc['error']})")
                if section and not fresh:
                    info["note"] = (f"changed since it was indexed on {_date(doc['indexed_at'])}: "
                                    "these are the indexed pages")
                if (fresh or section) and doc["status"] in ("ok", "no_text", "empty"):
                    pages: dict[int, str] = {}
                    for r in con.execute("SELECT page_no, text FROM chunks WHERE doc_id = ? "
                                         "ORDER BY page_no, seq", (doc["id"],)):
                        pages[r["page_no"]] = pages.get(r["page_no"], "") + r["text"]
                    ocr_pages = sorted(p for _, p in _ocr_text_pages(con, [doc["id"]]))
                    info.update(ext=doc["ext"], mtime=doc["mtime"], status=doc["status"], ocr_pages=ocr_pages,
                                picture_pages=_picture_pages(con, doc["id"]))
                    return pages, doc["n_pages"] or 0, info
        finally:
            con.close()
        if not (readable and os.path.isfile(store.fs_path(path))):
            raise FileNotFoundError(f"File not found: {path}")
        ext = os.path.splitext(path)[1].lower()
        result = extract_file(path, self.cfg.extract_options())
        if result.status == "error" or result.status == "skipped":
            raise ValueError(f"Cannot read {path}: {result.error}")
        info = {"source": "live extraction (file new or changed since indexing)", "ext": ext,
                "mtime": os.stat(store.fs_path(path)).st_mtime, "status": result.status}
        return dict(result.pages), result.n_pages, info

    def read_document(self, path: str, start_page: int = 1, end_page: int | None = None) -> str:
        path, key = self._resolve(path)
        pages, n_pages, info = self._pages(path, key)
        start_page = max(1, int(start_page))
        last = n_pages or (max(pages) if pages else 0)
        end_page = min(int(end_page), last) if end_page else last
        if start_page > last:
            return f"{path} has {last} pages; start_page {start_page} is beyond the end."
        label = _page_label(info.get("ext", ""))
        out, used, shown_until = [], 0, start_page - 1
        for p in range(start_page, end_page + 1):
            text = pages.get(p, "")
            block = f"--- {label} {p} ---\n{text if text else '[no text on this page]'}\n"
            if used + len(block) > self.cfg.max_read_chars and out:
                break
            if len(block) > self.cfg.max_read_chars:
                block = block[:self.cfg.max_read_chars] + "\n[... page truncated ...]\n"
            out.append(block)
            used += len(block)
            shown_until = p
        header = (f"{path} ({TYPE_LABEL.get(info.get('ext'), info.get('ext'))}, {last} {_units(info.get('ext', ''))}, "
                  f"modified {_date(info.get('mtime'))}; source: {info['source']})")
        if info.get("note"):
            header += "\n" + info["note"]
        if info.get("status") == "no_text" and info.get("ext") in PICTURE_EXTS:
            header += "\nNote: a picture file with no description yet — look at it with view_page."
        elif info.get("status") == "no_text":
            header += "\nNote: this PDF has (almost) no text layer — probably a scan; content may be missing."
        ocr_shown = [p for p in info.get("ocr_pages", ()) if start_page <= p <= shown_until]  # OCR pages in this output
        if ocr_shown:
            shown = ", ".join(map(str, ocr_shown[:30])) + (" ..." if len(ocr_shown) > 30 else "")
            header += (f"\nNote: text of {label} {shown} was recognized by OCR (scanned page): it may contain "
                       "recognition errors - check codes and numbers with view_page.")
        pic_shown = [p for p in info.get("picture_pages", ()) if start_page <= p <= shown_until]
        if pic_shown:
            header += ("\nNote: passages starting with [Image N — type] or [Page entière — type] were written by a "
                       "vision model from the pictures: look at the picture with view_page before quoting a "
                       "precise value (number, label, code) from one.")
        footer = ""
        if shown_until < end_page:
            footer = f"\n[Showing {label}s {start_page}-{shown_until} of {last}. " \
                     f"Continue with start_page={shown_until + 1}.]"
        elif shown_until < last:
            footer = f"\n[Showing {label}s {start_page}-{shown_until} of {last}.]"
        return header + "\n\n" + "\n".join(out) + footer

    def find_in_document(self, path: str, text: str, max_hits: int = 20) -> str:
        path, key = self._resolve(path)
        pages, n_pages, info = self._pages(path, key)
        needle = fold(" ".join(text.split()))
        if not needle:
            return "Please give the text to look for."
        max_hits = max(1, min(int(max_hits), 100))
        label = _page_label(info.get("ext", ""))
        hits = []
        total = 0
        for p in sorted(pages):
            page_text = " ".join(pages[p].split())
            folded = fold(page_text)
            for m in re.finditer(re.escape(needle), folded):
                total += 1
                if len(hits) < max_hits:
                    a, b = max(0, m.start() - 250), min(len(page_text), m.end() + 250)
                    hits.append(f"[{label} {p}] …{page_text[a:b]}…")
        note = "\n" + info["note"] if info.get("note") else ""
        if not hits:
            return f"'{text}' not found in {path} ({n_pages} pages; search ignores case and accents).{note}"
        more = f" (showing first {len(hits)})" if total > len(hits) else ""
        return f"{total} occurrences of '{text}' in {path}{more}:{note}\n\n" + "\n\n".join(hits)

    # ------------------------------------------------------------ status
    def world_map(self) -> str:
        now = time.monotonic()
        # Counted for the caller: one caller's counts never serve another. The predicate's token
        # names the readable folders of one snapshot of the rules; the counts are taken with that
        # very predicate and kept under its token, so they only serve a caller reading the same
        # folders (None: reads everything).
        pred = self.access.folder_predicate()
        scope = pred.token if pred is not None else None
        allows = None if pred is None else (lambda k: self.cfg.allows(k) and pred(os.path.dirname(k)))
        # Read once: another request (another caller) may replace the cache between check and use.
        cached = self._overview
        if cached is None or now - cached[0] > worldmap.CACHE_S or cached[1] != scope:
            con = self._con()
            try:
                cached = (now, scope, worldmap.live_overview(con, self.cfg, allows=allows))
            finally:
                con.close()
            self._overview = cached
        return worldmap.compose(worldmap.load_map_text(self.cfg.map_path), cached[2])

    def index_status(self) -> str:
        con = self._con()
        try:
            readable = None  # documents this caller can read, for worlds with per-person access
            ids = None       # for a restricted caller: every count below is taken on these documents
            if self.cfg.access == "acl":
                ids = self._readable_ids(con)
                world_total = con.execute("SELECT count(*) FROM docs").fetchone()[0]
                readable = con.execute(f"SELECT count(*) FROM {ids}").fetchone()[0] if ids else world_total

            def mine(column: str, joined: str = " WHERE ") -> str:
                return f"{joined}{column} IN {ids}" if ids else ""
            by_status = con.execute(f"SELECT status, count(*) n FROM docs{mine('id')} GROUP BY status").fetchall()
            by_ext = con.execute(f"SELECT ext, count(*) n FROM docs WHERE status='ok'{mine('id', ' AND ')} "
                                 "GROUP BY ext").fetchall()
            chunks = con.execute(f"SELECT count(*), sum(embedded) FROM chunks{mine('doc_id')}").fetchone()
            last = store.get_meta(con, "last_index_finished")
            model = store.get_meta(con, "embedding_model")
            waiting = store.get_meta(con, "suggestions_waiting")
            ocr = (con.execute(f"""SELECT sum(status = 'text'), sum(status = 'empty'),
                                   sum(status IN ('error', 'timeout')) FROM ocr_pages{mine('doc_id')}""").fetchone()
                   if _has_ocr_table(con) else None)
            pictures = None
            if store.has_table(con, "picture_uses"):
                pictures = {
                    "status": dict(con.execute(f"SELECT status, count(*) FROM picture_uses{mine('doc_id')} "
                                               "GROUP BY status").fetchall()),
                    "types": con.execute(f"""SELECT type, count(*) n FROM picture_uses WHERE status = 'described'
                                             {mine('doc_id', ' AND ')} GROUP BY type ORDER BY n DESC""").fetchall(),
                    "models": [r[0] for r in con.execute(
                        f"SELECT DISTINCT model FROM picture_docs{mine('doc_id')} ORDER BY model")]}
        finally:
            con.close()
        total = sum(r["n"] for r in by_status)
        lines = ([f"World: {self.cfg.world_title} ({self.cfg.world})"] if self.cfg.world else []) + [
                 f"Indexed documents: {total}",
                 "By status: " + ", ".join(f"{r['status']}={r['n']}" for r in by_status),
                 "Readable by type: " + ", ".join(f"{TYPE_LABEL.get(r['ext'], r['ext'])}={r['n']}" for r in by_ext),
                 f"Chunks: {chunks[0]}, with embeddings: {chunks[1] or 0}"
                 + (f" (model {model})" if model else ""),
                 f"Last full index run finished: {_date(float(last)) if last else 'never'}",
                 "Roots: " + (", ".join(self.cfg.roots) or "none"),
                 "Excluded folders: " + (", ".join(self.cfg.excluded_dirs) or "none"),
                 f"Indexed file types: {', '.join(self.cfg.extensions)} (other types, e.g. Excel, are not searchable yet)"]
        if readable is not None:  # the world's total is the only place hidden documents show up
            access = [f"You can read {readable} of {world_total} documents of this world."]
            if not self.access.loaded:
                access.append("Access rules not loaded yet: only admins can read this world.")
            at = lines.index(f"Indexed documents: {total}") + 1
            lines[at:at] = access
        if self.cfg.profile == "engineering":  # proposals waiting for the user (suggestions.py)
            lines.insert(lines.index(next(l for l in lines if l.startswith("Roots:"))) + 1,
                         f"Keyword suggestions waiting: {waiting or 0}")
        lines.insert(lines.index(next(l for l in lines if l.startswith("Chunks:"))) + 1,
                     f"OCR: {ocr[0] or 0} pages with text, {ocr[1] or 0} without usable text (photos, drawings), "
                     f"{ocr[2] or 0} failed or timed out" if ocr and any(ocr) else "OCR: not run yet")
        if pictures and pictures["status"]:
            s = pictures["status"]
            types = ", ".join(f"{t} {n}" for t, n in pictures["types"])
            line = (f"Pictures: {s.get('described', 0)} described" + (f" ({types})" if types else "")
                    + f", {s.get('small', 0)} too small, {s.get('logo', 0)} logos, "
                    f"{s.get('error', 0) + s.get('timeout', 0)} failed; model {', '.join(pictures['models']) or '?'}")
        else:
            line = "Pictures: not described yet"
        lines.insert(lines.index(next(l for l in lines if l.startswith("OCR:"))) + 1, line)
        return "\n".join(lines)

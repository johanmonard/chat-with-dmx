"""Tool logic exposed to Claude (kept separate from MCP wiring so it is easy to test).

Every tool is read-only and limited to the configured root folders.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime

from . import facets, machines, sources, store
from .config import Config
from .extract import extract_file
from .search import Searcher, build_fts_query, fold, make_snippet

TYPE_LABEL = {".pdf": "PDF", ".docx": "Word", ".doc": "Word 97-2003"}

# Query/passage cosine similarity with multilingual-e5-large, calibrated on the indexed
# documents (short and long queries): the best hit of on-topic queries scores 0.84-0.89,
# of off-topic ones 0.77-0.83; a relevant passage that is not the best hit can score 0.83.
CALIBRATED_MODEL = "intfloat/multilingual-e5-large"
SIM_STRONG = 0.86
SIM_MEDIUM = 0.83


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
        media.sort(key=lambda n: [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", n)])
        shown = [n for n in media if os.path.splitext(n)[1].lower() in
                 (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tif", ".tiff")]
        if not shown:
            raise ValueError("No picture that can be shown in this file"
                             + (f" ({len(media)} vector drawings only)" if media else ""))
        k = 1 if image is None else int(image)
        if not 1 <= k <= len(shown):
            raise ValueError(f"image must be between 1 and {len(shown)}")
        return z.read(shown[k - 1]), os.path.splitext(shown[k - 1])[1].lower(), k, len(shown)


def _docx_picture(fs: str, path: str, image: int | None) -> tuple[str, bytes, str]:
    from .extract import pymupdf

    raw, ext, k, total = _docx_picture_raw(fs, image)
    with pymupdf.open(stream=raw, filetype=ext.lstrip(".")) as img:
        p = img[0]
        zoom = min(1.0, VIEW_MAX_PX / max(p.rect.width, p.rect.height))
        pix = p.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    data, fmt = _encode(pix)
    caption = (f"{path} — picture {k} of {total} embedded in the Word file (document order; Word "
               f"files have no fixed pages), {pix.width}x{pix.height} px. Use image=N for the others.")
    return caption, data, fmt


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
    return "page" if ext == ".pdf" else "page (approx.)"


class DocTools:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.searcher = Searcher(cfg)

    def _con(self):
        con = store.connect(self.cfg.db_path, create=False)
        # Root/excluded folders may have changed on the configuration page.
        sources.refresh(self.cfg, con)
        facets.refresh(con, self.cfg.roots)  # no-op when every document has its facets
        return con

    def _resolve(self, path: str) -> tuple[str, str]:
        path = sources.clean_path(path)  # also turns a mapped drive (N:\...) into the UNC path
        key = store.path_key(path)
        if not self.cfg.allows(key):
            raise ValueError(f"'{path}' is outside the indexed folders (roots: {', '.join(self.cfg.roots)}; "
                             "some subfolders may be excluded)")
        return path, key

    # ------------------------------------------------------------ search
    def search(self, query: str, folder: str | None = None, file_type: str | None = None,
               modified_after: str | None = None, limit: int = 10, mode: str = "hybrid",
               project: str | None = None, doc_type: str | None = None, section: str | None = None,
               collection: str | None = None, machine: str | None = None, expand: bool | None = None) -> str:
        if mode not in ("hybrid", "keyword", "semantic"):
            raise ValueError("mode must be 'hybrid', 'keyword' or 'semantic'")
        limit = max(1, min(int(limit), 30))
        facet_filter = {"project": project, "doc_type": doc_type, "section": section, "collection": collection,
                        "machine": machine}
        con = self._con()
        try:
            if folder:
                folder, _ = self._resolve(folder)
            hits = self.searcher.search(con, query, limit=limit, folder=folder, file_type=file_type,
                                        modified_after=modified_after, mode=mode,
                                        allowed=self.cfg.allows, facets=facet_filter, expand=expand)
            doc_facets = self._facets(con, {h.doc_id for h in hits})
        finally:
            con.close()
        notes = []
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
            lines.append(f"[{i}] {h.path} — {_page_label(h.ext)} {h.page_no}/{h.n_pages} "
                         f"({TYPE_LABEL.get(h.ext, h.ext)}, modified {_date(h.mtime)}) [{match}]")
            if h.doc_id in doc_facets:
                lines.append("    " + doc_facets[h.doc_id])
            lines.append("    " + make_snippet(h.text, query))
            lines.append("")
        return "\n".join(lines).rstrip()

    @staticmethod
    def _facets(con, doc_ids: set[int]) -> dict[int, str]:
        """One line per document: project / collection / section / type (and how the type was found)."""
        if not doc_ids:
            return {}
        ids = list(doc_ids)
        out = {}
        machine_cache: dict[str, str] = {}
        for r in con.execute(f"SELECT * FROM doc_facets WHERE doc_id IN ({','.join('?' * len(ids))})", ids):
            project = r["project"] and (r["project"] + (f" / {r['subproject']}" if r["subproject"] else ""))
            if r["project"] and r["project"] not in machine_cache:
                machine_cache[r["project"]] = machines.describe(con, r["project"], limit=2)
            if r["project"] and machine_cache[r["project"]]:
                project += f" [{machine_cache[r['project']]}]"
            parts = [f"project {project}" if project else None,
                     f"({r['collection']})" if r["collection"] else None,
                     f"section {r['section']}" if r["section"] else None,
                     f"type {r['doc_type']}" + ("" if r["facet_source"] == "folder" else f" (from {r['facet_source']})")
                     if r["doc_type"] else "type unknown"]
            out[r["doc_id"]] = " · ".join(p for p in parts if p)
        return out

    # ----------------------------------------------------------- view_page
    def view_page(self, path: str, page: int = 1, region: str | None = None,
                  image: int | None = None) -> tuple[str, bytes, str]:
        """Render a PDF page (or a region of it) or return a picture embedded in a .docx.
        Returns (caption, image bytes, format). Reads the live file: needs the network share."""
        self._con().close()  # loads the root/excluded folders that _resolve checks against
        path, _ = self._resolve(path)
        fs = store.fs_path(path)
        if not os.path.isfile(fs):
            raise FileNotFoundError(f"File not reachable now: {path} (viewing pages needs access to the file server)")
        ext = os.path.splitext(path)[1].lower()
        if ext == ".pdf":
            return _render_pdf_page(fs, path, int(page), region)
        if ext == ".docx":
            return _docx_picture(fs, path, image)
        raise ValueError(f"Pictures of {TYPE_LABEL.get(ext, ext)} files cannot be shown "
                         "(only PDF pages and pictures embedded in .docx files).")

    # -------------------------------------------------------- export_image
    def export_image(self, path: str, page: int = 1, region: str | None = None,
                     image: int | None = None, name: str | None = None) -> str:
        """Save a PDF page/region rendering or a .docx picture as an image file in the export
        folder (default C:\\dmx-rag\\exports), e.g. to put it on slides."""
        self._con().close()  # loads the root/excluded folders that _resolve checks against
        path, _ = self._resolve(path)
        fs = store.fs_path(path)
        if not os.path.isfile(fs):
            raise FileNotFoundError(f"File not reachable now: {path} (needs access to the file server)")
        ext = os.path.splitext(path)[1].lower()
        stem = _safe_name(os.path.splitext(os.path.basename(path))[0], 50)
        if ext == ".pdf":
            _, data, fmt = _render_pdf_page(fs, path, int(page), region, max_px=EXPORT_MAX_PX)
            default = f"{stem}_p{int(page)}" + ("_zoom" if region else "")
            out_ext = "." + ("jpg" if fmt == "jpeg" else fmt)
            what = f"page {page}" + (f", region {region}" if region else "")
        elif ext == ".docx":
            data, out_ext, k, total = _docx_picture_raw(fs, image)  # original resolution
            default, what = f"{stem}_img{k}", f"picture {k} of {total}"
        else:
            raise ValueError(f"Images can only be exported from PDF pages and .docx files, not {ext}")
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
        path, _ = self._resolve(path)
        ext = os.path.splitext(path)[1].lower()
        if ext not in TYPE_LABEL:  # never launch anything but an indexed document type
            raise ValueError(f"Only {', '.join(TYPE_LABEL)} files can be opened")
        fs = store.fs_path(path)
        if not os.path.isfile(fs):
            raise FileNotFoundError(f"File not reachable now: {path}")
        how = _open_file(path, ext, page)
        return f"Opened {path} {how}."

    # ------------------------------------------------------- list_projects
    def list_projects(self, name: str | None = None, collection: str | None = None,
                      machine: str | None = None, limit: int = 300) -> str:
        limit = max(1, min(int(limit), 1000))
        con = self._con()
        try:
            where, having, params = "", "", []
            if collection:
                where = " AND coalesce(f.collection, '') LIKE ?"
                params.append(f"%{collection}%")
            if machine:  # a family ("Paloma") or a model ("Paloma 4R"), comma-separated
                values = [v.strip() for v in machine.split(",") if v.strip()]
                marks = ",".join("?" * len(values))
                where += (f" AND f.project IN (SELECT project FROM project_machines pm WHERE "
                          f"{machines.confirmed_sql()} AND (family COLLATE NOCASE IN ({marks}) "
                          f"OR model COLLATE NOCASE IN ({marks})))")
                params += values + values
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
            machine_text = {r["project"]: machines.describe(con, r["project"]) for r in rows}
        finally:
            con.close()
        if not rows:
            return "No matching project in the index."
        lines = [f"{len(rows)} projects (project | collection | indexed documents | last modified | "
                 "machines found in the documents | sections):"]
        for r in rows:
            subs = f" | sub-projects: {', '.join(sorted(r['subprojects'].split(',')))}" if r["subprojects"] else ""
            lines.append(f"- {r['project']} | {r['collection'] or 'current'} | {r['n']} docs | {_date(r['last'])} | "
                         f"{machine_text[r['project']] or 'machine unknown'} | {r['sections'] or '-'}{subs}")
        if machine:
            lines.append("(machine types come from file names and from offers, specifications, FAT/SAT... "
                         "a project whose documents never name its machine is not listed)")
        return "\n".join(lines)

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
                _, key = self._resolve(folder)
                prefix = key.rstrip(os.sep) + os.sep
                clauses.append("substr(d.path_key, 1, ?) = ?")
                params += [len(prefix), prefix]
            where = " AND ".join(["docs_fts MATCH ?"] + clauses)
            rows = [r for r in con.execute(
                f"""SELECT d.path, d.path_key, d.ext, d.size, d.mtime, d.n_pages, d.status FROM docs_fts f
                    JOIN docs d ON d.id = f.rowid WHERE {where} ORDER BY f.rank LIMIT ?""",
                [fts] + params + [limit * 3]) if self.cfg.allows(r["path_key"])][:limit]
        finally:
            con.close()
        if not rows:
            return f"No indexed file matches '{name}'."
        lines = [f"{len(rows)} files matching '{name}':"]
        for r in rows:
            extra = "" if r["status"] == "ok" else f", status: {r['status']}"
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
                for root, rk in zip(self.cfg.roots, self.cfg.root_keys):
                    prefix = rk.rstrip(os.sep) + os.sep
                    n = con.execute("SELECT count(*) FROM docs WHERE substr(path_key, 1, ?) = ?",
                                    (len(prefix), prefix)).fetchone()[0]
                    state = "reachable" if os.path.isdir(root) else "NOT reachable"
                    lines.append(f"- {root} ({n} indexed documents, {state})")
                if self.cfg.excluded_dirs:
                    lines.append("Excluded folders: " + ", ".join(self.cfg.excluded_dirs))
                return "\n".join(lines)
            path, key = self._resolve(path)
            indexed = {r["path_key"]: r for r in con.execute(
                "SELECT path_key, status, n_pages FROM docs WHERE folder_key = ?", (key,))}
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
                    if not self.cfg.is_excluded_dir(store.path_key(e.path)):
                        folders.append(f"[folder] {e.name}")
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
        """Return ({page_no: text}, n_pages, info). Uses the index, or extracts live if needed."""
        con = self._con()
        try:
            doc = con.execute("SELECT * FROM docs WHERE path_key = ?", (key,)).fetchone()
            info = {"source": "index"}
            fresh = False
            if doc is not None:
                try:
                    st = os.stat(store.fs_path(path))
                    fresh = st.st_size == doc["size"] and abs(st.st_mtime - (doc["mtime"] or 0)) < 0.01
                except OSError:
                    fresh = True  # file not reachable: serve the indexed copy
                if fresh and doc["status"] in ("ok", "no_text", "empty"):
                    pages: dict[int, str] = {}
                    for r in con.execute("SELECT page_no, text FROM chunks WHERE doc_id = ? "
                                         "ORDER BY page_no, seq", (doc["id"],)):
                        pages[r["page_no"]] = pages.get(r["page_no"], "") + r["text"]
                    info.update(ext=doc["ext"], mtime=doc["mtime"], status=doc["status"])
                    return pages, doc["n_pages"] or 0, info
        finally:
            con.close()
        if not os.path.isfile(store.fs_path(path)):
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
        header = (f"{path} ({TYPE_LABEL.get(info.get('ext'), info.get('ext'))}, {last} pages, "
                  f"modified {_date(info.get('mtime'))}; source: {info['source']})")
        if info.get("status") == "no_text":
            header += "\nNote: this PDF has (almost) no text layer — probably a scan; content may be missing."
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
        if not hits:
            return f"'{text}' not found in {path} ({n_pages} pages; search ignores case and accents)."
        more = f" (showing first {len(hits)})" if total > len(hits) else ""
        return f"{total} occurrences of '{text}' in {path}{more}:\n\n" + "\n\n".join(hits)

    # ------------------------------------------------------------ status
    def index_status(self) -> str:
        con = self._con()
        try:
            by_status = con.execute("SELECT status, count(*) n FROM docs GROUP BY status").fetchall()
            by_ext = con.execute("SELECT ext, count(*) n FROM docs WHERE status='ok' GROUP BY ext").fetchall()
            chunks = con.execute("SELECT count(*), sum(embedded) FROM chunks").fetchone()
            last = store.get_meta(con, "last_index_finished")
            model = store.get_meta(con, "embedding_model")
        finally:
            con.close()
        total = sum(r["n"] for r in by_status)
        lines = [f"Indexed documents: {total}",
                 "By status: " + ", ".join(f"{r['status']}={r['n']}" for r in by_status),
                 "Readable by type: " + ", ".join(f"{TYPE_LABEL.get(r['ext'], r['ext'])}={r['n']}" for r in by_ext),
                 f"Chunks: {chunks[0]}, with embeddings: {chunks[1] or 0}"
                 + (f" (model {model})" if model else ""),
                 f"Last full index run finished: {_date(float(last)) if last else 'never'}",
                 "Roots: " + (", ".join(self.cfg.roots) or "none"),
                 "Excluded folders: " + (", ".join(self.cfg.excluded_dirs) or "none"),
                 f"Indexed file types: {', '.join(self.cfg.extensions)} (other types, e.g. Excel, are not searchable yet)"]
        return "\n".join(lines)

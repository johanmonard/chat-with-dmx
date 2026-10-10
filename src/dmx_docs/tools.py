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

from . import facets, machines, register, sources, store
from .config import Config
from .extract import extract_file
from .search import Searcher, build_fts_query, fold, make_snippet

TYPE_LABEL = {".pdf": "PDF", ".docx": "Word", ".doc": "Word 97-2003",
              ".pptx": "PowerPoint", ".ppt": "PowerPoint 97-2003", ".md": "Markdown"}
SLIDE_EXTS = (".pptx", ".ppt")

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


def resolve_md_link(target: str, md_path: str, roots: list[str]) -> str | None:
    """File a Markdown picture link points to, or None. The machine manual uses absolute drive
    paths (O:/ASA/.../Source/...): tried through this PC's drive mapping, then rebuilt from an
    indexed root folder whose last folder names appear in the link (drives differ between PCs)."""
    import urllib.parse

    t = urllib.parse.unquote(target.strip()).replace("/", "\\")
    candidates: list[str] = []
    if re.match(r"^[A-Za-z]:\\", t):
        candidates.append(sources.to_unc(t))
        low = t.lower()
        for root in roots:
            parts = [p for p in root.rstrip("\\").split("\\") if p]
            for n in range(min(3, len(parts)), 0, -1):
                tail = "\\" + "\\".join(parts[-n:]).lower() + "\\"
                i = low.find(tail)
                if i >= 0:
                    candidates.append(root.rstrip("\\") + "\\" + t[i + len(tail):])
                    break
    elif t.startswith("\\\\"):
        candidates.append(t)
    else:
        candidates.append(os.path.normpath(os.path.join(os.path.dirname(md_path), t)))
    return next((c for c in candidates if os.path.isfile(store.fs_path(c))), None)


def _md_picture_raw(fs: str, path: str, image: int | None, cfg: Config) -> tuple[bytes, str, int, int, str]:
    """Original bytes of the image-th picture linked from a Markdown file:
    (data, extension, number, pictures in the file, caption)."""
    from .extract import PICTURE_EXTS, md_pictures

    with open(fs, "rb") as f:
        pictures = md_pictures(f.read().decode("utf-8-sig", errors="replace"))
    if not pictures:
        raise ValueError("No picture in this Markdown file")
    k = 1 if image is None else int(image)
    if not 1 <= k <= len(pictures):
        raise ValueError(f"image must be between 1 and {len(pictures)}")
    caption, target = pictures[k - 1]
    found = resolve_md_link(target, path, cfg.roots)
    if found is None:
        raise FileNotFoundError(f"Picture {k} of {path} not found: {target}")
    if not cfg.allows(store.path_key(found)):
        raise ValueError(f"Picture {k} is outside the indexed folders: {found}")
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
    return "page" if ext in (".pdf", ".md") else "page (approx.)"


def _units(ext: str) -> str:
    return "slides" if ext in SLIDE_EXTS else "pages"


def _has_ocr_table(con) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'ocr_pages'").fetchone() is not None


def _ocr_text_pages(con, doc_ids) -> set[tuple[int, int]]:
    """(doc_id, page_no) of pages whose text comes from OCR. Empty for indexes without OCR."""
    ids = list(doc_ids)
    if not ids or not _has_ocr_table(con):
        return set()
    return {(r[0], r[1]) for r in con.execute(
        f"SELECT doc_id, page_no FROM ocr_pages WHERE status = 'text' AND doc_id IN ({','.join('?' * len(ids))})", ids)}


class DocTools:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.searcher = Searcher(cfg)

    def _con(self):
        con = store.connect(self.cfg.db_path, create=False)
        # Root/excluded folders may have changed on the configuration page.
        sources.refresh(self.cfg, con)
        facets.refresh(con, self.cfg.roots, self.cfg.profile)  # no-op when every document has its facets
        if self.cfg.profile == "projects":  # the machine register belongs to the project documentation
            register.refresh(con, self.cfg.register_path)  # no-op when neither the file nor the documents changed
        return con

    def _resolve(self, path: str) -> tuple[str, str]:
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
        for root in self.cfg.roots:
            base = root.rstrip("\\/").split("\\")[-1]
            tail = rel[len(base):].lstrip("\\") if rel.lower() == base.lower() or rel.lower().startswith(
                base.lower() + "\\") else rel
            cand = os.path.join(root, tail) if tail else root
            key = store.path_key(cand)
            if self.cfg.allows(key) and con.execute(
                    "SELECT 1 FROM docs WHERE substr(path_key, 1, ?) = ? LIMIT 1",
                    (len(key) + 1, key.rstrip(os.sep) + os.sep)).fetchone():
                return cand, key
        needle = os.sep + os.path.normcase(rel) + os.sep
        row = con.execute("SELECT path, path_key FROM docs WHERE instr(path_key, ?) > 0 "
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
               language: str | None = None) -> str:
        if mode not in ("hybrid", "keyword", "semantic"):
            raise ValueError("mode must be 'hybrid', 'keyword' or 'semantic'")
        limit = max(1, min(int(limit), 30))
        facet_filter = {"project": project, "doc_type": doc_type, "section": section, "collection": collection,
                        "machine": machine, "client": client, "country": country, "category": category,
                        "language": language}
        con = self._con()
        try:
            if folder:
                folder, _ = self._resolve_folder(con, folder)
            hits = self.searcher.search(con, query, limit=limit, folder=folder, file_type=file_type,
                                        modified_after=modified_after, mode=mode,
                                        allowed=self.cfg.allows, facets=facet_filter, expand=expand)
            doc_facets = self._facets(con, {h.doc_id for h in hits})
            ocr_pages = _ocr_text_pages(con, {h.doc_id for h in hits})
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
            ocr_mark = " (OCR)" if (h.doc_id, h.page_no) in ocr_pages else ""
            lines.append(f"[{i}] {h.path} — {_page_label(h.ext)} {h.page_no}/{h.n_pages}{ocr_mark} "
                         f"({TYPE_LABEL.get(h.ext, h.ext)}, modified {_date(h.mtime)}) [{match}]")
            if h.doc_id in doc_facets:
                lines.append("    " + doc_facets[h.doc_id])
            lines.append("    " + make_snippet(h.text, query))
            lines.append("")
        return "\n".join(lines).rstrip()

    def _facets(self, con, doc_ids: set[int]) -> dict[int, str]:
        """One line per document: project / collection / section / type (projects), category
        (marketing), category and language (documentation)."""
        if not doc_ids or self.cfg.profile == "none":
            return {}
        ids = list(doc_ids)
        if self.cfg.profile in ("marketing", "documentation"):
            out = {}
            for r in con.execute(f"SELECT * FROM doc_facets WHERE doc_id IN ({','.join('?' * len(ids))})", ids):
                line = f"category {r['category'] or 'unknown'}"
                if self.cfg.profile == "documentation":
                    line += f" · language {r['language'] or 'unknown'}"
                out[r["doc_id"]] = line
            return out
        out = {}
        machine_cache: dict[str, str] = {}
        for r in con.execute(f"SELECT * FROM doc_facets WHERE doc_id IN ({','.join('?' * len(ids))})", ids):
            project = r["project"] and (r["project"] + (f" / {r['subproject']}" if r["subproject"] else ""))
            if r["project"] and r["project"] not in machine_cache:
                machine_cache[r["project"]] = machines.describe(con, r["project"], limit=2)
                client = con.execute("SELECT client FROM register_machines WHERE project = ? AND client != '' "
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

    # ----------------------------------------------------------- view_page
    def view_page(self, path: str, page: int = 1, region: str | None = None,
                  image: int | None = None) -> tuple[str, bytes, str]:
        """Render a PDF page (or a region of it) or return a picture embedded in a .docx or on a
        PowerPoint slide.
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
        if ext in SLIDE_EXTS:
            raw, pic_ext, k, total, n = _slide_picture_raw(fs, path, int(page), image, self.cfg.extract_options())
            data, fmt, w, h = _scaled_picture(raw, pic_ext)
            return (f"{path} — picture {k} of {total} on slide {page}/{n}, {w}x{h} px. Use image=N for "
                    "the others (whole slides cannot be shown, only their pictures)."), data, fmt
        if ext == ".md":
            raw, pic_ext, k, total, caption = _md_picture_raw(fs, path, image, self.cfg)
            data, fmt, w, h = _scaled_picture(raw, pic_ext)
            label = f' "{caption}"' if caption else ""
            return (f"{path} — picture {k} of {total}{label} (the [Image {k}] marker in the text), "
                    f"{w}x{h} px. Use image=N for the others."), data, fmt
        raise ValueError(f"Pictures of {TYPE_LABEL.get(ext, ext)} files cannot be shown "
                         "(only PDF pages and pictures in .docx, PowerPoint and Markdown files).")

    # -------------------------------------------------------- export_image
    def export_image(self, path: str, page: int = 1, region: str | None = None,
                     image: int | None = None, name: str | None = None) -> str:
        """Save a PDF page/region rendering or a .docx or PowerPoint picture as an image file in the export
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
        elif ext in SLIDE_EXTS:
            data, out_ext, k, total, _ = _slide_picture_raw(fs, path, int(page), image, self.cfg.extract_options())
            default, what = f"{stem}_s{int(page)}_img{k}", f"picture {k} of {total} on slide {page}"
        elif ext == ".md":
            data, out_ext, k, total, _ = _md_picture_raw(fs, path, image, self.cfg)  # original file
            default, what = f"{stem}_img{k}", f"picture {k} of {total}"
        else:
            raise ValueError(f"Images can only be exported from PDF pages and pictures in .docx, "
                             f"PowerPoint and Markdown files, not {ext}")
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
                      machine: str | None = None, limit: int = 300, client: str | None = None,
                      country: str | None = None, year_from: int | None = None, year_to: int | None = None) -> str:
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
                where += (f" AND (f.project IN (SELECT project FROM project_machines pm WHERE "
                          f"{machines.confirmed_sql()} AND (family COLLATE NOCASE IN ({marks}) "
                          f"OR model COLLATE NOCASE IN ({marks}))) OR f.project IN (SELECT project FROM "
                          f"register_machines WHERE family_ok = 1 AND (family COLLATE NOCASE IN ({marks}) "
                          f"OR (model_ok = 1 AND model COLLATE NOCASE IN ({marks})))))")
                params += values * 4
            reg_cond, reg_params = self._register_filter(client, country, year_from, year_to)
            if reg_cond:
                where += f" AND f.project IN (SELECT rm.project FROM register_machines rm WHERE {reg_cond})"
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
            machine_text = {r["project"]: machines.describe(con, r["project"]) for r in rows}
            client_text = {r["project"]: register.summary(con, r["project"]) for r in rows}
            unindexed = []
            if reg_cond or (machine and not collection):  # delivered machines whose documents are not indexed
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
    def _register_filter(client, country, year_from, year_to) -> tuple[str, list]:
        conds, params = [], []
        if client:
            c, p = register.client_clause(client)
            conds.append(c)
            params += p
        if country:
            c, p = register.country_clause(country)
            conds.append(c)
            params += p
        if year_from:
            conds.append("rm.year >= ?")
            params.append(int(year_from))
        if year_to:
            conds.append("rm.year <= ?")
            params.append(int(year_to))
        return " AND ".join(conds), params

    # -------------------------------------------------------- project_card
    def project_card(self, project: str) -> str:
        """Everything known about one project: client, delivered machines, order numbers, documents."""
        con = self._con()
        try:
            hit = con.execute("SELECT project FROM doc_facets WHERE project = ? COLLATE NOCASE "
                              "OR subproject = ? COLLATE NOCASE LIMIT 1", (project, project)).fetchone()
            name = hit[0] if hit else None
            reg = con.execute(
                "SELECT * FROM register_machines WHERE project = ? ORDER BY order_no, serial" if name else
                "SELECT * FROM register_machines WHERE name = ? COLLATE NOCASE ORDER BY order_no, serial",
                (name or project,)).fetchall()
            if not name and not reg:
                like = con.execute("""SELECT DISTINCT project FROM doc_facets WHERE project LIKE ? UNION
                                      SELECT DISTINCT name FROM register_machines WHERE name LIKE ? LIMIT 10""",
                                   (f"%{project}%", f"%{project}%")).fetchall()
                return (f"No project '{project}' in the index or the machine register."
                        + (f" Similar names: {', '.join(r[0] for r in like)}" if like else ""))
            lines = []
            if name:
                n, first, last, coll = con.execute(
                    """SELECT count(*), min(d.mtime), max(d.mtime), max(f.collection) FROM doc_facets f
                       JOIN docs d ON d.id = f.doc_id WHERE f.project = ?""", (name,)).fetchone()
                subs = [r[0] for r in con.execute("SELECT DISTINCT subproject FROM doc_facets WHERE project = ? "
                                                  "AND subproject IS NOT NULL ORDER BY 1", (name,))]
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
                found = machines.describe(con, name, limit=6)
                if found:
                    lines.append(f"\nMachines named in the documents: {found}")
            if reg:
                lines.append(f"\nMachines delivered (machine register, {len(reg)}; where it disagrees with the "
                             "documents, the documents are more reliable - see ⚠):")
                for r in reg:
                    bits = [r["serial"] or r["order_no"] or "no serial", r["model"] or "model ?"]
                    if r["year"]:
                        bits.append(str(r["year"]))
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
                    lines.append("- " + " · ".join(bits) + (f"\n    ⚠ {r['doc_note']}" if r["doc_note"] else ""))
            if name:
                moved = con.execute("SELECT serial, order_no, model, project FROM register_machines "
                                    "WHERE reg_project = ?", (name,)).fetchall()
                if moved:
                    lines.append("\nFiled under this project in the register, but their numbers are in another "
                                 "project's documents (counted there): " + "; ".join(
                                     f"{m['serial'] or m['order_no']} {m['model'] or ''} -> {m['project']}"
                                     for m in moved))
                evidence = {}
                for (path,) in con.execute("SELECT d.path FROM docs d JOIN doc_facets f ON f.doc_id = d.id "
                                           "WHERE f.project = ?", (name,)):
                    for num in register.numbers(path):
                        evidence[num] = evidence.get(num, 0) + 1
                known = {n for r in reg for n in register.numbers(r["order_no"], r["serial"])}
                if evidence or known:
                    nums = sorted(set(evidence) | known, key=lambda k: -evidence.get(k, 0))[:8]
                    lines.append("Order / serial numbers: " + ", ".join(
                        f"{k} ({evidence[k]} file names)" if k in evidence else f"{k} (register)" for k in nums))
                lines.append("\nDocuments by type:")
                for dt, cnt, newest in con.execute(
                        """SELECT coalesce(f.doc_type, 'unknown'), count(*), max(d.mtime) FROM doc_facets f
                           JOIN docs d ON d.id = f.doc_id WHERE f.project = ? GROUP BY 1 ORDER BY 2 DESC""", (name,)):
                    lines.append(f"- {dt}: {cnt} (newest {_date(newest)})")
                lines.append("\nKey documents (newest of each kind):")
                for dt in ("offre", "commande", "cahier_des_charges", "fat", "sat", "mise_en_service",
                           "reception", "cloture"):
                    r = con.execute("""SELECT d.path, d.mtime FROM doc_facets f JOIN docs d ON d.id = f.doc_id
                                       WHERE f.project = ? AND f.doc_type = ? ORDER BY d.mtime DESC LIMIT 1""",
                                    (name, dt)).fetchone()
                    if r:
                        lines.append(f"- {dt}: {r['path']} ({_date(r['mtime'])})")
            clients = sorted({r["client"] for r in reg if r["client"]})
            groups = sorted({r["grp"] for r in reg if r["grp"]})
            if clients:
                others = con.execute(
                    f"""SELECT coalesce(project, name) p, group_concat(DISTINCT model), min(year), max(project IS NOT NULL)
                        FROM register_machines WHERE (client IN ({','.join('?' * len(clients))})
                        OR grp IN ({','.join('?' * len(groups)) or "''"}))
                        AND coalesce(project, name) != ? GROUP BY 1 ORDER BY 3 DESC LIMIT 25""",
                    clients + groups + [name or reg[0]["name"]]).fetchall()
                if others:
                    lines.append("\nOther projects for the same client or group: " + "; ".join(
                        f"{p} ({m or 'model ?'}, {y or '?'}{'' if idx else ', not indexed'})"
                        for p, m, y, idx in others))
        finally:
            con.close()
        lines.append("\n(client, machines delivered and years come from the company's machine register, a "
                     "hand-made list: when it disagrees with the documents, trust the documents; the rest "
                     "comes from the indexed documents)")
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
                _, key = self._resolve_folder(con, folder)
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
            path, key = self._resolve_folder(con, path)
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
                    ocr_pages = sorted(p for _, p in _ocr_text_pages(con, [doc["id"]]))
                    info.update(ext=doc["ext"], mtime=doc["mtime"], status=doc["status"], ocr_pages=ocr_pages)
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
        header = (f"{path} ({TYPE_LABEL.get(info.get('ext'), info.get('ext'))}, {last} {_units(info.get('ext', ''))}, "
                  f"modified {_date(info.get('mtime'))}; source: {info['source']})")
        if info.get("status") == "no_text":
            header += "\nNote: this PDF has (almost) no text layer — probably a scan; content may be missing."
        ocr_shown = [p for p in info.get("ocr_pages", ()) if start_page <= p <= shown_until]  # OCR pages in this output
        if ocr_shown:
            shown = ", ".join(map(str, ocr_shown[:30])) + (" ..." if len(ocr_shown) > 30 else "")
            header += (f"\nNote: text of {label} {shown} was recognized by OCR (scanned page): it may contain "
                       "recognition errors - check codes and numbers with view_page.")
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
            ocr = (con.execute("""SELECT sum(status = 'text'), sum(status = 'empty'),
                                  sum(status IN ('error', 'timeout')) FROM ocr_pages""").fetchone()
                   if _has_ocr_table(con) else None)
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
        lines.insert(lines.index(next(l for l in lines if l.startswith("Chunks:"))) + 1,
                     f"OCR: {ocr[0] or 0} pages with text, {ocr[1] or 0} without usable text (photos, drawings), "
                     f"{ocr[2] or 0} failed or timed out" if ocr and any(ocr) else "OCR: not run yet")
        return "\n".join(lines)

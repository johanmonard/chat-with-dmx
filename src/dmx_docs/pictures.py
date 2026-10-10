"""Picture occurrences of one document, numbered exactly like view_page / export_image, scaled
for the vision model and with a short context. Runs in worker processes (describe.py): plain
arguments and results only, no database access.

  Markdown     each picture link that resolves inside the indexed folders: page 1, image N of [Image N]
  Word .docx   each picture of word/media (docx_picture_names): page 1, image N
  PowerPoint   each picture of each slide (pptx_pictures; .ppt converted first): slide S, image N
  PDF          a whole page, rendered, when it is a picture page (is_picture_page): page P, image 0
  picture file the file itself: page 1, image 1
"""

from __future__ import annotations

import hashlib
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .extract import PICTURE_EXTS, convert_ppt, docx_picture_names, md_pictures, pptx_pictures, pymupdf
from .store import fs_path

PICTURE_PX = 1024      # pictures sent to the model: longer side
PAGE_PX = 1400         # whole PDF pages: longer side (brochure text stays legible)
MIN_PAGE_CHARS = 50    # a PDF page with at least this much non-OCR text has a text layer
TEXT_PAGE_CHARS = 600  # a page without text layer is a text scan (left to OCR) from this much OCR text
MAX_PICTURE_BYTES = 100 * 2**20
CONTEXT_CHARS = 800
_MD_IMAGE = re.compile(r"!\[([^\]]*)\]\(((?:[^()]|\([^()]*\))*)\)")   # as extract._MD_IMAGE


@dataclass
class Prepared:
    page_no: int
    image_no: int              # 0: the whole PDF page (or, with page_no 0, the whole document failed)
    status: str                # ready | small | error
    sha1: str | None = None    # of the original picture bytes (of the rendered page for PDF pages)
    image: bytes | None = None  # scaled, ready for the model
    context: str = ""
    error: str | None = None


def scale_picture(raw: bytes, max_px: int) -> tuple[bytes, int, int]:
    """A picture as a JPEG of at most max_px on its longer side, as a viewer shows it (turned by its
    EXIF orientation, transparency on white, CMYK in RGB, as view_page does), and its original width
    and height in pixels."""
    pix = pymupdf.Pixmap(raw)
    w, h = pix.width, pix.height
    with pymupdf.open(stream=raw) as doc:
        page = doc[0]
        zoom = min(max_px, max(w, h)) / max(page.rect.width, page.rect.height)
        shown = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return shown.tobytes("jpg", jpg_quality=90), w, h


def _clip(text: str, limit: int = CONTEXT_CHARS) -> str:
    return " ".join(text.split())[:limit]


def _base_context(job: dict) -> list[str]:
    lines = [f"Document : {job.get('title') or os.path.basename(job['path'])}"]
    if job.get("category"):
        lines.append(f"Catégorie : {job['category']}")
    return lines


def md_context(text: str, k: int) -> str:
    """Nearest heading above the k-th picture of a Markdown text, and the paragraph just before it."""
    matches = list(_MD_IMAGE.finditer(text))
    before = text[:matches[k - 1].start()] if 0 < k <= len(matches) else text
    heading = ""
    for line in before.splitlines():
        if line.lstrip().startswith("#") and re.search(r"[^\W\d_]", line):
            heading = line.strip().strip("#").strip()
    blocks = [b.strip() for b in re.split(r"\n\s*\n", _MD_IMAGE.sub("", before)) if b.strip()]
    blocks = [b for b in blocks if not b.lstrip().startswith("#")]
    para = " ".join(blocks[-1].split())[-400:] if blocks else ""
    return f"Section : {heading}\nTexte juste avant l'image : {para}"


def _read(path: str) -> bytes:
    fs = fs_path(path)
    if os.path.getsize(fs) > MAX_PICTURE_BYTES:
        raise ValueError(f"too large (over {MAX_PICTURE_BYTES // 2**20} MB)")
    with open(fs, "rb") as f:
        return f.read()


def _occurrence(page_no: int, image_no: int, load, context: str, options: dict) -> Prepared:
    """One picture; load() returns its original bytes. Unreadable: an 'error' occurrence."""
    try:
        raw = load()
        image, w, h = scale_picture(raw, PICTURE_PX)
        if max(w, h) < options["min_side"]:
            return Prepared(page_no, image_no, "small")
        return Prepared(page_no, image_no, "ready", sha1=hashlib.sha1(raw).hexdigest(), image=image, context=context)
    except Exception as e:  # noqa: BLE001 - corrupt or unknown picture format
        return Prepared(page_no, image_no, "error", error=f"{type(e).__name__}: {e}"[:300])


def _markdown(job: dict, options: dict) -> list[Prepared]:
    from .config import Config
    from .tools import resolve_md_link

    sources = Config(roots=options["roots"], data_dir=Path("."), excluded_dirs=options["excluded_dirs"])
    with open(fs_path(job["path"]), "rb") as f:
        text = f.read().decode("utf-8-sig", errors="replace")
    out = []
    for k, (caption, target) in enumerate(md_pictures(text), 1):
        found = resolve_md_link(target, job["path"], sources.roots, allows=sources.allows)
        if found is None or os.path.splitext(found)[1].lower() not in PICTURE_EXTS:
            continue  # broken, outside the indexed folders or not a picture: not an occurrence (as in view_page)
        context = "\n".join(_base_context(job) + [f"Légende : {caption or '—'}", md_context(text, k)])
        out.append(_occurrence(1, k, lambda p=found: _read(p), context, options))
    return out


def _word(job: dict, options: dict) -> list[Prepared]:
    import zipfile

    context = "\n".join(_base_context(job))
    with zipfile.ZipFile(fs_path(job["path"])) as z:
        return [_occurrence(1, k, lambda n=name: z.read(n), context, options)
                for k, name in enumerate(docx_picture_names(z), 1)]


def _slides(job: dict, options: dict) -> list[Prepared]:
    with tempfile.TemporaryDirectory(prefix="dmx_describe_") as tmp:
        src = fs_path(job["path"])
        if job["ext"] == ".ppt":
            src = convert_ppt(job["path"], tmp, options.get("doc_converter", "auto"), options.get("libreoffice_path"))
        slides = pptx_pictures(src)
    out = []
    for s, pictures in enumerate(slides, 1):
        context = "\n".join(_base_context(job) + [f"Texte de la diapositive : {_clip(job['pages'].get(s, ''))}"])
        for k, (data, _ext) in enumerate(pictures, 1):
            out.append(_occurrence(s, k, lambda d=data: d, context, options))
    return out


def picture_share(page, min_side: int) -> float:
    """Share of the page covered by raster pictures of at least min_side pixels (capped at 1)."""
    area = abs(page.rect) or 1.0
    covered = sum(abs(pymupdf.Rect(info["bbox"]) & page.rect) for info in page.get_image_info()
                  if max(info["width"], info["height"]) >= min_side)
    return min(1.0, covered / area)


def is_picture_page(page, native_chars: int, ocr_chars: int | None, options: dict) -> bool:
    """With a text layer: pictures cover at least pdf_picture_share of the page (brochure photos,
    diagrams). Without: OCR found less than TEXT_PAGE_CHARS (posters, photos, drawings; a scanned
    letter or form is left to OCR). A page OCR has not read yet waits for it when OCR is on (OCR
    then makes describe look at the document again); with OCR off it counts as no OCR text."""
    if native_chars >= MIN_PAGE_CHARS:
        return picture_share(page, options["min_side"]) >= options["pdf_picture_share"]
    if ocr_chars is None and options.get("ocr_on"):
        return False
    return (ocr_chars or 0) < TEXT_PAGE_CHARS


def _pdf(job: dict, options: dict) -> list[Prepared]:
    out = []
    with pymupdf.open(fs_path(job["path"])) as doc:
        if doc.needs_pass:
            raise ValueError("password-protected PDF")
        for i in range(min(doc.page_count, options["max_pdf_pages"])):
            p = i + 1
            page = doc[i]
            try:
                if not is_picture_page(page, job["native_chars"].get(p, 0), job["ocr_chars"].get(p), options):
                    continue
                zoom = PAGE_PX / max(page.rect.width, page.rect.height)
                data = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False).tobytes("jpg", jpg_quality=90)
            except Exception as e:  # noqa: BLE001 - a broken page
                out.append(Prepared(p, 0, "error", error=f"{type(e).__name__}: {e}"[:300]))
                continue
            context = "\n".join(_base_context(job) + [f"Texte de la page : {_clip(job['pages'].get(p, ''))}"])
            out.append(Prepared(p, 0, "ready", sha1=hashlib.sha1(data).hexdigest(), image=data, context=context))
    return out


def _picture_file(job: dict, options: dict) -> list[Prepared]:
    parts = os.path.normpath(job["path"]).split(os.sep)
    lines = [f"Fichier : {job.get('title') or parts[-1]}", "Dossiers : " + " / ".join(parts[-3:-1])]
    if job.get("category"):
        lines.append(f"Catégorie : {job['category']}")
    return [_occurrence(1, 1, lambda: _read(job["path"]), "\n".join(lines), options)]


def prepare_document(job: dict, options: dict) -> list[Prepared]:
    """All picture occurrences of one document. A document that cannot be opened gives one
    Prepared(0, 0, 'error')."""
    crash = os.environ.get("DMX_DOCS_TEST_CRASH_SUFFIX")  # tests: a document that kills its worker process
    if crash and job["path"].endswith(crash):
        os._exit(3)
    hang = os.environ.get("DMX_DOCS_TEST_HANG_SUFFIX")    # tests: a document that never finishes
    if hang and job["path"].endswith(hang):
        time.sleep(3600)
    ext = job["ext"]
    try:
        if ext == ".md":
            return _markdown(job, options)
        if ext == ".docx":
            return _word(job, options)
        if ext in (".pptx", ".ppt"):
            return _slides(job, options)
        if ext == ".pdf":
            return _pdf(job, options)
        if ext in PICTURE_EXTS:
            return _picture_file(job, options)
    except Exception as e:  # noqa: BLE001 - unreadable file or share not reachable
        return [Prepared(0, 0, "error", error=f"{type(e).__name__}: {e}"[:300])]
    return []

"""Text extraction from PDF, DOCX and DOC files.

`extract_file` runs in worker processes, so it only takes and returns plain,
picklable values.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field

try:
    import pymupdf
except ImportError:  # older PyMuPDF releases
    import fitz as pymupdf

# Keep MuPDF diagnostics off stdout: the MCP server talks JSON-RPC over stdout.
try:
    pymupdf.TOOLS.mupdf_display_errors(False)
    pymupdf.TOOLS.mupdf_display_warnings(False)
except Exception:
    pass

# Approximate size of a Word "page" when the file has no page-break markers.
DOCX_PAGE_CHARS = 3000
# Below this many characters per page on average, a PDF is considered to have
# no text layer (a scan). Scans usually have none at all, or a stray header.
MIN_PDF_CHARS_PER_PAGE = 15


@dataclass
class Extracted:
    status: str  # ok | no_text | empty | error | skipped
    pages: list[tuple[int, str]] = field(default_factory=list)  # (1-based page, text)
    n_pages: int = 0
    title: str | None = None
    error: str | None = None


_MULTI_BLANK = re.compile(r"\n{3,}")
_TRAILING_WS = re.compile(r"[ \t\u00a0]+\n")


def clean_text(text: str) -> str:
    text = text.replace("\x00", "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\u00ad", "")  # soft hyphens
    text = _TRAILING_WS.sub("\n", text)
    text = _MULTI_BLANK.sub("\n\n", text)
    return text.strip()


def extract_file(path: str, options: dict | None = None) -> Extracted:
    options = options or {}
    ext = os.path.splitext(path)[1].lower()
    try:
        if os.path.getsize(path) == 0:  # e.g. 0-byte placeholders created from folder templates
            return Extracted(status="empty")
        if ext == ".pdf":
            return extract_pdf(path, options.get("max_pdf_pages", 3000))
        if ext == ".docx":
            return extract_docx(path)
        if ext == ".doc":
            return extract_doc(path, options.get("doc_converter", "auto"), options.get("libreoffice_path"))
        return Extracted(status="skipped", error=f"unsupported extension {ext}")
    except Exception as e:  # corrupt or unreadable files must not stop the run
        return Extracted(status="error", error=f"{type(e).__name__}: {e}"[:500])


# --------------------------------------------------------------------- PDF

def extract_pdf(path: str, max_pages: int = 3000) -> Extracted:
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            return Extracted(status="error", n_pages=doc.page_count, error="password-protected PDF")
        n_pages = doc.page_count
        title = ((doc.metadata or {}).get("title") or "").strip() or None
        pages: list[tuple[int, str]] = []
        for i in range(min(n_pages, max_pages)):
            text = clean_text(doc[i].get_text("text", sort=True))
            if text:
                pages.append((i + 1, text))
    if n_pages == 0:
        return Extracted(status="empty", n_pages=0, title=title)
    if sum(len(t) for _, t in pages) < MIN_PDF_CHARS_PER_PAGE * min(n_pages, max_pages):
        return Extracted(status="no_text", pages=pages, n_pages=n_pages, title=title,
                         error="no text layer (probably a scan, would need OCR)")
    return Extracted(status="ok", pages=pages, n_pages=n_pages, title=title)


# -------------------------------------------------------------------- DOCX

_W_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_HEADING_PREFIXES = ("heading", "titre", "überschrift", "título", "titolo")


def _has_page_break(el) -> bool:
    for node in el.iter(f"{_W_NS}lastRenderedPageBreak", f"{_W_NS}br"):
        if node.tag.endswith("lastRenderedPageBreak") or node.get(f"{_W_NS}type") == "page":
            return True
    return False


def _heading_level(paragraph) -> int:
    try:
        name = (paragraph.style.name or "").lower()
    except Exception:
        return 0
    if name == "title" or name == "titre":
        return 1
    if name.startswith(_HEADING_PREFIXES):
        digits = re.findall(r"\d+", name)
        return min(int(digits[0]), 6) if digits else 1
    return 0


def _table_text(table) -> str:
    lines = []
    for row in table.rows:
        cells: list[str] = []
        try:
            row_cells = row.cells
        except Exception:
            continue
        for cell in row_cells:
            t = " ".join(cell.text.split())
            if not cells or cells[-1] != t:  # merged cells repeat their text
                cells.append(t)
        if any(cells):
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def docx_blocks(path: str) -> tuple[list[tuple[str, bool]], str | None]:
    """Return ([(block_text, starts_new_page)], title) in document order."""
    from docx import Document
    from docx.table import Table
    from docx.text.paragraph import Paragraph

    document = Document(path)
    title = None
    try:
        title = (document.core_properties.title or "").strip() or None
    except Exception:
        pass

    blocks: list[tuple[str, bool]] = []
    for el in document.element.body.iterchildren():
        tag = el.tag.rsplit("}", 1)[-1]
        if tag == "p":
            p = Paragraph(el, document)
            text = p.text.strip()
            if not text:
                if _has_page_break(el) and blocks:
                    blocks.append(("", True))
                continue
            level = _heading_level(p)
            if level:
                text = "#" * level + " " + text
            blocks.append((text, _has_page_break(el)))
        elif tag == "tbl":
            text = _table_text(Table(el, document))
            if text:
                blocks.append((text, _has_page_break(el)))
        elif tag == "sdt":  # content controls: take their raw text
            text = " ".join("".join(el.itertext()).split())
            if text:
                blocks.append((text, False))
    return blocks, title


def paginate_blocks(blocks: list[tuple[str, bool]], page_chars: int = DOCX_PAGE_CHARS) -> list[str]:
    """Group blocks into pages.

    Word stores where pages broke when the file was last saved
    (lastRenderedPageBreak); when present, they give page numbers close to the
    printed ones. Otherwise pages are approximated by size.
    """
    use_markers = sum(1 for _, brk in blocks if brk) >= 1
    pages: list[list[str]] = [[]]
    size = 0
    for text, brk in blocks:
        if use_markers:
            new_page = brk and size > 0
        else:
            new_page = size > 0 and (size + len(text) > page_chars
                                     or (text.startswith("#") and size > page_chars // 2))
        if new_page:
            pages.append([])
            size = 0
        if text:
            pages[-1].append(text)
            size += len(text) + 2
    return [clean_text("\n\n".join(p)) for p in pages]


def extract_docx(path: str) -> Extracted:
    blocks, title = docx_blocks(path)
    page_texts = paginate_blocks(blocks)
    pages = [(i + 1, t) for i, t in enumerate(page_texts) if t]
    if not pages:
        return Extracted(status="empty", n_pages=len(page_texts), title=title)
    return Extracted(status="ok", pages=pages, n_pages=len(page_texts), title=title)


# --------------------------------------------------------------------- DOC

def find_libreoffice(configured: str | None = None) -> str | None:
    if configured:
        return configured if os.path.exists(configured) else None
    for name in ("soffice", "libreoffice"):
        found = shutil.which(name)
        if found:
            return found
    if sys.platform == "win32":
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
            candidate = os.path.join(base, "LibreOffice", "program", "soffice.exe")
            if os.path.exists(candidate):
                return candidate
    return None


def _word_available() -> bool:
    if sys.platform != "win32":
        return False
    try:
        import win32com.client  # noqa: F401
        return True
    except ImportError:
        return False


def _convert_with_libreoffice(soffice: str, path: str, out_dir: str) -> str:
    # A private profile per call lets several worker processes convert at once.
    profile = os.path.join(out_dir, "lo_profile")
    profile_url = "file:///" + profile.replace("\\", "/").lstrip("/")
    subprocess.run(
        [soffice, f"-env:UserInstallation={profile_url}", "--headless", "--norestore",
         "--convert-to", "docx", "--outdir", out_dir, path],
        check=True, capture_output=True, timeout=180,
    )
    out = os.path.join(out_dir, os.path.splitext(os.path.basename(path))[0] + ".docx")
    if not os.path.exists(out):
        raise RuntimeError("LibreOffice produced no output")
    return out


def _convert_with_word(path: str, out_dir: str) -> str:
    import pythoncom
    import win32com.client

    out = os.path.join(out_dir, "converted.docx")
    pythoncom.CoInitialize()
    word = None
    try:
        word = win32com.client.DispatchEx("Word.Application")
        word.Visible = False
        word.DisplayAlerts = 0
        # A dummy password makes protected files fail instead of prompting.
        doc = word.Documents.Open(os.path.abspath(path), False, True, False, "dmx-no-password")
        try:
            doc.SaveAs2(out, FileFormat=16)  # wdFormatDocumentDefault (.docx)
        finally:
            doc.Close(False)
    finally:
        if word is not None:
            word.Quit()
        pythoncom.CoUninitialize()
    return out


def extract_doc(path: str, converter: str = "auto", libreoffice_path: str | None = None) -> Extracted:
    soffice = find_libreoffice(libreoffice_path) if converter in ("auto", "libreoffice") else None
    use_word = converter == "word" or (converter == "auto" and soffice is None and _word_available())
    if soffice is None and not use_word:
        return Extracted(status="skipped",
                         error="no .doc converter (install LibreOffice or Microsoft Word + pywin32)")
    with tempfile.TemporaryDirectory(prefix="dmx_doc_") as tmp:
        converted = _convert_with_libreoffice(soffice, path, tmp) if soffice else _convert_with_word(path, tmp)
        return extract_docx(converted)

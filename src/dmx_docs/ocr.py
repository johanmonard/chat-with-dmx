"""OCR of PDF pages that have no text layer (scans, image-only pages).

A separate step between `index` and `embed`: it finds PDF pages whose extracted text is
(almost) empty, reads them with the Tesseract engine built into PyMuPDF and APPENDS the
recognized text as new chunks. Existing chunks and their vectors are never touched, so only
the new chunks need embedding. Every page read is recorded in `ocr_pages` (store.py), which
makes the step resumable and lets the tools label OCR text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

OCR_VERSION = 1          # bump when OCR output changes: pages read by an older version are redone
MIN_PAGE_CHARS = 50      # a PDF page with less extracted text is an OCR candidate
MIN_OCR_CHARS = 20       # less recognized text: 'empty' (photo, drawing without notes)
PAGES_PER_JOB = 20       # pages of one document read by one worker call
STALL_S = 600            # no job finished for this long: the running ones are stuck
PROGRESS_EVERY_S = 15

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

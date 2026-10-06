"""Split page text into search chunks.

Chunks are contiguous, non-overlapping slices of one page, so a page can be
rebuilt exactly by concatenating its chunks (read_document relies on this),
and every search hit maps to a single page number.
"""

from __future__ import annotations

TARGET_CHARS = 1200
MAX_CHARS = 2000

_BREAKS = ("\n\n", "\n", ". ", "? ", "! ", "; ", ", ", " ")


def split_text(text: str, target: int = TARGET_CHARS, max_chars: int = MAX_CHARS) -> list[str]:
    parts: list[str] = []
    pos = 0
    n = len(text)
    while n - pos > max_chars:
        lo, hi = pos + target // 2, pos + max_chars
        cut = -1
        for sep in _BREAKS:
            i = text.rfind(sep, lo, hi)
            if i != -1:
                cut = i + len(sep)
                break
        if cut <= pos:
            cut = hi
        parts.append(text[pos:cut])
        pos = cut
    if pos < n:
        parts.append(text[pos:])
    return parts


def make_chunks(pages: list[tuple[int, str]]) -> list[tuple[int, int, str]]:
    """Return [(page_no, seq, text)]."""
    out = []
    for page_no, text in pages:
        for seq, part in enumerate(split_text(text)):
            out.append((page_no, seq, part))
    return out

"""Excel workbooks (.xlsx, .xlsm, .xls): one page of text per sheet.

Every row with a value becomes one line of cells joined by " | ". Long sheets repeat their header row
now and then, so that a passage cut out of the middle still says what its columns are."""
from __future__ import annotations

import datetime as dt
import os
from collections.abc import Iterable, Iterator

from .extract import Extracted, clean_text

# The text of a whole workbook is cut off after this many characters; later sheets are not read.
MAX_WORKBOOK_CHARS = 2_000_000
# In a long sheet the header row is written again before a row once this many characters of rows
# were written since the header (or its last repeat); a long header is cut to HEADER_MAX_CHARS.
HEADER_EVERY_CHARS = 1000
HEADER_MAX_CHARS = 300
# Rows read per sheet, empty ones included: a sheet formatted down to its last row (1 048 576) would
# otherwise be scanned to the end for nothing.
MAX_RAW_ROWS = 200_000


def cell_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "VRAI" if value else "FAUX"
    if isinstance(value, dt.datetime):
        if value.time() == dt.time(0):
            return value.date().isoformat()
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, dt.date):
        return value.isoformat()
    if isinstance(value, float):
        return str(int(value)) if value.is_integer() else format(value, ".10g")
    return " ".join(str(value).splitlines())


def _row_text(cells: Iterable) -> str:
    """The cells of a row as one line, trailing empty cells dropped; "" for a row without a value."""
    parts = [cell_text(c).strip() for c in cells]
    while parts and not parts[-1]:
        parts.pop()
    return " | ".join(parts)


def _sheet_text(name: str, hidden: bool, rows: Iterator[str], max_rows: int, budget: int) -> tuple[str, bool]:
    """Page text of a sheet from its rows (already converted), and whether the workbook limit was reached."""
    out = [f"Feuille : {name}" + (" (masquée)" if hidden else "")]
    header = ""
    written = since_header = size = raw = 0
    workbook_cut = False
    for row in rows:
        raw += 1
        if raw > MAX_RAW_ROWS:
            if written:
                out.append(f"[feuille coupée après {MAX_RAW_ROWS} lignes lues]")
            break
        if not row.strip(" |"):
            continue
        if written >= max_rows:
            out.append(f"[feuille coupée après {max_rows} lignes]")
            break
        if size >= budget:
            out.append("[classeur coupé : feuilles suivantes non lues]")
            workbook_cut = True
            break
        if not header:
            header = row[:HEADER_MAX_CHARS]
        elif since_header >= HEADER_EVERY_CHARS:
            out.append("[colonnes] " + header)
            size += len(header) + 11
            since_header = 0
        out.append(row)
        written += 1
        size += len(row) + 1
        since_header += len(row) + 1
    if len(out) == 1:  # no row with a value
        return "", False
    return clean_text("\n".join(out)), workbook_cut


def _xlsx_sheets(path: str, max_columns: int) -> Iterator[tuple[int, str, bool, Iterator[str]]]:
    """(number of sheets, name, hidden, rows) per sheet; a sheet is only loaded when asked for."""
    import openpyxl
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        total = len(wb.sheetnames)
        for name in wb.sheetnames:
            ws = wb[name]
            hidden = getattr(ws, "sheet_state", "visible") != "visible"
            if not hasattr(ws, "iter_rows"):  # a chart sheet: nothing to read
                yield total, name, hidden, iter(())
                continue
            ws.reset_dimensions()  # a workbook without dimensions is read fully
            yield total, name, hidden, (_row_text(r) for r in ws.iter_rows(values_only=True, max_col=max_columns))
    finally:
        wb.close()


def _xls_value(cell, datemode: int):
    import xlrd
    if cell.ctype == xlrd.XL_CELL_DATE:
        try:
            return xlrd.xldate_as_datetime(cell.value, datemode)
        except (ValueError, OverflowError):
            return cell.value
    if cell.ctype == xlrd.XL_CELL_ERROR:
        return xlrd.error_text_from_code.get(cell.value, "#ERR")
    if cell.ctype == xlrd.XL_CELL_BOOLEAN:
        return bool(cell.value)
    if cell.ctype in (xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK):
        return None
    return cell.value


def _xls_sheets(path: str, max_columns: int) -> Iterator[tuple[int, str, bool, Iterator[str]]]:
    """(number of sheets, name, hidden, rows) per sheet; a sheet is only loaded when asked for."""
    import xlrd
    book = xlrd.open_workbook(path, on_demand=True)
    try:
        for i in range(book.nsheets):
            sh = book.sheet_by_index(i)
            rows = (_row_text(_xls_value(c, book.datemode) for c in sh.row(r)[:max_columns])
                    for r in range(sh.nrows))
            yield book.nsheets, sh.name, sh.visibility != 0, rows
            book.unload_sheet(i)
    finally:
        book.release_resources()


def extract_excel(path: str, max_rows: int = 5000, max_columns: int = 200) -> Extracted:
    sheets = _xls_sheets if os.path.splitext(path)[1].lower() == ".xls" else _xlsx_sheets
    pages: list[tuple[int, str]] = []
    n_pages = 0
    used = 0
    for n_pages, (total, name, hidden, rows) in enumerate(sheets(path, max_columns), start=1):
        text, stopped = _sheet_text(name, hidden, rows, max_rows, MAX_WORKBOOK_CHARS - used)
        used += len(text)
        if text:
            pages.append((n_pages, text))
        if stopped:
            n_pages = total  # the sheets after the limit only count as pages: not loaded
            break
    if not pages:
        return Extracted(status="empty", n_pages=n_pages)
    return Extracted(status="ok", pages=pages, n_pages=n_pages)

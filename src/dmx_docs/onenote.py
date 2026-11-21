"""OneNote: page lists and page text from OneNote's hierarchy XML and page XML.

Pure functions; the part that talks to OneNote itself lives elsewhere.
Elements are matched by local name so that any schema year works.
"""
from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from .extract import clean_text

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

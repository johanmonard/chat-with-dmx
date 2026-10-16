"""Knowledge cards: pre-digested context, one Markdown card per project.

Cards are written by Claude from the project documents (procedure in knowledge\\README.md) and kept
in knowledge\\projects\\<PROJECT>.md next to config.toml; knowledge\\vocabulary.md holds the allowed
infeed/outfeed forms and end effectors with their synonyms. A card is a fast pointer, not the
truth: every fact cites its source, and where a card and a document disagree, the document wins.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .register import fold

REQUIRED = ("type", "project", "status", "written", "read")
STATUSES = ("unreviewed", "reviewed")


class CardError(ValueError):
    """A card that cannot be read: the reason says which part is wrong."""


@dataclass
class Card:
    file: str
    fields: dict[str, str | list[str]]
    body: str

    @property
    def project(self) -> str:
        return str(self.fields["project"])

    @property
    def status(self) -> str:
        return str(self.fields["status"])

    @property
    def written(self) -> str:
        return str(self.fields["written"])

    @property
    def read(self) -> int:
        return int(self.fields["read"])

    def values(self, key: str) -> list[str]:
        v = self.fields.get(key)
        if v is None or v == "":
            return []
        return list(v) if isinstance(v, list) else [v]


def _split_list(text: str) -> list[str]:
    """'a (x, y), b' -> ['a (x, y)', 'b']: commas inside parentheses stay in their item."""
    items, depth, cur = [], 0, ""
    for ch in text:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        if ch == "," and depth == 0:
            items.append(cur)
            cur = ""
        else:
            cur += ch
    items.append(cur)
    return [i.strip() for i in items if i.strip()]


def parse_card(text: str, file: str) -> Card:
    text = text.lstrip("﻿").replace("\r\n", "\n")
    m = re.match(r"---\n(.*?)\n---\n?(.*)", text, re.S)
    if not m:
        raise CardError("no header (a block between two --- lines at the top)")
    fields: dict[str, str | list[str]] = {}
    for n, line in enumerate(m.group(1).split("\n"), 2):
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep or not re.fullmatch(r"[a-z_]+", key.strip()):
            raise CardError(f"header line {n} is not 'key: value': {line.strip()}")
        value = value.strip()
        fields[key.strip()] = _split_list(value[1:-1]) if value.startswith("[") and value.endswith("]") else value
    missing = [k for k in REQUIRED if not fields.get(k)]
    if missing:
        raise CardError(f"required header field missing: {', '.join(missing)}")
    if fields["status"] not in STATUSES:
        raise CardError(f"status must be unreviewed or reviewed, not {fields['status']}")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(fields["written"])):
        raise CardError(f"written must be a date YYYY-MM-DD, not {fields['written']}")
    if not str(fields["read"]).isdigit():
        raise CardError(f"read must be a number of documents, not {fields['read']}")
    return Card(file=file, fields=fields, body=m.group(2).strip("\n"))


def form_of(entry: str) -> str:
    """'flowpack (chocolate bar 100 g)' -> 'flowpack'."""
    return entry.split("(", 1)[0].strip()


@dataclass
class Vocabulary:
    kinds: dict[str, dict[str, str]] = field(default_factory=dict)  # kind -> folded word or synonym -> value

    @classmethod
    def parse(cls, text: str) -> Vocabulary:
        kinds: dict[str, dict[str, str]] = {}
        kind = None
        for line in text.lstrip("﻿").splitlines():
            line = line.strip()
            if line.startswith("## "):
                kind = line[3:].strip()
                kinds.setdefault(kind, {})
            elif line.startswith("- ") and kind:
                value, _, syns = line[2:].partition(":")
                value = value.strip()
                for word in [value] + syns.split(","):
                    if fold(word):
                        kinds[kind][fold(word)] = value
        return cls(kinds)

    def canonical(self, kind: str, word: str) -> str | None:
        return self.kinds.get(kind, {}).get(fold(word))


def load_vocabulary(path: str) -> Vocabulary:
    try:
        return Vocabulary.parse(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError):
        return Vocabulary()


class Knowledge:
    """The cards of one knowledge folder, kept in memory; each call re-reads the files whose
    modification time changed, so an edited card is served without a restart."""

    def __init__(self, root: str | None):
        self.root = root
        self._files: dict[str, tuple[float, Card | None, str | None]] = {}  # name -> (mtime, card, error)
        self._vocab: tuple[float, Vocabulary] = (-1.0, Vocabulary())

    @property
    def available(self) -> bool:
        return bool(self.root) and os.path.isdir(self.root)

    def _refresh(self) -> None:
        if not self.available:
            self._files = {}
            return
        folder = os.path.join(self.root, "projects")
        seen = {}
        try:
            entries = [e for e in os.scandir(folder) if e.is_file() and e.name.lower().endswith(".md")]
        except OSError:
            entries = []
        for e in entries:
            mtime = e.stat().st_mtime
            seen[e.name] = mtime
            old = self._files.get(e.name)
            if old and old[0] == mtime:
                continue
            try:
                card, err = parse_card(Path(e.path).read_text(encoding="utf-8-sig"), e.name), None
            except CardError as ex:
                card, err = None, str(ex)
            except (OSError, UnicodeDecodeError) as ex:
                card, err = None, f"cannot read the file ({ex})"
            self._files[e.name] = (mtime, card, err)
        for name in set(self._files) - set(seen):
            del self._files[name]
        vpath = os.path.join(self.root, "vocabulary.md")
        try:
            vtime = os.path.getmtime(vpath)
        except OSError:
            vtime = 0.0
        if vtime != self._vocab[0]:
            self._vocab = (vtime, load_vocabulary(vpath) if vtime else Vocabulary())

    def cards(self) -> list[Card]:
        self._refresh()
        return sorted((c for _, c, _ in self._files.values() if c), key=lambda c: fold(c.project))

    def errors(self) -> dict[str, str]:
        self._refresh()
        return {name: err for name, (_, _, err) in sorted(self._files.items()) if err}

    @property
    def vocabulary(self) -> Vocabulary:
        self._refresh()
        return self._vocab[1]

    def card(self, name: str) -> Card | None:
        want = fold(name)
        for c in self.cards():
            stem = c.file.rsplit(".", 1)[0]
            if want in (fold(c.project), fold(stem), fold(stem.split("__")[-1])):
                return c
        return None

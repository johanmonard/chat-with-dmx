"""Knowledge cards: pre-digested context, one Markdown card per project.

Cards are written by Claude from the project documents (procedure in knowledge\\README.md) and kept
in knowledge\\projects\\<PROJECT>.md next to config.toml; knowledge\\vocabulary.md holds the allowed
infeed/outfeed forms and end effectors with their synonyms. A card is a fast pointer, not the
truth: every fact cites its source, and where a card and a document disagree, the document wins.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
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
    duplicates: list[str] = field(default_factory=list)             # words listed under two values

    @classmethod
    def parse(cls, text: str) -> Vocabulary:
        kinds: dict[str, dict[str, str]] = {}
        duplicates = []
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
                    key = fold(word)
                    if not key:
                        continue
                    old = kinds[kind].get(key)
                    if old and old != value:
                        duplicates.append(f"{kind}: '{word.strip()}' is listed under {old} and {value}")
                    else:
                        kinds[kind][key] = value
        return cls(kinds, duplicates)

    def canonical(self, kind: str, word: str) -> str | None:
        """The word list value for a word or synonym; plurals ('cases', 'boxes') find their singular."""
        words = self.kinds.get(kind, {})
        key = fold(word)
        for k in (key, key[:-1] if key.endswith("s") else "", key[:-2] if key.endswith("es") else ""):
            if k and k in words:
                return words[k]
        return None


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
        """The card named by its file: <name>.md first, else the only <PARENT>__<name>.md."""
        want = fold(name)
        cards = self.cards()
        exact = [c for c in cards if fold(stem_of(c.file)) == want]
        if exact:
            return exact[0]
        subs = [c for c in cards if "__" in c.file and fold(stem_of(c.file).split("__")[-1]) == want]
        return subs[0] if len(subs) == 1 else None


def stem_of(file: str) -> str:
    return file.rsplit(".", 1)[0]


# ----------------------------------------------------------------------------- check

SOURCE = re.compile(r"(?<!\[)\[(?!\[)([^\[\]]+)\](?!\])")   # [source], not [[link]]
SOURCED_SECTIONS = ("## A.", "## B.", "## C.")
BACKED_FIELDS = ("client", "country", "years", "machines", "speed")
WORD_LIST_FIELDS = (("forms", "infeed"), ("forms", "outfeed"), ("end_effectors", "end_effectors"))


@dataclass
class Issue:
    file: str
    level: str      # "error" | "warning"
    message: str


def _sourced_lines(body: str):
    """The '- ' lines of sections A, B and C."""
    section = ""
    for line in body.split("\n"):
        if line.startswith("## "):
            section = line
        elif line.startswith("- ") and section.startswith(SOURCED_SECTIONS):
            yield line


BARE_PATH = re.compile(r"(?:\b[A-Za-z]:\\|\\\\)[^\[\]\n]+")   # N:\X\a.pdf or \\server\share\a.pdf, to a bracket


def source_path(source: str) -> str:
    """'N:\\X\\a.pdf p.2' -> 'N:\\X\\a.pdf': a cited source without its page, pages or slides."""
    return re.sub(r"\s+(pp?\.|slides?)\s*\d[\d\s,.\-–p]*$", "", source.strip())  # p.2, p.2-3, pp. 1, 3


def backs(vocab: Vocabulary, key: str, value: str, folded_body: str) -> bool:
    """Does the (folded) body back this header value, by the rule of `check`? A word-list field
    (infeed, outfeed, end_effectors) needs one of its value's synonyms; any other its own text."""
    for kind, k in WORD_LIST_FIELDS:
        if k == key:
            canonical = vocab.canonical(kind, form_of(value))
            return bool(canonical) and any(word in folded_body for word, v in vocab.kinds.get(kind, {}).items()
                                           if v == canonical)
    return bool(fold(value)) and fold(value) in folded_body


def visible_card(card: Card, can_read_source: Callable[[str], bool]) -> tuple[str, int, bool]:
    """The card as one caller may see it: (body to show, sourced lines left out, whole).

    Each source is judged by can_read_source: a document path (with a backslash) without its page
    suffix, any other source ('register', 'user, 2026-...') as written. A document path written
    outside brackets (the key documents of section D) is a source too. whole: every source of the
    card is readable, and the body is then unchanged. Otherwise only the '- ' lines of sections
    A, B and C whose sources are all readable are kept; the other sections (Summary, key documents...)
    and unsourced text go, since nothing says where they come from; a section left without lines
    goes too, and the text ends with 'k lines not shown with your rights.'. Nothing left: ''.
    """
    def sources_readable(line: str) -> bool:
        cited = [source_path(s) if "\\" in s else s.strip() for s in SOURCE.findall(line)]
        bare = [source_path(p.rstrip(" -")) for p in BARE_PATH.findall(SOURCE.sub("", line))]
        return all(can_read_source(s) for s in cited + bare)

    def readable(line: str) -> bool:  # a line is shown on its citations: one without any is not
        return bool(SOURCE.search(line)) and sources_readable(line)

    lines = card.body.split("\n")
    if all(sources_readable(line) for line in lines):
        return card.body, 0, True
    kept: list[str] = []
    section: list[str] = []   # the heading of the current sourced section, until its first kept line
    hidden = 0
    sourced = False
    for line in lines:
        if line.startswith("## "):
            sourced = line.startswith(SOURCED_SECTIONS)
            section = [line] if sourced else []
        elif sourced and line.startswith("- "):
            if readable(line):
                if kept and section:
                    kept.append("")
                kept += section + [line]
                section = []
            else:
                hidden += 1
    if not kept:
        return "", hidden, False
    if hidden:
        kept += ["", f"{hidden} lines not shown with your rights."]
    return "\n".join(kept), hidden, False


def check(k: Knowledge, con) -> list[Issue]:
    """Every rule of knowledge\\README.md that a program can verify, per card."""
    from . import sources, store

    issues = [Issue(file, "error", f"unreadable: {reason}") for file, reason in k.errors().items()]
    vocab = k.vocabulary
    issues += [Issue("vocabulary.md", "error", d) for d in vocab.duplicates]
    names = {fold(r[0]) for r in con.execute(
        "SELECT project FROM doc_facets WHERE project IS NOT NULL UNION "
        "SELECT subproject FROM doc_facets WHERE subproject IS NOT NULL UNION "
        "SELECT name FROM register_machines WHERE name IS NOT NULL UNION "
        "SELECT project FROM register_machines WHERE project IS NOT NULL")}
    for card in k.cards():
        def err(message: str, level: str = "error") -> None:
            issues.append(Issue(card.file, level, message))

        stem = stem_of(card.file)
        if fold(card.project) != fold(stem.split("__")[-1]):
            err(f"file name {card.file} does not match project: {card.project} (a project's card is "
                "<PROJECT>.md; a sub-project's is <PARENT>__<SUB>.md with project: <SUB>)")
        body = fold(card.body)
        for line in _sourced_lines(card.body):
            if not SOURCE.search(line):
                err(f"no source on: {line[2:].strip()}")
        for key in BACKED_FIELDS:
            for value in card.values(key):
                if fold(value) not in body:
                    err(f"header {key} '{value}' does not appear in the body")
        for kind, key in WORD_LIST_FIELDS:
            for entry in card.values(key):
                word = form_of(entry)
                value = vocab.canonical(kind, word)
                if not value:
                    err(f"{key} '{word}' is not in the word list (vocabulary.md)")
                    continue
                synonyms = [w for w, v in vocab.kinds.get(kind, {}).items() if v == value]
                if not any(s in body for s in synonyms):
                    err(f"header {key} '{word}' does not appear in the body")
        for line in card.body.split("\n"):
            for src in SOURCE.findall(line):
                path = source_path(src)
                if "\\" in path and not con.execute("SELECT 1 FROM docs WHERE path_key = ?",
                                                    (store.path_key(sources.clean_path(path)),)).fetchone():
                    err(f"cited file not in the index: {path}")
        if fold(card.project) not in names:
            err(f"project {card.project} is not in the index nor the register")
            continue
        newest = con.execute("""SELECT max(d.mtime) FROM docs d JOIN doc_facets f ON f.doc_id = d.id
                                WHERE f.project = ? COLLATE NOCASE OR f.subproject = ? COLLATE NOCASE""",
                             (card.project, card.project)).fetchone()[0]
        if newest:
            newest_day = datetime.fromtimestamp(newest).date().isoformat()
            if card.written < newest_day:
                err(f"written {card.written} is older than the newest document ({newest_day}): "
                    "refresh the card", "warning")
    return issues

"""Bilingual (FR/EN/DE/ES) thesaurus used to expand keyword searches.

thesaurus.toml (next to config.toml) holds one table per concept:

    [prehenseur]
    fr = ["préhenseur", "pince"]
    en = ["gripper", "end effector"]
    de = ["Greifer"]
    status = "ok"        # "?" = to review; entries with status "no" are ignored

A query word or phrase that belongs to a concept is searched together with the other terms
of the concept (OR). Accents and case do not matter. Semantic search does not need this; it
helps the keyword part with jargon, abbreviations and exact cross-language terms.
"""

from __future__ import annotations

import os
import re
import tomllib
import unicodedata

LANGS = ("fr", "en", "de", "es", "it", "abbr")
MAX_NGRAM = 4


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.findall(r"\w+", text))


class Thesaurus:
    def __init__(self, concepts: dict[str, list[str]]):
        self.concepts = concepts                      # concept id -> terms (original spelling)
        self.index: dict[str, str] = {}               # normalized term -> concept id
        self.duplicates: dict[str, list[str]] = {}    # term listed in several concepts (first one wins)
        for cid, terms in concepts.items():
            for t in terms:
                key = norm(t)
                if not key:
                    continue
                if key in self.index and self.index[key] != cid:
                    self.duplicates.setdefault(key, [self.index[key]]).append(cid)
                self.index.setdefault(key, cid)

    @classmethod
    def load(cls, path: str | os.PathLike | None) -> "Thesaurus | None":
        if not path or not os.path.isfile(path):
            return None
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        concepts = {}
        for cid, entry in raw.items():
            if not isinstance(entry, dict) or str(entry.get("status", "")).lower() == "no":
                continue
            terms = [t for lang in LANGS for t in entry.get(lang, []) if isinstance(t, str) and t.strip()]
            if len(terms) >= 2:
                concepts[cid] = terms
        return cls(concepts)

    def expand(self, words: list[str]) -> list[tuple[str, list[str]]]:
        """Split query words into units: [(original text, [equivalent terms])]; a unit that is
        not in the thesaurus has an empty list. Longest phrases match first."""
        out, i = [], 0
        keys = [norm(w) for w in words]
        while i < len(words):
            for n in range(min(MAX_NGRAM, len(words) - i), 0, -1):
                key = " ".join(keys[i:i + n])
                cid = self.index.get(key)
                if cid:
                    # Short abbreviations (AU, ES, MES, FAT...) trigger an expansion when typed but are
                    # never added: the index ignores case, so they would also match au, es, mes, fat...
                    others = [t for t in self.concepts[cid] if norm(t) != key and len(norm(t).replace(" ", "")) > 3]
                    out.append((" ".join(words[i:i + n]), others))
                    i += n
                    break
            else:
                out.append((words[i], []))
                i += 1
        return out

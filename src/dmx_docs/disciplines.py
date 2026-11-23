"""Engineering world: the discipline of a document (Mechanical, Electrical, Software, Mixed, Unknown).

Decided from, in this order: a folder rule written by the user, the keyword score of the document's
path, title and first text, otherwise Unknown. This module only reads the rules file and scores text;
it knows nothing about the database (see labelling.py) or the suggestions (suggestions.py).
"""

from __future__ import annotations

import fnmatch
import hashlib
import os
import re
import tomllib
import unicodedata
from dataclasses import dataclass, field

DISCIPLINES = ("Mechanical", "Electrical", "Software")
LABELS = DISCIPLINES + ("Mixed", "Unknown")
STRONG, WEAK = 3, 1
HEAD_FACTOR = 3     # path below the root, file name and title count three times the text
WORD_CAP = 5        # one repeated word cannot dominate


class RulesError(ValueError):
    """disciplines.toml cannot be used."""


@dataclass
class Rules:
    folders: list[tuple[str, str]] = field(default_factory=list)   # (lowercased '/' pattern, label)
    words: dict[str, dict[str, list[str]]] = field(
        default_factory=lambda: {d: {"strong": [], "weak": []} for d in DISCIPLINES})
    min_score: int = 6
    mixed_share: float = 0.30
    top_share: float = 0.70
    suggestions: dict = field(default_factory=lambda: {
        "min_docs": 5, "min_sections": 2, "min_lift": 3.0, "max_rows": 200})
    fingerprint: str = "none"


@dataclass
class Decision:
    label: str
    source: str                      # "rule", "words" or "none"
    scores: dict[str, int]
    top_terms: list[str]


def normalize(text: str) -> str:
    """Lowercase, no accents, every run of non-alphanumeric characters one space."""
    decomposed = unicodedata.normalize("NFKD", text.lower())
    plain = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[\W_]+", " ", plain).strip()


def _words(table: dict, where: str, kind: str) -> list[str]:
    values = table.get(kind, [])
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise RulesError(f"{where}: {kind} must be a list of words")
    out: list[str] = []
    for v in values:
        n = normalize(v)
        if n and n not in out:
            out.append(n)
    return out


def load_rules(path: str | None) -> Rules:
    """Read disciplines.toml. No path or no file: empty rules (every document Unknown)."""
    if not path or not os.path.isfile(path):
        return Rules()
    with open(path, "rb") as f:
        data = f.read()
    try:
        raw = tomllib.loads(data.decode("utf-8-sig"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise RulesError(f"{path}: {e}") from e
    rules = Rules(fingerprint=hashlib.sha1(data).hexdigest()[:12])
    for entry in raw.get("folder", []):
        pattern, label = entry.get("pattern"), entry.get("discipline")
        if not isinstance(pattern, str) or not pattern.strip():
            raise RulesError(f"{path}: a [[folder]] rule needs a pattern")
        if label not in LABELS:
            raise RulesError(f"{path}: unknown discipline {label!r} in a folder rule (use {', '.join(LABELS)})")
        rules.folders.append((pattern.replace("\\", "/").lower(), label))
    for name, table in (raw.get("words") or {}).items():
        if name not in DISCIPLINES:
            raise RulesError(f"{path}: [words.{name}]: the disciplines are {', '.join(DISCIPLINES)}")
        rules.words[name] = {kind: _words(table, f"{path}: [words.{name}]", kind) for kind in ("strong", "weak")}
    scoring = raw.get("scoring") or {}
    try:
        rules.min_score = int(scoring.get("min_score", rules.min_score))
        rules.mixed_share = float(scoring.get("mixed_share", rules.mixed_share))
        rules.top_share = float(scoring.get("top_share", rules.top_share))
        for key in ("min_docs", "min_sections", "max_rows"):
            rules.suggestions[key] = int((raw.get("suggestions") or {}).get(key, rules.suggestions[key]))
        rules.suggestions["min_lift"] = float((raw.get("suggestions") or {}).get("min_lift", 3.0))
    except (TypeError, ValueError) as e:
        raise RulesError(f"{path}: a number is expected in [scoring] or [suggestions]: {e}") from e
    for key in ("mixed_share", "top_share"):
        if not 0 < getattr(rules, key) < 1:
            raise RulesError(f"{path}: [scoring] {key} must be between 0 and 1")
    return rules


def _count(padded: str, word: str) -> int:
    return len(re.findall(r"(?<= )" + re.escape(word) + r"(?= )", padded))


def score_text(rules: Rules, head: str, body: str) -> tuple[dict[str, int], dict[str, int]]:
    """Scores per discipline and the weighted contribution of each word ('<discipline>:<word>')."""
    heads, bodies = f" {normalize(head)} ", f" {normalize(body)} "
    scores = {d: 0 for d in DISCIPLINES}
    contributions: dict[str, int] = {}
    for discipline in DISCIPLINES:
        for kind, weight in (("strong", STRONG), ("weak", WEAK)):
            for word in rules.words.get(discipline, {}).get(kind, []):
                value = weight * (HEAD_FACTOR * min(_count(heads, word), WORD_CAP)
                                  + min(_count(bodies, word), WORD_CAP))
                if value:
                    scores[discipline] += value
                    contributions[f"{discipline}:{word}"] = value
    return scores, contributions


def classify(rules: Rules, full_path: str, rel_path: str, title: str | None, body: str) -> Decision:
    scores, contributions = score_text(rules, rel_path + " " + (title or ""), body)
    top_terms = [k.split(":", 1)[1] for k, _ in sorted(contributions.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]
    lowered = full_path.replace("\\", "/").lower()
    for pattern, label in rules.folders:
        if fnmatch.fnmatchcase(lowered, pattern):
            return Decision(label, "rule", scores, top_terms)
    total = sum(scores.values())
    if total < rules.min_score:
        return Decision("Unknown", "none", scores, top_terms)
    ranked = sorted(DISCIPLINES, key=lambda d: (-scores[d], DISCIPLINES.index(d)))
    top, second = ranked[0], ranked[1]
    if scores[top] / total >= rules.top_share:
        label = top
    elif scores[second] / total >= rules.mixed_share:
        label = "Mixed"
    else:
        label = top
    return Decision(label, "words", scores, top_terms)

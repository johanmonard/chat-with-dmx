"""Engineering world: keyword suggestions for disciplines.toml (proposals only, nothing is applied).

After the documents are labelled, the confidently labelled ones (decided by a folder rule, or by words
with a clear lead) are the reference. Words and two-word phrases that are frequent in the reference
documents of one discipline and rare in the others, and seen in several folders, are written to
keyword-suggestions.csv. The user decides which ones go into disciplines.toml.
"""

from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from . import labelling, store
from .config import Config
from .disciplines import DISCIPLINES, Rules, normalize

REPORT_NAME = "keyword-suggestions.csv"
REFERENCE_SHARE = 0.85          # a words-decided document is reference when its top share is this high...
REFERENCE_SCORE_FACTOR = 3      # ... and its score is at least this many times min_score

STOPWORDS = frozenset(normalize(w) for w in """
a au aux avec ce ces cet cette dans de des du elle elles en est et il ils je la le les leur leurs lui ma mais
me meme mes moi mon ne ni nos notre nous on ou par pas pour qu que qui sa se ses si son sur ta te tes toi ton
tu un une vos votre vous ont sont sera etre avoir fait faire plus moins tout tous toute toutes comme aussi
donc car ainsi alors entre vers sous chez sans selon ceci cela celui celle ceux celles dont ici
the and for are but not you all any can had her was one our out has have this that with from they will
would there their what about which when your said each she how its than them these those then some into
over such only also been being were does did more most other very just like may must shall should could
page pages fichier fichiers document documents version
""".split())


@dataclass
class Suggestion:
    discipline: str
    word: str
    docs_in: int
    docs_out: int
    lift: float
    examples: list[str] = field(default_factory=list)


def _listed(rules: Rules) -> set[str]:
    return {w for d in DISCIPLINES for kind in ("strong", "weak") for w in rules.words.get(d, {}).get(kind, [])}


def _candidates_of(tokens: list[str], listed_words: set[str], listed_tokens: set[str]) -> set[str]:
    out: set[str] = set()
    for t in tokens:
        if len(t) >= 4 and not t.isdigit() and t not in STOPWORDS and t not in listed_words:
            out.add(t)
    for a, b in zip(tokens, tokens[1:]):
        if a in STOPWORDS or b in STOPWORDS or a.isdigit() or b.isdigit() or len(a) < 3 or len(b) < 3:
            continue
        if a in listed_tokens or b in listed_tokens or f"{a} {b}" in listed_words:
            continue
        out.add(f"{a} {b}")
    return out


def candidates(con, cfg: Config, rules: Rules) -> list[Suggestion]:
    """Words worth proposing, per discipline (see the module docstring and the design, section 3b)."""
    settings = rules.suggestions
    listed_words = _listed(rules)
    listed_tokens = {t for w in listed_words for t in w.split()}
    root_keys = [(store.path_key(r), r) for r in cfg.roots]
    reference: dict[str, list] = {d: [] for d in DISCIPLINES}      # discipline -> [(path, folder, candidates)]
    for doc_id, path, key, title, discipline, source, scores in con.execute(
            """SELECT d.id, d.path, d.path_key, d.title, x.discipline, x.source, x.scores
               FROM docs d JOIN doc_discipline x ON x.doc_id = d.id""").fetchall():
        if discipline not in DISCIPLINES:
            continue
        if source != "rule":
            s = json.loads(scores)
            total = sum(s.values())
            if (source != "words" or total < REFERENCE_SCORE_FACTOR * rules.min_score
                    or s.get(discipline, 0) / total < REFERENCE_SHARE):
                continue
        rel, body = labelling.text_of(con, doc_id, path, key, root_keys)
        tokens = normalize(f"{rel} {title or ''} {body}").split()
        reference[discipline].append((path, os.path.dirname(key), _candidates_of(tokens, listed_words, listed_tokens)))
    sizes = {d: len(docs) for d, docs in reference.items()}
    df: dict[str, dict[str, int]] = {}                              # candidate -> discipline -> documents
    folders: dict[tuple[str, str], set[str]] = {}
    examples: dict[tuple[str, str], list[str]] = {}
    for discipline, docs in reference.items():
        for path, folder, cands in docs:
            for c in cands:
                per = df.setdefault(c, {})
                per[discipline] = per.get(discipline, 0) + 1
                folders.setdefault((discipline, c), set()).add(folder)
                ex = examples.setdefault((discipline, c), [])
                if len(ex) < 3:
                    ex.append(path)
    found: dict[str, list[Suggestion]] = {d: [] for d in DISCIPLINES}
    for c, per in df.items():
        best = max(DISCIPLINES, key=lambda d: (per.get(d, 0) / sizes[d]) if sizes[d] else -1)
        n_in = sizes[best]
        if not n_in:
            continue
        df_in = per.get(best, 0)
        df_out = sum(v for d, v in per.items() if d != best)
        n_out = sum(sizes[d] for d in DISCIPLINES if d != best)
        lift = (df_in / n_in) / ((df_out + 1) / (n_out + 1))
        if (df_in >= settings["min_docs"] and len(folders[(best, c)]) >= settings["min_sections"]
                and lift >= settings["min_lift"]):
            found[best].append(Suggestion(best, c, df_in, df_out, lift, examples[(best, c)]))
    out: list[Suggestion] = []
    for d in DISCIPLINES:
        ranked = sorted(found[d], key=lambda s: (-s.lift, s.word))
        out.extend(ranked[:settings["max_rows"]])
    return out


def write_report(rows: list[Suggestion], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        out = csv.writer(fh, delimiter=";")
        out.writerow(["discipline", "word", "docs_in", "docs_out", "lift", "example_1", "example_2", "example_3"])
        for s in rows:
            out.writerow([s.discipline, s.word, s.docs_in, s.docs_out, f"{s.lift:.1f}"]
                         + s.examples + [""] * (3 - len(s.examples)))


def refresh(con, cfg: Config, rules: Rules, progress=print) -> list[Suggestion]:
    """Write keyword-suggestions.csv and remind the user, after every run, of what waits for review."""
    rows = candidates(con, cfg, rules)
    path = cfg.world_dir / REPORT_NAME
    try:
        write_report(rows, path)
    except OSError as e:
        progress(f"{REPORT_NAME} not written: {e}")
    keys = [f"{s.discipline}|{s.word}" for s in rows]
    previous = set(json.loads(store.get_meta(con, "suggestions_prev", "[]")))
    store.set_meta(con, "suggestions_prev", json.dumps(keys))
    store.set_meta(con, "suggestions_waiting", len(rows))
    if rows:
        new = sum(1 for k in keys if k not in previous)
        progress(f"Keyword suggestions: {len(rows)} waiting for your review ({new} new since the last run) in "
                 f"{path}. Nothing is added to disciplines.toml until you approve it.")
    else:
        progress("Keyword suggestions: none.")
    return rows

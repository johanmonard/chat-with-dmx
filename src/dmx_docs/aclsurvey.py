"""Read-only survey of the Windows permissions (NTFS) of the folders a world indexed.

A world is meant to be shared with one audience. This survey shows whether the documents of a
world are all readable by the same AD groups, or whether some folders have rules of their own.
It reads the index (read-only) and the folders' access lists; it changes nothing.

A "rule" is the set of accounts and groups allowed to read the files of a folder (and those
denied). Two folders with the same readers have the same rule, even if their write rights differ.
A "boundary" is a folder whose rule differs from its parent's: where a different audience starts.

Not covered: the share permissions of the file server (only its administrators can read them) and,
unless --files is given, files that carry permissions of their own.
"""

from __future__ import annotations

import csv
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import store

# Access mask bits that let an account read a file's content.
FILE_READ_DATA = 0x0001
GENERIC_ALL = 0x10000000
GENERIC_READ = 0x80000000
READ_BITS = FILE_READ_DATA | GENERIC_ALL | GENERIC_READ

# ACE header flags.
OBJECT_INHERIT_ACE = 0x01
INHERIT_ONLY_ACE = 0x08
INHERITED_ACE = 0x10

ACCESS_ALLOWED_ACE_TYPE = 0
ACCESS_DENIED_ACE_TYPE = 1


@dataclass(frozen=True)
class Acl:
    """What a folder's access list means for the files in it."""
    allow: frozenset[str]           # SIDs (string form) that may read the files
    deny: frozenset[str]            # SIDs denied reading the files
    protected: bool = False         # inheritance from the parent is switched off
    explicit: int = 0               # access entries set on this folder itself (not inherited)

    @property
    def key(self) -> tuple[frozenset[str], frozenset[str]]:
        return self.allow, self.deny


def readers_from_aces(aces, for_files: bool) -> tuple[frozenset[str], frozenset[str], int]:
    """aces: (ace_type, ace_flags, mask, sid) tuples. for_files: the rule the files of a folder get
    (entries inherited by files), else the rule of the object itself (a file's own list)."""
    allow, deny, explicit = set(), set(), 0
    if for_files:
        chosen = [a for a in aces if a[1] & OBJECT_INHERIT_ACE]
        if not chosen:  # an unusual list that files do not inherit from: judge by the folder itself
            chosen = [a for a in aces if not a[1] & INHERIT_ONLY_ACE]
    else:
        chosen = [a for a in aces if not a[1] & INHERIT_ONLY_ACE]
    for ace_type, flags, mask, sid in aces:
        if not flags & INHERITED_ACE:
            explicit += 1
    for ace_type, flags, mask, sid in chosen:
        if not mask & READ_BITS:
            continue
        if ace_type == ACCESS_ALLOWED_ACE_TYPE:
            allow.add(sid)
        elif ace_type == ACCESS_DENIED_ACE_TYPE:
            deny.add(sid)
    return frozenset(allow), frozenset(deny), explicit


def read_windows_acl(path: str, for_files: bool = True) -> Acl:
    """The access list of a folder (for_files: what its files inherit) or of one file."""
    import win32security

    sd = win32security.GetFileSecurity(store.fs_path(path), win32security.DACL_SECURITY_INFORMATION)
    control, _rev = sd.GetSecurityDescriptorControl()
    protected = bool(control & win32security.SE_DACL_PROTECTED)
    dacl = sd.GetSecurityDescriptorDacl()
    aces = []
    if dacl is None:  # no access list at all: everyone has full access
        return Acl(frozenset({"S-1-1-0"}), frozenset(), protected, 0)
    for i in range(dacl.GetAceCount()):
        ace = dacl.GetAce(i)
        (ace_type, flags), mask = ace[0], ace[1]
        if ace_type not in (ACCESS_ALLOWED_ACE_TYPE, ACCESS_DENIED_ACE_TYPE):
            continue  # audit and object entries do not grant reading on files
        aces.append((ace_type, flags, mask, win32security.ConvertSidToStringSid(ace[2])))
    allow, deny, explicit = readers_from_aces(aces, for_files)
    return Acl(allow, deny, protected, explicit)


def windows_account_name(sid: str) -> str:
    import win32security

    try:
        name, domain, _type = win32security.LookupAccountSid(None, win32security.ConvertStringSidToSid(sid))
    except Exception:
        return sid  # deleted account or group: only its SID is left
    return f"{domain}\\{name}" if domain else name


@dataclass
class Survey:
    world: str
    docs: int = 0
    folders: int = 0
    rules: dict = field(default_factory=dict)        # key -> rule id (R1, R2...)
    rule_acl: dict = field(default_factory=dict)     # rule id -> Acl
    rule_docs: dict = field(default_factory=dict)    # rule id -> documents under it
    rule_folders: dict = field(default_factory=dict)  # rule id -> folders with it
    boundaries: list = field(default_factory=list)   # dicts: path, rule, parent_rule, protected, explicit, docs
    errors: list = field(default_factory=list)       # (path, message)
    files: list = field(default_factory=list)        # dicts: path, rule, folder_rule (--files only)
    names: dict = field(default_factory=dict)        # SID -> account name
    seconds: float = 0.0

    def rule_of(self, acl: Acl) -> str:
        rid = self.rules.get(acl.key)
        if rid is None:
            rid = f"R{len(self.rules) + 1}"
            self.rules[acl.key] = rid
            self.rule_acl[rid] = acl
            self.rule_docs[rid] = 0
            self.rule_folders[rid] = 0
        return rid


def _root_of(folder_key: str, root_keys: list[str]) -> str | None:
    best = None
    for rk in root_keys:
        if store.is_under(folder_key, rk) and (best is None or len(rk) > len(best)):
            best = rk
    return best


def run_survey(world: str, doc_paths: list[str], roots: list[str],
               read_acl: Callable[..., Acl] = read_windows_acl,
               account_name: Callable[[str], str] = windows_account_name,
               check_files: bool = False, workers: int = 16) -> Survey:
    """doc_paths: the indexed documents; roots: the world's root folders."""
    started = time.monotonic()
    s = Survey(world=world, docs=len(doc_paths))
    root_keys = {store.path_key(r): r for r in roots}

    # Folders holding documents, and every folder between them and their root.
    display: dict[str, str] = {}        # folder key -> path as written
    docs_in: dict[str, int] = {}
    doc_folder: dict[str, str] = {}
    for p in doc_paths:
        folder = os.path.dirname(p)
        k = store.path_key(folder)
        docs_in[k] = docs_in.get(k, 0) + 1
        doc_folder[p] = k
        if k in display:
            continue
        rk = _root_of(k, list(root_keys))
        while k not in display:
            display[k] = folder
            if rk is None or k == rk:
                break
            folder = os.path.dirname(folder)
            k = store.path_key(folder)
    for rk, r in root_keys.items():
        display.setdefault(rk, r)

    def one(k):
        try:
            return k, read_acl(display[k]), None
        except Exception as e:  # unreadable list, missing folder...
            return k, None, f"{type(e).__name__}: {e}"

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, sorted(display)))
    acls: dict[str, Acl] = {}
    for k, acl, err in results:
        if acl is None:
            s.errors.append((display[k], err))
        else:
            acls[k] = acl
    s.folders = len(acls)

    folder_rule: dict[str, str] = {}
    for k in sorted(acls):  # parents sort before their children
        rid = s.rule_of(acls[k])
        folder_rule[k] = rid
        s.rule_folders[rid] += 1
        s.rule_docs[rid] += docs_in.get(k, 0)
    for k in sorted(acls):
        parent = store.path_key(os.path.dirname(display[k]))
        parent_rule = folder_rule.get(parent) if k not in root_keys else None
        if parent_rule != folder_rule[k]:
            below = sum(n for fk, n in docs_in.items()
                        if store.is_under(fk, k) and folder_rule.get(fk) == folder_rule[k])
            acl = acls[k]
            s.boundaries.append(dict(path=display[k], rule=folder_rule[k], parent_rule=parent_rule or "",
                                     protected=acl.protected, explicit=acl.explicit, docs=below,
                                     root=k in root_keys))

    if check_files:
        def one_file(p):
            try:
                return p, read_acl(p, for_files=False), None
            except Exception as e:
                return p, None, f"{type(e).__name__}: {e}"

        with ThreadPoolExecutor(max_workers=workers) as pool:
            for p, acl, err in pool.map(one_file, doc_paths):
                if acl is None:
                    s.errors.append((p, err))
                    continue
                frule = folder_rule.get(doc_folder[p])
                if acl.explicit and (frule is None or s.rule_acl[frule].key != acl.key):
                    rid = s.rule_of(acl)
                    s.files.append(dict(path=p, rule=rid, folder_rule=frule or ""))
                    s.rule_docs[rid] += 1
                    if frule:
                        s.rule_docs[frule] -= 1

    sids = set()
    for acl in s.rule_acl.values():
        sids |= acl.allow | acl.deny
    s.names = {sid: account_name(sid) for sid in sorted(sids)}
    s.seconds = time.monotonic() - started
    return s


def _names(s: Survey, sids) -> str:
    return "; ".join(sorted(s.names.get(x, x) for x in sids)) or "-"


def write_reports(s: Survey, out_dir: str | os.PathLike) -> list[Path]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M")
    base = f"acl-{s.world}-{stamp}"
    written = []

    ordered = sorted(s.rule_acl, key=lambda r: -s.rule_docs[r])
    p = out / f"{base}-rules.csv"
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["rule", "documents", "folders", "can_read", "denied"])
        for r in ordered:
            w.writerow([r, s.rule_docs[r], s.rule_folders[r], _names(s, s.rule_acl[r].allow),
                        _names(s, s.rule_acl[r].deny)])
    written.append(p)

    p = out / f"{base}-boundaries.csv"
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(["folder", "rule", "parent_rule", "documents_below_with_this_rule",
                    "inheritance_off", "own_entries"])
        for b in sorted(s.boundaries, key=lambda b: b["path"].lower()):
            w.writerow([b["path"], b["rule"], b["parent_rule"] or ("(root)" if b["root"] else ""),
                        b["docs"], "yes" if b["protected"] else "", b["explicit"]])
    written.append(p)

    if s.files:
        p = out / f"{base}-files.csv"
        with open(p, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f, delimiter=";")
            w.writerow(["file", "rule", "folder_rule"])
            for x in s.files:
                w.writerow([x["path"], x["rule"], x["folder_rule"]])
        written.append(p)

    lines = [f"# Permissions survey: world {s.world}", "",
             f"{time.strftime('%Y-%m-%d %H:%M')} on {os.environ.get('COMPUTERNAME', '?')} "
             f"as {os.environ.get('USERDOMAIN', '')}\\{os.environ.get('USERNAME', '?')}, "
             f"{s.seconds / 60:.1f} min.", "",
             f"{s.docs:,} documents in {s.folders:,} folders read; {len(s.rule_acl)} different reading rules; "
             f"{sum(1 for b in s.boundaries if not b['root'])} folders where a different rule starts "
             f"(boundaries); {len(s.errors)} folders or files whose permissions could not be read.", ""]
    if len(s.rule_acl) == 1:
        lines += ["**All documents of this world are readable by the same accounts and groups.**", ""]
    lines += ["## Rules (most documents first)", ""]
    for r in ordered:
        acl = s.rule_acl[r]
        lines.append(f"- **{r}**: {s.rule_docs[r]:,} documents, {s.rule_folders[r]:,} folders")
        lines.append(f"  - can read: {_names(s, acl.allow)}")
        if acl.deny:
            lines.append(f"  - denied: {_names(s, acl.deny)}")
    lines += ["", "## Where each rule starts (first 200 boundaries, by documents)", ""]
    for b in sorted(s.boundaries, key=lambda b: -b["docs"])[:200]:
        what = "root" if b["root"] else f"{b['parent_rule']} -> {b['rule']}"
        extra = ", inheritance off" if b["protected"] else ""
        lines.append(f"- {b['path']}  ({what}, {b['docs']:,} documents{extra})")
    if s.files:
        lines += ["", f"## Files with permissions of their own: {len(s.files):,} (see the -files.csv)"]
    if s.errors:
        lines += ["", f"## Not readable ({len(s.errors)}, first 50)", ""]
        lines += [f"- {path}: {msg}" for path, msg in s.errors[:50]]
    lines += ["", "Not covered: share permissions of the file server"
              + ("" if s.files else "; files with permissions of their own (run with --files)") + ".",
              "Detail: " + ", ".join(x.name for x in written)]
    p = out / f"{base}.md"
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    written.insert(0, p)
    return written


def survey_index(db_path: str | os.PathLike, world: str, **kw) -> Survey:
    """Read the documents and roots of an index without changing it, then survey their folders."""
    con = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    try:
        docs = [r[0] for r in con.execute("SELECT path FROM docs")]
        roots = [r[0] for r in con.execute("SELECT path FROM roots")]
    finally:
        con.close()
    return run_survey(world, docs, roots, **kw)

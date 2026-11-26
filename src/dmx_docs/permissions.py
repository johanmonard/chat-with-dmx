"""Permissions refresh: read the reading rule of every folder holding indexed documents and write
access.sqlite3 (see access.py), next to the master index and next to the local copy.

Fail closed: a folder whose rule could not be read and never was readable has no rule (its documents
stay hidden). A folder whose rule cannot be read now keeps the rule it had at the last refresh.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path

from . import aclsurvey
from .access import AccessData, AccessStore, deny_judgeable, readonly_uri, usable_sids

log = logging.getLogger("dmx_docs.permissions")

WRITE_ATTEMPTS = 5
WRITE_DELAY_SECONDS = 0.5


@dataclass
class RefreshResult:
    folders: int
    rules: int
    changed: list[tuple[str, int | None, int | None]] = field(default_factory=list)  # folder, old rule id, new rule id
    unreadable: list[tuple[str, str]] = field(default_factory=list)                  # folder, message
    docs_affected: int = 0
    version: str = ""
    previous_from: str = ""   # the file the previous rules were read from ("" when there were none)
    missing: int = 0          # folders of the previous rules missing from this machine's index, read again
    missing_dropped: int = 0  # of those, folders not found on the file server (deleted or moved): rule dropped
    read: int = 0             # folders whose rule was read in this run
    allow_nobody: int = 0     # of those, folders whose rule allows nobody once local/builtin SIDs are dropped
    deny_unjudged: list[str] = field(default_factory=list)  # folders made admins-only: a deny entry names
                                                             # a local/builtin SID that cannot be judged here


def domain_prefix(cfg) -> str:
    """The AD domain's SID prefix: [access] domain_sid, else the running account's SID minus its last part."""
    if cfg.access_domain_sid:
        return cfg.access_domain_sid
    import win32api
    import win32con
    import win32security

    token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
    user_sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    return win32security.ConvertSidToStringSid(user_sid).rpartition("-")[0]


def _index_folders(db_path: Path) -> tuple[dict[str, str], dict[str, int]]:
    """Folder key -> folder as written, and documents per folder key, from the index (read-only)."""
    display: dict[str, str] = {}
    counts: dict[str, int] = {}
    with closing(sqlite3.connect(readonly_uri(db_path), uri=True)) as con:
        for path, folder_key in con.execute("SELECT path, folder_key FROM docs"):
            display.setdefault(folder_key, os.path.dirname(path))
            counts[folder_key] = counts.get(folder_key, 0) + 1
    return display, counts


def _previous(out_paths: list[Path]) -> tuple[AccessData | None, Path | None]:
    """The previous rules and the file they come from: the first out path that can be read
    (the master first)."""
    for p in out_paths:
        if Path(p).exists():
            try:
                return AccessStore(p).load(), Path(p)
            except sqlite3.Error as e:
                log.warning("previous rules: %s not readable (%s), trying the next file", p, e)
                continue
    return None, None


def _version_of(folder_pairs: dict[str, tuple[frozenset, frozenset]]) -> str:
    canon = sorted((k, sorted(a), sorted(d)) for k, (a, d) in folder_pairs.items())
    return hashlib.sha1(json.dumps(canon).encode("utf-8")).hexdigest()[:12]


def _write(path: Path, *args, **kw) -> None:
    """AccessStore.write, retried: on Windows the replace fails while a reader holds the old file open."""
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(1, WRITE_ATTEMPTS + 1):
        try:
            AccessStore.write(path, *args, **kw)
            return
        except PermissionError:
            if attempt == WRITE_ATTEMPTS:
                raise
            time.sleep(WRITE_DELAY_SECONDS)


def refresh_permissions(cfg, out_paths: list[Path], read_acl=aclsurvey.read_windows_acl,
                        account_name=aclsurvey.windows_account_name, workers: int = 16) -> RefreshResult:
    prefix = domain_prefix(cfg)
    display, doc_counts = _index_folders(cfg.db_path)
    prev, prev_from = _previous(out_paths)
    prev_pairs: dict[str, tuple[frozenset, frozenset]] = {}
    prev_ids: dict[str, int] = {}
    if prev:
        prev_pairs = {k: prev.rules[rid] for k, rid in prev.folder_rule.items() if rid in prev.rules}
        prev_ids = dict(prev.folder_rule)

    # Folders of the previous rules that this machine's index does not hold (a local copy older than
    # the master's index) are read again too, by their folder key (a full path): a rule is never
    # carried forward unread, or a group removed from the folder would keep reading it.
    missing = sorted(k for k in prev_pairs if k not in display)

    def one(k):
        try:
            return k, read_acl(display.get(k, k), for_files=True), None, False
        except Exception as e:  # unreadable list, missing folder...
            return k, None, f"{type(e).__name__}: {e}", _not_found(e)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(one, sorted(display) + missing))

    new_pairs: dict[str, tuple[frozenset, frozenset]] = {}
    unreadable: list[tuple[str, str]] = []
    errors: dict[str, str] = {}
    read = allow_nobody = dropped = 0
    deny_unjudged: list[str] = []
    for k, acl, err, gone in results:
        if acl is not None:
            allow = usable_sids(acl.allow, prefix)
            deny = frozenset(s for s in acl.deny if deny_judgeable(s, prefix))
            if deny != frozenset(acl.deny):
                # A deny entry for NETWORK, BUILTIN\Users, a file-server local account...: on the file
                # server it may shut out every user, here it cannot be judged. Fail closed: admins only.
                allow = frozenset()
                deny_unjudged.append(display.get(k, k))
            new_pairs[k] = (allow, deny)
            read += 1
            if not new_pairs[k][0]:
                allow_nobody += 1
        elif gone and k not in display:
            dropped += 1  # deleted or moved, and no indexed document here: its rule goes
        else:
            unreadable.append((display.get(k, k), err))
            errors[k] = err
            if k in prev_pairs:
                new_pairs[k] = prev_pairs[k]  # access denied, network...: the last rule read is kept

    # Rule ids: keep the previous id of a rule that still exists, number new ones after the highest.
    ids: dict[tuple[frozenset, frozenset], int] = {}
    if prev:
        ids = {pair: rid for rid, pair in prev.rules.items()}
    next_id = max(prev.rules, default=0) + 1 if prev else 1
    used: dict[tuple[frozenset, frozenset], int] = {}
    for k in sorted(new_pairs):
        pair = new_pairs[k]
        if pair not in used:
            if pair in ids:
                used[pair] = ids[pair]
            else:
                used[pair] = next_id
                next_id += 1
    rule_sets = {rid: pair for pair, rid in used.items()}
    folder_rules = {k: used[pair] for k, pair in new_pairs.items()}

    changed: list[tuple[str, int | None, int | None]] = []
    docs_affected = 0
    for k in sorted(set(new_pairs) | set(prev_pairs)):
        if new_pairs.get(k) != prev_pairs.get(k):
            changed.append((display.get(k, k), prev_ids.get(k), folder_rules.get(k)))
            docs_affected += doc_counts.get(k, 0)
    version = _version_of(new_pairs) if changed or prev is None else prev.version

    sids = set()
    for allow, deny in rule_sets.values():
        sids |= allow | deny
    names = {sid: account_name(sid) for sid in sorted(sids)}
    for out in out_paths:   # master first, then local
        _write(Path(out), {str(rid): rid for rid in rule_sets}, folder_rules, rule_sets, errors, names, version)

    return RefreshResult(folders=len(display), rules=len(rule_sets), changed=changed, unreadable=unreadable,
                         docs_affected=docs_affected, version=version,
                         previous_from=str(prev_from) if prev is not None else "",
                         missing=len(missing), missing_dropped=dropped, read=read, allow_nobody=allow_nobody,
                         deny_unjudged=deny_unjudged)


def _not_found(e: Exception) -> bool:
    """Does the error say the folder does not exist (deleted or moved)? Win32 errors 2 and 3 (file,
    path not found); network errors and access denied are not."""
    return isinstance(e, FileNotFoundError) or getattr(e, "winerror", None) in (2, 3)


def nobody_warning(result: RefreshResult) -> str | None:
    """A loud warning when every folder read allows nobody: typically a run under an account outside
    the domain without [access] domain_sid, which drops every domain SID as local to some machine."""
    if result.read and result.allow_nobody == result.read:
        return (f"WARNING: all {result.read:,} folders read allow nobody once local and built-in SIDs are "
                "dropped: nobody but the admins will read this world. Run under a domain account or set "
                "[access] domain_sid.")
    return None


def write_change_report(result: RefreshResult, out_dir: Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    p = out / f"changes-{time.strftime('%Y%m%d-%H%M')}.md"
    lines = ["# Permissions refresh", "",
             f"{time.strftime('%Y-%m-%d %H:%M')} on {os.environ.get('COMPUTERNAME', '?')}: "
             f"{result.folders:,} folders, {result.rules} different reading rules, version {result.version}.", "",
             f"{len(result.changed)} folders changed their rule ({result.docs_affected:,} documents affected); "
             f"{len(result.unreadable)} folders could not be read.", "",
             "Previous rules read from: " + (result.previous_from or "none (first refresh)") + ".",
             f"{result.missing:,} folders not in this machine's index, re-read from the file server; "
             f"{result.missing_dropped:,} dropped as deleted.",
             f"{result.allow_nobody:,} folders whose rule allows nobody after dropping local/builtin SIDs "
             f"(of {result.read:,} read).",
             f"{len(result.deny_unjudged):,} folders made admins-only: a deny entry names a local or built-in "
             "account that cannot be judged outside the file server.", ""]
    warning = nobody_warning(result)
    if warning:
        lines[2:2] = [f"**{warning}**", ""]
    if result.changed:
        lines += ["## Changed", ""]
        for folder, old, new in result.changed:
            lines.append(f"- {folder}: rule {old if old is not None else '-'} -> "
                         f"{new if new is not None else '-'}")
        lines += [""]
    if result.deny_unjudged:
        lines += ["## Admins only (deny entry that cannot be judged)", ""]
        lines += [f"- {path}" for path in result.deny_unjudged[:200]]
        lines += [""]
    if result.unreadable:
        lines += ["## Not readable (previous rule kept, none for a new folder)", ""]
        lines += [f"- {path}: {msg}" for path, msg in result.unreadable[:200]]
        lines += [""]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p

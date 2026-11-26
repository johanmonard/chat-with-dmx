"""Per-person access: which documents a caller may see, from the file server's reading rules.

A "rule" is what aclsurvey calls it: the SIDs allowed to read the files of a folder and the SIDs
denied. A refresh job (elsewhere) writes them to access.sqlite3 next to a world's index; the server
reads that file and answers, for each caller, whether the folder of a document is readable.

Fail closed: a folder without a rule, or a world whose access file does not exist yet, is hidden
from everybody except admins.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

import numpy as np

log = logging.getLogger("dmx_docs.access")

# Everyone and Authenticated Users: every caller carries them, rules naming them are kept.
KEEP_ALWAYS = {"S-1-1-0", "S-1-5-11"}

RELOAD_SECONDS = 60

_DROPPED_EXACT = {"S-1-3-0", "S-1-3-4", "S-1-5-18", "S-1-5-19", "S-1-5-20"}

_SCHEMA = """
CREATE TABLE acl_rules(id INTEGER PRIMARY KEY, hash TEXT UNIQUE, allow TEXT, deny TEXT);
CREATE TABLE folder_acl(folder_key TEXT PRIMARY KEY, rule_id INTEGER, read_at REAL, error TEXT);
CREATE TABLE acl_names(sid TEXT PRIMARY KEY, name TEXT);
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT);
"""


def usable_sids(sids: Iterable[str], domain_prefix: str) -> frozenset[str]:
    """Keep the SIDs that mean the same on every machine: drop creator-owner SIDs, built-in and
    system accounts, and accounts of a domain other than the AD domain (local to some machine)."""
    keep = set()
    for sid in sids:
        if sid in KEEP_ALWAYS:
            keep.add(sid)
        elif sid in _DROPPED_EXACT or sid.startswith("S-1-5-32-"):
            continue
        elif sid.startswith("S-1-5-21-"):
            if sid.rpartition("-")[0] == domain_prefix:
                keep.add(sid)
        else:
            keep.add(sid)
    return frozenset(keep)


def deny_judgeable(sid: str, domain_prefix: str) -> bool:
    """Can a deny entry for this SID be judged from a caller's AD groups? Only Everyone, Authenticated
    Users and the AD domain's accounts: NETWORK, INTERACTIVE, BUILTIN\\Users, a machine's local accounts...
    may shut out every user on the file server but are never among a caller's groups here."""
    return sid in KEEP_ALWAYS or (sid.startswith("S-1-5-21-") and sid.rpartition("-")[0] == domain_prefix)


def may_read(caller_sids: frozenset[str], allow: frozenset[str], deny: frozenset[str]) -> bool:
    return bool(caller_sids & allow) and not (caller_sids & deny)


def readonly_uri(path) -> str:
    """SQLite URI opening `path` read-only, for a drive path (D:\\x\\a.sqlite3 -> file:D:/x/a.sqlite3)
    as for a UNC path (\\\\server\\share\\a.sqlite3 -> file:////server/share/a.sqlite3, which plain
    'file:' + path refuses as an invalid authority). '?', '#' and '%' in names are escaped."""
    encoded = quote(os.fspath(path).replace("\\", "/"), safe="/:")
    return f"file:{'//' if encoded.startswith('//') else ''}{encoded}?mode=ro"


class FolderPredicate:
    """Function folder key -> may the caller read it, built from one snapshot of the rules; `token`
    names that snapshot (equal tokens: the same decision for every folder)."""
    __slots__ = ("_contains", "token")

    def __init__(self, folders: frozenset[str], token: str):
        self._contains = folders.__contains__
        self.token = token

    def __call__(self, folder_key) -> bool:
        return self._contains(folder_key)


def readable_clause(con, predicate, column: str = "d.folder_key") -> str | None:
    """SQL condition keeping the rows whose `column` passes `predicate` (None when there is no
    predicate: no restriction, no clause). Filtering in SQL rather than afterwards keeps hidden
    rows from taking the slots of a LIMIT. Registers the function dmx_can_read on `con`: one
    predicate per connection."""
    if predicate is None:
        return None
    con.create_function("dmx_can_read", 1, lambda value: bool(predicate(value)), deterministic=True)
    return f"dmx_can_read({column})"


@dataclass(frozen=True)
class AccessData:
    version: str
    rules: dict[int, tuple[frozenset[str], frozenset[str]]]
    folder_rule: dict[str, int]     # folder key -> rule id; folders without a rule are absent


def _rule_hash(allow, deny) -> str:
    return hashlib.sha1(json.dumps([sorted(allow), sorted(deny)]).encode("utf-8")).hexdigest()


class AccessStore:
    def __init__(self, path: Path):
        self.path = Path(path)

    @staticmethod
    def write(path: Path, rules: dict[str, int], folder_rules: dict[str, int | None],
              rule_sets: dict[int, tuple[frozenset, frozenset]], errors: dict[str, str],
              names: dict[str, str], version: str) -> None:
        """Write the whole file as <path>.new, then replace the old one (readers never see half a file).
        rules maps a rule's key to its id (informational: the ids used are those of rule_sets)."""
        path = Path(path)
        tmp = path.with_name(path.name + ".new")
        if tmp.exists():
            tmp.unlink()
        now = time.time()
        with closing(sqlite3.connect(tmp)) as con:
            con.executescript(_SCHEMA)
            con.executemany(
                "INSERT INTO acl_rules(id, hash, allow, deny) VALUES (?,?,?,?)",
                [(rid, _rule_hash(a, d), json.dumps(sorted(a)), json.dumps(sorted(d)))
                 for rid, (a, d) in rule_sets.items()])
            folders = set(folder_rules) | set(errors)
            con.executemany(
                "INSERT INTO folder_acl(folder_key, rule_id, read_at, error) VALUES (?,?,?,?)",
                [(k, folder_rules.get(k), now, errors.get(k)) for k in folders])
            con.executemany("INSERT INTO acl_names(sid, name) VALUES (?,?)", list(names.items()))
            con.execute("INSERT INTO meta(key, value) VALUES ('acl_version', ?)", (version,))
            con.commit()
        os.replace(tmp, path)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(readonly_uri(self.path), uri=True)

    def read_version(self) -> str | None:
        """The file's acl_version; None when the file is missing or unreadable."""
        if not self.path.exists():
            return None
        try:
            with closing(self._connect()) as con:
                row = con.execute("SELECT value FROM meta WHERE key='acl_version'").fetchone()
        except sqlite3.Error:
            return None
        return row[0] if row else None

    def load(self) -> AccessData | None:
        if not self.path.exists():
            return None
        with closing(self._connect()) as con:
            row = con.execute("SELECT value FROM meta WHERE key='acl_version'").fetchone()
            rules = {rid: (frozenset(json.loads(a)), frozenset(json.loads(d)))
                     for rid, a, d in con.execute("SELECT id, allow, deny FROM acl_rules")}
            folder_rule = {k: rid for k, rid in
                           con.execute("SELECT folder_key, rule_id FROM folder_acl WHERE rule_id IS NOT NULL")}
        return AccessData(row[0] if row else "", rules, folder_rule)


def _current_user_domain_prefix(win32security, token) -> tuple[str, str]:
    user_sid = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
    text = win32security.ConvertSidToStringSid(user_sid)
    return text, text.rpartition("-")[0]


def admin_names(admins: Iterable[str]) -> frozenset[str]:
    """The [access] admins entries that name one account: DOMAIN\\name, folded to lower case. A bare
    name could be a local account of any machine: it is ignored, with a warning."""
    wanted = set()
    for entry in admins:
        entry = entry.strip()
        if not entry:
            continue
        domain, sep, name = entry.partition("\\")
        if not sep or not domain.strip() or not name.strip():
            log.warning("[access] admins: '%s' ignored, write it as DOMAIN\\name", entry)
            continue
        wanted.add(entry.lower())
    return frozenset(wanted)


def is_admin_account(wanted: frozenset[str], domain: str, name: str) -> bool:
    """Is the account domain\\name one of the admins (admin_names)? Matched on DOMAIN\\name only."""
    return bool(domain) and f"{domain}\\{name}".lower() in wanted


@dataclass(frozen=True)
class Caller:
    sids: frozenset[str]
    admin: bool = False
    label: str = ""

    @classmethod
    def from_token(cls, admins: list[str]) -> "Caller":
        """The process's own identity: user SID and the groups of its token (not deny-only)."""
        import win32api
        import win32con
        import win32security

        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        user, prefix = _current_user_domain_prefix(win32security, token)
        raw = {user}
        for sid, attrs in win32security.GetTokenInformation(token, win32security.TokenGroups):
            if not attrs & win32security.SE_GROUP_USE_FOR_DENY_ONLY:
                raw.add(win32security.ConvertSidToStringSid(sid))
        wanted = admin_names(admins)
        admin = False
        if wanted:
            for sid in raw:
                try:
                    name, domain, _t = win32security.LookupAccountSid(
                        None, win32security.ConvertStringSidToSid(sid))
                except Exception:
                    continue
                if is_admin_account(wanted, domain, name):
                    admin = True
                    break
        try:
            uname, udomain, _t = win32security.LookupAccountSid(None, win32security.ConvertStringSidToSid(user))
            label = f"{udomain}\\{uname}" if udomain else uname
        except Exception:
            label = user
        return cls(usable_sids(raw, prefix) | KEEP_ALWAYS, admin, label)

    @classmethod
    def from_groups(cls, names: list[str]) -> "Caller":
        """A caller made of named AD groups or accounts (for a shared service speaking for a team)."""
        import win32api
        import win32con
        import win32security

        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        _user, prefix = _current_user_domain_prefix(win32security, token)
        raw = set()
        for name in names:
            try:
                sid, _domain, _t = win32security.LookupAccountName(None, name)
            except Exception as e:
                raise ValueError(f"unknown account or group: {name}") from e
            raw.add(win32security.ConvertSidToStringSid(sid))
        return cls(usable_sids(raw, prefix) | KEEP_ALWAYS, False, ", ".join(names))


@dataclass(frozen=True)
class _Snapshot:
    data: AccessData
    allowed: frozenset[int]     # ids of data.rules this caller may read, computed from the same data


class Access:
    """What one caller may read in one world."""

    def __init__(self, store: AccessStore, caller: Caller, clock=time.monotonic):
        self.store = store
        self.caller = caller
        self._clock = clock
        self._snap: _Snapshot | None = None   # replaced as one object, never edited
        self._checked: float | None = None
        self._lock = threading.Lock()         # serializes reloads; readers take no lock
        self._masks: dict = {}                # last few row masks, keyed by versions and allowed set
        self._mask_lock = threading.Lock()

    def _refresh(self) -> None:
        now = self._clock()
        if self._checked is not None and now - self._checked < RELOAD_SECONDS:
            return
        with self._lock:
            now = self._clock()
            if self._checked is not None and now - self._checked < RELOAD_SECONDS:
                return
            self._checked = now
            snap = self._snap
            try:
                if snap is not None and self.store.read_version() == snap.data.version:
                    return
                data = self.store.load()
            except Exception:
                return  # keep what we have
            if data is None:
                return
            allowed = frozenset(rid for rid, (allow, deny) in data.rules.items()
                                if may_read(self.caller.sids, allow, deny))
            self._snap = _Snapshot(data, allowed)

    def _snapshot(self) -> _Snapshot | None:
        """The current rules, folder map and allowed ids, consistent with each other (None: no data)."""
        self._refresh()
        return self._snap

    def can_read(self, path_key: str) -> bool:
        if self.caller.admin:
            return True
        snap = self._snapshot()
        if snap is None:
            return False
        rid = snap.data.folder_rule.get(os.path.dirname(path_key))
        return rid is not None and rid in snap.allowed

    def allowed_rules(self) -> frozenset[int]:
        snap = self._snapshot()
        return snap.allowed if snap else frozenset()

    @property
    def loaded(self) -> bool:
        """Are reading rules in hand (False: access.sqlite3 not written yet, only admins read)?"""
        return self._snapshot() is not None

    @property
    def version(self) -> str:
        snap = self._snapshot()
        return snap.data.version if snap else ""

    def folder_predicate(self) -> FolderPredicate | None:
        """Function folder_key -> bool for SQL (None: no restriction). Built from one snapshot,
        which its token names."""
        if self.caller.admin:
            return None
        snap = self._snapshot()
        if snap is None:
            return FolderPredicate(frozenset(), "none")
        key = ("folders", snap.data.version, snap.allowed)
        with self._mask_lock:
            pred = self._masks.get(key)
        if pred is None:
            folders = frozenset(fk for fk, rid in snap.data.folder_rule.items() if rid in snap.allowed)
            token = hashlib.sha1(json.dumps([snap.data.version, sorted(snap.allowed), sorted(folders)])
                                 .encode("utf-8")).hexdigest()
            pred = FolderPredicate(folders, token)
            with self._mask_lock:
                self._masks[key] = pred
                while len(self._masks) > 8:
                    del self._masks[next(iter(self._masks))]
        return pred

    def row_mask(self, index, con) -> np.ndarray | None:
        """Boolean mask over the vector index rows this caller may read (None: no restriction).
        Without access data a non-admin reads nothing. Rule map, allowed set and version come
        from one snapshot, so a concurrent reload cannot mix two numberings."""
        if self.caller.admin:
            return None
        snap = self._snapshot()
        if snap is None:
            return np.zeros(0 if index.ids is None else len(index.ids), dtype=bool)
        key = (index.version, snap.data.version, snap.allowed)
        with self._mask_lock:
            mask = self._masks.get(key)
        if mask is None:
            rows = index.rule_rows(con, snap.data.folder_rule, snap.data.version)
            mask = np.isin(rows, list(snap.allowed))
            with self._mask_lock:
                self._masks[key] = mask
                while len(self._masks) > 8:
                    del self._masks[next(iter(self._masks))]
        return mask


class Unrestricted:
    """Worlds with access = "world": every caller reads everything."""

    def can_read(self, path_key: str) -> bool:
        return True

    def allowed_rules(self) -> frozenset[int]:
        return frozenset()

    @property
    def version(self) -> str:
        return "world"

    def row_mask(self, index, con) -> None:
        return None

    def folder_predicate(self) -> None:
        return None

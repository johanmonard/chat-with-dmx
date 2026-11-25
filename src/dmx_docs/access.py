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
import os
import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

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


def may_read(caller_sids: frozenset[str], allow: frozenset[str], deny: frozenset[str]) -> bool:
    return bool(caller_sids & allow) and not (caller_sids & deny)


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
        return sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)

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
        wanted = {a.strip().lower() for a in admins if a.strip()}
        admin = False
        if wanted:
            for sid in raw:
                try:
                    name, domain, _t = win32security.LookupAccountSid(
                        None, win32security.ConvertStringSidToSid(sid))
                except Exception:
                    continue
                if name.lower() in wanted or f"{domain}\\{name}".lower() in wanted:
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


class Access:
    """What one caller may read in one world."""

    def __init__(self, store: AccessStore, caller: Caller, clock=time.monotonic):
        self.store = store
        self.caller = caller
        self._clock = clock
        self._data: AccessData | None = None
        self._checked: float | None = None
        self._allowed: frozenset[int] = frozenset()

    def _refresh(self) -> None:
        now = self._clock()
        if self._checked is not None and now - self._checked < RELOAD_SECONDS:
            return
        self._checked = now
        try:
            if self._data is not None and self.store.read_version() == self._data.version:
                return
            data = self.store.load()
        except Exception:
            return  # keep what we have
        if data is None:
            return
        self._data = data
        self._allowed = frozenset(
            rid for rid, (allow, deny) in data.rules.items()
            if may_read(self.caller.sids, allow, deny))

    def can_read(self, path_key: str) -> bool:
        if self.caller.admin:
            return True
        self._refresh()
        if self._data is None:
            return False
        rid = self._data.folder_rule.get(os.path.dirname(path_key))
        return rid is not None and rid in self._allowed

    def allowed_rules(self) -> frozenset[int]:
        self._refresh()
        return self._allowed

    @property
    def version(self) -> str:
        self._refresh()
        return self._data.version if self._data else ""


class Unrestricted:
    """Worlds with access = "world": every caller reads everything."""

    def can_read(self, path_key: str) -> bool:
        return True

    def allowed_rules(self) -> frozenset[int]:
        return frozenset()

    @property
    def version(self) -> str:
        return "world"

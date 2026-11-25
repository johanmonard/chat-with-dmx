import os
import sys

import pytest

from dmx_docs import store
from dmx_docs.aclsurvey import (ACCESS_ALLOWED_ACE_TYPE as ALLOW, ACCESS_DENIED_ACE_TYPE as DENY, INHERIT_ONLY_ACE,
                                INHERITED_ACE, OBJECT_INHERIT_ACE as OI, Acl, readers_from_aces, run_survey,
                                survey_index, write_reports)

SALES, ENG, ADMINS = "S-1-5-21-1-1001", "S-1-5-21-1-1002", "S-1-5-32-544"
NAMES = {SALES: "DMX\\Sales", ENG: "DMX\\Engineering", ADMINS: "BUILTIN\\Administrators"}
READ, WRITE_ONLY = 0x1200A9, 0x000116


def acl(*allow, deny=(), protected=False, explicit=0):
    return Acl(frozenset(allow), frozenset(deny), protected, explicit)


def j(*parts):
    return os.path.join(os.sep, "srv", *parts)


def fake_reader(table, files=None):
    def read(path, for_files=True):
        if not for_files:
            return (files or {}).get(path, acl(explicit=0))
        k = store.path_key(path)
        for p, a in table.items():
            if store.path_key(p) == k:
                if a is None:
                    raise PermissionError("access denied")
                return a
        raise FileNotFoundError(path)
    return read


def test_readers_ignore_write_only_entries_and_keep_denies():
    aces = [(ALLOW, OI | INHERITED_ACE, READ, SALES), (ALLOW, OI, WRITE_ONLY, ENG), (DENY, OI, READ, ENG)]
    allow, deny, explicit = readers_from_aces(aces, for_files=True)
    assert allow == {SALES} and deny == {ENG} and explicit == 2


def test_files_get_only_the_entries_they_inherit():
    aces = [(ALLOW, 0, READ, ENG), (ALLOW, OI | INHERIT_ONLY_ACE, READ, SALES)]
    assert readers_from_aces(aces, for_files=True)[0] == {SALES}       # what the files inherit
    assert readers_from_aces(aces, for_files=False)[0] == {ENG}        # the object's own list


def test_one_rule_for_the_whole_world():
    docs = [j("root", "A", "a.pdf"), j("root", "A", "sub", "b.pdf"), j("root", "c.pdf")]
    table = {j("root"): acl(SALES, ADMINS), j("root", "A"): acl(SALES, ADMINS), j("root", "A", "sub"): acl(SALES, ADMINS)}
    s = run_survey("w", docs, [j("root")], read_acl=fake_reader(table), account_name=NAMES.get, workers=2)
    assert len(s.rule_acl) == 1 and s.rule_docs["R1"] == 3 and s.folders == 3
    assert [b["root"] for b in s.boundaries] == [True]


def test_a_restricted_subfolder_is_a_boundary_with_its_documents():
    docs = [j("root", "open", "a.pdf"), j("root", "secret", "b.pdf"), j("root", "secret", "x", "c.pdf")]
    table = {j("root"): acl(SALES, ENG), j("root", "open"): acl(SALES, ENG),
             j("root", "secret"): acl(SALES, protected=True, explicit=1), j("root", "secret", "x"): acl(SALES)}
    s = run_survey("w", docs, [j("root")], read_acl=fake_reader(table), account_name=NAMES.get, workers=2)
    assert len(s.rule_acl) == 2
    inner = [b for b in s.boundaries if not b["root"]]
    assert len(inner) == 1
    b = inner[0]
    assert b["path"] == j("root", "secret") and b["docs"] == 2 and b["protected"]
    assert s.rule_docs[b["rule"]] == 2 and s.rule_docs[b["parent_rule"]] == 1


def test_unreadable_folder_is_reported_not_fatal(tmp_path):
    docs = [j("root", "a", "x.pdf"), j("root", "b", "y.pdf")]
    table = {j("root"): acl(SALES), j("root", "a"): None, j("root", "b"): acl(SALES)}
    s = run_survey("w", docs, [j("root")], read_acl=fake_reader(table), account_name=NAMES.get, workers=2)
    assert [p for p, _ in s.errors] == [j("root", "a")]
    md = write_reports(s, tmp_path)[0].read_text(encoding="utf-8")
    assert "Not readable (1" in md and "PermissionError" in md


def test_files_with_their_own_permissions_move_documents_to_another_rule():
    docs = [j("root", "a.pdf"), j("root", "b.pdf")]
    table = {j("root"): acl(SALES, ENG)}
    files = {j("root", "b.pdf"): acl(SALES, explicit=1)}
    s = run_survey("w", docs, [j("root")], read_acl=fake_reader(table, files), account_name=NAMES.get,
                   check_files=True, workers=2)
    assert [f["path"] for f in s.files] == [j("root", "b.pdf")]
    assert sorted(s.rule_docs.values()) == [1, 1]


def test_reports_name_the_groups(tmp_path):
    docs = [j("root", "a.pdf"), j("root", "s", "b.pdf")]
    table = {j("root"): acl(SALES, ENG), j("root", "s"): acl(SALES, deny=(ENG,))}
    s = run_survey("offers", docs, [j("root")], read_acl=fake_reader(table), account_name=NAMES.get, workers=2)
    paths = write_reports(s, tmp_path)
    md = paths[0].read_text(encoding="utf-8")
    assert "DMX\\Engineering; DMX\\Sales" in md and "denied: DMX\\Engineering" in md
    rules = next(p for p in paths if p.name.endswith("-rules.csv")).read_text(encoding="utf-8-sig")
    assert rules.splitlines()[0] == "rule;documents;folders;can_read;denied"
    assert any(p.name.endswith("-boundaries.csv") for p in paths)


def test_survey_index_reads_the_index_without_changing_it(tmp_path):
    db = tmp_path / "index.sqlite3"
    con = store.connect(db)
    root = str(tmp_path / "docs")
    con.execute("INSERT INTO roots(path, path_key) VALUES(?,?)", (root, store.path_key(root)))
    p = os.path.join(root, "a.pdf")
    con.execute("INSERT INTO docs(path, path_key, folder_key, name, ext, status) VALUES(?,?,?,?,?,?)",
                (p, store.path_key(p), store.path_key(root), "a.pdf", ".pdf", "ok"))
    con.commit()
    con.close()
    before = db.read_bytes()
    s = survey_index(db, "w", read_acl=fake_reader({root: acl(SALES)}), account_name=NAMES.get, workers=1)
    assert s.docs == 1 and len(s.rule_acl) == 1
    assert db.read_bytes() == before


@pytest.mark.skipif(sys.platform != "win32", reason="Windows permissions")
def test_real_windows_permissions_of_a_temp_folder(tmp_path):
    from dmx_docs.aclsurvey import read_windows_acl, windows_account_name

    (tmp_path / "a.txt").write_text("x")
    a = read_windows_acl(str(tmp_path))
    assert a.allow  # at least the current user or SYSTEM can read it
    assert all(windows_account_name(sid) for sid in a.allow)
    f = read_windows_acl(str(tmp_path / "a.txt"), for_files=False)
    assert f.key == a.key  # a new file gets the readers its folder hands down

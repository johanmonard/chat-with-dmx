import sys

import pytest

from dmx_docs.access import (KEEP_ALWAYS, Access, AccessStore, Caller, Unrestricted, may_read,
                             usable_sids)

A, B, C = "S-1-5-21-1-2-3-1101", "S-1-5-21-1-2-3-1102", "S-1-5-21-1-2-3-1103"


def test_usable_sids_drops_machine_relative_and_foreign_local_sids():
    got = usable_sids({"S-1-3-0", "S-1-3-4", "S-1-5-32-544", "S-1-5-18", "S-1-5-19", "S-1-5-20",
                       "S-1-5-21-9-9-9-500", "S-1-5-21-1-2-3-1105", "S-1-1-0", "S-1-5-11"},
                      "S-1-5-21-1-2-3")
    assert got == {"S-1-5-21-1-2-3-1105", "S-1-1-0", "S-1-5-11"}


def test_deny_wins_and_one_allowed_group_is_enough():
    assert may_read(frozenset({A, B}), frozenset({A}), frozenset())
    assert not may_read(frozenset({A, B}), frozenset({A}), frozenset({B}))
    assert not may_read(frozenset({C}), frozenset({A}), frozenset())


def _write(path, version, rule_of_acme, folders=None):
    AccessStore.write(
        path,
        rules={"h1": 1, "h2": 2},
        folder_rules=folders or {"d:\\p\\acme": rule_of_acme, "d:\\p\\thor": 2, "d:\\p\\broken": None},
        rule_sets={1: (frozenset({A}), frozenset()), 2: (frozenset({B}), frozenset({C}))},
        errors={"d:\\p\\broken": "PermissionError: denied"},
        names={A: "ACME\\staff"},
        version=version,
    )


def test_store_round_trip(tmp_path):
    p = tmp_path / "access.sqlite3"
    assert AccessStore(p).load() is None
    _write(p, "v1", 1)
    assert not (tmp_path / "access.sqlite3.new").exists()
    data = AccessStore(p).load()
    assert data.version == "v1"
    assert data.folder_rule == {"d:\\p\\acme": 1, "d:\\p\\thor": 2}
    assert data.rules[2] == (frozenset({B}), frozenset({C}))


def test_access_hides_folders_without_rule_except_for_admins(tmp_path):
    p = tmp_path / "access.sqlite3"
    _write(p, "v1", 1)
    user = Access(AccessStore(p), Caller(frozenset({A})))
    assert user.can_read("d:\\p\\acme\\a.pdf")
    assert not user.can_read("d:\\p\\thor\\a.pdf")       # rule 2 needs B
    assert not user.can_read("d:\\p\\broken\\a.pdf")     # no rule: closed
    assert not user.can_read("d:\\p\\unknown\\a.pdf")
    assert user.allowed_rules() == frozenset({1})
    assert user.version == "v1"
    admin = Access(AccessStore(p), Caller(frozenset(), admin=True))
    assert admin.can_read("d:\\p\\broken\\a.pdf")
    # a world with access = "acl" but no file yet: nothing for a user
    missing = Access(AccessStore(tmp_path / "none.sqlite3"), Caller(frozenset({A})))
    assert not missing.can_read("d:\\p\\acme\\a.pdf")
    assert missing.version == ""
    assert missing.allowed_rules() == frozenset()


def test_reload_keeps_previous_rules_when_the_file_is_briefly_missing(tmp_path):
    p = tmp_path / "access.sqlite3"
    now = [1000.0]
    _write(p, "v1", 1)
    acc = Access(AccessStore(p), Caller(frozenset({A})), clock=lambda: now[0])
    assert acc.can_read("d:\\p\\acme\\a.pdf")
    p.unlink()
    now[0] += 61
    assert acc.can_read("d:\\p\\acme\\a.pdf")            # old data kept
    _write(p, "v2", 2)                                    # acme now needs B
    now[0] += 61
    assert not acc.can_read("d:\\p\\acme\\a.pdf")
    assert acc.version == "v2"
    _write(p, "v3", 1)
    now[0] += 10
    assert not acc.can_read("d:\\p\\acme\\a.pdf")        # still v2: checked less than 60 s ago
    now[0] += 51
    assert acc.can_read("d:\\p\\acme\\a.pdf")


def test_unrestricted_reads_everything():
    u = Unrestricted()
    assert u.can_read("anything") and u.allowed_rules() == frozenset() and u.version == "world"


def test_from_groups_rejects_unknown_name():
    if sys.platform != "win32":
        pytest.skip("windows only")
    with pytest.raises(ValueError, match="NoSuchGroupXyz"):
        Caller.from_groups(["NoSuchGroupXyz"])


@pytest.mark.skipif(sys.platform != "win32", reason="windows only")
def test_from_token_has_the_user_and_everyone():
    c = Caller.from_token([])
    assert KEEP_ALWAYS <= c.sids
    assert any(s.startswith("S-1-5-21-") for s in c.sids)


def test_reload_with_renumbered_rule_ids_keeps_one_consistent_snapshot(tmp_path):
    p = tmp_path / "access.sqlite3"
    now = [1000.0]
    _write(p, "v1", 1)                      # acme -> rule 1 (A), thor -> rule 2 (B)
    acc = Access(AccessStore(p), Caller(frozenset({A})), clock=lambda: now[0])
    assert acc.can_read("d:\\p\\acme\\a.pdf") and acc.allowed_rules() == frozenset({1})
    snap1 = acc._snapshot()
    AccessStore.write(                      # ids renumbered: A's rule is now 2
        p, rules={}, folder_rules={"d:\\p\\acme": 2, "d:\\p\\thor": 1},
        rule_sets={1: (frozenset({B}), frozenset()), 2: (frozenset({A}), frozenset())},
        errors={}, names={}, version="v2")
    now[0] += 61
    snap2 = acc._snapshot()
    assert snap2 is not snap1 and snap2.data.version == "v2"
    assert snap2.allowed == frozenset({2}) == acc.allowed_rules()
    assert acc.can_read("d:\\p\\acme\\a.pdf") and not acc.can_read("d:\\p\\thor\\a.pdf")
    assert acc._snapshot() is snap2         # no reload inside the 60 s window

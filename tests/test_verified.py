"""Verified offer records: the record files, their loading and settings (fictional offers throughout)."""

import re
from pathlib import Path
from dataclasses import replace

import pytest

from dmx_docs import store, verified
from dmx_docs.config import Config, load_config

PDF = r"\\srv\Clients\ACME\3009001\3009001_02.pdf"


@pytest.fixture
def con(tmp_path):
    con = store.connect(tmp_path / "index.sqlite3")
    store.upsert_doc(con, path=PDF, size=1000, mtime=1700000000.0, status="ok", error=None, n_pages=5, title=None,
                     indexed_at=0.0, run_id=1, chunks=[])
    con.commit()
    yield con
    con.close()


def cfg_for(tmp_path, shared=None, mirror=None):
    return Config(roots=[r"\\srv\Clients"], data_dir=tmp_path, world="offers", profile="offers",
                  verified_dir=str(shared) if shared else None, verified_mirror=str(mirror) if mirror else None)


def record(**kw) -> verified.Record:
    base = dict(offer="3009001", revision="02", variant=None, path=PDF, size=1000, mtime=1700000000.0, pages=[4],
                not_price_pages={}, lines=[{"text": "Paloma 3R cell", "kind": "machine", "model": "Paloma 3R",
                                            "quantity": 1, "amounts": {"CHF": 125000.0}, "page": 4}],
                not_prices=[], totals={"CHF": 125000.0}, facts={"date": "2026-01-05", "country": "Switzerland",
                                                                 "offer_type": "firm"},
                checked_on="image", checked_by="claude-test", checked_at="2026-10-26T10:00:00", user="jdoe",
                note="", state="verified", file="")
    base.update(kw)
    return verified.Record(**base)


def test_write_then_load_roundtrip(tmp_path, con):
    shared = tmp_path / "shared"
    name = verified.write(str(shared), record())
    assert re.fullmatch(r"[0-9a-f]{16}\.json", name)
    assert not list(shared.glob("*.tmp"))
    loaded = verified.load(cfg_for(tmp_path, shared), con)
    assert loaded.source == "shared" and loaded.note is None
    got = loaded.records[verified.identity("3009001", "02", None)]
    assert got.lines[0]["amounts"] == {"CHF": 125000.0} and got.file == name


def test_latest_record_wins(tmp_path, con):
    shared = tmp_path / "shared"
    verified.write(str(shared), record(checked_at="2026-10-26T10:00:00", note="first"))
    verified.write(str(shared), record(checked_at="2026-10-26T10:00:05", note="second"))
    loaded = verified.load(cfg_for(tmp_path, shared), con)
    assert loaded.records[verified.identity("3009001", "02", None)].note == "second"
    assert len(list(shared.glob("*.json"))) == 2


def test_revoked_record_unverifies(tmp_path, con):
    shared = tmp_path / "shared"
    verified.write(str(shared), record())
    verified.write(str(shared), record(checked_at="2026-10-26T11:00:00", state="revoked", note="wrong"))
    loaded = verified.load(cfg_for(tmp_path, shared), con)
    assert verified.identity("3009001", "02", None) not in loaded.records


def test_expired_when_fingerprint_changes(tmp_path, con):
    shared = tmp_path / "shared"
    verified.write(str(shared), record())
    con.execute("UPDATE docs SET size = 1001")
    con.commit()
    loaded = verified.load(cfg_for(tmp_path, shared), con)
    assert not loaded.records and [r.offer for r in loaded.expired] == ["3009001"]


def test_load_skips_damaged_files(tmp_path, con):
    shared = tmp_path / "shared"
    verified.write(str(shared), record())
    (shared / "x.json").write_text("{not json", encoding="utf-8")
    (shared / "y.json.tmp").write_text("{}", encoding="utf-8")
    loaded = verified.load(cfg_for(tmp_path, shared), con)
    assert loaded.damaged == ["x.json"] and len(loaded.records) == 1


def test_load_falls_back_to_mirror(tmp_path, con):
    mirror = tmp_path / "mirror"
    verified.write(str(mirror), record())
    shared = tmp_path / "gone"
    loaded = verified.load(cfg_for(tmp_path, shared, mirror), con)
    assert loaded.source == "mirror" and "shared folder" in loaded.note and len(loaded.records) == 1


def test_load_without_configuration(tmp_path, con):
    loaded = verified.load(cfg_for(tmp_path), con)
    assert loaded.source == "none" and not loaded.records


def test_identity_normalises_variant():
    assert verified.identity("3009001 ", "02", "-") == verified.identity("3009001", "02", None) \
        == ("3009001", "02", "")


def test_config_reads_verified_settings(tmp_path):
    p = tmp_path / "config.toml"
    p.write_text("[index]\ndata_dir = 'data'\n[worlds.offers]\nprofile = \"offers\"\n"
                 "verified = '\\\\srv\\share\\worlds\\offers\\verified'\nverify_per_request = 10\n", encoding="utf-8")
    cfg = load_config(p, "offers")
    assert cfg.verified_dir == r"\\srv\share\worlds\offers\verified" and cfg.verify_per_request == 10
    assert cfg.verified_mirror == str(tmp_path / "data" / "offers" / "verified")
    p.write_text("[index]\ndata_dir = 'data'\n[worlds.offers]\nprofile = \"offers\"\n", encoding="utf-8")
    cfg = load_config(p, "offers")
    assert cfg.verified_dir is None and cfg.verify_per_request == 15


# --- The server's check ------------------------------------------------------------------------------

from dmx_docs import offers  # noqa: E402

PAGE4 = ("ACME packaging line\nPaloma 3R pick-and-place cell 1 CHF 125'000.00\n"
         "Installation and commissioning CHF 18'500.-\nTotal CHF 143'500.00")
OFFER_FOLDER = r"\\srv\Clients\ACME\3009001"


def register(con, text=PAGE4, page=4, amounts=({"CHF": 125000.0}, {"CHF": 18500.0}), rev_pages="4"):
    """The fictional document of the module's fixture, with its text and a register revision."""
    doc_id = con.execute("SELECT id FROM docs").fetchone()["id"]
    con.execute("DELETE FROM chunks WHERE doc_id = ?", (doc_id,))
    con.execute("INSERT INTO chunks(doc_id, page_no, seq, text) VALUES(?,?,?,?)", (doc_id, page, 0, text))
    for stmt in offers.TABLES.split(";"):
        if stmt.strip():
            con.execute(stmt)
    con.execute("INSERT INTO offers(offer_key, folder) VALUES('3009001', ?)", (OFFER_FOLDER,))
    con.execute("INSERT INTO offer_revisions(id, offer_key, revision, variant, rev_order, doc_id, path, pages) "
                "VALUES(1, '3009001', '02', NULL, 2, ?, ?, ?)", (doc_id, PDF, rev_pages))
    for pos, a in enumerate(amounts, 1):
        con.execute("INSERT INTO offer_lines(revision_id, position, text, kind, amounts, page) "
                    "VALUES(1, ?, 'x', 'machine', ?, ?)", (pos, __import__("json").dumps(a), page))
    con.commit()


def sub(**kw) -> verified.Submission:
    base = dict(offer="3009001", revision="02", path=PDF, pages=[4], totals={"CHF": 143500.0}, checked_on="image",
                checked_by="claude-test",
                lines=[{"text": "Paloma 3R cell", "kind": "machine", "model": "Paloma 3R", "quantity": 1,
                        "amounts": {"CHF": 125000.0}, "page": 4},
                       {"text": "Installation", "kind": "service", "quantity": None,
                        "amounts": {"CHF": 18500.0}, "page": 4}])
    base.update(kw)
    return verified.Submission(**base)


def test_check_accepts_printed_values(tmp_path, con):
    register(con)
    v = verified.check(con, cfg_for(tmp_path), sub())
    assert v.ok, v.problems
    assert v.record.checked_on == "image" and "add up" in v.record.note
    assert v.record.size == 1000 and v.record.path == PDF


@pytest.mark.parametrize("printed", ["1'234.50", "1.234,50", "1 234.50", "1234.50"])
def test_grounding_number_formats(tmp_path, con, printed):
    register(con, text=f"Cell CHF {printed}", amounts=({"CHF": 1234.5},))
    s = sub(lines=[{"text": "Cell", "kind": "machine", "amounts": {"CHF": 1234.5}, "page": 4}], totals={})
    assert verified.check(con, cfg_for(tmp_path), s).ok


def test_grounding_number_formats_dash_and_wrong(tmp_path, con):
    register(con, text="Cell CHF 1234.-", amounts=({"CHF": 1234.0},))
    ok = sub(lines=[{"text": "Cell", "kind": "machine", "amounts": {"CHF": 1234.0}, "page": 4}], totals={})
    assert verified.check(con, cfg_for(tmp_path), ok).ok
    register_text = "UPDATE chunks SET text = ?"
    con.execute(register_text, ("Cell CHF 1'243.50",))
    con.commit()
    bad = sub(lines=[{"text": "Cell", "kind": "machine", "amounts": {"CHF": 1234.5}, "page": 4}], totals={})
    v = verified.check(con, cfg_for(tmp_path), bad)
    assert not v.ok and any("page 4" in p and "1'234.50" in p for p in v.problems)


def test_check_refuses_missing_register_amount(tmp_path, con):
    register(con)
    only_cell = sub(lines=sub().lines[:1], totals={})
    v = verified.check(con, cfg_for(tmp_path), only_cell)
    assert not v.ok and any("page 4" in p and "CHF 18'500" in p for p in v.problems)
    listed = sub(lines=sub().lines[:1], totals={},
                 not_prices=[{"page": 4, "value": 18500, "reason": "installation listed apart"}])
    assert verified.check(con, cfg_for(tmp_path), listed).ok


def test_check_refuses_uncovered_price_page(tmp_path, con):
    register(con)
    other = sub(pages=[5], lines=[dict(l, page=5) for l in sub().lines])
    v = verified.check(con, cfg_for(tmp_path), other)
    assert not v.ok and any("page 4" in p for p in v.problems)
    other.not_price_pages = {4: "general conditions"}
    v = verified.check(con, cfg_for(tmp_path), other)
    assert not any("page 4 is a price page" in p for p in v.problems)


def test_check_image_only_page(tmp_path, con):
    register(con, text="Scanned picture page")
    s = sub(totals={})
    v = verified.check(con, cfg_for(tmp_path), s)
    assert v.ok, v.problems
    assert v.record.checked_on == "image only"


def test_check_refuses_unknown_revision_and_excluded_path(tmp_path, con):
    register(con)
    assert not verified.check(con, cfg_for(tmp_path), sub(revision="09")).ok
    cfg = cfg_for(tmp_path)
    cfg.set_sources(cfg.roots, [r"\\srv\Clients\ACME"], cfg.exclude)
    v = verified.check(con, cfg, sub())
    assert not v.ok and "indexed folders" in v.problems[0]
    outside = verified.check(con, cfg_for(tmp_path), sub(path=r"\\srv\Elsewhere\x.pdf"))
    assert not outside.ok


def test_check_caps_and_cleaning(tmp_path, con):
    register(con)
    v = verified.check(con, cfg_for(tmp_path), sub(lines=[sub().lines[0]] * 201))
    assert not v.ok and "201 lines" in v.problems[0]
    s = sub()
    s.lines[0]["text"] = "=HYPERLINK(1)"
    v = verified.check(con, cfg_for(tmp_path), s)
    assert v.ok and v.record.lines[0]["text"] == "'=HYPERLINK(1)"
    s.lines[0]["text"] = "x" * 400
    assert len(verified.check(con, cfg_for(tmp_path), s).record.lines[0]["text"]) == 300


def test_sum_mismatch_is_noted_not_refused(tmp_path, con):
    register(con, text=PAGE4.replace("143'500.00", "150'000.00"))
    v = verified.check(con, cfg_for(tmp_path), sub(totals={"CHF": 150000.0}))
    assert v.ok and "lines CHF 143'500" in v.record.note and "printed total CHF 150'000" in v.record.note


def test_dispute_makes_a_disputed_record(tmp_path, con):
    register(con)
    rec = verified.dispute(con, cfg_for(tmp_path), sub(), "the page shows other amounts")
    assert rec.state == "disputed" and rec.note == "the page shows other amounts"
    with pytest.raises(ValueError):
        verified.dispute(con, cfg_for(tmp_path), sub(revision="09"), "x")


def test_check_hardening(tmp_path, con):
    register(con)
    cfg = cfg_for(tmp_path)
    # a page outside the document, a not-price page standing for every price page, odd currency/quantity
    assert not verified.check(con, cfg, sub(pages=[4, 99])).ok
    v = verified.check(con, cfg, sub(pages=[5], lines=[dict(l, page=5) for l in sub().lines],
                                     not_price_pages={4: "general conditions"}))
    assert not v.ok and any("cannot stand for all" in p for p in v.problems)
    bad = sub()
    bad.lines[0]["amounts"] = {"=cmd": 125000.0}
    assert not verified.check(con, cfg, bad).ok
    bad = sub()
    bad.lines[0]["quantity"] = "=1+1"
    assert not verified.check(con, cfg, bad).ok
    # the time of a record is the server's unless a past time is given
    future = verified.check(con, cfg, sub(checked_at="2999-01-01T00:00:00"))
    assert future.record.checked_at < "2999"
    past = verified.check(con, cfg, sub(checked_at="2026-01-02T03:04:05"))
    assert past.record.checked_at == "2026-01-02T03:04:05"


def test_load_rejects_badly_typed_records(tmp_path, con):
    shared = tmp_path / "shared"
    verified.write(str(shared), record())
    for i, bad in enumerate([record(pages=["4"]), record(totals={"CHF": "x"}),
                             record(lines=[{"text": "a", "amounts": {"CHF": "12"}, "page": 4}]),
                             record(facts={"date": 5}), record(note=7)]):
        bad.offer = f"30090{i}"
        name = verified.write(str(shared), bad)
    loaded = verified.load(cfg_for(tmp_path, shared), con)
    assert len(loaded.damaged) == 5 and len(loaded.records) == 1


def test_load_expires_records_of_excluded_folders(tmp_path, con):
    shared = tmp_path / "shared"
    verified.write(str(shared), record())
    cfg = cfg_for(tmp_path, shared)
    cfg.set_sources(cfg.roots, [r"\\srv\Clients\ACME"], cfg.exclude)
    loaded = verified.load(cfg, con)
    assert not loaded.records and len(loaded.expired) == 1


# --- Seed from the spot checks ------------------------------------------------------------------------

import json  # noqa: E402

OK, NO = "\u2713", "\u2717"


def batch_entry(offer, lines_amount=125000.0, revision="02", **kw):
    entry = {"Offer": offer, "Client": "ACME", "Country": "Switzerland", "Category": "client", "Offer type": "firm",
             "Last revision": revision, "Date (source)": "2026-01-05 (document)", "Status (project)": "unknown",
             "Printed totals": "CHF 143'500", "Lines in register": 2, "Price document": PDF, "Pages": "4",
             "lines": [{"Offer": offer, "Revision": "02", "Pos": 1, "Line text (register)": "Paloma 3R cell",
                        "Kind": "machine", "Model": "Paloma 3R", "Qty": 1, "CHF": lines_amount, "EUR": None,
                        "USD": None, "GBP": None, "Page": 4},
                       {"Offer": offer, "Revision": "02", "Pos": 2, "Line text (register)": "Installation",
                        "Kind": "service", "Model": None, "Qty": None, "CHF": 18500.0, "EUR": None, "USD": None,
                        "GBP": None, "Page": 4}]}
    entry.update(kw)
    return entry


def verdict_entry(offer, lines=OK, amounts=OK, facts=OK, checked_on="page image"):
    return {"Offer": offer, "lines_ok": lines, "amounts_ok": amounts, "facts_ok": facts, "status_ok": OK,
            "comment": "", "checked_on": checked_on, "line_checks": [], "missing_items": [], "error_causes": []}


def spot_folder(path, entries, verdicts, n=1):
    path.mkdir(parents=True, exist_ok=True)
    (path / f"batch{n}.json").write_text(json.dumps(entries), encoding="utf-8")
    (path / f"result{n}.json").write_text(json.dumps(verdicts), encoding="utf-8")
    return str(path)


def seed_register(con):
    register(con)
    doc_id = con.execute("SELECT id FROM docs").fetchone()["id"]
    for key, rid in (("3009002", 2), ("3009003", 3)):
        con.execute("INSERT INTO offers(offer_key, folder) VALUES(?, ?)", (key, OFFER_FOLDER))
        con.execute("INSERT INTO offer_revisions(id, offer_key, revision, rev_order, doc_id, path, pages) "
                    "VALUES(?, ?, '02', 2, ?, ?, '4')", (rid, key, doc_id, PDF))
        for pos, amount in enumerate((125000.0, 18500.0), 1):
            con.execute("INSERT INTO offer_lines(revision_id, position, text, kind, amounts, page) VALUES(?,?,'x','machine',?,4)",
                        (rid, pos, json.dumps({"CHF": amount})))
    con.commit()


def test_parse_revision():
    assert verified._parse_revision("02 / A (2 variants)") == ("02", "A")
    assert verified._parse_revision("-") == ("-", None)
    assert verified._parse_revision("03 (2 variants)") == ("03", None)
    assert verified._parse_revision("V1") == ("V1", None)
    assert verified._parse_revision("03 / Vandelay version") == ("03", "Vandelay version")


def test_seed_takes_fully_right_offers(tmp_path, con):
    seed_register(con)
    cfg = cfg_for(tmp_path, tmp_path / "shared")
    folder = spot_folder(tmp_path / "s1",
                         [batch_entry("3009001"), batch_entry("3009002"), batch_entry("3009003", lines_amount=99999.0)],
                         [verdict_entry("3009001", checked_on="extracted text"), verdict_entry("3009002", amounts=NO),
                          verdict_entry("3009003")])
    out = verified.seed_from_spot_checks(con, cfg, [folder])
    assert "3009001: seeded" in out and any(l.startswith("3009002: skipped (amounts ") for l in out)
    assert any(l.startswith("3009003: refused: ") and "99'999" in l for l in out)
    got = verified.load(cfg, con).records[verified.identity("3009001", "02", None)]
    assert got.checked_by == "spot check" and got.checked_on == "text" and got.facts["date"] == "2026-01-05"
    assert got.totals == {"CHF": 143500.0} and got.lines[0]["model"] == "Paloma 3R"
    assert verified.seed_from_spot_checks(con, cfg, [folder])[0].endswith("already seeded")


def test_seed_later_folder_wins(tmp_path, con):
    seed_register(con)
    cfg = cfg_for(tmp_path, tmp_path / "shared")
    first = spot_folder(tmp_path / "s1", [batch_entry("3009001")], [verdict_entry("3009001", lines=NO)])
    second = spot_folder(tmp_path / "s2", [batch_entry("3009001")], [verdict_entry("3009001")])
    assert "3009001: skipped (lines \u2717)" in verified.seed_from_spot_checks(con, cfg, [first])
    assert "3009001: seeded" in verified.seed_from_spot_checks(con, cfg, [first, second])


def test_seed_dry_run_writes_nothing(tmp_path, con):
    seed_register(con)
    cfg = cfg_for(tmp_path, tmp_path / "shared")
    folder = spot_folder(tmp_path / "s1", [batch_entry("3009001")], [verdict_entry("3009001")])
    out = verified.seed_from_spot_checks(con, cfg, [folder], dry_run=True)
    assert "3009001: seeded (dry run)" in out and not (tmp_path / "shared").exists()


def test_review_fixes(tmp_path, con):
    register(con)
    cfg = cfg_for(tmp_path)
    # a string or boolean quantity is refused; a printed one is stored as a number
    for bad in ("2", True):
        s = sub()
        s.lines[0]["quantity"] = bad
        assert bool(bad) and (not verified.check(con, cfg, s).ok or bad == "2")
    s = sub()
    s.lines[0]["quantity"] = "1"
    v = verified.check(con, cfg, s)
    assert v.ok and v.record.lines[0]["quantity"] == 1 and type(v.record.lines[0]["quantity"]) is int
    # a hand-made record with a list as not_price_pages is damaged, not a crash
    shared = tmp_path / "shared"
    verified.write(str(shared), record())
    (shared / "z.json").write_text(json.dumps({**__import__("dataclasses").asdict(record(offer="3009009")),
                                               "not_price_pages": ["5"]}), encoding="utf-8")
    loaded = verified.load(cfg_for(tmp_path, shared), con)
    assert loaded.damaged == ["z.json"] and len(loaded.records) == 1


def test_read_error_means_unreachable_not_damaged(tmp_path, con, monkeypatch):
    shared, mirror = tmp_path / "shared", tmp_path / "mirror"
    verified.write(str(shared), record())
    verified.write(str(mirror), record())
    real = Path.read_text

    def flaky(self, *a, **k):
        if "shared" in str(self):
            raise OSError("share dropped")
        return real(self, *a, **k)

    monkeypatch.setattr(Path, "read_text", flaky)
    loaded = verified.load(cfg_for(tmp_path, shared, mirror), con)
    assert loaded.source == "mirror" and not loaded.damaged and len(loaded.records) == 1

import asyncio
from pathlib import Path

import pytest

from dmx_docs import offer_tools, offers, server, sources
from dmx_docs.tools import DocTools
from offers_corpus import build_offers_corpus, indexed, make_projects_db, world
from offers_corpus import offers_cfg as make_offers_cfg

ROW = ("date", "Coverage", "Left out", "Not checked")


@pytest.fixture(scope="session")
def _tools_index(tmp_path_factory):
    """The offers tree of test_offers.py indexed, once per session."""
    return indexed(tmp_path_factory, "tools", build_offers_corpus)


@pytest.fixture(scope="session")
def _thor_db(tmp_path_factory):
    """A projects world whose project THOR (a Paloma, as the offer's lines) quotes offer 3001234."""
    return make_projects_db(tmp_path_factory.mktemp("projects"),
                            {("THOR", r"THOR\0_Vente\00_Offres\Offre Paloma 2R.pdf"): "Notre offre 3001234 du 14.05.2020"})


@pytest.fixture
def offers_world_linked(_tools_index, _thor_db, tmp_path):
    """The register built and linked to the projects world: 3001234 won by THOR."""
    cfg, con = world(_tools_index, tmp_path, projects_db=_thor_db)
    yield cfg, con
    con.close()


@pytest.fixture
def offers_cfg(tmp_path):
    return make_offers_cfg(tmp_path / "Clients", tmp_path / "data", tmp_path / "knowledge")


def rows(out, offer=None):
    return [r for r in out.splitlines() if r and not r.startswith(ROW) and (offer is None or f";{offer};" in r)]


def _client(srv):
    try:
        from mcp import Client
    except ImportError:
        pytest.skip("in-process client requires mcp >= 2")
    return Client(srv)


def tool_names(srv):
    async def run():
        async with _client(srv) as client:
            return {t.name for t in (await client.list_tools()).tools}
    return asyncio.run(run())


# --- offer_lines -----------------------------------------------------------------------------------

def test_offer_lines_chart_rows(offers_world_linked):
    _, con = offers_world_linked
    out = offer_tools.offer_lines(con, machine="Paloma")
    assert all(r.split(";")[12] == "CHF" for r in rows(out)) and len(rows(out)) == 6
    assert out.splitlines()[0] == ("date;offer;revision;variant;kind;client;country;status;offer_type;quantity;amount;"
                                   "unit_price;currency;line;path;page;confidence;verified")
    assert "Coverage: 5 offers matched; 5 with readable prices; left out: 1 (1 no readable prices)" in out
    assert rows(out, "B-250306")[0].split(";")[8] == "budget"  # budget offers are in, and marked
    assert "3002222/00 annex (no readable prices)" in out  # an unread document of the last revision
    won = rows(out, "3001234")[0].split(";")
    assert won[:11] == ["2020-05-14", "3001234", "03", "Vandelay", "machine", "ACME Foods", "", "won", "firm", "",
                        "180000"]
    assert won[13].startswith("A Paloma 2R Top Loader") and won[14].endswith("Vandelay.pdf") and won[15:] == ["2", "high", "no"]
    firm = offer_tools.offer_lines(con, machine="Paloma", offer_type="firm")
    assert len(rows(firm)) == 5 and "B-250306 (budget)" in firm


def test_last_revision_default_and_all(offers_world_linked):
    _, con = offers_world_linked
    # 3001234's rows only: 3002222 (also ACME) has a single revision 00
    last = "\n".join(rows(offer_tools.offer_lines(con, client="ACME"), "3001234"))
    every = "\n".join(rows(offer_tools.offer_lines(con, client="ACME", revisions="all"), "3001234"))
    assert ";03;Vandelay;" in last and ";03;Wonka;" in last and ";02;" not in last and ";00;" not in last
    assert ";00;" in every and ";02;Vandelay version;" in every
    assert "_DE.pdf" not in every  # a translation has no lines of its own


def test_unit_price_quantity_and_kinds(offers_world_linked):
    _, con = offers_world_linked
    out = offer_tools.offer_lines(con, machine="paloma 3r")
    row = rows(out, "B-250306")[0].split(";")
    assert (row[0], row[6], row[9], row[10], row[11], row[12]) == ("2006-03-25", "Germany", "2", "410000", "205000", "CHF")
    discounts = rows(offer_tools.offer_lines(con, kind="discount", client="acme"))
    assert discounts and all(r.split(";")[10].startswith("-") and r.split(";")[11] == "" for r in discounts)


def test_currency_never_converted(offers_world_linked):
    _, con = offers_world_linked
    out = offer_tools.offer_lines(con, currency="EUR")
    assert "B-250306" in out and "no EUR amount" in out  # the EN offer has CHF only: left out, named
    assert {r.split(";")[1] for r in rows(out)} == {"B-250306"}
    assert ";2;400000;200000;EUR;" in rows(out, "B-250306")[0]
    assert "Left out: " in out and "3001234 (no EUR amount)" in out


def test_offer_lines_left_out_reasons(offers_world_linked):
    _, con = offers_world_linked
    out = offer_tools.offer_lines(con, client="acme", status="no_order_found,open")
    assert "3003333 (no readable prices)" in out and "0904023 (no readable prices)" in out
    out = offer_tools.offer_lines(con, year_from=2019, year_to=2019)
    assert {r.split(";")[1] for r in rows(out)} == {"3002222"} and "Coverage: 1 offers matched" in out
    out = offer_tools.offer_lines(con, country="germany", category="client")
    assert "B-250306 (category archive)" in out
    assert "B-250306 (budget)" in offer_tools.offer_lines(con, country="Germany", offer_type="firm")


def test_ignored_revisions(offers_world_linked, tmp_path):
    _, con = offers_world_linked
    p = tmp_path / "corrections.csv"
    p.write_text("offer;revision;line;field;value;note;who;date\n"
                 "3001234;03/Vandelay;-;ignore;yes;;JM;2026-10-16\n3001234;03/Wonka;-;ignore;yes;;JM;2026-10-16\n"
                 "B-250306;V1;-;ignore;yes;;JM;2026-10-16\n", encoding="utf-8")
    assert offers.apply_corrections(con, str(p)) == []
    last = offer_tools.offer_lines(con, machine="Paloma", offer_type="firm,budget")
    assert ";02;Vandelay version;" in "\n".join(rows(last, "3001234")) and ";03;" not in last  # the revision before
    assert "B-250306 (ignored by correction)" in last
    assert "3001234/03 Vandelay (ignored by correction), 3001234/03 Wonka (ignored by correction)" in last
    card = offer_tools.offer_card(con, "3001234")
    assert "ignored by correction" in card and "03/Vandelay ignore = yes (JM, 2026-10-16)" in card


def _wonka(con, sql):
    con.execute(sql + " (SELECT id FROM offer_revisions WHERE offer_key = '3001234' AND variant = 'Wonka')")


def test_parts_left_out_are_named(offers_world_linked):
    _, con = offers_world_linked
    _wonka(con, "UPDATE offer_lines SET amounts = '{\"EUR\": 150000.0}' WHERE position = 1 AND revision_id =")
    out = offer_tools.offer_lines(con, client="acme")
    assert "3001234/03 Wonka line 1 (no CHF amount)" in out and len(rows(out, "3001234")) == 7
    assert "1 no CHF amount" in out.splitlines()[-2]  # counted in the coverage line's reasons
    _wonka(con, "UPDATE offer_lines SET amounts = '{\"EUR\": 1.0}' WHERE revision_id =")
    out = offer_tools.offer_lines(con, client="acme")
    assert "3001234/03 Wonka (no CHF amount)" in out
    assert {r.split(";")[3] for r in rows(out, "3001234")} == {"Vandelay"}
    _wonka(con, "UPDATE offer_revisions SET date = NULL WHERE id =")
    out = offer_tools.offer_lines(con, client="acme", year_from=2020)
    assert "3001234/03 Wonka (no date)" in out and len(rows(out, "3001234")) == 4


def test_offer_lines_unread_last_revision_and_no_revision(offers_world_linked):
    _, con = offers_world_linked
    con.execute("DELETE FROM offer_lines WHERE revision_id IN "
                "(SELECT id FROM offer_revisions WHERE offer_key = '3001234' AND revision = '03')")
    con.execute("DELETE FROM offer_revisions WHERE offer_key = '0904023'")
    out = offer_tools.offer_lines(con, client="acme")
    assert "3001234 (last revision not read)" in out and "0904023 (no revision)" in out
    assert "0904023 (no revision)" in offer_tools.offer_lines(con, client="acme", year_from=2020)
    con.execute("UPDATE offers SET status = NULL, status_source = NULL WHERE offer_key = '3002222'")
    card = offer_tools.offer_card(con, "3002222")
    assert "Offer status unknown" in card and "None" not in card


def test_excluded_folders_are_hidden(offers_world_linked):
    cfg, con = offers_world_linked
    sources.set_excluded(con, str(Path(cfg.roots[0]) / "Globex"), True)  # unticked on the configuration page
    con.commit()
    tools = DocTools(cfg)
    assert "3006666" not in tools.list_offers() and "3006666" not in tools.offer_lines()
    assert tools.offer_card("3006666").startswith("No offer 3006666")
    assert "Globex" not in tools.offer_card("3001234") and "Globex" not in tools.offer_stats("client")
    assert "3006666" in offer_tools.list_offers(con)  # without the folder rules: the whole register


def test_table_cells_are_safe(offers_world_linked):
    _, con = offers_world_linked
    con.execute("UPDATE offer_lines SET text = '=cmd|x; type\nX' WHERE kind = 'machine' AND revision_id = "
                "(SELECT id FROM offer_revisions WHERE offer_key = '0904099')")
    row = rows(offer_tools.offer_lines(con, client="ACME", kind="machine"), "0904099")[0]
    assert ";'=cmd|x, type X;" in row and len(row.split(";")) == 18


def test_machine_charts_show_machine_lines_unless_a_kind_is_asked(offers_world_linked):
    _, con = offers_world_linked
    # A training line naming the machine ("Training for the Paloma 2R operators") carries its family and model.
    con.execute("UPDATE offer_lines SET family = 'Paloma', model = 'Paloma 2R' WHERE kind = 'service' AND revision_id "
                "IN (SELECT id FROM offer_revisions WHERE offer_key = '3002222')")
    out = offer_tools.offer_lines(con, machine="Paloma 2R")
    assert {r.split(";")[4] for r in rows(out)} == {"machine"} and "; kind machine (default with machine=)" in out
    service = offer_tools.offer_lines(con, machine="Paloma 2R", kind="service")
    assert {r.split(";")[4] for r in rows(service)} == {"service"} and "default with machine=" not in service
    assert {r.split(";")[4] for r in rows(offer_tools.offer_lines(con, machine="Paloma 2R", kind=""))} == {
        "machine", "service"}  # empty: every kind
    assert "default with machine=" not in offer_tools.offer_lines(con, client="acme")


def test_unreadable_offers_are_named_when_a_machine_is_asked(offers_world_linked):
    _, con = offers_world_linked
    clause = "3 offers in scope have no readable prices and could not be checked for this machine"
    for out in (offer_tools.offer_lines(con, machine="Paloma"), offer_tools.offer_stats(con, "year", machine="Paloma")):
        coverage = next(l for l in out.splitlines() if l.startswith("Coverage: "))
        assert clause in coverage, out
        named = next(l for l in out.splitlines() if l.startswith("Not checked for this machine: "))
        assert sorted(named.split(": ", 1)[1].split(", ")) == ["0904023", "3003333", "3004444"]
    assert "could not be checked" not in offer_tools.offer_lines(con, client="acme")  # no machine asked
    assert "could not be checked" not in offer_tools.offer_lines(con, machine="Paloma", client="Globex")


# --- offer_stats, offer_card, list_offers -------------------------------------------------------------

def test_offer_stats(offers_world_linked):
    _, con = offers_world_linked
    out = offer_tools.offer_stats(con, "year,country")
    assert out.splitlines()[0] == "year;country;offers;revisions" and any(l.startswith("2020;") for l in out.splitlines())
    with pytest.raises(ValueError):
        offer_tools.offer_stats(con, "colour")
    with pytest.raises(ValueError):
        offer_tools.offer_stats(con, "year,country,client")
    status = offer_tools.offer_stats(con, "status")
    assert "won;1;4" in status.splitlines() and "Coverage: 8 offers matched; 5 with readable prices" in status
    machine = offer_tools.offer_stats(con, "machine", client="acme")
    assert "Paloma 2R;3;6" in machine.splitlines() and "unknown;3;4" in machine.splitlines()
    assert "2006;Germany;1;1" in offer_tools.offer_stats(con, "year,country", offer_type="budget").splitlines()


def test_offer_card_and_list(offers_world_linked):
    _, con = offers_world_linked
    card = offer_tools.offer_card(con, "3001234")
    assert "Vandelay version" in card and "(p.2)" in card and "translation" in card.lower()
    assert "status won -> THOR" in card and "Final negotiated price" in card
    assert "1. A Paloma 2R Top Loader cell including: robots, frame, HMI · machine · Paloma 2R · CHF 180,000 (p.2)" in card
    assert "1 EUR = 1.2 CHF (November 1st, 2011)" in card and "Globex" in card  # rate, duplicate folder
    assert offer_tools.offer_card(con, "1234567").startswith("No offer 1234567")
    assert "Spec A.pdf" in offer_tools.offer_card(con, "3006666")  # the offer's other documents
    lst = offer_tools.list_offers(con, client="acme")
    assert any(l.startswith("3001234 · ACME Foods") for l in lst.splitlines()) and "Coverage:" in lst
    line = next(l for l in lst.splitlines() if l.startswith("3001234 · "))
    assert line.startswith("3001234 · ACME Foods · country ? · 2020-02-10..2020-05-14 · 4 revisions · Paloma 2R · "
                           "total CHF 200,000 (Vandelay) / CHF 180,000 (Wonka) · won -> THOR · ")
    assert line.endswith(r"\03\Offer - 3001234_03 Vandelay.pdf p.2 / " + str(con.execute(
        "SELECT path FROM offer_revisions WHERE variant = 'Wonka'").fetchone()[0]) + " p.2")  # the source
    assert "B-250306 · Dupont Biscuits · Germany" in offer_tools.list_offers(con, machine="Paloma 3R")
    assert "3001234" not in offer_tools.list_offers(con, year_to=2019)
    assert "Coverage: 0 offers matched" in offer_tools.list_offers(con, text="no such words")


def test_unconfirmed_won_is_shown_apart_from_won(offers_world_linked):
    _, con = offers_world_linked
    con.execute("UPDATE offers SET status = 'won_unconfirmed' WHERE offer_key = '3001234'")
    assert "Offer status won_unconfirmed -> THOR" in offer_tools.offer_card(con, "3001234")
    assert "Won unconfirmed" in offer_tools.offer_card(con, "3001234")
    assert {r.split(";")[7] for r in rows(offer_tools.offer_lines(con, client="acme"), "3001234")} == {"won_unconfirmed"}
    assert "won_unconfirmed;1;4" in offer_tools.offer_stats(con, "status").splitlines()
    assert "3001234" in offer_tools.list_offers(con, status="won_unconfirmed")
    assert "3001234" not in offer_tools.list_offers(con, status="won")


def test_tools_without_register(_tools_index, tmp_path):
    cfg, con = world(_tools_index, tmp_path, run=False)
    try:
        for out in (offer_tools.list_offers(con), offer_tools.offer_card(con, "3001234"), offer_tools.offer_lines(con),
                    offer_tools.offer_stats(con, "year")):
            assert out == ("The offer register is empty on this index: run the offers step (dmx-docs offers, "
                           "or launcher 7 for the offers world).")
    finally:
        con.close()
    tools = DocTools(cfg)
    assert tools.list_offers().startswith("The offer register is empty")
    assert "Offer - 3001234_00.pdf" in tools.search("Paloma", mode="keyword")  # plain search works
    assert "No results" in tools.search("Paloma", year_from=2020, mode="keyword")  # needs the register
    assert "Dupont B-250306 V1.pdf" in tools.search("Paloma", country="Germany", mode="keyword")


# --- server and search --------------------------------------------------------------------------------

def test_offers_server_tools(offers_cfg):
    names = tool_names(server.build_server(offers_cfg))
    assert {"list_offers", "offer_card", "offer_lines", "offer_stats", "search", "world_map"} <= names
    assert "list_projects" not in names and "catalog" not in names
    assert "offer_lines" in server.instructions_for(offers_cfg)


def test_offers_server_calls(offers_world_linked):
    cfg, _ = offers_world_linked

    async def run():
        async with _client(server.build_server(cfg)) as client:
            stats = await client.call_tool("offer_stats", {"group_by": "status"})
            hits = await client.call_tool("search", {"query": "Paloma", "status": "won", "mode": "keyword"})
            chart = await client.call_tool("offer_lines", {"machine": "Paloma"})
            return stats.content[0].text, hits.content[0].text, chart.content[0].text

    stats, hits, chart = asyncio.run(run())
    assert stats.startswith("status;offers;revisions") and "won;1;4" in stats
    assert ";B-250306;" in chart and ";budget;" in chart  # budget offers are in the charts by default
    assert "3001234" in hits and "3002222" not in hits and "Dupont" not in hits


def test_offers_search_filters(offers_world_linked):
    cfg, _ = offers_world_linked
    out = DocTools(cfg).search("Paloma", client="ACME Foods", year_from=2020)
    assert "ACME Foods" in out and "Dupont" not in out
    assert "offer 3001234 rev 00" in out  # facet line of an offers hit
    tools = DocTools(cfg)
    out = tools.search("Paloma", client="ACME Foods", year_from=2020, mode="keyword", limit=30)
    assert "offer 3001234 rev 03 (Vandelay)" in out and "3002222" not in out  # 2019
    assert "offer 3001234 rev 01 (translation)" in out
    assert "Dupont B-250306 V1.pdf" in tools.search("Paloma", country="germ", mode="keyword")
    out = tools.search("Paloma", client="acme, nobody", mode="keyword", limit=30)  # part of the name, comma = or
    assert "client ACME Foods" in out and "Dupont" not in out and "Globex" not in out
    out = tools.search("Paloma", offer="3001234", revision="02", mode="keyword")
    assert "3001234_02.pdf" in out and "3001234_00.pdf" not in out
    for written in ("3001234_02", "Offer - 3001234_00.pdf", "3001234-02"):  # as file and folder names write it
        assert "3001234_02.pdf" in tools.search("Paloma", offer=written, revision="02", mode="keyword"), written
    assert "Dupont B-250306 V1.pdf" in tools.search("Paloma", offer="B-250306 V1, 9999999", mode="keyword")
    assert "No results" in tools.search("Paloma", status="lost", mode="keyword")
    assert "No results" in tools.search("Paloma", partner="Initech", mode="keyword")


# --- verified records: the overlay ------------------------------------------------------------------

import json  # noqa: E402

from dmx_docs import verified  # noqa: E402

MACHINE = "A Paloma 2R Top Loader cell including: robots, frame, HMI"


def _record(con, offer, revision, variant=None, amount=21000.0, text=MACHINE, **kw):
    r = con.execute("SELECT r.*, d.size, d.mtime FROM offer_revisions r JOIN docs d ON d.id = r.doc_id "
                    "WHERE r.offer_key = ? AND r.revision = ? AND ifnull(r.variant, '') = ?",
                    (offer, revision, variant or "")).fetchone()
    base = dict(offer=offer, revision=revision, variant=variant, path=r["path"], size=r["size"], mtime=r["mtime"],
                pages=[2], not_price_pages={}, not_prices=[], totals={"CHF": amount},
                lines=[{"text": text, "kind": "machine", "model": "Paloma 2R", "quantity": None,
                        "amounts": {"CHF": amount}, "page": 2}],
                facts={"date": "2021-07-02", "country": "Italy", "offer_type": "budget"}, checked_on="image",
                checked_by="claude-test", checked_at="2026-10-26T10:00:00", user="jdoe", note="", state="verified",
                file="")
    base.update(kw)
    return verified.Record(**base)


def _loaded(cfg, con, tmp_path, *records):
    shared = tmp_path / "shared"
    shared.mkdir(exist_ok=True)
    for rec in records:
        verified.write(str(shared), rec)
    cfg.verified_dir = str(shared)
    return verified.load(cfg, con)


def _machine_rows(out, offer):
    return [r.split(";") for r in rows(out, offer)]


def test_offer_lines_uses_verified_values(offers_world_linked, tmp_path):
    cfg, con = offers_world_linked
    loaded = _loaded(cfg, con, tmp_path, _record(con, "3006666", "00"))
    out = offer_tools.offer_lines(con, machine="Paloma", loaded=loaded)
    row = _machine_rows(out, "3006666")[0]
    assert row[10] == "21000" and row[-1] == "image" and row[6] == "Italy" and row[8] == "budget"
    assert "1 verified, " in out and " not verified (0 expired, 0 disputed)" in out
    assert _machine_rows(out, "3002222")[0][-1] == "no"
    plain = offer_tools.offer_lines(con, machine="Paloma")
    assert _machine_rows(plain, "3006666")[0][10] == "180000" and "verified," not in plain


def test_correction_beats_verified(offers_world_linked, tmp_path):
    cfg, con = offers_world_linked
    con.execute("INSERT INTO offer_corrections(offer, revision, line, field, value, applied) "
                "VALUES('3006666', '00', 1, 'amount:CHF', '999', 1)")
    loaded = _loaded(cfg, con, tmp_path, _record(con, "3006666", "00"))
    out = offer_tools.offer_lines(con, machine="Paloma", loaded=loaded)
    assert _machine_rows(out, "3006666")[0][10] == "999"


def test_stale_line_correction_not_applied(offers_world_linked, tmp_path):
    cfg, con = offers_world_linked
    con.execute("INSERT INTO offer_corrections(offer, revision, line, field, value, applied) "
                "VALUES('3006666', '00', 1, 'amount:CHF', '999', 1)")
    loaded = _loaded(cfg, con, tmp_path, _record(con, "3006666", "00", text="Completely other machine Paloma 2R"))
    out = offer_tools.offer_lines(con, machine="Paloma", loaded=loaded)
    assert _machine_rows(out, "3006666")[0][10] == "21000"
    stale = verified.stale_corrections(con, loaded)
    assert [(s[0], s[1], s[2], s[3]) for s in stale] == [("3006666", "00", 1, "amount:CHF")]


def test_offer_level_correction_beats_verified_fact(offers_world_linked, tmp_path):
    cfg, con = offers_world_linked
    con.execute("INSERT INTO offer_corrections(offer, revision, line, field, value, applied) "
                "VALUES('3006666', NULL, NULL, 'country', 'France', 1)")
    con.execute("UPDATE offers SET country = 'France' WHERE offer_key = '3006666'")
    loaded = _loaded(cfg, con, tmp_path, _record(con, "3006666", "00"))
    row = _machine_rows(offer_tools.offer_lines(con, machine="Paloma", loaded=loaded), "3006666")[0]
    assert row[6] == "France" and row[8] == "budget"


def test_offer_card_marks_revisions(offers_world_linked, tmp_path):
    cfg, con = offers_world_linked
    loaded = _loaded(cfg, con, tmp_path, _record(con, "3006666", "00"),
                     _record(con, "3002222", "00", state="disputed", note="the page shows other amounts"))
    assert "verified (image, 2026-10-26, claude-test)" in offer_tools.offer_card(con, "3006666", loaded=loaded)
    assert "verification disputed: the page shows other amounts" in offer_tools.offer_card(con, "3002222", loaded=loaded)
    card = offer_tools.offer_card(con, "3001234", loaded=loaded)
    assert "not verified" in card and "verified (" not in card
    assert "not verified" not in offer_tools.offer_card(con, "3001234")  # no marks without verification
    assert "verified" in offer_tools.list_offers(con, client="Globex", loaded=loaded)


def test_overlay_ignores_expired_and_revoked(offers_world_linked, tmp_path):
    cfg, con = offers_world_linked
    old = _record(con, "3006666", "00")
    old.size += 1
    gone = _record(con, "3002222", "00")
    loaded = _loaded(cfg, con, tmp_path, old, gone,
                     _record(con, "3002222", "00", state="revoked", checked_at="2026-10-26T11:00:00"))
    out = offer_tools.offer_lines(con, machine="Paloma", loaded=loaded)
    assert _machine_rows(out, "3006666")[0][10] == "180000" and _machine_rows(out, "3006666")[0][-1] == "no"
    assert "1 expired" in out and "0 verified" in out


# --- the bounded "To verify" list ----------------------------------------------------------------------

def _to_verify_lines(out):
    return [l for l in out.splitlines() if l.startswith("- offer ")]


def test_to_verify_caps_and_orders(offers_world_linked, tmp_path):
    cfg, con = offers_world_linked
    con.execute("UPDATE offer_lines SET amounts = '{\"CHF\": 900000.0}' WHERE kind = 'machine' AND revision_id = "
                "(SELECT id FROM offer_revisions WHERE offer_key = '3006666')")
    loaded = _loaded(cfg, con, tmp_path)
    listed = {}
    out = offer_tools.offer_lines(con, machine="Paloma", revisions="all", loaded=loaded, verify_limit=3,
                                  listed=listed, now=1000.0)
    shown = _to_verify_lines(out)
    assert len(shown) == 3 and len(listed) == 3
    assert shown[0].startswith("- offer 3006666 rev 00")          # the outlier first
    assert "offer 3001234" in shown[1]                            # then the won offer
    assert "unverified)" in out and "say continue for the next ones" in out
    first = {s.split(":")[0] for s in shown}
    again = offer_tools.offer_lines(con, machine="Paloma", revisions="all", loaded=loaded, verify_limit=3,
                                    listed=listed, now=1500.0)
    assert first.isdisjoint({s.split(":")[0] for s in _to_verify_lines(again)})   # skipped for 30 minutes
    later = offer_tools.offer_lines(con, machine="Paloma", revisions="all", loaded=loaded, verify_limit=3,
                                    listed=listed, now=1000.0 + 1801)
    assert _to_verify_lines(later)[0].startswith("- offer 3006666 rev 00")      # may come back after 30 minutes


def test_offer_stats_appends_list_offer_card_does_not(offers_world_linked, tmp_path):
    cfg, con = offers_world_linked
    loaded = _loaded(cfg, con, tmp_path)
    stats = offer_tools.offer_stats(con, "year", loaded=loaded, verify_limit=2, listed={}, now=0.0)
    assert len(_to_verify_lines(stats)) == 2 and "To verify (2 of " in stats
    assert "To verify" not in offer_tools.offer_card(con, "3006666", loaded=loaded)
    assert "To verify" not in offer_tools.list_offers(con, loaded=loaded)


def test_no_list_when_all_verified_or_off(offers_world_linked, tmp_path):
    cfg, con = offers_world_linked
    loaded = _loaded(cfg, con, tmp_path, _record(con, "3006666", "00"))
    out = offer_tools.offer_lines(con, client="Globex", machine="Paloma", loaded=loaded, verify_limit=5,
                                  listed={}, now=0.0)
    assert "To verify" not in out and "1 verified, 0 not verified" in out
    off = offer_tools.offer_lines(con, machine="Paloma", loaded=verified.Loaded(), verify_limit=5, listed={}, now=0.0)
    assert "To verify" not in off


# --- offer_verify and offer_health ------------------------------------------------------------------------

def _register_submission(con, offer="3006666", revision="00"):
    """The register's own lines of a revision, submitted back as what the agent saw."""
    r = con.execute("SELECT * FROM offer_revisions WHERE offer_key = ? AND revision = ?", (offer, revision)).fetchone()
    lines = [{"text": l["text"], "kind": l["kind"], "model": l["model"], "quantity": l["quantity"],
              "amounts": json.loads(l["amounts"]), "page": l["page"]}
             for l in con.execute("SELECT * FROM offer_lines WHERE revision_id = ? ORDER BY position", (r["id"],))]
    return dict(offer=offer, revision=revision, path=r["path"], pages=r["pages"], lines=lines,
                totals=json.loads(r["totals"]), checked_on="image", checked_by="claude-test")


@pytest.fixture
def verifying(offers_world_linked, tmp_path):
    cfg, con = offers_world_linked
    cfg.verified_dir = str(tmp_path / "shared")
    (tmp_path / "shared").mkdir()
    return cfg, con, DocTools(cfg), tmp_path / "shared"


def test_offer_verify_stores_and_shares(verifying):
    cfg, con, tools, shared = verifying
    out = tools.offer_verify(**_register_submission(con))
    assert out.startswith("Verified: offer 3006666 rev 00: 4 lines") and "checked on image" in out
    assert len(list(shared.glob("*.json"))) == 1
    chart = tools.offer_lines(machine="Paloma")
    assert [r.split(";")[-1] for r in rows(chart, "3006666")] == ["image"]
    assert "1 verified, " in chart


def test_offer_verify_refusal_lists_problems(verifying):
    cfg, con, tools, shared = verifying
    sub = _register_submission(con)
    sub["lines"][0]["amounts"] = {"CHF": 21000.0}
    out = tools.offer_verify(**sub)
    assert out.startswith("Not stored:") and "page 2: CHF 21'000 is not printed there" in out
    assert "dispute=true" in out and not list(shared.glob("*.json"))


def test_verify_refused_without_shared_folder(verifying, tmp_path):
    cfg, con, tools, shared = verifying
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    cfg.verified_mirror = str(mirror)
    cfg.verified_dir = str(tmp_path / "gone")
    out = tools.offer_verify(**_register_submission(con))
    assert "cannot be reached" in out and "nothing was stored" in out and not list(mirror.glob("*"))
    cfg.verified_dir = None
    assert "not configured" in tools.offer_verify(**_register_submission(con))


def test_offer_verify_dispute_and_revoke(verifying):
    cfg, con, tools, shared = verifying
    sub = _register_submission(con)
    assert "need a reason" in tools.offer_verify(**sub, dispute=True)
    assert tools.offer_verify(**sub, dispute=True, reason="other amounts on the page").startswith("Recorded as disputed")
    assert "verification disputed: other amounts on the page" in tools.offer_card("3006666")
    # a later verified record, then revoked
    sub["checked_on"] = "image"
    import time
    time.sleep(1.1)  # records are ordered by the second
    assert tools.offer_verify(**sub).startswith("Verified:")
    assert "verified (image" in tools.offer_card("3006666")
    time.sleep(1.1)
    assert tools.offer_verify(**sub, revoke=True, reason="wrong").startswith("Recorded as revoked")
    assert "not verified" in tools.offer_card("3006666")


def test_offer_health_counts(verifying):
    cfg, con, tools, shared = verifying
    assert tools.offer_verify(**_register_submission(con)).startswith("Verified:")
    bad = _record(con, "3002222", "00")
    bad.size += 1                                                   # expired
    verified.write(str(shared), bad)
    (shared / "broken.json").write_text("{", encoding="utf-8")
    out = tools.offer_health()
    first = out.splitlines()[0].split(";")
    assert first[:7] == ["scope", "offers", "offers verified", "revisions", "revisions verified", "lines",
                         "lines verified"] and "CHF printed" in first and "CHF verified" in first
    allrow = [l for l in out.splitlines() if l.startswith("all;")][0].split(";")
    assert allrow[2] == "1" and allrow[4] == "1"
    assert any(l.startswith("year 2021;") for l in out.splitlines()) and any(l.startswith("category ") for l in out.splitlines())
    assert "Checked on: image 1" in out and "Expired (the document changed): 1" in out
    assert "Damaged record files: broken.json" in out
    scoped = tools.offer_health(machine="Paloma 2R")
    assert scoped.startswith("Backlog for machine Paloma 2R: 1 of ") and "revisions verified" in scoped.splitlines()[0]


def test_offers_profile_tools(offers_cfg):
    names = tool_names(server.build_server(offers_cfg))
    assert {"offer_verify", "offer_health"} <= names

    async def annotations():
        async with _client(server.build_server(offers_cfg)) as client:
            return {t.name: t.annotations for t in (await client.list_tools()).tools}

    ann = asyncio.run(annotations())
    assert ann["offer_verify"] is None or not ann["offer_verify"].read_only_hint
    assert ann["offer_health"].read_only_hint
    other = make_offers_cfg(offers_cfg.roots[0], offers_cfg.data_dir, offers_cfg.knowledge_path)
    other.profile = "marketing"
    assert not {"offer_verify", "offer_health"} & tool_names(server.build_server(other))


def test_offer_card_header_shows_verified_facts(offers_world_linked, tmp_path):
    cfg, con = offers_world_linked
    loaded = _loaded(cfg, con, tmp_path, _record(con, "3006666", "00"))
    card = offer_tools.offer_card(con, "3006666", loaded=loaded)
    assert "country Italy" in card and "type budget" in card and "2021-07-02..2021-07-02" in card

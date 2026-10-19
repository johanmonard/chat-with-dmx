import json
from datetime import date, timedelta

import pytest

from dmx_docs import prices, vision

DE = {4: "PREIS\n2x Paloma 3R einschliesslich jeweils:\n- 3 Delta Roboter\n(EUR 400 000.-)\nCHF 410 000.-\n"
         "Installation und Inbetriebnahme\n(EUR 50 000.-)\nCHF 51 000.-\n"
         "Zusätzliche Tage sind auf Anfrage zum Preis von CHF 1 100.-/Tag\n",
      5: "PREIS, netto, ohne Steuern,\n(EUR 450 000.-)\nCHF 461 000.-\n"
         "Nur Preise in Schweizer Franken (CHF) sind gültig. Richtwert ist der tatsächliche Tageskurs ist 1.00 EUR = 1,0244 CHF"}
EN = {4: "3. PRICE\nA Paloma 2R Top Loader cell including: robots, frame, HMI CHF 180,000.00\n"
         "Option: vision system CHF 25,000.00\nTraining (2 days) CHF 4,000.00\nDiscount -CHF 9,000.00\n"
         "(Additional days: 1100 CHF/Day)\nPRICE, net, without taxes, CHF 200,000.00\n"
         "Only prices in Swiss Francs are valid (CHF). As indication, actual rate (November 1st, 2011) 1 Euro = 1.20 CHF."}


@pytest.mark.parametrize("s, v", [("410 200.-", 410200.0), ("410'200.-", 410200.0), ("1’230’000", 1230000.0),
    ("1.230.000,00", 1230000.0), ("1,230,000.00", 1230000.0), ("-9 000.-", -9000.0), ("25,000.00", 25000.0),
    ("1 100.-", 1100.0), ("12,5", 12.5), ("abc", None)])
def test_parse_number(s, v):
    assert prices.parse_number(s) == v


def test_rates_are_not_amounts():
    assert prices.amounts_in("Zusätzliche Tage zum Preis von CHF 1 100.-/Tag") == []
    assert prices.amounts_in("(Additional days: 1100 CHF/Day)") == []
    assert prices.amounts_in("(EUR 50 000.-) CHF 51 000.-") == [("EUR", 50000.0), ("CHF", 51000.0)]


def test_german_section_second_currency_first():
    s = prices.parse_section(DE)
    assert s.pages == [4, 5] and s.totals == {"CHF": 461000.0, "EUR": 450000.0} and s.sum_ok is True
    first = s.lines[0]
    assert first.amounts == {"CHF": 410000.0, "EUR": 400000.0} and first.quantity == 2
    assert (first.kind, first.family, first.model) == ("machine", "Paloma", "Paloma 3R")
    assert first.text.startswith("2x Paloma 3R einschliesslich") and "3 Delta Roboter" in first.text
    assert s.lines[1].kind == "service" and len(s.lines) == 2
    assert s.rates == [prices.Rate("EUR", "CHF", 1.0244, None)]


def test_english_section_options_discount_rate():
    s = prices.parse_section(EN)
    assert [l.kind for l in s.lines] == ["machine", "option", "service", "discount"]
    assert [l.position for l in s.lines] == [1, 2, 3, 4]  # 1-based, document order
    assert s.lines[3].amounts == {"CHF": -9000.0} and s.sum_ok is True and s.totals == {"CHF": 200000.0}
    assert s.rates == [prices.Rate("EUR", "CHF", 1.20, "November 1st, 2011")]


def test_sum_mismatch_and_no_total():
    bad = {1: EN[4].replace("CHF 4,000.00", "CHF 5,000.00")}
    assert prices.parse_section(bad).sum_ok is False
    no_total = {1: "PRICE\nPaloma 4R cell CHF 300,000.00\nOption: second infeed CHF 40,000.00\n"}
    s = prices.parse_section(no_total)
    assert s.totals == {} and s.sum_ok is None and len(s.lines) == 2


def test_no_price_section():
    assert prices.parse_section({1: "Technical description of the Paloma 2R", 2: "Layout"}) is None


def test_offer_date_and_type():
    assert prices.offer_date({1: "Vufflens-la-Ville, 23.06.2025\nOffer 3001234"}) == ("2025-06-23", "23.06.2025")
    assert prices.offer_date({1: "Lausanne, le 15 mars 2012"}) == ("2012-03-15", "15 mars 2012")
    assert prices.offer_date(EN) is None  # only date is inside the rate sentence, and not on pages 1-2
    assert prices.offer_type({1: "Budgetary offer for a Paloma 4R"}, "x.pdf") == "budget"
    assert prices.offer_type({1: "Offer"}, "ACME Richtpreis.pdf") == "budget"
    assert prices.offer_type({1: "Offer"}, "x.pdf") == "firm"


# --- review fixes (round 1) and rulings R11-R14 ---

ITEMS = "PRICE\nPaloma 4R cell CHF 300,000.00\nOption: vision system CHF 25,000.00\n"


@pytest.mark.parametrize("s, expected", [
    ("US$ 1,000.00", [("USD", 1000.0)]), ("$ 1,000.00", [("USD", 1000.0)]), ("€ 500.-", [("EUR", 500.0)]),
    ("£ 200", [("GBP", 200.0)]), ("Fr. 1'000.-", [("CHF", 1000.0)]), ("SFr. 1'000.-", [("CHF", 1000.0)]),
    ("Preis: 1 200.- CHF", [("CHF", 1200.0)]), ("25 000,00 EUR", [("EUR", 25000.0)]),
    ("- CHF 5 000.-", [("CHF", 5000.0)]),  # a bullet, not a minus sign
    ("CHF 1 100.- par jour", []), ("EUR 95.-/h", []), ("Fr. 12.05.2020", []),
    ("1 EUR = 1.065 CHF", []), ("1 EUR = CHF 1.08", []), ("1 € = 1.08 CHF", []),
])
def test_currency_forms_and_rates_in_text(s, expected):
    assert prices.amounts_in(s) == expected


@pytest.mark.parametrize("written, rate", [("1 EUR = 1.065 CHF", 1.065), ("1 EUR = CHF 1.08", 1.08),
                                           ("1 € = 1.08 CHF", 1.08)])
def test_exchange_rate_forms(written, rate):
    s = prices.parse_section({4: EN[4].replace("1 Euro = 1.20 CHF", written)})
    assert s.rates == [prices.Rate("EUR", "CHF", rate, "November 1st, 2011")]
    assert len(s.lines) == 4 and s.sum_ok is True


@pytest.mark.parametrize("label", ["Gesamtpreis netto", "Nettopreis total", "Prix total HT", "TOTAL", "Gesamtbetrag",
                                   "Prix total", "Montant total", "Grand total", "PRIX TOTAL"])
@pytest.mark.parametrize("sep", [" ", "\n"])
def test_compound_and_bare_totals(label, sep):
    s = prices.parse_section({1: ITEMS + f"{label}{sep}CHF 325,000.00"})
    assert s.totals == {"CHF": 325000.0} and s.sum_ok is True and len(s.lines) == 2


def test_fused_quantity_needs_a_printed_total():
    no_total = prices.parse_section({1: "PRICE\nCellule Paloma 4R 1 150 000 €\nOption vision 50 000 €"})
    assert no_total.lines[0].amounts == {"EUR": 1150000.0} and no_total.sum_ok is False
    confirmed = prices.parse_section({1: "PRICE\nCellule Paloma 4R 1 150 000 €\nOption vision 50 000 €\n"
                                         "Prix total net HT 1 200 000 €"})
    assert confirmed.sum_ok is True
    wrong = prices.parse_section({1: "PRICE\nQty 2 150 000,00 EUR\nPRICE net EUR 150 000,00"})
    assert wrong.lines[0].amounts == {"EUR": 2150000.0} and wrong.sum_ok is False


def test_amounts_without_a_label_make_the_section_unreadable():
    s = prices.parse_section({1: "PRICE\nPaloma 4R cell\nCHF 300 000.-\nCHF 20 000.-\nPRICE net CHF 320 000.-"})
    assert [l.text for l in s.lines] == ["Paloma 4R cell"] and s.sum_ok is False
    s = prices.parse_section({1: "PRICE\nCHF 300 000.-\nOption: vision CHF 5 000.-"})
    assert [l.text for l in s.lines] == ["Option: vision"] and s.sum_ok is False


def test_lines_after_the_net_total_are_kept_but_not_trusted():
    s = prices.parse_section({1: "PRICE\nVariant A: Paloma 3R cell CHF 300,000.00\n"
                                 "PRICE, net, without taxes, CHF 300,000.00\n"
                                 "Variant B: Paloma 4R cell CHF 380,000.00\n"
                                 "PRICE, net, without taxes, CHF 380,000.00"})
    assert len(s.lines) == 2 and s.totals == {"CHF": 300000.0} and s.sum_ok is False


@pytest.mark.parametrize("text, amounts, kind", [
    ("Ligne d'emballage Paloma 4R", {}, "machine"), ("Top loader packing cell with 2 robots", {}, "machine"),
    ("Installation und Inbetriebnahme", {}, "service"), ("Option: Paloma 2R second cell", {}, "option"),
    ("Transport and packing", {}, "service"), ("Rabatt 5%", {}, "discount"),
    ("Paloma 4R cell", {"CHF": -1000.0}, "discount"), ("Spare parts kit", {}, "other"),
    ("Top loader cell\nincluding installation", {}, "machine"),  # the first line decides
    # R28: a service word before the machine name wins; after it, the machine does.
    ("Installation and commissioning of the 2 Paloma 3R cells", {}, "service"),
    ("Training for the Paloma 3R operators", {}, "service"), ("Transport of the Paloma 3R", {}, "service"),
    ("Option: Paloma 3R second gripper", {}, "option"), ("Paloma 3R cell including installation", {}, "machine"),
])
def test_classify(text, amounts, kind):
    assert prices.classify(text, amounts) == kind


def test_label_hygiene():
    heading = prices.parse_section({1: "PRICE\nPaloma 4R cell CHF 300,000.00\nPRICES AND OPTIONS\nVision system\n"
                                       "CHF 25,000.00\nPRICE, net, without taxes, CHF 325,000.00"})
    assert [l.text for l in heading.lines] == ["Paloma 4R cell", "Vision system"] and heading.sum_ok is True
    described = prices.parse_section({1: "PRICE\nPaloma 4R cell CHF 300,000.00\n"
                                         "Installation and commissioning CHF 20,000.00\n"
                                         "including travel and accommodation\n"
                                         "PRICE, net, without taxes, CHF 320,000.00"})
    assert described.lines[1].text == "Installation and commissioning including travel and accommodation"
    assert described.lines[1].kind == "service" and described.sum_ok is True
    header = prices.parse_section({1: ITEMS + "Page 1 of 2",
                                   2: "Offer 3001234 - ACME Foods - Paloma 4R\nSpare parts kit CHF 12,000.00\n"
                                      "PRICE, net, without taxes, CHF 337,000.00"})
    spare = header.lines[2]
    assert (spare.text, spare.kind, spare.family, spare.page) == ("Spare parts kit", "other", None, 2)
    assert header.pages == [1, 2] and header.sum_ok is True


def test_price_pages():
    total = "PRICE, net, without taxes, CHF 325,000.00"
    cover = {1: "Dear Sir, our price for the Paloma 4R is CHF 300,000.00 (EUR 290,000.00).", 2: "Technical description",
             3: "Layout", 4: "Delivery", 5: ITEMS + total}
    assert prices.price_pages(cover) == [5]  # the first run without a total gives way to one with a total
    assert prices.price_pages({1: ITEMS, 2: "a", 3: total}) == [1, 2, 3]
    assert prices.price_pages({1: ITEMS, 2: "a", 3: "b", 4: total}) == [1]  # at most 3 pages
    assert prices.price_pages({1: "Layout"}) == []


@pytest.mark.parametrize("text, expected", [
    ("Bern, 1. November 2011", ("2011-11-01", "1. November 2011")),
    ("November 1st, 2011", ("2011-11-01", "November 1st, 2011")),
    ("Paris, le 1er novembre 2011", ("2011-11-01", "1er novembre 2011")),
    ("Vufflens-la-Ville, 23/06/2025", ("2025-06-23", "23/06/2025")),
    ("Date: 2025-06-23", ("2025-06-23", "2025-06-23")),
    ("Projet en cours, Lausanne 12.03.2019", ("2019-03-12", "12.03.2019")),
    ("As indication, actual rate (November 1st, 2011) 1 Euro = 1.20 CHF. Lausanne, 15.12.2011",
     ("2011-12-15", "15.12.2011")),
    ("Exchange rate of 01.11.2011 applies.\nLausanne, 15.12.2011", ("2011-12-15", "15.12.2011")),
])
def test_offer_date_formats_and_rate_sentences(text, expected):
    assert prices.offer_date({1: text}) == expected


# --- review fixes (round 2) ---

@pytest.mark.parametrize("pages", [
    {4: "PRIX\nNos prix s'entendent HT, depart usine.\nCellule Paloma 4R EUR 300 000.-\nOption vision EUR 20 000.-",
     5: "Formation EUR 5 000.-\nPrix total net HT EUR 325 000.-"},
    {4: "PREIS\nAlle Preise verstehen sich netto ab Werk.\nPaloma 4R Zelle EUR 300 000.-\nOption Vision EUR 20 000.-",
     5: "Schulung EUR 5 000.-\nPREIS, netto, ohne Steuern,\nEUR 325 000.-"},
    {4: "PRICE\nPaloma 4R cell\nTotal of 4 Delta robots per cell.\nEUR 300 000.-\nOption vision EUR 20 000.-",
     5: "Training EUR 5 000.-\nPRICE, net, without taxes, EUR 325 000.-"},
])
def test_sentences_without_amounts_never_end_the_section(pages):
    s = prices.parse_section(pages)
    assert s.pages == [4, 5] and s.totals == {"EUR": 325000.0} and s.sum_ok is True
    assert len(s.lines) == 3 and (s.lines[0].kind, s.lines[0].model) == ("machine", "Paloma 4R")


def test_amounts_of_two_groups_are_not_ambiguous():
    s = prices.parse_section({1: "PRICE\nOption vision 50 000 EUR\nFormation 4 000 EUR"})
    assert s.sum_ok is None and [l.amounts for l in s.lines] == [{"EUR": 50000.0}, {"EUR": 4000.0}]


def test_price_intro_sentence_is_not_the_item():
    s = prices.parse_section({1: "PRICE\nThe price for the equipment described in this offer is:\nPaloma 4R cell\n"
                                 "CHF 300,000.00\nOption: vision system CHF 25,000.00\n"
                                 "PRICE, net, without taxes, CHF 325,000.00"})
    first = s.lines[0]
    assert (first.text, first.kind, first.family, first.model) == ("Paloma 4R cell", "machine", "Paloma", "Paloma 4R")
    assert s.sum_ok is True


@pytest.mark.parametrize("text, expected", [
    ("As indication, actual rate (November 1st, 2011)\n1 Euro = 1.20 CHF.\nLausanne, 15.12.2011",
     ("2011-12-15", "15.12.2011")),
    ("cours du jour (12.03.2019)\n1 EUR = 1.08 CHF\nLausanne, le 15 mars 2019", ("2019-03-15", "15 mars 2019")),
])
def test_offer_date_skips_rate_dates_wrapped_over_two_lines(text, expected):
    assert prices.offer_date({1: text}) == expected


# --- review fixes (round 3) ---

@pytest.mark.parametrize("subtotal", ["Sous-total", "Subtotal", "Sub-total", "Zwischensumme"])
@pytest.mark.parametrize("total", ["Prix total net HT", "TOTAL"])
def test_subtotal_is_neither_the_total_nor_a_line(subtotal, total):
    s = prices.parse_section({4: f"PRIX\nCellule Paloma 4R EUR 300 000.-\nOption vision EUR 25 000.-\n"
                                 f"{subtotal} EUR 325 000.-",
                              5: f"Formation EUR 5 000.-\n{total} EUR 330 000.-"})
    assert s.pages == [4, 5] and s.totals == {"EUR": 330000.0} and s.sum_ok is True
    assert [l.amounts["EUR"] for l in s.lines] == [300000.0, 25000.0, 5000.0]


def test_item_label_ending_with_colon_before_its_amount():
    s = prices.parse_section({1: "PRICE\nPaloma 4R cell price:\nCHF 300,000\nInstallation price:\nCHF 25,000\n"
                                 "Total CHF 325,000"})
    assert [l.kind for l in s.lines] == ["machine", "service"] and s.sum_ok is True


def _model_says(fake_ollama, answer):
    fake_ollama.reply = lambda body: (200, {"message": {"content": json.dumps(answer)}, "done": True})


def test_model_reads_a_page(fake_ollama):
    fake_ollama.reply = lambda body: (200, {"message": {"content": json.dumps({
        "lines": [{"text": "Paloma 4R cell", "quantity": 1, "amounts": {"CHF": 300000}},
                  {"text": "Installation", "quantity": None, "amounts": {"CHF": 20000}}],
        "totals": {"CHF": 320000}, "rates": []})}, "done": True})
    s = prices.parse_with_model(fake_ollama.url, "test-vision", {3: "garbled table text 300000 20000 320000"}, timeout=10)
    assert [l.kind for l in s.lines] == ["machine", "service"] and s.sum_ok is True
    assert s.lines[0].model == "Paloma 4R" and s.pages == [3]
    assert fake_ollama.calls[0]["format"] == "json" and "garbled table text" in fake_ollama.calls[0]["messages"][-1]["content"]


def test_model_bad_json_raises(fake_ollama):
    fake_ollama.reply = lambda body: (200, {"message": {"content": "not json"}, "done": True})
    with pytest.raises(vision.VisionError):
        prices.parse_with_model(fake_ollama.url, "test-vision", {1: "x"}, timeout=10)


def test_model_http_error_and_timeout_raise(fake_ollama):
    fake_ollama.reply = lambda body: (500, {"error": "boom"})
    with pytest.raises(vision.VisionError) as e:
        prices.parse_with_model(fake_ollama.url, "test-vision", {1: "x"}, timeout=10)
    assert e.value.timeout is False
    fake_ollama.reply = lambda body: (200, {"message": {"content": "{}"}, "done": True})
    fake_ollama.delay = 2
    with pytest.raises(vision.VisionError) as e:
        prices.parse_with_model(fake_ollama.url, "test-vision", {1: "x"}, timeout=0.5)
    assert e.value.timeout is True


def test_chat_json_request_and_answer(fake_ollama):
    fake_ollama.reply = lambda body: (200, {"message": {"content": '{"a": 1}'}, "done": True})
    assert vision.chat_json(fake_ollama.url, "test-vision", "hello", timeout=10) == {"a": 1}
    body = fake_ollama.calls[0]
    assert body["model"] == "test-vision" and body["stream"] is False and body["format"] == "json"
    assert body["options"] == {"temperature": 0, "num_predict": 2048, "num_ctx": 8192} and body["keep_alive"] == "10m"
    assert body["messages"][-1] == {"role": "user", "content": "hello"}


def test_chat_json_answer_must_be_an_object(fake_ollama):
    fake_ollama.reply = lambda body: (200, {"message": {"content": "[1, 2]"}, "done": True})
    with pytest.raises(vision.VisionError):
        vision.chat_json(fake_ollama.url, "test-vision", "hello", timeout=10)


def test_model_answer_without_lines_is_none(fake_ollama):
    _model_says(fake_ollama, {"lines": [], "totals": {}, "rates": []})
    assert prices.parse_with_model(fake_ollama.url, "test-vision", {1: "x"}, timeout=10) is None
    _model_says(fake_ollama, {"lines": [{"text": "Paloma 4R", "amounts": {"CHF": "n/a"}}]})
    assert prices.parse_with_model(fake_ollama.url, "test-vision", {1: "x"}, timeout=10) is None


def test_model_without_pages_is_not_called(fake_ollama):
    assert prices.parse_with_model(fake_ollama.url, "test-vision", {}, timeout=10) is None
    assert fake_ollama.calls == []


def test_model_gets_the_price_pages_or_the_first_six(fake_ollama):
    _model_says(fake_ollama, {"lines": []})
    pages = {i: f"text of page {i}" for i in range(1, 9)}
    prices.parse_with_model(fake_ollama.url, "test-vision", pages, timeout=10)
    sent = fake_ollama.calls[-1]["messages"][-1]["content"]
    assert "text of page 6" in sent and "text of page 7" not in sent
    pages[4] = "PRICE\nPaloma 4R CHF 300,000\nTotal CHF 300,000"
    prices.parse_with_model(fake_ollama.url, "test-vision", pages, timeout=10)
    sent = fake_ollama.calls[-1]["messages"][-1]["content"]
    assert "Paloma 4R CHF 300,000" in sent and "text of page 3" not in sent and "text of page 5" not in sent


def test_model_amounts_are_cleaned_like_the_rules_do(fake_ollama):
    _model_says(fake_ollama, {
        "lines": [{"text": " 2x  Paloma 3R cell ", "quantity": "2", "amounts": {"chf": "1'000.50", "€": 900, "EUR": "x"}},
                  {"text": "Discount", "quantity": 1.5, "amounts": {"CHF": -50, "USD": None, "XX": 5}},
                  {"text": "", "amounts": {"CHF": 1}},
                  "not a line",
                  {"text": "Option vision", "amounts": {"CHF": True}}],
        "totals": {"CHF": "950,50", "Fr.": "bad"},
        "rates": [{"base": "EUR", "quote": "chf", "rate": "1.1", "date": "12.03.2019"},
                  {"base": "EUR", "quote": "CHF", "rate": 0}, {"base": "EUR"}, {"base": "CHF", "quote": "chf", "rate": 1}]})
    s = prices.parse_with_model(fake_ollama.url, "test-vision", {2: "x 1'000.50 / 900", 3: "y 50 / 950,50"}, timeout=10)
    assert [(l.position, l.text, l.quantity, l.amounts, l.kind, l.page) for l in s.lines] == [
        (1, "2x Paloma 3R cell", 2, {"CHF": 1000.5, "EUR": 900.0}, "machine", 2),
        (2, "Discount", None, {"CHF": -50.0}, "discount", 2)]
    assert (s.lines[0].family, s.lines[0].model, s.lines[1].model) == ("Paloma", "Paloma 3R", None)
    assert s.totals == {"CHF": 950.5} and s.sum_ok is True and s.pages == [2, 3]
    assert s.rates == [prices.Rate("EUR", "CHF", 1.1, "12.03.2019")]


def test_model_sum_is_checked_and_kind_never_comes_from_the_model(fake_ollama):
    _model_says(fake_ollama, {
        "lines": [{"text": "Paloma 4R cell", "kind": "service", "amounts": {"CHF": 300000}},
                  {"text": "Option vision", "amounts": {"CHF": 20000}}],
        "totals": {"CHF": 330000}})
    s = prices.parse_with_model(fake_ollama.url, "test-vision", {1: "x 300000 20000 330000"}, timeout=10)
    assert [l.kind for l in s.lines] == ["machine", "option"] and s.sum_ok is False
    _model_says(fake_ollama, {"lines": [{"text": "Paloma 4R cell", "amounts": {"CHF": 300000}}]})
    assert prices.parse_with_model(fake_ollama.url, "test-vision", {1: "x 300000"}, timeout=10).sum_ok is None


def test_model_line_page_when_it_can_say(fake_ollama):
    _model_says(fake_ollama, {"lines": [{"text": "Paloma 4R", "page": 3, "amounts": {"CHF": 1}},
                                        {"text": "Option a", "page": 9, "amounts": {"CHF": 1}},
                                        {"text": "Option b", "page": "x", "amounts": {"CHF": 1}}]})
    s = prices.parse_with_model(fake_ollama.url, "test-vision", {2: "x", 3: "y"}, timeout=10)
    assert [l.page for l in s.lines] == [3, 2, 2]


def test_model_quantity_and_page_ignore_nan_and_booleans(fake_ollama):
    fake_ollama.reply = lambda body: (200, {"message": {"content": '{"lines": [{"text": "Paloma 4R", "quantity": NaN,'
        ' "page": true, "amounts": {"CHF": 1}}, {"text": "Option a", "quantity": Infinity, "amounts": {"CHF": 1}}],'
        ' "totals": {"CHF": Infinity}}'}, "done": True})
    s = prices.parse_with_model(fake_ollama.url, "test-vision", {1: "x 1", 2: "y"}, timeout=10)
    assert [(l.quantity, l.page) for l in s.lines] == [(None, 1), (None, 1)] and s.totals == {} and s.sum_ok is None


def test_chat_json_window_follows_the_prompt_length(fake_ollama):
    fake_ollama.reply = lambda body: (200, {"message": {"content": "{}"}, "done": True})
    for prompt in ("short", "x" * 30000, "x" * 90000):
        vision.chat_json(fake_ollama.url, "test-vision", prompt, timeout=10)
    assert [c["options"]["num_ctx"] for c in fake_ollama.calls] == [8192, 12048, 16384]


def test_chat_json_prompt_cut_raises(fake_ollama):
    fake_ollama.reply = lambda body: (200, {"message": {"content": "{}"}, "prompt_eval_count": 8192 - 2048,
                                            "done": True})
    with pytest.raises(vision.VisionError, match="prompt cut"):
        vision.chat_json(fake_ollama.url, "test-vision", "short", timeout=10)
    fake_ollama.reply = lambda body: (200, {"message": {"content": "{}"}, "prompt_eval_count": 8192 - 2049,
                                            "done": True})
    assert vision.chat_json(fake_ollama.url, "test-vision", "short", timeout=10) == {}


def test_chat_json_model_without_thinking_switch_is_asked_again(fake_ollama):
    def reply(body):
        if "think" in body:
            return 400, {"error": '"test-vision" does not support thinking'}
        return 200, {"message": {"content": '{"a": 1}'}, "done": True}

    fake_ollama.reply = reply
    assert vision.chat_json(fake_ollama.url, "test-vision", "hello", timeout=10) == {"a": 1}
    assert len(fake_ollama.calls) == 2 and "think" not in fake_ollama.calls[1]


def test_model_prompt_has_the_pages_first_and_the_instructions_last(fake_ollama):
    _model_says(fake_ollama, {"lines": []})
    prices.parse_with_model(fake_ollama.url, "test-vision", {2: "page two text"}, timeout=10)
    sent = fake_ollama.calls[0]["messages"][-1]["content"]
    assert sent.startswith(prices.MODEL_ROLE) and sent.endswith(prices.MODEL_PROMPT)
    assert sent.index(prices.MODEL_ROLE) < sent.index("[Page 2]") < sent.index("page two text") < sent.index(prices.MODEL_PROMPT)


def test_model_prompt_cut_raises(fake_ollama):
    fake_ollama.reply = lambda body: (200, {"message": {"content": '{"lines": []}'}, "prompt_eval_count": 9000,
                                            "done": True})
    with pytest.raises(vision.VisionError, match="prompt cut"):
        prices.parse_with_model(fake_ollama.url, "test-vision", {1: "x"}, timeout=10)


def test_model_kind_and_model_come_from_the_name_not_the_description(fake_ollama):
    _model_says(fake_ollama, {"lines": [
        {"name": "Paloma 4R cell", "text": "Paloma 4R cell, option vision, training, compatible with Presto 2R",
         "amounts": {"CHF": 300000}},
        {"name": "Installation", "text": "Installation incl. option for Paloma 3R", "amounts": {"CHF": 20000}}]})
    s = prices.parse_with_model(fake_ollama.url, "test-vision", {1: "300000 20000"}, timeout=10)
    assert [(l.kind, l.family, l.model) for l in s.lines] == [("machine", "Paloma", "Paloma 4R"),
                                                              ("service", None, None)]
    assert s.lines[0].text == "Paloma 4R cell, option vision, training, compatible with Presto 2R"


def test_model_without_a_name_uses_the_first_line_of_the_text(fake_ollama):
    _model_says(fake_ollama, {"lines": [{"text": "Installation\noption for Paloma 3R", "amounts": {"CHF": 20000}}]})
    s = prices.parse_with_model(fake_ollama.url, "test-vision", {1: "20000"}, timeout=10)
    assert (s.lines[0].kind, s.lines[0].model) == ("service", None)


def test_model_amounts_must_be_printed_in_the_pages(fake_ollama):
    answer = {"lines": [{"name": "Paloma 4R", "amounts": {"CHF": 300000}}, {"name": "Option", "amounts": {"CHF": 20000}}],
              "totals": {"CHF": 320000}}
    _model_says(fake_ollama, answer)
    page = "Paloma 4R CHF 300'000.-\nOption CHF 20 000.-"
    s = prices.parse_with_model(fake_ollama.url, "test-vision", {1: page}, timeout=10)
    assert s.totals == {"CHF": 320000.0} and s.sum_ok is False  # the sum is the model's, not printed
    s = prices.parse_with_model(fake_ollama.url, "test-vision", {1: page + "\nTotal CHF 320 000.-"}, timeout=10)
    assert s.sum_ok is True
    answer["lines"][0]["amounts"] = {"CHF": 310000}
    answer["totals"] = {}
    s = prices.parse_with_model(fake_ollama.url, "test-vision", {1: page}, timeout=10)
    assert s.sum_ok is False  # an invented amount, even without a total


# --- ruling R19: exchange rates as printed in real offers ---

def test_rate_wrapped_after_the_equals_sign_is_not_a_line():
    wrapped = {4: EN[4].replace("1 Euro = 1.20 CHF.", "1 Euro =\n1.20 CHF.")}
    s = prices.parse_section(wrapped)
    assert len(s.lines) == 4 and s.sum_ok is True and s.totals == {"CHF": 200000.0}
    assert s.rates == [prices.Rate("EUR", "CHF", 1.20, "November 1st, 2011")]


def test_euro_written_with_the_euro_sign():
    assert prices.amounts_in("As indication, 1 €uro = 1.20 CHF") == []
    s = prices.parse_section({4: EN[4].replace("1 Euro = 1.20 CHF", "1 €uro = 1.20 CHF")})
    assert s.rates == [prices.Rate("EUR", "CHF", 1.20, "November 1st, 2011")] and s.sum_ok is True


# --- ruling R27: the sum tolerance is a few units in the main currency ---

def test_a_small_line_is_never_lost_in_the_rounding_tolerance():
    s = prices.parse_section({1: "PRICE\nPaloma 3R cell CHF 1,940,000.00\nTraining CHF 3,500.00\n"
                                 "PRICE, net, without taxes, CHF 1,940,000.00"})
    assert len(s.lines) == 2 and s.sum_ok is False


def test_a_misread_digit_fails_the_sum_check():
    s = prices.parse_section({1: "PRICE\nPaloma 3R cell CHF 410 800.-\nInstallation CHF 20 000.-\n"
                                 "PRICE, net, without taxes, CHF 430 200.-"})
    assert len(s.lines) == 2 and s.sum_ok is False


def test_conversion_currency_in_brackets_keeps_its_rounding_tolerance():
    rounded = {4: DE[4].replace("(EUR 400 000.-)", "(EUR 399 990.-)"), 5: DE[5]}  # each line converted and rounded
    assert prices.parse_section(rounded).sum_ok is True
    off = {4: DE[4].replace("CHF 410 000.-", "CHF 410 010.-"), 5: DE[5]}  # the main currency: a few units only
    assert prices.parse_section(off).sum_ok is False
    assert prices.parse_section({1: ITEMS + "TOTAL CHF 325,002.00"}).sum_ok is True  # 2 lines: 2 units of rounding


# --- fixes from the first real run (2026-10-19) ---

@pytest.mark.parametrize("page, model, quantity, start", [
    ("ACME 3001234 - 02 04.11.2024\n2. Proposal\nIn this quotation, we kindly offer you:\n"
     "2x Paloma 4R Dense each including:\n- 4 Delta robots\nCHF 771 100.-\nInstallation and commissioning\n"
     "CHF 28 900.-\nPRICE, net, without taxes, CHF 800 000.-", "Paloma 4R", 2, "2x Paloma 4R"),
    ("DUPONT F-0908021 V3 08.02.2010\n2. Proposition Equipement\n"
     "Nous avons le plaisir de vous proposer les équipements suivants :\n1x Paloma 3R comprenant :\n"
     "- 3 robots Delta\nEUR 410 000.-\nMise en service\nEUR 20 000.-\nPrix total net HT EUR 430 000.-",
     "Paloma 3R", 1, "1x Paloma 3R"),
    ("GLOBEX 3005678 - 01 15/03/2021\n2. Angebot\nGerne unterbreiten wir Ihnen das folgende Angebot:\n"
     "2xPresto 2R einschliesslich:\n- 2 Delta Roboter\nCHF 500 000.-\nInbetriebnahme\nCHF 30 000.-\n"
     "PREIS, netto, ohne Steuern, CHF 530 000.-", "Presto 2R", 2, "2xPresto 2R"),
])
def test_page_header_heading_and_intro_are_not_the_item(page, model, quantity, start):
    s = prices.parse_section({4: page})
    first = s.lines[0]
    assert first.text.startswith(start) and (first.kind, first.quantity, first.model) == ("machine", quantity, model)
    assert s.lines[1].kind == "service" and len(s.lines) == 2 and s.sum_ok is True


def test_item_line_is_the_first_with_a_quantity_or_a_machine():
    s = prices.parse_section({1: "PRICE\nPaloma 4R cell CHF 300,000.00\nAs agreed during our visit\n"
                                 "1x Paloma 3R second cell\nCHF 250,000.00\nPRICE, net, without taxes, CHF 550,000.00"})
    second = s.lines[1]
    assert (second.text, second.kind, second.quantity, second.model) == ("1x Paloma 3R second cell", "machine", 1,
                                                                         "Paloma 3R")
    assert s.sum_ok is True


def test_a_label_ending_with_a_colon_before_its_description_is_the_item():
    s = prices.parse_section({1: "PRICE\nPaloma 4R cell CHF 300,000.00\nTraining includes:\n- 2 days on site\n"
                                 "CHF 4,000.00\nPRICE, net, without taxes, CHF 304,000.00"})
    assert (s.lines[1].text, s.lines[1].kind) == ("Training includes: - 2 days on site", "service")


def test_options_announced_by_an_introduction_stay_options():
    s = prices.parse_section({1: "PRICE\nPaloma 4R cell CHF 300,000.00\nThe following option is also possible:\n"
                                 "1 Spreader CHF 5,000.00\nPRICE, net, without taxes, CHF 305,000.00"})
    assert [(l.text, l.kind) for l in s.lines] == [("Paloma 4R cell", "machine"), ("1 Spreader", "option")]
    assert s.sum_ok is True


@pytest.mark.parametrize("delivered", ["CIP Berlin (INCOTERMS 2010) EUR 290 000.- CHF 340 000.-",
                                       "Berlin (Germany) CIP Berlin, Germany (INCOTERMS 2000)\nCHF 340 000.-",
                                       "DAP Madrid CHF 340 000.-"])
def test_delivered_price_after_the_net_total_is_neither_a_line_nor_a_late_line(delivered):
    s = prices.parse_section({1: ITEMS + "PRICE, net, without taxes, CHF 325,000.00\n" + delivered})
    assert len(s.lines) == 2 and s.totals == {"CHF": 325000.0} and s.sum_ok is True


def test_delivered_price_is_the_total_when_no_net_total_is_printed():
    s = prices.parse_section({1: ITEMS + "FCA Ecublens (Incoterms 2010) CHF 325,000.00"})
    assert len(s.lines) == 2 and s.totals == {"CHF": 325000.0} and s.sum_ok is True
    wrong = prices.parse_section({1: ITEMS + "FCA Ecublens (Incoterms 2010) CHF 340,000.00"})
    assert wrong.totals == {"CHF": 340000.0} and wrong.sum_ok is False


def test_an_incoterm_caption_before_an_item_is_not_the_item():
    s = prices.parse_section({1: "PRICE\nPaloma 4R cell CHF 300,000.00\nPRICE, net, without taxes, CHF 300,000.00\n"
                                 "FCA Ecublens (Incoterms 2010)\nThe following option is also possible:\n"
                                 "1 Spreader CHF 5,000.00"})
    assert [(l.text, l.kind) for l in s.lines] == [("Paloma 4R cell", "machine"), ("1 Spreader", "option")]


@pytest.mark.parametrize("footer", ["Demaurex SA\nA Bosch Packaging Technology Company",
                                    "Demaurex SA A Bosch Packaging Technology Company", "ROTZINGER Group",
                                    "Route de Crissier 4, CH-1030 Bussigny", "www.example.ch",
                                    "Tel +41 21 123 45 67 Fax +41 21 123 45 68"])
def test_company_footers_are_no_label_lines(footer):
    s = prices.parse_section({1: f"PRICE\nPaloma 4R cell CHF 300,000.00\n{footer}\nSpare parts kit\n"
                                 "CHF 25,000.00\nPRICE, net, without taxes, CHF 325,000.00"})
    assert [(l.text, l.kind) for l in s.lines] == [("Paloma 4R cell", "machine"), ("Spare parts kit", "other")]
    assert s.sum_ok is True


def test_a_line_naming_the_company_is_not_a_footer():
    s = prices.parse_section({1: "PRICE\nPaloma 4R cell CHF 300,000.00\nAcceptance test at Demaurex SA before delivery\n"
                                 "CHF 8,000.00\nPRICE, net, without taxes, CHF 308,000.00"})
    assert s.lines[1].text == "Acceptance test at Demaurex SA before delivery" and s.sum_ok is True


def test_values_below_50_are_not_amounts():
    assert prices.amounts_in("Services included in the equipment price USD 2.1") == []
    assert prices.amounts_in("Option CHF 50.-, Discount -CHF 49.-, CHF 0.-") == [("CHF", 50.0), ("CHF", 0.0)]
    s = prices.parse_section({1: ITEMS + "9.1. Services included in the equipment price USD 2.1\n"
                                         "PRICE, net, without taxes, CHF 325,000.00"})
    assert len(s.lines) == 2 and s.sum_ok is True


def test_offer_date_before_1990_or_far_ahead_is_no_date():
    far = (date.today() + timedelta(days=400)).strftime("%d.%m.%Y")
    near = (date.today() + timedelta(days=300)).strftime("%d.%m.%Y")
    assert prices.offer_date({1: f"Printed 01.01.1900, valid until {far}\nLausanne, 23.06.2025"}) == (
        "2025-06-23", "23.06.2025")
    assert prices.offer_date({1: "Date: 1899-12-31\nLausanne, 31.12.1989"}) is None
    assert prices.offer_date({1: f"Lausanne, {near}"})[1] == near
    assert prices.plausible_date("1990-01-01") and not prices.plausible_date("1989-12-31")


def test_model_sum_uses_the_same_tolerance(fake_ollama):
    _model_says(fake_ollama, {"lines": [{"name": "Paloma 3R cell", "amounts": {"CHF": 1940000}},
                                        {"name": "Training", "amounts": {"CHF": 3500}}], "totals": {"CHF": 1940000}})
    page = "Paloma 3R cell CHF 1,940,000.-\nTraining CHF 3,500.-\nTotal CHF 1,940,000.-"
    assert prices.parse_with_model(fake_ollama.url, "test-vision", {1: page}, timeout=10).sum_ok is False

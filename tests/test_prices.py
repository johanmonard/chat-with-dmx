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
    assert s.totals == {} and s.sum_ok is None  # the sum is the model's, not printed: no total (R40)
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


# --- fix round 1 of the real-data fixes ---

def test_an_item_stating_its_delivery_term_stays_a_line():
    s = prices.parse_section({1: "PRIX\n1x Paloma 3R, livré DAP Lyon\nEUR 400 000.-\nFormation EUR 5 000.-\n"
                                 "Prix total net HT EUR 405 000.-"})
    assert [(l.kind, l.model) for l in s.lines] == [("machine", "Paloma 3R"), ("service", None)]
    assert s.lines[0].text == "1x Paloma 3R, livré DAP Lyon" and s.sum_ok is True
    s = prices.parse_section({1: "PRICE\n1x Paloma 3R cell\nFCA Ecublens (Incoterms 2010)\nCHF 400,000\n"
                                 "Training CHF 5,000"})
    assert [l.text for l in s.lines] == ["1x Paloma 3R cell FCA Ecublens (Incoterms 2010)", "Training"]
    assert s.lines[0].kind == "machine" and s.totals == {} and s.sum_ok is None


@pytest.mark.parametrize("late, kind", [("Transport and insurance CIP Berlin (Incoterms 2010) CHF 12,000.00", "service"),
                                        ("CIP Berlin (Incoterms 2010) CHF 12,000.00", "other")])
def test_a_delivered_price_below_the_net_total_is_a_late_line(late, kind):
    s = prices.parse_section({1: ITEMS + "PRICE, net, without taxes, CHF 325,000.00\n" + late})
    assert len(s.lines) == 3 and s.lines[2].kind == kind and s.sum_ok is False


def test_clean_in_place_is_no_incoterm():
    s = prices.parse_section({1: ITEMS + "CIP Cleaning station CHF 25,000.00\nPRICE, net, without taxes, CHF 350,000.00"})
    assert len(s.lines) == 3 and s.lines[2].text == "CIP Cleaning station" and s.sum_ok is True
    s = prices.parse_section({1: ITEMS + "PRICE, net, without taxes, CHF 325,000.00\nOption CIP Cleaning station CHF 25,000.00"})
    assert len(s.lines) == 3 and s.lines[2].kind == "option" and s.sum_ok is True  # R35: an option after the total


@pytest.mark.parametrize("label, kind, text", [
    ("Training:\n3 days at the customer site", "service", "Training: 3 days at the customer site"),
    ("Installation and commissioning:\nOn site, 10 days", "service", "Installation and commissioning: On site, 10 days"),
    ("Option N°2 au choix:\nCellule supplémentaire", "option", "Option N°2 au choix: Cellule supplémentaire"),
])
def test_a_colon_line_naming_a_kind_is_the_item(label, kind, text):
    s = prices.parse_section({1: f"PRICE\nPaloma 4R cell CHF 300,000.00\n{label}\nCHF 6,000.00\n"
                                 "PRICE, net, without taxes, CHF 306,000.00"})
    assert (s.lines[1].kind, s.lines[1].text) == (kind, text) and s.sum_ok is True


def test_options_after_the_net_total_are_kept_out_of_the_sum():  # R35
    s = prices.parse_section({1: ITEMS + "PRICE, net, without taxes, CHF 325,000.00\n"
                                         "The following options are also possible:\nSecond infeed CHF 40,000.00\n"
                                         "Option: spare parts kit CHF 12,000.00"})
    assert [l.kind for l in s.lines] == ["machine", "option", "option", "option"] and s.sum_ok is True
    s = prices.parse_section({1: ITEMS + "PRICE, net, without taxes, CHF 325,000.00\nTraining CHF 4,000.00"})
    assert len(s.lines) == 3 and s.sum_ok is False  # R13: any other late line


# --- fix round 2 of the real-data fixes ---

@pytest.mark.parametrize("late, kind", [
    ("1x Paloma 3R cell with optional vision CHF 280,000.00", "machine"),
    ("Paloma 3R cell, options as described in chapter 4 CHF 350,000.00", "machine"),
    ("Installation and commissioning incl. options CHF 20,000.00", "service"),
    ("Variante avec option nettoyage\n1x Paloma 4R\nCHF 340,000.00", "machine"),
])
def test_a_stray_option_word_never_makes_a_late_line_an_extra(late, kind):
    s = prices.parse_section({1: ITEMS + "PRICE, net, without taxes, CHF 325,000.00\n" + late})
    assert len(s.lines) == 3 and s.lines[2].kind == kind and s.sum_ok is False


@pytest.mark.parametrize("intro", ["Les options suivantes sont également possibles :",
                                   "L'option suivante est également possible :", "Folgende Optionen sind möglich:",
                                   "The following options are also possible:"])
def test_option_introductions_in_three_languages(intro):
    s = prices.parse_section({1: ITEMS + f"PRICE, net, without taxes, CHF 325,000.00\n{intro}\n1 Spreader CHF 5,000.00"})
    assert (s.lines[2].text, s.lines[2].kind) == ("1 Spreader", "option") and s.sum_ok is True


def test_an_intro_phrase_naming_a_service_is_the_item():
    s = prices.parse_section({1: "PRIX\nCellule Paloma 4R EUR 300 000.-\nNous vous proposons la formation suivante:\n"
                                 "2 jours sur site\nEUR 6 000.-\nPrix total net HT EUR 306 000.-"})
    assert (s.lines[1].kind, s.lines[1].text) == ("service", "Nous vous proposons la formation suivante: 2 jours sur site")


def test_a_delivered_total_naming_transport_stays_the_total():
    s = prices.parse_section({1: ITEMS.replace("CHF", "EUR") + "Prix rendu DAP Lyon, transport et assurance compris "
                                                               "EUR 325,000.00"})
    assert len(s.lines) == 2 and s.totals == {"EUR": 325000.0} and s.sum_ok is True


def test_a_delivered_total_opening_the_list_counts_its_options():
    s = prices.parse_section({1: "PRICE\nCIP Berlin (Incoterms 2010) CHF 325,000.00\nPaloma 4R cell CHF 300,000.00\n"
                                 "Option: vision system CHF 25,000.00"})
    assert len(s.lines) == 2 and s.totals == {"CHF": 325000.0} and s.sum_ok is True


# --- fix round 3 of the real-data fixes ---

@pytest.mark.parametrize("label, kind", [
    ("Installation and commissioning\nPaloma 4R cell, 3 days on site", "service"),
    ("Commissioning\nFor the Delfi cells", "service"),
    ("Formation\nOpérateurs Paloma 4R, 2 jours", "service"),
    ("SAT\nPaloma 4R cell at customer site", "service"),
    ("Option\nPaloma 2R second cell", "option"),
])
def test_a_kind_line_keeps_its_kind_above_a_machine_line(label, kind):
    s = prices.parse_section({1: f"PRICE\nPaloma 4R cell CHF 300,000.00\n{label}\nCHF 20,000.00\n"
                                 "PRICE, net, without taxes, CHF 320,000.00"})
    line = s.lines[1]
    assert line.kind == kind and line.text == " ".join(label.split()) and s.sum_ok is True


def test_a_variant_line_takes_the_kind_of_the_machine_below_and_keeps_its_name():
    s = prices.parse_section({1: "PRICE\nPaloma 4R cell CHF 300,000.00\nVariante avec option nettoyage\n1x Paloma 4R\n"
                                 "CHF 340,000.00\nPRICE, net, without taxes, CHF 640,000.00"})
    line = s.lines[1]
    assert (line.kind, line.quantity, line.model) == ("machine", 1, "Paloma 4R")
    assert line.text == "Variante avec option nettoyage 1x Paloma 4R" and s.sum_ok is True


def test_an_incoterm_line_naming_transport_first_is_a_service_line():
    s = prices.parse_section({1: "PRICE\nPaloma 4R cell CHF 300,000.00\nTransport and insurance CIP Berlin CHF 8,000.00"})
    assert [l.kind for l in s.lines] == ["machine", "service"] and s.totals == {} and s.sum_ok is None
    s = prices.parse_section({1: "PRICE\nPaloma 4R cell CHF 300,000.00\nTransport and insurance CIP Berlin CHF 8,000.00\n"
                                 "CIP Berlin (Incoterms 2010) CHF 308,000.00"})
    assert [l.kind for l in s.lines] == ["machine", "service"] and s.totals == {"CHF": 308000.0} and s.sum_ok is True


def test_the_largest_delivered_price_is_the_total():
    s = prices.parse_section({1: ITEMS + "EXW Ecublens CHF 320,000.00\nCIP Berlin (Incoterms 2010) CHF 325,000.00"})
    assert s.totals == {"CHF": 325000.0} and [l.text for l in s.lines][-1] == "EXW Ecublens" and s.sum_ok is False


def test_an_intro_phrase_offering_options_announces_options():
    s = prices.parse_section({1: ITEMS + "PRICE, net, without taxes, CHF 325,000.00\n"
                                         "We are pleased to offer the following options:\nSecond infeed CHF 40,000.00"})
    assert (s.lines[2].text, s.lines[2].kind) == ("Second infeed", "option") and s.sum_ok is True


def test_footer_and_header_shapes_in_a_description_are_kept():
    s = prices.parse_section({1: "PRICE\nVision system CHF 25,000.00\n- see www.example.com for specs\n"
                                 "- Delivery 12.03.2025 per order 3001234\nPRICE, net, without taxes, CHF 25,000.00"})
    assert s.lines[0].text == ("Vision system - see www.example.com for specs - Delivery 12.03.2025 per order "
                               "3001234")


def test_a_numbered_item_with_its_description_is_not_a_heading():
    s = prices.parse_section({1: "PRICE\nPaloma 4R cell CHF 300,000.00\n2. Equipment upgrade\n- new grippers\n"
                                 "CHF 30,000.00\nPRICE, net, without taxes, CHF 330,000.00"})
    assert s.lines[1].text == "2. Equipment upgrade - new grippers" and s.sum_ok is True


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


# --- fixes from the 30-offer spot check (2026-10-19) ---

TABLE_FR = ("Pos | Désignation | Qté | Prix/unit. CHF | Total CHF\n"
            "Engineering :\n"
            "1 | Prestation interne | 1 | | 12'000.00\n"
            "Fourniture de matériel comprenant :\n"
            "2 | Ensemble de 2 systèmes de vision ACME comprenant : Un PC | 1 | | 40'000.00\n"
            "Montage sur site (2 hommes) :\n"
            "3 | Monteur en externe, en régie : câblage | ~100 h | 120.00 | 12'000.00\n"
            "| Total | | CHF | 64'000.00\n"
            "Pour la position 3, la durée de travail est une estimation.")


def test_a_table_with_the_currency_in_its_column_header_is_read():
    s = prices.parse_section({2: TABLE_FR})
    assert s.pages == [2] and s.totals == {"CHF": 64000.0} and s.sum_ok is True
    assert [l.amounts for l in s.lines] == [{"CHF": 12000.0}, {"CHF": 40000.0}, {"CHF": 12000.0}]
    assert s.lines[2].kind == "service" and not any("|" in l.text for l in s.lines)
    assert s.lines[0].text == "Engineering : Prestation interne"  # the position number and quantity cells are left out


def test_the_table_currency_ends_with_the_table():
    text = prices._tables(TABLE_FR + "\nLes dimensions de la cellule sont données dans le plan en annexe.\n"
                                     "Poids de la machine | 1'250.00")
    assert text.endswith("Poids de la machine | 1'250.00")  # not 'CHF 1'250.00': another table
    assert "Prestation interne CHF 12'000.00" in text  # a short label line inside the table keeps it
    longer = prices._tables(TABLE_FR.replace("Montage sur site (2 hommes) :",
                                             "Installation et mise en service sur site (2 hommes) :"))
    assert "câblage ~100 h CHF 12'000.00" in longer  # so does a longer one ending with ':'


def test_a_table_with_a_column_per_currency_keeps_both():
    s = prices.parse_section({2: "Pos. | Désignation | Qty | Total CHF | Total €uros\n"
                                 "1 | One (1) Complete end-effector | 1 | 6’000.00 | 4’080.00\n"
                                 "2 | One (1) Finger for the end-effector | 1 | 1’700.00 | 1’155.00\n"
                                 "Prices | : | Our prices are understood net, in CHF, taxes not included."})
    assert [l.amounts for l in s.lines] == [{"CHF": 6000.0, "EUR": 4080.0}, {"CHF": 1700.0, "EUR": 1155.0}]


def test_a_deduction_after_the_table_total_is_kept_out_of_the_sum():  # R36
    s = prices.parse_section({2: "Pos | Designation | Qty | Unit price | Total €uro\n"
                                 "1 | Training package for technicians | 2 | 2'000.00 | 4'000.00\n"
                                 "2 | Travel package 5 days | 1 | 1'500.00 | 1'500.00\n"
                                 "| Total | | €uro | 5'500.00\n"
                                 "Special deduction:\n"
                                 "| As agreed with the ACME order, 8 hours deduction | -1 | 600.00\n"})
    assert s.totals == {"EUR": 5500.0} and [l.kind for l in s.lines] == ["service", "service", "discount"]
    assert s.lines[2].amounts == {"EUR": 600.0} and s.sum_ok is True


def test_included_items_are_lines_without_amounts():
    s = prices.parse_section({13: "PRIX\nPrix, net, hors taxe\nEquipement (selon §5.3) € Inclus\n"
                                  "Emballage, transport et assurance € Inclus\nMontage dans votre usine € Inclus\n"
                                  "Total: € 180'000.--\nCIP Lyon (France)\nIncoterms 2000"})
    assert s.pages == [13] and s.totals == {"EUR": 180000.0} and s.sum_ok is None  # nothing priced to check
    assert [(l.kind, l.amounts) for l in s.lines] == [("machine", {}), ("service", {}), ("service", {})]
    assert s.lines[0].text == "Equipement (selon §5.3) € Inclus"


def test_included_items_named_before_inclus_and_a_paragraph_between_them():
    s = prices.parse_section({4: "2. Proposition\nNous avons le plaisir de vous proposer l'équipement suivant :\n"
                                 "2 Paloma 4R composés chacun de : Inclus\n• 2 Bâtis en acier\n• 4 Robots delta\n"
                                 "Des jours additionnels sont possibles sur demande au tarif\n"
                                 "de CHF 1'100.- par jour supplémentaire.\n"
                                 "1 Installation et Mise en Service à Lyon Inclus\n"
                                 "L'installation et la mise en service est effectuée par\n"
                                 "ACME SA et est prévue pour 6 semaines,\nles frais de voyage sont inclus.\n"
                                 "Emballage, Transport et Assurance Inclus\n"
                                 "PRIX TOTAL, net, hors taxes, EUR 900,000.- CHF 1,000,000.-\nDAP Lyon (INCOTERMS 2010)"})
    assert [l.kind for l in s.lines] == ["machine", "service", "service"] and s.sum_ok is None
    assert s.lines[0].text.startswith("2 Paloma 4R composés chacun de : Inclus • 2 Bâtis")
    assert s.lines[1].text == "1 Installation et Mise en Service à Lyon Inclus"
    assert s.lines[2].text == "Emballage, Transport et Assurance Inclus"
    assert s.totals == {"EUR": 900000.0, "CHF": 1000000.0}


def test_headings_cut_the_text_before_them_and_not_included_items_are_no_lines():
    doc = prices.parse_section({13: "# PRIX\n## Emballage, transport et assurance\nNon inclus\n"
                                    "## Prix, net, hors taxe, EXW Ecublens (INCOTERMS 2000)\n"
                                    "Equipement (selon §6.3) CHF 500'000.--\nMise en service (selon §9.2) CHF 40'000.--\n"})
    assert [(l.text, l.kind) for l in doc.lines] == [("Equipement (selon §6.3)", "machine"),
                                                     ("Mise en service (selon §9.2)", "service")]
    pdf = prices.parse_section({10: "9. SERVICES INCLUS\n9.1. Services inclus dans le prix de l'équipement\n"
                                    "Les applications définies seront contrôlées avant livraison.\n"
                                    "9.2. Services inclus dans le prix de la mise en service\n"
                                    "Une formation est comprise dans le prix de la mise en service.\n"
                                    "10. PRIX\n10.1. Emballage, transport et assurance\n"
                                    "Inclus dans le prix de l'équipement au point 10.2.\n"
                                    "10.2. Prix, net, hors taxe, CIP Lyon (INCOTERMS 2000)\n"
                                    "Equipement (selon §6.2) CHF 493'000.-- (€ 316'000.--)\n"
                                    "Mise en service (selon §9.2) CHF 32'600.-- (€ 20'900.--)\n"
                                    "Le seul prix valable est celui en francs suisses (CHF)."})
    assert [l.kind for l in pdf.lines] == ["service", "machine", "service"] and pdf.lines[0].amounts == {}
    assert pdf.lines[0].text.startswith("10.1. Emballage") and pdf.lines[1].text == "Equipement (selon §6.2)"


def test_a_budgetary_price_section_with_written_quantity_and_an_option_heading():
    s = prices.parse_section({9: "9.2.3. Operating training at your location during installation\n"
                                 "10. BUDGETARY PRICE\n10.1. Packaging, Transport & Insurance\nNot included\n"
                                 "10.2. Price, net, without taxes, EXW Ecublens (INCOTERMS 2000)\n"
                                 "Equipment (according to §6.2 & §9.1)\n"
                                 "Four (4) Presto machine CHF 900'000.-- (€ 600'000.--)\n"
                                 "Installation and setup of one line in Lyon\n(according to §9.2) CHF 20'000.-- (€ 13'000.--)\n"
                                 "10.3. OPTION\nExtra bucket chain\n(according to §6.2) CHF 7'000.-- (€ 4'700.--)\n"})
    assert [(l.kind, l.quantity, l.family) for l in s.lines] == [("machine", 4, "Presto"), ("service", None, None),
                                                                ("option", None, None)]
    assert "Four (4) Presto machine" in s.lines[0].text and s.lines[1].text.startswith("Installation and setup")


def test_the_line_carrying_the_amount_is_the_item_not_the_paragraph_above():
    s = prices.parse_section({4: "2. Angebot\nGerne unterbreiten wir Ihnen das folgende Angebot:\n"
                                 "1x Paloma 3R einschliesslich: (EUR 500 000.-) CHF 510 000.-\n"
                                 "• 3 Delta Roboter\n• 1 Tag für die Werksabnahme (FAT)\n"
                                 "Zusätzliche Tage sind auf Anfrage zum Preis von CHF\n1'100 pro Zusatztag möglich.\n"
                                 "2 Infeed belt conveyor (EUR 30 000.-) CHF 30 600.-\n"
                                 "Installation and commissioning (EUR 100 000.-) CHF 102 000.-\n"
                                 "Installation und Einrichtung erfolgen durch ACME SA, geplante Dauer 50 Mann Tage.\n"
                                 "Reisekosten, Unterkunft und Verpflegung im Preis\ninbegriffen.\n"
                                 "Verpackung, Transport und Versicherung (EUR 15 000.-) CHF 15 300.-\n"
                                 "PREIS, netto, ohne Steuern, (EUR 645 000.-) CHF 657 900.-"})
    assert [(l.text, l.kind) for l in s.lines[1:]] == [("2 Infeed belt conveyor", "machine"),
                                                       ("Installation and commissioning", "service"),
                                                       ("Verpackung, Transport und Versicherung", "service")]
    assert s.sum_ok is True


@pytest.mark.parametrize("page, texts, kinds", [
    ("1. Introduction\nRegarding your request, we propose an assessment of your line.\n"
     "The goal is to understand the situation.\n2. Proposal\nIn this quotation, we kindly offer you:\n"
     "Specialist intervention CHF 7'000.-\nThe intervention will be performed by ACME SA and\n"
     "is planned for 1 man-day with 1 travel included\n(additional travels will be charged). Travelling fees are included.\n"
     "One extra day on-site CHF 2'000.-\nTOTAL PRICE, net, without taxes, CHF 9'000.-",
     ["Specialist intervention", "One extra day on-site"], ["service", "service"]),
    ("1. Proposition\nPour donner suite à nos conversations, nous vous faisons parvenir cette offre.\n"
     "1 Globex Sidebot EUR 80,000.-\nSidebot complet avec un bâti en cantilever, robot Delta 4 axes\n(tracking)\n"
     "1 Smart Caméra avec support et licences EUR 9,000.-\nCaméra intelligente couleur 1920x1200.\n"
     "Support mécanique inclus.\n1 Emballage, Transport et Assurance EUR 3,000.-\nPRIX TOTAL, net, hors taxes, EUR 92,000.-",
     ["1 Globex Sidebot", "1 Smart Caméra avec support et licences", "1 Emballage, Transport et Assurance"],
     ["machine", "other", "service"]),
])
def test_text_of_the_previous_item_is_not_glued_to_the_next(page, texts, kinds):
    s = prices.parse_section({3: page})
    assert [l.text for l in s.lines] == texts and [l.kind for l in s.lines] == kinds and s.sum_ok is True


def test_options_after_the_total_on_the_next_page_are_read():
    s = prices.parse_section({3: "2x Presto DC each including : | (USD 700'000.-) | CHF 630'000.-\n"
                                 "1 Painted mild steel frame including electrical cabinet 1 Delta robot 1 HMI screen "
                                 "Preparation and tests for 2 recipes\n"
                                 "2x Erecting x 3 (include 2 formats) Format versatility: automatic former | "
                                 "(USD 600'000.-) | CHF 540'000.-\n"
                                 "Installation and commissioning Presto | (USD 100'000.-) | CHF 90'000.-\n"
                                 "TOTAL PRICE, net, without taxes, DAP Ecublens (INCOTERMS 2020) | (USD 1'400'000.-) | "
                                 "CHF 1'260'000.-\nThe following options are also possible:\n",
                              4: "2 Checkweigher High-speed accuracy | (USD 100'000.-) | CHF 90'000.-\n"
                                 "Installation and commissioning Casepacker | (USD 200'000.-) | CHF 180'000.-\n"
                                 "Only prices in Swiss Francs are valid (CHF). As indication, 1.00 USD = 0.9 CHF.\n"})
    assert s.pages == [3, 4] and len(s.lines) == 5 and s.sum_ok is True
    assert s.lines[1].text.startswith("2x Erecting x 3") and (s.lines[1].kind, s.lines[1].quantity) == ("machine", 2)
    assert [l.kind for l in s.lines[3:]] == ["option", "option"]
    s = prices.parse_section({4: "PREIS\n1x Paloma 3R CHF 300 000.-\nInstallation CHF 20 000.-\n"
                                 "PREIS, netto, ohne Steuern, CHF 320 000.-\nFCA Ecublens (INCOTERMS 2020)",
                              5: "ACME GmbH 3001234 - 00 18.07.2022\nDie folgenden Optionen sind ebenfalls möglich:\n"
                                 "2 Additional robot CHF 140 000.-\n2 Additional end effector CHF 22 000.-\n"
                                 "Nur Preise in Schweizer Franken (CHF) sind gültig."})
    assert s.pages == [4, 5] and [l.kind for l in s.lines] == ["machine", "service", "option", "option"]
    assert s.sum_ok is True


def test_items_on_the_page_before_the_total_page_are_read():
    pages = {2: "1. Proposal\nIn this quotation, we kindly offer you:\n"
                "1x Paloma 7R including : (EUR 700’000.-) CHF 680’000.-\n• 7 Delta robots\n"
                "1 Output conveyor (EUR 70’000.-) CHF 68’000.-\n",
             3: "Packaging (EUR 10’000.-) CHF 9’700.-\nTOTAL PRICE, net, without taxes, (EUR 780’000.-) CHF 757’700.-\n"
                "FCA Ecublens (INCOTERMS 2020)"}
    s = prices.parse_section(pages)
    assert s.pages == [2, 3] and [l.kind for l in s.lines] == ["machine", "machine", "service"] and s.sum_ok is True
    assert prices.price_pages({1: ITEMS + "TOTAL CHF 325,000.00", 2: "Spare parts CHF 5,000 and CHF 2,000"}) == [1]


def test_a_total_currency_no_line_carries_is_a_conversion_of_the_total():
    s = prices.parse_section({4: "PRICE\n1 Delfi 2R including: CHF 325,000.-\n1 Buffer conveyor of 10m CHF 29,000.-\n"
                                 "Packaging for ACME Equipment CHF 3,000.-\n"
                                 "TOTAL PRICE, net, without taxes, EUR 347,000.- CHF 357,000.-"})
    assert s.totals == {"EUR": 347000.0, "CHF": 357000.0} and s.sum_ok is True and s.lines[2].kind == "service"
    s = prices.parse_section({4: "PRICE\nPaloma 4R cell EUR 300,000.-\nTOTAL PRICE, net, without taxes, CHF 320,000.-"})
    assert s.sum_ok is False  # no line in the total's currency: nothing checked


def test_the_machine_named_after_a_generic_equipment_price_belongs_to_it():
    s = prices.parse_section({4: "2. Proposal\nEquipment\nIn this quotation, we kindly offer you:\n"
                                 " Equipment (EUR 244’000.-) CHF 320’000.-\n"
                                 "1 Paloma 2R Top Loader cell including each :\n Two Delta Robots\n One Controller\n"
                                 " Installation and setup 8 to 10% equipment price\n"
                                 " Packaging, transport and insurance Not included\n"
                                 "PRICE, net, without taxes,\nFCA Ecublens (INCOTERMS 2000) (EUR 244’000.-) CHF 320’000.-"})
    assert len(s.lines) == 1 and (s.lines[0].kind, s.lines[0].model) == ("machine", "Paloma 2R")
    assert "1 Paloma 2R Top Loader cell" in s.lines[0].text
    assert s.totals == {"CHF": 320000.0, "EUR": 244000.0} and s.sum_ok is True


@pytest.mark.parametrize("text, kind", [
    ("Equipement (selon §6.2)", "machine"), ("Equipment (according to §6.2 & §9.1)", "machine"),
    ("Lieferumfang", "machine"), ("2 Infeed belt conveyor", "machine"), ("1 case erector + closer", "machine"),
    ("Packaging", "service"), ("Packaging for ACME equipment", "service"), ("Transportation Cost", "service"),
    ("Monteur en externe, en régie", "service"), ("Montage dans votre usine", "service"),
    ("1 ACME Supply I&C in Lyon (1)", "service"), ("Specialist intervention", "service"),
    ("One extra day on-site", "service"), ("Special deduction 8 hours", "discount"),
    ("Packaging line with Paloma 3R", "machine"), ("Spare parts kit", "other"),
])
def test_classify_equipment_services_and_deductions(text, kind):
    assert prices.classify(text, {}) == kind


@pytest.mark.parametrize("pages, name, kind", [
    ({1: "Offre budgétaire n° 250001 Version 1"}, "x.pdf", "budget"),
    ({1: "ACME (Globex) – Price indication B-1112001 V1"}, "x.doc", "budget"),
    ({3: "Indication de prix pour une cellule Paloma"}, "x.pdf", "budget"),
    ({2: "Preisindikation"}, "x.pdf", "budget"), ({1: "Richtangebot"}, "x.pdf", "budget"),
    ({1: "Budget offer"}, "x.pdf", "budget"),
    ({1: "Offre", 2: "Pour les positions 4 et 5, la durée de travail est une estimation."}, "x.pdf", "firm"),
    ({1: "Offer", 2: "Prices in EUR are indicative only, at the rate of the day."}, "x.pdf", "firm"),
    ({1: "Offer", 4: "Budgetary offer"}, "x.pdf", "firm"),  # the wording counts on pages 1-3 only
    ({1: "Offer", 3: "1 Budget for flow wrapper CHF 400'000.-"}, "x.pdf", "firm"),  # page 3: a budget phrase only
    ({1: "Offer", 3: "This budgetary proposal is intended as a base for discussion."}, "x.pdf", "budget"),
])
def test_offer_type_wording(pages, name, kind):
    assert prices.offer_type(pages, name) == kind


def test_model_zero_amounts_and_day_rates_are_no_prices(fake_ollama):
    _model_says(fake_ollama, {"lines": [{"name": "Vision systems", "amounts": {"CHF": 0}},
                                        {"name": "Paloma 2R cell", "amounts": {"CHF": 1100}}], "totals": {"CHF": 0}})
    page = "cell equipment negotiation 0 indication days:1100 CHF/Day) 10% included"
    assert prices.parse_with_model(fake_ollama.url, "test-vision", {1: page}, timeout=10) is None
    cached = prices.PriceSection([prices.PriceLine(1, "Paloma 2R cell", "machine", None, {"CHF": 300000.0}, 1),
                                  prices.PriceLine(2, "Training", "service", None, {"CHF": 0.0}, 1)],
                                 {"CHF": 0.0}, [], [1], False)
    s = prices.clean_model(cached, {1: "Paloma 2R cell CHF 300,000.-\nTraining 0"})
    assert [l.text for l in s.lines] == ["Paloma 2R cell"] and s.totals == {} and s.sum_ok is None


def test_a_price_quoted_in_a_sentence_is_no_line():
    s = prices.parse_section({2: "PRICE\nTraining package CHF 4'000.-\nTravel package CHF 1'500.-\n"
                                 "TOTAL PRICE, net, without taxes, CHF 5'500.-\n"
                                 "The operator training is given during commissioning. A credit note of CHF 600.- , "
                                 "that is 8 hours, will be issued in favour of ACME with this order.\n"})
    assert [l.text for l in s.lines] == ["Training package", "Travel package"] and s.sum_ok is True


def test_an_option_word_deep_in_a_long_description_is_no_option():
    assert prices.classify("2x Erecting x 3 Format versatility: automatic former with an optional extended head", {}) \
        == "machine"
    assert prices.classify("Option: second infeed with its own conveyor and a vision camera on top", {}) == "option"


@pytest.mark.parametrize("pages, name, key, kind", [
    ({1: "Offre formelle F-0903001 V2\nCette offre remplace l'offre budgétaire n° 0902011 V1."}, "x.pdf", "0903001",
     "firm"),
    ({2: "Prix en baisse par rapport à l'offre budgétaire B-250001 V1."}, "x.pdf", "B-250099", "firm"),
    ({1: "Les équipements définis dans les offres budgétaires n° 250001 et 250002"}, "x.pdf", "B-250099", "firm"),
    ({1: "Offer", 3: "Prices for these products must be understood as budgetary."}, "x.pdf", None, "firm"),
    ({1: "Offre budgétaire n° 250001 Version 1"}, "x.pdf", "B-250001", "budget"),  # its own number
    ({1: "Budgetary Proposal #250001 Version 2"}, "x.pdf", "B-250001", "budget"),
    ({1: "Offre", 2: "Les prix sont une estimation sur la base des échantillons reçus."}, "x.pdf", None, "budget"),
    ({1: "Offer", 2: "Estimation of the investment: CHF 300'000 to CHF 350'000"}, "x.pdf", None, "budget"),
    ({1: "ACME - Offre estimation pour une cellule Paloma"}, "x.pdf", None, "budget"),
    ({1: "Offre formelle", 2: "Prix budgétaire de l'option vision"}, "ACME F-250001 V1.pdf", "F-250001", "firm"),
    ({1: "Offre budgétaire n° 250001"}, "ACME F-250001 V1.pdf", "F-250001", "budget"),  # the title beats F-
])
def test_offer_type_cited_budget_offers_estimations_and_formal_offers(pages, name, key, kind):
    assert prices.offer_type(pages, name, key) == kind


def test_offer_date_with_of_between_day_and_month():
    assert prices.offer_date({1: "Written by ACME, the 10th of February 2006"}) == ("2006-02-10", "10th of February 2006")


# --- fixes from the fresh 30-offer spot check (2026-10-25) ------------------------------------------------

def test_euro_written_as_a_word_before_the_number_is_eur():
    assert prices.amounts_in("Equipement : €uro 410'000.-- (CHF 500'000.--)") == [("EUR", 410000.0), ("CHF", 500000.0)]
    assert prices.amounts_in("Machine EURO 12'000.- / Euro 13'000.- / 14'000 €uros") == [
        ("EUR", 12000.0), ("EUR", 13000.0), ("EUR", 14000.0)]
    assert prices.amounts_in("Le prix en €URO n'a qu'une valeur indicative.") == []
    s = prices.parse_section({9: "10. PRIX\n10.1. Prix, net, hors taxe\nEquipement : €uro 410'000.-- (CHF 500'000.--)\n"
                                 "(selon §6.3)\nMise en service : €uro 40'000.-- (CHF 49'000.--)\n"
                                 "Le seul prix valable est celui en francs suisses (CHF)."})
    assert [l.amounts for l in s.lines] == [{"EUR": 410000.0, "CHF": 500000.0}, {"EUR": 40000.0, "CHF": 49000.0}]


@pytest.mark.parametrize("text", ["Zusätzliche Tage sind auf Anfrage zum Preis von CHF 1'100\npro Zusatztag möglich.",
                                  "Additional days are possible at CHF 1'100.- per additional day.",
                                  "Des jours additionnels au tarif de CHF 1'100.- par jour supplémentaire.",
                                  "Additional FAT days: EUR 1'000 / day"])
def test_day_rates_in_an_item_description_are_no_amounts(text):
    assert prices.amounts_in(text) == []


def test_a_day_rate_wrapped_in_an_item_description_is_no_line():
    s = prices.parse_section({3: "1. Angebot\n2 Paloma 4R einschließlich jeweils: CHF 900,000.-\n"
                                 "• 2 Tage für die Werksabnahme (FAT) vor dem Versand\n"
                                 "Zusätzliche Tage sind auf Anfrage zum Preis von CHF 1'100\npro Zusatztag möglich.\n"
                                 "1 Product Conveyor CHF 80,000.-\nPREIS, netto, ohne Steuern, CHF 980,000.-"})
    assert [l.amounts for l in s.lines] == [{"CHF": 900000.0}, {"CHF": 80000.0}] and s.sum_ok is True


def test_a_numbered_price_heading_cuts_the_footnote_above_it():
    page = ("7. PRIX\n7.1. Prix des cellules Presto, net, hors taxe\n"
            "Deux (2) cellules Presto €uro 500'000.-- (CHF 600'000.--)\n"
            "Le seul prix valable est celui en francs suisses (CHF). Le prix en €URO n'a qu'une valeur indicative.\n"
            "7.2. Prix du système de convoyeurs, net, hors taxe\n"
            "Système de convoyeur ACME\n€uro 100'000.-- (CHF 120'000.--)\nTransport et Installation inclus\n"
            "Le seul prix valable est celui en francs suisses (CHF). Le prix en €URO n'a qu'une valeur indicative.\n"
            "7.3. Prix de l'étuyeuse, net, hors taxe\nEtuyeuse continue Globex ET100 €uro 200'000.-- (CHF 240'000.--)\n")
    s = prices.parse_section({9: page})
    assert [l.text for l in s.lines] == ["Deux (2) cellules Presto", "Système de convoyeur ACME",
                                         "Etuyeuse continue Globex ET100"]
    assert [l.kind for l in s.lines] == ["machine", "machine", "machine"]
    assert s.lines[2].amounts == {"EUR": 200000.0, "CHF": 240000.0}


@pytest.mark.parametrize("text, kind", [
    ("Engineering für Flowpack Integration", "service"), ("Equipment Engineering for the ACME solution", "service"),
    ("Project Management by Globex", "service"), ("Gestion de projet", "service"), ("Ingénierie mécanique", "service"),
    ("Encartonneuse Globex 500", "machine"), ("Cartoner ACME CT-2", "machine"), ("Kartonierer", "machine"),
])
def test_classify_engineering_and_cartoners(text, kind):
    assert prices.classify(text, {"CHF": 10000.0}) == kind


def test_a_tiered_price_table_gives_a_line_per_tier():
    page = ("1- FRAMEWORK CONTRACT\n\nAll prices are in CHF * | < 10 machines | ≥ 10 machines | ≥ 20 machines\n"
            "| 0% | 10% | 20%\nACME 2R | 170'000 | 153'000 | 136'000\nACME 1R | 130'000 | 117'000 | 104'000\n\n"
            "COMMERCIAL CONDITIONS\nDelivery time for 1 machine = 8 weeks")
    s = prices.parse_section({2: page})
    assert [(l.text, l.amounts) for l in s.lines] == [
        ("ACME 2R (< 10 machines 0%)", {"CHF": 170000.0}), ("ACME 2R (≥ 10 machines 10%)", {"CHF": 153000.0}),
        ("ACME 2R (≥ 20 machines 20%)", {"CHF": 136000.0}), ("ACME 1R (< 10 machines 0%)", {"CHF": 130000.0}),
        ("ACME 1R (≥ 10 machines 10%)", {"CHF": 117000.0}), ("ACME 1R (≥ 20 machines 20%)", {"CHF": 104000.0})]
    assert s.totals == {} and s.sum_ok is None


def test_a_cost_sheet_with_margins_is_no_tiered_table():
    page = "Item | Cost CHF | Margin | Price\n2x Paloma 4R | 300'000 | 30% | 390'000\n"
    assert "(" not in prices._tables(page)


def test_a_model_total_not_printed_is_dropped():  # R40
    cached = prices.PriceSection([prices.PriceLine(1, "Delfi 4R", "machine", None, {"EUR": 800000.0}, 1),
                                  prices.PriceLine(2, "Mise en service", "service", None, {"EUR": 40000.0}, 1)],
                                 {"EUR": 840000.0}, [], [1], False)
    s = prices.clean_model(cached, {1: "Delfi 4R EUR 800'000.-\nMise en service EUR 40'000.-"})
    assert s.totals == {} and s.sum_ok is None


@pytest.mark.parametrize("page", [
    "Nous nous référons à votre visite dans nos locaux du 21.07.2025 et vous remercions.\nRédigée par: ACME, le 21 août 2025",
    "With reference to our meeting of 21.07.2025 we are pleased to offer.\nWritten by Globex, 21 August 2025",
    "Bezugnehmend auf Ihre Anfrage vom 21.07.2025 unterbreiten wir.\nDatum: 21.08.2025",
])
def test_a_visit_meeting_or_request_date_is_not_the_offer_date(page):
    assert prices.offer_date({1: page})[0] == "2025-08-21"


@pytest.mark.parametrize("pages, name, kind", [
    ({1: "Offer", 9: "Validity of the budgetary offer | This budgetary proposal is intended as a base for discussion"},
     "x.docx", "budget"),
    ({1: "Offre", 12: "Validité de l'offre | Cette offre budgétaire vous est fournie afin de servir de base"}, "x.pdf",
     "budget"),
    ({1: "Angebot", 14: "Gültigkeit des Budgetangebots | Dieses Budgetangebot wird Ihnen als Diskussionsgrundlage"},
     "x.pdf", "budget"),
    ({1: "Angebot", 11: "Es ist als Budget zu verstehen und für ACME nicht bindend."}, "x.pdf", "budget"),
    ({1: "Unverbindliches Angebot ACME 3001234-02"}, "x.pdf", "budget"),
    ({1: "ACME – Preisangabe – 3001234 V1 Datum : 23.05.2021"}, "x.doc", "budget"),
    ({1: "Angebot"}, "Globex Preisangabe 3001234 V1.pdf", "budget"),
    ({1: "Oferta presupuestaria Nº 3001234 Versión 1"}, "x.pdf", "budget"),
    ({1: "Offerta di budget per ACME"}, "x.pdf", "budget"),
    ({1: "Offer", 3: "We submit a price together with this description as a base for this discussion, and is therefore "
                     "not binding."}, "x.pdf", "budget"),
    ({1: "Offre", 10: "This budget price is subject to receipt and approval of product samples."}, "x.pdf", "budget"),
    # guards: firm offers
    ({1: "Angebot", 7: "Die Preisangabe in €URO ist nur informativ, zum Tageskurs."}, "x.pdf", "firm"),
    ({1: "Offer", 8: "Unless otherwise agreed upon, brochures and catalogues are not binding."}, "x.pdf", "firm"),
    ({1: "Angebot", 9: "Lieferzeit: unverbindlich, nach Klärung aller Einzelheiten."}, "x.pdf", "firm"),
    ({1: "Offer", 9: "This offer replaces our budgetary offer 3001233 of last year."}, "x.pdf", "firm"),
    ({1: "Offer", 9: "Following this budgetary proposal 3001233 you ordered"}, "x.pdf", "firm"),
    ({1: "Offre formelle", 9: "Cette offre budgétaire vous est fournie"}, "ACME F-250001 V1.pdf", "firm"),  # R39
])
def test_offer_type_own_budget_wording_on_any_page(pages, name, kind):
    assert prices.offer_type(pages, name, "3001234") == kind


def test_euros_after_a_number_in_a_sentence_is_no_amount():
    assert prices.amounts_in("• 1 First maintenance kit for an amount of 4'000.- Euros") == []


def test_angle_bullets_are_description_lines_of_a_delivered_total():
    page = ("- One (1) Presto cell, including CHF 270'000.- (€ 240'000.-)\n> One (1) Delta robot with 4 axis\n"
            "> Engineering for the integration of the outgoing flow\n> One (1) HMI\n"
            "- Installation and setup Not Included (8-10% of equipment price)\n"
            "Budget Price, FCA Ecublens CHF 270'000.- (€ 240'000.-)\n")
    s = prices.parse_section({1: page})
    assert len(s.lines) == 1 and s.totals == {"CHF": 270000.0, "EUR": 240000.0} and s.sum_ok is True

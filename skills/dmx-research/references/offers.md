# Offers world (`dmx-offers`)

For sales and management only. The connector is installed only for people who may read it; never
copy its content into anything shared with others, and do not mix it into answers for the other
worlds' audiences. This file describes the world and holds no offer data.

## What is in the index

Call `world_map` (this connector) for what this world holds, where, and what it lacks. Two parts:

- **The documents**: every offer sent to a client since the 1999 archive, won or not, with its
  revisions and variants, technical solutions, specifications, Outlook mails (subject, from, date,
  body; attachments by name only), layouts, visit reports, partner offers, market studies, NDAs,
  sales conditions. French, English, German. `search` works as in the other worlds.
- **The offer register**: one row per **priced line** of every offer revision, read from the offer
  documents: line text as written, kind (machine, option, service, discount, other), machine
  family and model, quantity, amounts **per currency exactly as printed**, source path and page,
  confidence. Per offer: client, country, dates, firm or budget, **status** and its evidence, and
  the user's corrections. Prices, counts and histories come from the register, because `search`
  returns only the best 10-20 excerpts and would silently miss offers.

## The four register tools

| Question | Tool |
|---|---|
| a price chart or table ("Paloma 3R prices over the years") | `offer_lines machine="Paloma 3R"` |
| the history of a client, a list of offers | `list_offers client="acme"` |
| one offer in full (lines, rates, status evidence, documents) | `offer_card offer="3001234"` |
| counts ("offers per year, per country") | `offer_stats group_by="year,country"` |

Common filters: `client=` and `country=` (part of the name, comma = or), `machine=` (family or
model), `status=`, `year_from=`, `year_to=`. `offer_lines` also has `kind=`, `offer_type=`,
`category=`, `currency=` and `revisions=`; `offer_stats` has `offer_type=` and `category=`;
`list_offers` has `text=`.

- **Defaults of `offer_lines`**: currency CHF, the last revision of each offer (every variant of it
  is one point), `offer_type="firm"`, `category="client,archive"`. Budget and partner offers are
  counted in the coverage line and added with `offer_type="firm,budget"` or `category=""`.
  `revisions="all"` gives every revision (the history of one offer or client).
- **Kinds of line**: each row has a `kind` column (machine, option, service, discount, other). With
  `machine=` and no `kind=`, only machine lines are returned (the coverage line says
  "kind machine (default with machine=)"): a training or installation line naming the machine is not
  a machine price. Ask `kind="service"` for those, `kind=""` for every kind.
- **Offers that could not be checked**: with `machine=`, offers without readable prices cannot be
  told to quote that machine or not; the coverage line counts them and names them ("Not checked for
  this machine: ..."). Say so under the chart.
- **Currencies are never converted**, not even with a rate printed in the offer. One currency per
  chart; an offer without it is left out and named. Say it when the user asks for EUR or USD:
  `currency="EUR"` shows only the offers that printed EUR.
- **Every row carries path and page**: cite them, and check a surprising value on the page with
  `view_page`.

## Filters of `search`

`client=`, `country=`, `offer=` (number), `revision=`, `partner=`, `machine=`, `status=`,
`year_from=` / `year_to=`; `doc_type="offer"` (also specification, technical_solution, mail,
layout, visit_report, history, proforma, market_study, nda, sales_info, other);
`category="client"` (also archive, partner, unknown_client, market_study, nda, sales_info).
Facet filters leave out documents whose facet is unknown: search again without them when thin.
For an exact offer number use the number in "double quotes" with `mode="keyword"`.

## Method for price, history and count questions

1. **Register first**: `offer_lines` / `list_offers` / `offer_stats` with the filters of the question.
   Never answer a price trend or a count from `search`.
2. **Read the coverage line** before anything else: offers matched, with readable prices, left out
   and why. If many are left out (no CHF amount, low confidence, no date), say so and consider
   `revisions="all"`, another currency or a wider scope - do not hide it.
3. **Show the scope of each point**: the `line` column says what a price includes (grippers, vision,
   conveyors, installation). Group or flag lines of a different scope; never compare totals of
   different scopes as if they were the same ("Paloma 3R" vs "Paloma 3R with conveyors and vision").
4. **Won and not won**: mark them differently (filled and hollow points, a column). `no_order_found`
   is not "lost": the project may not be indexed or may quote another revision. `won_unconfirmed`
   is not "won": the number is only in the text of a project of another client with another machine
   (it may compare offers); show it as a third kind of point and check the evidence in `offer_card`.
   A won offer's final negotiated price is in the Projects world (`0_Vente` of the linked project),
   not here. Statuses: won, won_unconfirmed, no_order_found, open, unknown, lost, cancelled.
5. **Then the documents for the points that matter**: `offer_card` for one offer, `read_document`
   around the price page, `search` with `offer=` for mails and specifications that explain a price.
   Old archive offers are OCR text ("(OCR)"): check amounts with `view_page`.
6. **Always state the coverage line under a chart or a count**, with the currency, the revisions
   (last or all) and the scope (firm offers of client and archive folders unless widened).
   Cite path and page for every value you quote.

## Pitfalls

- Variants of one revision are separate points, never summed. Translations are one offer.
- Budget offers ("Richtpreis", "budget", "indicative") and partner offers (prices may be to the
  partner) are not in default price trends.
- A machine name the register does not know is kept as written: the same model can appear under an
  old name. Search the line text (`list_offers text="..."`) when `machine=` finds too little.
- Confidence `low` or `medium`: check the page. The user's corrections (shown in `offer_card`) beat
  the documents, the documents beat anything else.
- The register is rebuilt by the offers step after the index: if a tool says it is empty, say so and stop.

## Examples

« Fais-moi un graphique des prix des Paloma 3R au fil des années »
1. `offer_lines machine="Paloma 3R"`; read the coverage line.
2. Chart date against unit price (or amount), filled for won and hollow for not won; show the line
   text of the outliers; state "N offers matched, M with CHF prices, K left out" under the chart.
3. Open the 2-3 surprising points with `view_page` (path and page of the row) and `offer_card`.

« Quel est l'historique des offres faites au client ACME ? »
1. `list_offers client="acme"` (newest first), then `offer_card` for the offers that matter.
2. Complete with `search "negotiation" client="acme" doc_type="mail"` for the discussion.

« Nombre d'offres par année et par pays »
1. `offer_stats group_by="year,country"` (every offer counts: firm, budget, every category, unless
   filtered); chart it with the coverage line.

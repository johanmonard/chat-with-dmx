# Projects world (`dmx-projects`, formerly `dmx-docs`)

## What is in the index

Call `world_map` (this connector) for what this world holds, where, and what it lacks; it is kept
up to date by hand and with live counts. The essentials for searching:

- One folder per project, code-named (THOR, YAKUMA, ANGE, BOULE, MANOLO, INIESTA...). Current
  projects sit directly under `RMA_PROJETS`; finished ones may be grouped in **collections** such
  as `2_Hors_Garantie` (out of warranty). **What is indexed changes over time**: check with
  `list_projects` (or `index_status`) which projects and collections are in the index before
  concluding anything from an absence of results. If a project is not listed, say "not in the
  index" - never invent another reason.
- Every document carries **facets**, shown under each hit and usable as filters:
  `project` (with its sub-project when a project groups several machines, e.g.
  "MOUSQUETAIRES / 1_Athos"), `collection`, `section` (Vente, Finances, Electrique, Mecanique, Soft,
  Gestion, Rapports_Tests, Photos_Videos, Documentation, SAV, Cloture) and `doc_type` (offre,
  commande, cahier_des_charges, facture, modification, schema_electrique, layout, plan,
  nomenclature, pieces_detachees, manuel, doc_fournisseur, fat, sat, reception, mise_en_service,
  open_points, tests, suivi, planning, transport, sav, doc_electrique, doc_mecanique, soft).
  "type X" = from the template folder (reliable); "type X (from name)" = guessed from the file
  name; "(from section)" = only the section is known; "type unknown" = not classified.

## Vocabulary

- gripping: préhenseur, ventouse(s), pince, doigts souples, préhension, tilt, vide/venturi — gripper, suction cup
- speed: cadence, débit, produits par minute (ppm), coups/min, rattrapage — throughput, rate
- acceptance: FAT (réception usine), SAT (réception site), MES / mise en service, protocole de réception, OPL / open points list, réserves
- machine parts: convoyeur (à bande, de boîtes), collateur, infeed, dépileur/denester, formeuse, fermeuse, encolleuse, flowpack, étui, carton, barquette, blister, francomat, XTS, vision/caméra
- robots/machines: Paloma (nR), Presto, Hector, Delfi, Astor, Nestor — the number before R is the number of robots
- documents: offre, cahier des charges (CDC), spécification, note de modification, rapport d'intervention, compte rendu, manuel, schéma électrique, nomenclature/BOM, pièces détachées

## Filters and project tools

- **A project in the question?** Check its exact name with `list_projects name="THOR"` (it also
  lists sub-projects), then filter with `project="THOR"` (several: `project="THOR,YAKUMA"`). Many
  project names are ordinary words (ANGE, BOULE, LEON, VENUS): without the filter a keyword
  search mixes the project with the word.
- **One project?** Start with `project_card project="THOR"`: client (group, industry, site,
  country), every machine delivered (serial number, model, year, robots, controller, camera,
  maintenance contract, order date), order numbers, document counts by type, the newest offer /
  order / specification / FAT / SAT, and the client's other projects. Then search for details.
- **Client, country or period?** `list_projects client="<client or group>"`,
  `list_projects country="United Kingdom" year_from=2015` (combinable with `machine=`) give the
  complete list - including register machines whose documents are not indexed - and searches
  take `client=` / `country=`. Client, machines and years come from the company's machine
  register (installed base); a project missing from it is not found by these filters: fall back
  to `find_files` or a search without filter, and say so. **When the register disagrees with the
  documents, the documents win** (the card marks these lines with ⚠): report the disagreement.
- **Machine type?** Each hit shows the machines found in the project's documents, e.g.
  `project THOR [Paloma 7R (65 docs)]`. Filter with `machine="Paloma"` (family: Paloma, Presto,
  Hector, Delfi, Astor, Nestor, FeedPlacer) or `machine="Paloma 4R"` (model).
- **Kind of document?** `doc_type="fat,reception"` (FAT findings), `doc_type="cahier_des_charges"`
  (client requirement), `doc_type="offre"` (price/scope offered), `doc_type="mise_en_service,sat"`
  (commissioning), `doc_type="manuel"` (how the machine works); or `section="SAV"` for an area.
- Recent only: `modified_after="2025"`. Out-of-warranty projects: `collection="2_Hors_Garantie"`.
- Pictures (photos in FAT reports, schemas...): the project world is not described by the vision
  model yet - `pictures=true` finds nothing there for now; use `view_page` on the pages found by text.

## Aggregation ("which projects ...")

Search results are never exhaustive. Get the COMPLETE candidate list with `list_projects`
(e.g. `list_projects machine="Paloma"`), run the broad searches with the same filter
(`search "..." machine="Paloma"`), then check the remaining candidates one by one with
`project=`; say how many projects were checked out of how many candidates.

## Example

Question: « Quels problèmes ont été relevés lors de la FAT de THOR, et sont-ils résolus ? »

1. Reformulate: `list_projects name="THOR"`; `search "problèmes relevés lors de la FAT" project="THOR" doc_type="fat,reception"`;
   `search "points ouverts OPL réserves" project="THOR"`; `search "levée des réserves mise en service sur site" project="THOR" doc_type="sat,mise_en_service,suivi"`.
2. Retrieve → FAT protocol (type fat) strong; an open-points list (type open_points) medium.
3. Evaluate → FAT problems covered; resolution not yet. Read the FAT protocol around the hits.
4. Retry → same query without `doc_type`; `list_folder` on the project's report folder → later report found, read.
5. Synthesize → FAT issues with page citations, status of each from the later report, what remains unconfirmed.

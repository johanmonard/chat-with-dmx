# Projects - map

## What this world is for
Everything about each client project, from the first offer to closure and after-sales: one
folder per project (code names: THOR, LEON, ANGE...), PDF and Word, mostly French, also English,
German, Spanish. Current projects use the numbered template (0_Vente ... 9_SAV); older ones (most
of `2_Hors_Garantie`) use named folders (Cahier des charges, Electrique, Mécanique, Gestion,
Rapports, Documentation, SAV, Clôture). Use it for what was offered, agreed, built, measured,
delivered and fixed for a given client or machine.

## Kinds of information
| Kind | What it is | Where | Filter | Answers questions like |
|---|---|---|---|---|
| Offers (reference offers) | formal offers and quotations sent to clients, with scope, options and price | `0_Vente\00_Offres`, old: `Cahier des charges\Offre` | `doc_type="offre"` + `machine=` | "find offers for a Paloma 4R to reuse for a new one" |
| Final price, negotiation | price summaries, signed price sheets | `0_Vente\01_Prix_final` | `folder=` | "what was the final price structure" |
| Orders | purchase orders, order confirmations, additional orders | `0_Vente\02_Commandes`, `10_Commandes complémentaires` | `doc_type="commande"` | "what did the client finally order" |
| Specifications | client specifications, line descriptions, validation form (signed scope: products, cadence, formats) | `0_Vente\03_Cahier_des_charges`, `04_Formulaire_validation`, old: `Cahier des charges` | `doc_type="cahier_des_charges"` | "what cadence/products were agreed" |
| Third-party equipment | offers, orders, specifications (CDC) and manuals of bought-in equipment: conveyors, case packers, printers, vision... | `0_Vente\06_Materiel_Tiers` (purchase), `3_Mecanique\37_Materiel_Tiers`, `2_Electrique\27_Materiel_Tiers`, `8_Documentation\89_Third_Party`, `85_Received_DOC` | `doc_type="doc_fournisseur"` | "which conveyor supplier, what was specified to them" |
| Design and development | electrical and mechanical design: open points, studies, calculations, product and tray studies, component datasheets | `2_Electrique\20_Conception`, `3_Mecanique\30_Conception`, old: `Developpement mec`, `Mécanique\Calculs` | `folder=` (doc_type often doc_electrique / doc_mecanique) | "how was the buffer designed, which tests were made" |
| Modifications | modification notes after design or delivery | `21_Modifications`, `31_Modifications` | `doc_type="modification"` | "what was changed after FAT" |
| Risk analysis, safety | risk assessments, safety checklists, CE | `3_Mecanique\32_Analyse_Risques`, `2_Electrique\28_Safety`, old: `Analyse sécurité` | `doc_type="securite"` | "risk analysis of the machine" |
| Electrical schematics | schematics (long, mostly labels) | `2_Electrique\23_Schemas`, old: `Electrique\Schema` | `doc_type="schema_electrique"` | "schematic of the cabinet" |
| Layouts and drawings | layouts, implantation, mechanical drawings | `0_Vente\05_Layouts`, `3_Mecanique\33_Layouts`, `34_Dessins`, `Documentation\Chapitre 7` | `doc_type="layout,plan"` | "line layout, footprint" |
| Software | project software specification (SpecSoft), software protocols, telemaintenance, backups | `4_Soft\42_Documents`, `40_Applications`, `43_Backups`, old: `Soft\Documents` | `section="Soft"` | "software functions of this machine" |
| Follow-up, planning | meeting minutes, follow-up, planning | `5_Gestion\51_Suivi`, `52_Planning` | `doc_type="suivi,planning"` | "what was decided in the meetings" |
| Release before delivery | internal release, exhaustive safety checklists | `5_Gestion\53_Liberation_avant_livraison` | `doc_type="liberation"` | "was the machine released, which checks" |
| FAT / SAT / acceptance | acceptance protocols with findings and reserves | `5_Gestion\56_Acceptation_machine`, old: `Gestion\FAT`, `Gestion\SAT` | `doc_type="fat,sat,reception"` | "FAT findings, measured cadence" |
| Commissioning | commissioning (MES) reports, intervention reports | `5_Gestion\55_MES`, `6_Rapports_Tests\61_MES` | `doc_type="mise_en_service"` | "what happened on site" |
| Tests, open points | internal tests, noise measurements, open points lists | `6_Rapports_Tests\60_Tests_Internes`, `604_Open_points_lists` | `doc_type="tests,open_points"` | "test results, remaining points" |
| Transport, supply chain | packing lists, shipping | `5_Gestion\54_Transport`, `58_Supply_Chain` | `doc_type="transport"` | "how was it shipped" |
| Project manual | the machine's customised manual (FR/EN/DE), drawings, spare-part drawings, BOM | `8_Documentation\80_CD Doc Project`, `80_USB stick Doc Project`, `84_Spare_Parts`, `86_BOM_Internal_Use`, old: `Documentation\CD Doc Project` | `doc_type="manuel,pieces_detachees"` | "how does THIS machine work, its spare parts" |
| After-sales | interventions, modernisations, upgrades (e.g. Gemini migration) | `9_SAV`, old: `SAV\Modernisation` | `section="SAV"` | "what was done after warranty" |
| Closure | closure report, warranty confirmation, satisfaction survey | `5_Gestion\57_Cloture`, old: `Clôture` | `section="Cloture"` | "how did the project end" |
| Invoices | invoices, bank guarantees | `1_Finances\11_Facturation`, `10_Garanties_bancaires` | `doc_type="facture"` | "invoicing milestones" |

## Not in this world - look there
- The STANDARD (generic) machine manual -> Documentation world. A project manual here may differ from it.
- Product claims, sales decks, trainings, brochures, photos for slides, offer templates -> Marketing world.
- Standard software and electrical documentation do not exist in any world yet: only each project's own
  (`4_Soft`, `2_Electrique`, `8_Documentation`). Say so when asked for the standard version.
- Excel files (costing, spare-part lists, register), CAD and videos are not indexed: browse the folder
  with `list_folder` and open the file with `open_document`.
- Machines of the register without any document folder: `list_projects` shows them, searches cannot.

## Pitfalls
- Old projects (named folders) often have doc_type unknown or guessed: when a filtered search is thin,
  drop `doc_type=` and use `folder=` or a plain search with `project=`.
- Project names are often ordinary words (ANGE, BOULE, LEON): always filter with `project=`.
- Manuals are duplicated (`80_CD Doc Project`, `80_USB stick`): cite one, do not count twice.
- Scanned documents are OCR text ("(OCR)" hits): check figures with `view_page`.

# Marketing - map

## What this world is for
What the company says and shows about its products: brochures, datasheets, sales and company
presentations, sales trainings, offer templates, exhibitions, competition, press articles, photos
and graphics. PDF, Word, PowerPoint (read slide by slide) and picture files, mostly English and
French, also German. Use it for product arguments, technical claims, training material and
pictures for slides - not for what a client's machine really did.

## Kinds of information
| Kind | What it is | Where | Filter | Answers questions like |
|---|---|---|---|---|
| Sales trainings | numbered training chapters for the sales team | `Presentations\Training Sales\<chapter>` | `category="Presentations"` + `folder=` | see the chapter rows below |
| - basics | Delta robot vs others, applications, product flows, base machines, DIN 8743 efficiency | `Training Sales\010 Généralités` | | "why a Delta robot, which product flows" |
| - D3 platform | D3 modular platform: introduction, real cases, news | `210 Product - D3 Platform`, `212 D3 Training`, `Presentations\D3 Sales Info` | | "what is the D3 platform" |
| - project Demain | market research and concept work for a new machine (XTS, change parts) | `220 Product - Demain` | | "market studies, Demain concept" |
| - Paloma SQ | Paloma Square presentations | `260 Product - Paloma SQ` | | "Paloma Square arguments" |
| - end-effectors | gripper types with photos: suction, Coanda, bell, needles, Monopick, Reflex, tilt & rotation, comparisons | `310 Technology - End-effectors` | `pictures=true` | "which gripper for which product" |
| - robots, HMI, vision | GD33 robot, Gemini HMI, vision | `320 Technology - GD33`, `330 Technology - GEMINI`, `340 Technology - Vision` | | "robot range, HMI features" |
| - efficiency, speed | OEE / DIN 8743, TCO; Delta robot rate estimation | `410 Application - Efficiency`, `420 Application - Speed Calculation` | | "how to compute line efficiency or robot rate" |
| - coflow vs counterflow | co-flow / counter-flow principles; mostly screenshots of a supervisor simulator | `440 Application - Coflow vs Counterflow` | `pictures=true` | "coflow or counterflow for this case" |
| - denesting, UX | denester application (incl. a supplier's quotations), user experience | `450 Application - Denesting`, `460 Application - UX` | | "denesting solutions" |
| - agent training | full training course for sales agents (machines, Paloma, Delfi, robot training program) | `710 Training - PACD Formation Agents` | | "training material for agents" |
| Company presentations | company and group decks, templates, carve-out mailing | `Présentation Demaurex`, `Présentation Rotzinger`, `Group Rotzinger templates` | `category="Presentations"` | "company presentation, group template" |
| Product decks and photos | Paloma washdown (WD) | `Presentations\Paloma WD` | | "washdown arguments, photos" |
| Offer templates | standard 3-page offers per machine and configuration (Paloma D3 2R-6R, TL/FP, Presto, Delfi), old quotations, machine photos for offers | `Brochures\Marketing\Quotations` (`offre_3_pages_a_valider`, `Images machines et dessins pour offres`) | `folder=` | "offer template for a Paloma 3R" |
| Brochures | product and company brochures, several versions | `Brochures` | `category="Brochures"` | "brochure text, key figures" |
| Datasheets | technical datasheets per machine (EN/FR/DE), GD robots | `Datasheets` | `category="Datasheets"` | "advertised cadence, dimensions" |
| Exhibitions | stands, posters, material of CFIA Rennes 2019-2022 and IBA Munich 2018 | `Exhibition` | `category="Exhibition"` | "what did we show at CFIA" |
| Competition | competitor documentation (mostly Cavanna, also others) | `Competition` | `category="Competition"` | "what does the competitor offer" |
| Press | articles and press releases by country, 2014-2016 | `Publications` | `category="Publications"` | "press coverage" |
| Photos and graphics | photo sets per machine (Astor, Delfi, Paloma...), visual identity, website mockups | `Pictures`, `Graphics` | `category="Pictures,Graphics"`, `pictures=true` | "a photo of a Delfi for a slide" |
| Video stills | photos taken from demo and acceptance videos, per project | `Videos\Video_Internal Use_Demos` | `category="Videos"` | "pictures of a past demo" |

## Not in this world - look there
- What a client's machine actually reached (FAT/SAT, commissioning) and real offers sent to clients -> Projects world.
- How to operate, maintain or troubleshoot a machine -> Documentation world (standard manual).
- The videos themselves, Excel price lists and design files (.ai, .psd) are not indexed; folder `CSI` has no indexed file.
- Standard software and electrical documentation: in no world yet.

## Pitfalls
- Marketing figures are promotional: quote them as claims ("according to the datasheet").
- Several versions of the same brochure or deck: prefer the newest (date in the name or modification date) and say which.
- Folders under `Training Sales` also hold archives and supplier documents (e.g. denester quotations): not Demaurex material.
- `440 Coflow vs Counterflow` holds hundreds of small simulator screenshots: search text first, pictures second.

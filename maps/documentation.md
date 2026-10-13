# Documentation - map

## What this world is for
The STANDARD machine manual (generic, not for one client): Paloma, Presto, Hector, Delfi, Astor,
Nestor, conveyors, vision, Gemini HMI. One Markdown file per section and language (FR, DE, EN),
read as one page, organised in chapters 0000-7000; a few PDF and Word procedures. Use it for how
the standard machine works, is operated, kept safe and maintained. It covers mainly the
MECHANICAL and functional side.

## Kinds of information
| Kind | What it is | Where | Filter | Answers questions like |
|---|---|---|---|---|
| Machine overview | preface, machine composition | `1000_Introduction\1100`, `1200` | `category="1000_Introduction"` | "what modules make up a Paloma" |
| Module descriptions | how each module works (robot cell, conveyors, Presto...), with pictures | `1300_Description du fonctionnement des modules` | `category="1000_Introduction"` | "how does the product flow work" |
| Technical data | technical data per machine | `1400_Données Techniques` | | "standard dimensions, consumption" |
| Transport, installation | transport, installation, connections | `1500_Transport, Installation et connexions` | | "how to install and connect" |
| Safety | standard safety, former + gluer safety | `2000_Sécurité` (`2100`, `2118`) | `category="2000_Sécurité"` | "safety rules, guards" |
| Functional elements | the machine's elements and controls, user manual | `3000_Instruction de commande\3100`, `3200` | `category="3000_Instruction de commande"` | "what does this button/element do" |
| Troubleshooting | fault analysis | `3300_Analyse des pannes` | | "the robot stops, what to check" |
| HMI | Gemini operator interface: introduction, machine parameters, errors and warnings | `4000_Interface opérateur` (`4100`, `4200`, `4300`) | `category="4000_Interface opérateur"` | "what does error X mean, which parameter" |
| Functional descriptions | detailed functions, tool change (format change), Gemini installation manual | `5000_Descriptions fonctionnelles` (`5100`, `5200`, `5300`) | `category="5000_Descriptions fonctionnelles"` | "how to change format" |
| Maintenance | maintenance plan and procedures (incl. pneumatics), replacement of mechanical and electrical parts | `6000_Entretien` (`6200`, `6216`, `6300`, `6400`) | `category="6000_Entretien"` | "how to replace the gearbox, intervals" |
| Appendix | spare parts, pneumatic schema, safety data sheet, CE declaration | `7000_Appendice` (`7200`, `7400`, `7500`, `7600`) | `category="7000_Appendice"` | "CE declaration, pneumatic schema" |
| Pictures | schemas, photos and HMI screenshots of the manual, described | all chapters | `pictures=true` | "a picture of the gripper" |

## Not in this world - look there
- **Standard software documentation and standard electrical documentation are missing.**
  `7300_Schémas électriques` only says that the schematics are in the machine's cabinet, and
  `7100_Description de l'application` is a template with placeholders. Say that the standard version
  does not exist in the documents, then look in the Projects world for a project's own:
  `4_Soft\42_Documents` (software specification SpecSoft), `2_Electrique\23_Schemas` (schematics),
  `8_Documentation` (the project manual with its appendices).
- A client's customised manual, spare parts of a delivered machine -> Projects `8_Documentation`, `84_Spare_Parts`.
- Product arguments and datasheet figures -> Marketing world.
- Spare-part lists in Excel are not indexed.

## Pitfalls
- Every section exists up to three times (FR, DE, EN): filter with `language=` and cite one.
- 7100 and 7300 are templates or notes, not content: do not present them as the standard documentation.
- Old versions are excluded from the index: the sections shown are the current ones.

# Documentation world (`dmx-documentation`)

## What is in the index

- The **standard** machine manual of Demaurex (`O:\ASA\Documentation\DOC_Machines\Source`):
  Paloma, Presto, Hector, Delfi, Astor, Nestor, conveyors, vision, the Gemini HMI. Mostly Markdown
  sections (one file = one section, read as one page), plus some PDF and Word procedures.
- Each hit shows its **category** = the manual chapter, and its **language**:
  `0000_Page de garde`, `1000_Introduction`, `2000_Sécurité`, `3000_Instruction de commande`,
  `4000_Interface opérateur`, `5000_Descriptions fonctionnelles`, `6000_Entretien`,
  `7000_Appendice`; languages FR, DE, EN (most sections exist in several languages).
- Old versions are not indexed. Each project's own, customised manual is in the Projects world
  (`8_Documentation` of the project): this manual is the generic reference.

## Filters

- `language="fr"` (or `"de"`, `"en"`): the user's language; French by default. Repeat without it
  when a section is missing in that language.
- `category="6000_Entretien"` (maintenance), `category="2000_Sécurité"` (safety),
  `category="4000_Interface opérateur"` (HMI screens), `category="5000_Descriptions fonctionnelles"`
  (how each module works), `category="1000_Introduction"` (machine overview, technical data).
- `file_type="md"` for manual sections, `"pdf"` / `"docx"` for procedures.
- `pictures=true` finds the manual's pictures (screenshots of the HMI, photos of parts, schemas).

## Reading and showing

- Sections contain picture markers like `[Image 3: Dessus de la Paloma]`; `read_document` also shows
  each picture's description after the text. Show a picture with `view_page path image=3`, save it
  with `export_image path image=3`.
- Word procedures: `view_page path image=N` shows their N-th picture.

## Example

Question: « Comment remplace-t-on le réducteur d'un robot Delta ? Montre-moi les images. »

1. `search "remplacement du réducteur robot Delta" language="fr" category="6000_Entretien"`;
   `search "réducteur moteur démontage" language="fr" pictures=true`.
2. Read the best section with `read_document`; note the steps and the `[Image N]` markers.
3. Show the key pictures with `view_page path image=N`; answer with the steps and the pictures,
   citing the section; say if a project's customised manual should be checked (Projects world).

# Documentation world (`dmx-documentation`)

## What is in the index

Call `world_map` (this connector) for what this world holds, where, and what it lacks; it is kept
up to date by hand and with live counts. The essentials for searching:

- Each hit shows its **category** = the manual chapter, and its **language**:
  `0000_Page de garde`, `1000_Introduction`, `2000_Sécurité`, `3000_Instruction de commande`,
  `4000_Interface opérateur`, `5000_Descriptions fonctionnelles`, `6000_Entretien`,
  `7000_Appendice`; languages FR, DE, EN (most sections exist in several languages).

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

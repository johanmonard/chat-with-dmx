# Marketing world (`dmx-marketing`)

## What is in the index

- The marketing share `O:\SAL\2_Groupes\22_Marketing`: PDF, Word, PowerPoint (read **slide by
  slide**: "slide" instead of "page") and **picture files** (JPG, PNG, BMP, GIF, TIF), mostly
  English and French, also German.
- Each hit shows its **category** = the top folder: Brochures, Competition, Datasheets, Exhibition,
  Graphics, Pictures, Presentations, Publications, Videos.
- Pictures are described by the vision model: photos and graphics in the picture files, pictures
  on slides and in Word files, brochure pages and exhibition posters (picture pages of PDFs).
  A picture file is a one-page document named after the file; its description passage starts
  with "Fichier : <name> (dossiers : A / B)".

## Filters

- `category="Datasheets"` (technical data), `category="Presentations"` (sales and training decks),
  `category="Competition"` (competitors), `category="Brochures"`, `category="Pictures,Graphics"`
  (photos and graphics for slides), `category="Exhibition"` (posters, stands). Several: comma-separated.
- `file_type="pptx"` / `"pdf"` / `"jpg"` to restrict to one kind of file.
- `pictures=true` for pictures only, e.g. `search "Paloma préhenseur ventouses vue d'ensemble" pictures=true category="Pictures"`.
- Product names: Paloma, Presto, Hector, Delfi, Astor, Nestor, FeedPlacer; search their names in
  "double quotes" with `mode="keyword"` when you need every mention.

## Reading and showing

- PowerPoint: `view_page path page=5 image=2` shows the 2nd picture of slide 5 (whole slides cannot
  be shown, only their pictures); `export_image` with the same page/image saves it.
- Picture files: `view_page path` shows the picture; `export_image path` copies the original file.
- PDF brochures: `view_page path page=3` renders the page; `region=` zooms on a part.

## How to use Marketing answers

- Marketing material is **promotional**: present its figures as claims ("according to the
  datasheet ..."). For what a specific client's machine really reached, check the Projects world
  (FAT/SAT); for the standard technical value, the Documentation world.
- Prefer the most recent document (modification date, version in the file name) and say which one
  you used; several versions of the same brochure or deck often exist.

## Example

Question: « Trouve une photo d'une Paloma 4R avec préhenseur à ventouses pour une présentation »

1. `search "Paloma 4R robots préhenseur à ventouses" pictures=true`; `search "Paloma 4R suction cup gripper photo" pictures=true category="Pictures,Graphics"`.
2. Evaluate the descriptions; open the best 2-3 with `view_page` (path and page/image from the hit).
3. Keep the one that really shows it; `export_image` with `name="Paloma4R_ventouses"`; give the file path and the source.

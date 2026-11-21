# Engineering world (`dmx-engineering`)

## What is in the index

Call `world_map` (this connector) for what this world holds, where, and what it lacks. The
essentials for searching:

- The world holds the **working knowledge of the engineering groups**: methods, calculation tools,
  templates, reference documents, and notebooks handed over by engineers. Mostly French, also
  English and German. It is not a project world: no client project folders here (Projects), no
  standard machine manual (Documentation).
- It has three **groups**: Mechanical, Electrical, Software. **Only Mechanical is filled today**;
  Electrical and Software are empty until their folders are added. Do not conclude "the
  documentation does not exist" from an empty group: say that the group is not indexed yet.
- Besides PDF, Word, PowerPoint, mails, Markdown and picture files (described by the vision
  model), the world reads **Excel workbooks** and **OneNote notebooks**.
- Every hit shows a line `group <G> · section <S>`: the group, and the section = the first folder
  below the group folder, e.g. "224 - Pneumatique". A document directly in the group folder has no
  section.

## Filters

- `category="Mechanical"` filters by group (`category="Mechanical,Electrical"` for several).
- `section="224 - Pneumatique"` filters by section; give the folder name as it is written, number
  included. The sections are listed by `world_map` and `list_folder`.
- `file_type="xlsx"` / `"one"` / `"pdf"` to restrict to one kind of file; `modified_after=2022`
  for recent work only.
- `pictures=true` for picture descriptions only.
- `229 - Archives` is an **ordinary section**, indexed like the others; it is recognisable only by
  its name in the `section` line. Its documents are old: say so when you rely on one, and look for
  a newer version in the other sections (`section="229 - Archives"` to search only there).
- Filters leave out documents whose facet is unknown: if a filtered search is thin, run it again
  without the filter.

## Reading an Excel hit

- The page word is **sheet**: one sheet is one page, in workbook order, hidden sheets included.
  The page text starts with `Feuille : <name>`; a hidden sheet is marked "(masquée)".
- A row is one line, cells joined by ` | `. The lines starting with `[colonnes]` repeat the
  **header** of the sheet, so a passage from row 3,000 still tells what its columns are: read the
  values against that header.
- **Values only: formulas are not indexed**, only the last value Excel stored (nothing when the
  workbook stored none). To know how a figure is calculated, open the workbook with
  `open_document`.
- A sheet is **cut after 5,000 rows** (the line `[feuille coupée après 5000 lignes]` says so); a
  figure missing from a long sheet may be below the cut. Say so rather than concluding it is absent.
- Dates are written `YYYY-MM-DD`; large workbooks may be cut as a whole (the last page says so).
- Cite the file path and the **sheet name**.

## Reading a OneNote hit

- The page word is **page**: one OneNote page is one page of the section file (the document is
  the section, named after the file). Sub-pages are titled `<parent title> > <title>`.
- The text starts with the **page title** and a line `Créée le ..., modifiée le ...`: use the
  dates to judge how old a note is.
- `[Texte des images]` holds **OneNote's own recognition** of the text inside the pictures of the
  page. It is machine-read: **check a number, a code or a name before quoting it**, and say that
  it comes from picture recognition. Pictures without text in them are not searchable.
- `Fichiers joints :` lists the **names** of attached files only; their content is not indexed.
  Look for the file itself with `find_files`.
- The notebooks are read during the index run only. When a notebook changed since it was
  indexed, `read_document` serves the indexed pages with a note saying so: the note, not the
  page, tells you the page may be out of date. A notebook not read yet is named with its reason.
- Notebooks of individual engineers may hold personal or priced notes (sections such as "Perso"):
  use them for the question asked and do not pass them on beyond what the question needs.
- Cite the file path (the section) and the **page title**.

## Reading and showing

- **Excel and OneNote cannot be shown** with `view_page` or `export_image`: they answer with an
  explanation. To let the user see the original, call `open_document` (it opens the file on their
  PC; for a notebook, OneNote itself opens it).
- PDF: `view_page path page=3` renders the page; `region=` zooms on a part. PowerPoint: page =
  slide, `image=` the n-th picture on it. Picture files: `view_page path`.
- Passages "picture N" or "whole-page picture" were written by a vision model: open the picture
  with `view_page` before quoting anything precise from it.
- Hits marked (OCR) come from scanned pages: check codes and numbers on the page.

## How to use Engineering answers

- These are **methods and working documents**, not what was delivered to a client: for what a
  machine really reached or what was agreed, check the Projects world (FAT/SAT, specifications);
  for the standard design, the Documentation world.
- Prefer the most recent version and say which one you used (file modification date, page dates
  in OneNote, version in the file name). Several versions of a calculation sheet often exist, and
  `229 - Archives` keeps old ones.
- A calculation tool or a template: say which workbook and sheet it comes from, and that the
  user should check the input values for their case.

## Example

Question: « Comment dimensionne-t-on un vérin pneumatique ? Existe-t-il un fichier de calcul ? »

1. `search "dimensionnement vérin pneumatique" category="Mechanical" section="224 - Pneumatique"`;
   `search "calcul vérin course diamètre pression" file_type="xlsx" category="Mechanical"`.
2. Evaluate the hits: a sheet (`Feuille : ...`) with its `[colonnes]` header, a OneNote page with
   its title and dates, or a PDF. Read the best with `read_document` around the matching page.
3. If the answer is a calculation sheet, say that formulas are not indexed and point to the file;
   offer `open_document` to open it.
4. Answer with the file path and the sheet (or page title); say what was not found, and that
   Electrical and Software are not indexed yet if the question touched them.

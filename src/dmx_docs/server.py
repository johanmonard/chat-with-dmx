"""MCP server (stdio) exposing the document tools to Claude Desktop / Claude Code."""

from __future__ import annotations

import logging
import sys

try:  # mcp >= 2
    from mcp.server.mcpserver import Image, MCPServer
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer
    from mcp.server.fastmcp import Image

try:
    from mcp.types import ToolAnnotations
    READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
except Exception:  # pragma: no cover
    READ_ONLY = None

from .config import Config
from .tools import DocTools

WORLD_MAP_LINE = ("Call world_map first when you do not know where a kind of information is kept or whether "
                  "this world holds it at all: it lists what is here, where, and what is missing.")

INSTRUCTIONS = """\
Read-only access to the company's project documentation (Demaurex: robotic packaging lines;
PDF and Word files, mostly French, also English, German, Spanish). One folder per project
(code names like THOR, YAKUMA, ANGE); current projects use the subfolders 0_Vente (offers,
specifications), 1_Finances, 2_Electrique (schematics), 3_Mecanique, 4_Soft,
5_Gestion (follow-up, FAT/SAT acceptance), 6_Rapports_Tests, 7_Photos_Videos,
8_Documentation (manuals), 9_SAV; older projects use named folders (Cahier des charges,
Electrique, Gestion, Rapports, Documentation, SAV, Clôture). Each search hit shows its
project, collection, section and document type when known.

""" + WORLD_MAP_LINE + "\n\n" + """\
Never answer from the first search alone. Follow this loop for every question:

1. REFORMULATE. Rewrite the question into 2-4 search queries: precise French technical terms
   and synonyms (préhenseur/ventouse/pince, cadence/débit/produits par minute, FAT/réception
   usine, SAT/mise en service), English/German equivalents, exact codes in "double quotes".
   If the question has several parts or spans several projects, make one query per part.
   If it names a project, check its exact name with `list_projects` and filter with
   project= (project names are often ordinary words: ANGE, BOULE, LEON). Use doc_type= when
   the answer lives in a known kind of document (fat, sat, offre, cahier_des_charges,
   mise_en_service, manuel...). If the question is about a machine type (Paloma, Presto,
   Hector, Delfi, Astor, Nestor, FeedPlacer, or a model like "Paloma 4R"), filter with
   machine=; for "which projects ..." questions, get the complete candidate list with
   list_projects(machine=...) and check them one by one. For "which projects ..." and "what
   did we build for ..." questions, start with `catalog` (written project cards: infeed,
   outfeed, end effector, speed), then check in the documents; mind its coverage line.
   For questions about a client, a
   country or delivery years ("what did we deliver to client X?", "projects in the UK since
   2015"), use list_projects(client=/country=/year_from=) and filter searches with client= or
   country=; `project_card` gives a project's client, delivered machines (serial numbers,
   models, years, robots), order numbers and key documents. The machine register is a
   hand-made list: when it disagrees with the documents (marked ⚠), trust the documents and
   say so. Old projects (collections such as 2_Hors_Garantie) are less
   well classified: if a filtered search is thin, repeat it without doc_type/section. What is
   indexed changes: check with `list_projects` before concluding from an absence of results.
2. RETRIEVE with `search` (hybrid by default; mode="keyword" for codes and names).
3. EVALUATE each hit before using it: does the excerpt actually address the question (not
   just share words)? Each hit shows how it matched and a meaning similarity: "strong"
   (>= 0.86) is usually on topic, "medium" must be checked by reading, "weak" (< 0.83) is
   often off topic - hints only: short queries score lower, judge by reading. It also shows
   the document's project, section and type. Are all parts of the question covered? Do sources contradict each other
   (versions, dates)? Open the best documents with `read_document` around the matching pages
   (excerpts are short, tables and context matter) and use `find_in_document` in long files.
   When the answer is visual (drawing, layout, schematic, photo, gripper, table whose text is
   garbled) or the user asks to see something, look at the page with `view_page` (zoom with
   region=; for .docx, pictures with image=).
4. RETRY if coverage is insufficient, results are weak or off topic, or a part is missing:
   change the angle (synonyms, other language, broader or narrower terms, the document type
   that would hold the answer: offer, specification, FAT report, manual, schematic), search
   the missing sub-question, or browse the project with `list_folder`. At most 3 rounds of
   searching, then answer with the best available evidence.
5. SYNTHESIZE: answer in the user's language, only from what you read. Cite every fact as
   full path + page, e.g. \\\\server\\share\\THOR\\5_Gestion\\FAT.pdf (p. 7). Prefer the most recent
   document when versions disagree and say so. State clearly what was not found or is
   uncertain; never fill gaps with general knowledge presented as coming from the documents.

Hits marked (OCR) come from scanned pages read by OCR: the text can contain recognition errors -
check codes, numbers and names with view_page before quoting them.
"""

MARKETING_INSTRUCTIONS = """\
Read-only access to the company's marketing documents (Demaurex: robotic packaging lines):
brochures, datasheets, presentations (sales, trainings, company), exhibitions, competition
analyses and publications; PDF, Word and PowerPoint, mostly English and French, also German.
Each search hit shows its category = the top folder: Brochures, Competition, Datasheets,
Exhibition, Graphics, Pictures, Presentations, Publications, Videos. PowerPoint files are read
slide by slide ("slide" instead of "page").

""" + WORLD_MAP_LINE + "\n\n" + """\
Never answer from the first search alone. Follow this loop for every question:

1. REFORMULATE the question into 2-4 search queries: English and French terms, product names
   (Paloma, Presto, Hector, Delfi, Astor, Nestor, FeedPlacer), exact names in "double quotes".
   Filter with category= when the kind of document is known (category="Competition" for
   competitors, "Datasheets" for technical data, "Presentations" for sales decks).
2. RETRIEVE with `search` (hybrid by default; mode="keyword" for names and codes); browse a
   category with `list_folder`.
3. EVALUATE each hit: does the excerpt answer the question or only share words? Read the best
   documents with `read_document` / `find_in_document`; look at pictures with `view_page`
   (PDF pages; for PowerPoint, page = slide and image = n-th picture on it).
4. RETRY with other words or another language if coverage is thin, at most 3 rounds.
5. SYNTHESIZE in the user's language, only from what you read. Cite every fact as full path +
   page or slide. Marketing material is promotional: say so when a figure only comes from a
   brochure. State clearly what was not found.

Hits marked (OCR) come from scanned pages read by OCR: the text can contain recognition errors -
check codes, numbers and names with view_page before quoting them.
"""

GENERIC_INSTRUCTIONS = """\
Read-only access to the company's "{title}" documents (Demaurex: robotic packaging lines).
""" + WORLD_MAP_LINE + "\n" + """\
Search with `search` (2-4 reformulated queries, several languages), check each hit by reading it
with `read_document` / `find_in_document`, look at pictures with `view_page`, browse with
`list_folder`, and answer only from what you read, citing full path + page.
Hits marked (OCR) come from scanned pages read by OCR: the text can contain recognition errors -
check codes, numbers and names with view_page before quoting them.
"""


DOCUMENTATION_INSTRUCTIONS = """\
Read-only access to the STANDARD machine manual of Demaurex (robotic packaging lines: Paloma,
Presto, Hector, Delfi, Astor, Nestor, conveyors, vision, Gemini HMI), in French, German and
English. Each search hit shows its category = the manual chapter (0000_Page de garde,
1000_Introduction, 2000_Sécurité, 3000_Instruction de commande, 4000_Interface opérateur,
5000_Descriptions fonctionnelles, 6000_Entretien, 7000_Appendice) and its language (FR, DE, EN).
Most sections exist in several languages: search with language= set to the user's language (fr by
default; de or en when the user writes German or English), and repeat without the filter if a
section is missing in that language. Each project's own, customised manual is in the project
documentation (the projects' 8_Documentation folders, another world): this manual is the
generic reference.

""" + WORLD_MAP_LINE + "\n\n" + """\
Never answer from the first search alone:
1. REFORMULATE the question into 2-4 queries (precise terms, synonyms, module names); filter with
   category= when the chapter is obvious (maintenance -> 6000_Entretien, safety -> 2000_Sécurité).
2. RETRIEVE with `search`; read the best sections in full with `read_document`.
3. Sections contain picture markers like [Image 3: Dessus de la Paloma]: show or check a picture
   with `view_page` (image=3), save it for a presentation with `export_image` (image=3).
4. RETRY with other words or another language if coverage is thin, at most 3 rounds.
5. SYNTHESIZE in the user's language, only from what you read, citing full path (and image
   numbers when you rely on a picture). State clearly what was not found.
Hits marked (OCR) come from scanned pages read by OCR: the text can contain recognition errors -
check codes, numbers and names with view_page before quoting them.
"""


OFFERS_INSTRUCTIONS = """\
Read-only access to every offer Demaurex sent to clients (robotic packaging lines), won or not,
from the 1999 archive to today: each offer's revisions and variants, its specifications, mails,
layouts and visit reports (PDF, Word, PowerPoint, Outlook mails; French, English, German).
For sales and management only: never pass its content to anyone else. Each search hit shows the
client (and site), the offer number and revision (variant), the kind of document and the country.

Besides the documents, an OFFER REGISTER lists every offer with each revision's priced lines, read
from the offer documents (amounts per currency exactly as printed, source path and page), whether it
was won (and by which project of the projects world) and the user's corrections. Use it:
- charts and price trends ("Paloma 3R prices over the years") -> `offer_lines` (rows for the chart);
- counts ("offers per year, per country") -> `offer_stats` (group_by= one or two keys);
- a client's history -> `list_offers(client=...)`, then `offer_card` for the offers that matter;
- one offer -> `offer_card` (revisions, lines, rates, status evidence, other documents).
Rules for every answer built from the register:
- One currency per chart (CHF by default); never convert amounts, not even with a rate printed in
  the offer. An offer without that currency is left out and named in the coverage line.
- Always show the coverage line (offers matched, with readable prices, left out and why).
- Show the line text of each point, so that different scopes (with or without vision, conveyors,
  installation...) are visible; never compare totals of different scopes as if they were the same.
- Mark won and not won offers differently (e.g. filled and hollow points); a won offer's final
  negotiated price is in the project's 0_Vente in the projects world, not here. won_unconfirmed (the
  number only in the text of another client's project with another machine) is shown apart from won,
  never counted as won without checking the evidence (`offer_card`).
- Defaults: last revision of each offer (every variant of it is one point), client and archive
  folders. Budget offers are included and marked (offer_type column: mark them on the chart);
  filter offer_type="firm" to exclude them. Partner offers are counted in the coverage line and
  included with category=. Use revisions="all" for the history of one offer or client.
- Cite path and page for every value; check a surprising value on the page with `view_page`.

""" + WORLD_MAP_LINE + "\n\n" + """\
Never answer from the first search alone. Follow this loop for every question:

1. REFORMULATE the question into 2-4 search queries (French, English and German terms, machine
   names, exact offer numbers in "double quotes"); filter with client=, country=, offer=,
   doc_type=, year_from= / year_to= or status= when the question names them.
2. RETRIEVE with the register tools above and with `search`; browse an offer folder with
   `list_folder`.
3. EVALUATE: does each hit or row answer the question? Read the offer around the price page with
   `read_document`; look at the page with `view_page` when a value matters or looks wrong.
4. RETRY with other words, another language or other filters if coverage is thin, at most 3 rounds.
5. SYNTHESIZE in the user's language, only from what you read, citing full path + page, with the
   coverage line for every chart or count. State clearly what was not found.

Hits marked (OCR) come from scanned pages read by OCR: the text can contain recognition errors -
check codes, numbers and names with view_page before quoting them.
"""


PICTURES_NOTE = """\
Passages marked "picture N" or "whole-page picture" in search results were written by a vision model
from the pictures (photos, schemas, screenshots, picture pages). Use them to find and choose pictures
- open one with view_page (the page/image given in the hit), save it for a presentation with
export_image - and to answer questions about what pictures show. To find a picture, search with
pictures=true. Look at the picture with view_page before quoting a precise value (number, label,
code) from a description.
"""


def instructions_for(cfg: Config) -> str:
    if cfg.profile == "projects":
        base = INSTRUCTIONS
    elif cfg.profile == "marketing":
        base = MARKETING_INSTRUCTIONS
    elif cfg.profile == "documentation":
        base = DOCUMENTATION_INSTRUCTIONS
    elif cfg.profile == "offers":
        base = OFFERS_INSTRUCTIONS
    else:
        base = GENERIC_INSTRUCTIONS.format(title=cfg.world_title or cfg.world)
    return base + "\n" + PICTURES_NOTE


def build_server(cfg: Config) -> MCPServer:
    tools = DocTools(cfg)
    mcp = MCPServer(cfg.server_name, instructions=instructions_for(cfg))
    kw = {"annotations": READ_ONLY} if READ_ONLY is not None else {}

    def search(query: str, folder: str | None = None, file_type: str | None = None,
               modified_after: str | None = None, limit: int = 10, mode: str = "hybrid",
               project: str | None = None, doc_type: str | None = None, section: str | None = None,
               collection: str | None = None, machine: str | None = None,
               expand: bool | None = None, client: str | None = None, country: str | None = None,
               pictures: bool = False) -> str:
        """Search the documentation by meaning and keywords. Returns excerpts with file path and page,
        how each one matched (keyword, semantic or both) and its meaning similarity to the query
        (strong >= 0.86, medium 0.83-0.86, weak < 0.83 = often off topic). These are hints: judge by reading.

        Args:
            query: What to look for (any language; documents are mostly French).
                Use "double quotes" for exact phrases or codes, and word* for prefix matches.
            folder: Optional folder path to restrict the search to (includes subfolders).
            file_type: Optional 'pdf', 'docx' or 'doc'.
            modified_after: Optional date (YYYY, YYYY-MM or YYYY-MM-DD) - only files modified since then.
            limit: Number of results (1-30, default 10). At most 3 excerpts per document.
            mode: 'hybrid' (default), 'keyword' (exact words, codes, names) or 'semantic' (by meaning).
            project: Optional project name(s), comma-separated, exactly as in `list_projects` (e.g. "THOR").
            doc_type: Optional document type(s), comma-separated: offre, commande, cahier_des_charges,
                facture, modification, schema_electrique, layout, plan, nomenclature, pieces_detachees,
                manuel, doc_fournisseur, fat, sat, reception, mise_en_service, open_points, tests,
                suivi, planning, transport, sav, doc_electrique, doc_mecanique, soft.
            section: Optional project section(s): Vente, Finances, Electrique, Mecanique, Soft, Gestion,
                Rapports_Tests, Photos_Videos, Documentation, SAV, Cloture.
            collection: Optional collection (folder grouping projects), e.g. "2_Hors_Garantie".
            machine: Optional machine family or model of the project, comma-separated: "Paloma",
                "Presto", "Hector", "Delfi", "Astor", "Nestor", "FeedPlacer", or a model like
                "Paloma 4R", "Presto 2R", "Paloma 8R SQ".
            expand: Widen the keyword part with the company thesaurus (synonyms, translations,
                abbreviations). Default: as configured.
            client: Optional client or client group (any part of the name, comma = or), e.g.
                "Acme", "Acme Foods": documents of the projects delivered to that client
                (from the machine register).
            country: Optional country of installation in English, e.g. "France", "United Kingdom".
            pictures: True to search only the descriptions of pictures (photos, schemas, screenshots,
                picture pages), e.g. to find a picture to show or export.
            Facet filters leave out documents whose facet is unknown (mostly old projects):
            search again without them when results are thin.
        """
        return tools.search(query, folder=folder, file_type=file_type, modified_after=modified_after,
                            limit=limit, mode=mode, project=project, doc_type=doc_type,
                            section=section, collection=collection, machine=machine, expand=expand,
                            client=client, country=country, pictures=pictures)

    def list_projects(name: str | None = None, collection: str | None = None, machine: str | None = None,
                      limit: int = 300, client: str | None = None, country: str | None = None,
                      year_from: int | None = None, year_to: int | None = None) -> str:
        """List the projects in the index with their collection (current projects, 2_Hors_Garantie...),
        number of documents, last modification date, the machine models found in their documents
        (Paloma 11R, Presto 2R...), the client, machines delivered and year from the company's
        machine register, and sections. Use it to find the exact project name before filtering
        searches with project=, to get ALL the projects with a given machine (machine="Paloma"),
        client, country or delivery period before checking them one by one, or to answer
        questions about projects. With client/country/year/machine filters it also lists machines
        of the register whose project documents are not indexed.

        Args:
            name: Optional part of the project name.
            collection: Optional part of the collection name.
            machine: Optional machine family or model, e.g. "Paloma", "Presto 2R".
            limit: Maximum projects listed (default 300).
            client: Optional client or client group, any part of the name (e.g. "Acme").
            country: Optional country of installation in English (e.g. "France", "Germany").
            year_from: Optional first year of manufacture/order (e.g. 2015).
            year_to: Optional last year.
        """
        return tools.list_projects(name=name, collection=collection, machine=machine, limit=limit,
                                   client=client, country=country, year_from=year_from, year_to=year_to)

    def project_card(project: str) -> str:
        """One page about a project: client (group, industry, site, country), every machine
        delivered (serial number, model, year, robots, controller, camera, maintenance contract,
        order date), order numbers, machines named in the documents, document counts by type,
        the newest key documents (offer, order, specification, FAT, SAT, commissioning) and the
        client's other projects. Use it first when a question is about one project.

        Args:
            project: Project name as in list_projects (e.g. "THOR"), or a project name from the
                machine register.
        """
        return tools.project_card(project)

    def catalog(infeed: str | None = None, outfeed: str | None = None, end_effector: str | None = None,
                machine: str | None = None, client: str | None = None, country: str | None = None,
                year_from: int | None = None, year_to: int | None = None, status: str | None = None,
                text: str | None = None, limit: int = 100) -> str:
        """List the written project cards: one line per project with client, years, machines, what
        goes in -> what comes out (infeed -> outfeed), end effector, speed and the card's status
        (unreviewed / reviewed). Cards are written by Claude from the documents, with a source on every
        fact (read one with project_card); where a card and a document disagree, the document wins.
        Not every project has a card yet: the last line gives the coverage - for a complete answer,
        also use search / list_projects. Use it first for "which projects ..." and "what did we build
        for ..." questions.

        Args:
            infeed: What goes into the line, e.g. "flowpack", "loose", "pouch" (synonyms in any language
                work: "sachet", "vrac").
            outfeed: What comes out, e.g. "box", "case", "tray", "blister", "flow wrapper chain"
                (products placed into a flow wrapper's infeed chain).
            end_effector: e.g. "vacuum", "mechanical gripper" ("ventouse", "vide", "pince" work too).
            machine: Machine family or model, e.g. "Paloma", "Presto 2R".
            client: Any part of the client's name.
            country: Country as written on the cards.
            year_from: First year (order to SAT period overlaps it or later).
            year_to: Last year.
            status: "reviewed" (checked by a person) or "unreviewed".
            text: Free text anywhere in the card.
            limit: Maximum cards listed (default 100).
        """
        return tools.catalog(infeed=infeed, outfeed=outfeed, end_effector=end_effector, machine=machine,
                             client=client, country=country, year_from=year_from, year_to=year_to,
                             status=status, text=text, limit=limit)

    @mcp.tool(**kw)
    def find_files(name: str, folder: str | None = None, limit: int = 30) -> str:
        """Find indexed documents whose file name or folder path contains all the given words
        (e.g. a project number, client, machine type or document title). Prefix matching per word.

        Args:
            name: Words of the file or folder name, e.g. "P1234 offre" or "manuel delta".
            folder: Optional folder to restrict to.
            limit: Maximum number of files (default 30).
        """
        return tools.find_files(name, folder=folder, limit=limit)

    @mcp.tool(**kw)
    def list_folder(path: str | None = None, limit: int = 200) -> str:
        """List the subfolders and files of a folder (with size, date and whether each file is indexed).
        Without a path, lists the indexed root folders.

        Args:
            path: Folder path, e.g. a folder taken from a search result.
            limit: Maximum entries to list (default 200).
        """
        return tools.list_folder(path, limit=limit)

    @mcp.tool(**kw)
    def read_document(path: str, start_page: int = 1, end_page: int | None = None) -> str:
        """Read the full text of a document, page by page. Long documents are returned in parts;
        the answer says which start_page to use to continue. For Word files, page numbers are
        approximate.

        Args:
            path: Full file path (as shown in search results).
            start_page: First page to read (default 1).
            end_page: Last page to read (default: as much as fits).
        """
        return tools.read_document(path, start_page=start_page, end_page=end_page)

    @mcp.tool(**kw)
    def find_in_document(path: str, text: str, max_hits: int = 20) -> str:
        """Find every occurrence of a word or phrase inside one document (ignores case and accents),
        with the page number and surrounding text.

        Args:
            path: Full file path.
            text: Word or phrase to find.
            max_hits: Maximum occurrences to show (default 20).
        """
        return tools.find_in_document(path, text, max_hits=max_hits)

    @mcp.tool(**kw)
    def view_page(path: str, page: int = 1, region: str | None = None,
                  image: int | None = None) -> list[str | Image]:
        """Look at a document as an image: a PDF page as it is printed (drawings, schematics,
        layouts, photos, tables), a picture embedded in a Word .docx (photos of FAT reports...),
        a picture on a PowerPoint slide (page = slide number, image = n-th picture on it), or a
        picture file (JPG, PNG...).
        Use it when the extracted text is not enough: visual content, garbled tables, values in
        drawings. Reads the live file, so it needs access to the file server.

        Args:
            path: Full file path (as shown in search results).
            page: PDF page number, or PowerPoint slide number (default 1).
            region: Optional zoom on part of a PDF page, 'x0,y0,x1,y1' as fractions of the page
                (0,0 = top-left), e.g. '0.5,0.5,1,1' = bottom-right quarter. Rendered sharper.
            image: For .docx files: number of the embedded picture (1 = first, document order).
                For PowerPoint: number of the picture on the slide (1 = first). For Markdown
                (.md): the number N of an [Image N] marker in the text.
        """
        caption, data, fmt = tools.view_page(path, page=page, region=region, image=image)
        return [caption, Image(data=data, format=fmt)]

    @mcp.tool(**kw)
    def export_image(path: str, page: int = 1, region: str | None = None, image: int | None = None,
                     name: str | None = None) -> str:
        """Save a picture from a document as an image FILE on the user's PC, to use it elsewhere
        (presentation, report, email): a PDF page or a zoomed region of it (rendered sharply, up
        to 2400 px), a picture embedded in a .docx or on a PowerPoint slide (original
        resolution), or a copy of a picture file (JPG, PNG...). Returns the file path, in the user's local export folder (default
        C:\\dmx-rag\\exports). Check the page/region first with view_page.

        Args:
            path: Full file path (as shown in search results).
            page: PDF page number, or PowerPoint slide number (default 1).
            region: Optional part of a PDF page, 'x0,y0,x1,y1' as fractions (0,0 = top-left).
            image: For .docx files: number of the embedded picture (1 = first). For PowerPoint:
                number of the picture on the slide. For Markdown (.md): the number N of an
                [Image N] marker in the text.
            name: Optional file name (without extension), e.g. "DUOMO_preheneur_SS".
        """
        return tools.export_image(path, page=page, region=region, image=image, name=name)

    @mcp.tool(**kw)
    def open_document(path: str, page: int | None = None) -> str:
        """Open a document on the user's PC in its usual application (Acrobat, Edge, Word...),
        at the given page when the PDF viewer supports it; Word files open read-only.
        Only when the USER asks to open or see the original file - never to read it yourself
        (use read_document / view_page for that).

        Args:
            path: Full file path (as shown in search results).
            page: Optional page to open the PDF at.
        """
        return tools.open_document(path, page=page)

    @mcp.tool(**kw)
    def world_map() -> str:
        """What this world holds and where: the kinds of documents and the folders or filters that reach
        them, what is NOT here and where to look instead (hand-written map), then live counts and the
        folder tree from the index. Call it before routing a question you are unsure about."""
        return tools.world_map()

    @mcp.tool(**kw)
    def index_status() -> str:
        """Show what is indexed: number of documents per status and type, embeddings coverage,
        root folders and last indexing date."""
        return tools.index_status()

    def marketing_search(query: str, folder: str | None = None, file_type: str | None = None,
                         modified_after: str | None = None, limit: int = 10, mode: str = "hybrid",
                         category: str | None = None, expand: bool | None = None, pictures: bool = False) -> str:
        """Search the marketing documents by meaning and keywords. Returns excerpts with file path
        and page (slide for PowerPoint), how each one matched (keyword, semantic or both) and its
        meaning similarity to the query (strong >= 0.86, medium 0.83-0.86, weak < 0.83 = often off
        topic). These are hints: judge by reading.

        Args:
            query: What to look for (any language; documents are mostly English and French).
                Use "double quotes" for exact phrases or names, and word* for prefix matches.
            folder: Optional folder path to restrict the search to (includes subfolders).
            file_type: Optional 'pdf', 'docx', 'doc', 'pptx' or 'ppt'.
            modified_after: Optional date (YYYY, YYYY-MM or YYYY-MM-DD) - only files modified since then.
            limit: Number of results (1-30, default 10). At most 3 excerpts per document.
            mode: 'hybrid' (default), 'keyword' (exact words, codes, names) or 'semantic' (by meaning).
            category: Optional top folder(s), comma-separated: Brochures, Competition, Datasheets,
                Exhibition, Graphics, Pictures, Presentations, Publications, Videos.
            expand: Widen the keyword part with the company thesaurus. Default: as configured.
            pictures: True to search only the descriptions of pictures (photos, schemas, screenshots,
                picture pages, picture files), e.g. to find a picture to show or export.
        """
        return tools.search(query, folder=folder, file_type=file_type, modified_after=modified_after,
                            limit=limit, mode=mode, category=category, expand=expand, pictures=pictures)

    def plain_search(query: str, folder: str | None = None, file_type: str | None = None,
                     modified_after: str | None = None, limit: int = 10, mode: str = "hybrid",
                     expand: bool | None = None, pictures: bool = False) -> str:
        """Search the documents by meaning and keywords. Returns excerpts with file path and page,
        how each one matched and its meaning similarity to the query (strong >= 0.86, medium
        0.83-0.86, weak < 0.83). These are hints: judge by reading.

        Args:
            query: What to look for (any language). "double quotes" for exact phrases, word* for prefixes.
            folder: Optional folder path to restrict the search to (includes subfolders).
            file_type: Optional file type, e.g. 'pdf', 'docx', 'pptx'.
            modified_after: Optional date (YYYY, YYYY-MM or YYYY-MM-DD).
            limit: Number of results (1-30, default 10).
            mode: 'hybrid' (default), 'keyword' or 'semantic'.
            expand: Widen the keyword part with the company thesaurus. Default: as configured.
            pictures: True to search only the descriptions of pictures (photos, schemas, screenshots,
                picture pages), e.g. to find a picture to show or export.
        """
        return tools.search(query, folder=folder, file_type=file_type, modified_after=modified_after,
                            limit=limit, mode=mode, expand=expand, pictures=pictures)

    def documentation_search(query: str, folder: str | None = None, file_type: str | None = None,
                             modified_after: str | None = None, limit: int = 10, mode: str = "hybrid",
                             category: str | None = None, language: str | None = None,
                             expand: bool | None = None, pictures: bool = False) -> str:
        """Search the standard machine manual by meaning and keywords. Returns excerpts with file
        path, how each one matched and its meaning similarity to the query (strong >= 0.86, medium
        0.83-0.86, weak < 0.83 = often off topic), and each section's chapter and language.

        Args:
            query: What to look for (any language). "double quotes" for exact phrases, word* for prefixes.
            folder: Optional folder path to restrict the search to (includes subfolders).
            file_type: Optional 'md' (manual sections), 'pdf' or 'docx' (procedures).
            modified_after: Optional date (YYYY, YYYY-MM or YYYY-MM-DD).
            limit: Number of results (1-30, default 10). At most 3 excerpts per document.
            mode: 'hybrid' (default), 'keyword' or 'semantic'.
            category: Optional manual chapter(s), comma-separated, e.g. "6000_Entretien",
                "1000_Introduction", "2000_Sécurité", "5000_Descriptions fonctionnelles".
            language: Optional language(s), comma-separated: fr, de, en. Use the user's language;
                repeat without it if a section is missing in that language.
            expand: Widen the keyword part with the company thesaurus. Default: as configured.
            pictures: True to search only the descriptions of the manual's pictures (photos, schemas,
                screenshots), e.g. to find a picture to show or export.
        """
        return tools.search(query, folder=folder, file_type=file_type, modified_after=modified_after,
                            limit=limit, mode=mode, category=category, language=language, expand=expand,
                            pictures=pictures)

    def offers_search(query: str, folder: str | None = None, file_type: str | None = None,
                      modified_after: str | None = None, limit: int = 10, mode: str = "hybrid",
                      client: str | None = None, country: str | None = None, offer: str | None = None,
                      revision: str | None = None, doc_type: str | None = None, category: str | None = None,
                      partner: str | None = None, machine: str | None = None, year_from: int | None = None,
                      year_to: int | None = None, status: str | None = None, expand: bool | None = None,
                      pictures: bool = False) -> str:
        """Search the offer documents (offers, specifications, mails, layouts, visit reports) by meaning
        and keywords. Returns excerpts with file path and page, how each one matched and its meaning
        similarity to the query (strong >= 0.86, medium 0.83-0.86, weak < 0.83 = often off topic), and each
        document's client, offer number, revision, kind and country. For prices, counts and histories use
        offer_lines / offer_stats / list_offers instead: search returns only the best excerpts.

        Args:
            query: What to look for (any language). "double quotes" for exact phrases or numbers, word* for prefixes.
            folder: Optional folder path to restrict the search to (includes subfolders).
            file_type: Optional 'pdf', 'docx', 'doc', 'pptx', 'ppt' or 'msg'.
            modified_after: Optional date (YYYY, YYYY-MM or YYYY-MM-DD) - only files modified since then.
            limit: Number of results (1-30, default 10). At most 3 excerpts per document.
            mode: 'hybrid' (default), 'keyword' (exact words, numbers, names) or 'semantic' (by meaning).
            client: Optional part of the client folder's name (case ignored; comma = or), e.g. "acme".
            country: Optional part of the country in English (case ignored; comma = or), e.g. "Germany":
                the archive folder's or the register's.
            offer: Optional offer number(s), comma-separated, e.g. "3001234", "B-250306".
            revision: Optional revision label(s), e.g. "02", "V1".
            doc_type: Optional kind(s) of document: offer, specification, mail, layout, history,
                technical_solution, visit_report, proforma, market_study, nda, sales_info, other.
            category: Optional top-level category: client, archive, partner, unknown_client,
                market_study, nda, sales_info.
            partner: Optional partner of a partner offer: the partner name, as in the partner folders.
            machine: Optional machine family or model named in the path, e.g. "Paloma", "Paloma 3R".
            year_from: Optional first year of the offer (its last revision), from the register.
            year_to: Optional last year of the offer, from the register.
            status: Optional status(es) from the register: won, won_unconfirmed, no_order_found, open, unknown,
                lost, cancelled.
            expand: Widen the keyword part with the company thesaurus. Default: as configured.
            pictures: True to search only the descriptions of pictures.
            Facet filters leave out documents whose facet is unknown: search again without them when
            results are thin.
        """
        return tools.search(query, folder=folder, file_type=file_type, modified_after=modified_after,
                            limit=limit, mode=mode, client=client, country=country, offer=offer, revision=revision,
                            doc_type=doc_type, category=category, partner=partner, machine=machine,
                            year_from=year_from, year_to=year_to, status=status, expand=expand, pictures=pictures)

    def list_offers(client: str | None = None, country: str | None = None, machine: str | None = None,
                    status: str | None = None, year_from: int | None = None, year_to: int | None = None,
                    text: str | None = None, limit: int = 100) -> str:
        """List the offers of the register, newest first, one line each: offer number, client (/ site),
        country, first..last revision date, number of revisions, machine lines of the last revision, its
        total (all currencies; variants side by side), status (-> won project), and the last revision's
        document path and pages. Ends with the coverage line.
        Use it for a client's history, then offer_card for the offers that matter.

        Args:
            client: Optional part of the client's name (case and accents ignored; comma = or), e.g. "acme".
            country: Optional part of the country in English (comma = or), e.g. "Germany".
            machine: Optional machine family or model of a priced line (or named in the paths), e.g.
                "Paloma", "Paloma 3R".
            status: Optional status(es): won, won_unconfirmed, no_order_found, open, unknown, lost, cancelled.
            year_from: Optional first year (of the offer's last revision).
            year_to: Optional last year.
            text: Optional words anywhere in the number, title, client, folder or line texts.
            limit: Maximum offers listed (default 100).
        """
        return tools.list_offers(client=client, country=country, machine=machine, status=status,
                                 year_from=year_from, year_to=year_to, text=text, limit=limit)

    def offer_card(offer: str) -> str:
        """Everything the register knows about one offer: client, country, dates, type, status with its
        evidence (the project that quotes it), every revision oldest first (variant, date and its source,
        document path and pages, totals, confidence, how it was read) with its priced lines (text, kind,
        machine, quantity, amounts per currency, page), exchange rates printed, translations, the user's
        corrections, and the offer's other documents (specifications, mails, layouts).

        Args:
            offer: Offer number, e.g. "3001234" or "B-250306".
        """
        return tools.offer_card(offer)

    def offer_lines(machine: str | None = None, kind: str | None = None, client: str | None = None,
                    country: str | None = None, status: str | None = None, offer_type: str | None = None,
                    category: str | None = "client,archive", year_from: int | None = None,
                    year_to: int | None = None, currency: str = "CHF", revisions: str = "last",
                    limit: int = 500) -> str:
        """The rows for a price chart or table, ';'-separated with a header: date, offer, revision,
        variant, kind, client, country, status, offer_type (firm or budget), quantity, amount, unit_price
        (amount / quantity, only when the quantity is printed), currency, line (the text as written), path,
        page, confidence; one row per priced line with an amount in the chosen currency, never converted.
        Budget offers are included and marked: mark them on the chart, or filter offer_type="firm" to
        exclude them. Ends with the coverage line: offers matched, with readable prices, left out and why
        (category, no CHF amount, no readable prices, ignored by correction, no date), naming them. With
        machine=, it also names the offers without readable prices, which could not be checked for that
        machine.

        Args:
            machine: Optional machine family or model of the line (case ignored; comma = or), e.g. "Paloma 3R".
                With machine= and no kind=, only machine lines (kind machine): the coverage line says so.
            kind: Optional kind(s) of line: machine, option, service, discount, other; empty for every kind.
            client: Optional part of the client's name (comma = or).
            country: Optional part of the country in English (comma = or).
            status: Optional status(es): won, won_unconfirmed, no_order_found, open, unknown, lost, cancelled.
            offer_type: Optional firm or budget; every offer by default (firm and budget, marked).
            category: Folder categories, default "client,archive" (partner offers' prices may be to the
                partner: add "partner" to include them); empty for all.
            year_from: Optional first year of the revision's date.
            year_to: Optional last year.
            currency: The one currency of the chart, default CHF (EUR, USD...).
            revisions: "last" (default: the last revision of each offer, every variant of it) or "all".
            limit: Maximum rows (default 500).
        """
        return tools.offer_lines(machine=machine, kind=kind, client=client, country=country, status=status,
                                 offer_type=offer_type, category=category, year_from=year_from, year_to=year_to,
                                 currency=currency, revisions=revisions, limit=limit)

    def offer_stats(group_by: str, client: str | None = None, country: str | None = None,
                    machine: str | None = None, status: str | None = None, offer_type: str | None = None,
                    category: str | None = None, year_from: int | None = None, year_to: int | None = None) -> str:
        """Count offers and their revisions grouped by one or two keys, ';'-separated with a header
        (<keys>;offers;revisions), sorted by keys; offers with an unknown value are counted under
        "unknown". Every offer counts (firm and budget, every category) unless filtered. Ends with the
        coverage line.

        Args:
            group_by: One or two of year, country, client, machine, status, author, partner, offer_type,
                category, comma-separated, e.g. "year" or "year,country". machine = the machines of the
                last revision (an offer with several counts under each).
            client: Optional part of the client's name (comma = or).
            country: Optional part of the country in English (comma = or).
            machine: Optional machine family or model of a priced line (or named in the paths); the offers
                without readable prices, which could not be checked for it, are named in the coverage line.
            status: Optional status(es): won, won_unconfirmed, no_order_found, open, unknown, lost, cancelled.
            offer_type: Optional firm or budget.
            category: Optional folder categories, e.g. "client,archive".
            year_from: Optional first year (of the offer's last revision).
            year_to: Optional last year.
        """
        return tools.offer_stats(group_by, client=client, country=country, machine=machine, status=status,
                                 offer_type=offer_type, category=category, year_from=year_from, year_to=year_to)

    # Tools that depend on the world's profile.
    if cfg.profile == "projects":
        mcp.tool(**kw)(search)
        mcp.tool(**kw)(list_projects)
        mcp.tool(**kw)(project_card)
        mcp.tool(**kw)(catalog)
    elif cfg.profile == "marketing":
        mcp.tool(name="search", **kw)(marketing_search)
    elif cfg.profile == "documentation":
        mcp.tool(name="search", **kw)(documentation_search)
    elif cfg.profile == "offers":
        mcp.tool(name="search", **kw)(offers_search)
        mcp.tool(**kw)(list_offers)
        mcp.tool(**kw)(offer_card)
        mcp.tool(**kw)(offer_lines)
        mcp.tool(**kw)(offer_stats)
    else:
        mcp.tool(name="search", **kw)(plain_search)

    return mcp


def serve(cfg: Config) -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    build_server(cfg).run()

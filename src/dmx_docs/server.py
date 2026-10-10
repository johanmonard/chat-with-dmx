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

INSTRUCTIONS = """\
Read-only access to the company's project documentation (Demaurex: robotic packaging lines;
PDF and Word files, mostly French, also English, German, Spanish). One folder per project
(code names like THOR, YAKUMA, ANGE); current projects use the subfolders 0_Vente (offers,
specifications), 1_Finances, 2_Electrique (schematics), 3_Mecanique, 4_Soft,
5_Gestion (follow-up, FAT/SAT acceptance), 6_Rapports_Tests, 7_Photos_Videos,
8_Documentation (manuals), 9_SAV; older projects use named folders (Cahier des charges,
Electrique, Gestion, Rapports, Documentation, SAV, Clôture). Each search hit shows its
project, collection, section and document type when known.

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
   list_projects(machine=...) and check them one by one. For questions about a client, a
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


def instructions_for(cfg: Config) -> str:
    if cfg.profile == "projects":
        return INSTRUCTIONS
    if cfg.profile == "marketing":
        return MARKETING_INSTRUCTIONS
    if cfg.profile == "documentation":
        return DOCUMENTATION_INSTRUCTIONS
    return GENERIC_INSTRUCTIONS.format(title=cfg.world_title or cfg.world)


def build_server(cfg: Config) -> MCPServer:
    tools = DocTools(cfg)
    mcp = MCPServer(cfg.server_name, instructions=instructions_for(cfg))
    kw = {"annotations": READ_ONLY} if READ_ONLY is not None else {}

    def search(query: str, folder: str | None = None, file_type: str | None = None,
               modified_after: str | None = None, limit: int = 10, mode: str = "hybrid",
               project: str | None = None, doc_type: str | None = None, section: str | None = None,
               collection: str | None = None, machine: str | None = None,
               expand: bool | None = None, client: str | None = None, country: str | None = None) -> str:
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
            Facet filters leave out documents whose facet is unknown (mostly old projects):
            search again without them when results are thin.
        """
        return tools.search(query, folder=folder, file_type=file_type, modified_after=modified_after,
                            limit=limit, mode=mode, project=project, doc_type=doc_type,
                            section=section, collection=collection, machine=machine, expand=expand,
                            client=client, country=country)

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
        layouts, photos, tables), a picture embedded in a Word .docx (photos of FAT reports...), or
        a picture on a PowerPoint slide (page = slide number, image = n-th picture on it).
        Use it when the extracted text is not enough: visual content, garbled tables, values in
        drawings. Reads the live file, so it needs access to the file server.

        Args:
            path: Full file path (as shown in search results).
            page: PDF page number, or PowerPoint slide number (default 1).
            region: Optional zoom on part of a PDF page, 'x0,y0,x1,y1' as fractions of the page
                (0,0 = top-left), e.g. '0.5,0.5,1,1' = bottom-right quarter. Rendered sharper.
            image: For .docx files: number of the embedded picture (1 = first, document order).
                For PowerPoint: number of the picture on the slide (1 = first).
        """
        caption, data, fmt = tools.view_page(path, page=page, region=region, image=image)
        return [caption, Image(data=data, format=fmt)]

    @mcp.tool(**kw)
    def export_image(path: str, page: int = 1, region: str | None = None, image: int | None = None,
                     name: str | None = None) -> str:
        """Save a picture from a document as an image FILE on the user's PC, to use it elsewhere
        (presentation, report, email): a PDF page or a zoomed region of it (rendered sharply, up
        to 2400 px), or a picture embedded in a .docx or on a PowerPoint slide (original
        resolution). Returns the file path, in the user's local export folder (default
        C:\\dmx-rag\\exports). Check the page/region first with view_page.

        Args:
            path: Full file path (as shown in search results).
            page: PDF page number, or PowerPoint slide number (default 1).
            region: Optional part of a PDF page, 'x0,y0,x1,y1' as fractions (0,0 = top-left).
            image: For .docx files: number of the embedded picture (1 = first). For PowerPoint:
                number of the picture on the slide.
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
    def index_status() -> str:
        """Show what is indexed: number of documents per status and type, embeddings coverage,
        root folders and last indexing date."""
        return tools.index_status()

    def marketing_search(query: str, folder: str | None = None, file_type: str | None = None,
                         modified_after: str | None = None, limit: int = 10, mode: str = "hybrid",
                         category: str | None = None, expand: bool | None = None) -> str:
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
        """
        return tools.search(query, folder=folder, file_type=file_type, modified_after=modified_after,
                            limit=limit, mode=mode, category=category, expand=expand)

    def plain_search(query: str, folder: str | None = None, file_type: str | None = None,
                     modified_after: str | None = None, limit: int = 10, mode: str = "hybrid",
                     expand: bool | None = None) -> str:
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
        """
        return tools.search(query, folder=folder, file_type=file_type, modified_after=modified_after,
                            limit=limit, mode=mode, expand=expand)

    def documentation_search(query: str, folder: str | None = None, file_type: str | None = None,
                             modified_after: str | None = None, limit: int = 10, mode: str = "hybrid",
                             category: str | None = None, language: str | None = None,
                             expand: bool | None = None) -> str:
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
        """
        return tools.search(query, folder=folder, file_type=file_type, modified_after=modified_after,
                            limit=limit, mode=mode, category=category, language=language, expand=expand)

    # Tools that depend on the world's profile.
    if cfg.profile == "projects":
        mcp.tool(**kw)(search)
        mcp.tool(**kw)(list_projects)
        mcp.tool(**kw)(project_card)
    elif cfg.profile == "marketing":
        mcp.tool(name="search", **kw)(marketing_search)
    elif cfg.profile == "documentation":
        mcp.tool(name="search", **kw)(documentation_search)
    else:
        mcp.tool(name="search", **kw)(plain_search)

    return mcp


def serve(cfg: Config) -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    build_server(cfg).run()

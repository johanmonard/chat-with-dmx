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
   models, years, robots), order numbers and key documents. Old projects (collections such as 2_Hors_Garantie) are less
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
"""


def build_server(cfg: Config) -> MCPServer:
    tools = DocTools(cfg)
    mcp = MCPServer("dmx-docs", instructions=INSTRUCTIONS)
    kw = {"annotations": READ_ONLY} if READ_ONLY is not None else {}

    @mcp.tool(**kw)
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

    @mcp.tool(**kw)
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

    @mcp.tool(**kw)
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
        layouts, photos, tables), or a picture embedded in a Word .docx (photos of FAT reports...).
        Use it when the extracted text is not enough: visual content, garbled tables, values in
        drawings. Reads the live file, so it needs access to the file server.

        Args:
            path: Full file path (as shown in search results).
            page: PDF page number (default 1).
            region: Optional zoom on part of a PDF page, 'x0,y0,x1,y1' as fractions of the page
                (0,0 = top-left), e.g. '0.5,0.5,1,1' = bottom-right quarter. Rendered sharper.
            image: For .docx files: number of the embedded picture (1 = first, document order).
        """
        caption, data, fmt = tools.view_page(path, page=page, region=region, image=image)
        return [caption, Image(data=data, format=fmt)]

    @mcp.tool(**kw)
    def export_image(path: str, page: int = 1, region: str | None = None, image: int | None = None,
                     name: str | None = None) -> str:
        """Save a picture from a document as an image FILE on the user's PC, to use it elsewhere
        (presentation, report, email): a PDF page or a zoomed region of it (rendered sharply, up
        to 2400 px), or a picture embedded in a .docx (original resolution). Returns the file path,
        in the user's local export folder (default C:\\dmx-rag\\exports).
        Check the page/region first with view_page.

        Args:
            path: Full file path (as shown in search results).
            page: PDF page number (default 1).
            region: Optional part of a PDF page, 'x0,y0,x1,y1' as fractions (0,0 = top-left).
            image: For .docx files: number of the embedded picture (1 = first).
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

    return mcp


def serve(cfg: Config) -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    build_server(cfg).run()

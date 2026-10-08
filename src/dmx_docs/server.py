"""MCP server (stdio) exposing the document tools to Claude Desktop / Claude Code."""

from __future__ import annotations

import logging
import sys

try:  # mcp >= 2
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

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
(code names like THOR, YAKUMA, ANGE) with the same subfolders: 0_Vente (offers,
specifications), 1_Finances, 2_Electrique (schematics), 3_Mecanique, 4_Soft,
5_Gestion (follow-up, FAT/SAT acceptance), 6_Rapports_Tests, 7_Photos_Videos,
8_Documentation (manuals), 9_SAV.

Never answer from the first search alone. Follow this loop for every question:

1. REFORMULATE. Rewrite the question into 2-4 search queries: precise French technical terms
   and synonyms (préhenseur/ventouse/pince, cadence/débit/produits par minute, FAT/réception
   usine, SAT/mise en service), English/German equivalents, exact codes in "double quotes".
   If the question has several parts or spans several projects, make one query per part.
   If it names a project, client or machine, also run `find_files` and use `folder=` to
   restrict searches to that project.
2. RETRIEVE with `search` (hybrid by default; mode="keyword" for codes and names).
3. EVALUATE each hit before using it: does the excerpt actually address the question (not
   just share words)? Each hit shows how it matched and a meaning similarity: "strong"
   (>= 0.86) is usually on topic, "medium" must be checked by reading, "weak" (< 0.84) is
   probably off topic. Are all parts of the question covered? Do sources contradict each other
   (versions, dates)? Open the best documents with `read_document` around the matching pages
   (excerpts are short, tables and context matter) and use `find_in_document` in long files.
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
               modified_after: str | None = None, limit: int = 10, mode: str = "hybrid") -> str:
        """Search the documentation by meaning and keywords. Returns excerpts with file path and page,
        how each one matched (keyword, semantic or both) and its meaning similarity to the query
        (strong >= 0.86, medium 0.84-0.86, weak < 0.84 = probably off topic).

        Args:
            query: What to look for (any language; documents are mostly French).
                Use "double quotes" for exact phrases or codes, and word* for prefix matches.
            folder: Optional folder path to restrict the search to (includes subfolders).
            file_type: Optional 'pdf', 'docx' or 'doc'.
            modified_after: Optional date (YYYY, YYYY-MM or YYYY-MM-DD) - only files modified since then.
            limit: Number of results (1-30, default 10). At most 3 excerpts per document.
            mode: 'hybrid' (default), 'keyword' (exact words, codes, names) or 'semantic' (by meaning).
        """
        return tools.search(query, folder=folder, file_type=file_type,
                            modified_after=modified_after, limit=limit, mode=mode)

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
    def index_status() -> str:
        """Show what is indexed: number of documents per status and type, embeddings coverage,
        root folders and last indexing date."""
        return tools.index_status()

    return mcp


def serve(cfg: Config) -> None:
    logging.basicConfig(level=logging.INFO, stream=sys.stderr,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    build_server(cfg).run()

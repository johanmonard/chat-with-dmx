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
These tools give read-only access to the company's documentation on its file servers
(PDF and Word files, mostly in French, sometimes English, German or Spanish).

How to research a question:
1. Start with `search`. Run several searches with different wordings: French terms first,
   then synonyms, English (or German/Spanish) equivalents, product names, part numbers or
   project codes. Put exact codes or phrases in "double quotes".
2. Use `find_files` when the user mentions a project, client, machine or document name:
   file and folder names often carry this information.
3. Search results are short excerpts. Before answering, open the most relevant documents
   with `read_document` (around the pages that matched) to read the full context, follow
   references to other documents, and check tables.
4. Use `list_folder` to explore the folder around a relevant document (related specs,
   reports, later versions) and `find_in_document` to locate a term inside a long file.
5. Prefer the most recent document when versions disagree, and say so.

Always cite your sources as full file path + page, e.g. Z:\\Projets\\P123\\Spec.pdf (p. 12).
If the documents do not contain the answer, say so instead of guessing.
Answer in the language of the user's question.
"""


def build_server(cfg: Config) -> MCPServer:
    tools = DocTools(cfg)
    mcp = MCPServer("dmx-docs", instructions=INSTRUCTIONS)
    kw = {"annotations": READ_ONLY} if READ_ONLY is not None else {}

    @mcp.tool(**kw)
    def search(query: str, folder: str | None = None, file_type: str | None = None,
               modified_after: str | None = None, limit: int = 10, mode: str = "hybrid") -> str:
        """Search the documentation by meaning and keywords. Returns excerpts with file path and page.

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

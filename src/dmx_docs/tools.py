"""Tool logic exposed to Claude (kept separate from MCP wiring so it is easy to test).

Every tool is read-only and limited to the configured root folders.
"""

from __future__ import annotations

import os
import re
from datetime import datetime

from . import sources, store
from .config import Config
from .extract import extract_file
from .search import Searcher, build_fts_query, fold, make_snippet

TYPE_LABEL = {".pdf": "PDF", ".docx": "Word", ".doc": "Word 97-2003"}

# Query/passage cosine similarity with multilingual-e5-large, calibrated on the indexed
# documents: on-topic questions score 0.85-0.89 at best, off-topic ones 0.80-0.83.
CALIBRATED_MODEL = "intfloat/multilingual-e5-large"
SIM_STRONG = 0.86
SIM_MEDIUM = 0.84


def _strength(sim: float) -> str:
    return "strong" if sim >= SIM_STRONG else "medium" if sim >= SIM_MEDIUM else "weak"


def _date(ts: float | None) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d") if ts else "?"


def _size(n: int | None) -> str:
    if n is None:
        return "?"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} B"


def _page_label(ext: str) -> str:
    return "page" if ext == ".pdf" else "page (approx.)"


class DocTools:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.searcher = Searcher(cfg)

    def _con(self):
        con = store.connect(self.cfg.db_path, create=False)
        # Root/excluded folders may have changed on the configuration page.
        sources.refresh(self.cfg, con)
        return con

    def _resolve(self, path: str) -> tuple[str, str]:
        path = path.strip().strip('"')
        key = store.path_key(path)
        if not self.cfg.allows(key):
            raise ValueError(f"'{path}' is outside the indexed folders (roots: {', '.join(self.cfg.roots)}; "
                             "some subfolders may be excluded)")
        return path, key

    # ------------------------------------------------------------ search
    def search(self, query: str, folder: str | None = None, file_type: str | None = None,
               modified_after: str | None = None, limit: int = 10, mode: str = "hybrid") -> str:
        if mode not in ("hybrid", "keyword", "semantic"):
            raise ValueError("mode must be 'hybrid', 'keyword' or 'semantic'")
        limit = max(1, min(int(limit), 30))
        con = self._con()
        try:
            if folder:
                folder, _ = self._resolve(folder)
            hits = self.searcher.search(con, query, limit=limit, folder=folder, file_type=file_type,
                                        modified_after=modified_after, mode=mode,
                                        allowed=self.cfg.allows)
        finally:
            con.close()
        notes = []
        if mode != "keyword" and self.searcher.embedder_error:
            notes.append(f"(semantic search unavailable: {self.searcher.embedder_error}; keyword results only)")
        elif mode != "keyword" and self.searcher.vectors.ids is not None and len(self.searcher.vectors.ids) == 0:
            notes.append("(no embeddings yet - keyword results only; run `dmx-docs embed`)")
        if not hits:
            return "\n".join(notes + [f"No results for: {query}. Try other words, synonyms or "
                                      "another language (documents are mostly French)."])
        calibrated = self.cfg.embedding_model == CALIBRATED_MODEL
        sims = [h.similarity for h in hits if h.similarity is not None]
        if calibrated and sims and max(sims) < SIM_MEDIUM:
            notes.append(f"(all matches are weak - best meaning similarity {max(sims):.2f}: the documents "
                         "probably do not cover this as phrased; try other terms or another angle)")
        lines = notes + [f"{len(hits)} results for: {query}\n"]
        for i, h in enumerate(hits, 1):
            match = "+".join(h.sources)
            if h.similarity is not None:
                match += f", similarity {h.similarity:.2f}"
                if calibrated:
                    match += " " + _strength(h.similarity)
            lines.append(f"[{i}] {h.path} — {_page_label(h.ext)} {h.page_no}/{h.n_pages} "
                         f"({TYPE_LABEL.get(h.ext, h.ext)}, modified {_date(h.mtime)}) [{match}]")
            lines.append("    " + make_snippet(h.text, query))
            lines.append("")
        return "\n".join(lines).rstrip()

    # --------------------------------------------------------- find_files
    def find_files(self, name: str, folder: str | None = None, limit: int = 30) -> str:
        limit = max(1, min(int(limit), 100))
        fts = build_fts_query(name.replace("*", " "), mode="AND")
        if not fts:
            return "Please give at least one word of the file or folder name."
        fts = " AND ".join(t + "*" if not t.endswith("*") else t for t in fts.split(" AND "))
        clauses, params = [], []
        con = self._con()
        try:
            if folder:
                _, key = self._resolve(folder)
                prefix = key.rstrip(os.sep) + os.sep
                clauses.append("substr(d.path_key, 1, ?) = ?")
                params += [len(prefix), prefix]
            where = " AND ".join(["docs_fts MATCH ?"] + clauses)
            rows = [r for r in con.execute(
                f"""SELECT d.path, d.path_key, d.ext, d.size, d.mtime, d.n_pages, d.status FROM docs_fts f
                    JOIN docs d ON d.id = f.rowid WHERE {where} ORDER BY f.rank LIMIT ?""",
                [fts] + params + [limit * 3]) if self.cfg.allows(r["path_key"])][:limit]
        finally:
            con.close()
        if not rows:
            return f"No indexed file matches '{name}'."
        lines = [f"{len(rows)} files matching '{name}':"]
        for r in rows:
            extra = "" if r["status"] == "ok" else f", status: {r['status']}"
            lines.append(f"- {r['path']} ({TYPE_LABEL.get(r['ext'], r['ext'])}, {r['n_pages'] or 0} pages, "
                         f"{_size(r['size'])}, modified {_date(r['mtime'])}{extra})")
        return "\n".join(lines)

    # -------------------------------------------------------- list_folder
    def list_folder(self, path: str | None = None, limit: int = 200) -> str:
        limit = max(1, min(int(limit), 1000))
        con = self._con()
        try:
            if not path:
                if not self.cfg.roots:
                    return "No root folder is configured yet."
                lines = ["Indexed root folders:"]
                for root, rk in zip(self.cfg.roots, self.cfg.root_keys):
                    prefix = rk.rstrip(os.sep) + os.sep
                    n = con.execute("SELECT count(*) FROM docs WHERE substr(path_key, 1, ?) = ?",
                                    (len(prefix), prefix)).fetchone()[0]
                    state = "reachable" if os.path.isdir(root) else "NOT reachable"
                    lines.append(f"- {root} ({n} indexed documents, {state})")
                if self.cfg.excluded_dirs:
                    lines.append("Excluded folders: " + ", ".join(self.cfg.excluded_dirs))
                return "\n".join(lines)
            path, key = self._resolve(path)
            indexed = {r["path_key"]: r for r in con.execute(
                "SELECT path_key, status, n_pages FROM docs WHERE folder_key = ?", (key,))}
        finally:
            con.close()

        try:
            with os.scandir(path) as it:
                entries = sorted(it, key=lambda e: (not e.is_dir(), e.name.lower()))
        except OSError as e:
            if not indexed:
                return f"Cannot open folder {path}: {e}"
            lines = [f"{path} (folder not reachable right now; indexed files only):"]
            for k, r in sorted(indexed.items()):
                lines.append(f"- {os.path.basename(k)} ({r['n_pages']} pages, {r['status']})")
            return "\n".join(lines)

        folders, files = [], []
        for e in entries:
            if self.cfg.is_excluded(e.name, e.path):
                continue
            try:
                if e.is_dir():
                    if not self.cfg.is_excluded_dir(store.path_key(e.path)):
                        folders.append(f"[folder] {e.name}")
                    continue
                st = e.stat()
            except OSError:
                continue
            r = indexed.get(store.path_key(e.path))
            if r is not None:
                info = f"indexed, {r['n_pages'] or 0} pages" if r["status"] == "ok" else f"status: {r['status']}"
            else:
                ext = os.path.splitext(e.name)[1].lower()
                info = "not indexed yet" if ext in self.cfg.extensions else "not indexed (file type)"
            files.append(f"{e.name} ({_size(st.st_size)}, modified {_date(st.st_mtime)}, {info})")
        items = folders + files
        lines = [f"{path}: {len(folders)} folders, {len(files)} files"]
        lines += ["- " + x for x in items[:limit]]
        if len(items) > limit:
            lines.append(f"... {len(items) - limit} more (increase limit)")
        return "\n".join(lines)

    # ------------------------------------------------------ document text
    def _pages(self, path: str, key: str) -> tuple[dict[int, str], int, dict]:
        """Return ({page_no: text}, n_pages, info). Uses the index, or extracts live if needed."""
        con = self._con()
        try:
            doc = con.execute("SELECT * FROM docs WHERE path_key = ?", (key,)).fetchone()
            info = {"source": "index"}
            fresh = False
            if doc is not None:
                try:
                    st = os.stat(store.fs_path(path))
                    fresh = st.st_size == doc["size"] and abs(st.st_mtime - (doc["mtime"] or 0)) < 0.01
                except OSError:
                    fresh = True  # file not reachable: serve the indexed copy
                if fresh and doc["status"] in ("ok", "no_text", "empty"):
                    pages: dict[int, str] = {}
                    for r in con.execute("SELECT page_no, text FROM chunks WHERE doc_id = ? "
                                         "ORDER BY page_no, seq", (doc["id"],)):
                        pages[r["page_no"]] = pages.get(r["page_no"], "") + r["text"]
                    info.update(ext=doc["ext"], mtime=doc["mtime"], status=doc["status"])
                    return pages, doc["n_pages"] or 0, info
        finally:
            con.close()
        if not os.path.isfile(store.fs_path(path)):
            raise FileNotFoundError(f"File not found: {path}")
        ext = os.path.splitext(path)[1].lower()
        result = extract_file(path, self.cfg.extract_options())
        if result.status == "error" or result.status == "skipped":
            raise ValueError(f"Cannot read {path}: {result.error}")
        info = {"source": "live extraction (file new or changed since indexing)", "ext": ext,
                "mtime": os.stat(store.fs_path(path)).st_mtime, "status": result.status}
        return dict(result.pages), result.n_pages, info

    def read_document(self, path: str, start_page: int = 1, end_page: int | None = None) -> str:
        path, key = self._resolve(path)
        pages, n_pages, info = self._pages(path, key)
        start_page = max(1, int(start_page))
        last = n_pages or (max(pages) if pages else 0)
        end_page = min(int(end_page), last) if end_page else last
        if start_page > last:
            return f"{path} has {last} pages; start_page {start_page} is beyond the end."
        label = _page_label(info.get("ext", ""))
        out, used, shown_until = [], 0, start_page - 1
        for p in range(start_page, end_page + 1):
            text = pages.get(p, "")
            block = f"--- {label} {p} ---\n{text if text else '[no text on this page]'}\n"
            if used + len(block) > self.cfg.max_read_chars and out:
                break
            if len(block) > self.cfg.max_read_chars:
                block = block[:self.cfg.max_read_chars] + "\n[... page truncated ...]\n"
            out.append(block)
            used += len(block)
            shown_until = p
        header = (f"{path} ({TYPE_LABEL.get(info.get('ext'), info.get('ext'))}, {last} pages, "
                  f"modified {_date(info.get('mtime'))}; source: {info['source']})")
        if info.get("status") == "no_text":
            header += "\nNote: this PDF has (almost) no text layer — probably a scan; content may be missing."
        footer = ""
        if shown_until < end_page:
            footer = f"\n[Showing {label}s {start_page}-{shown_until} of {last}. " \
                     f"Continue with start_page={shown_until + 1}.]"
        elif shown_until < last:
            footer = f"\n[Showing {label}s {start_page}-{shown_until} of {last}.]"
        return header + "\n\n" + "\n".join(out) + footer

    def find_in_document(self, path: str, text: str, max_hits: int = 20) -> str:
        path, key = self._resolve(path)
        pages, n_pages, info = self._pages(path, key)
        needle = fold(" ".join(text.split()))
        if not needle:
            return "Please give the text to look for."
        max_hits = max(1, min(int(max_hits), 100))
        label = _page_label(info.get("ext", ""))
        hits = []
        total = 0
        for p in sorted(pages):
            page_text = " ".join(pages[p].split())
            folded = fold(page_text)
            for m in re.finditer(re.escape(needle), folded):
                total += 1
                if len(hits) < max_hits:
                    a, b = max(0, m.start() - 250), min(len(page_text), m.end() + 250)
                    hits.append(f"[{label} {p}] …{page_text[a:b]}…")
        if not hits:
            return f"'{text}' not found in {path} ({n_pages} pages; search ignores case and accents)."
        more = f" (showing first {len(hits)})" if total > len(hits) else ""
        return f"{total} occurrences of '{text}' in {path}{more}:\n\n" + "\n\n".join(hits)

    # ------------------------------------------------------------ status
    def index_status(self) -> str:
        con = self._con()
        try:
            by_status = con.execute("SELECT status, count(*) n FROM docs GROUP BY status").fetchall()
            by_ext = con.execute("SELECT ext, count(*) n FROM docs WHERE status='ok' GROUP BY ext").fetchall()
            chunks = con.execute("SELECT count(*), sum(embedded) FROM chunks").fetchone()
            last = store.get_meta(con, "last_index_finished")
            model = store.get_meta(con, "embedding_model")
        finally:
            con.close()
        total = sum(r["n"] for r in by_status)
        lines = [f"Indexed documents: {total}",
                 "By status: " + ", ".join(f"{r['status']}={r['n']}" for r in by_status),
                 "Readable by type: " + ", ".join(f"{TYPE_LABEL.get(r['ext'], r['ext'])}={r['n']}" for r in by_ext),
                 f"Chunks: {chunks[0]}, with embeddings: {chunks[1] or 0}"
                 + (f" (model {model})" if model else ""),
                 f"Last full index run finished: {_date(float(last)) if last else 'never'}",
                 "Roots: " + (", ".join(self.cfg.roots) or "none"),
                 "Excluded folders: " + (", ".join(self.cfg.excluded_dirs) or "none"),
                 f"Indexed file types: {', '.join(self.cfg.extensions)} (other types, e.g. Excel, are not searchable yet)"]
        return "\n".join(lines)

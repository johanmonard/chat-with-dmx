"""Command line: dmx-docs web | index | embed | status | search | read | serve."""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

from .config import load_config


def _setup_logging(cfg, name: str) -> None:
    cfg.logs_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(cfg.logs_dir / f"{name}-{time.strftime('%Y%m%d')}.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def main(argv: list[str] | None = None) -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    parser = argparse.ArgumentParser(prog="dmx-docs", description=__doc__)
    parser.add_argument("--config", default=os.environ.get("DMX_DOCS_CONFIG", "config.toml"),
                        help="path to config.toml (default: ./config.toml or $DMX_DOCS_CONFIG)")
    parser.add_argument("--world", default=None,
                        help="world (family of documents) to use, e.g. projects or marketing "
                             "(default: $DMX_DOCS_WORLD, else projects)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("web", help="open the configuration page (folders, scans, status)")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--host", default="127.0.0.1",
                   help="network interface (default: this computer only)")
    p.add_argument("--no-browser", action="store_true", help="don't open the browser")

    p = sub.add_parser("index", help="crawl the root folders and update the index")
    p.add_argument("--retry-errors", action="store_true", help="re-process files that failed before")

    p = sub.add_parser("embed", help="compute embeddings for chunks that have none yet")
    p.add_argument("--max-minutes", type=float, help="stop after this many minutes (resume later)")
    p.add_argument("--reset", action="store_true", help="drop all embeddings and start over")

    sub.add_parser("status", help="show index statistics")

    p = sub.add_parser("search", help="try a search from the command line")
    p.add_argument("query")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--mode", default="hybrid", choices=["hybrid", "keyword", "semantic"])
    p.add_argument("--folder")

    p = sub.add_parser("read", help="print a document as the read_document tool returns it")
    p.add_argument("path")
    p.add_argument("--start-page", type=int, default=1)
    p.add_argument("--end-page", type=int)

    sub.add_parser("serve", help="run the MCP server (stdio) for Claude Desktop / Claude Code")

    args = parser.parse_args(argv)
    config_path = Path(args.config)
    if not config_path.exists():
        parser.error(f"config file not found: {config_path.resolve()} "
                     "(copy config.example.toml to config.toml and edit it)")
    try:
        cfg = load_config(config_path, world=args.world)
    except ValueError as e:
        parser.error(str(e))
    from .sources import refresh
    refresh(cfg)  # root/excluded folders are managed on the configuration page

    if args.command == "web":
        from .web import run_web
        run_web(cfg, host=args.host, port=args.port, open_browser=not args.no_browser)
        return

    if args.command == "serve":
        from .server import serve
        serve(cfg)
        return

    if args.command == "index":
        from .indexer import run_index
        _setup_logging(cfg, "index")
        print(f"Indexing {', '.join(cfg.roots)} -> {cfg.db_path} ({cfg.workers} workers)")
        started = time.monotonic()
        try:
            stats = run_index(cfg, retry_errors=args.retry_errors)
        except KeyboardInterrupt:
            print("Stopped. Run `dmx-docs index` again to continue.")
            sys.exit(130)
        print(f"Done in {(time.monotonic() - started) / 60:.1f} min: {stats.summary()}")
        print(f"Details (errors, skipped files): {cfg.logs_dir}")
        if cfg.embeddings_enabled:
            print("Next: `dmx-docs embed` to enable semantic search for new content.")
    elif args.command == "embed":
        from .embeddings import run_embed
        _setup_logging(cfg, "embed")
        try:
            run_embed(cfg, max_minutes=args.max_minutes, reset=args.reset)
        except KeyboardInterrupt:
            print("Stopped. Run `dmx-docs embed` again to continue.")
            sys.exit(130)
    elif args.command == "status":
        from .tools import DocTools
        print(DocTools(cfg).index_status())
    elif args.command == "search":
        from .tools import DocTools
        print(DocTools(cfg).search(args.query, folder=args.folder, limit=args.limit, mode=args.mode))
    elif args.command == "read":
        from .tools import DocTools
        print(DocTools(cfg).read_document(args.path, args.start_page, args.end_page))


if __name__ == "__main__":
    main()

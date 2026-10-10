"""Configuration loading (TOML)."""

from __future__ import annotations

import fnmatch
import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_EXCLUDES = ["~$*", ".*", "$RECYCLE.BIN", "System Volume Information"]
WORLD_NAME = re.compile(r"[a-z0-9_-]+")
# Facet rules, MCP tools and server instructions of a world (see facets.py and server.py).
PROFILES = ("projects", "marketing", "documentation", "none")


def _default_workers() -> int:
    # Extraction is mostly CPU work (sorted PDF text); one core is left for the main process.
    return max(1, min(16, (os.cpu_count() or 2) - 1))


@dataclass
class Config:
    # Root folders and excluded folders are managed on the configuration page
    # (stored in the index database, see sources.py); `roots` in config.toml
    # only seeds them on first use.
    roots: list[str]
    data_dir: Path
    excluded_dirs: list[str] = field(default_factory=list)
    extensions: list[str] = field(default_factory=lambda: [".pdf", ".docx", ".doc"])
    exclude: list[str] = field(default_factory=lambda: list(DEFAULT_EXCLUDES))
    max_file_mb: float = 300
    max_pdf_pages: int = 3000
    workers: int = field(default_factory=_default_workers)
    doc_converter: str = "auto"
    libreoffice_path: str | None = None

    embeddings_enabled: bool = True
    embedding_model: str = "intfloat/multilingual-e5-large"
    query_prefix: str = "query: "
    passage_prefix: str = "passage: "
    embed_batch_size: int = 1
    embed_gpu_batch_size: int = 64
    embed_threads: int | None = None
    embed_device: str = "auto"  # 'auto' (GPU if available), 'cpu' or 'cuda'

    max_read_chars: int = 40000
    thesaurus_path: str | None = None   # thesaurus.toml next to config.toml by default
    expand_synonyms: bool = False       # expand keyword searches with the thesaurus
    register_path: str | None = None    # register.csv (installed machines) next to config.toml by default
    export_dir: str | None = None  # where export_image saves files (default: ~\Claude\dmx-images)
    config_path: Path | None = None
    # A world is one family of documents with its own index (projects, marketing...), chosen
    # with --world. None: a config without [worlds] tables, one index directly in data_dir.
    world: str | None = None
    world_title: str | None = None
    profile: str = "projects"
    # OCR of PDF pages without text (ocr.py). The language files live in tessdata_dir.
    ocr_enabled: bool = True
    ocr_languages: str = "fra"
    ocr_dpi: int = 300
    ocr_tessdata: str | None = None

    def __post_init__(self) -> None:
        self.extensions = [e.lower() if e.startswith(".") else "." + e.lower() for e in self.extensions]
        self.set_sources(self.roots, self.excluded_dirs, self.exclude)

    def set_sources(self, roots: list[str], excluded_dirs: list[str], patterns: list[str]) -> None:
        from .store import path_key

        self.roots = list(roots)
        self.excluded_dirs = list(excluded_dirs)
        self.exclude = list(patterns)
        self.root_keys = [path_key(r) for r in self.roots]
        self.excluded_keys = {path_key(d) for d in self.excluded_dirs}
        self._name_patterns = [p.lower() for p in self.exclude if "/" not in p]
        self._path_patterns = [p.lower() for p in self.exclude if "/" in p]

    def is_excluded_dir(self, key: str) -> bool:
        """True if the folder (path_key) or one of its parents is excluded."""
        if not self.excluded_keys:
            return False
        from .store import is_under

        return any(is_under(key, ex) for ex in self.excluded_keys)

    def allows(self, key: str) -> bool:
        """True if a path (path_key) is inside a root and not in an excluded folder."""
        from .store import is_under

        return any(is_under(key, rk) for rk in self.root_keys) and not self.is_excluded_dir(key)

    @property
    def world_dir(self) -> Path:
        """Local folder of this world's index, search cache and logs."""
        return self.data_dir / self.world if self.world else self.data_dir

    @property
    def db_path(self) -> Path:
        return self.world_dir / "index.sqlite3"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"  # one embedding model for every world

    @property
    def logs_dir(self) -> Path:
        return self.world_dir / "logs"

    @property
    def tessdata_dir(self) -> Path:
        """Tesseract language files: [ocr] tessdata, else next to data_dir (C:\\dmx-rag\\tessdata)."""
        return Path(self.ocr_tessdata) if self.ocr_tessdata else self.data_dir.parent / "tessdata"

    @property
    def server_name(self) -> str:
        """MCP server name in Claude Desktop (dmx-docs for the project documentation)."""
        return "dmx-docs" if self.world in (None, "projects") else f"dmx-{self.world}"

    def is_excluded(self, name: str, path: str) -> bool:
        name_l = name.lower()
        if any(fnmatch.fnmatchcase(name_l, p) for p in self._name_patterns):
            return True
        if self._path_patterns:
            path_l = path.replace("\\", "/").lower()
            return any(fnmatch.fnmatchcase(path_l, p) for p in self._path_patterns)
        return False

    def extract_options(self) -> dict:
        """Plain-dict options passed to extraction worker processes."""
        return {
            "max_pdf_pages": self.max_pdf_pages,
            "doc_converter": self.doc_converter,
            "libreoffice_path": self.libreoffice_path,
        }


def load_config(path: str | os.PathLike, world: str | None = None) -> Config:
    """Read config.toml. When it has [worlds.<name>] tables, one world is used: `world`, else
    $DMX_DOCS_WORLD, else 'projects'. A world table may set title, profile, extensions, exclude,
    roots (first-run seed) and register; all other settings are shared by every world."""
    path = Path(path)
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    idx = dict(raw.get("index", {}))
    emb = raw.get("embeddings", {})
    srv = raw.get("server", {})
    worlds = raw.get("worlds") or {}

    name = world or os.environ.get("DMX_DOCS_WORLD") or None
    if worlds:
        name = name or ("projects" if "projects" in worlds else None)
        if name not in worlds:
            raise ValueError(f"Unknown world '{name or ''}'. Configured worlds in {path}: {', '.join(worlds)}")
        if not WORLD_NAME.fullmatch(name):
            raise ValueError(f"World names use lowercase letters, digits, '-' and '_' only: '{name}'")
    elif name:
        raise ValueError(f"{path} has no [worlds.<name>] tables: --world {name} cannot be used")
    w = worlds.get(name, {}) if name else {}
    for key in ("roots", "extensions", "exclude"):
        if key in w:
            idx[key] = w[key]

    roots = idx.get("roots") or []
    data_dir = Path(idx.get("data_dir", "data"))
    if not data_dir.is_absolute():
        data_dir = (path.parent / data_dir).resolve()

    kwargs: dict = {"roots": [str(r) for r in roots], "data_dir": data_dir, "config_path": path.resolve()}
    if name:
        profile = w.get("profile", "none")
        if profile not in PROFILES:
            raise ValueError(f"World '{name}': unknown profile '{profile}' (use {', '.join(PROFILES)})")
        kwargs.update(world=name, world_title=w.get("title") or name.capitalize(), profile=profile)
    for key in ("extensions", "exclude", "max_file_mb", "max_pdf_pages", "workers",
                "doc_converter", "libreoffice_path"):
        if key in idx:
            kwargs[key] = idx[key]
    mapping = {
        "enabled": "embeddings_enabled",
        "model": "embedding_model",
        "query_prefix": "query_prefix",
        "passage_prefix": "passage_prefix",
        "batch_size": "embed_batch_size",
        "gpu_batch_size": "embed_gpu_batch_size",
        "threads": "embed_threads",
        "device": "embed_device",
    }
    for key, attr in mapping.items():
        if key in emb:
            kwargs[attr] = emb[key]
    if "max_read_chars" in srv:
        kwargs["max_read_chars"] = srv["max_read_chars"]
    srch = raw.get("search", {})
    thesaurus = srch.get("thesaurus", "thesaurus.toml")
    if thesaurus:
        tp = Path(os.path.expandvars(str(thesaurus)))
        kwargs["thesaurus_path"] = str(tp if tp.is_absolute() else (path.parent / tp))
    kwargs["expand_synonyms"] = bool(srch.get("expand", False))
    if name:  # the machine register belongs to a world: data_dir\<world>\register.csv
        register = w.get("register", "register.csv" if kwargs["profile"] == "projects" else None)
        if register:
            rp = Path(os.path.expandvars(str(register)))
            kwargs["register_path"] = str(rp if rp.is_absolute() else data_dir / name / rp)
    else:
        register = srch.get("register", "register.csv")
        if register:
            rp = Path(os.path.expandvars(str(register)))
            kwargs["register_path"] = str(rp if rp.is_absolute() else (path.parent / rp))
    if srv.get("export_dir"):
        kwargs["export_dir"] = os.path.expandvars(os.path.expanduser(str(srv["export_dir"])))
    ocr = raw.get("ocr", {})
    for key, attr in (("enabled", "ocr_enabled"), ("languages", "ocr_languages"),
                      ("dpi", "ocr_dpi"), ("tessdata", "ocr_tessdata")):
        if key in ocr:
            kwargs[attr] = ocr[key]
    if name and "ocr" in w:  # a world can switch OCR off
        kwargs["ocr_enabled"] = bool(w["ocr"])
    return Config(**kwargs)

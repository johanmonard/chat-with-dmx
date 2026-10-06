"""Configuration loading (TOML)."""

from __future__ import annotations

import fnmatch
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_EXCLUDES = ["~$*", ".*", "$RECYCLE.BIN", "System Volume Information"]


def _default_workers() -> int:
    return max(1, min(8, (os.cpu_count() or 2) - 1))


@dataclass
class Config:
    roots: list[str]
    data_dir: Path
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
    embed_batch_size: int = 16
    embed_threads: int | None = None

    max_read_chars: int = 40000

    def __post_init__(self) -> None:
        self.extensions = [e.lower() if e.startswith(".") else "." + e.lower() for e in self.extensions]
        self._name_patterns = [p.lower() for p in self.exclude if "/" not in p]
        self._path_patterns = [p.lower() for p in self.exclude if "/" in p]

    @property
    def db_path(self) -> Path:
        return self.data_dir / "index.sqlite3"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

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


def load_config(path: str | os.PathLike) -> Config:
    path = Path(path)
    with open(path, "rb") as f:
        raw = tomllib.load(f)
    idx = raw.get("index", {})
    emb = raw.get("embeddings", {})
    srv = raw.get("server", {})

    roots = idx.get("roots") or []
    if not roots:
        raise ValueError(f"{path}: [index] roots is empty")
    data_dir = Path(idx.get("data_dir", "data"))
    if not data_dir.is_absolute():
        data_dir = (path.parent / data_dir).resolve()

    kwargs: dict = {"roots": [str(r) for r in roots], "data_dir": data_dir}
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
        "threads": "embed_threads",
    }
    for key, attr in mapping.items():
        if key in emb:
            kwargs[attr] = emb[key]
    if "max_read_chars" in srv:
        kwargs["max_read_chars"] = srv["max_read_chars"]
    return Config(**kwargs)

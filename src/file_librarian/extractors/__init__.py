"""Content extractors, one module per format, looked up by extension."""

from __future__ import annotations

from importlib import import_module
from pathlib import Path

from file_librarian.extractors.base import ExtractionError, Extractor

_BY_EXTENSION: dict[str, Extractor] = {}

# Built-in extractor modules, imported on first lookup. Each calls ``register``.
BUILTIN: tuple[str, ...] = ("text", "office", "pdf", "archive", "mail")
_loaded = False


def register(extractor: Extractor) -> Extractor:
    """Register an extractor for each of its extensions. Later registrations win."""
    for ext in extractor.extensions:
        if not ext.startswith(".") or ext != ext.lower():
            raise ValueError(f"extensions must be lower-case and start with '.': {ext!r}")
        _BY_EXTENSION[ext] = extractor
    return extractor


def extractor_for(path: Path) -> Extractor | None:
    global _loaded
    if not _loaded:
        for module in BUILTIN:
            import_module(f"{__name__}.{module}")
        _loaded = True
    return _BY_EXTENSION.get(path.suffix.lower())


__all__ = ["ExtractionError", "Extractor", "extractor_for", "register"]

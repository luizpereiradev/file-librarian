"""The interface every content extractor implements.

One module per format, registered by extension. An extractor turns a file into
plain text (capped at ``max_chars``) or returns ``None`` when it has nothing
readable. It must never raise for a damaged, encrypted or legacy file: raise
``ExtractionError`` and the pipeline falls back to metadata (RF-27).
See docs/adding-an-extractor.md.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class ExtractionError(Exception):
    """The file could not be read; classify it from metadata only."""


class Extractor(Protocol):
    extensions: frozenset[str]
    """Lower-case extensions with the leading dot, e.g. ``frozenset({".pdf"})``."""

    def extract(self, path: Path, max_chars: int) -> str | None: ...

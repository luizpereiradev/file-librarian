"""Plain text, Markdown, CSV and source code (RF-04)."""

from __future__ import annotations

import codecs
from pathlib import Path

from file_librarian.extractors import register
from file_librarian.extractors.base import ExtractionError

TEXT_EXTENSIONS = frozenset(
    {
        # prose and data
        ".txt", ".text", ".md", ".markdown", ".rst", ".csv", ".tsv", ".log",
        ".json", ".jsonl", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".conf",
        ".xml", ".html", ".htm", ".tex", ".srt", ".vtt", ".ics", ".vcf",
        # source code
        ".py", ".js", ".mjs", ".ts", ".tsx", ".jsx", ".go", ".rs", ".java",
        ".kt", ".swift", ".c", ".h", ".cpp", ".hpp", ".cc", ".cs", ".rb",
        ".php", ".sh", ".bash", ".zsh", ".sql", ".r", ".lua", ".pl", ".scala",
        ".css", ".scss", ".vue", ".svelte", ".ipynb",
    }
)  # fmt: skip

_SNIFF = 8192


class TextExtractor:
    extensions = TEXT_EXTENSIONS

    def extract(self, path: Path, max_chars: int) -> str | None:
        try:
            with path.open("rb") as fh:
                raw = fh.read(max_chars * 4 + 4)
        except OSError as exc:
            raise ExtractionError(str(exc)) from exc
        if raw.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
            text = raw.decode("utf-16", errors="replace")
        else:
            if b"\0" in raw[:_SNIFF]:
                return None  # binary despite the extension
            text = raw.decode("utf-8-sig", errors="replace")
        text = text[:max_chars].strip()
        return text or None


register(TextExtractor())

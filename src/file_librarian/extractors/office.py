"""Word, Excel and PowerPoint (OOXML) documents (RF-04, RF-27).

OOXML files are ZIP archives of XML parts, so the standard library is enough:
no third-party parser touches untrusted Office files. Defences:

- only named XML parts are read, never extracted to disk;
- each part is streamed with a hard cap on decompressed bytes (zip bombs);
- a part declaring a DTD is refused (entity expansion);
- reading stops as soon as ``max_chars`` of text are collected.

Password-protected Office files are not ZIP archives at all; they raise
``ExtractionError`` and are classified from metadata (RF-27). Legacy binary
formats (``.doc``, ``.xls``, ``.ppt``) have no extractor on purpose.
"""

from __future__ import annotations

import re
import zipfile
import zlib
from collections.abc import Callable, Iterable, Iterator
from pathlib import Path
from xml.etree.ElementTree import Element, ParseError, XMLPullParser

from file_librarian.extractors import register
from file_librarian.extractors.base import ExtractionError

MAX_PART_BYTES = 32 * 1024 * 1024
# What a damaged or truncated OOXML file raises while we stream it.
# NotImplementedError: zipfile's answer to a corrupted version or compression field.
_DAMAGED = (
    ParseError,
    zipfile.BadZipFile,
    zlib.error,
    EOFError,
    OSError,
    RuntimeError,
    ValueError,
    NotImplementedError,
)
MAX_PARTS = 500
_CHUNK = 64 * 1024


def _local(tag: str) -> str:
    return tag.rpartition("}")[2]


def _elements(parser: XMLPullParser[Element]) -> Iterator[Element]:
    for event in parser.read_events():
        item = event[-1]
        if isinstance(item, Element):
            yield item


def _part_text(
    zf: zipfile.ZipFile, name: str, text_tags: set[str], break_tags: set[str], budget: int
) -> str:
    """Collect text from one XML part, streaming, until ``budget`` characters."""
    parser: XMLPullParser[Element] = XMLPullParser(events=("end",))
    pieces: list[str] = []
    collected = 0
    read = 0
    with zf.open(name) as fh:
        first = True
        while collected < budget:
            chunk = fh.read(_CHUNK)
            if not chunk:
                break
            if first and b"<!DOCTYPE" in chunk[:4096].upper():
                raise ExtractionError(f"{name}: XML with a DTD is not allowed")
            first = False
            read += len(chunk)
            if read > MAX_PART_BYTES:
                break
            parser.feed(chunk)
            for elem in _elements(parser):
                tag = _local(elem.tag)
                if tag in text_tags and elem.text:
                    pieces.append(elem.text)
                    collected += len(elem.text)
                elif tag in break_tags:
                    pieces.append("\n")
                elem.clear()
    return "".join(pieces)


def _collect(
    zf: zipfile.ZipFile,
    parts: Iterable[str],
    text_tags: set[str],
    break_tags: set[str],
    max_chars: int,
) -> str | None:
    out: list[str] = []
    remaining = max_chars
    for name in parts:
        if remaining <= 0:
            break
        try:
            text = _part_text(zf, name, text_tags, break_tags, remaining)
        except KeyError:
            continue
        out.append(text)
        remaining -= len(text)
    joined = re.sub(r"\n{3,}", "\n\n", "".join(out)).strip()
    return joined[:max_chars] or None


_Reader = Callable[[zipfile.ZipFile, list[str], int], "str | None"]


def _extract(path: Path, max_chars: int, kind: str, read: _Reader) -> str | None:
    """Open ``path`` as a ZIP once and hand the open archive to ``read``.

    Any damage maps to ``ExtractionError``: while opening (not a ZIP, or a
    password-protected OLE container) and while reading the parts.
    """
    try:
        zf = zipfile.ZipFile(path)
    except _DAMAGED as exc:
        raise ExtractionError("not a readable Office file (damaged or password-protected)") from exc
    with zf:
        names = zf.namelist()
        if len(names) > 10_000:
            raise ExtractionError("too many parts for an Office file")
        try:
            return read(zf, names, max_chars)
        except _DAMAGED as exc:
            raise ExtractionError(f"cannot read {kind}: {exc}") from exc


def _numbered(names: list[str], prefix: str) -> list[str]:
    pattern = re.compile(rf"^{re.escape(prefix)}(\d+)\.xml$")
    found = [(int(m.group(1)), n) for n in names if (m := pattern.match(n))]
    return [n for _, n in sorted(found)][:MAX_PARTS]


def _docx_text(zf: zipfile.ZipFile, names: list[str], max_chars: int) -> str | None:
    parts = [n for n in names if n == "word/document.xml"]
    return _collect(zf, parts, {"t"}, {"p", "br", "tab"}, max_chars)


def _pptx_text(zf: zipfile.ZipFile, names: list[str], max_chars: int) -> str | None:
    return _collect(zf, _numbered(names, "ppt/slides/slide"), {"t"}, {"p"}, max_chars)


def _xlsx_text(zf: zipfile.ZipFile, names: list[str], max_chars: int) -> str | None:
    sheet_names = _sheet_names(zf) if "xl/workbook.xml" in names else []
    header = f"Sheets: {', '.join(sheet_names)}\n" if sheet_names else ""
    body = _collect(zf, ["xl/sharedStrings.xml"], {"t"}, {"si"}, max_chars)
    text = (header + (body or "")).strip()
    return text[:max_chars] or None


class DocxExtractor:
    extensions = frozenset({".docx", ".docm"})

    def extract(self, path: Path, max_chars: int) -> str | None:
        return _extract(path, max_chars, "document", _docx_text)


class PptxExtractor:
    extensions = frozenset({".pptx", ".pptm"})

    def extract(self, path: Path, max_chars: int) -> str | None:
        return _extract(path, max_chars, "presentation", _pptx_text)


class XlsxExtractor:
    extensions = frozenset({".xlsx", ".xlsm"})

    def extract(self, path: Path, max_chars: int) -> str | None:
        return _extract(path, max_chars, "spreadsheet", _xlsx_text)


def _sheet_names(zf: zipfile.ZipFile) -> list[str]:
    parser: XMLPullParser[Element] = XMLPullParser(events=("start",))
    with zf.open("xl/workbook.xml") as fh:
        data = fh.read(MAX_PART_BYTES)
    if b"<!DOCTYPE" in data[:4096].upper():
        raise ExtractionError("workbook XML with a DTD is not allowed")
    parser.feed(data)
    return [
        elem.attrib["name"]
        for elem in _elements(parser)
        if _local(elem.tag) == "sheet" and "name" in elem.attrib
    ][:50]


register(DocxExtractor())
register(PptxExtractor())
register(XlsxExtractor())

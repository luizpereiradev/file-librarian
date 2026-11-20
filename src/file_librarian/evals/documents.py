"""Synthetic documents, written byte for byte the same every time.

The builders behind the evaluation corpus (``evals/corpus.py``) and the test
suite (``tests/_helpers.py`` re-exports them): minimal but real Word, Excel,
PowerPoint, PDF and e-mail files that flib's extractors read. Every byte
depends only on the arguments: ZIP entries carry a fixed timestamp, e-mails a
fixed date and boundary, so the same corpus always hashes the same.
"""

from __future__ import annotations

import struct
import zipfile
import zlib
from email.message import EmailMessage
from pathlib import Path

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"

ZIP_DATE = (2026, 1, 1, 0, 0, 0)
"""The timestamp of every ZIP entry written here, so archives hash the same each time."""


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def write_zip(path: Path, members: dict[str, bytes | str]) -> Path:
    """A ZIP archive of ``members`` (name -> content) with fixed timestamps."""
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            info = zipfile.ZipInfo(name, date_time=ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            zf.writestr(info, data)
    return path


def make_docx(path: Path, paragraphs: list[str]) -> Path:
    body = "".join(f"<w:p><w:r><w:t>{_xml_escape(p)}</w:t></w:r></w:p>" for p in paragraphs)
    xml = f'<?xml version="1.0"?><w:document xmlns:w="{_W}"><w:body>{body}</w:body></w:document>'
    return write_zip(path, {"[Content_Types].xml": "<Types/>", "word/document.xml": xml})


def make_pptx(path: Path, slides: list[str]) -> Path:
    members: dict[str, bytes | str] = {"[Content_Types].xml": "<Types/>"}
    for i, text in enumerate(slides, start=1):
        members[f"ppt/slides/slide{i}.xml"] = (
            f'<?xml version="1.0"?><p:sld xmlns:p="p" xmlns:a="{_A}"><a:p><a:r>'
            f"<a:t>{_xml_escape(text)}</a:t></a:r></a:p></p:sld>"
        )
    return write_zip(path, members)


def make_xlsx(path: Path, sheets: list[str], strings: list[str]) -> Path:
    sheet_xml = "".join(
        f'<sheet name="{_xml_escape(s)}" sheetId="{i}"/>' for i, s in enumerate(sheets, 1)
    )
    si = "".join(f"<si><t>{_xml_escape(s)}</t></si>" for s in strings)
    return write_zip(
        path,
        {
            "[Content_Types].xml": "<Types/>",
            "xl/workbook.xml": f'<workbook xmlns="{_S}"><sheets>{sheet_xml}</sheets></workbook>',
            "xl/sharedStrings.xml": f'<sst xmlns="{_S}">{si}</sst>',
        },
    )


def _pdf_string(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def make_pdf(path: Path, text: str) -> Path:
    """A minimal one-page PDF with ``text`` in Helvetica, one line per ``\\n``.

    Text is encoded as WinAnsi (Latin-1), so Portuguese accents survive extraction.
    """
    lines = [f"({_pdf_string(line)}) Tj" for line in text.split("\n")]
    stream = f"BT /F1 11 Tf 14 TL 72 720 Td {' T* '.join(lines)} ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
    ]
    return _write_pdf(path, objects)


def make_blank_pdf(path: Path, pages: int = 1) -> Path:
    """A PDF with ``pages`` empty pages and no text layer, like a scan."""
    kids = " ".join(f"{3 + i} 0 R" for i in range(pages))
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode(),
        *[b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] >>" for _ in range(pages)],
    ]
    return _write_pdf(path, objects)


def _write_pdf(path: Path, objects: list[bytes]) -> Path:
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + body + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    out += b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    path.write_bytes(bytes(out))
    return path


def make_eml(
    path: Path,
    body: str = "Your October bill is ready.",
    *,
    html: str | None = None,
    subject: str = "Electricity bill",
    sender: str = "Power Co <billing@power.example>",
    to: str = "someone@mail.example",
    attachments: dict[str, bytes] | None = None,
    date: str = "Mon, 05 Oct 2026 10:00:00 -0300",
) -> Path:
    """A synthetic e-mail message: a plain body, an optional HTML one, attachments."""
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    msg["Date"] = date
    if body:
        msg.set_content(body)
    if html is not None:
        if body:
            msg.add_alternative(html, subtype="html")
        else:
            msg.set_content(html, subtype="html")
    for name, data in (attachments or {}).items():
        msg.add_attachment(data, maintype="application", subtype="octet-stream", filename=name)
    for number, part in enumerate(msg.walk()):
        if part.is_multipart():
            part.set_boundary(f"flib-synthetic-boundary-{number}")  # the default is random
    path.write_bytes(msg.as_bytes())
    return path


def make_png(path: Path, width: int = 8, height: int = 8, shade: int = 0) -> Path:
    """A small, valid, single-colour PNG; ``shade`` makes images of the same size differ."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)  # 8-bit greyscale
    rows = b"".join(b"\0" + bytes([shade % 256]) * width for _ in range(height))
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
    data += chunk(b"IDAT", zlib.compress(rows, 9)) + chunk(b"IEND", b"")
    path.write_bytes(data)
    return path

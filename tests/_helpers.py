"""Shared test helpers. Import from here, not from conftest.py."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_XATTR_NOFOLLOW = 0x0001


def write_xattr(path: Path, name: str, value: bytes) -> None:
    """Set an extended attribute on a synthetic test file (macOS only)."""
    lib = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    lib.setxattr.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_uint32,
        ctypes.c_int,
    ]
    lib.setxattr.restype = ctypes.c_int
    rc = lib.setxattr(os.fsencode(path), name.encode(), value, len(value), 0, _XATTR_NOFOLLOW)
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), str(path))


# --- Synthetic documents ---------------------------------------------------------

_W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_A = "http://schemas.openxmlformats.org/drawingml/2006/main"
_S = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def make_docx(path: Path, paragraphs: list[str]) -> Path:
    body = "".join(f"<w:p><w:r><w:t>{_xml_escape(p)}</w:t></w:r></w:p>" for p in paragraphs)
    xml = f'<?xml version="1.0"?><w:document xmlns:w="{_W}"><w:body>{body}</w:body></w:document>'
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr("word/document.xml", xml)
    return path


def make_pptx(path: Path, slides: list[str]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        for i, text in enumerate(slides, start=1):
            xml = (
                f'<?xml version="1.0"?><p:sld xmlns:p="p" xmlns:a="{_A}"><a:p><a:r>'
                f"<a:t>{_xml_escape(text)}</a:t></a:r></a:p></p:sld>"
            )
            zf.writestr(f"ppt/slides/slide{i}.xml", xml)
    return path


def make_xlsx(path: Path, sheets: list[str], strings: list[str]) -> Path:
    sheet_xml = "".join(
        f'<sheet name="{_xml_escape(s)}" sheetId="{i}"/>' for i, s in enumerate(sheets, 1)
    )
    si = "".join(f"<si><t>{_xml_escape(s)}</t></si>" for s in strings)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("[Content_Types].xml", "<Types/>")
        zf.writestr(
            "xl/workbook.xml", f'<workbook xmlns="{_S}"><sheets>{sheet_xml}</sheets></workbook>'
        )
        zf.writestr("xl/sharedStrings.xml", f'<sst xmlns="{_S}">{si}</sst>')
    return path


def make_pdf(path: Path, text: str) -> Path:
    """A minimal one-page PDF with a Helvetica text line."""
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    stream = f"BT /F1 12 Tf 72 720 Td ({escaped}) Tj ET".encode("latin-1")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
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

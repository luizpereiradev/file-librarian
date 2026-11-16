from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

import pytest
from pypdf import PdfWriter

from file_librarian.extractors import ExtractionError, extractor_for
from tests._helpers import make_docx, make_pdf, make_pptx, make_xlsx


def extract(path: Path, max_chars: int = 4000) -> str | None:
    extractor = extractor_for(path)
    assert extractor is not None, f"no extractor for {path.suffix}"
    return extractor.extract(path, max_chars)


@pytest.mark.parametrize("name", ["notes.txt", "README.md", "data.csv", "main.py", "a.JSON"])
def test_text_formats(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_text("Orçamento da reforma\nlinha 2\n", encoding="utf-8")
    assert extract(path) == "Orçamento da reforma\nlinha 2"


def test_text_respects_max_chars_and_skips_binary(tmp_path: Path) -> None:
    long = tmp_path / "long.txt"
    long.write_text("x" * 10_000, encoding="utf-8")
    assert extract(long, max_chars=100) == "x" * 100
    binary = tmp_path / "fake.txt"
    binary.write_bytes(b"\x00\x01\x02binary")
    assert extract(binary) is None


def test_utf16_text(tmp_path: Path) -> None:
    path = tmp_path / "export.csv"
    path.write_bytes("nome;valor\nAção;10\n".encode("utf-16"))
    assert extract(path) == "nome;valor\nAção;10"


def test_docx(tmp_path: Path) -> None:
    path = make_docx(tmp_path / "contrato.docx", ["Contrato de locação", "Cláusula 1 & 2"])
    assert extract(path) == "Contrato de locação\nCláusula 1 & 2"


def test_pptx_slides_in_order(tmp_path: Path) -> None:
    slides = [f"Slide {i}" for i in range(1, 12)]
    path = make_pptx(tmp_path / "deck.pptx", slides)
    text = extract(path)
    assert text is not None
    assert text.splitlines() == slides  # slide10 after slide9, not after slide1


def test_xlsx(tmp_path: Path) -> None:
    path = make_xlsx(tmp_path / "budget.xlsx", ["2026", "Notes"], ["Rent", "Electricity"])
    assert extract(path) == "Sheets: 2026, Notes\nRent\nElectricity"


def test_office_max_chars(tmp_path: Path) -> None:
    path = make_docx(tmp_path / "big.docx", ["word " * 1000] * 50)
    text = extract(path, max_chars=500)
    assert text is not None and len(text) <= 500


@pytest.mark.parametrize("name", ["locked.docx", "locked.xlsx", "locked.pptx"])
def test_password_protected_office_is_an_extraction_error(tmp_path: Path, name: str) -> None:
    path = tmp_path / name
    path.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\0" * 512)  # OLE container
    with pytest.raises(ExtractionError, match="password-protected"):
        extract(path)


def test_office_with_dtd_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "evil.docx"
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">]>'
        "<w:document xmlns:w='w'><w:body><w:p><w:r><w:t>&lol;</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("word/document.xml", bomb)
    with pytest.raises(ExtractionError, match="DTD"):
        extract(path)


def test_pdf(tmp_path: Path) -> None:
    path = make_pdf(tmp_path / "fatura.pdf", "Fatura de energia - outubro 2026")
    assert extract(path) == "Fatura de energia - outubro 2026"


def test_pdf_without_text_layer_returns_none(tmp_path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    path = tmp_path / "scan.pdf"
    with path.open("wb") as fh:
        writer.write(fh)
    assert extract(path) is None


def test_password_protected_pdf(tmp_path: Path) -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=612, height=792)
    writer.encrypt(user_password="test-only", algorithm="RC4-128")  # noqa: S106
    path = tmp_path / "locked.pdf"
    with path.open("wb") as fh:
        writer.write(fh)
    with pytest.raises(ExtractionError, match="password"):
        extract(path)


def test_corrupted_pdf(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"%PDF-1.4\nthis is not really a pdf")
    with pytest.raises(ExtractionError):
        extract(path)


def test_zip_listing_never_extracts(tmp_path: Path) -> None:
    path = tmp_path / "photos.zip"
    with zipfile.ZipFile(path, "w") as zf:
        for i in range(250):
            zf.writestr(f"trip/IMG_{i:04}.jpg", b"jpeg")
    text = extract(path)
    assert text is not None
    assert text.startswith("Archive contents (200+ entries):\ntrip/IMG_0000.jpg")
    assert list(tmp_path.iterdir()) == [path]


def test_tar_listing(tmp_path: Path) -> None:
    member = tmp_path / "report.txt"
    member.write_text("x", encoding="utf-8")
    path = tmp_path / "backup.tar.gz"
    with tarfile.open(path, "w:gz") as tf:
        tf.add(member, arcname="docs/report.txt")
    assert extract(path) == "Archive contents (1 entries):\ndocs/report.txt"


def test_single_gzip_file_is_not_a_tarball(tmp_path: Path) -> None:
    path = tmp_path / "log.gz"
    path.write_bytes(b"\x1f\x8b not really")
    assert extract(path) is None


def test_corrupted_zip(tmp_path: Path) -> None:
    path = tmp_path / "broken.zip"
    path.write_bytes(b"PK\x03\x04 truncated")
    with pytest.raises(ExtractionError):
        extract(path)


@pytest.mark.parametrize("name", ["old.doc", "old.xls", "old.ppt", "movie.mp4", "setup.dmg"])
def test_formats_without_extractor(name: str) -> None:
    assert extractor_for(Path(name)) is None

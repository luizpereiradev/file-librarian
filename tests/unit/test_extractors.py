from __future__ import annotations

import tarfile
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from pypdf import PdfWriter

from file_librarian.extractors import ExtractionError, extractor_for, mail
from tests._helpers import make_docx, make_eml, make_pdf, make_pptx, make_xlsx


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


@pytest.mark.parametrize(
    ("name", "build"),
    [
        ("doc.docx", lambda p: make_docx(p, ["One", "Two"])),
        ("sheet.xlsx", lambda p: make_xlsx(p, ["2026"], ["Rent"])),
        ("deck.pptx", lambda p: make_pptx(p, ["Slide 1", "Slide 2", "Slide 3"])),
    ],
)
def test_office_opens_the_zip_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    build: Callable[[Path], Path],
) -> None:
    path = build(tmp_path / name)
    opened: list[object] = []

    class CountingZipFile(zipfile.ZipFile):  # defined before the patch below
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            opened.append(args[0] if args else kwargs.get("file"))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(zipfile, "ZipFile", CountingZipFile)
    assert extract(path)
    assert opened == [path]


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


# --- E-mail (.eml) -------------------------------------------------------------------


def test_eml_headers_and_plain_body(tmp_path: Path) -> None:
    path = make_eml(
        tmp_path / "message.eml",
        "Hello,\n\n\n\nyour   October bill is R$ 231,40.",
        sender='"billing@power.example" <billing@power.example>',
        to="Someone <someone@mail.example>, other@mail.example",
    )
    assert extract(path) == (
        "Subject: Electricity bill\n"
        "From: …@power.example <…@power.example>\n"
        "To: Someone <…@mail.example>, …@mail.example\n"
        "Date: 2026-10-05\n\n"
        "Hello,\n\nyour October bill is R$ 231,40."
    )


def test_eml_never_sends_the_local_part_of_an_address(tmp_path: Path) -> None:
    many = ", ".join(f"person{i}@mail.example" for i in range(8))
    path = make_eml(tmp_path / "m.eml", subject="Re: ask billing@power.example", to=many)
    text = extract(path)
    assert text is not None
    assert "billing@" not in text and "person0@" not in text
    assert "Subject: Re: ask …@power.example" in text
    assert "To: " + ", ".join(["…@mail.example"] * 5) + " (+3 more)" in text


def test_eml_html_body_when_there_is_no_plain_one(tmp_path: Path) -> None:
    html = (
        "<html><head><title>t</title><style>p {color: red}</style></head><body>"
        "<p>Order #42 shipped</p><script>track()</script><div>Thanks</div></body></html>"
    )
    text = extract(make_eml(tmp_path / "order.eml", "", html=html))
    assert text is not None
    assert text.endswith("Order #42 shipped\n\nThanks")
    assert "track()" not in text and "color" not in text


def test_eml_prefers_the_plain_body(tmp_path: Path) -> None:
    text = extract(make_eml(tmp_path / "both.eml", "plain words", html="<p>html words</p>"))
    assert text is not None and text.endswith("plain words") and "html words" not in text


def test_eml_lists_attachment_names_never_their_content(tmp_path: Path) -> None:
    path = make_eml(
        tmp_path / "with-files.eml",
        attachments={
            "invoice-october.pdf": b"%PDF hidden-content",
            "../../folder/notes.txt": b"attached words",
            "C:\\Users\\x\\scan.jpg": b"jpeg",
        },
    )
    text = extract(path)
    assert text is not None
    assert "Attachments: invoice-october.pdf, notes.txt, scan.jpg" in text
    assert "hidden-content" not in text and "attached words" not in text


def test_eml_caps_attachments_and_parts(tmp_path: Path) -> None:
    files = {f"file-{i:03}.pdf": b"x" for i in range(300)}
    text = extract(make_eml(tmp_path / "many.eml", attachments=files))
    assert text is not None
    listed = ", ".join(f"file-{i:03}.pdf" for i in range(mail.MAX_ATTACHMENTS))
    assert f"Attachments: {listed}, …" in text


def test_eml_odd_headers_and_unnamed_attachments(tmp_path: Path) -> None:
    path = tmp_path / "odd.eml"
    path.write_bytes(
        b"Subject: hi\nDate: sometime last week\nTo: undisclosed-recipients:;\n"
        b'Content-Type: multipart/mixed; boundary="b"\n\n'
        b"--b\nContent-Type: text/plain\nContent-Disposition: attachment\n\nnot the body\n"
        b"--b\nContent-Type: text/plain\n\nthe body\n--b--\n"
    )
    assert extract(path) == "Subject: hi\nDate: sometime last week\n\nthe body"


def test_eml_max_chars(tmp_path: Path) -> None:
    text = extract(make_eml(tmp_path / "long.eml", "word " * 5000), max_chars=300)
    assert text is not None and len(text) <= 300


@pytest.mark.parametrize("content", [b"", b"\n\n\n"])
def test_eml_without_anything_readable(tmp_path: Path, content: bytes) -> None:
    path = tmp_path / "empty.eml"
    path.write_bytes(content)
    assert extract(path) is None


def test_eml_with_an_unknown_charset_falls_back_to_utf8(tmp_path: Path) -> None:
    path = tmp_path / "odd.eml"
    path.write_bytes(
        b"Subject: hi\nContent-Type: text/plain; charset=x-unknown\n\nOr\xc3\xa7amento\n"
    )
    assert extract(path) == "Subject: hi\n\nOrçamento"


def test_eml_reads_at_most_the_byte_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mail, "MAX_MESSAGE_BYTES", 400)
    path = make_eml(tmp_path / "big.eml", "start " + "x" * 10_000 + " END-MARKER")
    text = extract(path)
    assert text is not None and "start" in text and "END-MARKER" not in text


def test_eml_nested_too_deep_is_an_extraction_error(tmp_path: Path) -> None:
    head = "".join(
        f'Content-Type: multipart/mixed; boundary="b{i}"\n\n--b{i}\n' for i in range(5000)
    )
    path = tmp_path / "deep.eml"
    path.write_text("Subject: deep\n" + head + "Content-Type: text/plain\n\nhello\n")
    with pytest.raises(ExtractionError):
        extract(path)


def test_eml_unreadable_is_an_extraction_error(tmp_path: Path) -> None:
    folder = tmp_path / "folder.eml"
    folder.mkdir()
    with pytest.raises(ExtractionError):
        extract(folder)

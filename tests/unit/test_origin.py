from __future__ import annotations

import plistlib
from pathlib import Path

from file_librarian.extractors.origin import (
    WHERE_FROMS,
    download_origin,
    parse_where_froms,
)
from tests._helpers import write_xattr

URL = "https://example.com/files/invoice-2026-10.pdf"
REFERRER = "https://example.com/account/billing"


def make_download(folder: Path, value: list[str]) -> Path:
    path = folder / "documento(3).pdf"
    path.write_bytes(b"%PDF-1.4 synthetic")
    write_xattr(path, WHERE_FROMS, plistlib.dumps(value, fmt=plistlib.FMT_BINARY))
    return path


def test_reads_origin_written_by_browser(downloads: Path) -> None:
    path = make_download(downloads, [URL, REFERRER])
    assert download_origin(path) == (URL, REFERRER)


def test_file_without_origin(downloads: Path) -> None:
    path = downloads / "notes.txt"
    path.write_text("hello", encoding="utf-8")
    assert download_origin(path) == ()


def test_missing_file_yields_nothing(downloads: Path) -> None:
    assert download_origin(downloads / "gone.pdf") == ()


def test_single_string_and_duplicates() -> None:
    assert parse_where_froms(plistlib.dumps(URL, fmt=plistlib.FMT_BINARY)) == (URL,)
    assert parse_where_froms(plistlib.dumps([URL, URL, "", 3])) == (URL,)


def test_malformed_value_never_raises(downloads: Path) -> None:
    assert parse_where_froms(b"not a plist") == ()
    assert parse_where_froms(plistlib.dumps({"a": 1})) == ()
    path = downloads / "weird.bin"
    path.write_bytes(b"x")
    write_xattr(path, WHERE_FROMS, b"\x00garbage")
    assert download_origin(path) == ()


def test_overlong_urls_are_dropped() -> None:
    long_url = "https://example.com/" + "a" * 5000
    assert parse_where_froms(plistlib.dumps([long_url, URL])) == (URL,)

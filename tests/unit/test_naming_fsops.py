from __future__ import annotations

import ctypes
import errno
import os
from datetime import date
from pathlib import Path

import pytest

from file_librarian import fsops
from file_librarian.config import NamingConfig
from file_librarian.fsops import DestinationExistsError, ensure_folder, move_no_replace
from file_librarian.naming import build_name, safe_component, slugify, split_name, with_counter
from tests._helpers import case_insensitive, names

DAY = date(2026, 10, 5)


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("report.pdf", ("report", ".pdf")),
        ("backup.tar.gz", ("backup", ".tar.gz")),
        ("archive.TAR.GZ", ("archive", ".TAR.GZ")),
        ("Makefile", ("Makefile", "")),
        (".tar.gz", (".tar", ".gz")),
        ("v1.2.3", ("v1.2", ".3")),
    ],
)
def test_split_name(name: str, expected: tuple[str, str]) -> None:
    assert split_name(name) == expected


def test_slugify_keeps_letters_in_any_language() -> None:
    assert slugify("Nota Fiscal — Energia/Outubro: 2026!") == "nota-fiscal-energia-outubro-2026"
    assert slugify("Orçamento Reforma") == "orçamento-reforma"
    assert slugify("../../etc/passwd") == "etc-passwd"
    assert slugify("   ") == ""


def test_build_name_uses_pattern_date_and_original_extension() -> None:
    naming = NamingConfig()
    assert build_name("documento(3).PDF", "Electricity invoice", DAY, naming) == (
        "2026-10-05_electricity-invoice.PDF"
    )
    assert build_name("x.tar.gz", "Backup", DAY, naming) == "2026-10-05_backup.tar.gz"


def test_build_name_rejects_empty_and_respects_length() -> None:
    naming = NamingConfig(max_length=30)
    assert build_name("a.pdf", "!!!", DAY, naming) is None
    long = build_name("a.pdf", "word " * 50, DAY, naming)
    assert long is not None and len(long) <= 30 and long.endswith(".pdf")


def test_build_name_stays_under_255_bytes() -> None:
    naming = NamingConfig(max_length=255)
    name = build_name("a.pdf", "ç" * 300, DAY, naming)
    assert name is not None and len(name.encode()) <= 255


def test_safe_component() -> None:
    assert safe_component("../evil") == "-evil"
    assert safe_component("..") == ""
    assert safe_component("a/b:c\x00d") == "a-b-c-d"
    assert safe_component(".hidden") == "hidden"


def test_with_counter() -> None:
    assert with_counter("report.pdf", 2) == "report-2.pdf"
    assert with_counter("backup.tar.gz", 3) == "backup-3.tar.gz"


def test_move_never_overwrites(tmp_path: Path) -> None:
    src = tmp_path / "a.txt"
    dst = tmp_path / "b.txt"
    src.write_text("new", encoding="utf-8")
    dst.write_text("precious", encoding="utf-8")
    with pytest.raises(DestinationExistsError):
        move_no_replace(src, dst)
    assert dst.read_text(encoding="utf-8") == "precious"
    assert src.read_text(encoding="utf-8") == "new"


def test_move_refuses_case_variant_on_apfs(tmp_path: Path) -> None:
    src = tmp_path / "a.txt"
    src.write_text("new", encoding="utf-8")
    (tmp_path / "B.txt").write_text("precious", encoding="utf-8")
    with pytest.raises(DestinationExistsError):
        move_no_replace(src, tmp_path / "b.txt")


def test_move_changes_only_case_in_place(tmp_path: Path) -> None:
    if not case_insensitive(tmp_path):
        pytest.skip("the temporary folder's volume is case-sensitive; there is no clash")
    src = tmp_path / "a.txt"
    src.write_text("mine", encoding="utf-8")
    move_no_replace(src, tmp_path / "A.txt")
    assert names(tmp_path) == ["A.txt"]


class _ExclReportsSelf:
    """A libc whose ``RENAME_EXCL`` sees the source itself as the destination, as
    case-insensitive APFS does on some macOS versions; plain renames are real."""

    real = fsops._libc()

    def __init__(self, plain_fails: bool = False) -> None:
        self.plain_fails = plain_fails

    def renamex_np(self, src: bytes, dst: bytes, flags: int) -> int:
        if (flags and os.path.lexists(dst)) or (not flags and self.plain_fails):
            ctypes.set_errno(errno.EEXIST if flags else errno.EACCES)
            return -1
        return int(self.real.renamex_np(src, dst, flags))


@pytest.fixture
def excl_reports_self(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fsops, "_libc", _ExclReportsSelf)


@pytest.mark.usefixtures("excl_reports_self")
def test_case_only_rename_of_the_same_entry_goes_ahead(tmp_path: Path) -> None:
    src = tmp_path / "report.pdf"
    src.write_text("mine", encoding="utf-8")
    dst = tmp_path / "Report.pdf"
    if not case_insensitive(tmp_path):
        os.link(src, dst)  # same inode, but a second entry: never replaced
        with pytest.raises(DestinationExistsError):
            move_no_replace(src, dst)
        return
    move_no_replace(src, dst)
    assert names(tmp_path) == ["Report.pdf"]


@pytest.mark.usefixtures("excl_reports_self")
def test_a_different_entry_is_never_replaced(tmp_path: Path) -> None:
    src = tmp_path / "a.txt"
    src.write_text("mine", encoding="utf-8")
    link = tmp_path / "b.txt"
    os.link(src, link)  # same inode, another name
    with pytest.raises(DestinationExistsError):
        move_no_replace(src, tmp_path / "B.txt")
    (tmp_path / "other").mkdir()
    (tmp_path / "other" / "A.txt").write_text("precious", encoding="utf-8")
    with pytest.raises(DestinationExistsError):
        move_no_replace(src, tmp_path / "other" / "A.txt")  # same name, another folder
    assert names(tmp_path) == ["a.txt", "b.txt", "other"]
    assert (tmp_path / "other" / "A.txt").read_text(encoding="utf-8") == "precious"


def test_an_exact_spelling_on_disk_is_a_different_entry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    src = tmp_path / "a.txt"
    src.write_text("mine", encoding="utf-8")
    if case_insensitive(tmp_path):
        # As a case-sensitive volume lists a hard link "A.txt" next to "a.txt".
        monkeypatch.setattr(fsops, "has_exact_name", lambda _p: True)
    else:
        os.link(src, tmp_path / "A.txt")
    assert not fsops._same_entry(src, tmp_path / "A.txt")


def test_a_failed_case_only_rename_is_an_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not case_insensitive(tmp_path):
        pytest.skip("the temporary folder's volume is case-sensitive; there is no clash")
    monkeypatch.setattr(fsops, "_libc", lambda: _ExclReportsSelf(plain_fails=True))
    src = tmp_path / "a.txt"
    src.write_text("mine", encoding="utf-8")
    with pytest.raises(PermissionError):
        move_no_replace(src, tmp_path / "A.txt")
    assert names(tmp_path) == ["a.txt"]


def test_move_folder(tmp_path: Path) -> None:
    src = tmp_path / "project"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f.txt").write_text("x", encoding="utf-8")
    move_no_replace(src, tmp_path / "moved")
    assert (tmp_path / "moved" / "sub" / "f.txt").read_text(encoding="utf-8") == "x"
    assert not src.exists()


def test_ensure_folder_refuses_symlinks(tmp_path: Path) -> None:
    root = tmp_path / "Organized"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "Finance").symlink_to(outside)
    with pytest.raises(NotADirectoryError):
        ensure_folder(root / "Finance", inside=root)
    with pytest.raises(ValueError, match="outside"):
        ensure_folder(tmp_path / "elsewhere", inside=root)
    ensure_folder(root / "Contracts", inside=root)
    assert (root / "Contracts").is_dir()


def test_ensure_folder_creates_missing_root(tmp_path: Path) -> None:
    root = tmp_path / "Downloads" / "Organized"
    (tmp_path / "Downloads").mkdir()
    ensure_folder(root / "Finance", inside=root)
    assert (root / "Finance").is_dir()

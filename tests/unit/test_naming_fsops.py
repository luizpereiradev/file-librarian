from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from file_librarian.config import NamingConfig
from file_librarian.fsops import DestinationExistsError, ensure_folder, move_no_replace
from file_librarian.naming import build_name, safe_component, slugify, split_name, with_counter

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

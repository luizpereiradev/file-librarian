from __future__ import annotations

import plistlib
from pathlib import Path

import pytest

from file_librarian.extractors.origin import read_xattr
from file_librarian.finder_tags import (
    USER_TAGS,
    _write_tags,
    add_tag,
    read_tags,
    remove_tag,
    tag_moved,
    untag_restored,
)
from tests._helpers import write_xattr


def tagged(folder: Path, name: str, tags: list[str] | None = None) -> Path:
    path = folder / name
    path.write_bytes(b"synthetic")
    if tags is not None:
        write_xattr(path, USER_TAGS, plistlib.dumps(tags, fmt=plistlib.FMT_BINARY))
    return path


def test_adds_a_tag_to_an_untagged_file(tmp_path: Path) -> None:
    path = tagged(tmp_path, "a.pdf")
    assert add_tag(path, "Finance") is True
    assert read_tags(path) == ["Finance"]
    # Written as a binary plist, the format Finder writes.
    raw = read_xattr(path, USER_TAGS)
    assert raw is not None and raw.startswith(b"bplist00")


def test_keeps_existing_tags_and_does_not_duplicate(tmp_path: Path) -> None:
    path = tagged(tmp_path, "a.pdf", ["Important\n6", "Work"])
    assert add_tag(path, "Finance") is True
    assert read_tags(path) == ["Important\n6", "Work", "Finance"]
    assert add_tag(path, "Finance") is False
    # Finder matches tag names ignoring case and color.
    assert add_tag(path, "work") is False
    assert add_tag(path, "Important") is False
    assert read_tags(path) == ["Important\n6", "Work", "Finance"]


def test_remove_keeps_other_tags_and_any_color(tmp_path: Path) -> None:
    path = tagged(tmp_path, "a.pdf", ["Work", "Finance\n2"])
    assert remove_tag(path, "Finance") is True  # the user colored it since: still ours
    assert read_tags(path) == ["Work"]
    assert remove_tag(path, "Finance") is False
    assert remove_tag(path, "Work") is True
    assert read_xattr(path, USER_TAGS) is None  # no empty attribute left behind


def test_folders_can_be_tagged(tmp_path: Path) -> None:
    folder = tmp_path / "Tool.app"
    folder.mkdir()
    assert add_tag(folder, "Installers") is True
    assert read_tags(folder) == ["Installers"]


def test_unreadable_tags_are_never_overwritten(tmp_path: Path) -> None:
    garbage = tagged(tmp_path, "a.pdf")
    write_xattr(garbage, USER_TAGS, b"\x00not a plist")
    wrong = tagged(tmp_path, "b.pdf", None)
    write_xattr(wrong, USER_TAGS, plistlib.dumps({"a": 1}, fmt=plistlib.FMT_BINARY))
    for path in (garbage, wrong):
        before = read_xattr(path, USER_TAGS)
        with pytest.raises(ValueError):
            add_tag(path, "Finance")
        added, warning = tag_moved(path, "Finance")
        assert added is None and warning is not None and "Finance" in warning
        assert untag_restored(path, "Finance") is not None
        assert read_xattr(path, USER_TAGS) == before


def test_wrappers_report_success_and_os_errors(tmp_path: Path) -> None:
    path = tagged(tmp_path, "a.pdf", ["Finance"])
    assert tag_moved(path, "Finance") == (None, None)  # already there: not ours
    assert tag_moved(path, "Other") == ("Other", None)
    assert untag_restored(path, "Other") is None
    assert read_tags(path) == ["Finance"]
    gone = tmp_path / "gone.pdf"
    added, warning = tag_moved(gone, "Finance")
    assert added is None and warning is not None and "No such file" in warning
    assert untag_restored(gone, "Finance") is not None


def test_low_level_write_errors_raise(tmp_path: Path) -> None:
    gone = tmp_path / "gone.pdf"
    with pytest.raises(OSError):
        _write_tags(gone, ["Finance"])
    with pytest.raises(OSError):
        _write_tags(gone, [])
    plain = tagged(tmp_path, "a.pdf")
    _write_tags(plain, [])  # removing an absent attribute is fine
    assert read_tags(plain) == []

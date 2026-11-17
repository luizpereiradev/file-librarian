from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

import pytest

from file_librarian import hashing, scanner
from file_librarian.config import Config, parse_config
from file_librarian.hashing import SF_DATALESS, file_sha256
from file_librarian.models import FileState, FileStatus
from file_librarian.scanner import ScanError, SkipReason, scan

NOW = time.time()
OLD = NOW - 3600  # well past stable_seconds


def make_config(downloads: Path, **overrides: Any) -> Config:
    raw: dict[str, Any] = {
        "inputs": [str(downloads)],
        "output_root": str(downloads / "Organized"),
        "provider": {"name": "fake", "model": "fixed"},
        "ignore": ["*.tmp"],
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(raw.get(key), dict):
            raw[key] = raw[key] | value
        else:
            raw[key] = value
    return parse_config(raw)


def put(folder: Path, name: str, content: bytes = b"data", mtime: float = OLD) -> Path:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    os.utime(path, (mtime, mtime))
    return path


def nothing_known(_sha: str) -> FileState | None:
    return None


def reasons(result: Any) -> dict[str, SkipReason]:
    return {path.name: reason for path, reason in result.skipped}


def test_new_stable_file_is_a_candidate(downloads: Path) -> None:
    path = put(downloads, "documento(3).pdf", b"%PDF synthetic")
    result = scan(make_config(downloads), nothing_known, now=NOW)
    [candidate] = result.candidates
    assert candidate.path == path
    assert candidate.sha256 == file_sha256(path)
    assert candidate.size_bytes == len(b"%PDF synthetic")
    assert candidate.is_unit is False


def test_skips_hidden_system_ignored_and_in_progress(downloads: Path) -> None:
    for name in (".DS_Store", "._resource", ".localized", "notes.tmp", "REPORT.TMP"):
        put(downloads, name)
    for name in ("movie.mp4.crdownload", "big.iso.part", "file.zip.opdownload"):
        put(downloads, name)
    (downloads / "page.html.download").mkdir()  # Safari's in-progress bundle
    result = scan(make_config(downloads), nothing_known, now=NOW)
    assert result.candidates == []
    got = reasons(result)
    assert got[".DS_Store"] is SkipReason.HIDDEN
    assert got["._resource"] is SkipReason.HIDDEN
    assert got["notes.tmp"] is SkipReason.IGNORED
    assert got["REPORT.TMP"] is SkipReason.IGNORED  # globs ignore case, like APFS
    assert got["movie.mp4.crdownload"] is SkipReason.IN_PROGRESS
    assert got["page.html.download"] is SkipReason.IN_PROGRESS


def test_skips_recent_files_until_stable(downloads: Path) -> None:
    put(downloads, "fresh.pdf", mtime=NOW - 10)
    put(downloads, "settled.pdf", mtime=NOW - 61)
    result = scan(make_config(downloads), nothing_known, now=NOW)
    assert [c.path.name for c in result.candidates] == ["settled.pdf"]
    assert reasons(result)["fresh.pdf"] is SkipReason.UNSTABLE


def test_never_enters_the_managed_output_folder(downloads: Path) -> None:
    put(downloads, "Organized/Finance/invoice.pdf")
    os.utime(downloads / "Organized", (OLD, OLD))
    result = scan(make_config(downloads), nothing_known, now=NOW)
    assert result.candidates == []
    assert reasons(result)["Organized"] is SkipReason.MANAGED


def test_folders_and_bundles_are_units(downloads: Path) -> None:
    put(downloads, "project/src/main.py", b"print('hi')")
    put(downloads, "Tool.app/Contents/MacOS/tool", b"\x7fELF")
    for folder in ("project/src", "project", "Tool.app/Contents/MacOS", "Tool.app/Contents"):
        os.utime(downloads / folder, (OLD, OLD))
    os.utime(downloads / "Tool.app", (OLD, OLD))
    result = scan(make_config(downloads), nothing_known, now=NOW)
    units = {c.path.name: c for c in result.candidates}
    assert set(units) == {"project", "Tool.app"}
    assert all(c.is_unit for c in units.values())
    assert units["project"].size_bytes == len(b"print('hi')")


def test_unit_with_a_recent_file_inside_waits(downloads: Path) -> None:
    put(downloads, "project/old.txt")
    put(downloads, "project/new.txt", mtime=NOW - 5)
    os.utime(downloads / "project", (OLD, OLD))
    result = scan(make_config(downloads), nothing_known, now=NOW)
    assert reasons(result)["project"] is SkipReason.UNSTABLE


def test_folders_ignore_skips_units(downloads: Path) -> None:
    put(downloads, "project/a.txt")
    os.utime(downloads / "project", (OLD, OLD))
    result = scan(make_config(downloads, folders="ignore"), nothing_known, now=NOW)
    assert result.candidates == []
    assert reasons(result)["project"] is SkipReason.FOLDER_IGNORED


def mark_cloud_only(monkeypatch: pytest.MonkeyPatch, *paths: Path) -> set[Path]:
    """Real dataless files cannot be made in a test: report these inodes as cloud-only.

    Any read of them (hashing, opening, listing a folder) fails the test.
    """
    inodes = {os.lstat(p).st_ino for p in paths}
    real_is_dataless = hashing.is_dataless
    monkeypatch.setattr(
        hashing, "is_dataless", lambda st: st.st_ino in inodes or real_is_dataless(st)
    )

    def touched(path: object) -> bool:
        target = Path(os.fsdecode(path))  # type: ignore[arg-type]
        return any(target == p or p in target.parents for p in paths)

    real_sha = file_sha256
    real_open = Path.open
    real_walk = os.walk

    def guarded_sha(path: Path) -> str:
        assert not touched(path), f"hashed a cloud-only item: {path}"
        return real_sha(path)

    def guarded_open(self: Path, *args: Any, **kwargs: Any) -> Any:
        assert not touched(self), f"opened a cloud-only item: {self}"
        return real_open(self, *args, **kwargs)

    def guarded_walk(top: Any, *args: Any, **kwargs: Any) -> Any:
        assert not touched(top), f"listed a cloud-only folder: {top}"
        return real_walk(top, *args, **kwargs)

    monkeypatch.setattr(scanner, "file_sha256", guarded_sha)
    monkeypatch.setattr(Path, "open", guarded_open)
    monkeypatch.setattr(os, "walk", guarded_walk)
    return set(paths)


def test_dataless_flag_is_read_from_st_flags(downloads: Path) -> None:
    class Flags:
        def __init__(self, flags: int) -> None:
            self.st_flags = flags

    assert SF_DATALESS == 0x40000000
    assert hashing.is_dataless(Flags(SF_DATALESS))  # type: ignore[arg-type]
    assert not hashing.is_dataless(Flags(0))  # type: ignore[arg-type]
    assert not hashing.is_dataless(os.lstat(put(downloads, "local.pdf")))


def test_cloud_only_file_is_skipped_without_reading(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cloud = put(downloads, "report.pdf", b"%PDF only in the cloud")
    local = put(downloads, "local.pdf", b"%PDF here")
    mark_cloud_only(monkeypatch, cloud)
    result = scan(make_config(downloads), nothing_known, now=NOW)
    assert [c.path for c in result.candidates] == [local]
    assert reasons(result)["report.pdf"] is SkipReason.CLOUD_ONLY


def test_cloud_only_file_is_picked_up_once_downloaded(downloads: Path) -> None:
    put(downloads, "report.pdf", b"%PDF downloaded now")
    result = scan(make_config(downloads), nothing_known, now=NOW)
    assert [c.path.name for c in result.candidates] == ["report.pdf"]


def test_unit_with_a_cloud_only_file_inside_is_skipped_whole(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(downloads, "project/a_local.txt")
    cloud = put(downloads, "project/b_cloud.bin", b"only in the cloud")
    put(downloads, "project/c_local.txt")
    os.utime(downloads / "project", (OLD, OLD))
    mark_cloud_only(monkeypatch, cloud)
    result = scan(make_config(downloads), nothing_known, now=NOW)
    assert result.candidates == []
    assert reasons(result)["project"] is SkipReason.CLOUD_ONLY


def test_cloud_only_folder_is_not_listed(downloads: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    put(downloads, "Album/photo.jpg")
    os.utime(downloads / "Album", (OLD, OLD))
    mark_cloud_only(monkeypatch, downloads / "Album")
    result = scan(make_config(downloads), nothing_known, now=NOW)
    assert result.candidates == []
    assert reasons(result)["Album"] is SkipReason.CLOUD_ONLY


def test_manifest_stops_at_a_cloud_only_member(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    unit = downloads / "Tool.app"
    put(downloads, "Tool.app/Contents/Info.plist")
    cloud = put(downloads, "Tool.app/Contents/MacOS/tool", b"\x7fELF")
    assert not hashing.unit_manifest(unit).dataless
    mark_cloud_only(monkeypatch, cloud)
    assert hashing.unit_manifest(unit).dataless
    # The top folder itself being cloud-only is caught before listing it.
    mark_cloud_only(monkeypatch, unit)
    assert hashing.unit_manifest(unit).dataless


def test_symlinks_and_special_files_are_skipped(downloads: Path, tmp_path: Path) -> None:
    target = put(tmp_path, "outside.pdf")
    (downloads / "link.pdf").symlink_to(target)
    os.mkfifo(downloads / "pipe")
    result = scan(make_config(downloads), nothing_known, now=NOW)
    got = reasons(result)
    assert got["link.pdf"] is SkipReason.SYMLINK
    assert got["pipe"] is SkipReason.SPECIAL
    assert result.candidates == []


@pytest.mark.parametrize(
    ("state", "is_candidate"),
    [
        (None, True),
        (FileState(FileStatus.ORGANIZED, 0), False),
        (FileState(FileStatus.SENSITIVE, 0), False),
        (FileState(FileStatus.REVIEW, 0), False),
        (FileState(FileStatus.FAILED, 1), True),  # retried
        (FileState(FileStatus.FAILED, 3), False),  # gave up after max_attempts
    ],
)
def test_known_hashes(downloads: Path, state: FileState | None, is_candidate: bool) -> None:
    put(downloads, "a.pdf")
    result = scan(make_config(downloads), lambda _sha: state, now=NOW)
    assert (len(result.candidates) == 1) is is_candidate
    if not is_candidate:
        assert reasons(result)["a.pdf"] is SkipReason.KNOWN


def test_cap_keeps_oldest_and_counts_deferred(downloads: Path) -> None:
    for i in range(5):
        put(downloads, f"f{i}.txt", f"content {i}".encode(), mtime=OLD - i * 60)
    config = make_config(downloads, limits={"max_files_per_run": 2})
    result = scan(config, nothing_known, now=NOW)
    assert [c.path.name for c in result.candidates] == ["f4.txt", "f3.txt"]
    assert result.deferred == 3


def test_unreadable_input_explains_macos_privacy(downloads: Path) -> None:
    downloads.chmod(0o000)
    try:
        with pytest.raises(ScanError, match="Privacy & Security"):
            scan(make_config(downloads), nothing_known, now=NOW)
    finally:
        downloads.chmod(0o755)


def test_missing_input_folder(downloads: Path) -> None:
    config = make_config(downloads)
    downloads.rmdir()
    with pytest.raises(ScanError, match="does not exist"):
        scan(config, nothing_known, now=NOW)


def test_unreadable_file_is_skipped_not_fatal(downloads: Path) -> None:
    locked = put(downloads, "locked.pdf")
    put(downloads, "ok.pdf", b"fine")
    locked.chmod(0o000)
    try:
        result = scan(make_config(downloads), nothing_known, now=NOW)
    finally:
        locked.chmod(0o644)
    assert [c.path.name for c in result.candidates] == ["ok.pdf"]
    assert reasons(result)["locked.pdf"] is SkipReason.UNREADABLE


def test_scanner_does_not_modify_anything(downloads: Path) -> None:
    put(downloads, "a.pdf")
    put(downloads, "project/b.txt")
    os.utime(downloads / "project", (OLD, OLD))
    before = {p: p.stat().st_mtime_ns for p in downloads.rglob("*")}
    scan(make_config(downloads), nothing_known, now=NOW)
    assert {p: p.stat().st_mtime_ns for p in downloads.rglob("*")} == before


@pytest.mark.parametrize(
    "names",
    [
        ["WhatsApp.dmg", "WhatsApp (1).dmg"],
        ["WhatsApp (1).dmg", "WhatsApp.dmg"],  # creation order does not beat the copy suffix
        ["report.pdf", "report copy.pdf", "report copy 2.pdf"],
    ],
)
def test_duplicates_keep_the_original_copy(downloads: Path, names: list[str]) -> None:
    for i, name in enumerate(names):
        put(downloads, name, b"same bytes", mtime=OLD + i)
    result = scan(make_config(downloads), nothing_known, now=NOW)
    original = min(names, key=len)
    assert [c.path.name for c in result.candidates] == [original]
    assert sorted(p.name for p, r in result.skipped if r is SkipReason.DUPLICATE) == sorted(
        n for n in names if n != original
    )


def test_version_numbers_are_not_mistaken_for_copies(downloads: Path) -> None:
    put(downloads, "Tool-0.1.2.dmg", b"same", mtime=OLD)
    put(downloads, "Tool-0.1.dmg", b"same", mtime=OLD)
    result = scan(make_config(downloads), nothing_known, now=NOW)
    # Neither looks like a copy: the shorter name wins the tie.
    assert [c.path.name for c in result.candidates] == ["Tool-0.1.dmg"]

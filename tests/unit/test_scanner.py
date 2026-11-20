from __future__ import annotations

import errno
import os
import time
from pathlib import Path
from typing import Any

import pytest

from file_librarian import hashing, scanner
from file_librarian.config import Config
from file_librarian.hashing import SF_DATALESS, file_sha256
from file_librarian.models import FileState, FileStatus
from file_librarian.scanner import ScanError, SkipReason, scan
from tests._helpers import OLD, FakeProtection, fake_config, make_old, mark_cloud_only, put

NOW = time.time()


def make_config(downloads: Path, **overrides: Any) -> Config:
    """The fake config, also ignoring ``*.tmp``."""
    defaults: dict[str, Any] = {"output_root": str(downloads / "Organized"), "ignore": ["*.tmp"]}
    return fake_config(downloads, **(defaults | overrides))


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
    make_old(downloads / "Organized")
    result = scan(make_config(downloads), nothing_known, now=NOW)
    assert result.candidates == []
    assert reasons(result)["Organized"] is SkipReason.MANAGED


def test_folders_and_bundles_are_units(downloads: Path) -> None:
    put(downloads, "project/src/main.py", b"print('hi')")
    put(downloads, "Tool.app/Contents/MacOS/tool", b"\x7fELF")
    for folder in ("project/src", "project", "Tool.app/Contents/MacOS", "Tool.app/Contents"):
        make_old(downloads / folder)
    make_old(downloads / "Tool.app")
    result = scan(make_config(downloads), nothing_known, now=NOW)
    units = {c.path.name: c for c in result.candidates}
    assert set(units) == {"project", "Tool.app"}
    assert all(c.is_unit for c in units.values())
    assert units["project"].size_bytes == len(b"print('hi')")


def test_unit_with_a_recent_file_inside_waits(downloads: Path) -> None:
    put(downloads, "project/old.txt")
    put(downloads, "project/new.txt", mtime=NOW - 5)
    make_old(downloads / "project")
    result = scan(make_config(downloads), nothing_known, now=NOW)
    assert reasons(result)["project"] is SkipReason.UNSTABLE


def test_folders_ignore_skips_units(downloads: Path) -> None:
    put(downloads, "project/a.txt")
    make_old(downloads / "project")
    result = scan(make_config(downloads, folders="ignore"), nothing_known, now=NOW)
    assert result.candidates == []
    assert reasons(result)["project"] is SkipReason.FOLDER_IGNORED


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
    make_old(downloads / "project")
    mark_cloud_only(monkeypatch, cloud)
    result = scan(make_config(downloads), nothing_known, now=NOW)
    assert result.candidates == []
    assert reasons(result)["project"] is SkipReason.CLOUD_ONLY


def test_cloud_only_folder_is_not_listed(downloads: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    put(downloads, "Album/photo.jpg")
    make_old(downloads / "Album")
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
    assert str(result.details[locked]) == "EACCES Permission denied, on read"


def test_scanner_does_not_modify_anything(downloads: Path) -> None:
    put(downloads, "a.pdf")
    put(downloads, "project/b.txt")
    make_old(downloads / "project")
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
    copies = sorted(n for n in names if n != original)
    assert sorted(d.candidate.path.name for d in result.duplicates) == copies
    assert {d.original.name for d in result.duplicates} == {original}
    assert not any(d.in_library for d in result.duplicates)
    # With `duplicates: leave`, they are skipped and wait where they are.
    left = scan(make_config(downloads, duplicates="leave"), nothing_known, now=NOW)
    assert [c.path.name for c in left.candidates] == [original] and left.duplicates == []
    assert sorted(p.name for p, r in left.skipped if r is SkipReason.DUPLICATE) == copies


def test_version_numbers_are_not_mistaken_for_copies(downloads: Path) -> None:
    put(downloads, "Tool-0.1.2.dmg", b"same", mtime=OLD)
    put(downloads, "Tool-0.1.dmg", b"same", mtime=OLD)
    result = scan(make_config(downloads), nothing_known, now=NOW)
    # Neither looks like a copy: the shorter name wins the tie.
    assert [c.path.name for c in result.candidates] == ["Tool-0.1.dmg"]


def failing_read(monkeypatch: pytest.MonkeyPatch, name: str, codes: list[int]) -> list[Path]:
    """Reading ``name`` raises each errno in ``codes`` in turn, then works; returns the calls."""
    calls: list[Path] = []
    real = hashing.file_sha256

    def read(path: Path) -> str:
        if path.name == name:
            calls.append(path)
            if len(calls) <= len(codes):
                code = codes[len(calls) - 1]
                raise OSError(code, os.strerror(code), str(path))
        return real(path)

    monkeypatch.setattr(scanner, "file_sha256", read)
    return calls


@pytest.mark.parametrize(
    "code",
    [
        errno.EPERM,
        errno.EACCES,
        errno.ENOENT,
        errno.EDEADLK,
        errno.EAGAIN,
        errno.EINTR,
        errno.ETIMEDOUT,
        errno.EBUSY,
        errno.ESTALE,
        errno.EIO,
    ],
)
def test_unreadable_keeps_the_errno_and_step(
    downloads: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    put(downloads, "photo.jpeg", b"jpeg bytes")
    put(downloads, "ok.pdf", b"fine")
    failing_read(monkeypatch, "photo.jpeg", [code, code])
    pauses: list[float] = []
    result = scan(make_config(downloads), nothing_known, now=NOW, sleep=pauses.append)
    assert [c.path.name for c in result.candidates] == ["ok.pdf"]
    assert reasons(result)["photo.jpeg"] is SkipReason.UNREADABLE
    detail = result.details[downloads / "photo.jpeg"]
    name = errno.errorcode[code]
    assert str(detail) == f"{name} {os.strerror(code)}, on read"
    assert str(downloads) not in str(detail)
    assert detail.denied is (code in (errno.EPERM, errno.EACCES))


@pytest.mark.parametrize("code", sorted(scanner.TRANSIENT_ERRNOS))
def test_a_transient_read_error_is_retried_once(
    downloads: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    put(downloads, "photo.jpeg", b"jpeg bytes")
    calls = failing_read(monkeypatch, "photo.jpeg", [code])
    pauses: list[float] = []
    result = scan(make_config(downloads), nothing_known, now=NOW, sleep=pauses.append)
    assert [c.path.name for c in result.candidates] == ["photo.jpeg"]
    assert len(calls) == 2
    assert pauses == [scanner.RETRY_PAUSE]
    assert result.details == {}


@pytest.mark.parametrize("code", [errno.EPERM, errno.EACCES, errno.ENOENT, errno.EIO])
def test_other_read_errors_are_not_retried(
    downloads: Path, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    put(downloads, "photo.jpeg", b"jpeg bytes")
    calls = failing_read(monkeypatch, "photo.jpeg", [code])
    pauses: list[float] = []
    result = scan(make_config(downloads), nothing_known, now=NOW, sleep=pauses.append)
    assert reasons(result)["photo.jpeg"] is SkipReason.UNREADABLE
    assert len(calls) == 1
    assert pauses == []


def test_a_transient_error_in_a_folder_unit_is_retried(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(downloads, "album/photo.jpeg", b"jpeg bytes")
    make_old(downloads / "album")
    real = hashing.unit_manifest
    calls: list[Path] = []

    def manifest(path: Path) -> Any:
        calls.append(path)
        if len(calls) == 1:
            raise OSError(errno.EDEADLK, os.strerror(errno.EDEADLK))
        return real(path)

    monkeypatch.setattr(scanner, "unit_manifest", manifest)
    pauses: list[float] = []
    result = scan(make_config(downloads), nothing_known, now=NOW, sleep=pauses.append)
    assert [c.path.name for c in result.candidates] == ["album"]
    assert pauses == [scanner.RETRY_PAUSE]


def test_a_failed_stat_says_so(downloads: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    put(downloads, "photo.jpeg", b"jpeg bytes")
    real = os.lstat

    def lstat(path: Any, *args: Any, **kwargs: Any) -> os.stat_result:
        if Path(path).name == "photo.jpeg":
            raise OSError(errno.EPERM, os.strerror(errno.EPERM))
        return real(path, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", lstat)
    result = scan(make_config(downloads), nothing_known, now=NOW)
    detail = result.details[downloads / "photo.jpeg"]
    assert (detail.step, detail.denied) == ("stat", True)
    assert str(detail) == "EPERM Operation not permitted, on stat"


def test_an_error_without_errno_still_explains_itself() -> None:
    detail = scanner.SkipDetail.from_error("read", OSError("no code"))
    assert (str(detail), detail.denied) == ("OSError, on read", False)


# --- files readable only while the Mac is unlocked (Data Protection) -------------------


@pytest.mark.parametrize("cls", [1, 2])
def test_a_file_protected_while_locked_is_skipped_as_locked(
    downloads: Path, monkeypatch: pytest.MonkeyPatch, fake_protection: FakeProtection, cls: int
) -> None:
    photo = put(downloads, "photo.jpeg", b"jpeg bytes")
    put(downloads, "ok.pdf", b"fine")
    fake_protection.protect(monkeypatch, photo, cls=cls)
    fake_protection.lock_screen()
    pauses: list[float] = []
    result = scan(make_config(downloads), nothing_known, now=NOW, sleep=pauses.append)
    assert [c.path.name for c in result.candidates] == ["ok.pdf"]
    assert reasons(result)["photo.jpeg"] is SkipReason.LOCKED
    assert result.details == {}  # not "unreadable": nothing for the Full Disk Access hint
    assert pauses == []


def test_the_same_file_goes_ahead_once_the_mac_is_unlocked(
    downloads: Path, monkeypatch: pytest.MonkeyPatch, fake_protection: FakeProtection
) -> None:
    photo = put(downloads, "photo.jpeg", b"jpeg bytes")
    fake_protection.protect(monkeypatch, photo)
    fake_protection.lock_screen()
    assert reasons(scan(make_config(downloads), nothing_known, now=NOW)) == {
        "photo.jpeg": SkipReason.LOCKED
    }
    fake_protection.locked = False
    [candidate] = scan(make_config(downloads), nothing_known, now=NOW).candidates
    assert candidate.path == photo


def test_eperm_on_an_ordinary_file_stays_unreadable(
    downloads: Path, monkeypatch: pytest.MonkeyPatch, fake_protection: FakeProtection
) -> None:
    put(downloads, "photo.jpeg", b"jpeg bytes")
    failing_read(monkeypatch, "photo.jpeg", [errno.EPERM])
    fake_protection.lock_screen()  # class C (the default): the lock is not the reason
    result = scan(make_config(downloads), nothing_known, now=NOW)
    assert reasons(result)["photo.jpeg"] is SkipReason.UNREADABLE
    assert result.details[downloads / "photo.jpeg"].denied


@pytest.mark.parametrize("code", [errno.EIO, errno.ENOENT])
def test_other_errors_on_a_protected_file_stay_unreadable(
    downloads: Path, monkeypatch: pytest.MonkeyPatch, fake_protection: FakeProtection, code: int
) -> None:
    photo = put(downloads, "photo.jpeg", b"jpeg bytes")
    fake_protection.classes[photo] = 1
    failing_read(monkeypatch, "photo.jpeg", [code])
    result = scan(make_config(downloads), nothing_known, now=NOW)
    assert reasons(result)["photo.jpeg"] is SkipReason.UNREADABLE


def test_a_folder_unit_with_a_protected_file_waits_while_locked(
    downloads: Path, monkeypatch: pytest.MonkeyPatch, fake_protection: FakeProtection
) -> None:
    photo = put(downloads, "album/photo.jpeg", b"jpeg bytes")
    put(downloads, "album/notes.txt", b"notes")
    make_old(downloads / "album")
    fake_protection.protect(monkeypatch, photo)
    fake_protection.lock_screen()
    result = scan(make_config(downloads), nothing_known, now=NOW)
    assert reasons(result) == {"album": SkipReason.LOCKED}
    assert result.candidates == []

    fake_protection.locked = False
    again = scan(make_config(downloads), nothing_known, now=NOW)
    assert [c.path.name for c in again.candidates] == ["album"]


def test_a_folder_unit_refused_inside_is_skipped_whole_as_locked(
    downloads: Path, monkeypatch: pytest.MonkeyPatch, fake_protection: FakeProtection
) -> None:
    photo = put(downloads, "album/photo.jpeg", b"jpeg bytes")
    make_old(downloads / "album")
    fake_protection.classes[photo] = 1

    def manifest(_path: Path) -> Any:
        raise PermissionError(errno.EPERM, os.strerror(errno.EPERM), str(photo))

    monkeypatch.setattr(scanner, "unit_manifest", manifest)
    result = scan(make_config(downloads), nothing_known, now=NOW)
    assert reasons(result) == {"album": SkipReason.LOCKED}
    assert result.details == {}


def test_the_screen_is_asked_only_for_folder_units_and_once(
    downloads: Path, fake_protection: FakeProtection
) -> None:
    put(downloads, "photo.jpeg", b"jpeg bytes")
    scan(make_config(downloads), nothing_known, now=NOW)
    assert fake_protection.polls == 0
    put(downloads, "a/one.txt", b"1")
    put(downloads, "b/two.txt", b"2")
    make_old(downloads / "a")
    make_old(downloads / "b")
    scan(make_config(downloads), nothing_known, now=NOW)
    assert fake_protection.polls == 1


def test_a_duplicate_of_a_library_file_protected_while_locked_waits_as_locked(
    downloads: Path, monkeypatch: pytest.MonkeyPatch, fake_protection: FakeProtection
) -> None:
    filed = put(downloads / "Organized" / "Photos", "photo.jpeg", b"jpeg bytes")
    put(downloads, "photo copy.jpeg", b"jpeg bytes")
    state = FileState(FileStatus.ORGANIZED, 0, filed)
    cfg = make_config(downloads, duplicates="move")
    fake_protection.protect(monkeypatch, filed)
    fake_protection.lock_screen()
    result = scan(cfg, lambda _sha: state, now=NOW)
    # Neither a duplicate nor a new file: the library copy cannot be checked yet.
    assert result.duplicates == [] and result.candidates == []
    assert reasons(result)["photo copy.jpeg"] is SkipReason.LOCKED

    fake_protection.locked = False
    result = scan(cfg, lambda _sha: state, now=NOW)
    assert [d.candidate.path.name for d in result.duplicates] == ["photo copy.jpeg"]

"""Data Protection classes and the screen lock (``protection``).

The real ``getattrlist`` call runs only on a temporary file; the screen lock
is never asked of the real session (CoreGraphics or ioreg): a shim stands in.
"""

from __future__ import annotations

import errno
import os
import plistlib
import sys
from pathlib import Path

import pytest

from file_librarian import protection
from tests._helpers import FakeProtection


def test_the_real_class_of_a_temporary_file_is_an_int(tmp_path: Path) -> None:
    path = tmp_path / "photo.jpeg"
    path.write_bytes(b"jpeg bytes")
    found = protection._read_class(path)
    assert isinstance(found, int) and 1 <= found <= 4
    assert protection._read_class(tmp_path / "absent") is None


def test_the_real_class_never_follows_a_symlink(tmp_path: Path) -> None:
    (tmp_path / "link").symlink_to(tmp_path / "absent")
    assert protection._read_class(tmp_path / "link") in (None, 1, 2, 3, 4)


def ioreg_plist(*users: dict[str, object]) -> bytes:
    return plistlib.dumps({"IOConsoleUsers": list(users), "IOKitBuildVersion": "x"})


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        (ioreg_plist({"kCGSSessionOnConsoleKey": True, "CGSSessionScreenIsLocked": True}), True),
        (ioreg_plist({"kCGSSessionOnConsoleKey": True}), False),
        (
            ioreg_plist(
                {"kCGSSessionOnConsoleKey": False, "CGSSessionScreenIsLocked": True},
                {"kCGSSessionOnConsoleKey": True, "CGSSessionScreenIsLocked": False},
            ),
            False,
        ),
        (ioreg_plist({"CGSSessionScreenIsLocked": True}), True),
        (ioreg_plist(), None),
        (plistlib.dumps({"other": 1}), None),
        (plistlib.dumps([1, 2]), None),
        (b"not a plist", None),
    ],
)
def test_ioreg_output_is_parsed(output: bytes, expected: bool | None) -> None:
    assert protection.parse_ioreg(output) is expected


def fake_ioreg(folder: Path, output: bytes, *, code: int = 0) -> Path:
    data = folder / "ioreg.plist"
    data.write_bytes(output)
    shim = folder / "ioreg"
    shim.write_text(
        f"#!/bin/sh\n{sys.executable} -c 'import sys; "
        f'sys.stdout.buffer.write(open("{data}", "rb").read())\'\nexit {code}\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)
    return shim


def test_without_a_window_server_session_ioreg_tells(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(protection, "_session_locked", lambda: None)
    locked = ioreg_plist({"kCGSSessionOnConsoleKey": True, "CGSSessionScreenIsLocked": True})
    monkeypatch.setattr(protection, "IOREG", str(fake_ioreg(tmp_path, locked)))
    assert protection._read_screen() is True

    monkeypatch.setattr(protection, "IOREG", str(fake_ioreg(tmp_path, locked, code=1)))
    assert protection._read_screen() is None
    monkeypatch.setattr(protection, "IOREG", "/nonexistent/ioreg")
    assert protection._read_screen() is None


def test_the_window_server_answer_comes_first(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(protection, "_session_locked", lambda: False)
    assert protection._read_screen() is False


def test_the_seams_are_faked_in_every_test(fake_protection: FakeProtection) -> None:
    assert protection.screen_locked() is False
    assert protection.protection_class(Path("/any/photo.jpeg")) == 3
    fake_protection.lock_screen(unlock_after=1)
    assert protection.screen_locked() is True
    assert protection.screen_locked() is False
    assert fake_protection.polls == 3


@pytest.mark.parametrize(
    ("code", "cls", "expected"),
    [
        (errno.EPERM, 1, True),
        (errno.EACCES, 2, True),
        (errno.EPERM, 3, False),
        (errno.EPERM, 4, False),
        (errno.EIO, 1, False),
        (errno.ENOENT, 1, False),
    ],
)
def test_locked_out_needs_a_denial_and_class_a_or_b(
    fake_protection: FakeProtection, code: int, cls: int, expected: bool
) -> None:
    path = Path("/x/photo.jpeg")
    fake_protection.classes[path] = cls
    assert protection.is_locked_out(OSError(code, os.strerror(code)), path) is expected


def test_locked_out_looks_at_the_file_the_error_names(fake_protection: FakeProtection) -> None:
    inside = Path("/x/album/photo.jpeg")
    fake_protection.classes[inside] = 1
    exc = PermissionError(errno.EPERM, "denied", str(inside))
    assert protection.is_locked_out(exc, Path("/x/album"))
    assert protection.is_locked_out(exc)
    assert not protection.is_locked_out(PermissionError(errno.EPERM, "denied"))


def test_has_locked_files_looks_inside_a_folder(
    tmp_path: Path, fake_protection: FakeProtection
) -> None:
    (tmp_path / "album" / "deep").mkdir(parents=True)
    (tmp_path / "album" / "notes.txt").write_bytes(b"notes")
    photo = tmp_path / "album" / "deep" / "photo.jpeg"
    photo.write_bytes(b"jpeg")
    assert not protection.has_locked_files(tmp_path / "album")
    fake_protection.classes[photo] = 2
    assert protection.has_locked_files(tmp_path / "album")
    assert not protection.has_locked_files(tmp_path / "absent")

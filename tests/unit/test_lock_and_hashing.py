from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from file_librarian.db import Store
from file_librarian.hashing import unit_manifest
from file_librarian.lock import AlreadyRunningError, instance_lock, lock_path
from file_librarian.models import FileState, FileStatus


def test_lock_excludes_a_second_holder(tmp_path: Path) -> None:
    database = tmp_path / "data" / "library.db"
    with instance_lock(database):
        assert lock_path(database).read_text() == str(os.getpid())
        with (
            pytest.raises(AlreadyRunningError, match=f"pid {os.getpid()}"),
            instance_lock(database),
        ):
            pass
    with instance_lock(database):  # released on exit
        pass


def test_lock_is_released_when_the_holder_dies(tmp_path: Path) -> None:
    database = tmp_path / "library.db"
    code = (
        "import os, sys; from pathlib import Path; from file_librarian.lock import instance_lock\n"
        f"with instance_lock(Path({str(database)!r})): os._exit(1)\n"
    )
    # Fixed interpreter and code built from a temp path: no untrusted input.
    subprocess.run([sys.executable, "-c", code], check=False)  # noqa: S603
    with instance_lock(database):
        pass


def test_unit_digest_tracks_changes_inside(tmp_path: Path) -> None:
    unit = tmp_path / "Tool.app"
    (unit / "Contents").mkdir(parents=True)
    (unit / "Contents" / "Info.plist").write_text("v1", encoding="utf-8")
    first = unit_manifest(unit)
    assert first == unit_manifest(unit)
    assert first.entries == 2
    (unit / "Contents" / "Info.plist").write_text("v22", encoding="utf-8")
    second = unit_manifest(unit)
    assert second.digest != first.digest
    assert second.size_bytes == 3


def test_unit_digest_does_not_follow_symlinks(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "big.bin").write_bytes(b"x" * 1000)
    unit = tmp_path / "unit"
    unit.mkdir()
    (unit / "link").symlink_to(outside)
    manifest = unit_manifest(unit)
    assert manifest.entries == 1
    assert manifest.size_bytes == 0


def test_store_file_state(tmp_path: Path) -> None:
    with Store.open(tmp_path / "library.db") as store:
        assert store.file_state("abc") is None
        store.conn.execute(
            """INSERT INTO files (sha256, original_name, original_path, current_name,
                                  current_path, status, attempts, processed_at)
               VALUES ('abc', 'a', '/a', 'a', '/a', 'failed', 2, '2026-11-16')"""
        )
        assert store.file_state("abc") == FileState(FileStatus.FAILED, 2, Path("/a"))

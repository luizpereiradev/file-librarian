"""Single-instance lock (RF-34).

The scheduled run, a manual run and the MCP write tools must never change
files or the database at the same time. They all take this lock, an exclusive
``flock`` on a file next to the database. The operating system releases it if
the process dies, so a crash never leaves a stale lock behind.
"""

from __future__ import annotations

import fcntl
import os
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

LOCK_NAME = "flib.lock"
REPROCESS_LOCK_NAME = "flib-reprocess.lock"


class AlreadyRunningError(RuntimeError):
    """Another flib process holds the lock."""


def lock_path(database: Path) -> Path:
    return database.parent / LOCK_NAME


@contextmanager
def instance_lock(database: Path) -> Iterator[None]:
    """Hold the lock for the duration of the block, or fail immediately."""
    with _flock(
        lock_path(database),
        "another flib process (pid {pid}) is running; try again when it finishes",
    ):
        yield


@contextmanager
def reprocess_lock(database: Path) -> Iterator[None]:
    """One ``flib reprocess`` at a time, from planning to the last move.

    Separate from the instance lock, which a reprocess holds only to recover
    and to apply: two reprocesses planning at once would both pay the provider.
    """
    with _flock(
        database.parent / REPROCESS_LOCK_NAME,
        "another flib reprocess (pid {pid}) is running; try again when it finishes",
    ):
        yield


@contextmanager
def _flock(path: Path, busy: str) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            holder = os.pread(fd, 32, 0).decode(errors="replace").strip() or "unknown"
            raise AlreadyRunningError(busy.format(pid=holder)) from exc
        os.ftruncate(fd, 0)
        os.pwrite(fd, str(os.getpid()).encode(), 0)
        try:
            yield
        finally:
            os.ftruncate(fd, 0)
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)

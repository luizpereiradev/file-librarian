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


class AlreadyRunningError(RuntimeError):
    """Another flib process holds the lock."""


def lock_path(database: Path) -> Path:
    return database.parent / LOCK_NAME


@contextmanager
def instance_lock(database: Path) -> Iterator[None]:
    """Hold the lock for the duration of the block, or fail immediately."""
    path = lock_path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            holder = os.pread(fd, 32, 0).decode(errors="replace").strip() or "unknown"
            raise AlreadyRunningError(
                f"another flib process (pid {holder}) is running; try again when it finishes"
            ) from exc
        os.ftruncate(fd, 0)
        os.pwrite(fd, str(os.getpid()).encode(), 0)
        try:
            yield
        finally:
            os.ftruncate(fd, 0)
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)

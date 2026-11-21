"""Single-instance lock (RF-34).

The scheduled run, a manual run and the MCP write tools must never change
files or the database at the same time. They all take this lock, an exclusive
``flock`` on a file next to the database. The operating system releases it if
the process dies, so a crash never leaves a stale lock behind.

Runs started by hand fail at once when the lock is held. Automatic runs (the
daily run, watch mode) ask again every ``LOCK_POLL_SECONDS`` for up to
``wait`` seconds first, so the daily run is not lost to a watch run that
happens to be going on. Nothing is ever done to the process holding it.
"""

from __future__ import annotations

import fcntl
import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

LOCK_NAME = "flib.lock"
LOCK_POLL_SECONDS = 2.0
REPROCESS_LOCK_NAME = "flib-reprocess.lock"


class AlreadyRunningError(RuntimeError):
    """Another flib process holds the lock."""


def lock_path(database: Path) -> Path:
    return database.parent / LOCK_NAME


@contextmanager
def instance_lock(
    database: Path,
    *,
    wait: float = 0.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[None]:
    """Hold the lock for the duration of the block; if it is held, ask again for up
    to ``wait`` seconds, then fail."""
    with _flock(
        lock_path(database),
        "another flib process (pid {pid}) is running; try again when it finishes",
        wait=wait,
        clock=clock,
        sleep=sleep,
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
def _flock(
    path: Path,
    busy: str,
    *,
    wait: float = 0.0,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        deadline = clock() + wait
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError as exc:
                remaining = deadline - clock()
                if remaining > 0:
                    sleep(min(LOCK_POLL_SECONDS, remaining))
                    continue
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

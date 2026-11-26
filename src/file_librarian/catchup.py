"""After a scheduled run: wait for the Mac to be unlocked, then run once more.

Some files can be read only while the Mac is unlocked (Data Protection class
A or B, see ``protection``). A scheduled run that starts while the screen is
locked skips them as ``locked``. Instead of leaving them for the next day
(where the Mac may well be locked again), the scheduled run then:

1. releases the instance lock (the run is over), so a ``flib run`` by hand is
   never blocked while it waits;
2. tries to take the *waiter lock*, an exclusive ``flock`` on
   ``flib-waiter.lock`` next to the database, without blocking. If another
   process holds it, a waiter is alive and will run once more after the
   unlock (rescanning everything), so this one does not wait: one waiter is
   enough. The operating system releases the lock if the waiter dies, so a
   lock file left behind is simply taken. No process is ever signalled;
3. sleeps, asking every ``POLL_SECONDS`` whether the Mac is unlocked, up to
   ``schedule.wait_for_unlock_hours`` by the wall clock (time asleep counts).
   When the lock state cannot be told, it tries to open one of the skipped
   files instead. ``SIGTERM`` (``flib schedule --remove``, a logout) ends the
   wait cleanly;
4. once unlocked, releases the waiter lock and runs once more, with the same
   ``schedule`` trigger (a second run record). If another run is going on
   then, it simply exits: that run takes care of them.

Nothing here reads a file's contents except the one ``open`` of the fallback
in step 3, which reads no byte.
"""

from __future__ import annotations

import contextlib
import fcntl
import os
import signal
import threading
import time
from collections.abc import Callable, Iterable, Iterator
from enum import StrEnum
from pathlib import Path
from types import FrameType

from file_librarian import protection

POLL_SECONDS = 60.0
"""How often the waiter asks whether the Mac is unlocked."""

# What the run logs say about a wait; ``activity`` reads them back (#114).
CATCH_UP_LINE = "Catch-up run, after the Mac was unlocked."
"""First summary line of the run made after the unlock."""
WAITING_LINE = (
    "Waiting up to {hours} h for the Mac to be unlocked to read {count} file(s) "
    "protected while it is locked; then it runs once more."
)
"""In the log of a run that is going to wait."""
UNLOCKED_LINE = "The Mac was unlocked; running once more for the files skipped while it was locked."
TIMED_OUT_LINE = (
    "The Mac was not unlocked within {hours} h; the next scheduled run "
    "(or `flib run`) gets the files protected while it is locked."
)
STOPPED_LINE = "Stopped while waiting for an unlock (SIGTERM); the next run gets those files."
NOT_WAITING_LINE = (
    "Not waiting for an unlock: an earlier run is waiting already and runs once more after it."
)
WAITER_LOCK_NAME = "flib-waiter.lock"

Sleep = Callable[[float], None]
Clock = Callable[[], float]


class Waited(StrEnum):
    UNLOCKED = "unlocked"
    TIMED_OUT = "timed out"
    STOPPED = "stopped"


class Stopped(Exception):
    """Raised by the ``SIGTERM`` handler to end a wait."""


def waiter_lock_path(database: Path) -> Path:
    return database.parent / WAITER_LOCK_NAME


@contextlib.contextmanager
def waiter(database: Path) -> Iterator[bool]:
    """Hold the waiter lock for the block, if it is free: ``True`` if held.

    ``False`` means a waiter is alive already; nothing is done to it. The
    file's contents are never read or acted upon: only the ``flock`` counts.
    """
    with try_flock(waiter_lock_path(database)) as held:
        yield held


@contextlib.contextmanager
def try_flock(path: Path) -> Iterator[bool]:
    """Hold an exclusive ``flock`` on ``path`` for the block, if it is free: ``True`` if
    held, ``False`` if another process holds it (nothing is done to that process). The
    operating system releases it when the holder dies; the file's contents never count.
    Also used by watch mode for its own lock (``scheduler.watch``)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def unlocked(paths: Iterable[Path] = ()) -> bool:
    """Whether the Mac is unlocked now.

    When the lock state cannot be told, whether the first of ``paths`` (files
    skipped as ``locked``) can be opened now; nothing is read from it.
    """
    state = protection.screen_locked()
    if state is not None:
        return not state
    for path in paths:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except OSError:
            return False
        os.close(fd)
        return True
    return False


def wait_for_unlock(
    hours: float,
    ready: Callable[[], bool] = unlocked,
    *,
    clock: Clock = time.time,
    sleep: Sleep = time.sleep,
) -> Waited:
    """Sleep until ``ready()`` (checked every ``POLL_SECONDS``), for at most ``hours``.

    The limit is by ``clock`` (the wall clock), so time the Mac spends asleep
    counts. ``SIGTERM`` ends the wait with ``STOPPED``; the previous handler
    is restored afterwards.
    """
    deadline = clock() + hours * 3600
    with stop_on_sigterm():
        try:
            while True:
                if ready():
                    return Waited.UNLOCKED
                remaining = deadline - clock()
                if remaining <= 0:
                    return Waited.TIMED_OUT
                sleep(min(POLL_SECONDS, remaining))
        except Stopped:
            return Waited.STOPPED


@contextlib.contextmanager
def stop_on_sigterm() -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        yield  # signal handlers can only be set from the main thread
        return

    def handler(_signum: int, _frame: FrameType | None) -> None:
        raise Stopped

    previous = signal.signal(signal.SIGTERM, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)

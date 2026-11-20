"""After a scheduled run: wait for the Mac to be unlocked, then run once more.

Some files can be read only while the Mac is unlocked (Data Protection class
A or B, see ``protection``). A scheduled run that starts while the screen is
locked skips them as ``locked``. Instead of leaving them for the next day
(where the Mac may well be locked again), the scheduled run then:

1. releases the instance lock (the run is over), so a ``flib run`` by hand is
   never blocked while it waits;
2. takes the *waiter lock* (``flib-waiter.lock`` next to the database), taking
   over from a waiter left by an earlier scheduled run: that one is asked to
   stop (``SIGTERM``) and its place taken, so waiters never stack up. The start
   of every scheduled run does the same;
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
TAKEOVER_SECONDS = 10.0
"""How long a new waiter waits for an older one to stop."""
WAITER_LOCK_NAME = "flib-waiter.lock"

Sleep = Callable[[float], None]
Clock = Callable[[], float]
Kill = Callable[[int, int], None]


class Waited(StrEnum):
    UNLOCKED = "unlocked"
    TIMED_OUT = "timed out"
    STOPPED = "stopped"


class _Stopped(Exception):
    """Raised by the ``SIGTERM`` handler to end a wait."""


def waiter_lock_path(database: Path) -> Path:
    return database.parent / WAITER_LOCK_NAME


def _open(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    return os.open(path, os.O_RDWR | os.O_CREAT, 0o600)


def _try_lock(fd: int) -> bool:
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def _holder(fd: int) -> int | None:
    text = os.pread(fd, 32, 0).decode(errors="replace").strip()
    return int(text) if text.isdigit() and int(text) > 0 else None


@contextlib.contextmanager
def waiter(
    database: Path,
    *,
    kill: Kill = os.kill,
    sleep: Sleep = time.sleep,
    clock: Clock = time.monotonic,
) -> Iterator[bool]:
    """Hold the waiter lock for the block; ``True`` if it is held.

    An earlier waiter holding it is asked to stop (``SIGTERM`` to the pid it
    wrote) and given ``TAKEOVER_SECONDS`` to go. ``False`` if it did not.
    """
    fd = _open(waiter_lock_path(database))
    try:
        held = _try_lock(fd) or _take_over(fd, kill=kill, sleep=sleep, clock=clock)
        if not held:
            yield False
            return
        os.ftruncate(fd, 0)
        os.pwrite(fd, str(os.getpid()).encode(), 0)
        try:
            yield True
        finally:
            os.ftruncate(fd, 0)
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def _take_over(fd: int, *, kill: Kill, sleep: Sleep, clock: Clock) -> bool:
    """Ask the holder of the waiter lock on ``fd`` to stop, and take the lock once it has."""
    pid = _holder(fd)
    if pid is None or pid == os.getpid():
        return False
    with contextlib.suppress(ProcessLookupError, PermissionError):
        kill(pid, signal.SIGTERM)
    deadline = clock() + TAKEOVER_SECONDS
    while clock() < deadline:
        sleep(0.2)
        if _try_lock(fd):
            return True
    return _try_lock(fd)


def stop_waiter(
    database: Path,
    *,
    kill: Kill = os.kill,
    sleep: Sleep = time.sleep,
    clock: Clock = time.monotonic,
) -> bool:
    """Stop a waiter left by an earlier scheduled run, if there is one. ``True`` if there was.

    Called when a scheduled run starts: whatever it waited for, this run does now.
    """
    path = waiter_lock_path(database)
    if not path.exists():
        return False
    fd = _open(path)
    try:
        if _try_lock(fd):
            fcntl.flock(fd, fcntl.LOCK_UN)
            return False
        if _take_over(fd, kill=kill, sleep=sleep, clock=clock):
            fcntl.flock(fd, fcntl.LOCK_UN)
        return True
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
    with _stop_on_sigterm():
        try:
            while True:
                if ready():
                    return Waited.UNLOCKED
                remaining = deadline - clock()
                if remaining <= 0:
                    return Waited.TIMED_OUT
                sleep(min(POLL_SECONDS, remaining))
        except _Stopped:
            return Waited.STOPPED


@contextlib.contextmanager
def _stop_on_sigterm() -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        yield  # signal handlers can only be set from the main thread
        return

    def handler(_signum: int, _frame: FrameType | None) -> None:
        raise _Stopped

    previous = signal.signal(signal.SIGTERM, handler)
    try:
        yield
    finally:
        signal.signal(signal.SIGTERM, previous)

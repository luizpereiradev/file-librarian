"""Waiting for the Mac to be unlocked after a scheduled run (``catchup``)."""

from __future__ import annotations

import fcntl
import os
import signal
from collections.abc import Iterator
from pathlib import Path

import pytest

from file_librarian import catchup
from file_librarian.catchup import POLL_SECONDS, Waited, wait_for_unlock
from tests._helpers import FakeProtection


class FakeTime:
    """A wall clock that moves only when the waiter sleeps."""

    def __init__(self) -> None:
        self.now = 1_000_000.0
        self.sleeps: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def after(polls: int) -> Iterator[bool]:
    """``ready`` answers: not yet ``polls`` times, then yes."""
    yield from [False] * polls
    while True:
        yield True


def test_waits_until_unlocked_polling_every_minute() -> None:
    clock = FakeTime()
    answers = after(3)
    got = wait_for_unlock(8, lambda: next(answers), clock=clock.clock, sleep=clock.sleep)
    assert got is Waited.UNLOCKED
    assert clock.sleeps == [POLL_SECONDS] * 3


def test_already_unlocked_does_not_sleep() -> None:
    clock = FakeTime()
    assert wait_for_unlock(8, lambda: True, clock=clock.clock, sleep=clock.sleep) is (
        Waited.UNLOCKED
    )
    assert clock.sleeps == []


def test_gives_up_at_the_limit() -> None:
    clock = FakeTime()
    start = clock.now
    got = wait_for_unlock(2, lambda: False, clock=clock.clock, sleep=clock.sleep)
    assert got is Waited.TIMED_OUT
    assert clock.now - start == 2 * 3600
    assert max(clock.sleeps) == POLL_SECONDS
    assert len(clock.sleeps) == 120


def test_time_asleep_counts_against_the_limit() -> None:
    clock = FakeTime()

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        clock.now += 5 * 3600  # the Mac slept meanwhile

    got = wait_for_unlock(8, lambda: False, clock=clock.clock, sleep=sleep)
    assert got is Waited.TIMED_OUT
    assert len(clock.sleeps) == 2


def test_sigterm_ends_the_wait_cleanly() -> None:
    clock = FakeTime()
    before = signal.getsignal(signal.SIGTERM)

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        if len(clock.sleeps) == 2:
            signal.raise_signal(signal.SIGTERM)

    got = wait_for_unlock(8, lambda: False, clock=clock.clock, sleep=sleep)
    assert got is Waited.STOPPED
    assert len(clock.sleeps) == 2
    assert signal.getsignal(signal.SIGTERM) == before


# --- the waiter lock ---------------------------------------------------------------------


def hold(database: Path) -> int:
    """Hold the waiter lock from another open file, as a live waiter does."""
    path = catchup.waiter_lock_path(database)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


def no_signals(monkeypatch: pytest.MonkeyPatch) -> None:
    def kill(*_args: object) -> None:
        pytest.fail("flib must never signal another process")

    monkeypatch.setattr(os, "kill", kill)
    monkeypatch.setattr(os, "killpg", kill)


def test_the_waiter_lock_is_taken_and_released(tmp_path: Path) -> None:
    database = tmp_path / "library.db"
    with catchup.waiter(database) as held:
        assert held
    with catchup.waiter(database) as held:
        assert held


def test_a_live_waiter_keeps_its_place_and_is_not_signalled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    no_signals(monkeypatch)
    database = tmp_path / "library.db"
    fd = hold(database)
    try:
        with catchup.waiter(database) as held:
            assert not held
    finally:
        os.close(fd)
    with catchup.waiter(database) as held:
        assert held  # it died (here: closed): the system released its lock


def test_a_stale_lock_file_is_simply_taken(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    no_signals(monkeypatch)
    database = tmp_path / "library.db"
    # Left behind by an older flib, or tampered with: its contents mean nothing.
    catchup.waiter_lock_path(database).write_text("1\n", encoding="utf-8")
    with catchup.waiter(database) as held:
        assert held


# --- is the Mac unlocked? ---------------------------------------------------------------


def test_unlocked_follows_the_screen(fake_protection: FakeProtection, tmp_path: Path) -> None:
    assert catchup.unlocked([tmp_path / "photo.jpeg"])
    fake_protection.lock_screen()
    assert not catchup.unlocked([tmp_path / "photo.jpeg"])


def test_unknown_screen_tries_to_open_a_skipped_file(
    fake_protection: FakeProtection, tmp_path: Path
) -> None:
    fake_protection.locked = None
    photo = tmp_path / "photo.jpeg"
    assert not catchup.unlocked([photo])  # cannot be opened (here: not there)
    photo.write_bytes(b"jpeg")
    assert catchup.unlocked([photo])
    assert not catchup.unlocked([])

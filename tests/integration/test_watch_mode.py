"""Watch mode (#90): runs when something arrives, without loops, bursts or partial files.

launchd is never involved: each test starts what the watch agent starts
(``flib run --trigger watch``, or ``scheduler.watch.watch`` with a fake clock
and sleep), as many times as launchd would.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import os
import plistlib
import signal
import sqlite3
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from file_librarian import catchup, hashing, pipeline, runlog, scanner, scheduler
from file_librarian import status as status_mod
from file_librarian.cli import app
from file_librarian.config import Config, load_config
from file_librarian.db import store as store_mod
from file_librarian.db.store import CorrectionRow, Store, day_start_utc
from file_librarian.lock import AlreadyRunningError, instance_lock
from file_librarian.models import Classification, FileStatus, RunMode, RunTrigger
from file_librarian.providers.base import FatalProviderError, Usage
from file_librarian.providers.fake import FakeProvider
from file_librarian.scanner import HashCache, ScanMemory, SkipReason, scan
from file_librarian.scheduler import watch
from tests._helpers import (
    OLD,
    FakeProtection,
    answer,
    fake_config,
    library,
    put,
    rows,
    write_config,
    write_xattr,
)

pytestmark = pytest.mark.integration

runner = CliRunner()


class Clock:
    """A fake clock and sleep: each sleep moves the clock, then runs ``on_sleep``."""

    def __init__(self, on_sleep: Callable[[int], object] | None = None) -> None:
        self.now = 1_000_000.0
        self.start = self.now
        self.sleeps: list[float] = []
        self._on_sleep = on_sleep

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        if self._on_sleep is not None:
            self._on_sleep(len(self.sleeps))


class Counted:
    """A ``run_once`` that only counts its calls."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, _memory: ScanMemory | None = None) -> bool:
        self.calls += 1
        return True


def triggered(config: Path) -> str:
    """What one start of the watch agent prints; it must succeed."""
    result = runner.invoke(app, ["run", "-c", str(config), "--trigger", "watch"])
    assert result.exit_code == 0, result.stdout + result.stderr
    return result.stdout


def triggers(cfg: Config) -> list[str]:
    return [row["trigger"] for row in rows(cfg, "SELECT trigger FROM runs ORDER BY id")]


def organizer(cfg: Config, provider: FakeProvider) -> Callable[[ScanMemory | None], bool]:
    """``run_once`` as the CLI gives it: one real watch run."""

    def run_once(memory: ScanMemory | None = None) -> bool:
        pipeline.run(cfg, provider, simulate=False, trigger=RunTrigger.WATCH, memory=memory)
        return True

    return run_once


def test_nothing_new_is_no_run_and_no_provider_call(downloads: Path, tmp_path: Path) -> None:
    config = write_config(tmp_path, downloads)
    assert triggered(config) == ""  # silent: no run, no log line
    cfg = load_config(config)
    assert triggers(cfg) == []
    provider = FakeProvider(default=answer())
    clock = Clock()
    ended = watch.watch(cfg, organizer(cfg, provider), clock=clock, sleep=clock.sleep)
    assert ended == watch.Watched(watch.Ended.NOTHING_NEW)
    assert provider.calls == [] and clock.sleeps == []


def test_ten_files_arriving_together_are_one_run(downloads: Path, tmp_path: Path) -> None:
    config = write_config(tmp_path, downloads)
    for i in range(10):
        put(downloads, f"file-{i}.txt", f"content {i}".encode())
    stdout = triggered(config)
    cfg = load_config(config)
    assert triggers(cfg) == ["watch"]  # the trigger is recorded
    assert "Watch run: something arrived in an input folder." in stdout
    assert sorted(p.name for p in downloads.iterdir()) == ["Organized"]
    assert rows(cfg, "SELECT COUNT(*) AS n FROM files")[0]["n"] == 10


def test_flib_own_moves_do_not_start_a_loop(downloads: Path, tmp_path: Path) -> None:
    """launchd starts the agent again after the run moved files out of the folder; those
    starts, and any after them, find nothing new: no run, no provider call."""
    cfg = fake_config(downloads)
    put(downloads, "bill.pdf", b"bill")
    put(downloads, "broken.pdf", b"no answer for this one")  # fails, stays, retried later
    provider = FakeProvider({"bill.pdf": answer()})
    clock = Clock()
    first = watch.watch(cfg, organizer(cfg, provider), clock=clock, sleep=clock.sleep)
    assert first.ended is watch.Ended.RAN and first.runs == 1
    assert len(provider.calls) == 2
    assert (downloads / "broken.pdf").exists() and not (downloads / "bill.pdf").exists()
    for _ in range(5):  # the folder changed: launchd starts it again, and again
        again = watch.watch(cfg, organizer(cfg, provider), clock=clock, sleep=clock.sleep)
        assert again.ended is watch.Ended.NOTHING_NEW
    assert len(provider.calls) == 2  # the failed file is not new: the daily run retries it
    assert triggers(cfg) == ["watch"]
    # A new arrival is new, and that run retries the failed file with it.
    put(downloads, "letter.pdf", b"letter")
    watch.watch(cfg, organizer(cfg, provider), clock=clock, sleep=clock.sleep)
    assert triggers(cfg) == ["watch", "watch"]


def test_the_handled_set_names_no_file(downloads: Path) -> None:
    cfg = fake_config(downloads)
    put(downloads, "broken.pdf", b"x")
    provider = FakeProvider()
    watch.watch(cfg, organizer(cfg, provider), clock=Clock(), sleep=lambda _s: None)
    with Store.open(cfg.database) as store:
        stored = store.meta(watch.HANDLED_KEY)
    assert stored is not None and "broken" not in stored and str(downloads) not in stored


def test_a_download_in_progress_settles_then_one_run(downloads: Path) -> None:
    cfg = fake_config(downloads, limits={"stable_seconds": 0}, schedule={"watch_quiet_seconds": 0})
    put(downloads, "ready.txt", b"ready")
    partial = put(downloads, "report.pdf.crdownload", b"half", mtime=time.time())

    def finish(sleeps: int) -> None:
        if sleeps == 2:  # the browser finishes and renames it
            partial.rename(downloads / "report.pdf")

    clock = Clock(finish)
    provider = FakeProvider(default=answer())
    runs: list[int] = []

    def run_once(memory: ScanMemory | None) -> bool:
        runs.append(len(clock.sleeps))
        return organizer(cfg, provider)(memory)

    ended = watch.watch(cfg, run_once, clock=clock, sleep=clock.sleep)
    assert ended.ended is watch.Ended.RAN and ended.runs == 1
    assert runs == [2]  # one run, once the download was done: never before
    assert clock.sleeps == [watch.POLL_SECONDS] * 2
    assert sorted(c.path.name for c in provider.calls) == ["ready.txt", "report.pdf"]


def test_a_file_still_changing_is_waited_for(downloads: Path) -> None:
    cfg = fake_config(downloads)  # stable_seconds: 60, watch_quiet_seconds: 300
    now = time.time()
    put(downloads, "copying.mov", b"frames", mtime=now)
    found = watch.probe(cfg, now=now)
    assert (found.new, found.pending) == (0, 1)
    # A pause longer than stable_seconds is not enough for a watch run: a slow writer
    # (a torrent, `curl -o`, a copy from a slow disk) often pauses that long.
    assert watch.probe(cfg, now=now + 61).new == 0
    assert watch.probe(cfg, now=now + 301).new == 1
    # A daily run keeps to stable_seconds.
    assert [c.path.name for c in scan(cfg, lambda _sha: None, now=now + 61).candidates] == [
        "copying.mov"
    ]


def test_the_settle_limit_ends_the_wait(downloads: Path) -> None:
    cfg = fake_config(downloads, schedule={"watch_settle_minutes": 2})
    stuck = watch.Probe(new=0, pending=1, keys=frozenset())
    clock = Clock()
    ran = Counted()
    ended = watch.watch(cfg, ran, look=lambda: stuck, clock=clock, sleep=clock.sleep)
    assert ended == watch.Watched(watch.Ended.STILL_PENDING, 0, 1)
    assert sum(clock.sleeps) == 120 and ran.calls == 0


def test_ready_files_go_ahead_when_others_never_settle(downloads: Path) -> None:
    cfg = fake_config(downloads, schedule={"watch_settle_minutes": 1})
    looks = iter(
        [watch.Probe(1, 1, frozenset({"a"}))] * 5 + [watch.Probe(0, 1, frozenset({"a"}))] * 5
    )
    clock = Clock()
    ran = Counted()
    ended = watch.watch(cfg, ran, look=lambda: next(looks), clock=clock, sleep=clock.sleep)
    assert ran.calls == 1 and ended.ended is watch.Ended.RAN and ended.pending == 1


def test_stale_partial_downloads_and_young_items_are_not_pending(downloads: Path) -> None:
    other = downloads.parent / "Inbox"
    other.mkdir()
    cfg = fake_config(
        downloads,
        inputs=[str(downloads), {"path": str(other), "min_age_days": 3}],
    )
    put(downloads, "abandoned.zip.part", b"x")  # changed hours ago
    put(other, "fresh.pdf", b"y", mtime=time.time() - 3600)  # too recent for its input
    found = watch.probe(cfg)
    assert (found.new, found.pending) == (0, 0)


def test_a_run_holding_the_lock_makes_it_exit_quietly(downloads: Path, tmp_path: Path) -> None:
    config = write_config(tmp_path, downloads)
    cfg = load_config(config)
    put(downloads, "bill.pdf", b"bill")
    with instance_lock(cfg.database):
        stdout = triggered(config)
    assert "Not running: another flib process" in stdout
    assert triggers(cfg) == []
    with Store.open(cfg.database) as store:
        assert store.meta(watch.HANDLED_KEY) is None  # still new for the next trigger
    triggered(config)
    assert triggers(cfg) == ["watch"]


def test_watch_runs_do_not_stack(downloads: Path) -> None:
    cfg = fake_config(downloads)
    put(downloads, "bill.pdf", b"bill")

    def look() -> watch.Probe:
        pytest.fail("a second watch run must not even look")

    with catchup.try_flock(watch.watch_lock_path(cfg.database)) as held:
        assert held
        ended = watch.watch(cfg, Counted(), look=look)
    assert ended.ended is watch.Ended.WATCHING
    # The watch lock and the unlock waiter's lock are different files: one never blocks
    # the other, and each allows a single process.
    assert watch.watch_lock_path(cfg.database) != catchup.waiter_lock_path(cfg.database)


def test_sigterm_while_settling_ends_cleanly(downloads: Path) -> None:
    cfg = fake_config(downloads)
    before = signal.getsignal(signal.SIGTERM)

    def sleep(_seconds: float) -> None:
        signal.raise_signal(signal.SIGTERM)

    pending = watch.Probe(0, 1, frozenset())
    ended = watch.watch(cfg, Counted(), look=lambda: pending, sleep=sleep)
    assert ended.ended is watch.Ended.STOPPED
    assert signal.getsignal(signal.SIGTERM) == before


def test_locked_files_wait_for_the_unlock_after_the_watch_lock_is_released(
    downloads: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_protection: FakeProtection,
) -> None:
    config = write_config(tmp_path, downloads)
    cfg = load_config(config)
    put(downloads, "bill.pdf", b"bill")
    photo = put(downloads, "photo.jpeg", b"jpeg")
    fake_protection.protect(monkeypatch, photo)
    seen: list[bool] = []

    def wait(hours: float, ready: Callable[[], bool]) -> catchup.Waited:
        with catchup.try_flock(watch.watch_lock_path(cfg.database)) as free:
            seen.append(free)  # a new watch trigger could start meanwhile
        return catchup.Waited.UNLOCKED

    monkeypatch.setattr(catchup, "wait_for_unlock", wait)
    fake_protection.lock_screen(unlock_after=0)
    stdout = triggered(config)
    assert seen == [True]
    assert "The Mac was unlocked; running once more" in stdout
    assert triggers(cfg) == ["watch", "watch"]


def test_a_live_unlock_waiter_is_not_doubled_by_a_watch_run(
    downloads: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_protection: FakeProtection,
) -> None:
    config = write_config(tmp_path, downloads)
    cfg = load_config(config)
    photo = put(downloads, "photo.jpeg", b"jpeg")
    put(downloads, "bill.pdf", b"bill")
    fake_protection.protect(monkeypatch, photo)
    fake_protection.lock_screen()

    def kill(*_args: object) -> None:
        pytest.fail("flib must never signal another process")

    monkeypatch.setattr(os, "kill", kill)
    with catchup.waiter(cfg.database) as held:  # the daily run's waiter, still alive
        assert held
        stdout = triggered(config)
    assert "Not waiting for an unlock: an earlier run is waiting already" in stdout
    assert triggers(cfg) == ["watch"]


def test_items_still_pending_are_reported(downloads: Path, tmp_path: Path) -> None:
    config = write_config(tmp_path, downloads, extra="schedule: {watch_settle_minutes: 0}\n")
    put(downloads, "movie.mp4.crdownload", b"x", mtime=time.time())
    stdout = triggered(config)
    assert "1 item(s) are still downloading or changing (no change for 0 min" in stdout
    assert "next start (at most 30 min later)" in stdout
    assert triggers(load_config(config)) == []


# --- The daily cost cap ----------------------------------------------------------------


class Priced(FakeProvider):
    """A fake provider whose every call costs US$ 0.40."""

    def classify(self, context: object, rules: object) -> Classification:
        self.last_usage = Usage(input_tokens=1)
        return super().classify(context, rules)  # type: ignore[arg-type]

    def cost_usd(self, usage: Usage) -> float:
        return 0.40 if usage.input_tokens else 0.0


def spend_today(cfg: Config, usd: float, trigger: str = "schedule") -> None:
    now = datetime.now(UTC).isoformat(timespec="seconds")
    with Store.open(cfg.database) as store:
        store.conn.execute(
            "INSERT INTO runs (mode, trigger, started_at, finished_at, cost_usd)"
            " VALUES ('run', ?, ?, ?, ?)",
            (trigger, now, now, usd),
        )


def test_the_daily_cap_stops_automatic_runs_only(downloads: Path) -> None:
    cfg = fake_config(downloads, limits={"max_cost_usd_per_day": 1.0})
    spend_today(cfg, 0.70, "schedule")
    spend_today(cfg, 5.00, "manual")  # runs by hand never count
    for name in ("a.txt", "b.txt", "c.txt"):
        put(downloads, name, name.encode())
    provider = Priced(default=answer())
    outcome = pipeline.run(cfg, provider, simulate=False, trigger=RunTrigger.WATCH)
    assert len(provider.calls) == 1  # US$ 0.30 left: one call, then the rest wait
    assert outcome.deferred == 2 and outcome.day_cap_reached
    again = pipeline.run(cfg, provider, simulate=False, trigger=RunTrigger.SCHEDULE)
    assert len(provider.calls) == 1 and again.day_cap_reached  # nothing left today
    by_hand = pipeline.run(cfg, provider, simulate=False, trigger=RunTrigger.MANUAL)
    assert len(provider.calls) == 3 and not by_hand.day_cap_reached


def test_the_daily_cap_defaults_to_three_runs(downloads: Path) -> None:
    assert fake_config(downloads).limits.cost_cap_per_day == 3.0
    assert fake_config(downloads, limits={"max_cost_usd_per_run": 2}).limits.cost_cap_per_day == 6
    off = fake_config(downloads, limits={"max_cost_usd_per_day": None})
    assert off.limits.cost_cap_per_day is None
    uncapped = fake_config(downloads, limits={"max_cost_usd_per_run": None})
    assert uncapped.limits.cost_cap_per_day is None


def test_the_daily_cap_is_said_once_a_day(downloads: Path, tmp_path: Path) -> None:
    config = write_config(tmp_path, downloads, extra="limits: {max_cost_usd_per_day: 0.5}\n")
    cfg = load_config(config)
    spend_today(cfg, 0.5)
    put(downloads, "a.txt", b"a")
    first = triggered(config)
    assert "Automatic runs reached today's cost cap (US$ 0.50" in first
    put(downloads, "b.txt", b"b")
    second = triggered(config)
    assert "Watch run" in second and "cost cap" not in second
    shown = runner.invoke(app, ["status", "-c", str(config)]).stdout
    assert "Automatic runs reached today's cost cap" in shown
    assert "Automatic runs today: US$ 0.5000 of US$ 0.50" in shown
    assert (downloads / "a.txt").exists()  # nothing sent, nothing moved


# --- status and doctor ---------------------------------------------------------------


def test_status_shows_the_mode_and_the_last_watch_run(
    downloads: Path, tmp_path: Path, fake_launchd: Path
) -> None:
    config = write_config(tmp_path, downloads, extra="schedule: {mode: both}\n")
    runner.invoke(app, ["schedule", "-c", str(config), "--no-check"])
    cfg = load_config(config)
    shown = runner.invoke(app, ["status", "-c", str(config)]).stdout
    assert "Watch mode: on; flib runs when something arrives in the input folder" in shown
    assert "(daily at 12:30)" in shown
    put(downloads, "bill.pdf", b"bill")
    triggered(config)
    shown = runner.invoke(app, ["status", "-c", str(config)]).stdout
    assert "Last watch run: " in shown and "1 organized" in shown
    assert "on arrival (watch mode)" in shown
    data = runner.invoke(app, ["status", "-c", str(config), "--json"]).stdout
    assert '"mode": "both"' in data and '"automatic_cost_usd": 0.0' in data
    doctor = runner.invoke(app, ["doctor", "-c", str(config), "--stdout"]).stdout
    assert "mode: both" in doctor and "watching: 1 input folder(s)" in doctor
    assert str(downloads) not in doctor
    assert library(downloads).exists()
    assert status_mod.collect(cfg).last_watch is not None


def test_status_with_watch_mode_only(downloads: Path, tmp_path: Path, fake_launchd: Path) -> None:
    config = write_config(tmp_path, downloads, extra="schedule: {mode: watch}\n")
    runner.invoke(app, ["schedule", "-c", str(config), "--no-check"])
    shown = runner.invoke(app, ["status", "-c", str(config)]).stdout
    assert "Watch mode: on" in shown and "Next run:" not in shown


def test_the_periodic_summary_stays_with_the_daily_run(
    downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    def summary(*_args: object, **_kwargs: object) -> list[str]:
        calls.append("summary")
        return ["SUMMARY"]

    monkeypatch.setattr(status_mod, "after_scheduled_run", summary)
    config = write_config(tmp_path, downloads)
    put(downloads, "a.txt", b"a")
    assert "SUMMARY" not in triggered(config)
    put(downloads, "b.txt", b"b")
    result = runner.invoke(app, ["run", "-c", str(config), "--trigger", "schedule"])
    assert "SUMMARY" in result.stdout and calls == ["summary"]


# --- Review fixes: starvation, slow writers, locks, hashing, clocks, unknown costs -------


def spend(cfg: Config, usd: float | None, trigger: str = "schedule", **extra: object) -> None:
    """A finished run with this cost (``started`` and ``files_seen`` in ``extra``)."""
    started = extra.get("started") or datetime.now(UTC).isoformat(timespec="seconds")
    with Store.open(cfg.database) as store:
        store.conn.execute(
            "INSERT INTO runs (mode, trigger, started_at, finished_at, cost_usd, files_seen)"
            " VALUES ('run', ?, ?, ?, ?, ?)",
            (trigger, started, started, usd, extra.get("files_seen", 0)),
        )


def test_a_backlog_over_max_files_never_keeps_new_arrivals_out(downloads: Path) -> None:
    cfg = fake_config(downloads, limits={"max_files_per_run": 2, "max_cost_usd_per_day": 0.5})
    for i, name in enumerate(["a.txt", "b.txt", "c.txt"]):
        put(downloads, name, name.encode(), mtime=OLD + i)
    spend_today(cfg, 0.5)  # today's budget is gone: all three stay
    provider = Priced(default=answer())
    clock = Clock()
    first = watch.watch(cfg, organizer(cfg, provider), clock=clock, sleep=clock.sleep)
    assert first.ended is watch.Ended.RAN and first.left == 3 and provider.calls == []
    with Store.open(cfg.database) as store:
        store.conn.execute("DELETE FROM runs")  # the budget is back
    put(downloads, "new-invoice.txt", b"fresh", mtime=time.time() - 3600)
    second = watch.watch(cfg, organizer(cfg, provider), clock=clock, sleep=clock.sleep)
    assert second.ended is watch.Ended.RAN
    # The new arrival went first, ahead of the older items left behind.
    assert provider.calls[0].path.name == "new-invoice.txt"
    assert not (downloads / "new-invoice.txt").exists()


def test_new_is_counted_over_every_ready_item(downloads: Path) -> None:
    cfg = fake_config(downloads, limits={"max_files_per_run": 1})
    for name in ("a.txt", "b.txt", "c.txt"):
        put(downloads, name, name.encode())
    found = watch.probe(cfg)
    assert (found.new, len(found.keys)) == (3, 3)


def test_items_left_behind_are_new_again_the_next_day(downloads: Path) -> None:
    cfg = fake_config(downloads)
    put(downloads, "broken.pdf", b"x")
    watch.watch(cfg, organizer(cfg, FakeProvider()), clock=Clock(), sleep=lambda _s: None)
    assert watch.probe(cfg).new == 0
    with Store.open(cfg.database) as store:
        value = json.loads(store.meta(watch.HANDLED_KEY) or "{}")
        value["day"] = "2000-01-01"
        store.set_meta(watch.HANDLED_KEY, json.dumps(value))
    assert watch.probe(cfg).new == 1


def test_a_stuck_backlog_shows_in_status(downloads: Path, tmp_path: Path) -> None:
    config = write_config(tmp_path, downloads, extra="limits: {max_cost_usd_per_day: 0.5}\n")
    cfg = load_config(config)
    spend_today(cfg, 0.5)
    put(downloads, "a.txt", b"a")
    put(downloads, "b.txt", b"b")
    triggered(config)
    shown = runner.invoke(app, ["status", "-c", str(config)]).stdout
    assert "Watch: 2 files wait in the inputs (daily cap reached)" in shown
    with Store.open(cfg.database) as store:
        store.conn.execute("DELETE FROM runs")
    triggered(config)  # nothing new: the count stays, and so does why
    assert "Watch: 2 files wait" in runner.invoke(app, ["status", "-c", str(config)]).stdout
    runner.invoke(app, ["run", "-c", str(config)])  # by hand: no cap
    triggered(config)
    assert "wait in the inputs" not in runner.invoke(app, ["status", "-c", str(config)]).stdout


def test_a_failing_provider_is_named_as_the_reason(downloads: Path) -> None:
    cfg = fake_config(downloads)
    put(downloads, "a.txt", b"a")
    outcome = pipeline.run(cfg, FakeProvider(), simulate=False, trigger=RunTrigger.WATCH)
    assert status_mod.backlog_reason(outcome) == "provider failing"
    assert status_mod.backlog_reason(None) is None


def test_finder_copies_in_progress_are_skipped_in_every_mode(downloads: Path) -> None:
    cfg = fake_config(downloads)
    marked = put(downloads, "big.mov", b"half a movie")
    write_xattr(marked, "com.apple.FinderInfo", scanner.FINDER_COPYING + b"\0" * 24)
    dated = put(downloads, "other.mov", b"also copying")
    copy_date = time.mktime((1984, 1, 24, 12, 0, 0, 0, 0, -1))
    os.utime(dated, (copy_date, copy_date))  # on APFS this moves the birth time too
    put(downloads, "done.pdf", b"done")
    found = scan(cfg, lambda _sha: None)
    assert [c.path.name for c in found.candidates] == ["done.pdf"]
    skipped = {path.name: reason for path, reason in found.skipped}
    assert skipped["big.mov"] is SkipReason.IN_PROGRESS
    if time.localtime(dated.stat().st_birthtime)[:3] == (1984, 1, 24):
        assert skipped["other.mov"] is SkipReason.IN_PROGRESS


def test_a_file_rewritten_with_the_same_size_and_mtime_is_not_moved(downloads: Path) -> None:
    cfg = fake_config(downloads)
    path = put(downloads, "report.txt", b"first version")
    st = path.stat()

    class Rewrites(FakeProvider):
        def classify(self, context: Any, rules: Any) -> Classification:
            path.write_bytes(b"other version")  # same length
            os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
            return super().classify(context, rules)

    outcome = pipeline.run(cfg, Rewrites(default=answer()), simulate=False)
    assert outcome.report is not None
    [item] = outcome.report.items
    assert item.status is FileStatus.SKIPPED and "changed" in item.note
    assert path.read_bytes() == b"other version"


def test_a_scheduled_run_waits_for_a_watch_run_to_finish(
    downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write_config(tmp_path, downloads)
    cfg = load_config(config)
    put(downloads, "bill.pdf", b"bill")
    monkeypatch.setattr("file_librarian.cli.LOCK_WAIT_SECONDS", 30.0)
    monkeypatch.setattr("file_librarian.lock.LOCK_POLL_SECONDS", 0.05)
    held = threading.Event()

    def watch_run() -> None:
        with instance_lock(cfg.database):
            held.set()
            time.sleep(0.3)

    other = threading.Thread(target=watch_run)
    other.start()
    held.wait()
    result = runner.invoke(app, ["run", "-c", str(config), "--trigger", "schedule"])
    other.join()
    assert result.exit_code == 0, result.stdout + result.stderr
    assert triggers(cfg) == ["schedule"]


def test_a_run_by_hand_still_fails_at_once(downloads: Path, tmp_path: Path) -> None:
    config = write_config(tmp_path, downloads)
    cfg = load_config(config)
    put(downloads, "bill.pdf", b"bill")
    with instance_lock(cfg.database):
        result = runner.invoke(app, ["run", "-c", str(config)])
        timed_out = runner.invoke(app, ["run", "-c", str(config), "--trigger", "schedule"])
    assert result.exit_code == 1 and "another flib process" in result.stderr
    assert timed_out.exit_code == 1  # after LOCK_WAIT_SECONDS (0 in tests)


def test_the_lock_wait_gives_up_after_its_limit(tmp_path: Path) -> None:
    clock = Clock()
    database = tmp_path / "library.db"
    waiting = instance_lock(database, wait=10, clock=clock, sleep=clock.sleep)
    with instance_lock(database), pytest.raises(AlreadyRunningError), waiting:
        pass
    assert sum(clock.sleeps) == 10


def test_settling_only_checks_the_pending_items_and_hashes_once(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = fake_config(downloads)
    for i in range(3):
        put(downloads, f"left-{i}.bin", bytes([i]) * 1000)
    partial = put(downloads, "movie.mp4.part", b"x", mtime=time.time())
    reads: list[str] = []
    real = hashing.file_sha256

    def counted(path: Path) -> str:
        reads.append(path.name)
        return real(path)

    monkeypatch.setattr(scanner, "file_sha256", counted)
    looks: list[int] = []
    real_probe = watch.probe

    def probe(config: Config, **kwargs: Any) -> watch.Probe:
        looks.append(1)
        return real_probe(config, **kwargs)

    monkeypatch.setattr(watch, "probe", probe)

    def finish(sleeps: int) -> None:
        if sleeps == 4:
            partial.unlink()  # cancelled

    clock = Clock(finish)
    ran = Counted()
    watch.watch(cfg, ran, clock=clock, sleep=clock.sleep)
    assert len(clock.sleeps) == 4
    # Scanned at the start, once it settled and after the run: never in between.
    assert len(looks) == 3
    assert sorted(reads) == ["left-0.bin", "left-1.bin", "left-2.bin"]  # each hashed once
    # The digests of what stays are kept, without names, for the next start.
    with Store.open(cfg.database) as store:
        kept = store.meta(watch.HASHES_KEY) or ""
    assert len(json.loads(kept)) == 3 and "left" not in kept
    reads.clear()
    watch.probe(cfg, memory=ScanMemory())
    assert reads == []  # a new process reuses them


def test_the_hash_cache_rehashes_a_changed_file(downloads: Path) -> None:
    cfg = fake_config(downloads)
    path = put(downloads, "a.txt", b"one")
    cache = HashCache()
    first = scan(cfg, lambda _sha: None, hashes=cache).candidates[0].sha256
    path.write_bytes(b"two")
    os.utime(path, (OLD + 5, OLD + 5))
    second = scan(cfg, lambda _sha: None, hashes=cache).candidates[0].sha256
    assert first != second


def test_the_settle_deadline_uses_a_monotonic_clock() -> None:
    assert inspect.signature(watch.watch).parameters["clock"].default is time.monotonic


def test_a_file_dated_in_the_future_settles_once_it_stops_changing(downloads: Path) -> None:
    cfg = fake_config(downloads)
    t = time.time()
    future = t + 86400 * 365
    path = put(downloads, "from-share.pdf", b"x", mtime=future)
    memory = ScanMemory()
    first = watch.probe(cfg, now=t, memory=memory)
    assert (first.new, first.pending) == (0, 1)  # never settled at the first look
    settling = memory.settling
    assert watch.still_pending(cfg, first.pending_paths, now=t + 100, settling=settling)
    assert not watch.still_pending(cfg, first.pending_paths, now=t + 301, settling=settling)
    assert watch.still_pending(cfg, first.pending_paths, now=t + 301)  # no memory: wait
    assert watch.probe(cfg, now=t + 301, memory=memory).new == 1
    path.write_bytes(b"xy")  # changed after all (a share writing it): start over
    os.utime(path, (future, future))
    assert watch.probe(cfg, now=t + 302, memory=memory).new == 0
    assert watch.probe(cfg, now=t + 603, memory=memory).new == 1


def test_a_daily_run_takes_a_future_dated_file_from_its_second_look(downloads: Path) -> None:
    cfg = fake_config(downloads)
    t = time.time()
    put(downloads, "from-zip.pdf", b"x", mtime=t + 86400 * 365)
    provider = FakeProvider(default=answer())
    first = pipeline.run(cfg, provider, simulate=False, now=t, trigger=RunTrigger.SCHEDULE)
    assert provider.calls == [] and first.scan.future == []
    later = pipeline.run(cfg, provider, simulate=False, now=t + 61, trigger=RunTrigger.SCHEDULE)
    assert [c.path.name for c in provider.calls] == ["from-zip.pdf"]
    assert [p.name for p in later.scan.future] == ["from-zip.pdf"]


def test_the_future_slack_boundary(downloads: Path) -> None:
    cfg = fake_config(downloads)
    now = time.time()
    near = put(downloads, "near.pdf", b"a", mtime=now + scanner.FUTURE_SLACK - 1)
    put(downloads, "far.pdf", b"b", mtime=now + scanner.FUTURE_SLACK + 1)
    settling = scanner.Settling()
    found = scan(cfg, lambda _sha: None, now=now, settling=settling)
    skipped = {p.name: r for p, r in found.skipped}
    # Both wait: the near one as just changed, the far one until it is seen unchanged.
    assert skipped == {"near.pdf": SkipReason.UNSTABLE, "far.pdf": SkipReason.UNSTABLE}
    assert len(settling.seen) == 1  # only the far one is observed
    assert watch.still_pending(cfg, [(near, False)], now=now)


def test_the_hash_cache_never_keeps_a_stale_digest(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A file rewritten in place with its size and mtime put back."""
    cfg = fake_config(downloads)
    path = put(downloads, "report.txt", b"A" * 64)
    clock = Clock()
    watch.watch(cfg, organizer(cfg, FakeProvider()), clock=clock, sleep=clock.sleep)  # fails
    st = path.stat()
    with path.open("r+b") as fh:
        fh.write(b"B" * 64)
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns))
    found = watch.probe(cfg, memory=ScanMemory())
    assert found.new == 1  # a new content: ctime moved, so the stored digest is not used
    provider = FakeProvider({"report.txt": answer()})
    watch.watch(cfg, organizer(cfg, provider), clock=clock, sleep=clock.sleep)
    [row] = rows(cfg, "SELECT sha256 FROM files WHERE status = 'organized'")
    assert row["sha256"] == hashlib.sha256(b"B" * 64).hexdigest()


def test_a_large_file_is_hashed_again_by_the_run_even_when_cached(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("file_librarian.pipeline.RECHECK_HASH_MAX_BYTES", 1)
    cfg = fake_config(downloads)
    path = put(downloads, "big.bin", b"Z" * 64)
    memory = ScanMemory()
    st = path.stat()
    memory.hashes.entries[HashCache.key(st)] = "0" * 64  # a wrong digest in the cache
    provider = FakeProvider(default=answer())
    pipeline.run(cfg, provider, simulate=False, trigger=RunTrigger.WATCH, memory=memory)
    [row] = rows(cfg, "SELECT sha256 FROM files")
    assert row["sha256"] == hashlib.sha256(b"Z" * 64).hexdigest()
    assert memory.hashes.verify_above is None


def test_a_wrong_digest_is_forgotten_when_the_move_is_refused(downloads: Path) -> None:
    cache = HashCache()
    path = put(downloads, "a.txt", b"a")
    cache.digest(path, path.stat(), lambda _p: "x")
    assert cache.entries
    cache.forget(path)
    assert cache.entries == {} and cache.seen == {}
    cache.forget(path)  # twice is fine


def test_a_slow_download_keeps_the_wait_going(downloads: Path) -> None:
    """A download that is still growing after watch_settle_minutes is waited for."""
    cfg = fake_config(downloads, schedule={"watch_settle_minutes": 2})
    pending = watch.Probe(0, 1, frozenset(), ((downloads / "x.part", True),))
    done = watch.Probe(1, 0, frozenset({"k"}))
    clock = Clock()
    growing = {"size": 0}

    def observe(_paths: object) -> object:
        return growing["size"]

    def recheck(_paths: object) -> bool:
        return clock.now - clock.start < 1800  # it finishes after 30 minutes

    looks: list[watch.Probe] = []

    def look() -> watch.Probe:
        found = pending if clock.now - clock.start < 1800 else done
        if found is done and done in looks:
            found = watch.Probe(0, 0, frozenset({"k"}))  # after the run: nothing new
        looks.append(found)
        return found

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        if clock.now - clock.start < 1800:
            growing["size"] += 1  # it keeps growing

    ran = Counted()
    watch.watch(cfg, ran, look=look, recheck=recheck, observe_paths=observe, clock=clock,
                sleep=sleep)  # fmt: skip
    assert ran.calls == 1
    # 15 s polls at first, 60 s once five minutes passed.
    assert clock.sleeps[0] == watch.POLL_SECONDS and clock.sleeps[-1] == watch.SLOW_POLL_SECONDS


def test_a_stalled_download_is_given_up_after_the_idle_limit(downloads: Path) -> None:
    cfg = fake_config(downloads, schedule={"watch_settle_minutes": 2})
    pending = watch.Probe(0, 1, frozenset(), ((downloads / "x.part", True),))
    clock = Clock()
    ended = watch.watch(
        cfg, Counted(), look=lambda: pending, recheck=lambda _p: True,
        observe_paths=lambda _p: "same", clock=clock, sleep=clock.sleep,
    )  # fmt: skip
    assert ended.ended is watch.Ended.STILL_PENDING and sum(clock.sleeps) == 120


def test_the_hard_limit_ends_even_a_download_that_never_stops(downloads: Path) -> None:
    cfg = fake_config(downloads, schedule={"watch_settle_minutes": 2})
    pending = watch.Probe(0, 1, frozenset(), ((downloads / "x.part", True),))
    clock = Clock()
    ended = watch.watch(
        cfg, Counted(), look=lambda: pending, recheck=lambda _p: True,
        observe_paths=lambda _p: clock.now, clock=clock, sleep=clock.sleep,
    )  # fmt: skip
    assert ended.ended is watch.Ended.STILL_PENDING
    assert sum(clock.sleeps) == watch.HARD_LIMIT_SECONDS


def test_observe_sees_a_growing_file(downloads: Path) -> None:
    path = put(downloads, "x.part", b"a")
    before = watch.observe([(path, True)])
    path.write_bytes(b"ab")
    assert watch.observe([(path, True)]) != before
    path.unlink()
    assert watch.observe([(path, True)]) == ()


def test_the_watch_agent_has_a_fallback_start(
    downloads: Path, tmp_path: Path, fake_launchd: Path
) -> None:
    config = write_config(tmp_path, downloads, extra="schedule: {mode: watch}\n")
    result = runner.invoke(app, ["schedule", "-c", str(config), "--no-check"])
    assert "It also looks every 30 min" in result.stdout
    data = plistlib.loads(scheduler.plist_path(scheduler.WATCH_LABEL).read_bytes())
    assert data["StartInterval"] == 1800
    assert "and every 30 min" in runner.invoke(app, ["schedule", "--status"]).stdout
    off = write_config(
        tmp_path, downloads, extra="schedule: {mode: watch, watch_fallback_minutes: 0}\n"
    )
    runner.invoke(app, ["schedule", "-c", str(off), "--no-check"])
    data = plistlib.loads(scheduler.plist_path(scheduler.WATCH_LABEL).read_bytes())
    assert "StartInterval" not in data
    assert scheduler.status().watch_fallback_minutes is None


def test_locked_files_are_left_to_the_fallback_start(
    downloads: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_protection: FakeProtection,
    fake_launchd: Path,
) -> None:
    config = write_config(tmp_path, downloads, extra="schedule: {mode: watch}\n")
    runner.invoke(app, ["schedule", "-c", str(config), "--no-check"])
    photo = put(downloads, "photo.jpeg", b"jpeg")
    put(downloads, "bill.pdf", b"bill")
    fake_protection.protect(monkeypatch, photo)
    fake_protection.lock_screen()

    def wait(*_args: object) -> catchup.Waited:
        pytest.fail("the fallback start files them: no wait in a watch run")

    monkeypatch.setattr(catchup, "wait_for_unlock", wait)
    stdout = triggered(config)
    assert "next start once the Mac is unlocked (at most 30 min later)" in stdout


def test_an_unloaded_daily_agent_does_not_count(
    downloads: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_protection: FakeProtection,
    fake_launchd: Path,
) -> None:
    extra = "schedule: {mode: both, watch_fallback_minutes: 0}\n"
    config = write_config(tmp_path, downloads, extra=extra)
    runner.invoke(app, ["schedule", "-c", str(config), "--no-check"])
    assert scheduler.daily_agent_loaded()
    fake_launchd.write_text(json.dumps({}))  # launchd lost them (a reboot gone wrong)
    assert not scheduler.daily_agent_loaded()
    photo = put(downloads, "photo.jpeg", b"jpeg")
    put(downloads, "bill.pdf", b"bill")
    fake_protection.protect(monkeypatch, photo)
    fake_protection.lock_screen()
    waited: list[bool] = []

    def wait(*_args: object) -> catchup.Waited:
        waited.append(True)
        return catchup.Waited.TIMED_OUT

    monkeypatch.setattr(catchup, "wait_for_unlock", wait)
    triggered(config)
    assert waited == [True]  # nobody else will: this run waits itself


def test_unpriced_files_count_even_when_the_provider_stops_the_run(downloads: Path) -> None:
    cfg = fake_config(downloads, provider=UNPRICED)
    for name in ("a.txt", "b.txt"):
        put(downloads, name, name.encode())

    class Refused(FakeProvider):
        prices_known = False

        def classify(self, context: Any, rules: Any) -> Classification:
            self.last_usage = Usage(input_tokens=1)
            raise FatalProviderError("the key was revoked")

    with pytest.raises(FatalProviderError):
        pipeline.run(cfg, Refused(), simulate=False, trigger=RunTrigger.SCHEDULE)
    with Store.open(cfg.database) as store:
        assert store.automatic_files_today() == 2
        [run] = store.conn.execute("SELECT cost_usd FROM runs").fetchall()
        assert run[0] is None


def test_the_run_itself_keeps_the_watch_quiet_time(downloads: Path) -> None:
    cfg = fake_config(downloads)
    put(downloads, "recent.pdf", b"x", mtime=time.time() - 100)  # > 60 s, < 300 s
    provider = FakeProvider(default=answer())
    watched = pipeline.run(cfg, provider, simulate=False, trigger=RunTrigger.WATCH)
    assert provider.calls == [] and watched.scan.candidates == []
    pipeline.run(cfg, provider, simulate=False, trigger=RunTrigger.SCHEDULE)
    assert [c.path.name for c in provider.calls] == ["recent.pdf"]


def test_a_manual_run_never_waits_for_the_lock(
    downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write_config(tmp_path, downloads)
    cfg = load_config(config)
    put(downloads, "bill.pdf", b"bill")
    monkeypatch.setattr("file_librarian.cli.LOCK_WAIT_SECONDS", 600.0)
    started = time.monotonic()
    with instance_lock(cfg.database):
        result = runner.invoke(app, ["run", "-c", str(config)])
    assert result.exit_code == 1 and time.monotonic() - started < 5


def test_automatic_files_today_counts_only_today(downloads: Path) -> None:
    cfg = fake_config(downloads)
    yesterday = (datetime.now(UTC) - timedelta(days=1, hours=1)).isoformat(timespec="seconds")
    spend(cfg, None, files_seen=7, started=yesterday)
    spend(cfg, None, files_seen=3)
    spend(cfg, None, "manual", files_seen=50)
    with Store.open(cfg.database) as store:
        assert store.automatic_files_today() == 3


def test_failures_left_the_same_day_never_block_a_new_arrival(downloads: Path) -> None:
    cfg = fake_config(downloads, limits={"max_files_per_run": 2})
    for i, name in enumerate(["a.txt", "b.txt", "c.txt", "d.txt"]):
        put(downloads, name, name.encode(), mtime=OLD + i)
    provider = FakeProvider({"new-invoice.txt": answer()})  # everything else fails
    clock = Clock()
    watch.watch(cfg, organizer(cfg, provider), clock=clock, sleep=clock.sleep)
    put(downloads, "new-invoice.txt", b"fresh", mtime=time.time() - 3600)
    watch.watch(cfg, organizer(cfg, provider), clock=clock, sleep=clock.sleep)
    assert not (downloads / "new-invoice.txt").exists()


def test_the_watch_settle_loop_stops_at_max_runs(downloads: Path) -> None:
    cfg = fake_config(downloads)
    counter = iter(range(1000))

    def always_new() -> watch.Probe:
        return watch.Probe(1, 0, frozenset({str(next(counter))}))

    ran = Counted()
    ended = watch.watch(cfg, ran, look=always_new, clock=Clock(), sleep=lambda _s: None)
    assert ran.calls == watch.MAX_RUNS and ended.runs == watch.MAX_RUNS


def test_yesterdays_spend_does_not_count_today(downloads: Path) -> None:
    cfg = fake_config(downloads, limits={"max_cost_usd_per_day": 1.0})
    yesterday = (datetime.now(UTC) - timedelta(days=1, hours=1)).isoformat(timespec="seconds")
    spend(cfg, 5.0, started=yesterday)
    put(downloads, "a.txt", b"a")
    provider = Priced(default=answer())
    outcome = pipeline.run(cfg, provider, simulate=False, trigger=RunTrigger.WATCH)
    assert len(provider.calls) == 1 and not outcome.day_cap_reached


UNPRICED = {"name": "openai-compat", "model": "m", "base_url": "https://llm.example.invalid/v1"}


def test_a_provider_without_prices_is_limited_by_files_a_day(downloads: Path) -> None:
    cfg = fake_config(downloads, provider=UNPRICED, limits={"max_files_per_day": 3})
    assert cfg.provider.cost_unknown
    spend(cfg, None, files_seen=2)
    for name in ("a.txt", "b.txt", "c.txt"):
        put(downloads, name, name.encode())
    provider = FakeProvider(default=answer())
    outcome = pipeline.run(cfg, provider, simulate=False, trigger=RunTrigger.SCHEDULE)
    assert len(provider.calls) == 1 and outcome.day_cap_reached
    again = pipeline.run(cfg, provider, simulate=False, trigger=RunTrigger.WATCH)
    assert len(provider.calls) == 1 and again.day_cap_reached
    by_hand = pipeline.run(cfg, provider, simulate=False, trigger=RunTrigger.MANUAL)
    assert len(provider.calls) == 3 and not by_hand.day_cap_reached
    assert fake_config(downloads, provider=UNPRICED).limits.files_cap_per_day == 1000
    local = UNPRICED | {"base_url": "http://127.0.0.1:8080/v1"}
    assert not fake_config(downloads, provider=local).provider.cost_unknown
    priced = UNPRICED | {"price_input_per_mtok": 1}
    assert not fake_config(downloads, provider=priced).provider.cost_unknown


def test_unknown_costs_are_said_everywhere(
    downloads: Path, tmp_path: Path, fake_launchd: Path
) -> None:
    provider = json.dumps(UNPRICED)
    config = write_config(tmp_path, downloads, provider, "schedule: {mode: watch}\n")
    scheduled = runner.invoke(app, ["schedule", "-c", str(config), "--no-check"])
    assert "no cost cap can stop it" in scheduled.stderr
    assert "1000 files a day" in scheduled.stderr
    checked = runner.invoke(app, ["check-config", "-c", str(config)])
    assert "daily cost cap for automatic runs: US$ 3.00" in checked.stdout
    assert "no cost cap can stop it" in checked.stderr
    doctor = runner.invoke(app, ["doctor", "-c", str(config), "--stdout"]).stdout
    assert "daily cost cap for automatic runs" in doctor


def test_status_says_cost_unknown(downloads: Path, tmp_path: Path, fake_launchd: Path) -> None:
    config = write_config(tmp_path, downloads, json.dumps(UNPRICED), "schedule: {mode: both}\n")
    runner.invoke(app, ["schedule", "-c", str(config), "--no-check"])
    spend(load_config(config), None, files_seen=4)
    shown = runner.invoke(app, ["status", "-c", str(config)]).stdout
    assert "Automatic runs today: cost unknown (no prices for openai-compat); 4 of 1000" in shown


def test_spend_before_a_fatal_provider_error_counts(downloads: Path) -> None:
    cfg = fake_config(downloads)
    for name in ("a.txt", "b.txt"):
        put(downloads, name, name.encode())

    class Expires(Priced):
        def classify(self, context: Any, rules: Any) -> Classification:
            if self.calls:
                self.last_usage = Usage(input_tokens=1)  # billed, then refused
                self.calls.append(context)
                raise FatalProviderError("the key was revoked")
            return super().classify(context, rules)

    with pytest.raises(FatalProviderError):
        pipeline.run(cfg, Expires(default=answer()), simulate=False, trigger=RunTrigger.WATCH)
    with Store.open(cfg.database) as store:
        assert store.automatic_cost_today() == pytest.approx(0.8)
        [run] = store.conn.execute("SELECT trigger, files_moved FROM runs").fetchall()
        assert tuple(run) == ("watch", 0)


def test_a_watch_run_leaves_the_unlock_wait_to_the_daily_agent(
    downloads: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_protection: FakeProtection,
    fake_launchd: Path,
) -> None:
    extra = "schedule: {mode: both, watch_fallback_minutes: 0}\n"
    config = write_config(tmp_path, downloads, extra=extra)
    runner.invoke(app, ["schedule", "-c", str(config), "--no-check"])
    photo = put(downloads, "photo.jpeg", b"jpeg")
    put(downloads, "bill.pdf", b"bill")
    fake_protection.protect(monkeypatch, photo)
    fake_protection.lock_screen()

    def wait(*_args: object) -> catchup.Waited:
        pytest.fail("a watch run must not wait for the unlock when the daily agent will")

    monkeypatch.setattr(catchup, "wait_for_unlock", wait)
    stdout = triggered(config)
    assert "left for the daily run, which waits for the unlock" in stdout
    assert "Waiting up to" not in stdout


def test_watch_logs_are_kept_apart_and_output_logs_trimmed(
    downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write_config(tmp_path, downloads)
    cfg = load_config(config)
    folder = runlog.logs_dir(cfg.database)
    folder.mkdir(parents=True, exist_ok=True)
    big = folder / "watch.out.log"
    big.write_bytes(b"old line with a name\n" * 100_000)
    monkeypatch.setattr(runlog, "KEEP_WATCH", 2)
    runlog.write_log(cfg.database, 1, ["daily"])
    for i in range(2, 6):
        runlog.write_log(cfg.database, i, ["watch"], watch=True)
    names = sorted(p.name for p in folder.glob("*run-*.log"))
    assert names == ["run-000001.log", "watch-run-000004.log", "watch-run-000005.log"]
    assert runlog.log_path(cfg.database, 5).name == "watch-run-000005.log"
    triggered(config)
    assert big.stat().st_size <= runlog.OUTPUT_LOG_MAX_BYTES
    assert big.read_bytes().startswith(b"old line")  # whole lines only
    assert runlog.trim(folder / "missing.log") is False


def test_stale_watch_paths_are_reported(
    downloads: Path, tmp_path: Path, fake_launchd: Path
) -> None:
    config = write_config(tmp_path, downloads, extra="schedule: {mode: watch}\n")
    runner.invoke(app, ["schedule", "-c", str(config), "--no-check"])
    assert "--refresh" not in runner.invoke(app, ["schedule", "--status"]).stderr
    other = downloads.parent / "Inbox"
    other.mkdir()
    text = config.read_text(encoding="utf-8")
    config.write_text(
        text.replace(
            f"inputs: [{json.dumps(str(downloads))}]", f"inputs: [{json.dumps(str(other))}]"
        ),
        encoding="utf-8",
    )
    stale = "watches other folders than the configuration's inputs"
    assert stale in runner.invoke(app, ["schedule", "--status"]).stderr
    assert stale in runner.invoke(app, ["status", "-c", str(config)]).stdout
    assert stale in runner.invoke(app, ["check-config", "-c", str(config)]).stderr
    runner.invoke(app, ["schedule", "--refresh"])
    assert stale not in runner.invoke(app, ["schedule", "--status"]).stderr


def test_at_alone_keeps_the_installed_mode(
    downloads: Path, tmp_path: Path, fake_launchd: Path
) -> None:
    config = write_config(tmp_path, downloads)  # schedule.mode left out: daily
    runner.invoke(app, ["schedule", "-c", str(config), "--mode", "both", "--no-check"])
    result = runner.invoke(app, ["schedule", "-c", str(config), "--at", "07:00", "--no-check"])
    assert "Daily run scheduled at 07:00" in result.stdout and "Watch mode on" in result.stdout
    assert scheduler.status().mode == "both"
    explicit = write_config(tmp_path, downloads, extra="schedule: {mode: daily}\n")
    runner.invoke(app, ["schedule", "-c", str(explicit), "--no-check"])
    assert scheduler.status().mode == "daily"  # a mode in the configuration wins


def test_watch_only_mode_says_what_needs_the_daily_run(
    downloads: Path, tmp_path: Path, fake_launchd: Path
) -> None:
    config = write_config(tmp_path, downloads)
    result = runner.invoke(app, ["schedule", "-c", str(config), "--mode", "watch"])
    assert "due reminders come only after the daily run" in result.stdout
    assert "--mode both" in result.stdout
    assert "Access OK: the scheduled runs can read every input folder." in result.stdout
    refreshed = runner.invoke(app, ["schedule", "--remove"])
    assert "Schedule removed" in refreshed.stdout
    assert "Nothing is scheduled; nothing to refresh." in (
        runner.invoke(app, ["schedule", "--refresh"]).stdout
    )


@pytest.fixture
def local_zone(monkeypatch: pytest.MonkeyPatch) -> Iterator[Callable[[str], None]]:
    """Switch this process's local time zone for the test (and back afterwards)."""

    def use(zone: str) -> None:
        monkeypatch.setenv("TZ", zone)
        time.tzset()

    yield use
    monkeypatch.delenv("TZ", raising=False)
    time.tzset()


@pytest.mark.parametrize(
    ("zone", "day", "offset_hours"),
    [
        ("America/New_York", (2026, 3, 8), 5),  # DST starts at 2:00: midnight is EST
        ("America/New_York", (2026, 11, 1), 4),  # DST ends at 2:00: midnight is EDT
        ("America/Sao_Paulo", (2026, 3, 8), 3),
    ],
)
def test_days_and_months_start_at_local_midnight_on_dst_days(
    local_zone: Callable[[str], None], zone: str, day: tuple[int, int, int], offset_hours: int
) -> None:
    local_zone(zone)
    noon = datetime(*day, 12, 0)  # naive: local time in the zone just set
    start = datetime.fromisoformat(day_start_utc(noon))
    assert start == datetime(*day, offset_hours, 0, tzinfo=UTC)


def test_the_month_starts_at_local_midnight_across_dst(
    local_zone: Callable[[str], None], monkeypatch: pytest.MonkeyPatch
) -> None:
    local_zone("America/New_York")

    class March(datetime):
        @classmethod
        def now(cls, tz: Any = None) -> March:
            return cls(2026, 3, 20, 12, 0, tzinfo=tz)

    monkeypatch.setattr(store_mod, "datetime", March)
    assert datetime.fromisoformat(store_mod._month_start()) == datetime(2026, 3, 1, 5, tzinfo=UTC)


def _dump(database: Path) -> dict[str, list[tuple[object, ...]]]:
    conn = sqlite3.connect(database)
    try:
        tables = [
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
                " AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%fts%'"
            )
        ]
        out = {
            table: sorted(
                (tuple(r) for r in conn.execute(f"SELECT * FROM {table}")),  # noqa: S608
                key=repr,
            )
            for table in tables
        }
        out["_schema"] = sorted(
            tuple(r)
            for r in conn.execute(
                "SELECT type, name, tbl_name FROM sqlite_master"
                " WHERE type IN ('index', 'trigger', 'view')"
            )
        )
    finally:
        conn.close()
    return out


def _code_before_0019(monkeypatch: pytest.MonkeyPatch) -> None:
    """Corrections as the code wrote and read them before 0019 (no ``operation_id``)."""

    def add_correction(
        self: Store, file_id: int, field: str, old: str | None, new: str, **_: object
    ) -> None:
        self.conn.execute(
            "INSERT INTO corrections (file_id, field, old_value, new_value, created_at)"
            " VALUES (?, ?, ?, ?, '2026-01-01T00:00:00+00:00')",
            (file_id, field, old, new),
        )

    def current_corrections(self: Store) -> list[CorrectionRow]:
        rows = self.conn.execute(
            "SELECT c.field, c.old_value, c.new_value, f.extension, f.is_unit, f.description,"
            " f.original_name, f.current_name, NULL AS run_id"
            " FROM corrections c JOIN files f ON f.id = c.file_id"
        )
        return [CorrectionRow.from_row(r) for r in rows]

    monkeypatch.setattr(Store, "add_correction", add_correction)
    monkeypatch.setattr(Store, "current_corrections", current_corrections)
    monkeypatch.setattr(Store, "drop_corrections", lambda self, operation_id: 0)


def test_migration_0018_keeps_a_lived_in_index_unchanged(
    downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Runs of every trigger, moves, duplicates, a rename, an undo and an unfinished run:
    after 0018 every table holds exactly what it held, and the references still check."""
    cats = "categories: [{name: Finance}, {name: Other}, {name: Docs}]\n"
    path = write_config(tmp_path, downloads, extra=cats)
    cfg = load_config(path)
    before = [m for m in store_mod.load_migrations() if m.version < 18]
    upto = [m for m in store_mod.load_migrations() if m.version <= 18]
    _code_before_0019(monkeypatch)  # the code this index was written with
    with monkeypatch.context() as patch:
        patch.setattr("file_librarian.db.store.load_migrations", lambda: before)
        put(downloads, "a.txt", b"aaa")
        put(downloads, "b.txt", b"bbb")
        put(downloads, "c.txt", b"fail")
        provider = FakeProvider({"a.txt": answer("Docs"), "b.txt": answer("Other")})
        pipeline.run(cfg, provider, simulate=False)
        put(downloads, "a copy.txt", b"aaa")
        put(downloads, "d.txt", b"ddd")
        pipeline.run(
            cfg, FakeProvider({"d.txt": answer("Docs")}), simulate=False,
            trigger=RunTrigger.SCHEDULE,
        )  # fmt: skip
        put(downloads, "e.txt", b"eee")
        pipeline.run(
            cfg, FakeProvider({"e.txt": answer("Finance")}), simulate=False,
            trigger=RunTrigger.MCP,
        )  # fmt: skip
        renamed = write_config(
            tmp_path, downloads,
            extra="categories: [{name: Finance}, {name: Other}, {name: Documents}]\n",
        )  # fmt: skip
        result = runner.invoke(
            app, ["reprocess", "-c", str(renamed), "--rename-category", "Docs", "Documents",
                  "--yes"],
        )  # fmt: skip
        assert result.exit_code == 0, result.stderr
        assert runner.invoke(app, ["undo", "-c", str(renamed)]).exit_code == 0
        with Store.open(cfg.database) as store:
            assert store.schema_version == 17
            store.start_run(RunMode.RUN, RunTrigger.SCHEDULE)  # crashed, never finished
        snapshot = _dump(cfg.database)
    monkeypatch.setattr("file_librarian.db.store.load_migrations", lambda: upto)
    with Store.open(cfg.database) as store:
        assert store.schema_version == 18
        assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert store.conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert _dump(cfg.database) == snapshot
    assert {r[2] for r in snapshot["runs"]} >= {"manual", "schedule", "mcp", "reprocess"}
    assert snapshot["operations"]

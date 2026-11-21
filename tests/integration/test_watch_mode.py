"""Watch mode (#90): runs when something arrives, without loops, bursts or partial files.

launchd is never involved: each test starts what the watch agent starts
(``flib run --trigger watch``, or ``scheduler.watch.watch`` with a fake clock
and sleep), as many times as launchd would.
"""

from __future__ import annotations

import os
import signal
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_librarian import catchup, pipeline
from file_librarian import status as status_mod
from file_librarian.cli import app
from file_librarian.config import Config, load_config
from file_librarian.db.store import Store
from file_librarian.lock import instance_lock
from file_librarian.models import Classification, RunTrigger
from file_librarian.providers.base import Usage
from file_librarian.providers.fake import FakeProvider
from file_librarian.scheduler import watch
from tests._helpers import FakeProtection, answer, fake_config, library, put, rows, write_config

pytestmark = pytest.mark.integration

runner = CliRunner()


class Clock:
    """A fake clock and sleep: each sleep moves the clock, then runs ``on_sleep``."""

    def __init__(self, on_sleep: Callable[[int], object] | None = None) -> None:
        self.now = 1_000_000.0
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

    def __call__(self) -> bool:
        self.calls += 1
        return True


def triggered(config: Path) -> str:
    """What one start of the watch agent prints; it must succeed."""
    result = runner.invoke(app, ["run", "-c", str(config), "--trigger", "watch"])
    assert result.exit_code == 0, result.stdout + result.stderr
    return result.stdout


def triggers(cfg: Config) -> list[str]:
    return [row["trigger"] for row in rows(cfg, "SELECT trigger FROM runs ORDER BY id")]


def organizer(cfg: Config, provider: FakeProvider) -> Callable[[], bool]:
    """``run_once`` as the CLI gives it: one real watch run."""

    def run_once() -> bool:
        pipeline.run(cfg, provider, simulate=False, trigger=RunTrigger.WATCH)
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
    cfg = fake_config(downloads, limits={"stable_seconds": 0})
    put(downloads, "ready.txt", b"ready")
    partial = put(downloads, "report.pdf.crdownload", b"half", mtime=time.time())

    def finish(sleeps: int) -> None:
        if sleeps == 2:  # the browser finishes and renames it
            partial.rename(downloads / "report.pdf")

    clock = Clock(finish)
    provider = FakeProvider(default=answer())
    runs: list[int] = []

    def run_once() -> bool:
        runs.append(len(clock.sleeps))
        return organizer(cfg, provider)()

    ended = watch.watch(cfg, run_once, clock=clock, sleep=clock.sleep)
    assert ended.ended is watch.Ended.RAN and ended.runs == 1
    assert runs == [2]  # one run, once the download was done: never before
    assert clock.sleeps == [watch.POLL_SECONDS] * 2
    assert sorted(c.path.name for c in provider.calls) == ["ready.txt", "report.pdf"]


def test_a_file_still_changing_is_waited_for(downloads: Path) -> None:
    cfg = fake_config(downloads)  # stable_seconds: 60
    now = time.time()
    put(downloads, "copying.mov", b"frames", mtime=now)
    clock = Clock()
    found = watch.probe(cfg, now=now)
    assert (found.new, found.pending) == (0, 1)
    assert watch.probe(cfg, now=now + 61).new == 1  # a minute later it is ready
    assert clock.sleeps == []


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
        ended = watch.watch(cfg, lambda: True, look=look)
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
    ended = watch.watch(cfg, lambda: True, look=lambda: pending, sleep=sleep)
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
    assert "1 item(s) were still downloading or changing after 0 min" in stdout
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

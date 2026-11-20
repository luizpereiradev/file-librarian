"""Files readable only while the Mac is unlocked (Data Protection class A or B).

A scheduled run that starts while the Mac is locked skips them as ``locked``
(never ``unreadable``, never a failed attempt), waits for the next unlock with
no instance lock held, and runs once more. In the library, such a file that
cannot be read is "can't tell": never changed, never missing.

The screen lock and the classes are the ``fake_protection`` fixture's; the
wait runs on a fake clock.
"""

from __future__ import annotations

import signal
import subprocess
import sys
from collections.abc import Callable
from contextlib import ExitStack
from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_librarian import catchup, pipeline
from file_librarian.cli import ACCESS_HINT, app
from file_librarian.config import Config, load_config
from file_librarian.db.store import Store
from file_librarian.executor import recover
from file_librarian.lock import instance_lock
from file_librarian.models import FileStatus
from file_librarian.protection import LOCKED_NOW
from file_librarian.providers.fake import FakeProvider
from file_librarian.reconcile import reconcile
from file_librarian.reprocess import select
from file_librarian.runlog import logs_dir
from file_librarian.undo import undo_file
from tests._helpers import (
    FakeProtection,
    fake_config,
    library,
    organize,
    put,
    row_for,
    rows,
    write_config,
)

pytestmark = pytest.mark.integration

runner = CliRunner()
LOCKED_LINE = "  skip    photo.jpeg  (protected by macOS while the Mac is locked)"


class FakeWait:
    """``catchup.wait_for_unlock`` on a fake clock; records the sleeps."""

    def __init__(
        self, monkeypatch: pytest.MonkeyPatch, on_sleep: Callable[[], object] | None = None
    ) -> None:
        self.sleeps: list[float] = []
        self.now = 0.0
        self.calls = 0
        self._on_sleep = on_sleep
        real = catchup.wait_for_unlock

        def wait(hours: float, ready: Callable[[], bool]) -> catchup.Waited:
            self.calls += 1
            return real(hours, ready, clock=lambda: self.now, sleep=self.sleep)

        monkeypatch.setattr(catchup, "wait_for_unlock", wait)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds
        if self._on_sleep is not None:
            self._on_sleep()


@pytest.fixture
def locked_photo(
    downloads: Path, monkeypatch: pytest.MonkeyPatch, fake_protection: FakeProtection
) -> Path:
    """A class A photo in Downloads, and the Mac locked."""
    photo = put(downloads, "photo.jpeg", b"jpeg bytes")
    fake_protection.protect(monkeypatch, photo)
    fake_protection.lock_screen()
    return photo


def scheduled(config: Path) -> str:
    """The output of a scheduled run, which must succeed."""
    result = runner.invoke(app, ["run", "-c", str(config), "--trigger", "schedule"])
    assert result.exit_code == 0, result.stdout + result.stderr
    return result.stdout


def run_logs(cfg: Config) -> list[list[str]]:
    return [
        p.read_text(encoding="utf-8").splitlines()
        for p in sorted(logs_dir(cfg.database).glob("run-*.log"))
    ]


def schedule_runs(cfg: Config) -> list[str]:
    return [row["trigger"] for row in rows(cfg, "SELECT trigger FROM runs ORDER BY id")]


def test_a_scheduled_run_waits_for_the_unlock_and_files_it(
    downloads: Path,
    tmp_path: Path,
    locked_photo: Path,
    fake_protection: FakeProtection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = write_config(tmp_path, downloads)
    cfg = load_config(config)
    put(downloads, "bill.pdf", b"bill")
    wait = FakeWait(monkeypatch)
    fake_protection.lock_screen(unlock_after=2)

    stdout = scheduled(config)

    assert LOCKED_LINE in stdout.splitlines()
    assert ACCESS_HINT not in stdout  # not a Full Disk Access problem
    assert "Waiting up to 8 h for the Mac to be unlocked (checking every minute)." in stdout
    assert "The Mac was unlocked; running once more" in stdout
    assert wait.sleeps == [catchup.POLL_SECONDS] * 2
    assert not locked_photo.exists()
    assert row_for(cfg, "photo.jpeg").status is FileStatus.ORGANIZED
    assert row_for(cfg, "photo.jpeg").attempts == 0
    assert schedule_runs(cfg) == ["schedule", "schedule"]
    first, second = run_logs(cfg)
    assert LOCKED_LINE in first
    assert (
        "Waiting up to 8 h for the Mac to be unlocked to read 1 file(s) protected while it is "
        "locked; then it runs once more."
    ) in first
    assert any(line.startswith("The Mac was unlocked") for line in first)
    assert "Catch-up run, after the Mac was unlocked." in second
    assert not any(line.startswith("Waiting up to") for line in second)  # never waits twice


def test_it_gives_up_at_the_limit(
    downloads: Path, tmp_path: Path, locked_photo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write_config(tmp_path, downloads, extra="schedule: {wait_for_unlock_hours: 2}\n")
    cfg = load_config(config)
    wait = FakeWait(monkeypatch)

    stdout = scheduled(config)

    assert sum(wait.sleeps) == 2 * 3600
    assert "The Mac was not unlocked within 2 h" in stdout
    assert locked_photo.exists()
    assert schedule_runs(cfg) == ["schedule"]
    [log] = run_logs(cfg)
    assert log[-1].startswith("The Mac was not unlocked within 2 h")
    with Store.open(cfg.database) as store:
        assert [r for r in store.all_files() if r.original_name == "photo.jpeg"] == []


def test_zero_hours_means_no_wait(
    downloads: Path, tmp_path: Path, locked_photo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write_config(tmp_path, downloads, extra="schedule: {wait_for_unlock_hours: 0}\n")
    wait = FakeWait(monkeypatch)
    stdout = scheduled(config)
    assert LOCKED_LINE in stdout.splitlines()
    assert wait.calls == 0 and "Waiting" not in stdout


def test_a_manual_run_never_waits(
    downloads: Path, tmp_path: Path, locked_photo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write_config(tmp_path, downloads)
    wait = FakeWait(monkeypatch)
    result = runner.invoke(app, ["run", "-c", str(config)])
    assert result.exit_code == 0, result.stderr
    assert LOCKED_LINE in result.stdout.splitlines()
    assert wait.calls == 0


def test_sigterm_while_waiting_ends_cleanly(
    downloads: Path, tmp_path: Path, locked_photo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write_config(tmp_path, downloads)
    cfg = load_config(config)
    FakeWait(monkeypatch, on_sleep=lambda: signal.raise_signal(signal.SIGTERM))
    stdout = scheduled(config)
    assert "Stopped while waiting for an unlock (SIGTERM)" in stdout
    assert schedule_runs(cfg) == ["schedule"]


def test_no_instance_lock_is_held_while_waiting_and_a_busy_wake_just_exits(
    downloads: Path,
    tmp_path: Path,
    locked_photo: Path,
    fake_protection: FakeProtection,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = write_config(tmp_path, downloads)
    cfg = load_config(config)
    fake_protection.lock_screen(unlock_after=1)
    with ExitStack() as other_run:
        # While it waits, a run by hand takes the instance lock (so it was free)...
        FakeWait(monkeypatch, on_sleep=lambda: other_run.enter_context(instance_lock(cfg.database)))
        stdout = scheduled(config)
        # ...and is still going on when the Mac is unlocked: the catch-up leaves it to that run.
    assert "Not running again after the unlock: another flib process" in stdout
    assert schedule_runs(cfg) == ["schedule"]


def test_a_scheduled_start_stops_an_earlier_waiter(
    downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = write_config(tmp_path, downloads)
    cfg = load_config(config)
    lock = catchup.waiter_lock_path(cfg.database)
    lock.parent.mkdir(parents=True, exist_ok=True)
    holder = subprocess.Popen(  # noqa: S603 - this Python, a fixed script
        [
            sys.executable,
            "-c",
            "import fcntl, os, sys, time\n"
            f"fd = os.open({str(lock)!r}, os.O_RDWR | os.O_CREAT, 0o600)\n"
            "fcntl.flock(fd, fcntl.LOCK_EX)\n"
            "os.pwrite(fd, str(os.getpid()).encode(), 0)\n"
            "print('ready', flush=True)\n"
            "time.sleep(60)\n",
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None and holder.stdout.readline().strip() == "ready"
        stdout = scheduled(config)
        assert "Stopped an earlier scheduled run's wait for an unlock" in stdout
        assert holder.wait(timeout=10) == -signal.SIGTERM
    finally:
        holder.kill()
        holder.wait()


def test_an_ordinary_denied_file_still_gets_the_full_disk_access_hint(
    downloads: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_protection: FakeProtection,
) -> None:
    photo = put(downloads, "photo.jpeg", b"jpeg bytes")
    fake_protection.protect(monkeypatch, photo, cls=3)  # class C: the lock is not why
    fake_protection.lock_screen()
    config = write_config(tmp_path, downloads)
    real_open = Path.open

    def denied(path: Path, *args: object, **kwargs: object) -> object:
        if path.name == "photo.jpeg":
            raise PermissionError(1, "Operation not permitted", str(path))
        return real_open(path, *args, **kwargs)  # type: ignore[call-overload]

    monkeypatch.setattr(Path, "open", denied)
    wait = FakeWait(monkeypatch)
    stdout = scheduled(config)
    assert "  skip    photo.jpeg  (unreadable: EPERM Operation not permitted, on read)" in (
        stdout.splitlines()
    )
    assert ACCESS_HINT in stdout
    assert wait.calls == 0


# --- the library side --------------------------------------------------------------------


def lock_library_file(
    path: Path, monkeypatch: pytest.MonkeyPatch, fake_protection: FakeProtection
) -> None:
    fake_protection.protect(monkeypatch, path)
    fake_protection.lock_screen()


def test_reconcile_never_calls_a_locked_library_file_missing(
    downloads: Path,
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
    fake_protection: FakeProtection,
) -> None:
    organize(downloads, cfg, {"photo.jpeg": (b"jpeg bytes", "Other")})
    filed = library(downloads) / "Other" / "photo.jpeg"
    lock_library_file(filed, monkeypatch, fake_protection)
    with Store.open(cfg.database) as store:
        report = reconcile(cfg, store)
    assert report.locked == [filed]
    assert not (report.missing or report.moved)
    row = row_for(cfg, "photo.jpeg")
    assert (row.status, row.current_path) == (FileStatus.ORGANIZED, filed)

    result = runner.invoke(app, ["reconcile", "-c", str(write_config(downloads.parent, downloads))])
    assert result.exit_code == 0, result.stderr
    line = "  locked  Other/photo.jpeg  (protected by macOS while the Mac is locked, not checked)"
    assert line in result.stdout.splitlines()
    assert "run `flib reconcile` again once it is unlocked" in result.stdout


def test_reconcile_leaves_a_locked_duplicate_as_it_is(
    downloads: Path, monkeypatch: pytest.MonkeyPatch, fake_protection: FakeProtection
) -> None:
    cfg = fake_config(downloads, duplicates="move")
    organize(downloads, cfg, {"photo.jpeg": (b"jpeg bytes", "Other")})
    put(downloads, "photo copy.jpeg", b"jpeg bytes")
    pipeline.run(cfg, FakeProvider({}), simulate=False)
    dup = row_for(cfg, "photo copy.jpeg")
    assert dup.status is FileStatus.DUPLICATE
    lock_library_file(dup.current_path, monkeypatch, fake_protection)
    with Store.open(cfg.database) as store:
        report = reconcile(cfg, store)
    assert report.locked == [dup.current_path]
    assert row_for(cfg, "photo copy.jpeg").status is FileStatus.DUPLICATE


def test_a_copy_of_a_locked_library_file_waits_and_is_no_failed_attempt(
    downloads: Path, monkeypatch: pytest.MonkeyPatch, fake_protection: FakeProtection
) -> None:
    cfg = fake_config(downloads, duplicates="move")
    organize(downloads, cfg, {"photo.jpeg": (b"jpeg bytes", "Other")})
    filed = library(downloads) / "Other" / "photo.jpeg"
    copy = put(downloads, "photo copy.jpeg", b"jpeg bytes")
    lock_library_file(filed, monkeypatch, fake_protection)
    outcome = pipeline.run(cfg, FakeProvider({}), simulate=False)
    assert (copy, "protected by macOS while the Mac is locked") in [
        (p, r.value) for p, r in outcome.scan.skipped
    ]
    assert copy.exists()
    assert row_for(cfg, "photo.jpeg").attempts == 0

    fake_protection.locked = False
    pipeline.run(cfg, FakeProvider({}), simulate=False)
    assert row_for(cfg, "photo copy.jpeg").status is FileStatus.DUPLICATE


def test_undo_of_a_locked_file_waits(
    downloads: Path,
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
    fake_protection: FakeProtection,
) -> None:
    organize(downloads, cfg, {"photo.jpeg": (b"jpeg bytes", "Other")})
    filed = library(downloads) / "Other" / "photo.jpeg"
    lock_library_file(filed, monkeypatch, fake_protection)
    with Store.open(cfg.database) as store:
        report = undo_file(store, filed, config=cfg)
    assert report.skipped == [(filed, LOCKED_NOW)]
    assert filed.exists()


def test_reprocess_of_a_locked_file_says_why(
    downloads: Path,
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
    fake_protection: FakeProtection,
) -> None:
    organize(downloads, cfg, {"photo.jpeg": (b"jpeg bytes", "Other")})
    filed = library(downloads) / "Other" / "photo.jpeg"
    lock_library_file(filed, monkeypatch, fake_protection)
    with Store.open(cfg.database) as store:
        selection = select(cfg, store)
    assert selection.left == [(filed, LOCKED_NOW)]


def test_fix_of_a_locked_file_says_why(
    downloads: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fake_protection: FakeProtection,
) -> None:
    config = write_config(tmp_path, downloads)
    cfg = load_config(config)
    organize(downloads, cfg, {"photo.jpeg": (b"jpeg bytes", "Other")})
    filed = library(downloads) / "Other" / "photo.jpeg"
    lock_library_file(filed, monkeypatch, fake_protection)
    result = runner.invoke(app, ["fix", str(filed), "--name", "new.jpeg", "-c", str(config)])
    assert result.exit_code != 0
    assert LOCKED_NOW in result.stderr


def test_recovery_leaves_a_move_it_cannot_check_for_later(
    downloads: Path,
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
    fake_protection: FakeProtection,
) -> None:
    organize(downloads, cfg, {"photo.jpeg": (b"jpeg bytes", "Other")})
    filed = library(downloads) / "Other" / "photo.jpeg"
    with Store.open(cfg.database) as store:
        # As if a crash had cut the move off after the rename, and the file was replaced
        # by a copy (another inode): only its content can tell.
        store.conn.execute("UPDATE operations SET status = 'planned', src_dev = 0, src_ino = 0")
    lock_library_file(filed, monkeypatch, fake_protection)
    with Store.open(cfg.database) as store:
        assert recover(store, cfg) == 0
        [status] = [r["status"] for r in store.conn.execute("SELECT status FROM operations")]
    assert status == "planned"
    assert row_for(cfg, "photo.jpeg").attempts == 0

    fake_protection.locked = False
    with Store.open(cfg.database) as store:
        assert recover(store, cfg) == 1

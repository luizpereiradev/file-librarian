"""The menu bar item end to end, with fakes (#116): ``flib menu`` installs it, every
place where flib's state changes writes the state file and nudges the applet,
``Organize now`` runs exactly what the schedule runs, and ``flib status`` and
``flib doctor`` say how it is."""

from __future__ import annotations

import dataclasses
import json
import logging
import os
import shutil
import stat
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from file_librarian import lock, menubar, menubar_state, notifier, pipeline, poses, scheduler
from file_librarian import status as st
from file_librarian.cli import app
from file_librarian.config import Config, load_config
from file_librarian.doctor import Options, build_report
from file_librarian.due import DueCounts
from file_librarian.lock import instance_lock, ui_lock
from file_librarian.providers.fake import FakeProvider
from file_librarian.ui import server as ui_server
from tests._helpers import answer, put, write_config

pytestmark = pytest.mark.integration

runner = CliRunner()
ELLIPSIS = chr(0x2026)


def calls(log: Path) -> list[list[Any]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def shown() -> dict[str, Any]:
    raw = menubar.state_path().read_bytes()
    data: dict[str, Any] = json.loads(raw)
    assert menubar.sanitize(raw) == data, "flib wrote what the applet would refuse"
    return data


def loaded(fake_launchd: Path) -> dict[str, str]:
    data: dict[str, str] = json.loads(fake_launchd.read_text()) if fake_launchd.exists() else {}
    return data


def nudges(fake_notifier_tools: Path) -> list[list[Any]]:
    return [c for c in calls(fake_notifier_tools) if c[0] == "open"]


@pytest.fixture
def config_file(tmp_path: Path, downloads: Path) -> Path:
    return write_config(tmp_path, downloads)


@pytest.fixture
def installed(config_file: Path) -> Config:
    result = runner.invoke(app, ["menu", "-c", str(config_file)])
    assert result.exit_code == 0, result.stdout + result.stderr
    return load_config(config_file)


def files_in_review(downloads: Path, cfg: Config) -> None:
    answers = {
        "unsure-one.pdf": answer("Finance", confidence=0.3),
        "unsure-two.pdf": answer("Finance", confidence=0.4),
    }
    for name in [*answers, "broken.pdf"]:
        put(downloads, name, name.encode())
    pipeline.run(cfg, FakeProvider(answers), simulate=False)


# --- installing and removing ------------------------------------------------------------


def test_flib_menu_builds_installs_and_starts_it(
    config_file: Path, fake_launchd: Path, fake_notifier_tools: Path
) -> None:
    result = runner.invoke(app, ["menu", "-c", str(config_file)])
    assert result.exit_code == 0, result.stderr
    assert "The cat is in the menu bar now" in result.stdout
    assert "`flib menu --remove` takes it away" in result.stdout
    assert scheduler.MENU_LABEL in loaded(fake_launchd)
    plist = scheduler.read_agent(scheduler.MENU_LABEL)
    assert plist == menubar.build_plist(config_file.resolve())
    assert menubar.problem(config_file.resolve()) is None
    target = menubar.read_target()
    assert target == menubar.Target(config_file.resolve(), load_config(config_file).database)
    # flib's own folder, owner only; the first state is there before the app starts.
    assert stat.S_IMODE(menubar.folder().stat().st_mode) == 0o700
    for name in (menubar.TARGET_NAME, menubar.STATE_NAME):
        assert stat.S_IMODE((menubar.folder() / name).stat().st_mode) == 0o600
    assert shown() == {
        "version": 1,
        "pose": "asleep",
        "count": 0,
        "line": "Nothing pending",
        "next": "Not scheduled",
        "review": 0,
        "due": 0,
        "running": False,
    }
    # The nudge is a distributed notification, never `open`: it cannot start the app.
    assert nudges(fake_notifier_tools) == []


def test_flib_menu_status_and_remove(installed: Config, fake_notifier_tools: Path) -> None:
    status = runner.invoke(app, ["menu", "--status"])
    assert "Menu bar: shown (`flib menu --remove` takes it away)." in status.stdout
    app_path = menubar.app_path()
    removed = runner.invoke(app, ["menu", "--remove"])
    assert removed.stdout.strip() == "Removed from the menu bar."
    assert not scheduler.plist_path(scheduler.MENU_LABEL).exists()
    assert not app_path.exists() and not menubar.folder().exists()
    assert calls(fake_notifier_tools)[-1] == ["lsregister", "-u", str(app_path)]
    again = runner.invoke(app, ["menu", "--remove"])
    assert again.stdout.strip() == "The menu bar item was not installed."
    status = runner.invoke(app, ["menu", "--status"])
    assert "not installed. `flib menu` puts flib in the menu bar." in status.stdout


def test_status_reports_what_the_applet_last_showed(installed: Config) -> None:
    menubar.write_private(
        menubar.folder() / menubar.SEEN_NAME, json.dumps({**shown(), "pid": 4242}).encode()
    )
    status = runner.invoke(app, ["menu", "--status"])
    assert "  it shows: asleep, 'Nothing pending'" in status.stdout
    assert "  for: " in status.stdout


def test_flib_menu_again_after_quit_starts_it(
    installed: Config, config_file: Path, fake_launchd: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_LAUNCHD_STOPPED", scheduler.MENU_LABEL)
    assert "installed, not running (Quit from its menu)" in (
        runner.invoke(app, ["menu", "--status"]).stdout
    )
    result = runner.invoke(app, ["menu", "-c", str(config_file)])
    assert result.exit_code == 0
    assert scheduler.MENU_LABEL in loaded(fake_launchd)  # loaded again: RunAtLoad starts it


def test_without_osacompile_it_says_so_and_installs_nothing(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notifier, "OSACOMPILE", "/nonexistent/osacompile")
    result = runner.invoke(app, ["menu", "-c", str(config_file)])
    assert result.exit_code == 1
    assert "the menu bar item cannot be built on this Mac (/nonexistent/osacompile missing)" in (
        result.stderr
    )
    assert "notifications keep working" in result.stderr
    assert not menubar.installed() and not menubar.folder().exists()
    report = build_report(Options(config_path=config_file))
    assert "cannot be built here: /nonexistent/osacompile missing" in report


def test_a_build_that_fails_is_reported(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_NOTIFIER_FAIL", "codesign")
    result = runner.invoke(app, ["menu", "-c", str(config_file)])
    assert result.exit_code == 1
    assert "could not set up the menu bar item: codesign failed" in result.stderr
    assert not menubar.installed()


def test_a_launchd_refusal_is_reported(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FAKE_LAUNCHD_FAIL", "bootstrap")
    result = runner.invoke(app, ["menu", "-c", str(config_file)])
    assert result.exit_code == 1
    assert "launchctl could not load" in result.stderr


def test_a_development_environment_is_warned_about(config_file: Path) -> None:
    result = runner.invoke(app, ["menu", "-c", str(config_file)])
    assert "warning: flib is running from a development environment" in result.stderr


def test_one_action_at_a_time(installed: Config) -> None:
    result = runner.invoke(app, ["menu", "--remove", "--status"])
    assert result.exit_code == 1
    assert "choose one of" in result.stderr
    assert menubar.installed()


def test_refresh_rebuilds_only_when_needed(
    installed: Config,
    config_file: Path,
    fake_launchd: Path,
    fake_notifier_tools: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    result = runner.invoke(app, ["menu", "--refresh"])
    assert result.stdout.strip() == "The menu bar item is up to date."
    builds = len([c for c in calls(fake_notifier_tools) if c[0] == "osacompile"])
    monkeypatch.setattr(menubar, "APP_VERSION", "test")
    result = runner.invoke(app, ["menu", "--refresh"])
    assert result.stdout.strip() == "Menu bar item rebuilt and restarted."
    assert len([c for c in calls(fake_notifier_tools) if c[0] == "osacompile"]) == builds + 1
    assert menubar.problem(config_file.resolve()) is None
    assert scheduler.MENU_LABEL in loaded(fake_launchd)
    # An update recreates the environment, and the app with it: `schedule --refresh`
    # (install.sh) rebuilds it even with nothing scheduled.
    shutil.rmtree(menubar.app_path())
    again = runner.invoke(app, ["schedule", "--refresh"])
    assert "Menu bar item rebuilt and restarted." in again.stdout
    assert "Nothing is scheduled; nothing to refresh." in again.stdout
    assert menubar.app_path().exists()


def test_refresh_without_the_item(fake_notifier_tools: Path) -> None:
    result = runner.invoke(app, ["menu", "--refresh"])
    assert result.stdout.strip() == "The menu bar item is not installed; nothing to refresh."
    assert calls(fake_notifier_tools) == []


def test_a_failed_refresh_is_a_warning(installed: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(menubar, "APP_VERSION", "test")
    monkeypatch.setenv("FAKE_NOTIFIER_FAIL", "osacompile")
    result = runner.invoke(app, ["menu", "--refresh"])
    assert result.exit_code == 0
    assert "warning: could not rebuild the menu bar item: osacompile failed" in result.stderr


# --- flib init ----------------------------------------------------------------------------


def test_init_offers_it_and_yes_installs_it(tmp_path: Path, downloads: Path) -> None:
    path = tmp_path / "config.yaml"
    result = runner.invoke(app, ["init", "-c", str(path), "--yes", "--sample", "0"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "Show flib in the menu bar (what needs you, and a menu that opens flib)? [Y/n]: y" in (
        result.stdout
    )
    assert menubar.installed()
    again = runner.invoke(app, ["init", "-c", str(path), "--yes", "--sample", "0"])
    assert "flib is already in the menu bar" in again.stdout


def test_init_says_nothing_about_it_where_it_cannot_be_built(
    tmp_path: Path, downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notifier, "OSACOMPILE", "/nonexistent/osacompile")
    path = tmp_path / "config.yaml"
    result = runner.invoke(app, ["init", "-c", str(path), "--yes", "--sample", "0"])
    assert result.exit_code == 0
    assert "menu bar" not in result.stdout
    assert not menubar.installed()


def test_init_declined(tmp_path: Path, downloads: Path) -> None:
    path = tmp_path / "config.yaml"
    answers = "\n" * 5 + "n\n" + "n\n" + "n\n"  # defaults, no trial, no schedule, no menu
    result = runner.invoke(app, ["init", "-c", str(path), "--sample", "0"], input=answers)
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "You can add it later with `flib menu`." in result.stdout
    assert not menubar.installed()


# --- the state, where it changes ----------------------------------------------------------


def test_a_run_shows_busy_then_what_waits(
    installed: Config, downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    written: list[dict[str, Any]] = []
    real = menubar.write_private

    def spy(path: Path, data: bytes) -> None:
        if path == menubar.state_path():
            written.append(json.loads(data))
        real(path, data)

    monkeypatch.setattr(menubar, "write_private", spy)
    files_in_review(downloads, installed)
    assert written[0]["pose"] == "busy" and written[0]["running"] is True
    assert written[0]["line"] == "Organizing" + ELLIPSIS
    assert shown() == {
        "version": 1,
        "pose": "attention",
        "count": 3,
        "line": "2 files to review, 1 failed",
        "next": "Not scheduled",
        "review": 2,
        "due": 0,
        "running": False,
    }
    # Counts only: no file name reaches the menu bar.
    raw = menubar.state_path().read_text(encoding="utf-8")
    assert "unsure" not in raw and "broken" not in raw


def test_undo_from_the_command_line_updates_it(
    installed: Config, config_file: Path, downloads: Path
) -> None:
    files_in_review(downloads, installed)
    assert shown()["review"] == 2
    result = runner.invoke(app, ["undo", "-c", str(config_file)])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert shown()["review"] == 0


def test_every_release_of_the_lock_updates_it(installed: Config) -> None:
    menubar.state_path().unlink()
    with instance_lock(installed.database):
        assert not menubar.state_path().exists()
    assert shown()["pose"] == "asleep"


def test_another_librarys_lock_is_ignored(installed: Config, tmp_path: Path) -> None:
    menubar.state_path().unlink()
    with instance_lock(tmp_path / "other" / "library.db"):
        pass
    assert not menubar.state_path().exists()


def test_a_lock_that_was_never_held_writes_nothing(installed: Config) -> None:
    menubar.state_path().unlink()
    with instance_lock(installed.database), pytest.raises(lock.AlreadyRunningError):  # noqa: SIM117
        with instance_lock(installed.database):
            pass
    assert shown()["pose"] == "asleep"  # only the outer one, once released


def test_nothing_is_written_without_the_item(cfg: Config, downloads: Path) -> None:
    files_in_review(downloads, cfg)
    assert not menubar.folder().exists()


def test_a_broken_hook_never_breaks_flib(
    installed: Config,
    config_file: Path,
    downloads: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    config_file.write_text("inputs: [", encoding="utf-8")  # the item's configuration is broken
    with caplog.at_level(logging.DEBUG, logger="file_librarian.menubar_state"):
        menubar_state.changed()
    assert "menu bar state not written" in caplog.text
    assert shown()["pose"] == "asleep"  # the last good state stays


def test_an_unexpected_error_is_swallowed(
    installed: Config, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    def boom(*args: object, **kwargs: object) -> None:
        raise RuntimeError("surprise")

    monkeypatch.setattr(menubar_state, "view", boom)
    with caplog.at_level(logging.DEBUG, logger="file_librarian.menubar_state"):
        menubar_state.changed()
    assert "RuntimeError: surprise" in caplog.text


def test_nudged_only_when_the_state_changed(
    installed: Config, menu_nudges: list[str], fake_notifier_tools: Path
) -> None:
    menu_nudges.clear()
    menubar_state.changed()  # nothing changed: no write, no nudge
    assert menu_nudges == []
    menubar.state_path().unlink()
    menubar_state.changed()
    assert menu_nudges == [menubar.NOTE_NAME]
    assert nudges(fake_notifier_tools) == []  # never `open`, which could start the app


def test_a_nudge_that_cannot_be_posted_is_ignored(
    installed: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(name: str) -> bool:
        raise OSError("no CoreFoundation")

    monkeypatch.setattr(menubar_state, "POST", broken)
    assert menubar_state.nudge() is False


def test_the_real_nudge_is_a_distributed_notification(monkeypatch: pytest.MonkeyPatch) -> None:
    calls_made: list[tuple[str, tuple[object, ...]]] = []

    class FakeCF:
        def __getattr__(self, name: str) -> Any:
            def call(*args: object) -> int:
                calls_made.append((name, args))
                return 7

            return type("F", (), {"__call__": staticmethod(call)})()

    monkeypatch.setattr("ctypes.CDLL", lambda path: FakeCF())
    assert menubar_state._post_distributed(menubar.NOTE_NAME) is True
    names = [name for name, _ in calls_made]
    assert names == [
        "CFStringCreateWithCString",
        "CFNotificationCenterGetDistributedCenter",
        "CFNotificationCenterPostNotification",
        "CFRelease",
    ]
    assert calls_made[0][1][1] == menubar.NOTE_NAME.encode()
    assert calls_made[2][1][1:] == (7, None, None, True)


def test_no_launchctl_and_no_subprocess_on_the_hot_path(
    installed: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    import subprocess

    def forbidden(*args: object, **kwargs: object) -> None:
        raise AssertionError("a subprocess on the hot path")

    monkeypatch.setattr(subprocess, "run", forbidden)
    menubar.state_path().unlink()
    with instance_lock(installed.database):
        pass
    assert shown()["pose"] == "asleep"


def test_a_ctrl_c_in_the_hook_never_replaces_the_callers_exception(
    installed: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    def interrupted(database: Path | None = None) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr(menubar_state, "changed", interrupted)
    with pytest.raises(ValueError, match="the caller's"), instance_lock(installed.database):
        raise ValueError("the caller's")
    with pytest.raises(KeyboardInterrupt), instance_lock(installed.database):
        pass  # with nothing else on its way, a Ctrl-C is a Ctrl-C


def test_the_schedule_shows_as_the_next_run(installed: Config, config_file: Path) -> None:
    result = runner.invoke(app, ["schedule", "-c", str(config_file), "--no-check"])
    assert result.exit_code == 0, result.stderr
    assert shown()["next"].startswith("Next run ")
    runner.invoke(app, ["schedule", "--remove"])
    assert shown()["next"] == "Not scheduled"


def test_a_run_that_cannot_work_shows_trouble_until_one_works(
    installed: Config, config_file: Path, downloads: Path
) -> None:
    gone = downloads.rename(downloads.with_name("Downloads-elsewhere"))  # no input to read
    result = runner.invoke(app, ["run", "-c", str(config_file)])
    assert result.exit_code == 1, result.stdout + result.stderr
    assert shown()["pose"] == "trouble"
    assert shown()["line"].startswith("flib could not run")
    gone.rename(downloads)
    result = runner.invoke(app, ["run", "-c", str(config_file)])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert shown()["pose"] == "asleep"
    assert menubar_state.read_problem() is None


def test_a_problem_is_noted_only_for_the_items_library(installed: Config, tmp_path: Path) -> None:
    menubar_state.note_problem(tmp_path / "other.db", "run_failed")
    assert menubar_state.read_problem() is None
    menubar_state.note_problem(installed.database, "no_access")
    assert menubar_state.read_problem() == "no_access"
    assert shown()["line"].startswith("macOS denied a folder")
    menubar_state.note_problem(installed.database, None)
    assert menubar_state.read_problem() is None


# --- the words -----------------------------------------------------------------------------


def _status(cfg: Config, **changes: Any) -> st.Status:
    return dataclasses.replace(st.collect(cfg, menu_bar=False), **changes)


def test_describe(cfg: Config) -> None:
    base = _status(cfg)
    assert menubar_state.describe(base, running=True, problem=None)[0] == "Organizing" + ELLIPSIS
    assert menubar_state.describe(None, running=False, problem=None)[0].startswith(
        "Cannot read flib's index"
    )
    assert menubar_state.describe(base, running=False, problem=None)[0] == "Nothing pending"
    due = _status(cfg, due=DueCounts(7, 2, 1))
    assert menubar_state.describe(due, running=False, problem=None)[0] == "1 overdue, 2 due soon"
    assert (
        menubar_state.describe(base, running=False, problem="nonsense")[0]
        == (menubar_state.PROBLEMS["run_failed"])
    )


def test_next_line() -> None:
    at = datetime(2026, 11, 26, 12, 30, tzinfo=UTC)
    run = st.NextRun(True, True, "12:30", at, mode="daily")
    assert menubar_state.next_line(run) == f"Next run {at.strftime('%a')} 12:30"
    assert menubar_state.next_line(dataclasses.replace(run, mode="both")).endswith(
        ", and as files arrive"
    )
    watch = st.NextRun(True, True, None, None, mode="watch")
    assert menubar_state.next_line(watch) == "Runs as files arrive"
    assert menubar_state.next_line(dataclasses.replace(watch, loaded=False)).startswith(
        "Schedule not active"
    )
    assert menubar_state.next_line(st.NextRun(False, False, None, None)) == "Not scheduled"
    assert menubar_state.next_line(st.NextRun(False, False, None, None, "launchctl")) == ""
    assert menubar_state.next_line(st.NextRun(True, True, None, None, mode="daily")) == ""


def test_due_documents_choose_the_pose_and_the_direct_entry(
    installed: Config, config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = st.collect

    def with_due(*args: Any, **kwargs: Any) -> st.Status:
        return dataclasses.replace(real(*args, **kwargs), due=DueCounts(7, 2, 1))

    monkeypatch.setattr(st, "collect", with_due)
    view = menubar_state.view(installed.database, config_file)
    assert (view.pose, view.count, view.due) == (poses.Pose.DUE, 3, 3)


def test_the_state_is_bounded(installed: Config) -> None:
    view = menubar_state.View(poses.Pose.ATTENTION, 123456, "x" * 200, "", -4, 10**6, False)
    data = json.loads(view.to_json())
    assert data["count"] == data["due"] == menubar.MAX_COUNT and data["review"] == 0
    assert len(data["line"]) == menubar.MAX_TEXT and data["line"].endswith(ELLIPSIS)
    assert menubar.sanitize(view.to_json()) == data


def test_an_unreadable_index_is_trouble(
    installed: Config, config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: Any, **kwargs: Any) -> st.Status:
        raise st.StatusError("the index is newer than this flib")

    monkeypatch.setattr(st, "collect", broken)
    view = menubar_state.view(installed.database, config_file)
    assert view.pose is poses.Pose.TROUBLE and view.next == ""


# --- the lock's holder ------------------------------------------------------------------


def test_holder_reads_the_lock_without_taking_it(cfg: Config) -> None:
    assert lock.holder(cfg.database) is None
    with instance_lock(cfg.database):
        assert lock.holder(cfg.database) == os.getpid()
        # Asking did not take it: another taker still gets "already running".
        with pytest.raises(lock.AlreadyRunningError):  # noqa: SIM117
            with instance_lock(cfg.database):
                pass
    assert lock.holder(cfg.database) is None


@pytest.mark.parametrize("content", [b"", b"abc", b"0", b"-5", b"99999999", b"1" * 40])
def test_holder_ignores_what_is_not_a_live_pid(cfg: Config, content: bytes) -> None:
    path = lock.lock_path(cfg.database)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    assert lock.holder(cfg.database) is None


def test_holder_of_another_account(cfg: Config) -> None:
    path = lock.lock_path(cfg.database)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"1")  # launchd: alive, not ours
    assert lock.holder(cfg.database) == 1


# --- Organize now ---------------------------------------------------------------------------


def test_organize_now_says_so_when_a_run_is_going_on(
    installed: Config, config_file: Path, downloads: Path
) -> None:
    put(downloads, "a.pdf", b"a")
    with instance_lock(installed.database):
        result = runner.invoke(app, ["menu", "--organize", "-c", str(config_file)])
        assert shown()["running"] is True
    assert result.exit_code == 0
    assert "A run is in progress" in result.stdout
    assert (downloads / "a.pdf").exists()


def test_organize_now_starts_the_daily_agent_of_this_configuration(
    installed: Config, config_file: Path, fake_launchd: Path
) -> None:
    runner.invoke(app, ["schedule", "-c", str(config_file), "--no-check"])
    result = runner.invoke(app, ["menu", "--organize", "-c", str(config_file)])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "Started the daily run now" in result.stdout
    assert calls(fake_launchd.with_name("kicks.jsonl")) == [[scheduler.LABEL, False]]


def test_organize_now_when_launchd_refuses(
    installed: Config, config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    runner.invoke(app, ["schedule", "-c", str(config_file), "--no-check"])
    monkeypatch.setenv("FAKE_LAUNCHD_FAIL", "kickstart")
    result = runner.invoke(app, ["menu", "--organize", "-c", str(config_file)])
    assert result.exit_code == 1
    assert "launchctl could not start" in result.stderr


def test_organize_now_without_a_schedule_runs_here(
    installed: Config, config_file: Path, downloads: Path, fake_launchd: Path, tmp_path: Path
) -> None:
    other = write_config(tmp_path, downloads)
    runner.invoke(app, ["schedule", "-c", str(other), "--no-check"])  # another configuration
    put(downloads, "a.pdf", b"a")
    result = runner.invoke(app, ["menu", "--organize", "-c", str(config_file)])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert not (downloads / "a.pdf").exists()
    assert calls(fake_launchd.with_name("kicks.jsonl")) == []


# --- flib status, flib doctor -----------------------------------------------------------


def test_status_and_doctor_say_how_it_is(
    installed: Config, config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    status = runner.invoke(app, ["status", "-c", str(config_file)])
    assert "Menu bar: shown (`flib menu --remove` takes it away)." in status.stdout
    report = build_report(Options(config_path=config_file))
    section = report.split("== Menu bar ==", 1)[1].split("==", 1)[0]
    assert "installed: yes" in section and "running: yes" in section
    assert "app: built and current" in section
    assert "uses this configuration: yes" in section
    assert "last shown: nothing reported by the app yet" in section
    menubar.write_private(
        menubar.folder() / menubar.SEEN_NAME, json.dumps({**shown(), "pid": 4242}).encode()
    )
    report = build_report(Options(config_path=config_file))
    assert "last shown: asleep (count 0, review 0, due 0), " in report
    monkeypatch.setattr(menubar, "APP_VERSION", "9")
    # flib status stays cheap (the source is not regenerated); doctor and --status check.
    status = runner.invoke(app, ["status", "-c", str(config_file)])
    assert "Menu bar: shown" in status.stdout
    shown_status = runner.invoke(app, ["menu", "--status"])
    assert "the menu bar app is out of date; `flib menu` fixes it." in shown_status.stdout
    assert "app: the menu bar app is out of date" in build_report(Options(config_path=config_file))
    monkeypatch.setenv("FAKE_LAUNCHD_FAIL", "print")
    found = menubar.summary()
    assert found.installed and not found.loaded and not found.running


def test_status_without_the_item_says_nothing_about_it(config_file: Path) -> None:
    status = runner.invoke(app, ["status", "-c", str(config_file)])
    assert "Menu bar" not in status.stdout
    report = build_report(Options(config_path=config_file))
    assert "== Menu bar ==\ninstalled: no\n" in report


def test_status_lines_for_every_state() -> None:
    assert menubar.status_line(menubar.Summary(False)) is None
    assert "not loaded" in (menubar.status_line(menubar.Summary(True)) or "")
    unset = menubar.Summary(True, True, True, "flib menu was not set up completely")
    assert "not set up completely" in (menubar.status_line(unset) or "")


def test_an_install_without_its_record_is_reported(installed: Config) -> None:
    (menubar.folder() / menubar.TARGET_NAME).unlink()
    assert menubar.summary().problem == "flib menu was not set up completely"
    assert menubar.refresh() is None


# --- the interface when it already runs ---------------------------------------------------


def test_open_goes_to_the_page_of_the_interface_already_running(
    cfg: Config, fake_browser: Path
) -> None:
    lines: list[str] = []
    with ui_lock(cfg.database):
        ui_server.write_running(cfg.database, 54321)
        assert ui_server.running_port(cfg.database) == 54321
        reason = ui_server.run(cfg, page="review", echo=lines.append)
        assert reason == "running"
        assert calls(fake_browser)[-1] == ["http://127.0.0.1:54321/review"]
        assert "opened its review page in the browser where it runs" in lines[-1]
        # With --no-browser it only says so, as before.
        lines.clear()
        assert ui_server.run(cfg, page="due", browser=False, echo=lines.append) == "running"
        assert "flib ui is already running" in lines[-1] and len(calls(fake_browser)) == 1
        ui_server.remove_running(cfg.database)
    assert not ui_server.running_path(cfg.database).exists()


@pytest.mark.parametrize(
    "record",
    [
        b"not json",
        b"[]",
        json.dumps({"pid": 1, "port": 54321}).encode(),  # not the holder
        json.dumps({"pid": "me", "port": 54321}).encode(),
        json.dumps({"pid": None, "port": True}).encode(),
    ],
)
def test_a_record_that_does_not_match_the_holder_is_not_believed(
    cfg: Config, fake_browser: Path, record: bytes
) -> None:
    with ui_lock(cfg.database):
        ui_server.running_path(cfg.database).write_bytes(record)
        assert ui_server.running_port(cfg.database) is None
        lines: list[str] = []
        assert ui_server.run(cfg, page="review", echo=lines.append) == "running"
        assert "Use the browser tab it opened" in lines[-1]
    assert calls(fake_browser) == []


def test_a_port_out_of_range_is_not_believed(cfg: Config) -> None:
    with ui_lock(cfg.database):
        record = {"pid": os.getpid(), "port": 70000}
        ui_server.running_path(cfg.database).write_text(json.dumps(record))
        assert ui_server.running_port(cfg.database) is None


def test_a_browser_that_cannot_be_opened_falls_back_to_the_message(
    cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_OPEN_FAIL", "1")
    with ui_lock(cfg.database):
        ui_server.write_running(cfg.database, 54321)
        lines: list[str] = []
        assert ui_server.run(cfg, page="review", echo=lines.append) == "running"
        assert "Use the browser tab it opened" in lines[-1]


def test_another_servers_record_is_left_alone(cfg: Config) -> None:
    path = ui_server.running_path(cfg.database)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"pid": 1, "port": 5}))
    ui_server.remove_running(cfg.database)
    assert path.exists()


def test_install_script_refreshes_the_item() -> None:
    script = (Path(__file__).parents[2] / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert '"$agents.menu.plist"' in script and "menu --refresh" in script
    assert scheduler.MENU_LABEL == scheduler.LABEL + ".menu"  # what "$agents.menu" names


# --- after the review of #132 ---------------------------------------------------------------


def test_a_failed_install_leaves_no_target_and_no_writes(
    config_file: Path, monkeypatch: pytest.MonkeyPatch, cfg: Config
) -> None:
    monkeypatch.setenv("FAKE_LAUNCHD_FAIL", "bootstrap")
    result = runner.invoke(app, ["menu", "-c", str(config_file)])
    assert result.exit_code == 1
    assert "the menu bar item is not installed" in result.stderr
    assert not menubar.installed() and menubar.read_target() is None
    monkeypatch.delenv("FAKE_LAUNCHD_FAIL")
    if menubar.state_path().exists():
        menubar.state_path().unlink()
    with instance_lock(cfg.database):
        pass
    assert not menubar.state_path().exists()  # nothing installed: nothing written


def test_a_failed_reinstall_never_mixes_two_configurations(
    installed: Config, tmp_path: Path, downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = write_config(tmp_path, downloads)
    monkeypatch.setenv("FAKE_NOTIFIER_FAIL", "osacompile")  # B's build fails
    result = runner.invoke(app, ["menu", "-c", str(other)])
    assert result.exit_code == 1
    target = menubar.read_target()
    assert target is not None and target.config != other.resolve()  # still A, as the app
    monkeypatch.delenv("FAKE_NOTIFIER_FAIL")
    monkeypatch.setenv("FAKE_LAUNCHD_FAIL", "bootstrap")  # B builds, launchd refuses
    result = runner.invoke(app, ["menu", "-c", str(other)])
    assert result.exit_code == 1
    assert not menubar.installed()  # rolled back: nothing half-installed keeps writing
    kept = menubar.read_target()
    assert kept is not None and kept.config != other.resolve()  # B was never recorded


def test_a_record_without_the_agent_writes_nothing(config_file: Path, cfg: Config) -> None:
    menubar.write_private(
        menubar.folder() / menubar.TARGET_NAME,
        json.dumps({"config": str(config_file), "database": str(cfg.database)}).encode(),
    )
    menubar_state.note_problem(cfg.database, "run_failed")
    assert menubar_state.read_problem() is None  # no agent: not installed
    with instance_lock(cfg.database):
        pass
    menubar_state.changed()
    assert not menubar.state_path().exists()


def test_summary_never_raises_on_a_hostile_seen_file(installed: Config) -> None:
    seen = menubar.folder() / menubar.SEEN_NAME
    for raw in (b"[" * 3000 + b"]" * 3000, b'{"pid": 1e999}', b"\xff\xfe", b"{" * 5000):
        menubar.write_private(seen, raw)
        assert menubar.read_seen() is None
        found = menubar.summary(full=True)
        assert found.installed and found.seen is None and found.problem is None
    big = json.dumps({"pid": 5, "line": "x" * 20000}).encode()
    menubar.write_private(seen, big)
    assert menubar.read_seen() is None  # more than it ever reads
    (menubar.folder() / menubar.TARGET_NAME).write_bytes(b"[" * 5000)
    assert menubar.summary().problem == "flib menu was not set up completely"


def test_summary_never_raises_at_all(installed: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise RecursionError

    monkeypatch.setattr(menubar, "read_target", boom)
    found = menubar.summary()
    assert found.installed and found.problem == "its state cannot be read (RecursionError)"
    status = runner.invoke(app, ["menu", "--status"])
    assert status.exit_code == 0


def test_status_asks_launchd_once(installed: Config, monkeypatch: pytest.MonkeyPatch) -> None:
    asked: list[tuple[str, ...]] = []
    real = scheduler._launchctl

    def counting(*args: str) -> Any:
        asked.append(args)
        return real(*args)

    monkeypatch.setattr(scheduler, "_launchctl", counting)
    monkeypatch.setattr(menubar, "source_for", lambda config: pytest.fail("regenerated"))
    menubar.summary()
    assert len(asked) == 1


def test_a_linked_menu_folder_is_never_written_through(installed: Config, tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    shutil.rmtree(menubar.folder())
    menubar.folder().symlink_to(elsewhere)
    with pytest.raises(OSError, match="not a folder of this user's"):
        menubar.write_private(menubar.state_path(), b"x")
    menubar_state.changed()  # swallowed
    assert list(elsewhere.iterdir()) == []
    assert runner.invoke(app, ["menu", "--remove"]).exit_code == 0
    assert not menubar.folder().is_symlink() and not menubar.folder().exists()
    assert elsewhere.is_dir()  # the link went, not what it points to


def test_a_partial_removal_says_what_is_left(
    installed: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = menubar._delete

    def stuck(path: Path) -> bool:
        if path == menubar.folder():
            raise PermissionError(13, "Permission denied")
        return real(path)

    monkeypatch.setattr(menubar, "_delete", stuck)
    result = runner.invoke(app, ["menu", "--remove"])
    assert result.exit_code == 1 and result.exception is not None
    assert isinstance(result.exception, SystemExit)  # said, not a traceback
    assert "could not remove" in result.stderr and "Permission denied" in result.stderr
    assert not menubar.installed() and not menubar.app_path().exists()  # the rest went


def test_an_app_path_that_is_a_link_is_unlinked(installed: Config, tmp_path: Path) -> None:
    elsewhere = tmp_path / "real.app"
    elsewhere.mkdir()
    shutil.rmtree(menubar.app_path())
    menubar.app_path().symlink_to(elsewhere)
    assert menubar.remove() is True
    assert elsewhere.is_dir() and not menubar.app_path().is_symlink()


def test_the_settings_page_says_whether_it_is_installed(config_file: Path) -> None:
    from file_librarian.ui import settings

    assert "`flib menu` puts the cat there" in settings.menu_bar_note()
    assert runner.invoke(app, ["menu", "-c", str(config_file)]).exit_code == 0
    assert "`flib menu --remove` takes it away" in settings.menu_bar_note()


def test_a_page_not_in_the_table_never_reaches_an_address(cfg: Config, fake_browser: Path) -> None:
    with ui_lock(cfg.database):
        ui_server.write_running(cfg.database, 54321)
        for page in ("../../etc", "launch?token=abc", "review/accept?id=1", "Review"):
            with pytest.raises(ui_server.UiError, match="no page"):
                ui_server.run(cfg, page=page, echo=lambda line: None)
    assert calls(fake_browser) == []
    assert {page: f"/{page}" for page in ui_server.PAGES} == ui_server.PAGE_PATHS


def test_flib_ui_open_is_a_fixed_choice(config_file: Path, fake_browser: Path) -> None:
    result = runner.invoke(app, ["ui", "--open", "../../etc", "-c", str(config_file)])
    assert result.exit_code == 2
    assert calls(fake_browser) == []


def test_a_boolean_port_is_not_believed_even_from_the_holder(cfg: Config) -> None:
    with ui_lock(cfg.database):
        record = {"pid": os.getpid(), "port": True}
        ui_server.running_path(cfg.database).write_text(json.dumps(record))
        assert ui_server.running_port(cfg.database) is None
        record = {"pid": os.getpid(), "port": 5}
        ui_server.running_path(cfg.database).write_text(json.dumps(record))
        assert ui_server.running_port(cfg.database) == 5


def test_the_running_record_is_private_and_never_follows_a_link(
    cfg: Config, tmp_path: Path
) -> None:
    cfg.database.parent.mkdir(parents=True, exist_ok=True)
    ui_server.write_running(cfg.database, 4242)
    path = ui_server.running_path(cfg.database)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    path.unlink()
    victim = tmp_path / "victim"
    victim.write_text("keep")
    tmp = path.with_name(f".{ui_server.RUNNING_NAME}.{os.getpid()}")
    tmp.symlink_to(victim)
    ui_server.write_running(cfg.database, 4242)
    assert victim.read_text() == "keep"
    assert not path.exists()


def test_a_removal_goes_on_after_a_step_that_failed(
    installed: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    real = menubar._delete

    def stuck(path: Path) -> bool:
        if path == menubar.app_path():
            raise PermissionError(13, "Permission denied")
        return real(path)

    monkeypatch.setattr(menubar, "_delete", stuck)
    with pytest.raises(menubar.MenuError, match=r"file-librarian-menu\.app"):
        menubar.remove()
    assert not menubar.installed() and not menubar.folder().exists()

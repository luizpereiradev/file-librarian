from __future__ import annotations

import json
import os
import plistlib
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_librarian import keychain, scheduler
from file_librarian.cli import app
from file_librarian.config import load_config
from file_librarian.db.store import Store
from tests._helpers import OLD, REPO_ROOT

runner = CliRunner()
KEY = "sk-ant-" + "sched-" + "k" * 20


@pytest.fixture
def config_file(tmp_path: Path, downloads: Path) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(
        f"inputs: [{downloads}]\n"
        "provider: {name: anthropic, model: m}\n"
        "schedule: {time: '9:05'}\n",
        encoding="utf-8",
    )
    return path


def loaded(fake_launchd: Path) -> dict[str, str]:
    return json.loads(fake_launchd.read_text()) if fake_launchd.exists() else {}


def test_schedule_installs_a_daily_agent(config_file: Path, fake_launchd: Path) -> None:
    keychain.write(KEY)
    result = runner.invoke(app, ["schedule", "-c", str(config_file)])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "Daily run scheduled at 09:05" in result.stdout
    data = plistlib.loads(scheduler.plist_path().read_bytes())
    assert data["StartCalendarInterval"] == {"Hour": 9, "Minute": 5}
    interpreter = scheduler.dedicated_interpreter()
    assert data["ProgramArguments"] == [
        str(interpreter), "-m", "file_librarian", "run", "--trigger", "schedule"
    ]  # fmt: skip
    # flib's own copy of the interpreter: a real file, not the shared one or a link to it.
    assert interpreter.name == "flib-python" and not interpreter.is_symlink()
    assert interpreter.read_bytes() == Path(os.path.realpath(sys.executable)).read_bytes()
    assert data["EnvironmentVariables"]["FLIB_CONFIG"] == str(config_file.resolve())
    assert "ANTHROPIC_API_KEY" not in data["EnvironmentVariables"]  # never in the plist
    assert list(loaded(fake_launchd)) == [scheduler.LABEL]
    # The access check ran in the scheduled context and cleaned up after itself.
    assert "Access OK" in result.stdout
    assert not scheduler.plist_path(scheduler.CHECK_LABEL).exists()


def test_at_overrides_the_config_and_reinstall_replaces(config_file: Path) -> None:
    keychain.write(KEY)
    runner.invoke(app, ["schedule", "-c", str(config_file), "--no-check"])
    result = runner.invoke(app, ["schedule", "-c", str(config_file), "--at", "21:30", "--no-check"])
    assert "scheduled at 21:30" in result.stdout
    status = runner.invoke(app, ["schedule", "--status"])
    assert "Daily run at 21:30: active" in status.stdout


@pytest.mark.parametrize("bad", ["25:00", "9", "noon", "12:5"])
def test_invalid_times_are_rejected(config_file: Path, bad: str) -> None:
    result = runner.invoke(app, ["schedule", "-c", str(config_file), "--at", bad, "--no-check"])
    assert result.exit_code == 1 and "24-hour time" in result.stderr


def test_remove_and_status(config_file: Path, fake_launchd: Path) -> None:
    assert "No daily run is scheduled" in runner.invoke(app, ["schedule", "--status"]).stdout
    keychain.write(KEY)
    runner.invoke(app, ["schedule", "-c", str(config_file), "--no-check"])
    removed = runner.invoke(app, ["schedule", "--remove"])
    assert "Daily run removed" in removed.stdout
    assert not scheduler.plist_path().exists() and loaded(fake_launchd) == {}
    assert "No daily run was scheduled" in runner.invoke(app, ["schedule", "--remove"]).stdout


def test_key_only_in_environment_is_offered_to_the_keychain(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    result = runner.invoke(app, ["schedule", "-c", str(config_file), "--no-check"], input="y\n")
    assert "Save it to the macOS Keychain?" in result.stdout
    assert keychain.read() == KEY


def test_declining_to_save_the_key_warns(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", KEY)
    result = runner.invoke(app, ["schedule", "-c", str(config_file), "--no-check", "--no-save-key"])
    assert keychain.read() is None
    assert "will not find the API key" in result.stderr


def test_no_key_anywhere_warns(config_file: Path) -> None:
    result = runner.invoke(app, ["schedule", "-c", str(config_file), "--no-check"])
    assert result.exit_code == 0 and "no API key found" in result.stderr


def test_development_environment_warning(config_file: Path) -> None:
    keychain.write(KEY)
    result = runner.invoke(app, ["schedule", "-c", str(config_file), "--no-check"])
    assert ("development environment" in result.stderr) is not scheduler.is_installed_tool()


def test_denied_folder_explains_how_to_grant_access(config_file: Path, downloads: Path) -> None:
    keychain.write(KEY)
    downloads.chmod(0o000)
    try:
        result = runner.invoke(app, ["schedule", "-c", str(config_file)])
    finally:
        downloads.chmod(0o755)
    assert result.exit_code == 1
    assert "denied by macOS privacy settings" in result.stdout
    assert "Full Disk Access" in result.stderr and "Cmd+Shift+G" in result.stderr


def test_scheduled_run_records_its_trigger(tmp_path: Path, downloads: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text(f"inputs: [{downloads}]\nprovider: {{name: fake, model: x}}\n", encoding="utf-8")
    (downloads / "a.txt").write_text("x", encoding="utf-8")
    os.utime(downloads / "a.txt", (OLD, OLD))
    assert runner.invoke(app, ["run", "-c", str(cfg), "--trigger", "schedule"]).exit_code == 0
    with Store.open(load_config(cfg).database) as store:
        assert store.conn.execute("SELECT trigger FROM runs").fetchone()[0] == "schedule"


def test_launchctl_refusing_to_load_is_reported(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keychain.write(KEY)
    monkeypatch.setenv("FAKE_LAUNCHD_FAIL", "bootstrap")
    result = runner.invoke(app, ["schedule", "-c", str(config_file), "--no-check"])
    assert result.exit_code == 1 and "could not load" in result.stderr


def test_missing_launchctl(config_file: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    keychain.write(KEY)
    monkeypatch.setattr("file_librarian.scheduler.LAUNCHCTL", "/nonexistent/launchctl")
    result = runner.invoke(app, ["schedule", "-c", str(config_file), "--no-check"])
    assert result.exit_code == 1 and "cannot run" in result.stderr
    assert runner.invoke(app, ["schedule", "--status"]).exit_code in (0, 1)


def test_access_check_that_never_reports_times_out(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("file_librarian.scheduler.program", lambda: ["/usr/bin/true"])
    with pytest.raises(scheduler.ScheduleError, match="did not report back"):
        scheduler.check_access(config_file, timeout=0.3)
    assert not scheduler.plist_path(scheduler.CHECK_LABEL).exists()  # cleaned up anyway


def test_a_failed_access_check_is_a_warning(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keychain.write(KEY)

    def fail(_config: Path) -> dict[str, str]:
        raise scheduler.ScheduleError("the access check did not report back in time")

    monkeypatch.setattr("file_librarian.scheduler.check_access", fail)
    result = runner.invoke(app, ["schedule", "-c", str(config_file)])
    assert result.exit_code == 0 and "warning: the access check" in result.stderr


def test_probe_reports_missing_folders(tmp_path: Path) -> None:
    report = scheduler.probe_inputs([tmp_path, tmp_path / "missing"])
    assert report[str(tmp_path)] == "ok"
    assert report[str(tmp_path / "missing")].startswith("error:")


def test_check_access_with_a_broken_config(tmp_path: Path) -> None:
    bad = tmp_path / "c.yaml"
    bad.write_text("inputs: []\n", encoding="utf-8")
    out = tmp_path / "report.json"
    assert runner.invoke(app, ["check-access", "--out", str(out), "-c", str(bad)]).exit_code == 0
    assert json.loads(out.read_text())["config"].startswith("error:")


def test_access_instructions_name_the_dedicated_interpreter(
    config_file: Path, downloads: Path
) -> None:
    keychain.write(KEY)
    downloads.chmod(0o000)
    try:
        result = runner.invoke(app, ["schedule", "-c", str(config_file)])
    finally:
        downloads.chmod(0o755)
    assert str(scheduler.dedicated_interpreter()) in result.stderr
    assert "covers flib only" in result.stderr
    assert os.path.realpath(sys.executable) not in result.stderr


def test_status_warns_when_the_interpreter_copy_is_stale(config_file: Path) -> None:
    keychain.write(KEY)
    runner.invoke(app, ["schedule", "-c", str(config_file), "--no-check"])
    scheduler.dedicated_interpreter().write_bytes(b"old python")
    status = runner.invoke(app, ["schedule", "--status"])
    assert "out of date" in status.stderr and "--refresh" in status.stderr


def test_refresh_keeps_the_time_and_recopies(config_file: Path) -> None:
    keychain.write(KEY)
    runner.invoke(app, ["schedule", "-c", str(config_file), "--at", "07:45", "--no-check"])
    scheduler.dedicated_interpreter().unlink()  # what a reinstall does
    refreshed = runner.invoke(app, ["schedule", "--refresh"])
    assert refreshed.exit_code == 0, refreshed.stderr
    assert "scheduled at 07:45" in refreshed.stdout
    assert scheduler.interpreter_is_current()
    data = plistlib.loads(scheduler.plist_path().read_bytes())
    assert data["EnvironmentVariables"]["FLIB_CONFIG"] == str(config_file.resolve())


def test_refresh_without_a_schedule_does_nothing() -> None:
    result = runner.invoke(app, ["schedule", "--refresh"])
    assert result.exit_code == 0 and "nothing to refresh" in result.stdout
    assert not scheduler.plist_path().exists()


def test_install_script_refreshes_an_existing_schedule() -> None:
    script = (REPO_ROOT / "scripts" / "install.sh").read_text(encoding="utf-8")
    assert "schedule --refresh" in script
    assert scheduler.LABEL in script

"""flib's notifier app: built, refreshed and removed with the schedule, used when ready,
with ``osascript`` as the fallback. Every tool is a fake (tests/fake_notifier_tools.py);
the opt-in test at the bottom builds a real app, and never posts a notification."""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_librarian import keychain, notifier, scheduler
from file_librarian import status as st
from file_librarian.cli import app
from file_librarian.config import load_config
from file_librarian.doctor import Options, build_report
from file_librarian.runlog import logs_dir
from tests._helpers import write_config

runner = CliRunner()
KEY = "sk-ant-" + "notif-" + "k" * 20
PROVIDER = "{name: anthropic, model: m}"
NOTIFY_OFF = "summary:\n  notify: false\n"


def calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def tools(log: Path) -> list[str]:
    return [call[0] for call in calls(log)]


@pytest.fixture
def config_file(tmp_path: Path, downloads: Path) -> Path:
    return write_config(tmp_path, downloads, "{name: anthropic, model: m}")


# --- building ---------------------------------------------------------------------------


def test_build_makes_a_signed_app_with_flibs_name_icon_and_script(
    fake_notifier_tools: Path,
) -> None:
    built = notifier.build()
    assert (
        built
        == notifier.app_path()
        == scheduler.dedicated_interpreter().parent / ("file-librarian.app")
    )
    contents = built / "Contents"
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == "io.github.luizpereiradev.file-librarian.notifier"
    assert info["CFBundleName"] == info["CFBundleDisplayName"] == "file-librarian"
    assert info["LSUIElement"] is True
    # The stock icon is gone and no permission wording is left behind.
    assert "CFBundleIconName" not in info and info["CFBundleIconFile"] == "applet"
    assert not any(key.endswith("UsageDescription") for key in info)
    assert not (contents / "Resources" / "Assets.car").exists()
    # Every icon size, @2x included, made from the packaged artwork.
    icns = (contents / "Resources" / "applet.icns").read_text(encoding="utf-8").split()
    assert icns == sorted(name for name, _ in notifier.ICON_SIZES)
    sips = [call for call in calls(fake_notifier_tools) if call[0] == "sips"]
    assert {call[call.index("-z") + 1] for call in sips} == {"16", "32", "64", "128", "256",
                                                              "512", "1024"}  # fmt: skip
    script = (contents / "Resources" / "Scripts" / "main.scpt").read_text(encoding="utf-8")
    assert script == notifier.SCRIPT
    # Signed after every edit, then verified; registered so the new icon shows.
    assert tools(fake_notifier_tools)[-3:] == ["codesign", "codesign", "lsregister"]
    sign, verify, register = calls(fake_notifier_tools)[-3:]
    # Signed in the work folder, then moved into place whole.
    assert sign[1:-1] == ["--force", "--deep", "--sign", "-"]
    assert verify[1:] == ["--verify", "--deep", "--strict", sign[-1]]
    assert Path(sign[-1]).name == built.name and Path(sign[-1]).parent.parent == built.parent
    assert register[1:] == ["-f", str(built)]
    assert notifier.state() is None
    assert notifier.choice() == notifier.Choice("app", "built and current")
    # Nothing is left of the work folder.
    assert sorted(p.name for p in built.parent.iterdir() if p.name.startswith(".")) == []


def test_the_script_reads_its_text_as_arguments_never_as_code() -> None:
    assert "NSProcessInfo" in notifier.SCRIPT
    assert "display notification (item -2 of args) with title (item -1 of args)" in (
        notifier.SCRIPT
    )
    # Errors are swallowed: a failing applet would show a dialog.
    assert notifier.SCRIPT.count("\ttry\n") == 1 and "end try" in notifier.SCRIPT


def test_ensure_builds_only_when_needed(fake_notifier_tools: Path) -> None:
    assert notifier.ensure() is None
    count = len(calls(fake_notifier_tools))
    assert notifier.ensure() is None
    assert len(calls(fake_notifier_tools)) == count  # current: nothing rebuilt


def test_a_new_icon_or_script_means_a_rebuild(
    fake_notifier_tools: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notifier.build()
    monkeypatch.setattr(notifier, "icon_bytes", lambda: b"the chosen artwork")
    assert notifier.state() is not None and "out of date" in notifier.state()  # type: ignore[operator]
    assert notifier.choice().kind == "osascript"
    assert notifier.ensure() is None and notifier.state() is None
    stamp = json.loads(
        (notifier.app_path() / "Contents" / "Resources" / notifier.STAMP).read_text("utf-8")
    )
    assert stamp["icon"] == hashlib.sha256(b"the chosen artwork").hexdigest()
    monkeypatch.setattr(notifier, "SCRIPT", notifier.SCRIPT + "-- changed\n")
    assert notifier.state() is not None
    monkeypatch.setattr(notifier, "APP_VERSION", "2")
    assert notifier.ensure() is None and notifier.state() is None


def test_a_damaged_app_is_not_used(fake_notifier_tools: Path) -> None:
    notifier.build()
    notifier.executable().unlink()
    assert "incomplete" in (notifier.state() or "")
    notifier.build()
    (notifier.app_path() / "Contents" / "Resources" / notifier.STAMP).write_text("not json")
    assert "out of date" in (notifier.state() or "")


@pytest.mark.parametrize("tool", ["osacompile", "sips", "iconutil", "codesign"])
def test_a_failed_build_leaves_no_app_and_says_why(
    tool: str, fake_notifier_tools: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_NOTIFIER_FAIL", tool)
    problem = notifier.ensure()
    assert problem is not None and f"{tool} failed: {tool}: something went wrong" in problem
    assert not notifier.app_path().exists()
    assert [p for p in notifier.app_path().parent.iterdir() if p.name.startswith(".")] == []
    assert "not built" in notifier.choice().reason


def test_a_failed_rebuild_keeps_falling_back(
    fake_notifier_tools: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notifier.build()
    monkeypatch.setattr(notifier, "icon_bytes", lambda: b"new artwork")
    monkeypatch.setenv("FAKE_NOTIFIER_FAIL", "codesign")
    assert notifier.ensure() is not None
    assert notifier.choice().kind == "osascript"


def test_a_missing_tool_means_no_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notifier, "CODESIGN", "/nonexistent/codesign")
    assert notifier.ensure() == "/nonexistent/codesign not found"
    assert not notifier.app_path().exists()


def test_a_tool_that_cannot_run_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    not_executable = tmp_path / "osacompile"
    not_executable.write_text("plain text")
    monkeypatch.setattr(notifier, "OSACOMPILE", str(not_executable))
    assert (notifier.ensure() or "").startswith("cannot run osacompile")


def test_an_osacompile_that_makes_nothing_is_caught(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notifier, "OSACOMPILE", shutil.which("true") or "/usr/bin/true")
    assert notifier.ensure() == "osacompile made no app bundle"


def test_remove_deletes_the_app_and_unregisters_it(fake_notifier_tools: Path) -> None:
    assert notifier.remove() is False
    built = notifier.build()
    assert notifier.remove() is True
    assert not built.exists()
    assert calls(fake_notifier_tools)[-1] == ["lsregister", "-u", str(built)]


def test_without_lsregister_registration_is_skipped(
    fake_notifier_tools: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notifier, "LSREGISTER", "/nonexistent/lsregister")
    notifier.build()
    assert "lsregister" not in tools(fake_notifier_tools)


# --- posting ----------------------------------------------------------------------------


def test_notify_posts_through_the_app_with_the_text_as_arguments(
    fake_notifier_tools: Path, fake_osascript: Path
) -> None:
    notifier.build()
    hostile = '"; do shell script "touch /tmp/pwned" --'
    assert st.notify(hostile) is None
    assert calls(fake_notifier_tools)[-1] == [
        "open", "-g", "-n", "-W", str(notifier.app_path()), "--args", hostile, "file-librarian",
    ]  # fmt: skip
    assert calls(fake_osascript) == []


def test_notify_falls_back_to_osascript_without_the_app(
    fake_notifier_tools: Path, fake_osascript: Path
) -> None:
    assert st.notify("3 files wait for review.") is None
    assert "open" not in tools(fake_notifier_tools)
    assert calls(fake_osascript)[0][-2:] == ["3 files wait for review.", "file-librarian"]


def test_notify_falls_back_to_osascript_when_the_app_cannot_be_launched(
    fake_notifier_tools: Path, fake_osascript: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    notifier.build()
    monkeypatch.setenv("FAKE_NOTIFIER_FAIL", "open")
    assert st.notify("x") is None
    assert len(calls(fake_osascript)) == 1


def test_post_reports_rather_than_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(notifier, "OPEN", "/nonexistent/open")
    assert notifier.post("x", "y")


def test_post_reports_an_exit_code_without_a_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(notifier, "OPEN", shutil.which("false") or "/usr/bin/false")
    assert notifier.post("x", "y") == "the notifier app exited with 1"


# --- the schedule -----------------------------------------------------------------------


def test_schedule_builds_the_app_and_introduces_it_once(
    config_file: Path, fake_notifier_tools: Path
) -> None:
    keychain.write(KEY)
    result = runner.invoke(app, ["schedule", "-c", str(config_file), "--no-check"])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert notifier.state() is None
    assert 'Notifications come from "file-librarian"' in result.stdout
    posts = [call for call in calls(fake_notifier_tools) if call[0] == "open"]
    assert [call[-2:] for call in posts] == [[notifier.INTRO_TEXT, "file-librarian"]]
    logs = logs_dir(load_config(config_file).database)
    assert notifier.introduced(logs)
    # A reinstall recreates the environment, and the app with it: no second introduction.
    notifier.remove()
    again = runner.invoke(app, ["schedule", "--refresh"])
    assert again.exit_code == 0, again.stderr
    assert notifier.state() is None
    assert len([call for call in calls(fake_notifier_tools) if call[0] == "open"]) == 1
    assert "Notifications come from" not in again.stdout


def test_schedule_without_notifications_builds_but_posts_nothing(
    tmp_path: Path, downloads: Path, fake_notifier_tools: Path
) -> None:
    path = write_config(tmp_path, downloads, PROVIDER, NOTIFY_OFF)
    keychain.write(KEY)
    result = runner.invoke(app, ["schedule", "-c", str(path), "--no-check"])
    assert result.exit_code == 0, result.stderr
    assert notifier.state() is None and "open" not in tools(fake_notifier_tools)


def test_an_intro_that_fails_is_tried_again_next_time(
    config_file: Path, fake_notifier_tools: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keychain.write(KEY)
    monkeypatch.setenv("FAKE_NOTIFIER_FAIL", "open")
    runner.invoke(app, ["schedule", "-c", str(config_file), "--no-check"])
    logs = logs_dir(load_config(config_file).database)
    assert not notifier.introduced(logs)
    monkeypatch.delenv("FAKE_NOTIFIER_FAIL")
    runner.invoke(app, ["schedule", "-c", str(config_file), "--no-check"])
    assert notifier.introduced(logs)


def test_introduce_needs_a_ready_app(tmp_path: Path) -> None:
    assert notifier.introduce(tmp_path) == "the notifier app is not ready"
    assert not notifier.introduced(tmp_path)


def test_a_failed_build_never_fails_the_schedule(
    config_file: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    keychain.write(KEY)
    monkeypatch.setenv("FAKE_NOTIFIER_FAIL", "osacompile")
    result = runner.invoke(app, ["schedule", "-c", str(config_file), "--no-check"])
    assert result.exit_code == 0, result.stderr
    assert "notifications will show as Script Editor (osacompile failed" in result.stderr
    assert scheduler.plist_path().exists()


def test_removing_the_schedule_removes_the_app(config_file: Path) -> None:
    keychain.write(KEY)
    runner.invoke(app, ["schedule", "-c", str(config_file), "--no-check"])
    assert notifier.app_path().exists()
    result = runner.invoke(app, ["schedule", "--remove"])
    assert "Schedule removed" in result.stdout
    assert not notifier.app_path().exists()


# --- status and doctor ------------------------------------------------------------------


def test_status_and_doctor_say_which_notifier_is_in_use(
    config_file: Path, fake_notifier_tools: Path
) -> None:
    keychain.write(KEY)
    runner.invoke(app, ["schedule", "-c", str(config_file), "--no-check"])
    status = runner.invoke(app, ["status", "-c", str(config_file)])
    assert 'Notifications: from the "file-librarian" app, with its own icon.' in status.stdout
    report = build_report(Options(config_path=config_file))
    assert "notifier: app (file-librarian)" in report
    notifier.remove()
    status = runner.invoke(app, ["status", "-c", str(config_file)])
    assert "Notifications: through osascript, shown as Script Editor (the notifier app is " in (
        status.stdout
    )
    report = build_report(Options(config_path=config_file))
    assert "notifier: osascript fallback (the notifier app is not built" in report


def test_status_says_nothing_about_notifications_when_they_are_off(
    tmp_path: Path, downloads: Path
) -> None:
    path = write_config(tmp_path, downloads, PROVIDER, NOTIFY_OFF)
    keychain.write(KEY)
    runner.invoke(app, ["schedule", "-c", str(path), "--no-check"])
    status = runner.invoke(app, ["status", "-c", str(path)])
    assert "Notifications:" not in status.stdout


# --- for real (opt-in) ------------------------------------------------------------------


REAL = {
    "OSACOMPILE": "/usr/bin/osacompile",
    "CODESIGN": "/usr/bin/codesign",
    "SIPS": "/usr/bin/sips",
    "ICONUTIL": "/usr/bin/iconutil",
}


@pytest.mark.notifier
@pytest.mark.skipif(
    os.environ.get("FLIB_TEST_NOTIFIER") != "1" or not Path(REAL["OSACOMPILE"]).exists(),
    reason="building the real notifier app is opt-in: FLIB_TEST_NOTIFIER=1 on macOS",
)
def test_the_real_tools_build_a_valid_app(monkeypatch: pytest.MonkeyPatch) -> None:
    for constant, path in REAL.items():
        monkeypatch.setattr(notifier, constant, path)
    # Not registered with Launch Services, never launched: no notification is posted.
    monkeypatch.setattr(notifier, "LSREGISTER", "/nonexistent/lsregister")
    monkeypatch.setattr(notifier, "OPEN", "/nonexistent/open")
    built = notifier.build()
    assert notifier.state() is None
    contents = built / "Contents"
    assert (contents / "MacOS" / "applet").is_file()
    assert (contents / "Resources" / "Scripts" / "main.scpt").is_file()
    assert (contents / "Resources" / "applet.icns").read_bytes()[:4] == b"icns"
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    assert info["CFBundleIdentifier"] == notifier.BUNDLE_ID and info["LSUIElement"] is True
    assert "CFBundleIconName" not in info
    verify = subprocess.run(  # noqa: S603
        [REAL["CODESIGN"], "--verify", "--deep", "--strict", "--verbose=2", str(built)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert verify.returncode == 0, verify.stderr
    shown = subprocess.run(  # noqa: S603
        [REAL["CODESIGN"], "-dv", str(built)], capture_output=True, text=True, check=False
    )
    assert f"Identifier={notifier.BUNDLE_ID}" in shown.stderr
    assert "Signature=adhoc" in shown.stderr

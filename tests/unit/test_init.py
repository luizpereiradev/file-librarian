"""``flib init``: the guided first run (RF-38). Never moves a real file."""

from __future__ import annotations

import hashlib
import os
import plistlib
import stat
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from file_librarian import hashing, keychain, onboarding, scheduler
from file_librarian.cli import app
from file_librarian.config import default_config_path, default_database_path, load_config
from file_librarian.providers import claude_code
from file_librarian.providers.fake import FakeProvider
from tests._helpers import OLD, FakeClaude, answer, fake_config, planned, put, write_config

runner = CliRunner()
KEY = "sk-ant-" + "init-" + "k" * 24


@pytest.fixture(autouse=True)
def _english_locale(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("LC_ALL", "LC_MESSAGES"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")


def snapshot(folder: Path) -> dict[str, tuple[str, int, int]]:
    """Every entry under ``folder``: content hash (files), size and mtime in ns."""
    seen: dict[str, tuple[str, int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(folder):
        for name in [*dirnames, *filenames]:
            path = Path(dirpath, name)
            st = os.lstat(path)
            digest = "" if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()
            seen[str(path.relative_to(folder))] = (digest, st.st_size, st.st_mtime_ns)
    return seen


def fill(downloads: Path) -> None:
    put(downloads, "invoice.txt", b"electricity invoice october", mtime=OLD + 50)
    put(downloads, "notes.md", b"meeting notes", mtime=OLD + 40)
    put(downloads, "project/readme.txt", b"a small project", mtime=OLD + 30)
    os.utime(downloads / "project", (OLD + 30, OLD + 30))
    put(downloads, ".hidden", b"hidden")
    put(downloads, "id_rsa", b"not really a key")
    put(downloads, "empty.txt", b"")
    put(downloads, "movie.crdownload", b"partial")
    big = downloads / "big.bin"
    with big.open("wb") as fh:
        fh.truncate(onboarding.MAX_ITEM_BYTES + 1)


def classifications(fake_claude: FakeClaude) -> list[dict[str, object]]:
    return [c for c in fake_claude.calls() if c["argv"][:2] != ["auth", "status"]]


def test_defaults_all_the_way_give_a_working_setup(
    downloads: Path, fake_claude: FakeClaude, fake_launchd: Path
) -> None:
    fill(downloads)
    before = snapshot(downloads)

    result = runner.invoke(app, ["init", "--yes"])

    assert result.exit_code == 0, result.stdout + result.stderr
    path = default_config_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    cfg = load_config(path)
    assert cfg.language == "en"
    assert cfg.inputs == [downloads]
    assert cfg.output_root == downloads / "Organized"
    assert cfg.review_folder == "_Review"
    # Claude Code is installed and logged in (the fake), so it is the default.
    assert (cfg.provider.name, cfg.provider.model) == ("claude-code", "haiku")
    assert cfg.vision.enabled is False
    assert 'inputs:\n  - "~/Downloads"' in path.read_text(encoding="utf-8")
    # The fake claude is not on launchd's PATH, so init pins it for the daily run.
    assert cfg.provider.command == fake_claude.executable
    assert "will not find" not in result.stderr

    # The trial classified copies of the three eligible items, newest first.
    assert "Trial on 3 copies" in result.stdout
    sent = classifications(fake_claude)
    assert len(sent) == 3
    assert all(Path(str(c["cwd"])).name.startswith("flib-claude-") for c in sent)
    assert "Summary: 3 to categories" in result.stdout
    assert "Estimated cost of the trial: US$ 0.0369" in result.stdout

    # Nothing real was touched: the files, their dates, the real index.
    assert snapshot(downloads) == before
    assert not (downloads / "Organized").exists()
    assert not default_database_path().exists()

    # The daily run was installed and its access checked.
    data = plistlib.loads(scheduler.plist_path().read_bytes())
    assert data["StartCalendarInterval"] == {"Hour": 12, "Minute": 30}
    assert data["EnvironmentVariables"]["FLIB_CONFIG"] == str(path.resolve())
    assert "Access OK" in result.stdout
    assert "./scripts/install.sh --with-mcp" in result.stdout


def test_running_it_again_keeps_the_config(downloads: Path, tmp_path: Path) -> None:
    path = write_config(tmp_path, downloads, extra="# my own settings\n")
    original = path.read_text(encoding="utf-8")

    result = runner.invoke(app, ["init", "--yes", "-c", str(path), "--sample", "0"])

    assert result.exit_code == 0, result.stderr
    assert "Replace it? [y/N]: n" in result.stdout
    assert f"Keeping {path}" in result.stdout
    assert path.read_text(encoding="utf-8") == original
    # A second run finds the schedule the first one installed.
    again = runner.invoke(app, ["init", "--yes", "-c", str(path), "--sample", "0"])
    assert "A daily run is already scheduled at 12:30" in again.stdout


def test_overwrite_when_confirmed(downloads: Path, tmp_path: Path) -> None:
    path = write_config(tmp_path, downloads, extra="# my own settings\n")
    # Replace? y, then Enter for every question; decline the schedule.
    answers = "y\n" + "\n" * 5 + "n\n"
    result = runner.invoke(app, ["init", "-c", str(path), "--sample", "0"], input=answers)
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "my own settings" not in path.read_text(encoding="utf-8")
    assert load_config(path).provider.name == "claude-code"
    assert "You can schedule it later" in result.stdout
    assert not scheduler.plist_path().exists()


def test_force_overwrites_without_asking(downloads: Path, tmp_path: Path) -> None:
    path = write_config(tmp_path, downloads, extra="# my own settings\n")
    result = runner.invoke(app, ["init", "--yes", "--force", "-c", str(path), "--sample", "0"])
    assert result.exit_code == 0, result.stderr
    assert "Replace it?" not in result.stdout
    assert "my own settings" not in path.read_text(encoding="utf-8")


def test_anthropic_with_the_key_prompt_and_vision(
    downloads: Path, tmp_path: Path, fake_claude: FakeClaude
) -> None:
    fake_claude.log_out()  # Claude Code is not ready, so the API is the default
    path = tmp_path / "config.yaml"
    # language, folder, output, review, provider: Enter; vision: y;
    # store a key: Enter (yes), the key; schedule: n.
    answers = "\n\n\n\n\ny\n\n" + KEY + "\nn\n"
    result = runner.invoke(app, ["init", "-c", str(path), "--sample", "0"], input=answers)

    assert result.exit_code == 0, result.stdout + result.stderr
    cfg = load_config(path)
    assert (cfg.provider.name, cfg.provider.model) == ("anthropic", "claude-haiku-5-5")
    assert cfg.vision.enabled is True
    assert "found, not logged in" in result.stdout
    assert "EXIF and GPS" in result.stdout and "US$0.0005 per image" in result.stdout
    assert keychain.read() == KEY
    assert KEY not in result.stdout + result.stderr  # never shown


def test_anthropic_without_a_key_under_yes(
    downloads: Path, tmp_path: Path, fake_claude: FakeClaude
) -> None:
    fake_claude.log_out()
    path = tmp_path / "config.yaml"
    result = runner.invoke(app, ["init", "--yes", "-c", str(path)])
    assert result.exit_code == 0, result.stderr
    assert load_config(path).provider.name == "anthropic"
    assert "run `flib key set`" in result.stderr
    assert "Skipping the trial" in result.stdout
    assert "no API key found; the daily run will fail" in result.stderr


def test_anthropic_with_a_key_already_in_the_keychain_runs_the_trial(
    downloads: Path, tmp_path: Path, fake_claude: FakeClaude, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_claude.log_out()
    keychain.write(KEY)
    put(downloads, "invoice.txt", b"electricity invoice")
    seen: list[str] = []

    def provider(config: object) -> object:
        from file_librarian.providers.fake import FakeProvider

        seen.append("called")
        return FakeProvider(echo=True)

    monkeypatch.setattr("file_librarian.cli.get_provider", provider)
    path = tmp_path / "config.yaml"
    result = runner.invoke(app, ["init", "--yes", "-c", str(path)])
    assert result.exit_code == 0, result.stderr
    assert "API key: found in the keychain" in result.stdout
    assert KEY not in result.stdout
    assert seen and "Trial on 1 copies" in result.stdout


def test_claude_code_not_found(
    downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(claude_code, "find_claude", lambda *_a, **_k: None)
    path = tmp_path / "config.yaml"
    # Pick claude-code anyway.
    answers = "\n\n\n\nclaude-code\nn\n"
    result = runner.invoke(app, ["init", "-c", str(path)], input=answers)
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "claude-code    your Claude subscription through Claude Code (not found)" in (
        result.stdout
    )
    assert "Claude Code was not found" in result.stderr
    assert "Skipping the trial" in result.stdout


def test_claude_code_chosen_but_logged_out(
    downloads: Path, tmp_path: Path, fake_claude: FakeClaude
) -> None:
    path = write_config(tmp_path, downloads, "{name: claude-code, model: haiku}")
    fake_claude.log_out()
    result = runner.invoke(app, ["init", "--yes", "-c", str(path), "--sample", "0"])
    assert "not logged in" in result.stderr


def test_empty_downloads(downloads: Path, tmp_path: Path, fake_claude: FakeClaude) -> None:
    result = runner.invoke(app, ["init", "--yes", "-c", str(tmp_path / "c.yaml")])
    assert result.exit_code == 0, result.stderr
    assert "No files to try it on in ~/Downloads" in result.stdout
    assert classifications(fake_claude) == []


def test_tiny_downloads_and_a_declined_trial(
    downloads: Path, tmp_path: Path, fake_claude: FakeClaude
) -> None:
    put(downloads, "only.txt", b"just one")
    path = tmp_path / "c.yaml"
    answers = "\n" * 5 + "n\n" + "n\n"  # questions, decline the trial, decline the schedule
    result = runner.invoke(app, ["init", "-c", str(path)], input=answers)
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "Try it on copies of your 1 most recent item(s)?" in result.stdout
    assert classifications(fake_claude) == []


def test_a_failed_trial_does_not_stop_the_setup(
    downloads: Path, tmp_path: Path, fake_claude: FakeClaude
) -> None:
    put(downloads, "only.txt", b"just one")
    fake_claude.reply(stderr="Invalid API key · Please run /login", exit=1)
    result = runner.invoke(app, ["init", "--yes", "-c", str(tmp_path / "c.yaml")])
    assert result.exit_code == 0, result.stderr
    assert "the trial stopped" in result.stderr
    assert "Daily run scheduled" in result.stdout


def test_missing_folder_is_asked_again(downloads: Path, tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    answers = "\n~/Nowhere\n\n\n\n\nn\n"
    result = runner.invoke(app, ["init", "-c", str(path), "--sample", "0"], input=answers)
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "is not a folder" in result.stderr
    assert load_config(path).inputs == [downloads]


def test_invalid_folders_are_asked_again(downloads: Path, tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    # An output root that contains the input is rejected; the second try is the default.
    answers = "\n\n~\n\n" + "\n\n\n" + "\n" + "n\n"
    result = runner.invoke(app, ["init", "-c", str(path), "--sample", "0"], input=answers)
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "Let's try the folders again" in result.stderr
    assert load_config(path).output_root == downloads / "Organized"


def test_yes_without_a_downloads_folder_fails(tmp_path: Path) -> None:
    result = runner.invoke(app, ["init", "--yes", "-c", str(tmp_path / "c.yaml")])
    assert result.exit_code == 1
    assert "is not a folder" in result.stderr
    assert not (tmp_path / "c.yaml").exists()


def test_yes_with_invalid_folders_fails(
    downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(onboarding, "default_output_root", lambda folder, _lang: folder.parent)
    result = runner.invoke(app, ["init", "--yes", "-c", str(tmp_path / "c.yaml")])
    assert result.exit_code == 1
    assert "must not be inside output_root" in result.stderr


def test_portuguese_locale_picks_portuguese_defaults(
    downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("LANG", "pt_BR.UTF-8")
    path = tmp_path / "c.yaml"
    result = runner.invoke(app, ["init", "--yes", "-c", str(path), "--sample", "0"])
    assert result.exit_code == 0, result.stderr
    cfg = load_config(path)
    assert cfg.language == "pt-BR"
    assert cfg.output_root == downloads / "Organizado"
    assert cfg.review_folder == "_Revisar"


def test_schedule_errors_are_reported(
    downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FAKE_LAUNCHD_FAIL", "bootstrap")
    result = runner.invoke(app, ["init", "--yes", "-c", str(tmp_path / "c.yaml"), "--sample", "0"])
    assert result.exit_code == 0, result.stderr
    assert "could not schedule the daily run" in result.stderr


def test_unreadable_schedule_is_reported(
    downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken() -> scheduler.Status:
        raise scheduler.ScheduleError("launchctl is gone")

    monkeypatch.setattr(scheduler, "status", broken)
    result = runner.invoke(app, ["init", "--yes", "-c", str(tmp_path / "c.yaml"), "--sample", "0"])
    assert "cannot read the schedule: launchctl is gone" in result.stderr


def test_planned_provider_in_an_existing_config(
    downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    planned(monkeypatch, "fake")
    path = write_config(tmp_path, downloads, "{name: fake, model: m}")
    result = runner.invoke(app, ["init", "--yes", "-c", str(path)])
    assert "provider 'fake' is not available yet" in result.stderr


# --- onboarding ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({}, "en"),
        ({"LANG": "pt_BR.UTF-8"}, "pt-BR"),
        ({"LANG": "pt_PT.UTF-8"}, "pt-BR"),
        ({"LANG": "de_DE.UTF-8"}, "en"),
        ({"LC_ALL": "en_US.UTF-8", "LANG": "pt_BR.UTF-8"}, "en"),
    ],
)
def test_detect_language(env: dict[str, str], expected: str) -> None:
    assert onboarding.detect_language(env) == expected


def test_rendered_config_is_valid_and_quotes_odd_names(tmp_path: Path) -> None:
    answers = onboarding.Answers(
        "en", tmp_path / "in: box", tmp_path / "out", 'To "review"', "anthropic", vision=True
    )
    (tmp_path / "in: box").mkdir()
    cfg = onboarding.check_answers(answers)
    assert cfg.inputs == [tmp_path / "in: box"]
    assert cfg.review_folder == 'To "review"'
    assert cfg.vision.enabled is True
    assert yaml.safe_load(onboarding.render_config(answers))["provider"]["name"] == "anthropic"


def test_write_config_is_private_and_atomic(tmp_path: Path) -> None:
    path = tmp_path / "deep" / "config.yaml"
    onboarding.write_config(path, "language: en\n")
    onboarding.write_config(path, "language: pt-BR\n")
    assert path.read_text(encoding="utf-8") == "language: pt-BR\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert [p.name for p in path.parent.iterdir()] == ["config.yaml"]


def test_pick_sample_takes_the_newest_eligible_items(downloads: Path) -> None:
    fill(downloads)
    cfg = fake_config(downloads, never_send=["*.md"])
    assert onboarding.pick_sample(cfg, 10) == [downloads / "invoice.txt", downloads / "project"]
    assert onboarding.pick_sample(cfg, 1) == [downloads / "invoice.txt"]
    assert onboarding.pick_sample(fake_config(downloads, folders="ignore"), 10) == [
        downloads / "invoice.txt",
        downloads / "notes.md",
    ]


def test_pick_sample_skips_flib_folders_cloud_only_and_big_units(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = fake_config(downloads)
    put(cfg.output_root, "Finance/old.pdf", b"organized earlier")
    for i in range(onboarding.MAX_UNIT_ENTRIES + 1):
        put(downloads, f"many/{i}.txt", b"x")
    put(downloads, "icloud.pdf", b"placeholder")
    put(downloads, "album/photo.jpg", b"placeholder")
    put(downloads, "link-target.txt", b"real")
    (downloads / "link.txt").symlink_to(downloads / "link-target.txt")
    cloud = {os.lstat(downloads / name).st_ino for name in ("icloud.pdf", "album/photo.jpg")}
    real = hashing.is_dataless
    monkeypatch.setattr(hashing, "is_dataless", lambda st: st.st_ino in cloud or real(st))
    assert onboarding.pick_sample(cfg, 10) == [downloads / "link-target.txt"]


def test_pick_sample_ignores_missing_folders(tmp_path: Path) -> None:
    cfg = fake_config(tmp_path / "gone")
    assert onboarding.pick_sample(cfg, 10) == []


def test_copy_sample_keeps_dates_and_skips_name_clashes(downloads: Path, tmp_path: Path) -> None:
    a = put(downloads, "a.txt", b"one")
    other = tmp_path / "other"
    b = put(other, "a.txt", b"two")
    unit = put(downloads, "proj/x.txt", b"x").parent
    into = tmp_path / "into"
    into.mkdir()
    copies = onboarding.copy_sample([a, b, unit], into)
    assert copies == [into / "a.txt", into / "proj"]
    assert (into / "a.txt").read_bytes() == b"one"
    assert (into / "a.txt").stat().st_mtime == a.stat().st_mtime


def test_copy_sample_skips_unreadable_items(downloads: Path, tmp_path: Path) -> None:
    gone = downloads / "vanished.txt"
    into = tmp_path / "into"
    into.mkdir()
    assert onboarding.copy_sample([gone], into) == []
    assert list(into.iterdir()) == []


def test_display_path(fake_home: Path) -> None:
    assert onboarding.display_path(fake_home / "Downloads") == "~/Downloads"
    assert onboarding.display_path(fake_home) == str(fake_home)
    assert onboarding.display_path(Path("/tmp/x")) == "/tmp/x"  # noqa: S108


def test_an_unknown_choice_is_asked_again(downloads: Path, tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    answers = "fr\n\n" + "\n" * 4 + "n\n"
    result = runner.invoke(app, ["init", "-c", str(path), "--sample", "0"], input=answers)
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "choose one of en, pt-BR" in result.stderr
    assert load_config(path).language == "en"


def test_claude_found_by_the_daily_run_is_not_pinned(
    downloads: Path, tmp_path: Path, fake_claude: FakeClaude, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(claude_code, "INSTALL_LOCATIONS", (str(fake_claude.executable),))
    path = tmp_path / "c.yaml"
    result = runner.invoke(app, ["init", "--yes", "-c", str(path), "--sample", "0"])
    assert result.exit_code == 0, result.stderr
    assert load_config(path).provider.command is None
    assert "command:" not in path.read_text(encoding="utf-8")


def test_trial_summary_shows_review_kept_and_moved_items(
    downloads: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(downloads, "unsure.txt", b"something vague", mtime=OLD + 3)
    put(downloads, "secret.txt", b"-----BEGIN PRIVATE KEY-----\nabc\n", mtime=OLD + 2)
    put(downloads, "bill.txt", b"electricity bill", mtime=OLD + 1)
    fake = FakeProvider(
        {"unsure.txt": answer("Finance", confidence=0.3), "bill.txt": answer("Finance")}
    )
    monkeypatch.setattr("file_librarian.cli.get_provider", lambda _cfg: fake)
    path = write_config(tmp_path, downloads)
    result = runner.invoke(app, ["init", "--yes", "-c", str(path)])
    assert result.exit_code == 0, result.stderr
    assert "  review  unsure.txt  ->  _Review/unsure.txt" in result.stdout
    assert "  keep    secret.txt" in result.stdout
    assert "  move    bill.txt  ->  Finance/bill.txt" in result.stdout
    summary = "Summary: 1 to categories, 1 to _Review for you to decide, 1 would stay."
    assert summary in result.stdout
    assert "  Finance: 1" in result.stdout


def test_trial_stops_at_the_cost_cap(downloads: Path, tmp_path: Path) -> None:
    for i in range(3):
        put(downloads, f"file{i}.txt", f"content {i}".encode())
    path = write_config(
        tmp_path,
        downloads,
        "{name: claude-code, model: haiku}",
        "limits: {max_cost_usd_per_run: 0.02}\n",
    )
    result = runner.invoke(app, ["init", "--yes", "-c", str(path)])
    assert result.exit_code == 0, result.stderr
    assert "not tried: the cost cap was reached" in result.stdout


def test_a_keychain_failure_is_reported(
    downloads: Path, tmp_path: Path, fake_claude: FakeClaude, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_claude.log_out()
    monkeypatch.setattr(keychain, "SECURITY", str(tmp_path / "no-security"))
    path = write_config(tmp_path, downloads, "{name: anthropic, model: m}")
    # Keep the config (Enter), store a key (Enter), the key, then no schedule.
    answers = "\n\n" + KEY + "\nn\n"
    result = runner.invoke(app, ["init", "-c", str(path)], input=answers)
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "cannot run" in result.stderr
    assert "run `flib key set`" in result.stderr
    assert KEY not in result.stdout + result.stderr

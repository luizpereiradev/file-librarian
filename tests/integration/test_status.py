"""``flib status`` and the periodic summary after a scheduled run (RF-40, #38)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from typer.testing import CliRunner

from file_librarian import pipeline, scheduler
from file_librarian import status as st
from file_librarian.cli import app
from file_librarian.config import Config, load_config
from file_librarian.db.store import Store, load_migrations
from file_librarian.doctor import Options, build_report
from file_librarian.lock import instance_lock
from file_librarian.models import RunTrigger
from file_librarian.providers.fake import FakeProvider
from file_librarian.runlog import logs_dir, write_log
from tests._helpers import answer, fake_config, put, write_config

pytestmark = pytest.mark.integration

runner = CliRunner()

# Names a notification or `flib status` must never show.
NAMES = ("unsure-one", "unsure-two", "odd-recipe", "broken-scan", "id_rsa", "electricity")


def populate(downloads: Path, cfg: Config) -> None:
    """Organized, in review (two reasons), failed and sensitive files, filed 10 days ago."""
    answers = {
        "electricity.pdf": answer("Finance", confidence=0.9),
        "unsure-one.pdf": answer("Finance", confidence=0.3),
        "unsure-two.pdf": answer("Finance", confidence=0.4),
        "odd-recipe.pdf": answer("Recipes", confidence=0.9),
    }
    for name in [*answers, "broken-scan.pdf"]:
        put(downloads, name, name.encode())
    put(downloads, "id_rsa", b"key material")
    pipeline.run(cfg, FakeProvider(answers), simulate=False)
    age(cfg, days=10)


def age(cfg: Config, *, days: float) -> None:
    """Pretend every file was processed ``days`` ago."""
    when = (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")
    with Store.open(cfg.database) as store:
        store.conn.execute("UPDATE files SET processed_at = ?", (when,))


def calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def with_summary(cfg: Config, **settings: object) -> Config:
    return cfg.model_copy(update={"summary": cfg.summary.model_copy(update=settings)})


# --- flib status ---------------------------------------------------------------------


def test_status_before_the_first_run_creates_nothing(tmp_path: Path, downloads: Path) -> None:
    path = write_config(tmp_path, downloads)
    result = runner.invoke(app, ["status", "-c", str(path)])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "No run yet" in result.stdout
    assert "Next run: none scheduled" in result.stdout
    assert not load_config(path).database.exists()

    data = json.loads(runner.invoke(app, ["status", "--json", "-c", str(path)]).stdout)
    assert data["database_exists"] is False
    assert data["review"]["total"] == 0 and data["last_run"] is None
    assert not load_config(path).database.exists()


def test_status_shows_what_waits_without_naming_files(tmp_path: Path, downloads: Path) -> None:
    path = write_config(tmp_path, downloads)
    populate(downloads, load_config(path))

    result = runner.invoke(app, ["status", "-c", str(path)])

    assert result.exit_code == 0, result.stdout + result.stderr
    out = result.stdout
    assert "Review: 3 files in _Review, the oldest for 10 days" in out
    assert "    2  the AI was not sure enough; it suggested Finance" in out
    assert "    1  the AI chose a category that is not configured: Recipes" in out
    assert "Failed: 1 file" in out
    assert "    1  classification failed (1 of 3 attempts; the next run tries again" in out
    assert "Left in place as sensitive: 1 file" in out
    assert "Last run: " in out and "by hand: 1 organized, 3 to review, 1 failed" in out
    assert "Next run: none scheduled" in out
    assert "This month: US$ 0.0000" in out
    for name in NAMES:
        assert name not in out


def test_status_json(tmp_path: Path, downloads: Path) -> None:
    path = write_config(tmp_path, downloads)
    populate(downloads, load_config(path))

    result = runner.invoke(app, ["status", "--json", "-c", str(path)])

    assert result.exit_code == 0, result.stdout + result.stderr
    data = json.loads(result.stdout)
    assert data["review"]["total"] == 3 and data["review"]["oldest_days"] == 10
    groups = {(g["reason"], g["suggested_category"]): g["count"] for g in data["review"]["groups"]}
    assert groups == {("low_confidence", "Finance"): 2, ("unknown_category", "Recipes"): 1}
    failed = data["failed"]["groups"]
    assert data["failed"]["total"] == 1
    assert failed == [
        {
            "error": "classification failed",
            "count": 1,
            "attempts": 1,
            "retried": True,
            "oldest_days": 10,
        }
    ]
    assert data["sensitive"] == 1
    assert data["last_run"]["trigger"] == "manual" and data["last_run"]["files_review"] == 3
    assert data["month"] == {"cost_usd": 0.0, "runs_with_unknown_cost": 0}
    assert data["summary"] == {"notify": True, "every_days": 7, "min_age_days": 2, "last_at": None}
    for name in NAMES:
        assert name not in result.stdout


def test_given_up_failures_say_so(downloads: Path) -> None:
    cfg = fake_config(downloads)
    populate(downloads, cfg)
    with Store.open(cfg.database) as store:
        store.conn.execute("UPDATE files SET attempts = 3 WHERE status = 'failed'")
    lines = st.render(st.collect(cfg), cfg)
    assert "    1  classification failed (gave up after 3 attempts; for 10 days)" in lines


def test_runs_without_a_recorded_cost_are_counted_neutrally(downloads: Path) -> None:
    # The index cannot tell unknown prices from a run recorded before costs were
    # kept (migration 0010), so the wording names neither cause.
    cfg = fake_config(downloads)
    populate(downloads, cfg)
    with Store.open(cfg.database) as store:
        store.conn.execute("UPDATE runs SET cost_usd = 0.5")
        store.conn.execute(
            """INSERT INTO runs (mode, trigger, started_at, finished_at, cost_usd)
               VALUES ('run', 'schedule', ?, ?, NULL)""",
            (datetime.now(UTC).isoformat(), datetime.now(UTC).isoformat()),
        )
    state = st.collect(cfg)
    lines = st.render(state, cfg)
    assert "This month: US$ 0.5000 (plus 1 run(s) without a recorded cost)" in lines
    assert any(
        line.startswith("Last run: ") and line.endswith("; no cost recorded") for line in lines
    )
    assert not any("unknown prices" in line for line in lines)
    month = json.loads(st.to_json(state, cfg))["month"]
    assert month == {"cost_usd": 0.5, "runs_with_unknown_cost": 1}  # the key stays


def test_status_takes_no_lock_and_writes_nothing(downloads: Path) -> None:
    cfg = fake_config(downloads)
    populate(downloads, cfg)
    before = cfg.database.read_bytes()
    with instance_lock(cfg.database):  # a run is going on
        state = st.collect(cfg)
    assert state.review_total == 3
    assert cfg.database.read_bytes() == before


def test_status_counts_the_last_runs_unreadable_and_cloud_only_skips(downloads: Path) -> None:
    cfg = fake_config(downloads)
    populate(downloads, cfg)
    run_id = st.collect(cfg).last_run.id  # type: ignore[union-attr]
    write_log(
        cfg.database,
        run_id,
        [
            "  skip    a.pdf  (unreadable)",
            "  skip    b.pdf  (unreadable: permission denied)",
            "  skip    c folder  (in iCloud only, not downloaded)",
            "  skip    d.pdf  (still changing)",
        ],
    )
    state = st.collect(cfg)
    assert state.skipped == {
        "unreadable": 2,
        "cloud_only": 1,
        "not_where_left": 0,
        "locked": 0,
        "too_recent": 0,
        "input_unreadable": 0,
    }
    assert "  skipped: 2 unreadable, 1 only in iCloud (see its log)" in st.render(state, cfg)


def test_status_counts_files_skipped_while_the_mac_was_locked(downloads: Path) -> None:
    cfg = fake_config(downloads)
    populate(downloads, cfg)
    run_id = st.collect(cfg).last_run.id  # type: ignore[union-attr]
    write_log(
        cfg.database,
        run_id,
        [
            "  skip    photo.jpeg  (protected by macOS while the Mac is locked)",
            "  skip    scan.pdf  (protected by macOS while the Mac is locked)",
        ],
    )
    state = st.collect(cfg)
    assert state.skipped is not None and state.skipped["locked"] == 2
    lines = st.render(state, cfg)
    assert "  skipped: 2 protected while the Mac was locked (see its log)" in lines
    assert (
        "  2 files could only be read while the Mac is unlocked (macOS Data Protection); "
        "the scheduled run waits for the next unlock, or run `flib run` now."
    ) in lines
    assert json.loads(st.to_json(state, cfg))["last_run"]["skipped"]["locked"] == 2

    no_wait = fake_config(downloads, schedule={"wait_for_unlock_hours": 0})
    assert (
        "  2 files could only be read while the Mac is unlocked (macOS Data Protection); "
        "run `flib run` now."
    ) in st.render(state, no_wait)


def test_without_a_log_the_skips_are_unknown(downloads: Path) -> None:
    cfg = fake_config(downloads)
    populate(downloads, cfg)
    assert st.collect(cfg).skipped is None


def test_next_run_comes_from_the_installed_agent(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = fake_config(downloads)
    local = datetime(2026, 3, 10, 9, 0).astimezone()
    installed = scheduler.Status(True, True, "12:30", [])
    monkeypatch.setattr("file_librarian.scheduler.status", lambda: installed)
    assert st.next_run(local).at == local.replace(hour=12, minute=30)
    late = local.replace(hour=13)
    assert st.next_run(late).at == (late + timedelta(days=1)).replace(hour=12, minute=30)
    line = st.render(st.collect(cfg, now=local), cfg)[-1]
    assert line == "Next run: 2026-03-10 12:30 (daily at 12:30)"

    unloaded = scheduler.Status(True, False, "12:30", [])
    monkeypatch.setattr("file_librarian.scheduler.status", lambda: unloaded)
    assert "installed but not loaded" in st.render(st.collect(cfg), cfg)[-1]

    def broken() -> scheduler.Status:
        raise scheduler.ScheduleError("cannot run launchctl")

    monkeypatch.setattr("file_librarian.scheduler.status", broken)
    assert st.render(st.collect(cfg), cfg)[-1] == "Next run: unknown (cannot run launchctl)"


def test_status_refuses_an_index_from_an_older_or_newer_flib(
    tmp_path: Path, downloads: Path
) -> None:
    path = write_config(tmp_path, downloads)
    database = load_config(path).database
    Store.open(database).close()
    for version in (9, len(load_migrations()) + 1):
        conn = sqlite3.connect(database)
        conn.execute(f"PRAGMA user_version = {version}")
        conn.close()
        result = runner.invoke(app, ["status", "-c", str(path)])
        assert result.exit_code == 1
        assert "cannot read the index" in result.stderr


def test_status_reads_an_index_not_yet_migrated_to_the_meta_table(downloads: Path) -> None:
    cfg = fake_config(downloads)
    populate(downloads, cfg)
    conn = sqlite3.connect(cfg.database)
    conn.execute("DROP TABLE meta")
    conn.execute("PRAGMA user_version = 10")
    conn.close()
    state = st.collect(cfg)
    assert state.review_total == 3 and state.last_summary is None


def test_doctor_includes_the_status_without_names(downloads: Path, tmp_path: Path) -> None:
    path = write_config(tmp_path, downloads)
    populate(downloads, load_config(path))
    report = build_report(Options(config_path=path))
    assert "== Status ==" in report
    assert "Review: 3 files in _Review" in report
    assert "classification failed" in report
    # A suggestion outside the configured categories is the model's text: hidden.
    assert "not configured: <other>" in report
    for name in NAMES:
        assert name not in report


# --- the periodic summary --------------------------------------------------------------


def test_summary_comes_once_per_period(downloads: Path, fake_osascript: Path) -> None:
    cfg = fake_config(downloads)
    populate(downloads, cfg)
    start = datetime.now(UTC)

    first = st.after_scheduled_run(cfg, now=start)
    assert first == ["Summary: 3 files wait for review, 1 failed. Run `flib status` for details."]
    assert len(calls(fake_osascript)) == 1
    for days in (0, 1, 6):
        assert st.after_scheduled_run(cfg, now=start + timedelta(days=days)) == []
    assert len(calls(fake_osascript)) == 1
    assert st.after_scheduled_run(cfg, now=start + timedelta(days=7)) == first
    assert len(calls(fake_osascript)) == 2
    assert st.collect(cfg).last_summary is not None


def test_summary_period_is_configurable(downloads: Path, fake_osascript: Path) -> None:
    cfg = with_summary(fake_config(downloads), every_days=1)
    populate(downloads, cfg)
    start = datetime.now(UTC)
    assert st.after_scheduled_run(cfg, now=start)
    assert st.after_scheduled_run(cfg, now=start + timedelta(days=1))
    assert len(calls(fake_osascript)) == 2


def test_summary_waits_until_a_file_is_old_enough(downloads: Path, fake_osascript: Path) -> None:
    cfg = fake_config(downloads)
    populate(downloads, cfg)
    age(cfg, days=1)  # min_age_days is 2
    assert st.after_scheduled_run(cfg) == []
    assert calls(fake_osascript) == []
    with Store.open(cfg.database) as store:
        assert store.meta(st.SUMMARY_KEY) is None  # nothing recorded: the next run may tell
    assert st.after_scheduled_run(with_summary(cfg, min_age_days=0))
    assert len(calls(fake_osascript)) == 1


def test_no_summary_when_nothing_waits(downloads: Path, fake_osascript: Path) -> None:
    cfg = fake_config(downloads)
    put(downloads, "electricity.pdf")
    pipeline.run(
        cfg, FakeProvider({"electricity.pdf": answer("Finance", confidence=0.9)}), simulate=False
    )
    age(cfg, days=30)
    assert st.after_scheduled_run(cfg) == []
    assert calls(fake_osascript) == []


def test_notify_off_writes_only_the_log_line(downloads: Path, fake_osascript: Path) -> None:
    cfg = with_summary(fake_config(downloads), notify=False)
    populate(downloads, cfg)
    assert st.after_scheduled_run(cfg) == [
        "Summary: 3 files wait for review, 1 failed. Run `flib status` for details."
    ]
    assert calls(fake_osascript) == []


def test_the_notification_text_is_passed_as_arguments(fake_osascript: Path) -> None:
    hostile = 'a" & (do shell script "touch /tmp/x") & "'
    assert st.notify(hostile) is None
    assert calls(fake_osascript) == [
        [
            "-e",
            "on run argv",
            "-e",
            "display notification (item 1 of argv) with title (item 2 of argv)",
            "-e",
            "end run",
            "--",
            hostile,
            "file-librarian",
        ]
    ]


def test_summary_text_wording() -> None:
    assert st.summary_text(1, 0) == "1 file waits for review. Run `flib status` for details."
    assert st.summary_text(0, 2) == "2 failed. Run `flib status` for details."


def test_files_skipped_while_locked_are_counted_in_the_summary(
    downloads: Path, fake_osascript: Path
) -> None:
    cfg = fake_config(downloads)
    put(downloads, "electricity.pdf")
    pipeline.run(
        cfg, FakeProvider({"electricity.pdf": answer("Finance", confidence=0.9)}), simulate=False
    )
    # The scheduled run waits for the next unlock: that alone is no reason to tell.
    assert st.after_scheduled_run(cfg, locked=2) == []
    no_wait = fake_config(downloads, schedule={"wait_for_unlock_hours": 0})
    assert st.after_scheduled_run(no_wait, locked=2) == [
        "Summary: 2 could only be read while the Mac is unlocked. Run `flib status` for details.",
        "2 files could only be read while the Mac is unlocked (macOS Data Protection); "
        "run `flib run` now.",
    ]
    (argv,) = calls(fake_osascript)
    assert "photo" not in " ".join(argv)


def test_a_due_summary_also_counts_files_skipped_while_locked(
    downloads: Path, fake_osascript: Path
) -> None:
    cfg = with_summary(fake_config(downloads), notify=False)
    populate(downloads, cfg)
    assert st.after_scheduled_run(cfg, locked=1) == [
        "Summary: 3 files wait for review, 1 failed, 1 could only be read while the Mac is "
        "unlocked. Run `flib status` for details.",
        "1 file could only be read while the Mac is unlocked (macOS Data Protection); "
        "the scheduled run waits for the next unlock, or run `flib run` now.",
    ]


def test_a_failed_notification_is_logged_and_not_retried_next_run(
    downloads: Path, fake_osascript: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cfg = fake_config(downloads)
    populate(downloads, cfg)
    monkeypatch.setenv("FAKE_OSASCRIPT_FAIL", "1")
    lines = st.after_scheduled_run(cfg)
    assert lines[0].startswith("Summary: 3 files wait for review")
    assert "could not be shown (execution error: Notifications are not allowed" in lines[1]
    assert st.after_scheduled_run(cfg) == []  # recorded anyway: never nag
    assert len(calls(fake_osascript)) == 1


def test_a_missing_osascript_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("file_librarian.status.OSASCRIPT", "/nonexistent/osascript")
    problem = st.notify("x")
    assert problem is not None and "osascript" in problem


# --- the scheduled run ---------------------------------------------------------------


def test_scheduled_run_posts_the_summary_and_logs_it(
    tmp_path: Path, downloads: Path, fake_osascript: Path
) -> None:
    path = write_config(tmp_path, downloads)
    cfg = load_config(path)
    populate(downloads, cfg)

    result = runner.invoke(app, ["run", "--trigger", "schedule", "-c", str(path)])

    assert result.exit_code == 0, result.stdout + result.stderr
    # The echo provider filed the failed file on this retry; the three in review still wait.
    text = "Summary: 3 files wait for review. Run `flib status` for details."
    assert text in result.stdout
    log = sorted(logs_dir(cfg.database).glob("run-*.log"))[-1]
    assert text in log.read_text(encoding="utf-8")
    (argv,) = calls(fake_osascript)
    assert argv[-2:] == [text.removeprefix("Summary: "), "file-librarian"]

    again = runner.invoke(app, ["run", "--trigger", "schedule", "-c", str(path)])
    assert again.exit_code == 0 and "Summary:" not in again.stdout
    assert len(calls(fake_osascript)) == 1


def test_a_manual_run_never_notifies(tmp_path: Path, downloads: Path, fake_osascript: Path) -> None:
    path = write_config(tmp_path, downloads)
    populate(downloads, load_config(path))
    result = runner.invoke(app, ["run", "-c", str(path)])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "Summary:" not in result.stdout
    assert calls(fake_osascript) == []


def test_a_broken_summary_never_fails_the_scheduled_run(
    tmp_path: Path, downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = write_config(tmp_path, downloads)
    populate(downloads, load_config(path))

    def broken(_cfg: Config, **_kwargs: object) -> list[str]:
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr("file_librarian.status.after_scheduled_run", broken)
    result = runner.invoke(app, ["run", "--trigger", "schedule", "-c", str(path)])
    assert result.exit_code == 0, result.stdout + result.stderr
    assert "warning: the periodic summary failed: OperationalError: database is locked" in (
        result.stdout
    )


def test_summary_config_is_validated(downloads: Path) -> None:
    from file_librarian.config import ConfigError, parse_config

    with pytest.raises(ConfigError, match=r"summary\.every_days"):
        parse_config({"provider": {"name": "fake", "model": "x"}, "summary": {"every_days": 0}})
    with pytest.raises(ConfigError, match=r"summary\.colour"):
        parse_config({"provider": {"name": "fake", "model": "x"}, "summary": {"colour": 1}})


def test_scheduled_trigger_is_what_the_agent_passes() -> None:
    # The hook keys on this trigger; the launchd agent must keep passing it.
    args = scheduler.build_plist("12:30", Path("/c.yaml"), Path("/logs"))["ProgramArguments"]
    assert isinstance(args, list) and args[-2:] == ["--trigger", RunTrigger.SCHEDULE.value]

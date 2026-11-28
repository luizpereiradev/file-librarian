"""The demo library (``scripts/demo.py``): where it refuses to go, and what it builds."""

from __future__ import annotations

import os
from collections import Counter
from datetime import date
from pathlib import Path

import pytest
from scripts import demo

from file_librarian import keychain, pipeline, scheduler
from file_librarian.config import load_config
from file_librarian.db.store import Store
from file_librarian.due import counts
from file_librarian.providers.fake import FakeProvider

# --- refusals ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "where",
    [
        "/",
        "/tmp",  # noqa: S108 - a path the demo must refuse, never written
        "HOME",
        "HOME/..",
        "HOME/Downloads",
        "HOME/Downloads/demo",
        "HOME/Desktop/demo",
        "HOME/Documents",
        "HOME/.config/file-librarian",
        "HOME/.config/file-librarian/demo",
        "HOME/Library",
        "HOME/Library/Application Support",
        "HOME/Library/Application Support/file-librarian/demo",
        "HOME/Library/LaunchAgents",
    ],
)
def test_it_refuses_the_root_your_home_and_flibs_real_folders(
    where: str, fake_home: Path, tmp_path: Path
) -> None:
    target = Path(where.replace("HOME", str(fake_home)))
    with pytest.raises(demo.DemoError, match="refusing"):
        demo.check_target(target, fake_home)
    with pytest.raises(demo.DemoError, match="refusing"):
        demo.build(target, home=fake_home)
    with pytest.raises(demo.DemoError, match="refusing"):
        demo.clean(target, home=fake_home)


def test_a_folder_of_its_own_inside_home_is_accepted(fake_home: Path) -> None:
    assert (
        demo.check_target(fake_home / "demos" / "flib", fake_home)
        == (fake_home / "demos" / "flib").resolve()
    )


def test_it_never_replaces_a_folder_that_holds_something_else(
    tmp_path: Path, fake_home: Path
) -> None:
    target = tmp_path / "mine"
    target.mkdir()
    (target / "notes.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(demo.DemoError, match="not empty"):
        demo.build(target, home=fake_home)
    with pytest.raises(demo.DemoError, match="holds no demo"):
        demo.clean(target, home=fake_home)
    assert (target / "notes.txt").read_text(encoding="utf-8") == "mine"


def test_the_command_line_refuses_with_a_message(
    fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert demo.main([str(fake_home)]) == 2
    assert "refusing" in capsys.readouterr().err
    assert not (fake_home / demo.MARKER).exists()


# --- what it builds ----------------------------------------------------------------------


@pytest.mark.parametrize("language", ["en", "pt-BR"])
def test_a_few_days_of_use(language: demo.Language, tmp_path: Path, fake_home: Path) -> None:
    built = demo.build(tmp_path / "library", language, home=fake_home)
    config = load_config(built.config_path)
    with Store.open(built.database) as store:
        rows = store.all_files()
        runs = store.conn.execute("SELECT trigger, files_moved FROM runs ORDER BY id").fetchall()
        corrections = store.conn.execute("SELECT count(*) FROM corrections").fetchone()[0]
        undone = store.conn.execute(
            "SELECT count(*) FROM operations WHERE undone_at IS NOT NULL"
        ).fetchone()[0]
        days = store.conn.execute("SELECT count(DISTINCT date(started_at)) FROM runs").fetchone()
        due = counts(store, config, date.today())
    assert Counter(str(r.status) for r in rows) == {
        "organized": 29,
        "review": 5,
        "duplicate": 1,
        "sensitive": 1,
        "failed": 1,
        "skipped": 1,  # the wallpaper, put back by undo
    }
    assert [tuple(r) for r in runs] == [
        ("schedule", 14),
        ("schedule", 10),
        ("manual", 1),  # the fix by hand
        ("schedule", 3),
        ("schedule", 3),
    ]
    assert (corrections, undone, days[0]) == (1, 1, 5)  # one fix, one undo, five days
    # The photos wait in review with one suggestion: one group, one "Accept all".
    review = Counter(r.review_category for r in rows if str(r.status) == "review")
    assert review == {demo.work(language): 4, demo.UNCONFIGURED[language]: 1}
    assert (due.overdue, due.upcoming) == (1, 2)
    currencies = {r.currency for r in rows if r.amount_minor is not None}
    assert currencies == {"USD", "BRL"}
    # The sensitive file stays in place with nothing about it stored.
    sensitive = [r for r in rows if str(r.status) == "sensitive"]
    assert [r.original_name for r in sensitive] == [demo.SENSITIVE]
    assert sensitive[0].description is None
    assert "FAKE-DEMO-" in (built.downloads / demo.SENSITIVE).read_text(encoding="utf-8")
    # Today's downloads, the failed file, the undone one and the secret stay in Downloads.
    left = sorted(p.name for p in built.downloads.iterdir() if p != built.output)
    today = {i.name_in(language) for i in demo.TODAY}
    assert left == sorted({*today, demo.SENSITIVE, demo.FAILED, demo.UNDONE})
    # Their answers wait in the cache: the configuration's own fake provider, which
    # would echo otherwise, gets the demo's answers in a live run.
    outcome = pipeline.run(config, FakeProvider(echo=True), simulate=True)
    assert outcome.cached == len(demo.TODAY)
    # Everything is inside the demo's folder, and the configuration is private.
    for path in (config.database, config.output_root, *(i.path for i in config.inputs)):
        assert built.root in path.parents
    assert built.config_path.stat().st_mode & 0o077 == 0
    # Nothing landed in the home folder the build was told is the real one.
    assert list(fake_home.iterdir()) == []
    # The test's own guards are back in place.
    assert os.environ["HOME"] == str(fake_home)
    assert keychain.SECURITY != demo.NOWHERE
    assert scheduler.LAUNCHCTL != demo.NOWHERE


def test_clean_removes_only_the_demo(tmp_path: Path, fake_home: Path) -> None:
    root = tmp_path / "demo"
    demo.build(root, home=fake_home)
    assert (root / demo.MARKER).is_file()
    assert demo.clean(root, home=fake_home)
    assert not root.exists()
    assert not demo.clean(root, home=fake_home)


def test_the_printed_commands_keep_to_the_demo(
    tmp_path: Path,
    fake_home: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(Path, "home", lambda: fake_home)
    assert demo.main([str(tmp_path / "demo")]) == 0
    out = capsys.readouterr().out
    commands = [line for line in out.splitlines() if '"$FLIB"' in line]
    assert commands and all(line.startswith('HOME="$DEMO_HOME" "$FLIB" ') for line in commands)
    assert all(line.endswith('-c "$CFG"') for line in commands)
    assert f"DEMO_HOME={tmp_path / 'demo' / 'home'}" in out
    assert "--clean" in out

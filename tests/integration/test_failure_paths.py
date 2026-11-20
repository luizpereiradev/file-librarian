"""Failure paths of the executor, undo, store and CLI: the paths this project exists for."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from file_librarian import cli, pipeline
from file_librarian.config import Config, parse_config
from file_librarian.db.store import Store
from file_librarian.fsops import DestinationExistsError, move_no_replace
from file_librarian.models import FileStatus, OperationStatus, RunMode, RunTrigger
from file_librarian.providers.fake import FakeProvider
from file_librarian.undo import NothingToUndoError, undo_file, undo_run
from tests._helpers import answer, put, write_config

pytestmark = pytest.mark.integration
runner = CliRunner()


def finance(downloads: Path) -> Path:
    return downloads / "Organized" / "Finance"


# --- executor ------------------------------------------------------------------------------


def test_name_taken_between_plan_and_move_gets_the_next_counter(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(downloads, "a.pdf", b"mine")
    calls: list[str] = []

    def first_name_taken(src: Path, dst: Path) -> None:
        calls.append(dst.name)
        if len(calls) == 1:
            raise DestinationExistsError(17, "exists", str(dst))
        move_no_replace(src, dst)

    monkeypatch.setattr("file_librarian.executor.move_no_replace", first_name_taken)
    pipeline.run(cfg, FakeProvider(default=answer()), simulate=False)
    assert calls == ["a.pdf", "a-2.pdf"]
    assert (finance(downloads) / "a-2.pdf").read_bytes() == b"mine"
    with Store.open(cfg.database) as store:
        [op] = store.operations()
    assert (op.status, op.dst_path.name) == (OperationStatus.DONE, "a-2.pdf")


@pytest.mark.parametrize(
    ("error", "message"),
    [
        (PermissionError(13, "denied"), "move failed"),
        (NotADirectoryError(20, "not a folder"), "move failed"),
    ],
)
def test_failed_move_is_journaled_and_leaves_the_file(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch, error: OSError, message: str
) -> None:
    source = put(downloads, "a.pdf", b"mine")

    def fail(src: Path, dst: Path) -> None:
        raise error

    monkeypatch.setattr("file_librarian.executor.move_no_replace", fail)
    outcome = pipeline.run(cfg, FakeProvider(default=answer()), simulate=False)
    assert source.read_bytes() == b"mine"
    assert outcome.report is not None
    [item] = outcome.report.items
    assert item.status is FileStatus.FAILED and message in item.note
    with Store.open(cfg.database) as store:
        [op] = store.operations()
        row = store.file_by_id(op.file_id)
    assert op.status is OperationStatus.FAILED
    assert (row.status, row.attempts) == (FileStatus.FAILED, 1)


def test_file_deleted_after_planning_is_skipped(downloads: Path, cfg: Config) -> None:
    source = put(downloads, "a.pdf", b"x")
    planned = pipeline.run(cfg, FakeProvider(default=answer()), simulate=True)
    source.unlink()
    from file_librarian.executor import execute

    with Store.open(cfg.database) as store:
        report = execute(
            planned.plan, cfg, store, trigger=RunTrigger.MANUAL, provider="f", model="m", seen=1
        )
        assert store.operations() == []
    assert report.items[0].status is FileStatus.SKIPPED


# --- undo ----------------------------------------------------------------------------------


def organize_one(downloads: Path, cfg: Config) -> Path:
    put(downloads, "a.pdf", b"mine")
    pipeline.run(cfg, FakeProvider(default=answer()), simulate=False)
    return finance(downloads) / "a.pdf"


def test_undo_skips_a_file_moved_away_by_hand(downloads: Path, cfg: Config, tmp_path: Path) -> None:
    moved = organize_one(downloads, cfg)
    moved.rename(tmp_path / "elsewhere.pdf")
    with Store.open(cfg.database) as store:
        report = undo_run(store)
    assert report.skipped == [(moved, "no longer there (moved or deleted by hand)")]


def test_undo_skips_when_the_original_folder_is_gone(downloads: Path, tmp_path: Path) -> None:
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    config = parse_config(
        {
            "inputs": [str(inbox)],
            "output_root": str(tmp_path / "library"),
            "provider": {"name": "fake", "model": "x"},
        }
    )
    put(inbox, "a.pdf", b"mine")
    pipeline.run(config, FakeProvider(default=answer()), simulate=False)
    inbox.rmdir()
    with Store.open(config.database) as store:
        report = undo_run(store)
    [(_, reason)] = report.skipped
    assert reason.startswith("original folder is gone")


def test_undo_file_errors(downloads: Path, cfg: Config) -> None:
    moved = organize_one(downloads, cfg)
    with Store.open(cfg.database) as store:
        with pytest.raises(NothingToUndoError, match="not a file flib moved"):
            undo_file(store, downloads / "never-seen.pdf")
        undo_file(store, moved)
        restored = downloads / "a.pdf"
        assert restored.exists()
        with pytest.raises(NothingToUndoError, match="no move to undo"):
            undo_file(store, restored)


# --- store ---------------------------------------------------------------------------------


def test_transaction_rolls_back_on_error(tmp_path: Path) -> None:
    with Store.open(tmp_path / "db" / "l.db") as store:
        with pytest.raises(RuntimeError), store.transaction():
            store.start_run(RunMode.RUN, RunTrigger.MANUAL)
            raise RuntimeError("crash mid-transaction")
        assert store.conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


# --- CLI -----------------------------------------------------------------------------------


@pytest.mark.parametrize("args", [["run"], ["simulate"], ["search", "x"], ["undo"]])
def test_commands_reject_an_invalid_config(tmp_path: Path, args: list[str]) -> None:
    bad = tmp_path / "config.yaml"
    bad.write_text("inputs: []\n", encoding="utf-8")
    result = runner.invoke(cli.app, [*args, "-c", str(bad)])
    assert result.exit_code == 1
    assert "invalid configuration" in result.stderr


def test_undo_rejects_file_and_run_together(tmp_path: Path, downloads: Path) -> None:
    result = runner.invoke(
        cli.app,
        ["undo", "--file", "a.pdf", "--run", "1", "-c", str(write_config(tmp_path, downloads))],
    )
    assert result.exit_code == 1 and "either --file or --run" in result.stderr


def test_undo_lists_what_it_skipped(tmp_path: Path, downloads: Path) -> None:
    config_file = write_config(tmp_path, downloads)
    put(downloads, "a.txt", b"mine")
    assert runner.invoke(cli.app, ["run", "-c", str(config_file)]).exit_code == 0
    [moved] = (downloads / "Organized").rglob("*a.txt")  # renamed: it has content
    moved.write_bytes(b"edited")
    result = runner.invoke(cli.app, ["undo", "-c", str(config_file)])
    assert "skipped" in result.stdout and "changed since it was moved" in result.stdout


def test_run_warns_when_the_log_cannot_be_written(tmp_path: Path, downloads: Path) -> None:
    config_file = write_config(tmp_path, downloads)
    put(downloads, "a.txt", b"mine")
    cfg = parse_config({"inputs": [str(downloads)], "provider": {"name": "fake", "model": "x"}})
    logs = cfg.database.parent / "logs"
    logs.parent.mkdir(parents=True, exist_ok=True)
    logs.write_text("a file where the logs folder should be", encoding="utf-8")
    result = runner.invoke(cli.app, ["run", "-c", str(config_file)])
    assert result.exit_code == 0
    assert "could not write the run log" in result.stderr


def test_main_refuses_other_platforms(monkeypatch: pytest.MonkeyPatch, capsys: Any) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 1
    assert "macOS only" in capsys.readouterr().err


def test_main_turns_unexpected_errors_into_a_report_request(
    monkeypatch: pytest.MonkeyPatch, capsys: Any
) -> None:
    def boom() -> None:
        raise KeyError("surprise")

    monkeypatch.setattr(cli, "app", boom)
    with pytest.raises(SystemExit) as exit_info:
        cli.main()
    assert exit_info.value.code == 1
    err = capsys.readouterr().err
    assert "unexpected KeyError" in err and "Please report it" in err


def test_simulate_and_run_say_why_items_were_skipped(tmp_path: Path, downloads: Path) -> None:
    config_file = write_config(tmp_path, downloads, extra="duplicates: leave\n")
    put(downloads, "app.dmg", b"same")
    put(downloads, "app (1).dmg", b"same")
    put(downloads, "movie.mkv.part", b"partial")
    (downloads / ".DS_Store").write_bytes(b"noise")
    sim = runner.invoke(cli.app, ["simulate", "-c", str(config_file)])
    assert "skip    app (1).dmg  (same content as another item in this run)" in sim.stdout
    assert "skip    movie.mkv.part  (download in progress)" in sim.stdout
    assert ".DS_Store" not in sim.stdout
    run = runner.invoke(cli.app, ["run", "-c", str(config_file)])
    assert "app (1).dmg  (same content" in run.stdout
    again = runner.invoke(cli.app, ["run", "-c", str(config_file)])
    # The duplicate's content is now known: it is counted, not listed.
    assert "already processed earlier were left alone" in again.stdout

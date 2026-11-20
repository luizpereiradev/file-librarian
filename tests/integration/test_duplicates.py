"""Duplicates are moved to the duplicates folder, never deleted (RF-21).

A duplicate is an item with the same content as another item going ahead in
the same run, or as a file filed in the library that is still there with
that content. It is moved by the executor (journaled, undoable, recovered
after a crash, copied and verified across volumes), indexed with status
``duplicate`` and a pointer to its original, and never sent to the provider.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from file_librarian import cli, executor, fsops, pipeline
from file_librarian.config import Config, ConfigError, parse_config
from file_librarian.db.store import Store, load_migrations
from file_librarian.embeddings import embeddable
from file_librarian.fsops import move_no_replace
from file_librarian.hashing import file_sha256
from file_librarian.models import FileStatus, OperationStatus, RunTrigger
from file_librarian.providers.fake import FakeProvider
from file_librarian.reconcile import content_at, reconcile
from file_librarian.reprocess import select
from file_librarian.scanner import SkipReason
from file_librarian.search import search
from file_librarian.status import collect, render, to_json
from file_librarian.undo import undo_run
from tests._helpers import (
    OLD,
    Crash,
    answer,
    fake_config,
    library,
    make_old,
    mark_cloud_only,
    put,
    write_config,
)

pytestmark = pytest.mark.integration

runner = CliRunner()


def dups(downloads: Path) -> Path:
    return library(downloads) / "_Duplicates"


def finance(downloads: Path) -> Path:
    return library(downloads) / "Finance"


def run(cfg: Config, provider: FakeProvider | None = None) -> pipeline.Outcome:
    return pipeline.run(cfg, provider or FakeProvider(default=answer()), simulate=False)


def rows(cfg: Config) -> dict[str, tuple[str, int | None, str]]:
    """current name -> (status, duplicate_of, sha256)"""
    with Store.open(cfg.database) as store:
        return {
            r.current_name: (r.status.value, r.duplicate_of, r.sha256) for r in store.all_files()
        }


def file_id(cfg: Config, name: str) -> int:
    with Store.open(cfg.database) as store:
        return next(r.id for r in store.all_files() if r.current_name == name)


# --- deciding what is a duplicate -------------------------------------------------


def test_copies_in_the_same_run_follow_their_original(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same", mtime=OLD)
    put(downloads, "bill (1).pdf", b"same", mtime=OLD + 1)
    put(downloads, "bill copy.pdf", b"same", mtime=OLD + 2)
    provider = FakeProvider(default=answer())

    outcome = run(cfg, provider)

    assert [c.path.name for c in provider.calls] == ["bill.pdf"]  # one call, for the original
    assert (finance(downloads) / "bill.pdf").read_bytes() == b"same"
    assert sorted(p.name for p in dups(downloads).iterdir()) == ["bill (1).pdf", "bill copy.pdf"]
    assert not any(p.is_file() for p in downloads.iterdir())
    original = file_id(cfg, "bill.pdf")
    found = rows(cfg)
    assert found["bill (1).pdf"][:2] == ("duplicate", original)
    assert found["bill copy.pdf"][:2] == ("duplicate", original)
    assert outcome.report is not None
    assert outcome.report.count(FileStatus.DUPLICATE, moved=True) == 2
    with Store.open(cfg.database) as store:
        assert len(store.operations(status=OperationStatus.DONE)) == 3
        [only] = store.conn.execute("SELECT files_seen FROM runs").fetchall()
        assert only[0] == 3


def test_a_copy_of_a_library_file_costs_nothing(
    tmp_path: Path, downloads: Path, cfg: Config
) -> None:
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    put(downloads, "bill again.pdf", b"same")
    provider = FakeProvider(default=answer())

    sim = pipeline.run(cfg, provider, simulate=True)
    assert [i.status for i in sim.plan.items] == [FileStatus.DUPLICATE]
    outcome = run(cfg, provider)

    assert provider.calls == []
    assert outcome.cost_usd == 0
    assert (dups(downloads) / "bill again.pdf").read_bytes() == b"same"
    assert rows(cfg)["bill again.pdf"][:2] == ("duplicate", file_id(cfg, "bill.pdf"))
    # The original's row is untouched.
    assert rows(cfg)["bill.pdf"][:2] == ("organized", None)

    # The CLI says where the original is, and counts the duplicate.
    config_file = write_config(tmp_path, downloads)
    put(downloads, "bill third.pdf", b"same")
    sim_cli = runner.invoke(cli.app, ["simulate", "-c", str(config_file)])
    assert (
        "  dup     bill third.pdf  ->  _Duplicates/bill third.pdf  "
        "(same content as Finance/bill.pdf)" in sim_cli.stdout
    )
    assert "(1 duplicate(s) to _Duplicates)" in sim_cli.stdout
    result = runner.invoke(cli.app, ["run", "-c", str(config_file)])
    assert (
        "  dup     bill third.pdf  ->  _Duplicates/bill third.pdf  "
        "(same content as Finance/bill.pdf)" in result.stdout
    )
    assert "1 duplicate(s) set aside" in result.stdout


def test_a_copy_in_review_is_a_duplicate_too(downloads: Path, cfg: Config) -> None:
    put(downloads, "unsure.pdf", b"hm")
    run(cfg, FakeProvider(default=answer(confidence=0.2)))
    put(downloads, "unsure (1).pdf", b"hm")
    outcome = run(cfg)
    assert outcome.report is not None
    [item] = outcome.report.items
    assert item.status is FileStatus.DUPLICATE
    assert item.original == library(downloads) / "_Review" / "unsure.pdf"


def snapshot(cfg: Config) -> list[tuple[int, str, str]]:
    with Store.open(cfg.database) as store:
        return [(r.id, r.status.value, str(r.current_path)) for r in store.all_files()]


def go_missing(cfg: Config, downloads: Path) -> None:
    """Delete the filed bill.pdf by hand and let reconcile mark it missing."""
    (finance(downloads) / "bill.pdf").unlink()
    with Store.open(cfg.database) as store:
        reconcile(cfg, store)
    assert rows(cfg)["bill.pdf"][0] == "missing"


def renamed(downloads: Path) -> None:
    (finance(downloads) / "bill.pdf").rename(finance(downloads) / "electricity.pdf")


def edited(downloads: Path) -> None:
    (finance(downloads) / "bill.pdf").write_bytes(b"edited since")


def deleted(downloads: Path) -> None:
    (finance(downloads) / "bill.pdf").unlink()


def dragged_back(downloads: Path) -> None:
    (finance(downloads) / "bill.pdf").rename(downloads / "bill.pdf")


@pytest.mark.parametrize("change", [renamed, edited, deleted, dragged_back])
def test_a_filed_file_not_where_flib_left_it_is_never_taken_over(
    downloads: Path, cfg: Config, change: Any
) -> None:
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    before = snapshot(cfg)
    change(downloads)
    copy = put(downloads, "bill.pdf", b"same") if change is not dragged_back else None
    provider = FakeProvider(default=answer())

    outcome = run(cfg, provider)

    assert provider.calls == []
    assert outcome.report is not None and outcome.report.items == []
    assert (downloads / "bill.pdf", SkipReason.NOT_WHERE_LEFT) in outcome.scan.skipped
    assert snapshot(cfg) == before  # the row is untouched until reconcile says where it is
    assert copy is None or copy.exists()
    assert not dups(downloads).exists()


def test_the_skip_says_to_run_reconcile(tmp_path: Path, downloads: Path) -> None:
    config_file = write_config(tmp_path, downloads)
    put(downloads, "bill.pdf", b"same")
    runner.invoke(cli.app, ["run", "-c", str(config_file)])
    (library(downloads) / "Other" / "bill.pdf").rename(downloads / "bill.pdf")
    result = runner.invoke(cli.app, ["run", "-c", str(config_file)])
    assert (
        "  skip    bill.pdf  (same content as a filed item that is not where flib left it; "
        "run `flib reconcile`)" in result.stdout
    )


def test_an_unreadable_original_makes_the_copy_wait(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    original = finance(downloads) / "bill.pdf"
    before = snapshot(cfg)
    original.chmod(0)
    try:
        copy = put(downloads, "bill (1).pdf", b"same")
        outcome = run(cfg)
    finally:
        original.chmod(0o644)
    assert (copy, SkipReason.KNOWN) in outcome.scan.skipped
    assert snapshot(cfg) == before and copy.exists()


def test_an_unmounted_library_volume_never_loses_the_row(tmp_path: Path, downloads: Path) -> None:
    volumes = tmp_path / "Volumes"
    lib = volumes / "External" / "Library"
    lib.mkdir(parents=True)
    cfg = fake_config(downloads, output_root=str(lib))
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    before = snapshot(cfg)
    (volumes / "External").rename(tmp_path / "External.offline")  # "unmounted"
    copy = put(downloads, "bill.pdf", b"same")  # downloaded again meanwhile
    outcome = run(cfg)
    assert outcome.report is not None and outcome.report.items == []
    assert snapshot(cfg) == before and copy.exists()
    (tmp_path / "External.offline").rename(volumes / "External")  # back
    run(cfg)
    assert sorted(p.name for p in (lib / "Finance").iterdir()) == ["bill.pdf"]
    assert (lib / "_Duplicates" / "bill.pdf").read_bytes() == b"same"


def test_a_missing_original_lets_the_copy_be_filed(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    [(row_id, _, _)] = snapshot(cfg)
    go_missing(cfg, downloads)
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    assert snapshot(cfg) == [(row_id, "organized", str(finance(downloads) / "bill.pdf"))]
    assert (finance(downloads) / "bill.pdf").read_bytes() == b"same"


def test_a_missing_row_is_not_taken_over_by_a_copy_that_stays(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    go_missing(cfg, downloads)
    before = snapshot(cfg)
    # Failed classification, then a name that is sensitive: neither is filed.
    put(downloads, "bill.pdf", b"same")
    with Store.open(cfg.database) as store:
        store.conn.execute("DELETE FROM classification_cache")
    failed = run(cfg, FakeProvider())
    assert failed.report is not None
    assert [i.status for i in failed.report.items] == [FileStatus.FAILED]
    assert snapshot(cfg) == before
    (downloads / "bill.pdf").rename(downloads / "credentials.json")
    sensitive = run(cfg)
    assert sensitive.report is not None
    assert [i.status for i in sensitive.report.items] == [FileStatus.SENSITIVE]
    assert snapshot(cfg) == before


def test_a_missing_row_is_not_taken_over_by_a_failed_move(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    go_missing(cfg, downloads)
    before = snapshot(cfg)
    copy = put(downloads, "bill.pdf", b"same")

    def refuse(src: Path, dst: Path) -> None:
        raise PermissionError(13, "Permission denied")

    with monkeypatch.context() as patch:
        patch.setattr("file_librarian.executor.move_no_replace", refuse)
        outcome = run(cfg)
    assert outcome.report is not None
    assert [i.status for i in outcome.report.items] == [FileStatus.FAILED]
    assert snapshot(cfg) == before and copy.exists()
    with Store.open(cfg.database) as store:
        assert store.operations()[-1].status is OperationStatus.FAILED
    run(cfg)  # the next run files it
    assert rows(cfg)["bill.pdf"][0] == "organized"


@pytest.mark.parametrize("arrived", [True, False])
def test_a_crash_while_taking_over_a_missing_row_is_recovered(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch, arrived: bool
) -> None:
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    go_missing(cfg, downloads)
    before = snapshot(cfg)
    copy = put(downloads, "bill.pdf", b"same")

    def crash(src: Path, dst: Path) -> None:
        if arrived:
            move_no_replace(src, dst)
        raise Crash

    with monkeypatch.context() as patch, pytest.raises(Crash):
        patch.setattr("file_librarian.executor.move_no_replace", crash)
        run(cfg)
    assert snapshot(cfg) == before  # nothing written before the move finished
    with Store.open(cfg.database) as store:
        assert executor.recover(store, cfg) == 1
    if arrived:
        [(_, status, path)] = snapshot(cfg)
        assert (status, path) == ("organized", str(finance(downloads) / "bill.pdf"))
    else:
        assert snapshot(cfg) == before and copy.exists()


def test_a_copy_stays_when_its_original_fails_to_move_in_the_same_run(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(downloads, "bill.pdf", b"same")
    copy = put(downloads, "bill (1).pdf", b"same")

    def refuse_the_original(src: Path, dst: Path) -> None:
        if src.name == "bill.pdf":
            raise PermissionError(13, "Permission denied")
        move_no_replace(src, dst)

    with monkeypatch.context() as patch:
        patch.setattr("file_librarian.executor.move_no_replace", refuse_the_original)
        outcome = run(cfg)
    assert outcome.report is not None
    assert {i.source.name: i.status for i in outcome.report.items} == {
        "bill.pdf": FileStatus.FAILED,
        "bill (1).pdf": FileStatus.SKIPPED,
    }
    assert copy.exists() and list(rows(cfg)) == ["bill.pdf"]


def test_content_at_tells_absent_from_unknown(tmp_path: Path) -> None:
    there = put(tmp_path, "a.txt", b"a")
    digest = file_sha256(there)
    assert content_at(there, digest) is True
    assert content_at(there, "0" * 64) is False
    assert content_at(tmp_path / "gone.txt", digest) is False
    assert content_at(tmp_path / "no folder" / "a.txt", digest) is None
    there.chmod(0)
    try:
        assert content_at(there, digest) is None
    finally:
        there.chmod(0o644)


def test_a_cloud_only_original_is_never_read_and_the_copy_waits(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    mark_cloud_only(monkeypatch, finance(downloads) / "bill.pdf")
    copy = put(downloads, "bill (1).pdf", b"same")
    outcome = run(cfg)
    assert outcome.report is not None and outcome.report.items == []
    assert copy.exists() and not dups(downloads).exists()


def test_folder_units_are_duplicates_too(downloads: Path, cfg: Config) -> None:
    for name in ("Project", "Project copy"):
        put(downloads, f"{name}/a.txt", b"a")
        put(downloads, f"{name}/b.txt", b"b")
        make_old(downloads / name)
    run(cfg, FakeProvider(default=answer("Other")))
    assert (library(downloads) / "Other" / "Project" / "a.txt").exists()
    assert (dups(downloads) / "Project copy" / "b.txt").read_bytes() == b"b"
    status, original, _ = rows(cfg)["Project copy"]
    assert (status, original) == ("duplicate", file_id(cfg, "Project"))


def test_a_taken_name_in_the_duplicates_folder_gets_a_counter(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    other = put(dups(downloads), "bill.pdf", b"someone else's")
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    assert (dups(downloads) / "bill-2.pdf").read_bytes() == b"same"
    assert other.read_bytes() == b"someone else's"


def test_leave_keeps_duplicates_where_they_are(downloads: Path) -> None:
    cfg = fake_config(downloads, duplicates="leave")
    put(downloads, "bill.pdf", b"same")
    put(downloads, "bill (1).pdf", b"same")
    run(cfg)
    put(downloads, "bill (2).pdf", b"same")
    outcome = run(cfg)
    assert (downloads / "bill (1).pdf").exists() and (downloads / "bill (2).pdf").exists()
    assert not dups(downloads).exists()
    assert outcome.scan.duplicates == []
    assert list(rows(cfg)) == ["bill.pdf"]


# --- what stays in place -----------------------------------------------------------


def test_a_copy_of_a_sensitive_file_stays_in_place(downloads: Path, cfg: Config) -> None:
    key = b"-----BEGIN OPENSSH PRIVATE KEY-----\nAAAA\n-----END OPENSSH PRIVATE KEY-----\n"
    put(downloads, "key.txt", key)
    put(downloads, "key (1).txt", key)
    outcome = run(cfg)
    assert outcome.report is not None
    assert {i.source.name: i.status for i in outcome.report.items} == {
        "key.txt": FileStatus.SENSITIVE,
        "key (1).txt": FileStatus.SKIPPED,
    }
    assert (downloads / "key.txt").exists() and (downloads / "key (1).txt").exists()
    assert list(rows(cfg)) == ["key.txt"]
    # Later runs leave both alone too.
    put(downloads, "key (2).txt", key)
    again = run(cfg)
    assert again.report is not None and again.report.items == []
    assert not dups(downloads).exists()


def test_a_copy_waits_with_an_original_that_failed(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    put(downloads, "bill (1).pdf", b"same")
    outcome = run(cfg, FakeProvider())  # no answer: classification fails
    assert outcome.report is not None
    assert {i.source.name: i.status for i in outcome.report.items} == {
        "bill.pdf": FileStatus.FAILED,
        "bill (1).pdf": FileStatus.SKIPPED,
    }
    assert list(rows(cfg)) == ["bill.pdf"]
    assert (downloads / "bill (1).pdf").exists()


def test_a_copy_waits_with_an_original_left_for_a_later_run(downloads: Path) -> None:
    cfg = fake_config(downloads, limits={"max_files_per_run": 1})
    put(downloads, "a.pdf", b"a", mtime=OLD)
    put(downloads, "b.pdf", b"b", mtime=OLD + 1)
    put(downloads, "b (1).pdf", b"b", mtime=OLD + 2)
    outcome = run(cfg)
    assert outcome.scan.duplicates == []
    assert (downloads / "b (1).pdf").exists()


def test_the_original_is_checked_again_before_the_copy_moves(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    copy = put(downloads, "bill (1).pdf", b"same")
    sim = pipeline.run(cfg, FakeProvider(), simulate=True)
    (finance(downloads) / "bill.pdf").unlink()  # gone between the plan and the move
    with Store.open(cfg.database) as store:
        report = executor.execute(
            sim.plan, cfg, store, trigger=RunTrigger.MANUAL, provider="fake", model="x", seen=1
        )
    [item] = report.items
    assert item.status is FileStatus.SKIPPED and "no longer in the library" in item.note
    assert copy.exists() and list(rows(cfg)) == ["bill.pdf"]


def test_the_duplicates_folder_is_never_scanned(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    put(downloads, "bill (1).pdf", b"same")
    run(cfg)
    put(dups(downloads), "dropped there.pdf", b"new")
    provider = FakeProvider(default=answer())
    outcome = run(cfg, provider)
    assert provider.calls == []
    assert outcome.report is not None and outcome.report.items == []
    assert (dups(downloads) / "dropped there.pdf").exists()


# --- undo and recovery ----------------------------------------------------------------


def test_undo_puts_duplicates_back_and_later_runs_leave_them(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    copy = put(downloads, "bill (1).pdf", b"same")
    run(cfg)
    with Store.open(cfg.database) as store:
        report = undo_run(store, config=cfg)
    assert report.restored == [(dups(downloads) / "bill (1).pdf", copy)]
    assert copy.read_bytes() == b"same"
    assert rows(cfg)["bill (1).pdf"][:2] == ("skipped", file_id(cfg, "bill.pdf"))
    # Put back on purpose: the next run leaves it where it is.
    outcome = run(cfg)
    assert outcome.report is not None and outcome.report.items == []
    assert copy.exists()


def test_a_crash_while_moving_a_duplicate_is_recovered(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    copy = put(downloads, "bill (1).pdf", b"same")

    def move_then_crash(src: Path, dst: Path) -> None:
        move_no_replace(src, dst)
        raise Crash

    with monkeypatch.context() as patch, pytest.raises(Crash):
        patch.setattr("file_librarian.executor.move_no_replace", move_then_crash)
        run(cfg)

    outcome = run(cfg)
    assert outcome.recovered == 1
    moved = dups(downloads) / "bill (1).pdf"
    assert moved.read_bytes() == b"same" and not copy.exists()
    assert rows(cfg)["bill (1).pdf"][:2] == ("duplicate", file_id(cfg, "bill.pdf"))
    with Store.open(cfg.database) as store:
        assert undo_run(store, config=cfg).restored == [(moved, copy)]


def test_a_failed_move_reuses_the_duplicate_row(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    put(downloads, "bill (1).pdf", b"same")

    def refuse(src: Path, dst: Path) -> None:
        raise PermissionError(13, "Permission denied")

    with monkeypatch.context() as patch:
        patch.setattr("file_librarian.executor.move_no_replace", refuse)
        run(cfg)
    assert rows(cfg)["bill (1).pdf"][0] == "failed"
    run(cfg)
    assert rows(cfg)["bill (1).pdf"][:2] == ("duplicate", file_id(cfg, "bill.pdf"))
    with Store.open(cfg.database) as store:
        assert len(store.all_files()) == 2


@pytest.mark.usefixtures("cross_volume")
def test_a_duplicate_across_volumes_is_copied_and_verified(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    put(downloads, "bill (1).pdf", b"same")
    run(cfg)
    moved = dups(downloads) / "bill (1).pdf"
    assert moved.read_bytes() == b"same"
    assert not (downloads / "bill (1).pdf").exists()
    assert rows(cfg)["bill (1).pdf"][0] == "duplicate"
    with Store.open(cfg.database) as store:
        report = undo_run(store, config=cfg)
    assert sorted(p.name for _, p in report.restored) == ["bill (1).pdf", "bill.pdf"]


# --- reconcile, reprocess, search, inspect, status ---------------------------------------


def test_reconcile_never_promotes_a_duplicate(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    put(downloads, "bill (1).pdf", b"same")
    run(cfg)
    original = finance(downloads) / "bill.pdf"
    original.unlink()
    with Store.open(cfg.database) as store:
        report = reconcile(cfg, store)
    assert report.missing == [original]
    assert report.orphaned == [(dups(downloads) / "bill (1).pdf", original)]
    found = rows(cfg)
    assert found["bill.pdf"][0] == "missing"
    assert found["bill (1).pdf"][0] == "duplicate"
    # Moved back to Downloads by the user, the copy is filed like a new file.
    (dups(downloads) / "bill (1).pdf").rename(downloads / "bill (1).pdf")
    run(cfg)
    assert (finance(downloads) / "bill (1).pdf").exists()


def test_reconcile_marks_a_duplicate_moved_away_missing(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    put(downloads, "bill (1).pdf", b"same")
    run(cfg)
    copy = dups(downloads) / "bill (1).pdf"
    copy.rename(library(downloads) / "Finance" / "kept copy.pdf")
    with Store.open(cfg.database) as store:
        report = reconcile(cfg, store)
        assert report.missing == [copy] and report.moved == []
        again = reconcile(cfg, store)
        assert again.missing == []
    found = rows(cfg)
    assert found["bill (1).pdf"][:2] == ("missing", file_id(cfg, "bill.pdf"))
    assert found["bill.pdf"][0] == "organized"


def test_reconcile_output_lists_orphans(tmp_path: Path, downloads: Path) -> None:
    config_file = write_config(tmp_path, downloads)
    put(downloads, "bill.pdf", b"same")
    put(downloads, "bill (1).pdf", b"same")
    assert runner.invoke(cli.app, ["run", "-c", str(config_file)]).exit_code == 0
    (library(downloads) / "Other" / "bill.pdf").unlink()
    result = runner.invoke(cli.app, ["reconcile", "-c", str(config_file)])
    assert "  orphan  _Duplicates/bill (1).pdf  (its original Other/bill.pdf is missing" in (
        result.stdout
    )
    assert "1 duplicate(s) of missing files" in result.stdout


def test_reprocess_never_reclassifies_duplicates(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    put(downloads, "bill (1).pdf", b"same")
    run(cfg)
    with Store.open(cfg.database) as store:
        selection = select(cfg, store)
    assert [t.row.current_name for t in selection.targets] == ["bill.pdf"]


def test_search_leaves_duplicates_out_unless_asked(
    tmp_path: Path, downloads: Path, cfg: Config
) -> None:
    put(downloads, "bill.pdf", b"same")
    put(downloads, "bill (1).pdf", b"same")
    run(cfg)
    original = str(finance(downloads) / "bill.pdf")
    with Store.open(cfg.database) as store:
        assert [h.path for h in search(store, "bill")] == [original]
        hits = search(store, "bill", include_duplicates=True)
    by_path = {h.path: h for h in hits}
    copy = str(dups(downloads) / "bill (1).pdf")
    assert by_path[copy].duplicate_of == original and by_path[copy].status == "duplicate"
    assert by_path[original].duplicate_of is None

    config_file = write_config(tmp_path, downloads)
    plain = runner.invoke(cli.app, ["search", "bill", "-c", str(config_file)])
    assert copy not in plain.stdout
    both = runner.invoke(cli.app, ["search", "bill", "--duplicates", "-c", str(config_file)])
    assert copy in both.stdout and f"duplicate of {original}" in both.stdout


def test_inspect_says_what_a_duplicate_is_a_copy_of(tmp_path: Path, downloads: Path) -> None:
    config_file = write_config(tmp_path, downloads)
    put(downloads, "bill.pdf", b"same")
    put(downloads, "bill (1).pdf", b"same")
    runner.invoke(cli.app, ["run", "-c", str(config_file)])
    copy = dups(downloads) / "bill (1).pdf"
    original = library(downloads) / "Other" / "bill.pdf"
    result = runner.invoke(cli.app, ["inspect", str(copy), "--json", "-c", str(config_file)])
    [item] = json.loads(result.stdout)
    assert item["index"]["status"] == "duplicate"
    assert item["index"]["duplicate_of"] == str(original)
    text = runner.invoke(cli.app, ["inspect", str(copy), "-c", str(config_file)])
    assert f"duplicate of: {original}" in text.stdout
    # The original itself is shown as the filed file.
    of_original = runner.invoke(
        cli.app, ["inspect", str(original), "--json", "-c", str(config_file)]
    )
    [entry] = json.loads(of_original.stdout)
    assert (entry["index"]["status"], entry["index"]["duplicate_of"]) == ("organized", None)


def test_duplicates_get_no_embedding(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    put(downloads, "bill (1).pdf", b"same")
    run(cfg)
    with Store.open(cfg.database) as store:
        verdicts = {r.current_name: embeddable(r, cfg) for r in store.all_files()}
    assert verdicts == {"bill.pdf": True, "bill (1).pdf": False}


def test_status_counts_duplicates(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    put(downloads, "bill (1).pdf", b"same")
    run(cfg)
    found = collect(cfg)
    assert found.duplicates == 1
    assert "Duplicates set aside in _Duplicates: 1 file" in "\n".join(render(found, cfg))
    assert json.loads(to_json(found, cfg))["duplicates"] == {
        "folder": "_Duplicates",
        "total": 1,
        "notice_pending": True,
    }


def test_reconcile_never_reads_a_cloud_only_duplicate(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(downloads, "bill.pdf", b"same")
    put(downloads, "bill (1).pdf", b"same")
    run(cfg)
    copy = dups(downloads) / "bill (1).pdf"
    mark_cloud_only(monkeypatch, copy)
    with Store.open(cfg.database) as store:
        report = reconcile(cfg, store)
    assert report.cloud_only == [copy] and report.missing == []
    assert rows(cfg)["bill (1).pdf"][0] == "duplicate"


def test_a_duplicates_folder_that_is_a_file_fails_only_the_copy(
    downloads: Path, cfg: Config
) -> None:
    put(library(downloads), "_Duplicates", b"not a folder")
    put(downloads, "bill.pdf", b"same")
    put(downloads, "bill (1).pdf", b"same")
    outcome = run(cfg)
    assert outcome.report is not None
    assert {i.source.name: i.status for i in outcome.report.items} == {
        "bill.pdf": FileStatus.ORGANIZED,
        "bill (1).pdf": FileStatus.SKIPPED,
    }
    assert (downloads / "bill (1).pdf").exists()
    assert list(rows(cfg)) == ["bill.pdf"]


# --- configuration and schema ---------------------------------------------------------


def test_the_duplicates_folder_follows_the_language() -> None:
    raw: dict[str, Any] = {"provider": {"name": "fake", "model": "x"}}
    assert parse_config(raw).duplicates_folder == "_Duplicates"
    assert parse_config(raw | {"language": "pt-BR"}).duplicates_folder == "_Duplicados"
    assert parse_config(raw).duplicates == "move"


@pytest.mark.parametrize("name", ["Finance", "_review", "../x", ".dups"])
def test_the_duplicates_folder_must_be_its_own_safe_folder(name: str) -> None:
    raw = {"provider": {"name": "fake", "model": "x"}, "duplicates_folder": name}
    with pytest.raises(ConfigError):
        parse_config(raw)


def test_migration_keeps_rows_and_lets_duplicates_share_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "library.db"
    version = next(m.version for m in load_migrations() if m.name.endswith("_duplicates.sql"))
    before = [m for m in load_migrations() if m.version < version]
    with monkeypatch.context() as patch:
        patch.setattr("file_librarian.db.store.load_migrations", lambda: before)
        old = Store.open(path)
    with old:
        old.conn.execute(
            "INSERT INTO runs (id, mode, trigger, started_at) VALUES (1, 'run', 'manual', 'x')"
        )
        old.conn.execute(
            """INSERT INTO files (id, sha256, original_name, original_path, current_name,
                   current_path, description, tags, status, run_id, processed_at)
               VALUES (7, 'abc', 'a.pdf', '/in/a.pdf', 'a.pdf', '/out/a.pdf', 'invoice',
                       '[]', 'organized', 1, 'x')"""
        )
        old.conn.execute(
            """INSERT INTO operations (run_id, file_id, src_path, dst_path, status, created_at)
               VALUES (1, 7, '/in/a.pdf', '/out/a.pdf', 'done', 'x')"""
        )
        old.conn.execute(
            """INSERT INTO embeddings (file_id, model, dimension, text_hash, vector, created_at)
               VALUES (7, 'm', 1, 'h', x'00000000', 'x')"""
        )
    with Store.open(path) as store:
        assert store.schema_version == len(load_migrations())
        row = store.file_by_id(7)
        assert (row.sha256, row.status, row.duplicate_of) == ("abc", FileStatus.ORGANIZED, None)
        assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []
        assert store.embedding_keys() == {7: ("m", "h")}  # not lost to the cascade
        [run_row] = store.conn.execute("SELECT files_duplicates FROM runs").fetchall()
        assert run_row[0] is None  # not counted before
        # Full-text search still finds it, and keeps following changes.
        match = "SELECT rowid FROM files_fts WHERE files_fts MATCH ?"
        assert store.conn.execute(match, ("invoice",)).fetchall()[0][0] == 7
        store.conn.execute(
            """INSERT INTO files (sha256, original_name, original_path, current_name,
                   current_path, status, processed_at, duplicate_of)
               VALUES ('abc', 'a (1).pdf', '/in/a (1).pdf', 'a (1).pdf', '/d/a (1).pdf',
                       'duplicate', 'x', 7)"""
        )
        assert store.conn.execute(match, ('"a"',)).fetchall() != []
        with pytest.raises(sqlite3.IntegrityError):  # one row per content among the rest
            store.conn.execute(
                """INSERT INTO files (sha256, original_name, original_path, current_name,
                       current_path, status, processed_at)
                   VALUES ('abc', 'b', '/b', 'b', '/b', 'organized', 'x')"""
            )
        with pytest.raises(sqlite3.IntegrityError):  # a duplicate points at its original
            store.conn.execute(
                """INSERT INTO files (sha256, original_name, original_path, current_name,
                       current_path, status, processed_at)
                   VALUES ('def', 'c', '/c', 'c', '/c', 'duplicate', 'x')"""
            )
        with pytest.raises(sqlite3.IntegrityError):  # references are still enforced
            store.conn.execute("DELETE FROM files WHERE id = 7")


# --- reconcile never takes a copy for its original ------------------------------------


def test_reconcile_never_searches_the_duplicates_folder(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    dropped = put(dups(downloads), "kept by hand.pdf", b"same")  # not a duplicate row
    original = finance(downloads) / "bill.pdf"
    original.unlink()
    with Store.open(cfg.database) as store:
        report = reconcile(cfg, store)
    assert report.missing == [original] and report.moved == []
    assert rows(cfg)["bill.pdf"][0] == "missing"
    assert dropped.exists()


def test_reconcile_skips_where_duplicates_are_recorded(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    copy = put(downloads, "bill (1).pdf", b"same")
    run(cfg)
    with Store.open(cfg.database) as store:
        undo_run(store, config=cfg)  # the copy is back in Downloads, recorded there
    original = finance(downloads) / "bill.pdf"
    original.unlink()
    with Store.open(cfg.database) as store:
        report = reconcile(cfg, store)
    assert report.missing == [original] and report.moved == []
    found = rows(cfg)
    assert found["bill.pdf"][0] == "missing"
    assert found["bill (1).pdf"][0] == "skipped" and copy.exists()


def test_reconcile_skips_a_duplicates_folder_used_before(downloads: Path) -> None:
    cfg = fake_config(downloads)
    put(downloads, "bill.pdf", b"same")
    put(downloads, "bill (1).pdf", b"same")
    run(cfg)
    old = dups(downloads)
    (old / "bill (1).pdf").rename(old / "keep.pdf")  # renamed in the old folder
    original = finance(downloads) / "bill.pdf"
    original.unlink()
    renamed = fake_config(downloads, duplicates_folder="_Copies")
    with Store.open(renamed.database) as store:
        report = reconcile(renamed, store)
    assert original in report.missing and report.moved == []
    assert rows(renamed)["bill.pdf"][0] == "missing"


# --- counting and telling the user ----------------------------------------------------


def test_runs_count_the_duplicates_they_set_aside(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    put(downloads, "bill (1).pdf", b"same")
    run(cfg)
    with Store.open(cfg.database) as store:
        [(moved, duplicates)] = store.conn.execute(
            "SELECT files_moved, files_duplicates FROM runs"
        ).fetchall()
    assert (moved, duplicates) == (1, 1)
    found = collect(cfg)
    assert found.last_run is not None and found.last_run.files_duplicates == 1
    assert "1 organized, 0 to review, 1 duplicate(s) set aside" in "\n".join(render(found, cfg))
    assert json.loads(to_json(found, cfg))["last_run"]["files_duplicates"] == 1


NOTE = "Note: files with the same content as one already filed were moved to _Duplicates"


def test_the_note_about_duplicates_waits_for_someone_at_a_terminal(
    tmp_path: Path, downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config_file = write_config(tmp_path, downloads)
    put(downloads, "a.pdf", b"a")
    put(downloads, "a (1).pdf", b"a")
    # A scheduled run: the note goes to its log, and is not used up.
    scheduled = runner.invoke(cli.app, ["run", "--trigger", "schedule", "-c", str(config_file)])
    assert NOTE in scheduled.stdout
    assert "nothing was deleted" in scheduled.stdout and "duplicates: leave" in scheduled.stdout
    status = runner.invoke(cli.app, ["status", "-c", str(config_file)])
    assert NOTE in status.stdout
    # A run by hand whose output is not a terminal (a pipe) shows it, and keeps it.
    again = runner.invoke(cli.app, ["run", "-c", str(config_file)])
    assert NOTE in again.stdout
    # At a terminal it is shown once more, then never again.
    monkeypatch.setattr(cli, "_interactive", lambda: True)
    seen = runner.invoke(cli.app, ["run", "-c", str(config_file)])
    assert NOTE in seen.stdout
    put(downloads, "b.pdf", b"b")
    put(downloads, "b (1).pdf", b"b")
    later = runner.invoke(cli.app, ["run", "-c", str(config_file)])
    assert "dup     b (1).pdf" in later.stdout and NOTE not in later.stdout
    assert NOTE not in runner.invoke(cli.app, ["status", "-c", str(config_file)]).stdout


def test_no_note_without_duplicates(tmp_path: Path, downloads: Path) -> None:
    config_file = write_config(tmp_path, downloads)
    put(downloads, "a.pdf", b"a")
    assert NOTE not in runner.invoke(cli.app, ["run", "-c", str(config_file)]).stdout


def row_of(cfg: Config) -> tuple[str, str | None, str | None, str]:
    """(status, category, description, current name) of the only non-duplicate row."""
    with Store.open(cfg.database) as store:
        [row] = [r for r in store.all_files() if r.duplicate_of is None]
    return row.status.value, row.category, row.description, row.current_name


def test_following_the_advice_files_the_waiting_copy(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    run(cfg, FakeProvider(default=answer(description="old words")))
    (finance(downloads) / "bill.pdf").unlink()
    copy = put(downloads, "bill (1).pdf", b"same")
    waiting = run(cfg)
    assert (copy, SkipReason.NOT_WHERE_LEFT) in waiting.scan.skipped
    with Store.open(cfg.database) as store:
        report = reconcile(cfg, store)
    # The copy in Downloads is not the file flib moved: the row is missing, not repointed.
    assert report.missing == [finance(downloads) / "bill.pdf"] and report.moved == []
    with Store.open(cfg.database) as store:
        store.conn.execute("DELETE FROM classification_cache")
    run(cfg, FakeProvider(default=answer("Contracts", description="new words")))
    assert not copy.exists()
    moved = library(downloads) / "Contracts" / "bill (1).pdf"
    assert moved.read_bytes() == b"same"
    # The row now carries the new classification.
    assert row_of(cfg) == ("organized", "Contracts", "new words", "bill (1).pdf")


def test_reconcile_follows_the_very_file_dragged_back(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    dragged = downloads / "bill.pdf"
    (finance(downloads) / "bill.pdf").rename(dragged)
    with Store.open(cfg.database) as store:
        report = reconcile(cfg, store)
    assert report.moved == [(finance(downloads) / "bill.pdf", dragged)]
    assert row_of(cfg)[0] == "skipped"
    outcome = run(cfg)
    assert outcome.report is not None and outcome.report.items == []
    assert dragged.exists()


def test_a_takeover_that_keeps_failing_stops_at_max_attempts(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    go_missing(cfg, downloads)
    with Store.open(cfg.database) as store:
        store.conn.execute("DELETE FROM classification_cache")
    copy = put(downloads, "bill.pdf", b"same")
    calls = 0
    for _ in range(cfg.limits.max_attempts + 2):
        provider = FakeProvider()  # no answer: the classification fails
        run(cfg, provider)
        calls += len(provider.calls)
    assert calls == cfg.limits.max_attempts
    assert copy.exists() and row_of(cfg)[0] == "missing"
    found = collect(cfg)
    assert found.failed_total == 1
    [group] = found.failed
    assert (group.attempts, group.retried) == (cfg.limits.max_attempts, False)


@pytest.mark.parametrize("with_config", [True, False])
def test_a_crash_after_a_takeover_move_writes_the_new_record(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch, with_config: bool
) -> None:
    put(downloads, "bill.pdf", b"same")
    run(cfg, FakeProvider(default=answer(description="old words")))
    go_missing(cfg, downloads)
    with Store.open(cfg.database) as store:
        store.conn.execute("DELETE FROM classification_cache")
    put(downloads, "bill.pdf", b"same")

    def move_then_crash(src: Path, dst: Path) -> None:
        move_no_replace(src, dst)
        raise Crash

    with monkeypatch.context() as patch, pytest.raises(Crash):
        patch.setattr("file_librarian.executor.move_no_replace", move_then_crash)
        run(cfg, FakeProvider(default=answer("Contracts", description="new words")))
    with Store.open(cfg.database) as store:
        assert executor.recover(store, cfg if with_config else None) == 1
    assert row_of(cfg) == ("organized", "Contracts", "new words", "bill.pdf")
    assert (library(downloads) / "Contracts" / "bill.pdf").exists()


@pytest.mark.usefixtures("cross_volume")
def test_a_cross_volume_takeover_rolled_back_leaves_the_row_missing(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    go_missing(cfg, downloads)
    before = snapshot(cfg)
    copy = put(downloads, "bill.pdf", b"same")
    real = fsops._copyfile

    def copy_then_crash(src: Path, dst: Path) -> None:
        real(src, dst)
        raise Crash

    with monkeypatch.context() as patch, pytest.raises(Crash):
        patch.setattr(fsops, "_copyfile", copy_then_crash)
        run(cfg)
    with Store.open(cfg.database) as store:
        assert executor.recover(store, cfg) == 1
        assert store.operations()[-1].status is OperationStatus.FAILED
    assert snapshot(cfg) == before and copy.read_bytes() == b"same"


def test_status_and_the_summary_count_copies_waiting_for_reconcile(
    tmp_path: Path, downloads: Path
) -> None:
    config_file = write_config(tmp_path, downloads)
    put(downloads, "bill.pdf", b"same")
    runner.invoke(cli.app, ["run", "-c", str(config_file)])
    (library(downloads) / "Other" / "bill.pdf").unlink()
    put(downloads, "bill (1).pdf", b"same")
    scheduled = runner.invoke(cli.app, ["run", "--trigger", "schedule", "-c", str(config_file)])
    assert "Summary: 1 wait for `flib reconcile`. Run `flib status` for details." in (
        scheduled.stdout
    )
    status = runner.invoke(cli.app, ["status", "-c", str(config_file)])
    assert (
        "1 waiting for `flib reconcile` (copies of filed files that are not where flib left them)"
        in status.stdout
    )
    data = json.loads(runner.invoke(cli.app, ["status", "--json", "-c", str(config_file)]).stdout)
    assert data["last_run"]["skipped"]["not_where_left"] == 1


def test_reconcile_leaves_an_unreadable_duplicate_as_it_is(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    put(downloads, "bill (1).pdf", b"same")
    run(cfg)
    copy = dups(downloads) / "bill (1).pdf"
    copy.chmod(0)
    try:
        with Store.open(cfg.database) as store:
            report = reconcile(cfg, store)
    finally:
        copy.chmod(0o644)
    assert report.unchecked == [copy] and report.missing == []
    assert rows(cfg)["bill (1).pdf"][0] == "duplicate"

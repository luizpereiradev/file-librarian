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

from file_librarian import cli, executor, pipeline
from file_librarian.config import Config, ConfigError, parse_config
from file_librarian.db.store import Store, load_migrations
from file_librarian.embeddings import embeddable
from file_librarian.fsops import move_no_replace
from file_librarian.models import FileStatus, OperationStatus, RunTrigger
from file_librarian.providers.fake import FakeProvider
from file_librarian.reconcile import reconcile
from file_librarian.reprocess import select
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


def test_when_the_original_is_gone_the_copy_is_filed_normally(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    (finance(downloads) / "bill.pdf").unlink()  # deleted by hand
    put(downloads, "bill.pdf", b"same")

    outcome = run(cfg)

    assert outcome.report is not None
    assert [i.status for i in outcome.report.items] == [FileStatus.ORGANIZED]
    assert (finance(downloads) / "bill.pdf").read_bytes() == b"same"
    assert not dups(downloads).exists()
    # The content keeps one row, now pointing at the new copy.
    assert rows(cfg) == {"bill.pdf": ("organized", None, rows(cfg)["bill.pdf"][2])}


def test_when_the_original_changed_the_copy_is_no_duplicate(downloads: Path, cfg: Config) -> None:
    put(downloads, "notes.txt", b"first")
    run(cfg)
    (finance(downloads) / "notes.txt").write_bytes(b"edited since")
    put(downloads, "notes.txt", b"first")

    outcome = run(cfg)

    assert outcome.report is not None
    assert [i.status for i in outcome.report.items] == [FileStatus.ORGANIZED]
    assert (finance(downloads) / "notes-2.txt").read_bytes() == b"first"
    assert (finance(downloads) / "notes.txt").read_bytes() == b"edited since"
    assert not dups(downloads).exists()


def test_a_missing_original_lets_the_copy_be_filed(downloads: Path, cfg: Config) -> None:
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    (finance(downloads) / "bill.pdf").unlink()
    with Store.open(cfg.database) as store:
        reconcile(cfg, store)
    assert rows(cfg)["bill.pdf"][0] == "missing"
    put(downloads, "bill.pdf", b"same")
    run(cfg)
    assert rows(cfg)["bill.pdf"][0] == "organized"
    assert (finance(downloads) / "bill.pdf").exists()


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
    assert json.loads(to_json(found, cfg))["duplicates"] == {"folder": "_Duplicates", "total": 1}


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

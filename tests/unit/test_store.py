from __future__ import annotations

import sqlite3
import unicodedata
from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import pytest

from file_librarian.db import Store, normalize
from file_librarian.db.store import MigrationError, _month_start, load_migrations
from file_librarian.models import RunMode, RunTrigger


def insert_file(store: Store, name: str, description: str, sha: str = "abc") -> int:
    store.conn.execute(
        "INSERT INTO runs (id, mode, trigger, started_at) VALUES (1, 'run', 'manual', '2026-11-16')"
        " ON CONFLICT DO NOTHING"
    )
    cur = store.conn.execute(
        """INSERT INTO files (sha256, original_name, original_path, current_name, current_path,
                              description, tags, status, run_id, processed_at)
           VALUES (?, ?, ?, ?, ?, ?, '[]', 'organized', 1, '2026-11-16')""",
        (sha, name, f"/in/{name}", name, f"/out/{name}", description),
    )
    assert cur.lastrowid is not None
    return cur.lastrowid


def search(store: Store, query: str) -> list[str]:
    rows = store.conn.execute(
        "SELECT f.current_name FROM files_fts JOIN files f ON f.id = files_fts.rowid "
        "WHERE files_fts MATCH ? ORDER BY rank",
        (query,),
    ).fetchall()
    return [r[0] for r in rows]


@pytest.fixture
def store(tmp_path: Path) -> Iterator[Store]:
    with Store.open(tmp_path / "data" / "library.db") as s:
        yield s


def test_open_creates_schema_in_wal_mode(store: Store) -> None:
    assert store.schema_version == len(load_migrations())
    assert store.conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert store.conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    tables = {
        r[0] for r in store.conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {"files", "runs", "operations", "corrections", "files_fts"} <= tables


def test_reopen_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "library.db"
    Store.open(path).close()
    with Store.open(path) as again:
        assert again.migrate() == len(load_migrations())


def test_newer_database_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "library.db"
    Store.open(path).close()
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA user_version = 999")
    conn.close()
    with pytest.raises(MigrationError, match="newer"):
        Store.open(path)


def test_search_ignores_accents(store: Store) -> None:
    insert_file(store, "orçamento-reforma.pdf", "Orçamento da reforma da cozinha")
    assert search(store, "orcamento") == ["orçamento-reforma.pdf"]
    assert search(store, "orçamento") == ["orçamento-reforma.pdf"]


def test_fts_follows_updates_and_deletes(store: Store) -> None:
    file_id = insert_file(store, "doc.pdf", "electricity invoice")
    store.conn.execute("UPDATE files SET description = 'water bill' WHERE id = ?", (file_id,))
    assert search(store, "electricity") == []
    assert search(store, "water") == ["doc.pdf"]
    store.conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
    assert search(store, "water") == []


def test_status_is_constrained(store: Store) -> None:
    file_id = insert_file(store, "x.pdf", "x")
    with pytest.raises(sqlite3.IntegrityError):
        store.conn.execute("UPDATE files SET status = 'deleted' WHERE id = ?", (file_id,))


def test_normalize_composes_decomposed_names() -> None:
    decomposed = unicodedata.normalize("NFD", "orçamento")
    assert decomposed != "orçamento"
    assert normalize(decomposed) == "orçamento"


def test_migration_0009_keeps_runs_and_their_references(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "library.db"
    before = [m for m in load_migrations() if m.version < 9]
    with monkeypatch.context() as m:
        m.setattr("file_librarian.db.store.load_migrations", lambda: before)
        with Store.open(path) as old:
            file_id = insert_file(old, "a.pdf", "d")
            old.conn.execute(
                "INSERT INTO runs (id, mode, trigger, started_at) VALUES (2, 'run', 'mcp', 'x')"
            )
            old.conn.execute(
                """INSERT INTO operations (run_id, file_id, src_path, dst_path, status, created_at)
                   VALUES (2, ?, '/in/a.pdf', '/out/a.pdf', 'done', 'x')""",
                (file_id,),
            )
            with pytest.raises(sqlite3.IntegrityError):
                old.conn.execute(
                    "INSERT INTO runs (mode, trigger, started_at) VALUES ('run', 'reprocess', 'x')"
                )
    with Store.open(path) as store:
        assert store.schema_version == len(load_migrations())
        runs = store.conn.execute("SELECT id, trigger FROM runs ORDER BY id").fetchall()
        assert [tuple(r) for r in runs] == [(1, "manual"), (2, "mcp")]
        assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []
        [op] = store.operations()
        assert op.previous is None and store.index_changes(2) == []
        store.conn.execute(
            "INSERT INTO runs (mode, trigger, started_at) VALUES ('run', 'reprocess', 'x')"
        )
        with pytest.raises(sqlite3.IntegrityError):  # references are still enforced
            store.conn.execute("DELETE FROM runs WHERE id = 2")


def test_migration_0010_keeps_rows_and_leaves_the_new_values_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "library.db"
    before = [m for m in load_migrations() if m.version < 10]
    with monkeypatch.context() as patch:
        patch.setattr("file_librarian.db.store.load_migrations", lambda: before)
        old = Store.open(path)
    with old:
        assert old.schema_version == 9
        file_id = insert_file(old, "a.pdf", "d")
        old.conn.execute("UPDATE files SET status = 'review' WHERE id = ?", (file_id,))
        old.conn.execute("UPDATE runs SET finished_at = '2026-11-16', files_seen = 1 WHERE id = 1")
        old.conn.execute(
            """INSERT INTO operations (run_id, file_id, src_path, dst_path, status, created_at)
               VALUES (1, ?, '/in/a.pdf', '/out/a.pdf', 'undone', 'x')""",
            (file_id,),
        )
        old.change_index(1, "files.description", file_id, "d", "new")
        with pytest.raises(sqlite3.IntegrityError):  # not a target before 0010
            old.conn.execute(
                """INSERT INTO index_changes (run_id, target, row_id, status, created_at)
                   VALUES (1, 'files.review_reason', ?, 'done', 'x')""",
                (file_id,),
            )
    with Store.open(path) as store:
        assert store.schema_version == len(load_migrations())
        row = store.file_by_id(file_id)
        assert (row.status.value, row.description, row.review) == ("review", "new", (None, None))
        [op] = store.operations()
        assert (op.status.value, op.undone_at) == ("undone", None)
        run = store.conn.execute("SELECT files_seen, cost_usd FROM runs").fetchone()
        assert tuple(run) == (1, None)
        [change] = store.index_changes(1)
        assert (change.target, change.old_value) == ("files.description", "d")
        assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []
        # The new targets and codes are accepted, and only known codes.
        store.change_index(1, "files.review_reason", file_id, None, "low_confidence")
        with pytest.raises(sqlite3.IntegrityError):
            store.conn.execute("UPDATE files SET review_reason = 'bored' WHERE id = ?", (file_id,))
        stats = store.stats(month_start="2026-11-01")
        assert (stats.month_cost_usd, stats.month_unpriced_runs) == (0.0, 1)


def test_migration_0014_keeps_index_changes_and_leaves_layout_values_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "library.db"
    before = [m for m in load_migrations() if m.version < 14]
    with monkeypatch.context() as patch:
        patch.setattr("file_librarian.db.store.load_migrations", lambda: before)
        old = Store.open(path)
    with old:
        assert old.schema_version == 13
        file_id = insert_file(old, "a.pdf", "d")
        old.change_index(1, "files.description", file_id, "d", "new")
        with pytest.raises(sqlite3.IntegrityError):  # not a target before 0014
            old.conn.execute(
                """INSERT INTO index_changes (run_id, target, row_id, status, created_at)
                   VALUES (1, 'files.issuer', ?, 'done', 'x')""",
                (file_id,),
            )
    with Store.open(path) as store:
        assert store.schema_version == len(load_migrations())
        row = store.file_by_id(file_id)
        assert (row.description, row.document_date, row.issuer) == ("new", None, None)
        [change] = store.index_changes(1)
        assert (change.target, change.old_value, change.new_value) == (
            "files.description",
            "d",
            "new",
        )
        store.change_index(1, "files.issuer", file_id, None, "Acme")
        store.change_index(1, "files.document_date", file_id, None, "2026-01-02")
        store.change_index(1, "files.layout_date", file_id, None, "2026-01-03")
        row = store.file_by_id(file_id)
        assert (row.document_date, row.issuer) == ("2026-01-02", "Acme")
        assert row.layout_date == "2026-01-03"
        assert store.flib_folders() == set()
        store.add_flib_folders(1, [Path("/lib/Finance/Acme")])
        assert store.flib_folders() == {Path("/lib/Finance/Acme")}
        assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_migration_0015_adds_structured_fields_and_journals_them(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "library.db"
    before = [m for m in load_migrations() if m.version < 15]
    with monkeypatch.context() as patch:
        patch.setattr("file_librarian.db.store.load_migrations", lambda: before)
        old = Store.open(path)
    with old:
        assert old.schema_version == 14
        file_id = insert_file(old, "a.pdf", "d")
        old.change_index(1, "files.issuer", file_id, None, "Acme")
        with pytest.raises(sqlite3.IntegrityError):  # not a target before 0015
            old.conn.execute(
                """INSERT INTO index_changes (run_id, target, row_id, status, created_at)
                   VALUES (1, 'files.amount_minor', ?, 'done', 'x')""",
                (file_id,),
            )
    with Store.open(path) as store:
        assert store.schema_version == len(load_migrations()) >= 15
        row = store.file_by_id(file_id)
        assert (row.issuer, row.amount_minor, row.currency, row.kind) == ("Acme", None, None, None)
        [change] = store.index_changes(1)
        assert (change.target, change.new_value) == ("files.issuer", "Acme")
        for target, value in (
            ("files.amount_minor", "10391"),
            ("files.currency", "USD"),
            ("files.due_date", "2026-09-20"),
            ("files.period", "2026-08"),
            ("files.kind", "bill"),
        ):
            store.change_index(1, target, file_id, None, value)
        row = store.file_by_id(file_id)
        assert (row.amount_minor, row.currency, row.due_date, row.period, row.kind) == (
            10391,  # INTEGER affinity: the journal's text comes back a number
            "USD",
            "2026-09-20",
            "2026-08",
            "bill",
        )
        # Undo puts each back: the journaled text matches the stored integer.
        assert all(store.revert_index_change(c) for c in store.index_changes(1))
        row = store.file_by_id(file_id)
        assert (row.issuer, row.amount_minor, row.kind) == (None, None, None)
        for column, bad in (("kind", "spaceship"), ("amount_minor", -1), ("currency", "EURO")):
            with pytest.raises(sqlite3.IntegrityError):
                store.conn.execute(
                    f"UPDATE files SET {column} = ? WHERE id = ?",  # noqa: S608
                    (bad, file_id),
                )
        assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_month_cost_counts_this_months_finished_runs(store: Store) -> None:
    def finished(started: str, cost: float | None) -> None:
        run_id = store.start_run(RunMode.RUN, RunTrigger.MANUAL)
        store.conn.execute("UPDATE runs SET started_at = ? WHERE id = ?", (started, run_id))
        store.finish_run(run_id, seen=1, moved=1, review=0, errors=0, cost_usd=cost)

    finished("2026-10-31T23:59:59+00:00", 5.0)  # last month
    finished("2026-11-01T00:00:00+00:00", 0.25)
    finished("2026-11-17T10:00:00+00:00", 0.5)
    finished("2026-11-18T10:00:00+00:00", None)  # prices unknown
    store.start_run(RunMode.RUN, RunTrigger.MANUAL)  # still running: not counted
    stats = store.stats(month_start="2026-11-01T00:00:00+00:00")
    assert stats.month_cost_usd == pytest.approx(0.75)
    assert stats.month_unpriced_runs == 1
    assert stats.last_run is not None and stats.last_run.cost_usd is None


def test_the_default_month_starts_at_local_midnight_on_the_first(store: Store) -> None:
    run_id = store.start_run(RunMode.RUN, RunTrigger.MANUAL)
    store.finish_run(run_id, seen=0, moved=0, review=0, errors=0, cost_usd=0.125)
    assert store.stats().month_cost_usd == 0.125  # started now: this month
    start = datetime.fromisoformat(_month_start()).astimezone()
    assert (start.day, start.hour, start.minute) == (1, 0, 0)


def test_migration_0017_adds_settled_and_journals_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "library.db"
    before = [m for m in load_migrations() if m.version < 17]
    with monkeypatch.context() as patch:
        patch.setattr("file_librarian.db.store.load_migrations", lambda: before)
        old = Store.open(path)
    with old:
        assert old.schema_version == 16
        file_id = insert_file(old, "a.pdf", "d")
        old.change_index(1, "files.due_date", file_id, None, "2026-11-23")
        with pytest.raises(sqlite3.IntegrityError):  # not a target before 0017
            old.conn.execute(
                """INSERT INTO index_changes (run_id, target, row_id, status, created_at)
                   VALUES (1, 'files.settled_at', ?, 'done', 'x')""",
                (file_id,),
            )
    with Store.open(path) as store:
        assert store.schema_version == len(load_migrations()) >= 17
        row = store.file_by_id(file_id)
        assert (row.due_date, row.settled_at, row.due_reminded_for) == ("2026-11-23", None, None)
        [change] = store.index_changes(1)
        assert (change.target, change.new_value) == ("files.due_date", "2026-11-23")
        store.change_index(1, "files.settled_at", file_id, None, "2026-11-20T12:00:00+00:00")
        assert store.file_by_id(file_id).settled_at == "2026-11-20T12:00:00+00:00"
        store.set_reminded([(file_id, "2026-11-23")])
        assert store.file_by_id(file_id).due_reminded_for == "2026-11-23"
        assert all(store.revert_index_change(c) for c in reversed(store.index_changes(1)))
        assert store.file_by_id(file_id).settled_at is None
        assert store.conn.execute("PRAGMA foreign_key_check").fetchall() == []

from __future__ import annotations

import sqlite3
import unicodedata
from collections.abc import Iterator
from pathlib import Path

import pytest

from file_librarian.db import Store, normalize
from file_librarian.db.store import MigrationError, load_migrations


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

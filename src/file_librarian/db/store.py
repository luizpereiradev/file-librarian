"""The SQLite store: connection settings and schema migrations.

- WAL mode with a busy timeout, so MCP reads never block a running job.
- Foreign keys on.
- Migrations are numbered ``.sql`` files in ``migrations/``; the applied
  version is kept in ``PRAGMA user_version``. A published migration is never
  edited; schema changes add a new file.
- Names and paths are stored in NFC (see ``normalize``), so a search for
  "orçamento" finds the file whatever form the disk returned.
"""

from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path
from types import TracebackType

from file_librarian.models import FileRecord, FileState, FileStatus, OperationStatus

_MIGRATION_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.sql$")
BUSY_TIMEOUT_MS = 5000


class MigrationError(Exception):
    pass


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    sql: str


def normalize(text: str) -> str:
    """NFC-normalize a name or path before it goes into the database."""
    return unicodedata.normalize("NFC", text)


def load_migrations() -> list[Migration]:
    package = resources.files("file_librarian.db.migrations")
    found: list[Migration] = []
    for entry in package.iterdir():
        match = _MIGRATION_NAME.match(entry.name)
        if match:
            found.append(Migration(int(match.group(1)), entry.name, entry.read_text("utf-8")))
    found.sort(key=lambda m: m.version)
    for expected, migration in enumerate(found, start=1):
        if migration.version != expected:
            raise MigrationError(f"migrations must be numbered 1..N without gaps: {migration.name}")
    return found


class Store:
    """An open database. Use as a context manager."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    @classmethod
    def open(cls, path: Path) -> Store:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        store = cls(conn)
        store.migrate()
        return store

    @property
    def schema_version(self) -> int:
        return int(self.conn.execute("PRAGMA user_version").fetchone()[0])

    def migrate(self) -> int:
        """Apply pending migrations, each in its own transaction. Returns the new version."""
        migrations = load_migrations()
        current = self.schema_version
        if current > len(migrations):
            raise MigrationError(
                f"database schema version {current} is newer than this flib "
                f"({len(migrations)}); upgrade file-librarian"
            )
        for migration in migrations[current:]:
            try:
                self.conn.execute("BEGIN IMMEDIATE")
                for statement in _split_sql(migration.sql):
                    self.conn.execute(statement)
                self.conn.execute(f"PRAGMA user_version = {migration.version}")
                self.conn.execute("COMMIT")
            except sqlite3.Error as exc:
                self.conn.execute("ROLLBACK")
                raise MigrationError(f"{migration.name} failed: {exc}") from exc
        return self.schema_version

    def file_state(self, sha256: str) -> FileState | None:
        """Status and attempt count of a known file, or ``None`` if it was never seen."""
        row = self.conn.execute(
            "SELECT status, attempts FROM files WHERE sha256 = ?", (sha256,)
        ).fetchone()
        if row is None:
            return None
        return FileState(FileStatus(row["status"]), int(row["attempts"]))

    @contextmanager
    def transaction(self) -> Iterator[None]:
        """Group writes so a crash leaves all of them or none."""
        self.conn.execute("BEGIN IMMEDIATE")
        try:
            yield
        except BaseException:
            self.conn.execute("ROLLBACK")
            raise
        self.conn.execute("COMMIT")

    # --- runs -----------------------------------------------------------------

    def start_run(self, mode: str, trigger: str) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (mode, trigger, started_at) VALUES (?, ?, ?)",
            (mode, trigger, _now()),
        )
        return _rowid(cur)

    def finish_run(self, run_id: int, *, seen: int, moved: int, review: int, errors: int) -> None:
        self.conn.execute(
            """UPDATE runs SET finished_at = ?, files_seen = ?, files_moved = ?,
                              files_review = ?, errors = ? WHERE id = ?""",
            (_now(), seen, moved, review, errors, run_id),
        )

    def last_run_with_operations(self) -> int | None:
        row = self.conn.execute("SELECT MAX(run_id) FROM operations").fetchone()
        return None if row is None or row[0] is None else int(row[0])

    # --- files ----------------------------------------------------------------

    def upsert_file(self, record: FileRecord, *, run_id: int) -> int:
        """Insert or update the row for ``record.sha256``; returns its id."""
        r = record
        self.conn.execute(
            """INSERT INTO files (sha256, original_name, original_path, current_name,
                   current_path, extension, size_bytes, source_url, category, description,
                   tags, confidence, status, attempts, last_error, provider, model, run_id,
                   processed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(sha256) DO UPDATE SET
                   current_name = excluded.current_name, current_path = excluded.current_path,
                   category = excluded.category, description = excluded.description,
                   tags = excluded.tags, confidence = excluded.confidence,
                   status = excluded.status, attempts = files.attempts + excluded.attempts,
                   last_error = excluded.last_error, provider = excluded.provider,
                   model = excluded.model, run_id = excluded.run_id,
                   processed_at = excluded.processed_at""",
            (
                r.sha256, normalize(r.original_path.name), normalize(str(r.original_path)),
                normalize(r.current_path.name), normalize(str(r.current_path)), r.extension,
                r.size_bytes, r.source_url, r.category, r.description,
                json.dumps(list(r.tags), ensure_ascii=False), r.confidence, r.status.value,
                r.attempts, r.last_error, r.provider, r.model, run_id, _now(),
            ),
        )  # fmt: skip
        row = self.conn.execute("SELECT id FROM files WHERE sha256 = ?", (r.sha256,)).fetchone()
        return int(row[0])

    def set_file_location(self, file_id: int, path: Path, status: FileStatus) -> None:
        self.conn.execute(
            "UPDATE files SET current_name = ?, current_path = ?, status = ? WHERE id = ?",
            (normalize(path.name), normalize(str(path)), status.value, file_id),
        )

    def mark_file_failed(self, file_id: int, error: str) -> None:
        self.conn.execute(
            """UPDATE files SET status = 'failed', attempts = attempts + 1, last_error = ?
               WHERE id = ?""",
            (error[:500], file_id),
        )

    def file_by_id(self, file_id: int) -> sqlite3.Row:
        row = self.conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if row is None:
            raise LookupError(f"no file with id {file_id}")
        return row  # type: ignore[no-any-return]

    def file_by_current_path(self, path: Path) -> sqlite3.Row | None:
        row = self.conn.execute(
            "SELECT * FROM files WHERE current_path = ?", (normalize(str(path)),)
        ).fetchone()
        return row  # type: ignore[no-any-return]

    # --- operations (the journal behind undo) ------------------------------------

    def add_operation(self, run_id: int, file_id: int, src: Path, dst: Path) -> int:
        cur = self.conn.execute(
            """INSERT INTO operations (run_id, file_id, src_path, dst_path, status, created_at)
               VALUES (?, ?, ?, ?, 'planned', ?)""",
            (run_id, file_id, str(src), str(dst), _now()),
        )
        return _rowid(cur)

    def set_operation(self, op_id: int, status: OperationStatus, dst: Path | None = None) -> None:
        if dst is None:
            self.conn.execute(
                "UPDATE operations SET status = ? WHERE id = ?", (status.value, op_id)
            )
        else:
            self.conn.execute(
                "UPDATE operations SET status = ?, dst_path = ? WHERE id = ?",
                (status.value, str(dst), op_id),
            )

    def operations(
        self, *, run_id: int | None = None, status: str | None = None
    ) -> list[sqlite3.Row]:
        sql = "SELECT * FROM operations WHERE 1 = 1"
        args: list[object] = []
        if run_id is not None:
            sql += " AND run_id = ?"
            args.append(run_id)
        if status is not None:
            sql += " AND status = ?"
            args.append(status)
        return list(self.conn.execute(sql + " ORDER BY id", args))

    def last_done_operation(self, file_id: int) -> sqlite3.Row | None:
        row = self.conn.execute(
            """SELECT * FROM operations WHERE file_id = ? AND status = 'done'
               ORDER BY id DESC LIMIT 1""",
            (file_id,),
        ).fetchone()
        return row  # type: ignore[no-any-return]

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()


def _rowid(cur: sqlite3.Cursor) -> int:
    if cur.lastrowid is None:
        raise RuntimeError("insert did not return a row id")
    return cur.lastrowid


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _split_sql(script: str) -> list[str]:
    """Split a migration into statements, keeping trigger bodies intact."""
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        if not buffer and (not line.strip() or line.lstrip().startswith("--")):
            continue
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise MigrationError(f"incomplete SQL statement: {buffer.strip()[:60]}...")
    return statements

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

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from types import TracebackType

from file_librarian.models import FileState, FileStatus

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

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
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from importlib import resources
from pathlib import Path
from types import TracebackType

from file_librarian.models import (
    CopyPhase,
    FileRecord,
    FileState,
    FileStatus,
    OperationStatus,
    RunMode,
    RunTrigger,
)

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


@dataclass(frozen=True, slots=True)
class FileRow:
    """A row of ``files`` as read back."""

    id: int
    sha256: str
    original_name: str
    original_path: Path
    current_name: str
    current_path: Path
    status: FileStatus
    category: str | None
    description: str | None
    tags: list[str]
    attempts: int
    processed_at: str
    size_bytes: int | None
    is_unit: bool
    name_choice: str | None = None
    """``original`` or ``proposed`` (#59); ``None`` when no rename was considered."""
    name_choice_reason: str | None = None
    confidence: float | None = None
    """The model's confidence in its answer; ``None`` if it was not classified."""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> FileRow:
        try:
            tags = [str(t) for t in json.loads(row["tags"] or "[]")]
        except (ValueError, TypeError):
            tags = []
        return cls(
            id=int(row["id"]),
            sha256=str(row["sha256"]),
            original_name=str(row["original_name"]),
            original_path=Path(row["original_path"]),
            current_name=str(row["current_name"]),
            current_path=Path(row["current_path"]),
            status=FileStatus(row["status"]),
            category=row["category"],
            description=row["description"],
            tags=tags,
            attempts=int(row["attempts"]),
            processed_at=str(row["processed_at"]),
            size_bytes=None if row["size_bytes"] is None else int(row["size_bytes"]),
            is_unit=bool(row["is_unit"]),
            name_choice=row["name_choice"],
            name_choice_reason=row["name_choice_reason"],
            confidence=None if row["confidence"] is None else float(row["confidence"]),
        )


@dataclass(frozen=True, slots=True)
class OperationRow:
    """A journal entry of ``operations`` as read back."""

    id: int
    run_id: int
    file_id: int
    src_path: Path
    dst_path: Path
    status: OperationStatus
    src_identity: tuple[int, int] | None
    """The source's (st_dev, st_ino) when journaled; ``None`` for older rows."""
    finder_tag: str | None = None
    """The Finder tag this move added, which undo removes; ``None`` if none."""
    fix: tuple[FileStatus, str | None] | None = None
    """For a ``flib fix``, the (status, category) it gives the file; ``None`` for a run."""
    copy_phase: CopyPhase | None = None
    """How far a move (``planned``) or its undo (``done``) to another volume got."""
    copy_key: str | None = None
    """Names that copy's staging and trash paths (``fsops.new_copy_key``)."""
    copy_volumes: tuple[int | None, int | None] = (None, None)
    """Devices of that copy's source and destination folders when it started."""

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> OperationRow:
        dev, ino = row["src_dev"], row["src_ino"]
        return cls(
            id=int(row["id"]),
            run_id=int(row["run_id"]),
            file_id=int(row["file_id"]),
            src_path=Path(row["src_path"]),
            dst_path=Path(row["dst_path"]),
            status=OperationStatus(row["status"]),
            src_identity=None if dev is None or ino is None else (int(dev), int(ino)),
            finder_tag=row["finder_tag"],
            fix=None
            if row["fix_status"] is None
            else (FileStatus(row["fix_status"]), row["fix_category"]),
            copy_phase=None if row["copy_phase"] is None else CopyPhase(row["copy_phase"]),
            copy_key=row["copy_key"],
            copy_volumes=(row["copy_src_dev"], row["copy_dst_dev"]),
        )


@dataclass(frozen=True, slots=True)
class RunRow:
    """A row of ``runs`` as read back."""

    id: int
    trigger: RunTrigger
    started_at: str
    finished_at: str | None
    files_seen: int | None
    files_moved: int | None
    files_review: int | None
    errors: int | None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> RunRow:
        return cls(
            id=int(row["id"]),
            trigger=RunTrigger(row["trigger"]),
            started_at=str(row["started_at"]),
            finished_at=row["finished_at"],
            files_seen=row["files_seen"],
            files_moved=row["files_moved"],
            files_review=row["files_review"],
            errors=row["errors"],
        )


@dataclass(frozen=True, slots=True)
class HistoryRow:
    """One journaled move of a file, with what started it."""

    operation: OperationRow
    trigger: RunTrigger
    created_at: str


@dataclass(frozen=True, slots=True)
class LibraryStats:
    """Counts over the whole index (the MCP ``get_stats`` tool)."""

    by_status: dict[str, int]
    """Every status, zero included."""
    by_category: dict[str, int]
    """Organized files per category, most first."""
    filed_bytes: int
    """Indexed size of the files flib filed: organized or in review."""
    last_run: RunRow | None
    """The newest finished run that was not a single-file ``flib fix``."""


@dataclass(frozen=True, slots=True)
class CorrectionRow:
    """A correction joined with what the index knows about its file."""

    field: str
    old_value: str | None
    new_value: str
    extension: str
    is_unit: bool
    description: str | None
    original_name: str
    current_name: str

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> CorrectionRow:
        return cls(
            field=str(row["field"]),
            old_value=row["old_value"],
            new_value=str(row["new_value"]),
            extension=str(row["extension"] or ""),
            is_unit=bool(row["is_unit"]),
            description=row["description"],
            original_name=str(row["original_name"]),
            current_name=str(row["current_name"]),
        )


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
        # Checkpoints reach the disk itself (F_FULLFSYNC); see set_copy_phase for commits.
        conn.execute("PRAGMA checkpoint_fullfsync = ON")
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

    def start_run(self, mode: RunMode, trigger: RunTrigger) -> int:
        cur = self.conn.execute(
            "INSERT INTO runs (mode, trigger, started_at) VALUES (?, ?, ?)",
            (mode.value, trigger.value, _now()),
        )
        return _rowid(cur)

    def finish_run(self, run_id: int, *, seen: int, moved: int, review: int, errors: int) -> None:
        self.conn.execute(
            """UPDATE runs SET finished_at = ?, files_seen = ?, files_moved = ?,
                              files_review = ?, errors = ? WHERE id = ?""",
            (_now(), seen, moved, review, errors, run_id),
        )

    def run_trigger(self, run_id: int) -> RunTrigger:
        row = self.conn.execute("SELECT trigger FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise LookupError(f"no run with id {run_id}")
        return RunTrigger(row[0])

    def last_run_with_operations(self) -> int | None:
        """The newest run that actually moved something (done or since undone)."""
        row = self.conn.execute(
            "SELECT MAX(run_id) FROM operations WHERE status IN ('done', 'undone')"
        ).fetchone()
        return None if row is None or row[0] is None else int(row[0])

    # --- files ----------------------------------------------------------------

    def upsert_file(self, record: FileRecord, *, run_id: int) -> int:
        """Insert or update the row for ``record.sha256``; returns its id."""
        r = record
        self.conn.execute(
            """INSERT INTO files (sha256, original_name, original_path, current_name,
                   current_path, extension, size_bytes, source_url, category, description,
                   tags, confidence, status, attempts, last_error, provider, model, run_id,
                   processed_at, is_unit, name_choice, name_choice_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(sha256) DO UPDATE SET
                   current_name = excluded.current_name, current_path = excluded.current_path,
                   category = excluded.category, description = excluded.description,
                   tags = excluded.tags, confidence = excluded.confidence,
                   status = excluded.status, attempts = files.attempts + excluded.attempts,
                   last_error = excluded.last_error, provider = excluded.provider,
                   model = excluded.model, run_id = excluded.run_id,
                   processed_at = excluded.processed_at, name_choice = excluded.name_choice,
                   name_choice_reason = excluded.name_choice_reason""",
            (
                r.sha256, normalize(r.original_path.name), normalize(str(r.original_path)),
                normalize(r.current_path.name), normalize(str(r.current_path)), r.extension,
                r.size_bytes, r.source_url, r.category, r.description,
                json.dumps(list(r.tags), ensure_ascii=False), r.confidence, r.status.value,
                r.attempts, r.last_error, r.provider, r.model, run_id, _now(), int(r.is_unit),
                r.name_choice, r.name_choice_reason,
            ),
        )  # fmt: skip
        row = self.conn.execute("SELECT id FROM files WHERE sha256 = ?", (r.sha256,)).fetchone()
        return int(row[0])

    def set_file_location(self, file_id: int, path: Path, status: FileStatus) -> None:
        self.conn.execute(
            "UPDATE files SET current_name = ?, current_path = ?, status = ? WHERE id = ?",
            (normalize(path.name), normalize(str(path)), status.value, file_id),
        )

    def all_files(self) -> list[FileRow]:
        return [FileRow.from_row(r) for r in self.conn.execute("SELECT * FROM files ORDER BY id")]

    def relocate_file(
        self, file_id: int, path: Path, status: FileStatus, category: str | None
    ) -> None:
        """Point a row at where the file really is now (``flib reconcile``)."""
        self.conn.execute(
            """UPDATE files SET current_name = ?, current_path = ?, status = ?, category = ?
               WHERE id = ?""",
            (normalize(path.name), normalize(str(path)), status.value, category, file_id),
        )

    def set_file_status(self, file_id: int, status: FileStatus) -> None:
        self.conn.execute("UPDATE files SET status = ? WHERE id = ?", (status.value, file_id))

    def add_correction(self, file_id: int, field: str, old: str | None, new: str) -> None:
        """A change the user made by hand; the classifier learns from these (RF-29)."""
        self.conn.execute(
            """INSERT INTO corrections (file_id, field, old_value, new_value, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (file_id, field, old, new, _now()),
        )

    def current_corrections(self) -> list[CorrectionRow]:
        """Corrections still in effect, newest first: the last one per file and field,
        and only while the file still has that value (an undone fix no longer counts).
        Sensitive files are left out here; ``never_send`` is the classifier's to apply."""
        rows = self.conn.execute(
            """SELECT c.field, c.old_value, c.new_value, f.extension, f.is_unit,
                      f.description, f.original_name, f.current_name
               FROM corrections c JOIN files f ON f.id = c.file_id
               WHERE f.status != 'sensitive'
                 AND c.id = (SELECT MAX(id) FROM corrections
                             WHERE file_id = c.file_id AND field = c.field)
                 AND ((c.field = 'category' AND f.status = 'organized'
                       AND f.category = c.new_value)
                      OR (c.field = 'name' AND f.current_name = c.new_value))
               ORDER BY c.id DESC"""
        )
        return [CorrectionRow.from_row(r) for r in rows]

    def corrected_fields(self) -> dict[int, set[str]]:
        """Per file id, the fields (``category``, ``name``) with a correction still in effect.

        The same rule as ``current_corrections``: the newest correction per file
        and field, and only while the file still has the value it set.
        """
        rows = self.conn.execute(
            """SELECT c.file_id, c.field
               FROM corrections c JOIN files f ON f.id = c.file_id
               WHERE c.id = (SELECT MAX(id) FROM corrections
                             WHERE file_id = c.file_id AND field = c.field)
                 AND ((c.field = 'category' AND f.status = 'organized'
                       AND f.category = c.new_value)
                      OR (c.field = 'name' AND f.current_name = c.new_value))"""
        )
        fields: dict[int, set[str]] = {}
        for row in rows:
            fields.setdefault(int(row[0]), set()).add(str(row[1]))
        return fields

    def rename_category(self, old: str, new: str, *, organized: bool = False) -> None:
        """A category renamed in the configuration (``flib reprocess --rename-category``).

        Rows outside the category folders (missing ones, say) and past
        corrections take the new name, so a correction to ``old`` stays in
        effect. Organized files are moved one by one, by the executor, unless
        ``organized``: the two names are the same folder on macOS (only the
        case differs), so their rows are renamed here too.
        """
        statuses = "1" if organized else "status != 'organized'"
        self.conn.execute(
            f"UPDATE files SET category = ? WHERE category = ? AND {statuses}",  # noqa: S608
            (new, old),
        )
        for column in ("old_value", "new_value"):
            self.conn.execute(
                f"UPDATE corrections SET {column} = ? "  # noqa: S608 - fixed column names
                f"WHERE field = 'category' AND {column} = ?",
                (new, old),
            )

    def organized_categories(self) -> list[str]:
        """Every category an organized file has (others keep no folder to move)."""
        rows = self.conn.execute(
            """SELECT DISTINCT category FROM files
               WHERE status = 'organized' AND category IS NOT NULL ORDER BY category"""
        )
        return [str(r[0]) for r in rows]

    def set_file_answer(
        self,
        file_id: int,
        record: FileRecord,
    ) -> None:
        """What a new classification says about a file already indexed: description,
        tags, confidence, provider, model and name choice (not where it is)."""
        r = record
        self.conn.execute(
            """UPDATE files SET description = ?, tags = ?, confidence = ?, provider = ?,
                                model = ?, name_choice = ?, name_choice_reason = ?,
                                processed_at = ?
               WHERE id = ?""",
            (
                r.description, json.dumps(list(r.tags), ensure_ascii=False), r.confidence,
                r.provider, r.model, r.name_choice, r.name_choice_reason, _now(), file_id,
            ),
        )  # fmt: skip

    def set_file_identity(self, file_id: int, sha256: str) -> bool:
        """A folder unit's new manifest digest after a copy to another file system.

        Returns ``False`` (and changes nothing) if another row already has it.
        """
        try:
            self.conn.execute("UPDATE files SET sha256 = ? WHERE id = ?", (sha256, file_id))
        except sqlite3.IntegrityError:
            return False
        return True

    def mark_file_failed(self, file_id: int, error: str, *, attempt: bool = True) -> None:
        """``attempt=False`` for a cause outside the file (a full disk): retried every run."""
        self.conn.execute(
            """UPDATE files SET status = 'failed', attempts = attempts + ?, last_error = ?
               WHERE id = ?""",
            (int(attempt), error[:500], file_id),
        )

    def file_by_id(self, file_id: int) -> FileRow:
        row = self.conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if row is None:
            raise LookupError(f"no file with id {file_id}")
        return FileRow.from_row(row)

    def file_by_sha256(self, sha256: str) -> FileRow | None:
        row = self.conn.execute("SELECT * FROM files WHERE sha256 = ?", (sha256,)).fetchone()
        return None if row is None else FileRow.from_row(row)

    def file_by_current_path(self, path: Path) -> FileRow | None:
        row = self.conn.execute(
            "SELECT * FROM files WHERE current_path = ?", (normalize(str(path)),)
        ).fetchone()
        return None if row is None else FileRow.from_row(row)

    def search_files(
        self,
        fts_query: str | None,
        *,
        categories: Sequence[str] = (),
        extensions: Sequence[str] = (),
        since: str | None = None,
        until: str | None = None,
        statuses: Sequence[FileStatus] = (),
        limit: int,
    ) -> list[FileRow]:
        """Rows matching an FTS5 query and the filters.

        With a query, best first (names weigh more than descriptions); without
        one, newest ``processed_at`` first. ``since`` is inclusive and ``until``
        exclusive, both UTC ISO text like ``processed_at``. Filter values are
        always bound as parameters.
        """
        where: list[str] = []
        params: list[object] = []
        if fts_query is not None:
            where.append("files_fts MATCH ?")
            params.append(fts_query)
        if categories:
            where.append(f"f.category IN ({', '.join('?' * len(categories))})")
            params.extend(categories)
        if extensions:
            where.append(f"lower(f.extension) IN ({', '.join('?' * len(extensions))})")
            params.extend(extensions)
        if statuses:
            where.append(f"f.status IN ({', '.join('?' * len(statuses))})")
            params.extend(s.value for s in statuses)
        if since is not None:
            where.append("f.processed_at >= ?")
            params.append(since)
        if until is not None:
            where.append("f.processed_at < ?")
            params.append(until)
        if fts_query is not None:
            source = "files_fts JOIN files f ON f.id = files_fts.rowid"
            order = "bm25(files_fts, 3.0, 3.0, 1.0, 2.0)"
        else:
            source = "files f"
            order = "f.processed_at DESC, f.id DESC"
        condition = " AND ".join(where) or "1"
        # Only fixed SQL fragments and placeholders are joined; every value is bound.
        rows = self.conn.execute(
            f"SELECT f.* FROM {source} WHERE {condition} ORDER BY {order} LIMIT ?",  # noqa: S608
            (*params, limit),
        ).fetchall()
        return [FileRow.from_row(r) for r in rows]

    def categories(self) -> list[str]:
        """Every category some indexed file has."""
        rows = self.conn.execute(
            "SELECT DISTINCT category FROM files WHERE category IS NOT NULL ORDER BY category"
        )
        return [str(r[0]) for r in rows]

    def stats(self) -> LibraryStats:
        by_status = dict.fromkeys((s.value for s in FileStatus), 0)
        for row in self.conn.execute("SELECT status, COUNT(*) FROM files GROUP BY status"):
            by_status[str(row[0])] = int(row[1])
        by_category = {
            str(row[0]): int(row[1])
            for row in self.conn.execute(
                """SELECT category, COUNT(*) FROM files
                   WHERE status = 'organized' AND category IS NOT NULL
                   GROUP BY category ORDER BY COUNT(*) DESC, category"""
            )
        }
        filed = self.conn.execute(
            "SELECT COALESCE(SUM(size_bytes), 0) FROM files WHERE status IN ('organized', 'review')"
        ).fetchone()[0]
        last = self.conn.execute(
            """SELECT * FROM runs r WHERE finished_at IS NOT NULL
                 AND (r.trigger = 'reprocess'
                      OR NOT EXISTS (SELECT 1 FROM operations o
                                     WHERE o.run_id = r.id AND o.fix_status IS NOT NULL))
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        return LibraryStats(
            by_status=by_status,
            by_category=by_category,
            filed_bytes=int(filed),
            last_run=None if last is None else RunRow.from_row(last),
        )

    # --- classification cache --------------------------------------------------------

    def cached_answer(self, sha256: str, rules_key: str, *, max_age_days: int) -> str | None:
        row = self.conn.execute(
            """SELECT answer FROM classification_cache
               WHERE sha256 = ? AND rules_key = ? AND created_at >= ?""",
            (sha256, rules_key, _days_ago(max_age_days)),
        ).fetchone()
        return None if row is None else str(row[0])

    def cache_answer(self, sha256: str, rules_key: str, answer_json: str) -> None:
        self.conn.execute(
            """INSERT INTO classification_cache (sha256, rules_key, answer, created_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(sha256, rules_key) DO UPDATE SET
                   answer = excluded.answer, created_at = excluded.created_at""",
            (sha256, rules_key, answer_json, _now()),
        )

    def prune_cache(self, *, max_age_days: int) -> int:
        cur = self.conn.execute(
            "DELETE FROM classification_cache WHERE created_at < ?", (_days_ago(max_age_days),)
        )
        return cur.rowcount

    # --- operations (the journal behind undo) ------------------------------------

    def add_operation(
        self,
        run_id: int,
        file_id: int,
        src: Path,
        dst: Path,
        *,
        identity: tuple[int, int],
        fix: tuple[FileStatus, str | None] | None = None,
    ) -> int:
        """Journal a move. ``identity`` is the source's (st_dev, st_ino); ``fix`` is the
        (status, category) a ``flib fix`` gives the file, so recovery can finish it."""
        fix_status, fix_category = (None, None) if fix is None else (fix[0].value, fix[1])
        cur = self.conn.execute(
            """INSERT INTO operations (run_id, file_id, src_path, dst_path, status, created_at,
                                      src_dev, src_ino, fix_status, fix_category)
               VALUES (?, ?, ?, ?, 'planned', ?, ?, ?, ?, ?)""",
            (
                run_id, file_id, str(src), str(dst), _now(), identity[0], identity[1],
                fix_status, fix_category,
            ),
        )  # fmt: skip
        return _rowid(cur)

    def set_operation(self, op_id: int, status: OperationStatus, dst: Path | None = None) -> None:
        """Set the status (and destination); this ends any cross-volume copy phase."""
        if dst is None:
            self.conn.execute(
                "UPDATE operations SET status = ?, copy_phase = NULL, copy_key = NULL WHERE id = ?",
                (status.value, op_id),
            )
        else:
            self.conn.execute(
                """UPDATE operations SET status = ?, dst_path = ?, copy_phase = NULL,
                                         copy_key = NULL
                   WHERE id = ?""",
                (status.value, str(dst), op_id),
            )

    def set_copy_phase(
        self,
        op_id: int,
        phase: CopyPhase,
        key: str,
        dst: Path | None = None,
        *,
        volumes: tuple[int, int] | None = None,
    ) -> None:
        """Journal how far a cross-volume move (or its undo) got, and where it is going.

        ``volumes``, given when the copy starts, are the devices of its source
        and destination folders.

        Committed with ``F_FULLFSYNC``, so the phase is on the disk itself, not
        only in the drive's cache, before the move goes on to delete a source.
        Only these few commits pay for it (a few milliseconds each).
        """
        self.conn.execute("PRAGMA fullfsync = ON")
        try:
            src_dev, dst_dev = volumes if volumes is not None else (None, None)
            self.conn.execute(
                """UPDATE operations SET copy_phase = ?, copy_key = ?,
                                         dst_path = coalesce(?, dst_path),
                                         copy_src_dev = coalesce(?, copy_src_dev),
                                         copy_dst_dev = coalesce(?, copy_dst_dev)
                   WHERE id = ?""",
                (phase.value, key, None if dst is None else str(dst), src_dev, dst_dev, op_id),
            )
        finally:
            self.conn.execute("PRAGMA fullfsync = OFF")

    def clear_copy_phase(self, op_id: int) -> None:
        """A copy that was rolled back: the entry keeps its status."""
        self.conn.execute(
            "UPDATE operations SET copy_phase = NULL, copy_key = NULL WHERE id = ?", (op_id,)
        )

    def interrupted_undos(self) -> list[OperationRow]:
        """``done`` entries whose undo to another volume a crash cut off."""
        rows = self.conn.execute(
            "SELECT * FROM operations WHERE status = 'done' AND copy_phase IS NOT NULL ORDER BY id"
        )
        return [OperationRow.from_row(r) for r in rows]

    def set_operation_tag(self, op_id: int, tag: str) -> None:
        self.conn.execute("UPDATE operations SET finder_tag = ? WHERE id = ?", (tag, op_id))

    def operations(
        self, *, run_id: int | None = None, status: OperationStatus | None = None
    ) -> list[OperationRow]:
        sql = "SELECT * FROM operations WHERE 1 = 1"
        args: list[object] = []
        if run_id is not None:
            sql += " AND run_id = ?"
            args.append(run_id)
        if status is not None:
            sql += " AND status = ?"
            args.append(status.value)
        return [OperationRow.from_row(r) for r in self.conn.execute(sql + " ORDER BY id", args)]

    def last_done_operation(self, file_id: int) -> OperationRow | None:
        row = self.conn.execute(
            """SELECT * FROM operations WHERE file_id = ? AND status = 'done'
               ORDER BY id DESC LIMIT 1""",
            (file_id,),
        ).fetchone()
        return None if row is None else OperationRow.from_row(row)

    def file_history(self, file_id: int, *, limit: int) -> list[HistoryRow]:
        """The file's journaled moves, newest first, at most ``limit``."""
        rows = self.conn.execute(
            """SELECT o.*, r.trigger AS run_trigger FROM operations o
               JOIN runs r ON r.id = o.run_id
               WHERE o.file_id = ? ORDER BY o.id DESC LIMIT ?""",
            (file_id, limit),
        )
        return [
            HistoryRow(OperationRow.from_row(r), RunTrigger(r["run_trigger"]), str(r["created_at"]))
            for r in rows
        ]

    def last_finder_tag(self, file_id: int) -> str | None:
        """The Finder tag of the newest move still in effect that added one."""
        row = self.conn.execute(
            """SELECT finder_tag FROM operations
               WHERE file_id = ? AND status = 'done' AND finder_tag IS NOT NULL
               ORDER BY id DESC LIMIT 1""",
            (file_id,),
        ).fetchone()
        return None if row is None else str(row[0])

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


def _days_ago(days: int) -> str:
    return (datetime.now(UTC) - timedelta(days=days)).isoformat(timespec="seconds")


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

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

import dataclasses
import json
import re
import sqlite3
import unicodedata
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal
from importlib import resources
from pathlib import Path
from types import TracebackType

from file_librarian.fields import exponent
from file_librarian.models import (
    CopyPhase,
    FileRecord,
    FileState,
    FileStatus,
    OperationStatus,
    ReviewReason,
    RunMode,
    RunTrigger,
)
from file_librarian.naming import issuer_key

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
    review_reason: ReviewReason | None = None
    """Why it is in review; ``None`` outside review and for rows filed before #68."""
    review_category: str | None = None
    """The category the model suggested for a file in review, if any."""
    duplicate_of: int | None = None
    """For a duplicate (RF-21), the id of the row of the file it is a copy of."""
    document_date: str | None = None
    """The date the document refers to (YYYY-MM-DD); ``None`` if unknown (#86)."""
    issuer: str | None = None
    """The organisation the document comes from; ``None`` if unknown (#86)."""
    layout_date: str | None = None
    """The date its layout's ``{year}``/``{month}`` come from, kept from when it was
    filed (YYYY-MM-DD); ``None`` until then (#86)."""
    amount_minor: int | None = None
    """The document's amount in ``currency``'s minor units; ``None`` if unknown (#88)."""
    currency: str | None = None
    due_date: str | None = None
    period: str | None = None
    """What the document refers to: ``YYYY-MM`` or ``YYYY``."""
    kind: str | None = None
    source_input: Path | None = None
    """The input folder it was taken from; ``None`` for rows from before #89."""
    filed_by_rule: bool = False
    """Filed by its input's fixed category, with no AI answer (#89)."""
    sensitive_by: str | None = None
    """For a sensitive file: ``name`` or ``content``; ``None`` on older rows."""
    sensitive_reason: str | None = None
    """For a sensitive file: why, in words."""
    settled_at: str | None = None
    """When the user marked it paid or done (UTC, ISO 8601); ``None`` while open (#91)."""
    due_reminded_for: str | None = None
    """The due date a due reminder last counted it for; ``None`` until then (#91)."""

    @property
    def review(self) -> tuple[ReviewReason | None, str | None]:
        return self.review_reason, self.review_category

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
            review_reason=None
            if row["review_reason"] is None
            else ReviewReason(row["review_reason"]),
            review_category=row["review_category"],
            duplicate_of=None if row["duplicate_of"] is None else int(row["duplicate_of"]),
            document_date=row["document_date"],
            issuer=row["issuer"],
            layout_date=row["layout_date"],
            amount_minor=None if row["amount_minor"] is None else int(row["amount_minor"]),
            currency=row["currency"],
            due_date=row["due_date"],
            period=row["period"],
            kind=row["kind"],
            source_input=None if row["source_input"] is None else Path(row["source_input"]),
            filed_by_rule=bool(row["filed_by_rule"]),
            sensitive_by=row["sensitive_by"],
            sensitive_reason=row["sensitive_reason"],
            settled_at=row["settled_at"],
            due_reminded_for=row["due_reminded_for"],
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
    previous: tuple[FileStatus, str | None] | None = None
    """For a ``flib reprocess`` move, the (status, category) the file had before."""
    undone_at: str | None = None
    pending: FileRecord | None = None
    """For a move that takes over a ``missing`` row (RF-21), the record the row gets
    once the item is in place; recovery writes it if a crash cut the move off."""
    """When the move was undone (UTC, ISO 8601); ``None`` if it never was."""

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
            previous=None
            if row["prev_status"] is None
            else (FileStatus(row["prev_status"]), row["prev_category"]),
            undone_at=row["undone_at"],
            pending=record_from_json(row["pending_record"])
            if "pending_record" in row.keys()  # noqa: SIM118 - a Row is a sequence
            else None,
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
    cost_usd: float | None = None
    """Estimated provider cost; ``None`` when the prices are unknown."""
    files_duplicates: int | None = None
    """Duplicates it set aside (RF-21); ``None`` for runs recorded before flib kept it."""

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
            cost_usd=None if row["cost_usd"] is None else float(row["cost_usd"]),
            # Read-only callers (`flib status`) may see a schema from before 0013.
            # (``in row`` would look among the values: a Row is a sequence.)
            files_duplicates=row["files_duplicates"]
            if "files_duplicates" in row.keys()  # noqa: SIM118
            else None,
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
    month_cost_usd: float = 0.0
    """Provider cost of the runs started this month, those with a known cost."""
    month_unpriced_runs: int = 0
    """Runs started this month without a recorded cost (not in ``month_cost_usd``): the
    provider's prices are unknown, or the run predates the cost column (migration 0010)."""


@dataclass(frozen=True, slots=True)
class WaitingRow:
    """A file that waits for the user: in review, or failed (``flib status``)."""

    status: FileStatus
    review_reason: ReviewReason | None
    review_category: str | None
    attempts: int
    last_error: str | None
    processed_at: str


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
    run_id: int | None = None
    """The run of the fix that recorded it; ``None`` for one ``flib reconcile`` found."""

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
            run_id=None if row["run_id"] is None else int(row["run_id"]),
        )


@dataclass(frozen=True, slots=True)
class IndexChange:
    """A change a run made to the index without moving a file (``index_changes``)."""

    id: int
    run_id: int
    target: str
    """A key of ``_INDEX_TARGETS``: ``files.category``, ``files.review_reason``,
    ``corrections.old_value``, ``operations.finder_tag``..."""
    row_id: int
    old_value: str | None
    new_value: str | None
    status: str


@dataclass(frozen=True, slots=True)
class FieldFilters:
    """Search filters on a document's structured fields (#88), already validated.

    ``period`` is a year (``YYYY``) or a month (``YYYY-MM``), matched against
    what the document refers to (``files.period``), else the month of its
    document date. Amounts are in major units (``1234.56``) and need an
    amount; ``due_before`` and ``due_after`` are exclusive ISO dates.
    """

    issuer_key: str | None = None
    """``naming.issuer_key`` of the issuer: case, accents and legal forms ignored."""
    kinds: tuple[str, ...] = ()
    min_amount: Decimal | None = None
    max_amount: Decimal | None = None
    currency: str | None = None
    due_before: str | None = None
    due_after: str | None = None
    period: str | None = None

    def __bool__(self) -> bool:
        return any(getattr(self, f.name) not in (None, ()) for f in dataclasses.fields(self))


@dataclass(frozen=True, slots=True)
class AmountTotal:
    """Matching files with an amount, summed per group and currency (never mixed)."""

    group: str | None
    """The group's value (an issuer, ``YYYY-MM``, ``YYYY``, a kind or a category);
    ``None`` for files without one, and when nothing groups the totals."""
    currency: str
    total_minor: int
    files: int


GROUPS = ("issuer", "month", "year", "kind", "category")
"""What ``Store.sum_amounts`` can group totals by."""

# What a document refers to: its period, else the month of its document date.
EFFECTIVE_PERIOD = "coalesce(f.period, substr(f.document_date, 1, 7))"
_GROUP_SQL = {
    "issuer": ("flib_issuer_key(f.issuer)", "min(f.issuer)"),
    "month": (
        f"CASE WHEN length({EFFECTIVE_PERIOD}) = 7 THEN {EFFECTIVE_PERIOD} END",
        f"CASE WHEN length({EFFECTIVE_PERIOD}) = 7 THEN {EFFECTIVE_PERIOD} END",
    ),
    "year": (f"substr({EFFECTIVE_PERIOD}, 1, 4)", f"substr({EFFECTIVE_PERIOD}, 1, 4)"),
    "kind": ("f.kind", "f.kind"),
    "category": ("f.category", "f.category"),
}


def _issuer_key_sql(value: object) -> str | None:
    return issuer_key(value) or None if isinstance(value, str) else None


def _amount_sql(minor: object, currency: object) -> float | None:
    """An amount in major units, for comparing with a typed threshold. Dividing an
    integer by a power of ten gives the double nearest the exact value, as parsing
    the threshold does, so an amount equal to the threshold compares equal."""
    if not isinstance(minor, int) or not isinstance(currency, str):
        return None
    return float(minor / 10 ** exponent(currency))


# The columns ``index_changes`` may point at. Only these fixed names ever reach SQL.
_ANSWER_COLUMNS = (
    "description",
    "tags",
    "confidence",
    "provider",
    "model",
    "name_choice",
    "name_choice_reason",
    "document_date",
    "issuer",
    "amount_minor",
    "currency",
    "due_date",
    "period",
    "kind",
)
# The structured fields of a document (#88), as ``FileRecord`` and ``FileRow`` name them.
FIELD_COLUMNS = (
    "document_date",
    "issuer",
    "amount_minor",
    "currency",
    "due_date",
    "period",
    "kind",
)
_INDEX_TARGETS = {
    "files.category": ("files", "category"),
    **{f"files.{c}": ("files", c) for c in (*_ANSWER_COLUMNS, "processed_at")},
    "files.review_reason": ("files", "review_reason"),
    "files.review_category": ("files", "review_category"),
    "files.layout_date": ("files", "layout_date"),
    "files.settled_at": ("files", "settled_at"),
    "corrections.old_value": ("corrections", "old_value"),
    "corrections.new_value": ("corrections", "new_value"),
    "operations.finder_tag": ("operations", "finder_tag"),
}


class Store:
    """An open database. Use as a context manager."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        # Used by the field filters (#88); deterministic, so SQLite may reuse results.
        conn.create_function("flib_issuer_key", 1, _issuer_key_sql, deterministic=True)
        conn.create_function("flib_amount", 2, _amount_sql, deterministic=True)

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

    @classmethod
    def open_read_only(cls, path: Path) -> Store:
        """The index for reading only: never created, migrated or written (``flib status``,
        ``flib due``). Fails if the file does not exist."""
        conn = sqlite3.connect(
            f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=BUSY_TIMEOUT_MS / 1000
        )
        conn.row_factory = sqlite3.Row
        return cls(conn)

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
        """What the index knows about a content, or ``None`` if it was never seen.

        Duplicates (RF-21) are not the content's state, except that the copies
        ``flib undo`` put back are listed (``FileState.kept``).
        """
        row = self.conn.execute(
            "SELECT status, attempts, current_path, sensitive_by FROM files "
            "WHERE sha256 = ? AND duplicate_of IS NULL",
            (sha256,),
        ).fetchone()
        if row is None:
            return None
        kept = self.conn.execute(
            """SELECT current_path FROM files
               WHERE sha256 = ? AND duplicate_of IS NOT NULL AND status = 'skipped'""",
            (sha256,),
        )
        return FileState(
            FileStatus(row["status"]),
            int(row["attempts"]),
            Path(row["current_path"]),
            frozenset(str(r[0]) for r in kept),
            sensitive_by=row["sensitive_by"],
        )

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

    def finish_run(
        self,
        run_id: int,
        *,
        seen: int,
        moved: int,
        review: int,
        errors: int,
        cost_usd: float | None,
        duplicates: int = 0,
    ) -> None:
        """``cost_usd``: the provider cost of the run; ``None`` when its prices are unknown.
        ``duplicates``: how many duplicates it set aside (RF-21)."""
        self.conn.execute(
            """UPDATE runs SET finished_at = ?, files_seen = ?, files_moved = ?,
                              files_review = ?, errors = ?, cost_usd = ?, files_duplicates = ?
               WHERE id = ?""",
            (_now(), seen, moved, review, errors, cost_usd, duplicates, run_id),
        )

    def run_trigger(self, run_id: int) -> RunTrigger:
        row = self.conn.execute("SELECT trigger FROM runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise LookupError(f"no run with id {run_id}")
        return RunTrigger(row[0])

    def last_run_with_operations(self) -> int | None:
        """The newest run that actually changed something: moved a file or changed the
        index (done, or since undone)."""
        row = self.conn.execute(
            """SELECT MAX(run_id) FROM (
                   SELECT run_id FROM operations WHERE status IN ('done', 'undone')
                   UNION ALL SELECT run_id FROM index_changes)"""
        ).fetchone()
        return None if row is None or row[0] is None else int(row[0])

    def newest_reprocess_after(self, run_id: int | None) -> int | None:
        """The newest ``flib reprocess`` run started after ``run_id`` (any, if ``None``)."""
        row = self.conn.execute(
            "SELECT MAX(id) FROM runs WHERE trigger = 'reprocess' AND id > ?",
            (run_id if run_id is not None else 0,),
        ).fetchone()
        return None if row is None or row[0] is None else int(row[0])

    # --- files ----------------------------------------------------------------

    def upsert_file(self, record: FileRecord, *, run_id: int) -> int:
        """Insert or update the row for ``record.sha256``; returns its id.

        A duplicate (``record.duplicate_of``) has a row of its own: the one of
        an earlier attempt to move the same copy from the same place, or a new one.
        """
        r = record
        if r.duplicate_of is not None:
            return self._upsert_duplicate(r, run_id=run_id)
        self.conn.execute(
            """INSERT INTO files (sha256, original_name, original_path, current_name,
                   current_path, extension, size_bytes, source_url, category, description,
                   tags, confidence, status, attempts, last_error, provider, model, run_id,
                   processed_at, is_unit, name_choice, name_choice_reason, review_reason,
                   review_category, document_date, issuer, layout_date, amount_minor, currency,
                   due_date, period, kind, source_input, filed_by_rule, sensitive_by,
                   sensitive_reason)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                       ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(sha256) WHERE duplicate_of IS NULL DO UPDATE SET
                   current_name = excluded.current_name, current_path = excluded.current_path,
                   category = excluded.category, description = excluded.description,
                   tags = excluded.tags, confidence = excluded.confidence,
                   status = excluded.status, attempts = files.attempts + excluded.attempts,
                   last_error = excluded.last_error, provider = excluded.provider,
                   model = excluded.model, run_id = excluded.run_id,
                   processed_at = excluded.processed_at, name_choice = excluded.name_choice,
                   name_choice_reason = excluded.name_choice_reason,
                   review_reason = excluded.review_reason,
                   review_category = excluded.review_category,
                   document_date = excluded.document_date, issuer = excluded.issuer,
                   layout_date = excluded.layout_date, amount_minor = excluded.amount_minor,
                   currency = excluded.currency, due_date = excluded.due_date,
                   period = excluded.period, kind = excluded.kind,
                   source_input = COALESCE(files.source_input, excluded.source_input),
                   filed_by_rule = excluded.filed_by_rule,
                   sensitive_by = excluded.sensitive_by,
                   sensitive_reason = excluded.sensitive_reason""",
            (
                r.sha256, normalize(r.original_path.name), normalize(str(r.original_path)),
                normalize(r.current_path.name), normalize(str(r.current_path)), r.extension,
                r.size_bytes, r.source_url, r.category, r.description,
                json.dumps(list(r.tags), ensure_ascii=False), r.confidence, r.status.value,
                r.attempts, r.last_error, r.provider, r.model, run_id, _now(), int(r.is_unit),
                r.name_choice, r.name_choice_reason,
                None if r.review_reason is None else r.review_reason.value, r.review_category,
                r.document_date, r.issuer, r.layout_date, r.amount_minor, r.currency,
                r.due_date, r.period, r.kind, _path_text(r.source_input), int(r.by_rule),
                r.sensitive_by, r.sensitive_reason,
            ),
        )  # fmt: skip
        row = self.conn.execute(
            "SELECT id FROM files WHERE sha256 = ? AND duplicate_of IS NULL", (r.sha256,)
        ).fetchone()
        return int(row[0])

    def _upsert_duplicate(self, r: FileRecord, *, run_id: int) -> int:
        found = self.conn.execute(
            """SELECT id FROM files
               WHERE sha256 = ? AND duplicate_of IS NOT NULL AND current_path = ?""",
            (r.sha256, normalize(str(r.current_path))),
        ).fetchone()
        if found is not None:
            self.conn.execute(
                """UPDATE files SET status = ?, duplicate_of = ?, run_id = ?, processed_at = ?,
                                  source_input = COALESCE(source_input, ?)
                   WHERE id = ?""",
                (
                    r.status.value,
                    r.duplicate_of,
                    run_id,
                    _now(),
                    _path_text(r.source_input),
                    int(found[0]),
                ),
            )
            return int(found[0])
        cur = self.conn.execute(
            """INSERT INTO files (sha256, original_name, original_path, current_name,
                   current_path, extension, size_bytes, source_url, tags, status, run_id,
                   processed_at, is_unit, duplicate_of, source_input)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?, ?, ?, ?)""",
            (
                r.sha256, normalize(r.original_path.name), normalize(str(r.original_path)),
                normalize(r.current_path.name), normalize(str(r.current_path)), r.extension,
                r.size_bytes, r.source_url, r.status.value, run_id, _now(), int(r.is_unit),
                r.duplicate_of, _path_text(r.source_input),
            ),
        )  # fmt: skip
        return _rowid(cur)

    def set_file_location(self, file_id: int, path: Path, status: FileStatus) -> None:
        """Where the file is now. Out of review, it loses its review reason."""
        self.conn.execute(
            f"""UPDATE files SET current_name = ?, current_path = ?, status = ?, {_KEEP_REVIEW}
               WHERE id = ?""",  # noqa: S608 - a fixed fragment
            (normalize(path.name), normalize(str(path)), status.value, status.value, status.value,
             file_id),
        )  # fmt: skip

    def count_by_input(self) -> dict[str, dict[str, int]]:
        """Rows per input folder and status (rows from before #89: by their original
        folder). Paths only, never a file name."""
        counts: dict[str, dict[str, int]] = {}
        for row in self.conn.execute(
            """SELECT source_input, original_path, status FROM files
               WHERE duplicate_of IS NULL"""
        ):
            folder = row[0] or str(Path(row[1]).parent)
            by_status = counts.setdefault(normalize(folder), {})
            by_status[str(row[2])] = by_status.get(str(row[2]), 0) + 1
        return counts

    def all_files(self) -> list[FileRow]:
        return [FileRow.from_row(r) for r in self.conn.execute("SELECT * FROM files ORDER BY id")]

    def relocate_file(
        self, file_id: int, path: Path, status: FileStatus, category: str | None
    ) -> None:
        """Point a row at where the file really is now (``flib reconcile``).

        Out of review, it loses its review reason.
        """
        self.conn.execute(
            f"""UPDATE files SET current_name = ?, current_path = ?, status = ?, category = ?,
                                {_KEEP_REVIEW}
               WHERE id = ?""",  # noqa: S608 - a fixed fragment
            (normalize(path.name), normalize(str(path)), status.value, category, status.value,
             status.value, file_id),
        )  # fmt: skip

    def set_review(
        self,
        run_id: int,
        file_id: int,
        new: tuple[ReviewReason | None, str | None],
        *,
        old: tuple[ReviewReason | None, str | None],
    ) -> None:
        """Give a file already indexed a new review reason, journaled with ``run_id`` so
        undo gives back ``old`` (what the row had before the run changed it)."""
        for target, before, after in (
            ("files.review_reason", _code(old[0]), _code(new[0])),
            ("files.review_category", old[1], new[1]),
        ):
            if before != after:
                self.change_index(run_id, target, file_id, before, after)

    def mark_review(self, file_id: int, reason: ReviewReason, category: str | None = None) -> None:
        """Record why a file is in review, outside any run (``flib reconcile``)."""
        self.conn.execute(
            "UPDATE files SET review_reason = ?, review_category = ? WHERE id = ?",
            (reason.value, category, file_id),
        )

    def mark_missing(self, file_id: int) -> None:
        """Found nowhere (``flib reconcile``): ``missing``, with a fresh attempt count, so a
        copy that takes the row over gets ``limits.max_attempts`` tries of its own."""
        self.conn.execute(
            "UPDATE files SET status = 'missing', attempts = 0, last_error = NULL WHERE id = ?",
            (file_id,),
        )

    def count_attempt(self, file_id: int, error: str) -> None:
        """A failed attempt at a ``missing`` row's content (a takeover that did not
        happen): counted and explained, the status kept."""
        self.conn.execute(
            """UPDATE files SET attempts = attempts + 1, last_error = ?, processed_at = ?
               WHERE id = ?""",
            (error[:500], _now(), file_id),
        )

    def set_file_status(self, file_id: int, status: FileStatus) -> None:
        self.conn.execute("UPDATE files SET status = ? WHERE id = ?", (status.value, file_id))

    def sensitive_files(self) -> list[tuple[Path, str | None]]:
        """Files left in place as sensitive: where each is and why (``flib status
        --sensitive``, on this Mac only)."""
        rows = self.conn.execute(
            """SELECT current_path, sensitive_reason FROM files
               WHERE status = 'sensitive' AND duplicate_of IS NULL ORDER BY current_path"""
        )
        return [(Path(r[0]), r[1]) for r in rows]

    def add_correction(
        self,
        file_id: int,
        field: str,
        old: str | None,
        new: str,
        *,
        operation_id: int | None = None,
    ) -> None:
        """A change the user made by hand; the classifier learns from these (RF-29).

        ``operation_id``: the ``flib fix`` move that made it; undoing that move
        deletes it (``drop_corrections``). The file is no longer one an input rule
        filed (#89): the user chose for it, so ``flib reprocess`` treats it like any
        corrected file from now on.
        """
        self.conn.execute(
            """INSERT INTO corrections (file_id, field, old_value, new_value, created_at,
                                        operation_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (file_id, field, old, new, _now(), operation_id),
        )
        self.conn.execute("UPDATE files SET filed_by_rule = 0 WHERE id = ?", (file_id,))

    def drop_corrections(self, operation_id: int) -> int:
        """Forget what an undone move taught (#115); how many corrections went."""
        cur = self.conn.execute("DELETE FROM corrections WHERE operation_id = ?", (operation_id,))
        return cur.rowcount

    def current_corrections(self) -> list[CorrectionRow]:
        """Corrections still in effect, newest first: the last one per file and field,
        and only while the file still has that value (an undone fix no longer counts).
        Sensitive files are left out here; ``never_send`` is the classifier's to apply."""
        rows = self.conn.execute(
            """SELECT c.field, c.old_value, c.new_value, f.extension, f.is_unit,
                      f.description, f.original_name, f.current_name, o.run_id
               FROM corrections c JOIN files f ON f.id = c.file_id
                    LEFT JOIN operations o ON o.id = c.operation_id
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

    def rename_category(self, run_id: int, old: str, new: str, *, organized: bool) -> int:
        """A category renamed in the configuration (``flib reprocess --rename-category``).

        Rows outside the category folders (missing ones, say) and past
        corrections take the new name, so a correction to ``old`` stays in
        effect. Organized files are moved one by one, by the executor, unless
        ``organized``: the two names are the same folder on macOS (only the
        case differs), so their rows are renamed here too. Every change is
        journaled with ``run_id`` for undo. Returns how many.
        """
        statuses = "1" if organized else "status != 'organized'"
        ids = [
            int(r[0])
            for r in self.conn.execute(
                f"SELECT id FROM files WHERE category = ? AND {statuses}",  # noqa: S608
                (old,),
            )
        ]
        changes = [("files.category", i) for i in ids]
        for column in ("old_value", "new_value"):
            changes.extend(
                (f"corrections.{column}", int(r[0]))
                for r in self.conn.execute(
                    f"SELECT id FROM corrections "  # noqa: S608 - fixed column names
                    f"WHERE field = 'category' AND {column} = ?",
                    (old,),
                )
            )
        for target, row_id in changes:
            self.change_index(run_id, target, row_id, old, new)
        return len(changes)

    def change_index(
        self, run_id: int, target: str, row_id: int, old: str | None, new: str | None
    ) -> None:
        """Set one indexed value and journal it with ``run_id`` (see ``index_changes``)."""
        table, column = _INDEX_TARGETS[target]
        self.conn.execute(
            f"UPDATE {table} SET {column} = ? WHERE id = ?",  # noqa: S608 - fixed names
            (new, row_id),
        )
        self.conn.execute(
            """INSERT INTO index_changes (run_id, target, row_id, old_value, new_value, status,
                                          created_at)
               VALUES (?, ?, ?, ?, ?, 'done', ?)""",
            (run_id, target, row_id, old, new, _now()),
        )

    def index_changes(self, run_id: int) -> list[IndexChange]:
        """The run's index changes still in effect, oldest first."""
        rows = self.conn.execute(
            "SELECT * FROM index_changes WHERE run_id = ? AND status = 'done' ORDER BY id",
            (run_id,),
        )
        return [
            IndexChange(
                id=int(r["id"]),
                run_id=int(r["run_id"]),
                target=str(r["target"]),
                row_id=int(r["row_id"]),
                old_value=r["old_value"],
                new_value=r["new_value"],
                status=str(r["status"]),
            )
            for r in rows
        ]

    def revert_index_change(self, change: IndexChange) -> bool:
        """Put the old value back, if the value is still the one the run set.

        Marks the change ``undone`` either way; returns whether it was put back.
        """
        table, column = _INDEX_TARGETS[change.target]
        cur = self.conn.execute(
            f"UPDATE {table} SET {column} = ? "  # noqa: S608 - fixed names
            f"WHERE id = ? AND {column} IS ?",
            (change.old_value, change.row_id, change.new_value),
        )
        self.conn.execute("UPDATE index_changes SET status = 'undone' WHERE id = ?", (change.id,))
        return cur.rowcount > 0

    def organized_categories(self) -> list[str]:
        """Every category an organized file has (others keep no folder to move)."""
        rows = self.conn.execute(
            """SELECT DISTINCT category FROM files
               WHERE status = 'organized' AND category IS NOT NULL ORDER BY category"""
        )
        return [str(r[0]) for r in rows]

    def set_file_answer(self, file_id: int, record: FileRecord, *, run_id: int) -> int:
        """What a new classification says about a file already indexed: description,
        tags, confidence, provider, model and name choice (not where it is).

        Only values that differ are written, each journaled with ``run_id`` so
        undo puts it back; ``processed_at`` moves only when something changed.
        Returns how many values changed.
        """
        r = record
        new: dict[str, object] = {
            "description": r.description,
            "tags": json.dumps(list(r.tags), ensure_ascii=False),
            "confidence": r.confidence,
            "provider": r.provider,
            "model": r.model,
            "name_choice": r.name_choice,
            "name_choice_reason": r.name_choice_reason,
            "document_date": r.document_date,
            "issuer": r.issuer,
            "amount_minor": r.amount_minor,
            "currency": r.currency,
            "due_date": r.due_date,
            "period": r.period,
            "kind": r.kind,
        }
        old = self.conn.execute(
            f"SELECT {', '.join(_ANSWER_COLUMNS)}, processed_at FROM files "  # noqa: S608
            "WHERE id = ?",
            (file_id,),
        ).fetchone()
        changed = [c for c in _ANSWER_COLUMNS if old[c] != new[c]]
        for column in changed:
            self.change_index(
                run_id, f"files.{column}", file_id, _as_text(old[column]), _as_text(new[column])
            )
        if changed:
            self.change_index(run_id, "files.processed_at", file_id, old["processed_at"], _now())
        return len(changed)

    def index_change_file(self, change: IndexChange) -> int | None:
        """The id of the file an index change is about (``None`` if it is gone)."""
        table, _ = _INDEX_TARGETS[change.target]
        if table == "files":
            return change.row_id
        row = self.conn.execute(
            f"SELECT file_id FROM {table} WHERE id = ?",  # noqa: S608 - fixed names
            (change.row_id,),
        ).fetchone()
        return None if row is None else int(row[0])

    def adopt_index_changes(self, run_id: int, old: str, new: str) -> int:
        """Bring the still-done changes of an earlier, interrupted rename of ``old`` to
        ``new`` into ``run_id``, so undoing this run reverts all of them."""
        cur = self.conn.execute(
            """UPDATE index_changes SET run_id = ?
               WHERE status = 'done' AND run_id != ? AND old_value IS ? AND new_value IS ?
                 AND target IN ('files.category', 'corrections.old_value',
                                'corrections.new_value')""",
            (run_id, run_id, old, new),
        )
        return cur.rowcount

    def count_index_changes(self, run_id: int) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM index_changes WHERE run_id = ?", (run_id,)
        ).fetchone()
        return int(row[0])

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
            """UPDATE files SET status = 'failed', attempts = attempts + ?, last_error = ?,
                                review_reason = NULL, review_category = NULL
               WHERE id = ?""",
            (int(attempt), error[:500], file_id),
        )

    def file_by_id(self, file_id: int) -> FileRow:
        row = self.conn.execute("SELECT * FROM files WHERE id = ?", (file_id,)).fetchone()
        if row is None:
            raise LookupError(f"no file with id {file_id}")
        return FileRow.from_row(row)

    def file_by_sha256(self, sha256: str) -> FileRow | None:
        """The row of a content; never a duplicate's (see ``duplicate_of``)."""
        row = self.conn.execute(
            "SELECT * FROM files WHERE sha256 = ? AND duplicate_of IS NULL", (sha256,)
        ).fetchone()
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
        include_duplicates: bool = False,
        fields: FieldFilters | None = None,
        limit: int,
    ) -> list[FileRow]:
        """Rows matching an FTS5 query and the filters; duplicates only if asked.

        With a query, best first (names weigh more than descriptions); without
        one, newest ``processed_at`` first. ``since`` is inclusive and ``until``
        exclusive, both UTC ISO text like ``processed_at``. Filter values are
        always bound as parameters.
        """
        where, params = _filter_clause(categories, extensions, since, until, statuses, fields)
        if not include_duplicates:
            where.append("f.duplicate_of IS NULL")
        if fts_query is not None:
            where.insert(0, "files_fts MATCH ?")
            params.insert(0, fts_query)
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

    def sum_amounts(
        self,
        fts_query: str | None,
        *,
        by: str | None = None,
        categories: Sequence[str] = (),
        extensions: Sequence[str] = (),
        since: str | None = None,
        until: str | None = None,
        fields: FieldFilters | None = None,
    ) -> tuple[list[AmountTotal], int]:
        """Totals of the amounts of every file matching the query and filters, per
        currency and, with ``by`` (one of ``GROUPS``), per group; largest first.

        Duplicates and sensitive files are never counted. Also returns how many
        matching files have no amount (left out of the totals).
        """
        where, params = _filter_clause(categories, extensions, since, until, (), fields)
        where += ["f.duplicate_of IS NULL", "f.status != 'sensitive'"]
        source = "files f"
        if fts_query is not None:
            where.insert(0, "files_fts MATCH ?")
            params.insert(0, fts_query)
            source = "files_fts JOIN files f ON f.id = files_fts.rowid"
        condition = " AND ".join(where)
        key, label = _GROUP_SQL[by] if by is not None else ("NULL", "NULL")
        # Only fixed SQL fragments and placeholders are joined; every value is bound.
        rows = self.conn.execute(
            f"""SELECT {label} AS label, f.currency, SUM(f.amount_minor), COUNT(*)
                FROM {source}
                WHERE {condition} AND f.amount_minor IS NOT NULL AND f.currency IS NOT NULL
                GROUP BY {key}, f.currency
                ORDER BY SUM(f.amount_minor) DESC, label""",  # noqa: S608
            params,
        ).fetchall()
        missing = self.conn.execute(
            f"SELECT COUNT(*) FROM {source} "  # noqa: S608
            f"WHERE {condition} AND (f.amount_minor IS NULL OR f.currency IS NULL)",
            params,
        ).fetchone()[0]
        totals = [
            AmountTotal(None if r[0] is None else str(r[0]), str(r[1]), int(r[2]), int(r[3]))
            for r in rows
        ]
        return totals, int(missing)

    def due_files(self) -> list[FileRow]:
        """Every filed document (organized or in review) with a due date, soonest first;
        never a duplicate (#91)."""
        rows = self.conn.execute(
            """SELECT * FROM files
               WHERE due_date IS NOT NULL AND duplicate_of IS NULL
                 AND status IN ('organized', 'review')
               ORDER BY due_date, current_path"""
        )
        return [FileRow.from_row(r) for r in rows]

    def set_reminded(self, reminded: Sequence[tuple[int, str]]) -> None:
        """Record that a due reminder counted each ``(file id, due date)`` (#91)."""
        self.conn.executemany(
            "UPDATE files SET due_reminded_for = ? WHERE id = ?",
            [(due, file_id) for file_id, due in reminded],
        )

    def files_by_ids(self, ids: Sequence[int]) -> dict[int, FileRow]:
        found: dict[int, FileRow] = {}
        for start in range(0, len(ids), 500):
            chunk = ids[start : start + 500]
            rows = self.conn.execute(
                f"SELECT * FROM files WHERE id IN ({', '.join('?' * len(chunk))})",  # noqa: S608
                tuple(chunk),
            )
            found.update((int(r["id"]), FileRow.from_row(r)) for r in rows)
        return found

    def review_files(self, *, limit: int) -> list[FileRow]:
        """Files in review, oldest first (the interface's Review page, #115)."""
        rows = self.conn.execute(
            "SELECT * FROM files WHERE status = 'review' ORDER BY processed_at, id LIMIT ?",
            (limit,),
        )
        return [FileRow.from_row(r) for r in rows]

    def last_done_operations(self, file_ids: Sequence[int]) -> dict[int, int]:
        """Each file's last ``done`` operation (the one ``flib undo --file`` reverts), by id."""
        found: dict[int, int] = {}
        for start in range(0, len(file_ids), 500):
            chunk = file_ids[start : start + 500]
            rows = self.conn.execute(
                "SELECT file_id, MAX(id) FROM operations WHERE status = 'done' "  # noqa: S608
                f"AND file_id IN ({', '.join('?' * len(chunk))}) GROUP BY file_id",
                tuple(chunk),
            )
            found.update((int(r[0]), int(r[1])) for r in rows)
        return found

    # --- embeddings (semantic search) -------------------------------------------------

    def embedding_keys(self) -> dict[int, tuple[str, str]]:
        """``file_id -> (model, text_hash)`` of every stored vector."""
        rows = self.conn.execute("SELECT file_id, model, text_hash FROM embeddings")
        return {int(r[0]): (str(r[1]), str(r[2])) for r in rows}

    def save_embeddings(self, items: Sequence[tuple[int, str, int, str, bytes]]) -> None:
        """Store ``(file_id, model, dimension, text_hash, vector)`` rows, replacing old ones."""
        now = _now()
        with self.transaction():
            self.conn.executemany(
                """INSERT INTO embeddings (file_id, model, dimension, text_hash, vector, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(file_id) DO UPDATE SET model = excluded.model,
                       dimension = excluded.dimension, text_hash = excluded.text_hash,
                       vector = excluded.vector, created_at = excluded.created_at""",
                [(*item, now) for item in items],
            )

    def delete_embeddings(self, file_ids: Sequence[int]) -> int:
        removed = 0
        for start in range(0, len(file_ids), 500):
            chunk = file_ids[start : start + 500]
            cur = self.conn.execute(
                f"DELETE FROM embeddings WHERE file_id IN ({', '.join('?' * len(chunk))})",  # noqa: S608
                tuple(chunk),
            )
            removed += cur.rowcount
        return removed

    def embedding_vectors(
        self,
        model: str,
        dimension: int,
        *,
        categories: Sequence[str] = (),
        extensions: Sequence[str] = (),
        since: str | None = None,
        until: str | None = None,
        fields: FieldFilters | None = None,
    ) -> Iterator[tuple[int, bytes]]:
        """``(file_id, vector)`` of ``model``'s vectors for files matching the filters.

        A sensitive file is never returned, even if a vector was stored for it.
        """
        where, params = _filter_clause(categories, extensions, since, until, (), fields)
        where[:0] = ["e.model = ?", "e.dimension = ?", "f.status != 'sensitive'"]
        params[:0] = [model, dimension]
        rows = self.conn.execute(
            "SELECT e.file_id, e.vector FROM embeddings e JOIN files f ON f.id = e.file_id "  # noqa: S608
            f"WHERE {' AND '.join(where)}",
            params,
        )
        for row in rows:
            yield int(row[0]), bytes(row[1])

    def embedding_count(self, model: str) -> int:
        """How many files that are not sensitive have a vector from ``model``."""
        row = self.conn.execute(
            """SELECT COUNT(*) FROM embeddings e JOIN files f ON f.id = e.file_id
               WHERE e.model = ? AND f.status != 'sensitive'""",
            (model,),
        ).fetchone()
        return int(row[0])

    def files_in_category(self, name: str) -> int:
        """How many indexed files have category ``name``, whatever their status (#117:
        a category that holds files is renamed or removed only by ``flib reprocess``)."""
        row = self.conn.execute("SELECT COUNT(*) FROM files WHERE category = ?", (name,))
        return int(row.fetchone()[0])

    def categories(self) -> list[str]:
        """Every category some indexed file has."""
        rows = self.conn.execute(
            "SELECT DISTINCT category FROM files WHERE category IS NOT NULL ORDER BY category"
        )
        return [str(r[0]) for r in rows]

    def stats(self, *, month_start: str | None = None) -> LibraryStats:
        """``month_start``: when this month began (UTC ISO text); by default the first
        day of the current month, at midnight local time."""
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
                 AND NOT EXISTS (SELECT 1 FROM index_changes c
                                 WHERE c.run_id = r.id AND c.target = 'files.settled_at')
               ORDER BY id DESC LIMIT 1"""
        ).fetchone()
        month = self.conn.execute(
            """SELECT COALESCE(SUM(cost_usd), 0), COUNT(*) - COUNT(cost_usd) FROM runs
               WHERE finished_at IS NOT NULL AND started_at >= ?""",
            (month_start or _month_start(),),
        ).fetchone()
        return LibraryStats(
            by_status=by_status,
            by_category=by_category,
            filed_bytes=int(filed),
            last_run=None if last is None else RunRow.from_row(last),
            month_cost_usd=float(month[0]),
            month_unpriced_runs=int(month[1]),
        )

    def automatic_cost_today(self, *, day_start: str | None = None) -> float:
        """What automatic runs (daily and watch) cost since ``day_start`` (UTC ISO text;
        by default midnight today, local time). Runs without a recorded cost add nothing."""
        row = self.conn.execute(
            """SELECT COALESCE(SUM(cost_usd), 0) FROM runs
               WHERE trigger IN ('schedule', 'watch') AND started_at >= ?""",
            (day_start or day_start_utc(),),
        ).fetchone()
        return float(row[0])

    def automatic_files_today(self, *, day_start: str | None = None) -> int:
        """Items today's automatic runs saw (``limits.max_files_per_day``)."""
        row = self.conn.execute(
            """SELECT COALESCE(SUM(files_seen), 0) FROM runs
               WHERE trigger IN ('schedule', 'watch') AND started_at >= ?""",
            (day_start or day_start_utc(),),
        ).fetchone()
        return int(row[0])

    def last_run_by(self, trigger: RunTrigger) -> RunRow | None:
        """The newest finished run with this trigger."""
        row = self.conn.execute(
            """SELECT * FROM runs WHERE trigger = ? AND finished_at IS NOT NULL
               ORDER BY id DESC LIMIT 1""",
            (trigger.value,),
        ).fetchone()
        return None if row is None else RunRow.from_row(row)

    def runs_since(self, since: str, *, limit: int) -> list[RunRow]:
        """Runs (never simulations) started at or after ``since`` (UTC ISO text),
        newest first, at most ``limit`` (the interface's Activity page, #114)."""
        rows = self.conn.execute(
            """SELECT * FROM runs WHERE mode = 'run' AND started_at >= ?
               ORDER BY id DESC LIMIT ?""",
            (since, limit),
        )
        return [RunRow.from_row(r) for r in rows]

    def sensitive_by_run(self, run_ids: Sequence[int]) -> dict[int, int]:
        """Sensitive files each run left in place, by run id (counts only)."""
        found: dict[int, int] = {}
        for start in range(0, len(run_ids), 500):
            chunk = run_ids[start : start + 500]
            rows = self.conn.execute(
                f"""SELECT run_id, COUNT(*) FROM files
                    WHERE status = 'sensitive' AND run_id IN ({", ".join("?" * len(chunk))})
                    GROUP BY run_id""",  # noqa: S608 - placeholders only
                tuple(chunk),
            )
            found.update((int(r[0]), int(r[1])) for r in rows)
        return found

    def waiting(self) -> list[WaitingRow]:
        """Files in review or failed, oldest first. No names: only why, and since when.

        A ``missing`` row with an error is a copy that failed to take it over: it
        counts as failed.
        """
        rows = self.conn.execute(
            """SELECT status, review_reason, review_category, attempts, last_error, processed_at
               FROM files
               WHERE status IN ('review', 'failed')
                  OR (status = 'missing' AND last_error IS NOT NULL)
               ORDER BY processed_at, id"""
        )
        return [
            WaitingRow(
                status=FileStatus(r["status"]),
                review_reason=_reason(r["review_reason"]),
                review_category=r["review_category"],
                attempts=int(r["attempts"]),
                last_error=r["last_error"],
                processed_at=str(r["processed_at"]),
            )
            for r in rows
        ]

    # --- meta: flib's own bookkeeping ---------------------------------------------

    def meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            """INSERT INTO meta (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, value),
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

    def latest_cached_answer(self, sha256: str) -> str | None:
        """The newest answer cached for ``sha256`` under any rules, whatever its age."""
        row = self.conn.execute(
            """SELECT answer FROM classification_cache WHERE sha256 = ?
               ORDER BY created_at DESC LIMIT 1""",
            (sha256,),
        ).fetchone()
        return None if row is None else str(row[0])

    def cached_answers(self) -> list[tuple[str, str, str]]:
        """Every cached ``(sha256, rules_key, answer)``, whatever its age."""
        rows = self.conn.execute("SELECT sha256, rules_key, answer FROM classification_cache")
        return [(str(r[0]), str(r[1]), str(r[2])) for r in rows]

    def replace_cached_answer(self, sha256: str, rules_key: str, answer_json: str) -> None:
        """Rewrite a cached answer in place; its age stays."""
        self.conn.execute(
            "UPDATE classification_cache SET answer = ? WHERE sha256 = ? AND rules_key = ?",
            (answer_json, sha256, rules_key),
        )

    def pending_records(self) -> list[tuple[int, str]]:
        """``(operation id, record JSON)`` of every journaled move that carries a record."""
        rows = self.conn.execute(
            "SELECT id, pending_record FROM operations WHERE pending_record IS NOT NULL"
        )
        return [(int(r[0]), str(r[1])) for r in rows]

    def replace_pending_record(self, op_id: int, record_json: str) -> None:
        self.conn.execute(
            "UPDATE operations SET pending_record = ? WHERE id = ?", (record_json, op_id)
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
        previous: tuple[FileStatus, str | None] | None = None,
        pending: FileRecord | None = None,
    ) -> int:
        """Journal a move. ``identity`` is the source's (st_dev, st_ino); ``fix`` is the
        (status, category) a ``flib fix`` gives the file, so recovery can finish it;
        ``pending`` the record a takeover writes once the item is in place."""
        fix_status, fix_category = (None, None) if fix is None else (fix[0].value, fix[1])
        prev_status, prev_category = (
            (None, None) if previous is None else (previous[0].value, previous[1])
        )
        cur = self.conn.execute(
            """INSERT INTO operations (run_id, file_id, src_path, dst_path, status, created_at,
                                      src_dev, src_ino, fix_status, fix_category, prev_status,
                                      prev_category, pending_record)
               VALUES (?, ?, ?, ?, 'planned', ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                run_id, file_id, str(src), str(dst), _now(), identity[0], identity[1],
                fix_status, fix_category, prev_status, prev_category,
                None if pending is None else record_to_json(pending),
            ),
        )  # fmt: skip
        return _rowid(cur)

    def set_operation(self, op_id: int, status: OperationStatus, dst: Path | None = None) -> None:
        """Set the status (and destination); this ends any cross-volume copy phase.

        ``undone`` also records when (``undone_at``).
        """
        undone_at = _now() if status is OperationStatus.UNDONE else None
        self.conn.execute(
            """UPDATE operations SET status = ?, dst_path = coalesce(?, dst_path),
                                     undone_at = ?, copy_phase = NULL, copy_key = NULL
               WHERE id = ?""",
            (status.value, None if dst is None else str(dst), undone_at, op_id),
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

    def done_moves(self) -> dict[int, list[tuple[Path, Path]]]:
        """Every file's moves still in effect (``done``), newest first: ``(src, dst)``."""
        moves: dict[int, list[tuple[Path, Path]]] = {}
        rows = self.conn.execute(
            "SELECT file_id, src_path, dst_path FROM operations WHERE status = 'done' "
            "ORDER BY id DESC"
        )
        for r in rows:
            moves.setdefault(int(r[0]), []).append((Path(r[1]), Path(r[2])))
        return moves

    def flib_folders(self) -> set[Path]:
        """The folders flib created while moving a file into the library."""
        return {Path(r[0]) for r in self.conn.execute("SELECT path FROM layout_folders")}

    def add_flib_folders(self, run_id: int, folders: Sequence[Path]) -> None:
        """Remember folders flib just created (``fsops.ensure_folder`` made them)."""
        self.conn.executemany(
            "INSERT OR IGNORE INTO layout_folders (path, run_id, created_at) VALUES (?, ?, ?)",
            [(normalize(str(f)), run_id, _now()) for f in folders],
        )

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

    def operation_by_id(self, op_id: int) -> OperationRow:
        row = self.conn.execute("SELECT * FROM operations WHERE id = ?", (op_id,)).fetchone()
        if row is None:
            raise LookupError(f"no operation with id {op_id}")
        return OperationRow.from_row(row)

    def last_tag_operation(self, file_id: int) -> OperationRow | None:
        """The newest move still in effect that added a Finder tag."""
        row = self.conn.execute(
            """SELECT * FROM operations
               WHERE file_id = ? AND status = 'done' AND finder_tag IS NOT NULL
               ORDER BY id DESC LIMIT 1""",
            (file_id,),
        ).fetchone()
        return None if row is None else OperationRow.from_row(row)

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


def _as_text(value: object) -> str | None:
    """A column value as ``index_changes`` keeps it; SQLite's column affinity turns it
    back into a number when it is written to a REAL column."""
    if value is None:
        return None
    return repr(value) if isinstance(value, float) else str(value)


# Keeps the review reason and suggested category only while the new status (bound
# twice, after the status itself) is ``review``.
_KEEP_REVIEW = (
    "review_reason = CASE WHEN ? = 'review' THEN review_reason END, "
    "review_category = CASE WHEN ? = 'review' THEN review_category END"
)


def _code(reason: ReviewReason | None) -> str | None:
    return None if reason is None else reason.value


def _reason(code: str | None) -> ReviewReason | None:
    return None if code is None else ReviewReason(code)


def _month_start() -> str:
    """Midnight on the first day of the current month, local time, as UTC ISO text."""
    today = datetime.now().astimezone().date()
    # Through a naive time, so the offset is the one in force at midnight (DST).
    start = datetime.combine(today.replace(day=1), time.min).astimezone()
    return start.astimezone(UTC).isoformat(timespec="seconds")


def day_start_utc(now: datetime | None = None) -> str:
    """Midnight today, local time, as UTC ISO text (the day of the daily cost cap)."""
    today = (now or datetime.now()).astimezone().date()
    # Through a naive time, so the offset is the one in force at midnight (DST).
    start = datetime.combine(today, time.min).astimezone()
    return start.astimezone(UTC).isoformat(timespec="seconds")


def _filter_clause(
    categories: Sequence[str],
    extensions: Sequence[str],
    since: str | None,
    until: str | None,
    statuses: Sequence[FileStatus],
    fields: FieldFilters | None = None,
) -> tuple[list[str], list[object]]:
    """SQL conditions on ``files f`` for the search filters, with their bound values."""
    where: list[str] = []
    params: list[object] = []
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
    if fields:
        _field_clause(fields, where, params)
    return where, params


def _field_clause(fields: FieldFilters, where: list[str], params: list[object]) -> None:
    if fields.issuer_key is not None:
        where.append("flib_issuer_key(f.issuer) = ?")
        params.append(fields.issuer_key)
    if fields.kinds:
        where.append(f"f.kind IN ({', '.join('?' * len(fields.kinds))})")
        params.extend(fields.kinds)
    if fields.currency is not None:
        where.append("f.currency = ?")
        params.append(fields.currency)
    for bound, operator in ((fields.min_amount, ">="), (fields.max_amount, "<=")):
        if bound is not None:
            where.append(f"flib_amount(f.amount_minor, f.currency) {operator} ?")
            params.append(float(bound))
    if fields.due_before is not None:
        where.append("f.due_date < ?")
        params.append(fields.due_before)
    if fields.due_after is not None:
        where.append("f.due_date > ?")
        params.append(fields.due_after)
    if fields.period is not None:
        if len(fields.period) == 4:
            where.append(f"({EFFECTIVE_PERIOD} = ? OR {EFFECTIVE_PERIOD} LIKE ?)")
            params.extend((fields.period, fields.period + "-%"))
        else:
            where.append(f"{EFFECTIVE_PERIOD} = ?")
            params.append(fields.period)


def record_to_json(r: FileRecord) -> str:
    """A ``FileRecord`` as JSON, for the journal (``operations.pending_record``)."""
    data = {
        f: getattr(r, f)
        for f in (
            "sha256", "extension", "size_bytes", "is_unit", "source_url", "category",
            "description", "confidence", "attempts", "last_error", "provider", "model",
            "name_choice", "name_choice_reason", "review_category", "duplicate_of",
            "document_date", "issuer", "layout_date", "amount_minor", "currency", "due_date",
            "period", "kind",
        )
    }  # fmt: skip
    data |= {
        "source_input": _path_text(r.source_input),
        "by_rule": r.by_rule,
        "sensitive_by": r.sensitive_by,
        "sensitive_reason": r.sensitive_reason,
        "original_path": str(r.original_path),
        "current_path": str(r.current_path),
        "status": r.status.value,
        "tags": list(r.tags),
        "review_reason": _code(r.review_reason),
    }
    return json.dumps(data, ensure_ascii=False)


def record_from_json(text: str | None) -> FileRecord | None:
    if text is None:
        return None
    data = json.loads(text)
    data["original_path"] = Path(data["original_path"])
    data["current_path"] = Path(data["current_path"])
    data["status"] = FileStatus(data["status"])
    data["tags"] = tuple(data["tags"])
    data["review_reason"] = _reason(data["review_reason"])
    # All absent from records journaled before #89.
    source = data.pop("source_input", None)
    by_rule = bool(data.pop("by_rule", False))
    data.setdefault("sensitive_by", None)
    data.setdefault("sensitive_reason", None)
    return FileRecord(
        **data, source_input=None if source is None else Path(source), by_rule=by_rule
    )


def _path_text(path: Path | None) -> str | None:
    return None if path is None else normalize(str(path))


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

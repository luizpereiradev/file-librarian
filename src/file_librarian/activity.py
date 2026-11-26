"""What flib did, run by run and day by day: the interface's Activity page (#114).

Read-only and lock-free, like ``flib status``: the runs of the last days
(never simulations), newest first, each with what it moved where (the undo
journal, ``operations``), what it set aside as a duplicate, how many
sensitive files it left in place (a count only: a sensitive file is never
listed with details), the index changes it made without moving anything
(``flib fix --done``, renamed categories), its cost, and what its log says
about the Mac being locked: files skipped while it was, a wait for the
unlock and how it ended, or a catch-up run made after it. Logs are kept for
the newest runs only (``runlog.KEEP``), so older runs say nothing about it.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from file_librarian import catchup
from file_librarian.config import Config
from file_librarian.db.store import FileRow, OperationRow, RunRow, Store, load_migrations
from file_librarian.models import FileStatus, OperationStatus, RunTrigger
from file_librarian.runlog import log_path
from file_librarian.status import count_skips

DAYS = (7, 30, 90)
"""The windows the Activity page offers, in days."""
DEFAULT_DAYS = 7
MAX_RUNS = 200
"""Most runs listed: a busy watch mode makes many small ones."""
MAX_MOVES = 50
"""Most moves listed per run; the rest are counted."""
MIN_SCHEMA = 18
"""Reads the current schema only; an older index is updated by the next run."""


class ActivityError(ValueError):
    """The index cannot be read here; the message says why (never a path)."""


@dataclass(frozen=True, slots=True)
class Move:
    """One journaled move of a run, as the page shows it."""

    file_id: int
    source: Path
    destination: Path
    status: OperationStatus
    """``done``, or ``undone`` once ``flib undo`` put it back; ``failed``..."""
    file_status: FileStatus
    """What the file is now: organized, review, duplicate..."""
    category: str | None
    fix: bool
    """A correction by hand (``flib fix``)."""

    @property
    def duplicate(self) -> bool:
        return self.file_status is FileStatus.DUPLICATE


@dataclass(frozen=True, slots=True)
class Unlock:
    """What a run's log says about the Mac being locked."""

    locked: int = 0
    """Files skipped because macOS protects them while the Mac is locked."""
    catch_up: bool = False
    """This run was made after the Mac was unlocked."""
    waited: str | None = None
    """``unlocked``, ``timed out``, ``stopped`` or ``waiting`` when the run waited
    for an unlock; ``elsewhere`` when another run was waiting already."""


@dataclass(frozen=True, slots=True)
class RunActivity:
    run: RunRow
    moves: list[Move] = field(default_factory=list)
    more: int = 0
    """Moves left out past ``MAX_MOVES``."""
    sensitive: int = 0
    settled: int = 0
    """Documents marked done (or open again): ``flib fix --done``."""
    index_changes: int = 0
    """Other changes to the index that moved nothing."""
    unlock: Unlock = Unlock()

    @property
    def duplicates(self) -> int:
        return sum(1 for m in self.moves if m.duplicate)


@dataclass(frozen=True, slots=True)
class Day:
    day: date
    """Local date."""
    runs: list[RunActivity]

    @property
    def cost_usd(self) -> float:
        return sum(r.run.cost_usd or 0.0 for r in self.runs)

    @property
    def unpriced(self) -> int:
        return sum(1 for r in self.runs if r.run.cost_usd is None)


@dataclass(frozen=True, slots=True)
class Activity:
    days: int
    """The window asked for."""
    found: list[Day]
    capped: bool = False
    """More runs than ``MAX_RUNS`` in the window: the oldest are left out."""


def local_day(instant: str) -> date:
    """The local date of a UTC ISO instant from the index."""
    moment = datetime.fromisoformat(instant)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone().date()


def collect(
    config: Config,
    days: int = DEFAULT_DAYS,
    *,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Activity:
    """The runs of the last ``days`` days; raises ``ActivityError``. Creates nothing."""
    if not config.database.exists():
        return Activity(days, [])
    today = now().astimezone().date()
    start = datetime.combine(today - timedelta(days=days - 1), datetime.min.time()).astimezone()
    since = start.astimezone(UTC).isoformat(timespec="seconds")
    try:
        with Store.open_read_only(config.database) as store:
            check_schema(store)
            runs = store.runs_since(since, limit=MAX_RUNS + 1)
            capped = len(runs) > MAX_RUNS
            runs = runs[:MAX_RUNS]
            sensitive = store.sensitive_by_run([r.id for r in runs])
            found = [_run(store, config, run, sensitive.get(run.id, 0)) for run in runs]
    except sqlite3.Error as exc:
        raise ActivityError("cannot read the index") from exc
    grouped: dict[date, list[RunActivity]] = {}
    for item in found:
        grouped.setdefault(local_day(item.run.started_at), []).append(item)
    return Activity(days, [Day(d, items) for d, items in grouped.items()], capped)


def check_schema(store: Store) -> None:
    version = store.schema_version
    if version > len(load_migrations()):
        raise ActivityError(
            f"the index (schema {version}) is newer than this flib; upgrade file-librarian"
        )
    if version < MIN_SCHEMA:
        raise ActivityError("the index is from an older flib; the next `flib run` updates it")


def _run(store: Store, config: Config, run: RunRow, sensitive: int) -> RunActivity:
    operations = store.operations(run_id=run.id)
    files = store.files_by_ids(sorted({op.file_id for op in operations}))
    moves = [
        _move(op, files[op.file_id])
        for op in operations
        if op.file_id in files and files[op.file_id].status is not FileStatus.SENSITIVE
    ]
    changes = store.index_changes(run.id)
    settled = sum(1 for c in changes if c.target == "files.settled_at")
    return RunActivity(
        run=run,
        moves=moves[:MAX_MOVES],
        more=max(0, len(moves) - MAX_MOVES),
        sensitive=sensitive,
        settled=settled,
        index_changes=len(changes) - settled,
        unlock=read_unlock(config.database, run.id),
    )


def _move(op: OperationRow, row: FileRow) -> Move:
    return Move(
        file_id=op.file_id,
        source=op.src_path,
        destination=op.dst_path,
        status=op.status,
        file_status=row.status,
        category=row.category,
        fix=op.fix is not None,
    )


_WAITING = catchup.WAITING_LINE.split("{hours}", 1)[0]
_TIMED_OUT = catchup.TIMED_OUT_LINE.split("{hours}", 1)[0]


def read_unlock(database: Path, run_id: int) -> Unlock:
    """What the run's log says about the Mac being locked; nothing without a log."""
    try:
        text = log_path(database, run_id).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return Unlock()
    lines = text.splitlines()
    waited = None
    for line in lines:
        if line.startswith(_WAITING) and waited is None:
            waited = "waiting"
        elif line == catchup.UNLOCKED_LINE:
            waited = "unlocked"
        elif line.startswith(_TIMED_OUT):
            waited = "timed out"
        elif line == catchup.STOPPED_LINE:
            waited = "stopped"
        elif line == catchup.NOT_WAITING_LINE:
            waited = "elsewhere"
    return Unlock(count_skips(lines)["locked"], catchup.CATCH_UP_LINE in lines, waited)


TRIGGERS = {
    RunTrigger.SCHEDULE: "daily run",
    RunTrigger.MANUAL: "by hand",
    RunTrigger.MCP: "from an assistant",
    RunTrigger.REPROCESS: "reprocess",
    RunTrigger.WATCH: "on arrival (watch)",
}

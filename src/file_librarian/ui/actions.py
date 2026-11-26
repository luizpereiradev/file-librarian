"""What the interface changes: review decisions and undo (#115).

The first routes of ``flib ui`` that move files. Each one does exactly what
the command line does, through the same functions, under the same lock and
journal: ``fix.fix_file`` (``flib fix``), ``fix.fix_files`` (several fixes
as one run, so one undo reverts them), ``undo.undo_run`` and
``undo.undo_file`` (``flib undo``), and ``due.settle`` (``flib fix --done``).
No file is moved by any other code path.

Every action:

- comes as a POST through the server's route table (cookie, exact origin,
  CSRF), and answers with a redirect to a page that shows what happened, so
  Back, Forward and reload never repeat it;
- names files by their index id, never a path, with a *pin*: a digest of
  the state the page showed (where the file is, its content, its status and
  review reason; for an undo, the journal entries it would revert). The pin
  is computed again under the lock: a file moved, renamed, undone, deleted or
  handled in another tab or by a run since the page was built is refused,
  never acted on;
- takes ``lock.instance_lock`` for that action only, as ``flib fix`` does,
  and finishes an interrupted journal first (``executor.recover``). When a
  run holds the lock it says so and does nothing;
- refuses a sensitive file, never naming it;
- never writes to an index of another schema (the next ``flib run``
  migrates it), and never creates one.

Messages never carry a path or an ``OSError``'s text: ``FixError`` and
undo reasons are turned into the same words without the path.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from file_librarian import due as due_mod
from file_librarian.config import Config
from file_librarian.db.store import FileRow, IndexChange, OperationRow, Store, load_migrations
from file_librarian.executor import recover
from file_librarian.fix import FixError, fix_file, fix_files
from file_librarian.lock import AlreadyRunningError, instance_lock
from file_librarian.models import FileStatus, ReviewReason, RunTrigger
from file_librarian.undo import (
    NothingToUndoError,
    UndoReport,
    file_undo_plan,
    run_undo_plan,
    undo_file,
    undo_run,
)

BULK_CAP = 100
"""Most files one "accept all" moves; the rest of the group waits for the next one."""
PIN_LENGTH = 20
MAX_ID_DIGITS = 18
MAX_NAME_CHARS = 1024
"""A new name longer than this is refused before ``fix`` looks at it (it allows 255
bytes)."""


@dataclass(frozen=True, slots=True)
class Item:
    """A file named in an outcome, and what happened to it (flib's words, no path)."""

    name: str
    text: str


@dataclass(frozen=True, slots=True)
class Outcome:
    """What an action did, kept for the page the browser is sent to next."""

    text: str
    """A sentence in flib's words; `backticks` mark a command."""
    error: bool = False
    items: tuple[Item, ...] = ()
    undo_run: int | None = None
    """The run that did it: the page offers to undo it."""


class Refused(Exception):
    """The action was not done; ``outcome`` says why."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.outcome = Outcome(text, error=True)


class Malformed(ValueError):
    """A form the pages never send: a bad id, an unknown choice (400)."""


STALE = (
    "This file is no longer as the page showed it: it was moved, renamed or undone since "
    "(in another tab, from the command line or by a run). Nothing was done; the page now "
    "shows how it is."
)
STALE_RUN = (
    "This run's moves changed since the page was shown (undone in another tab, or by a "
    "later run). Nothing was done; check again below."
)
STALE_GROUP = (
    "This group changed since the list was shown (a file was handled or added). Nothing "
    "was moved; check the list again."
)
NO_INDEX = "No run yet: the index is created by the first `flib run`."
OLD_INDEX = "The index is from another version of flib; the next `flib run` updates it."
SENSITIVE = (
    "The interface does not act on sensitive files. `flib undo` in Terminal does, if you need it."
)
GONE = "That file is not in the index any more."


# --- pins ------------------------------------------------------------------------------


def _digest(*parts: object) -> str:
    text = "\0".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(text.encode("utf-8", "surrogatepass")).hexdigest()[:PIN_LENGTH]


def file_pin(row: FileRow) -> str:
    """The state of a file as a page shows it: where, what content, what status."""
    return _digest(
        "file",
        row.id,
        row.status.value,
        row.current_path,
        row.sha256,
        row.category,
        row.review_reason,
        row.review_category,
        row.settled_at,
    )


def run_pin(ops: list[OperationRow], changes: list[IndexChange]) -> str:
    """What undoing a run would revert: its moves and index changes still in effect."""
    return _digest("run", *(op.id for op in ops), "changes", *(c.id for c in changes))


def undo_file_pin(row: FileRow, op: OperationRow) -> str:
    return _digest("undo", file_pin(row), op.id)


def group_key(reason: object, suggested: str | None) -> str:
    """A review group in an address: a digest, never the model's words."""
    return _digest("group", reason, suggested)[:16]


def group_pin(rows: list[FileRow]) -> str:
    return _digest("group", *(file_pin(r) for r in rows))


# --- the form --------------------------------------------------------------------------


def one(fields: dict[str, list[str]], name: str, *, required: bool = True) -> str | None:
    values = fields.get(name, [])
    if len(values) > 1 or (required and not values):
        raise Malformed(name)
    return values[0] if values else None


def an_id(fields: dict[str, list[str]], name: str = "id") -> int:
    value = one(fields, name)
    if value is None or not value.isdigit() or not value.isascii() or len(value) > MAX_ID_DIGITS:
        raise Malformed(name)
    return int(value)


def a_pin(fields: dict[str, list[str]]) -> str:
    value = one(fields, "pin")
    if value is None or len(value) != PIN_LENGTH or not all(c in "0123456789abcdef" for c in value):
        raise Malformed("pin")
    return value


def ids(text: str) -> list[int]:
    """``1,2,3``: the files an "accept all" confirmed."""
    parts = text.split(",")
    if not text or len(parts) > BULK_CAP or len(set(parts)) != len(parts):
        raise Malformed("ids")
    found = []
    for part in parts:
        if not part.isdigit() or not part.isascii() or len(part) > MAX_ID_DIGITS:
            raise Malformed("ids")
        found.append(int(part))
    return found


# --- the index -------------------------------------------------------------------------


def schema_ok(config: Config) -> None:
    """Refuse unless the index exists at exactly this flib's schema: an action never
    creates, migrates or writes an index of another version."""
    if not config.database.exists():
        raise Refused(NO_INDEX)
    try:
        with Store.open_read_only(config.database) as store:
            version = store.schema_version
    except sqlite3.Error as exc:
        raise Refused("Cannot read the index; nothing was done.") from exc
    if version != len(load_migrations()):
        raise Refused(OLD_INDEX)


@contextmanager
def writing(config: Config) -> Iterator[Store]:
    """The instance lock and the index for one action, as ``flib fix`` takes them;
    an interrupted journal is finished first. Raises ``Refused``."""
    schema_ok(config)
    try:
        with instance_lock(config.database), Store.open(config.database) as store:
            recover(store, config)  # finish the journal before adding to it
            yield store
    except AlreadyRunningError as exc:
        raise Refused(f"A run or another change holds flib now ({exc}). Nothing was done.") from exc
    except sqlite3.Error as exc:
        raise Refused("Cannot write to the index; nothing was done.") from exc


def _row(store: Store, file_id: int) -> FileRow:
    try:
        return store.file_by_id(file_id)
    except LookupError as exc:
        raise Refused(GONE) from exc


def without_path(text: str, path: Path) -> str:
    """A message of ``flib fix``/``flib undo`` about ``path`` with "This file" in its
    place; one that carries an ``OSError`` (a path) becomes general words."""
    shown = str(path)
    if text.startswith(shown):
        text = "This file" + text[len(shown) :]
    return text.replace(shown, "it")


def _fix_text(exc: FixError, path: Path) -> str:
    if isinstance(exc.__cause__, (OSError, ValueError)):
        return "It could not be moved; nothing was changed."
    text = str(exc)
    if "is not a plain file name" in text:
        return "The new name is not a plain file name (no '/', ':' or leading '.')."
    return without_path(text, path)


def _undo_reason(reason: str) -> str:
    """An undo's reason for skipping a file, without the path some of them end with."""
    for prefix in ("original folder is gone", "original path is taken", "could not move back"):
        if reason.startswith(prefix):
            return {
                "original folder is gone": "its original folder is gone",
                "original path is taken": "something else has its original name now",
                "could not move back": "it could not be moved back",
            }[prefix]
    return reason


# --- review ----------------------------------------------------------------------------


def fix_one(
    config: Config,
    file_id: int,
    pin: str,
    *,
    category: str | None,
    name: str | None,
) -> Outcome:
    """One file out of review: to ``category`` (a configured one), renamed to ``name``,
    or both. ``flib fix``, under its lock; refused when the pin no longer matches."""
    if category is not None and config.category(category) is None:
        raise Malformed("category")
    with writing(config) as store:
        row = _row(store, file_id)
        if row.status is FileStatus.SENSITIVE:
            raise Refused(SENSITIVE)
        if row.status is not FileStatus.REVIEW or file_pin(row) != pin:
            raise Refused(STALE)
        try:
            report = fix_file(config, store, row.current_path, category=category, name=name)
        except FixError as exc:
            raise Refused(_fix_text(exc, row.current_path)) from exc
    where = report.destination.parent.name if category is not None else config.review_folder
    text = f"Moved to {where}." if category is not None else "Renamed; it stays in review."
    items = [Item(report.destination.name, f"now in {where}/")]
    items += [Item(report.destination.name, w) for w in report.warnings]
    return Outcome(text, items=tuple(items), undo_run=report.run_id)


@dataclass(frozen=True, slots=True)
class Group:
    """Files in review for one reason with one suggestion, oldest first."""

    key: str
    reason: ReviewReason | None
    suggested: str | None
    rows: list[FileRow]

    def target(self, config: Config) -> str | None:
        """The configured category the suggestion names (as ``flib fix`` matches it), or
        ``None``: then there is nothing to accept."""
        if self.suggested is None:
            return None
        found = config.category(self.suggested) or next(
            (c for c in config.categories if c.name.casefold() == self.suggested.casefold()),
            None,
        )
        return None if found is None else found.name

    @property
    def batch(self) -> list[FileRow]:
        """What one "accept all" moves: the oldest ``BULK_CAP``."""
        return self.rows[:BULK_CAP]


MAX_LISTED = 500
"""Most files the Review page lists; the rest show once these are handled."""


def review_groups(store: Store) -> tuple[list[Group], int]:
    """The files in review by reason and suggestion, the biggest group first, and how
    many in review are not listed (past ``MAX_LISTED``)."""
    rows = store.review_files(limit=MAX_LISTED)
    grouped: dict[tuple[ReviewReason | None, str | None], list[FileRow]] = {}
    for row in rows:
        grouped.setdefault(row.review, []).append(row)
    groups = [Group(group_key(r, s), r, s, items) for (r, s), items in grouped.items()]
    groups.sort(key=lambda g: (-len(g.rows), g.rows[0].processed_at))
    total = store.stats().by_status[FileStatus.REVIEW.value]
    return groups, max(0, total - len(rows))


def find_group(store: Store, key: str) -> Group | None:
    groups, _ = review_groups(store)
    return next((g for g in groups if g.key == key), None)


def accept_group(config: Config, key: str, confirmed: list[int], pin: str) -> Outcome:
    """ "Accept all": the files the confirmation page listed go to the group's suggested
    category, as **one** run (``fix.fix_files``): one undo reverts them all. Refused
    unless the group's first ``BULK_CAP`` are still exactly those files, as shown."""
    with writing(config) as store:
        group = find_group(store, key)
        if group is None:
            raise Refused(STALE_GROUP)
        target = group.target(config)
        if target is None:
            raise Malformed("group")  # the page never offers it
        batch = group.batch
        if [r.id for r in batch] != confirmed or group_pin(batch) != pin:
            raise Refused(STALE_GROUP)
        report = fix_files(config, store, [(r.current_path, target, None) for r in batch])
    names = {r.current_path: r.current_name for r in batch}
    items = [Item(names[path], _fix_reason(text, path)) for path, text in report.skipped]
    for fixed in report.fixed:
        items += [Item(fixed.destination.name, w) for w in fixed.warnings]
    moved = len(report.fixed)
    if not moved:
        return Outcome("Nothing was moved.", error=True, items=tuple(items))
    text = f"Moved {_files(moved)} to {target}, as one run."
    if report.skipped:
        text += f" {_files(len(report.skipped))} could not be moved and stay in review."
    return Outcome(text, items=tuple(items), undo_run=report.run_id)


def _files(count: int) -> str:
    return "1 file" if count == 1 else f"{count} files"


def _fix_reason(text: str, path: Path) -> str:
    """A ``fix_files`` skip reason (a ``FixError``'s words) without the path."""
    if text.startswith("could not move"):
        return "it could not be moved"
    return without_path(text, path)


# --- undo ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RunPlan:
    run_id: int
    ops: list[OperationRow]
    changes: list[IndexChange]
    rows: dict[int, FileRow]
    pin: str


def plan_run(store: Store, run_id: int) -> RunPlan:
    """What undoing run ``run_id`` reverts; ``Refused`` with ``flib undo``'s words when
    it reverts nothing, and for a run that moved a file now sensitive."""
    try:
        ops, changes = run_undo_plan(store, run_id)
    except NothingToUndoError as exc:
        raise Refused(str(exc)) from exc
    rows = store.files_by_ids(sorted({op.file_id for op in ops}))
    if any(row.status is FileStatus.SENSITIVE for row in rows.values()):
        raise Refused(SENSITIVE)
    return RunPlan(run_id, ops, changes, rows, run_pin(ops, changes))


@dataclass(frozen=True, slots=True)
class FilePlan:
    row: FileRow
    op: OperationRow
    pin: str


def plan_file(store: Store, file_id: int) -> FilePlan:
    """The move undoing this one file reverts (its last); ``Refused`` as ``flib undo``."""
    row = _row(store, file_id)
    if row.status is FileStatus.SENSITIVE:
        raise Refused(SENSITIVE)
    try:
        op = file_undo_plan(store, row.current_path)
    except NothingToUndoError as exc:
        raise Refused(without_path(str(exc), row.current_path)) from exc
    return FilePlan(row, op, undo_file_pin(row, op))


def undo_one_run(config: Config, run_id: int, pin: str) -> Outcome:
    """``flib undo --run``, refused unless it still reverts what the page showed."""
    with writing(config) as store:
        plan = plan_run(store, run_id)
        if plan.pin != pin:
            raise Refused(STALE_RUN)
        report = undo_run(store, run_id, config=config)
    return _undone(report, config, f"Run {run_id} undone")


def undo_one_file(config: Config, file_id: int, pin: str) -> Outcome:
    """``flib undo --file``, refused unless the file and its last move are as shown."""
    with writing(config) as store:
        plan = plan_file(store, file_id)
        if plan.pin != pin:
            raise Refused(STALE)
        report = undo_file(store, plan.row.current_path, config=config)
    return _undone(report, config, "Undone")


def _undone(report: UndoReport, config: Config, title: str) -> Outcome:
    from file_librarian.ui.views import folder  # the page's way of showing a folder

    items = [Item(src.name, f"back in {folder(src, config)}") for _, src in report.restored]
    items += [Item(dst.name, f"skipped: {_undo_reason(why)}") for dst, why in report.skipped]
    items += [Item(path.name, f"warning: {_undo_reason(w)}") for path, w in report.warnings]
    text = f"{title}: {len(report.restored)} restored, {len(report.skipped)} skipped"
    if report.reverted:
        text += f", {report.reverted} index {'change' if report.reverted == 1 else 'changes'} "
        text += "reverted"
    return Outcome(
        text + ".", error=not report.restored and not report.reverted, items=tuple(items)
    )


# --- due -------------------------------------------------------------------------------


def settle_one(config: Config, file_id: int, pin: str, *, done: bool) -> Outcome:
    """``flib fix --done`` / ``--not-done`` for one document, journaled; undo reverts it."""
    with writing(config) as store:
        row = _row(store, file_id)
        if row.status is FileStatus.SENSITIVE:
            raise Refused(SENSITIVE)
        if file_pin(row) != pin:
            raise Refused(STALE)
        try:
            report = due_mod.settle(store, row.current_path, done=done, trigger=RunTrigger.MANUAL)
        except due_mod.DueError as exc:
            raise Refused(without_path(str(exc), row.current_path)) from exc
    text = "Marked done." if done else "Open again."
    return Outcome(
        text, items=(Item(row.current_name, f"due {report.due_date}"),), undo_run=report.run_id
    )

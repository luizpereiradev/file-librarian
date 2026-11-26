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
import hmac
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from file_librarian import due as due_mod
from file_librarian.config import Config
from file_librarian.db.store import FileRow, IndexChange, OperationRow, Store, load_migrations
from file_librarian.executor import recover
from file_librarian.fix import NOT_WRITTEN, FixError, FixItem, fix_file, fix_files
from file_librarian.lock import AlreadyRunningError, instance_lock
from file_librarian.models import FileStatus, OperationStatus, ReviewReason, RunTrigger
from file_librarian.undo import (
    NothingToUndoError,
    UndoReport,
    file_undo_plan,
    run_undo_plan,
    skip_reason,
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
    undo_label: str = "Undo this…"
    """What the undo link says it undoes."""
    name: str | None = None
    """A file the sentence is about: shown (isolated, as text) before ``text``."""
    anchor: str | None = None
    """Where on the page the browser goes back to (a review group's ``id``)."""
    restore: bool = False
    """A settings change (#117): the page offers to restore the settings from before."""
    form: tuple[tuple[str, str], ...] = ()
    """A refused settings form (#117): what was typed, put back in the fields."""


class Refused(Exception):
    """The action was not done; ``outcome`` says why."""

    def __init__(self, text: str) -> None:
        super().__init__(text)
        self.outcome = Outcome(text, error=True)


class Malformed(ValueError):
    """A form the pages never send: a bad id, an unknown choice (400)."""


STALE = (
    "This file is no longer as the page showed it: it was moved, renamed or undone since, "
    "in another tab, from Terminal or by a run. Nothing was done; the page now shows how "
    "it is."
)
STALE_UNDO = (
    "This is no longer as the Undo page showed it: something was moved or undone since, "
    "in another tab, from Terminal or by a run. Nothing was done. Open Undo again from "
    "Review or Activity to see how it is now."
)
STALE_GROUP = (
    "This group changed since the list was shown: a file was handled or added. Nothing "
    "was moved; check the list again."
)
BUSY = (
    "A flib run, or another change, is going on now. Nothing was done; try again when it finishes."
)
NOT_WRITTEN_TEXT = (
    "The index could not be written. Anything already moved is recorded by the next flib "
    "command; see Activity."
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
    """What undoing a run would revert: its moves still in effect, or, for a run that
    moved nothing, its index changes. (A run's moves carry index changes of their own,
    such as a review reason; recovery may add one, and only an undo, which marks the
    moves undone too, takes them away.)"""
    if ops:
        return _digest("run", *(op.id for op in ops))
    return _digest("changes", *(c.id for c in changes))


def undo_file_pin(row: FileRow, op: OperationRow) -> str:
    return _digest("undo", file_pin(row), op.id)


def group_key(secret: str, reason: object, suggested: str | None) -> str:
    """A review group in an address: keyed with the session's secret, so the model's
    words cannot be guessed back from the browser's history."""
    text = f"{reason}\0{suggested}".encode("utf-8", "surrogatepass")
    return hmac.new(secret.encode(), text, hashlib.sha256).hexdigest()[:16]


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


def a_group(value: str | None) -> str:
    """A group's name from a form or an address: 16 hex digits."""
    if value is None or len(value) != 16 or not all(c in "0123456789abcdef" for c in value):
        raise Malformed("g")
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
        raise Refused(BUSY) from exc
    except sqlite3.Error as exc:
        raise Refused(NOT_WRITTEN_TEXT) from exc


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
    return stop(without_path(text, path))


def stop(text: str) -> str:
    """A sentence ends with a full stop."""
    return text if text.endswith((".", "?", "!")) else text + "."


def _undo_reason(reason: str) -> str:
    """An undo's reason for skipping a file, without the path some of them end with."""
    for prefix, words in (
        ("original folder is gone", "its original folder is gone"),
        ("original path is taken", "something else has its original name now"),
        ("could not move back", "it could not be moved back"),
        ("changed since it was moved", "it changed since it was moved (edited by hand?)"),
    ):
        if reason.startswith(prefix):
            return words
    return reason


def _files(count: int) -> str:
    return "1 file" if count == 1 else f"{count} files"


def _numbered(source: Path, destination: Path, folder: str) -> Item | None:
    """A file that got a number because its name was taken where it went."""
    if destination.name == source.name:
        return None
    return Item(destination.name, f"is its new name: {source.name} was taken in {folder}/.")


# --- review ----------------------------------------------------------------------------


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
    def present(self) -> list[FileRow]:
        """The group's files still on disk: a missing one is never counted, nor tried."""
        return [r for r in self.rows if os.path.lexists(r.current_path)]

    @property
    def batch(self) -> list[FileRow]:
        """What one "accept all" moves: the oldest ``BULK_CAP`` still on disk."""
        return self.present[:BULK_CAP]


MAX_LISTED = 500
"""Most files the Review page lists; the rest show once these are handled."""


def review_groups(store: Store, secret: str) -> tuple[list[Group], int]:
    """The files in review by reason and suggestion, the biggest group first, and how
    many in review are not listed (past ``MAX_LISTED``). ``secret`` keys the groups'
    names in addresses (``group_key``)."""
    rows = store.review_files(limit=MAX_LISTED)
    grouped: dict[tuple[ReviewReason | None, str | None], list[FileRow]] = {}
    for row in rows:
        grouped.setdefault(row.review, []).append(row)
    groups = [Group(group_key(secret, r, s), r, s, items) for (r, s), items in grouped.items()]
    groups.sort(key=lambda g: (-len(g.rows), g.rows[0].processed_at))
    total = store.stats().by_status[FileStatus.REVIEW.value]
    return groups, max(0, total - len(rows))


def find_group(store: Store, secret: str, key: str) -> Group | None:
    groups, _ = review_groups(store, secret)
    return next((g for g in groups if hmac.compare_digest(g.key, key)), None)


def fix_one(
    config: Config,
    file_id: int,
    pin: str,
    *,
    category: str | None,
    name: str | None,
    secret: str,
) -> Outcome:
    """One file out of review: to ``category`` (a configured one), renamed to ``name``,
    or both. ``flib fix``, under its lock, on the row the form named; refused when
    the pin no longer matches."""
    if category is not None and config.category(category) is None:
        raise Malformed("category")
    with writing(config) as store:
        row = _row(store, file_id)
        if row.status is FileStatus.SENSITIVE:
            raise Refused(SENSITIVE)
        if row.status is not FileStatus.REVIEW or file_pin(row) != pin:
            raise Refused(STALE)
        anchor = group_key(secret, row.review_reason, row.review_category)
        try:
            report = fix_file(
                config, store, row.current_path, category=category, name=name, file_id=row.id
            )
        except FixError as exc:
            raise Refused(_fix_text(exc, row.current_path)) from exc
    shown = report.destination.name
    if category is not None:
        where = report.destination.parent.name
        text, label = f"is now in {where}.", f"Undo: put it back in {config.review_folder}…"
    else:
        where = config.review_folder
        text, label = "is its new name; it stays in review.", "Undo the rename…"
    items = []
    if name is None and (numbered := _numbered(report.source, report.destination, where)):
        items.append(numbered)
    items += [Item(shown, stop(w)) for w in report.warnings]
    return Outcome(
        text,
        items=tuple(items),
        undo_run=report.run_id,
        undo_label=label,
        name=shown,
        anchor=anchor,
    )


def accept_group(config: Config, secret: str, key: str, confirmed: list[int], pin: str) -> Outcome:
    """ "Accept all": the files the confirmation page listed go to the group's suggested
    category, as **one** run (``fix.fix_files``): one undo reverts them all. Refused
    unless the group's first ``BULK_CAP`` are still exactly those files, as shown."""
    with writing(config) as store:
        group = find_group(store, secret, key)
        if group is None:
            raise Refused(STALE_GROUP)
        target = group.target(config)
        if target is None:
            raise Malformed("group")  # the page never offers it
        batch = group.batch
        if [r.id for r in batch] != confirmed or group_pin(batch) != pin:
            raise Refused(STALE_GROUP)
        report = fix_files(
            config, store, [FixItem(r.current_path, target, None, r.id) for r in batch]
        )
    names = {r.current_path: r.current_name for r in batch}
    items = [Item(names[path], _fix_reason(text, path)) for path, text in report.skipped]
    for fixed in report.fixed:
        if numbered := _numbered(fixed.source, fixed.destination, target):
            items.append(numbered)
        items += [Item(fixed.destination.name, stop(w)) for w in fixed.warnings]
    moved = len(report.fixed)
    label = "Undo: put it back in review…" if moved == 1 else f"Undo: put these {moved} back…"
    if report.broken:
        text = (
            f"{_files(moved)} moved to {target} and recorded; then the index could not be "
            "written, so the rest stopped there. See Activity: the next flib command "
            "records a move left half-done."
        )
        return Outcome(
            text,
            error=True,
            items=tuple(items),
            undo_run=report.run_id if moved else None,
            undo_label="Undo this run…",
        )
    if not moved:
        return Outcome("Nothing was moved.", error=True, items=tuple(items))
    text = f"Moved {_files(moved)} to {target}, as one run."
    if report.skipped:
        left = len(report.skipped)
        text += f" {_files(left)} could not be moved and {'stays' if left == 1 else 'stay'}"
        text += " in review."
    return Outcome(text, items=tuple(items), undo_run=report.run_id, undo_label=label)


def _fix_reason(text: str, path: Path) -> str:
    """A ``fix_files`` skip reason (a ``FixError``'s words) without the path."""
    if text.startswith("could not move"):
        return "It could not be moved."
    if text == NOT_WRITTEN:
        return "Not moved: the index could not be written."
    return stop(without_path(text, path))


# --- undo ------------------------------------------------------------------------------


def blocked(store: Store, op: OperationRow, config: Config) -> str | None:
    """Why undo would skip this move, in the page's words; ``None`` if it can go back.
    The same judgement the undo itself acts on (``undo.skip_reason``)."""
    reason = skip_reason(store, op, config)
    return None if reason is None else _undo_reason(reason)


@dataclass(frozen=True, slots=True)
class RunPlan:
    run_id: int
    ops: list[OperationRow]
    changes: list[IndexChange]
    rows: dict[int, FileRow]
    pin: str
    blocked: dict[int, str]
    """Per operation id, why undo will skip it."""
    settled: list[tuple[str, bool]]
    """For a run that marked documents done or open (``flib fix --done``): each one's
    name, and whether undoing it opens it again."""

    @property
    def restorable(self) -> int:
        return len(self.ops) - len(self.blocked)


def plan_run(store: Store, run_id: int, config: Config) -> RunPlan:
    """What undoing run ``run_id`` reverts; ``Refused`` with ``flib undo``'s words when
    it reverts nothing, and for a run that moved a file now sensitive."""
    try:
        ops, changes = run_undo_plan(store, run_id)
    except NothingToUndoError as exc:
        if not store.operations(run_id=run_id, status=OperationStatus.PLANNED):
            raise Refused(stop(str(exc))) from exc
        ops, changes = [], []
    # An entry a failure left "planned" is finished by recovery, which every action runs
    # first: shown, and pinned, as the move it then is.
    ops = sorted(
        [*ops, *store.operations(run_id=run_id, status=OperationStatus.PLANNED)],
        key=lambda op: op.id,
    )
    settle = [c for c in changes if c.target == "files.settled_at"]
    rows = store.files_by_ids(sorted({op.file_id for op in ops} | {c.row_id for c in settle}))
    if any(row.status is FileStatus.SENSITIVE for row in rows.values()):
        raise Refused(SENSITIVE)
    reasons = {op.id: why for op in ops if (why := blocked(store, op, config))}
    settled = [(rows[c.row_id].current_name, c.old_value is None) for c in settle]
    return RunPlan(run_id, ops, changes, rows, run_pin(ops, changes), reasons, settled)


@dataclass(frozen=True, slots=True)
class FilePlan:
    row: FileRow
    op: OperationRow
    pin: str
    blocked: str | None


def plan_file(store: Store, file_id: int, config: Config) -> FilePlan:
    """The move undoing this one file reverts (its last); ``Refused`` as ``flib undo``."""
    row = _row(store, file_id)
    if row.status is FileStatus.SENSITIVE:
        raise Refused(SENSITIVE)
    try:
        op = file_undo_plan(store, row.current_path, file_id=row.id)
    except NothingToUndoError as exc:
        raise Refused(stop(without_path(str(exc), row.current_path))) from exc
    return FilePlan(row, op, undo_file_pin(row, op), blocked(store, op, config))


def undo_one_run(config: Config, run_id: int, pin: str) -> Outcome:
    """``flib undo --run``, refused unless it still reverts what the page showed."""
    with writing(config) as store:
        plan = plan_run(store, run_id, config)
        if plan.pin != pin:
            raise Refused(STALE_UNDO)
        report = undo_run(store, run_id, config=config)
    if not plan.ops and len(plan.settled) == 1 and report.reverted:
        name, opened = plan.settled[0]
        text = "is open again: it is back on the Due list." if opened else "is marked done again."
        return Outcome(text, name=name)
    return _undone(report, config, f"Run {run_id} undone")


def undo_one_file(config: Config, file_id: int, pin: str) -> Outcome:
    """``flib undo --file``, refused unless the file and its last move are as shown."""
    with writing(config) as store:
        plan = plan_file(store, file_id, config)
        if plan.pin != pin:
            raise Refused(STALE_UNDO)
        report = undo_file(store, plan.row.current_path, config=config, file_id=plan.row.id)
    return _undone(report, config, "Undone")


def _undone(report: UndoReport, config: Config, title: str) -> Outcome:
    from file_librarian.ui.views import folder  # the page's way of showing a folder

    items = [Item(src.name, f"is back in {folder(src, config)}.") for _, src in report.restored]
    items += [Item(dst.name, f"skipped: {stop(_undo_reason(why))}") for dst, why in report.skipped]
    items += [Item(path.name, f"warning: {stop(_undo_reason(w))}") for path, w in report.warnings]
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
            report = due_mod.settle(
                store, row.current_path, done=done, trigger=RunTrigger.MANUAL, file_id=row.id
            )
        except due_mod.DueError as exc:
            raise Refused(stop(without_path(str(exc), row.current_path))) from exc
    if done:
        text, label = "is marked done: it leaves the Due list.", "Undo: open it again…"
    else:
        text, label = "is open again: it is back on the Due list.", "Undo: mark it done again…"
    return Outcome(text, undo_run=report.run_id, undo_label=label, name=row.current_name)

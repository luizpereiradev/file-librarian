"""Correct one organized file by hand: ``flib fix`` (RF-29).

The user names a file in the library and the category and/or name it should
have. The move goes through the same journal as a run: it is recorded as its
own run, ``planned`` before the move and ``done`` after, so ``flib undo``
reverts it like any other. ``fix_files`` does several in one run (the
interface's "accept all", #115), so one undo reverts them together; the
run's counts always come from its journal. The index row follows the file (and with it the
search index), and each change is recorded in ``corrections``, the same way
``flib reconcile`` records moves made by hand. The classifier then sends it
as an example in later requests. A file fixed out of review loses its review
reason; that change is journaled with the run (``index_changes``), so undo
gives the reason back.

The same safety rules as the executor apply: the file must still be the one
indexed (same content), the destination is a configured folder under
``output_root``, and nothing is ever overwritten: a taken name gets the next
free ``-N``. Changing only the capitalization of a name renames the file in
place (see ``fsops.move_no_replace``). A file renamed in its own category
stays in its sub-folder (a layout's, or one the user made); a file moved to
another category goes to that category's top folder, which ``flib reprocess
--apply-layout`` then files into the category's layout.

A fix is all or nothing, even across a crash (#47): the journal entry
carries the status and category the fix gives the file, and stays
``planned`` until the index, the corrections and the run are written in one
transaction. ``recover`` (see the executor) then either finishes the whole
fix, if the file reached its destination, or fails the entry and leaves the
file's row as it was, if it did not.
"""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from file_librarian.config import Config
from file_librarian.db.store import FileRow, OperationRow, Store
from file_librarian.finder_tags import tag_moved, untag_restored
from file_librarian.fsops import DestinationExistsError, ensure_folder, move_no_replace
from file_librarian.hashing import check_content
from file_librarian.models import FileStatus, OperationStatus, RunMode, RunTrigger
from file_librarian.naming import candidate_names, safe_component, split_name
from file_librarian.protection import LOCKED_NOW

_FIXABLE = {FileStatus.ORGANIZED, FileStatus.REVIEW}
_MAX_NAME_BYTES = 255


class FixError(ValueError):
    """The correction cannot be applied; nothing was changed."""


@dataclass(frozen=True, slots=True)
class FixReport:
    run_id: int
    source: Path
    destination: Path
    category: tuple[str | None, str] | None
    """(old, new) when the category changed."""
    name: tuple[str, str] | None
    """(old, new) when the name changed."""
    warnings: list[str] = field(default_factory=list)
    """Done, but something minor went wrong (a Finder tag)."""


def fix_file(
    config: Config,
    store: Store,
    path: Path,
    *,
    category: str | None = None,
    name: str | None = None,
    trigger: RunTrigger = RunTrigger.MANUAL,
) -> FixReport:
    """Move one indexed file to ``category`` and/or rename it to ``name``."""
    planned = plan_fix(config, store, path, category=category, name=name)
    run_id = store.start_run(RunMode.RUN, trigger)
    return _apply(config, store, planned, run_id)


@dataclass(frozen=True, slots=True)
class PlannedFix:
    """A fix checked and ready to apply: the file is the one indexed, the name is free
    of '/' and the category is configured. Nothing has changed yet."""

    row: FileRow
    destination: Path
    status: FileStatus
    category: str | None


def plan_fix(
    config: Config,
    store: Store,
    path: Path,
    *,
    category: str | None = None,
    name: str | None = None,
) -> PlannedFix:
    """Check a fix without doing it; raises ``FixError`` with what is wrong."""
    if category is None and name is None:
        raise FixError("say what to change: a category, a name, or both")
    row = store.file_by_current_path(path.expanduser().absolute())
    if row is None:
        raise FixError(f"{path} is not a file flib organized (see `flib search`)")
    if row.status not in _FIXABLE:
        raise FixError(f"{path} is {row.status.value}; only organized or review files can be fixed")
    source = row.current_path
    same = check_content(source, row.sha256)
    if same is None:
        raise FixError(f"{path} is {LOCKED_NOW}")
    if not same:
        raise FixError(f"{path} changed or moved since it was indexed; run `flib reconcile` first")

    folder, new_category = _folder(config, row, category)
    new_name = _name(row, name) if name is not None else row.current_name
    destination = config.output_root / folder / new_name
    if row.status is FileStatus.ORGANIZED and new_category == row.category:
        # Same category: it stays in its sub-folder (a layout's, or one the user made).
        destination = source.parent / new_name
    if destination == source:
        raise FixError(f"{path} is already there with that name")
    status = FileStatus.REVIEW if new_category is None else FileStatus.ORGANIZED
    return PlannedFix(row, destination, status, new_category)


@dataclass(slots=True)
class BatchReport:
    """``fix_files``: one run, what it fixed and what it left as it was."""

    run_id: int
    fixed: list[FixReport] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    """A file left as it was, and why (``FixError``'s words)."""


def fix_files(
    config: Config,
    store: Store,
    items: list[tuple[Path, str | None, str | None]],
    *,
    trigger: RunTrigger = RunTrigger.MANUAL,
) -> BatchReport:
    """Several fixes, ``(path, category, name)`` each, journaled as **one** run: a
    single ``flib undo --run`` reverts them all. Each is checked and done as
    ``fix_file`` does it; one that cannot be done is skipped and listed, and
    the others go on."""
    run_id = store.start_run(RunMode.RUN, trigger)
    report = BatchReport(run_id)
    unplanned = 0  # skipped before a journal entry was made
    for path, category, name in items:
        try:
            planned = plan_fix(config, store, path, category=category, name=name)
        except FixError as exc:
            report.skipped.append((path, str(exc)))
            unplanned += 1
            continue
        try:
            report.fixed.append(_apply(config, store, planned, run_id, finish=False))
        except FixError as exc:
            report.skipped.append((path, str(exc)))
    with store.transaction():
        _finish(store, run_id, extra_seen=unplanned)
    return report


def _apply(
    config: Config, store: Store, planned: PlannedFix, run_id: int, *, finish: bool = True
) -> FixReport:
    """Journal, move and record one checked fix in run ``run_id``."""
    row = planned.row
    source = row.current_path
    st = os.lstat(source)
    with store.transaction():
        op_id = store.add_operation(
            run_id,
            row.id,
            source,
            planned.destination,
            identity=(st.st_dev, st.st_ino),
            fix=(planned.status, planned.category),
        )
    try:
        ensure_folder(planned.destination.parent, inside=config.output_root)
        destination = _move(source, planned.destination, store, op_id)
    except (OSError, ValueError) as exc:
        with store.transaction():
            store.set_operation(op_id, OperationStatus.FAILED)
            if finish:
                _finish(store, run_id)
        raise FixError(f"could not move {source}: {exc}") from exc

    category_change, name_change, warnings = _complete(
        config, store, row, run_id, op_id, destination, planned.status, planned.category,
        finish=finish,
    )  # fmt: skip
    return FixReport(run_id, source, destination, category_change, name_change, warnings)


def _finish(store: Store, run_id: int, *, extra_seen: int = 0) -> None:
    """The run's counts, from its journal: one fix, or a batch of them."""
    ops = store.operations(run_id=run_id)
    done = [op for op in ops if op.status is not OperationStatus.FAILED]
    store.finish_run(
        run_id,
        seen=len(ops) + extra_seen,
        moved=sum(1 for op in done if op.fix is not None and op.fix[0] is FileStatus.ORGANIZED),
        review=sum(1 for op in done if op.fix is not None and op.fix[0] is FileStatus.REVIEW),
        errors=len(ops) - len(done),
        cost_usd=0.0,  # nothing is sent to the provider
    )


def complete_interrupted(
    config: Config | None, store: Store, op: OperationRow, *, moved: bool
) -> None:
    """Resolve a fix a crash left ``planned`` (#47): all of it, or none of it.

    ``moved``: the file is at the journaled destination. Then the rest of the
    fix is done now, exactly as ``fix_file`` would have (index, corrections,
    Finder tag; the tag only given ``config``). Otherwise the fix never
    happened: the journal entry fails and the file's row is left as it was,
    since the file was never touched.
    """
    if op.fix is None:
        raise ValueError(f"operation {op.id} is not a fix")
    if not moved:
        with store.transaction():
            store.set_operation(op.id, OperationStatus.FAILED)
            _finish(store, op.run_id)
        return
    row = store.file_by_id(op.file_id)
    status, category = op.fix
    _complete(config, store, row, op.run_id, op.id, op.dst_path, status, category)


def _complete(
    config: Config | None,
    store: Store,
    row: FileRow,
    run_id: int,
    op_id: int,
    destination: Path,
    status: FileStatus,
    category: str | None,
    *,
    finish: bool = True,
) -> tuple[tuple[str | None, str] | None, tuple[str, str] | None, list[str]]:
    """Everything after the move. ``row`` is the file as indexed before the fix.

    The Finder tags are swapped first and the index written last, in one
    transaction with the journal entry becoming ``done``: until then the entry
    stays ``planned``, so a crash anywhere here is finished by recovery. Doing
    the tag swap again is harmless (a tag already there is not added twice).
    """
    category_change = None
    if category is not None and category != row.category:
        category_change = (row.category, category)
    name_change = None
    if destination.name != row.current_name:
        name_change = (row.current_name, destination.name)
    warnings: list[str] = []
    if category_change is not None and config is not None:
        warnings = swap_tag(config, store, row.id, op_id, destination, *category_change)
    with store.transaction():
        store.set_operation(op_id, OperationStatus.DONE, destination)
        store.relocate_file(row.id, destination, status, category)
        if status is not FileStatus.REVIEW:
            # Out of review: the reason goes, journaled so undo gives it back.
            store.set_review(run_id, row.id, (None, None), old=row.review)
        if category_change is not None:
            store.add_correction(row.id, "category", *category_change)
        if name_change is not None:
            store.add_correction(row.id, "name", *name_change)
        if finish:
            _finish(store, run_id)
    return category_change, name_change, warnings


def swap_tag(
    config: Config, store: Store, file_id: int, op_id: int, path: Path, old: str | None, new: str
) -> list[str]:
    """The tag flib set for the old category goes; the new category's comes (RF-36).

    Returns warnings: a tag is a convenience and never fails the fix.
    """
    warnings = []
    old_tag = store.last_finder_tag(file_id)
    # Finder ignores case in tags, so a category renamed only in case still matches.
    same = old_tag is not None and old is not None and old_tag.casefold() == old.casefold()
    if same and old_tag is not None and (warning := untag_restored(path, old_tag)):
        warnings.append(warning)
    if config.finder_tags.enabled:
        added, warning = tag_moved(path, new)
        if added is not None:
            with store.transaction():
                store.set_operation_tag(op_id, added)
        if warning:
            warnings.append(warning)
    return warnings


def _folder(config: Config, row: FileRow, category: str | None) -> tuple[str, str | None]:
    """The folder the file goes to, and its category (``None``: stays in review)."""
    if category is None:
        if row.status is FileStatus.ORGANIZED and row.category is not None:
            return row.category, row.category
        return config.review_folder, None
    match = config.category(category) or next(
        (c for c in config.categories if c.name.casefold() == category.casefold()), None
    )
    if match is None:
        known = ", ".join(c.name for c in config.categories)
        raise FixError(f"unknown category {category!r} (configured: {known})")
    return match.name, match.name


def _name(row: FileRow, name: str) -> str:
    """The user's new name as a single file name; the old extension is kept if left out."""
    wanted = unicodedata.normalize("NFC", name.strip())
    if not wanted or safe_component(wanted) != wanted:
        raise FixError(f"{name!r} is not a plain file name (no '/', ':' or leading '.')")
    _, extension = split_name(row.current_name)
    if not row.is_unit and extension and not split_name(wanted)[1]:
        wanted += extension
    if len(wanted.encode("utf-8")) > _MAX_NAME_BYTES:
        raise FixError("the new name is longer than macOS allows (255 bytes)")
    return wanted


def _move(src: Path, dst: Path, store: Store, op_id: int) -> Path:
    for candidate_name in candidate_names(dst.name):
        candidate = dst.with_name(candidate_name)
        # Journal the exact name before trying it, so recovery looks in the right place.
        store.set_operation(op_id, OperationStatus.PLANNED, candidate)
        try:
            move_no_replace(src, candidate)
            return candidate
        except DestinationExistsError:
            continue
    raise DestinationExistsError(f"no free name for {dst}")

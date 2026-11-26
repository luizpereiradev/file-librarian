"""Reverting moves (RF-17).

``undo_run`` walks a run's ``done`` operations newest first; ``undo_file``
reverts the last operation on one file. Before moving anything back, each
item must still be the same content that was moved (same SHA-256, or the same
manifest digest for a folder unit), and its original path must be free.
Anything else is skipped and listed, never forced. An item that has to go
back to another volume is copied, verified and only then removed from the
library (``fsops.copy_move``), each phase journaled on the entry, which stays
``done`` until the copy is complete; ``executor.recover`` finishes or rolls
back an undo a crash cut off. Restored files are marked
``skipped`` in the index, so the next run leaves them where you put them (a
duplicate too: its row lists it in ``FileState.kept``), and
lose the Finder tag the move added (if any), keeping every other tag.
Given the configuration, a file restored into the library (undoing ``flib
fix`` or ``flib reprocess``) gets back the status and category of the folder
it returns to, and the Finder tag flib had given it there. An undone entry
records when (``operations.undone_at``). A file that leaves review loses its
review reason; one that returns to review gets back the reason the run
cleared, from the run's ``index_changes``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from file_librarian.config import Config
from file_librarian.db.store import IndexChange, OperationRow, Store
from file_librarian.finder_tags import tag_moved, untag_restored
from file_librarian.fsops import (
    CrossVolumeError,
    DestinationExistsError,
    Journal,
    copy_move,
    ensure_folder,
    move_no_replace,
    new_copy_key,
    volume_of,
)
from file_librarian.hashing import check_content, unit_manifest
from file_librarian.models import CopyPhase, FileStatus, OperationStatus
from file_librarian.protection import LOCKED_NOW
from file_librarian.reconcile import place


class NothingToUndoError(LookupError):
    pass


@dataclass(slots=True)
class UndoReport:
    restored: list[tuple[Path, Path]] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    warnings: list[tuple[Path, str]] = field(default_factory=list)
    """Restored, but something minor went wrong (a Finder tag left behind)."""
    reverted: int = 0
    """Index changes put back (a category renamed by ``flib reprocess``)."""


def undo_run(
    store: Store, run_id: int | None = None, *, config: Config | None = None
) -> UndoReport:
    explicit = run_id is not None
    hint = "" if explicit else "; to go further back, name a run with --run ID"
    if run_id is None:
        run_id = store.last_run_with_operations()
        # A `flib reprocess` that changed nothing (every move skipped or failed)
        # was what the user asked for last: undo must not reach past it and
        # unwind an unrelated earlier run in its place.
        reprocess = store.newest_reprocess_after(run_id)
        if reprocess is not None:
            raise NothingToUndoError(
                f"the newest reprocess (run {reprocess}) changed nothing, so there is "
                f"nothing of it to undo{hint}"
            )
        if run_id is None:
            raise NothingToUndoError("no run has moved anything yet")
    ops, changes = run_undo_plan(store, run_id, hint=hint)
    report = UndoReport()
    for op in reversed(ops):
        _undo_one(store, op, report, config)
    for change in reversed(changes):
        _revert(store, change, report)
    return report


def _revert(store: Store, change: IndexChange, report: UndoReport) -> None:
    """Undo one index change (a renamed category), and for a Finder tag, the file's tag."""
    with store.transaction():
        reverted = store.revert_index_change(change)
    if not reverted:
        return  # changed again since (a later fix, say): that change stands
    report.reverted += 1
    if change.target != "operations.finder_tag":
        return
    op = store.operation_by_id(change.row_id)
    path = store.file_by_id(op.file_id).current_path
    if change.new_value is not None and (warning := untag_restored(path, change.new_value)):
        report.warnings.append((path, warning))
    if change.old_value is not None:
        _, warning = tag_moved(path, change.old_value)
        if warning:
            report.warnings.append((path, warning))


def run_undo_plan(
    store: Store, run_id: int, *, hint: str = ""
) -> tuple[list[OperationRow], list[IndexChange]]:
    """What ``undo_run`` would revert of run ``run_id``: its moves still in effect and
    its index changes; raises ``NothingToUndoError`` (the words ``flib undo`` uses)
    when there is nothing. The interface shows this before it asks (#115)."""
    ops = store.operations(run_id=run_id, status=OperationStatus.DONE)
    changes = store.index_changes(run_id)
    if not ops and not changes:
        raise NothingToUndoError(f"run {run_id} has nothing left to undo{hint}")
    return ops, changes


def file_undo_plan(store: Store, path: Path, *, file_id: int | None = None) -> OperationRow:
    """The move ``undo_file`` would revert for ``path`` (or the row ``file_id``, as the
    interface names it: several rows can share a path); raises ``NothingToUndoError``."""
    if file_id is None:
        row = store.file_by_current_path(path.expanduser().absolute())
    else:
        try:
            row = store.file_by_id(file_id)
        except LookupError:
            row = None
    if row is None:
        raise NothingToUndoError(f"{path} is not a file flib moved")
    op = store.last_done_operation(row.id)
    if op is None:
        raise NothingToUndoError(f"{path} has no move to undo")
    return op


def undo_file(
    store: Store, path: Path, *, config: Config | None = None, file_id: int | None = None
) -> UndoReport:
    op = file_undo_plan(store, path, file_id=file_id)
    row = store.file_by_id(op.file_id)
    report = UndoReport()
    _undo_one(store, op, report, config)
    if report.restored:
        # What the same run changed in the index about this file goes back with it
        # (its renamed corrections, its new description), and nothing else of the run.
        for change in reversed(store.index_changes(op.run_id)):
            if store.index_change_file(change) == row.id:
                _revert(store, change, report)
    return report


def _undo_one(store: Store, op: OperationRow, report: UndoReport, config: Config | None) -> None:
    src, dst = op.src_path, op.dst_path
    row = store.file_by_id(op.file_id)
    if not os.path.lexists(dst):
        report.skipped.append((dst, gone_reason(store, op)))
        return
    same = check_content(dst, row.sha256)
    if same is None:
        report.skipped.append((dst, LOCKED_NOW))
        return
    if not same:
        report.skipped.append((dst, "changed since it was moved"))
        return
    if not src.parent.is_dir():
        # A folder of the library (an emptied category folder deleted since)
        # is made again; any other missing folder is not flib's to create.
        root = config.output_root if config is not None else None
        try:
            if root is None or not src.parent.is_relative_to(root):
                raise FileNotFoundError(src.parent)
            ensure_folder(src.parent, inside=root)
        except (OSError, ValueError):
            report.skipped.append((dst, f"original folder is gone: {src.parent}"))
            return
    try:
        try:
            move_no_replace(dst, src)
        except CrossVolumeError:
            key = new_copy_key(op.id)
            _, kept = copy_move(
                dst, [src], key=key, expected=row.sha256, journal=_journal(store, op, key)
            )
            if kept:
                report.warnings.append((src, kept))
    except DestinationExistsError:
        store.clear_copy_phase(op.id)
        report.skipped.append((dst, f"original path is taken: {src}"))
        return
    except OSError as exc:
        store.clear_copy_phase(op.id)
        report.skipped.append((dst, f"could not move back: {exc.strerror or exc}"))
        return
    for warning in record_restored(store, op, config):
        report.warnings.append((src, warning))
    report.restored.append((dst, src))


def gone_reason(store: Store, op: OperationRow) -> str:
    """Why the item a move put at ``op.dst_path`` is not there: a later move of flib's
    took it elsewhere, or it was moved or deleted by hand."""
    later = store.last_done_operation(op.file_id)
    if later is not None and later.id != op.id:
        return f"moved again since, by run {later.run_id} (undo that first)"
    return "no longer there (moved or deleted by hand)"


def _journal(store: Store, op: OperationRow, key: str) -> Journal:
    """An undo's copy phases go on the entry it reverts, which keeps its paths.

    The copy goes from ``op.dst_path`` back to ``op.src_path``; their volumes
    are recorded when it starts.
    """

    def journal(phase: CopyPhase, _dst: Path) -> None:
        volumes = None
        if phase is CopyPhase.COPYING:
            volumes = (volume_of(op.dst_path.parent), volume_of(op.src_path.parent))
        store.set_copy_phase(op.id, phase, key, volumes=volumes)

    return journal


def record_restored(store: Store, op: OperationRow, config: Config | None) -> list[str]:
    """Everything after the item is back at ``op.src_path``: index, journal, Finder tags.

    Recovery calls it too, for an undo a crash cut off after the copy was in
    place. Returns warnings (a Finder tag that could not be changed).
    """
    src = op.src_path
    warnings: list[str] = []
    row = store.file_by_id(op.file_id)
    identity = unit_identity(src, row.sha256) if row.is_unit else None
    status, category = op.previous if op.previous is not None else _restored_place(config, src)
    with store.transaction():
        store.set_operation(op.id, OperationStatus.UNDONE)
        store.drop_corrections(op.id)  # an undone fix teaches nothing
        if identity is not None:
            store.set_file_identity(op.file_id, identity)
        if status is FileStatus.SKIPPED:
            store.set_file_location(op.file_id, src, FileStatus.SKIPPED)
        else:
            store.relocate_file(op.file_id, src, status, category)
    if op.finder_tag is not None and (warning := untag_restored(src, op.finder_tag)):
        warnings.append(warning)
    if status is FileStatus.ORGANIZED:
        # Back in a category folder (an undone fix): give back the tag flib set there.
        earlier = store.last_finder_tag(op.file_id)
        if (
            earlier is not None
            and category is not None
            and earlier.casefold() == category.casefold()
        ):
            _, warning = tag_moved(src, earlier)
            if warning:
                warnings.append(warning)
    return warnings


def _restored_place(config: Config | None, src: Path) -> tuple[FileStatus, str | None]:
    """The status and category of an item back at ``src``, from the folder it is in.

    A ``flib reprocess`` move records what the file had before instead
    (``OperationRow.previous``), so undoing a category rename or removal gives
    back the old category even though the configuration no longer has it.
    """
    if config is None:
        return FileStatus.SKIPPED, None
    return place(config, src)


def unit_identity(path: Path, current: str) -> str | None:
    """A folder unit's manifest digest where it now is, if it differs from ``current``.

    Copied to another file system (one that keeps coarser modification
    times, such as HFS+), a unit's manifest differs from the source's. The
    index takes the new one, in the same transaction that records the move,
    so undo and reconcile still recognise it there. A move within one volume
    keeps the manifest: ``None``.
    """
    try:
        digest = unit_manifest(path).digest
    except OSError:
        return None
    return digest if digest != current else None

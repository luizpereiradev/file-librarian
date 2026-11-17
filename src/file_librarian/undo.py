"""Reverting moves (RF-17).

``undo_run`` walks a run's ``done`` operations newest first; ``undo_file``
reverts the last operation on one file. Before moving anything back, each
item must still be the same content that was moved (same SHA-256, or the same
manifest digest for a folder unit), and its original path must be free.
Anything else is skipped and listed, never forced. Restored files are marked
``skipped`` in the index, so the next run leaves them where you put them, and
lose the Finder tag the move added (if any), keeping every other tag.
Given the configuration, a file restored into the library (undoing ``flib
fix``) gets back the status and category of the folder it returns to, and
the Finder tag flib had given it there.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from file_librarian.config import Config
from file_librarian.db.store import OperationRow, Store
from file_librarian.finder_tags import tag_moved, untag_restored
from file_librarian.fsops import DestinationExistsError, move_no_replace
from file_librarian.hashing import same_content
from file_librarian.models import FileStatus, OperationStatus
from file_librarian.reconcile import place


class NothingToUndoError(LookupError):
    pass


@dataclass(slots=True)
class UndoReport:
    restored: list[tuple[Path, Path]] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)
    warnings: list[tuple[Path, str]] = field(default_factory=list)
    """Restored, but something minor went wrong (a Finder tag left behind)."""


def undo_run(
    store: Store, run_id: int | None = None, *, config: Config | None = None
) -> UndoReport:
    explicit = run_id is not None
    run_id = run_id if run_id is not None else store.last_run_with_operations()
    if run_id is None:
        raise NothingToUndoError("no run has moved anything yet")
    ops = store.operations(run_id=run_id, status=OperationStatus.DONE)
    if not ops:
        hint = "" if explicit else "; to go further back, name a run with --run ID"
        # Never step back to an older run on its own: repeating `undo` must not
        # quietly unwind yesterday's work too.
        raise NothingToUndoError(f"run {run_id} has nothing left to undo{hint}")
    report = UndoReport()
    for op in reversed(ops):
        _undo_one(store, op, report, config)
    return report


def undo_file(store: Store, path: Path, *, config: Config | None = None) -> UndoReport:
    row = store.file_by_current_path(path.expanduser().absolute())
    if row is None:
        raise NothingToUndoError(f"{path} is not a file flib moved")
    op = store.last_done_operation(row.id)
    if op is None:
        raise NothingToUndoError(f"{path} has no move to undo")
    report = UndoReport()
    _undo_one(store, op, report, config)
    return report


def _undo_one(store: Store, op: OperationRow, report: UndoReport, config: Config | None) -> None:
    src, dst = op.src_path, op.dst_path
    row = store.file_by_id(op.file_id)
    if not os.path.lexists(dst):
        report.skipped.append((dst, "no longer there (moved or deleted by hand)"))
        return
    if not same_content(dst, row.sha256):
        report.skipped.append((dst, "changed since it was moved"))
        return
    if not src.parent.is_dir():
        report.skipped.append((dst, f"original folder is gone: {src.parent}"))
        return
    try:
        move_no_replace(dst, src)
    except DestinationExistsError:
        report.skipped.append((dst, f"original path is taken: {src}"))
        return
    except OSError as exc:
        report.skipped.append((dst, f"could not move back: {exc.strerror or exc}"))
        return
    status, category = place(config, src) if config else (FileStatus.SKIPPED, None)
    with store.transaction():
        store.set_operation(op.id, OperationStatus.UNDONE)
        if status is FileStatus.SKIPPED:
            store.set_file_location(op.file_id, src, FileStatus.SKIPPED)
        else:
            store.relocate_file(op.file_id, src, status, category)
    report.restored.append((dst, src))
    if op.finder_tag is not None and (warning := untag_restored(src, op.finder_tag)):
        report.warnings.append((src, warning))
    if status is FileStatus.ORGANIZED:
        # Back in a category folder (an undone fix): give back the tag flib set there.
        earlier = store.last_finder_tag(op.file_id)
        if earlier is not None and earlier == category:
            _, warning = tag_moved(src, earlier)
            if warning:
                report.warnings.append((src, warning))

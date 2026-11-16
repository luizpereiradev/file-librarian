"""Reverting moves (RF-17).

``undo_run`` walks a run's ``done`` operations newest first; ``undo_file``
reverts the last operation on one file. Before moving anything back, each
item must still be the same content that was moved (same SHA-256, or the same
manifest digest for a folder unit), and its original path must be free.
Anything else is skipped and listed, never forced. Restored files are marked
``skipped`` in the index, so the next run leaves them where you put them.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from file_librarian.db.store import Store
from file_librarian.fsops import DestinationExistsError, move_no_replace
from file_librarian.hashing import same_content
from file_librarian.models import FileStatus, OperationStatus


class NothingToUndoError(LookupError):
    pass


@dataclass(slots=True)
class UndoReport:
    restored: list[tuple[Path, Path]] = field(default_factory=list)
    skipped: list[tuple[Path, str]] = field(default_factory=list)


def undo_run(store: Store, run_id: int | None = None) -> UndoReport:
    explicit = run_id is not None
    run_id = run_id if run_id is not None else store.last_run_with_operations()
    if run_id is None:
        raise NothingToUndoError("no run has moved anything yet")
    ops = store.operations(run_id=run_id, status=OperationStatus.DONE.value)
    if not ops:
        hint = "" if explicit else "; to go further back, name a run with --run ID"
        # Never step back to an older run on its own: repeating `undo` must not
        # quietly unwind yesterday's work too.
        raise NothingToUndoError(f"run {run_id} has nothing left to undo{hint}")
    report = UndoReport()
    for op in reversed(ops):
        _undo_one(store, op, report)
    return report


def undo_file(store: Store, path: Path) -> UndoReport:
    row = store.file_by_current_path(path.expanduser().absolute())
    if row is None:
        raise NothingToUndoError(f"{path} is not a file flib moved")
    op = store.last_done_operation(int(row["id"]))
    if op is None:
        raise NothingToUndoError(f"{path} has no move to undo")
    report = UndoReport()
    _undo_one(store, op, report)
    return report


def _undo_one(store: Store, op: sqlite3.Row, report: UndoReport) -> None:
    src, dst = Path(op["src_path"]), Path(op["dst_path"])
    row = store.file_by_id(int(op["file_id"]))
    if not os.path.lexists(dst):
        report.skipped.append((dst, "no longer there (moved or deleted by hand)"))
        return
    if not same_content(dst, str(row["sha256"])):
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
    store.set_operation(int(op["id"]), OperationStatus.UNDONE)
    store.set_file_location(int(op["file_id"]), src, FileStatus.SKIPPED)
    report.restored.append((dst, src))

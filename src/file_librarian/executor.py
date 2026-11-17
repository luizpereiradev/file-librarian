"""Stage 5: apply a plan, journaling every move (RF-11, RF-12, RF-18, RF-34).

The caller holds the instance lock. For each planned move:

1. the file's index row is written and the move is journaled as ``planned``;
2. the item must still look exactly as it did at scan time (same mtime and
   size), otherwise it is left alone and retried next run;
3. the destination folder is created, refusing symlinks on the way;
4. ``move_no_replace`` renames atomically and never overwrites; if the name
   was taken in the meantime, the next free ``-N`` name is tried. When the
   destination is on another volume, ``fsops.copy_move`` copies, verifies and
   only then deletes the source (RF-33), journaling each phase on the entry
   (``copy_phase``: copying, copied, placed);
5. the journal entry becomes ``done`` and the index row points at the new place;
6. an organized item gets its category as a Finder tag (RF-36), recorded on
   the journal entry so undo removes exactly that tag. A tag failure is only
   a warning: the move stands.

A crash between 1 and 5 leaves a ``planned`` entry that ``recover`` resolves
at the start of the next run by looking at where the file actually is (for a
copy, by its phase: see ``fsops.resume_copy``). An entry made by ``flib fix``
is resolved as a whole fix (see ``fix``). Nothing is ever deleted, except the
source of a cross-volume move once its verified copy is in place.
"""

from __future__ import annotations

import errno
import logging
import os
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from functools import partial
from itertools import chain
from pathlib import Path

from file_librarian.config import Config
from file_librarian.db.store import FileRow, OperationRow, Store
from file_librarian.finder_tags import tag_moved
from file_librarian.fix import complete_interrupted
from file_librarian.fsops import (
    CrossVolumeError,
    DestinationExistsError,
    Journal,
    UnavailableError,
    copy_move,
    ensure_folder,
    has_exact_name,
    move_no_replace,
    new_copy_key,
    resume_copy,
)
from file_librarian.hashing import same_content, unit_manifest
from file_librarian.models import (
    CopyPhase,
    FileContext,
    FileRecord,
    FileStatus,
    OperationStatus,
    RunMode,
    RunTrigger,
)
from file_librarian.naming import candidate_names
from file_librarian.planner import Plan, PlannedItem
from file_librarian.undo import record_restored, refresh_unit_identity

# A full disk or quota is not the file's fault: the item is retried every run.
_ENVIRONMENTAL = frozenset({errno.ENOSPC, errno.EDQUOT})

log = logging.getLogger(__name__)


@dataclass(slots=True)
class ItemResult:
    source: Path
    status: FileStatus
    destination: Path | None
    note: str
    warning: str | None = None
    """Something that went wrong without failing the item, e.g. a Finder tag."""


@dataclass(slots=True)
class RunReport:
    run_id: int | None
    items: list[ItemResult] = field(default_factory=list)
    deferred: int = 0

    def count(self, status: FileStatus, *, moved: bool | None = None) -> int:
        return sum(
            1
            for i in self.items
            if i.status is status and (moved is None or (i.destination is not None) is moved)
        )

    @property
    def moved(self) -> int:
        return sum(1 for i in self.items if i.destination is not None)

    @property
    def errors(self) -> int:
        return self.count(FileStatus.FAILED)


def execute(
    plan: Plan,
    config: Config,
    store: Store,
    *,
    trigger: RunTrigger,
    provider: str,
    model: str,
    seen: int,
    deferred: int = 0,
) -> RunReport:
    run_id = store.start_run(RunMode.RUN, trigger)
    report = RunReport(run_id, deferred=deferred)
    for item in plan.items:
        report.items.append(_apply(item, config, store, run_id, provider, model))
    store.finish_run(
        run_id,
        seen=seen,
        moved=report.count(FileStatus.ORGANIZED, moved=True),
        review=report.count(FileStatus.REVIEW, moved=True),
        errors=report.errors,
    )
    return report


def file_record(
    item: PlannedItem, status: FileStatus, provider: str, model: str, error: str | None = None
) -> FileRecord:
    ctx = item.context
    # Only files that were classified get a description; only organized ones a category.
    answer = item.classification if status in {FileStatus.ORGANIZED, FileStatus.REVIEW} else None
    return FileRecord(
        sha256=ctx.sha256,
        original_path=ctx.path,
        current_path=ctx.path,
        status=status,
        extension=ctx.extension,
        size_bytes=ctx.size_bytes,
        is_unit=ctx.is_unit,
        source_url=ctx.source_urls[0] if ctx.source_urls else None,
        category=answer.category if answer and status is FileStatus.ORGANIZED else None,
        description=answer.description if answer else None,
        tags=tuple(answer.tags) if answer else (),
        confidence=answer.confidence if answer else None,
        attempts=1 if status is FileStatus.FAILED else 0,
        last_error=error,
        provider=provider if answer else None,
        model=model if answer else None,
        name_choice=item.name_choice if answer else None,
        name_choice_reason=answer.name_choice_reason if answer and item.name_choice else None,
    )


def _apply(
    item: PlannedItem, config: Config, store: Store, run_id: int, provider: str, model: str
) -> ItemResult:
    ctx = item.context
    if item.destination is None:
        error = item.reason if item.status is FileStatus.FAILED else None
        store.upsert_file(file_record(item, item.status, provider, model, error), run_id=run_id)
        return ItemResult(ctx.path, item.status, None, item.reason)

    identity = _unchanged(ctx)
    if identity is None:
        return ItemResult(ctx.path, FileStatus.SKIPPED, None, "changed since the scan; next run")

    destination = item.destination
    with store.transaction():
        file_id = store.upsert_file(file_record(item, item.status, provider, model), run_id=run_id)
        op_id = store.add_operation(run_id, file_id, ctx.path, destination, identity=identity)
    try:
        ensure_folder(destination.parent, inside=config.output_root)
        destination, kept = _move(ctx.path, destination, store, op_id, ctx.sha256)
    except (OSError, ValueError) as exc:
        if isinstance(exc, OSError) and exc.errno in _ENVIRONMENTAL:
            error = f"not enough space at the destination; retried next run: {exc.strerror}"
            return _fail(store, op_id, file_id, ctx.path, error, attempt=False)
        return _fail(store, op_id, file_id, ctx.path, f"move failed: {exc}")
    with store.transaction():
        store.set_operation(op_id, OperationStatus.DONE, destination)
        store.set_file_location(file_id, destination, item.status)
    if ctx.is_unit:
        refresh_unit_identity(store, file_id, destination, ctx.sha256)
    warnings = [w for w in (kept, _tag(item, destination, config, store, op_id)) if w]
    return ItemResult(ctx.path, item.status, destination, item.reason, "; ".join(warnings) or None)


def _tag(item: PlannedItem, path: Path, config: Config, store: Store, op_id: int) -> str | None:
    """Add the category as a Finder tag to an organized item. Returns a warning or ``None``."""
    answer = item.classification
    if not config.finder_tags.enabled or item.status is not FileStatus.ORGANIZED or answer is None:
        return None
    added, warning = tag_moved(path, answer.category)
    if added is not None:
        with store.transaction():
            store.set_operation_tag(op_id, added)
    return warning


def _unchanged(ctx: FileContext) -> tuple[int, int] | None:
    """(st_dev, st_ino) if the item is still the very one scanned and classified."""
    try:
        st = os.lstat(ctx.path)
    except OSError:
        return None
    if (st.st_ino, st.st_mtime_ns) != (ctx.inode, ctx.mtime_ns):
        return None
    if ctx.is_unit:
        # A file deep inside a folder can change without touching the folder's mtime.
        try:
            same = unit_manifest(ctx.path).digest == ctx.sha256
        except OSError:
            return None
    else:
        same = st.st_size == ctx.size_bytes
    return (st.st_dev, st.st_ino) if same else None


def _move(src: Path, dst: Path, store: Store, op_id: int, expected: str) -> tuple[Path, str | None]:
    """Move to ``dst`` or its next free name. Returns where, and a warning if any."""
    candidates: Iterator[Path] = (dst.with_name(name) for name in candidate_names(dst.name))
    for candidate in candidates:
        # Journal the exact name before trying it, so recovery looks in the right place.
        store.set_operation(op_id, OperationStatus.PLANNED, candidate)
        try:
            move_no_replace(src, candidate)
            return candidate, None
        except DestinationExistsError:
            continue
        except CrossVolumeError:
            key = new_copy_key(op_id)
            return copy_move(
                src,
                chain([candidate], candidates),
                key=key,
                expected=expected,
                journal=_journal(store, op_id, key),
            )
    raise DestinationExistsError(f"no free name for {dst}")


def _journal(store: Store, op_id: int, key: str, *, moves_dst: bool = True) -> Journal:
    """Record each cross-volume phase on the entry; a move also records where it aims."""

    def journal(phase: CopyPhase, dst: Path) -> None:
        store.set_copy_phase(op_id, phase, key, dst if moves_dst else None)

    return journal


def _fail(
    store: Store, op_id: int, file_id: int, src: Path, error: str, *, attempt: bool = True
) -> ItemResult:
    with store.transaction():
        store.set_operation(op_id, OperationStatus.FAILED)
        store.mark_file_failed(file_id, error, attempt=attempt)
    return ItemResult(src, FileStatus.FAILED, None, error)


def recover(store: Store, config: Config | None = None) -> int:
    """Resolve journal entries left ``planned`` by a crash. Returns how many.

    An entry made by ``flib fix`` is finished or failed as a whole (see
    ``fix.complete_interrupted``); given ``config``, that includes its Finder tags.
    A move or an undo cut off while copying to another volume is finished or
    rolled back by ``fsops.resume_copy``; one whose folders cannot be reached
    (an unmounted volume) is left as it is for a later run, with a warning.
    An entry that cannot be resolved is logged and left, never fatal: it
    must not stop every later run.
    """
    resolved = 0
    for op in store.interrupted_undos():
        resolved += _resolve(op, partial(_recover_undo, store, op, config))
    for op in store.operations(status=OperationStatus.PLANNED):
        resolved += _resolve(op, partial(_recover_one, store, op, config))
    return resolved


def _resolve(op: OperationRow, action: Callable[[], None]) -> int:
    """Run one entry's recovery; 1 if it was resolved, 0 if it was left for later."""
    try:
        action()
    except UnavailableError as exc:
        log.warning(
            "move of %s left as it is: %s is not available (an unmounted volume?); "
            "it is finished when it is back",
            op.src_path,
            exc.filename,
        )
        return 0
    except Exception as exc:
        log.warning("could not recover the move of %s: %s; left for later", op.src_path, exc)
        return 0
    return 1


def _recover_one(store: Store, op: OperationRow, config: Config | None) -> None:
    row = store.file_by_id(op.file_id)
    if op.copy_phase is not None and op.copy_key is not None:
        _recover_copy(store, op, op.copy_phase, op.copy_key, row)
    else:
        _recover_rename(store, op, row, config)


def _recover_rename(store: Store, op: OperationRow, row: FileRow, config: Config | None) -> None:
    # Done if the journaled destination is the very inode we moved (a rename keeps
    # it, even if the file was edited since), or holds exactly the content we moved.
    # Its exact name must be there: on a case-insensitive volume "A.pdf" opens "a.pdf".
    moved = has_exact_name(op.dst_path) and (
        _same_inode(op.dst_path, op.src_identity) or same_content(op.dst_path, row.sha256)
    )
    if op.fix is not None:
        complete_interrupted(config, store, op, moved=moved)
    elif moved:
        with store.transaction():
            store.set_operation(op.id, OperationStatus.DONE)
            store.set_file_location(op.file_id, op.dst_path, row.status)
    else:
        with store.transaction():
            store.set_operation(op.id, OperationStatus.FAILED)
            store.mark_file_failed(op.file_id, "interrupted before the move finished")


def _recover_copy(store: Store, op: OperationRow, phase: CopyPhase, key: str, row: FileRow) -> None:
    final = resume_copy(
        op.src_path,
        op.dst_path,
        key=key,
        phase=phase,
        expected=row.sha256,
        journal=_journal(store, op.id, key),
    )
    with store.transaction():
        if final is not None:
            store.set_operation(op.id, OperationStatus.DONE, final)
            store.set_file_location(op.file_id, final, row.status)
        else:
            store.set_operation(op.id, OperationStatus.FAILED)
            store.mark_file_failed(op.file_id, "interrupted while copying to another volume")
    if final is not None and row.is_unit:
        refresh_unit_identity(store, op.file_id, final, row.sha256)


def _recover_undo(store: Store, op: OperationRow, config: Config | None) -> None:
    """An undo across volumes cut off mid-copy: restored now, or still where it was moved."""
    if op.copy_phase is None or op.copy_key is None:
        return
    row = store.file_by_id(op.file_id)
    restored = resume_copy(
        op.dst_path,
        op.src_path,
        key=op.copy_key,
        phase=op.copy_phase,
        expected=row.sha256,
        journal=_journal(store, op.id, op.copy_key, moves_dst=False),
    )
    if restored is None:
        store.clear_copy_phase(op.id)  # rolled back: the move stands and can be undone
    else:
        record_restored(store, op, config)


def _same_inode(path: Path, identity: tuple[int, int] | None) -> bool:
    if identity is None:
        return False
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return (st.st_dev, st.st_ino) == identity

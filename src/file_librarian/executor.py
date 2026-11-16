"""Stage 5: apply a plan, journaling every move (RF-11, RF-12, RF-18, RF-34).

The caller holds the instance lock. For each planned move:

1. the file's index row is written and the move is journaled as ``planned``;
2. the item must still look exactly as it did at scan time (same mtime and
   size), otherwise it is left alone and retried next run;
3. the destination folder is created, refusing symlinks on the way;
4. ``move_no_replace`` renames atomically and never overwrites; if the name
   was taken in the meantime, the next free ``-N`` name is tried;
5. the journal entry becomes ``done`` and the index row points at the new place.

A crash between 1 and 5 leaves a ``planned`` entry that ``recover`` resolves
at the start of the next run by looking at where the file actually is.
Nothing is ever deleted.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from file_librarian.config import Config
from file_librarian.db.store import Store
from file_librarian.fsops import (
    CrossVolumeError,
    DestinationExistsError,
    ensure_folder,
    move_no_replace,
)
from file_librarian.hashing import file_sha256, unit_manifest
from file_librarian.models import FileContext, FileRecord, FileStatus, OperationStatus
from file_librarian.naming import with_counter
from file_librarian.planner import MAX_COUNTER, Plan, PlannedItem


@dataclass(slots=True)
class ItemResult:
    source: Path
    status: FileStatus
    destination: Path | None
    note: str


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


def execute(plan: Plan, config: Config, store: Store, *, trigger: str, provider: str, model: str,
            seen: int, deferred: int = 0) -> RunReport:  # fmt: skip
    run_id = store.start_run("run", trigger)
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


def file_record(item: PlannedItem, status: FileStatus, provider: str, model: str,
            error: str | None = None) -> FileRecord:  # fmt: skip
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
        source_url=ctx.source_urls[0] if ctx.source_urls else None,
        category=answer.category if answer and status is FileStatus.ORGANIZED else None,
        description=answer.description if answer else None,
        tags=tuple(answer.tags) if answer else (),
        confidence=answer.confidence if answer else None,
        attempts=1 if status is FileStatus.FAILED else 0,
        last_error=error,
        provider=provider if answer else None,
        model=model if answer else None,
    )


def _apply(item: PlannedItem, config: Config, store: Store, run_id: int, provider: str,
           model: str) -> ItemResult:  # fmt: skip
    ctx = item.context
    if item.destination is None:
        error = item.reason if item.status is FileStatus.FAILED else None
        store.upsert_file(file_record(item, item.status, provider, model, error), run_id=run_id)
        return ItemResult(ctx.path, item.status, None, item.reason)

    if not _unchanged(ctx):
        return ItemResult(ctx.path, FileStatus.SKIPPED, None, "changed since the scan; next run")

    destination = item.destination
    with store.transaction():
        file_id = store.upsert_file(file_record(item, item.status, provider, model), run_id=run_id)
        op_id = store.add_operation(run_id, file_id, ctx.path, destination)
    try:
        ensure_folder(destination.parent, inside=config.output_root)
        destination = _move(ctx.path, destination, store, op_id)
    except CrossVolumeError:
        return _fail(store, op_id, file_id, ctx.path, "destination is on another volume (RF-33)")
    except (OSError, ValueError) as exc:
        return _fail(store, op_id, file_id, ctx.path, f"move failed: {exc}")
    with store.transaction():
        store.set_operation(op_id, OperationStatus.DONE, destination)
        store.set_file_location(file_id, destination, item.status)
    return ItemResult(ctx.path, item.status, destination, item.reason)


def _unchanged(ctx: FileContext) -> bool:
    """The item is still the very one that was scanned and classified."""
    try:
        st = os.lstat(ctx.path)
    except OSError:
        return False
    if (st.st_ino, st.st_mtime_ns) != (ctx.inode, ctx.mtime_ns):
        return False
    if ctx.is_unit:
        # A file deep inside a folder can change without touching the folder's mtime.
        try:
            return unit_manifest(ctx.path).digest == ctx.sha256
        except OSError:
            return False
    return st.st_size == ctx.size_bytes


def _move(src: Path, dst: Path, store: Store, op_id: int) -> Path:
    candidate = dst
    for n in range(2, MAX_COUNTER + 2):
        # Journal the exact name before trying it, so recovery looks in the right place.
        store.set_operation(op_id, OperationStatus.PLANNED, candidate)
        try:
            move_no_replace(src, candidate)
            return candidate
        except DestinationExistsError:
            candidate = dst.with_name(with_counter(dst.name, n))
    raise DestinationExistsError(f"no free name for {dst}")


def _fail(store: Store, op_id: int, file_id: int, src: Path, error: str) -> ItemResult:
    with store.transaction():
        store.set_operation(op_id, OperationStatus.FAILED)
        store.mark_file_failed(file_id, error)
    return ItemResult(src, FileStatus.FAILED, None, error)


def recover(store: Store) -> int:
    """Resolve journal entries left ``planned`` by a crash. Returns how many."""
    pending = store.operations(status=OperationStatus.PLANNED.value)
    for op in pending:
        op_id, file_id = int(op["id"]), int(op["file_id"])
        dst = Path(op["dst_path"])
        row = store.file_by_id(file_id)
        # Done only if the journaled destination holds exactly the content we moved.
        if same_content(dst, str(row["sha256"])):
            with store.transaction():
                store.set_operation(op_id, OperationStatus.DONE)
                store.set_file_location(file_id, dst, FileStatus(row["status"]))
        else:
            with store.transaction():
                store.set_operation(op_id, OperationStatus.FAILED)
                store.mark_file_failed(file_id, "interrupted before the move finished")
    return len(pending)


def same_content(path: Path, sha256: str) -> bool:
    """``path`` exists, is not a symlink, and hashes to ``sha256``."""
    try:
        if not os.path.lexists(path) or path.is_symlink():
            return False
        if path.is_dir():
            return unit_manifest(path).digest == sha256
        return file_sha256(path) == sha256
    except OSError:
        return False

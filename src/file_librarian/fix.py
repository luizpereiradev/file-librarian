"""Correct one organized file by hand: ``flib fix`` (RF-29).

The user names a file in the library and the category and/or name it should
have. The move goes through the same journal as a run: it is recorded as its
own run, ``planned`` before the move and ``done`` after, so ``flib undo``
reverts it like any other. The index row follows the file (and with it the
search index), and each change is recorded in ``corrections``, the same way
``flib reconcile`` records moves made by hand. The classifier then sends it
as an example in later requests.

The same safety rules as the executor apply: the file must still be the one
indexed (same content), the destination is a configured folder under
``output_root``, and nothing is ever overwritten: a taken name gets the next
free ``-N``.
"""

from __future__ import annotations

import os
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

from file_librarian.config import Config
from file_librarian.db.store import FileRow, Store
from file_librarian.finder_tags import tag_moved, untag_restored
from file_librarian.fsops import DestinationExistsError, ensure_folder, move_no_replace
from file_librarian.hashing import same_content
from file_librarian.models import FileStatus, OperationStatus, RunMode, RunTrigger
from file_librarian.naming import candidate_names, safe_component, split_name

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
    if category is None and name is None:
        raise FixError("say what to change: a category, a name, or both")
    row = store.file_by_current_path(path.expanduser().absolute())
    if row is None:
        raise FixError(f"{path} is not a file flib organized (see `flib search`)")
    if row.status not in _FIXABLE:
        raise FixError(f"{path} is {row.status.value}; only organized or review files can be fixed")
    source = row.current_path
    if not same_content(source, row.sha256):
        raise FixError(f"{path} changed or moved since it was indexed; run `flib reconcile` first")

    folder, new_category = _folder(config, row, category)
    new_name = _name(row, name) if name is not None else row.current_name
    destination = config.output_root / folder / new_name
    if destination == source:
        raise FixError(f"{path} is already there with that name")

    st = os.lstat(source)
    run_id = store.start_run(RunMode.RUN, trigger)
    with store.transaction():
        op_id = store.add_operation(
            run_id, row.id, source, destination, identity=(st.st_dev, st.st_ino)
        )
    try:
        ensure_folder(destination.parent, inside=config.output_root)
        destination = _move(source, destination, store, op_id)
    except (OSError, ValueError) as exc:
        with store.transaction():
            store.set_operation(op_id, OperationStatus.FAILED)
            store.finish_run(run_id, seen=1, moved=0, review=0, errors=1)
        raise FixError(f"could not move {path}: {exc}") from exc

    status = FileStatus.REVIEW if new_category is None else FileStatus.ORGANIZED
    category_change = None
    if new_category is not None and new_category != row.category:
        category_change = (row.category, new_category)
    name_change = None
    if destination.name != row.current_name:
        name_change = (row.current_name, destination.name)
    with store.transaction():
        store.set_operation(op_id, OperationStatus.DONE, destination)
        store.relocate_file(row.id, destination, status, new_category)
        if category_change is not None:
            store.add_correction(row.id, "category", *category_change)
        if name_change is not None:
            store.add_correction(row.id, "name", *name_change)
        store.finish_run(
            run_id,
            seen=1,
            moved=int(status is FileStatus.ORGANIZED),
            review=int(status is FileStatus.REVIEW),
            errors=0,
        )
    warnings: list[str] = []
    if category_change is not None:
        warnings = _swap_tag(config, store, row.id, op_id, destination, *category_change)
    return FixReport(run_id, source, destination, category_change, name_change, warnings)


def _swap_tag(
    config: Config, store: Store, file_id: int, op_id: int, path: Path, old: str | None, new: str
) -> list[str]:
    """The tag flib set for the old category goes; the new category's comes (RF-36).

    Returns warnings: a tag is a convenience and never fails the fix.
    """
    warnings = []
    old_tag = store.last_finder_tag(file_id)
    if old_tag is not None and old_tag == old and (warning := untag_restored(path, old_tag)):
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

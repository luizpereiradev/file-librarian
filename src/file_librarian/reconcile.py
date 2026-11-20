"""Bring the index back in line with the disk (RF-20).

People move, rename and delete organized files by hand. ``reconcile`` walks
every row of the index and looks at where the file really is:

- still at its recorded path with the same content: nothing to do;
- gone from there: look for the same content (sha256, or the manifest for a
  folder unit) under ``output_root`` and the input folders. Only files of the
  same size are hashed, so this stays cheap;
  - found inside a category folder: the row follows it. If the category
    changed, that is a correction by the user and is recorded (RF-29);
  - found in the review folder: status ``review``; one that had no review
    reason gets ``moved_by_hand``;
  - found back in an input folder: status ``skipped``, so it is left alone;
- found nowhere: status ``missing``; if it reappears later it is picked up
  again by the same search.

Duplicates (RF-21) are never searched for: a copy is indistinguishable from
its original by content. A duplicate gone from its recorded place (moved or
deleted by hand) becomes ``missing``; if it was put back into an input
folder, the next run moves it to the duplicates folder again while its
original is in the library. For the same reason the search for any other
file skips the duplicates folder and every duplicate's place: a filed file
deleted by hand becomes ``missing`` even when a copy of it waits in the
duplicates folder. That copy is never promoted silently; it is reported as
orphaned (``ReconcileReport.orphaned``), and moving it back to an input
folder has it filed like a new file.

Nothing that exists only in iCloud is read, hashed or listed: that would make
macOS download it. A file still at its recorded path but only in iCloud (or a
folder unit with anything inside only in iCloud) cannot be checked, so its row
is left as it is and reported; it is not ``missing``, because it is there. The
search treats cloud-only files and folders as no match and does not walk into
them.

Nothing on disk is touched; only the index changes.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

from file_librarian import hashing
from file_librarian.config import Config, folder_key, is_within
from file_librarian.db.store import FileRow, Store, normalize
from file_librarian.models import FileStatus, ReviewReason

_TRACKED = {
    FileStatus.DUPLICATE,
    FileStatus.ORGANIZED,
    FileStatus.REVIEW,
    FileStatus.SKIPPED,
    FileStatus.MISSING,
    FileStatus.SENSITIVE,
}


@dataclass(slots=True)
class ReconcileReport:
    checked: int = 0
    moved: list[tuple[Path, Path]] = field(default_factory=list)
    recategorized: list[tuple[Path, str | None, str]] = field(default_factory=list)
    missing: list[Path] = field(default_factory=list)
    found_again: list[Path] = field(default_factory=list)
    cloud_only: list[Path] = field(default_factory=list)
    """At their recorded path but only in iCloud: not checked, row unchanged."""
    unchecked: list[Path] = field(default_factory=list)
    """Duplicates that could not be read where recorded (an unmounted volume, a read
    error): row unchanged."""
    orphaned: list[tuple[Path, Path]] = field(default_factory=list)
    """Duplicates whose original is missing: (the duplicate, where the original was)."""


def in_cloud_only(path: Path) -> bool:
    """``path``, or a folder above it, exists only in iCloud. Reads nothing but ``lstat``.

    Folders are checked from the top down, so a cloud-only folder is never listed
    by looking up something inside it.
    """
    for item in [*reversed(path.parents), path]:
        try:
            st = os.lstat(item)
        except OSError:
            return False
        if hashing.is_dataless(st):
            return True
    return False


def _dataless(path: Path) -> bool:
    try:
        return hashing.is_dataless(os.lstat(path))
    except OSError:
        return False


class _Seen(Enum):
    SAME = "same"
    DIFFERENT = "different"
    CLOUD_ONLY = "cloud only"


def _look(path: Path, sha256: str) -> _Seen:
    """Whether ``path`` holds the content ``sha256``. Reads nothing that is only in iCloud.

    A folder unit with anything inside only in iCloud cannot be compared: its
    manifest would be partial.
    """
    if in_cloud_only(path):
        return _Seen.CLOUD_ONLY
    try:
        if path.is_dir() and not path.is_symlink():
            manifest = hashing.unit_manifest(path)
            if manifest.dataless:
                return _Seen.CLOUD_ONLY
            return _Seen.SAME if manifest.digest == sha256 else _Seen.DIFFERENT
    except OSError:
        return _Seen.DIFFERENT
    return _Seen.SAME if hashing.same_content(path, sha256) else _Seen.DIFFERENT


def content_at(path: Path, sha256: str) -> bool | None:
    """Whether ``path`` still holds the content ``sha256`` (a file or a folder unit).

    ``False`` only when the answer is certain: nothing is there although its
    folder can be listed, or what is there has other content. ``None`` when it
    cannot be told: only in iCloud (never downloaded to check), a folder on
    the way that is gone or cannot be read (an unmounted volume), or a read
    error.
    """
    try:
        os.lstat(path)
    except FileNotFoundError:
        # Absent for sure only if its folder is there and readable (not an unmounted volume).
        parent = path.parent
        if parent.is_dir() and os.access(parent, os.R_OK | os.X_OK):
            return False
        return None
    except OSError:
        return None
    if in_cloud_only(path):
        return None
    try:
        if path.is_symlink():
            return False
        if path.is_dir():
            manifest = hashing.unit_manifest(path)
            return None if manifest.dataless else manifest.digest == sha256
        return hashing.file_sha256(path) == sha256
    except OSError:
        return None


class _Finder:
    """Find an item by content under a set of roots, hashing only same-size candidates.

    The duplicates folder and ``skip`` (the places of indexed duplicates and the
    library folders holding them) are never candidates nor walked into.
    """

    def __init__(self, config: Config, skip: set[str] | None = None) -> None:
        self._config = config
        self._skip = skip or set()
        self._index: tuple[dict[int, list[Path]], list[Path]] | None = None

    def _build(self) -> tuple[dict[int, list[Path]], list[Path]]:
        by_size: dict[int, list[Path]] = defaultdict(list)
        dirs: list[Path] = []
        output_root = self._config.output_root
        duplicates = folder_key(self._config.duplicates_folder)
        for root in [output_root, *self._config.inputs]:
            if not root.is_dir() or in_cloud_only(root):
                continue
            deep = is_within(root, output_root)
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                here = Path(dirpath)
                # Cloud-only folders are neither candidates nor walked into, and
                # neither is the duplicates folder.
                dirnames[:] = [
                    name
                    for name in dirnames
                    if not _dataless(here / name)
                    and not (deep and here == output_root and folder_key(name) == duplicates)
                    and normalize(str(here / name)) not in self._skip
                ]
                dirs.extend(here / name for name in dirnames)
                if not deep:
                    dirnames.clear()  # inputs: top level only, like the scanner
                for name in filenames:
                    path = here / name
                    if normalize(str(path)) in self._skip:
                        continue
                    try:
                        st = path.lstat()
                    except OSError:
                        continue
                    if not path.is_symlink() and not hashing.is_dataless(st):
                        by_size[st.st_size].append(path)
        return by_size, dirs

    def find(self, row: FileRow) -> Path | None:
        if self._index is None:
            self._index = self._build()
        by_size, dirs = self._index
        if row.is_unit:
            return next((d for d in dirs if _look(d, row.sha256) is _Seen.SAME), None)
        candidates = by_size.get(row.size_bytes, []) if row.size_bytes is not None else []
        return next((p for p in candidates if _look(p, row.sha256) is _Seen.SAME), None)


def place(config: Config, path: Path) -> tuple[FileStatus, str | None]:
    """What a location means: (status, category)."""
    root = config.output_root
    if is_within(path, root) and path.parent != root:
        folder = path.relative_to(root).parts[0]
        if folder == config.review_folder:
            return FileStatus.REVIEW, None
        if config.category(folder) is not None:
            return FileStatus.ORGANIZED, folder
    return FileStatus.SKIPPED, None


def reconcile(config: Config, store: Store) -> ReconcileReport:
    report = ReconcileReport()
    rows = store.all_files()
    duplicates = [row for row in rows if row.duplicate_of is not None]
    skip = {normalize(str(row.current_path)) for row in duplicates}
    # Folders of the library holding duplicates are skipped whole: a duplicates
    # folder used before ``duplicates_folder`` was renamed, say.
    skip |= {
        normalize(str(row.current_path.parent))
        for row in duplicates
        if row.current_path.parent != config.output_root
        and is_within(row.current_path.parent, config.output_root)
    }
    finder = _Finder(config, skip)
    for row in rows:
        if row.status not in _TRACKED:
            continue
        report.checked += 1
        recorded = row.current_path
        if row.duplicate_of is not None:
            _check_duplicate(store, row, report)
            continue
        if row.status is not FileStatus.MISSING:
            seen = _look(recorded, row.sha256)
            if seen is _Seen.SAME:
                continue
            if seen is _Seen.CLOUD_ONLY:
                # It is there, but checking it would download it: leave the row as it is.
                report.cloud_only.append(recorded)
                continue
        found = finder.find(row)
        if found is None:
            if row.status is not FileStatus.MISSING:
                store.set_file_status(row.id, FileStatus.MISSING)
                report.missing.append(recorded)
            continue
        status, category = place(config, found)
        if row.status is FileStatus.SENSITIVE:
            status, category = FileStatus.SENSITIVE, None  # stays sensitive wherever it is
        with store.transaction():
            store.relocate_file(row.id, found, status, category)
            if status is FileStatus.REVIEW and row.review_reason is None:
                store.mark_review(row.id, ReviewReason.MOVED_BY_HAND)
            if status is FileStatus.ORGANIZED and category != row.category:
                store.add_correction(row.id, "category", row.category, category or "")
                report.recategorized.append((found, row.category, category or ""))
            if found.name != recorded.name:
                store.add_correction(row.id, "name", recorded.name, found.name)
        if row.status is FileStatus.MISSING:
            report.found_again.append(found)
        else:
            report.moved.append((recorded, found))
    for row in duplicates:
        if store.file_by_id(row.id).status is not FileStatus.DUPLICATE:
            continue
        original = store.file_by_id(row.duplicate_of or 0)
        if original.status is FileStatus.MISSING:
            report.orphaned.append((row.current_path, original.current_path))
    return report


def _check_duplicate(store: Store, row: FileRow, report: ReconcileReport) -> None:
    """A duplicate is only looked for where it was recorded; gone from there, it is missing."""
    if row.status is FileStatus.MISSING:
        return
    there = content_at(row.current_path, row.sha256)
    if there is None:
        # Only in iCloud, unreadable or on a volume that is not there: left as it is.
        if in_cloud_only(row.current_path):
            report.cloud_only.append(row.current_path)
        else:
            report.unchecked.append(row.current_path)
    elif not there:
        store.set_file_status(row.id, FileStatus.MISSING)
        report.missing.append(row.current_path)

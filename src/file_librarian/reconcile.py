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
from file_librarian.config import Config, is_within
from file_librarian.db.store import FileRow, Store
from file_librarian.models import FileStatus, ReviewReason

_TRACKED = {
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


class _Finder:
    """Find an item by content under a set of roots, hashing only same-size candidates."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._index: tuple[dict[int, list[Path]], list[Path]] | None = None

    def _build(self) -> tuple[dict[int, list[Path]], list[Path]]:
        by_size: dict[int, list[Path]] = defaultdict(list)
        dirs: list[Path] = []
        for root in [self._config.output_root, *self._config.inputs]:
            if not root.is_dir() or in_cloud_only(root):
                continue
            deep = is_within(root, self._config.output_root)
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                here = Path(dirpath)
                # Cloud-only folders are neither candidates nor walked into.
                dirnames[:] = [name for name in dirnames if not _dataless(here / name)]
                dirs.extend(here / name for name in dirnames)
                if not deep:
                    dirnames.clear()  # inputs: top level only, like the scanner
                for name in filenames:
                    path = here / name
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
    finder = _Finder(config)
    for row in store.all_files():
        if row.status not in _TRACKED:
            continue
        report.checked += 1
        recorded = row.current_path
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
    return report

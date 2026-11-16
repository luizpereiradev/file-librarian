"""Bring the index back in line with the disk (RF-20).

People move, rename and delete organized files by hand. ``reconcile`` walks
every row of the index and looks at where the file really is:

- still at its recorded path with the same content: nothing to do;
- gone from there: look for the same content (sha256, or the manifest for a
  folder unit) under ``output_root`` and the input folders. Only files of the
  same size are hashed, so this stays cheap;
  - found inside a category folder: the row follows it. If the category
    changed, that is a correction by the user and is recorded (RF-29);
  - found in the review folder: status ``review``;
  - found back in an input folder: status ``skipped``, so it is left alone;
- found nowhere: status ``missing``; if it reappears later it is picked up
  again by the same search.

Nothing on disk is touched; only the index changes.
"""

from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from file_librarian.config import Config, is_within
from file_librarian.db.store import FileRow, Store
from file_librarian.hashing import same_content
from file_librarian.models import FileStatus

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


class _Finder:
    """Find an item by content under a set of roots, hashing only same-size candidates."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._index: tuple[dict[int, list[Path]], list[Path]] | None = None

    def _build(self) -> tuple[dict[int, list[Path]], list[Path]]:
        by_size: dict[int, list[Path]] = defaultdict(list)
        dirs: list[Path] = []
        for root in [self._config.output_root, *self._config.inputs]:
            if not root.is_dir():
                continue
            deep = is_within(root, self._config.output_root)
            for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
                here = Path(dirpath)
                dirs.extend(here / name for name in dirnames)
                for name in filenames:
                    path = here / name
                    try:
                        st = path.lstat()
                    except OSError:
                        continue
                    if not path.is_symlink():
                        by_size[st.st_size].append(path)
                if not deep:
                    dirnames.clear()  # inputs: top level only, like the scanner
        return by_size, dirs

    def find(self, row: FileRow) -> Path | None:
        if self._index is None:
            self._index = self._build()
        by_size, dirs = self._index
        if row.is_unit:
            return next((d for d in dirs if same_content(d, row.sha256)), None)
        candidates = by_size.get(row.size_bytes, []) if row.size_bytes is not None else []
        return next((p for p in candidates if same_content(p, row.sha256)), None)


def _place(config: Config, path: Path) -> tuple[FileStatus, str | None]:
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
        if row.status is not FileStatus.MISSING and same_content(recorded, row.sha256):
            continue
        found = finder.find(row)
        if found is None:
            if row.status is not FileStatus.MISSING:
                store.set_file_status(row.id, FileStatus.MISSING)
                report.missing.append(recorded)
            continue
        status, category = _place(config, found)
        if row.status is FileStatus.SENSITIVE:
            status, category = FileStatus.SENSITIVE, None  # stays sensitive wherever it is
        with store.transaction():
            store.relocate_file(row.id, found, status, category)
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

"""Stage 1: find what still needs processing.

- Lists the top level of each input folder; never descends into sub-folders.
- Skips hidden and system files, symlinks, special files, the folders flib
  manages, configured ``ignore`` globs and downloads in progress (RF-02, RF-03).
- Skips files that exist only in iCloud, and folder units with any such file
  inside, judging from ``lstat`` alone so nothing is downloaded (RF-24).
  They are picked up by a later run, once downloaded.
- Treats each sub-folder or bundle (``.app``, ``.pkg``...) as one unit, or
  skips it, per ``folders`` (RF-23).
- Waits until an item has not changed for ``stable_seconds`` and its size and
  mtime are the same before and after hashing (RF-02).
- Skips anything already in the database by content hash, except files that
  failed fewer than ``max_attempts`` times (RF-01, RF-30).
- Returns at most ``max_files_per_run`` candidates, oldest first.
- Keeps why an item was unreadable (the step and the errno) and reads it once
  more after a short pause when the error is plausibly transient.

The scanner only reads. It never opens a file for writing and never follows
a symlink.
"""

from __future__ import annotations

import errno
import os
import re
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TypeVar

from file_librarian import hashing
from file_librarian.config import Config, is_within
from file_librarian.globs import matches_any
from file_librarian.hashing import file_sha256, unit_manifest
from file_librarian.lock import lock_path
from file_librarian.models import FileState, FileStatus

# Browsers write in-progress downloads under these names (Safari's are folders).
IN_PROGRESS_PATTERNS = ("*.crdownload", "*.part", "*.partial", "*.download", "*.opdownload")
# A final-name placeholder next to one of these is still downloading (Firefox, aria2...).
PARTIAL_SIBLINGS = (".part", ".crdownload", ".aria2", ".download", ".opdownload")


class ScanError(RuntimeError):
    """An input folder cannot be read at all (for example, macOS privacy settings)."""


class SkipReason(StrEnum):
    HIDDEN = "hidden"
    SYMLINK = "symlink"
    SPECIAL = "special file"
    MANAGED = "managed by flib"
    IN_PROGRESS = "download in progress"
    IGNORED = "matches ignore"
    FOLDER_IGNORED = "folders: ignore"
    UNSTABLE = "still changing"
    KNOWN = "already processed"
    UNREADABLE = "unreadable"
    EMPTY = "empty file"
    DUPLICATE = "same content as another item in this run"
    CLOUD_ONLY = "in iCloud only, not downloaded"


@dataclass(frozen=True, slots=True)
class Candidate:
    path: Path
    sha256: str
    size_bytes: int
    mtime: float
    is_unit: bool
    mtime_ns: int = 0
    """The item's own mtime at scan time; the executor refuses to move it if this changed."""
    created: float = 0.0
    """Birth time on macOS, used as the download date."""
    inode: int = 0
    """Checked again before moving, so a replaced file is never moved under the old hash."""


# Read errors worth one more try: a lock, an interrupted call, a busy or stale file.
TRANSIENT_ERRNOS = frozenset(
    {errno.EDEADLK, errno.EAGAIN, errno.EINTR, errno.ETIMEDOUT, errno.EBUSY, errno.ESTALE}
)
RETRY_PAUSE = 0.5
"""Seconds to wait before reading a file again after a transient error."""


@dataclass(frozen=True, slots=True)
class SkipDetail:
    """Why an item could not be read: the step and the error, never a path or content."""

    step: str
    """``stat`` or ``read``."""
    errno: int | None
    message: str

    @classmethod
    def from_error(cls, step: str, exc: OSError) -> SkipDetail:
        name = errno.errorcode.get(exc.errno, "") if exc.errno is not None else ""
        message = exc.strerror or type(exc).__name__
        return cls(step, exc.errno, f"{name} {message}" if name else message)

    @property
    def denied(self) -> bool:
        """macOS (or the file's permissions) refused access."""
        return self.errno in (errno.EPERM, errno.EACCES)

    def __str__(self) -> str:
        return f"{self.message}, on {self.step}"


@dataclass(slots=True)
class ScanResult:
    candidates: list[Candidate] = field(default_factory=list)
    skipped: list[tuple[Path, SkipReason]] = field(default_factory=list)
    details: dict[Path, SkipDetail] = field(default_factory=dict)
    """For unreadable items in ``skipped``: what failed."""
    deferred: int = 0
    """Ready candidates left for the next run because of ``max_files_per_run``."""


Lookup = Callable[[str], FileState | None]
T = TypeVar("T")


# "report (1).pdf" (browsers), "report copy.pdf" / "report copy 2.pdf" (Finder).
# "-2" is left out on purpose: it is indistinguishable from a version number.
_COPY_SUFFIX = re.compile(r"(?: \(\d+\)| copy(?: \d+)?)$", re.IGNORECASE)


def looks_like_a_copy(path: Path) -> bool:
    stem = path.name.removesuffix(path.suffix) if path.suffix else path.name
    return bool(_COPY_SUFFIX.search(stem))


def _keep_originals(ready: list[Candidate], result: ScanResult) -> list[Candidate]:
    """One candidate per content: no copy suffix first, then created first, then shortest."""
    by_hash: dict[str, list[Candidate]] = {}
    for candidate in ready:
        by_hash.setdefault(candidate.sha256, []).append(candidate)
    kept = []
    for copies in by_hash.values():
        copies.sort(
            key=lambda c: (looks_like_a_copy(c.path), c.created, len(c.path.name), c.path.name)
        )
        kept.append(copies[0])
        result.skipped.extend((c.path, SkipReason.DUPLICATE) for c in copies[1:])
    return kept


def _protected_ids(config: Config) -> set[tuple[int, int]]:
    """Device and inode of everything flib owns, however its path is spelled."""
    db = config.database
    paths = [
        config.output_root,
        db,
        db.parent,
        db.with_name(db.name + "-wal"),
        db.with_name(db.name + "-shm"),
        db.with_name(db.name + "-journal"),
        lock_path(db),
    ]
    ids = set()
    for path in paths:
        try:
            st = path.stat()
        except OSError:
            continue
        ids.add((st.st_dev, st.st_ino))
    return ids


def _birth(st: os.stat_result) -> float:
    return float(getattr(st, "st_birthtime", st.st_mtime))


def _read_twice(read: Callable[[Path], T], path: Path, sleep: Callable[[float], None]) -> T:
    """``read(path)``, once more after a pause if the first error is plausibly transient."""
    try:
        return read(path)
    except OSError as exc:
        if exc.errno not in TRANSIENT_ERRNOS:
            raise
    sleep(RETRY_PAUSE)
    return read(path)


def scan(
    config: Config,
    lookup: Lookup,
    *,
    now: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> ScanResult:
    now = time.time() if now is None else now
    limits = config.limits
    result = ScanResult()
    ready: list[Candidate] = []
    protected = _protected_ids(config)

    for folder in config.inputs:
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except PermissionError as exc:
            raise ScanError(
                f"cannot read {folder}: macOS denied access. Give the app running flib "
                "access in System Settings > Privacy & Security > Files and Folders "
                "(or Full Disk Access) and try again."
            ) from exc
        except FileNotFoundError as exc:
            raise ScanError(f"input folder does not exist: {folder}") from exc

        names = {e.name.casefold() for e in entries}
        for entry in entries:
            path = Path(entry.path)

            def skip(
                reason: SkipReason, detail: SkipDetail | None = None, path: Path = path
            ) -> None:
                result.skipped.append((path, reason))
                if detail is not None:
                    result.details[path] = detail

            if entry.name.startswith("."):
                skip(SkipReason.HIDDEN)
                continue
            if entry.is_symlink():
                skip(SkipReason.SYMLINK)
                continue
            try:
                ident = entry.stat(follow_symlinks=False)
            except OSError as exc:
                skip(SkipReason.UNREADABLE, SkipDetail.from_error("stat", exc))
                continue
            if ident.st_flags & stat.UF_HIDDEN:
                skip(SkipReason.HIDDEN)
                continue
            if (ident.st_dev, ident.st_ino) in protected or is_within(path, config.output_root):
                skip(SkipReason.MANAGED)
                continue
            if matches_any(entry.name, IN_PROGRESS_PATTERNS) or any(
                f"{entry.name}{suffix}".casefold() in names for suffix in PARTIAL_SIBLINGS
            ):
                skip(SkipReason.IN_PROGRESS)
                continue
            if matches_any(entry.name, config.ignore):
                skip(SkipReason.IGNORED)
                continue
            # Before anything that opens, lists or hashes it: that would download it.
            if hashing.is_dataless(ident):
                skip(SkipReason.CLOUD_ONLY)
                continue

            step = "stat"
            try:
                st = entry.stat(follow_symlinks=False)
                step = "read"
                if stat.S_ISDIR(st.st_mode):
                    if config.folders == "ignore":
                        skip(SkipReason.FOLDER_IGNORED)
                        continue
                    manifest = _read_twice(unit_manifest, path, sleep)
                    if manifest.dataless:
                        skip(SkipReason.CLOUD_ONLY)
                        continue
                    if now - manifest.newest_mtime < limits.stable_seconds:
                        skip(SkipReason.UNSTABLE)
                        continue
                    candidate = Candidate(
                        path,
                        manifest.digest,
                        manifest.size_bytes,
                        st.st_mtime,
                        is_unit=True,
                        mtime_ns=st.st_mtime_ns,
                        inode=st.st_ino,
                        created=_birth(st),
                    )
                elif stat.S_ISREG(st.st_mode):
                    if st.st_size == 0:
                        skip(SkipReason.EMPTY)
                        continue
                    if now - st.st_mtime < limits.stable_seconds:
                        skip(SkipReason.UNSTABLE)
                        continue
                    digest = _read_twice(file_sha256, path, sleep)
                    step = "stat"
                    after = os.lstat(path)
                    if (after.st_size, after.st_mtime_ns) != (st.st_size, st.st_mtime_ns):
                        skip(SkipReason.UNSTABLE)
                        continue
                    candidate = Candidate(
                        path,
                        digest,
                        st.st_size,
                        st.st_mtime,
                        is_unit=False,
                        mtime_ns=st.st_mtime_ns,
                        inode=st.st_ino,
                        created=_birth(st),
                    )
                else:
                    skip(SkipReason.SPECIAL)
                    continue
            except OSError as exc:
                skip(SkipReason.UNREADABLE, SkipDetail.from_error(step, exc))
                continue

            state = lookup(candidate.sha256)
            retry = (
                state is not None
                and state.status is FileStatus.FAILED
                and state.attempts < limits.max_attempts
            )
            if state is not None and not retry:
                skip(SkipReason.KNOWN)
                continue
            ready.append(candidate)

    # The index is keyed by content: of several copies, only the original goes ahead;
    # the others wait where they are (moving duplicates is RF-21).
    ready = _keep_originals(ready, result)
    ready.sort(key=lambda c: (c.mtime, str(c.path)))
    result.candidates = ready[: limits.max_files_per_run]
    result.deferred = len(ready) - len(result.candidates)
    return result

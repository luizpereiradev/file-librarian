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
  mtime are the same before and after hashing (RF-02). An mtime more than
  ``FUTURE_SLACK`` seconds ahead of now (a file from an archive, a camera
  with a wrong clock, a share whose server clock is ahead) cannot tell how
  long ago it changed: such an item is settled once its size and mtime stayed
  the same for ``stable_seconds`` across scans (``Settling``, kept between
  runs), never "still changing" forever. Settled ones are listed in
  ``future``.
- Skips as a download in progress an item Finder is still copying in: Finder
  marks it with the type ``brok`` and creator ``MACS`` and the creation date
  24 January 1984 until the copy ends.
- Skips anything already in the database by content hash, except files that
  failed fewer than ``max_attempts`` times (RF-01, RF-30) and files left in
  place as sensitive because of their name alone whose current name no
  longer is (renamed, allowed by ``sensitive.allow_names``, or the rules
  changed): those are looked at again like a new file.
- Finds duplicates (RF-21), with ``duplicates: move``: an item with the same
  content as a file filed in the library (organized or in review) that is
  still there with that content, or as another item going ahead in this run.
  The library copy is checked before the item is called a duplicate; one
  that is only in iCloud or cannot be read is not guessed at, and the item
  waits. If the library copy is not where flib left it, the item is skipped
  until ``flib reconcile`` finds it; only once reconcile marked it
  ``missing`` does the item go ahead like a new file, taking over the
  content's index row when it is moved. A copy that ``flib undo`` put back
  is left alone. With ``duplicates: leave``, duplicates are skipped and stay
  where they are.
- Applies each input's own rules (#89): its ``ignore`` patterns on top of the
  global ones; with ``only``, leaves alone everything that matches none of
  its patterns (``ignore`` wins over ``only``); its ``folders`` and
  ``duplicates`` instead of the global ones; and with ``min_age_days``,
  leaves alone, as ``too recent``, an item younger than that (``item_age``).
  Each candidate and duplicate carries the input it came from.
- Returns at most ``max_files_per_run`` candidates, oldest first (with
  ``prefer``, the preferred ones first: watch mode puts new arrivals before
  items an earlier run left behind). The rest are in ``left_over``.
  Duplicates are not counted: they cost no provider call. A duplicate of an
  item left for a later run waits with it.
- With ``hashes``, reuses the digest of a regular file whose device, inode,
  size and mtime are unchanged since it was hashed (watch mode looks often).
- Skips an input folder that cannot be listed (macOS privacy, deleted,
  unmounted) as ``input folder could not be read`` and goes on with the
  others; raises ``ScanError`` only when no input can be read.
- Keeps why an item was unreadable (the step and the errno) and reads it once
  more after a short pause when the error is plausibly transient.
- Skips as ``locked`` (not ``unreadable``) a file the system refuses because
  it is readable only while the Mac is unlocked (Data Protection class A or
  B, see ``protection``), and, while the screen is locked, a folder unit with
  any such file inside. The same goes for a duplicate whose library copy is
  such a file. A scheduled run then waits for the next unlock (``catchup``).

The scanner only reads. It never opens a file for writing and never follows
a symlink.
"""

from __future__ import annotations

import errno
import json
import math
import os
import re
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import TypeVar
from unicodedata import normalize

from file_librarian import hashing, protection
from file_librarian.config import Config, InputConfig, is_within
from file_librarian.extractors.origin import read_xattr
from file_librarian.globs import matches_any
from file_librarian.hashing import file_sha256, unit_manifest
from file_librarian.lock import lock_path
from file_librarian.models import FileState, FileStatus
from file_librarian.reconcile import content_at
from file_librarian.sensitive import sensitive_name

# Browsers write in-progress downloads under these names (Safari's are folders).
# Office's lock files (``~$report.docx``) only exist while a document is open.
IN_PROGRESS_PATTERNS = (
    "*.crdownload", "*.part", "*.partial", "*.download", "*.opdownload", "~$*",
)  # fmt: skip
# A final-name placeholder next to one of these is still downloading (Firefox, aria2...).
PARTIAL_SIBLINGS = (".part", ".crdownload", ".aria2", ".download", ".opdownload")


FUTURE_SLACK = 300.0
"""Seconds an mtime may be ahead of now and still mean "just changed"."""
FINDER_COPYING = b"brokMACS"
"""Type and creator Finder gives an item while it copies it in."""
_FINDER_COPY_DATE = (1984, 1, 24)


class HashCache:
    """Digests of regular files by device, inode, size, mtime, ctime and birth time, and
    which were used by the last scan (``seen``), so only those need keeping.

    Any write or ``utime`` moves the ctime, so a file rewritten in place with its
    size and mtime restored still gets a new key. With ``verify_above`` set (a real
    run), a file larger than that is hashed again even when its digest is cached:
    the executor re-hashes only smaller files right before a move.
    """

    def __init__(self, entries: dict[str, str] | None = None) -> None:
        self.entries = dict(entries or {})
        self.seen: dict[str, str] = {}
        self.verify_above: int | None = None
        self._keys: dict[Path, str] = {}

    @staticmethod
    def key(st: os.stat_result) -> str:
        birth = getattr(st, "st_birthtime", 0.0)
        return f"{st.st_dev}:{st.st_ino}:{st.st_size}:{st.st_mtime_ns}:{st.st_ctime_ns}:{birth}"

    def digest(self, path: Path, st: os.stat_result, read: Callable[[Path], str]) -> str:
        key = self.key(st)
        value = self.entries.get(key)
        if value is None or (self.verify_above is not None and st.st_size > self.verify_above):
            value = read(path)
            self.entries[key] = value
        self.seen[key] = value
        self._keys[path] = key
        return value

    def forget(self, path: Path) -> None:
        """Drop the digest last given for ``path`` (it turned out to be wrong)."""
        key = self._keys.pop(path, None)
        if key is not None:
            self.entries.pop(key, None)
            self.seen.pop(key, None)


class Settling:
    """Items dated in the future: when their size and mtime were first seen as they are.

    An item is settled once that was ``stable_seconds`` or more ago (by the scan's
    ``now``). Only the items of the last scan are kept (``seen``).
    """

    def __init__(self, entries: dict[str, list[float]] | None = None) -> None:
        self.entries = {k: list(v) for k, v in (entries or {}).items()}
        self.seen: dict[str, list[float]] = {}

    @staticmethod
    def key(st: os.stat_result) -> str:
        return f"{st.st_dev}:{st.st_ino}"

    def settled(self, st: os.stat_result, now: float, quiet: float, *, peek: bool = False) -> bool:
        key = self.key(st)
        shape = [float(st.st_size), float(st.st_mtime_ns)]
        entry = self.entries.get(key)
        if entry is None or entry[:2] != shape:
            entry = [*shape, now]
        if not peek:
            self.entries[key] = entry
            self.seen[key] = entry
        return now - entry[2] >= quiet

    def dumps(self) -> str:
        return json.dumps(self.seen, sort_keys=True)

    @classmethod
    def loads(cls, text: str | None) -> Settling:
        try:
            value = json.loads(text or "{}")
        except ValueError:
            return cls()
        if not isinstance(value, dict):
            return cls()
        return cls({str(k): [float(x) for x in v] for k, v in value.items() if len(v) == 3})


SETTLING_KEY = "scan.future_dated"
"""The ``meta`` key holding ``Settling`` between runs (device and inode, no names)."""


@dataclass(slots=True)
class ScanMemory:
    """What one process remembers between scans: digests and future-dated items."""

    hashes: HashCache = field(default_factory=HashCache)
    settling: Settling = field(default_factory=Settling)


def finder_copying(path: Path, st: os.stat_result) -> bool:
    """Finder is still copying this item in (its placeholder type, or creation date)."""
    birth = time.localtime(_birth(st))
    if (birth.tm_year, birth.tm_mon, birth.tm_mday) == _FINDER_COPY_DATE:
        return True
    try:
        info = read_xattr(path, "com.apple.FinderInfo")
    except OSError:
        return False
    return info is not None and info[:8] == FINDER_COPYING


class ScanError(RuntimeError):
    """An input folder cannot be read at all (for example, macOS privacy settings)."""


class SkipReason(StrEnum):
    HIDDEN = "hidden"
    SYMLINK = "symlink"
    SPECIAL = "special file"
    MANAGED = "managed by flib"
    IN_PROGRESS = "download in progress"
    IGNORED = "matches ignore"
    NOT_ONLY = "matches none of the input's only patterns"
    INPUT_UNREADABLE = "input folder could not be read; the other inputs were scanned"
    TOO_RECENT = "too recent"
    """Younger than its input's ``min_age_days``; the log adds how many days are left."""
    FOLDER_IGNORED = "folders: ignore"
    UNSTABLE = "still changing"
    KNOWN = "already processed"
    UNREADABLE = "unreadable"
    LOCKED = "protected by macOS while the Mac is locked"
    EMPTY = "empty file"
    DUPLICATE = "same content as another item in this run"
    CLOUD_ONLY = "in iCloud only, not downloaded"
    NOT_WHERE_LEFT = (
        "same content as a filed item that is not where flib left it; run `flib reconcile`"
    )


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
    input: Path | None = None
    """The input folder it was found in (``None`` for a file already in the library)."""


DAY = 86400.0


def item_age(st: os.stat_result, now: float, newest_inside: float | None = None) -> float:
    """How long ago, in seconds, an item last changed or arrived in its folder.

    The later of its modification time and its status-change time (``ctime``),
    which macOS sets when an item is moved, copied or renamed into a folder,
    and when its contents or attributes change; for a folder unit, also the
    newest modification time of anything inside. An item with a time in the
    future counts as brand new.
    """
    newest = max(st.st_mtime, st.st_ctime, newest_inside or 0.0)
    return max(0.0, now - newest)


def days_left(age: float, min_age_days: int) -> int:
    """Whole days, rounded up, until an item of ``age`` seconds is old enough."""
    return max(1, math.ceil((min_age_days * DAY - age) / DAY))


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
    """``stat`` or ``read``; ``list`` for an input folder that could not be listed."""
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


@dataclass(frozen=True, slots=True)
class Duplicate:
    """An item with the same content as a file already filed, or about to be (RF-21)."""

    candidate: Candidate
    original: Path
    """Where its original is: in the library, or (``in_library`` False) the item
    with the same content that goes ahead in this run."""
    in_library: bool


@dataclass(slots=True)
class ScanResult:
    candidates: list[Candidate] = field(default_factory=list)
    duplicates: list[Duplicate] = field(default_factory=list)
    """With ``duplicates: move``: items to move to the duplicates folder, unclassified."""
    skipped: list[tuple[Path, SkipReason]] = field(default_factory=list)
    details: dict[Path, SkipDetail] = field(default_factory=dict)
    """For unreadable items in ``skipped``: what failed."""
    days_left: dict[Path, int] = field(default_factory=dict)
    """For items skipped as ``too recent``: whole days until they are old enough."""
    deferred: int = 0
    """Ready candidates left for the next run because of ``max_files_per_run``."""
    left_over: list[Candidate] = field(default_factory=list)
    """Those candidates themselves (``deferred`` of them)."""
    future: list[Path] = field(default_factory=list)
    """Items with an mtime in the future, taken as settled."""


Lookup = Callable[[str], FileState | None]
T = TypeVar("T")


# "report (1).pdf" (browsers), "report copy.pdf" / "report copy 2.pdf" (Finder).
# "-2" is left out on purpose: it is indistinguishable from a version number.
_COPY_SUFFIX = re.compile(r"(?: \(\d+\)| copy(?: \d+)?)$", re.IGNORECASE)


def looks_like_a_copy(path: Path) -> bool:
    stem = path.name.removesuffix(path.suffix) if path.suffix else path.name
    return bool(_COPY_SUFFIX.search(stem))


def _keep_originals(ready: list[Candidate]) -> tuple[list[Candidate], list[Duplicate]]:
    """One candidate per content: no copy suffix first, then created first, then shortest.

    Returns the originals, and the other copies as duplicates of them.
    """
    by_hash: dict[str, list[Candidate]] = {}
    for candidate in ready:
        by_hash.setdefault(candidate.sha256, []).append(candidate)
    kept = []
    copies_of: list[Duplicate] = []
    for copies in by_hash.values():
        copies.sort(
            key=lambda c: (looks_like_a_copy(c.path), c.created, len(c.path.name), c.path.name)
        )
        kept.append(copies[0])
        copies_of.extend(Duplicate(c, copies[0].path, in_library=False) for c in copies[1:])
    return kept, copies_of


# Statuses of a content filed in the library, whose copies are duplicates.
_FILED = frozenset({FileStatus.ORGANIZED, FileStatus.REVIEW, FileStatus.MISSING})


def _known(
    candidate: Candidate, state: FileState, config: Config, rules: InputConfig
) -> SkipReason | Duplicate | None:
    """For content already in the index: why it is skipped, a duplicate, or ``None``
    (new: it goes ahead and, once moved, takes over the content's row).

    Only with ``duplicates: move``; a content that is not filed (sensitive,
    skipped, failed for good) is always skipped, as are copies undo put back.
    Only a ``missing`` row (reconcile found it nowhere) is taken over. A filed
    file that is not where flib left it (renamed or moved by hand, dragged back
    to Downloads, on a volume that is not mounted, unreadable) is never
    assumed gone: the item is skipped until ``flib reconcile`` says where it is.
    """
    here = normalize("NFC", str(candidate.path))
    if (
        config.duplicates_for(rules) != "move"
        or state.status not in _FILED
        or state.path is None
        or here in state.kept
        or here == normalize("NFC", str(state.path))  # the indexed file itself
    ):
        return SkipReason.KNOWN
    if state.status is FileStatus.MISSING:
        # Reconcile found it nowhere: this copy is the one left, unless taking the
        # row over already failed max_attempts times.
        return SkipReason.KNOWN if state.attempts >= config.limits.max_attempts else None
    there = content_at(state.path, candidate.sha256)
    if there is None:
        # Only in iCloud, or cannot be read now: never guess.
        if protection.protection_class(state.path) in protection.LOCKED_CLASSES:
            return SkipReason.LOCKED  # readable again once the Mac is unlocked
        return SkipReason.KNOWN
    if there:
        return Duplicate(candidate, state.path, in_library=True)
    return SkipReason.NOT_WHERE_LEFT


def _name_cleared(candidate: Candidate, state: FileState, config: Config) -> bool:
    """A file left in place as sensitive because of its name alone, whose current name
    no longer is (renamed, ``sensitive.allow_names``, changed rules): it goes ahead
    and is looked at like any new file, its content scanned for secrets."""
    if state.status is not FileStatus.SENSITIVE or state.sensitive_by != "name":
        return False
    detect = config.sensitive.detect_secrets
    return not (detect and sensitive_name(candidate.path.name, config.sensitive.allow_names))


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


def _input_problem(folder: Path, exc: OSError) -> str:
    if isinstance(exc, PermissionError):
        return (
            f"cannot read {folder}: macOS denied access. Give the app running flib "
            "access in System Settings > Privacy & Security > Files and Folders "
            "(or Full Disk Access) and try again."
        )
    if isinstance(exc, FileNotFoundError):
        return f"input folder does not exist: {folder}"
    return f"cannot read {folder}: {exc.strerror or exc}"


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
    prefer: Callable[[Candidate], bool] | None = None,
    hashes: HashCache | None = None,
    settling: Settling | None = None,
) -> ScanResult:
    now = time.time() if now is None else now
    limits = config.limits
    result = ScanResult()
    ready: list[Candidate] = []
    protected = _protected_ids(config)
    locked_now: list[bool] = []  # whether the screen is locked, asked once and only if needed

    def screen_locked() -> bool:
        if not locked_now:
            locked_now.append(protection.screen_locked() is True)
        return locked_now[0]

    failures: list[str] = []

    future_dated = settling if settling is not None else Settling()

    def changing(path: Path, st: os.stat_result, mtime: float) -> bool:
        """Changed in the last ``stable_seconds``. An mtime far in the future says
        nothing about that: unchanged for ``stable_seconds`` across scans is settled."""
        if mtime - now > FUTURE_SLACK:
            if not future_dated.settled(st, now, limits.stable_seconds):
                return True
            result.future.append(path)
            return False
        return now - mtime < limits.stable_seconds

    def too_recent(path: Path, age: float, min_age: int) -> None:
        # Never pending work: not locked, not deferred, simply not old enough yet.
        result.skipped.append((path, SkipReason.TOO_RECENT))
        result.days_left[path] = days_left(age, min_age)

    for rules in config.inputs:
        folder = rules.path
        ignore = (*config.ignore, *rules.ignore)
        folders = config.folders_for(rules)
        min_age = rules.min_age_days
        try:
            entries = sorted(os.scandir(folder), key=lambda e: e.name)
        except OSError as exc:
            # One folder that cannot be listed (macOS privacy, deleted, unmounted)
            # does not stop the others; only when none can be read does the run stop.
            result.skipped.append((folder, SkipReason.INPUT_UNREADABLE))
            result.details[folder] = SkipDetail.from_error("list", exc)
            failures.append(_input_problem(folder, exc))
            continue

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
            if matches_any(entry.name, ignore):
                skip(SkipReason.IGNORED)
                continue
            if rules.only is not None and not matches_any(entry.name, rules.only):
                skip(SkipReason.NOT_ONLY)
                continue
            # Before anything that opens, lists or hashes it: that would download it.
            if hashing.is_dataless(ident):
                skip(SkipReason.CLOUD_ONLY)
                continue
            if finder_copying(path, ident):
                skip(SkipReason.IN_PROGRESS)
                continue

            step = "stat"
            try:
                st = entry.stat(follow_symlinks=False)
                step = "read"
                if stat.S_ISDIR(st.st_mode):
                    if folders == "ignore":
                        skip(SkipReason.FOLDER_IGNORED)
                        continue
                    manifest = _read_twice(unit_manifest, path, sleep)
                    if manifest.dataless:
                        skip(SkipReason.CLOUD_ONLY)
                        continue
                    age = item_age(st, now, manifest.newest_mtime)
                    if age < min_age * DAY:
                        too_recent(path, age, min_age)
                        continue
                    if changing(path, st, manifest.newest_mtime):
                        skip(SkipReason.UNSTABLE)
                        continue
                    if screen_locked() and protection.has_locked_files(path):
                        skip(SkipReason.LOCKED)
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
                        input=folder,
                    )
                elif stat.S_ISREG(st.st_mode):
                    if st.st_size == 0:
                        skip(SkipReason.EMPTY)
                        continue
                    age = item_age(st, now)
                    if age < min_age * DAY:
                        too_recent(path, age, min_age)
                        continue
                    if changing(path, st, st.st_mtime):
                        skip(SkipReason.UNSTABLE)
                        continue
                    if hashes is not None:
                        digest = hashes.digest(
                            path, st, lambda p: _read_twice(file_sha256, p, sleep)
                        )
                    else:
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
                        input=folder,
                    )
                else:
                    skip(SkipReason.SPECIAL)
                    continue
            except OSError as exc:
                if step == "read" and protection.is_locked_out(exc, path):
                    skip(SkipReason.LOCKED)
                else:
                    skip(SkipReason.UNREADABLE, SkipDetail.from_error(step, exc))
                continue

            state = lookup(candidate.sha256)
            retry = state is not None and (
                (state.status is FileStatus.FAILED and state.attempts < limits.max_attempts)
                or _name_cleared(candidate, state, config)
            )
            if state is not None and not retry:
                known = _known(candidate, state, config, rules)
                if isinstance(known, Duplicate):
                    result.duplicates.append(known)
                    continue
                if known is not None:
                    skip(known)
                    continue
            ready.append(candidate)

    if failures and len(failures) == len(config.inputs):
        raise ScanError("; ".join(failures))
    # The index is keyed by content: of several copies, only the original goes ahead.
    ready, copies = _keep_originals(ready)
    if prefer is not None:
        ready.sort(key=lambda c: (not prefer(c), c.mtime, str(c.path)))
    else:
        ready.sort(key=lambda c: (c.mtime, str(c.path)))
    result.candidates = ready[: limits.max_files_per_run]
    result.left_over = ready[limits.max_files_per_run :]
    result.deferred = len(result.left_over)
    going = {c.path for c in result.candidates}
    for copy in copies:
        # The others wait where they are, or with ``duplicates: move`` follow their original.
        source = config.input_rules(copy.candidate.input) if copy.candidate.input else None
        if config.duplicates_for(source) == "move" and copy.original in going:
            result.duplicates.append(copy)
        else:
            result.skipped.append((copy.candidate.path, SkipReason.DUPLICATE))
    result.duplicates.sort(key=lambda d: str(d.candidate.path))
    return result

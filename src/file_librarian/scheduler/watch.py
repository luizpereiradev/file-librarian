"""Watch mode (#90): run when something arrives in an input folder.

With ``schedule.mode: watch`` (or ``both``) a second launchd agent watches
every input folder (``WatchPaths``) and starts ``flib run --trigger watch``
when one changes. launchd watches only the folder itself, not its
sub-folders, which is also all flib looks at. It starts the agent at most
once every ``schedule.watch_min_interval_minutes`` (``ThrottleInterval``:
changes in between are coalesced into one start), also once when the agent
is loaded, and never two at a time. A change made while the agent is
running does *not* start it again once it ends, so a watch run looks again
itself after each run (step 5).

What one trigger does (``watch``):

1. Takes the *watch lock*, an exclusive ``flock`` on ``flib-watch.lock`` next
   to the database, without blocking. If another process holds it, a watch
   run is alive and will look again before it ends, so this one exits at
   once. No process is ever signalled; the operating system releases the
   lock if its holder dies.
2. Looks without changing anything (``probe``): a scan of the inputs, as a
   watch run would do it (``Config.for_watch``: items must have been quiet
   for ``schedule.watch_quiet_seconds``), holding no instance lock. Ready
   items, all of them and not only the first ``max_files_per_run``, that no
   watch run has gone ahead for yet today are *new*. Downloads in progress
   (``.crdownload``, ``.part``, a Finder copy... changed in the last
   ``settle`` minutes) and items still changing are *pending*. Items too
   recent for their input's ``min_age_days``, only in iCloud or protected
   while the Mac is locked are neither.
3. While anything is pending, sleeps and checks again every
   ``POLL_SECONDS``, for at most ``schedule.watch_settle_minutes`` by a
   monotonic clock. Those checks only ``lstat`` the pending items; the inputs
   are scanned again once they settle. launchd does not start flib again when
   a file merely stops changing, so this is what files a download once it
   finishes. Ten files arriving together are one run.
4. With nothing new, exits: no run, no run record, no provider call, no log
   line. This is what stops a loop: flib's own moves out of an input change
   that folder and launchd starts the agent again, but what the run moved is
   gone and what it left (failed, deferred by a cost cap) is not new. Left
   items become new again the next day, once.
5. Otherwise runs once (``run_once``: the normal run, which takes the
   instance lock, waiting for it a while; it puts new arrivals ahead of left
   items, so a backlog never keeps them out), remembers what it went ahead for
   (the ``meta`` key ``watch.handled``: the day and keyed hashes of path and
   content, never names), and looks again for what arrived meanwhile (back
   to step 3), at most ``MAX_RUNS`` times.

Digests are kept by device, inode, size and mtime (``scanner.HashCache``),
in memory while the process lives and in ``meta`` (``watch.hashes``, no
names) for the items left in the inputs, so an item that stays is hashed
once, not at every look.

A watch run never writes to the input folders other than the moves of the
run itself; its output goes to ``watch.out.log`` in flib's logs folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from file_librarian import catchup
from file_librarian.config import Config
from file_librarian.db.store import Store
from file_librarian.scanner import Candidate, HashCache, SkipReason, finder_copying, scan

WATCH_LOCK_NAME = "flib-watch.lock"
HANDLED_KEY = "watch.handled"
"""The ``meta`` key holding what watch runs went ahead for today (hashes only)."""
HASHES_KEY = "watch.hashes"
"""The ``meta`` key holding digests of the items left in the inputs, keyed without names."""
POLL_SECONDS = 15.0
"""How often a watch run checks again while downloads settle."""
MAX_RUNS = 10
"""Runs one trigger may make while files keep arriving; the next change starts another."""

Clock = Callable[[], float]
Sleep = Callable[[float], None]
RunOnce = Callable[[HashCache], bool]


class Ended(StrEnum):
    NOTHING_NEW = "nothing new"
    RAN = "ran"
    BUSY = "another run holds the lock"
    WATCHING = "another watch run is alive"
    STILL_PENDING = "still pending"
    STOPPED = "stopped"


@dataclass(frozen=True, slots=True)
class Probe:
    new: int
    """Ready items no watch run has gone ahead for yet today."""
    pending: int
    """Downloads in progress and items still changing."""
    keys: frozenset[str]
    """Every ready item (path and content), new or not."""
    pending_paths: tuple[tuple[Path, bool], ...] = ()
    """The pending items, and whether each is a download in progress (else changing)."""

    @property
    def left(self) -> int:
        """Ready items an earlier watch run already went ahead for (left behind)."""
        return len(self.keys) - self.new


@dataclass(frozen=True, slots=True)
class Watched:
    ended: Ended
    runs: int = 0
    pending: int = 0
    """Items still pending when it gave up waiting."""
    left: int = 0
    """Ready items left in the inputs at the end: failed, over a cap."""


@dataclass(slots=True)
class _Looker:
    """The default ``look`` and ``recheck``: real scans, sharing a digest cache."""

    config: Config
    hashes: HashCache = field(default_factory=HashCache)

    def look(self) -> Probe:
        return probe(self.config, hashes=self.hashes)

    def recheck(self, paths: Sequence[tuple[Path, bool]]) -> bool:
        return still_pending(self.config, paths)


def watch_lock_path(database: Path) -> Path:
    return database.parent / WATCH_LOCK_NAME


def item_key(candidate: Candidate) -> str:
    """A path and its content, as a hash: the index already has both, the meta row neither."""
    return hashlib.sha256(f"{candidate.sha256}\0{candidate.path}".encode()).hexdigest()[:32]


def _today() -> str:
    return datetime.now().astimezone().date().isoformat()


def probe(config: Config, *, now: float | None = None, hashes: HashCache | None = None) -> Probe:
    """What a watch run would find now, without changing anything or taking the
    instance lock. Raises ``ScanError`` when no input can be read, as a run does."""
    now = time.time() if now is None else now
    watching = config.for_watch()
    if hashes is not None:
        hashes.seen.clear()
    with Store.open(config.database) as store:
        if hashes is not None and not hashes.entries:
            hashes.entries.update(_stored_hashes(store))
        found = scan(watching, store.file_state, now=now, hashes=hashes)
        handled = handled_keys(store)
    ready = [*found.candidates, *found.left_over, *(d.candidate for d in found.duplicates)]
    keys = {item_key(c) for c in ready}
    settle = config.schedule.watch_settle_minutes
    pending = tuple(
        (path, reason is SkipReason.IN_PROGRESS)
        for path, reason in found.skipped
        if _pending(path, reason, now, settle)
    )
    return Probe(len(keys - handled), len(pending), frozenset(keys), pending)


def _pending(path: Path, reason: SkipReason, now: float, settle_minutes: float) -> bool:
    if reason is SkipReason.UNSTABLE:
        return True
    if reason is not SkipReason.IN_PROGRESS:
        return False
    # A download abandoned long ago (or an Office lock file left behind) is not waited for.
    try:
        changed = os.lstat(path).st_mtime
    except OSError:
        return False
    return now - changed < max(settle_minutes, 1) * 60


def still_pending(
    config: Config, paths: Sequence[tuple[Path, bool]], *, now: float | None = None
) -> bool:
    """Whether any of these items is still pending, from ``lstat`` alone (no hashing).

    A download in progress that is gone was renamed or removed: the next scan says.
    """
    now = time.time() if now is None else now
    quiet = config.for_watch().limits.stable_seconds
    window = max(config.schedule.watch_settle_minutes, 1) * 60
    for path, in_progress in paths:
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if in_progress and (finder_copying(path, st) or now - st.st_mtime < window):
            return True
        if not in_progress and 0 <= now - st.st_mtime < quiet:
            return True
    return False


def handled_keys(store: Store) -> set[str]:
    """What watch runs went ahead for today; left items are new again the next day."""
    try:
        value = json.loads(store.meta(HANDLED_KEY) or "{}")
    except ValueError:
        return set()
    if not isinstance(value, dict) or value.get("day") != _today():
        return set()
    keys = value.get("keys")
    return {str(k) for k in keys} if isinstance(keys, list) else set()


def remember(config: Config, keys: frozenset[str]) -> None:
    """What the watch run went ahead for: only these, so the set never grows."""
    with Store.open(config.database) as store:
        store.set_meta(HANDLED_KEY, json.dumps({"day": _today(), "keys": sorted(keys)}))


def _stored_hashes(store: Store) -> dict[str, str]:
    try:
        value = json.loads(store.meta(HASHES_KEY) or "{}")
    except ValueError:
        return {}
    return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}


def keep_hashes(config: Config, hashes: HashCache) -> None:
    """The digests the last scan used: the items still in the inputs."""
    with Store.open(config.database) as store:
        store.set_meta(HASHES_KEY, json.dumps(hashes.seen, sort_keys=True))


def watch(
    config: Config,
    run_once: RunOnce,
    *,
    look: Callable[[], Probe] | None = None,
    recheck: Callable[[Sequence[tuple[Path, bool]]], bool] | None = None,
    clock: Clock = time.monotonic,
    sleep: Sleep = time.sleep,
) -> Watched:
    """One trigger, as the module docstring describes. ``run_once`` runs flib once with
    the digest cache and returns ``False`` when another run holds the instance lock."""
    looker = _Looker(config)
    look = look or looker.look
    recheck = recheck or looker.recheck
    limit = config.schedule.watch_settle_minutes * 60
    with catchup.try_flock(watch_lock_path(config.database)) as held:
        if not held:
            return Watched(Ended.WATCHING)
        runs = 0
        try:
            while True:
                found = _settle(look, recheck, limit, clock, sleep)
                if found is None:
                    return Watched(Ended.STOPPED, runs)
                if not found.new:
                    if runs:
                        return Watched(Ended.RAN, runs, found.pending, found.left)
                    if found.pending:
                        return Watched(Ended.STILL_PENDING, 0, found.pending, found.left)
                    return Watched(Ended.NOTHING_NEW, left=found.left)
                if not run_once(looker.hashes):
                    return Watched(Ended.BUSY, runs)
                remember(config, found.keys)
                runs += 1
                if runs >= MAX_RUNS:
                    return Watched(Ended.RAN, runs, left=found.left)
        finally:
            if looker.hashes.seen:
                keep_hashes(config, looker.hashes)


def _settle(
    look: Callable[[], Probe],
    recheck: Callable[[Sequence[tuple[Path, bool]]], bool],
    limit: float,
    clock: Clock,
    sleep: Sleep,
) -> Probe | None:
    """Look until nothing is pending or ``limit`` seconds pass; holds no instance lock.

    In between, only the pending items are checked (``recheck``); the inputs are
    scanned again once they settled or at the limit. ``SIGTERM`` (``flib schedule
    --remove``, a logout) ends it with ``None``; only here, never during a run, whose
    moves are journaled against a hard stop anyway.
    """
    deadline = clock() + limit
    with catchup.stop_on_sigterm():
        try:
            found = look()
            while found.pending:
                remaining = deadline - clock()
                if remaining <= 0:
                    break
                sleep(min(POLL_SECONDS, remaining))
                if deadline - clock() > 0 and recheck(found.pending_paths):
                    continue
                found = look()
            return found
        except catchup.Stopped:
            return None

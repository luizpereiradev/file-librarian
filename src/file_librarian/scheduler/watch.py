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
3. While anything is pending, sleeps and checks again, every
   ``POLL_SECONDS`` and later every ``SLOW_POLL_SECONDS``, by a monotonic
   clock. It keeps waiting while the pending items keep changing, and gives
   up after ``schedule.watch_settle_minutes`` without any change, or after
   ``HARD_LIMIT_SECONDS`` in any case. Those checks only ``lstat`` the pending
   items; the inputs are scanned again once they settle. launchd does not
   start flib again when a file merely stops changing, or for changes made
   while flib runs, so this is what files a long download once it finishes.
   Ten files arriving together are one run. Anything still missed is picked
   up by the agent's fallback start every ``schedule.watch_fallback_minutes``
   (``StartInterval``), which exits silently like any start with nothing new.
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

Digests are kept by device, inode, size, mtime, ctime and birth time
(``scanner.HashCache``), in memory while the process lives and in ``meta``
(``watch.hashes``, no names) for the items left in the inputs, so an item
that stays is hashed once, not at every look; the run itself never trusts a
cached digest for a file the executor does not hash again before moving it.
Items dated in the future are observed the same way (``scanner.Settling``).

A watch run never writes to the input folders other than the moves of the
run itself; its output goes to ``watch.out.log`` in flib's logs folder.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable, Hashable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from file_librarian import catchup
from file_librarian.config import Config
from file_librarian.db.store import Store
from file_librarian.scanner import (
    FUTURE_SLACK,
    SETTLING_KEY,
    Candidate,
    ScanMemory,
    Settling,
    SkipReason,
    finder_copying,
    scan,
)

WATCH_LOCK_NAME = "flib-watch.lock"
HANDLED_KEY = "watch.handled"
"""The ``meta`` key holding what watch runs went ahead for today (hashes only)."""
HASHES_KEY = "watch.hashes"
"""The ``meta`` key holding digests of the items left in the inputs, keyed without names."""
POLL_SECONDS = 15.0
"""How often a watch run checks again while downloads settle, at first."""
SLOW_POLL_SECONDS = 60.0
"""How often once ``SLOW_POLL_AFTER`` seconds passed."""
SLOW_POLL_AFTER = 300.0
HARD_LIMIT_SECONDS = 6 * 3600.0
"""However much pending items keep changing, a watch run stops waiting after this."""
MAX_RUNS = 10
"""Runs one trigger may make while files keep arriving; the next change starts another."""

Clock = Callable[[], float]
Sleep = Callable[[float], None]
RunOnce = Callable[[ScanMemory], bool]
Paths = Sequence[tuple[Path, bool]]


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
    """The default ``look``, ``recheck`` and ``observe``: real scans and ``lstat``s,
    sharing what this process remembers (``ScanMemory``)."""

    config: Config
    memory: ScanMemory = field(default_factory=ScanMemory)

    def look(self) -> Probe:
        return probe(self.config, memory=self.memory)

    def recheck(self, paths: Paths) -> bool:
        return still_pending(self.config, paths, settling=self.memory.settling)

    def observe(self, paths: Paths) -> Hashable:
        return observe(paths)


def watch_lock_path(database: Path) -> Path:
    return database.parent / WATCH_LOCK_NAME


def item_key(candidate: Candidate) -> str:
    """A path and its content, as a hash: the index already has both, the meta row neither."""
    return hashlib.sha256(f"{candidate.sha256}\0{candidate.path}".encode()).hexdigest()[:32]


def _today() -> str:
    return datetime.now().astimezone().date().isoformat()


def probe(config: Config, *, now: float | None = None, memory: ScanMemory | None = None) -> Probe:
    """What a watch run would find now, without changing anything or taking the
    instance lock. Raises ``ScanError`` when no input can be read, as a run does."""
    now = time.time() if now is None else now
    watching = config.for_watch()
    memory = memory if memory is not None else ScanMemory()
    memory.hashes.seen.clear()
    with Store.open(config.database) as store:
        if not memory.hashes.entries:
            memory.hashes.entries.update(_stored_hashes(store))
        if not memory.settling.entries:
            memory.settling.entries.update(Settling.loads(store.meta(SETTLING_KEY)).entries)
        found = scan(
            watching, store.file_state, now=now, hashes=memory.hashes, settling=memory.settling
        )
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
    config: Config,
    paths: Paths,
    *,
    now: float | None = None,
    settling: Settling | None = None,
) -> bool:
    """Whether any of these items is still pending, from ``lstat`` alone (no hashing).

    A download in progress that is gone was renamed or removed: the next scan says.
    An item dated more than ``FUTURE_SLACK`` ahead is pending until ``settling`` has
    seen it unchanged for the quiet time (without one, until the next scan).
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
        if in_progress:
            continue
        if st.st_mtime - now > FUTURE_SLACK:
            if settling is None or not settling.settled(st, now, quiet, peek=True):
                return True
        elif now - st.st_mtime < quiet:
            return True
    return False


def observe(paths: Paths) -> Hashable:
    """What the pending items look like now (size and mtime), to tell they still change."""
    shapes = []
    for path, _ in paths:
        try:
            st = os.lstat(path)
        except OSError:
            continue
        shapes.append((str(path), st.st_size, st.st_mtime_ns))
    return tuple(sorted(shapes))


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


def keep_memory(config: Config, memory: ScanMemory) -> None:
    """The digests and future-dated items the last scan used: those still in the inputs."""
    with Store.open(config.database) as store:
        store.set_meta(HASHES_KEY, json.dumps(memory.hashes.seen, sort_keys=True))
        store.set_meta(SETTLING_KEY, memory.settling.dumps())


def watch(
    config: Config,
    run_once: RunOnce,
    *,
    look: Callable[[], Probe] | None = None,
    recheck: Callable[[Paths], bool] | None = None,
    observe_paths: Callable[[Paths], Hashable] | None = None,
    clock: Clock = time.monotonic,
    sleep: Sleep = time.sleep,
) -> Watched:
    """One trigger, as the module docstring describes. ``run_once`` runs flib once with
    what this process remembers and returns ``False`` when another run holds the
    instance lock."""
    looker = _Looker(config)
    look = look or looker.look
    recheck = recheck or looker.recheck
    observe_paths = observe_paths or looker.observe
    idle = config.schedule.watch_settle_minutes * 60
    with catchup.try_flock(watch_lock_path(config.database)) as held:
        if not held:
            return Watched(Ended.WATCHING)
        runs = 0
        try:
            while True:
                found = _settle(look, recheck, observe_paths, idle, clock, sleep)
                if found is None:
                    return Watched(Ended.STOPPED, runs)
                if not found.new:
                    if runs:
                        return Watched(Ended.RAN, runs, found.pending, found.left)
                    if found.pending:
                        return Watched(Ended.STILL_PENDING, 0, found.pending, found.left)
                    return Watched(Ended.NOTHING_NEW, left=found.left)
                if not run_once(looker.memory):
                    return Watched(Ended.BUSY, runs)
                remember(config, found.keys)
                runs += 1
                if runs >= MAX_RUNS:
                    return Watched(Ended.RAN, runs, left=found.left)
        finally:
            if looker.memory.hashes.seen or looker.memory.settling.seen:
                keep_memory(config, looker.memory)


def _settle(
    look: Callable[[], Probe],
    recheck: Callable[[Paths], bool],
    observe_paths: Callable[[Paths], Hashable],
    idle: float,
    clock: Clock,
    sleep: Sleep,
) -> Probe | None:
    """Look until nothing is pending; holds no instance lock.

    It gives up after ``idle`` seconds in which the pending items did not change at
    all (a download that stalled), or after ``HARD_LIMIT_SECONDS`` in any case: while
    they keep changing, it keeps waiting, since launchd does not start flib again for
    changes made while it runs. In between, only the pending items are checked
    (``recheck``, ``observe_paths``: ``lstat`` only), every ``POLL_SECONDS`` and
    after ``SLOW_POLL_AFTER`` every ``SLOW_POLL_SECONDS``; the inputs are scanned
    again once they settled or when it gives up. ``SIGTERM`` (``flib schedule
    --remove``, a logout) ends it with ``None``; only here, never during a run, whose
    moves are journaled against a hard stop anyway.
    """
    start = changed = clock()
    with catchup.stop_on_sigterm():
        try:
            found = look()
            shape = observe_paths(found.pending_paths)
            while found.pending:
                now = clock()
                deadline = min(changed + idle, start + HARD_LIMIT_SECONDS)
                if now >= deadline:
                    break
                every = POLL_SECONDS if now - start < SLOW_POLL_AFTER else SLOW_POLL_SECONDS
                sleep(min(every, deadline - now))
                latest = observe_paths(found.pending_paths)
                if latest != shape:
                    shape, changed = latest, clock()
                if clock() < min(changed + idle, start + HARD_LIMIT_SECONDS) and recheck(
                    found.pending_paths
                ):
                    continue
                found = look()
                latest = observe_paths(found.pending_paths)
                if latest != shape:
                    shape, changed = latest, clock()
            return found
        except catchup.Stopped:
            return None

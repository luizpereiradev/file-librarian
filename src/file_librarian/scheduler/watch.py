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
2. Looks without changing anything (``probe``): a scan of the inputs, as a run
   would do it, holding no instance lock. Items ready to go that no watch
   run has gone ahead for yet are *new*. Downloads in progress
   (``.crdownload``, ``.part``... changed in the last ``settle`` minutes) and
   items still changing (``limits.stable_seconds``) are *pending*. Items too
   recent for their input's ``min_age_days``, only in iCloud or protected
   while the Mac is locked are neither.
3. While anything is pending, sleeps and looks again every
   ``POLL_SECONDS``, for at most ``schedule.watch_settle_minutes``. launchd
   does not start flib again when a file merely stops changing, so this is
   what files a download once it finishes. Ten files arriving together are
   one run.
4. With nothing new, exits: no run, no run record, no provider call, no log
   line. This is what stops a loop: flib's own moves out of an input change
   that folder and launchd starts the agent again, but what the run moved is
   gone and what it left (failed, deferred by a cost cap) is not new.
5. Otherwise runs once (``run_once``: the normal run, which takes the
   instance lock; if another run holds it, the watch run exits quietly and
   the next trigger looks again), remembers what it went ahead for (the
   ``meta`` key ``watch.handled``: keyed hashes of path and content, never
   names), and looks again for what arrived meanwhile (back to step 3), at
   most ``MAX_RUNS`` times.

A watch run never writes to the input folders other than the moves of the
run itself; its output goes to ``watch.out.log`` in flib's logs folder. A
run that skipped files protected while the Mac is locked waits for an unlock
the way the daily run does (``catchup``), after it has released the watch
lock.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from file_librarian import catchup
from file_librarian.config import Config
from file_librarian.db.store import Store
from file_librarian.scanner import Candidate, SkipReason, scan

WATCH_LOCK_NAME = "flib-watch.lock"
HANDLED_KEY = "watch.handled"
"""The ``meta`` key holding what the last watch run went ahead for (hashes only)."""
POLL_SECONDS = 15.0
"""How often a watch run looks again while downloads settle."""
MAX_RUNS = 10
"""Runs one trigger may make while files keep arriving; the next change starts another."""

Clock = Callable[[], float]
Sleep = Callable[[float], None]


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
    """Ready items no watch run has gone ahead for yet."""
    pending: int
    """Downloads in progress and items still changing."""
    keys: frozenset[str]
    """Every ready item (path and content), new or not."""


@dataclass(frozen=True, slots=True)
class Watched:
    ended: Ended
    runs: int = 0
    pending: int = 0
    """Items still pending when it gave up waiting."""


def watch_lock_path(database: Path) -> Path:
    return database.parent / WATCH_LOCK_NAME


def item_key(candidate: Candidate) -> str:
    """A path and its content, as a hash: the index already has both, the meta row neither."""
    return hashlib.sha256(f"{candidate.sha256}\0{candidate.path}".encode()).hexdigest()[:32]


def probe(config: Config, *, now: float | None = None, settle_minutes: float = 10) -> Probe:
    """What a run would find now, without changing anything or taking the instance lock.

    Raises ``ScanError`` when no input can be read, as a run does.
    """
    now = time.time() if now is None else now
    with Store.open(config.database) as store:
        found = scan(config, store.file_state, now=now)
        handled = _handled(store)
    keys = {item_key(c) for c in found.candidates}
    keys |= {item_key(d.candidate) for d in found.duplicates}
    pending = sum(
        1 for path, reason in found.skipped if _pending(path, reason, now, settle_minutes)
    )
    return Probe(len(keys - handled), pending, frozenset(keys))


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


def _handled(store: Store) -> set[str]:
    raw = store.meta(HANDLED_KEY)
    if raw is None:
        return set()
    try:
        value = json.loads(raw)
    except ValueError:
        return set()
    return {str(v) for v in value} if isinstance(value, list) else set()


def remember(config: Config, keys: frozenset[str]) -> None:
    """What the watch run went ahead for: only these, so the set never grows."""
    with Store.open(config.database) as store:
        store.set_meta(HANDLED_KEY, json.dumps(sorted(keys)))


def watch(
    config: Config,
    run_once: Callable[[], bool],
    *,
    look: Callable[[], Probe] | None = None,
    clock: Clock = time.time,
    sleep: Sleep = time.sleep,
) -> Watched:
    """One trigger, as the module docstring describes. ``run_once`` runs flib once and
    returns ``False`` when another run holds the instance lock."""
    settle_minutes = config.schedule.watch_settle_minutes
    look = look or (lambda: probe(config, settle_minutes=settle_minutes))
    with catchup.try_flock(watch_lock_path(config.database)) as held:
        if not held:
            return Watched(Ended.WATCHING)
        runs = 0
        while True:
            found = _settle(look, settle_minutes * 60, clock, sleep)
            if found is None:
                return Watched(Ended.STOPPED, runs)
            if not found.new:
                if runs:
                    return Watched(Ended.RAN, runs, found.pending)
                if found.pending:
                    return Watched(Ended.STILL_PENDING, 0, found.pending)
                return Watched(Ended.NOTHING_NEW)
            if not run_once():
                return Watched(Ended.BUSY, runs)
            remember(config, found.keys)
            runs += 1
            if runs >= MAX_RUNS:
                return Watched(Ended.RAN, runs)


def _settle(look: Callable[[], Probe], limit: float, clock: Clock, sleep: Sleep) -> Probe | None:
    """Look until nothing is pending or ``limit`` seconds pass; holds no instance lock.

    ``SIGTERM`` (``flib schedule --remove``, a logout) ends it with ``None``; only
    here, never during a run, whose moves are journaled against a hard stop anyway.
    """
    deadline = clock() + limit
    with catchup.stop_on_sigterm():
        try:
            while True:
                found = look()
                remaining = deadline - clock()
                if not found.pending or remaining <= 0:
                    return found
                sleep(min(POLL_SECONDS, remaining))
        except catchup.Stopped:
            return None

"""What the menu bar item shows, written where flib's state changes (#116).

flib computes everything; the applet (``menubar``) only reads ``state.json``
and checks it. ``changed`` is the one hook, and it is called:

- when the instance lock is released (``lock.instance_lock``): after every run,
  undo, fix, review action, due settlement, reprocess and MCP write, from the
  command line, the interface or the MCP server alike;
- when a run starts, inside the lock (``pipeline.run``), for the busy pose;
- when the schedule is installed or removed (``scheduler``), for the line
  about the next run;
- by ``note_problem``, when a run could not work (no key, no access) or worked
  again, for the trouble pose.

It does nothing unless the item is installed (one ``stat`` of its agent's
plist), and only for the library it was installed for: a hook from another
configuration's database is ignored. It never raises (not even a Ctrl-C over
another exception: see ``lock``) and never runs inside a database transaction
(the lock is released after the index is closed). It reads the status in its
cheap form (``status.collect(probe=False)``: no ``launchctl``, no subprocess at
all), writes only when the content differs from the file, and then nudges the
applet with a distributed notification (``NOTE_NAME``), which reaches only a
running applet and can never start one, unlike ``open``. The applet also
re-reads on a 30-second timer and when its menu opens, so a missed nudge costs
little.

``state.json`` (``menubar.STATE_KEYS``, in ``menubar.FORM``): ``version``,
``pose`` (one of ``poses.Pose``), ``count`` (next to the icon), ``line`` (the
state in words), ``next`` (the next run), ``review`` and ``due`` (for the
menu's direct entries) and ``running``. ASCII with ``\\uXXXX`` escapes. Counts
only: the menu bar is visible to anyone looking at the screen, and in screen
sharing, so no file name ever goes in. The two lines are in the configuration's
language (#126, ``i18n.messages.menu``).
"""

from __future__ import annotations

import ctypes
import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from file_librarian import i18n, lock, menubar, poses
from file_librarian import status as status_mod
from file_librarian.config import Config, load_config
from file_librarian.i18n import formats, t

log = logging.getLogger(__name__)

PROBLEMS = {
    "run_failed": t("menu.problem.run_failed"),
    "no_access": t("menu.problem.no_access"),
}
"""What ``note_problem`` records, in words for the menu's first line."""


@dataclass(frozen=True, slots=True)
class View:
    pose: poses.Pose
    count: int
    line: str
    next: str
    review: int
    due: int
    running: bool

    def to_json(self) -> bytes:
        data = {
            "version": menubar.STATE_VERSION,
            "pose": str(self.pose),
            "count": _bounded(self.count),
            "line": _fit(self.line),
            "next": _fit(self.next),
            "review": _bounded(self.review),
            "due": _bounded(self.due),
            "running": self.running,
        }
        # ASCII, compact, flat: exactly ``menubar.FORM``.
        return json.dumps(data, separators=(",", ":")).encode("ascii") + b"\n"


def _bounded(count: int) -> int:
    return max(0, min(count, menubar.MAX_COUNT))


def _fit(text: str) -> str:
    """At most ``MAX_TEXT`` UTF-16 units, with no ``"`` or ``\\`` (``FORM`` has no
    escape for them) and no character the applet refuses."""
    text = "".join(
        "'" if char in '"\\' else char
        for char in text
        if char.isprintable() and not 0xD800 <= ord(char) <= 0xDFFF
    )
    if menubar._utf16_length(text) <= menubar.MAX_TEXT:
        return text
    while menubar._utf16_length(text) > menubar.MAX_TEXT - 1:
        text = text[:-1]
    return text + "…"


def describe(
    status: status_mod.Status | None, *, running: bool, problem: str | None
) -> tuple[str, str]:
    """The menu's two lines: the state in words, and the next run."""
    upcoming = "" if status is None else next_line(status.schedule)
    if running:
        return t("menu.organizing").text(), upcoming
    if status is None:
        return t("menu.unreadable").text(), upcoming
    parts = []
    if problem is not None:
        parts.append(PROBLEMS.get(problem, PROBLEMS["run_failed"]).text())
    if status.review_total:
        parts.append(t("menu.to_review", count=status.review_total).text())
    if status.failed_total:
        parts.append(t("menu.failed", count=status.failed_total).text())
    if status.due is not None:
        if status.due.overdue:
            parts.append(t("menu.overdue", count=status.due.overdue).text())
        if status.due.upcoming:
            parts.append(t("menu.due_soon", count=status.due.upcoming).text())
    return (", ".join(parts) or t("menu.nothing").text()), upcoming


def next_line(schedule: status_mod.NextRun) -> str:
    if schedule.error is not None:
        return ""
    if not schedule.installed:
        return t("menu.not_scheduled").text()
    if not schedule.loaded:
        return t("menu.not_active").text()
    watching = schedule.mode in ("watch", "both")
    if schedule.at is None:
        return t("menu.on_arrival").text() if watching else ""
    when = formats.weekday_clock(schedule.at)
    return t("menu.next_and_watch" if watching else "menu.next", when=when).text()


_CONFIGS: dict[tuple[str, int, int], Config] = {}
"""Configurations already read, by path, modification time and size: a long-lived
process (the interface, the MCP server) does not parse the file on every write."""


def _config(path: Path) -> Config:
    info = path.stat()
    key = (str(path), info.st_mtime_ns, info.st_size)
    config = _CONFIGS.get(key)
    if config is None:
        _CONFIGS.clear()
        config = _CONFIGS[key] = load_config(path)
    return config


def view(
    database: Path,
    config_path: Path,
    *,
    now: datetime | None = None,
    status: status_mod.Status | None = None,
    probe: bool = False,
) -> View:
    """What the item should show now, for the configuration at ``config_path``:
    from ``status`` when the caller has just collected it (``flib status``, which
    asked launchd), else collected here, cheaply unless ``probe``."""
    config = _config(config_path)
    if status is None:
        try:
            status = status_mod.collect(config, now=now, menu_bar=False, probe=probe)
        except Exception:  # an index it cannot read, whatever the reason: trouble
            status = None
    running = lock.holder(database) is not None
    problem = read_problem()
    shown = poses.menu_state(status, running=running, blocked=problem is not None)
    with i18n.using(config.language):
        line, upcoming = describe(status, running=running, problem=problem)
    return View(
        shown.pose,
        shown.count,
        line,
        upcoming,
        0 if status is None else status.review_total,
        0 if status is None or status.due is None else status.due.upcoming + status.due.overdue,
        running,
    )


def _same(a: Path, b: Path) -> bool:
    return os.path.realpath(a) == os.path.realpath(b)


def write_state(
    database: Path,
    config_path: Path,
    *,
    status: status_mod.Status | None = None,
    probe: bool = False,
) -> bool:
    """Write the state for this library if it differs from the file, and nudge the
    applet; ``True`` when written. Raises what it meets (``changed`` swallows it)."""
    data = view(database, config_path, status=status, probe=probe).to_json()
    if menubar.read_small(menubar.state_path()) == data:
        return False
    menubar.write_private(menubar.state_path(), data)
    nudge()
    return True


def changed(
    database: Path | None = None,
    *,
    status: status_mod.Status | None = None,
    probe: bool = False,
) -> None:
    """Write the state and nudge the applet, if the item is installed (for ``database``,
    when given). Never raises; with no item installed, costs one ``stat``.

    The hot path (every lock release) leaves ``probe`` off: no ``launchctl``, so the
    next-run line assumes the agents it finds are loaded. Where flib asks launchd
    anyway, it passes the real answer: ``flib status``, the interface's status and
    ``flib doctor`` (``status``, just collected) and ``flib schedule`` (``probe``).
    That also heals a "running" left by a run that was killed: running is the
    live lock holder, never the old file."""
    try:
        if not menubar.installed():
            return
        target = menubar.read_target()
        if target is None or (database is not None and not _same(database, target.database)):
            return
        write_state(target.database, target.config, status=status, probe=probe)
    except Exception as exc:  # the item is a convenience: it must never break flib
        log.debug("menu bar state not written: %s: %s", type(exc).__name__, exc)


def _post_distributed(name: str) -> bool:
    """Post the distributed notification ``name`` to this login session, through
    CoreFoundation (no Apple event, no permission; it starts nothing)."""
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFNotificationCenterGetDistributedCenter.restype = ctypes.c_void_p
    cf.CFNotificationCenterPostNotification.argtypes = [
        ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_bool,
    ]  # fmt: skip
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    utf8 = 0x08000100  # kCFStringEncodingUTF8
    ref = cf.CFStringCreateWithCString(None, name.encode("ascii"), utf8)
    if not ref:
        return False
    try:
        center = cf.CFNotificationCenterGetDistributedCenter()
        cf.CFNotificationCenterPostNotification(center, ref, None, None, True)
    finally:
        cf.CFRelease(ref)
    return True


POST: Callable[[str], bool] = _post_distributed
"""How the nudge is posted; tests swap in a recorder."""


def nudge() -> bool:
    """Tell a running applet to read the state now. ``True`` when posted."""
    try:
        return POST(menubar.NOTE_NAME)
    except (OSError, AttributeError) as exc:
        log.debug("menu bar app not nudged: %s", exc)
        return False


def read_problem() -> str | None:
    try:
        value = json.loads(menubar.read_small(menubar.folder() / menubar.PROBLEM_NAME) or b"")
    except Exception:
        return None
    return value if isinstance(value, str) and value in PROBLEMS else None


def note_problem(database: Path, problem: str | None) -> None:
    """Remember why the last run could not work (a key of ``PROBLEMS``), or that it
    worked (``None``), for the trouble pose; then update the state if that changed.
    Only while the item is installed, for its library. Never raises."""
    try:
        if not menubar.installed():
            return
        target = menubar.read_target()
        if target is None or not _same(database, target.database):
            return
        if read_problem() == problem:
            return
        path = menubar.folder() / menubar.PROBLEM_NAME
        if problem is None:
            path.unlink(missing_ok=True)
        else:
            menubar.write_private(path, json.dumps(problem).encode() + b"\n")
    except Exception as exc:
        log.debug("menu bar problem not noted: %s: %s", type(exc).__name__, exc)
        return
    changed(database)

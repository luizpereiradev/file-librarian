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

It does nothing unless the item is installed (one ``stat``), and only for the
library it was installed for: a hook from another configuration's database
is ignored. It never raises and never runs inside a database transaction (the
lock is released after the index is closed); anything that goes wrong is
logged at debug level and the item heals on the next change. After writing,
it nudges the applet with ``open -g`` only while launchd says it runs (an
``open`` would start an app the user quit); the applet also re-reads on a
30-second timer and when its menu opens, so a missed nudge costs little.

``state.json`` (``menubar.STATE_KEYS``): ``version``, ``pose`` (one of
``poses.Pose``), ``count`` (next to the icon), ``line`` (the state in words),
``next`` (the next run), ``review`` and ``due`` (for the menu's direct
entries) and ``running``. Counts only: the menu bar is visible to anyone
looking at the screen, and in screen sharing, so no file name ever goes in.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from file_librarian import lock, menubar, notifier, poses, scheduler
from file_librarian import status as status_mod
from file_librarian.config import ConfigError, load_config

log = logging.getLogger(__name__)

PROBLEMS = {
    "run_failed": "flib could not run (`flib doctor` says why)",
    "no_access": "macOS denied a folder (`flib doctor`)",
}
"""What ``note_problem`` records, in words for the menu's first line."""
NUDGE_TIMEOUT = 5.0


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
        return json.dumps(data, ensure_ascii=False).encode("utf-8") + b"\n"


def _bounded(count: int) -> int:
    return max(0, min(count, menubar.MAX_COUNT))


def _fit(text: str) -> str:
    if len(text) <= menubar.MAX_TEXT:
        return text
    return text[: menubar.MAX_TEXT - 1] + "…"


def _files(count: int, what: str) -> str:
    return f"{count} file{'s' if count != 1 else ''} {what}"


def describe(
    status: status_mod.Status | None, *, running: bool, problem: str | None
) -> tuple[str, str]:
    """The menu's two lines: the state in words, and the next run."""
    upcoming = "" if status is None else next_line(status.schedule)
    if running:
        return "Organizing…", upcoming
    if status is None:
        return "Cannot read flib's index (`flib doctor`)", upcoming
    parts = []
    if problem is not None:
        parts.append(PROBLEMS.get(problem, PROBLEMS["run_failed"]))
    if status.review_total:
        parts.append(_files(status.review_total, "to review"))
    if status.failed_total:
        parts.append(f"{status.failed_total} failed")
    if status.due is not None:
        if status.due.overdue:
            parts.append(f"{status.due.overdue} overdue")
        if status.due.upcoming:
            parts.append(f"{status.due.upcoming} due soon")
    return (", ".join(parts) or "Nothing pending"), upcoming


def next_line(schedule: status_mod.NextRun) -> str:
    if schedule.error is not None:
        return ""
    if not schedule.installed:
        return "Not scheduled"
    if not schedule.loaded:
        return "Schedule not active (`flib schedule`)"
    watching = schedule.mode in ("watch", "both")
    if schedule.at is None:
        return "Runs as files arrive" if watching else ""
    when = schedule.at.strftime("%a %H:%M")
    return f"Next run {when}, and as files arrive" if watching else f"Next run {when}"


def view(database: Path, config_path: Path, *, now: datetime | None = None) -> View:
    """What the item should show now, for the configuration at ``config_path``."""
    config = load_config(config_path)
    try:
        status: status_mod.Status | None = status_mod.collect(config, now=now, menu_bar=False)
    except (status_mod.StatusError, sqlite3.Error, OSError):
        status = None
    running = lock.holder(database) is not None
    problem = read_problem()
    shown = poses.menu_state(status, running=running, blocked=problem is not None)
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


def changed(database: Path | None = None) -> None:
    """Write the state and nudge the applet, if the item is installed (for ``database``,
    when given). Never raises; never slows a run by more than reading the status."""
    try:
        target = menubar.read_target()
        if target is None or (database is not None and not _same(database, target.database)):
            return
        menubar.write_private(menubar.state_path(), view(target.database, target.config).to_json())
        nudge()
    except (OSError, ValueError, ConfigError, sqlite3.Error, scheduler.ScheduleError) as exc:
        log.debug("menu bar state not written: %s", exc)
    except Exception as exc:  # the item is a convenience: it must never break flib
        log.debug("menu bar state not written: %s: %s", type(exc).__name__, exc)


def nudge() -> bool:
    """Tell a running applet to read the state now (``open -g``: a reopen event, no
    Automation permission). Not when launchd says it is not running: ``open`` would
    start an app the user quit. ``True`` when nudged."""
    app = menubar.app_path()
    if not app.exists() or not scheduler.agent_running(menubar.LABEL):
        return False
    try:
        # A fixed binary and flib's own app path.
        result = subprocess.run(  # noqa: S603
            [notifier.OPEN, "-g", str(app)],
            capture_output=True,
            timeout=NUDGE_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("menu bar app not nudged: %s", exc)
        return False
    return result.returncode == 0


def read_problem() -> str | None:
    try:
        value = json.loads((menubar.folder() / menubar.PROBLEM_NAME).read_text("utf-8"))
    except (OSError, ValueError):
        return None
    return value if value in PROBLEMS else None


def note_problem(database: Path, problem: str | None) -> None:
    """Remember why the last run could not work (a key of ``PROBLEMS``), or that it
    worked (``None``), for the trouble pose; then update the state if that changed.
    Only for the library the item was installed for. Never raises."""
    try:
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
    except OSError as exc:
        log.debug("menu bar problem not noted: %s", exc)
        return
    changed(database)

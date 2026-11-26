"""``flib status`` and the periodic summary after a scheduled run (RF-40).

``collect`` reads what waits for the user: files in the review folder,
grouped by why they are there, failed files grouped by the kind of error,
files left in place as sensitive, the last and next run, and this month's
provider cost. It never takes the lock and opens the index read-only (it
neither creates nor migrates it), so it works before the first run and while
a run is going on.

``after_scheduled_run`` is the periodic summary: after a *scheduled* run, at
most once every ``summary.every_days`` (counted in calendar days), and only
when some file has waited in review or failed for ``summary.min_age_days`` or
more, it writes one line to the run log and, with ``summary.notify``, posts
one macOS notification. The notification holds counts only, never a file
name: notifications show on the lock screen. Whether it shows at all is up to
macOS (see docs/troubleshooting.md); the log and ``flib status`` are enough on their own,
and a notification that fails never fails the run.

The notification comes from flib's own notifier app when it is built and
current (``notifier``: it shows as "file-librarian" with flib's icon), and
otherwise from ``osascript``, which macOS shows as Script Editor. All
``osascript`` calls go through ``OSASCRIPT`` (tests swap in a fake). Either
way the text is passed as arguments, never written into a script, so nothing
in it can run as AppleScript.
"""

from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from file_librarian import due as due_mod
from file_librarian import notifier, scheduler
from file_librarian.config import Config, canonical
from file_librarian.db.store import RunRow, Store, WaitingRow, load_migrations
from file_librarian.models import FileStatus, ReviewReason, RunTrigger, explain_review
from file_librarian.runlog import log_path
from file_librarian.scanner import SkipReason

OSASCRIPT = "/usr/bin/osascript"
NOTIFICATION_TITLE = "file-librarian"
DUPLICATES_MOVED = "duplicates.first_moved_at"
"""The ``meta`` key holding when a run first set duplicates aside (UTC, RF-21)."""
DUPLICATES_NOTICE_SEEN = "duplicates.notice_seen_at"
"""The ``meta`` key holding when the note about it was shown at a terminal (UTC)."""
SUMMARY_KEY = "summary.last_at"
"""The ``meta`` key holding when the last periodic summary was written (UTC)."""
BACKLOG_KEY = "watch.backlog"
"""The ``meta`` key holding what the last watch start left in the inputs (a count)."""
DAY_CAP_KEY = "limits.day_cap_reached_on"
"""The ``meta`` key holding the local date automatic runs last reached the daily cost cap."""

# The first schema with what status reads (review reasons, run costs).
_MIN_SCHEMA = 10
# The first schema with the meta table.
_META_SCHEMA = 11
# The skips of the last run worth telling the user about, as its log words them.
_LOGGED_SKIPS = {
    "unreadable": SkipReason.UNREADABLE,
    "cloud_only": SkipReason.CLOUD_ONLY,
    "not_where_left": SkipReason.NOT_WHERE_LEFT,
    "locked": SkipReason.LOCKED,
    "too_recent": SkipReason.TOO_RECENT,
    "input_unreadable": SkipReason.INPUT_UNREADABLE,
}
_SKIP_LINE = re.compile(r"  skip    .*  \((.*)\)")
_SCRIPT = (
    "-e", "on run argv",
    "-e", "display notification (item 1 of argv) with title (item 2 of argv)",
    "-e", "end run",
)  # fmt: skip


class StatusError(Exception):
    """The index cannot be read (written by another version of flib)."""


@dataclass(frozen=True, slots=True)
class ReviewGroup:
    """Files in review for the same reason, with the same suggested category."""

    reason: ReviewReason | None
    suggested: str | None
    count: int
    oldest: datetime

    @property
    def explanation(self) -> str:
        return explain_review(self.reason, self.suggested, None)


@dataclass(frozen=True, slots=True)
class FailedGroup:
    """Failed files with the same kind of error, still retried or given up on."""

    error: str
    """The kind of error: its text up to the first colon (no names or paths)."""
    count: int
    attempts: int
    """The most attempts any of them took."""
    retried: bool
    """Fewer than ``limits.max_attempts``: the next run tries them again."""
    oldest: datetime


@dataclass(frozen=True, slots=True)
class InputCount:
    """How many files flib took from one input folder, and where they are now."""

    index: int
    """1-based, in the configured order (``<input-N>`` in ``flib doctor``)."""
    folder: Path
    filed: int
    """Organized or in review."""
    left: int
    """Left in place: sensitive, skipped, failed, missing."""


@dataclass(frozen=True, slots=True)
class NextRun:
    installed: bool
    loaded: bool
    time: str | None
    at: datetime | None
    """The next scheduled run, local time; ``None`` when nothing is scheduled."""
    error: str | None = None
    mode: str | None = None
    """``daily``, ``watch`` or ``both`` from the agents installed; ``None`` with none."""
    watched: int = 0
    """Input folders the watch agent watches."""
    watch_interval_minutes: int | None = None
    stale_watch: str | None = None
    """The watch agent watches other folders than the inputs: what to do about it."""


@dataclass(frozen=True, slots=True)
class Status:
    database_exists: bool
    review: list[ReviewGroup]
    failed: list[FailedGroup]
    sensitive: int
    last_run: RunRow | None
    skipped: dict[str, int] | None
    """``unreadable``, ``cloud_only``, ``not_where_left`` and ``locked`` counts from the last
    run's log; ``None`` without one."""
    schedule: NextRun
    month_cost_usd: float
    month_unpriced_runs: int
    last_summary: datetime | None
    duplicates_notice: bool = False
    """The one-time note about duplicates set aside was not seen at a terminal yet."""
    duplicates: int = 0
    """Copies set aside in the duplicates folder (RF-21)."""
    by_input: list[InputCount] | None = None
    """Files per input folder, in the configured order; ``None`` with a single input
    or an index from before #89."""
    due: due_mod.DueCounts | None = None
    """Open documents due soon or overdue (#91); ``None`` on an index from before it."""
    last_watch: RunRow | None = None
    """The newest watch-mode run (#90)."""
    automatic_cost_today: float = 0.0
    """What automatic runs (daily and watch) cost today, local time."""
    day_cap_reached: bool = False
    """Automatic runs reached ``limits.max_cost_usd_per_day`` today."""
    backlog: tuple[int, str | None] | None = None
    """Ready items the last watch start left in the inputs today, and why."""
    automatic_files_today: int = 0
    notifier: notifier.Choice | None = None
    """How a summary would be posted; ``None`` with ``summary.notify`` off."""
    menu_bar: str | None = None
    """The line about the menu bar item (#116); ``None`` when it is not installed."""

    @property
    def review_total(self) -> int:
        return sum(g.count for g in self.review)

    @property
    def failed_total(self) -> int:
        return sum(g.count for g in self.failed)

    @property
    def oldest_review(self) -> datetime | None:
        return min((g.oldest for g in self.review), default=None)


# --- reading ---------------------------------------------------------------------------


def collect(config: Config, *, now: datetime | None = None, menu_bar: bool = True) -> Status:
    """What waits for the user. Read-only and lock-free; raises ``StatusError``.
    ``menu_bar=False`` leaves out the menu bar item's own state (what it shows is
    computed from the rest)."""
    # "Today" for due dates comes from the same clock `flib due` uses, so the two agree.
    due_today = due_mod.local_today(now or due_mod.clock())
    now = now or datetime.now(UTC)
    schedule = next_run(now, config.input_paths)
    posting = notifier.choice() if config.summary.notify else None
    menu_line = _menu_bar_line() if menu_bar else None
    if not config.database.exists():
        return Status(
            False, [], [], 0, None, None, schedule, 0.0, 0, None,
            notifier=posting, menu_bar=menu_line,
        )  # fmt: skip
    with Store.open_read_only(config.database) as store:
        version = store.schema_version
        if version > len(load_migrations()):
            raise StatusError(
                f"the index (schema {version}) is newer than this flib; upgrade file-librarian"
            )
        if version < _MIN_SCHEMA:
            raise StatusError("the index is from an older flib; the next `flib run` updates it")
        waiting = store.waiting()
        stats = store.stats()
        last_summary = _last_summary(store)
        notice = duplicates_notice_due(store)
        by_input = input_counts(config, store) if len(config.inputs) > 1 else None
        due = None
        if version >= due_mod.MIN_SCHEMA:
            due = due_mod.counts(store, config, due_today)
        last_watch = store.last_run_by(RunTrigger.WATCH)
        backlog = _backlog(store) if version >= _META_SCHEMA else None
        files_today = store.automatic_files_today()
        cost_today = store.automatic_cost_today()
        capped = version >= _META_SCHEMA and store.meta(DAY_CAP_KEY) == _today()
    last = stats.last_run
    return Status(
        database_exists=True,
        review=review_groups(waiting),
        failed=failed_groups(waiting, config.limits.max_attempts),
        sensitive=stats.by_status[FileStatus.SENSITIVE.value],
        last_run=last,
        skipped=None if last is None else logged_skips(config.database, last.id),
        schedule=schedule,
        month_cost_usd=stats.month_cost_usd,
        month_unpriced_runs=stats.month_unpriced_runs,
        last_summary=last_summary,
        duplicates=stats.by_status.get(FileStatus.DUPLICATE.value, 0),
        duplicates_notice=notice,
        by_input=by_input,
        due=due,
        last_watch=last_watch,
        backlog=backlog,
        automatic_files_today=files_today,
        automatic_cost_today=cost_today,
        day_cap_reached=capped,
        notifier=posting,
        menu_bar=menu_line,
    )


def _menu_bar_line() -> str | None:
    from file_librarian import menubar  # it builds on this module (poses)

    return menubar.status_line(menubar.summary())


def _today() -> str:
    return datetime.now().astimezone().date().isoformat()


def day_cap_line(config: Config) -> str:
    """Said once a day, in the log of the run that reached the cap, and by ``flib status``."""
    cap = config.limits.cost_cap_per_day
    amount = "" if cap is None else f" (US$ {cap:.2f}, limits.max_cost_usd_per_day)"
    return (
        f"Automatic runs reached today's cost cap{amount}: until midnight they send no "
        "file to the provider; the rest wait for tomorrow, or run `flib run` now."
    )


def input_counts(config: Config, store: Store) -> list[InputCount] | None:
    """Files per configured input (#89); ``None`` on an index from before it."""
    try:
        counts = store.count_by_input()
    except sqlite3.OperationalError:  # no source_input column until the next run
        return None
    found = []
    for i, entry in enumerate(config.inputs, start=1):
        key = canonical(entry.path)
        by_status: dict[str, int] = {}
        for folder, statuses in counts.items():
            if canonical(Path(folder)) == key:
                for status, n in statuses.items():
                    by_status[status] = by_status.get(status, 0) + n
        filed = by_status.get(FileStatus.ORGANIZED.value, 0) + by_status.get(
            FileStatus.REVIEW.value, 0
        )
        found.append(InputCount(i, entry.path, filed, sum(by_status.values()) - filed))
    return found


def sensitive_files(config: Config) -> list[tuple[Path, str | None]]:
    """Every file left in place as sensitive, with why (``flib status --sensitive``).

    Names and paths: for the terminal on this Mac only, never for ``flib doctor``,
    notifications or the MCP server. Read-only, like ``collect``.
    """
    if not config.database.exists():
        return []
    with Store.open_read_only(config.database) as store:
        try:
            return store.sensitive_files()
        except sqlite3.OperationalError:  # an index from before reasons were recorded
            rows = store.conn.execute(
                "SELECT current_path FROM files WHERE status = 'sensitive' ORDER BY current_path"
            )
            return [(Path(r[0]), None) for r in rows]


def render_sensitive(found: list[tuple[Path, str | None]]) -> list[str]:
    if not found:
        return ["No file is left in place as sensitive."]
    lines = [f"Left in place as sensitive: {_files(len(found))}"]
    for path, reason in found:
        why = reason or "reason not recorded (left in place by an earlier version)"
        lines.append(f"  {path}  ({why})")
    lines.append(
        "A file sensitive only because of its name is looked at again once renamed, or "
        "when `sensitive.allow_names` lets its name through."
    )
    return lines


def duplicates_notice_due(store: Store) -> bool:
    """Duplicates were set aside, and the note about it was not seen at a terminal."""
    try:
        moved = store.meta(DUPLICATES_MOVED)
        seen = store.meta(DUPLICATES_NOTICE_SEEN)
    except sqlite3.OperationalError:  # no meta table yet
        return False
    return moved is not None and seen is None


def acknowledge_duplicates_notice(store: Store) -> None:
    store.set_meta(DUPLICATES_NOTICE_SEEN, datetime.now(UTC).isoformat(timespec="seconds"))


def duplicates_notice(config: Config) -> str:
    """The note shown until a run by hand prints it to a terminal (RF-21)."""
    return (
        f"Note: files with the same content as one already filed were moved to "
        f"{config.duplicates_folder} in the organized folder; nothing was deleted. "
        "`flib undo` puts them back, and `duplicates: leave` in the configuration "
        "leaves copies where they are."
    )


def _last_summary(store: Store) -> datetime | None:
    try:
        value = store.meta(SUMMARY_KEY)
    except sqlite3.OperationalError:  # schema 10: no meta table until the next run
        return None
    return None if value is None else _when(value)


def review_groups(rows: Iterable[WaitingRow]) -> list[ReviewGroup]:
    groups: dict[tuple[ReviewReason | None, str | None], list[datetime]] = {}
    for row in rows:
        if row.status is FileStatus.REVIEW:
            key = (row.review_reason, row.review_category)
            groups.setdefault(key, []).append(_when(row.processed_at))
    found = [ReviewGroup(r, s, len(times), min(times)) for (r, s), times in groups.items()]
    return sorted(found, key=lambda g: (-g.count, g.oldest))


def failed_groups(rows: Iterable[WaitingRow], max_attempts: int) -> list[FailedGroup]:
    groups: dict[tuple[str, bool], list[WaitingRow]] = {}
    for row in rows:
        # A ``missing`` row in ``waiting`` is a copy that failed to take it over (RF-21).
        if row.status in (FileStatus.FAILED, FileStatus.MISSING):
            key = (error_kind(row.last_error), row.attempts < max_attempts)
            groups.setdefault(key, []).append(row)
    found = [
        FailedGroup(
            error=kind,
            count=len(members),
            attempts=max(m.attempts for m in members),
            retried=retried,
            oldest=min(_when(m.processed_at) for m in members),
        )
        for (kind, retried), members in groups.items()
    ]
    return sorted(found, key=lambda g: (-g.count, g.oldest))


def error_kind(error: str | None) -> str:
    """``classification failed: <details>`` -> ``classification failed``.

    The details can hold a path or a file name, so only the words before the
    first colon are kept.
    """
    kind = (error or "").split(":", 1)[0].strip()
    return kind[:60] if kind else "unknown error"


def logged_skips(database: Path, run_id: int) -> dict[str, int] | None:
    """How many items the run skipped as unreadable or only in iCloud, from its log."""
    log = log_path(database, run_id)
    try:
        lines = log.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    return count_skips(lines)


def count_skips(lines: Iterable[str]) -> dict[str, int]:
    """How many lines of a run log are skips, per kind of ``_LOGGED_SKIPS``."""
    counts = dict.fromkeys(_LOGGED_SKIPS, 0)
    for line in lines:
        match = _SKIP_LINE.fullmatch(line)
        if match is None:
            continue
        for key, reason in _LOGGED_SKIPS.items():
            if match.group(1).startswith(reason.value):
                counts[key] += 1
    return counts


def next_run(now: datetime, inputs: list[Path] | None = None) -> NextRun:
    """The daily run's next start, from the installed agent (``None`` if not active)."""
    try:
        state = scheduler.status()
    except (scheduler.ScheduleError, OSError, ValueError) as exc:
        return NextRun(False, False, None, None, str(exc))
    at = None
    if state.installed and state.loaded and state.time is not None:
        hour, minute = (int(part) for part in state.time.split(":"))
        local = now.astimezone()
        at = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
        if at <= local:
            at += timedelta(days=1)
    return NextRun(
        state.installed,
        state.loaded,
        state.time,
        at,
        mode=state.mode,
        watched=len(state.watch_paths),
        watch_interval_minutes=state.watch_interval_minutes,
        stale_watch=None if inputs is None else scheduler.stale_watch_message(state, inputs),
    )


def _when(text: str) -> datetime:
    value = datetime.fromisoformat(text)
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


# --- showing ---------------------------------------------------------------------------


def render(
    status: Status, config: Config, *, now: datetime | None = None, private: bool = False
) -> list[str]:
    """``flib status`` as lines. ``private`` hides suggested categories that are not
    configured (model-written text), for ``flib doctor``."""
    now = now or datetime.now(UTC)
    lines: list[str] = []
    if not status.database_exists:
        lines.append("No run yet: the index is created by the first `flib run`.")
    else:
        lines.extend(_review_lines(status, config, now, private=private))
        lines.extend(_failed_lines(status, config, now))
        if status.due is not None:
            lines.append(due_mod.status_line(status.due))
        if status.sensitive:
            lines.append(f"Left in place as sensitive: {_files(status.sensitive)}")
        if status.duplicates_notice:
            lines.append(duplicates_notice(config))
        if status.duplicates:
            lines.append(
                f"Duplicates set aside in {config.duplicates_folder}: "
                f"{_files(status.duplicates)} (nothing is deleted; review them when you like)"
            )
        lines.extend(_input_lines(status, private=private))
        lines.extend(_last_run_lines(status, config))
    lines.extend(_schedule_lines(status.schedule))
    if status.notifier is not None and status.schedule.installed:
        lines.append(notifier_line(status.notifier))
    if status.menu_bar is not None:
        lines.append(status.menu_bar)
    if status.database_exists:
        lines.extend(_watch_lines(status, config))
        month = f"This month: US$ {status.month_cost_usd:.4f}"
        if status.month_unpriced_runs:
            month += f" (plus {status.month_unpriced_runs} run(s) without a recorded cost)"
        lines.append(month)
    return lines


def notifier_line(choice: notifier.Choice) -> str:
    if choice.kind == "app":
        return 'Notifications: from the "file-librarian" app, with its own icon.'
    return f"Notifications: through osascript, shown as Script Editor ({choice.reason})."


def _watch_lines(status: Status, config: Config) -> list[str]:
    """The last watch run and what automatic runs cost today (#90)."""
    lines = []
    watching = status.schedule.mode in ("watch", "both")
    run = status.last_watch
    if run is not None:
        lines.append(
            f"Last watch run: {_local(run.finished_at or run.started_at)}: "
            f"{run.files_moved or 0} organized, {run.files_review or 0} to review"
        )
    elif watching:
        lines.append("Last watch run: none yet.")
    if watching or status.schedule.installed or status.automatic_cost_today:
        if config.provider.cost_unknown:
            files = config.limits.files_cap_per_day
            of = "" if files is None else f" of {files}"
            lines.append(
                f"Automatic runs today: cost unknown (no prices for {config.provider.name}); "
                f"{status.automatic_files_today}{of} files seen"
            )
        else:
            cap = config.limits.cost_cap_per_day
            of = "" if cap is None else f" of US$ {cap:.2f}"
            lines.append(f"Automatic runs today: US$ {status.automatic_cost_today:.4f}{of}")
    if status.day_cap_reached:
        lines.append(day_cap_line(config))
    if status.backlog is not None:
        count, reason = status.backlog
        why = f" ({reason})" if reason else ""
        lines.append(
            f"Watch: {_files(count)} wait in the inputs{why}; a new arrival or the daily run "
            "tries them again, or run `flib run` now."
        )
    return lines


def note_backlog(store: Store, count: int, reason: str | None) -> None:
    """What the last watch start left (``scheduler.watch``); ``reason`` kept from before
    when this start ran nothing."""
    if count <= 0:
        store.set_meta(BACKLOG_KEY, "{}")
        return
    try:
        old = json.loads(store.meta(BACKLOG_KEY) or "{}")
    except ValueError:
        old = {}
    reason = reason or (old.get("reason") if isinstance(old, dict) else None)
    value = {"count": count, "reason": reason, "day": _today()}
    store.set_meta(BACKLOG_KEY, json.dumps(value))


def backlog_reason(outcome: object) -> str | None:
    """Why a watch run left ready items behind, from its outcome."""
    if outcome is None:
        return None
    report = getattr(outcome, "report", None)
    if getattr(outcome, "day_cap_reached", False):
        return "daily cap reached"
    if report is not None and report.errors:
        return "provider failing"
    if getattr(outcome, "deferred", 0):
        return "over max_files_per_run or the per-run cost cap"
    return None


def _backlog(store: Store) -> tuple[int, str | None] | None:
    try:
        value = json.loads(store.meta(BACKLOG_KEY) or "{}")
    except ValueError:
        return None
    if not isinstance(value, dict) or value.get("day") != _today() or not value.get("count"):
        return None
    reason = value.get("reason")
    return int(value["count"]), str(reason) if reason else None


def cost_cap_lines(config: Config) -> list[str]:
    """The caps on what automatic runs spend, for ``check-config``, ``doctor`` and
    ``flib schedule``; a line starting with ``warning`` when no cost cap can work."""
    cap = config.limits.cost_cap_per_day
    lines = [
        "daily cost cap for automatic runs: "
        + ("none" if cap is None else f"US$ {cap:.2f} (limits.max_cost_usd_per_day)")
    ]
    if config.provider.cost_unknown:
        files = config.limits.files_cap_per_day
        limit = (
            "nothing limits them"
            if files is None
            else f"automatic runs see at most {files} files a day (limits.max_files_per_day)"
        )
        lines.append(
            f"warning: {config.provider.name} at a remote server has no prices "
            "(provider.price_input_per_mtok, price_output_per_mtok), so no cost cap can stop "
            f"it; {limit}."
        )
    return lines


def _review_lines(status: Status, config: Config, now: datetime, *, private: bool) -> list[str]:
    folder = config.review_folder
    oldest = status.oldest_review
    if oldest is None:
        return [f"Review: nothing waits in {folder}."]
    lines = [
        f"Review: {_files(status.review_total)} in {folder}, the oldest for {_age(now - oldest)}"
    ]
    configured = {c.name for c in config.categories}
    for group in status.review:
        suggested = group.suggested
        if private and suggested is not None and suggested not in configured:
            suggested = "<other>"
        why = explain_review(group.reason, suggested, None)
        lines.append(f"  {group.count:>3}  {why}")
    return lines


def _input_lines(status: Status, *, private: bool) -> list[str]:
    """Files per input folder; ``private`` names them ``<input-N>`` (``flib doctor``)."""
    if not status.by_input:
        return []
    lines = ["By input:"]
    for count in status.by_input:
        name = f"<input-{count.index}>" if private else str(count.folder)
        lines.append(f"  {name}: {_files(count.filed)} filed, {count.left} left in place")
    return lines


def _failed_lines(status: Status, config: Config, now: datetime) -> list[str]:
    if not status.failed:
        return []
    lines = [f"Failed: {_files(status.failed_total)}"]
    limit = config.limits.max_attempts
    for group in status.failed:
        if group.retried:
            note = f"{group.attempts} of {limit} attempts; the next run tries again"
        else:
            note = f"gave up after {group.attempts} attempts"
        lines.append(f"  {group.count:>3}  {group.error} ({note}; for {_age(now - group.oldest)})")
    return lines


def _last_run_lines(status: Status, config: Config) -> list[str]:
    run = status.last_run
    if run is None:
        return ["Last run: none yet."]
    when = _local(run.finished_at or run.started_at)
    names = {
        "schedule": "scheduled",
        "manual": "by hand",
        "mcp": "from an assistant",
        "watch": "on arrival (watch mode)",
    }
    trigger = names.get(run.trigger.value, run.trigger.value)
    cost = "no cost recorded" if run.cost_usd is None else f"US$ {run.cost_usd:.4f}"
    line = (
        f"Last run: {when}, {trigger}: {run.files_moved or 0} organized, "
        f"{run.files_review or 0} to review, "
        + (f"{run.files_duplicates} duplicate(s) set aside, " if run.files_duplicates else "")
        + f"{run.errors or 0} failed; {cost}"
    )
    lines = [line]
    skipped = status.skipped or {}
    parts = []
    if skipped.get("unreadable"):
        parts.append(f"{skipped['unreadable']} unreadable")
    if skipped.get("cloud_only"):
        parts.append(f"{skipped['cloud_only']} only in iCloud")
    if skipped.get("not_where_left"):
        parts.append(
            f"{skipped['not_where_left']} waiting for `flib reconcile` "
            "(copies of filed files that are not where flib left them)"
        )
    if skipped.get("locked"):
        parts.append(f"{skipped['locked']} protected while the Mac was locked")
    if skipped.get("input_unreadable"):
        parts.append(f"{skipped['input_unreadable']} input folder(s) that could not be read")
    if skipped.get("too_recent"):
        parts.append(f"{skipped['too_recent']} too recent for their input's min_age_days")
    if parts:
        lines.append(f"  skipped: {', '.join(parts)} (see its log)")
    if skipped.get("locked"):
        lines.append(f"  {locked_advice(skipped['locked'], config)}")
    return lines


def locked_advice(count: int, config: Config) -> str:
    """What to do about files skipped because the Mac was locked (no names)."""
    what = f"{_files(count)} could only be read while the Mac is unlocked (macOS Data Protection)"
    if config.schedule.wait_for_unlock_hours > 0:
        return f"{what}; the scheduled run waits for the next unlock, or run `flib run` now."
    return f"{what}; run `flib run` now."


def _schedule_lines(schedule: NextRun) -> list[str]:
    if schedule.error is not None:
        return [f"Next run: unknown ({schedule.error})"]
    if not schedule.installed:
        return ["Next run: none scheduled (`flib schedule` sets up a daily run)."]
    lines = []
    if schedule.mode in ("watch", "both"):
        state = "on" if schedule.loaded else "installed but not loaded (run `flib schedule` again)"
        lines.append(
            f"Watch mode: {state}; flib runs when something arrives in "
            f"{_folders(schedule.watched)}, at most once every "
            f"{schedule.watch_interval_minutes} min"
        )
        if schedule.stale_watch:
            lines.append(f"warning: {schedule.stale_watch}")
    if schedule.mode == "watch":
        return lines
    if schedule.at is None:
        lines.append(
            "Next run: the daily run is installed but not loaded (run `flib schedule` again)."
        )
    else:
        lines.append(
            f"Next run: {schedule.at.strftime('%Y-%m-%d %H:%M')} (daily at {schedule.time})"
        )
    return lines


def _folders(count: int) -> str:
    return "the input folder" if count == 1 else f"the {count} input folders"


def to_json(status: Status, config: Config, *, now: datetime | None = None) -> str:
    """``flib status --json``: the same facts, ages in whole days, times in ISO 8601."""
    now = now or datetime.now(UTC)
    oldest = status.oldest_review
    run = status.last_run
    data = {
        "database_exists": status.database_exists,
        "review": {
            "folder": config.review_folder,
            "total": status.review_total,
            "oldest_days": None if oldest is None else (now - oldest).days,
            "groups": [
                {
                    "reason": None if g.reason is None else g.reason.value,
                    "suggested_category": g.suggested,
                    "explanation": g.explanation,
                    "count": g.count,
                    "oldest_days": (now - g.oldest).days,
                }
                for g in status.review
            ],
        },
        "failed": {
            "total": status.failed_total,
            "max_attempts": config.limits.max_attempts,
            "groups": [
                {
                    "error": g.error,
                    "count": g.count,
                    "attempts": g.attempts,
                    "retried": g.retried,
                    "oldest_days": (now - g.oldest).days,
                }
                for g in status.failed
            ],
        },
        "sensitive": status.sensitive,
        "due": None
        if status.due is None
        else {
            "within_days": status.due.within_days,
            "upcoming": status.due.upcoming,
            "overdue": status.due.overdue,
        },
        "by_input": None
        if status.by_input is None
        else [
            {"input": str(c.folder), "filed": c.filed, "left_in_place": c.left}
            for c in status.by_input
        ],
        "duplicates": {
            "folder": config.duplicates_folder,
            "total": status.duplicates,
            "notice_pending": status.duplicates_notice,
        },
        "last_run": None
        if run is None
        else {
            "run_id": run.id,
            "trigger": run.trigger.value,
            "started_at": run.started_at,
            "finished_at": run.finished_at,
            "files_seen": run.files_seen,
            "files_moved": run.files_moved,
            "files_review": run.files_review,
            "files_duplicates": run.files_duplicates,
            "errors": run.errors,
            "cost_usd": run.cost_usd,
            "skipped": status.skipped,
        },
        "schedule": {
            "installed": status.schedule.installed,
            "loaded": status.schedule.loaded,
            "mode": status.schedule.mode,
            "time": status.schedule.time,
            "next_run": None if status.schedule.at is None else status.schedule.at.isoformat(),
            "watched_folders": status.schedule.watched,
            "watch_min_interval_minutes": status.schedule.watch_interval_minutes,
            "last_watch_run": None
            if status.last_watch is None
            else {
                "run_id": status.last_watch.id,
                "finished_at": status.last_watch.finished_at,
                "files_moved": status.last_watch.files_moved,
            },
        },
        "today": {
            "automatic_cost_usd": status.automatic_cost_today,
            "max_cost_usd_per_day": config.limits.cost_cap_per_day,
            "cap_reached": status.day_cap_reached,
        },
        "month": {
            "cost_usd": status.month_cost_usd,
            "runs_with_unknown_cost": status.month_unpriced_runs,
        },
        "summary": {
            "notify": config.summary.notify,
            "notifier": None
            if status.notifier is None
            else {"kind": status.notifier.kind, "reason": status.notifier.reason},
            "every_days": config.summary.every_days,
            "min_age_days": config.summary.min_age_days,
            "last_at": None if status.last_summary is None else status.last_summary.isoformat(),
        },
    }
    return json.dumps(data, indent=2, ensure_ascii=False)


def _files(count: int) -> str:
    return f"{count} file" if count == 1 else f"{count} files"


def _age(delta: timedelta) -> str:
    days = delta.days
    if days < 1:
        return "less than a day"
    return "1 day" if days == 1 else f"{days} days"


def _local(text: str) -> str:
    return _when(text).astimezone().strftime("%Y-%m-%d %H:%M")


# --- the periodic summary --------------------------------------------------------------


def summary_text(
    review: int, failed: int, not_where_left: int = 0, locked: int = 0, due: str | None = None
) -> str:
    """The notification's body: counts only, nothing that names a file. ``due``: what is
    due, in counts (``due.Reminder.text``)."""
    parts = []
    if due:
        parts.append(due)
    if review:
        parts.append(f"{_files(review)} {'waits' if review == 1 else 'wait'} for review")
    if failed:
        parts.append(f"{failed} failed")
    if not_where_left:
        parts.append(f"{not_where_left} wait for `flib reconcile`")
    if locked:
        parts.append(f"{locked} could only be read while the Mac is unlocked")
    return f"{', '.join(parts)}. Run `flib status` for details."


def after_scheduled_run(
    config: Config, *, now: datetime | None = None, not_where_left: int = 0, locked: int = 0
) -> list[str]:
    """The periodic summary, when one is due: lines for the run log (else none).

    Due when some file has waited in review or failed for ``min_age_days``, or
    the run skipped ``not_where_left`` copies of filed files that are not where
    flib left them (RF-21), and the last summary was ``every_days`` or more
    calendar days ago. Files skipped as ``locked`` (readable only while the
    Mac is unlocked) are counted in it; they make it due on their own only
    when the scheduled run does not wait for an unlock
    (``schedule.wait_for_unlock_hours: 0``). The time is
    recorded before notifying, so a notification that fails is not retried
    on every run.

    With ``due.remind``, it also counts the open documents due within
    ``due.remind_days_before`` days or overdue (#91, see ``due``). One that no
    earlier summary counted makes the summary due on its own, whatever
    ``every_days``; it is then recorded, so each document is reminded once.
    Such a reminder alone holds only the due counts and does not restart
    ``every_days``.
    """
    settings = config.summary
    due_today = due_mod.local_today(now or due_mod.clock())
    now = now or datetime.now(UTC)
    with Store.open(config.database) as store:
        waiting = store.waiting()
        cutoff = now - timedelta(days=settings.min_age_days)
        stuck = not_where_left or (locked and config.schedule.wait_for_unlock_hours == 0)
        waited = bool(stuck) or any(_when(row.processed_at) <= cutoff for row in waiting)
        if waited and (last := store.meta(SUMMARY_KEY)) is not None:
            days = (now.astimezone().date() - _when(last).astimezone().date()).days
            waited = days >= settings.every_days
        remind = None
        if config.due.remind:
            remind = due_mod.reminder(store, config, due_today)
        if not waited and (remind is None or not remind.new):
            return []
        with store.transaction():
            if waited:
                store.set_meta(SUMMARY_KEY, now.astimezone(UTC).isoformat(timespec="seconds"))
            if remind is not None:
                store.set_reminded(remind.new)
    due_text = None if remind is None else remind.text(config.due.remind_days_before)
    if not waited:
        waiting, not_where_left, locked = [], 0, 0
    review = sum(1 for row in waiting if row.status is FileStatus.REVIEW)
    text = summary_text(review, len(waiting) - review, not_where_left, locked, due_text)
    lines = [f"Summary: {text}"]
    if locked:
        lines.append(locked_advice(locked, config))
    if settings.notify:
        problem = notify(text)
        if problem is not None:
            lines.append(
                f"warning: the notification could not be shown ({problem}); "
                "this log and `flib status` have the summary"
            )
    return lines


def notify(text: str, title: str = NOTIFICATION_TITLE) -> str | None:
    """Post a macOS notification; returns why it failed, or ``None``. Never raises.

    Through the notifier app when it is ready; through ``osascript`` when it is not, or
    when launching it failed."""
    if notifier.state() is None and notifier.post(text, title) is None:
        return None
    return _notify_osascript(text, title)


def _notify_osascript(text: str, title: str) -> str | None:
    try:
        # A fixed binary and script; the text goes in as arguments (argv), never as code.
        result = subprocess.run(  # noqa: S603
            [OSASCRIPT, *_SCRIPT, "--", text, title],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return str(exc) or type(exc).__name__
    if result.returncode != 0:
        return result.stderr.strip()[:200] or f"osascript exited with {result.returncode}"
    return None

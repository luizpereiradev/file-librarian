"""What is due: ``flib due``, its line in ``flib status`` and its reminder (#91).

A document is *due* when the AI read a due date in it (``files.due_date``,
#88). Nothing is ever guessed: a document without a stated due date is never
listed. Only filed documents count (organized or in review), never a
duplicate, and never a sensitive file (it is left in place, unread).

Which documents are listed:

- Kinds: those in ``due.kinds`` (bills, invoices and taxes by default). A due
  date on another kind, or on a document whose kind is unknown, is shown only
  with ``--all``.
- Done: a document marked paid or done (``flib fix PATH --done``, journaled,
  so ``flib undo`` reverts it; or the Finder tag ``due.done_tag``, read when
  listing) is left out unless ``--all``. Totals count open documents only.
- The window, in calendar days of the local time zone: by default from 30
  days ago (overdue) to 7 days ahead; ``--within 2w`` moves the far end
  (``d`` days, ``w`` weeks, ``m`` calendar months), ``--overdue`` lists every
  open document whose due date has passed, however long ago. A document due
  today is not overdue.

"Today" always comes from ``local_today``: the date of ``clock()`` in the
local time zone (or ``tz``), so a due date is "in 1 day" from midnight to
midnight, whatever the time of day and across daylight-saving changes. Tests
swap ``clock``.

The reminder (``reminder``) runs after a scheduled run, inside the periodic
summary (``status.after_scheduled_run``): an open document of ``due.kinds``
due within ``due.remind_days_before`` days, or overdue within the window, is
counted in it once. Its due date is recorded with it (``due_reminded_for``),
so the same document never makes another summary due on its own; a new due
date (a reprocess) may be reminded once more. The notification holds counts
only: no names, amounts or issuers.

Everything here only reads the index, except ``settle`` (``flib fix --done``)
and ``Store.set_reminded`` (the summary).
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta, tzinfo
from pathlib import Path

from file_librarian.config import Config
from file_librarian.db.store import FileRow, Store, load_migrations
from file_librarian.fields import KINDS, format_amount, format_decimal
from file_librarian.finder_tags import read_tags, tag_name
from file_librarian.models import FileStatus, RunMode, RunTrigger
from file_librarian.naming import issuer_key

LOOKBACK_DAYS = 30
"""How far back the default window reaches for overdue documents."""
DEFAULT_DAYS = 7
"""How far ahead the default window reaches."""
MIN_SCHEMA = 17
"""The first schema with ``files.settled_at``."""

_SPAN = re.compile(r"(\d{1,3})\s*([dwm]?)")
_SETTLEABLE = {FileStatus.ORGANIZED, FileStatus.REVIEW}


def _system_clock() -> datetime:
    return datetime.now(UTC)


clock: Callable[[], datetime] = _system_clock
"""Now, as an aware datetime; tests swap it."""


class DueError(ValueError):
    """A bad option, or an index this flib cannot read; nothing was changed."""


def local_today(now: datetime | None = None, tz: tzinfo | None = None) -> date:
    """Today's date in the local time zone (or ``tz``)."""
    moment = now if now is not None else clock()
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(tz).date()


def add_months(day: date, months: int) -> date:
    """``day`` that many calendar months later; the 31st becomes the month's last day."""
    index = day.year * 12 + day.month - 1 + months
    year, month = divmod(index, 12)
    month += 1
    for last in (31, 30, 29, 28):
        try:
            return date(year, month, min(day.day, last))
        except ValueError:
            continue
    raise AssertionError("unreachable")  # pragma: no cover


@dataclass(frozen=True, slots=True)
class Span:
    """How far ahead the window reaches: ``count`` days, weeks or calendar months."""

    count: int
    unit: str
    """``d``, ``w`` or ``m``."""

    def end(self, today: date) -> date:
        if self.unit == "m":
            return add_months(today, self.count)
        return today + timedelta(days=self.count * (7 if self.unit == "w" else 1))

    def __str__(self) -> str:
        words = {"d": "day", "w": "week", "m": "month"}
        word = words[self.unit]
        return f"{self.count} {word}" if self.count == 1 else f"{self.count} {word}s"


def parse_within(text: str) -> Span:
    """``7d``, ``2w``, ``1m`` (or a bare number of days); raises ``DueError``."""
    match = _SPAN.fullmatch(text.strip().casefold())
    if match is None or int(match.group(1)) == 0:
        raise DueError(f"--within {text!r} is not a span like 7d, 2w or 1m")
    count = int(match.group(1))
    unit = match.group(2) or "d"
    limit = {"d": 366, "w": 53, "m": 12}[unit]
    if count > limit:
        raise DueError(f"--within {text!r} reaches more than a year ahead")
    return Span(count, unit)


@dataclass(frozen=True, slots=True)
class Query:
    """Which documents to list, validated (``make_query``)."""

    within: Span = Span(DEFAULT_DAYS, "d")
    overdue_only: bool = False
    include_all: bool = False
    """Also documents marked done and due dates on kinds outside ``due.kinds``."""
    issuer_key: str | None = None
    kinds: tuple[str, ...] = ()
    """Only these kinds (else ``due.kinds``, or every kind with ``include_all``)."""

    def window(self, today: date) -> tuple[date | None, date]:
        """The first and last due dates listed, both inclusive (``None``: no limit)."""
        if self.overdue_only:
            return None, today - timedelta(days=1)
        return today - timedelta(days=LOOKBACK_DAYS), self.within.end(today)


def make_query(
    *,
    within: str | None = None,
    overdue: bool = False,
    include_all: bool = False,
    issuer: str | None = None,
    kinds: Iterable[str] | None = None,
) -> Query:
    """Validate raw options; raises ``DueError`` on a bad one."""
    if overdue and within is not None:
        raise DueError("--overdue lists what is past due; leave out --within")
    span = parse_within(within) if within is not None else Span(DEFAULT_DAYS, "d")
    key = None
    if issuer is not None:
        key = issuer_key(issuer)
        if not key:
            raise DueError(f"--issuer {issuer!r} names no organisation")
    chosen: list[str] = []
    for raw in kinds or ():
        for part in raw.split(","):
            kind = part.strip().casefold()
            if not kind:
                continue
            if kind not in KINDS:
                raise DueError(f"unknown kind {part.strip()!r} (kinds: {', '.join(KINDS)})")
            if kind not in chosen:
                chosen.append(kind)
    return Query(span, overdue, include_all, key, tuple(chosen))


@dataclass(frozen=True, slots=True)
class DueItem:
    file_id: int
    path: Path
    exists: bool
    due_date: date
    days: int
    """Calendar days from today: negative when overdue, 0 when due today."""
    amount_minor: int | None
    currency: str | None
    issuer: str | None
    kind: str | None
    status: str
    category: str | None
    done_by: str | None
    """``fix`` (``flib fix --done``) or ``tag`` (the Finder tag ``due.done_tag``);
    ``None`` while open."""
    listed_kind: bool
    """Its kind is one ``due.kinds`` lists."""

    @property
    def done(self) -> bool:
        return self.done_by is not None

    @property
    def overdue(self) -> bool:
        return self.days < 0 and not self.done

    @property
    def amount(self) -> str | None:
        if self.amount_minor is None or self.currency is None:
            return None
        return format_decimal(self.amount_minor, self.currency)

    def to_dict(self) -> dict[str, object]:
        return {
            "path": str(self.path),
            "exists": self.exists,
            "due_date": self.due_date.isoformat(),
            "days": self.days,
            "relative": relative(self.days),
            "overdue": self.overdue,
            "amount": self.amount,
            "currency": self.currency,
            "issuer": self.issuer,
            "kind": self.kind,
            "status": self.status,
            "category": self.category,
            "done": self.done,
            "done_by": self.done_by,
        }


@dataclass(frozen=True, slots=True)
class Total:
    currency: str
    total_minor: int
    files: int

    def to_dict(self) -> dict[str, object]:
        return {
            "currency": self.currency,
            "total": format_decimal(self.total_minor, self.currency),
            "files": self.files,
        }


@dataclass(frozen=True, slots=True)
class DueList:
    today: date
    start: date | None
    end: date
    items: list[DueItem] = field(default_factory=list)
    hidden: int = 0
    """Documents in the window left out: marked done, or of a kind outside
    ``due.kinds`` (``--all`` shows them)."""
    without_amount: int = 0
    """Open documents listed that state no amount (not in the totals)."""

    @property
    def totals(self) -> list[Total]:
        """Per currency over the open documents listed, largest first; never mixed."""
        sums: dict[str, list[int]] = {}
        for item in self.items:
            if item.done or item.amount_minor is None or item.currency is None:
                continue
            entry = sums.setdefault(item.currency, [0, 0])
            entry[0] += item.amount_minor
            entry[1] += 1
        found = [Total(c, total, n) for c, (total, n) in sums.items()]
        return sorted(found, key=lambda t: (-t.total_minor, t.currency))

    def to_dict(self) -> dict[str, object]:
        return {
            "today": self.today.isoformat(),
            "from": None if self.start is None else self.start.isoformat(),
            "to": self.end.isoformat(),
            "items": [item.to_dict() for item in self.items],
            "totals": [t.to_dict() for t in self.totals],
            "hidden": self.hidden,
            "without_amount": self.without_amount,
        }


def relative(days: int) -> str:
    """``in 3 days``, ``today``, ``yesterday``, ``2 days ago``."""
    if days == 0:
        return "today"
    if days == 1:
        return "tomorrow"
    if days == -1:
        return "yesterday"
    return f"in {days} days" if days > 0 else f"{-days} days ago"


# --- reading ---------------------------------------------------------------------------


def _due(row: FileRow) -> date | None:
    try:
        return date.fromisoformat(row.due_date) if row.due_date else None
    except ValueError:
        return None


def _tagged(path: Path, tag: str) -> bool:
    """The file has the Finder tag ``tag`` (any case). Never raises."""
    try:
        entries = read_tags(path)
    except (OSError, ValueError):
        return False
    return any(tag_name(e).casefold() == tag.casefold() for e in entries)


def _item(row: FileRow, due: date, today: date, config: Config) -> DueItem:
    done_by = None
    if row.settled_at is not None:
        done_by = "fix"
    elif config.due.done_tag is not None and _tagged(row.current_path, config.due.done_tag):
        done_by = "tag"
    return DueItem(
        file_id=row.id,
        path=row.current_path,
        exists=row.current_path.exists(),
        due_date=due,
        days=(due - today).days,
        amount_minor=row.amount_minor,
        currency=row.currency,
        issuer=row.issuer,
        kind=row.kind,
        status=row.status.value,
        category=row.category,
        done_by=done_by,
        listed_kind=row.kind in config.due.kinds,
    )


def select(rows: Sequence[FileRow], config: Config, query: Query, today: date) -> DueList:
    """The documents ``query`` lists among ``rows``, soonest first."""
    start, end = query.window(today)
    items: list[DueItem] = []
    hidden = 0
    for row in rows:
        due = _due(row)
        if due is None or due > end or (start is not None and due < start):
            continue
        if query.issuer_key is not None and issuer_key(row.issuer or "") != query.issuer_key:
            continue
        if query.kinds and row.kind not in query.kinds:
            continue
        item = _item(row, due, today, config)
        # Kinds named on the command line are what the user asked for, listed or not.
        wanted_kind = bool(query.kinds) or item.listed_kind
        if not query.include_all and (item.done or not wanted_kind):
            hidden += 1
            continue
        items.append(item)
    items.sort(key=lambda i: (i.due_date, str(i.path)))
    missing = sum(1 for i in items if not i.done and i.amount is None)
    return DueList(today, start, end, items, hidden, missing)


def check_schema(store: Store) -> None:
    """Raise ``DueError`` for an index this flib cannot read for ``flib due``."""
    version = store.schema_version
    if version > len(load_migrations()):
        raise DueError(
            f"the index (schema {version}) is newer than this flib; upgrade file-librarian"
        )
    if version < MIN_SCHEMA:
        raise DueError("the index is from an older flib; the next `flib run` updates it")


def find(config: Config, query: Query, *, today: date | None = None) -> DueList:
    """``flib due``: read-only and lock-free; works before the first run."""
    today = today or local_today()
    if not config.database.exists():
        start, end = query.window(today)
        return DueList(today, start, end)
    try:
        with Store.open_read_only(config.database) as store:
            check_schema(store)
            rows = store.due_files()
    except sqlite3.Error as exc:
        raise DueError(f"cannot read the index: {exc}") from exc
    return select(rows, config, query, today)


@dataclass(frozen=True, slots=True)
class DueCounts:
    """Open documents of ``due.kinds`` in the default window, for ``flib status``."""

    within_days: int
    upcoming: int
    """Due from today to ``within_days`` days ahead."""
    overdue: int
    """Past due, up to ``LOOKBACK_DAYS`` ago."""


def counts(store: Store, config: Config, today: date) -> DueCounts:
    found = select(store.due_files(), config, Query(), today)
    overdue = sum(1 for i in found.items if i.days < 0)
    return DueCounts(DEFAULT_DAYS, len(found.items) - overdue, overdue)


def status_line(found: DueCounts) -> str:
    """``Due: 3 documents within 7 days, 1 overdue (`flib due`)``."""
    if not found.upcoming and not found.overdue:
        return f"Due: nothing within {found.within_days} days."
    parts = [f"{_documents(found.upcoming)} within {found.within_days} days"]
    if found.overdue:
        parts.append(f"{found.overdue} overdue")
    return f"Due: {', '.join(parts)} (`flib due`)."


def _documents(count: int) -> str:
    return f"{count} document" if count == 1 else f"{count} documents"


# --- the reminder ----------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Reminder:
    """What a summary says about due documents, and whether it makes one due."""

    soon: int
    """Open documents due from today to ``remind_days_before`` days ahead."""
    overdue: int
    new: list[tuple[int, str]]
    """``(file id, due date)`` of those no earlier summary counted: they make the
    summary due on their own, and are recorded once it is written."""

    def text(self, days_before: int) -> str | None:
        """Counts only, for a notification: no names, amounts or issuers."""
        if not self.soon and not self.overdue:
            return None
        when = "today" if days_before == 0 else f"within {days_before} days"
        parts = []
        if self.soon:
            verb = "is" if self.soon == 1 else "are"
            parts.append(f"{_documents(self.soon)} {verb} due {when}")
        if self.overdue:
            parts.append(f"{_documents(self.overdue)} overdue")
        return ", ".join(parts)


def reminder(store: Store, config: Config, today: date) -> Reminder:
    """Open documents of ``due.kinds`` due within ``remind_days_before`` days or overdue
    (up to ``LOOKBACK_DAYS`` ago)."""
    query = Query(within=Span(config.due.remind_days_before, "d"))
    rows = {row.id: row for row in store.due_files()}
    found = select(list(rows.values()), config, query, today)
    overdue = sum(1 for i in found.items if i.days < 0)
    new = [
        (i.file_id, i.due_date.isoformat())
        for i in found.items
        if rows[i.file_id].due_reminded_for != i.due_date.isoformat()
    ]
    return Reminder(len(found.items) - overdue, overdue, new)


# --- marking as done -------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class SettleReport:
    run_id: int
    path: Path
    due_date: str
    done: bool


def settle(
    store: Store, path: Path, *, done: bool, trigger: RunTrigger = RunTrigger.MANUAL
) -> SettleReport:
    """Mark one filed document paid or done (``done``), or open again.

    Journaled as its own run (``index_changes``), so ``flib undo`` reverts it.
    Raises ``DueError`` when there is nothing to change.
    """
    row = store.file_by_current_path(path.expanduser().absolute())
    if row is None:
        raise DueError(f"{path} is not a file flib organized (see `flib search`)")
    if row.status not in _SETTLEABLE:
        raise DueError(f"{path} is {row.status.value}; only organized or review files have a due")
    if row.due_date is None:
        raise DueError(f"{path} states no due date, so it is never listed as due")
    if done and row.settled_at is not None:
        raise DueError(f"{path} is already marked as done")
    if not done and row.settled_at is None:
        raise DueError(f"{path} is not marked as done")
    new = clock().astimezone(UTC).isoformat(timespec="seconds") if done else None
    run_id = store.start_run(RunMode.RUN, trigger)
    with store.transaction():
        store.change_index(run_id, "files.settled_at", row.id, row.settled_at, new)
        store.finish_run(run_id, seen=1, moved=0, review=0, errors=0, cost_usd=0.0)
    return SettleReport(run_id, row.current_path, row.due_date, done)


# --- showing ---------------------------------------------------------------------------


def render(found: DueList, *, show: Callable[[Path], str] = str) -> list[str]:
    """``flib due`` as lines; ``show`` shortens a path for the terminal."""
    since = "any time" if found.start is None else found.start.isoformat()
    span = f"from {since} to {found.end.isoformat()} (today is {found.today.isoformat()})"
    lines: list[str] = []
    if not found.items:
        lines.append(f"Nothing due {span}.")
    else:
        lines.append(f"Due {span}:")
        for item in found.items:
            lines.extend(_item_lines(item, show))
        totals = found.totals
        if totals:
            overall = "; ".join(
                f"{format_amount(t.total_minor, t.currency)} ({_documents(t.files)})"
                for t in totals
            )
            lines.append(f"Total open: {overall}")
        if found.without_amount:
            lines.append(f"{_documents(found.without_amount)} state no amount; not counted.")
    if found.hidden:
        lines.append(
            f"{_documents(found.hidden)} marked done or of another kind not shown "
            "(--all shows them)."
        )
    return lines


def _item_lines(item: DueItem, show: Callable[[Path], str]) -> list[str]:
    when = relative(item.days)
    if item.done:
        when += ", done" if item.done_by == "fix" else ", done (Finder tag)"
    elif item.overdue:
        when += ", overdue"
    amount = "-"
    if item.amount_minor is not None and item.currency is not None:
        amount = format_amount(item.amount_minor, item.currency)
    parts = [item.due_date.isoformat(), f"({when})", amount, item.issuer or "-", item.kind or "-"]
    where = show(item.path)
    if not item.exists:
        where += "  (missing: moved by hand?)"
    elif item.status == FileStatus.REVIEW.value:
        where += "  (in review)"
    return ["  " + "  ".join(parts), f"    {where}"]

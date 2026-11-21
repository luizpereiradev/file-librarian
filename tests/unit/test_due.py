"""What is due (#91): windows, days counted in the local time zone, wording."""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from file_librarian import due
from file_librarian.config import ConfigError, parse_config

SAO_PAULO = ZoneInfo("America/Sao_Paulo")
NEW_YORK = ZoneInfo("America/New_York")
TODAY = date(2026, 11, 20)


@pytest.mark.parametrize(
    ("text", "end"),
    [
        ("7d", date(2026, 11, 27)),
        ("7", date(2026, 11, 27)),
        (" 10D ", date(2026, 11, 30)),
        ("2w", date(2026, 12, 4)),
        ("1m", date(2026, 12, 20)),
        ("3m", date(2027, 2, 20)),
        ("12m", date(2027, 11, 20)),
        ("366d", date(2027, 11, 21)),
    ],
)
def test_parse_within(text: str, end: date) -> None:
    assert due.parse_within(text).end(TODAY) == end


@pytest.mark.parametrize("text", ["", "0d", "w", "7x", "-3d", "1y", "13m", "54w", "400d", "1.5w"])
def test_parse_within_rejects(text: str) -> None:
    with pytest.raises(due.DueError):
        due.parse_within(text)


def test_span_wording() -> None:
    assert str(due.Span(1, "d")) == "1 day"
    assert str(due.Span(2, "w")) == "2 weeks"
    assert str(due.Span(3, "m")) == "3 months"


def test_a_month_later_keeps_the_day_or_takes_the_last_one() -> None:
    assert due.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert due.add_months(date(2028, 1, 31), 1) == date(2028, 2, 29)
    assert due.add_months(date(2026, 3, 31), 1) == date(2026, 4, 30)
    assert due.add_months(date(2026, 12, 15), 1) == date(2027, 1, 15)
    assert due.add_months(date(2026, 8, 31), 6) == date(2027, 2, 28)


def test_the_default_window_reaches_30_days_back_and_7_ahead() -> None:
    assert due.Query().window(TODAY) == (date(2026, 10, 21), date(2026, 11, 27))
    assert due.Query(overdue_only=True).window(TODAY) == (None, date(2026, 11, 19))
    query = due.make_query(within="2w")
    assert query.window(TODAY) == (date(2026, 10, 21), date(2026, 12, 4))


def test_make_query_validates() -> None:
    query = due.make_query(issuer="ACME, Inc.", kinds=["Bill,tax", "bill"], include_all=True)
    assert query.issuer_key == "acme" and query.kinds == ("bill", "tax") and query.include_all
    with pytest.raises(due.DueError, match="leave out --within"):
        due.make_query(overdue=True, within="7d")
    with pytest.raises(due.DueError, match="unknown kind"):
        due.make_query(kinds=["bills-ish"])
    with pytest.raises(due.DueError, match="names no organisation"):
        due.make_query(issuer="  ...  ")


def test_relative_wording() -> None:
    assert [due.relative(d) for d in (-3, -1, 0, 1, 4)] == [
        "3 days ago",
        "yesterday",
        "today",
        "tomorrow",
        "in 4 days",
    ]


# --- "today" in the local time zone ------------------------------------------------------


@pytest.mark.parametrize(
    ("utc", "today"),
    [
        # 23:59 in São Paulo (UTC-3) is already the next day in UTC.
        (datetime(2026, 11, 21, 2, 59, tzinfo=UTC), date(2026, 11, 20)),
        (datetime(2026, 11, 21, 3, 1, tzinfo=UTC), date(2026, 11, 21)),
    ],
)
def test_today_turns_at_local_midnight(utc: datetime, today: date) -> None:
    assert due.local_today(utc, SAO_PAULO) == today


@pytest.mark.parametrize(
    ("local", "days"),
    [
        # New York leaves daylight saving on 2026-11-01 (that day lasts 25 hours) and
        # enters it on 2026-03-08 (23 hours): still one calendar day each.
        (datetime(2026, 10, 31, 23, 30, tzinfo=NEW_YORK), 2),
        (datetime(2026, 11, 1, 0, 30, tzinfo=NEW_YORK), 1),
        (datetime(2026, 11, 1, 23, 59, tzinfo=NEW_YORK), 1),
        (datetime(2026, 11, 2, 0, 0, tzinfo=NEW_YORK), 0),
    ],
)
def test_days_are_calendar_days_across_daylight_saving(local: datetime, days: int) -> None:
    today = due.local_today(local.astimezone(UTC), NEW_YORK)
    assert (date(2026, 11, 2) - today).days == days


def test_spring_forward_day_is_one_day() -> None:
    before = due.local_today(datetime(2026, 3, 8, 1, 59, tzinfo=NEW_YORK), NEW_YORK)
    after = due.local_today(datetime(2026, 3, 8, 3, 0, tzinfo=NEW_YORK), NEW_YORK)
    late = due.local_today(datetime(2026, 3, 8, 23, 59, tzinfo=NEW_YORK), NEW_YORK)
    assert before == after == late == date(2026, 3, 8)


def test_today_comes_from_the_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(due, "clock", lambda: datetime(2026, 11, 20, 12, 0, tzinfo=UTC))
    assert due.local_today(tz=UTC) == date(2026, 11, 20)
    # A naive time is taken as UTC.
    assert due.local_today(datetime(2026, 11, 20, 12, 0), UTC) == date(2026, 11, 20)


# --- configuration -----------------------------------------------------------------------


def test_due_config_defaults_and_checks() -> None:
    cfg = parse_config({"provider": {"name": "fake", "model": "x"}})
    assert cfg.due.kinds == ["bill", "invoice", "statement", "tax"]
    assert cfg.due.remind and cfg.due.remind_days_before == 3 and cfg.due.done_tag is None
    cfg = parse_config(
        {"provider": {"name": "fake", "model": "x"}, "due": {"done_tag": " Paid ", "kinds": []}}
    )
    assert cfg.due.done_tag == "Paid" and cfg.due.kinds == []
    for bad in ({"done_tag": "  "}, {"kinds": ["bills"]}, {"remind_days_before": -1}):
        with pytest.raises(ConfigError, match="due"):
            parse_config({"provider": {"name": "fake", "model": "x"}, "due": bad})


def test_reminder_wording() -> None:
    assert due.Reminder(0, 0, []).text(3) is None
    assert due.Reminder(1, 0, []).text(3) == "1 document is due within 3 days"
    assert due.Reminder(2, 1, []).text(0) == "2 documents are due today, 1 document overdue"
    assert due.Reminder(0, 2, []).text(3) == "2 documents overdue"


def test_status_line_wording() -> None:
    assert due.status_line(due.DueCounts(7, 0, 0)) == "Due: nothing within 7 days."
    assert due.status_line(due.DueCounts(7, 3, 1)) == (
        "Due: 3 documents within 7 days, 1 overdue (`flib due`)."
    )
    assert due.status_line(due.DueCounts(7, 1, 0)) == "Due: 1 document within 7 days (`flib due`)."

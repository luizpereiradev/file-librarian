"""What is due (#91): `flib due`, marking as done, `flib status`, the reminder and MCP.

Organisations are fictitious; amounts and dates are made up. "Today" is fixed
through ``due.clock``.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
import pytest
from mcp import Client
from typer.testing import CliRunner

from file_librarian import cli, due
from file_librarian import status as st
from file_librarian.config import Config, load_config
from file_librarian.db.store import Store
from file_librarian.mcp_server import build_server
from file_librarian.models import Classification
from tests._helpers import (
    answer,
    fake_config,
    library,
    make_old,
    organize,
    row_for,
    set_tags,
    write_config,
)

pytestmark = pytest.mark.integration

runner = CliRunner()
TODAY = date(2026, 11, 20)
NOON = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
SECRET = "token = " + "ghp_" + "a" * 36


def on(day: int) -> str:
    """The date ``day`` days from TODAY."""
    return (TODAY + timedelta(days=day)).isoformat()


def doc(
    issuer: str | None,
    due_in: int | None,
    amount: str | None = None,
    currency: str | None = None,
    *,
    kind: str = "bill",
    confidence: float = 0.75,
) -> Classification:
    return answer(
        "Finance",
        confidence=confidence,
        kind=kind,
        issuer=issuer,
        amount=amount,
        currency=currency,
        due_date=None if due_in is None else on(due_in),
    )


FILES: dict[str, tuple[bytes, Classification | str]] = {
    "power.txt": (b"power", doc("Northwind Power", 3, "96.74", "USD")),
    "water.txt": (b"water", doc("Agua Clara Ltda", -2, "80.06", "BRL")),
    "tax.txt": (b"tax", doc("Revenue Office", 7, "1137.44", "BRL", kind="tax")),
    "far.txt": (b"far", doc("Northwind Power", 20, "103.91", "USD")),
    "old.txt": (b"old", doc("Agua Clara Ltda", -40, "70.00", "BRL")),
    "lease.txt": (b"lease", doc("Contoso Rentals", 1, "900.00", "BRL", kind="contract")),
    "nodue.txt": (b"nodue", doc("Northwind Power", None, "50.00", "USD")),
    "unsure.txt": (b"unsure", doc("Fabrikam Mobile", 5, kind="invoice", confidence=0.3)),
}


@pytest.fixture(autouse=True)
def fixed_today(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(due, "clock", lambda: NOON)


@pytest.fixture
def filed(tmp_path: Path, downloads: Path) -> Path:
    """The library above, filed by a real run; returns the config file."""
    path = write_config(tmp_path, downloads)
    organize(downloads, load_config(path), FILES)
    return path


def flib(*args: str) -> Any:
    result = runner.invoke(cli.app, list(args))
    return result


def listed(path: Path, *args: str) -> dict[str, Any]:
    result = flib("due", "-c", str(path), "--json", *args)
    assert result.exit_code == 0, result.output
    data: dict[str, Any] = json.loads(result.stdout)
    return data


def names(data: dict[str, Any]) -> list[str]:
    return [Path(item["path"]).name for item in data["items"]]


# --- flib due ------------------------------------------------------------------------------


def test_due_lists_overdue_and_the_next_7_days_soonest_first(filed: Path) -> None:
    data = listed(filed)
    assert (data["today"], data["from"], data["to"]) == ("2026-11-20", "2026-10-21", "2026-11-27")
    assert names(data) == ["water.txt", "power.txt", "unsure.txt", "tax.txt"]
    water, power, unsure, tax = data["items"]
    assert (water["days"], water["relative"], water["overdue"]) == (-2, "2 days ago", True)
    assert (power["days"], power["relative"], power["overdue"]) == (3, "in 3 days", False)
    assert power["amount"] == "96.74" and power["currency"] == "USD"
    assert power["issuer"] == "Northwind Power" and power["kind"] == "bill"
    assert power["status"] == "organized" and power["exists"] and not power["done"]
    assert unsure["status"] == "review" and unsure["amount"] is None
    assert tax["kind"] == "tax" and tax["due_date"] == "2026-11-27"
    assert data["totals"] == [
        {"currency": "BRL", "total": "1217.50", "files": 2},
        {"currency": "USD", "total": "96.74", "files": 1},
    ]
    # The contract due tomorrow is of a kind outside due.kinds: left out, and counted.
    assert data["hidden"] == 1 and data["without_amount"] == 1


def test_due_text_output(filed: Path) -> None:
    result = flib("due", "-c", str(filed))
    assert result.exit_code == 0, result.output
    lines = result.stdout.splitlines()
    assert lines[0] == "Due from 2026-10-21 to 2026-11-27 (today is 2026-11-20):"
    assert lines[1] == "  2026-11-18  (2 days ago, overdue)  BRL 80.06  Agua Clara Ltda  bill"
    assert lines[2].endswith("/Finance/water.txt") and lines[2].startswith("    /")
    assert lines[3] == "  2026-11-23  (in 3 days)  USD 96.74  Northwind Power  bill"
    assert lines[5] == "  2026-11-25  (in 5 days)  -  Fabrikam Mobile  invoice"
    assert lines[6].endswith("unsure.txt  (in review)")
    assert "Total open: BRL 1,217.50 (2 documents); USD 96.74 (1 document)" in lines
    assert "1 document state no amount; not counted." in lines
    assert lines[-1] == "1 document marked done or of another kind not shown (--all shows them)."


def test_due_windows_and_filters(filed: Path) -> None:
    assert "far.txt" in names(listed(filed, "--within", "1m"))
    assert "far.txt" not in names(listed(filed, "--within", "2w"))
    overdue = listed(filed, "--overdue")
    assert names(overdue) == ["old.txt", "water.txt"] and overdue["from"] is None
    assert names(listed(filed, "--all")) == [
        "water.txt",
        "lease.txt",
        "power.txt",
        "unsure.txt",
        "tax.txt",
    ]
    assert names(listed(filed, "--kind", "contract")) == ["lease.txt"]
    assert names(listed(filed, "--issuer", "AGUA CLARA", "--overdue")) == ["old.txt", "water.txt"]
    assert names(listed(filed, "--kind", "tax,invoice")) == ["unsure.txt", "tax.txt"]


def test_a_document_without_a_due_date_is_never_listed(filed: Path) -> None:
    for args in ((), ("--all",), ("--overdue",), ("--within", "12m", "--all")):
        assert "nodue.txt" not in names(listed(filed, *args))


def test_due_refuses_bad_options(filed: Path) -> None:
    for args, message in (
        (("--within", "soon"), "not a span"),
        (("--overdue", "--within", "7d"), "leave out --within"),
        (("--kind", "bills-ish"), "unknown kind"),
    ):
        result = flib("due", "-c", str(filed), *args)
        assert result.exit_code == 1 and message in result.stderr


def test_due_works_before_the_first_run_and_creates_nothing(
    tmp_path: Path, downloads: Path
) -> None:
    path = write_config(tmp_path, downloads)
    result = flib("due", "-c", str(path))
    assert result.exit_code == 0, result.output
    assert result.stdout == "Nothing due from 2026-10-21 to 2026-11-27 (today is 2026-11-20).\n"
    assert not load_config(path).database.exists()


def test_due_refuses_an_index_from_an_older_or_newer_flib(filed: Path) -> None:
    database = load_config(filed).database
    for version, message in ((16, "older flib"), (99, "newer than this flib")):
        conn = sqlite3.connect(database)
        conn.execute(f"PRAGMA user_version = {version}")
        conn.close()
        result = flib("due", "-c", str(filed))
        assert result.exit_code == 1 and message in result.stderr


def test_due_reads_without_the_lock(filed: Path) -> None:
    from file_librarian.lock import instance_lock

    with instance_lock(load_config(filed).database):
        assert names(listed(filed))  # a run going on does not stop it


# --- marking as done -------------------------------------------------------------------


def test_done_leaves_the_list_and_undo_brings_it_back(filed: Path, downloads: Path) -> None:
    power = library(downloads) / "Finance" / "power.txt"
    result = flib("fix", str(power), "--done", "-c", str(filed))
    assert result.exit_code == 0, result.output
    assert "marked done  Finance/power.txt  (due 2026-11-23)" in result.stdout
    assert "flib undo" in result.stdout
    data = listed(filed)
    assert "power.txt" not in names(data) and data["hidden"] == 2
    assert {t["currency"] for t in data["totals"]} == {"BRL"}
    shown = next(i for i in listed(filed, "--all")["items"] if i["path"] == str(power))
    assert (shown["done"], shown["done_by"], shown["overdue"]) == (True, "fix", False)
    # A settle run is not "the last run" of `flib status`.
    cfg = load_config(filed)
    assert st.collect(cfg).last_run is not None
    assert st.collect(cfg).last_run.files_moved  # type: ignore[union-attr]

    result = flib("undo", "-c", str(filed))
    assert result.exit_code == 0, result.output
    assert "1 index change(s) reverted" in result.stdout
    assert "power.txt" in names(listed(filed))
    assert power.exists()  # undoing a settle moves nothing


def test_not_done_opens_it_again(filed: Path, downloads: Path) -> None:
    water = library(downloads) / "Finance" / "water.txt"
    assert flib("fix", str(water), "--done", "-c", str(filed)).exit_code == 0
    result = flib("fix", str(water), "--not-done", "-c", str(filed))
    assert result.exit_code == 0 and "marked not done (open again)" in result.stdout
    assert "water.txt" in names(listed(filed))
    assert row_for(load_config(filed), "water.txt").settled_at is None
    # Undo of --not-done marks it done again.
    assert flib("undo", "-c", str(filed)).exit_code == 0
    assert row_for(load_config(filed), "water.txt").settled_at is not None


def test_done_refuses_what_it_cannot_mark(filed: Path, downloads: Path) -> None:
    folder = library(downloads) / "Finance"
    for path, args, message in (
        (folder / "nodue.txt", ("--done",), "states no due date"),
        (folder / "power.txt", ("--not-done",), "not marked as done"),
        (folder / "power.txt", ("--done", "--category", "Finance"), "go alone"),
        (folder / "nothing.txt", ("--done",), "not a file flib organized"),
    ):
        result = flib("fix", str(path), *args, "-c", str(filed))
        assert result.exit_code == 1 and message in result.stderr, args
    assert flib("fix", str(folder / "power.txt"), "--done", "-c", str(filed)).exit_code == 0
    again = flib("fix", str(folder / "power.txt"), "--done", "-c", str(filed))
    assert again.exit_code == 1 and "already marked as done" in again.stderr


def test_done_refuses_a_file_left_in_place(downloads: Path, cfg: Config) -> None:
    (downloads / "creds.txt").write_text(SECRET, encoding="utf-8")
    make_old(downloads / "creds.txt")
    organize(downloads, cfg, {})
    with Store.open(cfg.database) as store:
        store.conn.execute("UPDATE files SET due_date = ?", (on(1),))
        with pytest.raises(due.DueError, match="sensitive"):
            due.settle(store, downloads / "creds.txt", done=True)


def test_a_finder_tag_can_mark_it_done(tmp_path: Path, downloads: Path) -> None:
    path = write_config(tmp_path, downloads, extra="due: {done_tag: Paid}\n")
    organize(downloads, load_config(path), FILES)
    power = library(downloads) / "Finance" / "power.txt"
    set_tags(power, ["Finance", "paid\n6"])  # any case, any color
    data = listed(path)
    assert "power.txt" not in names(data)
    shown = next(i for i in listed(path, "--all")["items"] if i["path"] == str(power))
    assert shown["done_by"] == "tag"
    result = flib("due", "-c", str(path), "--all")
    assert "(in 3 days, done (Finder tag))" in result.stdout
    set_tags(power, ["Finance"])  # the tag removed: open again
    assert "power.txt" in names(listed(path))


def test_a_file_moved_by_hand_is_flagged(filed: Path, downloads: Path) -> None:
    power = library(downloads) / "Finance" / "power.txt"
    power.rename(power.with_name("elsewhere.txt"))
    result = flib("due", "-c", str(filed))
    assert "power.txt  (missing: moved by hand?)" in result.stdout
    assert not next(i for i in listed(filed)["items"] if i["path"] == str(power))["exists"]


# --- flib status ---------------------------------------------------------------------------


def test_status_has_a_due_line(filed: Path) -> None:
    result = flib("status", "-c", str(filed))
    assert "Due: 3 documents within 7 days, 1 overdue (`flib due`)." in result.stdout.splitlines()
    data = json.loads(flib("status", "-c", str(filed), "--json").stdout)
    assert data["due"] == {"within_days": 7, "upcoming": 3, "overdue": 1}


def test_status_without_due_documents(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, {"a.txt": (b"a", "Finance")})
    assert "Due: nothing within 7 days." in st.render(st.collect(cfg), cfg)


def test_status_on_an_index_from_before_due_has_no_due_line(filed: Path) -> None:
    cfg = load_config(filed)
    conn = sqlite3.connect(cfg.database)
    conn.execute("PRAGMA user_version = 16")
    conn.close()
    state = st.collect(cfg)
    assert state.due is None
    assert not any(line.startswith("Due:") for line in st.render(state, cfg))
    assert json.loads(st.to_json(state, cfg))["due"] is None


# --- the reminder in the periodic summary ------------------------------------------------


def calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


def quiet_review(cfg: Config) -> Config:
    """No review summary in the way: only the due reminder can make one due."""
    return cfg.model_copy(update={"summary": cfg.summary.model_copy(update={"min_age_days": 9999})})


def at(day: int) -> datetime:
    return NOON + timedelta(days=day)


def test_each_document_is_reminded_once(downloads: Path, fake_osascript: Path) -> None:
    cfg = quiet_review(fake_config(downloads))
    organize(downloads, cfg, FILES)
    first = st.after_scheduled_run(cfg)  # the review file is new now; due dates use the fixed day
    assert first == [
        "Summary: 1 document is due within 3 days, 1 document overdue. "
        "Run `flib status` for details."
    ]
    (argv,) = calls(fake_osascript)
    text = " ".join(argv)
    for secret in ("Northwind", "Agua", "96.74", "80.06", "power", "water", "USD", "BRL"):
        assert secret not in text  # counts only: it shows on the lock screen
    # Nothing new later the same day, or the next: no nagging.
    assert st.after_scheduled_run(cfg, now=at(0)) == []
    assert st.after_scheduled_run(cfg, now=at(1)) == []
    # Two days on, the invoice (due in 5) comes within 3 days: one more reminder.
    assert st.after_scheduled_run(cfg, now=at(2)) == [
        "Summary: 2 documents are due within 3 days, 1 document overdue. "
        "Run `flib status` for details."
    ]
    assert len(calls(fake_osascript)) == 2
    with Store.open(cfg.database) as store:
        # A due reminder alone does not restart summary.every_days.
        assert store.meta(st.SUMMARY_KEY) is None


def test_done_documents_are_not_reminded(downloads: Path, fake_osascript: Path) -> None:
    cfg = quiet_review(fake_config(downloads))
    organize(downloads, cfg, FILES)
    with Store.open(cfg.database) as store:
        for name in ("power.txt", "water.txt"):
            due.settle(store, library(downloads) / "Finance" / name, done=True)
    assert st.after_scheduled_run(cfg, now=at(0)) == []
    assert calls(fake_osascript) == []


def test_a_new_due_date_is_reminded_once_more(downloads: Path, fake_osascript: Path) -> None:
    cfg = quiet_review(fake_config(downloads))
    organize(downloads, cfg, FILES)
    assert st.after_scheduled_run(cfg, now=at(0))
    with Store.open(cfg.database) as store:  # a reprocess read another due date
        store.conn.execute(
            "UPDATE files SET due_date = ? WHERE original_name = 'power.txt'", (on(2),)
        )
    assert st.after_scheduled_run(cfg, now=at(0))
    assert st.after_scheduled_run(cfg, now=at(0)) == []


def test_reminders_can_be_turned_off_or_moved(downloads: Path, fake_osascript: Path) -> None:
    cfg = quiet_review(fake_config(downloads, due={"remind": False}))
    organize(downloads, cfg, FILES)
    assert st.after_scheduled_run(cfg, now=at(0)) == []
    wide = cfg.model_copy(update={"due": cfg.due.model_copy(update={"remind": True})})
    wide = wide.model_copy(update={"due": wide.due.model_copy(update={"remind_days_before": 7})})
    assert st.after_scheduled_run(wide, now=at(0)) == [
        "Summary: 3 documents are due within 7 days, 1 document overdue. "
        "Run `flib status` for details."
    ]


def test_a_review_summary_carries_the_due_count(downloads: Path, fake_osascript: Path) -> None:
    cfg = fake_config(downloads, summary={"min_age_days": 0})
    organize(downloads, cfg, FILES)
    first = st.after_scheduled_run(cfg)  # the review file is new now; due dates use the fixed day
    assert first == [
        "Summary: 1 document is due within 3 days, 1 document overdue, 1 file waits for "
        "review. Run `flib status` for details."
    ]
    with Store.open(cfg.database) as store:
        assert store.meta(st.SUMMARY_KEY) is not None
    # Within summary.every_days and nothing new due: no second summary.
    assert st.after_scheduled_run(cfg) == []


# --- MCP ----------------------------------------------------------------------------------


def call(cfg: Config, tool: str, **arguments: Any) -> Any:
    async def main() -> Any:
        async with Client(build_server(cfg), mode="legacy", raise_exceptions=True) as client:
            return await client.call_tool(tool, arguments)

    return anyio.run(main)


def tools(cfg: Config) -> dict[str, Any]:
    async def main() -> Any:
        async with Client(build_server(cfg), mode="legacy", raise_exceptions=True) as client:
            return (await client.list_tools()).tools

    return {t.name: t for t in anyio.run(main)}


def test_mcp_list_due(downloads: Path, cfg: Config) -> None:
    organize(downloads, cfg, FILES)
    result = call(cfg, "list_due")
    assert not result.is_error, result.content
    data = result.structured_content
    assert [Path(i["path"]).name for i in data["items"]] == [
        "water.txt",
        "power.txt",
        "unsure.txt",
        "tax.txt",
    ]
    assert data["today"] == "2026-11-20" and data["from_date"] == "2026-10-21"
    assert data["items"][0]["days"] == -2 and data["items"][0]["overdue"] is True
    assert data["totals"][0] == {"currency": "BRL", "total": "1217.50", "files": 2}
    overdue = call(cfg, "list_due", overdue=True).structured_content
    assert [Path(i["path"]).name for i in overdue["items"]] == ["old.txt", "water.txt"]
    every = call(cfg, "list_due", include_done=True, within="1m", issuer="northwind power")
    assert [Path(i["path"]).name for i in every.structured_content["items"]] == [
        "power.txt",
        "far.txt",
    ]
    bad = call(cfg, "list_due", within="soon")
    assert bad.is_error and "not a span" in bad.content[0].text


def test_mcp_list_due_schema_and_sensitive_files(downloads: Path, cfg: Config) -> None:
    tool = tools(cfg)["list_due"]
    assert tool.annotations.read_only_hint is True
    assert "untrusted" in tool.description
    assert set(tool.input_schema["properties"]) == {
        "within",
        "overdue",
        "include_done",
        "issuer",
        "kinds",
    }
    assert {"items", "totals", "today"} <= set(tool.output_schema["properties"])
    assert call(cfg, "list_due").structured_content["items"] == []  # before the first run
    (downloads / "creds.txt").write_text(SECRET, encoding="utf-8")
    make_old(downloads / "creds.txt")
    organize(downloads, cfg, FILES)
    with Store.open(cfg.database) as store:
        store.conn.execute(
            "UPDATE files SET due_date = ?, kind = 'bill' WHERE status = 'sensitive'", (on(1),)
        )
    items = call(cfg, "list_due", include_done=True).structured_content["items"]
    assert "creds.txt" not in [Path(i["path"]).name for i in items]

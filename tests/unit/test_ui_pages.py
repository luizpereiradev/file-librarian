"""``flib ui``'s read-only pages (#114): Activity, Search and Due.

Each page is fetched from a real server on 127.0.0.1 (``loopback_network``),
over a real index the fake provider filled. File names, descriptions and
issuers are hostile on purpose: they must come out as text, never markup.
Opening a file goes to the fake ``open`` of conftest.py.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import sqlite3
import time
from array import array
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import pytest

from file_librarian import activity, catchup, due, pipeline
from file_librarian.config import Config
from file_librarian.db.store import RunRow, Store
from file_librarian.extractors import origin
from file_librarian.models import Classification, FileStatus, OperationStatus, RunTrigger
from file_librarian.providers.fake import FakeProvider
from file_librarian.runlog import write_log
from file_librarian.scanner import SkipReason
from file_librarian.ui import markup, views
from file_librarian.ui import server as ui_server
from file_librarian.ui.server import CSP, Ui
from file_librarian.undo import undo_run
from tests._helpers import (
    UiClient,
    UiResponse,
    answer,
    fake_config,
    library,
    put,
    write_xattr,
)
from tests.unit.test_ui import (  # noqa: F401 - fixtures: every server is checked the same way
    _Collector,
    events,
    no_hang,
    nothing_left_behind,
)

# Hostile on purpose: markup, quotes, an entity, the page's own slot, a formula.
EVIL_NAME = """<img src=x onerror=alert(1)> "q" 'a' &amp; {{csrf}}.pdf"""
EVIL_TEXT = """"><script>alert(2)</script> {{nav}}"""
EVIL_ISSUER = "<i>Evil & Co</i>"
FORMULA = "=HYPERLINK(1)"
RAW = ("<img src=x", "<script>", "<i>Evil", "onerror=alert(1)>")

TODAY = date.today()


def soon(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


def bill(description: str = "d", **kw: Any) -> Classification:
    fields: dict[str, Any] = {"kind": "bill", "currency": "USD"} | kw
    return answer("Finance", confidence=0.9, description=description, **fields)


LIBRARY = {
    EVIL_NAME: bill(description=EVIL_TEXT, issuer=EVIL_ISSUER, amount="10", due_date=soon(1)),
    "power.pdf": bill(issuer="Power Co", amount="120.50", due_date=soon(3)),
    "water.pdf": bill(issuer="Water Co", amount="40", due_date=soon(-2)),
    "formula.pdf": answer("Finance", confidence=0.9, description=FORMULA),
    "refund.pdf": bill(issuer="Shop", amount="5", currency="EUR", kind="receipt"),
    "unsure.pdf": answer("Finance", confidence=0.3, kind="bill", due_date=soon(2)),
    "notes.txt": answer("Other", name="Meeting notes", confidence=0.95, tags=["<tag>", "work"]),
}


def fill(downloads: Path, cfg: Config, files: dict[str, Classification] | None = None) -> int:
    """Run the pipeline over ``files`` (the library above by default); the run's id."""
    files = LIBRARY if files is None else files
    for name in files:
        put(downloads, name, name.encode())
    put(downloads, "id_rsa", b"key material")  # sensitive: left in place, never listed
    outcome = pipeline.run(cfg, FakeProvider(files, default=answer()), simulate=False)
    assert outcome.report is not None and outcome.report.run_id is not None
    return outcome.report.run_id


@pytest.fixture
def serve(loopback_network: None) -> Iterator[list[Ui]]:
    """Servers started with ``start``, all closed at the end."""
    started: list[Ui] = []
    yield started
    for ui in started:
        ui.close()


def start(serve: list[Ui], cfg: Config, **kw: object) -> UiClient:
    ui = Ui(cfg, poll_seconds=0.01, **kw)  # type: ignore[arg-type]
    ui.start()
    serve.append(ui)
    c = UiClient(ui.port, ui.host_name)
    assert c.launch(ui.launch_url).status == 303
    return c


@pytest.fixture
def filled(downloads: Path, cfg: Config) -> int:
    return fill(downloads, cfg)


@pytest.fixture
def client(serve: list[Ui], cfg: Config, filled: int) -> UiClient:
    return start(serve, cfg)


@pytest.fixture
def empty(serve: list[Ui], cfg: Config) -> UiClient:
    return start(serve, cfg)


def search(c: UiClient, path: str = "/search", **fields: str | list[str]) -> UiResponse:
    """Post the search form as a browser does; ``/search`` answers with a redirect to
    the kept search (post, redirect, get), which is followed."""
    body: dict[str, str | list[str]] = {"csrf": c.csrf("/search"), **fields}
    reply = c.request(
        "POST",
        path,
        body=urlencode(body, doseq=True).encode(),
        headers={
            "Origin": c.origin,
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    if path == "/search" and reply.status == 303:
        location = reply.headers["Location"]
        assert re.fullmatch(r"/search\?r=[A-Za-z0-9_-]{16}", location), location
        return c.get(location)
    return reply


def post(c: UiClient, route: str, **fields: str) -> UiResponse:
    return c.post_form(route, {"csrf": c.csrf("/search")} | fields)


def assert_safe(page: str) -> None:
    """No hostile value came out as markup, and the page has no inline code."""
    for raw in RAW:
        assert raw not in page, raw
    parser = _Collector()
    parser.feed(page)
    assert parser.problems == []
    assert page.count('<nav class="pages"') == 1  # a slot in a value is never filled


def file_id(cfg: Config, name: str) -> int:
    with Store.open(cfg.database) as store:
        return next(r.id for r in store.all_files() if r.original_name == name)


def opened(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]


# --- every page ------------------------------------------------------------------------


PAGES = ["/activity", "/search", "/due"]


@pytest.mark.parametrize("path", PAGES)
def test_each_page_needs_the_session(empty: UiClient, path: str) -> None:
    reply = UiClient(empty.port, empty.name).get(path)
    assert reply.status == 403
    assert "is open in another window" in reply.text  # the session lives elsewhere


@pytest.mark.parametrize("path", ["/search", "/search/export", "/files/open", "/files/reveal"])
def test_each_new_post_route_is_in_the_route_table(path: str) -> None:
    assert path in ui_server.POST_ROUTES  # so the route-table test covers it


@pytest.mark.parametrize("path", PAGES)
def test_an_empty_library_creates_nothing(empty: UiClient, cfg: Config, path: str) -> None:
    reply = empty.get(path)
    assert reply.status == 200
    assert "No run yet" in reply.text or "Nothing due" in reply.text
    assert_safe(reply.text)
    assert not cfg.database.exists()


def test_search_and_export_before_the_first_run(empty: UiClient, cfg: Config) -> None:
    reply = search(empty, q="bill")
    assert reply.status == 200 and "No run yet" in reply.text
    reply = search(empty, since="not a day")
    assert "is not a date" in reply.text
    export = search(empty, "/search/export", q="bill")
    assert export.status == 200
    assert export.text.splitlines() == [",".join(views.CSV_COLUMNS)]
    assert not cfg.database.exists()


@pytest.mark.parametrize("path", PAGES)
def test_each_page_is_escaped_and_has_no_inline_code(client: UiClient, path: str) -> None:
    reply = client.get(path)
    assert reply.status == 200
    assert reply.headers["Content-Security-Policy"] == CSP
    assert reply.headers["Content-Type"] == "text/html; charset=utf-8"
    assert_safe(reply.text)


def test_the_navigation_links_the_pages_that_exist(client: UiClient) -> None:
    for path in ["/status", *PAGES]:
        page = client.get(path).text
        for name in ("review", "search", "activity", "due", "settings"):
            assert f'href="/{name}"' in page, (path, name)
        parser = _Collector()
        parser.feed(page)
        assert parser.disabled == [], path
    assert 'aria-current="page"' in client.get("/due").text


def test_the_generated_navigation_matches_the_status_page() -> None:
    """Status is a static file; its menu is the one the other pages are built with."""
    static = markup.page(ui_server.load_pages()["status"])  # its words filled in
    built = markup.nav("status")
    assert built in static.replace("\n    ", "").replace("\n  </nav>", "</nav>")


def test_the_policy_requires_trusted_types() -> None:
    assert "require-trusted-types-for 'script'" in CSP


@pytest.mark.parametrize("page", ["search", "activity", "due"])
def test_flib_ui_can_open_each_page(serve: list[Ui], cfg: Config, page: str) -> None:
    ui = Ui(cfg, page=page, poll_seconds=0.01)
    ui.start()
    serve.append(ui)
    c = UiClient(ui.port, ui.host_name)
    assert c.launch(ui.launch_url).headers["Location"] == f"/{page}"
    assert c.get(f"/{page}").status == 200


# --- Search ----------------------------------------------------------------------------


def test_the_search_form_lists_categories_and_kinds(client: UiClient) -> None:
    page = client.get("/search").text
    assert 'name="category" value="Finance"' in page
    assert 'name="kind" value="bill"' in page
    assert 'formaction="/search/export"' in page
    assert 'method="post" action="/search"' in page
    assert "<ol" not in page  # no results without a search


def test_a_search_in_the_address_is_never_run(client: UiClient) -> None:
    """Words go in a form body only: an address stays in the browser's history."""
    reply = client.get("/search?q=power")
    assert reply.status == 400
    assert "power" not in reply.text


def test_search_finds_by_words_and_escapes_everything(client: UiClient) -> None:
    reply = search(client, q="img onerror")
    text = reply.text
    assert reply.status == 200
    assert_safe(text)
    assert "1 match" in text
    assert "&lt;img src=x onerror=alert(1)&gt; &quot;q&quot; &#x27;a&#x27; &amp;amp;" in text
    assert "{{csrf}}.pdf" in text  # the slot in a name is never filled
    assert "&quot;&gt;&lt;script&gt;alert(2)&lt;/script&gt; {{nav}}" in text
    assert "&lt;i&gt;Evil &amp; Co&lt;/i&gt;" in text


def test_search_by_filters_with_totals(client: UiClient) -> None:
    reply = search(client, kind=["bill"], by="issuer")
    text = reply.text
    assert "4 matches" in text  # one is in review
    assert "USD 170.50" in text  # per currency, every match
    assert "Power Co" in text and "By issuer" in text
    assert "refund.pdf" not in text


def test_search_totals_never_mix_currencies(client: UiClient) -> None:
    text = search(client, category=["finance"]).text
    assert "USD 170.50" in text and "EUR 5.00" in text
    assert "no amount; not counted" in text  # the files without one are counted apart


@pytest.mark.parametrize(
    ("fields", "error"),
    [
        ({}, views.NOTHING),
        ({"since": "2026-13-01"}, "Processed from &#x27;2026-13-01&#x27; is not a valid date"),
        ({"category": "Nope"}, "Unknown category &#x27;Nope&#x27;"),
        ({"min_amount": "lots"}, "At least &#x27;lots&#x27; is not an amount"),
        ({"period": "<b>"}, "Period &#x27;&lt;b&gt;&#x27; is not a year or month"),
        ({"ext": "p/d"}, "Bad extension"),
    ],
)
def test_search_errors_are_said_in_the_forms_words(
    client: UiClient, fields: dict[str, str], error: str
) -> None:
    reply = search(client, **fields)
    assert reply.status == 200
    assert error in reply.text
    assert "<ol" not in reply.text


def test_the_form_keeps_what_was_typed(client: UiClient) -> None:
    text = search(client, q='a "b"', issuer="Power Co", limit="20", duplicates="1").text
    assert 'value="a &quot;b&quot;"' in text
    assert 'name="issuer" value="Power Co"' in text
    assert '<option value="20" selected>' in text
    assert 'name="duplicates" value="1" checked' in text
    assert "<details open>" in text


def test_sensitive_files_never_appear_in_search(client: UiClient) -> None:
    text = search(client, q="id_rsa").text
    assert "id_rsa" not in text.replace('value="id_rsa"', "")
    assert "1 sensitive file also matched" in text


def test_a_hit_says_its_old_name_its_tags_and_review(client: UiClient) -> None:
    text = search(client, q="meeting").text
    assert 'was <bdi class="name" dir="ltr">notes.txt</bdi>' in text
    assert "<li>&lt;tag&gt;</li>" in text
    assert "in review" in search(client, q="unsure").text


def test_search_on_an_index_that_fails_to_read(
    monkeypatch: pytest.MonkeyPatch, client: UiClient
) -> None:
    def broken(self: Store) -> list[str]:
        raise sqlite3.OperationalError("disk I/O error at /secret/path")

    monkeypatch.setattr(Store, "categories", broken)
    reply = client.get("/search")
    assert reply.status == 503
    assert "Cannot read the index" in reply.text and "/secret" not in reply.text
    export = search(client, "/search/export", q="x")
    assert export.status == 503 and "Cannot read the index" in export.text
    assert "/secret" not in export.text


def test_no_matches(client: UiClient) -> None:
    assert "No matches." in search(client, q="nothingatall").text


def test_duplicates_only_when_asked(downloads: Path, cfg: Config, serve: list[Ui]) -> None:
    fill(downloads, cfg, {"bill.pdf": answer("Finance")})
    put(downloads, "bill copy.pdf", b"bill.pdf")
    pipeline.run(cfg, FakeProvider(default=answer()), simulate=False)
    c = start(serve, cfg)
    assert "duplicate of" not in search(c, q="bill").text
    text = search(c, q="bill", duplicates="1").text
    assert "duplicate of Finance/bill.pdf" in text


def test_semantic_search_falls_back_with_a_notice(
    downloads: Path, serve: list[Ui], tmp_path: Path
) -> None:
    cfg = fake_config(
        downloads, database=str(tmp_path / "l.db"), search={"semantic": {"enabled": True}}
    )
    fill(downloads, cfg, {"power.pdf": answer("Finance")})
    c = start(serve, cfg)
    assert 'name="semantic"' in c.get("/search").text
    text = search(c, q="power", semantic="1").text
    assert "No file has an embedding" in text
    assert "power.pdf" in text
    assert "not by meaning" in text  # the totals match the words only


def test_search_on_an_index_from_an_older_flib(client: UiClient, cfg: Config) -> None:
    with sqlite3.connect(cfg.database) as conn:
        conn.execute("PRAGMA user_version = 12")
    reply = client.get("/search")
    assert reply.status == 503
    expected = "The index is from an older flib; the next <code>flib run</code> updates it"
    assert expected in reply.text


def test_a_form_with_too_many_fields_is_refused(client: UiClient) -> None:
    fields = {f"f{i}": "x" for i in range(ui_server.MAX_FORM_FIELDS + 1)}
    assert post(client, "/search", **fields).status == 403


# --- CSV export ------------------------------------------------------------------------


def test_the_export_is_an_attachment_a_spreadsheet_cannot_run(client: UiClient) -> None:
    reply = search(client, "/search/export", category=["Finance"])
    assert reply.status == 200
    headers = reply.headers
    assert headers["Content-Disposition"] == 'attachment; filename="flib-search.csv"'
    assert headers["Content-Type"] == "text/csv; charset=utf-8"
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["Content-Security-Policy"] == CSP
    rows = list(csv.DictReader(io.StringIO(reply.text)))
    by_name = {Path(r["path"]).name: r for r in rows}
    assert "id_rsa" not in by_name
    assert by_name["formula.pdf"]["description"] == "'" + FORMULA
    assert by_name["refund.pdf"]["amount"] == "5.00"
    assert by_name[EVIL_NAME]["issuer"] == EVIL_ISSUER  # CSV is data, not HTML


def test_an_export_with_a_bad_filter_says_why(client: UiClient) -> None:
    reply = search(client, "/search/export", since="soon")
    assert reply.status == 400
    assert reply.headers["Content-Type"] == "text/html; charset=utf-8"  # a page, not a file
    assert "Content-Disposition" not in reply.headers
    assert "Processed from &#x27;soon&#x27; is not a date" in reply.text
    assert 'action="/session/end"' in reply.text  # the menu and the session's button
    empty = search(client, "/search/export")
    assert empty.status == 400 and views.NOTHING in empty.text


@pytest.mark.parametrize(
    ("value", "cell"),
    [
        ("=1+1", "'=1+1"),
        ("+1", "'+1"),
        ("@x", "'@x"),
        ("-x", "'-x"),
        ("\tx", "'\tx"),
        ("\rx", "'\rx"),
        ("a", "a"),
    ],
)
def test_cells_that_look_like_formulas_are_quoted(value: str, cell: str) -> None:
    assert views.cell(value) == cell
    assert views.cell("-12.50", number=True) == "-12.50"
    assert views.cell("-x", number=True) == "'-x"
    assert views.cell(None) == ""


# --- Due -------------------------------------------------------------------------------


def test_due_lists_the_window_with_totals(client: UiClient) -> None:
    text = client.get("/due").text
    assert_safe(text)
    for name in ("power.pdf", "water.pdf"):
        assert name in text
    assert "overdue" in text and "tomorrow" in text
    assert "USD 170.50" in text  # the three bills; the receipt is not a kind `flib due` lists
    assert "refund.pdf" not in text
    assert 'class="chip" aria-current="true">7 days' in text.replace('href="/due?within=7d" ', "")


def test_due_shows_review_and_documents_without_an_amount(client: UiClient) -> None:
    text = client.get("/due").text
    assert "unsure.pdf" in text and "in review" in text
    assert "1 document states no amount; not counted." in text


def test_due_choices_go_in_the_address(client: UiClient) -> None:
    assert "power.pdf" not in client.get("/due?overdue=1").text
    assert "water.pdf" in client.get("/due?overdue=1").text
    assert client.get("/due?within=1m&all=1").status == 200
    assert "Hide those marked done" in client.get("/due?within=1m&all=1").text


@pytest.mark.parametrize(
    "query",
    ["within=9y", "within=7d&within=2w", "overdue=yes", "all=0", "overdue=1&within=7d", "a=1&" * 9],
)
def test_due_refuses_choices_it_does_not_offer(client: UiClient, query: str) -> None:
    assert client.get(f"/due?{query}").status == 400


def test_due_says_what_is_done_or_missing(client: UiClient, cfg: Config) -> None:
    with Store.open(cfg.database) as store:
        row = store.file_by_id(file_id(cfg, "water.pdf"))
        due.settle(store, row.current_path, done=True)
        moved = store.file_by_id(file_id(cfg, "power.pdf")).current_path
    moved.unlink()
    text = client.get("/due").text
    assert "1 document marked done or of another kind not shown." in text
    assert "missing: moved by hand?" in text
    assert "done" in client.get("/due?all=1&within=7d").text


def test_due_on_an_index_it_cannot_read(client: UiClient, cfg: Config) -> None:
    with sqlite3.connect(cfg.database) as conn:
        conn.execute("PRAGMA user_version = 999")
    reply = client.get("/due")
    assert reply.status == 503
    assert "newer than this flib" in reply.text


def test_due_never_shows_a_database_error(
    monkeypatch: pytest.MonkeyPatch, client: UiClient, cfg: Config
) -> None:
    def broken(config: Config, query: due.Query) -> None:
        cause = sqlite3.OperationalError("disk I/O error at /cause/path")
        raise due.DueError(f"cannot read the index: {cause}") from cause

    monkeypatch.setattr(due, "find", broken)
    reply = client.get("/due")
    assert reply.status == 503
    assert "/cause/path" not in reply.text and "disk I/O" not in reply.text
    assert "Cannot read the index</p>" in reply.text


# --- Activity --------------------------------------------------------------------------


def test_activity_shows_each_run_and_what_it_moved(client: UiClient) -> None:
    text = client.get("/activity").text
    assert_safe(text)
    assert "by hand" in text and "run 1" in text
    assert "power.pdf</bdi>" in text and ">Finance/</span>" in text  # the move's folder
    assert "1 sensitive file left in place" in text
    assert "id_rsa" not in text
    assert 'aria-current="true">7 days' in text.replace('href="/activity?days=7" ', "")


def test_activity_marks_duplicates_and_undone_moves(
    downloads: Path, cfg: Config, serve: list[Ui]
) -> None:
    fill(downloads, cfg, {"bill.pdf": answer("Finance")})
    put(downloads, "bill copy.pdf", b"bill.pdf")
    pipeline.run(cfg, FakeProvider(default=answer()), simulate=False)
    with Store.open(cfg.database) as store:
        undo_run(store, 1, config=cfg)
    text = start(serve, cfg).get("/activity").text
    assert ">_Duplicates/</span>" in text
    assert ">undone<" in text


def test_activity_reads_the_wait_for_an_unlock_from_the_logs(
    filled: int, cfg: Config, serve: list[Ui]
) -> None:
    locked = f"  skip    photo.jpeg  ({SkipReason.LOCKED.value})"
    write_log(
        cfg.database,
        filled,
        [locked, catchup.WAITING_LINE.format(hours=8, count=1), catchup.UNLOCKED_LINE],
    )
    text = start(serve, cfg).get("/activity").text
    assert "1 file protected while the Mac is locked skipped" in text
    assert "then ran once more" in text
    assert "photo.jpeg" not in text


@pytest.mark.parametrize(
    ("lines", "expected"),
    [
        ([], activity.Unlock()),
        ([catchup.CATCH_UP_LINE], activity.Unlock(catch_up=True)),
        ([catchup.WAITING_LINE.format(hours=2, count=3)], activity.Unlock(waited="waiting")),
        (
            [catchup.WAITING_LINE.format(hours=2, count=3), catchup.TIMED_OUT_LINE.format(hours=2)],
            activity.Unlock(waited="timed out"),
        ),
        ([catchup.STOPPED_LINE], activity.Unlock(waited="stopped")),
        ([catchup.NOT_WAITING_LINE], activity.Unlock(waited="elsewhere")),
    ],
)
def test_what_a_log_says_about_the_unlock(
    cfg: Config, lines: list[str], expected: activity.Unlock
) -> None:
    write_log(cfg.database, 7, lines)
    assert activity.read_unlock(cfg.database, 7) == expected


def test_no_log_says_nothing_about_the_unlock(cfg: Config) -> None:
    assert activity.read_unlock(cfg.database, 99) == activity.Unlock()


@pytest.mark.parametrize(
    ("unlock", "text"),
    [
        (activity.Unlock(catch_up=True), "Run after the Mac was unlocked."),
        (activity.Unlock(locked=2, waited="timed out"), "2 files protected"),
        (activity.Unlock(locked=1), "skipped."),
        (activity.Unlock(), None),
    ],
)
def test_the_unlock_in_words(unlock: activity.Unlock, text: str | None) -> None:
    said = views._unlock(unlock)
    assert said == text if text is None else text in (said or "")


def test_activity_counts_documents_marked_done(filled: int, cfg: Config, serve: list[Ui]) -> None:
    with Store.open(cfg.database) as store:
        due.settle(store, store.file_by_id(file_id(cfg, "water.pdf")).current_path, done=True)
    assert "1 document marked done or open again." in start(serve, cfg).get("/activity").text


def test_activity_windows(client: UiClient) -> None:
    for days in activity.DAYS:
        assert client.get(f"/activity?days={days}").status == 200
    for query in ("days=1", "days=7&days=30", "days=x"):
        assert client.get(f"/activity?{query}").status == 400


def test_activity_with_no_run_in_the_window(
    monkeypatch: pytest.MonkeyPatch, filled: int, cfg: Config
) -> None:
    later = activity.collect(cfg, 7)
    assert len(later.found) == 1
    future = activity.collect(cfg, 7, now=lambda: datetime(2099, 1, 1, tzinfo=UTC))
    assert future.found == []
    page = views.activity_page(cfg, "t", "")[1].decode()
    assert "run 1" in page
    monkeypatch.setattr(activity, "collect", lambda config, days: activity.Activity(days, []))
    assert "No run in the last 7 days." in views.activity_page(cfg, "t", "")[1].decode()


def test_activity_lists_a_bounded_number_of_runs_and_moves(
    monkeypatch: pytest.MonkeyPatch, downloads: Path, cfg: Config
) -> None:
    fill(downloads, cfg, {f"f{i}.pdf": answer("Finance") for i in range(3)})
    pipeline.run(cfg, FakeProvider(default=answer()), simulate=False)
    monkeypatch.setattr(activity, "MAX_RUNS", 1)
    monkeypatch.setattr(activity, "MAX_MOVES", 2)
    found = activity.collect(cfg, 7)
    assert found.capped
    [day] = found.found
    [run] = day.runs
    assert run.run.id == 2
    with monkeypatch.context() as m:
        m.setattr(activity, "MAX_RUNS", 5)
        first = activity.collect(cfg, 7).found[0].runs[-1]
    assert len(first.moves) == 2 and first.more == 1
    page = views.activity_page(cfg, "t", "")[1].decode()
    assert "Only the newest 1 runs are shown" in page


def test_simulations_are_not_activity(downloads: Path, cfg: Config) -> None:
    put(downloads, "a.pdf", b"a")
    pipeline.run(cfg, FakeProvider(default=answer()), simulate=True)
    assert activity.collect(cfg, 7).found == []


def run_row(**kw: object) -> RunRow:
    fields: dict[str, object] = {
        "id": 3,
        "trigger": RunTrigger.WATCH,
        "started_at": "2026-01-02T03:04:05+00:00",
        "finished_at": "2026-01-02T03:05:05+00:00",
        "files_seen": 1,
        "files_moved": 1,
        "files_review": 0,
        "errors": 0,
        "cost_usd": 0.5,
    } | kw
    return RunRow(**fields)  # type: ignore[arg-type]


def test_a_run_in_words(cfg: Config, downloads: Path) -> None:
    move = activity.Move(
        file_id=1,
        source=downloads / "<a>.pdf",
        destination=library(downloads) / "Other" / "<a>.pdf",
        status=OperationStatus.FAILED,
        file_status=FileStatus.ORGANIZED,
        category=None,
        fix=True,
    )
    item = activity.RunActivity(run_row(), [move], more=4, index_changes=2)
    assert item.duplicates == 0
    text = views._run(item, cfg)
    assert "on arrival (watch)" in text
    assert "fixed by hand" in text and ">failed<" in text
    assert "and 4 more" in text and "2 changes to the index." in text
    assert "&lt;a&gt;.pdf" in text and "<a>" not in text
    day = activity.Day(date(2026, 1, 2), [item, activity.RunActivity(run_row(cost_usd=None))])
    assert day.cost_usd == 0.5 and day.unpriced == 1
    assert "US$ 0.5000 (and 1 run without a cost)" in views._day(day, cfg)


def test_a_run_that_did_not_finish_says_so(filled: int, cfg: Config) -> None:
    with sqlite3.connect(cfg.database) as conn:
        conn.execute("UPDATE runs SET finished_at = NULL, cost_usd = NULL")
    page = views.activity_page(cfg, "t", "")[1].decode()
    assert "It did not finish" in page
    assert "no cost recorded" in page


@pytest.mark.parametrize(
    ("version", "message"), [(12, "older flib"), (999, "newer than this flib")]
)
def test_activity_on_an_index_it_cannot_read(
    filled: int, cfg: Config, version: int, message: str
) -> None:
    with sqlite3.connect(cfg.database) as conn:
        conn.execute(f"PRAGMA user_version = {version}")
    code, page = views.activity_page(cfg, "t", "")
    assert code == 503 and message in page.decode()


def test_activity_never_shows_a_database_error(
    monkeypatch: pytest.MonkeyPatch, filled: int, cfg: Config
) -> None:
    def broken(self: Store, since: str, *, limit: int) -> None:
        raise sqlite3.OperationalError("disk I/O error at /secret/path")

    monkeypatch.setattr(Store, "runs_since", broken)
    code, page = views.activity_page(cfg, "t", "")
    assert code == 503
    assert "Cannot read the index" in page.decode() and "/secret" not in page.decode()


# --- Open and Show in Finder -----------------------------------------------------------


def test_open_and_reveal_ask_macos_by_id(
    client: UiClient, cfg: Config, fake_browser: Path, downloads: Path
) -> None:
    ident = str(file_id(cfg, "power.pdf"))
    target = str(library(downloads) / "Finance" / "power.pdf")
    reply = post(client, "/files/open", id=ident)
    assert reply.status == 204 and reply.body == b""
    assert post(client, "/files/reveal", id=ident).status == 204
    assert opened(fake_browser) == [["--", target], ["-R", "--", target]]


@pytest.mark.parametrize(
    ("ident", "status"), [("", 400), ("x", 400), ("-1", 400), ("1" * 19, 400), ("9999", 404)]
)
def test_open_needs_a_known_id(
    client: UiClient, fake_browser: Path, ident: str, status: int
) -> None:
    reply = post(client, "/files/open", id=ident)
    assert reply.status == status
    assert_safe(reply.text)
    assert opened(fake_browser) == []


def test_open_never_takes_a_path(
    client: UiClient, cfg: Config, fake_browser: Path, downloads: Path
) -> None:
    ident = str(file_id(cfg, "power.pdf"))
    reply = post(client, "/files/open", id=ident, path=str(downloads / "id_rsa"))
    assert reply.status == 204
    assert opened(fake_browser) == [["--", str(library(downloads) / "Finance" / "power.pdf")]]


def test_open_refuses_a_sensitive_file(client: UiClient, cfg: Config, fake_browser: Path) -> None:
    reply = post(client, "/files/open", id=str(file_id(cfg, "id_rsa")))
    assert reply.status == 403
    assert "id_rsa" not in reply.text
    assert opened(fake_browser) == []


def test_open_rechecks_the_path_from_the_index(
    client: UiClient, cfg: Config, fake_browser: Path, tmp_path: Path
) -> None:
    ident = file_id(cfg, "power.pdf")
    with Store.open(cfg.database) as store:
        path = store.file_by_id(ident).current_path
    outside = tmp_path / "elsewhere.pdf"
    outside.write_bytes(b"x")
    with sqlite3.connect(cfg.database) as conn:
        conn.execute("UPDATE files SET current_path = ? WHERE id = ?", (str(outside), ident))
    reply = post(client, "/files/open", id=str(ident))
    assert reply.status == 403
    assert str(tmp_path) not in reply.text
    with sqlite3.connect(cfg.database) as conn:
        conn.execute("UPDATE files SET current_path = ? WHERE id = ?", (str(path), ident))
    path.unlink()
    path.symlink_to(outside)
    assert post(client, "/files/open", id=str(ident)).status == 403
    path.unlink()
    reply = post(client, "/files/open", id=str(ident))
    assert reply.status == 404
    assert "no longer where the index says" in reply.text
    assert str(path) not in reply.text
    assert opened(fake_browser) == []


def test_when_macos_cannot_open_it_the_page_says_so_plainly(
    monkeypatch: pytest.MonkeyPatch, client: UiClient, cfg: Config
) -> None:
    monkeypatch.setenv("FAKE_OPEN_FAIL", "1")
    reply = post(client, "/files/open", id=str(file_id(cfg, "power.pdf")))
    assert reply.status == 502
    assert "macOS could not open it." in reply.text
    assert "LSOpen" not in reply.text and "power.pdf" not in reply.text


def test_open_survives_a_missing_program(client: UiClient, cfg: Config) -> None:
    fields = {"id": [str(file_id(cfg, "power.pdf"))]}
    code, text = views.open_by_id(cfg, fields, reveal=False, program="/nonexistent/open")
    assert code == 502 and text == "macOS could not open it."


def test_open_without_an_index(empty: UiClient, cfg: Config) -> None:
    assert post(empty, "/files/open", id="1").status == 404
    assert not cfg.database.exists()


def test_open_on_an_index_it_cannot_read(filled: int, cfg: Config) -> None:
    with sqlite3.connect(cfg.database) as conn:
        conn.execute("PRAGMA user_version = 999")
    code, text = views.open_by_id(cfg, {"id": ["1"]}, reveal=True, program="x")
    assert code == 503 and "newer" in (text or "")


def test_the_buttons_post_the_id_and_the_csrf_token(client: UiClient, cfg: Config) -> None:
    text = search(client, q="power").text
    ident = file_id(cfg, "power.pdf")
    assert f'<input type="hidden" name="id" value="{ident}">' in text
    assert 'action="/files/reveal"' in text
    assert str(cfg.output_root) not in text.split("<ol", 1)[1].split("</ol>", 1)[0]


def test_a_missing_file_has_no_buttons(client: UiClient, cfg: Config) -> None:
    with Store.open(cfg.database) as store:
        store.file_by_id(file_id(cfg, "power.pdf")).current_path.unlink()
    text = search(client, q="power").text
    assert "missing: moved by hand?" in text
    assert 'action="/files/open"' not in text


# --- markup ----------------------------------------------------------------------------


def test_el_escapes_text_and_attributes() -> None:
    built = markup.el("a", '<b>"x"</b>', href='/" onclick="x', title=None, hidden=True)
    assert built == '<a href="/&quot; onclick=&quot;x" hidden>&lt;b&gt;&quot;x&quot;&lt;/b&gt;</a>'
    assert markup.el("p", markup.el("b", "x"), 3, None, ["&", ["<"]]) == "<p><b>x</b>3&amp;&lt;</p>"
    assert markup.el("input", type="text", value="<") == '<input type="text" value="&lt;">'


@pytest.mark.parametrize(
    "call",
    [
        lambda: markup.el("a b"),
        lambda: markup.el("script>"),
        lambda: markup.el("a", **{"on click": "x"}),
        lambda: markup.el("input", "child"),
    ],
)
def test_el_refuses_names_that_are_not_code(call: object) -> None:
    with pytest.raises(ValueError):
        call()  # type: ignore[operator]


def test_esc_escapes_once() -> None:
    once = markup.esc("<&>")
    assert once == "&lt;&amp;&gt;"
    assert markup.esc(once) == once
    assert markup.join("<", markup.Markup("<br>")) == "&lt;<br>"


def test_the_layout_fills_its_slots_in_one_pass() -> None:
    page = markup.layout("{{csrf}}", "due", markup.esc("{{csrf}} {{main}}"), "TOKEN").decode()
    assert page.count("TOKEN") == 1  # the End session form only
    assert "<title>{{csrf}} · file-librarian</title>" in page
    assert "{{csrf}} {{main}}" in page


def test_short_paths_and_times(cfg: Config, downloads: Path) -> None:
    assert views.short(library(downloads) / "Finance" / "a.pdf", cfg) == "Finance/a.pdf"
    assert views.short(downloads / "a.pdf", cfg) == "a.pdf"
    assert views.short(Path("/elsewhere/a.pdf"), cfg) == "/elsewhere/a.pdf"
    assert views.local_time(None) == ""
    assert views.local_time("garbage") == "garbage"
    assert len(views.local_time("2026-01-02T03:04:05")) == 5
    assert activity.local_day("2026-01-02T12:00:00") in (date(2026, 1, 2), date(2026, 1, 3))


# --- review of #124 --------------------------------------------------------------------


def set_path(cfg: Config, ident: int, path: Path) -> None:
    with sqlite3.connect(cfg.database) as conn:
        conn.execute("UPDATE files SET current_path = ? WHERE id = ?", (str(path), ident))


def test_a_dotdot_in_the_index_never_leaves_the_library(
    client: UiClient, cfg: Config, fake_browser: Path, tmp_path: Path
) -> None:
    outside = tmp_path / "outside.txt"
    outside.write_text("x")
    ident = file_id(cfg, "power.pdf")
    sneaky = cfg.output_root / ("../" * 30) / outside.relative_to("/")
    assert sneaky.is_relative_to(cfg.output_root)  # textually inside
    set_path(cfg, ident, sneaky)
    for route in ("/files/open", "/files/reveal"):
        assert post(client, route, id=str(ident)).status == 403
    assert opened(fake_browser) == []


def test_a_symlinked_folder_never_leaves_the_library(
    client: UiClient, cfg: Config, fake_browser: Path, tmp_path: Path, downloads: Path
) -> None:
    finance = library(downloads) / "Finance"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    for item in finance.iterdir():
        item.rename(elsewhere / item.name)
    finance.rmdir()
    finance.symlink_to(elsewhere)
    ident = str(file_id(cfg, "power.pdf"))
    for route in ("/files/open", "/files/reveal"):
        assert post(client, route, id=ident).status == 403
    assert opened(fake_browser) == []


def test_a_link_above_the_library_is_the_librarys_own(tmp_path: Path) -> None:
    """``/var`` is a link to ``/private/var``: a library under it still opens."""
    real = tmp_path / "real"
    (real / "Organized" / "Finance").mkdir(parents=True)
    (tmp_path / "link").symlink_to(real)
    cfg = fake_config(tmp_path / "link", output_root=str(tmp_path / "link" / "Organized"))
    target = tmp_path / "link" / "Organized" / "Finance" / "a.pdf"
    target.write_bytes(b"x")
    assert views.real_location(target, cfg) == real / "Organized" / "Finance" / "a.pdf"
    assert views.real_location(Path("Organized/a.pdf"), cfg) is None
    assert views.real_location(tmp_path / "elsewhere.pdf", cfg) is None


DANGEROUS = (
    "run-me.command",
    "term.terminal",
    "tool.tool",
    "place.fileloc",
    "site.webloc",
    "site.inetloc",
    "setup.pkg",
    "disk.dmg",
    "profile.mobileconfig",
    "flow.workflow",
    "app.jar",
    "script.sh",
    "page.html",
    "noextension",
)


def test_open_refuses_what_could_run_and_offers_show_in_finder(
    downloads: Path, cfg: Config, serve: list[Ui], fake_browser: Path
) -> None:
    fill(downloads, cfg, {name: answer("Other", confidence=0.95) for name in DANGEROUS})
    c = start(serve, cfg)
    for name in DANGEROUS:
        ident = str(file_id(cfg, name))
        assert post(c, "/files/open", id=ident).status == 403, name
        assert post(c, "/files/reveal", id=ident).status == 204, name
    assert all(call[0] == "-R" for call in opened(fake_browser))
    text = search(c, category=["Other"], limit="100").text
    assert text.count('action="/files/open"') == 0
    assert text.count('action="/files/reveal"') == len(DANGEROUS)


def test_open_refuses_folders_bundles_executables_and_aliases(
    client: UiClient, cfg: Config, downloads: Path
) -> None:
    folder = library(downloads) / "Finance"
    app = folder / "Thing.app"
    app.mkdir()
    assert not views.openable(app)
    assert not views.openable(folder / "missing.pdf")
    power = folder / "power.pdf"
    assert views.openable(power)
    power.chmod(0o755)
    assert not views.openable(power)
    power.chmod(0o644)
    write_xattr(power, "com.apple.FinderInfo", bytes(8) + b"\x80\x00" + bytes(22))
    assert not views.openable(power)
    write_xattr(power, "com.apple.FinderInfo", bytes(8) + b"\x01\x00" + bytes(22))
    assert views.openable(power)  # another Finder flag is fine
    assert "pdf" in views.OPENABLE_EXTENSIONS and "app" not in views.OPENABLE_EXTENSIONS


def test_format_characters_never_reach_the_page(
    downloads: Path, cfg: Config, serve: list[Ui]
) -> None:
    rlo = "invoice\u202efdp.command"
    fill(downloads, cfg, {rlo: answer("Finance", confidence=0.95)})
    c = start(serve, cfg)
    pages = [search(c, q="invoice").text, c.get("/activity").text]
    for text in pages:
        assert "\u202e" not in text
        assert "invoicefdp.command" in text
    assert views.shown("a\u200bb\u2066c") == "abc"


def test_the_export_is_not_cut_at_the_pages_limit(
    monkeypatch: pytest.MonkeyPatch, downloads: Path, cfg: Config, serve: list[Ui]
) -> None:
    fill(downloads, cfg, {f"bill{i}.pdf": answer("Finance", description="bill") for i in range(25)})
    c = start(serve, cfg)
    rows = search(c, "/search/export", q="bill", limit="20").text.splitlines()
    assert len(rows) == 1 + 25
    monkeypatch.setattr(views, "EXPORT_LIMIT", 10)
    rows = search(c, "/search/export", q="bill").text.splitlines()
    assert len(rows) == 1 + 10 + 1
    assert rows[-1].startswith("# cut: more than 10 matches")


@pytest.mark.parametrize(
    "path",
    [
        "/activity?evil=%3Cscript%3E",
        "/activity?days=7&x=",
        "/due?issuer=Power+Co",
        "/due?within=7d&x=1",
        "/search?anything",
    ],
)
def test_names_the_page_does_not_offer_are_refused(client: UiClient, path: str) -> None:
    assert client.get(path).status == 400


@pytest.mark.parametrize("fields", [{"by": "nope"}, {"limit": "7"}, {"limit": "5000"}])
def test_the_forms_fixed_choices_are_enforced(client: UiClient, fields: dict[str, str]) -> None:
    for route in ("/search", "/search/export"):
        reply = search(client, route, q="power", **fields)
        assert reply.status == 400, route


def semantic_cfg(downloads: Path, tmp_path: Path) -> Config:
    return fake_config(
        downloads,
        database=str(tmp_path / "l.db"),
        search={"semantic": {"enabled": True, "timeout_seconds": 30}},
    )


def with_vector(cfg: Config, name: str) -> None:
    """Give ``name`` an embedding, so a search by meaning embeds the query."""
    vector = array("f", [1.0, 0.0]).tobytes()
    with Store.open(cfg.database) as store:
        model = cfg.search.semantic.model
        store.save_embeddings([(file_id(cfg, name), model, 2, "h", vector)])


def test_the_embedder_is_made_only_when_the_query_is_embedded(
    monkeypatch: pytest.MonkeyPatch, downloads: Path, tmp_path: Path
) -> None:
    cfg = semantic_cfg(downloads, tmp_path)
    fill(downloads, cfg, {"power.pdf": answer("Finance")})
    made: list[float] = []

    class Fake:
        def __init__(self, settings: Any) -> None:
            made.append(settings.timeout_seconds)

        def embed(self, texts: Any) -> list[array[float]]:
            return [array("f", [1.0, 0.0])]

    monkeypatch.setattr(views, "Embedder", Fake)
    views.find(cfg, views.SearchForm(q="power", semantic=True))  # no vector yet: words only
    assert made == []
    with_vector(cfg, "power.pdf")
    found = views.find(cfg, views.SearchForm(q="power", semantic=True))
    assert made == [views.EMBED_TIMEOUT]  # at most 5 s per socket operation, not 30
    assert [Path(h.path).name for h, _ in found.hits] == ["power.pdf"]


def test_an_embedder_that_cannot_be_made_falls_back_to_words(
    monkeypatch: pytest.MonkeyPatch, downloads: Path, tmp_path: Path, serve: list[Ui]
) -> None:
    cfg = semantic_cfg(downloads, tmp_path)
    fill(downloads, cfg, {"power.pdf": answer("Finance")})
    with_vector(cfg, "power.pdf")

    def broken(settings: Any) -> None:
        raise RuntimeError("no key at /secret/keychain")

    monkeypatch.setattr(views, "Embedder", broken)
    reply = search(start(serve, cfg), q="power", semantic="1")
    assert reply.status == 200
    assert "Semantic search unavailable (cannot set up (RuntimeError))" in reply.text
    assert "searched by words only" in reply.text and "power.pdf" in reply.text
    assert "/secret" not in reply.text


def test_an_operation_on_a_file_now_sensitive_is_not_named(filled: int, cfg: Config) -> None:
    with sqlite3.connect(cfg.database) as conn:
        conn.execute(
            "UPDATE files SET status = 'sensitive' WHERE id = ?", (file_id(cfg, "power.pdf"),)
        )
    page = views.activity_page(cfg, "t", "")[1].decode()
    assert "power.pdf" not in page
    assert "water.pdf" in page


def test_every_open_and_reveal_form_carries_the_csrf_token(client: UiClient) -> None:
    token = client.csrf("/search")
    for text in (search(client, q="power").text, client.get("/due").text):
        forms = re.findall(r'<form method="post" action="/files/[a-z]+"[^>]*>(.*?)</form>', text)
        assert forms
        for form in forms:
            assert f'<input type="hidden" name="csrf" value="{token}">' in form


def test_due_offers_no_buttons_for_a_missing_file(client: UiClient, cfg: Config) -> None:
    with Store.open(cfg.database) as store:
        for name in ("power.pdf", "water.pdf", EVIL_NAME, "unsure.pdf"):
            store.file_by_id(file_id(cfg, name)).current_path.unlink()
    text = client.get("/due?within=1m").text
    assert "missing: moved by hand?" in text
    assert "/files/" not in text


# --- review of #124, round 2 -----------------------------------------------------------


def a_document(tmp_path: Path, name: str = "a.pdf") -> Path:
    path = tmp_path / name
    path.write_bytes(b"x")
    return path


@pytest.mark.parametrize(
    "name",
    [
        "a.PDF.command",
        "a.pdf.command",
        "a.pdf.",
        "a.pdf ",
        "a.\uff50\uff44\uff46",
        "a.pd\u200bf",
        "a.scpt",
        "a.svg",
        "a.doc",
        "a.xls",
        "a.ppt",
        "a",
    ],
)
def test_open_refuses_extension_tricks_and_legacy_office(tmp_path: Path, name: str) -> None:
    assert not views.openable(a_document(tmp_path, name))


def test_the_extension_is_compared_in_any_case(tmp_path: Path) -> None:
    assert views.openable(a_document(tmp_path, "a.PdF"))
    assert views.openable(a_document(tmp_path, "b.DOCX"))


def test_a_file_that_chose_its_own_app_is_not_opened(tmp_path: Path) -> None:
    path = a_document(tmp_path)
    write_xattr(path, "com.apple.LaunchServices.OpenWith", b"bplist00")
    assert not views.openable(path)
    typed = a_document(tmp_path, "b.pdf")
    write_xattr(typed, "com.apple.FinderInfo", b"APPLaaaa" + bytes(24))  # type, creator
    assert not views.openable(typed)
    creator = a_document(tmp_path, "c.pdf")
    write_xattr(creator, "com.apple.FinderInfo", bytes(7) + b"x" + bytes(24))
    assert not views.openable(creator)
    plain = a_document(tmp_path, "d.pdf")
    write_xattr(plain, "com.apple.FinderInfo", bytes(32))
    assert views.openable(plain)


@pytest.mark.parametrize("attribute", ["com.apple.FinderInfo", "com.apple.LaunchServices.OpenWith"])
def test_an_attribute_that_cannot_be_read_counts_against_opening(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, attribute: str
) -> None:
    path = a_document(tmp_path)
    real = origin.read_xattr

    def unreadable(where: Path, name: str) -> bytes | None:
        if name == attribute:
            raise OSError(5, "Input/output error")
        return real(where, name)

    monkeypatch.setattr(views, "read_xattr", unreadable)
    assert not views.openable(path)


def test_an_open_with_file_gets_show_in_finder_only(
    client: UiClient, cfg: Config, fake_browser: Path, downloads: Path
) -> None:
    power = library(downloads) / "Finance" / "power.pdf"
    write_xattr(power, "com.apple.LaunchServices.OpenWith", b"bplist00")
    ident = str(file_id(cfg, "power.pdf"))
    assert post(client, "/files/open", id=ident).status == 403
    assert post(client, "/files/reveal", id=ident).status == 204
    assert opened(fake_browser) == [["-R", "--", str(power)]]
    text = search(client, q="power").text
    assert 'action="/files/reveal"' in text and 'action="/files/open"' not in text


def test_open_is_given_the_real_path_under_a_link_above_the_root(
    filled: int, cfg: Config, downloads: Path, tmp_path: Path
) -> None:
    link = tmp_path / "home-link"
    link.symlink_to(downloads.parent)
    linked = fake_config(
        link / "Downloads",
        database=str(cfg.database),
        output_root=str(link / "Downloads" / "Organized"),
    )
    ident = file_id(cfg, "power.pdf")
    set_path(cfg, ident, link / "Downloads" / "Organized" / "Finance" / "power.pdf")
    calls: list[list[str]] = []

    def runner(argv: list[str]) -> int:
        calls.append(argv)
        return 0

    for reveal in (False, True):
        code, _ = views.open_by_id(
            linked, {"id": [str(ident)]}, reveal=reveal, program="open", runner=runner
        )
        assert code == 204
    real = str(library(downloads) / "Finance" / "power.pdf")
    assert calls == [["open", "--", real], ["open", "-R", "--", real]]


def test_a_link_below_the_root_into_the_library_is_refused(
    client: UiClient, cfg: Config, fake_browser: Path, downloads: Path
) -> None:
    lib = library(downloads)
    (lib / "Finance").rename(lib / "Finance2")
    (lib / "Finance").symlink_to(lib / "Finance2")
    ident = str(file_id(cfg, "power.pdf"))
    for route in ("/files/open", "/files/reveal"):
        assert post(client, route, id=ident).status == 403
    assert opened(fake_browser) == []


def test_due_rows_offer_open_only_for_documents(
    downloads: Path, cfg: Config, serve: list[Ui]
) -> None:
    fill(downloads, cfg, {"pay.command": bill(amount="1", due_date=soon(1))})
    text = start(serve, cfg).get("/due").text
    assert "pay.command" in text
    assert 'action="/files/reveal"' in text and 'action="/files/open"' not in text


def test_the_csv_cut_is_exactly_at_the_limit_and_ignores_sensitive_matches(
    monkeypatch: pytest.MonkeyPatch, downloads: Path, cfg: Config, serve: list[Ui]
) -> None:
    fill(downloads, cfg, {f"key {c}.txt": answer("Other") for c in "abc"})
    with sqlite3.connect(cfg.database) as conn:
        conn.execute(
            "UPDATE files SET status = 'sensitive' WHERE id = ?", (file_id(cfg, "key c.txt"),)
        )
    c = start(serve, cfg)

    def rows(limit: int) -> list[str]:
        monkeypatch.setattr(views, "EXPORT_LIMIT", limit)
        return search(c, "/search/export", q="key").text.splitlines()

    assert len(rows(2)) == 1 + 2  # two matches to show, the sensitive one not counted
    cut = rows(1)
    assert len(cut) == 1 + 1 + 1 and cut[-1].startswith("# cut: more than 1 matches")
    assert all("key c" not in line for line in rows(2) + cut)


def test_format_characters_are_dropped_from_every_name_shown(
    downloads: Path, cfg: Config, serve: list[Ui]
) -> None:
    name = "pay\u202efdp.pdf"
    fill(downloads, cfg, {name: bill(amount="1", due_date=soon(1), description="pay")})
    # flib files it under a clean name; give the index one that is not, as a
    # file renamed by hand and reconciled would have.
    ident = file_id(cfg, name)
    with Store.open(cfg.database) as store:
        filed = store.file_by_id(ident).current_path
    renamed = filed.with_name(name)
    filed.rename(renamed)
    set_path(cfg, ident, renamed)
    c = start(serve, cfg)
    for text in (search(c, q="pay").text, c.get("/due").text, c.get("/activity").text):
        assert "\u202e" not in text
        assert "payfdp.pdf" in text
    folder = library(downloads) / "Fi\u2066nance" / "a.pdf"
    assert views.short(folder, cfg) == "Finance/a.pdf"
    assert views.short(Path("/else\u200bwhere/a.pdf"), cfg) == "/elsewhere/a.pdf"


# --- the browser round of #124 ---------------------------------------------------------


HTML = {"Accept": "text/html,application/xhtml+xml"}


def test_the_referrer_policy_lets_a_browser_send_its_origin_on_a_post(client: UiClient) -> None:
    """Under ``no-referrer`` a browser sends ``Origin: null`` on a same-origin form post,
    which the server refuses: every POST but the launch failed in Chromium. The policy
    must be one that sends the origin to the same origin; ``null`` stays refused."""
    policy = client.get("/search").headers["Referrer-Policy"]
    assert policy == ui_server.REFERRER_POLICY == "same-origin"
    token = client.csrf("/search")
    for sender, status in ((client.origin, 303), ("null", 403)):
        reply = client.post_form("/search", {"csrf": token, "q": "power"}, {"Origin": sender})
        assert reply.status == status, sender


def test_a_search_is_kept_and_reached_by_a_get(client: UiClient) -> None:
    """Post, redirect, get: Back and Forward reload a GET, and the words never go in
    the address (only a random id does)."""
    token = client.csrf("/search")
    reply = client.post_form("/search", {"csrf": token, "q": "power"})
    assert reply.status == 303
    location = reply.headers["Location"]
    assert "power" not in location and location.startswith("/search?r=")
    again = client.get(location)
    assert "power.pdf" in again.text and 'value="power"' in again.text
    assert client.get(location).text.count("power.pdf") == again.text.count("power.pdf")
    assert csrf_value(again.text) == token  # the export button posts it again


def csrf_value(page: str) -> str:
    match = re.search(r'name="csrf" value="([^"]+)"', page)
    assert match is not None
    return match.group(1)


def test_a_kept_search_belongs_to_its_session(serve: list[Ui], cfg: Config, filled: int) -> None:
    c = start(serve, cfg)
    location = search_location(c, q="power")
    ui = serve[0]
    other = UiClient(ui.port, ui.host_name)
    assert other.get(location).status == 403  # no session: no search
    ui.end_session()
    assert ui.saved_search(views_session(ui), location.split("=", 1)[1]) is None


def views_session(ui: Ui) -> ui_server.Session:
    return ui_server.Session("someone-else", "x")


def search_location(c: UiClient, **fields: str) -> str:
    reply = c.post_form("/search", {"csrf": c.csrf("/search"), **fields})
    assert reply.status == 303
    return reply.headers["Location"]


@pytest.mark.parametrize("key", ["nope", "", "x" * 16])
def test_a_search_no_longer_kept_says_so(client: UiClient, key: str) -> None:
    reply = client.get(f"/search?r={key}")
    assert reply.status == 200
    assert "That search is no longer kept" in reply.text
    assert "<ol" not in reply.text


def test_only_the_latest_searches_are_kept(
    monkeypatch: pytest.MonkeyPatch, client: UiClient
) -> None:
    monkeypatch.setattr(ui_server, "MAX_SAVED_SEARCHES", 2)
    first = search_location(client, q="power")
    second = search_location(client, q="water")
    third = search_location(client, q="notes")
    assert "no longer kept" in client.get(first).text
    for kept in (second, third):
        assert "no longer kept" not in client.get(kept).text


@pytest.mark.parametrize("query", ["r=a&r=b", "q=power", "r=x&q=y"])
def test_the_kept_search_address_takes_only_its_id(client: UiClient, query: str) -> None:
    assert client.get(f"/search?{query}").status == 400


def test_a_bad_fixed_choice_is_refused_before_it_is_kept(client: UiClient) -> None:
    reply = client.post_form("/search", {"csrf": client.csrf("/search"), "by": "nope"})
    assert reply.status == 400


def test_the_ended_page_still_gets_its_stylesheet(serve: list[Ui], cfg: Config) -> None:
    c = start(serve, cfg)
    ui = serve[0]
    assert c.post_form("/session/end", {"csrf": c.csrf()}).status == 200
    css = UiClient(ui.port, ui.host_name).get("/static/app.css")  # what the browser asks for next
    assert css.status == 200 and css.headers["Content-Type"].startswith("text/css")
    assert UiClient(ui.port, ui.host_name).get("/status").status == 403  # nothing else
    assert ui.wait(5) == "ended"


def test_closing_does_not_wait_out_the_linger(serve: list[Ui], cfg: Config) -> None:
    c = start(serve, cfg)
    ui = serve[0]
    ui.end_linger = 30.0
    c.post_form("/session/end", {"csrf": c.csrf()})
    began = time.monotonic()
    ui.stop("closed")
    assert ui.wait(5) == "ended"
    assert time.monotonic() - began < 5


@pytest.mark.parametrize(
    ("request_", "status", "text"),
    [
        (("GET", "/due?within=9y"), 400, "does not offer"),
        (("GET", "/status?x=1"), 400, "does not offer"),
        (("GET", "/nothing/here"), 404, "There is no such page here"),
        (("POST", "/search/export"), 403, "Reload the page and try again"),
    ],
)
def test_a_browser_gets_a_refusal_as_a_page(
    client: UiClient, request_: tuple[str, str], status: int, text: str
) -> None:
    method, path = request_
    if method == "GET":
        reply = client.get(path, headers=HTML)
    else:
        reply = client.post_form(path, {"csrf": "stale"}, HTML)
    assert reply.status == status
    assert reply.headers["Content-Type"] == "text/html; charset=utf-8"
    assert text in reply.text
    assert 'aria-label="Pages"' in reply.text  # the menu, to go on from there
    assert_safe(reply.text)
    plain = client.get(path) if method == "GET" else client.post_form(path, {"csrf": "x"})
    assert plain.headers["Content-Type"] == "text/plain; charset=utf-8"  # a script: plain


def test_a_form_from_an_earlier_session_says_so(serve: list[Ui], cfg: Config) -> None:
    c = start(serve, cfg)
    stale = UiClient(c.port, c.name)  # its cookie went with the earlier session
    reply = stale.post_form("/search", {"csrf": "old", "q": "x"}, HTML)
    assert reply.status == 403
    assert "earlier flib ui session" in reply.text
    assert 'action="/session/end"' not in reply.text  # not this browser's to end


def test_the_status_page_takes_no_query(client: UiClient) -> None:
    assert client.get("/status?x=1").status == 400
    assert client.get("/status").status == 200


def test_a_browser_gets_an_icon_at_the_usual_address(empty: UiClient) -> None:
    reply = UiClient(empty.port, empty.name).get("/favicon.ico")  # no session needed, like the CSS
    assert reply.status == 200 and reply.headers["Content-Type"] == "image/png"


def test_idle_minutes_are_counted_in_words(
    monkeypatch: pytest.MonkeyPatch, downloads: Path
) -> None:
    lines: list[str] = []
    one = fake_config(downloads, ui={"idle_minutes": 1})

    real_start = ui_server.Ui.start

    def start_and_stop(self: Ui) -> None:
        real_start(self)
        self.stop("signal")

    def run_and_stop(config: Config) -> None:
        monkeypatch.setattr(ui_server.Ui, "start", start_and_stop)
        ui_server.run(config, browser=False, echo=lines.append)

    run_and_stop(one)
    assert any("It ends after 1 minute without use" in line for line in lines)
    assert not any("1 minutes" in line or "idle minutes" in line for line in lines)


# --- functional fixes ------------------------------------------------------------------


def test_a_file_put_back_by_undo_is_listed_without_buttons(
    filled: int, cfg: Config, serve: list[Ui], downloads: Path
) -> None:
    with Store.open(cfg.database) as store:
        undo_run(store, filled, config=cfg)
    text = search(start(serve, cfg), q="power").text
    assert "power.pdf" in text  # as `flib search` lists it
    assert "not filed: back in Downloads" in text
    assert ">Downloads/</div>" in text and ">./</div>" not in text
    assert "/files/" not in text.split("<ol", 1)[1]


def test_an_activity_move_shows_its_own_folder(filled: int, cfg: Config) -> None:
    """The folder the move used, whatever the file is now (here: recategorised)."""
    with sqlite3.connect(cfg.database) as conn:
        conn.execute(
            "UPDATE files SET category = 'Other' WHERE id = ?", (file_id(cfg, "power.pdf"),)
        )
    page = views.activity_page(cfg, "t", "")[1].decode()
    assert ">Finance/</span>" in page
    assert ">Other<" not in page


def test_a_renamed_file_shows_its_new_name_in_activity(cfg: Config, downloads: Path) -> None:
    move = activity.Move(
        file_id=1,
        source=downloads / "scan.pdf",
        destination=library(downloads) / "Finance" / "Power bill.pdf",
        status=OperationStatus.DONE,
        file_status=FileStatus.ORGANIZED,
        category="Finance",
        fix=False,
    )
    assert views._destination(move, cfg) == "Finance/Power bill.pdf"


def test_sensitive_matches_never_take_a_shown_matchs_place(
    downloads: Path, cfg: Config, serve: list[Ui]
) -> None:
    fill(downloads, cfg, {f"key {c}.txt": answer("Other") for c in "abc"})
    with sqlite3.connect(cfg.database) as conn:
        conn.execute(
            "UPDATE files SET status = 'sensitive' WHERE id = ?", (file_id(cfg, "key a.txt"),)
        )
    text = search(start(serve, cfg), q="key", limit="20").text
    assert "2 matches" in text and "1 sensitive file also matched" in text
    found = views.find(cfg, views.SearchForm(q="key", limit=1))
    assert len(found.hits) == 1


@pytest.mark.parametrize("version", [17, 18])
def test_every_page_reads_an_index_one_schema_behind(
    filled: int, cfg: Config, serve: list[Ui], version: int
) -> None:
    with sqlite3.connect(cfg.database) as conn:
        conn.execute(f"PRAGMA user_version = {version}")
    c = start(serve, cfg)
    for path in ("/activity", "/due", "/search"):
        assert c.get(path).status == 200, path
    assert "power.pdf" in search(c, q="power").text


def test_every_page_says_the_same_about_an_index_too_old(
    filled: int, cfg: Config, serve: list[Ui]
) -> None:
    with sqlite3.connect(cfg.database) as conn:
        conn.execute("PRAGMA user_version = 16")
    c = start(serve, cfg)
    for path in ("/activity", "/due", "/search"):
        reply = c.get(path)
        assert reply.status == 503, path
        assert "The index is from an older flib; the next <code>flib run</code>" in reply.text


def test_a_file_in_review_has_one_review_badge(client: UiClient) -> None:
    text = search(client, q="unsure").text
    hit = text.split("<ol", 1)[1]
    assert hit.count(">in review<") == 1 and ">review<" not in hit


def test_due_says_how_far_back_the_window_reaches(client: UiClient) -> None:
    text = client.get("/due").text
    assert f"{due.LOOKBACK_DAYS} days back" in text
    assert "Past due, however long ago" in client.get("/due?overdue=1").text


# --- layout ----------------------------------------------------------------------------


def test_the_count_and_totals_come_before_the_matches(client: UiClient) -> None:
    text = search(client, kind=["bill"], by="issuer").text
    count, totals, hits = (text.index(t) for t in ('class="count"', 'class="sums"', "<ol"))
    assert count < totals < hits
    assert "<title>Search: 4 matches · file-librarian</title>" in text
    assert '<details class="filters">' in text  # folded once there are results
    assert '<details class="filters" open>' in client.get("/search").text


def test_grouped_totals_are_sorted_by_group_then_currency(client: UiClient) -> None:
    text = search(client, category=["Finance"], by="issuer").text
    table = text.split("By issuer", 1)[1].split("</table>", 1)[0]
    groups = re.findall(r"<tr><td>([^<]*)</td>", table)
    assert groups == sorted(groups, key=str.casefold)
    assert '<th scope="col" class="num">Files</th>' in table


def test_due_cells_carry_their_labels_for_narrow_screens(client: UiClient) -> None:
    text = client.get("/due").text
    for label in ("Due", "When", "Amount", "Issuer", "Kind", "File"):
        assert f'data-label="{label}"' in text
    assert 'class="date"' in text and 'class="buttons"' in text


def test_commands_in_messages_are_code(empty: UiClient) -> None:
    text = empty.get("/activity").text
    assert "the first <code>flib run</code>." in text and "`flib run`" not in text


def test_the_frame_has_a_skip_link_and_end_session(client: UiClient) -> None:
    for path in ("/status", "/search", "/due", "/activity"):
        text = client.get(path).text
        assert '<a class="skip" href="#main">Skip to content</a>' in text
        assert '<main id="main" tabindex="-1">' in text
        assert "coming soon" not in text
        assert '<button type="submit" class="quiet">End session</button>' in text


@pytest.mark.parametrize(
    ("value", "text"), [(None, "no cost recorded"), (0.0, "no cost"), (0.0123, "US$ 0.0123")]
)
def test_one_money_format(value: float | None, text: str) -> None:
    assert views.money(value) == text


def test_plurals_and_sentences(client: UiClient) -> None:
    assert views.plural(1, "match", "matches") == "1 match"
    assert views.plural(2, "match", "matches") == "2 matches"
    assert views.sentence("macOS could not") == "macOS could not"
    assert views.sentence("cannot read") == "Cannot read"
    text = client.get("/activity").text
    assert "(s)" not in text
    assert "0 duplicates set aside" in text


def test_who_owns_a_handoff_folder(tmp_path: Path) -> None:
    assert ui_server._handoff_owner("ui-launch-123-abcdefgh") == 123
    assert ui_server._handoff_owner("ui-launch-abcdefgh") is None
    assert ui_server._alive(os.getpid()) and ui_server._alive(1)  # 1: someone else's
    assert not ui_server._alive(2**22 + 12345)
    (tmp_path / "ui-launch-file").write_text("not a folder")
    ui_server.sweep_handoffs(tmp_path, now=time.time() + 10_000)
    assert (tmp_path / "ui-launch-file").exists()  # only folders


def test_prose_marks_commands_and_leaves_a_stray_backtick() -> None:
    assert markup.prose("run `flib run` now") == "run <code>flib run</code> now"
    assert markup.prose("a ` b <") == "a ` b &lt;"

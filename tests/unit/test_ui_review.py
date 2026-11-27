"""``flib ui``'s Review page and undo (#115): the first routes that move files.

Each action is driven through a real server on 127.0.0.1 as a browser sends it
(a form post with the session cookie, the exact origin and the CSRF token), over
a real index the fake provider filled. Every test checks the disk and the index,
not only the page: the file really moved (or did not), the journal and the
corrections say so, and undo gives back the same bytes in the same place.
"""

from __future__ import annotations

import hashlib
import os
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from file_librarian.config import Config
from file_librarian.db.store import FileRow, Store
from file_librarian.fix import FixError, FixItem, fix_file, fix_files
from file_librarian.lock import instance_lock
from file_librarian.models import FileStatus, ReviewReason
from file_librarian.ui import actions, review
from file_librarian.ui import server as ui_server
from file_librarian.ui.server import Ui
from file_librarian.undo import undo_run
from tests._helpers import UiClient, UiResponse, answer, put
from tests.unit.test_ui import (  # noqa: F401 - fixtures: every server is checked the same way
    _Collector,
    events,
    no_hang,
    nothing_left_behind,
)
from tests.unit.test_ui_pages import (
    EVIL_NAME,
    EVIL_TEXT,
    assert_safe,
    bill,
    fill,
    soon,
    start,
)

UNSURE = 0.3  # below the review threshold (0.7)

REVIEW = {
    "shot-1.png": answer("Images", confidence=UNSURE, description="a screen capture"),
    "shot-2.png": answer("Images", confidence=UNSURE, description="another capture"),
    "shot-3.png": answer("Images", confidence=UNSURE, description="a third capture"),
    EVIL_NAME: answer("Finance", confidence=UNSURE, description=EVIL_TEXT),
    "recipe.txt": answer("Recipes", confidence=0.9, description="not a configured category"),
    "filed.pdf": answer("Finance", confidence=0.95, description="filed with confidence"),
}


@pytest.fixture
def serve(loopback_network: None) -> Iterator[list[Ui]]:
    """Servers started with ``start``, all closed at the end."""
    started: list[Ui] = []
    yield started
    for ui in started:
        ui.close()


@pytest.fixture
def seeded(downloads: Path, cfg: Config) -> int:
    return fill(downloads, cfg, REVIEW)


@pytest.fixture
def client(serve: list[Ui], cfg: Config, seeded: int) -> UiClient:
    return start(serve, cfg)


def review_folder(cfg: Config) -> Path:
    return cfg.output_root / cfg.review_folder


def row(cfg: Config, original: str) -> FileRow:
    with Store.open_read_only(cfg.database) as store:  # never migrates, never writes
        return next(r for r in store.all_files() if r.original_name == original)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def group_of(c: UiClient, suggested: str) -> str:
    """A review group's key as the page names it (keyed with the session's secret)."""
    match = re.search(
        rf'id="g-([0-9a-f]{{16}})"><h2>[^<]*: suggested {suggested} ', c.get("/review").text
    )
    assert match is not None, suggested
    return match.group(1)


def pin_on_page(page: str, file_id: int) -> str:
    match = re.search(
        rf'name="id" value="{file_id}"><input type="hidden" name="pin" value="([0-9a-f]+)"', page
    )
    assert match is not None, f"no form for file {file_id}"
    return match.group(1)


def act(c: UiClient, route: str, **fields: str) -> UiResponse:
    """Post an action as the page's form does, and follow the redirect it answers with."""
    reply = c.post_form(route, {"csrf": c.csrf("/review")} | fields)
    assert reply.status == 303, (reply.status, reply.text)
    location = reply.headers["Location"]
    pattern = r"/(review|undo|due)\?((within=\w+|overdue=1)&)?(all=1&)?n=[A-Za-z0-9_-]{16}"
    assert re.fullmatch(pattern + r"(#g-[0-9a-f]{16})?", location), location
    page = c.get(location.split("#", 1)[0])  # a browser never sends the fragment
    assert page.status == 200
    assert_safe(page.text)
    return page


def accept(c: UiClient, cfg: Config, original: str, **extra: str) -> UiResponse:
    r = row(cfg, original)
    pin = pin_on_page(c.get("/review").text, r.id)
    fields = {"id": str(r.id), "pin": pin, "category": r.review_category or ""} | extra
    return act(c, "/review/file", **fields)


def snapshot(cfg: Config) -> tuple[bytes, list[tuple[str, int]]]:
    """The index's bytes and every file under the test home, to show nothing changed."""
    with sqlite3.connect(cfg.database) as conn:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    root = cfg.input_paths[0]
    files = sorted(
        (str(p.relative_to(root)), p.stat().st_size) for p in root.rglob("*") if p.is_file()
    )
    return cfg.database.read_bytes(), files


def undo_form(c: UiClient, query: str) -> dict[str, str]:
    page = c.get(f"/undo?{query}").text
    assert_safe(page)
    found = dict(re.findall(r'name="(run|file|pin)" value="([^"]+)"', page))
    assert "pin" in found, page
    return found


# --- the page --------------------------------------------------------------------------


def test_the_review_page_groups_the_files_and_lists_no_sensitive_one(
    client: UiClient, cfg: Config
) -> None:
    page = client.get("/review")
    assert page.status == 200
    assert_safe(page.text)
    text = page.text
    assert "5 files in review" in text
    assert "Not sure: suggested Images" in text  # the biggest group first
    assert text.index("suggested Images") < text.index("suggested Finance")
    assert "Category not configured: suggested Recipes" in text
    assert "Accept Images for all 3" in text
    assert "id_rsa" not in text and "filed.pdf" not in text
    assert "&lt;img src=x onerror=alert(1)&gt;" in text  # the hostile name, as text
    # A suggestion that is not configured has nothing to accept, only Move (open).
    recipe = row(cfg, "recipe.txt")
    assert f'value="{recipe.id}"' in text
    assert "Accept: Recipes" not in text


def test_the_status_page_and_the_menu_link_review(client: UiClient) -> None:
    status = client.get("/status").text
    assert status.count('href="/review"') == 3  # the menu, the card's title, its link
    assert 'href="/review" aria-current="page"' in client.get("/review").text


def test_flib_ui_can_open_on_review() -> None:
    assert "review" in ui_server.PAGES


def test_review_needs_the_session(client: UiClient) -> None:
    for path in ("/review", "/review/accept?g=0123456789abcdef", "/undo?run=1"):
        assert UiClient(client.port, client.name).get(path).status == 403


def test_an_empty_library_creates_nothing(serve: list[Ui], cfg: Config) -> None:
    c = start(serve, cfg)
    assert "No run yet" in c.get("/review").text
    page = act(c, "/review/file", id="1", pin="0" * 20, category="Finance")
    assert "No run yet" in page.text
    assert "No run yet" in c.get("/undo?run=1").text
    assert not cfg.database.exists()


def test_nothing_in_review_says_so(serve: list[Ui], cfg: Config, downloads: Path) -> None:
    fill(downloads, cfg, {"filed.pdf": answer("Finance", confidence=0.95)})
    assert "Nothing waits for review" in start(serve, cfg).get("/review").text


@pytest.mark.parametrize(
    "path",
    [
        "/review?x=1",
        "/review?n=a&n=b",
        "/review/accept",
        "/review/accept?g=zz",
        "/review/accept?g=0123456789abcdeg",
        "/undo",
        "/undo?run=1&file=2",
        "/undo?run=x",
        "/undo?file=-1",
        "/undo?run=1234567890123456789",
        "/undo?path=/etc",
    ],
)
def test_bad_addresses_are_refused(client: UiClient, path: str) -> None:
    assert client.get(path).status == 400


def test_getting_every_page_changes_nothing(client: UiClient, cfg: Config) -> None:
    before = snapshot(cfg)
    r = row(cfg, "shot-1.png")
    group = group_of(client, "Images")
    for path in (
        "/review",
        f"/review/accept?g={group}",
        f"/undo?file={r.id}",
        "/undo?run=1",
        "/activity",
        "/due",
        "/review?n=AAAAAAAAAAAAAAAA",
    ):
        assert client.get(path).status == 200, path
    assert snapshot(cfg) == before


# --- one file --------------------------------------------------------------------------


def test_accept_moves_the_file_journals_it_and_undo_restores_it(
    client: UiClient, cfg: Config
) -> None:
    before = row(cfg, "shot-1.png")
    content = digest(before.current_path)
    page = accept(client, cfg, "shot-1.png")
    assert "is now in Images." in page.text
    after = row(cfg, "shot-1.png")
    assert after.status is FileStatus.ORGANIZED and after.category == "Images"
    assert after.current_path == cfg.output_root / "Images" / "shot-1.png"
    assert not before.current_path.exists() and digest(after.current_path) == content
    assert after.review_reason is None
    with Store.open(cfg.database) as store:
        op = store.last_done_operation(after.id)
        assert op is not None and op.fix == (FileStatus.ORGANIZED, "Images")
        corrections = [(c.field, c.old_value, c.new_value) for c in store.current_corrections()]
    assert ("category", None, "Images") in corrections
    # "Undo this…": the confirmation (a GET), then the POST.
    link = re.search(r'href="(/undo\?run=\d+)"', page.text)
    assert link is not None
    form = undo_form(client, link.group(1).split("?", 1)[1])
    assert "shot-1.png" in client.get(link.group(1)).text
    done = act(client, "/undo", **form)
    assert "1 restored, 0 skipped" in done.text
    back = row(cfg, "shot-1.png")
    assert back.current_path == before.current_path and digest(back.current_path) == content
    assert back.status is FileStatus.REVIEW
    assert back.review == (ReviewReason.LOW_CONFIDENCE, "Images")
    assert not after.current_path.exists()


def test_move_to_another_category_with_a_new_name_is_learned(client: UiClient, cfg: Config) -> None:
    page = accept(client, cfg, "shot-2.png", category="Other", name="holiday")
    assert "is now in Other." in page.text
    after = row(cfg, "shot-2.png")
    assert after.current_path == cfg.output_root / "Other" / "holiday.png"  # extension kept
    with Store.open(cfg.database) as store:
        fields = {c.field: c.new_value for c in store.current_corrections()}
    assert fields == {"category": "Other", "name": "holiday.png"}


def test_a_rename_alone_stays_in_review(client: UiClient, cfg: Config) -> None:
    page = accept(client, cfg, "shot-3.png", category="", name="renamed")
    assert "is its new name; it stays in review." in page.text
    after = row(cfg, "shot-3.png")
    assert after.status is FileStatus.REVIEW
    assert after.current_path == review_folder(cfg) / "renamed.png"


def test_neither_a_category_nor_a_name_does_nothing(client: UiClient, cfg: Config) -> None:
    before = snapshot(cfg)
    page = accept(client, cfg, "shot-1.png", category="", name="  ")
    assert "Pick a category or type a new name" in page.text
    assert snapshot(cfg)[1] == before[1]


@pytest.mark.parametrize("name", ["a/b", "..", ".hidden", "x:y"])
def test_a_new_name_goes_through_the_fix_sanitiser(
    client: UiClient, cfg: Config, name: str
) -> None:
    before = snapshot(cfg)
    page = accept(client, cfg, "shot-1.png", name=name)
    assert "is not a plain file name" in page.text or "already there" in page.text
    assert snapshot(cfg)[1] == before[1]


def test_a_name_too_long_is_refused(client: UiClient, cfg: Config) -> None:
    page = accept(client, cfg, "shot-1.png", name="x" * 300)
    assert "longer than macOS allows" in page.text
    r = row(cfg, "shot-1.png")
    pin = pin_on_page(client.get("/review").text, r.id)
    reply = client.post_form(
        "/review/file",
        {"csrf": client.csrf(), "id": str(r.id), "pin": pin, "name": "x" * 2000},
    )
    assert reply.status == 400


def test_a_taken_name_gets_the_next_number_and_nothing_is_overwritten(
    client: UiClient, cfg: Config
) -> None:
    taken = put(cfg.output_root / "Images", "shot-1.png", b"already here")
    accept(client, cfg, "shot-1.png")
    assert taken.read_bytes() == b"already here"
    moved = row(cfg, "shot-1.png").current_path
    assert moved.parent == taken.parent and moved.name.startswith("shot-1-")
    assert moved.read_bytes() == b"shot-1.png"


def test_a_stale_row_is_refused(client: UiClient, cfg: Config) -> None:
    r = row(cfg, "shot-1.png")
    pin = pin_on_page(client.get("/review").text, r.id)
    with Store.open(cfg.database) as store:  # the command line handles it meanwhile
        fix_file(cfg, store, r.current_path, name="by hand")
    page = act(client, "/review/file", id=str(r.id), pin=pin, category="Images")
    assert actions.STALE[:40] in page.text
    assert row(cfg, "shot-1.png").current_path == review_folder(cfg) / "by hand.png"


def test_a_double_submit_moves_once(client: UiClient, cfg: Config) -> None:
    r = row(cfg, "shot-1.png")
    fields = {
        "id": str(r.id),
        "pin": pin_on_page(client.get("/review").text, r.id),
        "category": "Images",
    }
    assert "is now in Images." in act(client, "/review/file", **fields).text
    again = act(client, "/review/file", **fields | {"category": "Other"})
    assert actions.STALE[:40] in again.text
    assert row(cfg, "shot-1.png").category == "Images"
    with Store.open(cfg.database) as store:
        assert len([op for op in store.operations() if op.file_id == r.id and op.fix]) == 1


def test_two_tabs_one_wins(serve: list[Ui], cfg: Config, seeded: int) -> None:
    """Two windows of one session, each with the page as it was."""
    tab = start(serve, cfg)
    other = UiClient(tab.port, tab.name)
    other.cookies = dict(tab.cookies)
    r = row(cfg, "shot-1.png")
    first, second = (pin_on_page(c.get("/review").text, r.id) for c in (tab, other))
    act(tab, "/review/file", id=str(r.id), pin=first, category="Images")
    page = act(other, "/review/file", id=str(r.id), pin=second, category="Other")
    assert actions.STALE[:40] in page.text
    assert row(cfg, "shot-1.png").category == "Images"


def test_a_run_in_progress_holds_everything(client: UiClient, cfg: Config) -> None:
    r = row(cfg, "shot-1.png")
    pin = pin_on_page(client.get("/review").text, r.id)
    group = group_of(client, "Images")
    with instance_lock(cfg.database):  # a run holds the lock
        before = snapshot(cfg)
        page = act(client, "/review/file", id=str(r.id), pin=pin, category="Images")
        assert actions.BUSY in page.text and f"{os.getpid()}" not in page.text
        assert "Nothing was done" in page.text
        form = dict(
            re.findall(r'name="(g|ids|pin)" value="([^"]+)"', client.get(
                f"/review/accept?g={group}").text)
        )  # fmt: skip
        assert actions.BUSY in act(client, "/review/accept", **form).text
        assert snapshot(cfg)[1] == before[1]
    assert (
        "is now in Images."
        in act(client, "/review/file", id=str(r.id), pin=pin, category="Images").text
    )


def test_a_missing_file_has_no_buttons_and_is_refused(client: UiClient, cfg: Config) -> None:
    r = row(cfg, "shot-1.png")
    pin = pin_on_page(client.get("/review").text, r.id)
    r.current_path.unlink()
    page = client.get("/review").text
    assert "missing: moved by hand?" in page
    assert f'name="id" value="{r.id}"' not in page
    reply = act(client, "/review/file", id=str(r.id), pin=pin, category="Images")
    assert "This file changed or moved since it was indexed" in reply.text
    assert str(cfg.output_root) not in reply.text  # never a path


def test_a_sensitive_file_cannot_be_acted_on(client: UiClient, cfg: Config) -> None:
    r = row(cfg, "shot-1.png")
    pin = pin_on_page(client.get("/review").text, r.id)
    with Store.open(cfg.database) as store, store.transaction():
        store.set_file_status(r.id, FileStatus.SENSITIVE)
    assert "shot-1.png" not in client.get("/review").text
    page = act(client, "/review/file", id=str(r.id), pin=pin, category="Images")
    assert actions.SENSITIVE[:30] in page.text and "shot-1.png" not in page.text
    assert r.current_path.exists()
    undo = client.get(f"/undo?file={r.id}").text
    assert actions.SENSITIVE[:30] in undo and "shot-1.png" not in undo
    assert 'name="pin"' not in undo


def test_a_file_no_longer_in_the_index_is_refused(client: UiClient, cfg: Config) -> None:
    page = act(client, "/review/file", id="999999", pin="0" * 20, category="Images")
    assert actions.GONE in page.text


@pytest.mark.parametrize(
    "fields",
    [
        {"id": "x", "pin": "0" * 20, "category": "Images"},
        {"id": "", "pin": "0" * 20, "category": "Images"},
        {"id": "1" * 19, "pin": "0" * 20, "category": "Images"},
        {"id": "\u0661", "pin": "0" * 20, "category": "Images"},
        {"pin": "0" * 20, "category": "Images"},
        {"id": "1", "category": "Images"},
        {"id": "1", "pin": "0" * 19, "category": "Images"},
        {"id": "1", "pin": "G" * 20, "category": "Images"},
        {"id": "1", "pin": "0" * 20, "category": "../../etc"},
        {"id": "1", "pin": "0" * 20, "category": "Recipes"},
        {"id": "1", "pin": "0" * 20, "category": "images"},  # exact names only
    ],
)
def test_a_form_the_page_never_sends_is_refused(
    client: UiClient, cfg: Config, fields: dict[str, str]
) -> None:
    before = snapshot(cfg)
    reply = client.post_form("/review/file", {"csrf": client.csrf()} | fields)
    assert reply.status == 400
    assert snapshot(cfg)[1] == before[1]


def test_two_ids_are_refused(client: UiClient) -> None:
    body = f"csrf={client.csrf()}&id=1&id=2&pin={'0' * 20}&category=Images"
    reply = client.request(
        "POST",
        "/review/file",
        body=body.encode(),
        headers={
            "Origin": client.origin,
            "Sec-Fetch-Site": "same-origin",
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    assert reply.status == 400


def test_a_path_in_the_form_is_ignored(client: UiClient, cfg: Config) -> None:
    r, other = row(cfg, "shot-1.png"), row(cfg, "shot-2.png")
    pin = pin_on_page(client.get("/review").text, r.id)
    act(client, "/review/file", id=str(r.id), pin=pin, category="Images",
        path=str(other.current_path))  # fmt: skip
    assert row(cfg, "shot-1.png").category == "Images"
    assert row(cfg, "shot-2.png").status is FileStatus.REVIEW


def test_an_older_index_is_never_written(client: UiClient, cfg: Config) -> None:
    r = row(cfg, "shot-1.png")
    pin = pin_on_page(client.get("/review").text, r.id)
    with sqlite3.connect(cfg.database) as conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        conn.execute(f"PRAGMA user_version = {version - 1}")
    page = act(client, "/review/file", id=str(r.id), pin=pin, category="Images")
    assert "another version of flib" in page.text
    assert row(cfg, "shot-1.png").status is FileStatus.REVIEW


def test_a_kept_outcome_belongs_to_its_session(client: UiClient, cfg: Config) -> None:
    page = accept(client, cfg, "shot-1.png")
    assert "is now in Images." in page.text
    assert "is no longer kept" in client.get("/review?n=BBBBBBBBBBBBBBBB").text


def test_reload_back_and_forward_never_act_again(client: UiClient, cfg: Config) -> None:
    r = row(cfg, "shot-1.png")
    pin = pin_on_page(client.get("/review").text, r.id)
    reply = client.post_form(
        "/review/file", {"csrf": client.csrf(), "id": str(r.id), "pin": pin, "category": "Images"}
    )
    location = reply.headers["Location"]
    before = snapshot(cfg)
    for _ in range(3):
        assert "is now in Images." in client.get(location).text
    assert snapshot(cfg) == before


def test_outcomes_end_with_the_session(serve: list[Ui], cfg: Config, seeded: int) -> None:
    c = start(serve, cfg)
    ui = serve[-1]
    r = row(cfg, "shot-1.png")
    pin = pin_on_page(c.get("/review").text, r.id)
    reply = c.post_form(
        "/review/file", {"csrf": c.csrf(), "id": str(r.id), "pin": pin, "category": "Images"}
    )
    key = reply.headers["Location"].split("#", 1)[0].rsplit("=", 1)[1]
    session = ui.session(c.cookies[ui.cookie_name])
    assert session is not None and ui.kept_outcome(session, key) is not None
    stranger = ui_server.Session("someone", "else")
    assert ui.kept_outcome(stranger, key) is None
    ui.end_session()
    assert ui.kept_outcome(session, key) is None


def test_only_the_latest_outcomes_are_kept(serve: list[Ui], cfg: Config) -> None:
    start(serve, cfg)
    ui = serve[-1]
    session = ui_server.Session("s", "c")
    keys = [ui.keep_outcome(session, actions.Outcome(str(i))) for i in range(40)]
    assert ui.kept_outcome(session, keys[0]) is None
    assert ui.kept_outcome(session, keys[-1]) == actions.Outcome("39")


# --- accept all ------------------------------------------------------------------------


def accept_all_form(c: UiClient) -> dict[str, str]:
    link = re.search(r'href="(/review/accept\?g=[0-9a-f]{16})"', c.get("/review").text)
    assert link is not None
    page = c.get(link.group(1)).text
    assert_safe(page)
    return dict(re.findall(r'name="(g|ids|pin)" value="([^"]+)"', page))


def test_accept_all_lists_the_files_first_then_moves_them_as_one_run(
    client: UiClient, cfg: Config
) -> None:
    group = group_of(client, "Images")
    confirm = client.get(f"/review/accept?g={group}").text
    for name in ("shot-1.png", "shot-2.png", "shot-3.png"):
        assert name in confirm
    assert "Move 3 files to Images" in confirm
    contents = {n: digest(row(cfg, n).current_path) for n in ("shot-1.png", "shot-2.png")}
    page = act(client, "/review/accept", **accept_all_form(client))
    assert "Moved 3 files to Images, as one run." in page.text
    rows = [row(cfg, f"shot-{i}.png") for i in (1, 2, 3)]
    assert all(r.status is FileStatus.ORGANIZED and r.category == "Images" for r in rows)
    with Store.open(cfg.database) as store:
        runs = {store.last_done_operation(r.id).run_id for r in rows}  # type: ignore[union-attr]
        assert len(runs) == 1
        run_id = runs.pop()
        run = store.conn.execute(
            "SELECT files_seen, files_moved, errors FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
    assert tuple(run) == (3, 3, 0)
    # One undo puts all three back in review.
    form = undo_form(client, f"run={run_id}")
    assert "puts 3 files back where they were" in client.get(f"/undo?run={run_id}").text
    assert "3 restored" in act(client, "/undo", **form).text
    for name, content in contents.items():
        back = row(cfg, name)
        assert back.status is FileStatus.REVIEW and digest(back.current_path) == content
        assert back.current_path.parent == review_folder(cfg)


def test_accept_all_is_refused_when_the_group_changed(client: UiClient, cfg: Config) -> None:
    form = accept_all_form(client)
    r = row(cfg, "shot-2.png")
    with Store.open(cfg.database) as store:
        fix_file(cfg, store, r.current_path, category="Other")
    page = act(client, "/review/accept", **form)
    assert actions.STALE_GROUP[:40] in page.text
    assert row(cfg, "shot-1.png").status is FileStatus.REVIEW


def test_accept_all_is_refused_when_a_listed_file_changed(client: UiClient, cfg: Config) -> None:
    """Same files, same ids, but one was renamed since the list was shown."""
    form = accept_all_form(client)
    r = row(cfg, "shot-2.png")
    with Store.open(cfg.database) as store:
        fix_file(cfg, store, r.current_path, name="renamed")
    assert accept_all_form(client)["ids"] == form["ids"]
    page = act(client, "/review/accept", **form)
    assert actions.STALE_GROUP[:40] in page.text
    assert row(cfg, "shot-1.png").status is FileStatus.REVIEW


def test_review_actions_never_touch_a_filed_file(client: UiClient, cfg: Config) -> None:
    """Even with the right pin (it is a digest, not a secret), /review/file is for files
    in review only."""
    r = row(cfg, "filed.pdf")
    page = act(client, "/review/file", id=str(r.id), pin=actions.file_pin(r), category="Other")
    assert actions.STALE[:40] in page.text
    assert row(cfg, "filed.pdf").current_path == r.current_path


def test_accept_all_is_refused_for_other_ids(client: UiClient, cfg: Config) -> None:
    form = accept_all_form(client)
    ids = form["ids"].split(",")
    page = act(client, "/review/accept", **form | {"ids": ",".join(ids[:2])})
    assert actions.STALE_GROUP[:40] in page.text
    page = act(client, "/review/accept", **form | {"g": "0123456789abcdef"})
    assert actions.STALE_GROUP[:40] in page.text
    assert row(cfg, "shot-1.png").status is FileStatus.REVIEW


@pytest.mark.parametrize(
    "change",
    [
        {"ids": ""},
        {"ids": "1,1"},
        {"ids": "1,x"},
        {"ids": "1," * 100 + "1"},
        {"g": "short"},
        {"g": "0123456789ABCDEF"},
        {"pin": "nope"},
    ],
)
def test_accept_all_refuses_a_form_it_never_sends(
    client: UiClient, cfg: Config, change: dict[str, str]
) -> None:
    form = accept_all_form(client) | change
    assert client.post_form("/review/accept", {"csrf": client.csrf()} | form).status == 400
    assert row(cfg, "shot-1.png").status is FileStatus.REVIEW


def test_accept_all_without_a_configured_suggestion_is_a_bad_request(
    client: UiClient, cfg: Config, downloads: Path
) -> None:
    group = group_of(client, "Recipes")
    assert "not in review any more" in client.get(f"/review/accept?g={group}").text
    r = row(cfg, "recipe.txt")
    with Store.open(cfg.database) as store:
        pin = actions.group_pin([store.file_by_id(r.id)])
    fields = {"csrf": client.csrf(), "g": group, "ids": str(r.id), "pin": pin}
    assert client.post_form("/review/accept", fields).status == 400


def test_accept_all_is_capped(
    client: UiClient, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(actions, "BULK_CAP", 2)
    group = group_of(client, "Images")
    assert "for 2 of the 3" in client.get("/review").text
    assert "Only 2 at a time" in client.get(f"/review/accept?g={group}").text
    page = act(client, "/review/accept", **accept_all_form(client))
    assert "Moved 2 files to Images" in page.text
    statuses = sorted(row(cfg, f"shot-{i}.png").status.value for i in (1, 2, 3))
    assert statuses == ["organized", "organized", "review"]


def test_accept_all_skips_a_file_that_cannot_be_moved(
    client: UiClient, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    form = accept_all_form(client)
    real = fix_files

    def tamper(config: Config, store: Store, items: list[FixItem], **kw: object) -> object:
        items[0][0].write_bytes(b"changed on disk")  # after the checks, before the move
        return real(config, store, items)

    monkeypatch.setattr("file_librarian.ui.actions.fix_files", tamper)
    page = act(client, "/review/accept", **form)
    assert "Moved 2 files to Images" in page.text
    assert "1 file could not be moved and stays in review" in page.text
    assert "This file changed or moved since it was indexed" in page.text
    assert str(cfg.output_root) not in page.text


def test_accept_all_that_moves_nothing_says_so(
    client: UiClient, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    form = accept_all_form(client)
    real = fix_files

    def tamper(config: Config, store: Store, items: list[FixItem], **kw: object) -> object:
        for path, *_ in items:
            path.write_bytes(b"changed")
        return real(config, store, items)

    monkeypatch.setattr("file_librarian.ui.actions.fix_files", tamper)
    page = act(client, "/review/accept", **form)
    assert "Nothing was moved." in page.text and "Undo this" not in page.text


# --- undo ------------------------------------------------------------------------------


def test_put_back_returns_a_file_to_downloads_and_it_stays(
    client: UiClient, cfg: Config, downloads: Path
) -> None:
    r = row(cfg, "shot-1.png")
    content = digest(r.current_path)
    confirm = client.get(f"/undo?file={r.id}").text
    assert "Downloads/" in confirm and "stays there" in confirm
    page = act(client, "/undo", **undo_form(client, f"file={r.id}"))
    assert "Undone: 1 restored" in page.text and "is back in Downloads/." in page.text
    back = row(cfg, "shot-1.png")
    assert back.current_path == downloads / "shot-1.png" and digest(back.current_path) == content
    assert back.status is FileStatus.SKIPPED
    again = client.get(f"/undo?file={r.id}").text
    assert "This file has no move to undo." in again and 'name="pin"' not in again


def test_undo_refuses_what_the_cli_refuses_in_its_words(client: UiClient, cfg: Config) -> None:
    page = client.get("/undo?run=999").text
    assert "Run 999 has nothing left to undo." in page and 'name="pin"' not in page
    assert "That file is not in the index any more" in client.get("/undo?file=999").text
    reply = act(client, "/undo", run="999", pin="0" * 20)
    assert "Run 999 has nothing left to undo." in reply.text


def test_a_stale_undo_is_refused(client: UiClient, cfg: Config, seeded: int) -> None:
    form = undo_form(client, f"run={seeded}")
    with Store.open(cfg.database) as store:
        undo_run(store, seeded, config=cfg)  # done from the command line meanwhile
    page = act(client, "/undo", **form)
    assert f"Run {seeded} has nothing left to undo." in page.text


def test_an_undo_after_another_change_to_the_run_is_stale(
    client: UiClient, cfg: Config, seeded: int
) -> None:
    form = undo_form(client, f"run={seeded}")
    r = row(cfg, "shot-1.png")
    act(client, "/undo", **undo_form(client, f"file={r.id}"))  # one move of it, undone
    page = act(client, "/undo", **form)
    assert actions.STALE_UNDO[:40] in page.text
    assert row(cfg, "shot-2.png").status is FileStatus.REVIEW


def test_a_stale_file_undo_is_refused(client: UiClient, cfg: Config) -> None:
    r = row(cfg, "shot-1.png")
    form = undo_form(client, f"file={r.id}")
    accept(client, cfg, "shot-1.png")
    page = act(client, "/undo", **form)
    assert actions.STALE_UNDO[:40] in page.text
    assert row(cfg, "shot-1.png").category == "Images"


def test_undo_lists_what_it_cannot_put_back(client: UiClient, cfg: Config) -> None:
    page = accept(client, cfg, "shot-1.png")
    link = re.search(r'href="/undo\?(run=\d+)"', page.text)
    assert link is not None
    form = undo_form(client, link.group(1))
    put(review_folder(cfg), "shot-1.png", b"someone else")  # its old place is taken
    done = act(client, "/undo", **form)
    assert "0 restored, 1 skipped" in done.text
    assert "something else has its original name now" in done.text
    assert str(cfg.output_root) not in done.text


@pytest.mark.parametrize(
    "fields",
    [
        {"pin": "0" * 20},
        {"run": "1", "file": "1", "pin": "0" * 20},
        {"run": "x", "pin": "0" * 20},
        {"file": "1"},
    ],
)
def test_undo_refuses_a_form_it_never_sends(client: UiClient, fields: dict[str, str]) -> None:
    assert client.post_form("/undo", {"csrf": client.csrf()} | fields).status == 400


def test_a_run_that_moved_a_sensitive_file_is_left_to_the_terminal(
    client: UiClient, cfg: Config, seeded: int
) -> None:
    r = row(cfg, "shot-1.png")
    form = undo_form(client, f"run={seeded}")
    with Store.open(cfg.database) as store, store.transaction():
        store.set_file_status(r.id, FileStatus.SENSITIVE)
    page = client.get(f"/undo?run={seeded}").text
    assert actions.SENSITIVE[:30] in page and "shot-1.png" not in page
    assert actions.SENSITIVE[:30] in act(client, "/undo", **form).text
    assert row(cfg, "shot-2.png").status is FileStatus.REVIEW


def test_activity_offers_undo_for_runs_and_last_moves(client: UiClient, cfg: Config) -> None:
    accept(client, cfg, "shot-1.png")
    page = client.get("/activity").text
    assert_safe(page)
    r = row(cfg, "shot-1.png")
    assert page.count(f'href="/undo?file={r.id}"') == 1  # only its last move
    assert len(re.findall(r'href="/undo\?run=\d+"', page)) == 2
    other = row(cfg, "shot-2.png")
    assert f'href="/undo?file={other.id}"' in page


def test_an_undone_run_is_not_offered_again(client: UiClient, cfg: Config, seeded: int) -> None:
    act(client, "/undo", **undo_form(client, f"run={seeded}"))
    assert f'href="/undo?run={seeded}"' not in client.get("/activity").text


# --- due -------------------------------------------------------------------------------


def test_mark_done_and_open_again_from_due(serve: list[Ui], cfg: Config, downloads: Path) -> None:
    fill(downloads, cfg, {"power.pdf": bill(amount="10", due_date=soon(2))})
    c = start(serve, cfg)
    r = row(cfg, "power.pdf")
    page = c.get("/due").text
    assert "Mark done" in page
    done = act(c, "/due/settle", id=str(r.id), pin=pin_on_page(page, r.id), done="1")
    assert "is marked done: it leaves the Due list." in done.text
    assert row(cfg, "power.pdf").settled_at is not None
    shown = c.get("/due?within=7d&all=1").text
    assert "Open again" in shown
    again = act(c, "/due/settle", id=str(r.id), pin=pin_on_page(shown, r.id), done="0")
    assert "is open again: it is back on the Due list." in again.text
    assert row(cfg, "power.pdf").settled_at is None


def test_mark_done_refusals(serve: list[Ui], cfg: Config, downloads: Path) -> None:
    fill(downloads, cfg, {"power.pdf": bill(amount="10", due_date=soon(2))})
    c = start(serve, cfg)
    r = row(cfg, "power.pdf")
    pin = pin_on_page(c.get("/due").text, r.id)
    assert "is not marked as done" in act(c, "/due/settle", id=str(r.id), pin=pin, done="0").text
    act(c, "/due/settle", id=str(r.id), pin=pin, done="1")
    assert actions.STALE[:40] in act(c, "/due/settle", id=str(r.id), pin=pin, done="1").text
    with Store.open(cfg.database) as store, store.transaction():
        store.set_file_status(r.id, FileStatus.SENSITIVE)
    assert actions.SENSITIVE[:30] in act(c, "/due/settle", id=str(r.id), pin=pin, done="1").text
    bad = c.post_form("/due/settle", {"csrf": c.csrf(), "id": str(r.id), "pin": pin, "done": "2"})
    assert bad.status == 400
    assert c.get("/due?n=a&n=b").status == 400
    assert "is no longer kept" in c.get("/due?n=CCCCCCCCCCCCCCCC").text


# --- the route table -------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/review/file", "/review/accept", "/undo", "/due/settle"])
def test_each_action_is_in_the_route_table(path: str) -> None:
    assert path in ui_server.POST_ROUTES  # so the cookie/origin/CSRF test covers it


@pytest.mark.parametrize("path", ["/review/file", "/review/accept", "/undo", "/due/settle"])
@pytest.mark.parametrize("missing", ["cookie", "origin", "csrf", "same-site"])
def test_each_action_refuses_a_forged_post_and_moves_nothing(
    client: UiClient, cfg: Config, path: str, missing: str
) -> None:
    r = row(cfg, "shot-1.png")
    fields = {
        "csrf": client.csrf(),
        "id": str(r.id),
        "pin": pin_on_page(client.get("/review").text, r.id),
        "category": "Images",
        "done": "1",
        "file": str(r.id),
    }
    sender = UiClient(client.port, client.name)
    if missing != "cookie":
        sender.cookies = dict(client.cookies)
    if missing == "csrf":
        fields["csrf"] = "forged"
    headers = {"Origin": client.origin, "Sec-Fetch-Site": "same-origin"}
    if missing == "origin":
        headers = {"Origin": "http://evil.example"}
    if missing == "same-site":
        headers = {"Sec-Fetch-Site": "same-site"}
    assert sender.post_form(path, fields, headers).status == 403
    assert row(cfg, "shot-1.png").current_path == r.current_path


# --- pieces ----------------------------------------------------------------------------


def test_messages_lose_the_path() -> None:
    path = Path("/x/y/file.pdf")
    assert actions.without_path(f"{path} has no move to undo", path) == (
        "This file has no move to undo"
    )
    assert actions.without_path(f"could not reach {path} now", path) == "could not reach it now"
    error = FixError(f"could not move {path}: denied")
    error.__cause__ = OSError("/x/y/file.pdf: denied")
    assert actions._fix_text(error, path) == "It could not be moved; nothing was changed."
    assert actions._fix_reason(f"could not move {path}: x", path) == "It could not be moved."
    for reason in ("original folder is gone: /a", "original path is taken: /a/b"):
        assert "/" not in actions._undo_reason(reason)
    assert actions._undo_reason("could not move back: Permission denied") == (
        "it could not be moved back"
    )
    assert actions._undo_reason("changed since it was moved") == (
        "it changed since it was moved (edited by hand?)"
    )
    assert actions._undo_reason("no longer there") == "no longer there"


def test_pins_change_with_what_the_page_showed(cfg: Config, seeded: int) -> None:
    r = row(cfg, "shot-1.png")
    assert len(actions.file_pin(r)) == actions.PIN_LENGTH
    import dataclasses

    for change in (
        {"current_path": Path("/elsewhere")},
        {"sha256": "0" * 64},
        {"status": FileStatus.ORGANIZED},
        {"category": "Images"},
        {"review_reason": None},
        {"review_category": "Other"},
        {"settled_at": "2026-01-01"},
    ):
        assert actions.file_pin(dataclasses.replace(r, **change)) != actions.file_pin(r)


def test_ids_parses_only_what_the_confirmation_sends() -> None:
    assert actions.ids("3,1,2") == [3, 1, 2]
    for bad in ("", "1,,2", "a", "1,1", "-1", "\uff11"):
        with pytest.raises(actions.Malformed):
            actions.ids(bad)


def test_fix_files_counts_its_run_from_the_journal(cfg: Config, seeded: int) -> None:
    paths = [row(cfg, f"shot-{i}.png").current_path for i in (1, 2)]
    with Store.open(cfg.database) as store:
        report = fix_files(cfg, store, [FixItem(paths[0], "Images"), FixItem(paths[1], "Nope")])
        run = store.conn.execute(
            "SELECT files_seen, files_moved, errors FROM runs WHERE id = ?", (report.run_id,)
        ).fetchone()
    assert len(report.fixed) == 1 and report.skipped[0][0] == paths[1]
    assert tuple(run) == (2, 1, 0)


def test_review_groups_count_the_files_not_listed(
    cfg: Config, seeded: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(actions, "MAX_LISTED", 2)
    with Store.open(cfg.database) as store:
        groups, more = actions.review_groups(store, "secret")
    assert sum(len(g.rows) for g in groups) == 2 and more == 3


def test_the_page_says_when_files_are_not_listed(
    client: UiClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(actions, "MAX_LISTED", 2)
    assert "And 3 more files" in client.get("/review").text


def test_undo_confirmation_caps_its_list(
    client: UiClient, seeded: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(review, "MAX_UNDO_LISTED", 2)
    assert "and 4 more" in client.get(f"/undo?run={seeded}").text


# --- the rarer paths -------------------------------------------------------------------


def test_an_unreadable_index_is_said_and_nothing_is_done(client: UiClient, cfg: Config) -> None:
    r = row(cfg, "shot-1.png")
    pin = pin_on_page(client.get("/review").text, r.id)
    group = group_of(client, "Images")
    for suffix in ("-wal", "-shm"):
        cfg.database.with_name(cfg.database.name + suffix).unlink(missing_ok=True)
    cfg.database.write_bytes(b"not a database" * 100)
    for path in ("/review", f"/review/accept?g={group}", f"/undo?file={r.id}"):
        reply = client.get(path)
        assert reply.status == 503 and "annot read the index" in reply.text, path
    fields = {"csrf": client.csrf(), "id": str(r.id), "pin": pin, "category": "Images"}
    reply = client.post_form("/review/file", fields)
    assert reply.status == 303
    page = client.get(reply.headers["Location"])
    assert page.status == 503  # the list cannot be read; the outcome still shows
    assert "Cannot read the index; nothing was done." in page.text
    assert r.current_path.exists()


def test_files_from_before_review_reasons_and_without_a_suggestion(
    client: UiClient, cfg: Config
) -> None:
    r = row(cfg, "shot-1.png")
    with Store.open(cfg.database) as store, store.transaction():
        store.conn.execute(
            "UPDATE files SET review_reason = NULL, review_category = NULL, "
            "processed_at = '2020-01-01T00:00:00', confidence = NULL WHERE id = ?",
            (r.id,),
        )
    page = client.get("/review").text
    assert "Filed before flib kept a reason" in page
    assert re.search(r"in review for \d+ days", page)
    with Store.open(cfg.database) as store, store.transaction():
        store.conn.execute("UPDATE files SET processed_at = 'garbage' WHERE id = ?", (r.id,))
    assert "in review <" in client.get("/review").text


def test_a_file_renamed_in_review_says_what_it_was(client: UiClient, cfg: Config) -> None:
    accept(client, cfg, "shot-3.png", category="", name="renamed")
    page = client.get("/review").text
    assert "renamed.png" in page and "was <bdi" in page


def test_a_suggestion_in_another_case_is_accepted_as_configured(
    serve: list[Ui], cfg: Config, downloads: Path
) -> None:
    fill(downloads, cfg, {f"pic-{i}.png": answer("images", confidence=UNSURE) for i in (1, 2)})
    c = start(serve, cfg)
    assert "Accept: Images" in c.get("/review").text
    assert "Moved 2 files to Images" in act(c, "/review/accept", **accept_all_form(c)).text


def test_undoing_a_run_that_only_changed_the_index(
    serve: list[Ui], cfg: Config, downloads: Path
) -> None:
    fill(downloads, cfg, {"power.pdf": bill(amount="10", due_date=soon(2))})
    c = start(serve, cfg)
    r = row(cfg, "power.pdf")
    pin = pin_on_page(c.get("/due").text, r.id)
    done = act(c, "/due/settle", id=str(r.id), pin=pin, done="1")
    link = re.search(r'href="/undo\?(run=\d+)"', done.text)
    assert link is not None
    assert "Undo: open it again…" in done.text
    confirm = c.get(f"/undo?{link.group(1)}").text
    assert "power.pdf</bdi> is open again." in confirm and "run " not in confirm.split("<main")[1]
    page = act(c, "/undo", **undo_form(c, link.group(1)))
    assert "power.pdf</bdi> is open again: it is back on the Due list." in page.text
    assert row(cfg, "power.pdf").settled_at is None


def test_a_move_that_fails_in_a_batch_is_skipped(
    cfg: Config, seeded: int, monkeypatch: pytest.MonkeyPatch
) -> None:

    paths = [row(cfg, f"shot-{i}.png").current_path for i in (1, 2)]
    from file_librarian.fsops import move_no_replace as real

    def refuse(src: Path, dst: Path) -> None:
        if src == paths[0]:
            raise PermissionError(1, "denied", str(dst))
        real(src, dst)

    monkeypatch.setattr("file_librarian.fix.move_no_replace", refuse)
    with Store.open(cfg.database) as store:
        report = fix_files(cfg, store, [FixItem(p, "Images") for p in paths])
        run = store.conn.execute(
            "SELECT files_seen, files_moved, errors FROM runs WHERE id = ?", (report.run_id,)
        ).fetchone()
    assert [f.source for f in report.fixed] == [paths[1]]
    assert report.skipped[0][1].startswith("could not move")
    assert tuple(run) == (2, 1, 1)


def test_a_batch_cut_off_is_finished_by_recovery_with_true_counts(
    cfg: Config, seeded: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    import file_librarian.fix as fix_mod
    from file_librarian.executor import recover

    paths = [row(cfg, f"shot-{i}.png").current_path for i in (1, 2)]
    real = fix_mod._complete
    calls: list[int] = []

    def crash(*args: object, **kw: object) -> object:
        calls.append(1)
        if len(calls) == 2:
            raise KeyboardInterrupt  # the process dies after the second move
        return real(*args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(fix_mod, "_complete", crash)
    with Store.open(cfg.database) as store, pytest.raises(KeyboardInterrupt):
        fix_files(cfg, store, [FixItem(p, "Images") for p in paths])
    with Store.open(cfg.database) as store:
        assert recover(store, cfg) == 1
        run_id = store.last_run_with_operations()
        run = store.conn.execute(
            "SELECT files_seen, files_moved, errors FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
    assert tuple(run) == (2, 2, 0)
    assert all(row(cfg, f"shot-{i}.png").category == "Images" for i in (1, 2))


def test_accept_all_takes_at_most_the_cap() -> None:
    assert len(actions.ids(",".join(str(i) for i in range(actions.BULK_CAP)))) == actions.BULK_CAP
    with pytest.raises(actions.Malformed):
        actions.ids(",".join(str(i) for i in range(actions.BULK_CAP + 1)))


def test_a_group_key_of_another_length_is_refused(client: UiClient) -> None:
    assert client.get("/review/accept?g=0123456789abcde").status == 400
    assert client.get("/review/accept?g=0123456789abcdef0").status == 400


def test_an_interrupted_fix_is_finished_before_an_action(
    client: UiClient, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """As ``flib fix`` does: the journal is finished before anything is added to it."""
    import file_librarian.fix as fix_mod

    other = row(cfg, "shot-2.png")
    real = fix_mod._complete
    calls: list[int] = []

    def crash_once(*args: object, **kw: object) -> object:
        calls.append(1)
        if len(calls) == 1:
            raise KeyboardInterrupt  # moved, but the index was never written
        return real(*args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(fix_mod, "_complete", crash_once)
    with Store.open(cfg.database) as store, pytest.raises(KeyboardInterrupt):
        fix_file(cfg, store, other.current_path, category="Other")
    accept(client, cfg, "shot-1.png")
    finished = row(cfg, "shot-2.png")
    assert finished.category == "Other" and finished.status is FileStatus.ORGANIZED


def test_names_in_an_outcome_lose_format_characters(
    serve: list[Ui], cfg: Config, downloads: Path
) -> None:
    tricky = "invoice‮fdp.txt"
    fill(downloads, cfg, {tricky: answer("Finance", confidence=UNSURE)})
    c = start(serve, cfg)
    r = row(cfg, tricky)
    page = accept(c, cfg, tricky)
    assert "is now in Finance." in page.text and "‮" not in page.text
    # Undone twice (the fix, then its move into review), it is back in Downloads under
    # its own name, override and all; the page shows the name without it.
    act(c, "/undo", **undo_form(c, f"file={r.id}"))
    done = act(c, "/undo", **undo_form(c, f"file={r.id}"))
    assert "‮" in row(cfg, tricky).current_name
    assert "1 restored" in done.text and "‮" not in done.text

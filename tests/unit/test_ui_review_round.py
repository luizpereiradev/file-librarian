"""The independent review and browser-testing round of PR #128 (#115): one test, or
more, per finding. The setup is ``test_ui_review.py``'s (a real server over a real
index the fake provider filled)."""

from __future__ import annotations

import hashlib
import re
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest
from typer.testing import CliRunner

import file_librarian.fix as fix_mod
from file_librarian import pipeline
from file_librarian.classifier import correction_examples
from file_librarian.cli import app
from file_librarian.config import Config
from file_librarian.db.store import FileRow, Store
from file_librarian.executor import recover
from file_librarian.fix import FixItem, fix_file, fix_files
from file_librarian.models import FileStatus, OperationStatus, ReviewReason
from file_librarian.providers.fake import FakeProvider
from file_librarian.ui.server import Ui
from file_librarian.undo import undo_file, undo_run
from tests._helpers import UiClient, answer, put, set_tags, write_config
from tests.unit.test_ui import (  # noqa: F401 - fixtures: every server is checked the same way
    _Collector,
    events,
    no_hang,
    nothing_left_behind,
)
from tests.unit.test_ui_pages import bill, fill, soon, start
from tests.unit.test_ui_review import (
    REVIEW,
    UNSURE,
    accept,
    accept_all_form,
    act,
    group_of,
    pin_on_page,
    review_folder,
    row,
    undo_form,
)

SHOTS = [f"shot-{i}.png" for i in (1, 2, 3)]


@pytest.fixture
def serve(loopback_network: None) -> Iterator[list[Ui]]:
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


def by_id(cfg: Config, file_id: int) -> FileRow:
    with Store.open_read_only(cfg.database) as store:
        return store.file_by_id(file_id)


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Kill(BaseException):
    """The process dies here (nothing in flib catches a ``BaseException``)."""


# --- R1: the index failing mid "accept all" --------------------------------------------


def test_an_index_error_mid_batch_says_what_moved_and_offers_undo(
    client: UiClient, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    form = accept_all_form(client)
    contents = {n: digest(row(cfg, n).current_path) for n in SHOTS}
    real = fix_mod._complete
    calls: list[int] = []

    def flaky(*args: object, **kw: object) -> object:
        calls.append(1)
        if len(calls) == 2:
            raise sqlite3.OperationalError("disk I/O error at /some/path")
        return real(*args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(fix_mod, "_complete", flaky)
    page = act(client, "/review/accept", **form)
    text = page.text
    assert "1 file moved to Images and recorded; then the index could not be written" in text
    assert "See Activity" in text
    assert "nothing was done" not in text.lower()
    assert "/some/path" not in text and "disk I/O" not in text
    link = re.search(r'href="/undo\?(run=\d+)">Undo this run…', text)
    assert link is not None
    with Store.open_read_only(cfg.database) as store:
        run_id = int(link.group(1).split("=")[1])
        run = store.conn.execute(
            "SELECT finished_at, files_seen, files_moved FROM runs WHERE id = ?", (run_id,)
        ).fetchone()
    assert run[0] is not None and tuple(run[1:]) == (3, 2)  # the half-done one counts
    # The undo (an action: it recovers the planned entry first) puts everything back.
    monkeypatch.setattr(fix_mod, "_complete", real)
    done = act(client, "/undo", **undo_form(client, link.group(1)))
    assert "2 restored" in done.text
    for name, content in contents.items():
        back = row(cfg, name)
        assert back.status is FileStatus.REVIEW and digest(back.current_path) == content


def test_fix_files_stops_and_finishes_its_run_when_the_index_fails(
    cfg: Config, seeded: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = [row(cfg, n).current_path for n in SHOTS]
    real = fix_mod.plan_fix
    calls: list[int] = []

    def flaky(*args: object, **kw: object) -> object:
        calls.append(1)
        if len(calls) == 2:
            raise sqlite3.OperationalError("locked")
        return real(*args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(fix_mod, "plan_fix", flaky)
    with Store.open(cfg.database) as store:
        report = fix_files(cfg, store, [FixItem(p, "Images") for p in paths])
        run = store.conn.execute(
            "SELECT files_seen, files_moved, errors FROM runs WHERE id = ?", (report.run_id,)
        ).fetchone()
    assert report.broken and len(report.fixed) == 1
    assert [why for _, why in report.skipped] == [fix_mod.NOT_WRITTEN] * 2
    assert tuple(run) == (3, 1, 0)


def test_a_batch_whose_run_cannot_be_finished_says_so(
    cfg: Config, seeded: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    def broken(*args: object, **kw: object) -> None:
        raise sqlite3.OperationalError("full")

    monkeypatch.setattr(fix_mod, "_finish", broken)
    with Store.open(cfg.database) as store:
        report = fix_files(cfg, store, [FixItem(row(cfg, "shot-1.png").current_path, "Images")])
    assert report.broken and len(report.fixed) == 1


# --- R2: recovery never takes the user's copy for the moved file -----------------------


def test_a_crash_after_journaling_a_taken_name_never_misfiles_the_index(
    cfg: Config, seeded: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = row(cfg, "shot-1.png")
    occupant = put(cfg.output_root / "Images", "shot-1.png", r.current_path.read_bytes())
    calls: list[int] = []
    from file_librarian.fsops import move_no_replace as real

    def die(src: Path, dst: Path) -> None:
        calls.append(1)
        if len(calls) == 1:
            raise Kill  # the candidate name is journaled; the rename never ran
        real(src, dst)

    monkeypatch.setattr("file_librarian.fix.move_no_replace", die)
    with Store.open(cfg.database) as store, pytest.raises(Kill):
        fix_files(cfg, store, [FixItem(r.current_path, "Images", None, r.id)])
    with Store.open(cfg.database) as store:
        recover(store, cfg)
        op = max(store.operations(), key=lambda o: o.id)
    after = row(cfg, "shot-1.png")
    assert after.current_path == r.current_path and after.status is FileStatus.REVIEW
    assert op.status is OperationStatus.FAILED
    assert occupant.read_bytes() == r.current_path.read_bytes()  # untouched, unindexed


def test_recovery_still_trusts_the_content_once_the_source_is_gone(
    cfg: Config, seeded: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fallback the reviewer kept: the inode cannot tell (another rename since), and
    the source is gone, so the bytes at the destination are the file."""
    r = row(cfg, "shot-1.png")
    real = fix_mod._complete

    def die(*args: object, **kw: object) -> object:
        raise Kill

    monkeypatch.setattr(fix_mod, "_complete", die)
    with Store.open(cfg.database) as store, pytest.raises(Kill):
        fix_files(cfg, store, [FixItem(r.current_path, "Images", None, r.id)])
    monkeypatch.setattr(fix_mod, "_complete", real)
    moved = cfg.output_root / "Images" / "shot-1.png"
    data = moved.read_bytes()
    moved.unlink()
    moved.write_bytes(data)  # same bytes, another inode
    with Store.open(cfg.database) as store:
        recover(store, cfg)
    assert row(cfg, "shot-1.png").current_path == moved


# --- R3: the action is bound to the id the form named ----------------------------------


def test_two_rows_on_one_path_the_page_acts_on_the_named_one(
    serve: list[Ui], cfg: Config, downloads: Path
) -> None:
    fill(downloads, cfg, {"a.png": answer("Images", confidence=UNSURE)})
    old = row(cfg, "a.png")
    old.current_path.rename(cfg.output_root / "elsewhere.png")  # by hand, not reconciled
    put(downloads, "a.png", b"other bytes")
    unsure = answer("Images", confidence=UNSURE)
    pipeline.run(cfg, FakeProvider({"a.png": unsure}, default=answer()), simulate=False)
    with Store.open_read_only(cfg.database) as store:
        rows = [r for r in store.all_files() if r.original_name == "a.png"]
    assert len(rows) == 2 and rows[0].current_path == rows[1].current_path
    new = max(rows, key=lambda r: r.id)
    c = start(serve, cfg)
    pin = pin_on_page(c.get("/review").text, new.id)
    page = act(c, "/review/file", id=str(new.id), pin=pin, category="Images")
    assert "is now in Images." in page.text
    assert by_id(cfg, new.id).status is FileStatus.ORGANIZED
    assert by_id(cfg, old.id).current_path == old.current_path  # the stale row is left
    # Undo by id too: the named row's last move, not the other row's.
    page = act(c, "/undo", **undo_form(c, f"file={new.id}"))
    assert "Undone: 1 restored" in page.text
    assert by_id(cfg, new.id).status is FileStatus.REVIEW


def test_mark_done_acts_on_the_named_row(serve: list[Ui], cfg: Config, downloads: Path) -> None:
    fill(downloads, cfg, {"power.pdf": bill(amount="10", due_date=soon(2))})
    c = start(serve, cfg)
    r = row(cfg, "power.pdf")
    page = act(c, "/due/settle", id=str(r.id), pin=pin_on_page(c.get("/due").text, r.id), done="1")
    assert "is marked done" in page.text and by_id(cfg, r.id).settled_at is not None


# --- R4: the model's words, and groups in addresses -------------------------------------


def test_a_suggested_category_and_a_description_lose_format_characters(
    serve: list[Ui], cfg: Config, downloads: Path
) -> None:
    tricky = "Fin‮ecnan"
    told = answer(tricky, confidence=0.95, description="a‮description")
    fill(downloads, cfg, {"a.pdf": told, "b.pdf": told})
    page = start(serve, cfg).get("/review").text
    assert "‮" not in page
    assert "suggested Finecnan" in page and "adescription" in page


def test_a_group_is_named_with_a_secret_of_the_session(
    serve: list[Ui], cfg: Config, seeded: int
) -> None:
    first = group_of(start(serve, cfg), "Images")
    second = group_of(start(serve, cfg), "Images")
    assert first != second  # another session, another name
    plain = hashlib.sha256(f"{ReviewReason.LOW_CONFIDENCE}\0Images".encode()).hexdigest()
    assert first not in plain


def test_another_sessions_group_name_is_not_accepted(
    serve: list[Ui], cfg: Config, seeded: int
) -> None:
    other = group_of(start(serve, cfg), "Images")
    c = start(serve, cfg)
    assert "not in review any more" in c.get(f"/review/accept?g={other}").text


# --- R5: test gaps ----------------------------------------------------------------------


def test_activity_hides_undo_for_a_run_that_touched_a_now_sensitive_file(
    client: UiClient, cfg: Config, seeded: int
) -> None:
    assert f'href="/undo?run={seeded}"' in client.get("/activity").text
    r = row(cfg, "shot-1.png")
    with Store.open(cfg.database) as store, store.transaction():
        store.set_file_status(r.id, FileStatus.SENSITIVE)
    page = client.get("/activity").text
    assert f'href="/undo?run={seeded}"' not in page and "shot-1.png" not in page


def test_due_offers_no_mark_done_for_a_document_done_by_tag(
    serve: list[Ui], tmp_path: Path, downloads: Path
) -> None:
    from file_librarian.config import load_config

    cfg = load_config(write_config(tmp_path, downloads, extra="due: {done_tag: Paid}\n"))
    fill(downloads, cfg, {"power.pdf": bill(amount="10", due_date=soon(2))})
    set_tags(row(cfg, "power.pdf").current_path, ["Paid"])
    page = start(serve, cfg).get("/due?within=7d&all=1").text
    assert "done (Finder tag)" in page
    assert 'action="/due/settle"' not in page


@pytest.mark.parametrize("how", ["edited", "replaced", "folder_gone"])
def test_the_ui_undo_never_forces_a_changed_destination(
    client: UiClient, cfg: Config, how: str
) -> None:
    before = row(cfg, "shot-1.png")
    accept(client, cfg, "shot-1.png")
    dst = row(cfg, "shot-1.png").current_path
    if how == "edited":
        dst.write_bytes(b"the user's edits")
    elif how == "replaced":
        dst.unlink()
        dst.write_bytes(b"another file")
    else:
        dst.unlink()
        dst.parent.rmdir()
    page = client.get(f"/undo?file={before.id}").text
    assert "cannot be undone now" in page and 'name="pin"' not in page
    if how != "folder_gone":
        assert "changed since it was moved (edited by hand?)" in page
    else:
        assert "no longer there" in page
    # The command line's undo, which the page would call, skips it as well.
    with Store.open(cfg.database) as store:
        report = undo_file(store, dst, config=cfg)
    assert not report.restored and not before.current_path.exists()
    if how != "folder_gone":
        assert dst.read_bytes() in (b"the user's edits", b"another file")


# --- B1: undo plans say what will really happen ----------------------------------------


def test_undo_of_a_run_whose_files_moved_on_says_nothing_can_go_back(
    client: UiClient, cfg: Config, seeded: int
) -> None:
    act(client, "/review/accept", **accept_all_form(client))  # run 2 moves the shots on
    page = client.get(f"/undo?run={seeded}").text
    assert f"Undoing run {seeded} puts 3 files back where they were; 3 are skipped" in page
    assert page.count("moved again since, by run") == 3 and "(undo that first)" in page
    assert "moved or deleted by hand" not in page
    activity = client.get("/activity").text
    assert f'href="/undo?run={seeded}"' in activity  # the other files of run 1 still can


def test_activity_hides_undo_when_no_move_of_a_run_can_go_back(
    serve: list[Ui], cfg: Config, downloads: Path
) -> None:
    first = fill(downloads, cfg, {"one.png": answer("Images", confidence=UNSURE)})
    c = start(serve, cfg)
    accept(c, cfg, "one.png")
    activity = c.get("/activity").text
    assert f'href="/undo?run={first}"' not in activity  # its only file moved on
    assert len(re.findall(r'href="/undo\?run=\d+"', activity)) == 1


def test_undo_of_a_run_counts_what_goes_back_and_flags_an_edited_file(
    client: UiClient, cfg: Config, seeded: int
) -> None:
    accept(client, cfg, "shot-1.png")  # moved on by another run
    row(cfg, "shot-2.png").current_path.write_bytes(b"edited by hand")
    page = client.get(f"/undo?run={seeded}").text
    assert f"Undoing run {seeded} puts 4 files back where they were; 2 are skipped" in page
    assert "moved again since, by run" in page
    assert "it changed since it was moved (edited by hand?)" in page
    done = act(client, "/undo", **undo_form(client, f"run={seeded}"))
    assert "4 restored, 2 skipped" in done.text


def test_cli_undo_says_a_file_moved_on_rather_than_blaming_the_user(
    cfg: Config, seeded: int
) -> None:
    r = row(cfg, "shot-1.png")
    with Store.open(cfg.database) as store:
        later = fix_file(cfg, store, r.current_path, category="Images")
        report = undo_run(store, seeded, config=cfg)
    reasons = dict((p.name, why) for p, why in report.skipped)
    assert reasons["shot-1.png"] == f"moved again since, by run {later.run_id} (undo that first)"


# --- B2: what corrections teach ---------------------------------------------------------


def corrections(cfg: Config) -> list[tuple[str, str | None, str]]:
    with Store.open_read_only(cfg.database) as store:
        rows = store.conn.execute("SELECT field, old_value, new_value FROM corrections")
        return [(str(r[0]), r[1], str(r[2])) for r in rows]


def test_undo_forgets_the_corrections_its_move_recorded(client: UiClient, cfg: Config) -> None:
    page = accept(client, cfg, "shot-1.png", category="Other", name="kept")
    assert sorted(corrections(cfg)) == [
        ("category", None, "Other"),
        ("name", "shot-1.png", "kept.png"),
    ]
    link = re.search(r'href="/undo\?(run=\d+)"', page.text)
    assert link is not None
    act(client, "/undo", **undo_form(client, link.group(1)))
    assert corrections(cfg) == []


def test_an_undone_fix_never_hides_an_older_correction(cfg: Config, seeded: int) -> None:
    r = row(cfg, "shot-1.png")
    with Store.open(cfg.database) as store:
        fix_file(cfg, store, r.current_path, category="Other")
        moved = fix_file(cfg, store, row(cfg, "shot-1.png").current_path, category="Images")
        undo_run(store, moved.run_id, config=cfg)
        current = store.current_corrections()
    assert [(c.field, c.new_value) for c in current] == [("category", "Other")]


def test_a_number_forced_by_a_taken_name_is_not_a_name_correction(
    client: UiClient, cfg: Config
) -> None:
    put(cfg.output_root / "Images", "shot-1.png", b"already here")
    page = accept(client, cfg, "shot-1.png")
    assert corrections(cfg) == [("category", None, "Images")]
    assert (
        "shot-1-2.png</bdi> <span>is its new name: shot-1.png was taken in Images/.</span>"
        in page.text
    )


def test_the_command_line_still_learns_a_name_the_user_chose(cfg: Config, seeded: int) -> None:
    with Store.open(cfg.database) as store:
        report = fix_file(cfg, store, row(cfg, "shot-1.png").current_path, name="shot-1-2")
    assert report.name == ("shot-1.png", "shot-1-2.png") or report.name is None
    put(review_folder(cfg), "holiday.png", b"taken")
    with Store.open(cfg.database) as store:
        report = fix_file(cfg, store, row(cfg, "shot-2.png").current_path, name="holiday")
    assert report.name == ("shot-2.png", "holiday-2.png")  # chosen, then numbered: learned


def test_a_batch_is_one_example_for_the_classifier(client: UiClient, cfg: Config) -> None:
    act(client, "/review/accept", **accept_all_form(client))
    accept(client, cfg, EVIL := next(n for n in REVIEW if n.startswith("<img")), category="Other")
    with Store.open_read_only(cfg.database) as store:
        current = store.current_corrections()
    assert len([c for c in current if c.new_value == "Images"]) == 3  # reprocess needs each
    examples = correction_examples(current, cfg)
    assert [e.new_value for e in examples].count("Images") == 1
    assert [e.new_value for e in examples].count("Other") == 1
    assert EVIL


# --- B3: the move form starts on "keep in review" --------------------------------------


def test_a_name_typed_alone_only_renames(client: UiClient, cfg: Config) -> None:
    page = client.get("/review").text
    assert '<option value="" selected>Keep in review (only rename it)</option>' in page
    assert "Move or rename as chosen" in page
    assert 'name="name" value="" placeholder="New name (optional)" maxlength="255"' in page
    assert 'autocomplete="off"' in page
    r = row(cfg, "shot-1.png")
    pin = pin_on_page(page, r.id)
    act(client, "/review/file", id=str(r.id), pin=pin, category="", name="mine")
    after = row(cfg, "shot-1.png")
    assert after.status is FileStatus.REVIEW and after.current_name == "mine.png"


# --- B4, B7: accept all lists numbered names, never counts missing files ----------------


def test_accept_all_lists_files_renamed_for_a_taken_name(client: UiClient, cfg: Config) -> None:
    put(cfg.output_root / "Images", "shot-2.png", b"already here")
    page = act(client, "/review/accept", **accept_all_form(client))
    assert "Moved 3 files to Images, as one run." in page.text
    assert "shot-2-2.png</bdi> <span>is its new name: shot-2.png was taken in Images/.</span>" in (
        page.text
    )


def test_accept_all_leaves_out_files_missing_on_disk(client: UiClient, cfg: Config) -> None:
    row(cfg, "shot-2.png").current_path.unlink()
    assert "Accept Images for all 2…" in client.get("/review").text
    group = group_of(client, "Images")
    page = client.get(f"/review/accept?g={group}").text
    assert "Move 2 files to Images" in page and "1 file of this group is missing" in page
    assert "shot-2.png" not in page.split("<ol")[1]
    done = act(client, "/review/accept", **accept_all_form(client))
    assert "Moved 2 files to Images, as one run." in done.text


def test_accept_all_of_only_missing_files_is_not_offered(client: UiClient, cfg: Config) -> None:
    for name in SHOTS:
        row(cfg, name).current_path.unlink()
    assert "Accept Images for all" not in client.get("/review").text
    group = group_of(client, "Images")
    assert "not in review any more" in client.get(f"/review/accept?g={group}").text


# --- B6: back where the user was ---------------------------------------------------------


def test_an_action_goes_back_to_its_group(client: UiClient, cfg: Config) -> None:
    r = row(cfg, "shot-1.png")
    group = group_of(client, "Images")
    pin = pin_on_page(client.get("/review").text, r.id)
    fields = {"csrf": client.csrf(), "id": str(r.id), "pin": pin, "category": "Images"}
    reply = client.post_form("/review/file", fields)
    assert reply.headers["Location"].endswith(f"#g-{group}")
    form = accept_all_form(client)
    reply = client.post_form("/review/accept", {"csrf": client.csrf()} | form)
    assert reply.headers["Location"].endswith(f"#g-{group}")


def test_due_keeps_its_window_across_mark_done(
    serve: list[Ui], cfg: Config, downloads: Path
) -> None:
    fill(downloads, cfg, {"power.pdf": bill(amount="10", due_date=soon(10))})
    c = start(serve, cfg)
    r = row(cfg, "power.pdf")
    page = c.get("/due?within=2w&all=1").text
    assert 'name="within" value="2w"' in page and 'name="all" value="1"' in page
    fields = {"csrf": c.csrf(), "id": str(r.id), "pin": pin_on_page(page, r.id), "done": "1"}
    reply = c.post_form("/due/settle", fields | {"within": "2w", "all": "1"})
    assert re.fullmatch(r"/due\?within=2w&all=1&n=[\w-]{16}", reply.headers["Location"])
    shown = c.get(reply.headers["Location"]).text
    assert "is marked done" in shown and 'aria-current="true">2 weeks' in shown.replace(
        'href="/due?within=2w&amp;all=1" ', ""
    )
    for bad in ({"within": "9y"}, {"within": "2w", "overdue": "1"}, {"all": "2"}):
        fields["pin"] = pin_on_page(c.get("/due?within=2w&all=1").text, r.id)
        assert c.post_form("/due/settle", fields | bad).status == 400


def test_overdue_is_kept_too(serve: list[Ui], cfg: Config, downloads: Path) -> None:
    fill(downloads, cfg, {"late.pdf": bill(amount="10", due_date=soon(-3))})
    c = start(serve, cfg)
    r = row(cfg, "late.pdf")
    page = c.get("/due?overdue=1").text
    fields = {"csrf": c.csrf(), "id": str(r.id), "pin": pin_on_page(page, r.id), "done": "1"}
    reply = c.post_form("/due/settle", fields | {"overdue": "1"})
    assert reply.headers["Location"].startswith("/due?overdue=1&n=")


# --- B8: wording ------------------------------------------------------------------------


def test_the_missing_note_shows_its_command_as_code(client: UiClient, cfg: Config) -> None:
    row(cfg, "shot-1.png").current_path.unlink()
    page = client.get("/review").text
    assert "<code>flib reconcile</code> in Terminal finds it." in page and "`" not in page


def test_undo_links_say_what_they_undo(client: UiClient, cfg: Config) -> None:
    page = accept(client, cfg, "shot-1.png")
    assert "Undo: put it back in _Review…" in page.text
    page = accept(client, cfg, "shot-2.png", category="", name="other")
    assert "Undo the rename…" in page.text
    page = act(client, "/review/accept", **accept_all_form(client))
    assert "Undo: put these 2 back…" in page.text  # the two left in the group


def test_put_back_after_a_rename_says_the_old_name_returns(client: UiClient, cfg: Config) -> None:
    accept(client, cfg, "shot-1.png", category="", name="renamed")
    r = row(cfg, "shot-1.png")
    page = client.get(f"/undo?file={r.id}").text
    assert "gets its old name back: " in page and "shot-1.png</bdi>" in page


def test_an_undo_outcome_no_longer_kept_says_how_to_go_on(client: UiClient) -> None:
    page = client.get("/undo?n=AAAAAAAAAAAAAAAA").text
    assert "Reload the page from the menu" in page


def test_the_status_card_hides_its_link_when_nothing_waits(client: UiClient) -> None:
    page = client.get("/status").text
    assert '<p class="note" id="review-go" hidden><a href="/review">Go through them</a></p>' in page
    script = client.get("/static/status.js").text
    assert '$("review-go").hidden = data.review.total === 0;' in script


def test_flib_ui_help_lists_review() -> None:
    result = CliRunner().invoke(app, ["ui", "--help"])
    assert "Status, Review, Search, Activity and Due" in " ".join(result.stdout.split())
    assert "review" in result.stdout  # --open's choices


def test_names_are_isolated_left_to_right(client: UiClient, cfg: Config) -> None:
    page = client.get("/review").text
    assert '<bdi class="name" dir="ltr">shot-1.png</bdi>' in page
    for path in ("/activity", "/due"):
        assert '<span class="name">' not in client.get(path).text


def test_a_skipped_file_in_a_batch_says_stays(
    client: UiClient, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    form = accept_all_form(client)
    real = fix_files

    def tamper(config: Config, store: Store, items: list[FixItem], **kw: object) -> object:
        items[0].path.write_bytes(b"changed")
        return real(config, store, items)

    monkeypatch.setattr("file_librarian.ui.actions.fix_files", tamper)
    assert (
        "1 file could not be moved and stays in review."
        in act(client, "/review/accept", **form).text
    )


# --- B5: the page never scrolls sideways at 390 px -------------------------------------


def test_the_phone_menu_wraps_instead_of_overflowing(client: UiClient) -> None:
    css = client.get("/static/app.css").text
    phone = css.split("@media (max-width: 640px)", 1)[1].split("}\n}", 1)[0]
    assert "flex-wrap: wrap" in phone and "flex: 1 1 100%" in phone
    assert "nav .soon { position: relative;" in css

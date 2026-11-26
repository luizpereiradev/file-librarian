"""Round 2 of the review of PR #128 (#115): the undo page and the undo agree, recovery
after a safe save, the batch example, and the mutants that survived round 1."""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

import file_librarian.fix as fix_mod
from file_librarian import due, pipeline
from file_librarian.classifier import correction_examples
from file_librarian.config import Config
from file_librarian.db.store import CorrectionRow, FileRow, Store
from file_librarian.executor import recover
from file_librarian.fix import fix_file
from file_librarian.models import FileStatus, OperationStatus
from file_librarian.protection import LOCKED_NOW
from file_librarian.providers.fake import FakeProvider
from file_librarian.ui.server import Ui
from file_librarian.undo import file_undo_plan, skip_reason, undo_run
from tests._helpers import UiClient, answer, put
from tests.unit.test_ui import (  # noqa: F401 - fixtures: every server is checked the same way
    _Collector,
    events,
    no_hang,
    nothing_left_behind,
)
from tests.unit.test_ui_pages import bill, fill, soon, start
from tests.unit.test_ui_review import REVIEW, UNSURE, act, row, undo_form


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


def skipped_on_page(page: str) -> dict[str, str]:
    """The files an undo confirmation lists as skipped, with why."""
    found = re.findall(
        r'<bdi class="name" dir="ltr">([^<]+)</bdi> <span class="note">skipped: ([^<]+)</span>',
        page,
    )
    return dict(found)


def half_done_fix(cfg: Config, name: str, monkeypatch: pytest.MonkeyPatch) -> int:
    """A fix that moved the file but died before the index was written; its run."""
    r = row(cfg, name)
    real = fix_mod._complete

    def die(*args: object, **kw: object) -> object:
        raise KeyboardInterrupt

    monkeypatch.setattr(fix_mod, "_complete", die)
    with Store.open(cfg.database) as store, pytest.raises(KeyboardInterrupt):
        fix_file(cfg, store, r.current_path, category="Images", file_id=r.id)
    monkeypatch.setattr(fix_mod, "_complete", real)
    with Store.open_read_only(cfg.database) as store:
        return max(op.run_id for op in store.operations(status=OperationStatus.PLANNED))


# --- 1: a half-done move is judged as recovery will leave it ---------------------------


def test_the_undo_page_offers_a_half_done_move_and_the_undo_restores_it(
    client: UiClient, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = row(cfg, "shot-1.png")
    run_id = half_done_fix(cfg, "shot-1.png", monkeypatch)
    page = client.get(f"/undo?run={run_id}").text
    assert "moved again since" not in page and skipped_on_page(page) == {}
    assert f"Undoing run {run_id} puts 1 file back where it was" in page
    done = act(client, "/undo", **undo_form(client, f"run={run_id}"))  # recovers first
    assert "1 restored, 0 skipped" in done.text
    assert row(cfg, "shot-1.png").current_path == before.current_path


# --- 2: one judgement for the page and the undo ----------------------------------------


def test_a_file_moved_away_and_back_is_offered_and_restored(
    client: UiClient, cfg: Config, seeded: int
) -> None:
    r = row(cfg, "shot-1.png")
    with Store.open(cfg.database) as store:
        fix_file(cfg, store, r.current_path, name="tmp-name", file_id=r.id)
        fix_file(cfg, store, row(cfg, "shot-1.png").current_path, name="shot-1", file_id=r.id)
    page = client.get(f"/undo?run={seeded}").text
    assert "shot-1.png" not in skipped_on_page(page)
    done = act(client, "/undo", **undo_form(client, f"run={seeded}"))
    assert row(cfg, "shot-1.png").current_path.parent == cfg.input_paths[0]
    assert "shot-1.png</bdi> <span>is back in Downloads/.</span>" in done.text


@pytest.mark.parametrize("case", ["moved_on", "moved_on_replaced", "edited", "taken", "locked"])
def test_the_page_lists_exactly_what_the_undo_skips(
    client: UiClient,
    cfg: Config,
    seeded: int,
    downloads: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
) -> None:
    r = row(cfg, "shot-1.png")
    if case.startswith("moved_on"):
        with Store.open(cfg.database) as store:
            fix_file(cfg, store, r.current_path, category="Images", file_id=r.id)
        if case == "moved_on_replaced":  # and something else took its old place
            put(r.current_path.parent, r.current_path.name, b"another file")
    elif case == "edited":
        r.current_path.write_bytes(b"edited by hand")
    elif case == "taken":
        put(downloads, "shot-1.png", b"a new download with the old name")
    else:
        real = skip_reason.__globals__["check_content"]

        def locked(path: Path, sha256: str) -> bool | None:
            return None if path == r.current_path else real(path, sha256)

        monkeypatch.setattr("file_librarian.undo.check_content", locked)
    expected = {
        "moved_on": "moved again since, by run",
        "moved_on_replaced": "moved again since, by run",
        "edited": "it changed since it was moved (edited by hand?)",
        "taken": "something else has its original name now",
        "locked": LOCKED_NOW,
    }[case]
    page = client.get(f"/undo?run={seeded}").text
    skipped = skipped_on_page(page)
    assert list(skipped) == ["shot-1.png"], skipped
    assert skipped["shot-1.png"].startswith(expected)
    done = act(client, "/undo", **undo_form(client, f"run={seeded}"))
    assert "0 skipped" not in done.text and "1 skipped" in done.text
    assert f"shot-1.png</bdi> <span>skipped: {expected}" in done.text


def test_the_command_line_says_the_same_as_the_page(cfg: Config, seeded: int) -> None:
    r = row(cfg, "shot-1.png")
    put(r.original_path.parent, r.original_path.name, b"new")
    with Store.open(cfg.database) as store:
        op = file_undo_plan(store, r.current_path, file_id=r.id)
        assert skip_reason(store, op, cfg) == f"original path is taken: {r.original_path}"
        report = undo_run(store, seeded, config=cfg)
    assert (r.current_path, f"original path is taken: {r.original_path}") in report.skipped


def test_an_original_folder_gone_outside_the_library_is_said_by_both(
    cfg: Config, seeded: int, tmp_path: Path
) -> None:
    import dataclasses

    from file_librarian.undo import UndoReport, _undo_one

    r = row(cfg, "shot-1.png")
    gone = tmp_path / "gone" / "shot-1.png"  # never made: not the library's
    inside = cfg.output_root / "Emptied" / "shot-1.png"  # the library's: made again
    with Store.open(cfg.database) as store:
        op = file_undo_plan(store, r.current_path, file_id=r.id)
        outside = dataclasses.replace(op, src_path=gone)
        assert skip_reason(store, outside, cfg) == f"original folder is gone: {gone.parent}"
        report = UndoReport()
        _undo_one(store, outside, report, cfg)
        assert report.skipped == [(r.current_path, f"original folder is gone: {gone.parent}")]
        assert skip_reason(store, dataclasses.replace(op, src_path=inside), cfg) is None
        assert skip_reason(store, outside, None) == f"original folder is gone: {gone.parent}"


# --- 3: recovery after a safe save and a new file at the old name -----------------------


def test_a_moved_file_saved_anew_is_still_found_when_the_old_name_is_reused(
    cfg: Config, seeded: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = row(cfg, "shot-1.png")
    half_done_fix(cfg, "shot-1.png", monkeypatch)
    dst = cfg.output_root / "Images" / "shot-1.png"
    temp = dst.with_name(".save")
    temp.write_bytes(dst.read_bytes())
    temp.replace(dst)  # an atomic save: same bytes, another inode
    r.current_path.write_bytes(b"a new download with the old name")
    with Store.open(cfg.database) as store:
        recover(store, cfg)
    after = row(cfg, "shot-1.png")
    assert after.current_path == dst and after.status is FileStatus.ORGANIZED


def test_a_same_bytes_copy_is_never_taken_while_the_source_still_holds_the_file(
    cfg: Config, seeded: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    r = row(cfg, "shot-1.png")
    put(cfg.output_root / "Images", "shot-1.png", r.current_path.read_bytes())

    def die(src: Path, dst: Path) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("file_librarian.fix.move_no_replace", die)
    with Store.open(cfg.database) as store, pytest.raises(KeyboardInterrupt):
        fix_file(cfg, store, r.current_path, category="Images", file_id=r.id)
    with Store.open(cfg.database) as store:
        recover(store, cfg)
    assert row(cfg, "shot-1.png").current_path == r.current_path


# --- 4: a withheld row never hides its batch --------------------------------------------


def correction(name: str, *, run_id: int | None = 7, extension: str = ".png") -> CorrectionRow:
    return CorrectionRow(
        field="category",
        old_value=None,
        new_value="Images",
        extension=extension,
        is_unit=False,
        description=None,
        original_name=name,
        current_name=name,
        run_id=run_id,
    )


def test_a_withheld_newest_row_never_hides_its_batch(cfg: Config) -> None:
    rows = [correction("id_rsa.png"), correction("shot-2.png"), correction("shot-3.png")]
    examples = correction_examples(rows, cfg)
    assert len(examples) == 1 and examples[0].new_value == "Images"


def test_a_batch_is_one_example_per_extension(cfg: Config) -> None:
    rows = [correction("a.png"), correction("b.png"), correction("c.jpg", extension=".jpg")]
    assert [e.extension for e in correction_examples(rows, cfg)] == [".png", ".jpg"]
    single = [correction("a.png", run_id=None), correction("b.png", run_id=None)]
    assert len(correction_examples(single, cfg)) == 2  # reconcile's: each its own


# --- 5: the mutants that survived ------------------------------------------------------


def two_rows_on_one_path(cfg: Config, downloads: Path) -> tuple[FileRow, FileRow]:
    """A stale row and a live one sharing a path (the old file moved by hand, not yet
    reconciled, and a new one with its name filed since)."""
    fill(downloads, cfg, {"a.pdf": bill(amount="1", due_date=soon(2))})
    old = row(cfg, "a.pdf")
    old.current_path.rename(old.current_path.with_name("elsewhere.pdf"))
    put(downloads, "a.pdf", b"other bytes")
    told = bill(amount="2", due_date=soon(3))
    pipeline.run(cfg, FakeProvider({"a.pdf": told}, default=answer()), simulate=False)
    with Store.open_read_only(cfg.database) as store:
        rows = sorted(
            (r for r in store.all_files() if r.original_name == "a.pdf"), key=lambda r: r.id
        )
    assert len(rows) == 2 and rows[0].current_path == rows[1].current_path
    return rows[0], rows[1]


def test_settle_and_undo_plans_honour_the_row_they_are_given(cfg: Config, downloads: Path) -> None:
    old, new = two_rows_on_one_path(cfg, downloads)
    with Store.open(cfg.database) as store:
        assert file_undo_plan(store, new.current_path, file_id=new.id).file_id == new.id
        assert file_undo_plan(store, old.current_path, file_id=old.id).file_id == old.id
        due.settle(store, new.current_path, done=True, file_id=new.id)
        assert store.file_by_id(new.id).settled_at is not None
        assert store.file_by_id(old.id).settled_at is None


def test_activity_offers_no_undo_when_the_old_place_is_taken(
    serve: list[Ui], cfg: Config, downloads: Path
) -> None:
    run_id = fill(downloads, cfg, {"one.png": answer("Images", confidence=UNSURE)})
    r = row(cfg, "one.png")
    c = start(serve, cfg)
    assert f'href="/undo?file={r.id}"' in c.get("/activity").text
    put(downloads, "one.png", b"a new download with the old name")
    page = c.get("/activity").text
    assert f'href="/undo?file={r.id}"' not in page and f'href="/undo?run={run_id}"' not in page


def test_activity_offers_undo_only_on_a_files_last_move(
    serve: list[Ui], cfg: Config, downloads: Path
) -> None:
    """Moved away and back: the first move's file is where it put it again, and its
    old place is free, but undoing the file means its last move."""
    run_id = fill(downloads, cfg, {"one.png": answer("Images", confidence=UNSURE)})
    r = row(cfg, "one.png")
    with Store.open(cfg.database) as store:
        fix_file(cfg, store, r.current_path, name="away", file_id=r.id)
        back = fix_file(cfg, store, row(cfg, "one.png").current_path, name="one", file_id=r.id)
    page = start(serve, cfg).get("/activity").text
    assert page.count(f'href="/undo?file={r.id}"') == 1
    last_run = page.split(f"run {back.run_id}<", 1)[1].split("</article>", 1)[0]
    assert f'href="/undo?file={r.id}"' in last_run
    first_run = page.split(f"run {run_id}<", 1)[1].split("</article>", 1)[0]
    assert f'href="/undo?file={r.id}"' not in first_run


def test_activity_offers_no_undo_for_a_file_deleted_by_hand(
    serve: list[Ui], cfg: Config, downloads: Path
) -> None:
    run_id = fill(downloads, cfg, {"one.png": answer("Images", confidence=UNSURE)})
    r = row(cfg, "one.png")
    r.current_path.unlink()
    page = start(serve, cfg).get("/activity").text
    assert f'href="/undo?file={r.id}"' not in page and f'href="/undo?run={run_id}"' not in page


def test_a_half_done_move_edited_since_is_never_called_moved_again(
    client: UiClient, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_id = half_done_fix(cfg, "shot-1.png", monkeypatch)
    (cfg.output_root / "Images" / "shot-1.png").write_bytes(b"edited by hand")
    skipped = skipped_on_page(client.get(f"/undo?run={run_id}").text)
    assert skipped == {"shot-1.png": "it changed since it was moved (edited by hand?)."}


def test_a_locked_source_counts_as_still_holding_the_file(
    cfg: Config, seeded: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A source that cannot be read now (the Mac is locked) may still be the file: a
    same-bytes copy at the journaled name is never taken for it."""
    r = row(cfg, "shot-1.png")
    put(cfg.output_root / "Images", "shot-1.png", r.current_path.read_bytes())

    def die(src: Path, dst: Path) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("file_librarian.fix.move_no_replace", die)
    with Store.open(cfg.database) as store, pytest.raises(KeyboardInterrupt):
        fix_file(cfg, store, r.current_path, category="Images", file_id=r.id)
    real = recover.__globals__["check_content"]

    def locked(path: Path, sha256: str) -> bool | None:
        return None if path == r.current_path else real(path, sha256)

    monkeypatch.setattr("file_librarian.executor.check_content", locked)
    with Store.open(cfg.database) as store:
        recover(store, cfg)
    assert row(cfg, "shot-1.png").current_path == r.current_path

"""Category layouts (#86): runs, reconcile, fix, undo and ``flib reprocess --apply-layout``."""

from __future__ import annotations

import errno
import json
import shutil
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from file_librarian import executor, fsops, pipeline
from file_librarian import reprocess as rp
from file_librarian import status as status_mod
from file_librarian.cli import app
from file_librarian.config import Config
from file_librarian.db.store import Store
from file_librarian.executor import RunReport, recover
from file_librarian.finder_tags import read_tags
from file_librarian.fix import fix_file
from file_librarian.fsops import CrossVolumeError
from file_librarian.models import Classification, FileStatus, OperationStatus
from file_librarian.providers.fake import FakeProvider
from file_librarian.reconcile import reconcile
from file_librarian.search import make_filters, search
from file_librarian.undo import undo_run
from tests._helpers import (
    OLD_DATE,
    Crash,
    answer,
    fake_config,
    library,
    organize,
    put,
    row_for,
    rows,
    write_config,
)

pytestmark = pytest.mark.integration

runner = CliRunner()

MAY = date(2024, 5, 1)
YEAR = str(OLD_DATE.year)  # the files' download (birth) date, see tests/_helpers.py


def config(downloads: Path, finance: str | None = None, contracts: str | None = None) -> Config:
    categories = [
        {"name": "Finance", "layout": finance},
        {"name": "Contracts", "layout": contracts},
        {"name": "Other"},
    ]
    return fake_config(downloads, categories=categories)


def filed(reply_category: str = "Finance", **kw: Any) -> Classification:
    return answer(reply_category, **kw)


def no_provider() -> FakeProvider:
    """A provider that fails every request: applying a layout must not ask it."""
    return FakeProvider()


def apply_layout(
    cfg: Config, *, categories: tuple[str, ...] = (), provider: FakeProvider | None = None
) -> tuple[rp.ReprocessPlan, RunReport]:
    with Store.open(cfg.database) as store:
        selection = rp.select_layout(cfg, store, categories=categories, ask=provider is not None)
        planned = rp.plan_layout(selection, cfg, store, provider=provider)
        applied = rp.apply(planned, cfg, store)
    return planned, applied.report


def forget_answers(cfg: Config) -> None:
    """Empty the classification cache, so the provider is really asked."""
    with Store.open(cfg.database) as store:
        store.conn.execute("DELETE FROM classification_cache")


def path_of(cfg: Config, original: str) -> Path:
    return row_for(cfg, original).current_path


# --- runs ---------------------------------------------------------------------------------


def test_a_run_files_into_the_layout_and_the_index_keeps_what_it_used(downloads: Path) -> None:
    cfg = config(downloads, finance="{year}/{issuer}")
    organize(
        downloads,
        cfg,
        {
            "a.pdf": (b"one", filed(document_date=MAY, issuer="Acme")),
            "b.pdf": (b"two", filed(document_date=MAY, issuer="ACME Inc.")),
            "c.pdf": (b"three", filed()),
            "d.pdf": (b"four", filed("Other", issuer="Acme")),
        },
    )
    lib = library(downloads)
    acme = lib / "Finance" / "2024" / "Acme"
    assert path_of(cfg, "a.pdf") == acme / "a.pdf"
    assert path_of(cfg, "b.pdf") == acme / "b.pdf"  # the same company, the same folder
    assert path_of(cfg, "c.pdf") == lib / "Finance" / YEAR / "c.pdf"  # no issuer, no date
    assert path_of(cfg, "d.pdf") == lib / "Other" / "d.pdf"
    row = row_for(cfg, "b.pdf")
    assert (row.category, row.document_date, row.issuer) == ("Finance", "2024-05-01", "ACME Inc.")
    assert row_for(cfg, "c.pdf").issuer is None
    assert read_tags(acme / "a.pdf") == ["Finance"]

    # Search by category, and status, see the nested files like any other.
    with Store.open(cfg.database) as store:
        filters = make_filters(categories=["Finance"], known_categories=["Finance"])
        hits = search(store, "", filters=filters)
    assert sorted(Path(h.path).name for h in hits) == ["a.pdf", "b.pdf", "c.pdf"]
    assert all(h.exists for h in hits)
    assert status_mod.collect(cfg).last_run is not None

    with Store.open(cfg.database) as store:
        report = undo_run(store, config=cfg)
    assert len(report.restored) == 4 and report.skipped == []
    assert sorted(p.name for p in downloads.iterdir() if p.is_file()) == [
        "a.pdf",
        "b.pdf",
        "c.pdf",
        "d.pdf",
    ]


def test_later_runs_reuse_the_issuer_folder_already_there(downloads: Path) -> None:
    cfg = config(downloads, finance="{issuer}")
    organize(downloads, cfg, {"a.pdf": (b"one", filed(issuer="Power Co"))})
    organize(downloads, cfg, {"b.pdf": (b"two", filed(issuer="POWER COMPANY"))})
    organize(downloads, cfg, {"c.pdf": (b"three", filed(issuer="Power Cooperative"))})
    lib = library(downloads)
    assert path_of(cfg, "b.pdf") == lib / "Finance" / "Power Co" / "b.pdf"
    # Another word: maybe another company, so a folder of its own.
    assert path_of(cfg, "c.pdf") == lib / "Finance" / "Power Cooperative" / "c.pdf"


def test_a_taken_name_in_the_layout_folder_gets_a_counter(downloads: Path) -> None:
    cfg = config(downloads, finance="{issuer}")
    lib = library(downloads)
    put(lib / "Finance" / "Acme", "bill.pdf", b"there before")
    organize(downloads, cfg, {"bill.pdf": (b"new", filed(issuer="acme"))})
    assert path_of(cfg, "bill.pdf") == lib / "Finance" / "Acme" / "bill-2.pdf"
    assert (lib / "Finance" / "Acme" / "bill.pdf").read_bytes() == b"there before"


def test_a_duplicate_of_a_nested_file_goes_to_the_duplicates_folder(downloads: Path) -> None:
    cfg = config(downloads, finance="{year}")
    organize(downloads, cfg, {"a.pdf": (b"same", filed())})
    organize(downloads, cfg, {"copy.pdf": (b"same", filed())})
    lib = library(downloads)
    copy = next(r for r in rows(cfg, "SELECT * FROM files") if r["duplicate_of"] is not None)
    assert Path(copy["current_path"]) == lib / cfg.duplicates_folder / "copy.pdf"
    assert path_of(cfg, "a.pdf") == lib / "Finance" / YEAR / "a.pdf"
    assert status_mod.collect(cfg).duplicates == 1


# --- reconcile and fix --------------------------------------------------------------------


def test_reconcile_after_a_move_between_sub_folders_is_no_correction(downloads: Path) -> None:
    cfg = config(downloads, finance="{year}/{issuer}")
    organize(downloads, cfg, {"a.pdf": (b"one", filed(document_date=MAY, issuer="Acme"))})
    lib = library(downloads)
    mine = lib / "Finance" / "Taxes" / "a.pdf"
    mine.parent.mkdir()
    shutil.move(path_of(cfg, "a.pdf"), mine)
    with Store.open(cfg.database) as store:
        report = reconcile(cfg, store)
    assert report.moved == [(lib / "Finance" / "2024" / "Acme" / "a.pdf", mine)]
    assert report.recategorized == []
    assert rows(cfg, "SELECT * FROM corrections") == []
    row = row_for(cfg, "a.pdf")
    assert (row.status, row.category, row.current_path) == (FileStatus.ORGANIZED, "Finance", mine)

    # Into another category's sub-folder: that is a correction.
    other = lib / "Contracts" / "Signed" / "a.pdf"
    other.parent.mkdir(parents=True)
    shutil.move(mine, other)
    with Store.open(cfg.database) as store:
        report = reconcile(cfg, store)
    assert report.recategorized == [(other, "Finance", "Contracts")]


def test_fix_renames_in_place_and_moves_to_another_category_s_top(downloads: Path) -> None:
    cfg = config(downloads, finance="{issuer}")
    organize(downloads, cfg, {"a.pdf": (b"one", filed(issuer="Acme"))})
    lib = library(downloads)
    nested = lib / "Finance" / "Acme" / "a.pdf"
    with Store.open(cfg.database) as store:
        renamed = fix_file(cfg, store, nested, name="invoice")
        assert renamed.destination == nested.with_name("invoice.pdf")
        same = fix_file(cfg, store, renamed.destination, category="finance", name="bill")
        assert same.destination == nested.with_name("bill.pdf")
        moved = fix_file(cfg, store, same.destination, category="Contracts")
        assert moved.destination == lib / "Contracts" / "bill.pdf"
        assert read_tags(moved.destination) == ["Contracts"]
        undo_run(store, config=cfg)
    assert path_of(cfg, "a.pdf") == nested.with_name("bill.pdf")
    assert read_tags(nested.with_name("bill.pdf")) == ["Finance"]


# --- plain reprocess --------------------------------------------------------------------


def reprocess(cfg: Config, provider: FakeProvider) -> tuple[rp.ReprocessPlan, RunReport]:
    forget_answers(cfg)
    with Store.open(cfg.database) as store:
        selection = rp.select(cfg, store)
        planned = rp.plan_reprocess(selection.targets, provider, cfg, store)
        applied = rp.apply(planned, cfg, store, provider="fake", model="x")
    return planned, applied.report


def test_reprocess_follows_the_layout_and_leaves_the_user_s_sub_folders(downloads: Path) -> None:
    cfg = config(downloads, finance="{issuer}", contracts="{issuer}")
    organize(
        downloads,
        cfg,
        {"a.pdf": (b"one", filed(issuer="Acme")), "b.pdf": (b"two", filed(issuer="Acme"))},
    )
    lib = library(downloads)
    mine = lib / "Finance" / "Mine" / "b.pdf"
    mine.parent.mkdir()
    shutil.move(lib / "Finance" / "Acme" / "b.pdf", mine)
    with Store.open(cfg.database) as store:
        reconcile(cfg, store)

    same = FakeProvider(default=filed(issuer="Acme"))
    planned, _ = reprocess(cfg, same)
    assert planned.moves() == []  # a.pdf is where its layout puts it; b.pdf is the user's

    # The model now reads another issuer: a.pdf follows it, and undo gives the old one back.
    other = FakeProvider(default=filed(issuer="Northwind"))
    planned, report = reprocess(cfg, other)
    assert [c.relocation.destination for c in planned.moves()] == [
        lib / "Finance" / "Northwind" / "a.pdf"
    ]
    assert row_for(cfg, "a.pdf").issuer == "Northwind"
    assert path_of(cfg, "b.pdf") == mine
    with Store.open(cfg.database) as store:
        undo_run(store, report.run_id, config=cfg)
    row = row_for(cfg, "a.pdf")
    assert (row.issuer, row.current_path) == ("Acme", lib / "Finance" / "Acme" / "a.pdf")

    # A new category takes both, the user's file too, into its own layout.
    planned, _ = reprocess(cfg, FakeProvider(default=filed("Contracts", issuer="Acme")))
    assert path_of(cfg, "a.pdf") == lib / "Contracts" / "Acme" / "a.pdf"
    assert path_of(cfg, "b.pdf") == lib / "Contracts" / "Acme" / "b.pdf"


# --- reprocess --apply-layout --------------------------------------------------------------


def flat_library(downloads: Path) -> Path:
    """Four files filed flat, with what layouts need already in the index."""
    cfg = config(downloads)
    organize(
        downloads,
        cfg,
        {
            "a.pdf": (b"one", filed(document_date=MAY, issuer="Acme")),
            "b.pdf": (b"two", filed(document_date=date(2025, 2, 3), issuer="Acme Ltda")),
            "c.pdf": (b"three", filed()),
            "d.pdf": (b"four", filed("Contracts", issuer="Power Co")),
        },
    )
    return library(downloads)


def test_apply_layout_moves_files_without_the_provider_and_undoes(downloads: Path) -> None:
    lib = flat_library(downloads)
    cfg = config(downloads, finance="{year}/{month}", contracts="{issuer}")
    planned, report = apply_layout(cfg)
    assert planned.cost_usd == 0 and planned.learned == {}
    assert {c.row.original_name: c.relocation.destination for c in planned.moves()} == {
        "a.pdf": lib / "Finance" / "2024" / "05" / "a.pdf",
        "b.pdf": lib / "Finance" / "2025" / "02" / "b.pdf",
        "c.pdf": lib / "Finance" / YEAR / f"{OLD_DATE.month:02d}" / "c.pdf",
        "d.pdf": lib / "Contracts" / "Power Co" / "d.pdf",
    }
    assert report.moved == 4 and report.errors == 0
    assert path_of(cfg, "d.pdf") == lib / "Contracts" / "Power Co" / "d.pdf"
    assert read_tags(lib / "Contracts" / "Power Co" / "d.pdf") == ["Contracts"]
    trigger = rows(cfg, "SELECT trigger FROM runs ORDER BY id DESC LIMIT 1")[0][0]
    assert trigger == "reprocess"

    # Applying it again changes nothing: every file is in place.
    again, _ = apply_layout(cfg)
    assert again.moves() == [] and again.in_place == 4

    with Store.open(cfg.database) as store:
        undone = undo_run(store, report.run_id, config=cfg)
    assert len(undone.restored) == 4
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        assert path_of(cfg, name) == lib / "Finance" / name
    assert path_of(cfg, "d.pdf") == lib / "Contracts" / "d.pdf"
    assert read_tags(lib / "Contracts" / "d.pdf") == ["Contracts"]


def test_apply_layout_only_for_the_chosen_category(downloads: Path) -> None:
    lib = flat_library(downloads)
    cfg = config(downloads, finance="{year}", contracts="{issuer}")
    planned, _ = apply_layout(cfg, categories=("Contracts",))
    assert [c.row.original_name for c in planned.moves()] == ["d.pdf"]
    assert path_of(cfg, "a.pdf") == lib / "Finance" / "a.pdf"


def test_files_without_an_issuer_stay_unless_asked(downloads: Path) -> None:
    lib = flat_library(downloads)
    cfg = config(downloads, finance="{issuer}")
    planned, _ = apply_layout(cfg)
    assert sorted(c.row.original_name for c in planned.moves()) == ["a.pdf", "b.pdf"]
    assert path_of(cfg, "a.pdf") == lib / "Finance" / "Acme" / "a.pdf"
    assert path_of(cfg, "b.pdf") == lib / "Finance" / "Acme" / "b.pdf"
    assert path_of(cfg, "c.pdf") == lib / "Finance" / "c.pdf"
    with Store.open(cfg.database) as store:
        left = rp.select_layout(cfg, store).left
    assert [(p.name, why.split(";")[0]) for p, why in left] == [("c.pdf", "issuer unknown")]

    # Asked, the provider names the issuer; only that (and the date) is kept.
    forget_answers(cfg)
    ask = FakeProvider({"c.pdf": answer("Other", issuer="Fabrikam", document_date=MAY)})
    planned, report = apply_layout(cfg, provider=ask)
    assert [ctx.path.name for ctx in ask.calls] == ["c.pdf"]
    assert planned.learned == {row_for(cfg, "c.pdf").id: ("2024-05-01", "Fabrikam")}
    row = row_for(cfg, "c.pdf")
    assert (row.category, row.issuer, row.document_date) == ("Finance", "Fabrikam", "2024-05-01")
    assert row.current_path == lib / "Finance" / "Fabrikam" / "c.pdf"
    with Store.open(cfg.database) as store:
        undo_run(store, report.run_id, config=cfg)
    row = row_for(cfg, "c.pdf")
    assert (row.issuer, row.document_date, row.current_path) == (
        None,
        None,
        lib / "Finance" / "c.pdf",
    )


def test_an_answer_without_issuer_leaves_the_file(downloads: Path) -> None:
    flat_library(downloads)
    cfg = config(downloads, finance="{issuer}")
    planned, report = apply_layout(cfg, provider=FakeProvider(default=answer("Finance")))
    assert [p.name for p, _ in planned.kept] == ["c.pdf"]
    assert "c.pdf" not in [c.row.original_name for c in planned.moves()]
    assert report.errors == 0


def test_older_rows_use_the_cached_answer(downloads: Path) -> None:
    lib = flat_library(downloads)
    cfg = config(downloads, contracts="{year}/{issuer}")
    with Store.open(cfg.database) as store:
        # As filed before the index kept these: nothing stored, only a cached answer.
        store.conn.execute("UPDATE files SET issuer = NULL, document_date = NULL")
        store.conn.execute("DELETE FROM classification_cache")
        sha = row_for(cfg, "d.pdf").sha256
        cached = json.dumps({"document_date": "2023-09-09", "issuer": "Northwind"})
        store.cache_answer(sha, "an older key", cached)
    planned, _ = apply_layout(cfg)
    assert [c.relocation.destination for c in planned.moves()] == [
        lib / "Contracts" / "2023" / "Northwind" / "d.pdf"
    ]


def test_hand_placed_review_duplicate_and_sensitive_files_are_never_touched(
    downloads: Path,
) -> None:
    lib = flat_library(downloads)
    cfg = config(downloads, finance="{year}")
    # The user files a.pdf into a folder of their own; reconcile records it.
    mine = lib / "Finance" / "Mine" / "a.pdf"
    mine.parent.mkdir()
    shutil.move(lib / "Finance" / "a.pdf", mine)
    organize(downloads, cfg, {"unsure.pdf": (b"five", answer("Finance", "x", 0.1))})
    organize(downloads, cfg, {"copy.pdf": (b"two", filed())})
    put(downloads, "key.txt", b"token = " + b"ghp_" + b"a" * 36)
    pipeline.run(cfg, FakeProvider(default=filed()), simulate=False)
    with Store.open(cfg.database) as store:
        reconcile(cfg, store)
        before = {r.id: (r.status, r.current_path) for r in store.all_files()}
    planned, _ = apply_layout(cfg)
    assert sorted(c.row.original_name for c in planned.moves()) == ["b.pdf", "c.pdf"]
    with Store.open(cfg.database) as store:
        after = {r.id: (r.status, r.current_path) for r in store.all_files()}
        left = rp.select_layout(cfg, store).left
    moved = {k for k in before if before[k] != after[k]}
    assert moved == {row_for(cfg, "b.pdf").id, row_for(cfg, "c.pdf").id}
    assert path_of(cfg, "a.pdf") == mine
    assert ("a.pdf", "in a sub-folder you chose; it stays") in [(p.name, w) for p, w in left]
    statuses = {s for s, _ in after.values()}
    assert {FileStatus.REVIEW, FileStatus.DUPLICATE, FileStatus.SENSITIVE} <= statuses


def test_a_layout_removed_brings_flib_s_sub_folders_back_to_the_top(downloads: Path) -> None:
    cfg = config(downloads, finance="{issuer}")
    organize(downloads, cfg, {"a.pdf": (b"one", filed(issuer="Acme"))})
    lib = library(downloads)
    mine = lib / "Finance" / "Mine" / "b.pdf"
    organize(downloads, cfg, {"b.pdf": (b"two", filed(issuer="Acme"))})
    mine.parent.mkdir()
    shutil.move(path_of(cfg, "b.pdf"), mine)
    with Store.open(cfg.database) as store:
        reconcile(cfg, store)
    flat = config(downloads)
    planned, _ = apply_layout(flat)
    assert [c.relocation.destination for c in planned.moves()] == [lib / "Finance" / "a.pdf"]
    assert path_of(flat, "b.pdf") == mine


def test_a_category_rename_keeps_the_user_s_sub_folder_theirs(downloads: Path) -> None:
    before = fake_config(downloads, categories=[{"name": "Bills"}, {"name": "Other"}])
    organize(downloads, before, {"a.pdf": (b"one", filed("Bills"))})
    lib = library(downloads)
    mine = lib / "Bills" / "Mine" / "a.pdf"
    mine.parent.mkdir()
    shutil.move(lib / "Bills" / "a.pdf", mine)
    with Store.open(before.database) as store:
        reconcile(before, store)
    after = config(downloads, finance="{year}")
    with Store.open(after.database) as store:
        rp.apply(rp.plan_rename(after, store, "Bills", "Finance"), after, store)
    assert path_of(after, "a.pdf") == lib / "Finance" / "Mine" / "a.pdf"
    planned, _ = apply_layout(after)
    assert planned.moves() == []


def test_apply_layout_refuses_orphan_categories_and_unknown_names(downloads: Path) -> None:
    flat_library(downloads)
    renamed = fake_config(downloads, categories=[{"name": "Money"}, {"name": "Contracts"}])
    with Store.open(renamed.database) as store, pytest.raises(rp.ReprocessError, match="Finance"):
        rp.select_layout(renamed, store)
    cfg = config(downloads, finance="{year}")
    with Store.open(cfg.database) as store, pytest.raises(rp.ReprocessError, match="unknown"):
        rp.select_layout(cfg, store, categories=["Nope"])


def test_a_file_changed_since_it_was_filed_is_left(downloads: Path) -> None:
    lib = flat_library(downloads)
    cfg = config(downloads, finance="{year}")
    (lib / "Finance" / "a.pdf").write_bytes(b"edited")
    planned, _ = apply_layout(cfg)
    assert ("a.pdf", "changed or moved since it was indexed; run `flib reconcile`") in [
        (p.name, why) for p, why in planned.kept
    ]
    assert (lib / "Finance" / "a.pdf").read_bytes() == b"edited"


# --- crashes and other volumes ---------------------------------------------------------------


def test_a_crash_after_a_layout_move_is_finished_by_recovery(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = flat_library(downloads)
    cfg = config(downloads, contracts="{issuer}")

    def crash(*_args: Any, **_kwargs: Any) -> None:
        raise Crash

    with monkeypatch.context() as patch:
        patch.setattr(executor, "finish_relocation", crash)
        with pytest.raises(Crash):
            apply_layout(cfg)
    target = lib / "Contracts" / "Power Co" / "d.pdf"
    assert target.exists() and path_of(cfg, "d.pdf") == lib / "Contracts" / "d.pdf"
    with Store.open(cfg.database) as store:
        assert recover(store, cfg) == 1
    assert path_of(cfg, "d.pdf") == target
    assert read_tags(target) == ["Contracts"]
    with Store.open(cfg.database) as store:
        undo_run(store, config=cfg)
    assert path_of(cfg, "d.pdf") == lib / "Contracts" / "d.pdf"


def test_a_crash_before_a_layout_move_leaves_the_file(
    downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lib = flat_library(downloads)
    cfg = config(downloads, contracts="{issuer}")

    def crash(*_args: Any, **_kwargs: Any) -> None:
        raise Crash

    with monkeypatch.context() as patch:
        patch.setattr(executor, "_move", crash)
        with pytest.raises(Crash):
            apply_layout(cfg)
    with Store.open(cfg.database) as store:
        assert recover(store, cfg) == 1
        assert store.operations()[-1].status is OperationStatus.FAILED
    assert path_of(cfg, "d.pdf") == lib / "Contracts" / "d.pdf"
    assert (lib / "Contracts" / "d.pdf").exists()


@pytest.mark.parametrize(("crash_at", "finished"), [("_discard", True), ("_copyfile", False)])
def test_a_cross_volume_layout_move_is_recovered(
    downloads: Path, monkeypatch: pytest.MonkeyPatch, crash_at: str, finished: bool
) -> None:
    lib = flat_library(downloads)
    cfg = config(downloads, contracts="{issuer}")

    def exdev(src: Path, dst: Path) -> None:
        raise CrossVolumeError(errno.EXDEV, "cross-device link", str(dst))

    real = getattr(fsops, crash_at)

    def crash(*args: Any, **kwargs: Any) -> Any:
        if not finished:
            real(*args, **kwargs)
        raise Crash

    with monkeypatch.context() as patch:
        patch.setattr(executor, "move_no_replace", exdev)
        patch.setattr(fsops, crash_at, crash)
        with pytest.raises(Crash):
            apply_layout(cfg)
    with Store.open(cfg.database) as store:
        assert recover(store, cfg) == 1
    target = lib / "Contracts" / "Power Co" / "d.pdf"
    if finished:
        assert path_of(cfg, "d.pdf") == target
        assert not (lib / "Contracts" / "d.pdf").exists()
    else:
        assert path_of(cfg, "d.pdf") == lib / "Contracts" / "d.pdf"
        assert not target.exists()
    assert list(lib.rglob(".flib-*")) == []


# --- command line ---------------------------------------------------------------------------


def cli_config(tmp_path: Path, downloads: Path, layout: str = "{year}") -> Path:
    return write_config(
        tmp_path,
        downloads,
        extra=(
            f'categories: [{{name: Finance, layout: "{layout}"}}, {{name: Contracts}}, '
            "{name: Other}]\n"
        ),
    )


def test_cli_simulates_asks_then_applies(downloads: Path, tmp_path: Path) -> None:
    lib = flat_library(downloads)
    path = cli_config(tmp_path, downloads)
    simulated = runner.invoke(app, ["reprocess", "-c", str(path), "--apply-layout", "--simulate"])
    assert simulated.exit_code == 0, simulated.output
    assert "Finance/2024/a.pdf" in simulated.output
    assert "Simulation: nothing moved" in simulated.output
    assert (lib / "Finance" / "a.pdf").exists()

    declined = runner.invoke(app, ["reprocess", "-c", str(path), "--apply-layout"], input="n\n")
    assert "Nothing changed." in declined.output and (lib / "Finance" / "a.pdf").exists()

    applied = runner.invoke(app, ["reprocess", "-c", str(path), "--apply-layout", "--yes"])
    assert applied.exit_code == 0, applied.output
    assert "`flib undo` reverts it" in applied.output
    assert (lib / "Finance" / "2024" / "a.pdf").exists()
    again = runner.invoke(app, ["reprocess", "-c", str(path), "--apply-layout", "--yes"])
    assert "already follow their category's layout" in again.output
    assert "Nothing to move" in again.output


def test_cli_asks_for_issuers_with_the_cost_first(downloads: Path, tmp_path: Path) -> None:
    lib = flat_library(downloads)
    path = cli_config(tmp_path, downloads, "{issuer}")
    forget_answers(fake_config(downloads))
    declined = runner.invoke(
        app,
        ["reprocess", "-c", str(path), "--apply-layout", "--ask-issuer", "--simulate"],
        input="n\n",
    )
    assert declined.exit_code == 0, declined.output
    assert "1 file(s) need their issuer" in declined.output
    assert "Nothing sent" in declined.output
    # The fake provider answers with no issuer: c.pdf stays all the same.
    applied = runner.invoke(
        app, ["reprocess", "-c", str(path), "--apply-layout", "--ask-issuer", "--yes"]
    )
    assert applied.exit_code == 0, applied.output
    assert (lib / "Finance" / "c.pdf").exists()
    assert (lib / "Finance" / "Acme" / "a.pdf").exists()


@pytest.mark.parametrize(
    ("args", "message"),
    [
        (["--apply-layout", "--rename-category", "A", "B"], "no category migration"),
        (["--apply-layout", "--remove-category", "A"], "no category migration"),
        (["--apply-layout", "--rename"], "only moves files into their layout"),
        (["--apply-layout", "--since", "2026-01-01"], "only moves files into their layout"),
        (["--apply-layout", "--scrub-descriptions"], "only moves files into their layout"),
        (["--ask-issuer"], "goes with --apply-layout"),
        (["--apply-layout", "--category", "Nope"], "unknown category"),
    ],
)
def test_cli_refusals(downloads: Path, tmp_path: Path, args: list[str], message: str) -> None:
    flat_library(downloads)
    path = cli_config(tmp_path, downloads)
    result = runner.invoke(app, ["reprocess", "-c", str(path), *args])
    assert result.exit_code == 1 and message in result.stderr

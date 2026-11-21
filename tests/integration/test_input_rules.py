"""More than one inbox, each with its own rules (#89), end to end with the fake provider.

Every folder is a temporary one under the fake home; nothing real is touched.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from file_librarian import pipeline
from file_librarian import reprocess as rp
from file_librarian import status as st
from file_librarian.config import Config
from file_librarian.db.store import Store
from file_librarian.doctor import redacted_config, redactor_for
from file_librarian.models import FileStatus
from file_librarian.providers.fake import FakeProvider
from file_librarian.reconcile import reconcile
from file_librarian.runlog import write_log
from file_librarian.scanner import DAY, SkipReason, days_left, scan
from file_librarian.undo import undo_run
from tests._helpers import OLD, FakeProtection, answer, fake_config, put, row_for, rows

LIBRARY = "Organized"


@pytest.fixture
def desktop(fake_home: Path) -> Path:
    folder = fake_home / "Desktop"
    folder.mkdir()
    return folder


def two_inboxes(downloads: Path, desktop: Path, **rules: Any) -> Config:
    """Downloads with no rules and the Desktop with ``rules``; the library outside both."""
    entry: dict[str, Any] = {"path": str(desktop), **rules}
    return fake_config(inputs=[str(downloads), entry], output_root=str(downloads.parent / LIBRARY))


def lib(cfg: Config) -> Path:
    return cfg.output_root


def newest(path: Path) -> float:
    st_ = os.lstat(path)
    return max(st_.st_mtime, st_.st_ctime)


def skipped(result: Any) -> dict[str, SkipReason]:
    return {path.name: reason for path, reason in result.skipped}


# --- only / ignore -----------------------------------------------------------------------


def test_only_takes_matching_items_and_leaves_everything_else(
    downloads: Path, desktop: Path
) -> None:
    cfg = two_inboxes(downloads, desktop, only=["*.pdf", "Screenshot*"])
    put(desktop, "report.pdf", b"a report")
    put(desktop, "Screenshot 1.png", b"pixels")
    put(desktop, "notes.txt", b"work in progress")
    put(desktop, "project/main.py", b"print()")
    put(downloads, "notes.txt", b"a download")  # Downloads has no rules

    found = scan(cfg, lambda _sha: None)

    taken = sorted(c.path.name for c in found.candidates)
    assert taken == ["Screenshot 1.png", "notes.txt", "report.pdf"]
    assert {c.path.parent for c in found.candidates if c.path.name == "notes.txt"} == {downloads}
    assert skipped(found) == {
        "notes.txt": SkipReason.NOT_ONLY,
        "project": SkipReason.NOT_ONLY,
    }
    assert {c.input for c in found.candidates} == {downloads, desktop}


def test_ignore_adds_to_the_global_list_and_wins_over_only(downloads: Path, desktop: Path) -> None:
    cfg = fake_config(
        inputs=[str(downloads), {"path": str(desktop), "only": ["*.pdf"], "ignore": ["draft*"]}],
        output_root=str(downloads.parent / LIBRARY),
        ignore=["*.tmp"],
    )
    put(desktop, "draft contract.pdf", b"not yet")
    put(desktop, "contract.pdf", b"signed")
    put(desktop, "cache.tmp", b"x")
    put(downloads, "draft contract.pdf", b"a download with the same kind of name")

    found = scan(cfg, lambda _sha: None)

    taken = sorted((c.path.parent.name, c.path.name) for c in found.candidates)
    assert taken == [("Desktop", "contract.pdf"), ("Downloads", "draft contract.pdf")]
    assert skipped(found) == {
        "draft contract.pdf": SkipReason.IGNORED,
        "cache.tmp": SkipReason.IGNORED,
    }


# --- min_age_days ------------------------------------------------------------------------


def test_min_age_boundaries_with_an_injected_clock(downloads: Path, desktop: Path) -> None:
    cfg = two_inboxes(downloads, desktop, min_age_days=7)
    item = put(desktop, "report.pdf", b"a report")
    born = newest(item)  # ctime is "now" even though put backdated the mtime

    def reason_at(now: float) -> SkipReason | None:
        found = scan(cfg, lambda _sha: None, now=now)
        if any(c.path == item for c in found.candidates):
            return None
        return skipped(found)["report.pdf"]

    assert reason_at(born + 7 * DAY - 1) is SkipReason.TOO_RECENT
    assert reason_at(born + 7 * DAY) is None  # exactly old enough
    early = scan(cfg, lambda _sha: None, now=born + 1)
    assert early.days_left[item] == 7
    late = scan(cfg, lambda _sha: None, now=born + 6 * DAY + 1)
    assert late.days_left[item] == 1


def test_age_counts_from_the_last_change_not_the_old_modification_time(
    downloads: Path, desktop: Path
) -> None:
    """A file with an old mtime that just arrived (moved or copied in) is still too recent."""
    cfg = two_inboxes(downloads, desktop, min_age_days=3)
    item = put(desktop, "old.pdf", b"x", mtime=1_000_000_000.0)  # 2001
    found = scan(cfg, lambda _sha: None, now=newest(item) + DAY)
    assert skipped(found)["old.pdf"] is SkipReason.TOO_RECENT
    assert found.days_left[item] == 2


def test_a_folder_unit_is_as_young_as_the_newest_item_inside(
    downloads: Path, desktop: Path
) -> None:
    cfg = two_inboxes(downloads, desktop, min_age_days=2)
    put(desktop, "album/a.jpg", b"a")
    inner = put(desktop, "album/b.jpg", b"b")
    folder = desktop / "album"
    os.utime(folder, (1_000_000_000.0, 1_000_000_000.0))
    now = max(newest(folder), newest(inner)) + DAY
    os.utime(inner, (now - 3600, now - 3600))  # edited an hour before "now"

    found = scan(cfg, lambda _sha: None, now=now)

    assert skipped(found)["album"] is SkipReason.TOO_RECENT
    assert found.days_left[folder] == 2


def test_days_left_rounds_up() -> None:
    assert days_left(0.0, 7) == 7
    assert days_left(6.5 * DAY, 7) == 1
    assert days_left(7 * DAY - 1, 7) == 1


def test_a_recent_file_is_never_touched_and_not_pending_work(
    downloads: Path, desktop: Path, fake_protection: FakeProtection
) -> None:
    cfg = two_inboxes(downloads, desktop, min_age_days=7)
    item = put(desktop, "report.pdf", b"a report")
    unit = put(desktop, "album/a.jpg", b"a").parent
    fake_protection.lock_screen()  # the scheduled run's situation: the Mac is locked
    provider = FakeProvider(default=answer("Finance"))

    outcome = pipeline.run(cfg, provider, simulate=False, now=newest(item) + DAY)

    assert item.exists() and unit.exists()
    assert provider.calls == []
    assert outcome.deferred == 0
    reasons = skipped(outcome.scan)
    assert reasons == {"report.pdf": SkipReason.TOO_RECENT, "album": SkipReason.TOO_RECENT}
    # Not locked: the scheduled run does not wait for an unlock because of them.
    assert SkipReason.LOCKED not in reasons.values()
    with Store.open(cfg.database) as store:
        assert store.all_files() == []  # nothing recorded: the next run looks again


def test_a_recent_file_is_filed_once_old_enough(downloads: Path, desktop: Path) -> None:
    cfg = two_inboxes(downloads, desktop, min_age_days=7)
    item = put(desktop, "report.pdf", b"a report")
    provider = FakeProvider(default=answer("Finance"))

    pipeline.run(cfg, provider, simulate=False, now=newest(item) + DAY)
    assert item.exists()
    pipeline.run(cfg, provider, simulate=False, now=newest(item) + 8 * DAY)

    assert not item.exists()
    assert (lib(cfg) / "Finance" / "report.pdf").exists()


# --- a fixed category --------------------------------------------------------------------


def test_a_fixed_category_makes_no_provider_call(downloads: Path, desktop: Path) -> None:
    cfg = two_inboxes(downloads, desktop, category="Images", layout="{year}/{month}")
    shot = put(desktop, "Screenshot 2026-03-04 at 10.00.00.png", b"pixels")
    born = date.fromtimestamp(shot.stat().st_birthtime)
    provider = FakeProvider()  # no answers: any call would fail the file

    outcome = pipeline.run(cfg, provider, simulate=False)

    assert provider.calls == []
    assert outcome.cost_usd == 0.0
    expected = lib(cfg) / "Images" / f"{born.year:04d}" / f"{born.month:02d}" / shot.name
    assert expected.exists()
    row = row_for(cfg, shot.name)
    assert (row.status, row.category) == (FileStatus.ORGANIZED, "Images")
    assert row.description is None and row.confidence is None
    (provider_row,) = rows(cfg, "SELECT provider, model FROM files")
    assert tuple(provider_row) == (None, None)
    assert row.tags == ["screenshot"]
    assert row.source_input == desktop
    item = outcome.plan.items[0]
    assert item.by_rule and "input rule, no AI" in item.reason
    with Store.open(cfg.database) as store:
        assert store.cached_answers() == []  # nothing to cache: nobody was asked


def test_a_fixed_category_uses_the_categorys_own_layout_and_can_rename(
    downloads: Path, desktop: Path
) -> None:
    cfg = fake_config(
        inputs=[str(downloads), {"path": str(desktop), "category": "Images", "rename": True}],
        output_root=str(downloads.parent / LIBRARY),
        categories=[{"name": "Images", "layout": "{year}"}, {"name": "Other"}],
    )
    shot = put(desktop, "holiday beach.jpg", b"pixels")
    born = date.fromtimestamp(shot.stat().st_birthtime)

    pipeline.run(cfg, FakeProvider(), simulate=False)

    folder = lib(cfg) / "Images" / f"{born.year:04d}"
    assert [p.name for p in folder.iterdir()] == [f"{born.isoformat()}_holiday-beach.jpg"]


def test_a_sensitive_file_in_a_fixed_category_input_stays_put(
    downloads: Path, desktop: Path
) -> None:
    cfg = two_inboxes(downloads, desktop, category="Images")
    key = put(desktop, "id_rsa", b"-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n")
    provider = FakeProvider()

    pipeline.run(cfg, provider, simulate=False)

    assert key.exists()
    assert provider.calls == []
    assert row_for(cfg, "id_rsa").status is FileStatus.SENSITIVE


def test_other_inputs_are_still_classified(downloads: Path, desktop: Path) -> None:
    cfg = two_inboxes(downloads, desktop, category="Images")
    put(desktop, "shot.png", b"pixels")
    put(downloads, "invoice.txt", b"an invoice")
    provider = FakeProvider({"invoice.txt": answer("Finance")})

    pipeline.run(cfg, provider, simulate=False)

    assert [c.path.name for c in provider.calls] == ["invoice.txt"]
    assert (lib(cfg) / "Finance" / "invoice.txt").exists()
    assert (lib(cfg) / "Images" / "shot.png").exists()
    assert row_for(cfg, "invoice.txt").source_input == downloads


# --- folders and duplicates --------------------------------------------------------------


def test_folders_and_duplicates_can_differ_per_input(downloads: Path, desktop: Path) -> None:
    cfg = two_inboxes(downloads, desktop, folders="ignore", duplicates="leave")
    put(desktop, "project/main.py", b"print()")
    put(downloads, "project/main.py", b"print('downloads')")
    os.utime(downloads / "project", (OLD, OLD))
    put(downloads, "invoice.txt", b"an invoice")
    copy = put(desktop, "invoice copy.txt", b"an invoice")
    provider = FakeProvider(default=answer("Other"))

    outcome = pipeline.run(cfg, provider, simulate=False)

    assert (desktop / "project").is_dir()  # folders: ignore on the Desktop
    assert (lib(cfg) / "Other" / "project").is_dir()  # Downloads keeps as_unit
    assert copy.exists()  # duplicates: leave on the Desktop
    assert ("invoice copy.txt", SkipReason.DUPLICATE) in [
        (p.name, r) for p, r in outcome.scan.skipped
    ]
    assert ("project", SkipReason.FOLDER_IGNORED) in [(p.name, r) for p, r in outcome.scan.skipped]

    # Downloads (duplicates: move) sets aside a copy of the same content.
    again = put(downloads, "invoice (1).txt", b"an invoice")
    pipeline.run(cfg, provider, simulate=False)
    assert not again.exists()
    assert (lib(cfg) / cfg.duplicates_folder / "invoice (1).txt").exists()
    assert copy.exists()


# --- which input a file came from: undo, reconcile, status -----------------------------


def test_undo_puts_each_file_back_in_its_own_input(downloads: Path, desktop: Path) -> None:
    cfg = two_inboxes(downloads, desktop)
    a = put(downloads, "a.txt", b"from downloads")
    b = put(desktop, "b.txt", b"from the desktop")
    pipeline.run(cfg, FakeProvider(default=answer("Other")), simulate=False)
    assert not a.exists() and not b.exists()
    assert row_for(cfg, "b.txt").source_input == desktop

    with Store.open(cfg.database) as store:
        undo_run(store, config=cfg)

    assert a.read_bytes() == b"from downloads"
    assert b.read_bytes() == b"from the desktop"


def test_reconcile_knows_a_file_dragged_back_to_its_input(downloads: Path, desktop: Path) -> None:
    cfg = two_inboxes(downloads, desktop)
    put(desktop, "b.txt", b"from the desktop")
    put(downloads, "c.txt", b"from downloads")
    pipeline.run(cfg, FakeProvider(default=answer("Other")), simulate=False)
    (lib(cfg) / "Other" / "b.txt").rename(desktop / "b.txt")  # back where it came from
    (lib(cfg) / "Other" / "c.txt").rename(desktop / "c.txt")  # to the other inbox

    with Store.open(cfg.database) as store:
        report = reconcile(cfg, store)

    assert len(report.moved) == 2
    b = row_for(cfg, "b.txt")
    assert (b.status, b.current_path, b.source_input) == (
        FileStatus.SKIPPED,
        desktop / "b.txt",
        desktop,
    )
    c = row_for(cfg, "c.txt")
    # It keeps the input it was taken from; where it is now is its current path.
    assert (c.status, c.current_path, c.source_input) == (
        FileStatus.SKIPPED,
        desktop / "c.txt",
        downloads,
    )
    # The next run leaves both alone.
    outcome = pipeline.run(cfg, FakeProvider(), simulate=False)
    assert skipped(outcome.scan) == {"b.txt": SkipReason.KNOWN, "c.txt": SkipReason.KNOWN}


def test_status_and_doctor_group_by_input_without_names(downloads: Path, desktop: Path) -> None:
    cfg = fake_config(
        inputs=[
            str(downloads),
            {"path": str(desktop), "only": ["Tax*", "*.pdf"], "min_age_days": 7},
        ],
        output_root=str(downloads.parent / LIBRARY),
    )
    put(downloads, "a.txt", b"a")
    put(downloads, "id_rsa", b"key")
    item = put(desktop, "report.pdf", b"r")
    pipeline.run(cfg, FakeProvider(default=answer("Other")), simulate=False, now=newest(item))
    pipeline.run(
        cfg, FakeProvider(default=answer("Other")), simulate=False, now=newest(item) + 8 * DAY
    )

    state = st.collect(cfg)
    assert state.by_input is not None
    assert [(c.folder, c.filed, c.left) for c in state.by_input] == [
        (downloads, 1, 1),
        (desktop, 1, 0),
    ]
    mine = "\n".join(st.render(state, cfg))
    assert f"  {downloads}: 1 file filed, 1 left in place" in mine
    private = "\n".join(st.render(state, cfg, private=True))
    assert "  <input-1>: 1 file filed, 1 left in place" in private
    assert "  <input-2>: 1 file filed, 0 left in place" in private
    assert str(desktop) not in private

    r = redactor_for(cfg)
    dumped = "\n".join(redacted_config(cfg, r))
    assert "- <input-1>" in dumped
    assert "path: <input-2>" in dumped
    assert "<pattern-1>" in dumped and "'*.pdf'" in dumped
    assert "Tax" not in dumped and str(desktop) not in dumped
    assert r.text(f"moved {desktop}/x.pdf").startswith("moved <input-2>/")


def test_status_counts_items_too_recent_in_the_last_run(downloads: Path, desktop: Path) -> None:
    cfg = two_inboxes(downloads, desktop, min_age_days=7)
    put(desktop, "report.pdf", b"r")
    put(downloads, "a.txt", b"a")
    pipeline.run(cfg, FakeProvider(default=answer("Other")), simulate=False)
    state = st.collect(cfg)
    run_id = state.last_run.id  # type: ignore[union-attr]

    write_log(cfg.database, run_id, ["  skip    report.pdf  (too recent (7 days left))"])

    state = st.collect(cfg)
    assert state.skipped is not None and state.skipped["too_recent"] == 1
    assert "1 too recent for their input's min_age_days" in "\n".join(st.render(state, cfg))


def test_reprocess_leaves_files_filed_by_rule_alone(downloads: Path, desktop: Path) -> None:
    cfg = two_inboxes(downloads, desktop, category="Images", layout="{year}")
    put(desktop, "shot.png", b"pixels")
    put(downloads, "photo.jpg", b"a photo")
    pipeline.run(cfg, FakeProvider({"photo.jpg": answer("Images")}), simulate=False)

    with Store.open(cfg.database) as store:
        selection = rp.select(cfg, store)
        layout = rp.select_layout(cfg, store)

    assert [t.row.original_name for t in selection.targets] == ["photo.jpg"]
    assert [(p.name, why) for p, why in selection.left] == [("shot.png", rp.RULE_FILED)]
    assert [p.row.original_name for p in layout.ready] == ["photo.jpg"]
    assert [p.name for p, _ in layout.left] == ["shot.png"]

"""Moves to another volume: copy, verify, then delete the source (RF-33).

CI cannot mount a second volume, so ``cross_volume`` makes the executor's and
undo's rename fail with ``CrossVolumeError``, as ``renamex_np`` does across
volumes; the copy path then runs for real on the test's own volume. Crashes
are simulated with ``Crash`` at each phase, and recovery must finish or roll
back from whatever was left on disk.
"""

from __future__ import annotations

import errno
import os
import plistlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from file_librarian import fsops, pipeline
from file_librarian.config import Config
from file_librarian.db.store import OperationRow, Store
from file_librarian.executor import recover
from file_librarian.extractors.origin import WHERE_FROMS, read_xattr
from file_librarian.finder_tags import read_tags
from file_librarian.fsops import CrossVolumeError, copy_verified, resume_copy
from file_librarian.models import CopyPhase, FileStatus, OperationStatus
from file_librarian.providers.fake import FakeProvider
from file_librarian.undo import undo_run
from tests._helpers import OLD, Crash, answer, make_old, put, set_tags, write_xattr

pytestmark = pytest.mark.integration


@pytest.fixture
def cross_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every move by a run or an undo behaves as if it crossed volumes."""

    def exdev(src: Path, dst: Path) -> None:
        raise CrossVolumeError(errno.EXDEV, "cross-device link", str(dst))

    monkeypatch.setattr("file_librarian.executor.move_no_replace", exdev)
    monkeypatch.setattr("file_librarian.undo.move_no_replace", exdev)


def finance(downloads: Path) -> Path:
    return downloads / "Organized" / "Finance"


def run(cfg: Config) -> pipeline.Outcome:
    return pipeline.run(cfg, FakeProvider(default=answer()), simulate=False)


def only_op(cfg: Config) -> tuple[OperationRow, FileStatus, int]:
    with Store.open(cfg.database) as store:
        [op] = store.operations()
        row = store.file_by_id(op.file_id)
    return op, row.status, row.attempts


def leftovers(*folders: Path) -> list[str]:
    return sorted(p.name for f in folders if f.exists() for p in f.iterdir() if ".flib-" in p.name)


def crash_in(monkeypatch: pytest.MonkeyPatch, name: str, *, after: bool = False) -> None:
    """Make ``fsops.<name>`` crash, before doing anything or right after doing its work."""
    real: Callable[..., Any] = getattr(fsops, name)

    def crash(*args: Any, **kwargs: Any) -> Any:
        if after:
            real(*args, **kwargs)
        raise Crash

    monkeypatch.setattr(fsops, name, crash)


# --- the move -------------------------------------------------------------------------------


@pytest.mark.usefixtures("cross_volume")
def test_a_file_is_copied_verified_and_the_source_removed(downloads: Path, cfg: Config) -> None:
    source = put(downloads, "a.pdf", b"mine")
    source.chmod(0o640)
    mtime = os.lstat(source).st_mtime_ns
    origin = plistlib.dumps(["https://example.com/a.pdf"], fmt=plistlib.FMT_BINARY)
    write_xattr(source, WHERE_FROMS, origin)
    set_tags(source, ["Mine"])

    outcome = run(cfg)

    assert outcome.report is not None
    [item] = outcome.report.items
    moved = finance(downloads) / "a.pdf"
    assert (item.status, item.destination, item.warning) == (FileStatus.ORGANIZED, moved, None)
    assert not source.exists() and moved.read_bytes() == b"mine"
    st = os.lstat(moved)
    assert (st.st_mode & 0o777, st.st_mtime_ns) == (0o640, mtime)
    assert read_xattr(moved, WHERE_FROMS) == origin
    assert read_tags(moved) == ["Mine", "Finance"]
    op, status, _ = only_op(cfg)
    assert (op.status, op.copy_phase, op.dst_path) == (OperationStatus.DONE, None, moved)
    assert status is FileStatus.ORGANIZED
    assert leftovers(downloads, finance(downloads)) == []


@pytest.mark.usefixtures("cross_volume")
def test_a_folder_unit_is_copied_whole(downloads: Path, cfg: Config) -> None:
    app = downloads / "Tool.app"
    put(downloads, "Tool.app/Contents/Info.plist", b"<plist/>")
    put(downloads, "Tool.app/Contents/MacOS/tool", b"\x7fELF")
    for name, target in (("Current", "MacOS"), ("Tool", "MacOS/tool")):
        link = app / "Contents" / name
        link.symlink_to(target)
        os.utime(link, (OLD, OLD), follow_symlinks=False)
    for folder in ("Tool.app/Contents/MacOS", "Tool.app/Contents", "Tool.app"):
        make_old(downloads / folder)

    outcome = run(cfg)

    assert outcome.report is not None and outcome.report.moved == 1
    moved = finance(downloads) / "Tool.app"
    assert not app.exists()
    assert (moved / "Contents" / "MacOS" / "tool").read_bytes() == b"\x7fELF"
    assert (moved / "Contents" / "Current").readlink() == Path("MacOS")
    op, status, _ = only_op(cfg)
    assert (op.status, status) == (OperationStatus.DONE, FileStatus.ORGANIZED)
    assert leftovers(downloads, finance(downloads)) == []


@pytest.mark.usefixtures("cross_volume")
def test_a_taken_name_gets_the_next_counter(downloads: Path, cfg: Config) -> None:
    put(downloads, "a.pdf", b"mine")
    other = put(downloads, "Organized/Finance/a.pdf", b"theirs")
    run(cfg)
    assert other.read_bytes() == b"theirs"
    assert (finance(downloads) / "a-2.pdf").read_bytes() == b"mine"
    op, _, _ = only_op(cfg)
    assert op.dst_path.name == "a-2.pdf"


@pytest.mark.usefixtures("cross_volume")
@pytest.mark.parametrize("code", [errno.ENOSPC, errno.EDQUOT])
def test_disk_full_cleans_up_keeps_the_source_and_spends_no_attempt(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    source = put(downloads, "a.pdf", b"mine")

    def partial_copy(src: Path, dst: Path) -> None:
        dst.write_bytes(b"mi")
        raise OSError(code, os.strerror(code), str(dst))

    with monkeypatch.context() as patch:
        patch.setattr(fsops, "_copyfile", partial_copy)
        outcome = run(cfg)

    assert outcome.report is not None
    [item] = outcome.report.items
    assert item.status is FileStatus.FAILED and "not enough space" in item.note
    assert source.read_bytes() == b"mine"
    assert leftovers(downloads, finance(downloads)) == []
    op, status, attempts = only_op(cfg)
    assert (op.status, status, attempts) == (OperationStatus.FAILED, FileStatus.FAILED, 0)

    run(cfg)  # space was freed: the next run moves it
    assert (finance(downloads) / "a.pdf").read_bytes() == b"mine" and not source.exists()


@pytest.mark.usefixtures("cross_volume")
def test_a_copy_that_does_not_match_is_discarded(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = put(downloads, "a.pdf", b"mine")

    def bad_copy(src: Path, dst: Path) -> None:
        dst.write_bytes(b"MINE")

    monkeypatch.setattr(fsops, "_copyfile", bad_copy)
    outcome = run(cfg)
    assert outcome.report is not None
    [item] = outcome.report.items
    assert item.status is FileStatus.FAILED and "does not match" in item.note
    assert source.read_bytes() == b"mine"
    assert leftovers(downloads, finance(downloads)) == []
    _, _, attempts = only_op(cfg)
    assert attempts == 1


@pytest.mark.usefixtures("cross_volume")
def test_a_source_changed_while_copying_is_kept(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = put(downloads, "a.pdf", b"mine")
    real = fsops._copyfile

    def copy_then_edit(src: Path, dst: Path) -> None:
        real(src, dst)
        src.write_bytes(b"mine, edited")

    monkeypatch.setattr(fsops, "_copyfile", copy_then_edit)
    outcome = run(cfg)
    assert outcome.report is not None
    assert "changed while it was copied" in outcome.report.items[0].note
    assert source.read_bytes() == b"mine, edited"
    assert leftovers(downloads, finance(downloads)) == []


@pytest.mark.usefixtures("cross_volume")
def test_a_unit_whose_copy_differs_is_discarded(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(downloads, "Project/a.txt", b"one")
    make_old(downloads / "Project")
    real = fsops._copyfile

    def copy_and_corrupt(src: Path, dst: Path) -> None:
        real(src, dst)
        (dst / "a.txt").write_bytes(b"two")

    monkeypatch.setattr(fsops, "_copyfile", copy_and_corrupt)
    outcome = run(cfg)
    assert outcome.report is not None
    assert "does not match" in outcome.report.items[0].note
    assert (downloads / "Project" / "a.txt").read_bytes() == b"one"
    assert leftovers(downloads, finance(downloads)) == []


@pytest.mark.usefixtures("cross_volume")
def test_a_source_that_cannot_be_removed_is_a_warning(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = put(downloads, "a.pdf", b"mine")
    real = fsops.move_no_replace

    def no_trash(src: Path, dst: Path) -> None:
        if dst.name.endswith(".delete"):
            raise PermissionError(errno.EPERM, "Operation not permitted", str(src))
        real(src, dst)

    monkeypatch.setattr(fsops, "move_no_replace", no_trash)
    outcome = run(cfg)
    assert outcome.report is not None
    [item] = outcome.report.items
    assert item.status is FileStatus.ORGANIZED and item.warning is not None
    assert "could not be removed" in item.warning
    assert source.read_bytes() == b"mine"
    assert (finance(downloads) / "a.pdf").read_bytes() == b"mine"
    op, _, _ = only_op(cfg)
    assert op.status is OperationStatus.DONE


def test_a_stale_staging_folder_is_never_reused(tmp_path: Path) -> None:
    source = put(tmp_path, "a.pdf", b"mine")
    stage = tmp_path / ".flib-1.copy"
    (stage / "item").parent.mkdir()
    (stage / "item").write_bytes(b"someone's")
    with pytest.raises(FileExistsError):
        copy_verified(source, stage, "0" * 64)
    assert (stage / "item").read_bytes() == b"someone's"


# --- crash recovery of a move ---------------------------------------------------------------


@pytest.mark.usefixtures("cross_volume")
@pytest.mark.parametrize(
    ("where", "after", "phase", "finished"),
    [
        ("_copyfile", True, CopyPhase.COPYING, False),  # copied, not yet verified
        ("_verify", True, CopyPhase.COPYING, False),  # verified, not yet journaled
        ("_place", False, CopyPhase.COPIED, True),  # verified, not yet renamed
        ("_place", True, CopyPhase.COPIED, True),  # renamed, not yet journaled
        ("_discard", False, CopyPhase.PLACED, True),  # in place, source untouched
        ("remove_tree", False, CopyPhase.PLACED, True),  # source moved aside, not deleted
    ],
)
def test_recovery_after_a_crash_in_each_phase(
    downloads: Path,
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
    where: str,
    after: bool,
    phase: CopyPhase,
    finished: bool,
) -> None:
    source = put(downloads, "a.pdf", b"mine")
    if where == "remove_tree":
        real = fsops.remove_tree

        def crash_on_trash(path: Path) -> None:
            if path.name.endswith(".delete"):
                raise Crash
            real(path)

        monkeypatch.setattr(fsops, "remove_tree", crash_on_trash)
    else:
        crash_in(monkeypatch, where, after=after)
    with pytest.raises(Crash):
        run(cfg)
    monkeypatch.undo()

    op, _, _ = only_op(cfg)
    assert (op.status, op.copy_phase) == (OperationStatus.PLANNED, phase)
    with Store.open(cfg.database) as store:
        assert recover(store, cfg) == 1
    op, status, attempts = only_op(cfg)
    moved = finance(downloads) / "a.pdf"
    if finished:
        assert (op.status, op.copy_phase, status) == (
            OperationStatus.DONE,
            None,
            FileStatus.ORGANIZED,
        )
        assert moved.read_bytes() == b"mine" and not source.exists()
    else:
        assert (op.status, status, attempts) == (OperationStatus.FAILED, FileStatus.FAILED, 1)
        assert source.read_bytes() == b"mine" and not moved.exists()
    assert leftovers(downloads, finance(downloads)) == []


@pytest.mark.usefixtures("cross_volume")
def test_recovery_of_a_unit_cut_off_while_deleting_the_source(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(downloads, "Project/a.txt", b"one")
    put(downloads, "Project/b.txt", b"two")
    make_old(downloads / "Project")
    real = fsops.remove_tree

    def half_delete(path: Path) -> None:
        if path.name.endswith(".delete"):
            (path / "a.txt").unlink()
            raise Crash
        real(path)

    monkeypatch.setattr(fsops, "remove_tree", half_delete)
    with pytest.raises(Crash):
        run(cfg)
    monkeypatch.undo()
    assert leftovers(downloads) == [".flib-1.delete"]
    with Store.open(cfg.database) as store:
        recover(store, cfg)
    moved = finance(downloads) / "Project"
    assert sorted(p.name for p in moved.iterdir()) == ["a.txt", "b.txt"]
    assert not (downloads / "Project").exists()
    assert leftovers(downloads, finance(downloads)) == []


@pytest.mark.usefixtures("cross_volume")
def test_recovery_puts_the_source_back_if_the_copy_vanished(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = put(downloads, "a.pdf", b"mine")
    real = fsops.remove_tree

    def crash_on_trash(path: Path) -> None:
        if path.name.endswith(".delete"):
            raise Crash
        real(path)

    monkeypatch.setattr(fsops, "remove_tree", crash_on_trash)
    with pytest.raises(Crash):
        run(cfg)
    monkeypatch.undo()
    (finance(downloads) / "a.pdf").unlink()  # gone before the next run
    with Store.open(cfg.database) as store:
        recover(store, cfg)
    assert source.read_bytes() == b"mine"
    op, status, _ = only_op(cfg)
    assert (op.status, status) == (OperationStatus.FAILED, FileStatus.FAILED)
    assert leftovers(downloads, finance(downloads)) == []


@pytest.mark.usefixtures("cross_volume")
def test_recovery_rolls_back_when_the_name_was_taken_meanwhile(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = put(downloads, "a.pdf", b"mine")
    crash_in(monkeypatch, "_place")
    with pytest.raises(Crash):
        run(cfg)
    monkeypatch.undo()
    other = put(downloads, "Organized/Finance/a.pdf", b"theirs")
    with Store.open(cfg.database) as store:
        recover(store, cfg)
    assert source.read_bytes() == b"mine" and other.read_bytes() == b"theirs"
    op, _, _ = only_op(cfg)
    assert op.status is OperationStatus.FAILED
    assert leftovers(downloads, finance(downloads)) == []


@pytest.mark.usefixtures("cross_volume")
@pytest.mark.parametrize("verified", [True, False])
def test_recovery_never_deletes_the_only_copy(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch, verified: bool
) -> None:
    source = put(downloads, "a.pdf", b"mine")
    crash_in(monkeypatch, "_copyfile", after=True)
    with pytest.raises(Crash):
        run(cfg)
    monkeypatch.undo()
    staged = finance(downloads) / ".flib-1.copy" / "item"
    if not verified:
        staged.write_bytes(b"mi")  # a copy cut off half-way
    source.unlink()  # and the original deleted by hand before the next run
    with Store.open(cfg.database) as store:
        recover(store, cfg)
    op, _, _ = only_op(cfg)
    if verified:
        assert op.status is OperationStatus.DONE
        assert (finance(downloads) / "a.pdf").read_bytes() == b"mine"
    else:
        assert op.status is OperationStatus.FAILED
        assert staged.read_bytes() == b"mi"  # left for the user, not deleted


# --- undo -----------------------------------------------------------------------------------


@pytest.mark.usefixtures("cross_volume")
def test_undo_copies_back_across_volumes(downloads: Path, cfg: Config) -> None:
    source = put(downloads, "a.pdf", b"mine")
    write_xattr(source, WHERE_FROMS, b"origin")
    run(cfg)
    with Store.open(cfg.database) as store:
        report = undo_run(store, config=cfg)
    moved = finance(downloads) / "a.pdf"
    assert report.restored == [(moved, source)] and report.skipped == []
    assert source.read_bytes() == b"mine" and not moved.exists()
    assert read_xattr(source, WHERE_FROMS) == b"origin"
    assert read_tags(source) == []  # the category tag flib added is gone again
    op, status, _ = only_op(cfg)
    assert (op.status, op.copy_phase, status) == (OperationStatus.UNDONE, None, FileStatus.SKIPPED)
    assert leftovers(downloads, finance(downloads)) == []


@pytest.mark.usefixtures("cross_volume")
def test_undo_that_cannot_copy_back_is_skipped(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    run_source = put(downloads, "a.pdf", b"mine")
    run(cfg)

    def disk_full(src: Path, dst: Path) -> None:
        raise OSError(errno.ENOSPC, "No space left on device", str(dst))

    monkeypatch.setattr(fsops, "_copyfile", disk_full)
    with Store.open(cfg.database) as store:
        report = undo_run(store, config=cfg)
    assert report.restored == [] and "could not move back" in report.skipped[0][1]
    assert not run_source.exists() and (finance(downloads) / "a.pdf").exists()
    op, _, _ = only_op(cfg)
    assert (op.status, op.copy_phase) == (OperationStatus.DONE, None)
    assert leftovers(downloads, finance(downloads)) == []


@pytest.mark.usefixtures("cross_volume")
@pytest.mark.parametrize(("where", "restored"), [("_copyfile", False), ("_discard", True)])
def test_recovery_of_an_undo_cut_off_mid_copy(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch, where: str, restored: bool
) -> None:
    source = put(downloads, "a.pdf", b"mine")
    run(cfg)
    moved = finance(downloads) / "a.pdf"
    with monkeypatch.context() as patch:
        real: Callable[..., Any] = getattr(fsops, where)

        def crash(*args: Any) -> None:
            real(*args) if where == "_copyfile" else None
            raise Crash

        patch.setattr(fsops, where, crash)
        with Store.open(cfg.database) as store, pytest.raises(Crash):
            undo_run(store, config=cfg)
    op, _, _ = only_op(cfg)
    assert op.status is OperationStatus.DONE and op.copy_phase is not None

    with Store.open(cfg.database) as store:
        assert recover(store, cfg) == 1
    op, status, _ = only_op(cfg)
    assert op.copy_phase is None
    if restored:
        assert (op.status, status) == (OperationStatus.UNDONE, FileStatus.SKIPPED)
        assert source.read_bytes() == b"mine" and not moved.exists()
    else:
        assert (op.status, status) == (OperationStatus.DONE, FileStatus.ORGANIZED)
        assert moved.read_bytes() == b"mine" and not source.exists()
        with Store.open(cfg.database) as store:
            assert undo_run(store, config=cfg).restored == [(moved, source)]
    assert leftovers(downloads, finance(downloads)) == []


@pytest.mark.usefixtures("cross_volume")
def test_undo_skips_when_the_original_path_is_taken_meanwhile(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = put(downloads, "a.pdf", b"mine")
    run(cfg)
    real = fsops._copyfile

    def copy_while_someone_takes_the_name(src: Path, dst: Path) -> None:
        real(src, dst)
        source.write_bytes(b"new download")

    monkeypatch.setattr(fsops, "_copyfile", copy_while_someone_takes_the_name)
    with Store.open(cfg.database) as store:
        report = undo_run(store, config=cfg)
    assert report.restored == [] and "original path is taken" in report.skipped[0][1]
    assert source.read_bytes() == b"new download"
    assert (finance(downloads) / "a.pdf").read_bytes() == b"mine"
    op, _, _ = only_op(cfg)
    assert (op.status, op.copy_phase) == (OperationStatus.DONE, None)
    assert leftovers(downloads, finance(downloads)) == []


@pytest.mark.usefixtures("cross_volume")
def test_undo_warns_when_the_library_copy_cannot_be_removed(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = put(downloads, "a.pdf", b"mine")
    run(cfg)
    real = fsops.move_no_replace

    def no_trash(src: Path, dst: Path) -> None:
        if dst.name.endswith(".delete"):
            raise PermissionError(errno.EPERM, "Operation not permitted", str(src))
        real(src, dst)

    monkeypatch.setattr(fsops, "move_no_replace", no_trash)
    with Store.open(cfg.database) as store:
        report = undo_run(store, config=cfg)
    assert report.restored == [(finance(downloads) / "a.pdf", source)]
    assert [w for _, w in report.warnings if "could not be removed" in w]
    assert source.read_bytes() == b"mine"


# --- fsops on its own -------------------------------------------------------------------------


def test_a_failed_copyfile_raises_with_its_errno(tmp_path: Path) -> None:
    source = put(tmp_path, "a.pdf", b"mine")
    with pytest.raises(OSError) as caught:
        fsops._copyfile(source, tmp_path / "missing" / "copy")
    assert caught.value.errno == errno.ENOENT


def test_sync_falls_back_to_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = put(tmp_path, "a.pdf", b"mine")
    synced: list[int] = []

    def unsupported(fd: int, cmd: int, *args: Any) -> int:
        raise OSError(errno.ENOTSUP, "not supported")

    monkeypatch.setattr("fcntl.fcntl", unsupported)
    monkeypatch.setattr("os.fsync", synced.append)
    fsops._sync(path)
    assert len(synced) == 1


# --- resume_copy on its own ---------------------------------------------------------------------


def test_resume_without_a_copy_or_destination_rolls_back(tmp_path: Path) -> None:
    source = put(tmp_path, "a.pdf", b"mine")
    phases: list[CopyPhase] = []
    result = resume_copy(
        source,
        tmp_path / "lib" / "a.pdf",
        key=7,
        phase=CopyPhase.COPIED,
        expected="0" * 64,
        journal=lambda phase, _dst: phases.append(phase),
    )
    assert result is None and phases == [] and source.read_bytes() == b"mine"


def test_resume_keeps_a_verified_copy_it_cannot_place_once_the_source_is_gone(
    tmp_path: Path,
) -> None:
    staged = put(tmp_path, "lib/.flib-7.copy/item", b"mine")
    taken = put(tmp_path, "lib/a.pdf", b"theirs")
    result = resume_copy(
        tmp_path / "a.pdf",
        taken,
        key=7,
        phase=CopyPhase.COPIED,
        expected="0" * 64,
        journal=lambda _phase, _dst: None,
    )
    assert result is None
    assert staged.read_bytes() == b"mine" and taken.read_bytes() == b"theirs"


def test_resume_with_nothing_left_anywhere_changes_nothing(tmp_path: Path) -> None:
    staged = put(tmp_path, "lib/.flib-7.copy/other", b"?")
    result = resume_copy(
        tmp_path / "a.pdf",
        tmp_path / "lib" / "a.pdf",
        key=7,
        phase=CopyPhase.PLACED,
        expected="0" * 64,
        journal=lambda _phase, _dst: None,
    )
    assert result is None and staged.exists()

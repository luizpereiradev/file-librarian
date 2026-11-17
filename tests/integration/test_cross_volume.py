"""Moves to another volume: copy, verify, then delete the source (RF-33).

CI cannot mount a second volume, so ``cross_volume`` makes the executor's and
undo's rename fail with ``CrossVolumeError``, as ``renamex_np`` does across
volumes; the copy path then runs for real on the test's own volume. Crashes
are simulated with ``Crash`` at each phase, and recovery must finish or roll
back from whatever was left on disk.
"""

from __future__ import annotations

import errno
import logging
import os
import plistlib
import stat
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
from file_librarian.fsops import (
    CopyMismatchError,
    CrossVolumeError,
    UnavailableError,
    copy_verified,
    new_copy_key,
    remove_tree,
    resume_copy,
)
from file_librarian.hashing import content_digest, file_sha256, unit_manifest
from file_librarian.models import CopyPhase, FileStatus, OperationStatus
from file_librarian.providers.fake import FakeProvider
from file_librarian.undo import undo_run
from tests._helpers import OLD, Crash, answer, make_old, put, row_for, set_tags, write_xattr

pytestmark = pytest.mark.integration


@pytest.fixture
def cross_volume(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every move by a run or an undo behaves as if it crossed volumes.

    Copy keys lose their random suffix, so tests can name the staging paths.
    """
    monkeypatch.setattr("file_librarian.executor.new_copy_key", lambda op_id: f"{op_id}-k")
    monkeypatch.setattr("file_librarian.undo.new_copy_key", lambda op_id: f"{op_id}-k")

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
    stage = tmp_path / ".flib-1-k.copy"
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
    assert leftovers(downloads) == [".flib-1-k.delete"]
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
    staged = finance(downloads) / ".flib-1-k.copy" / "item"
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
    (tmp_path / "lib").mkdir()
    phases: list[CopyPhase] = []
    result = resume_copy(
        source,
        tmp_path / "lib" / "a.pdf",
        key="7-k",
        phase=CopyPhase.COPIED,
        expected="0" * 64,
        journal=lambda phase, _dst: phases.append(phase),
    )
    assert result is None and phases == [] and source.read_bytes() == b"mine"


def test_resume_keeps_a_verified_copy_it_cannot_place_once_the_source_is_gone(
    tmp_path: Path,
) -> None:
    staged = put(tmp_path, "lib/.flib-7-k.copy/item", b"mine")
    taken = put(tmp_path, "lib/a.pdf", b"theirs")
    result = resume_copy(
        tmp_path / "a.pdf",
        taken,
        key="7-k",
        phase=CopyPhase.COPIED,
        expected="0" * 64,
        journal=lambda _phase, _dst: None,
    )
    assert result is None
    assert staged.read_bytes() == b"mine" and taken.read_bytes() == b"theirs"


def test_resume_with_nothing_left_anywhere_changes_nothing(tmp_path: Path) -> None:
    staged = put(tmp_path, "lib/.flib-7-k.copy/other", b"?")
    result = resume_copy(
        tmp_path / "a.pdf",
        tmp_path / "lib" / "a.pdf",
        key="7-k",
        phase=CopyPhase.PLACED,
        expected="0" * 64,
        journal=lambda _phase, _dst: None,
    )
    assert result is None and staged.exists()


# --- unreachable folders, locked entries, unsupported volumes ---------------------------------


def crash_while_removing_trash(monkeypatch: pytest.MonkeyPatch) -> None:
    real = fsops.remove_tree

    def crash(path: Path) -> None:
        if path.name.endswith(".delete"):
            raise Crash
        real(path)

    monkeypatch.setattr(fsops, "remove_tree", crash)


def tree(folder: Path) -> list[str]:
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*"))


@pytest.mark.usefixtures("cross_volume")
@pytest.mark.parametrize("where", ["_place", "remove_tree"])
def test_recovery_waits_while_the_destination_is_unavailable(
    downloads: Path,
    cfg: Config,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    where: str,
) -> None:
    put(downloads, "Project/a.txt", b"one")
    put(downloads, "Project/b.txt", b"two")
    make_old(downloads / "Project")
    if where == "_place":
        crash_in(monkeypatch, "_place")  # verified copy staged in the library
    else:
        crash_while_removing_trash(monkeypatch)  # source moved aside, copy in place
    with pytest.raises(Crash):
        run(cfg)
    monkeypatch.undo()
    op, _, _ = only_op(cfg)
    assert op.copy_volumes == (downloads.stat().st_dev, finance(downloads).stat().st_dev)
    library = downloads / "Organized"
    unmounted = downloads.parent / "unmounted"
    library.rename(unmounted)
    # As if the library had been on a volume of its own that is now unmounted.
    with Store.open(cfg.database) as store:
        store.conn.execute("UPDATE operations SET copy_dst_dev = -1")
    before = tree(downloads)

    with Store.open(cfg.database) as store, caplog.at_level(logging.WARNING):
        assert recover(store, cfg) == 0
    assert "not available" in caplog.text
    assert tree(downloads) == before  # nothing put back, nothing deleted
    op, _, _ = only_op(cfg)
    assert op.status is OperationStatus.PLANNED and op.copy_phase is not None

    unmounted.rename(library)  # mounted again
    with Store.open(cfg.database) as store:
        assert recover(store, cfg) == 1
    op, status, _ = only_op(cfg)
    assert (op.status, status) == (OperationStatus.DONE, FileStatus.ORGANIZED)
    assert sorted(p.name for p in (finance(downloads) / "Project").iterdir()) == ["a.txt", "b.txt"]
    assert not (downloads / "Project").exists()
    assert leftovers(downloads, finance(downloads)) == []


@pytest.mark.usefixtures("cross_volume")
def test_an_entry_that_cannot_be_recovered_does_not_stop_the_run(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    put(downloads, "a.pdf", b"mine")
    crash_in(monkeypatch, "_place")
    with pytest.raises(Crash):
        run(cfg)
    monkeypatch.undo()

    def broken(*_args: Any, **_kwargs: Any) -> None:
        raise PermissionError(errno.EACCES, "Permission denied")

    monkeypatch.setattr("file_librarian.executor.resume_copy", broken)
    with caplog.at_level(logging.WARNING):
        outcome = run(cfg)
    assert outcome.recovered == 0 and "could not recover" in caplog.text
    op, _, _ = only_op(cfg)
    assert op.status is OperationStatus.PLANNED


@pytest.mark.usefixtures("cross_volume")
@pytest.mark.parametrize("which", ["Project", "Project/b.txt"])
def test_a_locked_item_is_not_moved_across_volumes(
    downloads: Path, cfg: Config, which: str
) -> None:
    put(downloads, "Project/b.txt", b"two")
    make_old(downloads / "Project")
    os.chflags(downloads / which, stat.UF_IMMUTABLE)  # Finder's "Locked"
    try:
        outcome = run(cfg)
        assert outcome.report is not None
        [item] = outcome.report.items
        assert item.status is FileStatus.FAILED and "locked" in item.note
        assert (downloads / "Project" / "b.txt").read_bytes() == b"two"
        assert not (finance(downloads) / "Project").exists()
        assert leftovers(downloads, finance(downloads)) == []
    finally:
        os.chflags(downloads / which, 0)


@pytest.mark.usefixtures("cross_volume")
def test_read_only_folders_move_and_come_back(downloads: Path, cfg: Config) -> None:
    put(downloads, "Project/sub/a.txt", b"one")
    put(downloads, "Project/b.txt", b"two")
    link = downloads / "Project" / "sub" / "link"  # COPYFILE_RECURSIVE fails on this one
    link.symlink_to("a.txt")
    os.utime(link, (OLD, OLD), follow_symlinks=False)
    (downloads / "Project" / "sub").chmod(0o555)
    for folder in ("Project/sub", "Project"):
        make_old(downloads / folder)

    outcome = run(cfg)

    assert outcome.report is not None and outcome.report.moved == 1
    assert outcome.report.items[0].warning is None
    moved = finance(downloads) / "Project"
    assert not (downloads / "Project").exists()
    assert (moved / "sub" / "a.txt").read_bytes() == b"one"
    assert stat.S_IMODE(os.lstat(moved / "sub").st_mode) == 0o555
    assert leftovers(downloads, finance(downloads)) == []
    with Store.open(cfg.database) as store:
        report = undo_run(store, config=cfg)
    assert report.restored == [(moved, downloads / "Project")] and report.warnings == []
    assert not moved.exists() and leftovers(downloads, finance(downloads)) == []
    remove_tree(downloads / "Project")  # made writable, so the temporary folder can be cleaned


@pytest.mark.usefixtures("cross_volume")
def test_recovery_cleans_up_a_read_only_staged_copy(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(downloads, "Project/sub/a.txt", b"one")
    (downloads / "Project" / "sub").chmod(0o555)
    for folder in ("Project/sub", "Project"):
        make_old(downloads / folder)
    crash_in(monkeypatch, "_copyfile", after=True)
    with pytest.raises(Crash):
        run(cfg)
    monkeypatch.undo()
    with Store.open(cfg.database) as store:
        assert recover(store, cfg) == 1
    assert leftovers(downloads, finance(downloads)) == []
    assert (downloads / "Project" / "sub" / "a.txt").read_bytes() == b"one"
    (downloads / "Project" / "sub").chmod(0o755)


@pytest.mark.usefixtures("cross_volume")
@pytest.mark.parametrize(
    ("code", "message"),
    [
        (errno.ENOTSUP, "cannot rename without replacing"),
        (errno.EINVAL, "cannot rename without replacing"),
        (errno.EACCES, "move failed"),
    ],
)
def test_a_volume_without_rename_excl_is_refused_before_copying(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch, code: int, message: str
) -> None:
    source = put(downloads, "a.pdf", b"mine")
    real = fsops.move_no_replace
    copies: list[Path] = []

    def no_excl(src: Path, dst: Path) -> None:
        if src.name.startswith(".flib-probe-"):  # what exFAT answers to RENAME_EXCL
            raise OSError(code, os.strerror(code), str(src))
        real(src, dst)

    monkeypatch.setattr(fsops, "move_no_replace", no_excl)
    monkeypatch.setattr(fsops, "_copyfile", lambda _src, dst: copies.append(dst))
    outcome = run(cfg)
    assert outcome.report is not None
    [item] = outcome.report.items
    assert item.status is FileStatus.FAILED and message in item.note
    assert copies == [] and source.read_bytes() == b"mine"
    assert leftovers(downloads, finance(downloads)) == []


@pytest.mark.usefixtures("cross_volume")
def test_a_cleanup_error_does_not_hide_a_full_disk(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    source = put(downloads, "a.pdf", b"mine")

    def full(_src: Path, dst: Path) -> None:
        dst.write_bytes(b"mi")
        raise OSError(errno.ENOSPC, "No space left on device", str(dst))

    def stuck(path: Path) -> None:
        raise PermissionError(errno.EPERM, "Operation not permitted", str(path))

    monkeypatch.setattr(fsops, "_copyfile", full)
    monkeypatch.setattr(fsops, "remove_tree", stuck)
    with caplog.at_level(logging.WARNING):
        outcome = run(cfg)
    assert outcome.report is not None
    assert "not enough space" in outcome.report.items[0].note
    assert "could not remove" in caplog.text
    assert source.read_bytes() == b"mine"
    _, _, attempts = only_op(cfg)
    assert attempts == 0


# --- the source after the copy ----------------------------------------------------------------


@pytest.mark.usefixtures("cross_volume")
def test_a_folder_changed_after_copying_is_kept_with_a_warning(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(downloads, "Project/a.txt", b"one")
    make_old(downloads / "Project")
    real = fsops._place

    def place_then_edit(*args: Any) -> Path:
        final: Path = real(*args)
        put(downloads, "Project/new.txt", b"new")
        return final

    monkeypatch.setattr(fsops, "_place", place_then_edit)
    outcome = run(cfg)
    assert outcome.report is not None
    [item] = outcome.report.items
    assert item.status is FileStatus.ORGANIZED and item.warning is not None
    assert "changed after it was copied" in item.warning
    assert (downloads / "Project" / "new.txt").exists()
    assert (finance(downloads) / "Project" / "a.txt").exists()
    assert leftovers(downloads, finance(downloads)) == []


@pytest.mark.usefixtures("cross_volume")
def test_a_file_replaced_after_copying_is_kept(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = put(downloads, "a.pdf", b"mine")
    real = fsops._place

    def place_then_replace(*args: Any) -> Path:
        final: Path = real(*args)
        source.unlink()
        put(downloads, "a.pdf", b"mine")  # same name, size and time; another file
        return final

    monkeypatch.setattr(fsops, "_place", place_then_replace)
    outcome = run(cfg)
    assert outcome.report is not None
    warning = outcome.report.items[0].warning
    assert warning is not None and "changed after it was copied" in warning
    assert source.read_bytes() == b"mine"


@pytest.mark.usefixtures("cross_volume")
def test_a_source_swapped_while_moving_it_aside_is_put_back(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = put(downloads, "a.pdf", b"mine")
    real = fsops.move_no_replace

    def swap(src: Path, dst: Path) -> None:
        real(src, dst)
        if dst.name.endswith(".delete"):
            dst.unlink()
            dst.write_bytes(b"someone else's")

    monkeypatch.setattr(fsops, "move_no_replace", swap)
    outcome = run(cfg)
    assert outcome.report is not None
    warning = outcome.report.items[0].warning
    assert warning is not None and "while it was being removed" in warning
    assert source.read_bytes() == b"someone else's"
    assert leftovers(downloads, finance(downloads)) == []


@pytest.mark.usefixtures("cross_volume")
def test_a_flush_error_after_placing_is_only_a_warning(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = put(downloads, "a.pdf", b"mine")
    real = fsops._sync

    def flaky(path: Path) -> None:
        if path == finance(downloads):
            raise OSError(errno.EIO, "Input/output error", str(path))
        real(path)

    monkeypatch.setattr(fsops, "_sync", flaky)
    outcome = run(cfg)
    assert outcome.report is not None
    [item] = outcome.report.items
    assert item.status is FileStatus.ORGANIZED and item.warning is not None
    assert "could not flush" in item.warning
    assert (finance(downloads) / "a.pdf").read_bytes() == b"mine" and not source.exists()


@pytest.mark.usefixtures("cross_volume")
def test_recovery_keeps_a_source_edited_after_the_crash(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    source = put(downloads, "a.pdf", b"mine")
    crash_in(monkeypatch, "_discard")
    with pytest.raises(Crash):
        run(cfg)
    monkeypatch.undo()
    source.write_bytes(b"MINE")  # same size, edited before the next run
    with Store.open(cfg.database) as store, caplog.at_level(logging.WARNING):
        recover(store, cfg)
    assert source.read_bytes() == b"MINE"
    assert (finance(downloads) / "a.pdf").read_bytes() == b"mine"
    assert "changed after it was copied" in caplog.text
    op, _, _ = only_op(cfg)
    assert op.status is OperationStatus.DONE


# --- recovery checks what it finds -------------------------------------------------------------


@pytest.mark.usefixtures("cross_volume")
def test_recovery_does_not_take_someone_elses_file_for_the_copy(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = put(downloads, "a.pdf", b"mine")
    crash_in(monkeypatch, "_place", after=True)  # renamed into place, phase still "copied"
    with pytest.raises(Crash):
        run(cfg)
    monkeypatch.undo()
    moved = finance(downloads) / "a.pdf"
    moved.write_bytes(b"theirs")
    with Store.open(cfg.database) as store:
        recover(store, cfg)
    op, _, _ = only_op(cfg)
    assert op.status is OperationStatus.FAILED
    assert source.read_bytes() == b"mine" and moved.read_bytes() == b"theirs"


@pytest.mark.usefixtures("cross_volume")
def test_recovery_finds_a_finished_copy_when_later_phases_were_lost(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = put(downloads, "a.pdf", b"mine")
    crash_in(monkeypatch, "_discard")
    with pytest.raises(Crash):
        run(cfg)
    monkeypatch.undo()
    source.unlink()
    with Store.open(cfg.database) as store:
        store.conn.execute("UPDATE operations SET copy_phase = 'copying'")
        recover(store, cfg)
    op, status, _ = only_op(cfg)
    assert (op.status, status) == (OperationStatus.DONE, FileStatus.ORGANIZED)
    assert (finance(downloads) / "a.pdf").read_bytes() == b"mine"


@pytest.mark.usefixtures("cross_volume")
def test_a_unit_on_a_coarser_file_system_is_indexed_as_it_is_there(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(downloads, "Project/a.txt", b"one", mtime=OLD + 0.123456)
    make_old(downloads / "Project")
    real = fsops._copyfile

    def coarse(src: Path, dst: Path) -> None:
        real(src, dst)
        # Whole seconds there, as HFS+ keeps them; even seconds on the way back, as FAT.
        step = 1 if finance(downloads) in dst.parents else 2
        for path in [dst, *dst.rglob("*")]:
            seconds = int(os.lstat(path).st_mtime) // step * step
            os.utime(path, (seconds, seconds), follow_symlinks=False)

    monkeypatch.setattr(fsops, "_copyfile", coarse)
    run(cfg)
    moved = finance(downloads) / "Project"
    assert row_for(cfg, "Project").sha256 == unit_manifest(moved).digest
    with Store.open(cfg.database) as store:
        report = undo_run(store, config=cfg)
    assert report.restored == [(moved, downloads / "Project")]
    assert row_for(cfg, "Project").sha256 == unit_manifest(downloads / "Project").digest


# --- the pieces on their own --------------------------------------------------------------------


def test_a_unit_that_is_no_longer_what_was_indexed_is_not_copied(tmp_path: Path) -> None:
    put(tmp_path, "Project/a.txt")
    stage = tmp_path / ".flib-x.copy"
    with pytest.raises(CopyMismatchError):
        copy_verified(tmp_path / "Project", stage, "0" * 64)
    assert not stage.exists()


def test_content_digest_tells_symlink_targets_apart(tmp_path: Path) -> None:
    for name, target in (("a", "x"), ("b", "y")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "link").symlink_to(target)
    assert content_digest(tmp_path / "a") != content_digest(tmp_path / "b")


def test_copy_keys_never_repeat() -> None:
    key = new_copy_key(5)
    assert key.startswith("5-") and key != new_copy_key(5)


def test_resume_does_nothing_while_a_folder_is_unavailable(tmp_path: Path) -> None:
    source = put(tmp_path, "a.pdf", b"mine")
    with pytest.raises(UnavailableError):
        resume_copy(
            source,
            tmp_path / "unmounted" / "a.pdf",
            key="7-k",
            phase=CopyPhase.PLACED,
            expected="0" * 64,
            journal=lambda _phase, _dst: None,
        )
    assert source.read_bytes() == b"mine"


# --- second review: folders gone, foreign folders, unit metadata ------------------------------


@pytest.mark.usefixtures("cross_volume")
def test_a_category_folder_deleted_on_a_mounted_volume_is_resolved(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = put(downloads, "a.pdf", b"mine")
    crash_in(monkeypatch, "_copyfile", after=True)
    with pytest.raises(Crash):
        run(cfg)
    monkeypatch.undo()
    remove_tree(finance(downloads))  # deleted by hand, staged copy and all; the volume is there
    with Store.open(cfg.database) as store:
        assert recover(store, cfg) == 1
    op, status, _ = only_op(cfg)
    assert (op.status, status) == (OperationStatus.FAILED, FileStatus.FAILED)
    assert source.read_bytes() == b"mine"


def test_an_undo_whose_original_folder_is_gone_rolls_back(tmp_path: Path) -> None:
    library_item = put(tmp_path, "lib/Finance/a.pdf", b"mine")
    volume = tmp_path.stat().st_dev
    restored = resume_copy(
        library_item,
        tmp_path / "Downloads" / "a.pdf",  # the original folder no longer exists
        key="7-k",
        phase=CopyPhase.COPYING,
        expected=file_sha256(library_item),
        journal=lambda _phase, _dst: None,
        volumes=(volume, volume),
    )
    assert restored is None and library_item.read_bytes() == b"mine"


@pytest.mark.usefixtures("cross_volume")
def test_recovery_does_not_take_another_folder_for_the_copied_unit(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(downloads, "Project/a.txt", b"one")
    make_old(downloads / "Project")
    crash_in(monkeypatch, "_discard")  # copy in place, source still there
    with pytest.raises(Crash):
        run(cfg)
    monkeypatch.undo()
    moved = finance(downloads) / "Project"
    remove_tree(moved)
    put(finance(downloads), "Project/other.txt", b"someone else's")  # same name, not ours
    with Store.open(cfg.database) as store:
        recover(store, cfg)
    assert (downloads / "Project" / "a.txt").read_bytes() == b"one"
    assert (moved / "other.txt").read_bytes() == b"someone else's"
    op, _, _ = only_op(cfg)
    assert op.status is OperationStatus.FAILED


@pytest.mark.usefixtures("cross_volume")
def test_a_units_own_metadata_crosses_volumes(downloads: Path, cfg: Config) -> None:
    unit = downloads / "Project"
    put(downloads, "Project/a.txt", b"one")
    origin = plistlib.dumps(["https://example.com/p.zip"], fmt=plistlib.FMT_BINARY)
    write_xattr(unit, WHERE_FROMS, origin)
    set_tags(unit, ["Mine"])
    unit.chmod(0o750)
    make_old(unit)
    mtime = os.lstat(unit).st_mtime_ns

    run(cfg)

    moved = finance(downloads) / "Project"
    st = os.lstat(moved)
    assert (stat.S_IMODE(st.st_mode), st.st_mtime_ns) == (0o750, mtime)
    assert read_xattr(moved, WHERE_FROMS) == origin
    assert read_tags(moved) == ["Mine", "Finance"]


def test_remove_tree_never_follows_a_symlink_out(tmp_path: Path) -> None:
    outside = put(tmp_path, "outside/keep.txt", b"keep")
    put(tmp_path, "tree/sub/a.txt", b"a")
    (tmp_path / "tree" / "sub" / "out").symlink_to(outside.parent)
    (tmp_path / "tree" / "sub").chmod(0o500)
    remove_tree(tmp_path / "tree")
    assert not (tmp_path / "tree").exists() and outside.read_bytes() == b"keep"


def test_remove_tree_stops_if_a_folder_is_swapped_meanwhile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    outside = put(tmp_path, "outside/keep.txt", b"keep")
    put(tmp_path, "tree/sub/a.txt", b"a")
    real = fsops._empty_folder_at

    def swap(dir_fd: int, name: str, st: os.stat_result) -> None:
        if name == "sub":  # between its lstat and its open: now a symlink out
            (tmp_path / "tree" / "sub").rename(tmp_path / "moved-sub")
            (tmp_path / "tree" / "sub").symlink_to(outside.parent)
        real(dir_fd, name, st)

    monkeypatch.setattr(fsops, "_empty_folder_at", swap)
    with pytest.raises(OSError):
        remove_tree(tmp_path / "tree")
    assert outside.read_bytes() == b"keep"


@pytest.mark.usefixtures("cross_volume")
def test_recovery_without_a_source_does_not_take_another_folder_for_the_unit(
    downloads: Path, cfg: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    put(downloads, "Project/a.txt", b"one")
    make_old(downloads / "Project")
    crash_in(monkeypatch, "require_rename_excl")  # "copying" journaled, nothing copied yet
    with pytest.raises(Crash):
        run(cfg)
    monkeypatch.undo()
    remove_tree(downloads / "Project")  # the original deleted by hand
    foreign = put(finance(downloads), "Project/other.txt", b"someone else's")
    with Store.open(cfg.database) as store:
        recover(store, cfg)
    op, _, _ = only_op(cfg)
    assert op.status is OperationStatus.FAILED
    assert foreign.read_bytes() == b"someone else's"


def test_copy_phases_are_committed_with_fullfsync(downloads: Path, cfg: Config) -> None:
    put(downloads, "a.pdf", b"mine")
    run(cfg)
    statements: list[str] = []
    with Store.open(cfg.database) as store:
        [op] = store.operations()
        store.conn.set_trace_callback(statements.append)
        store.set_copy_phase(op.id, CopyPhase.COPYING, "1-k")
    assert statements[0] == "PRAGMA fullfsync = ON"
    assert statements[1].lstrip().startswith("UPDATE operations SET copy_phase")
    assert statements[-1] == "PRAGMA fullfsync = OFF"

"""Cross-volume moves onto real file systems, in disk images (RF-33). Opt-in.

Each test creates a small disk image in its temporary folder with
``hdiutil``, attaches it there (``-nobrowse``: Finder does not show it) and
detaches it afterwards, so only that image is ever written to; nothing
under ``/Volumes`` is touched. They run only with ``FLIB_TEST_VOLUMES=1``
(``FLIB_TEST_VOLUMES=1 uv run pytest -m volumes``), since creating images
takes seconds and needs ``hdiutil``.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest

from file_librarian import fsops, pipeline
from file_librarian.config import Config
from file_librarian.db.store import Store
from file_librarian.executor import recover
from file_librarian.fsops import remove_tree
from file_librarian.models import FileStatus, OperationStatus
from file_librarian.providers.fake import FakeProvider
from file_librarian.undo import undo_run
from tests._helpers import Crash, answer, fake_config, make_old, put

pytestmark = [
    pytest.mark.integration,
    pytest.mark.volumes,
    pytest.mark.skipif(
        os.environ.get("FLIB_TEST_VOLUMES") != "1" or shutil.which("hdiutil") is None,
        reason="disk image tests are opt-in: FLIB_TEST_VOLUMES=1 and hdiutil",
    ),
]

HDIUTIL = "hdiutil"


class Volume:
    def __init__(self, fs: str, image: Path, mountpoint: Path) -> None:
        self.fs = fs
        self.image = image
        self.mountpoint = mountpoint

    def attach(self) -> None:
        subprocess.run(  # noqa: S603 - hdiutil with fixed arguments
            [HDIUTIL, "attach", "-quiet", "-nobrowse", "-noautoopen", "-mountpoint",
             str(self.mountpoint), str(self.image)],
            check=True,
        )  # fmt: skip

    def detach(self) -> None:
        subprocess.run(  # noqa: S603 - hdiutil with fixed arguments
            [HDIUTIL, "detach", "-quiet", "-force", str(self.mountpoint)], check=True
        )


@pytest.fixture(params=["APFS", "Case-sensitive APFS", "HFS+", "ExFAT"])
def volume(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[Volume]:
    image = tmp_path / "volume.dmg"
    subprocess.run(  # noqa: S603 - hdiutil with fixed arguments
        [HDIUTIL, "create", "-quiet", "-size", "32m", "-fs", request.param, "-volname",
         "flibtest", str(image)],
        check=True,
    )  # fmt: skip
    mountpoint = tmp_path / "mnt"
    mountpoint.mkdir()
    disk = Volume(request.param, image, mountpoint)
    disk.attach()
    try:
        yield disk
    finally:
        if os.path.ismount(mountpoint):
            disk.detach()


def library_on(volume: Volume, downloads: Path) -> Config:
    return fake_config(downloads, output_root=str(volume.mountpoint / "Library"))


def leftovers(*folders: Path) -> list[str]:
    return sorted(p.name for f in folders for p in f.rglob(".flib-*"))


def add_items(downloads: Path) -> None:
    put(downloads, "a.pdf", b"mine")
    put(downloads, "Project/a.txt", b"one", mtime=1_700_000_000.123456)
    put(downloads, "Project/sub/b.txt", b"two")
    (downloads / "Project" / "sub" / "link").symlink_to("b.txt")
    os.utime(downloads / "Project" / "sub" / "link", (1e9, 1e9), follow_symlinks=False)
    (downloads / "Project" / "sub").chmod(0o555)
    for folder in ("Project/sub", "Project"):
        make_old(downloads / folder)


def test_moves_and_undo_onto_a_real_volume(volume: Volume, downloads: Path) -> None:
    add_items(downloads)
    config = library_on(volume, downloads)
    outcome = pipeline.run(config, FakeProvider(default=answer()), simulate=False)
    assert outcome.report is not None
    library = volume.mountpoint / "Library"
    try:
        if volume.fs == "ExFAT":
            # No RENAME_EXCL there: refused before anything is copied.
            assert all(i.status is FileStatus.FAILED for i in outcome.report.items)
            assert all("cannot rename without replacing" in i.note for i in outcome.report.items)
            assert (downloads / "a.pdf").read_bytes() == b"mine"
            assert (downloads / "Project" / "sub" / "b.txt").exists()
            assert not library.exists()  # refused before any folder was created there
            assert leftovers(downloads, volume.mountpoint) == []
            return
        assert outcome.report.moved == 2
        assert [i.warning for i in outcome.report.items] == [None, None]
        assert not (downloads / "a.pdf").exists() and not (downloads / "Project").exists()
        moved = library / "Finance" / "Project"
        assert (moved / "sub" / "link").readlink() == Path("b.txt")
        assert leftovers(downloads, library) == []
        with Store.open(config.database) as store:
            report = undo_run(store, config=config)
        assert sorted(src.name for _, src in report.restored) == ["Project", "a.pdf"]
        assert report.skipped == []
        assert (downloads / "a.pdf").read_bytes() == b"mine"
        assert (downloads / "Project" / "sub" / "b.txt").read_bytes() == b"two"
        assert leftovers(downloads, library) == []
    finally:
        remove_tree(downloads / "Project")


def test_recovery_waits_for_an_unmounted_volume(
    volume: Volume, downloads: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if volume.fs == "ExFAT":
        pytest.skip("nothing is copied to exFAT")
    put(downloads, "Project/a.txt", b"one")
    put(downloads, "Project/b.txt", b"two")
    make_old(downloads / "Project")
    config = library_on(volume, downloads)
    real = fsops.remove_tree

    def half_delete(path: Path) -> None:
        if path.name.endswith(".delete"):
            (path / "a.txt").unlink()
            raise Crash
        real(path)

    with monkeypatch.context() as patch, pytest.raises(Crash):
        patch.setattr(fsops, "remove_tree", half_delete)
        pipeline.run(config, FakeProvider(default=answer()), simulate=False)

    volume.detach()
    with Store.open(config.database) as store:
        assert recover(store, config) == 0
    assert not (downloads / "Project").exists()  # the half-deleted source is not put back
    volume.attach()
    with Store.open(config.database) as store:
        assert recover(store, config) == 1
        [op] = store.operations()
    assert op.status is OperationStatus.DONE
    moved = volume.mountpoint / "Library" / "Finance" / "Project"
    assert sorted(p.name for p in moved.iterdir()) == ["a.txt", "b.txt"]
    assert leftovers(downloads, volume.mountpoint / "Library") == []


def test_a_layout_on_another_volume_is_created_moved_into_and_undone(
    volume: Volume, downloads: Path
) -> None:
    """Category layouts (#86): nested folders made on the other volume, files copied in."""
    if volume.fs == "ExFAT":
        pytest.skip("nothing is copied to exFAT")
    put(downloads, "a.pdf", b"one")
    put(downloads, "b.pdf", b"two")
    categories = [{"name": "Finance", "layout": "{year}/{issuer}"}, {"name": "Other"}]
    config = fake_config(
        downloads, output_root=str(volume.mountpoint / "Library"), categories=categories
    )
    one = answer().model_copy(update={"issuer": "Acme", "document_date": date(2024, 5, 1)})
    two = answer().model_copy(update={"issuer": "ACME Inc.", "document_date": date(2024, 6, 1)})
    pipeline.run(config, FakeProvider({"a.pdf": one, "b.pdf": two}), simulate=False)
    folder = volume.mountpoint / "Library" / "Finance" / "2024" / "Acme"
    assert sorted(p.name for p in folder.iterdir()) == ["a.pdf", "b.pdf"]
    assert leftovers(downloads, volume.mountpoint / "Library") == []
    with Store.open(config.database) as store:
        report = undo_run(store, config=config)
    assert report.skipped == []
    assert (downloads / "a.pdf").read_bytes() == b"one"
    assert (downloads / "b.pdf").read_bytes() == b"two"

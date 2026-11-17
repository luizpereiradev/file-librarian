"""The only code in flib that changes the file system.

``move_no_replace`` renames with macOS ``renamex_np(..., RENAME_EXCL)``: the
kernel checks that the destination does not exist and performs the rename in
one atomic step, so a file that appears at the destination meanwhile is never
overwritten. It works for files and whole folders on the same volume. Across
volumes it fails with ``CrossVolumeError``, and the caller moves the item with
``copy_move`` instead (RF-33), in three journaled phases:

1. ``copying``: the item is copied into a new staging folder next to the
   destination (``.flib-<key>.copy``) with ``copyfile(3)`` and
   ``COPYFILE_ALL``, which keeps extended attributes (download origin, Finder
   tags), ACLs, permissions, flags and timestamps; flushed to the disk
   (``F_FULLFSYNC``); and verified: the copy's contents must match the
   source's, and the source must still be what the caller expects (its
   SHA-256, or its manifest digest for a folder unit) and must not have
   changed while it was copied.
2. ``copied``: the verified copy is renamed into place with ``RENAME_EXCL``,
   so it never overwrites anything (a taken name gets the next candidate).
3. ``placed``: the source is renamed to ``.flib-<key>.delete`` beside it and
   only then deleted, so a crash mid-way leaves either the whole source under
   its own name or a half-deleted item under a name that is clearly flib's.

The caller journals each phase before it starts, and ``resume_copy`` decides
after a crash, from the phase and what is on disk, whether to finish the move
or roll it back. The only things ever deleted are the source of a move whose
verified copy is in place, and the staging folder of a copy that never got
there while its source is still in place.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import errno
import fcntl
import os
import shutil
import stat
import unicodedata
from collections.abc import Callable, Iterable
from functools import cache
from itertools import chain
from pathlib import Path

from file_librarian.hashing import content_digest, file_sha256, same_content, unit_manifest
from file_librarian.models import CopyPhase

_RENAME_EXCL = 0x00000004
# <copyfile.h>: COPYFILE_ALL is ACL | STAT | XATTR | DATA.
_COPYFILE_ALL = 0x0000000F
_COPYFILE_RECURSIVE = 1 << 15
_COPYFILE_EXCL = 1 << 17
_COPYFILE_NOFOLLOW = (1 << 18) | (1 << 19)
_STAGED_NAME = "item"

Journal = Callable[[CopyPhase, Path], None]
"""Records that a cross-volume move enters a phase, aiming at the given destination."""


class DestinationExistsError(FileExistsError):
    pass


class CrossVolumeError(OSError):
    pass


class CopyMismatchError(OSError):
    """A copy did not match its source, or the source changed while it was copied."""


@cache
def _libc() -> ctypes.CDLL:
    lib = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    lib.renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    lib.renamex_np.restype = ctypes.c_int
    lib.copyfile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32]
    lib.copyfile.restype = ctypes.c_int
    return lib


def move_no_replace(src: Path, dst: Path) -> None:
    """Rename ``src`` to ``dst``; fail if ``dst`` exists. Never overwrites.

    On a case-insensitive volume, ``a.pdf`` -> ``A.pdf`` finds the source
    itself at the destination; when it is provably the same directory entry,
    the rename only changes the name's case and goes ahead.
    """
    rc = _libc().renamex_np(os.fsencode(src), os.fsencode(dst), _RENAME_EXCL)
    if rc == 0:
        return
    err = ctypes.get_errno()
    if err == errno.EEXIST and _same_entry(src, dst):
        if _libc().renamex_np(os.fsencode(src), os.fsencode(dst), 0) == 0:
            return
        err = ctypes.get_errno()
    if err == errno.EEXIST:
        raise DestinationExistsError(err, "destination exists", str(dst))
    if err == errno.EXDEV:
        raise CrossVolumeError(err, "source and destination are on different volumes", str(dst))
    raise OSError(err, os.strerror(err), str(src))


def _fold(name: str) -> str:
    return unicodedata.normalize("NFD", name).casefold()


def _same_entry(src: Path, dst: Path) -> bool:
    """Whether ``dst`` is just another spelling of ``src``'s own directory entry.

    Same folder, names equal but for case, same inode, and no
    entry spelled exactly like ``dst`` (a hard link with that name on a
    case-sensitive volume is a different entry and must not be replaced).
    """
    if src.parent != dst.parent or src.name == dst.name or _fold(src.name) != _fold(dst.name):
        return False
    try:
        a, b = os.lstat(src), os.lstat(dst)
    except OSError:
        return False
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino) and not has_exact_name(dst)


def has_exact_name(path: Path) -> bool:
    """Whether ``path``'s folder holds an entry spelled exactly like ``path`` (up to NFC).

    On a case-insensitive volume ``A.pdf`` opens ``a.pdf``, so existing is not enough.
    """
    try:
        names = [entry.name for entry in path.parent.iterdir()]
    except OSError:
        return False
    wanted = unicodedata.normalize("NFC", path.name)
    return any(unicodedata.normalize("NFC", n) == wanted for n in names)


def ensure_folder(path: Path, *, inside: Path) -> None:
    """Create ``path`` (a folder under ``inside``) refusing symlinks on the way.

    A symlinked category folder could send files anywhere on disk, so every
    existing component from ``inside`` down must be a real directory.
    """
    if not (path == inside or path.is_relative_to(inside)):
        raise ValueError(f"{path} is outside {inside}")
    current = inside.parent
    for part in path.relative_to(inside.parent).parts:
        current = current / part
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            current.mkdir()
            continue
        if not stat.S_ISDIR(st.st_mode):
            raise NotADirectoryError(f"{current} exists and is not a real folder (symlink or file)")


# --- moving across volumes (RF-33) ---------------------------------------------------------


def staging_folder(folder: Path, key: int) -> Path:
    """Where the cross-volume move ``key`` stages its copy before it goes into ``folder``."""
    return folder / f".flib-{key}.copy"


def trash_path(source: Path, key: int) -> Path:
    """Where the cross-volume move ``key`` puts its source just before deleting it."""
    return source.parent / f".flib-{key}.delete"


def copy_move(
    src: Path, candidates: Iterable[Path], *, key: int, expected: str, journal: Journal
) -> tuple[Path, str | None]:
    """Move ``src`` to the first free name in ``candidates`` by copy, verify, delete.

    The candidates share one folder. ``key`` (the journal entry's id) names
    the staging and trash paths, so recovery finds them; ``expected`` is what
    the source must still be (see ``hashing.same_content``). Until the copy
    is in place, any error removes the staging folder and leaves the source
    untouched. Returns the new path, and a warning if the source could not be
    removed afterwards (the move stands: the verified copy is in place).
    """
    names = iter(candidates)
    first = next(names)
    stage = staging_folder(first.parent, key)
    journal(CopyPhase.COPYING, first)
    item = copy_verified(src, stage, expected)
    journal(CopyPhase.COPIED, first)
    try:
        final = _place(item, chain([first], names), lambda c: journal(CopyPhase.COPIED, c))
    except OSError:
        remove_tree(stage)  # the source is still there
        raise
    journal(CopyPhase.PLACED, final)
    return final, _discard(src, trash_path(src, key), expected)


def copy_verified(src: Path, stage: Path, expected: str) -> Path:
    """Copy ``src`` into the new folder ``stage``, flush and verify it. Returns the copy.

    On any failure (disk full, a mismatch, a source that changed meanwhile)
    ``stage`` is removed again and the error raised; ``src`` is never touched.
    """
    before = os.lstat(src)
    stage.mkdir()  # fails if a stale one is there: nothing of ours is overwritten
    item = stage / _STAGED_NAME
    try:
        _copyfile(src, item)
        _sync_tree(item)
        _sync(stage)
        _verify(src, item, before, expected)
    except Exception:
        remove_tree(stage)
        raise
    return item


def resume_copy(
    src: Path, dst: Path, *, key: int, phase: CopyPhase, expected: str, journal: Journal
) -> Path | None:
    """Finish or roll back a ``copy_move`` that a crash cut off in ``phase``.

    Returns where the item now is when the move was completed, or ``None``
    when it was rolled back, the source left as it is. A copy is deleted only
    while its source is still in place.
    """
    stage = staging_folder(dst.parent, key)
    item = stage / _STAGED_NAME
    if phase is CopyPhase.COPYING:
        if os.path.lexists(src):
            remove_tree(stage)  # never verified, and the source is still there
            return None
        if not same_content(item, expected):
            return None  # the source is gone and the copy unverified: leave both alone
        phase = CopyPhase.COPIED
    if phase is CopyPhase.COPIED and os.path.lexists(item):
        try:
            dst = _place(item, [dst], lambda c: journal(CopyPhase.COPIED, c))
        except OSError:
            if os.path.lexists(src):
                remove_tree(stage)
            return None
        journal(CopyPhase.PLACED, dst)
    if not has_exact_name(dst):
        # The copy never reached its destination, or is gone from there.
        trash = trash_path(src, key)
        if os.path.lexists(trash) and not os.path.lexists(src):
            move_no_replace(trash, src)
        if os.path.lexists(src):
            remove_tree(stage)
        return None
    remove_tree(stage)
    _discard(src, trash_path(src, key), expected, check=True)
    return dst


def remove_tree(path: Path) -> None:
    """Delete ``path`` (a file, symlink or folder) if it exists, never following symlinks."""
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(st.st_mode):
        shutil.rmtree(path)
    else:
        path.unlink()


def _copyfile(src: Path, dst: Path) -> None:
    flags = _COPYFILE_ALL | _COPYFILE_EXCL | _COPYFILE_NOFOLLOW
    if stat.S_ISDIR(os.lstat(src).st_mode):
        flags |= _COPYFILE_RECURSIVE
    if _libc().copyfile(os.fsencode(src), os.fsencode(dst), None, flags) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), str(dst))


def _sync(path: Path) -> None:
    """Flush ``path`` to the disk itself, not only to the drive's cache, where supported."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        try:
            fcntl.fcntl(fd, fcntl.F_FULLFSYNC)
        except OSError:
            os.fsync(fd)  # some file systems (network shares) only offer fsync
    finally:
        os.close(fd)


def _sync_tree(root: Path) -> None:
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            path = Path(dirpath, name)
            if stat.S_ISREG(os.lstat(path).st_mode):
                _sync(path)
        _sync(Path(dirpath))
    if not stat.S_ISDIR(os.lstat(root).st_mode):
        _sync(root)


def _verify(src: Path, copy: Path, before: os.stat_result, expected: str) -> None:
    """The copy matches the source, which is still ``expected`` and did not change meanwhile."""
    after = os.lstat(src)
    if (after.st_ino, after.st_size, after.st_mtime_ns) != (
        before.st_ino,
        before.st_size,
        before.st_mtime_ns,
    ):
        raise CopyMismatchError(errno.EAGAIN, "changed while it was copied", str(src))
    if stat.S_ISDIR(after.st_mode):
        same = unit_manifest(src).digest == expected and content_digest(copy) == content_digest(src)
    else:
        same = file_sha256(copy) == expected
    if not same:
        raise CopyMismatchError(errno.EIO, "the copy does not match the original", str(src))


def _place(item: Path, candidates: Iterable[Path], trying: Callable[[Path], None]) -> Path:
    """Rename the staged ``item`` to the first free candidate, then drop its staging folder."""
    for candidate in candidates:
        trying(candidate)
        try:
            move_no_replace(item, candidate)
        except DestinationExistsError:
            continue
        _sync(candidate.parent)
        with contextlib.suppress(OSError):
            item.parent.rmdir()
        return candidate
    raise DestinationExistsError(errno.EEXIST, "no free name", str(item))


def _discard(src: Path, trash: Path, expected: str, *, check: bool = False) -> str | None:
    """Delete the source of a copy now in place, if it is still what was copied.

    It is renamed aside first, so its own name never holds a half-deleted
    item. A folder is checked again (its manifest is cheap); a file was
    checked moments ago, unless ``check``. Returns a warning instead of
    raising: by now the move itself stands.
    """
    try:
        if os.path.lexists(src) and (not (check or src.is_dir()) or same_content(src, expected)):
            move_no_replace(src, trash)
        remove_tree(trash)
        _sync(src.parent)
    except OSError as exc:
        return f"copied, but the original could not be removed: {exc.strerror or exc}"
    return None

"""The only code in flib that changes the file system.

``move_no_replace`` renames with macOS ``renamex_np(..., RENAME_EXCL)``: the
kernel checks that the destination does not exist and performs the rename in
one atomic step, so a file that appears at the destination meanwhile is never
overwritten. It works for files and whole folders on the same volume. Across
volumes it fails with ``CrossVolumeError``, and the caller moves the item with
``copy_move`` instead (RF-33), in three journaled phases:

1. ``copying``: the destination volume must support ``RENAME_EXCL`` (exFAT
   and FAT do not: nothing is copied there). The item is copied into a new
   staging folder next to the destination (``.flib-<key>.copy``, ``key``
   being the journal entry's id and a random suffix) with ``copyfile(3)`` and
   ``COPYFILE_ALL`` (a folder entry by entry), which keeps extended
   attributes (download origin, Finder tags), ACLs, permissions, flags and
   timestamps; flushed to the disk
   (``F_FULLFSYNC``); and verified: the copy's contents must match the
   source's, and the source must still be what the caller expects (its
   SHA-256, or its manifest digest for a folder unit) and must not have
   changed while it was copied.
2. ``copied``: the verified copy is renamed into place with ``RENAME_EXCL``,
   so it never overwrites anything (a taken name gets the next candidate).
3. ``placed``: the source, if it is still what was copied, is renamed to
   ``.flib-<key>.delete`` beside it and only then deleted (read-only folders
   included; locked items are refused before copying), so a crash mid-way
   leaves either the whole
   source under its own name or a half-deleted item under a name that is
   clearly flib's. A source that changed after it was copied is kept, with a
   warning.

The caller journals each phase before it starts, and ``resume_copy`` decides
after a crash, from the phase and what is on disk, whether to finish the move
or roll it back. When either folder cannot be listed (an unmounted volume),
it decides nothing and the entry waits. The only things ever deleted are the
source of a move whose verified copy is in place, and the staging folder of a
copy that never got there while its source is still in place.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import errno
import fcntl
import logging
import os
import secrets
import stat
import unicodedata
from collections.abc import Callable, Iterable
from functools import cache
from itertools import chain
from pathlib import Path

from file_librarian.hashing import content_digest, file_sha256, same_content, unit_manifest
from file_librarian.models import CopyPhase

_RENAME_EXCL = 0x00000004
# <copyfile.h>: COPYFILE_METADATA is ACL | STAT | XATTR; COPYFILE_ALL adds DATA.
_COPYFILE_METADATA = 0x00000007
_COPYFILE_ALL = 0x0000000F
_COPYFILE_EXCL = 1 << 17
_COPYFILE_NOFOLLOW = (1 << 18) | (1 << 19)
_STAGED_NAME = "item"

_USER_LOCKS = stat.UF_IMMUTABLE | stat.UF_APPEND
_LOCKS = _USER_LOCKS | stat.SF_IMMUTABLE | stat.SF_APPEND
_NO_RENAME_EXCL = frozenset({errno.ENOTSUP, errno.EINVAL})

Journal = Callable[[CopyPhase, Path], None]
"""Records that a cross-volume move enters a phase, aiming at the given destination."""

log = logging.getLogger(__name__)


class DestinationExistsError(FileExistsError):
    pass


class CrossVolumeError(OSError):
    pass


class CopyMismatchError(OSError):
    """A copy did not match its source, or the source changed while it was copied."""


class UnsupportedVolumeError(OSError):
    """The destination volume cannot rename without replacing, so nothing is moved there."""


class UnavailableError(OSError):
    """The volume of an interrupted copy is not there (unmounted)."""


class LockedError(OSError):
    """The item, or something inside it, is locked (Finder's Locked, or an immutable flag)."""


@cache
def _libc() -> ctypes.CDLL:
    lib = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    lib.renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    lib.renamex_np.restype = ctypes.c_int
    lib.copyfile.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_void_p, ctypes.c_uint32]
    lib.copyfile.restype = ctypes.c_int
    lib.fchflags.argtypes = [ctypes.c_int, ctypes.c_uint]
    lib.fchflags.restype = ctypes.c_int
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


def ensure_folder(path: Path, *, inside: Path) -> list[Path]:
    """Create ``path`` (a folder under ``inside``) refusing symlinks on the way.

    A symlinked category folder could send files anywhere on disk, so every
    existing component from ``inside`` down must be a real directory.
    Returns the folders it created, outermost first.
    """
    if not (path == inside or path.is_relative_to(inside)):
        raise ValueError(f"{path} is outside {inside}")
    created: list[Path] = []
    current = inside.parent
    for part in path.relative_to(inside.parent).parts:
        current = current / part
        try:
            st = os.lstat(current)
        except FileNotFoundError:
            current.mkdir()
            created.append(current)
            continue
        if not stat.S_ISDIR(st.st_mode):
            raise NotADirectoryError(f"{current} exists and is not a real folder (symlink or file)")
    return created


# --- moving across volumes (RF-33) ---------------------------------------------------------


def new_copy_key(op_id: int) -> str:
    """The journal entry's id and a random suffix, so a reset database never reuses names."""
    return f"{op_id}-{secrets.token_hex(4)}"


def staging_folder(folder: Path, key: str) -> Path:
    """Where the cross-volume move ``key`` stages its copy before it goes into ``folder``."""
    return folder / f".flib-{key}.copy"


def trash_path(source: Path, key: str) -> Path:
    """Where the cross-volume move ``key`` puts its source just before deleting it."""
    return source.parent / f".flib-{key}.delete"


def copy_move(
    src: Path, candidates: Iterable[Path], *, key: str, expected: str, journal: Journal
) -> tuple[Path, str | None]:
    """Move ``src`` to the first free name in ``candidates`` by copy, verify, delete.

    The candidates share one folder. ``key`` (see ``new_copy_key``) names
    the staging and trash paths, so recovery finds them; ``expected`` is what
    the source must still be (see ``hashing.same_content``). Until the copy
    is in place, any error removes the staging folder and leaves the source
    untouched. Returns the new path, and a warning if something went wrong
    after that (the move stands: the verified copy is in place).
    """
    names = iter(candidates)
    first = next(names)
    stage = staging_folder(first.parent, key)
    journal(CopyPhase.COPYING, first)
    item, before = copy_verified(src, stage, expected)
    journal(CopyPhase.COPIED, first)
    try:
        final = _place(item, chain([first], names), lambda c: journal(CopyPhase.COPIED, c))
    except OSError:
        _remove_quietly(stage)  # the source is still there
        raise
    journal(CopyPhase.PLACED, final)
    warnings = [_sync_warning(final.parent), _discard(src, trash_path(src, key), expected, before)]
    return final, "; ".join(w for w in warnings if w) or None


def copy_verified(src: Path, stage: Path, expected: str) -> tuple[Path, os.stat_result]:
    """Copy ``src`` into the new folder ``stage``, flush and verify it.

    Returns the copy and the source's ``lstat`` from before the copy. A
    locked item (or a folder with anything locked inside) is refused before
    anything is written, as a rename on one volume would refuse it. On any
    other failure (a volume without ``RENAME_EXCL``, a full disk, a mismatch,
    a source that changed meanwhile) ``stage`` is removed again and the error
    raised; ``src`` is never touched.
    """
    before = os.lstat(src)
    _refuse_locked(src)
    stage.mkdir()  # fails if a stale one is there: nothing of ours is overwritten
    item = stage / _STAGED_NAME
    try:
        require_rename_excl(stage)
        _copyfile(src, item)
        _sync_tree(item)
        _sync(stage)
        _verify(src, item, before, expected)
    except Exception:
        _remove_quietly(stage)  # never hide the error that matters behind a cleanup error
        raise
    return item, before


def volume_of(folder: Path) -> int:
    """The device of ``folder``, recorded when a copy starts (see ``resume_copy``)."""
    return folder.stat().st_dev


def resume_copy(
    src: Path,
    dst: Path,
    *,
    key: str,
    phase: CopyPhase,
    expected: str,
    journal: Journal,
    volumes: tuple[int | None, int | None] = (None, None),
) -> Path | None:
    """Finish or roll back a ``copy_move`` that a crash cut off in ``phase``.

    Returns where the item now is when the move was completed, or ``None``
    when it was rolled back, the source left as it is. A copy is deleted only
    while its source is still in place. ``volumes`` are the devices of the
    source's and the destination's folders when the copy started. If either
    volume is gone (unmounted) it raises ``UnavailableError``, having changed
    nothing: an unmounted volume must not look like a vanished copy. A folder
    removed from a volume that is still there is simply absent.
    """
    for folder, volume in zip((src.parent, dst.parent), volumes, strict=True):
        if _volume_missing(folder, volume):
            raise UnavailableError(errno.ENOENT, "volume not available", str(folder))
    stage = staging_folder(dst.parent, key)
    item = stage / _STAGED_NAME
    if phase is CopyPhase.COPYING:
        if os.path.lexists(src):
            remove_tree(stage)  # never verified, and the source is still there
            return None
        # The source is gone: a later phase may have been lost, or it was deleted by hand.
        if not (_holds(dst, expected) or _holds(item, expected)):
            return None  # nothing verifiable: leave whatever is there alone
        phase = CopyPhase.COPIED
    if phase is CopyPhase.COPIED and os.path.lexists(item):
        try:
            dst = _place(item, [dst], lambda c: journal(CopyPhase.COPIED, c))
        except OSError:
            if os.path.lexists(src):
                _remove_quietly(stage)
            return None
        journal(CopyPhase.PLACED, dst)
    source = src if os.path.lexists(src) else None
    if not _holds(dst, expected, source, trusted=True):
        # The copy never reached its destination, or is not ours, or is gone from there.
        trash = trash_path(src, key)
        if os.path.lexists(trash) and not os.path.lexists(src):
            move_no_replace(trash, src)
        if os.path.lexists(src):
            _remove_quietly(stage)
        return None
    _remove_quietly(stage)
    if warning := _discard(src, trash_path(src, key), expected):
        log.warning("%s: %s", src, warning)
    return dst


def remove_tree(path: Path) -> None:
    """Delete ``path`` (a file, symlink or folder) if it exists, never following symlinks.

    Only flib's own staging and trash paths are removed this way. The walk
    goes through open folder descriptors (``O_NOFOLLOW``, names relative to
    them), so a folder swapped for a symlink meanwhile cannot lead it
    elsewhere. Read-only folders and ``uchg`` entries of flib's own copies
    are unlocked first; entries that vanish meanwhile (such as the ``._``
    files some volumes keep beside each file) are skipped.
    """
    try:
        parent = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    except FileNotFoundError:
        return
    try:
        _remove_at(parent, path.name)
    finally:
        os.close(parent)


def _remove_at(dir_fd: int, name: str) -> None:
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return
    if stat.S_ISDIR(st.st_mode):
        _empty_folder_at(dir_fd, name, st)
        with contextlib.suppress(FileNotFoundError):
            os.rmdir(name, dir_fd=dir_fd)
        return
    if stat.S_ISREG(st.st_mode) and st.st_flags & _USER_LOCKS:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=dir_fd)
        try:
            _unlock(fd, st)
        finally:
            os.close(fd)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(name, dir_fd=dir_fd)


def _empty_folder_at(dir_fd: int, name: str, st: os.stat_result) -> None:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    try:
        fd = os.open(name, flags, dir_fd=dir_fd)
    except PermissionError:  # a folder without read permission at all
        os.chmod(name, 0o700, dir_fd=dir_fd, follow_symlinks=False)
        fd = os.open(name, flags, dir_fd=dir_fd)
    try:
        _unlock(fd, st)
        mode = os.fstat(fd).st_mode
        if mode & 0o700 != 0o700:
            os.fchmod(fd, stat.S_IMODE(mode) | 0o700)
        with os.scandir(fd) as entries:
            names = [entry.name for entry in entries]
        for child in names:
            _remove_at(fd, child)
    finally:
        os.close(fd)


def _unlock(fd: int, expected: os.stat_result) -> None:
    """Clear the user lock flags of the open ``fd``, which must be the entry ``expected``."""
    st = os.fstat(fd)
    if (st.st_dev, st.st_ino) != (expected.st_dev, expected.st_ino):
        raise OSError(errno.EAGAIN, "changed while it was being removed")
    if st.st_flags & _USER_LOCKS and _libc().fchflags(fd, st.st_flags & ~_USER_LOCKS) != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err))


def _refuse_locked(src: Path) -> None:
    """Raise ``LockedError`` if ``src`` or anything inside it is locked.

    A locked item cannot be renamed on its own volume either, so it is not
    moved to another one, where flib would have to delete the locked original.
    """
    inside = (Path(d, n) for d, ds, fs in os.walk(src) for n in [*ds, *fs])
    for path in chain([src], inside):
        if os.lstat(path).st_flags & _LOCKS:
            raise LockedError(
                errno.EPERM, "locked (Finder's Locked); unlock it to let flib move it", str(path)
            )


def _remove_quietly(path: Path) -> None:
    try:
        remove_tree(path)
    except OSError as exc:
        log.warning("could not remove %s: %s", path, exc.strerror or exc)


def _volume_missing(folder: Path, volume: int | None) -> bool:
    """``folder`` cannot be listed, and its volume is not mounted (as far as can be told).

    The nearest existing ancestor tells: on another device than ``volume``,
    the volume is gone; on the same one, only the folder is. Without a
    recorded ``volume``, any folder that cannot be listed counts as missing.
    """
    try:
        with os.scandir(folder):
            return False
    except OSError:
        pass
    if volume is None:
        return True
    ancestor = folder
    while not os.path.lexists(ancestor) and ancestor != ancestor.parent:
        ancestor = ancestor.parent
    try:
        return ancestor.stat().st_dev != volume
    except OSError:
        return True


def _holds(path: Path, expected: str, source: Path | None = None, *, trusted: bool = False) -> bool:
    """``path``, spelled exactly, holds the item that was copied.

    A file must hash to ``expected``. A folder must have the same contents as
    ``source`` while it exists; without it, its manifest must equal
    ``expected``, unless the journal already said the copy was verified
    (``trusted``): its own manifest differs on a volume with coarser times.
    """
    try:
        if not has_exact_name(path) or path.is_symlink():
            return False
        if not path.is_dir():
            return file_sha256(path) == expected
        if source is not None and source.is_dir():
            return content_digest(path) == content_digest(source)
        return trusted or unit_manifest(path).digest == expected
    except OSError:
        return False


def require_rename_excl(folder: Path) -> None:
    """Fail unless the volume of ``folder`` can rename without replacing (exFAT cannot).

    Renames an empty probe folder inside ``folder`` with ``RENAME_EXCL``.
    """
    token = secrets.token_hex(4)
    probe, probed = folder / f".flib-probe-{token}", folder / f".flib-probed-{token}"
    probe.mkdir()
    try:
        move_no_replace(probe, probed)
    except OSError as exc:
        if exc.errno in _NO_RENAME_EXCL:
            raise UnsupportedVolumeError(
                exc.errno,
                "the destination volume cannot rename without replacing, "
                "so flib does not move items there",
                str(folder),
            ) from exc
        raise
    finally:
        for path in (probe, probed):
            with contextlib.suppress(FileNotFoundError):
                path.rmdir()


def _copyfile(src: Path, dst: Path) -> None:
    """Copy ``src`` (a file, symlink or whole folder) to the new path ``dst``, metadata included.

    A folder is walked here rather than with ``COPYFILE_RECURSIVE``, which
    fails on a symlink inside a read-only folder: each folder is created
    writable, filled, and only then given its own permissions, flags, dates,
    extended attributes and ACLs.
    """
    _copy_entry(src, dst)


def _copy_entry(src: Path, dst: Path) -> None:
    if not stat.S_ISDIR(os.lstat(src).st_mode):
        _copyfile_call(src, dst, _COPYFILE_ALL | _COPYFILE_EXCL | _COPYFILE_NOFOLLOW)
        return
    dst.mkdir(mode=0o700)
    with os.scandir(src) as entries:
        names = sorted(entry.name for entry in entries)
    for name in names:
        _copy_entry(src / name, dst / name)
    _copyfile_call(src, dst, _COPYFILE_METADATA | _COPYFILE_NOFOLLOW)


def _copyfile_call(src: Path, dst: Path, flags: int) -> None:
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


def _sync_warning(path: Path) -> str | None:
    """Flush a folder whose entries changed; once the item is in place, failing is a warning."""
    try:
        _sync(path)
    except OSError as exc:
        return f"could not flush {path} to disk: {exc.strerror or exc}"
    return None


def _sync_tree(root: Path) -> None:
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
        for name in filenames:
            path = Path(dirpath, name)
            if stat.S_ISREG(os.lstat(path).st_mode):
                _sync(path)
        _sync(Path(dirpath))
    if not stat.S_ISDIR(os.lstat(root).st_mode):
        _sync(root)


def _identity(st: os.stat_result) -> tuple[int, int, int]:
    return st.st_ino, st.st_size, st.st_mtime_ns


def _verify(src: Path, copy: Path, before: os.stat_result, expected: str) -> None:
    """The copy matches the source, which is still ``expected`` and did not change meanwhile."""
    after = os.lstat(src)
    if _identity(after) != _identity(before):
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
        with contextlib.suppress(OSError):
            item.parent.rmdir()
        return candidate
    raise DestinationExistsError(errno.EEXIST, "no free name", str(item))


def _discard(
    src: Path, trash: Path, expected: str, before: os.stat_result | None = None
) -> str | None:
    """Delete the source of a copy now in place, if it is still what was copied.

    A file checked moments ago (``before``, its ``lstat`` from before the
    copy) is compared by inode, size and modification time; otherwise, and
    for a folder always (its manifest is cheap), by content. It is renamed
    aside first, so its own name never holds a half-deleted item, and put
    back if what was renamed is not what was checked. Returns a warning
    instead of raising: by now the move itself stands.
    """
    try:
        if os.path.lexists(src):
            st = os.lstat(src)
            if before is not None and not stat.S_ISDIR(st.st_mode):
                same = _identity(st) == _identity(before)
            else:
                same = same_content(src, expected)
            if not same:
                return "the original changed after it was copied, so it was kept"
            move_no_replace(src, trash)
            if os.lstat(trash).st_ino != st.st_ino:
                move_no_replace(trash, src)
                return "the original changed while it was being removed, so it was kept"
        remove_tree(trash)
    except OSError as exc:
        return f"copied, but the original could not be removed: {exc.strerror or exc}"
    return _sync_warning(src.parent)

"""The only code in flib that changes the file system.

``move_no_replace`` renames with macOS ``renamex_np(..., RENAME_EXCL)``: the
kernel checks that the destination does not exist and performs the rename in
one atomic step, so a file that appears at the destination meanwhile is never
overwritten. It works for files and whole folders on the same volume. Moving
across volumes fails with ``CrossVolumeError``; copy-verify-delete (RF-33) is
a later milestone, and until then such files are simply not moved.

Nothing here deletes anything.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import stat
import unicodedata
from functools import cache
from pathlib import Path

_RENAME_EXCL = 0x00000004


class DestinationExistsError(FileExistsError):
    pass


class CrossVolumeError(OSError):
    pass


@cache
def _libc() -> ctypes.CDLL:
    lib = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    lib.renamex_np.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_uint]
    lib.renamex_np.restype = ctypes.c_int
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

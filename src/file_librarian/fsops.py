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
    """Rename ``src`` to ``dst``; fail if ``dst`` exists. Never overwrites."""
    rc = _libc().renamex_np(os.fsencode(src), os.fsencode(dst), _RENAME_EXCL)
    if rc == 0:
        return
    err = ctypes.get_errno()
    if err == errno.EEXIST:
        raise DestinationExistsError(err, "destination exists", str(dst))
    if err == errno.EXDEV:
        raise CrossVolumeError(err, "source and destination are on different volumes", str(dst))
    raise OSError(err, os.strerror(err), str(src))


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

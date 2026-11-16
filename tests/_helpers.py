"""Shared test helpers. Import from here, not from conftest.py."""

from __future__ import annotations

import ctypes
import ctypes.util
import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_XATTR_NOFOLLOW = 0x0001


def write_xattr(path: Path, name: str, value: bytes) -> None:
    """Set an extended attribute on a synthetic test file (macOS only)."""
    lib = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    lib.setxattr.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_uint32,
        ctypes.c_int,
    ]
    lib.setxattr.restype = ctypes.c_int
    rc = lib.setxattr(os.fsencode(path), name.encode(), value, len(value), 0, _XATTR_NOFOLLOW)
    if rc != 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), str(path))

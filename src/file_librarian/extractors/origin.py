"""Read where a file was downloaded from (RF-25). M0 prototype.

When a browser saves a download, macOS stores the source in the extended
attribute ``com.apple.metadata:kMDItemWhereFroms``: a binary property list
holding a list of strings, usually ``[download URL, referring page URL]``.

Python's ``os.getxattr`` does not exist on macOS, so this calls libc directly
through ctypes; no extra dependency is needed. This module only reads; it
never writes metadata.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import plistlib
import sys
from pathlib import Path

WHERE_FROMS = "com.apple.metadata:kMDItemWhereFroms"

_XATTR_NOFOLLOW = 0x0001
_ENOATTR = 93  # macOS: attribute not found
_MAX_URLS = 5
_MAX_URL_LENGTH = 2048


def _libc() -> ctypes.CDLL:
    lib = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    lib.getxattr.argtypes = [
        ctypes.c_char_p,
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_uint32,
        ctypes.c_int,
    ]
    lib.getxattr.restype = ctypes.c_ssize_t
    return lib


def read_xattr(path: Path, name: str) -> bytes | None:
    """Return the raw value of an extended attribute, or ``None`` if it is absent."""
    if sys.platform != "darwin":
        return None
    lib = _libc()
    cpath = os.fsencode(path)
    cname = name.encode()
    size = lib.getxattr(cpath, cname, None, 0, 0, _XATTR_NOFOLLOW)
    if size < 0:
        err = ctypes.get_errno()
        if err in (_ENOATTR, errno.ENOTSUP):
            return None
        raise OSError(err, os.strerror(err), str(path))
    buf = ctypes.create_string_buffer(size)
    read = lib.getxattr(cpath, cname, buf, size, 0, _XATTR_NOFOLLOW)
    if read < 0:
        err = ctypes.get_errno()
        raise OSError(err, os.strerror(err), str(path))
    return buf.raw[:read]


def parse_where_froms(raw: bytes) -> tuple[str, ...]:
    """Decode the attribute value. Malformed data yields an empty tuple, never an error."""
    try:
        value = plistlib.loads(raw)
    except Exception:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return ()
    urls = []
    for item in value:
        if isinstance(item, str):
            item = item.strip()
            if item and len(item) <= _MAX_URL_LENGTH and item not in urls:
                urls.append(item)
    return tuple(urls[:_MAX_URLS])


def download_origin(path: Path) -> tuple[str, ...]:
    """The URLs macOS recorded for this download, most specific first. Empty if unknown."""
    try:
        raw = read_xattr(path, WHERE_FROMS)
    except OSError:
        return ()
    return parse_where_froms(raw) if raw else ()

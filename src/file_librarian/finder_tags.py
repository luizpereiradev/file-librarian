"""The category as a Finder tag (RF-36).

Finder keeps a file's tags in the extended attribute
``com.apple.metadata:_kMDItemUserTags``: a binary property list holding a
list of strings, each ``"Name"`` or ``"Name\\n<color index>"``. Tags are added
and removed one entry at a time, so the user's own tags (and their colors)
are always kept. A value flib cannot parse is left untouched.

Callers in the executor and in undo use ``tag_moved`` and ``untag_restored``,
which never raise: a tag is a convenience and must never fail a move.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import os
import plistlib
from functools import cache
from pathlib import Path

from file_librarian.extractors.origin import read_xattr

USER_TAGS = "com.apple.metadata:_kMDItemUserTags"

_XATTR_NOFOLLOW = 0x0001
_ENOATTR = 93  # macOS: attribute not found


@cache
def _libc() -> ctypes.CDLL:
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
    lib.removexattr.argtypes = [ctypes.c_char_p, ctypes.c_char_p, ctypes.c_int]
    lib.removexattr.restype = ctypes.c_int
    return lib


def _raise_errno(path: Path) -> None:
    err = ctypes.get_errno()
    raise OSError(err, os.strerror(err), str(path))


def tag_name(entry: str) -> str:
    """The name part of a tag entry, without its color index."""
    return entry.split("\n", 1)[0]


def read_tags(path: Path) -> list[str]:
    """The file's tag entries as stored. Raises ``ValueError`` on a value flib cannot parse."""
    raw = read_xattr(path, USER_TAGS)
    if not raw:
        return []
    try:
        value = plistlib.loads(raw)
    except Exception as exc:
        raise ValueError(f"unreadable Finder tags on {path}") from exc
    if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
        raise ValueError(f"unexpected Finder tags on {path}")
    return value


def _write_tags(path: Path, entries: list[str]) -> None:
    lib = _libc()
    cpath, cname = os.fsencode(path), USER_TAGS.encode()
    if not entries:
        if lib.removexattr(cpath, cname, _XATTR_NOFOLLOW) != 0 and ctypes.get_errno() != _ENOATTR:
            _raise_errno(path)
        return
    value = plistlib.dumps(entries, fmt=plistlib.FMT_BINARY)
    if lib.setxattr(cpath, cname, value, len(value), 0, _XATTR_NOFOLLOW) != 0:
        _raise_errno(path)


def add_tag(path: Path, name: str) -> bool:
    """Add tag ``name`` unless the file already has it (Finder ignores case). True if added."""
    entries = read_tags(path)
    if any(tag_name(e).casefold() == name.casefold() for e in entries):
        return False
    _write_tags(path, [*entries, name])
    return True


def remove_tag(path: Path, name: str) -> bool:
    """Remove tag ``name`` (whatever color it has now), keeping every other tag."""
    entries = read_tags(path)
    kept = [e for e in entries if tag_name(e) != name]
    if len(kept) == len(entries):
        return False
    _write_tags(path, kept)
    return True


def tag_moved(path: Path, name: str) -> tuple[str | None, str | None]:
    """Tag a file flib just moved. Returns (the tag added or ``None``, a warning or ``None``)."""
    try:
        return (name if add_tag(path, name) else None), None
    except (OSError, ValueError) as exc:
        return None, f"Finder tag {name!r} not set: {_describe(exc)}"


def untag_restored(path: Path, name: str) -> str | None:
    """Remove the tag flib added from a file moved back by undo. Returns a warning or ``None``."""
    try:
        remove_tag(path, name)
    except (OSError, ValueError) as exc:
        return f"Finder tag {name!r} not removed: {_describe(exc)}"
    return None


def _describe(exc: Exception) -> str:
    return getattr(exc, "strerror", None) or str(exc)

"""macOS Data Protection: files that can be read only while the Mac is unlocked.

Apps built for iOS and run on the Mac can save files with Data Protection
class A ("complete") or B: the system refuses to open them (``EPERM``) while
the screen is locked. Most files are class C (readable after the first unlock
since startup) or D (always readable). A scheduled run that starts while the
Mac is locked cannot read class A or B files, and that is neither a broken
file nor a missing Full Disk Access permission: the scanner skips them as
``locked`` and the scheduled run waits for the next unlock (see
``catchup``).

``protection_class`` reads the class with ``getattrlist(2)``
(``ATTR_CMN_DATA_PROTECT_FLAGS``), which works while the Mac is locked and
never opens the file. ``screen_locked`` asks the window server for the
console session (``CGSessionCopyCurrentDictionary``), falling back to
``ioreg`` when there is no window-server session. Neither raises: ``None``
means "cannot tell". flib never changes a file's protection class.

Tests replace ``protection_class`` and ``screen_locked`` (see
``tests/conftest.py``); nothing here is called on a real session by them.
"""

from __future__ import annotations

import contextlib
import ctypes
import ctypes.util
import errno
import os
import plistlib
import subprocess
from functools import cache
from pathlib import Path

CLASS_COMPLETE = 1
"""Class A: readable only while the Mac is unlocked."""
CLASS_UNLESS_OPEN = 2
"""Class B: once closed, readable only while the Mac is unlocked."""
CLASS_UNTIL_FIRST_UNLOCK = 3
"""Class C: what most files have; readable after the first unlock since startup."""
CLASS_NONE = 4
"""Class D: always readable."""
LOCKED_CLASSES = frozenset({CLASS_COMPLETE, CLASS_UNLESS_OPEN})
"""The classes the system will not read while the Mac is locked."""

LOCKED_NOW = "protected by macOS while the Mac is locked; unlock it and try again"
"""Why a library file could not be checked: it is neither changed nor gone."""

IOREG = "/usr/sbin/ioreg"
_LOCKED_KEY = "CGSSessionScreenIsLocked"

# <sys/attr.h>
_ATTR_BIT_MAP_COUNT = 5
_ATTR_CMN_DATA_PROTECT_FLAGS = 0x40000000
_FSOPT_NOFOLLOW = 0x00000001
_CLASS_MASK = 0x1F
_UTF8 = 0x08000100  # kCFStringEncodingUTF8


class LockedOutError(PermissionError):
    """A file readable only while the Mac is unlocked could not be read: try again later."""


class _AttrList(ctypes.Structure):
    _fields_ = [
        ("bitmapcount", ctypes.c_ushort),
        ("reserved", ctypes.c_uint16),
        ("commonattr", ctypes.c_uint32),
        ("volattr", ctypes.c_uint32),
        ("dirattr", ctypes.c_uint32),
        ("fileattr", ctypes.c_uint32),
        ("forkattr", ctypes.c_uint32),
    ]


class _ProtectBuffer(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint32), ("flags", ctypes.c_uint32)]


@cache
def _libc() -> ctypes.CDLL:
    lib = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    lib.getattrlist.argtypes = [
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_ulong,
    ]
    lib.getattrlist.restype = ctypes.c_int
    return lib


def _read_class(path: Path) -> int | None:
    """The real ``getattrlist`` call behind ``protection_class``."""
    try:
        request = _AttrList(_ATTR_BIT_MAP_COUNT, 0, _ATTR_CMN_DATA_PROTECT_FLAGS, 0, 0, 0, 0)
        buffer = _ProtectBuffer()
        rc = _libc().getattrlist(
            os.fsencode(path),
            ctypes.byref(request),
            ctypes.byref(buffer),
            ctypes.sizeof(buffer),
            _FSOPT_NOFOLLOW,
        )
    except (OSError, AttributeError, TypeError, ValueError):
        return None
    if rc != 0 or buffer.length < ctypes.sizeof(buffer):
        return None
    value = buffer.flags & _CLASS_MASK
    return value or None


def protection_class(path: Path) -> int | None:
    """The Data Protection class of ``path`` (1 to 4), or ``None`` when it cannot be told.

    Reads metadata only (it works while the Mac is locked), never follows a
    symlink and never raises.
    """
    return _read_class(path)


def screen_locked() -> bool | None:
    """Whether the Mac's screen is locked now; ``None`` when it cannot be told. Never raises."""
    return _read_screen()


def _read_screen() -> bool | None:
    """The real lookup behind ``screen_locked``."""
    found = _session_locked()
    return found if found is not None else _ioreg_locked()


@cache
def _frameworks() -> tuple[ctypes.CDLL, ctypes.CDLL]:  # pragma: no cover - the real session
    cg = ctypes.CDLL("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics")
    cf = ctypes.CDLL("/System/Library/Frameworks/CoreFoundation.framework/CoreFoundation")
    cg.CGSessionCopyCurrentDictionary.argtypes = []
    cg.CGSessionCopyCurrentDictionary.restype = ctypes.c_void_p
    cf.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    cf.CFStringCreateWithCString.restype = ctypes.c_void_p
    cf.CFDictionaryGetValue.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    cf.CFDictionaryGetValue.restype = ctypes.c_void_p
    cf.CFGetTypeID.argtypes = [ctypes.c_void_p]
    cf.CFGetTypeID.restype = ctypes.c_ulong
    cf.CFBooleanGetTypeID.argtypes = []
    cf.CFBooleanGetTypeID.restype = ctypes.c_ulong
    cf.CFBooleanGetValue.argtypes = [ctypes.c_void_p]
    cf.CFBooleanGetValue.restype = ctypes.c_bool
    cf.CFRelease.argtypes = [ctypes.c_void_p]
    cf.CFRelease.restype = None
    return cg, cf


def _session_locked() -> bool | None:  # pragma: no cover - needs a real window-server session
    """The console session's lock state from the window server, if there is a session."""
    try:
        cg, cf = _frameworks()
    except (OSError, AttributeError):
        return None
    session = cg.CGSessionCopyCurrentDictionary()
    if not session:
        return None  # no window-server session (ssh, before login)
    key = cf.CFStringCreateWithCString(None, _LOCKED_KEY.encode(), _UTF8)
    try:
        if not key:
            return None
        value = cf.CFDictionaryGetValue(session, key)
        if not value:
            return False  # the key is there only while locked
        if cf.CFGetTypeID(value) != cf.CFBooleanGetTypeID():
            return None
        return bool(cf.CFBooleanGetValue(value))
    finally:
        if key:
            cf.CFRelease(key)
        cf.CFRelease(session)


def _ioreg_locked() -> bool | None:
    """The lock state from ``ioreg``'s console users; ``None`` when it cannot be told."""
    try:
        # A fixed binary and fixed arguments.
        result = subprocess.run(  # noqa: S603
            [IOREG, "-n", "Root", "-d1", "-a"],
            capture_output=True,
            timeout=15,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return parse_ioreg(result.stdout)


def parse_ioreg(output: bytes) -> bool | None:
    """``CGSSessionScreenIsLocked`` of the console user in ``ioreg -a`` output."""
    try:
        root = plistlib.loads(output)
    except (plistlib.InvalidFileException, ValueError, TypeError):
        return None
    users = root.get("IOConsoleUsers") if isinstance(root, dict) else None
    if not isinstance(users, list) or not users:
        return None
    on_console = [u for u in users if isinstance(u, dict) and u.get("kCGSSessionOnConsoleKey")]
    for user in on_console or [u for u in users if isinstance(u, dict)]:
        return bool(user.get(_LOCKED_KEY, False))
    return None


def is_locked_out(exc: OSError, path: Path | None = None) -> bool:
    """``exc`` is the system refusing a file that is readable only while the Mac is unlocked.

    The error must be ``EPERM`` or ``EACCES`` and the file it names (else
    ``path``) must have protection class A or B. ``path`` may be a folder
    unit: the error then names the file inside that was refused.
    """
    if exc.errno not in (errno.EPERM, errno.EACCES):
        return False
    targets = [] if path is None else [path]
    if exc.filename is not None:
        with contextlib.suppress(TypeError, ValueError):
            targets.insert(0, Path(os.fsdecode(exc.filename)))
    return any(protection_class(target) in LOCKED_CLASSES for target in targets)


def has_locked_files(root: Path) -> bool:
    """Something inside the folder ``root`` is readable only while the Mac is unlocked.

    Reads metadata only; never follows symlinks. Never raises.
    """
    try:
        for dirpath, _dirnames, filenames in os.walk(root, followlinks=False):
            for name in filenames:
                if protection_class(Path(dirpath, name)) in LOCKED_CLASSES:
                    return True
    except OSError:
        return False
    return False

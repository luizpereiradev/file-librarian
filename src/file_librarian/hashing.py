"""Content identity for files and folder units.

A file is identified by the SHA-256 of its bytes. A folder unit (a sub-folder
or a bundle such as ``.app``) is identified by a digest of its manifest: each
file's relative path, size and modification time, each symlink's target, and
each folder's name. That avoids reading gigabytes inside an ``.app`` every day
while still changing whenever a file inside changes. Folder modification times
and the files Finder writes on its own (``.DS_Store``, ``Icon\r``) are left
out, so merely opening a folder in Finder does not change its identity.
Neither function follows symlinks.

Files that exist only in iCloud ("dataless") are recognised from ``lstat``
alone: reading one would make macOS download it.
"""

from __future__ import annotations

import hashlib
import os
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path

_CHUNK = 1 << 20
_UNIT_HEADER = b"file-librarian unit manifest v2\0"
FINDER_FILES = frozenset({".DS_Store", "Icon\r", ".localized"})
# Set on files (and folders) whose contents are only in the cloud; newer Pythons
# expose it as ``stat.SF_DATALESS``.
SF_DATALESS: int = getattr(stat, "SF_DATALESS", 0x40000000)


def is_dataless(st: os.stat_result) -> bool:
    """The item's contents are only in iCloud; opening or listing it would download them."""
    return bool(getattr(st, "st_flags", 0) & SF_DATALESS)


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class UnitManifest:
    digest: str
    entries: int
    size_bytes: int
    newest_mtime: float
    dataless: bool = False
    """Something inside is only in iCloud. The walk stopped there, so the digest is partial."""


def unit_manifest(root: Path) -> UnitManifest:
    """Digest a folder's structure and metadata without reading file contents.

    Stops at the first item that exists only in iCloud, without listing or
    reading it, and returns a manifest marked ``dataless``.
    """
    digest = hashlib.sha256(_UNIT_HEADER)
    entries = 0
    size = 0
    top = os.lstat(root)
    newest = top.st_mtime
    if is_dataless(top):
        return UnitManifest(digest.hexdigest(), entries, size, newest, dataless=True)
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted([*dirnames, *filenames]):
            if name in FINDER_FILES:
                continue
            full = Path(dirpath, name)
            st = os.lstat(full)
            if is_dataless(st):
                return UnitManifest(digest.hexdigest(), entries, size, newest, dataless=True)
            rel = unicodedata.normalize("NFC", str(full.relative_to(root)))
            if stat.S_ISLNK(st.st_mode):
                record = f"l\0{rel}\0{full.readlink()}\n"
            elif stat.S_ISDIR(st.st_mode):
                record = f"d\0{rel}\n"
            else:
                size += st.st_size
                record = f"f\0{rel}\0{st.st_size}\0{st.st_mtime_ns}\n"
            digest.update(record.encode("utf-8", "surrogateescape"))
            entries += 1
            newest = max(newest, st.st_mtime)
    return UnitManifest(digest.hexdigest(), entries, size, newest)


def content_digest(path: Path) -> str:
    """A digest of everything in ``path``, contents included: what a copy must match.

    For a file, its SHA-256. For a folder, every entry's relative path and
    kind, each file's SHA-256 and each symlink's target. Unlike the unit
    manifest it reads every byte and ignores modification times, so it
    compares a copy on another volume (whose file system may keep coarser
    times) with its source. Every entry counts, Finder's own files included.
    """
    if not stat.S_ISDIR(os.lstat(path).st_mode):
        return file_sha256(path)
    digest = hashlib.sha256(b"file-librarian content digest v1\0")
    for dirpath, dirnames, filenames in os.walk(path, followlinks=False):
        dirnames.sort()
        for name in sorted([*dirnames, *filenames]):
            full = Path(dirpath, name)
            st = os.lstat(full)
            rel = unicodedata.normalize("NFC", str(full.relative_to(path)))
            if stat.S_ISLNK(st.st_mode):
                record = f"l\0{rel}\0{full.readlink()}\n"
            elif stat.S_ISDIR(st.st_mode):
                record = f"d\0{rel}\n"
            else:
                record = f"f\0{rel}\0{file_sha256(full)}\n"
            digest.update(record.encode("utf-8", "surrogateescape"))
    return digest.hexdigest()


def same_content(path: Path, sha256: str) -> bool:
    """``path`` exists, is not a symlink, and hashes to ``sha256``."""
    try:
        if not os.path.lexists(path) or path.is_symlink():
            return False
        if path.is_dir():
            return unit_manifest(path).digest == sha256
        return file_sha256(path) == sha256
    except OSError:
        return False

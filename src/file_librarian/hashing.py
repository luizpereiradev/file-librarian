"""Content identity for files and folder units.

A file is identified by the SHA-256 of its bytes. A folder unit (a sub-folder
or a bundle such as ``.app``) is identified by a digest of its manifest: each
file's relative path, size and modification time, each symlink's target, and
each folder's name. That avoids reading gigabytes inside an ``.app`` every day
while still changing whenever a file inside changes. Folder modification times
and the files Finder writes on its own (``.DS_Store``, ``Icon\r``) are left
out, so merely opening a folder in Finder does not change its identity.
Neither function follows symlinks.
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


def unit_manifest(root: Path) -> UnitManifest:
    """Digest a folder's structure and metadata without reading file contents."""
    digest = hashlib.sha256(_UNIT_HEADER)
    entries = 0
    size = 0
    newest = os.lstat(root).st_mtime
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted([*dirnames, *filenames]):
            if name in FINDER_FILES:
                continue
            full = Path(dirpath, name)
            st = os.lstat(full)
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

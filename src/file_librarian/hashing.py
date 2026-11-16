"""Content identity for files and folder units.

A file is identified by the SHA-256 of its bytes. A folder unit (a sub-folder
or a bundle such as ``.app``) is identified by a digest of its manifest: every
entry's relative path, type, size, modification time and symlink target. That
avoids reading gigabytes inside an ``.app`` every day while still changing
whenever anything inside the unit changes. Neither function follows symlinks.
"""

from __future__ import annotations

import hashlib
import os
import stat
import unicodedata
from dataclasses import dataclass
from pathlib import Path

_CHUNK = 1 << 20
_UNIT_HEADER = b"file-librarian unit manifest v1\0"


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
            full = Path(dirpath, name)
            st = os.lstat(full)
            rel = unicodedata.normalize("NFC", str(full.relative_to(root)))
            if stat.S_ISLNK(st.st_mode):
                kind, extra = "l", str(full.readlink())
            elif stat.S_ISDIR(st.st_mode):
                kind, extra = "d", ""
            else:
                kind, extra = "f", ""
                size += st.st_size
            record = f"{kind}\0{rel}\0{st.st_size}\0{st.st_mtime_ns}\0{extra}\n"
            digest.update(record.encode("utf-8", "surrogateescape"))
            entries += 1
            newest = max(newest, st.st_mtime)
    return UnitManifest(digest.hexdigest(), entries, size, newest)

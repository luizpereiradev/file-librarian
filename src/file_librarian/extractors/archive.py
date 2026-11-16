"""Archives: the list of contained file names, never their contents (RF-06).

Nothing is extracted. ZIP listings come from the central directory. TAR
listings walk the headers, which for compressed tarballs means decompressing
the stream, so only tarballs up to ``MAX_TAR_BYTES`` are listed; larger ones
are classified from their name and size alone.
"""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

from file_librarian.extractors import register
from file_librarian.extractors.base import ExtractionError

MAX_ENTRIES = 200
MAX_TAR_BYTES = 512 * 1024 * 1024


def _format(names: list[str], more: bool, max_chars: int) -> str | None:
    if not names:
        return None
    suffix = "\n…" if more else ""
    text = f"Archive contents ({len(names)}{'+' if more else ''} entries):\n"
    return (text + "\n".join(names) + suffix)[:max_chars]


class ZipListing:
    extensions = frozenset({".zip"})

    def extract(self, path: Path, max_chars: int) -> str | None:
        try:
            with zipfile.ZipFile(path) as zf:
                infos = zf.infolist()
        except (zipfile.BadZipFile, OSError, ValueError) as exc:
            raise ExtractionError(f"cannot read ZIP archive: {exc}") from exc
        names = [i.filename for i in infos[:MAX_ENTRIES]]
        return _format(names, len(infos) > MAX_ENTRIES, max_chars)


class TarListing:
    extensions = frozenset({".tar", ".tgz", ".gz", ".bz2", ".xz", ".tbz2", ".txz"})

    def extract(self, path: Path, max_chars: int) -> str | None:
        name = path.name.lower()
        if path.suffix.lower() in {".gz", ".bz2", ".xz"} and ".tar." not in name:
            return None  # a single compressed file, not a tarball
        if path.stat().st_size > MAX_TAR_BYTES:
            return None
        names: list[str] = []
        more = False
        try:
            with tarfile.open(path, mode="r:*") as tf:
                for member in tf:
                    if len(names) == MAX_ENTRIES:
                        more = True
                        break
                    names.append(member.name)
        except (tarfile.TarError, OSError, EOFError, ValueError) as exc:
            raise ExtractionError(f"cannot read tar archive: {exc}") from exc
        return _format(names, more, max_chars)


register(ZipListing())
register(TarListing())

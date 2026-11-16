"""Stage 2: build the ``FileContext`` the classifier may see.

For each candidate this decides what, if anything, of its content goes to the
provider:

1. Folder units send only their name and a listing of entry names (and, for
   ``.app`` bundles, the name and version from ``Info.plist``).
2. Files matching ``never_send`` are classified from metadata only.
3. Otherwise the format's extractor reads up to ``SCAN_WINDOW`` characters.
   Files without an extractor that are small enough are read as text for the
   secret scan only; that text is never sent.
4. With ``sensitive.detect_secrets``, a sensitive name or any secret found in
   that window marks the file ``sensitive``: nothing is sent and it stays put.
5. What remains is cut to ``provider.max_chars_per_file`` for the provider.

Extraction failures never stop a run (RF-27): the file falls back to metadata.
"""

from __future__ import annotations

import os
import plistlib
from dataclasses import replace
from datetime import date
from pathlib import Path

from file_librarian.config import Config
from file_librarian.extractors import ExtractionError, extractor_for
from file_librarian.extractors.origin import download_origin
from file_librarian.models import FileContext
from file_librarian.scanner import Candidate
from file_librarian.sensitive import find_secrets, sensitive_name, withheld_by_config

SCAN_WINDOW = 256_000
SNIFF_LIMIT = 1024 * 1024
UNIT_LISTING = 100


def build_context(candidate: Candidate, config: Config) -> FileContext:
    path = candidate.path
    base = FileContext(
        path=path,
        sha256=candidate.sha256,
        size_bytes=candidate.size_bytes,
        extension=path.suffix.lower(),
        mtime_ns=candidate.mtime_ns,
        inode=candidate.inode,
        downloaded=date.fromtimestamp(candidate.created) if candidate.created else None,
        source_urls=download_origin(path),
        is_unit=candidate.is_unit,
    )
    detect = config.sensitive.detect_secrets

    if candidate.is_unit:
        names, info = _unit_listing(path)
        if detect and any(sensitive_name(Path(n).name) for n in names):
            return replace(base, sensitive=True, metadata={"sensitive": "credential files"})
        return replace(base, text=_describe_unit(names, info, config), metadata=info)

    if detect and sensitive_name(path.name):
        return replace(base, sensitive=True, metadata={"sensitive": "credential file name"})

    if withheld_by_config(path.name, config.never_send):
        return replace(base, metadata={"content": "withheld (never_send)"})

    metadata: dict[str, str] = {}
    extractor = extractor_for(path)
    text: str | None = None
    sendable = True
    try:
        if extractor is not None:
            text = extractor.extract(path, SCAN_WINDOW)
        elif candidate.size_bytes <= SNIFF_LIMIT:
            text = _sniff_text(path)
            sendable = False
    except ExtractionError as exc:
        metadata["extraction_error"] = str(exc)[:200]
    except OSError as exc:
        metadata["extraction_error"] = f"unreadable: {exc.strerror or exc}"[:200]
    except Exception as exc:
        # This is the boundary that keeps one damaged file from stopping every run (RF-27).
        metadata["extraction_error"] = f"{type(exc).__name__}: {exc}"[:200]

    if detect and text:
        kinds = find_secrets(text)
        if kinds:
            return replace(base, sensitive=True, metadata={"sensitive": ", ".join(kinds)})

    sent = text[: config.provider.max_chars_per_file] if (text and sendable) else None
    if extractor is None:
        metadata["content"] = "no extractor for this format"
    elif text is None and "extraction_error" not in metadata:
        metadata["content"] = "no readable text"
    return replace(base, text=sent, metadata=metadata)


def _sniff_text(path: Path) -> str | None:
    with path.open("rb") as fh:
        raw = fh.read(SNIFF_LIMIT)
    if b"\0" in raw[:8192]:
        return None
    return raw.decode("utf-8", errors="replace")


def _unit_listing(root: Path) -> tuple[list[str], dict[str, str]]:
    names: list[str] = []
    info: dict[str, str] = {}
    if root.suffix.lower() == ".app":
        plist = root / "Contents" / "Info.plist"
        try:
            if plist.is_file() and not plist.is_symlink() and plist.stat().st_size < 1_000_000:
                data = plistlib.loads(plist.read_bytes())
                for key in ("CFBundleName", "CFBundleIdentifier", "CFBundleShortVersionString"):
                    value = data.get(key)
                    if isinstance(value, str):
                        info[key] = value[:200]
        except (OSError, plistlib.InvalidFileException, ValueError):
            pass
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            names.append(str(Path(dirpath, name).relative_to(root)))
            if len(names) >= UNIT_LISTING:
                return names, info
    return names, info


def _describe_unit(names: list[str], info: dict[str, str], config: Config) -> str:
    lines = [f"{k}: {v}" for k, v in info.items()]
    if names:
        more = "+" if len(names) >= UNIT_LISTING else ""
        lines.append(f"Folder contents ({len(names)}{more} files):")
        lines.extend(names)
    else:
        lines.append("Empty folder")
    return "\n".join(lines)[: config.provider.max_chars_per_file]

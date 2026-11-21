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
   With ``ocr.enabled`` (the default), an image or a PDF with no text layer
   (scanned) is read on this Mac with macOS's text recognition first (see
   ``extractors/ocr.py``). What it finds is the file's text from here on: the
   secret scan of step 4 sees it, so a screenshot of a password or a key makes
   the file sensitive and nothing is sent. Text with fewer than
   ``ocr.min_chars`` letters and digits (a photo, not a document) is scanned
   but not sent.
6. With ``vision.enabled``, an image, or a PDF with no text layer (scanned),
   is marked ``visual``, unless OCR found enough text: then the text is sent
   instead (cheaper, and it went through the secret scan). The classifier
   sends a downscaled image or the first pages to a provider that reads them
   (see ``extractors/visual.py``). Steps 1, 2 and 4 come first, so
   ``never_send`` and sensitive names hold. Pixels cannot be scanned for
   secrets, which is one reason vision is opt-in.

Extraction failures never stop a run (RF-27): the file falls back to metadata.
"""

from __future__ import annotations

import os
import plistlib
from dataclasses import replace
from datetime import date
from pathlib import Path

from file_librarian.config import Config
from file_librarian.extractors import ExtractionError, extractor_for, ocr
from file_librarian.extractors.origin import download_origin
from file_librarian.extractors.visual import is_image
from file_librarian.models import BundleInfo, FileContext, FileMetadata, VisualKind
from file_librarian.scanner import Candidate
from file_librarian.sensitive import (
    find_secrets,
    name_sensitive,
    sensitive_name,
    sensitive_reason,
    withheld_by_config,
)

SCAN_WINDOW = 256_000
SNIFF_LIMIT = 1024 * 1024
UNIT_LISTING = 100


def build_context_safely(
    candidate: Candidate, config: Config, *, also_named: tuple[str, ...] = ()
) -> FileContext:
    """``build_context``, but nothing about one item can stop the run (RF-27).

    Whatever goes wrong while reading an item, it is classified from its name
    and size alone. This is the per-item boundary the pipeline relies on; the
    narrower handlers inside only make the recorded reason more precise.
    """
    try:
        return build_context(candidate, config, also_named=also_named)
    except Exception as exc:
        return FileContext(
            path=candidate.path,
            sha256=candidate.sha256,
            size_bytes=candidate.size_bytes,
            extension=candidate.path.suffix.lower(),
            mtime_ns=candidate.mtime_ns,
            inode=candidate.inode,
            downloaded=date.fromtimestamp(candidate.created) if candidate.created else None,
            is_unit=candidate.is_unit,
            metadata=FileMetadata(extraction_error=f"{type(exc).__name__}: {exc}"[:200]),
            input=candidate.input,
        )


def build_context(
    candidate: Candidate, config: Config, *, also_named: tuple[str, ...] = ()
) -> FileContext:
    """The context for ``candidate``. ``also_named``: other names the item had (an
    indexed file's original name, for ``flib reprocess``); a sensitive name or a
    ``never_send`` match under any of them counts as under the current one."""
    path = candidate.path
    known_as = (path.name, *also_named)
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
        input=candidate.input,
    )
    detect = config.sensitive.detect_secrets
    allow = config.sensitive.allow_names

    if candidate.is_unit:
        names, bundle = _unit_listing(path)
        inside = next((n for n in names if sensitive_name(Path(n).name, allow)), None)
        if detect and inside is not None:
            reason = sensitive_reason(Path(inside).name, allow)
            return replace(
                base,
                sensitive=True,
                metadata=FileMetadata(
                    sensitive=f"content: holds a file named like {reason}",
                    sensitive_by="content",
                ),
            )
        return replace(
            base, text=_describe_unit(names, bundle, config), metadata=FileMetadata(bundle=bundle)
        )

    named = next((r for n in known_as if (r := sensitive_reason(n, allow))), None)
    if detect and named is not None:
        metadata = FileMetadata(sensitive=name_sensitive(named), sensitive_by="name")
        return replace(base, sensitive=True, metadata=metadata)

    if any(withheld_by_config(n, config.never_send) for n in known_as):
        return replace(base, metadata=FileMetadata(content="withheld (never_send)"))

    error: str | None = None
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
        error = str(exc)[:200]
    except OSError as exc:
        error = f"unreadable: {exc.strerror or exc}"[:200]
    except Exception as exc:
        # This is the boundary that keeps one damaged file from stopping every run (RF-27).
        error = f"{type(exc).__name__}: {exc}"[:200]

    recognized: str | None = None
    ocr_error: str | None = None
    if config.ocr.enabled and error is None and _needs_ocr(base, text):
        try:
            recognized = ocr.recognize(path, config.ocr, SCAN_WINDOW) or None
        except ocr.OcrError as exc:
            ocr_error = str(exc)[:200]

    # Recognised text is scanned for secrets even when too short to be sent.
    scanned = "\n".join(t for t in (text, recognized) if t)
    if detect and scanned:
        kinds = find_secrets(scanned)
        if kinds:
            metadata = FileMetadata(
                sensitive=f"content: looks like {', '.join(kinds)}", sensitive_by="content"
            )
            return replace(base, sensitive=True, metadata=metadata)

    from_ocr = recognized is not None and ocr.enough_text(recognized, config.ocr)
    if from_ocr:
        text, sendable = recognized, True
    sent = text[: config.provider.max_chars_per_file] if (text and sendable) else None
    content: str | None = None
    if from_ocr:
        pass  # the recognised text is the content
    elif extractor is None:
        content = "no extractor for this format"
    elif sent is None and error is None:
        content = "no readable text"
    metadata = FileMetadata(
        content=content,
        extraction_error=error,
        text_source="ocr" if from_ocr else None,
        ocr_error=ocr_error,
    )
    visual = _visual(base, sent, metadata, config)
    return replace(base, text=sent, metadata=metadata, visual=visual)


def _visual(
    base: FileContext, text: str | None, metadata: FileMetadata, config: Config
) -> VisualKind | None:
    """``"image"``, ``"pdf"`` (scanned: readable, but no text) or ``None``."""
    if not config.vision.enabled or metadata.text_source == "ocr":
        return None  # with enough recognised text, the text is sent instead
    if is_image(base.path):
        return "image"
    if base.extension == ".pdf" and text is None and metadata.extraction_error is None:
        return "pdf"
    return None


def _needs_ocr(base: FileContext, text: str | None) -> bool:
    """An image, or a PDF the extractor found no text layer in."""
    return is_image(base.path) or (base.extension == ".pdf" and text is None)


def _sniff_text(path: Path) -> str | None:
    with path.open("rb") as fh:
        raw = fh.read(SNIFF_LIMIT)
    if b"\0" in raw[:8192]:
        return None
    return raw.decode("utf-8", errors="replace")


def _unit_listing(root: Path) -> tuple[list[str], BundleInfo | None]:
    names: list[str] = []
    bundle: BundleInfo | None = None
    if root.suffix.lower() == ".app":
        plist = root / "Contents" / "Info.plist"
        try:
            if plist.is_file() and not plist.is_symlink() and plist.stat().st_size < 1_000_000:
                data = plistlib.loads(plist.read_bytes())

                def string(key: str) -> str | None:
                    value = data.get(key)
                    return value[:200] if isinstance(value, str) else None

                bundle = BundleInfo(
                    name=string("CFBundleName"),
                    identifier=string("CFBundleIdentifier"),
                    version=string("CFBundleShortVersionString"),
                )
        except Exception:  # noqa: S110 - any damaged plist: the bundle is just listed by name
            pass
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirnames.sort()
        for name in sorted(filenames):
            names.append(str(Path(dirpath, name).relative_to(root)))
            if len(names) >= UNIT_LISTING:
                return names, bundle
    return names, bundle


def _describe_unit(names: list[str], bundle: BundleInfo | None, config: Config) -> str:
    lines = [f"{k}: {v}" for k, v in bundle.plist_items()] if bundle else []
    if names:
        more = "+" if len(names) >= UNIT_LISTING else ""
        lines.append(f"Folder contents ({len(names)}{more} files):")
        lines.extend(names)
    else:
        lines.append("Empty folder")
    return "\n".join(lines)[: config.provider.max_chars_per_file]

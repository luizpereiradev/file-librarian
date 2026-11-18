"""E-mail messages saved as ``.eml`` (RFC 5322 / MIME), with the standard library.

What the classifier gets, in this order:

- the subject, who sent and received it, and the date (``YYYY-MM-DD``).
  Addresses keep only their **domain**: ``Power Co <…@power.example>``. The
  part before the ``@`` identifies a person and says little about what the
  message is, so no header line carries it, not even inside a display name
  or the subject;
- the names of the attachments, never their content;
- the body: the ``text/plain`` part, else the ``text/html`` part with its
  tags, scripts and styles removed.

The body is passed on as it is, like the text of any other document, so the
secret scan in ``context.py`` sees it whole (masking addresses in it would
also hide ``user:password@host`` connection strings from that scan). Users
who want no message content sent list ``*.eml`` in ``never_send``.

Defences: at most ``MAX_MESSAGE_BYTES`` are read, parsed incrementally; at
most ``MAX_PARTS`` MIME parts are visited; and anything the ``email``
package raises on damaged input, including a nesting deep enough to exhaust
the stack, becomes ``ExtractionError`` (RF-27).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from email import policy
from email.errors import MessageError
from email.message import EmailMessage
from email.parser import BytesFeedParser
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import cast

from file_librarian.extractors import register
from file_librarian.extractors.base import ExtractionError

MAX_MESSAGE_BYTES = 8 * 1024 * 1024
"""Read cap. Headers and the body come first; past it only attachments are lost."""
MAX_PARTS = 200
MAX_HTML_CHARS = 2_000_000
MAX_ADDRESSES = 5
MAX_ATTACHMENTS = 20
_HEADER_CHARS = 300
_CHUNK = 64 * 1024

# What the email package raises on input it cannot make sense of. RuntimeError
# covers RecursionError (absurd nesting); AssertionError, its internal checks.
_DAMAGED = (
    MessageError,
    ValueError,
    TypeError,
    LookupError,
    AttributeError,
    RuntimeError,
    AssertionError,
)

_ADDRESS = re.compile(r"[\w.+'-]+@([\w-]+(?:\.[\w-]+)+)")
_SPACE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES = re.compile(r"\n\s*\n\s*(?:\n\s*)+")


def _clean(value: str) -> str:
    """One line, at most ``_HEADER_CHARS``, with ``someone@example.com`` as ``…@example.com``."""
    return _ADDRESS.sub(r"…@\1", " ".join(value.split()))[:_HEADER_CHARS]


def _header(msg: EmailMessage, name: str) -> str | None:
    value = msg.get(name)
    return _clean(str(value)) if value else None


def _addresses(msg: EmailMessage, name: str) -> str | None:
    shown: list[str] = []
    for display, address in getaddresses([str(v) for v in msg.get_all(name) or []]):
        domain = address.rpartition("@")[2]
        where = f"…@{domain}" if domain else ""
        display = _clean(display)
        if display and where:
            shown.append(f"{display} <{where}>")
        elif display or where:
            shown.append(display or where)
    if not shown:
        return None
    more = f" (+{len(shown) - MAX_ADDRESSES} more)" if len(shown) > MAX_ADDRESSES else ""
    return ", ".join(shown[:MAX_ADDRESSES]) + more


def _date(msg: EmailMessage) -> str | None:
    raw = _header(msg, "Date")
    if raw is None:
        return None
    try:
        return parsedate_to_datetime(raw).date().isoformat()
    except (ValueError, TypeError):
        return raw[:60]  # not a date we can parse; the model may still read it


def _parts(msg: EmailMessage) -> Iterator[EmailMessage]:
    for count, part in enumerate(msg.walk()):
        if count >= MAX_PARTS:
            return
        yield part


def _attachment_names(msg: EmailMessage) -> list[str]:
    names: list[str] = []
    for part in _parts(msg):
        filename = part.get_filename()
        # A name is just a name: drop any folder a sender put in it.
        name = _clean(PureWindowsPath(PurePosixPath(filename or "").name).name)[:100]
        if name and name not in names:
            names.append(name)
    return names


def _decoded(part: EmailMessage) -> str:
    """The part's text in its declared charset, or UTF-8 when that is unknown or wrong."""
    try:
        return str(part.get_content())
    except _DAMAGED:
        payload = part.get_payload(decode=True)
        return payload.decode("utf-8", errors="replace") if isinstance(payload, bytes) else ""


class _HTMLText(HTMLParser):
    """Visible text of an HTML body: no tags, scripts or styles."""

    _SKIP = frozenset({"script", "style", "head", "title"})
    _BREAK = frozenset({"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "table"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.pieces: list[str] = []
        self._skipping = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skipping += 1
        elif tag in self._BREAK:
            self.pieces.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP and self._skipping:
            self._skipping -= 1
        elif tag in self._BREAK:
            self.pieces.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skipping:
            self.pieces.append(data)


def _strip_html(html: str) -> str:
    parser = _HTMLText()
    parser.feed(html[:MAX_HTML_CHARS])
    parser.close()
    return "".join(parser.pieces)


def _body(msg: EmailMessage, max_chars: int) -> str:
    plain: EmailMessage | None = None
    html: EmailMessage | None = None
    for part in _parts(msg):
        if part.is_multipart() or part.get_filename():
            continue
        if part.get_content_disposition() == "attachment":
            continue
        kind = part.get_content_type()
        if kind == "text/plain" and plain is None:
            plain = part
        elif kind == "text/html" and html is None:
            html = part
    if plain is not None:
        text = _decoded(plain)
    elif html is not None:
        text = _strip_html(_decoded(html))
    else:
        return ""
    # Collapsing white space only shortens it, so a generous cut first loses nothing sent.
    lines = (_SPACE.sub(" ", line).strip() for line in text[: max_chars * 4].splitlines())
    return _BLANK_LINES.sub("\n\n", "\n".join(lines)).strip()


def _read(path: Path) -> EmailMessage:
    parser = BytesFeedParser(policy=policy.default)
    read = 0
    with path.open("rb") as fh:
        while read < MAX_MESSAGE_BYTES:
            chunk = fh.read(min(_CHUNK, MAX_MESSAGE_BYTES - read))
            if not chunk:
                break
            read += len(chunk)
            parser.feed(chunk)
    return cast(EmailMessage, parser.close())  # policy.default builds EmailMessage parts


def _text(msg: EmailMessage, max_chars: int) -> str:
    lines = [
        f"{label}: {value}"
        for label, value in (
            ("Subject", _header(msg, "Subject")),
            ("From", _addresses(msg, "From")),
            ("To", _addresses(msg, "To")),
            ("Cc", _addresses(msg, "Cc")),
            ("Date", _date(msg)),
        )
        if value
    ]
    attachments = _attachment_names(msg)
    if attachments:
        more = ", …" if len(attachments) > MAX_ATTACHMENTS else ""
        lines.append(f"Attachments: {', '.join(attachments[:MAX_ATTACHMENTS])}{more}")
    body = _body(msg, max_chars)
    return "\n\n".join(piece for piece in ("\n".join(lines), body) if piece)


class EmailExtractor:
    extensions = frozenset({".eml"})

    def extract(self, path: Path, max_chars: int) -> str | None:
        try:
            text = _text(_read(path), max_chars)
        except OSError as exc:
            raise ExtractionError(str(exc)) from exc
        except _DAMAGED as exc:
            raise ExtractionError(f"cannot read the message: {exc}") from exc
        return text[:max_chars].strip() or None


register(EmailExtractor())

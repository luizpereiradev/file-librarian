"""HTML built on the server, escaped by construction (#114).

Every value that reaches a page goes through ``el`` (or ``esc``), which
escapes it with ``html.escape``: text, attribute values, everything. Only
``Markup``, what ``el`` itself returns, is put in as is. Nothing here joins
untrusted text into HTML by hand: file names, descriptions, issuers and
search words come from the index, some of it written by the model, and are
data, never markup. ``layout`` fills the page frame in one pass, so a value
that happens to read ``{{csrf}}`` is never expanded.
"""

from __future__ import annotations

import html
import re
from collections.abc import Iterable


class Markup(str):
    """HTML that is already safe: only ``el``, ``esc`` and ``join`` make it."""

    __slots__ = ()


def esc(value: object) -> Markup:
    """``value`` as safe HTML text: escaped unless it already is ``Markup``."""
    if isinstance(value, Markup):
        return value
    return Markup(html.escape(str(value), quote=True))


def join(*parts: object) -> Markup:
    """The parts one after another, each escaped unless it is ``Markup``."""
    return Markup("".join(_children(parts)))


def _children(items: Iterable[object]) -> Iterable[str]:
    for item in items:
        if item is None or item is False:
            continue
        if isinstance(item, str):  # Markup is a str too
            yield esc(item)
        elif isinstance(item, Iterable):
            yield from _children(item)
        else:
            yield esc(item)


_NAME = re.compile(r"[a-z][a-z0-9-]*")
VOID = frozenset({"input", "img", "meta", "link", "br"})


def el(tag: str, *children: object, **attrs: object) -> Markup:
    """``<tag attrs>children</tag>``. ``class_`` is ``class``, ``aria_label`` is
    ``aria-label``; ``True`` is a bare attribute, ``None`` and ``False`` leave it out.
    Tag and attribute names are code, never data: anything else is refused."""
    if not _NAME.fullmatch(tag):
        raise ValueError(f"bad tag {tag!r}")
    parts = [tag]
    for raw, value in attrs.items():
        name = raw.rstrip("_").replace("_", "-")
        if not _NAME.fullmatch(name):
            raise ValueError(f"bad attribute {raw!r}")
        if value is None or value is False:
            continue
        parts.append(name if value is True else f'{name}="{esc(value)}"')
    opening = f"<{' '.join(parts)}>"
    if tag in VOID:
        if children:
            raise ValueError(f"<{tag}> takes no children")
        return Markup(opening)
    return Markup(f"{opening}{join(*children)}</{tag}>")


NAV = (
    ("status", "Status"),
    ("review", "Review"),
    ("search", "Search"),
    ("activity", "Activity"),
    ("due", "Due"),
    ("settings", "Settings"),
)
SOON = frozenset({"review", "settings"})
"""Pages not here yet (#115, #117): shown greyed out, never as a link."""


def nav(current: str) -> Markup:
    items = []
    for name, label in NAV:
        if name in SOON:
            soon = el("span", " (coming soon)", class_="sr-only")
            items.append(el("span", label, soon, class_="soon", aria_disabled="true"))
        else:
            here = "page" if name == current else None
            items.append(el("a", label, href=f"/{name}", aria_current=here))
    return el("nav", *items, aria_label="Pages")


def end_form(csrf: str | None) -> Markup | None:
    """End session, for a page that has the session."""
    if csrf is None:
        return None
    return el(
        "form",
        csrf_field(csrf),
        el("button", "End session", type="submit", class_="quiet"),
        class_="end",
        method="post",
        action="/session/end",
    )


_SLOT = re.compile(r"\{\{(title|nav|main|end)\}\}")
LAYOUT = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{title}} · file-librarian</title>
<link rel="icon" href="/static/icon.png">
<link rel="stylesheet" href="/static/app.css">
</head>
<body>
<a class="skip" href="#main">Skip to content</a>
<header class="top">
  <a class="brand" href="/status"><img src="/static/icon.png" alt="" width="28" height="28"><span>file-librarian</span></a>
  {{nav}}
  {{end}}
</header>
<main id="main" tabindex="-1">
{{main}}
</main>
<footer>Served on this Mac, at 127.0.0.1, while <code>flib ui</code> runs. Names and descriptions come from your files and the AI: they are shown as text only.</footer>
</body>
</html>
"""  # noqa: E501


def layout(title: str, current: str, main: Markup, csrf: str | None) -> bytes:
    """A whole page: the frame, the navigation and ``main``; one pass, no re-expansion.
    ``csrf`` ``None``: a page for a browser without the session (no End session)."""
    slots = {
        "title": esc(title),
        "nav": nav(current),
        "main": esc(main),
        "end": end_form(csrf) or Markup(""),
    }
    return _SLOT.sub(lambda m: slots[m.group(1)], LAYOUT).encode("utf-8")


def csrf_field(csrf: str) -> Markup:
    return el("input", type="hidden", name="csrf", value=csrf)


def prose(text: str) -> Markup:
    """Text where `backticks` mark a command: those parts become ``<code>``."""
    parts = text.split("`")
    if len(parts) % 2 == 0:  # an unpaired backtick: plain text
        return esc(text)
    return join(*(el("code", part) if i % 2 else part for i, part in enumerate(parts)))

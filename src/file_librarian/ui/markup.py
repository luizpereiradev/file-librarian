"""HTML built on the server, escaped by construction (#114).

Every value that reaches a page goes through ``el`` (or ``esc``), which
escapes it with ``html.escape``: text, attribute values, everything. Only
``Markup``, what ``el`` itself returns, is put in as is. Nothing here joins
untrusted text into HTML by hand: file names, descriptions, issuers and
search words come from the index, some of it written by the model, and are
data, never markup. ``layout`` fills the page frame in one pass, so a value
that happens to read ``{{csrf}}`` is never expanded.

Words come from the catalogue (``file_librarian.i18n``, #126) as ``Msg``: ``render``
turns one into HTML in the language of the request, escaping the translation's
text and every parameter alike, so neither a translation nor a value can add
markup; only the catalogue's own `backticks` become ``<code>``, and a backtick in
a value stays a backtick.
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable, Mapping

from file_librarian import i18n
from file_librarian.i18n import Lang, Msg, t


class Markup(str):
    """HTML that is already safe: only ``el``, ``esc`` and ``join`` make it."""

    __slots__ = ()

    def plain_text(self) -> str:
        """The text a reader sees: tags dropped, entities read (for an attribute)."""
        return html.unescape(re.sub(r"<[^>]*>", "", self))


def esc(value: object) -> Markup:
    """``value`` as safe HTML text: escaped unless it already is ``Markup``; a message
    is rendered in the language in use (``render``)."""
    if isinstance(value, Markup):
        return value
    if isinstance(value, Msg):
        return render(value)
    return Markup(html.escape(str(value), quote=True))


def render(msg: Msg, lang: Lang | None = None) -> Markup:
    """A message as HTML in ``lang`` (by default the language in use): the
    translation's text escaped, its `backticks` as ``<code>`` (an unpaired one is
    text), each parameter escaped (another message rendered, ``Markup`` as is)."""
    lang = i18n.current() if lang is None else lang
    text = i18n.template(lang, msg.msg_id, msg.params)
    parts = text.split("`")
    if len(parts) % 2 == 0:
        parts = [text]
    out = []
    for i, part in enumerate(parts):
        filled = _fill(part, msg.params, lang)
        out.append(el("code", filled) if i % 2 else filled)
    return Markup("".join(out))


def _fill(text: str, params: Mapping[str, object], lang: Lang) -> Markup:
    pieces: list[str] = []
    at = 0
    for match in i18n.PLACEHOLDER.finditer(text):
        pieces.append(html.escape(text[at : match.start()], quote=True))
        value = params[match.group(1)]
        if isinstance(value, float):
            raise TypeError("format a number with i18n.formats first")
        pieces.append(render(value, lang) if isinstance(value, Msg) else esc(value))
        at = match.end()
    pieces.append(html.escape(text[at:], quote=True))
    return Markup("".join(pieces))


def attr_text(value: object) -> str:
    """An attribute's value as text: a message in the language in use."""
    if isinstance(value, Msg):  # the catalogue's `backticks` mark code: no code here
        return value.text().replace("`", "")
    if isinstance(value, Markup):
        return value.plain_text()
    return str(value)


def join(*parts: object) -> Markup:
    """The parts one after another, each escaped unless it is ``Markup``."""
    return Markup("".join(_children(parts)))


def _children(items: Iterable[object]) -> Iterable[str]:
    for item in items:
        if item is None or item is False:
            continue
        if isinstance(item, str):  # Markup and Msg are a str too
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
        parts.append(name if value is True else f'{name}="{esc(attr_text(value))}"')
    opening = f"<{' '.join(parts)}>"
    if tag in VOID:
        if children:
            raise ValueError(f"<{tag}> takes no children")
        return Markup(opening)
    return Markup(f"{opening}{join(*children)}</{tag}>")


NAV = (
    ("status", t("nav.status")),
    ("review", t("nav.review")),
    ("search", t("nav.search")),
    ("activity", t("nav.activity")),
    ("due", t("nav.due")),
    ("settings", t("nav.settings")),
)


def nav(current: str) -> Markup:
    items = [
        el("a", label, href=f"/{name}", aria_current="page" if name == current else None)
        for name, label in NAV
    ]
    return el("nav", *items, class_="pages", aria_label=t("nav.label"))


def end_form(csrf: str | None) -> Markup | None:
    """End session, for a page that has the session."""
    if csrf is None:
        return None
    return el(
        "form",
        csrf_field(csrf),
        el("button", t("frame.end_session"), type="submit", class_="quiet"),
        class_="end",
        method="post",
        action="/session/end",
    )


_SLOT = re.compile(r"\{\{(title|nav|main|end|lang|skip|footer)\}\}")
LAYOUT = """<!doctype html>
<html lang="{{lang}}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{title}} · file-librarian</title>
<link rel="icon" href="/static/icon.png">
<link rel="stylesheet" href="/static/app.css">
</head>
<body>
<a class="skip" href="#main">{{skip}}</a>
<header class="top">
  <a class="brand" href="/status"><img src="/static/icon.png" alt="" width="28" height="28"><span>file-librarian</span></a>
  {{nav}}
  {{end}}
</header>
<main id="main" tabindex="-1">
{{main}}
</main>
<footer>{{footer}}</footer>
</body>
</html>
"""  # noqa: E501


def layout(title: str, current: str, main: Markup, csrf: str | None) -> bytes:
    """A whole page: the frame, the navigation and ``main``; one pass, no re-expansion.
    ``csrf`` ``None``: a page for a browser without the session (no End session).
    In the language in use: ``<html lang>`` says which."""
    slots = {
        "title": esc(attr_text(title)),
        "nav": nav(current),
        "main": esc(main),
        "end": end_form(csrf) or Markup(""),
        "lang": esc(i18n.current()),
        "skip": render(t("frame.skip")),
        "footer": render(t("frame.footer")),
    }
    return _SLOT.sub(lambda m: slots[m.group(1)], LAYOUT).encode("utf-8")


_PAGE_SLOT = re.compile(r"\{\{(?:t:([a-z0-9_.]+)|(lang|csrf|messages))\}\}")


def page(
    template: str, csrf: str | None = None, messages: Mapping[str, object] | None = None
) -> str:
    """A packaged page (``ui/pages/``) in the language in use, filled in one pass:
    ``{{t:<id>}}`` is that message rendered, ``{{lang}}`` the language, ``{{csrf}}``
    the session's token, ``{{messages}}`` what ``status.js`` reads (JSON, escaped
    for an attribute)."""
    slots: dict[str, str] = {
        "lang": esc(i18n.current()),
        "csrf": esc(csrf or ""),
        "messages": esc(json.dumps(messages or {}, ensure_ascii=False, separators=(",", ":"))),
    }

    def fill(match: re.Match[str]) -> str:
        if match.group(1) is not None:
            return render(t(match.group(1)))
        return slots[match.group(2)]

    return _PAGE_SLOT.sub(fill, template)


def csrf_field(csrf: str) -> Markup:
    return el("input", type="hidden", name="csrf", value=csrf)


def prose(text: str) -> Markup:
    """Text where `backticks` mark a command: those parts become ``<code>``. A message
    is rendered (``render``): only its translation's backticks count."""
    if isinstance(text, Msg):
        return render(text)
    parts = text.split("`")
    if len(parts) % 2 == 0:  # an unpaired backtick: plain text
        return esc(text)
    return join(*(el("code", part) if i % 2 else part for i, part in enumerate(parts)))

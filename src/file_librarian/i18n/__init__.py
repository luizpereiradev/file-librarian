"""The interface's words in the configured language (#126).

One catalogue (``i18n.messages``): message ids to text, per language. English is
the source and the fallback; Brazilian Portuguese (``pt-BR``) is the other
language. It covers the web interface (``ui/``), the menu bar item's menu and
state line, the command line's output for people and the notifications (#141).
Logs, ``flib doctor``, ``--json`` and every other machine format, the MCP server,
exceptions and the docs stay English.

- ``t(id, **params)`` is a message: a ``Msg``, a ``str`` whose value is the text
  in the language in use when it was made, which keeps its id and parameters so
  that a page renders it again in the language of the page that shows it (an
  outcome kept on the server, a constant made at import). ``ui.markup`` renders
  it, escaping every parameter: a translation is plain text, and only the
  catalogue's own `backticks` become ``<code>``.
- Placeholders are ``{name}``: whole sentences with named parameters, never
  fragments joined, since word order differs. A ``Plural`` picks its form from
  one integer parameter (``by``): ``one`` or ``other``. English: one is 1. pt-BR
  follows CLDR and formal grammar: 0 and 1 are singular ("0 arquivo"), which the
  catalogue never shows: every pt-BR plural with a number has a ``zero`` form
  ("nenhum arquivo"). English keeps "0 files", which reads naturally.
- The language in use is a context variable: the server sets it at the start of
  each request from the running configuration (``use``), so a change on the
  Settings page applies from the next page; the command line sets it once it has
  loaded the configuration (``cli._load``); everything else (tests, the MCP
  server, ``flib doctor``) gets English unless it asks (``using``). An unknown
  language is English. ``language_of`` is the one rule that reads a language from
  a configuration.
- A text written for a machine or a log is made English with ``english``, whatever
  the language in use: a run's log, a message matched by its English words.

``formats`` holds dates, times, numbers and money for display, and the reading
of an amount typed in a form; nothing here depends on the process's locale.
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Literal

Lang = Literal["en", "pt-BR"]
LANGUAGES: tuple[Lang, ...] = ("en", "pt-BR")
DEFAULT: Lang = "en"
NAMES: dict[Lang, str] = {"en": "English", "pt-BR": "Português (Brasil)"}
"""Each language by its own name, as the Settings page lists them."""


@dataclass(frozen=True, slots=True)
class Plural:
    """A message with a form per plural category, chosen by the integer ``by``; ``zero``,
    when given, is the form for exactly 0 ("nenhum arquivo" rather than "0 arquivo")."""

    one: str
    other: str
    by: str = "count"
    zero: str | None = None

    def forms(self) -> tuple[str, ...]:
        return (self.one, self.other) if self.zero is None else (self.zero, self.one, self.other)

    def pick(self, lang: Lang, count: int) -> str:
        if count == 0 and self.zero is not None:
            return self.zero
        return self.one if category(lang, count) == "one" else self.other


Entry = str | Plural

ONE: dict[Lang, frozenset[int]] = {"en": frozenset({1}), "pt-BR": frozenset({0, 1})}
"""Whole numbers whose plural category is ``one`` (CLDR: en ``i = 1 and v = 0``,
pt ``i = 0..1``). Every count in the interface is a whole number."""

PLACEHOLDER = re.compile(r"\{([a-z][a-z0-9_]*)\}")

_LANGUAGE: ContextVar[Lang] = ContextVar("flib_language", default=DEFAULT)


def resolve(value: object) -> Lang:
    """A configured language as one the interface speaks; anything else is English."""
    for lang in LANGUAGES:
        if value == lang:
            return lang
    return DEFAULT


def language_of(config: object) -> Lang:
    """The language a configuration asks for (its ``language``), resolved; English for
    ``None`` (no configuration could be read) or anything else."""
    return resolve(getattr(config, "language", None))


def current() -> Lang:
    return _LANGUAGE.get()


def use(value: object) -> None:
    """Speak ``value`` (resolved) from now on in this thread's context: a request."""
    _LANGUAGE.set(resolve(value))


@contextmanager
def using(value: object) -> Iterator[Lang]:
    """Speak ``value`` (resolved) inside the block."""
    lang = resolve(value)
    token = _LANGUAGE.set(lang)
    try:
        yield lang
    finally:
        _LANGUAGE.reset(token)


def category(lang: Lang, count: int) -> Literal["one", "other"]:
    return "one" if count in ONE[lang] else "other"


def entry(lang: Lang, msg_id: str) -> Entry:
    """The catalogue's entry, falling back to English (a test makes sure it never has to)."""
    from file_librarian.i18n.messages import CATALOGUE

    found = CATALOGUE[lang].get(msg_id)
    if found is None:
        found = CATALOGUE[DEFAULT][msg_id]  # an unknown id is a bug: KeyError
    return found


def template(lang: Lang, msg_id: str, params: Mapping[str, object]) -> str:
    """The text of ``msg_id`` in ``lang`` with its plural form chosen, placeholders unfilled."""
    found = entry(lang, msg_id)
    if isinstance(found, str):
        return found
    count = params[found.by]
    if not isinstance(count, int) or isinstance(count, bool):
        raise TypeError(f"{msg_id}: {found.by} must be a whole number")
    return found.pick(lang, count)


class Msg(str):
    """A message of the catalogue: its text in the language in use when made (so it
    works where a ``str`` is expected), with its id and parameters kept, so that it
    can be rendered again in another language (``text``, ``ui.markup.render``)."""

    msg_id: str
    params: dict[str, object]

    def __new__(cls, msg_id: str, params: Mapping[str, object]) -> Msg:
        kept = dict(params)
        made = super().__new__(cls, plain(current(), msg_id, kept))
        made.msg_id = msg_id
        made.params = kept
        return made

    def text(self, lang: Lang | None = None) -> str:
        """The text in ``lang`` (by default the language in use now), as plain text."""
        return plain(current() if lang is None else lang, self.msg_id, self.params)

    def __reduce__(self) -> tuple[object, ...]:  # pragma: no cover - never pickled
        return (Msg, (self.msg_id, self.params))


def t(msg_id: str, /, **params: object) -> Msg:
    """The message ``msg_id`` with ``params`` (text, whole numbers, other messages)."""
    return Msg(msg_id, params)


def value_text(lang: Lang, value: object) -> str:
    """A parameter as plain text: another message in the same language, else ``str``."""
    if isinstance(value, Msg):
        return value.text(lang)
    in_language = getattr(value, "in_language", None)  # ``formats.later``: a value shown
    if callable(in_language):  # in the language of the message it is in
        return str(in_language(lang))
    plain_text = getattr(value, "plain_text", None)  # ``ui.markup.Markup``: its text
    if callable(plain_text):
        return str(plain_text())
    if isinstance(value, float):
        raise TypeError("format a number with i18n.formats first")
    return str(value)


def plain(lang: Lang, msg_id: str, params: Mapping[str, object]) -> str:
    """The message as plain text in ``lang``: `backticks` kept (they mark a command)."""
    text = template(lang, msg_id, params)
    return PLACEHOLDER.sub(lambda m: value_text(lang, params[m.group(1)]), text)


def english(value: object) -> str:
    """``value`` as English plain text: a message rendered again in English, an
    exception by its message (``str(exc)`` for any other), anything else by ``str``."""
    if isinstance(value, BaseException) and len(value.args) == 1:
        value = value.args[0]
    return value_text(DEFAULT, value) if not isinstance(value, BaseException) else str(value)


def placeholders(text: str) -> set[str]:
    return set(PLACEHOLDER.findall(text))

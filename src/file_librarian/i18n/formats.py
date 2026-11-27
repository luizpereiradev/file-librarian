"""Dates, times, numbers and money as the interface shows them, per language (#126).

Display only: CSV exports, addresses, form values a browser fills (``<input
type=date>``) and the configuration file keep their machine formats. Written out
here rather than taken from the process's locale: ``locale.setlocale`` is
process-wide and not thread-safe, and the server answers in threads.

- English keeps what the pages always showed: ``2026-11-25 22:48``, ``US$ 0.0123``,
  ``BRL 1,234.56``.
- pt-BR: ``25/11/2026 22:48``, a decimal comma and a dot for thousands,
  ``R$ 1.234,56``, ``US$ 12,34`` (other currencies by their code: ``EUR 1.234,56``).

**Reading an amount typed in a form** (``parse_amount``), such as a cost cap: up to
four digits, then optionally one separator, ``.`` or ``,``, and one to four digits
of decimals; in either language both separators are read as the decimal point, so
``3,00`` and ``3.00`` are both three. A separator followed by exactly three digits
is refused when it is the language's thousands separator (``1.234`` in pt-BR,
``1,234`` in English): it could mean a thousand and some or a little over one, and
guessing could save the wrong number. No thousands separators otherwise, no sign,
no exponent, no spaces inside.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal

from file_librarian.fields import exponent, format_amount
from file_librarian.i18n import Lang, current

DECIMAL: dict[Lang, str] = {"en": ".", "pt-BR": ","}
GROUP: dict[Lang, str] = {"en": ",", "pt-BR": "."}
SYMBOLS: dict[Lang, dict[str, str]] = {"en": {}, "pt-BR": {"BRL": "R$", "USD": "US$"}}
"""Currencies shown with a symbol rather than their code."""

WEEKDAYS: dict[Lang, tuple[str, ...]] = {
    "en": ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"),
    "pt-BR": (
        "segunda-feira",
        "terça-feira",
        "quarta-feira",
        "quinta-feira",
        "sexta-feira",
        "sábado",
        "domingo",
    ),
}
SHORT_WEEKDAYS: dict[Lang, tuple[str, ...]] = {
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "pt-BR": ("seg", "ter", "qua", "qui", "sex", "sáb", "dom"),
}
MONTHS: dict[Lang, tuple[str, ...]] = {
    "en": (
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ),
    "pt-BR": (
        "janeiro",
        "fevereiro",
        "março",
        "abril",
        "maio",
        "junho",
        "julho",
        "agosto",
        "setembro",
        "outubro",
        "novembro",
        "dezembro",
    ),
}


def _lang(lang: Lang | None) -> Lang:
    return current() if lang is None else lang


def decimal(value: Decimal | float | int, places: int, lang: Lang | None = None) -> str:
    """``value`` with ``places`` decimals and no thousands separator: ``0.0123``, ``0,0123``."""
    text = f"{Decimal(str(value)):.{places}f}"
    return text.replace(".", DECIMAL[_lang(lang)])


def grouped(value: Decimal, places: int, lang: Lang | None = None) -> str:
    """``value`` with thousands separators: ``1,234.56``, ``1.234,56``."""
    lang = _lang(lang)
    text = f"{value:,.{places}f}"
    return text.translate({ord(","): GROUP[lang], ord("."): DECIMAL[lang]})


def usd(value: float, places: int = 4, lang: Lang | None = None) -> str:
    """A cost flib estimated: ``US$ 0.0123`` (``US$ 0,0123``)."""
    return f"US$ {decimal(value, places, lang)}"


def amount(minor: int, currency: str, lang: Lang | None = None) -> str:
    """An amount read in a document: ``BRL 1,234.56`` in English (as ``flib`` prints it),
    ``R$ 1.234,56`` in pt-BR."""
    lang = _lang(lang)
    if lang == "en":
        return format_amount(minor, currency)
    places = exponent(currency)
    number = grouped(Decimal(minor).scaleb(-places), places, lang)
    return f"{SYMBOLS[lang].get(currency, currency)} {number}"


def day(value: date, lang: Lang | None = None) -> str:
    """A date: ``2026-11-25`` (``25/11/2026``)."""
    if _lang(lang) == "en":
        return value.isoformat()
    return f"{value.day:02d}/{value.month:02d}/{value.year:04d}"


def clock(value: datetime) -> str:
    """A time of day, 24-hour: ``22:48`` in both languages."""
    return f"{value.hour:02d}:{value.minute:02d}"


def moment(value: datetime, lang: Lang | None = None) -> str:
    """A date and time: ``2026-11-25 22:48`` (``25/11/2026 22:48``)."""
    return f"{day(value.date(), lang)} {clock(value)}"


def long_day(value: date, lang: Lang | None = None) -> str:
    """A day as a heading: ``Wednesday, 25 November 2026``
    (``quarta-feira, 25 de novembro de 2026``)."""
    lang = _lang(lang)
    weekday, month = WEEKDAYS[lang][value.weekday()], MONTHS[lang][value.month - 1]
    if lang == "en":
        return f"{weekday}, {value.day:02d} {month} {value.year}"
    return f"{weekday}, {value.day} de {month} de {value.year}"


def weekday_clock(value: datetime, lang: Lang | None = None) -> str:
    """A time this week: ``Wed 22:48`` (``qua 22:48``)."""
    return f"{SHORT_WEEKDAYS[_lang(lang)][value.weekday()]} {clock(value)}"


def amount_input(value: float | None, lang: Lang | None = None) -> str:
    """A cost cap in a form field: every digit the file has, never an exponent, with
    the language's decimal separator; ``parse_amount`` reads it back as it is."""
    if value is None:
        return ""
    return format(Decimal(repr(value)), "f").replace(".", DECIMAL[_lang(lang)])


_TYPED = re.compile(r"([0-9]{1,4})(?:([.,])([0-9]{1,4}))?")


class AmountError(ValueError):
    """Not an amount the form takes: ``ambiguous`` (``1.234`` in pt-BR) or ``invalid``."""

    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def parse_amount(text: str, lang: Lang | None = None) -> Decimal:
    """An amount typed in a form, by the rule in this module's docstring."""
    match = _TYPED.fullmatch(text.strip())
    if match is None:
        raise AmountError("invalid")
    whole, separator, decimals = match.groups()
    if separator is None:
        return Decimal(whole)
    if separator == GROUP[_lang(lang)] and len(decimals) == 3:
        raise AmountError("ambiguous")
    return Decimal(f"{whole}.{decimals}")


def for_scripts(lang: Lang | None = None) -> dict[str, object]:
    """What ``status.js`` needs to show numbers, dates and plurals like the server."""
    from file_librarian.i18n import ONE

    lang = _lang(lang)
    return {
        "lang": lang,
        "one": sorted(ONE[lang]),
        "decimal": DECIMAL[lang],
        "dateOrder": "ymd" if lang == "en" else "dmy",
    }

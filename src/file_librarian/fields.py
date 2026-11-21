"""Structured fields of a document: amount, currency, dates, period and kind (#88).

The model's answer may carry what a document states about money and time
(``providers/prompt.py`` asks for each, empty when the document does not say).
Models write numbers the way the document does, so every value is read
tolerantly here, and a value that cannot be read is dropped: never an
exception, and a bad field never costs the rest of the answer.

- ``amount``: the document's total or amount due, as a decimal string with a
  dot (``"1234.56"``). Locale formats are understood (``1.234,56``,
  ``1,234.56``, ``1 234,56``, ``1'234.56``); a single separator followed by
  exactly three digits is a thousands separator unless the currency has
  three decimals. Negative values, more decimals than the currency has, and
  anything ``privacy.scrub`` would take for an identifier (a run of nine or
  more digits) are dropped. An amount needs a currency: from ``currency``, or
  a symbol or code written with the amount (``R$``, ``US$``, ``€``, ``EUR``);
  a bare ``$`` names no currency.
- ``currency``: an ISO 4217 code (``BRL``, ``USD``); symbols become codes.
- ``due_date`` (like ``document_date``): ``YYYY-MM-DD``; a time after it is
  ignored, ``/`` or ``.`` between year, month and day are accepted.
- ``period``: what the document refers to, ``YYYY-MM`` or ``YYYY``
  (``2026-9``, ``09/2026`` and a full date are read as their month).
- ``kind``: one of ``KINDS``; a plural or a longer name ending in one
  (``utility bill``, ``bank statement``) is read as that kind.

The index stores an amount as an integer in the currency's minor units
(cents), so sums are exact (``to_minor``, ``format_amount``).
"""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import get_args

from file_librarian.models import Kind
from file_librarian.privacy import scrub

KINDS: tuple[str, ...] = get_args(Kind)

# ISO 4217 codes in use, with their number of decimals (2 unless listed below).
_CODES = frozenset(
    """
    AED AFN ALL AMD ANG AOA ARS AUD AWG AZN BAM BBD BDT BGN BHD BIF BMD BND BOB BRL
    BSD BTN BWP BYN BZD CAD CDF CHF CLF CLP CNY COP CRC CUP CVE CZK DJF DKK DOP DZD
    EGP ERN ETB EUR FJD FKP GBP GEL GHS GIP GMD GNF GTQ GYD HKD HNL HTG HUF IDR ILS
    INR IQD IRR ISK JMD JOD JPY KES KGS KHR KMF KPW KRW KWD KYD KZT LAK LBP LKR LRD
    LSL LYD MAD MDL MGA MKD MMK MNT MOP MRU MUR MVR MWK MXN MYR MZN NAD NGN NIO NOK
    NPR NZD OMR PAB PEN PGK PHP PKR PLN PYG QAR RON RSD RUB RWF SAR SBD SCR SDG SEK
    SGD SHP SLE SOS SRD SSP STN SVC SYP SZL THB TJS TMT TND TOP TRY TTD TWD TZS UAH
    UGX USD UYU UYW UZS VES VND VUV WST XAF XCD XOF XPF YER ZAR ZMW ZWG
    """.split()  # noqa: SIM905 - a table is easier to check against the standard
)
_ZERO_DECIMALS = "BIF CLP DJF GNF ISK JPY KMF KRW PYG RWF UGX VND VUV XAF XOF XPF".split()  # noqa: SIM905
_EXPONENTS = {
    **dict.fromkeys(_ZERO_DECIMALS, 0),
    **dict.fromkeys(["BHD", "IQD", "JOD", "KWD", "LYD", "OMR", "TND"], 3),
    **dict.fromkeys(["CLF", "UYW"], 4),
}
# Symbols and words written with amounts. A bare "$" (or "kr") names no one currency.
_SYMBOLS = {
    "R$": "BRL",
    "US$": "USD",
    "U$": "USD",
    "U$S": "USD",
    "USD$": "USD",
    "€": "EUR",
    "£": "GBP",
    "¥": "JPY",
    "₹": "INR",
    "₩": "KRW",
    "₽": "RUB",
    "₺": "TRY",
    "₪": "ILS",
    "₫": "VND",
    "₱": "PHP",
    "₴": "UAH",
    "₦": "NGN",
    "C$": "CAD",
    "CA$": "CAD",
    "CAN$": "CAD",
    "A$": "AUD",
    "AU$": "AUD",
    "NZ$": "NZD",
    "HK$": "HKD",
    "S$": "SGD",
    "MX$": "MXN",
    "ZŁ": "PLN",
    "KČ": "CZK",
    "FT": "HUF",
    "FR.": "CHF",
    "REAL": "BRL",
    "REAIS": "BRL",
    "EURO": "EUR",
    "EUROS": "EUR",
}
MAX_MINOR = 10**14
"""The largest amount kept, in minor units: anything above is not a document's total."""

_NUMBER = re.compile(r"\d(?:[\d.,'\u2019\s]*\d)?")
_SPACES = re.compile(r"[\s'\u2019]")
_DATE = re.compile(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})(?:$|[T\s])")
_PERIOD_YM = re.compile(r"(\d{4})[-/.](\d{1,2})")
_PERIOD_MY = re.compile(r"(\d{1,2})[-/.](\d{4})")
_YEARS = range(1900, 2101)


def exponent(currency: str) -> int:
    """How many decimals the currency's minor unit has (2 for most)."""
    return _EXPONENTS.get(currency, 2)


def clean_currency(value: object) -> str | None:
    """An ISO 4217 code, from a code in any case or a symbol; ``None`` otherwise."""
    if not isinstance(value, str):
        return None
    text = " ".join(value.split()).upper()
    if text in _CODES:
        return text
    return _SYMBOLS.get(text)


def clean_amount(value: object, currency: object = None) -> tuple[str | None, str | None]:
    """``(amount, currency)`` as the answer keeps them: a canonical decimal string and an
    ISO code, or ``None`` for what cannot be read. An amount without a currency, or
    whose own symbol contradicts ``currency``, is dropped; the currency alone stays."""
    code = clean_currency(currency)
    number: Decimal | None = None
    written: str | None = None
    if isinstance(value, int | float) and not isinstance(value, bool):
        number = _from_number(value)
        raw = "" if number is None else f"{number:f}"
    elif isinstance(value, str):
        parsed = _split(value)
        if parsed is None:
            return None, code
        raw, written = parsed
    else:
        return None, code
    if not raw or scrub(raw) != raw:
        return None, code  # a number privacy would hide is an identifier, not an amount
    if written is not None:
        if code is not None and written != code:
            return None, code
        code = written
    if code is None:
        return None, None
    if number is None:
        number = _decimal(raw, exponent(code))
    minor = _minor(number, code)
    if minor is None:
        return None, code
    return format_decimal(minor, code), code


def to_minor(amount: str | None, currency: str | None) -> int | None:
    """A canonical amount (``clean_amount``) in the currency's minor units."""
    if amount is None or currency is None:
        return None
    try:
        return _minor(Decimal(amount), currency)
    except InvalidOperation:
        return None


def format_decimal(minor: int, currency: str) -> str:
    """Minor units as a plain decimal string with the currency's decimals: ``"1234.56"``."""
    places = exponent(currency)
    return f"{Decimal(minor).scaleb(-places):.{places}f}"


def format_amount(minor: int, currency: str) -> str:
    """For people: ``"BRL 1,234.56"``."""
    places = exponent(currency)
    return f"{currency} {Decimal(minor).scaleb(-places):,.{places}f}"


def parse_threshold(text: str) -> Decimal | None:
    """An amount typed on the command line (``100``, ``99.90``, ``1.234,56``), or ``None``."""
    parsed = _split(text)
    if parsed is None or parsed[1] is not None:
        return None
    return _decimal(parsed[0], 2)


def clean_date(value: object) -> str | None:
    """A valid ``YYYY-MM-DD`` (year 1900 to 2100), or ``None``."""
    if not isinstance(value, str):
        return None
    match = _DATE.match(value.strip() + " ")
    if match is None:
        return None
    year, month, day = (int(g) for g in match.groups())
    if year not in _YEARS:
        return None
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def clean_period(value: object) -> str | None:
    """What a document refers to: ``YYYY-MM`` or ``YYYY``, or ``None``."""
    if isinstance(value, int) and not isinstance(value, bool):
        value = str(value)
    if not isinstance(value, str):
        return None
    text = value.strip()
    if re.fullmatch(r"\d{4}", text):
        return text if int(text) in _YEARS else None
    year = month = None
    if match := _PERIOD_YM.match(text):
        year, month = int(match.group(1)), int(match.group(2))
        rest = text[match.end() :]
        if rest and not re.fullmatch(r"[-/.]\d{1,2}(?:[T\s].*)?", rest):
            return None
    elif match := _PERIOD_MY.fullmatch(text):
        month, year = int(match.group(1)), int(match.group(2))
    if year is None or month is None or year not in _YEARS or not 1 <= month <= 12:
        return None
    return f"{year:04d}-{month:02d}"


def clean_kind(value: object) -> str | None:
    """One of ``KINDS``, or ``None``: a plural or a name ending in a kind is read as it."""
    if not isinstance(value, str):
        return None
    words = re.findall(r"[a-z]+", value.casefold())
    if not words:
        return None
    for word in (" ".join(words), words[-1]):
        if word in KINDS:
            return word
        if word.endswith("s") and word[:-1] in KINDS:
            return word[:-1]
    return None


def period_matches(period: str | None, wanted: str) -> bool:
    """``period`` (``YYYY`` or ``YYYY-MM``) falls in ``wanted`` (a year or a month)."""
    if period is None:
        return False
    return period == wanted or (len(wanted) == 4 and period.startswith(wanted + "-"))


# --- reading numbers -------------------------------------------------------------------


def _from_number(value: float) -> Decimal | None:
    try:
        number = Decimal(repr(value)) if isinstance(value, float) else Decimal(value)
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _split(text: str) -> tuple[str, str | None] | None:
    """The number in ``text`` and the currency written around it (``None`` if none).

    ``None`` when there is no number, it is negative, or the words around it
    are not a known currency symbol or code."""
    text = text.strip()
    found = list(_NUMBER.finditer(text))
    if len(found) != 1:
        return None
    match = found[0]
    before, after = text[: match.start()].strip(), text[match.end() :].strip()
    if "-" in before or "(" in before or after.startswith(("-", ")")):
        return None
    before = before.removesuffix("+").strip()
    words = [w for w in (before, after) if w]
    if len(words) > 1:
        return None
    written = None
    if words and words[0] != "$":  # a bare "$": the currency must come from elsewhere
        written = clean_currency(words[0])
        if written is None:
            return None
    return match.group(0), written


def _decimal(raw: str, places: int) -> Decimal | None:
    """``raw`` (digits and separators) as a number, reading the separators by locale."""
    text = _SPACES.sub("", raw)
    if not re.fullmatch(r"[\d.,]+", text):
        return None
    dots, commas = text.count("."), text.count(",")
    if dots and commas:
        point = "." if text.rfind(".") > text.rfind(",") else ","
        grouping = "," if point == "." else "."
        whole, _, fraction = text.rpartition(point)
        if point in whole or not fraction or not _grouped(whole, grouping):
            return None
        return Decimal(whole.replace(grouping, "") + "." + fraction)
    if dots + commas == 0:
        return Decimal(text)
    mark = "." if dots else ","
    if dots + commas > 1:
        return Decimal(text.replace(mark, "")) if _grouped(text, mark) else None
    whole, _, fraction = text.partition(mark)
    if not whole or not fraction:
        return None
    if len(fraction) == 3 and places != 3 and whole != "0" and len(whole) <= 3:
        return Decimal(whole + fraction)  # 1.234 or 1,234: a thousands separator
    return Decimal(whole + "." + fraction)


def _grouped(whole: str, mark: str) -> bool:
    """``whole`` is digits in groups of three after the first, separated by ``mark``."""
    return re.fullmatch(r"\d{1,3}(?:" + re.escape(mark) + r"\d{3})*", whole) is not None


def _minor(number: Decimal | None, currency: str) -> int | None:
    if number is None or number < 0:
        return None
    scaled = number.scaleb(exponent(currency))
    if scaled != scaled.to_integral_value():
        return None
    minor = int(scaled)
    return minor if minor < MAX_MINOR else None

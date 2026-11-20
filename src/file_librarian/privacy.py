"""Keep personal identifiers out of what the model writes about a file (#82).

The description, tags, proposed name and name-choice reason of an answer are
stored in the index, searched, shown by ``flib search``/``flib inspect`` and
the MCP tools, and embedded for semantic search. For an image or a scanned
document the model transcribes what it sees, so an answer can carry a phone
number or a document number that was in the picture.

The prompt asks the model not to copy identifiers (``providers/prompt.py``).
This module is the second barrier: ``scrub`` replaces what still looks like
one with a neutral marker, before the answer is cached or indexed
(``classifier.classify_all`` is the single place answers come through).
``privacy.descriptions: verbatim`` turns this barrier off; the prompt
instruction stays.

What is replaced, most specific first:

- the user and password of a URL (``https://user:pass@host``) -> ``[user]@``;
- secrets (``sensitive.SECRET_PATTERNS``) -> ``[secret]``;
- Pix "copy and paste" payment codes -> ``[payment key]``;
- e-mail addresses (``sensitive.EMAIL_PATTERN``) -> ``[email]``;
- UUIDs, Brazilian CPF and CNPJ (also the alphanumeric CNPJ), RG and US SSN
  shapes, with their punctuation -> ``[id number]``;
- IBANs that pass the ISO 13616 check -> ``[account number]``;
- phone numbers: an international number (``+`` and 8 to 15 digits), a
  Brazilian one with the area code (``(00) 90000-0000``, ``00 90000-0000``,
  ``00900000000``), a North American one (``(000) 000-0000``,
  ``000-000-0000``), and any number of 8 or more digits right after a word
  such as "phone", "tel", "WhatsApp" or "celular" -> ``[phone]``;
- card numbers (13 to 19 digits that pass the Luhn check, in groups of four
  or not) -> ``[card number]``; other groups of four digits, three or more
  groups, -> ``[account number]``;
- any other run of 9 or more digits -> ``[id number]``.

Dates, times, amounts (``R$ 1.234,56``, ``US$ 12,000,000``), version numbers,
years and year ranges, and ordinary numbers are left alone: every phone shape
needs phone-like structure, and the digit runs need 9 or more digits. Names
of people, street addresses and licence plates cannot be told apart from
ordinary words by a pattern; only the prompt keeps them out.

The markers hold no digit and no ``@``, so scrubbing twice changes nothing.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from typing import Literal

from file_librarian.models import Classification
from file_librarian.sensitive import EMAIL_PATTERN, SECRET_PATTERNS

DescriptionsMode = Literal["redact", "verbatim"]

PHONE = "[phone]"
EMAIL = "[email]"
ID_NUMBER = "[id number]"
CARD = "[card number]"
ACCOUNT = "[account number]"
PAYMENT_KEY = "[payment key]"
SECRET = "[secret]"  # noqa: S105 - a marker, not a password
USER = "[user]"

MARKER = re.compile(
    r"\[(?:phone|email|id number|card number|account number|payment key|secret|user)\]"
)

# Lookarounds that keep a number from being cut out of a longer token.
_START = r"(?<![\w.,/+-])"
_END = r"(?![\w/]|[.,-]\d)"

_USERINFO = re.compile(r"\b([a-zA-Z][a-zA-Z0-9+.-]*://)[^/\s@]+@")
_PIX = re.compile(r"\b000201[\w.@/:*+-]{20,}")
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b")
_IDS = (
    # CNPJ, also the alphanumeric one (from 2026): 00.000.000/0000-00
    re.compile(_START + r"[0-9A-Z]{2}\.[0-9A-Z]{3}\.[0-9A-Z]{3}/[0-9A-Z]{4}-\d{2}" + _END),
    # CPF: 000.000.000-00
    re.compile(_START + r"\d{3}\.\d{3}\.\d{3}-\d{2}" + _END),
    # RG: 00.000.000-0 (or -X)
    re.compile(_START + r"\d{1,2}\.\d{3}\.\d{3}-[\dXx]" + _END),
    # US SSN: 000-00-0000
    re.compile(_START + r"\d{3}-\d{2}-\d{4}" + _END),
)
_IBAN = re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){2,7}(?: ?[A-Z0-9]{1,4})?\b")

_PHONE_WORDS = (
    r"phone|telephone|tel|cell|mobile|fax|whatsapp|whats|contact|call"
    r"|telefone|fone|celular|cel|contato|ligue|ramal|zap"
)
_PHONE_AFTER_WORD = re.compile(
    r"(?i)\b(?:" + _PHONE_WORDS + r")\b\.?(?:[ \t]*(?:n[oº°]\.?|number|n[uú]mero))?[ \t]*[:=-]?"
    r"[ \t]*(\+?\(?\d[\d ().-]{6,20}\d)"
)
_PHONES = (
    # +55 11 90000-0000, +1 (555) 010-0000, +44 20 7946 0000
    re.compile(r"(?<![\w+])\+\d{1,3}(?:[ .-]?(?:\(\d{1,4}\)|\d{1,6})){1,5}(?!\d)"),
    # (00) 90000-0000, (011) 3000-0000
    re.compile(r"(?<![\w(])\(0?\d{2}\)[ .-]?9?\d{4}[ .-]?\d{4}(?![\d])"),
    # 00 90000-0000, 00900000000: a Brazilian mobile number with its area code
    re.compile(_START + r"\d{2}[ .-]?9\d{4}[ .-]?\d{4}" + _END),
    # (000) 000-0000 and 000-000-0000, 000.000.0000
    re.compile(r"(?<![\w(])\(\d{3}\)[ .-]?\d{3}[ .-]\d{4}(?!\d)"),
    re.compile(_START + r"\d{3}([.-])\d{3}\1\d{4}" + _END),
)
_GROUPS_OF_FOUR = re.compile(_START + r"\d{4}(?:[ -]\d{4}){2,3}(?:[ -]\d{1,4})?" + _END)
_AMEX = re.compile(_START + r"\d{4}[ -]\d{6}[ -]\d{5}" + _END)
_LONG_RUN = re.compile(_START + r"\d{9,}" + _END)
_THOUSANDS = re.compile(r"\+?\d{1,3}(?:[., ]\d{3})+")
_DATE = re.compile(r"\d{4}[.-]\d{1,2}[.-]\d{1,2}|\d{1,2}[.-]\d{1,2}[.-]\d{4}")


def scrub(text: str) -> str:
    """``text`` with every identifier the module docstring lists replaced by its marker."""
    text = _USERINFO.sub(lambda m: m.group(1) + USER + "@", text)
    for _kind, pattern in SECRET_PATTERNS:
        text = pattern.sub(SECRET, text)
    text = _PIX.sub(PAYMENT_KEY, text)
    text = EMAIL_PATTERN.sub(EMAIL, text)
    text = _UUID.sub(ID_NUMBER, text)
    for pattern in _IDS:
        text = pattern.sub(ID_NUMBER, text)
    text = _IBAN.sub(_iban, text)
    text = _PHONE_AFTER_WORD.sub(_phone_after_word, text)
    text = _PHONES[0].sub(_international, text)
    for pattern in _PHONES[1:]:
        text = pattern.sub(PHONE, text)
    text = _AMEX.sub(_card_or_account, text)
    text = _GROUPS_OF_FOUR.sub(_card_or_account, text)
    return _LONG_RUN.sub(_long_run, text)


def scrub_name(text: str) -> str:
    """A proposed file name without identifiers: removed, not marked (a marker in a
    file name only gets in the way). Empty when nothing else was left."""
    cleaned = MARKER.sub(" ", scrub(text))
    cleaned = re.sub(r"\(\s*\)", " ", cleaned)
    return re.sub(r"\s{2,}", " ", cleaned).strip(" -_.,;:")


def scrub_tags(tags: Iterable[str], *, max_chars: int = 40) -> list[str]:
    """Tags scrubbed one by one; a tag that was only an identifier is dropped."""
    kept: list[str] = []
    for tag in tags:
        cleaned = scrub(tag)
        if not re.search(r"\w", MARKER.sub("", cleaned)):
            continue
        cleaned = cleaned[:max_chars].strip()
        if cleaned and cleaned not in kept:
            kept.append(cleaned)
    return kept


def scrub_answer(answer: Classification) -> Classification:
    """The answer with its free text scrubbed: description, tags, proposed name and reason."""
    description = scrub(answer.description)[:600].strip() or "No description."
    reason = answer.name_choice_reason
    if reason is not None:
        reason = scrub(reason)[:300].strip() or None
    name = scrub_name(answer.suggested_name)[:200] or "untitled"
    update = {
        "description": description,
        "tags": scrub_tags(answer.tags),
        "suggested_name": name,
        "name_choice_reason": reason,
    }
    if all(getattr(answer, k) == v for k, v in update.items()):
        return answer
    return answer.model_copy(update=update)


def scrubber(mode: DescriptionsMode) -> Callable[[Classification], Classification]:
    """What the classifier applies to every answer under ``privacy.descriptions``."""
    return scrub_answer if mode == "redact" else _same


def _same(answer: Classification) -> Classification:
    return answer


# --- checks behind the patterns -------------------------------------------------------


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _iban(match: re.Match[str]) -> str:
    """Only a string that passes the IBAN check (mod 97 = 1) is an account number."""
    value = re.sub(r"\s", "", match.group(0))
    if not 15 <= len(value) <= 34:
        return match.group(0)
    moved = value[4:] + value[:4]
    number = "".join(str(int(c, 36)) for c in moved)
    return ACCOUNT if int(number) % 97 == 1 else match.group(0)


def _phone_after_word(match: re.Match[str]) -> str:
    number = match.group(1)
    if (
        len(_digits(number)) < 8
        or _THOUSANDS.fullmatch(number.strip())
        or _DATE.search(number) is not None
    ):
        return match.group(0)
    start = match.start(1) - match.start(0)
    kept = _at_most_15_digits(number)
    return match.group(0)[:start] + PHONE + number[len(kept) :]


def _international(match: re.Match[str]) -> str:
    """``+`` and 8 to 15 digits. Trailing groups past 15 digits (a year after the number)
    are left in the text; an amount with a sign (``+1.234.567``) is not a phone."""
    text = match.group(0)
    if _THOUSANDS.fullmatch(text):
        return text
    number = _at_most_15_digits(text)
    if len(_digits(number)) < 8:
        return text
    return PHONE + text[len(number) :]


def _at_most_15_digits(number: str) -> str:
    """``number`` without the trailing groups past the 15 digits a phone number has at most."""
    while len(_digits(number)) > 15:
        shorter = re.sub(r"[ .-]?(?:\(\d+\)|\d+)$", "", number)
        if shorter == number:
            break
        number = shorter
    return number


def _luhn(digits: str) -> bool:
    total = 0
    for i, char in enumerate(reversed(digits)):
        n = int(char)
        if i % 2 == 1:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return total % 10 == 0


def _card_or_account(match: re.Match[str]) -> str:
    text = match.group(0)
    groups = re.split(r"[ -]", text)
    # A list of years (2019 2020 2021) is not an account.
    if all(len(g) == 4 and 1900 <= int(g) <= 2099 for g in groups):
        return text
    digits = _digits(text)
    if 13 <= len(digits) <= 19 and _luhn(digits):
        return CARD
    return ACCOUNT


def _long_run(match: re.Match[str]) -> str:
    digits = match.group(0)
    if 13 <= len(digits) <= 19 and _luhn(digits):
        return CARD
    return ID_NUMBER

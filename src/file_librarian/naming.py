"""Turn a model's suggested name into a safe file name (RF-10, RF-32).

Everything the model says is untrusted. A suggested name becomes a single
path component: Unicode NFC, no separators or control characters, no leading
dot, lower case words joined by ``-``, within ``naming.max_length`` characters
and 255 bytes. The original extension is always kept.

When the model judges the original name better than its proposal
(``name_choice: original``), ``original_subject`` makes that name the subject,
so it gets the same pattern, date and sanitising as any other.

Category layouts (#86) name folders after the document's issuer:
``issuer_folder_name`` makes the model's issuer a safe folder name, and
``issuer_matches`` finds the folders that already hold the same organisation.
The planner uses it in the folder the file goes to and, with ``{year}/{issuer}``
and the like, in the sibling folders too, so one company keeps one folder per
place and one spelling across them.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterator
from datetime import date
from pathlib import PurePath

from file_librarian.config import NamingConfig

COMPOUND_SUFFIXES = (".tar.gz", ".tar.bz2", ".tar.xz", ".tar.zst")
_MAX_NAME_BYTES = 255
# A date flib (or someone) already put in front of a name: "2026-11-16_", "2026-11-16 - ".
_LEADING_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}[\s_.-]*")


def split_name(name: str) -> tuple[str, str]:
    """Stem and extension, treating ``.tar.gz`` and friends as one extension."""
    lower = name.lower()
    for suffix in COMPOUND_SUFFIXES:
        if lower.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)], name[-len(suffix) :]
    suffix = PurePath(name).suffix
    if suffix and len(suffix) <= 16 and name != suffix:
        return name[: -len(suffix)], suffix
    return name, ""


def original_subject(name: str) -> str:
    """The subject an original name carries: its stem without a leading ISO date.

    The pattern adds the date again, so ``2026-11-16_report.pdf`` must not
    become ``2026-11-16_2026-11-16-report.pdf``.
    """
    stem, _ = split_name(name)
    return _LEADING_DATE.sub("", stem.strip())


def slugify(text: str) -> str:
    text = unicodedata.normalize("NFC", text).casefold()
    text = "".join(ch if unicodedata.category(ch)[0] in "LN" else " " for ch in text)
    return re.sub(r"\s+", "-", text.strip())


def _fit(stem: str, extension: str, max_chars: int) -> str:
    stem = stem[: max(1, max_chars - len(extension))].rstrip("-_ .")
    while len((stem + extension).encode("utf-8")) > _MAX_NAME_BYTES:
        stem = stem[:-1]
    return stem + extension


def build_name(original: str, subject: str, when: date, naming: NamingConfig) -> str | None:
    """The new file name, or ``None`` when the suggestion yields nothing usable."""
    slug = slugify(subject)
    if not slug:
        return None
    _, extension = split_name(original)
    stem = naming.pattern.format(date=when.isoformat(), subject=slug)
    stem = safe_component(stem)
    if not stem:
        return None
    return _fit(stem, extension, naming.max_length)


def safe_component(name: str) -> str:
    """Make any string a harmless single path component (may return ``""``)."""
    name = unicodedata.normalize("NFC", name)
    name = "".join(
        "-" if ch in "/:\\" or unicodedata.category(ch).startswith("C") else ch for ch in name
    )
    # Repeat until stable: " . .pdf" must not end up as the hidden ".pdf".
    previous = None
    while name != previous:
        previous = name
        name = name.strip().lstrip(".")
    return name


# --- issuer folders (#86) ----------------------------------------------------------

# Legal forms dropped from the end of a name before comparing ("Acme Inc." is
# "Acme"). Only forms that are never an ordinary word: "Co", "Company", "Spa",
# "Me" or "SA" without dots can be part of a name ("Lotus Spa", "Pay Me"), so
# they stay. Matched on the lower-cased, accent-free text, punctuation kept.
_LEGAL_FORM = re.compile(
    r"(?:^|[\s,]+)(?:inc|incorporated|llc|l\.\s?l\.\s?c|llp|ltd|limited|ltda|limitada|corp"
    r"|corporation|gmbh|plc|eireli|s\.\s?a\.\s?s|s\.\s?a|s\s?/\s?a|s\.\s?r\.\s?l"
    r"|s\.\s?p\.\s?a|b\.\s?v|n\.\s?v)\.?\s*$"
)
# A web address used as a brand: "Amazon.com", "Example.com.br" are "Amazon", "Example".
_WEB_SUFFIX = re.compile(r"\.(?:com|net|org)(?:\.[a-z]{2})?$")
MAX_ISSUER_FOLDER = 64
# A folder name is compared loosely only when both are at least this long.
_SIMILAR_MIN_CHARS = 6


def _plain(name: str) -> str:
    """Lower case, accents dropped (NFC or NFD alike), spaces collapsed."""
    text = unicodedata.normalize("NFKD", name.replace("&", " and "))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
    return " ".join(text.split())


def issuer_key(name: str) -> str:
    """``name`` reduced to what tells one organisation from another.

    Case, accents and punctuation go, a trailing web suffix (``.com``) and
    trailing legal forms (``Inc``, ``Ltd``, ``LLC``, ``Corp``, ``GmbH``,
    ``Ltda``, ``S.A.`` with its dots...) are dropped while something is left,
    single letters in a row are joined, and so is a leading "the":
    ``"ACME, Inc."``, ``"Acme Ltda"``, ``"acme.com"`` and ``"acme"`` all give
    ``"acme"``.
    """
    return " ".join(_key_words(name))


def _key_words(name: str) -> list[str]:
    text = _WEB_SUFFIX.sub("", _plain(name).rstrip(" ."))
    while True:
        stripped = _LEGAL_FORM.sub("", text).rstrip(" ,.")
        if stripped == text or not re.search(r"[^\W_]", stripped):
            break
        text = stripped
    words = _issuer_words(text)
    if len(words) > 1 and words[0] == "the":
        words.pop(0)
    while len(words) > 1 and words[-1] == "and":
        words.pop()
    return words


def _issuer_words(text: str) -> list[str]:
    words: list[str] = []
    letters = ""
    for word in re.findall(r"[^\W_]+", text):
        if len(word) == 1 and word.isalpha():
            letters += word
            continue
        if letters:
            words.append(letters)
            letters = ""
        words.append(word)
    if letters:
        words.append(letters)
    return words


def issuer_folder_name(issuer: str) -> str | None:
    """The folder a new issuer gets: its own spelling made a safe folder name (NFC,
    no trailing dots or spaces), or ``None`` when nothing usable is left."""
    name = safe_component(re.sub(r"\s+", " ", issuer)[:MAX_ISSUER_FOLDER]).rstrip(" .")
    if not name or not issuer_key(name):
        return None
    return name


def issuer_matches(issuer: str, folders: list[str]) -> list[str]:
    """The folders among ``folders`` that hold ``issuer``, best tier first.

    Tiers: the same ``issuer_key``; the same key without spaces ("Power Co"
    and "PowerCo"); then a conservative similarity (``_similar_keys``). Only
    the first tier that has any match is returned, every folder in it: the
    caller picks among them (the one a file is already in, the one flib
    made, the oldest). Distinct organisations are never merged on looks
    alone: one different letter (``Acme``/``Acne``), a different number, a
    plural (``Morgan``/``Morgans``) or a different word keeps them apart.
    """
    key = issuer_key(issuer)
    if not key:
        return []
    keyed = [(folder, issuer_key(folder)) for folder in folders]
    same = [folder for folder, other in keyed if other == key]
    if same:
        return same
    compact = {key.replace(" ", ""), "".join(_issuer_words(_plain(issuer)))}
    joined = [
        folder
        for folder, other in keyed
        if other and compact & {other.replace(" ", ""), "".join(_issuer_words(_plain(folder)))}
    ]
    if joined:
        return joined
    close = [folder for folder, other in keyed if other and _similar_keys(key, other)]
    return close if len(close) == 1 else []


def match_issuer(issuer: str, folders: list[str]) -> str | None:
    """The folder among ``folders`` that already holds ``issuer``, if one clearly does
    (the first of ``issuer_matches``, which keeps the order of ``folders``)."""
    found = issuer_matches(issuer, folders)
    return found[0] if found else None


def _similar_keys(a: str, b: str) -> bool:
    """Two issuer keys that only a slip tells apart.

    Both at least ``_SIMILAR_MIN_CHARS`` characters long without spaces, and
    the same but for one letter doubled (``Fabrikkam``) or two neighbouring
    letters swapped (``Nortwhind``). Never a digit, never a substituted or
    added letter: ``Acme`` and ``Acne``, ``Morgan`` and ``Morgans``, are
    different names.
    """
    ca, cb = a.replace(" ", ""), b.replace(" ", "")
    if min(len(ca), len(cb)) < _SIMILAR_MIN_CHARS or ca == cb:
        return False
    if abs(len(ca) - len(cb)) == 1:
        long, short = (ca, cb) if len(ca) > len(cb) else (cb, ca)
        for i, ch in enumerate(long):
            if long[:i] + long[i + 1 :] == short:
                doubled = (i > 0 and long[i - 1] == ch) or (i + 1 < len(long) and long[i + 1] == ch)
                return doubled and ch.isalpha()
        return False
    if len(ca) == len(cb):
        diff = [i for i, (x, y) in enumerate(zip(ca, cb, strict=True)) if x != y]
        return (
            len(diff) == 2
            and diff[1] == diff[0] + 1
            and ca[diff[0]] == cb[diff[1]]
            and ca[diff[1]] == cb[diff[0]]
            and ca[diff[0]].isalpha()
            and ca[diff[1]].isalpha()
        )
    return False


MAX_COUNTER = 999


def candidate_names(name: str) -> Iterator[str]:
    """``name``, then ``name-2`` ... ``name-999``: the order collisions are resolved in."""
    yield name
    for n in range(2, MAX_COUNTER + 1):
        yield with_counter(name, n)


def with_counter(name: str, n: int) -> str:
    """``report.pdf`` -> ``report-2.pdf``, still within 255 bytes."""
    stem, extension = split_name(name)
    suffix = f"-{n}{extension}"
    while stem and len((stem + suffix).encode("utf-8")) > _MAX_NAME_BYTES:
        stem = stem[:-1]
    return f"{stem}{suffix}"

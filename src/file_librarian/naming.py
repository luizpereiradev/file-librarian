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
``match_issuer`` finds the folder that already holds the same organisation,
so one company never ends up in two folders.
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

# Words that say what kind of company it is, not which one: dropped from the end
# of a name before comparing ("Acme Inc." is "Acme"). Dotted forms ("S.A.",
# "L.L.C.") arrive here as one word: single letters in a row are joined first.
LEGAL_SUFFIXES = frozenset(
    {
        "ab", "ag", "bv", "cia", "co", "company", "corp", "corporation", "eireli", "epp",
        "gmbh", "inc", "incorporated", "kg", "limitada", "limited", "llc", "llp", "lp",
        "ltd", "ltda", "me", "nv", "oy", "plc", "pty", "sa", "sarl", "sas", "spa", "srl",
    }
)  # fmt: skip
MAX_ISSUER_FOLDER = 64
# A folder name is compared loosely only when both are at least this long.
_SIMILAR_MIN_CHARS = 6


def issuer_key(name: str) -> str:
    """``name`` reduced to what tells one organisation from another.

    Case, accents and punctuation go, single letters in a row are joined
    (``S.A.`` is ``sa``), a leading "the" and trailing legal suffixes are
    dropped while a word is left: ``"ACME, Inc."``, ``"Acme Ltda"`` and
    ``"acme"`` all give ``"acme"``.
    """
    words = _issuer_words(name)
    if len(words) > 1 and words[0] == "the":
        words.pop(0)
    while len(words) > 1 and (words[-1] in LEGAL_SUFFIXES or words[-1] == "and"):
        words.pop()
    return " ".join(words)


def _issuer_words(name: str) -> list[str]:
    text = unicodedata.normalize("NFKD", name.replace("&", " and "))
    text = "".join(ch for ch in text if not unicodedata.combining(ch)).casefold()
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
    """The folder a new issuer gets: its own spelling made a safe folder name, or
    ``None`` when nothing usable is left."""
    name = safe_component(re.sub(r"\s+", " ", issuer)[:MAX_ISSUER_FOLDER])
    if not name or not issuer_key(name):
        return None
    return name


def match_issuer(issuer: str, folders: list[str]) -> str | None:
    """The folder among ``folders`` that already holds ``issuer``'s files, if any.

    First the same ``issuer_key``, then the same key without spaces ("Power Co"
    and "PowerCo"), and only then a conservative similarity
    (``_similar_keys``), used only when exactly one folder is that close.
    Distinct organisations are never merged on looks alone: one different
    letter (``Acme``/``Acne``), a different number or a different word keeps
    them apart.
    """
    key = issuer_key(issuer)
    if not key:
        return None
    keyed = [(folder, issuer_key(folder)) for folder in folders]
    for folder, other in keyed:
        if other == key:
            return folder
    compact = {key.replace(" ", ""), "".join(_issuer_words(issuer))}
    for folder, other in keyed:
        if other and compact & {other.replace(" ", ""), "".join(_issuer_words(folder))}:
            return folder
    close = [folder for folder, other in keyed if other and _similar_keys(key, other)]
    return close[0] if len(close) == 1 else None


def _similar_keys(a: str, b: str) -> bool:
    """Two issuer keys that only a slip tells apart.

    Both at least ``_SIMILAR_MIN_CHARS`` letters long without spaces, and the
    same words but for one of: a plural ``s`` at the end of a word, one letter
    doubled (``Fabrikkam``), or two neighbouring letters swapped
    (``Nortwhind``). A substituted letter never counts: ``Acme`` and ``Acne``
    are different names.
    """
    ca, cb = a.replace(" ", ""), b.replace(" ", "")
    if min(len(ca), len(cb)) < _SIMILAR_MIN_CHARS or ca == cb:
        return False
    if _singular(a) == _singular(b):
        return True
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


def _singular(key: str) -> str:
    return " ".join(w[:-1] if len(w) > 3 and w.endswith("s") else w for w in key.split())


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

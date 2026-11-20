"""privacy.scrub: identifiers never survive; dates, amounts, versions and numbers do (#82).

Property-style: each seed generates identifiers in every shape the module
knows and ordinary values that look a bit like them, inside digit-free
sentences, so "no digit left" proves an identifier went entirely.
"""

from __future__ import annotations

import random
import re

import pytest

from file_librarian.privacy import (
    MARKER,
    scrub,
    scrub_answer,
    scrub_name,
    scrub_tags,
    scrubber,
)
from tests._helpers import answer

SEEDS = range(40)


def _d(rng: random.Random, n: int) -> str:
    return "".join(rng.choice("0123456789") for _ in range(n))


def _word(rng: random.Random) -> str:
    return "".join(rng.choice("abcdefghijklmnopqrstuvwxyz") for _ in range(rng.randint(3, 9)))


def _luhn_digit(body: str) -> str:
    total = 0
    for i, char in enumerate(reversed(body)):
        n = int(char)
        if i % 2 == 0:
            n = n * 2 - 9 if n > 4 else n * 2
        total += n
    return str((10 - total % 10) % 10)


def _card(rng: random.Random) -> str:
    body = rng.choice("3456") + _d(rng, 14)
    number = body + _luhn_digit(body)
    if rng.random() < 0.5:
        return number
    sep = rng.choice(" -")
    return sep.join(number[i : i + 4] for i in range(0, 16, 4))


def _iban(rng: random.Random) -> str:
    country, bban = rng.choice(["DE", "PT", "BR"]), _d(rng, 18)
    moved = bban + country + "00"
    check = 98 - int("".join(str(int(c, 36)) for c in moved)) % 97
    iban = f"{country}{check:02d}{bban}"
    if rng.random() < 0.5:
        return iban
    return " ".join(iban[i : i + 4] for i in range(0, len(iban), 4))


def _identifiers(rng: random.Random) -> list[str]:
    dd, cc = _d(rng, 2), rng.choice(["1", "44", "55", "351"])
    return [
        f"({dd}) 9{_d(rng, 4)}-{_d(rng, 4)}",
        f"({dd}) {rng.choice('2345')}{_d(rng, 3)}-{_d(rng, 4)}",
        f"{dd} 9{_d(rng, 4)}-{_d(rng, 4)}",
        f"{dd}9{_d(rng, 8)}",
        f"+{cc} {dd} 9{_d(rng, 4)}-{_d(rng, 4)}",
        f"+{cc}{_d(rng, 10)}",
        f"+{cc} ({_d(rng, 3)}) {_d(rng, 3)}-{_d(rng, 4)}",
        f"({_d(rng, 3)}) {_d(rng, 3)}-{_d(rng, 4)}",
        f"{_d(rng, 3)}-{_d(rng, 3)}-{_d(rng, 4)}",
        f"{_d(rng, 3)}.{_d(rng, 3)}.{_d(rng, 4)}",
        f"{_d(rng, 3)}.{_d(rng, 3)}.{_d(rng, 3)}-{_d(rng, 2)}",  # CPF
        f"{_d(rng, 2)}.{_d(rng, 3)}.{_d(rng, 3)}/{_d(rng, 4)}-{_d(rng, 2)}",  # CNPJ
        f"{_d(rng, 2)}.{_d(rng, 3)}.{_d(rng, 3)}-{rng.choice('0123456789X')}",  # RG
        f"{_d(rng, 3)}-{_d(rng, 2)}-{_d(rng, 4)}",  # SSN
        _card(rng),
        f"{_d(rng, 4)} {_d(rng, 4)} {_d(rng, 4)}",
        _iban(rng),
        rng.choice("123456789") + _d(rng, rng.randint(8, 14)),
    ]


@pytest.mark.parametrize("seed", SEEDS)
def test_generated_identifiers_never_survive(seed: int) -> None:
    rng = random.Random(seed)
    for ident in _identifiers(rng):
        text = f"A flyer about {_word(rng)}; reach {ident} today."
        out = scrub(text)
        assert not re.search(r"\d", out), (ident, out)
        assert MARKER.search(out), (ident, out)
        assert scrub(out) == out  # scrubbing twice changes nothing


@pytest.mark.parametrize("seed", SEEDS)
def test_phone_numbers_after_a_word_never_survive(seed: int) -> None:
    rng = random.Random(seed)
    word = rng.choice(["Tel:", "phone", "WhatsApp", "telefone:", "Celular", "fax", "contato"])
    number = rng.choice([f"{_d(rng, 4)}-{_d(rng, 4)}", f"{_d(rng, 5)} {_d(rng, 4)}"])
    out = scrub(f"Shop sign. {word} {number}.")
    assert out == f"Shop sign. {word} [phone]."


@pytest.mark.parametrize("seed", SEEDS)
def test_emails_secrets_and_userinfo_never_survive(seed: int) -> None:
    rng = random.Random(seed)
    user, host = _word(rng), _word(rng)
    out = scrub(
        f"Mail {user}.{_word(rng)}@{host}.example.com; see https://{user}:{_word(rng)}x9@"
        f"{host}.example/x and https://{user}@{host}.example/y; key AKIA{'A' * 16}; "
        f"pix 00020126580014br.gov.bcb.pix0136{_word(rng)}{_d(rng, 20)}; "
        f"id 123e4567-e89b-12d3-a456-{_d(rng, 12)}."
    )
    assert out == (
        f"Mail [email]; see https://[user]@{host}.example/x and https://[user]@{host}.example/y; "
        "key [secret]; pix [payment key]; id [id number]."
    )


@pytest.mark.parametrize("seed", SEEDS)
def test_dates_amounts_versions_and_numbers_survive(seed: int) -> None:
    rng = random.Random(seed)
    year = rng.randint(1990, 2030)
    day, month = rng.randint(1, 28), rng.randint(1, 12)
    values = [
        f"{day:02d}/{month:02d}/{year}",
        f"{year}-{month:02d}-{day:02d}",
        f"{day:02d}.{month:02d}.{year}",
        f"{year}{month:02d}{day:02d}",
        f"{rng.randint(0, 23):02d}:{rng.randint(0, 59):02d}",
        str(year),
        f"{year - rng.randint(1, 9)}-{year}",
        f"{year} {year + 1} {year + 2}",
        f"{year} {year + 1} {year + 2} {year + 3}",
        f"R$ {rng.randint(1, 999)}.{_d(rng, 3)}.{_d(rng, 3)},{_d(rng, 2)}",
        f"US$ {rng.randint(1, 999)},{_d(rng, 3)},{_d(rng, 3)}.{_d(rng, 2)}",
        f"€ {rng.randint(1, 99999)}",
        f"+{rng.randint(1, 999)}.{_d(rng, 3)}.{_d(rng, 3)} views",
        f"v{rng.randint(0, 30)}.{rng.randint(0, 30)}.{rng.randint(0, 99)}",
        f"{rng.randint(0, 30)}.{rng.randint(0, 30)}.{rng.randint(0, 99)}.{rng.randint(0, 999)}",
        f"{rng.randint(1, 99_999_999)}",
        f"{rng.randint(1, 99)}.{rng.randint(1, 99)}%",
        f"{rng.choice([1280, 1920, 3840])}x{rng.choice([720, 1080, 2160])}",
        f"page {rng.randint(1, 99)} of {rng.randint(100, 999)}",
        f"contact on {year}-{month:02d}-{day:02d}",
        f"ISO {rng.randint(1000, 99999)}",
    ]
    for value in values:
        text = f"Invoice {value} for {_word(rng)}."
        assert scrub(text) == text, value


def test_the_flyer_of_the_issue() -> None:
    out = scrub("Flyer of a bakery, contact (00) 90000-0000 (First Last), user@example.com.")
    # The name is the prompt's job: no pattern tells a name from other words.
    assert out == "Flyer of a bakery, contact [phone] (First Last), [email]."


def test_an_iban_that_fails_its_check_is_left() -> None:
    assert scrub("Code AB12 CDEF GHIJ KLMN.") == "Code AB12 CDEF GHIJ KLMN."
    assert scrub("Part RX78 GUND AMXX.") == "Part RX78 GUND AMXX."


def test_numbers_that_fail_luhn_are_ids_not_cards() -> None:
    assert scrub("n 4111111111111112") == "n [id number]"
    assert scrub("n 4111111111111111") == "n [card number]"
    assert scrub("n 4111 1111 1111 1112") == "n [account number]"


def test_a_long_international_number_keeps_what_follows() -> None:
    assert scrub("Call +55 11 90000-0000 2026") == "Call [phone] 2026"
    assert scrub("+12 345") == "+12 345"


def test_tags_that_are_only_identifiers_are_dropped() -> None:
    tags = ["bakery", "(00) 90000-0000", "user@example.com", "call 11 90000-0000", "bakery"]
    assert scrub_tags(tags) == ["bakery", "call [phone]"]
    assert all(len(t) <= 40 for t in scrub_tags(["x" * 36 + " user@example.com"]))


def test_a_proposed_name_loses_identifiers_without_markers() -> None:
    assert scrub_name("Bakery flyer (00) 90000-0000") == "Bakery flyer"
    assert scrub_name("Contact - user@example.com") == "Contact"
    assert scrub_name("00 90000-0000") == ""


def test_scrub_answer_cleans_every_free_text_field() -> None:
    raw = answer(
        name="Flyer 00 90000-0000",
        description="Flyer, contact (00) 90000-0000 or user@example.com.",
        tags=["flyer", "user@example.com"],
        name_choice_reason="The current name has CPF 000.000.000-00.",
    )
    clean = scrub_answer(raw)
    assert clean.description == "Flyer, contact [phone] or [email]."
    assert clean.tags == ["flyer"]
    assert clean.suggested_name == "Flyer"
    assert clean.name_choice_reason == "The current name has CPF [id number]."
    assert (clean.category, clean.confidence) == (raw.category, raw.confidence)


def test_scrub_answer_keeps_a_clean_answer_and_fills_emptied_fields() -> None:
    clean = answer(description="A bakery flyer.", tags=["flyer"])
    assert scrub_answer(clean) is clean
    emptied = scrub_answer(answer(name="user@example.com", description="user@example.com"))
    assert (emptied.suggested_name, emptied.description) == ("untitled", "[email]")


def test_verbatim_keeps_the_answer() -> None:
    raw = answer(description="Contact (00) 90000-0000.")
    assert scrubber("verbatim")(raw) is raw
    assert scrubber("redact")(raw).description == "Contact [phone]."

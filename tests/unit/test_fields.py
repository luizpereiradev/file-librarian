"""Structured fields (#88): amounts, currencies, dates, periods and kinds read tolerantly.

The model writes numbers the way the document does; whatever it writes, the
answer must survive: a value that cannot be read is dropped, never raised.
The property tests draw values from a seeded generator, so a failure is
reproducible.
"""

from __future__ import annotations

import random
import string
from decimal import Decimal
from typing import Any

import pytest
from pydantic import ValidationError

from file_librarian.fields import (
    KINDS,
    clean_amount,
    clean_currency,
    clean_date,
    clean_kind,
    clean_period,
    exponent,
    format_amount,
    format_decimal,
    parse_threshold,
    period_matches,
    to_minor,
)
from file_librarian.models import Classification
from file_librarian.privacy import scrub, scrub_answer
from file_librarian.providers.prompt import normalize_answer

SEED = 88


def _grouped(whole: int, mark: str) -> str:
    return f"{whole:,}".replace(",", mark)


def _styles(minor: int, places: int) -> list[str]:
    """``minor`` written as documents in several locales write it."""
    whole, cents = divmod(minor, 10**places)
    fraction = f"{cents:0{places}d}"
    return [
        f"{whole}.{fraction}",  # what the prompt asks for
        f"{_grouped(whole, ',')}.{fraction}",  # en: 1,234.56
        f"{_grouped(whole, '.')},{fraction}",  # pt-BR, de: 1.234,56
        f"{_grouped(whole, ' ')},{fraction}",  # fr: 1 234,56
        f"{_grouped(whole, chr(0xA0))},{fraction}",  # with a no-break space
        f"{_grouped(whole, chr(0x2019))}.{fraction}",  # ch: 1'234.56 with a typographic apostrophe
        f"{whole},{fraction}",  # 1234,56
    ]


@pytest.mark.parametrize("currency", ["USD", "BRL", "EUR", "GBP"])
def test_every_locale_format_reads_as_the_same_amount(currency: str) -> None:
    rng = random.Random(SEED)
    for _ in range(300):
        minor = rng.choice([rng.randrange(1, 100), rng.randrange(100, 10**6), rng.randrange(10**8)])
        for text in _styles(minor, 2):
            assert clean_amount(text, currency) == (format_decimal(minor, currency), currency), text
            assert to_minor(*clean_amount(text, currency)) == minor


def test_zero_and_three_decimal_currencies() -> None:
    rng = random.Random(SEED)
    for _ in range(200):
        yen = rng.randrange(1, 10**7)
        assert clean_amount(f"¥{yen:,}", None) == (str(yen), "JPY")
        assert clean_amount(_grouped(yen, "."), "JPY") == (str(yen), "JPY")
        dinar = rng.randrange(1, 10**6)
        whole, fils = divmod(dinar, 1000)
        assert clean_amount(f"{whole}.{fils:03d}", "KWD") == (f"{whole}.{fils:03d}", "KWD")
    assert (exponent("JPY"), exponent("KWD"), exponent("USD"), exponent("CLF")) == (0, 3, 2, 4)
    assert clean_amount("5.5", "JPY") == (None, "JPY")  # no yen cents


@pytest.mark.parametrize(
    ("value", "currency", "expected"),
    [
        ("R$ 1.234,56", None, ("1234.56", "BRL")),
        ("US$ 12,000,000", None, ("12000000.00", "USD")),
        ("12 EUR", None, ("12.00", "EUR")),
        ("€ 9,90", "eur", ("9.90", "EUR")),
        ("$ 5", "USD", ("5.00", "USD")),  # a bare $ takes the currency given
        ("1,234", "USD", ("1234.00", "USD")),  # one separator and three digits: thousands
        ("1.234", "BRL", ("1234.00", "BRL")),
        ("0.125", "USD", (None, "USD")),  # more decimals than the currency has
        ("1234.5", "USD", ("1234.50", "USD")),
        (103.91, "USD", ("103.91", "USD")),  # a JSON number
        (5, "JPY", ("5", "JPY")),
        ("0", "USD", ("0.00", "USD")),
        # Dropped: no currency, a contradicting symbol, negative, not one number.
        ("$ 5", None, (None, None)),
        ("1234.56", None, (None, None)),
        ("€12", "USD", (None, "USD")),
        ("-5", "USD", (None, "USD")),
        ("(5.00)", "USD", (None, "USD")),
        ("5 - 6", "USD", (None, "USD")),
        ("about 12", "USD", (None, "USD")),
        ("1.2.3,4,5", "USD", (None, "USD")),
        ("12,34.56", "USD", (None, "USD")),
        ("", "USD", (None, "USD")),
        (True, "USD", (None, "USD")),
        (float("nan"), "USD", (None, "USD")),
        (["1"], "USD", (None, "USD")),
        # An identifier is never an amount: privacy.scrub would hide it.
        ("123456789", "BRL", (None, "BRL")),
        (123456789, "BRL", (None, "BRL")),
        ("4111 1111 1111 1111", "USD", (None, "USD")),
        ("10" * 9, "USD", (None, "USD")),
    ],
)
def test_amounts(value: object, currency: object, expected: tuple[str | None, str | None]) -> None:
    assert clean_amount(value, currency) == expected


def test_garbage_never_raises_and_whatever_survives_is_valid() -> None:
    """Whatever the model writes, the answer validates: bad fields are dropped."""
    rng = random.Random(SEED)
    alphabet = string.digits * 4 + ".,'$€£R -()/:abcXYZ" + chr(0xA0)
    values: list[Any] = [None, 0, -1, 1e30, float("inf"), {}, [], "null"]
    values += ["".join(rng.choice(alphabet) for _ in range(rng.randrange(12))) for _ in range(800)]
    for value in values:
        data = normalize_answer(
            {
                "category": "Finance",
                "confidence": 0.9,
                "amount": value,
                "currency": rng.choice([value, "BRL", "usd", "R$", None, "XXX"]),
                "due_date": value,
                "period": value,
                "kind": value,
                "document_date": value,
            }
        )
        answer = Classification.model_validate(data)  # never raises
        if answer.amount is not None:
            assert to_minor(answer.amount, answer.currency) is not None


def test_a_bad_field_never_costs_the_answer() -> None:
    data = normalize_answer(
        {
            "category": "Finance",
            "suggested_name": "power bill",
            "description": "A bill.",
            "confidence": 0.9,
            "amount": "lots",
            "currency": "BRL",
            "due_date": "2026-02-30",
            "period": "sometime",
            "kind": "spaceship",
            "issuer": "Power Co",
            "document_date": "2026-09-03",
        }
    )
    answer = Classification.model_validate(data)
    assert (answer.amount, answer.currency, answer.due_date, answer.period, answer.kind) == (
        None,
        "BRL",
        None,
        None,
        None,
    )
    assert (answer.issuer, str(answer.document_date), answer.category) == (
        "Power Co",
        "2026-09-03",
        "Finance",
    )


def test_an_amount_needs_its_currency() -> None:
    with pytest.raises(ValidationError, match="currency"):
        Classification(
            category="F", suggested_name="x", description="d", confidence=1, amount="1.00"
        )
    data = normalize_answer({"category": "F", "confidence": 1, "amount": "12.50"})
    assert (data["amount"], data["currency"]) == (None, None)


@pytest.mark.parametrize(
    ("value", "code"),
    [
        ("usd", "USD"),
        (" brl ", "BRL"),
        ("R$", "BRL"),
        ("r$", "BRL"),
        ("US$", "USD"),
        ("€", "EUR"),
        ("£", "GBP"),
        ("¥", "JPY"),
        ("reais", "BRL"),
        ("$", None),  # many currencies use it
        ("kr", None),
        ("XXX", None),
        ("dollars", None),
        ("", None),
        (840, None),
    ],
)
def test_currencies(value: object, code: str | None) -> None:
    assert clean_currency(value) == code


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026-09-20", "2026-09-20"),
        (" 2026-09-20 ", "2026-09-20"),
        ("2026-09-20T10:00:00Z", "2026-09-20"),
        ("2026/9/2", "2026-09-02"),
        ("2026.09.02", "2026-09-02"),
        ("20/09/2026", None),  # ambiguous order: the prompt asks for ISO
        ("2026-02-30", None),
        ("2026-13-01", None),
        ("1850-01-01", None),
        ("2026-09-20xyz", None),
        ("", None),
        (None, None),
        (20260920, None),
    ],
)
def test_dates(value: object, expected: str | None) -> None:
    assert clean_date(value) == expected


def test_every_valid_date_survives() -> None:
    rng = random.Random(SEED)
    for _ in range(500):
        ordinal = rng.randrange(693596, 766645)  # 1900-01-01 .. 2099-12-31
        day = __import__("datetime").date.fromordinal(ordinal)
        assert clean_date(day.isoformat()) == day.isoformat()
        assert clean_date(f"{day.year}/{day.month}/{day.day}") == day.isoformat()
        assert clean_period(day.isoformat()) == day.isoformat()[:7]


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("2026", "2026"),
        (2025, "2025"),
        ("2026-09", "2026-09"),
        ("2026-9", "2026-09"),
        ("2026/09", "2026-09"),
        ("09/2026", "2026-09"),
        ("9-2026", "2026-09"),
        ("2026-09-15", "2026-09"),
        ("2026-13", None),
        ("13/2026", None),
        ("2026-09-xx", None),
        ("Sept 2026", None),
        ("26", None),
        ("", None),
        (True, None),
    ],
)
def test_periods(value: object, expected: str | None) -> None:
    assert clean_period(value) == expected


def test_period_matches_a_year_or_a_month() -> None:
    assert period_matches("2026-09", "2026") and period_matches("2026-09", "2026-09")
    assert period_matches("2026", "2026")
    assert not period_matches("2026", "2026-09")  # a yearly document is not about September
    assert not period_matches("2025-12", "2026") and not period_matches(None, "2026")


@pytest.mark.parametrize(
    ("value", "kind"),
    [
        ("bill", "bill"),
        (" Bills ", "bill"),
        ("utility bill", "bill"),
        ("Bank Statement", "statement"),
        ("insurance policy", "policy"),
        ("tax", "tax"),
        ("tax return", None),
        ("spaceship", None),
        ("", None),
        (None, None),
    ],
)
def test_kinds(value: object, kind: str | None) -> None:
    assert clean_kind(value) == kind


def test_every_kind_is_its_own_answer() -> None:
    for kind in KINDS:
        assert clean_kind(kind) == kind
        assert clean_kind(kind.upper()) == kind


def test_formatting_and_thresholds() -> None:
    assert format_amount(123456, "BRL") == "BRL 1,234.56"
    assert format_amount(1234, "JPY") == "JPY 1,234"
    assert format_decimal(5, "USD") == "0.05"
    assert to_minor("12.30", "USD") == 1230 and to_minor(None, "USD") is None
    assert to_minor("abc", "USD") is None
    assert parse_threshold("1.234,56") == Decimal("1234.56")
    assert parse_threshold("$100") == Decimal(100)
    assert parse_threshold("R$ 100") is None  # the currency goes in --currency
    assert parse_threshold("lots") is None


def test_privacy_leaves_amounts_and_dates_alone() -> None:
    """Amounts and dates are not identifiers: the scrub keeps them in descriptions."""
    text = "Bill of R$ 1.234,56 (US$ 12,000,000.00) due 2026-09-20 for 2026-09, 20/09/2026."
    assert scrub(text) == text
    answer = Classification(
        category="Finance",
        suggested_name="bill",
        description=text,
        confidence=0.9,
        amount="1234.56",
        currency="BRL",
        due_date="2026-09-20",
        period="2026-09",
        kind="bill",
    )
    assert scrub_answer(answer) is answer

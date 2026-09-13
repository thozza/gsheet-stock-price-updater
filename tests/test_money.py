"""Table-driven tests for locale-aware money parsing and currency normalization."""

from __future__ import annotations

from decimal import Decimal

import pytest

from gsheet_stock_price_updater.money import (
    MoneyParseError,
    detect_currency,
    normalize_currency,
    parse_decimal,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Czech: NBSP / space thousands, comma decimal.
        ("1 385,00 Kč", Decimal("1385.00")),
        ("1 385,00 Kč", Decimal("1385.00")),
        ("1 385,00 Kč", Decimal("1385.00")),
        ("12 345,60", Decimal("12345.60")),
        ("1 234 567,89 Kč", Decimal("1234567.89")),
        # US: comma thousands, dot decimal, dollar sign.
        ("$1,385.00", Decimal("1385.00")),
        ("US$ 1,234,567.89", Decimal("1234567.89")),
        # Euro.
        ("€1.234,56", Decimal("1234.56")),
        ("1.234.567,89 €", Decimal("1234567.89")),
        # Plain dotted / comma decimals, no thousands.
        ("1385.00", Decimal("1385.00")),
        ("1385,00", Decimal("1385.00")),
        ("0.5", Decimal("0.5")),
        ("42", Decimal("42")),
        # Swiss apostrophe thousands.
        ("1'385.00", Decimal("1385.00")),
        # Negative and surrounding whitespace.
        ("  -12,50 Kč ", Decimal("-12.50")),
        # European dotted thousands, no decimal.
        ("1.234.567", Decimal("1234567")),
        # US comma thousands, no decimal.
        ("1,234,567", Decimal("1234567")),
    ],
)
def test_parse_decimal(raw: str, expected: Decimal) -> None:
    assert parse_decimal(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "Kč", "abc", "N/A", "--"])
def test_parse_decimal_rejects_non_numeric(raw: str) -> None:
    with pytest.raises(MoneyParseError):
        parse_decimal(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Kč", "CZK"),
        ("Kc", "CZK"),
        ("czk", "CZK"),
        ("$", "USD"),
        ("US$", "USD"),
        ("usd", "USD"),
        ("€", "EUR"),
        ("eur", "EUR"),
        ("£", "GBP"),
        ("gbp", "GBP"),
        # Arbitrary provider-supplied three-letter code passes through.
        ("nok", "NOK"),
    ],
)
def test_normalize_currency(raw: str, expected: str) -> None:
    assert normalize_currency(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "  ",
        "dollars",
        "12",
        "??",
        # Unmapped symbol-suffixed tokens must not collapse onto a mapped symbol.
        "C$",  # CAD, not USD
        "A$",  # AUD, not USD
        "R$",  # BRL, not USD
    ],
)
def test_normalize_currency_rejects_unknown(raw: str) -> None:
    with pytest.raises(MoneyParseError):
        normalize_currency(raw)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1 385,00 Kč", "CZK"),
        ("$1,385.00", "USD"),
        ("€1.234,56", "EUR"),
        ("1'385.00", None),  # no currency token present
        ("1385.00", None),
    ],
)
def test_detect_currency(raw: str, expected: str | None) -> None:
    assert detect_currency(raw) == expected

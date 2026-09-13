"""Locale-aware money parsing and currency normalization.

Money is always a `decimal.Decimal`, never a float, so parsing, storage, and
the value we hand to the sheet are all exact. Prices arrive in mixed formats
(Czech "1 385,00 Kc", US "$1,385.00", plain dotted decimals), so parsing has to
disambiguate thousands vs decimal separators, and currency has to normalize to
an ISO 4217 code.
"""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

# Currency symbols / local tokens mapped to ISO 4217 codes. Ordered longest-key
# first at lookup time so multi-character tokens (US$) win over their substrings.
SYMBOL_TO_ISO: dict[str, str] = {
    "Kc": "CZK",  # ASCII fallback for "K" + "c" with hacek
    "Kč": "CZK",
    "CZK": "CZK",
    "US$": "USD",
    "$": "USD",
    "USD": "USD",
    "€": "EUR",
    "EUR": "EUR",
    "£": "GBP",
    "GBP": "GBP",
    "¥": "JPY",
    "JPY": "JPY",
    "zł": "PLN",
    "PLN": "PLN",
    "CHF": "CHF",
}

# Characters used as thousands separators across locales: regular space, no-break
# space, narrow no-break space, and the Swiss apostrophe.
_THOUSANDS_WHITESPACE = " \u00a0\u202f'"


class MoneyParseError(ValueError):
    """Raised when a string cannot be parsed into a price or a currency."""


def parse_decimal(raw: str) -> Decimal:
    """Parse a localized number string into a `Decimal`.

    Handles space / no-break-space / apostrophe thousands separators and both
    comma and dot decimal separators. When both a comma and a dot are present,
    the rightmost one is the decimal separator and the other is thousands. A
    single separator is treated as the decimal point; a separator that repeats
    is treated as thousands.
    """

    if raw is None:
        raise MoneyParseError("Cannot parse a price from None.")

    # Drop thousands-whitespace and any character that is not a digit, sign, or a
    # comma/dot separator (this strips currency symbols and stray letters).
    stripped = re.sub(f"[{re.escape(_THOUSANDS_WHITESPACE)}]", "", raw.strip())
    cleaned = re.sub(r"[^0-9,.\-]", "", stripped)

    if not re.search(r"\d", cleaned):
        raise MoneyParseError(f"No numeric value found in {raw!r}.")

    has_dot = "." in cleaned
    has_comma = "," in cleaned

    if has_dot and has_comma:
        if cleaned.rfind(",") > cleaned.rfind("."):
            # Comma is the decimal separator; dots are thousands.
            cleaned = cleaned.replace(".", "").replace(",", ".")
        else:
            # Dot is the decimal separator; commas are thousands.
            cleaned = cleaned.replace(",", "")
    elif has_comma:
        # A repeated comma can only be a thousands separator.
        cleaned = cleaned.replace(",", "") if cleaned.count(",") > 1 else cleaned.replace(",", ".")
    elif has_dot and cleaned.count(".") > 1:
        cleaned = cleaned.replace(".", "")

    try:
        return Decimal(cleaned)
    except InvalidOperation as exc:
        raise MoneyParseError(f"Could not parse {raw!r} as a decimal.") from exc


def normalize_currency(raw: str) -> str:
    """Normalize an explicit currency token to an ISO 4217 code.

    Accepts a symbol (Kc, $, EUR) or an ISO code. Any bare three-letter alpha
    token is accepted and upper-cased, so a provider-supplied code (e.g. a
    scraped "EUR") passes through. Raises `MoneyParseError` on anything
    unrecognizable.
    """

    if raw is None:
        raise MoneyParseError("Cannot normalize a currency from None.")

    token = raw.strip()
    if not token:
        raise MoneyParseError("Cannot normalize an empty currency.")

    # Strict, exact match (case-insensitive) against known symbols and codes.
    # Substring matching would mis-map tokens like "C$" (CAD) or "R$" (BRL) onto
    # USD, so require the whole token to equal a known key.
    upper_token = token.upper()
    for symbol, iso in SYMBOL_TO_ISO.items():
        if token == symbol or upper_token == symbol.upper():
            return iso

    if re.fullmatch(r"[A-Za-z]{3}", token):
        return upper_token

    raise MoneyParseError(f"Unrecognized currency: {raw!r}.")


def detect_currency(raw: str) -> str | None:
    """Best-effort currency detection from a full price string.

    Returns the ISO code if a known symbol or code is present, otherwise None so
    the caller can fall back to a currency it knows from elsewhere. Unlike
    `normalize_currency`, this never raises and never guesses from an arbitrary
    three-letter run (which would misfire on scraped prose).
    """

    if not raw:
        return None

    for symbol in sorted(SYMBOL_TO_ISO, key=len, reverse=True):
        if symbol in raw:
            return SYMBOL_TO_ISO[symbol]

    return None

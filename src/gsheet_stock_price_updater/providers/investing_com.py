"""Investing.com provider.

Fetches a configured Investing.com detail URL and extracts the price and
currency via XPaths (defaults in config, since the page structure can shift).
"""

from __future__ import annotations

from curl_cffi import requests as curl_requests
from pydantic import BaseModel, ConfigDict, Field

from ..money import MoneyParseError, normalize_currency, parse_decimal
from . import ProviderError, Quote, RowContext, request_text_impersonated
from ._html import extract_text

DEFAULT_INVESTING_COM_XPATH = "//div[@data-test='instrument-price-last']"
DEFAULT_INVESTING_COM_CURRENCY_XPATH = "//div[@data-test='currency-in-label']/span"


class InvestingComProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    identifier_xpath: str = Field(
        default=DEFAULT_INVESTING_COM_XPATH,
        min_length=1,
        description="XPath to the price on an Investing.com detail page.",
    )
    currency_xpath: str = Field(
        default=DEFAULT_INVESTING_COM_CURRENCY_XPATH,
        min_length=1,
        description="XPath to the currency on an Investing.com detail page.",
    )
    # Investing.com sits behind Cloudflare, which 403s non-browser TLS
    # fingerprints. curl_cffi impersonates a real browser; this selects which one
    # (see curl_cffi's supported targets, e.g. "chrome", "chrome124", "safari").
    impersonate: str = Field(default="chrome", min_length=1)
    timeout_seconds: float = Field(default=15.0, gt=0)


class InvestingComProvider:
    name = "investing-com"

    def __init__(
        self,
        config: InvestingComProviderConfig,
        session: curl_requests.Session,
        max_retries: int,
    ) -> None:
        self._config = config
        self._session = session
        self._max_retries = max_retries

    def fetch(self, row: RowContext) -> Quote:
        if not row.url:
            raise ProviderError("investing-com: missing source URL for the row.")

        # No custom headers: the impersonated session already sends a complete,
        # internally consistent browser header set. Overriding them would risk a
        # mismatch with the impersonated TLS fingerprint.
        html_text = request_text_impersonated(
            self._session,
            row.url,
            headers={},
            timeout=self._config.timeout_seconds,
            max_retries=self._max_retries,
        )

        price_text = extract_text(html_text, self._config.identifier_xpath)
        try:
            price = parse_decimal(price_text)
        except MoneyParseError as exc:
            raise ProviderError(f"investing-com: could not parse price {price_text!r}: {exc}") from exc

        currency_text = extract_text(html_text, self._config.currency_xpath)
        try:
            currency = normalize_currency(currency_text)
        except MoneyParseError as exc:
            raise ProviderError(
                f"investing-com: could not parse currency {currency_text!r}: {exc}"
            ) from exc

        return Quote(price=price, currency=currency)

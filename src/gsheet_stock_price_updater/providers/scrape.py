"""Generic XPath scrape provider.

Makes the tool useful beyond the two built-ins with no new code: point it at a
URL (url column), give it a price XPath (identifier column), and optionally a
currency XPath in provider config. Currency is detected from the price text when
no currency XPath is set; if neither yields a currency the row fails rather than
writing a price with an unknown currency.
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ..money import MoneyParseError, detect_currency, normalize_currency, parse_decimal
from . import DEFAULT_USER_AGENT, ProviderError, Quote, RowContext, request_text
from ._html import extract_text


class ScrapeProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_agent: str = DEFAULT_USER_AGENT
    timeout_seconds: float = Field(default=15.0, gt=0)
    # Optional XPath for the currency. When omitted, the currency is detected
    # from the scraped price text; if neither yields a currency the row fails
    # (rather than writing a price with an unknown currency).
    currency_xpath: str | None = Field(default=None, min_length=1)


class ScrapeProvider:
    name = "scrape"

    def __init__(
        self, config: ScrapeProviderConfig, client: httpx.Client, max_retries: int
    ) -> None:
        self._config = config
        self._client = client
        self._max_retries = max_retries

    def fetch(self, row: RowContext) -> Quote:
        if not row.url:
            raise ProviderError("scrape: missing source URL for the row.")
        price_xpath = row.identifier.strip()
        if not price_xpath:
            raise ProviderError("scrape: missing price XPath (identifier column).")

        headers = {"User-Agent": self._config.user_agent}
        html_text = request_text(
            self._client,
            row.url,
            headers=headers,
            timeout=self._config.timeout_seconds,
            max_retries=self._max_retries,
        )

        price_text = extract_text(html_text, price_xpath)
        try:
            price = parse_decimal(price_text)
        except MoneyParseError as exc:
            raise ProviderError(f"scrape: could not parse price {price_text!r}: {exc}") from exc

        currency = self._resolve_currency(html_text, price_text)
        return Quote(price=price, currency=currency)

    def _resolve_currency(self, html_text: str, price_text: str) -> str:
        if self._config.currency_xpath:
            currency_text = extract_text(html_text, self._config.currency_xpath)
            try:
                return normalize_currency(currency_text)
            except MoneyParseError as exc:
                raise ProviderError(
                    f"scrape: could not normalize currency {currency_text!r}: {exc}"
                ) from exc

        detected = detect_currency(price_text)
        if detected is None:
            raise ProviderError(
                "scrape: could not determine currency from the price text; "
                "set providers.scrape.currency_xpath."
            )
        return detected

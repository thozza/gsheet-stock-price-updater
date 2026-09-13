"""Prague Stock Exchange provider.

Fetches a configured PSE detail URL and extracts the price via an XPath (default
in config, since the page structure can shift). The Czech-formatted number is
normalized to a Decimal and the currency defaults to CZK (mapping "Kc" -> CZK).
"""

from __future__ import annotations

import httpx
from pydantic import BaseModel, ConfigDict, Field

from ..money import MoneyParseError, detect_currency, parse_decimal
from . import DEFAULT_USER_AGENT, ProviderError, Quote, RowContext, request_text
from ._html import extract_text

# A sensible default XPath for the Prague Stock Exchange detail page. Page
# structure shifts over time, so this is overridable per config and per row.
DEFAULT_PSE_XPATH = "/html/body/main/div[3]/div/div[2]/div/div[2]/div[2]/div[2]"


class PseProviderConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    default_xpath: str = DEFAULT_PSE_XPATH
    user_agent: str = DEFAULT_USER_AGENT
    timeout_seconds: float = Field(default=15.0, gt=0)


class PseProvider:
    name = "pse"

    def __init__(
        self, config: PseProviderConfig, client: httpx.Client, max_retries: int
    ) -> None:
        self._config = config
        self._client = client
        self._max_retries = max_retries

    def fetch(self, row: RowContext) -> Quote:
        if not row.url:
            raise ProviderError("pse: missing source URL for the row.")

        headers = {"User-Agent": self._config.user_agent}
        html_text = request_text(
            self._client,
            row.url,
            headers=headers,
            timeout=self._config.timeout_seconds,
            max_retries=self._max_retries,
        )

        price_text = extract_text(html_text, self._config.default_xpath)
        try:
            price = parse_decimal(price_text)
        except MoneyParseError as exc:
            raise ProviderError(f"pse: could not parse price {price_text!r}: {exc}") from exc

        # PSE quotes are in CZK; honor an explicit "Kc" token if present.
        currency = detect_currency(price_text) or "CZK"
        return Quote(price=price, currency=currency)

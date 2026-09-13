"""Provider interface, shared HTTP helpers, and the provider registry.

A provider turns a sheet row into a `Quote` (price + ISO currency). New
providers only need to implement the small `Provider` protocol and get
registered in `build_registry`. Any failure inside a provider raises
`ProviderError`; the runner catches it per row so one bad fetch never aborts the
run or overwrites a previously good price.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol

import httpx
from curl_cffi import requests as curl_requests
from curl_cffi.requests.exceptions import RequestException as CurlRequestError

if TYPE_CHECKING:
    from ..config import Config

# Transient HTTP statuses worth retrying. Everything else fails fast.
RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})

# Ceiling on a single backoff sleep, so a misconfigured max_retries cannot stall
# the run for minutes between attempts.
BACKOFF_CAP_SECONDS = 30.0

# Scraped sites commonly reject requests without a browser-like User-Agent, so
# ship a realistic default. Shared by every provider config.
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


class ProviderError(Exception):
    """Raised for any per-row fetch failure (network, bad status, missing data)."""


@dataclass(frozen=True)
class Quote:
    """A fetched price and its normalized ISO 4217 currency."""

    price: Decimal
    currency: str


@dataclass(frozen=True)
class RowContext:
    """The per-row inputs a provider needs.

    `identifier` is the provider-specific identifier from the identifier column:
    for the scrape provider, the price XPath. `url` is the optional per-row
    source URL used by the pse and scrape providers.
    """

    identifier: str
    url: str | None
    row_number: int


class Provider(Protocol):
    name: str

    def fetch(self, row: RowContext) -> Quote:
        ...


def request_response(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    timeout: float,
    max_retries: int,
    backoff_base: float = 0.5,
) -> httpx.Response:
    """GET `url`, retrying transient failures with exponential backoff.

    Retries on network errors and on retryable HTTP statuses. A non-retryable
    error status fails immediately. Raises `ProviderError` when all attempts are
    exhausted so the caller treats it as a per-row failure.
    """

    last_error: str | None = None
    for attempt in range(max_retries + 1):
        try:
            response = client.get(
                url, headers=headers, timeout=timeout, follow_redirects=True
            )
        except httpx.RequestError as exc:
            last_error = f"network error: {exc!r}"
        else:
            if response.status_code == 200:
                return response
            last_error = f"HTTP {response.status_code}"
            if response.status_code not in RETRYABLE_STATUS:
                raise ProviderError(f"GET {url} failed: {last_error}")

        if attempt < max_retries:
            time.sleep(min(backoff_base * (2**attempt), BACKOFF_CAP_SECONDS))

    raise ProviderError(
        f"GET {url} failed after {max_retries + 1} attempt(s): {last_error}"
    )


def request_text(
    client: httpx.Client,
    url: str,
    *,
    headers: dict[str, str],
    timeout: float,
    max_retries: int,
) -> str:
    """GET and return the response body text."""

    response = request_response(
        client, url, headers=headers, timeout=timeout, max_retries=max_retries
    )
    return response.text


def request_text_impersonated(
    session: curl_requests.Session,
    url: str,
    *,
    headers: dict[str, str],
    timeout: float,
    max_retries: int,
    backoff_base: float = 0.5,
) -> str:
    """GET `url` via a browser-impersonating curl_cffi session, returning text.

    Mirrors `request_response`'s retry policy (transient statuses and network
    errors, exponential backoff), but uses curl_cffi so the TLS/HTTP fingerprint
    matches a real browser. Some sources (e.g. investing.com behind Cloudflare)
    return 403 to non-browser clients like httpx regardless of headers, so plain
    `request_text` is not enough for them.
    """

    last_error: str | None = None
    for attempt in range(max_retries + 1):
        try:
            response = session.get(
                url, headers=headers, timeout=timeout, allow_redirects=True
            )
        except CurlRequestError as exc:
            last_error = f"network error: {exc!r}"
        else:
            if response.status_code == 200:
                return response.text
            last_error = f"HTTP {response.status_code}"
            if response.status_code not in RETRYABLE_STATUS:
                raise ProviderError(f"GET {url} failed: {last_error}")

        if attempt < max_retries:
            time.sleep(min(backoff_base * (2**attempt), BACKOFF_CAP_SECONDS))

    raise ProviderError(
        f"GET {url} failed after {max_retries + 1} attempt(s): {last_error}"
    )


def build_registry(
    config: Config,
    client: httpx.Client,
    impersonate_session: curl_requests.Session,
) -> dict[str, Provider]:
    """Construct every provider.

    pse and scrape share the plain httpx `client`; investing.com uses the
    browser-impersonating curl_cffi `impersonate_session` to get past Cloudflare.
    """

    # Imported here to avoid a circular import at module load time.
    from .investing_com import InvestingComProvider
    from .pse import PseProvider
    from .scrape import ScrapeProvider

    return {
        PseProvider.name: PseProvider(config.providers.pse, client, config.max_retries),
        ScrapeProvider.name: ScrapeProvider(config.providers.scrape, client, config.max_retries),
        InvestingComProvider.name: InvestingComProvider(
            config.providers.investing_com, impersonate_session, config.max_retries
        ),
    }

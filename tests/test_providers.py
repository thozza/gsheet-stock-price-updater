"""Tests for the pse and scrape providers against recorded fixtures.

No live network: every request is served by an httpx.MockTransport handler.
"""

from __future__ import annotations

from collections.abc import Callable
from decimal import Decimal

import httpx
import pytest

from gsheet_stock_price_updater import providers as providers_pkg
from gsheet_stock_price_updater.config import Config
from gsheet_stock_price_updater.providers import ProviderError, RowContext, build_registry
from gsheet_stock_price_updater.providers.investing_com import (
    InvestingComProvider,
    InvestingComProviderConfig,
)
from gsheet_stock_price_updater.providers.pse import PseProvider, PseProviderConfig
from gsheet_stock_price_updater.providers.scrape import ScrapeProvider, ScrapeProviderConfig

from .conftest import load_fixture

ClientFactory = Callable[[Callable[[httpx.Request], httpx.Response]], httpx.Client]


def _row(identifier: str = "", url: str | None = None) -> RowContext:
    return RowContext(identifier=identifier, url=url, row_number=2)


# --------------------------------------------------------------------------- #
# PSE
# --------------------------------------------------------------------------- #
def test_pse_happy_path(make_client: ClientFactory) -> None:
    body = load_fixture("pse_cez.html")
    provider = PseProvider(
        PseProviderConfig(), make_client(lambda r: httpx.Response(200, text=body)), 0
    )
    quote = provider.fetch(_row(url="https://www.pse.cz/detail/CZ0005112300"))

    assert quote.price == Decimal("1385.00")
    assert quote.currency == "CZK"


@pytest.mark.parametrize(
    ("body", "url", "match"),
    [
        pytest.param("", None, "missing source URL", id="missing-url"),
        pytest.param(
            "<html><body>no price</body></html>",
            "https://www.pse.cz/detail/X",
            "matched no usable text",
            id="price-not-found",
        ),
    ],
)
def test_pse_error_raises(
    make_client: ClientFactory, body: str, url: str | None, match: str
) -> None:
    provider = PseProvider(
        PseProviderConfig(), make_client(lambda r: httpx.Response(200, text=body)), 0
    )
    with pytest.raises(ProviderError, match=match):
        provider.fetch(_row(url=url))


# --------------------------------------------------------------------------- #
# Scrape
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("config", "identifier", "expected_price", "expected_currency"),
    [
        pytest.param(
            ScrapeProviderConfig(),
            '//*[@id="price"]/text()',
            Decimal("1234.56"),
            "USD",
            id="detect-currency-from-price",
        ),
        pytest.param(
            ScrapeProviderConfig(currency_xpath='//*[@id="currency"]/text()'),
            '//*[@id="bare-price"]/text()',
            Decimal("1999.00"),
            "USD",
            id="currency-from-xpath",
        ),
    ],
)
def test_scrape_happy_path(
    make_client: ClientFactory,
    config: ScrapeProviderConfig,
    identifier: str,
    expected_price: Decimal,
    expected_currency: str,
) -> None:
    body = load_fixture("scrape_generic.html")
    provider = ScrapeProvider(config, make_client(lambda r: httpx.Response(200, text=body)), 0)
    quote = provider.fetch(_row(identifier=identifier, url="https://acme.test"))

    assert quote.price == expected_price
    assert quote.currency == expected_currency


@pytest.mark.parametrize(
    ("config", "identifier", "url", "match"),
    [
        pytest.param(
            ScrapeProviderConfig(), '//*[@id="price"]', None, "missing source URL", id="missing-url"
        ),
        pytest.param(
            ScrapeProviderConfig(),
            '//*[@id="bare-price"]/text()',
            "https://acme.test",
            "could not determine currency",
            id="no-currency-signal",
        ),
        # A scalar XPath (count/number/boolean) must be a per-row ProviderError,
        # never a bare AttributeError that aborts the run.
        pytest.param(
            ScrapeProviderConfig(),
            "count(//span)",
            "https://acme.test",
            "returned a scalar",
            id="scalar-xpath",
        ),
    ],
)
def test_scrape_error_raises(
    make_client: ClientFactory,
    config: ScrapeProviderConfig,
    identifier: str,
    url: str | None,
    match: str,
) -> None:
    body = load_fixture("scrape_generic.html")
    provider = ScrapeProvider(config, make_client(lambda r: httpx.Response(200, text=body)), 0)
    with pytest.raises(ProviderError, match=match):
        provider.fetch(_row(identifier=identifier, url=url))


# --------------------------------------------------------------------------- #
# Investing.com
# --------------------------------------------------------------------------- #
def test_investing_com_happy_path(make_client: ClientFactory) -> None:
    body = load_fixture("investing_com_quote.html")
    provider = InvestingComProvider(
        InvestingComProviderConfig(), make_client(lambda r: httpx.Response(200, text=body)), 0
    )
    quote = provider.fetch(_row(url="https://www.investing.com/equities/acme-corp"))

    assert quote.price == Decimal("142.37")
    assert quote.currency == "USD"


@pytest.mark.parametrize(
    ("config", "body", "url", "match"),
    [
        pytest.param(
            InvestingComProviderConfig(), "", None, "missing source URL", id="missing-url"
        ),
        pytest.param(
            InvestingComProviderConfig(),
            "<html><body>no price</body></html>",
            "https://www.investing.com/equities/acme-corp",
            "matched no usable text",
            id="price-not-found",
        ),
        pytest.param(
            InvestingComProviderConfig(currency_xpath="//div[@data-test='currency-bad']/span"),
            load_fixture("investing_com_quote.html"),
            "https://www.investing.com/equities/acme-corp",
            "could not parse currency",
            id="unrecognized-currency",
        ),
    ],
)
def test_investing_com_error_raises(
    make_client: ClientFactory,
    config: InvestingComProviderConfig,
    body: str,
    url: str | None,
    match: str,
) -> None:
    provider = InvestingComProvider(
        config, make_client(lambda r: httpx.Response(200, text=body)), 0
    )
    with pytest.raises(ProviderError, match=match):
        provider.fetch(_row(url=url))


# --------------------------------------------------------------------------- #
# Shared HTTP retry + registry
# --------------------------------------------------------------------------- #
def test_retry_then_success(
    make_client: ClientFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(providers_pkg.time, "sleep", lambda _s: None)
    body = load_fixture("pse_cez.html")
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            return httpx.Response(503, text="temporarily down")
        return httpx.Response(200, text=body)

    provider = PseProvider(PseProviderConfig(), make_client(handler), max_retries=1)
    quote = provider.fetch(_row(url="https://www.pse.cz/detail/CZ0005112300"))
    assert quote.currency == "CZK"
    assert attempts["n"] == 2


def test_build_registry_has_all_providers(make_client: ClientFactory) -> None:
    config = Config.model_validate(
        {
            "spreadsheet": "id",
            "worksheet": "Sheet1",
            "first_data_row": 2,
            "columns": {
                "identifier": "Symbol",
                "price_out": "Price",
                "currency_out": "Currency",
            },
        }
    )
    registry = build_registry(config, make_client(lambda r: httpx.Response(200, text="{}")))
    assert set(registry) == {"pse", "scrape", "investing-com"}

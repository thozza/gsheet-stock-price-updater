"""Shared test helpers: fixture loading and a mock HTTP client."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"


def load_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


@pytest.fixture
def make_client() -> Callable[[Callable[[httpx.Request], httpx.Response]], httpx.Client]:
    """Return a factory that builds an httpx.Client backed by a mock handler."""

    def factory(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(handler))

    return factory


@pytest.fixture
def make_impersonate_client() -> Callable[[Callable[[str], tuple[int, str]]], Any]:
    """Return a factory that builds a fake curl_cffi session from a handler.

    The handler maps a requested URL to a (status_code, text) pair; the fake
    session exposes just the `.get(...) -> response(.status_code, .text)` shape
    that the investing.com provider and `request_text_impersonated` rely on.
    """

    def factory(handler: Callable[[str], tuple[int, str]]) -> Any:
        class _FakeSession:
            def get(
                self,
                url: str,
                *,
                headers: dict[str, str] | None = None,
                timeout: float | None = None,
                allow_redirects: bool = True,
            ) -> SimpleNamespace:
                status_code, text = handler(url)
                return SimpleNamespace(status_code=status_code, text=text)

        return _FakeSession()

    return factory

"""Tests for config loading and validation."""

from __future__ import annotations

import os
import textwrap
from pathlib import Path

import pytest

from gsheet_stock_price_updater.config import ConfigError, load_config

VALID_CONFIG = """
spreadsheet: "https://docs.google.com/spreadsheets/d/ABC/edit"
worksheet: "Portfolio"
header_row: 1
first_data_row: 2
columns:
  identifier: "Symbol"
  provider: "Provider"
  url: "Source URL"
  price_out: "Price"
  currency_out: "Currency"
  updated_at: "Updated At"
"""


def _write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "config.yaml"
    path.write_text(textwrap.dedent(content), encoding="utf-8")
    return path


def test_load_valid_config(tmp_path: Path) -> None:
    config = load_config(_write(tmp_path, VALID_CONFIG))

    assert config.spreadsheet.endswith("/edit")
    assert config.worksheet == "Portfolio"
    assert config.columns.identifier == "Symbol"
    assert config.columns.price_out == "Price"
    # Defaults fill in.
    assert config.auth.credentials_env == "GOOGLE_APPLICATION_CREDENTIALS"
    assert config.providers.pse.timeout_seconds == 15.0
    assert config.request_delay_seconds == 0.5


def test_defaults_for_optional_columns(tmp_path: Path) -> None:
    content = """
    spreadsheet: "id123"
    worksheet: "Sheet1"
    first_data_row: 2
    columns:
      identifier: "Symbol"
      provider: "Provider"
      price_out: "Price"
      currency_out: "Currency"
    """
    config = load_config(_write(tmp_path, content))
    assert config.columns.url is None
    assert config.columns.updated_at is None
    assert config.header_row == 1


@pytest.mark.parametrize(
    ("content", "needle"),
    [
        pytest.param("", "empty", id="empty-file"),
        pytest.param("- just\n- a\n- list\n", "must be a mapping", id="not-a-mapping"),
        pytest.param(
            """
            worksheet: "Sheet1"
            first_data_row: 2
            columns:
              identifier: "Symbol"
              price_out: "Price"
              currency_out: "Currency"
            """,
            "spreadsheet",
            id="missing-spreadsheet",
        ),
        pytest.param(
            """
            spreadsheet: "id"
            worksheet: "Sheet1"
            first_data_row: 2
            columns:
              identifier: "Symbol"
              price_out: "Price"
            """,
            "currency_out",
            id="missing-required-column",
        ),
        pytest.param(
            """
            spreadsheet: "id"
            worksheet: "Sheet1"
            first_data_row: 2
            unknown_key: 1
            columns:
              identifier: "Symbol"
              price_out: "Price"
              currency_out: "Currency"
            """,
            "unknown_key",
            id="extra-key-forbidden",
        ),
        pytest.param(
            """
            spreadsheet: "id"
            worksheet: "Sheet1"
            header_row: 5
            first_data_row: 3
            columns:
              identifier: "Symbol"
              price_out: "Price"
              currency_out: "Currency"
            """,
            "must be greater than",
            id="data-row-before-header",
        ),
        pytest.param(
            """
            spreadsheet: "id"
            worksheet: "Sheet1"
            first_data_row: 10
            last_data_row: 5
            columns:
              identifier: "Symbol"
              price_out: "Price"
              currency_out: "Currency"
            """,
            "last_data_row",
            id="last-before-first",
        ),
        pytest.param(
            """
            spreadsheet: ""
            worksheet: "Sheet1"
            first_data_row: 2
            columns:
              identifier: "Symbol"
              price_out: "Price"
              currency_out: "Currency"
            """,
            "spreadsheet",
            id="empty-spreadsheet",
        ),
        pytest.param(
            """
            spreadsheet: "id"
            worksheet: "Sheet1"
            first_data_row: 2
            columns:
              identifier: ""
              price_out: "Price"
              currency_out: "Currency"
            """,
            "identifier",
            id="empty-identifier-column",
        ),
    ],
)
def test_invalid_configs(tmp_path: Path, content: str, needle: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        load_config(_write(tmp_path, content))
    assert needle in str(excinfo.value)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        load_config(tmp_path / "does-not-exist.yaml")


@pytest.mark.skipif(
    not hasattr(os, "geteuid") or os.geteuid() == 0,
    reason="root bypasses file permission bits, so chmod 000 stays readable",
)
def test_unreadable_file_raises_configerror(tmp_path: Path) -> None:
    path = _write(tmp_path, VALID_CONFIG)
    path.chmod(0o000)
    try:
        with pytest.raises(ConfigError, match="Could not read config file"):
            load_config(path)
    finally:
        path.chmod(0o644)  # let tmp cleanup remove it

"""Configuration models and YAML loader.

The whole tool is config-driven: nothing about a specific sheet or a specific
holding lives in the code. This module defines the validated shape of that
config (via pydantic v2) and a loader that turns a YAML file into a `Config`
with clear, actionable error messages.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .providers.investing_com import InvestingComProviderConfig
from .providers.pse import PseProviderConfig
from .providers.scrape import ScrapeProviderConfig


class ColumnsConfig(BaseModel):
    """Maps logical fields to sheet columns.

    Every value is either a header name (matched against the header row) or a
    column letter (e.g. ``"C"``). Resolution happens in the sheets layer, so
    here we only require the strings to be present.
    """

    model_config = ConfigDict(extra="forbid")

    identifier: str = Field(
        min_length=1,
        description="Column holding the provider identifier / symbol / URL slug.",
    )
    provider: str | None = Field(
        default=None,
        min_length=1,
        description="Column naming the provider. Optional if 'url' alone drives a scrape.",
    )
    url: str | None = Field(
        default=None,
        min_length=1,
        description="Column holding a per-row source URL (for pse/scrape).",
    )
    price_out: str = Field(min_length=1, description="Column the fetched price is written into.")
    currency_out: str = Field(
        min_length=1, description="Column the fetched ISO currency is written into."
    )
    updated_at: str | None = Field(
        default=None,
        min_length=1,
        description="Optional column that receives a per-run timestamp.",
    )


class ProvidersConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pse: PseProviderConfig = Field(default_factory=PseProviderConfig)
    scrape: ScrapeProviderConfig = Field(default_factory=ScrapeProviderConfig)
    investing_com: InvestingComProviderConfig = Field(default_factory=InvestingComProviderConfig)


class AuthConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Name of the environment variable that holds the path to the service
    # account JSON key. The key itself is never inlined in config.
    credentials_env: str = "GOOGLE_APPLICATION_CREDENTIALS"


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")

    spreadsheet: str = Field(min_length=1, description="Spreadsheet URL or ID.")
    worksheet: str = Field(min_length=1, description="Worksheet (tab) name.")
    header_row: int = Field(default=1, ge=1, description="1-based row holding column headers.")
    first_data_row: int = Field(ge=1, description="1-based first row that holds instrument data.")
    last_data_row: int | None = Field(
        default=None,
        ge=1,
        description="1-based last data row. If omitted, all rows below the header are read.",
    )
    columns: ColumnsConfig
    providers: ProvidersConfig = Field(default_factory=ProvidersConfig)
    auth: AuthConfig = Field(default_factory=AuthConfig)
    request_delay_seconds: float = Field(
        default=0.5,
        ge=0,
        description="Polite delay inserted between external provider calls.",
    )
    max_retries: int = Field(
        default=2, ge=0, description="Retries per external call before giving up on a row."
    )

    @model_validator(mode="after")
    def _validate_row_range(self) -> Config:
        if self.first_data_row <= self.header_row:
            raise ValueError(
                f"first_data_row ({self.first_data_row}) must be greater than "
                f"header_row ({self.header_row})."
            )
        if self.last_data_row is not None and self.last_data_row < self.first_data_row:
            raise ValueError(
                f"last_data_row ({self.last_data_row}) must be >= "
                f"first_data_row ({self.first_data_row})."
            )
        return self


class ConfigError(Exception):
    """Raised when a config file cannot be read or fails validation."""


def load_config(path: str | Path) -> Config:
    """Load and validate a YAML config file into a `Config`.

    Raises `ConfigError` with a readable message on any failure so the CLI can
    print it and exit non-zero without a traceback.
    """

    config_path = Path(path)
    try:
        text = config_path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(f"Config file not found: {config_path}") from exc
    except OSError as exc:
        # Permission denied, is-a-directory, etc. Report cleanly instead of
        # letting a raw OSError (or a re-raised is_file() error) escape.
        raise ConfigError(f"Could not read config file {config_path}: {exc}") from exc

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse YAML in {config_path}: {exc}") from exc

    if raw is None:
        raise ConfigError(f"Config file is empty: {config_path}")
    if not isinstance(raw, dict):
        raise ConfigError(f"Config root must be a mapping, got {type(raw).__name__}: {config_path}")

    try:
        config = Config.model_validate(raw)
    except ValidationError as exc:
        raise ConfigError(f"Invalid config in {config_path}:\n{exc}") from exc

    return config

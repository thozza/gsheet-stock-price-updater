"""Batched Google Sheets read/write via gspread.

Reads the whole worksheet once, resolves the configured column mapping (header
name or column letter) against the header row, and writes every update back in a
single batch call to respect API quotas. The write path only ever touches the
price, currency, and optional timestamp cells; a row that is not in the update
list is left exactly as it was, which is how a failed fetch preserves the prior
value.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from .config import Config

# Least-privilege scope: the service account needs Sheets access and nothing else.
SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets"

_COLUMN_LETTER_RE = re.compile(r"[A-Z]{1,3}")


class SheetError(Exception):
    """Raised for sheet access, column resolution, or write-shape problems."""


class WorksheetLike(Protocol):
    """The slice of a gspread Worksheet this module depends on (eases testing)."""

    def get_all_values(self) -> list[list[str]]:
        ...

    def batch_update(
        self, data: list[dict[str, object]], value_input_option: str = ...
    ) -> object:
        ...


@dataclass(frozen=True)
class ResolvedColumns:
    """0-based column indices resolved from config against the header row."""

    identifier: int
    price_out: int
    currency_out: int
    provider: int | None
    url: int | None
    updated_at: int | None


@dataclass(frozen=True)
class SheetRow:
    """One data row's inputs, already pulled out by column."""

    row_number: int
    identifier: str
    provider: str | None
    url: str | None


@dataclass(frozen=True)
class RowUpdate:
    """A successful fetch to write back: price + currency (+ optional timestamp)."""

    row_number: int
    price: Decimal
    currency: str
    timestamp: str | None = None


@dataclass(frozen=True)
class CellWrite:
    """A single A1-addressed cell write."""

    a1: str
    value: object


def column_letter_to_index(letter: str) -> int:
    """Convert a spreadsheet column letter (A, B, ..., AA) to a 0-based index."""

    index = 0
    for char in letter:
        index = index * 26 + (ord(char) - ord("A") + 1)
    return index - 1


def index_to_column_letter(index: int) -> str:
    """Convert a 0-based column index to a spreadsheet column letter."""

    if index < 0:
        raise SheetError(f"Negative column index: {index}.")
    letters = ""
    current = index + 1
    while current > 0:
        current, remainder = divmod(current - 1, 26)
        letters = chr(ord("A") + remainder) + letters
    return letters


def _resolve_column(ref: str, headers: list[str]) -> int:
    """Resolve a column reference (header name or letter) to a 0-based index.

    Header names win over letters, so a header literally named "C" resolves to
    its own position, not column 3. A 1-3 uppercase-letter reference that is not
    a header is treated as a column letter.
    """

    if ref in headers:
        return headers.index(ref)
    candidate = ref.strip().upper()
    if _COLUMN_LETTER_RE.fullmatch(candidate):
        return column_letter_to_index(candidate)
    raise SheetError(
        f"Column {ref!r} not found in the header row and is not a column letter (A-ZZZ)."
    )


class SheetService:
    """Reads rows and writes batched updates for one worksheet."""

    def __init__(self, worksheet: WorksheetLike, config: Config) -> None:
        self._worksheet = worksheet
        self._config = config
        self._columns: ResolvedColumns | None = None

    def read_rows(self) -> list[SheetRow]:
        """Read the worksheet once and return the configured data rows."""

        values = self._worksheet.get_all_values()
        header_index = self._config.header_row - 1
        if header_index >= len(values):
            raise SheetError(
                f"Header row {self._config.header_row} is beyond the sheet "
                f"({len(values)} row(s) present)."
            )

        headers = [cell.strip() for cell in values[header_index]]
        self._columns = self._resolve_columns(headers)

        last = self._config.last_data_row or len(values)
        rows: list[SheetRow] = []
        for row_number in range(self._config.first_data_row, last + 1):
            if row_number - 1 >= len(values):
                break
            raw = values[row_number - 1]
            rows.append(self._build_row(row_number, raw, self._columns))
        return rows

    def write(self, updates: list[RowUpdate]) -> list[CellWrite]:
        """Write all updates in a single batch call; return the cell writes made.

        Uses RAW input so the price lands as a true number (locale-independent)
        and the currency/timestamp as text. Returns the planned writes so the
        caller can log or dry-run them.
        """

        writes = self.build_writes(updates)
        if writes:
            payload: list[dict[str, object]] = [
                {"range": w.a1, "values": [[w.value]]} for w in writes
            ]
            self._worksheet.batch_update(payload, value_input_option="RAW")
        return writes

    def build_writes(self, updates: list[RowUpdate]) -> list[CellWrite]:
        """Turn row updates into flat A1 cell writes (no API call)."""

        columns = self._require_columns()
        price_col = index_to_column_letter(columns.price_out)
        currency_col = index_to_column_letter(columns.currency_out)
        updated_col = (
            index_to_column_letter(columns.updated_at)
            if columns.updated_at is not None
            else None
        )

        writes: list[CellWrite] = []
        for update in updates:
            # A price is serialized to float only at this boundary: Google Sheets
            # stores every number as an IEEE double anyway, so this loses nothing
            # versus typing the value in by hand, and keeps the cell numeric for
            # downstream formulas. All internal handling stays Decimal.
            writes.append(
                CellWrite(f"{price_col}{update.row_number}", float(update.price))
            )
            writes.append(
                CellWrite(f"{currency_col}{update.row_number}", update.currency)
            )
            if updated_col is not None and update.timestamp is not None:
                writes.append(
                    CellWrite(f"{updated_col}{update.row_number}", update.timestamp)
                )
        return writes

    def _require_columns(self) -> ResolvedColumns:
        if self._columns is None:
            raise SheetError("Columns not resolved yet; call read_rows() first.")
        return self._columns

    def _resolve_columns(self, headers: list[str]) -> ResolvedColumns:
        cols = self._config.columns
        return ResolvedColumns(
            identifier=_resolve_column(cols.identifier, headers),
            price_out=_resolve_column(cols.price_out, headers),
            currency_out=_resolve_column(cols.currency_out, headers),
            provider=_resolve_column(cols.provider, headers) if cols.provider else None,
            url=_resolve_column(cols.url, headers) if cols.url else None,
            updated_at=_resolve_column(cols.updated_at, headers) if cols.updated_at else None,
        )

    @staticmethod
    def _cell(raw: list[str], index: int | None) -> str | None:
        if index is None or index >= len(raw):
            return None
        value = raw[index].strip()
        return value or None

    def _build_row(
        self, row_number: int, raw: list[str], columns: ResolvedColumns
    ) -> SheetRow:
        identifier = self._cell(raw, columns.identifier) or ""
        return SheetRow(
            row_number=row_number,
            identifier=identifier,
            provider=self._cell(raw, columns.provider),
            url=self._cell(raw, columns.url),
        )


def open_worksheet(config: Config) -> WorksheetLike:
    """Authenticate with the service-account key and open the target worksheet.

    The key path comes from the environment variable named in config; the key is
    never inlined. Requests the Sheets scope only (least privilege).
    """

    import gspread
    from google.oauth2.service_account import Credentials

    key_path = os.environ.get(config.auth.credentials_env)
    if not key_path:
        raise SheetError(
            f"Environment variable {config.auth.credentials_env} is not set; "
            "it must point at the service-account JSON key file."
        )
    if not os.path.isfile(key_path):
        raise SheetError(f"Service-account key file not found: {key_path}")

    credentials = Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
        key_path, scopes=[SHEETS_SCOPE]
    )
    client = gspread.authorize(credentials)

    if config.spreadsheet.startswith("http"):
        spreadsheet = client.open_by_url(config.spreadsheet)
    else:
        spreadsheet = client.open_by_key(config.spreadsheet)

    # gspread's Worksheet satisfies the subset we use; its batch_update signature
    # is wider than our Protocol, so cast at this boundary.
    return cast(WorksheetLike, spreadsheet.worksheet(config.worksheet))

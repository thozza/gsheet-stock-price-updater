"""Tests for column resolution and batched sheet writes.

The gspread worksheet is replaced by a fake that records batch_update calls, so
we can assert a single batched write with the exact cell targets and values.
"""

from __future__ import annotations

import re
from decimal import Decimal

import pytest

from gsheet_stock_price_updater.config import Config
from gsheet_stock_price_updater.sheets import (
    RowUpdate,
    SheetError,
    SheetService,
    column_letter_to_index,
    index_to_column_letter,
)

HEADERS = ["Symbol", "Provider", "Source URL", "Price", "Currency", "Updated At"]


class FakeWorksheet:
    def __init__(self, values: list[list[str]]) -> None:
        self._values = values
        self.batch_calls: list[dict[str, object]] = []

    def get_all_values(self) -> list[list[str]]:
        return self._values

    def batch_update(
        self, data: list[dict[str, object]], value_input_option: str = "RAW"
    ) -> object:
        self.batch_calls.append({"data": data, "value_input_option": value_input_option})
        return {"ok": True}


def _config(**overrides: object) -> Config:
    base: dict[str, object] = {
        "spreadsheet": "sheet-id",
        "worksheet": "Portfolio",
        "header_row": 1,
        "first_data_row": 2,
        "columns": {
            "identifier": "Symbol",
            "provider": "Provider",
            "url": "Source URL",
            "price_out": "Price",
            "currency_out": "Currency",
            "updated_at": "Updated At",
        },
    }
    base.update(overrides)
    return Config.model_validate(base)


def _sheet(
    values: list[list[str]], config: Config | None = None
) -> tuple[SheetService, FakeWorksheet]:
    ws = FakeWorksheet(values)
    return SheetService(ws, config or _config()), ws


# --------------------------------------------------------------------------- #
# Column letter helpers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("letter", "index"),
    [("A", 0), ("B", 1), ("Z", 25), ("AA", 26), ("AB", 27), ("BA", 52)],
)
def test_column_letter_roundtrip(letter: str, index: int) -> None:
    assert column_letter_to_index(letter) == index
    assert index_to_column_letter(index) == letter


# --------------------------------------------------------------------------- #
# Reading
# --------------------------------------------------------------------------- #
def test_read_rows_by_header_name() -> None:
    values = [
        HEADERS,
        ["SXR8.DE", "stub", "", "0", "", ""],
        ["CEZ", "pse", "https://pse.cz/detail/X", "", "", ""],
    ]
    service, _ = _sheet(values)
    rows = service.read_rows()

    assert [r.row_number for r in rows] == [2, 3]
    assert rows[0].identifier == "SXR8.DE"
    assert rows[0].provider == "stub"
    assert rows[0].url is None
    assert rows[1].url == "https://pse.cz/detail/X"


@pytest.mark.parametrize(
    "columns",
    [
        pytest.param(
            {"identifier": "A", "provider": "B", "price_out": "D", "currency_out": "E"},
            id="uppercase-letters",
        ),
        pytest.param(
            {"identifier": "a", "provider": "b", "price_out": "d", "currency_out": "e"},
            id="lowercase-letters",
        ),
    ],
)
def test_read_rows_by_column_letter(columns: dict[str, str]) -> None:
    values = [HEADERS, ["SXR8.DE", "stub", "", "", "", ""]]
    service, _ = _sheet(values, _config(columns=columns))
    rows = service.read_rows()
    assert rows[0].identifier == "SXR8.DE"
    assert rows[0].provider == "stub"


def test_read_rows_tolerates_ragged_short_rows() -> None:
    # get_all_values trims trailing empty cells, so a data row can be shorter
    # than the header. Missing cells must read as None, not IndexError.
    values = [HEADERS, ["SXR8.DE", "stub"]]
    service, _ = _sheet(values)
    row = service.read_rows()[0]
    assert row.identifier == "SXR8.DE"
    assert row.provider == "stub"
    assert row.url is None


def test_read_rows_respects_last_data_row() -> None:
    values = [HEADERS] + [[f"S{i}", "stub", "", "", "", ""] for i in range(1, 6)]
    service, _ = _sheet(values, _config(first_data_row=2, last_data_row=3))
    rows = service.read_rows()
    assert [r.row_number for r in rows] == [2, 3]


@pytest.mark.parametrize(
    ("values", "config", "match"),
    [
        pytest.param(
            [["Ticker", "Price", "Currency"], ["AAA", "", ""]],
            None,
            "not found",
            id="unknown-column",
        ),
        pytest.param(
            [HEADERS],
            _config(header_row=5, first_data_row=6),
            "beyond the sheet",
            id="header-beyond-sheet",
        ),
    ],
)
def test_read_rows_raises(
    values: list[list[str]], config: Config | None, match: str
) -> None:
    service, _ = _sheet(values, config)
    with pytest.raises(SheetError, match=match):
        service.read_rows()


# --------------------------------------------------------------------------- #
# Writing (single batched call)
# --------------------------------------------------------------------------- #
def test_write_is_single_batch_with_expected_cells() -> None:
    values = [HEADERS, ["SXR8.DE", "stub", "", "", "", ""], ["CEZ", "pse", "", "", "", ""]]
    service, ws = _sheet(values)
    service.read_rows()

    stamp = "2026-08-22T10:00:00Z"
    updates = [
        RowUpdate(row_number=2, price=Decimal("585.42"), currency="EUR", timestamp=stamp),
        RowUpdate(row_number=3, price=Decimal("1385.00"), currency="CZK", timestamp=stamp),
    ]
    writes = service.write(updates)

    # Exactly one API call.
    assert len(ws.batch_calls) == 1
    call = ws.batch_calls[0]
    assert call["value_input_option"] == "RAW"

    data = call["data"]
    ranges = {entry["range"]: entry["values"][0][0] for entry in data}
    assert ranges == {
        "D2": 585.42,
        "E2": "EUR",
        "F2": "2026-08-22T10:00:00Z",
        "D3": 1385.00,
        "E3": "CZK",
        "F3": "2026-08-22T10:00:00Z",
    }
    # Price cells carry real numbers, not strings.
    assert isinstance(ranges["D2"], float)
    assert isinstance(ranges["E2"], str)
    assert len(writes) == 6


def test_write_without_timestamp_column_omits_it() -> None:
    config = _config(
        columns={
            "identifier": "Symbol",
            "provider": "Provider",
            "price_out": "Price",
            "currency_out": "Currency",
        }
    )
    values = [HEADERS, ["SXR8.DE", "stub", "", "", "", ""]]
    service, ws = _sheet(values, config)
    service.read_rows()

    service.write([RowUpdate(2, Decimal("10.5"), "USD", timestamp="ignored")])
    data = ws.batch_calls[0]["data"]
    ranges = {entry["range"] for entry in data}
    assert ranges == {"D2", "E2"}  # no Updated At column configured


def test_failed_row_is_never_written() -> None:
    # Row 3 failed to fetch, so it is simply absent from updates: no cell for it.
    values = [
        HEADERS,
        ["SXR8.DE", "stub", "", "999", "EUR", ""],
        ["BAD", "stub", "", "42", "USD", ""],
    ]
    service, ws = _sheet(values)
    service.read_rows()

    service.write([RowUpdate(2, Decimal("585.42"), "EUR")])
    data = ws.batch_calls[0]["data"]
    touched_rows = {re.sub(r"^[A-Z]+", "", entry["range"]) for entry in data}
    assert touched_rows == {"2"}  # row 3's prior value (42/USD) is left intact


def test_empty_updates_makes_no_api_call() -> None:
    service, ws = _sheet([HEADERS, ["SXR8.DE", "stub", "", "", "", ""]])
    service.read_rows()
    writes = service.write([])
    assert writes == []
    assert ws.batch_calls == []


def test_write_before_read_raises() -> None:
    service, _ = _sheet([HEADERS, ["SXR8.DE", "stub", "", "", "", ""]])
    with pytest.raises(SheetError, match="call read_rows"):
        service.write([RowUpdate(2, Decimal("1"), "USD")])

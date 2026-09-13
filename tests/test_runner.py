"""Tests for run orchestration and the failure-isolation guarantee."""

from __future__ import annotations

from decimal import Decimal

from gsheet_stock_price_updater.config import Config
from gsheet_stock_price_updater.providers import Provider, ProviderError, Quote, RowContext
from gsheet_stock_price_updater.runner import RunSummary, execute_run
from gsheet_stock_price_updater.sheets import CellWrite, RowUpdate, SheetRow


class StubProvider:
    """A provider whose behavior per identifier is scripted by the test."""

    def __init__(self, name: str, results: dict[str, Quote | Exception]) -> None:
        self.name = name
        self._results = results
        self.calls: list[str] = []

    def fetch(self, row: RowContext) -> Quote:
        self.calls.append(row.identifier)
        outcome = self._results[row.identifier]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class RecordingSheet:
    """A stand-in SheetService that records reads and the single write."""

    def __init__(self, rows: list[SheetRow], updated_at: bool = True) -> None:
        self._rows = rows
        self.written: list[RowUpdate] | None = None
        self.build_calls: list[list[RowUpdate]] = []
        self._updated_at = updated_at

    def read_rows(self) -> list[SheetRow]:
        return self._rows

    def build_writes(self, updates: list[RowUpdate]) -> list[CellWrite]:
        self.build_calls.append(updates)
        return [CellWrite(f"D{u.row_number}", float(u.price)) for u in updates]

    def write(self, updates: list[RowUpdate]) -> list[object]:
        assert self.written is None, "write must be called at most once"
        self.written = updates
        return list(updates)


def _config(request_delay_seconds: float = 0.0, with_timestamp: bool = True) -> Config:
    columns: dict[str, str] = {
        "identifier": "Symbol",
        "provider": "Provider",
        "url": "Source URL",
        "price_out": "Price",
        "currency_out": "Currency",
    }
    if with_timestamp:
        columns["updated_at"] = "Updated At"
    return Config.model_validate(
        {
            "spreadsheet": "id",
            "worksheet": "Sheet1",
            "first_data_row": 2,
            "request_delay_seconds": request_delay_seconds,
            "columns": columns,
        }
    )


def _row(n: int, identifier: str, provider: str | None, url: str | None = None) -> SheetRow:
    return SheetRow(row_number=n, identifier=identifier, provider=provider, url=url)


def _no_sleep(_seconds: float) -> None:
    return None


def _fixed_now() -> str:
    return "2026-08-22T10:00:00Z"


def _run(
    rows: list[SheetRow],
    registry: dict[str, Provider],
    *,
    dry_run: bool = False,
    config: Config | None = None,
) -> tuple[RunSummary, RecordingSheet]:
    sheet = RecordingSheet(rows)
    summary = execute_run(
        config or _config(),
        sheet,  # type: ignore[arg-type]  # structural stand-in for SheetService
        registry,
        dry_run=dry_run,
        now=_fixed_now,
        sleep=_no_sleep,
    )
    return summary, sheet


def test_one_failure_does_not_stop_other_rows() -> None:
    # The entire reason the tool exists: row 2 fails, rows 3 and 4 still update.
    stub = StubProvider(
        "stub",
        {
            "BAD": ProviderError("boom"),
            "SXR8.DE": Quote(Decimal("585.42"), "EUR"),
            "VUSA.DE": Quote(Decimal("90.10"), "EUR"),
        },
    )
    rows = [
        _row(2, "BAD", "stub"),
        _row(3, "SXR8.DE", "stub"),
        _row(4, "VUSA.DE", "stub"),
    ]
    summary, sheet = _run(rows, {"stub": stub})

    assert summary == RunSummary(updated=2, skipped=0, failed=1)
    assert sheet.written is not None
    written_rows = {u.row_number for u in sheet.written}
    assert written_rows == {3, 4}  # the failed row 2 is absent from the write


def test_unexpected_exception_is_isolated() -> None:
    # A provider raising a non-ProviderError must still be contained to its row.
    stub = StubProvider(
        "stub",
        {"KABOOM": RuntimeError("unexpected"), "OK": Quote(Decimal("1"), "USD")},
    )
    rows = [_row(2, "KABOOM", "stub"), _row(3, "OK", "stub")]
    summary, sheet = _run(rows, {"stub": stub})

    assert summary == RunSummary(updated=1, skipped=0, failed=1)
    assert {u.row_number for u in (sheet.written or [])} == {3}


def test_empty_identifier_row_is_isolated_as_failure() -> None:
    # A row with a valid provider but a blank identifier is not guarded centrally
    # (requirements are provider-specific); the provider rejects it, and the run
    # continues, counting it as a failure without touching its cell.
    stub = StubProvider(
        "stub",
        {"": ProviderError("stub: empty symbol."), "OK": Quote(Decimal("1"), "USD")},
    )
    rows = [_row(2, "", "stub"), _row(3, "OK", "stub")]
    summary, sheet = _run(rows, {"stub": stub})

    assert summary == RunSummary(updated=1, skipped=0, failed=1)
    assert {u.row_number for u in (sheet.written or [])} == {3}


def test_missing_and_unknown_provider_are_skipped() -> None:
    stub = StubProvider("stub", {"OK": Quote(Decimal("1"), "USD")})
    rows = [
        _row(2, "OK", "stub"),
        _row(3, "X", None),  # no provider
        _row(4, "Y", "bogus"),  # unknown provider
    ]
    summary, sheet = _run(rows, {"stub": stub})

    assert summary == RunSummary(updated=1, skipped=2, failed=0)
    assert {u.row_number for u in (sheet.written or [])} == {2}


def test_timestamp_written_only_when_column_configured() -> None:
    stub = StubProvider("stub", {"OK": Quote(Decimal("1"), "USD")})
    rows = [_row(2, "OK", "stub")]

    summary, sheet = _run(rows, {"stub": stub})
    assert sheet.written is not None
    assert sheet.written[0].timestamp == "2026-08-22T10:00:00Z"

    sheet2 = RecordingSheet(rows)
    execute_run(
        _config(with_timestamp=False),
        sheet2,  # type: ignore[arg-type]
        {"stub": stub},
        dry_run=False,
        now=_fixed_now,
        sleep=_no_sleep,
    )
    assert sheet2.written is not None
    assert sheet2.written[0].timestamp is None


def test_dry_run_does_not_write() -> None:
    stub = StubProvider("stub", {"OK": Quote(Decimal("1"), "USD")})
    rows = [_row(2, "OK", "stub")]
    summary, sheet = _run(rows, {"stub": stub}, dry_run=True)

    assert summary.updated == 1
    assert sheet.written is None  # no write call at all
    assert sheet.build_calls  # build_writes was used to preview


def test_polite_delay_between_fetches_only() -> None:
    stub = StubProvider(
        "stub",
        {"A": Quote(Decimal("1"), "USD"), "B": Quote(Decimal("2"), "USD")},
    )
    rows = [_row(2, "A", "stub"), _row(3, "B", "stub")]
    sleeps: list[float] = []

    sheet = RecordingSheet(rows)
    execute_run(
        _config(request_delay_seconds=0.5),
        sheet,  # type: ignore[arg-type]
        {"stub": stub},
        dry_run=False,
        now=_fixed_now,
        sleep=sleeps.append,
    )
    # Two fetches -> exactly one inter-call delay.
    assert sleeps == [0.5]


def test_empty_sheet_writes_nothing() -> None:
    summary, sheet = _run([], {})
    assert summary == RunSummary(0, 0, 0)
    assert sheet.written == []

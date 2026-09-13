"""Run orchestration with per-row failure isolation.

This is the module the whole tool exists for: it reads the rows, fetches each
one through its provider, and collects only the successful quotes into a single
batched write. A failure on one row is logged with context and skipped; it never
aborts the run and never contributes a blank or error value to the write, so a
previously good price is left untouched.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from .providers import Provider, ProviderError, RowContext
from .sheets import RowUpdate, SheetService

if TYPE_CHECKING:
    from .config import Config

logger = logging.getLogger("gsheet_stock_price_updater")


@dataclass(frozen=True)
class RunSummary:
    """Per-run outcome counts."""

    updated: int
    skipped: int
    failed: int

    @property
    def total(self) -> int:
        return self.updated + self.skipped + self.failed


def utc_now_iso() -> str:
    """Current UTC time as an ISO 8601 string with a trailing Z."""

    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def execute_run(
    config: Config,
    sheet_service: SheetService,
    registry: dict[str, Provider],
    *,
    dry_run: bool,
    now: Callable[[], str] = utc_now_iso,
    sleep: Callable[[float], None] = time.sleep,
) -> RunSummary:
    """Fetch every row's price and write the successes in one batch.

    `now` and `sleep` are injectable for tests. Returns a `RunSummary`; the
    write happens once, at the end, unless `dry_run` is set.
    """

    rows = sheet_service.read_rows()
    timestamp = now() if config.columns.updated_at else None

    updates: list[RowUpdate] = []
    skipped = 0
    failed = 0
    attempted = 0

    for row in rows:
        provider_name = row.provider
        if not provider_name:
            logger.warning("Row %d: no provider specified; skipping.", row.row_number)
            skipped += 1
            continue

        provider = registry.get(provider_name)
        if provider is None:
            logger.warning(
                "Row %d: unknown provider %r; skipping.", row.row_number, provider_name
            )
            skipped += 1
            continue

        # The identifier is not validated here on purpose: what a row requires is
        # provider-specific (scrape needs a price XPath, pse a URL and no
        # identifier at all). Each provider validates its own inputs
        # and raises ProviderError before any network call, so a row missing what
        # its provider needs is isolated as a failure below.

        # Be polite: space out external calls, but only between actual fetches.
        if attempted > 0 and config.request_delay_seconds > 0:
            sleep(config.request_delay_seconds)
        attempted += 1

        try:
            quote = provider.fetch(
                RowContext(identifier=row.identifier, url=row.url, row_number=row.row_number)
            )
        except ProviderError as exc:
            logger.warning(
                "Row %d: fetch failed via %s: %s", row.row_number, provider_name, exc
            )
            failed += 1
            continue
        except Exception as exc:  # isolate any unexpected provider bug to its row
            logger.warning(
                "Row %d: unexpected error via %s: %s", row.row_number, provider_name, exc
            )
            failed += 1
            continue

        logger.info(
            "Row %d: %s -> %s %s", row.row_number, provider_name, quote.price, quote.currency
        )
        updates.append(
            RowUpdate(
                row_number=row.row_number,
                price=quote.price,
                currency=quote.currency,
                timestamp=timestamp,
            )
        )

    _commit(sheet_service, updates, dry_run=dry_run)

    summary = RunSummary(updated=len(updates), skipped=skipped, failed=failed)
    logger.info(
        "Run complete: %d updated, %d skipped, %d failed (of %d rows).",
        summary.updated,
        summary.skipped,
        summary.failed,
        summary.total,
    )
    return summary


def _commit(sheet_service: SheetService, updates: list[RowUpdate], *, dry_run: bool) -> None:
    if dry_run:
        writes = sheet_service.build_writes(updates)
        if not writes:
            logger.info("[dry-run] No successful fetches; nothing would be written.")
            return
        logger.info("[dry-run] Would write %d cell(s) in one batch:", len(writes))
        for write in writes:
            logger.info("[dry-run]   %s = %r", write.a1, write.value)
        return

    sheet_service.write(updates)

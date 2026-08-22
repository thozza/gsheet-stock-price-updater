"""Command-line entry point.

One-shot batch: load config, open the worksheet, fetch prices, write once. Not a
daemon and no network service. Flags: --config, --dry-run, --verbose.
"""

from __future__ import annotations

import argparse
import logging
import sys

import httpx

from .config import ConfigError, load_config
from .providers import build_registry
from .runner import execute_run
from .sheets import SheetError, SheetService, open_worksheet

# Overall client timeout; per-provider timeouts still apply per request.
_CLIENT_TIMEOUT_SECONDS = 30.0


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="gsheet-stock-price-updater",
        description="Fetch stock prices and write price + currency into a Google Sheet.",
    )
    parser.add_argument("-c", "--config", required=True, help="Path to the YAML config file.")
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        help="Print the intended writes without touching the sheet.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable debug-level logging."
    )
    return parser.parse_args(argv)


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    _configure_logging(args.verbose)
    log = logging.getLogger("gsheet_stock_price_updater")

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2

    try:
        worksheet = open_worksheet(config)
    except SheetError as exc:
        log.error("%s", exc)
        return 2

    sheet_service = SheetService(worksheet, config)
    with httpx.Client(timeout=_CLIENT_TIMEOUT_SECONDS) as client:
        registry = build_registry(config, client)
        try:
            execute_run(config, sheet_service, registry, dry_run=args.dry_run)
        except SheetError as exc:
            # Read or write against the sheet failed as a whole (not a per-row issue).
            log.error("Sheet operation failed: %s", exc)
            return 1

    # A completed run is a success even if some rows failed: per-row failures are
    # logged and isolated, and must never affect the exit path.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

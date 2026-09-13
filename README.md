# gsheet-stock-price-updater

A small, config-driven CLI that fetches current stock prices and writes them
into a Google Sheet. It replaces fragile in-sheet `IMPORTXML` scraping (which
times out and leaves gaps, breaking downstream formulas) with values pushed in
from a source you control.

The tool is generic and reusable: nothing about a specific sheet or a specific
holding lives in the code. The **sheet is the source of truth** for which
instruments to price. Adding an instrument is adding a row; no code or config
change is needed.

## What it does

- Reads rows from a configured worksheet.
- For each row it determines a **provider** and an identifier, fetches the
  instrument's **price and currency only**, and writes those two values back
  into configured output columns. Optionally it writes a per-run timestamp.
- Runs as a one-shot batch (on a schedule a few times a day, or on demand). It
  is not a daemon and exposes no network service.

### Design guarantees

- **Failure isolation.** A failed fetch for one row never aborts the run and
  never overwrites a previously good price with a blank or error value. The
  warning is logged, the existing cell is left untouched, and the run continues.
  This is the entire reason the tool exists.
- **Price and currency only** are written per instrument. Nothing else in the
  row is modified.
- **Money is `Decimal`, never `float`,** through all parsing and normalization.
  Currency is a normalized ISO 4217 code (`CZK`, `EUR`, `USD`, ...).
- **A single batched write** per run, to respect Google Sheets API quotas.

### Explicit non-goals

No FX/currency conversion (keep that in the sheet with `GOOGLEFINANCE`); no
transactions, holdings, cost basis, P/L, dividends, or analytics; no web UI or
API server; no paid/real-time market-data integration.

## Providers

The per-row provider and its identifier/URL come from the sheet via configured
columns. If a row's provider is unknown or its required input is missing, that
row is treated as a per-row failure and skipped.

| Provider | Use for | Row inputs | Notes |
| --- | --- | --- | --- |
| `investing-com` | Investing.com instrument detail pages | `url` = detail page URL | Extracts price and currency via configurable XPaths (`providers.investing_com.identifier_xpath` / `currency_xpath`). The row fails if either XPath doesn't resolve to a recognizable value. Investing.com sits behind Cloudflare, which 403s non-browser clients, so this provider fetches with `curl_cffi` browser impersonation; `providers.investing_com.impersonate` selects the target (default `chrome`). |
| `pse` | Prague Stock Exchange instruments | `url` = detail page URL | Extracts the price via a configurable XPath (`providers.pse.default_xpath`). Currency defaults to CZK. |
| `scrape` | Anything else, without new code | `identifier` = price XPath, `url` = page URL | Optional `providers.scrape.currency_xpath`; otherwise the currency is detected from the price text, and the row fails if none is found. |

Adding a provider is implementing the small `Provider` protocol in
`src/gsheet_stock_price_updater/providers/` and registering it in
`build_registry`.

## Google Cloud setup

The tool authenticates as a **service account** using a JSON key. The sheet is
shared with the service account's email like any other collaborator.

1. In the [Google Cloud Console](https://console.cloud.google.com/) create (or
   pick) a project.
2. Enable the **Google Sheets API** for the project (APIs and Services ->
   Library -> Google Sheets API -> Enable). You do **not** need the Drive API.
3. Create a **service account** (IAM and Admin -> Service Accounts -> Create).
   No project roles are required.
4. Create a **JSON key** for that service account (Keys -> Add Key -> JSON) and
   download it. This file is the secret; keep it out of the repo (see Security).
5. Open your target spreadsheet and **Share** it with the service account's
   email address (looks like `name@project.iam.gserviceaccount.com`), granting
   **Editor**. Share only the one spreadsheet, nothing else.

The service account now has the least privilege it needs: Editor on exactly one
spreadsheet, Sheets scope only, and a key you can rotate at any time by creating
a new key and deleting the old one.

## Configuration

Configuration is a single YAML file, validated by pydantic with clear error
messages. Copy the example and edit it:

```bash
cp config.example.yaml config.yaml   # config.yaml is gitignored
cp .env.example .env                 # optional, for local runs
```

### Fields

| Field | Required | Meaning |
| --- | --- | --- |
| `spreadsheet` | yes | Spreadsheet URL or bare ID. |
| `worksheet` | yes | Worksheet (tab) name. |
| `header_row` | no (default `1`) | 1-based row holding the column headers. |
| `first_data_row` | yes | 1-based first row of instrument data. Must be greater than `header_row`. |
| `last_data_row` | no | 1-based last data row. Omit to read every row below the header. |
| `columns.identifier` | yes | Column with the provider identifier / symbol / price XPath. |
| `columns.provider` | no | Column naming the provider (`pse`/`scrape`/`investing-com`). |
| `columns.url` | no | Column with a per-row source URL (for `pse`/`scrape`/`investing-com`). |
| `columns.price_out` | yes | Column the fetched price is written into. |
| `columns.currency_out` | yes | Column the fetched ISO currency is written into. |
| `columns.updated_at` | no | Column that receives a per-run timestamp (UTC, ISO 8601). |
| `request_delay_seconds` | no (default `0.5`) | Polite delay between external calls. |
| `max_retries` | no (default `2`) | Retries per external call on transient failures. |
| `providers.*` | no | Per-provider settings (timeouts, default XPaths, User-Agent). |
| `auth.credentials_env` | no (default `GOOGLE_APPLICATION_CREDENTIALS`) | Name of the env var holding the path to the JSON key. The key is never inlined. |

Every column mapping accepts either a **header name** (matched against the
header row) or a **column letter** (e.g. `C`). Header names win, so a header
literally named `C` resolves to its own position rather than column 3. See
`config.example.yaml` for a fully worked example.

## Running

```bash
# Point at your key (or set auth.credentials_env to a different var name).
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/service-account.json

# Preview the intended writes without touching the sheet.
uv run gsheet-stock-price-updater --config config.yaml --dry-run

# Real run.
uv run gsheet-stock-price-updater --config config.yaml
```

Flags: `--config/-c` (path, required), `--dry-run/-n`, `--verbose/-v`. Logging
is structured to stdout and ends with a per-run summary of updated / skipped /
failed rows. Exit code is `0` for a completed run (even if some rows failed),
`2` for a config or setup error, and `1` if the sheet read/write itself fails.

## Build and deployment (local build, Synology NAS)

This project is built **locally** and the image is moved to a Synology NAS
(Container Manager) by hand. There is deliberately **no container registry
and no CI image build/push**, which is why the repo is public and reproducible
from source alone.

### 1. Build the image locally

```bash
# podman (shown) or docker both work; the file is a standard Containerfile.
podman build -t gsheet-stock-price-updater:latest -f Containerfile .
```

A [`Justfile`](Justfile) wraps this and the save step below as `just
image-build`, `just image-save`, and `just image` (both combined); run `just`
to list all recipes.

The build pins both base images by digest and installs dependencies from the
hashed `uv.lock`, so the result is reproducible.

### 2. Save it to a tarball

```bash
podman save gsheet-stock-price-updater:latest | gzip > gsheet-stock-price-updater.tar.gz
# docker: docker save gsheet-stock-price-updater:latest | gzip > gsheet-stock-price-updater.tar.gz
```

### 3. Import into Container Manager (DSM)

1. Copy `gsheet-stock-price-updater.tar.gz` to the NAS (File Station or scp).
2. In **Container Manager -> Image -> Add -> Add from file**, select the tarball.
3. Put your real `config.yaml` and `service-account.json` somewhere on the NAS,
   for example `/volume1/docker/gsheet-price/`. Keep the key readable only by
   your admin user.

### 4. Create the container

Create a container from the imported image with:

- **No port mappings.** The container needs no inbound access.
- **Mounts (read-only):**
  - `/volume1/docker/gsheet-price/config.yaml` -> `/config/config.yaml`
  - `/volume1/docker/gsheet-price/service-account.json` -> `/run/secrets/service-account.json`
- **Environment:** the image already sets
  `GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/service-account.json`, so no env
  is required if you mount the key at that path.
- Optionally enable a **read-only root filesystem**; the tool writes nothing to
  disk. If a read-only rootfs is used, provide a writable `/tmp` (tmpfs).

The default command runs against `/config/config.yaml`. To test first, run it
once with `--dry-run` appended to the command.

### 5. Schedule one-shot runs

Container Manager keeps a container "running"; for a one-shot batch, use the
**Synology Task Scheduler** instead:

1. **Control Panel -> Task Scheduler -> Create -> Scheduled Task -> User-defined
   script.**
2. Schedule it a few times a day during/after market hours.
3. Run a script that starts the container for a single run, e.g.:

   ```bash
   #!/bin/bash
   exec > /volume2/docker/gsheet-stock-price-updater/run.log 2>&1
   echo "=== $(date '+%F %T') run start ==="
   /usr/local/bin/docker run --rm \
     -v /volume2/docker/gsheet-stock-price-updater/config.yaml:/config/config.yaml:ro \
     -v /volume2/docker/gsheet-stock-price-updater/service-account.json:/run/secrets/service-account.json:ro \
     localhost/gsheet-stock-price-updater:latest
   echo "=== $(date '+%F %T') run exit=$? ==="

   ```

   (DSM ships the `docker` CLI even when using Container Manager.) The container
   runs the batch once, writes its updates, and exits.

## Rebuild and update policy

Because the base images are pinned by digest and dependencies are locked with
hashes, builds are reproducible: you get the same image until you deliberately
change a pin. **Rebuild deliberately and infrequently** - a couple of times a
year, or when a serious CVE lands in the actual stack you ship (TLS, HTTP, or
the HTML/JSON parsing path):

1. Bump the pins: `uv lock --upgrade` for dependencies, and refresh the base
   image digests in `Containerfile` (for example
   `skopeo inspect --format '{{.Digest}}' docker://python:3.14-slim`).
2. Rebuild locally, re-run the tests, and re-import into DSM.

"Never rebuild" is discouraged: it freezes CVEs forever. If you enable the
optional Renovate config, it only opens PRs proposing these bumps for you to
review and merge manually; nothing runs against your machine automatically.

## Security notes

- The service-account key is provided **at runtime only**, mounted read-only,
  and referenced by path via an environment variable. It is never committed and
  never baked into the image. `.gitignore` and `.dockerignore` exclude keys and
  real config, and `.dockerignore` excludes root-level JSON (where downloaded
  keys land). Independently, the image `COPY` lines are explicit (only
  `pyproject.toml`, `uv.lock`, `README.md`, and `src/`), so no key or config
  enters the build context regardless.
- Least privilege: share the service account with **only** the target
  spreadsheet, Editor role, Sheets scope only. Rotate the key by issuing a new
  one and deleting the old.
- The container runs **non-root**, publishes **no ports**, and makes only
  outbound HTTPS calls. It works with a read-only root filesystem.
- Secret scanning: the repo ships a gitleaks config and a pre-commit hook (see
  Development) to catch an accidental key commit before it is pushed.

## Parsing caveats

- **Ambiguous single separators.** A price with a single grouping separator and
  exactly three trailing digits is inherently ambiguous: `"1,385"` could mean
  one-thousand-three-hundred-eighty-five (US thousands) or `1.385` (Czech
  decimal). The parser treats a lone separator as a **decimal point**. This is
  correct for the shipped `pse` provider (comma decimal), but if you point the
  generic `scrape` provider at a page that shows integer thousands like
  `"1,385"` with no decimal part, the value will be off by 1000. Prefer
  sources that include the decimal part.
- **Subunit currencies.** Some London-listed pages quote in pence and label the
  currency `GBp`. The tool normalizes `GBp` to ISO `GBP` and does not divide by
  100, so a pence-denominated value scraped this way would be 100x too large.
  If you point the generic `scrape` provider at an LSE pence-quoted page,
  handle the conversion in the sheet.

## Development

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-groups        # create the venv with dev tools
uv run pytest               # tests (no network; providers use recorded fixtures)
uv run ruff check .         # lint
uv run mypy                 # type-check
uv run pip-audit            # dependency vulnerability audit
```

These are also available as [`just`](https://github.com/casey/just) recipes
(`just lint`, `just typecheck`, `just test`, `just check` for all three); run
`just` to list them.

Tests never touch the network: provider extraction is tested against recorded
fixtures in `tests/fixtures/` via a mock HTTP transport, and the Sheets client
is faked to assert a single batched write with exact cell targets.

To enable the local secret-scanning hook (requires
[pre-commit](https://pre-commit.com/) and
[gitleaks](https://github.com/gitleaks/gitleaks)):

```bash
pre-commit install
```

## License

Apache-2.0. See [LICENSE](LICENSE).

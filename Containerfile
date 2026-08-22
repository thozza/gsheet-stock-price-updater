# Reproducible container build for gsheet-stock-price-updater.
#
# Both base images are pinned by digest and dependencies are installed from the
# hashed uv.lock, so a build today and a build next year are byte-comparable.
# See the README "Rebuild and update policy" for when and how to bump the pins.

# --- Builder: resolve and install the locked dependencies into a venv -------- #
FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a AS builder

# uv is copied in from its official image, pinned by digest to match the version
# that produced uv.lock. --frozen makes the install fail if the lock is stale.
COPY --from=ghcr.io/astral-sh/uv:0.12.5@sha256:e85be844203885286c60ffad8a858d48afb6c5a5c237ca0e67f12e74b8f174b1 /uv /bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Only the files needed to build and install the package. Copy the lock first so
# this layer caches unless the dependencies actually change.
COPY pyproject.toml uv.lock README.md ./
COPY src ./src

# Install runtime dependencies and the package itself (non-editable) into
# /app/.venv, using only the hashed lock. No dev tools, no network beyond PyPI.
RUN uv sync --frozen --no-dev --no-editable

# --- Runtime: minimal, non-root, read-only-friendly -------------------------- #
FROM python:3.12-slim@sha256:2c941e860699f878900b0edc2403613c234d4b32eda3cc9fa7036991a2a63c4a

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" \
    # The tool reads the key path from this env var (see config.auth). Mount the
    # real key read-only at this path at run time; it is never baked in.
    GOOGLE_APPLICATION_CREDENTIALS=/run/secrets/service-account.json

# Run as a dedicated non-root user (fixed high UID/GID, no login shell).
RUN groupadd --gid 10001 app \
    && useradd --uid 10001 --gid app --no-create-home --home-dir /app --shell /usr/sbin/nologin app

# Copy just the built virtualenv from the builder; source is not needed at
# runtime because the package is installed non-editable.
COPY --from=builder --chown=app:app /app/.venv /app/.venv

USER app
WORKDIR /app

# No EXPOSE: this container needs no inbound access. It only makes outbound HTTPS
# calls to the price sources and to the Google Sheets API.

ENTRYPOINT ["python", "-m", "gsheet_stock_price_updater"]
# Default arguments; override at run time (e.g. add --dry-run). The config is
# mounted read-only at /config/config.yaml.
CMD ["--config", "/config/config.yaml"]

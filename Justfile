# Common dev recipes. Run `just` to list them.

image_name := "gsheet-stock-price-updater"
image_tag := "latest"
image_tarball := image_name + ".tar.gz"

default:
    @just --list

# Install/sync all dependency groups from the lockfile.
sync:
    uv sync --all-groups --frozen

# Run the linter.
lint:
    uv run ruff check .

# Run the linter and apply safe fixes.
fix:
    uv run ruff check --fix .

# Run the type checker.
typecheck:
    uv run mypy

# Run the test suite.
test:
    uv run pytest

# Run lint, type-check, and tests (same checks as CI).
check: lint typecheck test

# Run the CLI (pass args after --, e.g. `just run -- --config config.yaml`).
run *args:
    uv run gsheet-stock-price-updater {{args}}

# Bump dependency lock and print the current base image digests to refresh
# in the Containerfile (see README "Rebuild and update policy").
upgrade-pins:
    uv lock --upgrade
    skopeo inspect --format '{{ "{{" }}.Digest{{ "}}" }}' docker://python:3.12-slim

# Build the container image locally (podman; docker also works).
image-build:
    podman build -t {{image_name}}:{{image_tag}} -f Containerfile .

# Save the built image to a gzipped tarball for import into Container Manager.
image-save:
    podman save {{image_name}}:{{image_tag}} | gzip > {{image_tarball}}

# Build and save the image in one step.
image: image-build image-save

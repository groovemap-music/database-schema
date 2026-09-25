set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

setup:
    uv sync --dev --frozen

check: format-check lint typecheck coverage contract-check repository-check install-check license-check secret-scan bump-preview

format:
    uv run ruff format .
    uv run ruff check --fix .

format-check:
    uv run ruff format --check .

lint:
    uv run ruff check .

typecheck:
    uv run mypy

test:
    uv run pytest -m "not integration" --cov=groovemap_schema --cov-report=term-missing --cov-report=xml

# Required tier. Starts and removes disposable PostgreSQL 18 and Neo4j containers.
test-integration:
    bash scripts/test-integration.sh

# Advisory tier. Builds pgvector 0.8.6 onto the tier's digest-pinned official
# PostgreSQL 19 beta Alpine image, then runs the same script and Neo4j image
# against that local, never-published image. The HNSW build integration test
# raises maintenance_work_mem for one small synthetic build (see
# docs/architecture.md, "Building the artist HNSW index"); a parallel index
# build needs /dev/shm at least that large, so this tier raises the container's
# shm size past Docker's 64 MB default -- modestly, since the test itself uses
# a modest maintenance_work_mem, not the ~2 GB ADR 0013 documents for production.
test-integration-pg19:
    docker build \
        --build-arg POSTGRES_BASE_IMAGE=postgres:19beta3-alpine@sha256:b1692e50613a21e61c424859f943b9e193ae73e5a8c68abd5382dfb235bf15fc \
        --file scripts/postgres19-pgvector.Dockerfile \
        --tag database-schema-postgres19-pgvector:local \
        .
    POSTGRES_INTEGRATION_IMAGE=database-schema-postgres19-pgvector:local POSTGRES_INTEGRATION_SHM_SIZE=256m bash scripts/test-integration.sh

coverage: test

contract-check:
    uv run python scripts/check-contracts.py

repository-check:
    uv run python scripts/check-repository.py

build:
    uv build --out-dir dist --clear

prepare-runtime-wheel:
    bash scripts/prepare-runtime-wheel.sh

image: build prepare-runtime-wheel
    bash scripts/build-image.sh

install-check: build prepare-runtime-wheel
    bash scripts/install-check.sh

license-check:
    uv run python scripts/check-licenses.py
    uv run pip-licenses --fail-on "GPL-2.0-only;GPL-3.0-only;AGPL-3.0-only"

secret-scan:
    gitleaks git --redact --no-banner
    gitleaks dir . --redact --no-banner

audit:
    uv run pip-audit

bump-preview:
    uv run python scripts/check_bump_preview.py

# Update local version metadata and changelog only; do not commit, tag, push, or publish.
bump:
    uv run cz bump --version-files-only --changelog --yes --check-consistency
    uv lock

release-dry-run: check
    bash scripts/release-dry-run.sh

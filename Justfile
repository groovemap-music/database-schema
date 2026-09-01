set shell := ["bash", "-euo", "pipefail", "-c"]

default:
    @just --list

setup:
    uv sync --dev --frozen

check: format-check lint typecheck test contract-check repository-check build install-check license-check secret-scan bump-preview

# Credential-free validation used when private-library access is unavailable.
source-check: repository-check

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
    uv run pytest --cov=groovemap_schema --cov-report=term-missing --cov-report=xml

contract-check:
    uv run python scripts/check-contracts.py

repository-check:
    python scripts/check-repository.py

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

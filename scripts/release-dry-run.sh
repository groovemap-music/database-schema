#!/usr/bin/env bash
set -euo pipefail

uv build --out-dir dist --clear
(
  cd dist
  shasum -a 256 ./*.whl ./*.tar.gz > SHA256SUMS
)
uv run cyclonedx-py environment --output-file dist/sbom.json
uv run python scripts/check-release-artifacts.py
test -s dist/SHA256SUMS
test -s dist/sbom.json

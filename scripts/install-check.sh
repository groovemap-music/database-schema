#!/usr/bin/env bash
set -euo pipefail

schema_wheel=$(find dist -maxdepth 1 -type f -name 'groovemap_database_schema-*.whl' -print -quit)
test -n "$schema_wheel"

check_dir=$(mktemp -d)
case "$check_dir" in
/tmp/* | /private/tmp/* | /var/folders/*) ;;
*) echo "Unexpected temporary directory: $check_dir" >&2; exit 2 ;;
esac
trap 'rm -rf -- "$check_dir"' EXIT

uv venv --python 3.14.5 "$check_dir/venv"
uv pip install \
  --python "$check_dir/venv/bin/python" \
  --find-links .build/private \
  "$schema_wheel"
"$check_dir/venv/bin/python" - <<'PY'
from importlib.resources import files

from groovemap_schema import NEO4J_SCHEMA_STATEMENTS

assert NEO4J_SCHEMA_STATEMENTS
assert files("groovemap_schema").joinpath("contracts/persistence/v1/compatibility.json").is_file()
PY

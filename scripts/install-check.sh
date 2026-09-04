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

uv venv --python 3.14.7 "$check_dir/venv"
uv pip install \
  --python "$check_dir/venv/bin/python" \
  --find-links .build/private \
  "$schema_wheel"
"$check_dir/venv/bin/python" - <<'PY'
from importlib.metadata import distribution
from importlib.resources import files

from groovemap_schema import NEO4J_SCHEMA_STATEMENTS

assert NEO4J_SCHEMA_STATEMENTS
assert files("groovemap_schema").joinpath("contracts/persistence/v1/compatibility.json").is_file()
dist = distribution("groovemap-database-schema")
assert any(entry.name == "database-schema" and entry.value == "groovemap_schema.initializer:cli" for entry in dist.entry_points)
assert any(str(path).endswith("licenses/LICENSE") for path in (dist.files or []))
assert any(str(path).endswith("licenses/NOTICE") for path in (dist.files or []))
PY
expected_version=$(python - <<'PY'
import tomllib
from pathlib import Path

print(tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"])
PY
)
"$check_dir/venv/bin/database-schema" --version | grep -Fx "database-schema ${expected_version}"
"$check_dir/venv/bin/database-schema" --help | grep -F "Apply the GrooveMap PostgreSQL and Neo4j schemas."

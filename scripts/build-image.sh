#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

if [[ -n "$(git status --short)" ]]; then
  echo "Refusing to label an image from a dirty source tree." >&2
  exit 2
fi

version="$(python - <<'PY'
import tomllib
from pathlib import Path

print(tomllib.loads(Path("pyproject.toml").read_text())["project"]["version"])
PY
)"
revision="$(git rev-parse HEAD)"
build_date="$(git show -s --format=%cI HEAD)"

docker_config="$(mktemp -d)"
trap 'rm -rf "${docker_config}"' EXIT
active_context="$(docker context ls --format '{{if .Current}}{{.Name}}{{end}}' | sed -n '1p')"
docker_host="$(docker context inspect "${active_context}" --format '{{.Endpoints.docker.Host}}')"

expect_invalid_revision_rejected() {
  local invalid_revision="$1"
  local build_log="${docker_config}/invalid-revision-build.log"

  if DOCKER_HOST="${docker_host}" docker --config "${docker_config}" build \
    --build-arg "VCS_REF=${invalid_revision}" \
    --target builder \
    . >"${build_log}" 2>&1; then
    echo "Dockerfile accepted malformed VCS_REF: ${invalid_revision@Q}" >&2
    return 1
  fi
  if ! grep -q "VCS_REF must be exactly 40 lowercase hexadecimal characters" "${build_log}"; then
    cat "${build_log}" >&2
    echo "Dockerfile failed for an unexpected reason while rejecting VCS_REF." >&2
    return 1
  fi
}

expect_invalid_revision_rejected ""
expect_invalid_revision_rejected "not-a-commit"

DOCKER_HOST="${docker_host}" docker --config "${docker_config}" build \
  --build-arg "BUILD_DATE=${build_date}" \
  --build-arg "BUILD_VERSION=${version}" \
  --build-arg "VCS_REF=${revision}" \
  --tag database-schema:local \
  .

DOCKER_HOST="${docker_host}" docker --config "${docker_config}" run --rm \
  --entrypoint /app/.venv/bin/python database-schema:local \
  -c 'from groovemap_schema.initializer import SERVICE_NAME; assert SERVICE_NAME == "database-schema"'

DOCKER_HOST="${docker_host}" docker --config "${docker_config}" image inspect database-schema:local \
  > "${docker_config}/image.json"
python scripts/check-image-metadata.py "${docker_config}/image.json" "${revision}" "${version}"

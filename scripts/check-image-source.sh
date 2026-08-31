#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}"

if ! git rev-parse --verify HEAD >/dev/null 2>&1; then
  echo "Refusing to label an image without a verifiable source revision." >&2
  exit 2
fi

# Image provenance describes the committed first-party source. Hosted workflows
# may add untracked dependency checkouts, while build preparation adds ignored
# artifacts; neither changes HEAD. Staged or unstaged changes to tracked files do.
if ! git diff --quiet HEAD --; then
  echo "Refusing to label an image from modified tracked source." >&2
  exit 2
fi

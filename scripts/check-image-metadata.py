#!/usr/bin/env python3
"""Validate exact metadata on a locally built database-schema image."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from validate_vcs_ref import require_vcs_ref


if len(sys.argv) != 4:
    raise SystemExit("usage: check-image-metadata.py IMAGE_JSON REVISION VERSION")

revision = require_vcs_ref(sys.argv[2])
document = json.loads(Path(sys.argv[1]).read_text())
if not isinstance(document, list) or len(document) != 1:
    raise SystemExit("docker image inspect must return exactly one image")

labels = document[0]["Config"]["Labels"]
require_vcs_ref(labels.get("org.opencontainers.image.revision"))
expected = {
    "org.opencontainers.image.title": "database-schema",
    "org.opencontainers.image.source": "https://github.com/groovemap-music/database-schema",
    "org.opencontainers.image.revision": revision,
    "org.opencontainers.image.version": sys.argv[3],
    "org.opencontainers.image.licenses": "MIT",
}
for name, value in expected.items():
    if labels.get(name) != value:
        raise SystemExit(f"unexpected {name}: {labels.get(name)!r}; expected {value!r}")

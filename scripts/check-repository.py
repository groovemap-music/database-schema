#!/usr/bin/env python3
"""Credential-free checks for the runnable repository distribution contract."""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/groovemap-music/database-schema"


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(message)


project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
require(project["name"] == "groovemap-database-schema", "package name must match the repository")
require(project["license"] == "MIT", "package license must be MIT")
require(project["license-files"] == ["LICENSE", "NOTICE"], "wheel and sdist must carry LICENSE and NOTICE")
require(project["scripts"] == {"database-schema": "groovemap_schema.initializer:cli"}, "console entry point is incorrect")
require(project["urls"]["Repository"] == REPOSITORY, "package repository URL is incorrect")

for required in (
    "LICENSE",
    "NOTICE",
    "README.md",
    "Dockerfile",
    "src/groovemap_schema/initializer.py",
    "src/groovemap_schema/neo4j.py",
    "src/groovemap_schema/postgres.py",
    "docs/README.md",
    "docs/architecture.md",
    "docs/runtime-configuration.md",
):
    require((ROOT / required).is_file(), f"required distribution file is missing: {required}")

dockerfile = (ROOT / "Dockerfile").read_text()
for fragment in (
    'org.opencontainers.image.title="database-schema"',
    f'org.opencontainers.image.source="{REPOSITORY}"',
    'org.opencontainers.image.revision="${VCS_REF}"',
    'org.opencontainers.image.licenses="MIT"',
    'RUN python /tmp/validate_vcs_ref.py "${VCS_REF}"',
    'ENTRYPOINT ["/app/.venv/bin/database-schema"]',
    "USER 1000:1000",
):
    require(fragment in dockerfile, f"Dockerfile distribution contract is missing {fragment}")
require("HEALTHCHECK" not in dockerfile and "EXPOSE" not in dockerfile, "one-shot image must use its exit status, not a health endpoint")

revision_validator = ROOT / "scripts/validate_vcs_ref.py"
valid_revision = "0123456789abcdef0123456789abcdef01234567"
require(
    subprocess.run(  # noqa: S603 - fixed interpreter and repository-owned script
        [sys.executable, revision_validator, valid_revision], check=False
    ).returncode
    == 0,
    "VCS_REF validator rejected an exact lowercase 40-hex revision",
)
for invalid_revision in ("", "abc123", "A" * 40, "g" * 40, valid_revision + "0"):
    require(
        subprocess.run(  # noqa: S603 - fixed interpreter and repository-owned script
            [sys.executable, revision_validator, invalid_revision],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        ).returncode
        != 0,
        f"VCS_REF validator accepted malformed revision: {invalid_revision!r}",
    )

dockerignore = (ROOT / ".dockerignore").read_text().splitlines()
require("dist" not in dockerignore and ".build" not in dockerignore, "Docker context must include built application and runtime wheels")

build_image = (ROOT / "scripts/build-image.sh").read_text()
for fragment in (
    "BUILD_DATE=${build_date}",
    "BUILD_VERSION=${version}",
    "VCS_REF=${revision}",
    'expect_invalid_revision_rejected ""',
    'expect_invalid_revision_rejected "not-a-commit"',
    "--tag database-schema:local",
    "check-image-metadata.py",
):
    require(fragment in build_image, f"local image build does not verify {fragment}")

for workflow_name in ("ci.yml", "release.yml"):
    workflow = (ROOT / ".github/workflows" / workflow_name).read_text()
    callers = re.findall(r"uses:\s+groovemap-music/\.github/\.github/workflows/[^@\s]+@([^\s]+)", workflow)
    require(callers and all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in callers), f"{workflow_name} must pin shared workflows to a full commit")

ci = (ROOT / ".github/workflows/ci.yml").read_text()
release = (ROOT / ".github/workflows/release.yml").read_text()
require("fallback-command: just source-check" in ci, "CI fallback must use the credential-free source gate")
require("image-command: just image" in ci, "CI must build and inspect the repository image")
require("image-name: database-schema" in release, "release image name must match the repository")

readme = (ROOT / "README.md").read_text()
docs_index = (ROOT / "docs/README.md").read_text()
require("docs/README.md" in readme, "README must link the repository documentation index")
require("architecture.md" in docs_index and "runtime-configuration.md" in docs_index, "documentation index is incomplete")
for stale in ("SimplicityGuy", "discogsography", "schema-init"):
    require(stale not in readme and stale not in docs_index, f"published repository docs retain stale name: {stale}")

for markdown in (ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md")), *sorted((ROOT / "contracts").rglob("*.md"))):
    for target in re.findall(r"(?<!!)\[[^]]+\]\(([^)]+)\)", markdown.read_text()):
        if target.startswith(("https://", "http://", "mailto:", "#")):
            continue
        relative = target.split("#", 1)[0]
        if relative:
            require((markdown.parent / relative).exists(), f"broken Markdown link in {markdown.relative_to(ROOT)}: {target}")

print("repository distribution contract passed")

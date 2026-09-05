#!/usr/bin/env python3
"""Credential-free checks for the runnable repository distribution contract."""

from __future__ import annotations

import re
import subprocess
import sys
import tomllib
from pathlib import Path

from repository_source import RepositorySourceError, check_retired_branding


ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "https://github.com/groovemap-music/database-schema"
AUTOMATION_REVISION = "2f34a4da5c552bc23c75edd3d8d81be0a4b3271c"
PYTHON_LIBRARIES_REVISION = "455523ec388fdb9862d7aca65d9434aa7073dcb5"


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
    "bash scripts/check-image-source.sh",
    "BUILD_DATE=${build_date}",
    "BUILD_VERSION=${version}",
    "VCS_REF=${revision}",
    'expect_invalid_revision_rejected ""',
    'expect_invalid_revision_rejected "not-a-commit"',
    "--tag database-schema:local",
    "check-image-metadata.py",
):
    require(fragment in build_image, f"local image build does not verify {fragment}")

ci = (ROOT / ".github/workflows/ci.yml").read_text()
release = (ROOT / ".github/workflows/release.yml").read_text()
require("attestations: write" in release, "release.yml must grant the reusable release attestation permission")
for workflow_name, workflow, reusable_name in (
    ("ci.yml", ci, "reusable-ci.yml"),
    ("release.yml", release, "reusable-release.yml"),
):
    callers = re.findall(rf"uses:\s+groovemap-music/automation/\.github/workflows/{reusable_name}@([^\s]+)", workflow)
    require(callers == [AUTOMATION_REVISION], f"{workflow_name} must pin {reusable_name} to the approved automation commit")
    require("groovemap-music/.github/" not in workflow, f"{workflow_name} retains the superseded shared-workflow repository")
    require("secrets: inherit" not in workflow, f"{workflow_name} must pass only the secrets required by its reusable workflow")

for fragment in (
    "pull_request:",
    "schedule:",
    "workflow_dispatch:",
    "language: python",
    "setup-command: just setup",
    "check-command: just check",
    "coverage-command: just test",
    "audit-command: just audit",
    "license-command: just license-check",
    "secret-scan-command: just secret-scan",
    "package-command: just build",
    "install-command: just install-check",
    "image-command: just image",
    "coverage-files: coverage.xml",
    "upload-codecov: true",
    "CODECOV_TOKEN: ${{ secrets.CODECOV_TOKEN }}",
):
    require(fragment in ci, f"CI does not execute the full shared validation contract: {fragment}")
for forbidden in ("fallback-command", "github.actor", "dependabot", "if:"):
    require(forbidden not in ci.lower(), f"CI must not reduce or skip checks based on pull-request author: {forbidden}")
for forbidden in (
    "requires-private-library",
    "private-library-client-id",
    "private-library-revision",
    "private_library_private_key",
    "groovemap_ci_app_client_id",
    "groovemap_ci_app_private_key",
):
    require(forbidden not in ci.lower(), f"CI retains obsolete private-library credential configuration: {forbidden}")
ci_jobs = ci.split("jobs:\n", 1)[1]
require(len(re.findall(r"^  [a-zA-Z0-9_-]+:\s*$", ci_jobs, re.MULTILINE)) == 1, "CI must expose one required caller job for every event")

require(re.search(r'on:\s*\n  push:\s*\n    tags: \["v\*"\]', release) is not None, "release must be restricted to pushed version tags")
for fragment in (
    "repository-name: database-schema",
    "setup-command: just setup",
    "check-command: just check",
    "release-command: just release-dry-run",
    "dist/*.whl",
    "dist/*.tar.gz",
    "publish-image: true",
    "build-context: .",
    "dockerfile: Dockerfile",
    "prepare-image-command: just prepare-runtime-wheel",
):
    require(fragment in release, f"release does not execute the full shared release contract: {fragment}")
for forbidden in ("workflow_dispatch:", "schedule:", "branches:", "latest", "secrets: inherit"):
    require(forbidden not in release.lower(), f"release caller contains a forbidden mutable or non-tag path: {forbidden}")
for forbidden in (
    "requires-private-library",
    "private-library-client-id",
    "private-library-revision",
    "private_library_private_key",
    "groovemap_ci_app_client_id",
    "groovemap_ci_app_private_key",
):
    require(forbidden not in release.lower(), f"release retains obsolete private-library credential configuration: {forbidden}")

pyproject_text = (ROOT / "pyproject.toml").read_text()
require("https://github.com/groovemap-music/python-libraries.git" in pyproject_text, "public python-libraries source is missing")
require(PYTHON_LIBRARIES_REVISION in pyproject_text, "python-libraries source is not pinned to the approved revision")

for forbidden_path in (
    ROOT / "renovate.json",
    ROOT / "renovate.json5",
    ROOT / ".github/renovate.json",
    ROOT / ".github/renovate.json5",
):
    require(not forbidden_path.exists(), f"Renovate configuration must not coexist with Dependabot: {forbidden_path.relative_to(ROOT)}")
require(not list((ROOT / ".github/workflows").glob("*claude*")), "legacy Claude workflow must not exist")

retired_name = "discogs" + "ography"
published_extensions = {".json", ".md", ".py", ".sh", ".toml", ".yaml", ".yml"}
try:
    check_retired_branding(
        ROOT,
        retired_name=retired_name,
        published_extensions=published_extensions,
        excluded_parts={".git", ".venv", ".build", "dist"},
    )
except RepositorySourceError as error:
    raise SystemExit(str(error)) from error

readme = (ROOT / "README.md").read_text()
docs_index = (ROOT / "docs/README.md").read_text()
require("docs/README.md" in readme, "README must link the repository documentation index")
require("architecture.md" in docs_index and "runtime-configuration.md" in docs_index, "documentation index is incomplete")
for stale in ("SimplicityGuy", retired_name, "schema-init"):
    require(stale not in readme and stale not in docs_index, f"published repository docs retain stale name: {stale}")

for markdown in (ROOT / "README.md", *sorted((ROOT / "docs").rglob("*.md")), *sorted((ROOT / "contracts").rglob("*.md"))):
    for target in re.findall(r"(?<!!)\[[^]]+\]\(([^)]+)\)", markdown.read_text()):
        if target.startswith(("https://", "http://", "mailto:", "#")):
            continue
        relative = target.split("#", 1)[0]
        if relative:
            require((markdown.parent / relative).exists(), f"broken Markdown link in {markdown.relative_to(ROOT)}: {target}")

print("repository distribution contract passed")

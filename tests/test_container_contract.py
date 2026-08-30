"""Static tests for the independently buildable schema image contract."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parent.parent
AUTOMATION_REVISION = "2f34a4da5c552bc23c75edd3d8d81be0a4b3271c"
PRIVATE_LIBRARY_REVISION = "28fa329702bc76896cc54ab8d05ec5b1bd3d929e"


def test_dockerfile_uses_repository_image_identity() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert 'org.opencontainers.image.title="database-schema"' in dockerfile
    assert 'org.opencontainers.image.source="https://github.com/groovemap-music/database-schema"' in dockerfile
    assert 'org.opencontainers.image.revision="${VCS_REF}"' in dockerfile
    assert 'RUN python /tmp/validate_vcs_ref.py "${VCS_REF}"' in dockerfile
    assert 'org.opencontainers.image.licenses="MIT"' in dockerfile
    assert 'ENTRYPOINT ["/app/.venv/bin/database-schema"]' in dockerfile


def test_dockerfile_runs_as_numeric_non_root_user() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "USER 1000:1000" in dockerfile
    assert "USER root" not in dockerfile


def test_one_shot_image_uses_exit_status_as_health_contract() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "HEALTHCHECK" not in dockerfile
    assert "EXPOSE" not in dockerfile


def test_local_image_name_matches_repository() -> None:
    script = (ROOT / "scripts" / "build-image.sh").read_text()
    assert "--tag database-schema:local" in script
    assert "VCS_REF=${revision}" in script
    assert 'expect_invalid_revision_rejected ""' in script
    assert 'expect_invalid_revision_rejected "not-a-commit"' in script
    assert "BUILD_VERSION=${version}" in script
    assert "BUILD_DATE=${build_date}" in script
    assert "check-image-metadata.py" in script


@pytest.mark.parametrize("revision", ["", "abc123", "A" * 40, "g" * 40, "0" * 39, "0" * 41])
def test_docker_revision_validator_rejects_blank_or_malformed_values(revision: str) -> None:
    result = subprocess.run(  # noqa: S603 - fixed interpreter and repository-owned script
        [sys.executable, ROOT / "scripts/validate_vcs_ref.py", revision],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "exactly 40 lowercase hexadecimal characters" in result.stderr


def test_docker_revision_validator_accepts_exact_commit() -> None:
    revision = "0123456789abcdef0123456789abcdef01234567"
    subprocess.run(  # noqa: S603 - fixed interpreter and repository-owned script
        [sys.executable, ROOT / "scripts/validate_vcs_ref.py", revision], check=True
    )


@pytest.mark.parametrize("revision", ["", "abc123", "A" * 40, "g" * 40, "0" * 39, "0" * 41])
def test_image_metadata_check_rejects_blank_or_malformed_revision(tmp_path: Path, revision: str) -> None:
    inspection = [
        {
            "Config": {
                "Labels": {
                    "org.opencontainers.image.title": "database-schema",
                    "org.opencontainers.image.source": "https://github.com/groovemap-music/database-schema",
                    "org.opencontainers.image.revision": revision,
                    "org.opencontainers.image.version": "0.1.0",
                    "org.opencontainers.image.licenses": "MIT",
                }
            }
        }
    ]
    image_json = tmp_path / "image.json"
    image_json.write_text(json.dumps(inspection))

    result = subprocess.run(  # noqa: S603 - fixed interpreter and repository-owned script
        [sys.executable, ROOT / "scripts/check-image-metadata.py", image_json, revision, "0.1.0"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "exactly 40 lowercase hexadecimal characters" in result.stderr


def test_legal_and_repository_documentation_contract() -> None:
    project = (ROOT / "pyproject.toml").read_text()
    readme = (ROOT / "README.md").read_text()
    docs = (ROOT / "docs" / "README.md").read_text()

    assert 'license-files = ["LICENSE", "NOTICE"]' in project
    assert (ROOT / "LICENSE").read_text().startswith("MIT License\n")
    assert (ROOT / "NOTICE").read_text().startswith("GrooveMap database-schema\n")
    assert "docs/README.md" in readme
    assert "architecture.md" in docs
    assert "runtime-configuration.md" in docs


def test_reusable_workflows_are_immutably_pinned() -> None:
    expected = {
        "ci.yml": "reusable-ci.yml",
        "release.yml": "reusable-release.yml",
    }
    for name, reusable_name in expected.items():
        workflow = (ROOT / ".github" / "workflows" / name).read_text()
        refs = re.findall(
            rf"uses: groovemap-music/automation/\.github/workflows/{reusable_name}@([^\s]+)",
            workflow,
        )
        assert refs == [AUTOMATION_REVISION]
        assert "groovemap-music/.github/" not in workflow
        assert "secrets: inherit" not in workflow


def test_dependabot_pull_requests_run_the_ordinary_required_ci_graph() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()

    assert "pull_request:" in workflow
    assert "schedule:" in workflow
    assert "workflow_dispatch:" in workflow
    jobs = workflow.split("jobs:\n", 1)[1]
    assert len(re.findall(r"^  [a-zA-Z0-9_-]+:\s*$", jobs, re.MULTILINE)) == 1
    assert "jobs:\n  required:" in workflow
    assert "github.actor" not in workflow.lower()
    assert "dependabot" not in workflow.lower()
    assert "fallback-command" not in workflow
    assert "if:" not in workflow.lower()

    for fragment in (
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
        "requires-private-library: true",
        "private-library-client-id: ${{ vars.GROOVEMAP_CI_APP_CLIENT_ID }}",
        f"private-library-revision: {PRIVATE_LIBRARY_REVISION}",
        "PRIVATE_LIBRARY_PRIVATE_KEY: ${{ secrets.GROOVEMAP_CI_APP_PRIVATE_KEY }}",
        "CODECOV_TOKEN: ${{ secrets.CODECOV_TOKEN }}",
    ):
        assert fragment in workflow


def test_release_is_tag_only_and_uses_repository_named_image() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()

    assert re.search(r'on:\s*\n  push:\s*\n    tags: \["v\*"\]', workflow)
    assert "workflow_dispatch:" not in workflow
    assert "schedule:" not in workflow
    assert "branches:" not in workflow
    assert "repository-name: database-schema" in workflow
    assert "release-command: just release-dry-run" in workflow
    assert "publish-image: true" in workflow
    assert "prepare-image-command: just prepare-runtime-wheel" in workflow
    assert "latest" not in workflow.lower()


def test_release_dry_run_produces_shared_automation_evidence() -> None:
    script = (ROOT / "scripts" / "release-dry-run.sh").read_text()

    assert "dist/SHA256SUMS" in script
    assert "dist/sbom.json" in script
    assert "dist/THIRD_PARTY_NOTICES.json" in script
    assert "pip-licenses --format=json" in script

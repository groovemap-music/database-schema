"""Static tests for the independently buildable schema image contract."""

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parent.parent


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
    for name in ("ci.yml", "release.yml"):
        workflow = (ROOT / ".github" / "workflows" / name).read_text()
        refs = re.findall(r"uses: groovemap-music/\.github/\.github/workflows/[^@]+@([^\s]+)", workflow)
        assert refs
        assert all(re.fullmatch(r"[0-9a-f]{40}", ref) for ref in refs)

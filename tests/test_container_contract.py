"""Static tests for the independently buildable schema image contract."""

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).parent.parent
AUTOMATION_REVISION = "833cb464507678c38ab78bd4718ce697399463e9"
PYTHON_LIBRARIES_REVISION = "6e84fe9acfd9551bd3bba2f2e78fef0ec1ef38ef"
PG19_BASE_IMAGE = "postgres:19beta3-alpine@sha256:b1692e50613a21e61c424859f943b9e193ae73e5a8c68abd5382dfb235bf15fc"
PG19_LOCAL_IMAGE = "database-schema-postgres19-pgvector:local"


def _required_executable(name: str) -> str:
    executable = shutil.which(name)
    if executable is None:
        raise RuntimeError(f"required test executable is unavailable: {name}")
    return executable


GIT = _required_executable("git")
BASH = _required_executable("bash")


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
    assert "bash scripts/check-image-source.sh" in script
    assert "--tag database-schema:local" in script
    assert "VCS_REF=${revision}" in script
    assert 'expect_invalid_revision_rejected ""' in script
    assert 'expect_invalid_revision_rejected "not-a-commit"' in script
    assert "BUILD_VERSION=${version}" in script
    assert "BUILD_DATE=${build_date}" in script
    assert "check-image-metadata.py" in script


def _image_source_repository(tmp_path: Path) -> Path:
    repository = tmp_path / "repository"
    scripts = repository / "scripts"
    scripts.mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "check-image-source.sh", scripts)
    (repository / "tracked.txt").write_text("committed\n")
    subprocess.run([GIT, "init", "--quiet"], cwd=repository, check=True)  # noqa: S603
    subprocess.run([GIT, "add", "."], cwd=repository, check=True)  # noqa: S603
    subprocess.run(  # noqa: S603
        [
            GIT,
            "-c",
            "user.name=GrooveMap Test",
            "-c",
            "user.email=test@groovemap.music",
            "commit",
            "--quiet",
            "-m",
            "test: establish image source",
        ],
        cwd=repository,
        check=True,
    )
    return repository


def test_image_source_check_allows_hosted_dependency_checkout(tmp_path: Path) -> None:
    repository = _image_source_repository(tmp_path)
    dependency = repository / "python-libraries"
    dependency.mkdir()
    (dependency / "README.md").write_text("workflow-injected dependency\n")

    subprocess.run([BASH, "scripts/check-image-source.sh"], cwd=repository, check=True)  # noqa: S603


@pytest.mark.parametrize("staged", [False, True], ids=["unstaged", "staged"])
def test_image_source_check_rejects_modified_tracked_source(tmp_path: Path, staged: bool) -> None:
    repository = _image_source_repository(tmp_path)
    (repository / "tracked.txt").write_text("modified\n")
    if staged:
        subprocess.run([GIT, "add", "tracked.txt"], cwd=repository, check=True)  # noqa: S603

    result = subprocess.run(  # noqa: S603
        [BASH, "scripts/check-image-source.sh"],
        cwd=repository,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "modified tracked source" in result.stderr


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
    assert re.findall(r"^  ([a-zA-Z0-9_-]+):\s*$", jobs, re.MULTILINE) == ["required", "postgres-19-beta"]
    assert "jobs:\n  required:" in workflow
    assert "github.actor" not in workflow.lower()
    assert "dependabot" not in workflow.lower()
    assert "fallback-command" not in workflow
    assert "if:" not in workflow.lower()

    for fragment in (
        "language: python",
        "setup-command: just setup",
        "check-command: just check",
        "coverage-command: just coverage",
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
        assert fragment in workflow

    for marker in (
        "requires-private-library",
        "private-library-client-id",
        "private-library-revision",
        "private_library_private_key",
        "groovemap_ci_app_client_id",
        "groovemap_ci_app_private_key",
    ):
        assert marker not in workflow.lower()

    pyproject = (ROOT / "pyproject.toml").read_text()
    assert "https://github.com/groovemap-music/python-libraries.git" in pyproject
    assert PYTHON_LIBRARIES_REVISION in pyproject


def test_integration_runs_a_required_and_an_advisory_engine_tier() -> None:
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text()
    justfile = (ROOT / "Justfile").read_text()

    required_job, advisory_job = workflow.split("  postgres-19-beta:\n", 1)

    # PostgreSQL 18 is the gate: the reusable caller runs it and must fail the workflow.
    assert "integration-command: just test-integration\n" in required_job
    assert "continue-on-error" not in required_job

    # PostgreSQL 19 beta reports without blocking until general availability.
    assert "run: just test-integration-pg19" in advisory_job
    assert "continue-on-error: true" in advisory_job

    # One parameterized script serves both tiers; only the PostgreSQL image differs.
    script = (ROOT / "scripts" / "test-integration.sh").read_text()
    assert "POSTGRES_INTEGRATION_IMAGE" in script
    assert "NEO4J_INTEGRATION_IMAGE" in script
    assert re.search(r"\ntest-integration:\n    bash scripts/test-integration\.sh\n", justfile)
    assert "NEO4J_INTEGRATION_IMAGE" not in justfile

    # The PostgreSQL 19 beta tier builds pgvector onto the tier's digest-pinned
    # official image and runs the required script against that local image only.
    assert f"--build-arg POSTGRES_BASE_IMAGE={PG19_BASE_IMAGE}" in justfile
    assert "--file scripts/postgres19-pgvector.Dockerfile" in justfile
    assert f"--tag {PG19_LOCAL_IMAGE}" in justfile
    assert f"POSTGRES_INTEGRATION_IMAGE={PG19_LOCAL_IMAGE} POSTGRES_INTEGRATION_SHM_SIZE=256m bash scripts/test-integration.sh" in justfile
    assert "docker push" not in justfile


def test_postgres19_tier_raises_shared_memory_for_the_hnsw_build() -> None:
    """A parallel HNSW build needs `/dev/shm` at least as large as `maintenance_work_mem`.

    The PostgreSQL 18 tier never builds an HNSW index and keeps Docker's own
    64 MB default; only the PostgreSQL 19 tier's recipe overrides it, and only
    to fit its own modest, test-only `maintenance_work_mem` (see
    docs/architecture.md, "Building the artist HNSW index") -- never the ~2 GB
    ADR 0013 documents for production.
    """
    justfile = (ROOT / "Justfile").read_text()
    script = (ROOT / "scripts" / "test-integration.sh").read_text()

    assert "POSTGRES_INTEGRATION_SHM_SIZE" in script
    assert '--shm-size "${postgres_shm_size}"' in script
    assert "POSTGRES_INTEGRATION_SHM_SIZE=256m" in justfile
    assert re.search(r"\ntest-integration:\n    bash scripts/test-integration\.sh\n", justfile), (
        "the required PostgreSQL 18 tier must keep Docker's shm-size default"
    )


def test_postgres19_pgvector_image_builds_from_the_pinned_tier_base() -> None:
    dockerfile = (ROOT / "scripts" / "postgres19-pgvector.Dockerfile").read_text()

    assert f"ARG POSTGRES_BASE_IMAGE={PG19_BASE_IMAGE}" in dockerfile
    assert "FROM ${POSTGRES_BASE_IMAGE}" in dockerfile
    assert "ARG PGVECTOR_VERSION=v0.8.6" in dockerfile
    assert '--branch "${PGVECTOR_VERSION}"' in dockerfile
    assert "https://github.com/pgvector/pgvector.git" in dockerfile

    # Build tools are installed and removed within the same RUN layer.
    build_step = next(line for line in dockerfile.splitlines() if line.strip().startswith("RUN apk add"))
    run_layer_start = dockerfile.index(build_step)
    run_layer = dockerfile[run_layer_start:]
    assert "apk del .build-deps" in run_layer
    assert "HEALTHCHECK" not in dockerfile and "EXPOSE" not in dockerfile


def test_integration_cleanup_removes_test_container_anonymous_volumes() -> None:
    script = (ROOT / "scripts" / "test-integration.sh").read_text()

    assert 'docker rm --force --volumes "${postgres_container}" "${neo4j_container}"' in script
    assert "docker volume prune" not in script
    assert "docker volume rm" not in script


def test_release_is_tag_only_and_uses_repository_named_image() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text()

    assert re.search(r"permissions:\s*\n(?:  [^\n]+\n)*  attestations: write", workflow)
    assert re.search(r'on:\s*\n  push:\s*\n    tags: \["v\*"\]', workflow)
    assert "workflow_dispatch:" not in workflow
    assert "schedule:" not in workflow
    assert "branches:" not in workflow
    assert "repository-name: database-schema" in workflow
    assert "release-command: just release-dry-run" in workflow
    assert "publish-image: true" in workflow
    assert "prepare-image-command: just prepare-runtime-wheel" in workflow
    assert "latest" not in workflow.lower()
    for marker in (
        "requires-private-library",
        "private-library-client-id",
        "private-library-revision",
        "private_library_private_key",
        "groovemap_ci_app_client_id",
        "groovemap_ci_app_private_key",
    ):
        assert marker not in workflow.lower()


def test_release_dry_run_produces_shared_automation_evidence() -> None:
    script = (ROOT / "scripts" / "release-dry-run.sh").read_text()

    assert "dist/SHA256SUMS" in script
    assert "dist/sbom.json" in script
    assert "dist/THIRD_PARTY_NOTICES.json" in script
    assert "pip-licenses --format=json" in script

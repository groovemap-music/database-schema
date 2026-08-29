"""Static tests for the independently buildable schema image contract."""

from pathlib import Path


ROOT = Path(__file__).parent.parent


def test_dockerfile_uses_repository_image_identity() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert 'org.opencontainers.image.title="database-schema"' in dockerfile
    assert "github.com/groovemap-music/database-schema" in dockerfile
    assert 'ENTRYPOINT ["/app/.venv/bin/database-schema"]' in dockerfile


def test_dockerfile_runs_as_numeric_non_root_user() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()
    assert "USER 1000:1000" in dockerfile
    assert "USER root" not in dockerfile


def test_local_image_name_matches_repository() -> None:
    script = (ROOT / "scripts" / "build-image.sh").read_text()
    assert "--tag database-schema:local" in script

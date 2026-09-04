"""Regression tests for the repository's bounded Python support contract."""

import sys
import tomllib
from pathlib import Path
from typing import Any


ROOT = Path(__file__).parent.parent
SUPPORTED_MINOR = (3, 14)
SUPPORTED_PATCH = "3.14.7"


def _load_toml(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text())


def test_test_suite_runs_on_the_supported_python_minor() -> None:
    assert sys.version_info[:2] == SUPPORTED_MINOR


def test_package_and_tooling_target_python_314() -> None:
    project = _load_toml(ROOT / "pyproject.toml")

    assert project["project"]["requires-python"] == ">=3.14,<3.15"
    assert "Programming Language :: Python :: 3.14" in project["project"]["classifiers"]
    assert "Programming Language :: Python :: 3.13" not in project["project"]["classifiers"]
    assert project["tool"]["ruff"]["target-version"] == "py314"
    assert project["tool"]["mypy"]["python_version"] == "3.14"


def test_managed_runtime_and_lock_match_the_support_contract() -> None:
    mise = _load_toml(ROOT / ".mise.toml")
    lock = _load_toml(ROOT / "uv.lock")

    assert mise["tools"]["python"] == SUPPORTED_PATCH
    assert (ROOT / ".python-version").read_text().strip() == SUPPORTED_PATCH
    assert lock["requires-python"] == "==3.14.*"


def test_install_check_uses_the_managed_python_patch() -> None:
    install_check = (ROOT / "scripts" / "install-check.sh").read_text()

    assert f"uv venv --python {SUPPORTED_PATCH}" in install_check


def test_active_project_surfaces_do_not_claim_python_313() -> None:
    active_paths = [
        ROOT / ".github",
        ROOT / "contracts",
        ROOT / "docs",
        ROOT / "scripts",
        ROOT / "README.md",
        ROOT / "Dockerfile",
        ROOT / "pyproject.toml",
    ]
    stale_markers = ("Python :: 3.13", "Python 3.13", 'python = "3.13', 'python_version = "3.13', "py313")
    text_suffixes = {".json", ".md", ".py", ".sh", ".toml", ".yaml", ".yml"}

    for path in active_paths:
        candidates = path.rglob("*") if path.is_dir() else [path]
        for candidate in candidates:
            if candidate.is_file() and (not candidate.suffix or candidate.suffix in text_suffixes):
                content = candidate.read_text()
                assert not any(marker in content for marker in stale_markers), f"stale Python 3.13 policy in {candidate.relative_to(ROOT)}"


def test_container_uses_the_managed_python_patch() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert f"ARG PYTHON_IMAGE=python:{SUPPORTED_PATCH}-slim@sha256:" in dockerfile
    assert f'org.opencontainers.image.base.name="docker.io/library/python:{SUPPORTED_PATCH}-slim"' in dockerfile

"""Exercise the full source checker with independent CI and release revisions."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
CI_REVISION = "2f890111657d9f3e6f55d8bd5a5e7b8f9ca97b26"
RELEASE_REVISION = "833cb464507678c38ab78bd4718ce697399463e9"


def indexed_fixture(root: Path) -> None:
    """Copy tracked working-tree source, excluding generated/private directories."""
    result = subprocess.run(  # noqa: S603 - fixed Git read of this repository's tracked source
        ["git", "-C", str(ROOT), "ls-files", "-z"],  # noqa: S607 - trusted test PATH
        check=True,
        capture_output=True,
        text=True,
    )
    for name in result.stdout.split("\0"):
        if not name:
            continue
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    for arguments in (["init", "--quiet"], ["add", "."]):
        subprocess.run(  # noqa: S603 - fixed setup of a disposable source-policy fixture
            ["git", "-C", str(root), *arguments],  # noqa: S607 - trusted test PATH
            check=True,
            capture_output=True,
        )


@pytest.mark.parametrize(
    ("workflow_name", "before", "after", "expected_valid"),
    [
        pytest.param("ci.yml", CI_REVISION, CI_REVISION, True, id="current-ci-and-unchanged-release"),
        pytest.param("ci.yml", CI_REVISION, RELEASE_REVISION, False, id="old-ci-rejected"),
        pytest.param("ci.yml", CI_REVISION, "1" * 40, False, id="foreign-ci-revision-rejected"),
        pytest.param("ci.yml", "groovemap-music/automation", "foreign/automation", False, id="foreign-ci-owner-rejected"),
        pytest.param("release.yml", RELEASE_REVISION, CI_REVISION, False, id="ci-revision-for-release-rejected"),
        pytest.param("release.yml", RELEASE_REVISION, "1" * 40, False, id="foreign-release-revision-rejected"),
    ],
)
def test_exact_independent_workflow_policy(tmp_path: Path, workflow_name: str, before: str, after: str, expected_valid: bool) -> None:
    indexed_fixture(tmp_path)
    workflow = tmp_path / ".github" / "workflows" / workflow_name
    text = workflow.read_text()
    assert text.count(before) == 1
    workflow.write_text(text.replace(before, after))

    result = subprocess.run(  # noqa: S603 - actual repository-owned checker in an isolated fixture
        [sys.executable, str(tmp_path / "scripts" / "check-repository.py")],
        cwd=tmp_path,
        check=False,
        capture_output=True,
        text=True,
    )

    if expected_valid:
        assert result.returncode == 0, result.stdout + result.stderr
        assert "repository distribution contract passed" in result.stdout
    else:
        assert result.returncode != 0
        reusable_name = "reusable-ci.yml" if workflow_name == "ci.yml" else "reusable-release.yml"
        assert f"{workflow_name} must pin {reusable_name} to the approved automation commit" in result.stderr

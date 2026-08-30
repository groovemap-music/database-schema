from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

from scripts.repository_source import RepositorySourceError, check_retired_branding, tracked_files


if TYPE_CHECKING:
    from pathlib import Path


PUBLISHED_EXTENSIONS = {".md", ".py"}
EXCLUDED_PARTS = {".git", ".venv", ".build", "dist"}
RETIRED_NAME = "discogs" + "ography"


def run_git(root: Path, *arguments: str) -> None:
    subprocess.run(  # noqa: S603 - fixed Git setup used only for disposable test repositories
        ["git", "-C", str(root), *arguments],  # noqa: S607 - Git is resolved from the test environment PATH
        check=True,
        capture_output=True,
    )


def initialize_repository(root: Path) -> None:
    run_git(root, "init", "--quiet")


def check_repository(root: Path) -> None:
    check_retired_branding(
        root,
        retired_name=RETIRED_NAME,
        published_extensions=PUBLISHED_EXTENSIONS,
        excluded_parts=EXCLUDED_PARTS,
    )


def test_tracked_retired_branding_is_rejected(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text(f"Retired project: {RETIRED_NAME}\n", encoding="utf-8")
    run_git(tmp_path, "add", "README.md")

    with pytest.raises(RepositorySourceError, match=r"published source retains retired project branding: README\.md"):
        check_repository(tmp_path)


def test_untracked_injected_dependency_is_outside_source_boundary(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text("GrooveMap database schema\n", encoding="utf-8")
    injected_readme = tmp_path / "python-libraries" / "README.md"
    injected_readme.parent.mkdir()
    injected_readme.write_text(f"Historical dependency content: {RETIRED_NAME}\n", encoding="utf-8")
    run_git(tmp_path, "add", "README.md")

    check_repository(tmp_path)

    assert tracked_files(tmp_path) == (readme,)


def test_git_inventory_failure_is_reported_and_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(RepositorySourceError, match=r"cannot obtain Git-tracked source inventory .*not a git repository"):
        check_repository(tmp_path)


def test_missing_tracked_source_is_reported_and_fails_closed(tmp_path: Path) -> None:
    initialize_repository(tmp_path)
    readme = tmp_path / "README.md"
    readme.write_text("GrooveMap database schema\n", encoding="utf-8")
    run_git(tmp_path, "add", "README.md")
    readme.unlink()

    with pytest.raises(RepositorySourceError, match=r"tracked published source is unavailable: README\.md"):
        check_repository(tmp_path)

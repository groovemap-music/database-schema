"""First-party source inventory helpers for repository policy checks."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


class RepositorySourceError(RuntimeError):
    """Raised when the repository source boundary cannot be trusted."""


def tracked_files(root: Path) -> tuple[Path, ...]:
    """Return the deterministic Git-tracked file inventory below ``root``."""
    try:
        result = subprocess.run(  # noqa: S603 - fixed Git query with no user-controlled arguments
            ["git", "-C", str(root), "ls-files", "--cached", "-z"],  # noqa: S607 - Git is resolved from the trusted CI PATH
            check=False,
            capture_output=True,
        )
    except OSError as error:
        raise RepositorySourceError(f"cannot obtain Git-tracked source inventory: {error}") from error

    if result.returncode != 0:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        suffix = f": {detail}" if detail else ""
        raise RepositorySourceError(f"cannot obtain Git-tracked source inventory (git exited {result.returncode}){suffix}")

    if result.stdout and not result.stdout.endswith(b"\0"):
        raise RepositorySourceError("cannot trust Git-tracked source inventory: output is not NUL-terminated")

    files: list[Path] = []
    for raw_path in result.stdout.split(b"\0"):
        if not raw_path:
            continue
        relative = Path(os.fsdecode(raw_path))
        if relative.is_absolute() or ".." in relative.parts:
            raise RepositorySourceError(f"cannot trust Git-tracked source inventory: unsafe path {relative!s}")
        files.append(root / relative)
    return tuple(sorted(files, key=lambda path: os.fsencode(path.relative_to(root))))


def check_retired_branding(
    root: Path,
    *,
    retired_name: str,
    published_extensions: set[str],
    excluded_parts: set[str],
) -> None:
    """Reject retired branding in tracked, publication-relevant source files."""
    for path in tracked_files(root):
        relative = path.relative_to(root)
        if path.suffix not in published_extensions or any(part in excluded_parts for part in relative.parts):
            continue
        if not path.is_file():
            raise RepositorySourceError(f"tracked published source is unavailable: {relative}")
        try:
            contents = path.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as error:
            raise RepositorySourceError(f"cannot inspect tracked published source {relative}: {error}") from error
        if retired_name in contents.lower():
            raise RepositorySourceError(f"published source retains retired project branding: {relative}")

#!/usr/bin/env python3
"""Reject image revisions that are not exact Git commit object names."""

from __future__ import annotations

import re
import sys


EXACT_GIT_REVISION = re.compile(r"[0-9a-f]{40}")


def is_valid_vcs_ref(value: object) -> bool:
    """Return whether value is exactly one lowercase 40-hex Git revision."""
    return isinstance(value, str) and EXACT_GIT_REVISION.fullmatch(value) is not None


def require_vcs_ref(value: object) -> str:
    """Return a valid revision or terminate the calling check."""
    if not is_valid_vcs_ref(value):
        raise SystemExit("VCS_REF must be exactly 40 lowercase hexadecimal characters")
    return value


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("usage: validate_vcs_ref.py VCS_REF")
    require_vcs_ref(sys.argv[1])

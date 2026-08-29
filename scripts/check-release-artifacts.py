#!/usr/bin/env python3
"""Verify that dry-run package artifacts contain the runnable and legal contract."""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path


DIST = Path("dist")
wheel = next(DIST.glob("groovemap_database_schema-*.whl"))
source = next(DIST.glob("groovemap_database_schema-*.tar.gz"))

with zipfile.ZipFile(wheel) as archive:
    names = archive.namelist()
    for suffix in (
        "groovemap_schema/initializer.py",
        "groovemap_schema/neo4j.py",
        "groovemap_schema/postgres.py",
        ".dist-info/entry_points.txt",
        ".dist-info/licenses/LICENSE",
        ".dist-info/licenses/NOTICE",
    ):
        if not any(name.endswith(suffix) for name in names):
            raise SystemExit(f"wheel is missing {suffix}")

with tarfile.open(source, "r:gz") as archive:
    names = archive.getnames()
    for suffix in ("/LICENSE", "/NOTICE", "/README.md", "/src/groovemap_schema/initializer.py"):
        if not any(name.endswith(suffix) for name in names):
            raise SystemExit(f"source distribution is missing {suffix}")

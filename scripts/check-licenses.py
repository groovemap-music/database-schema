"""Validate first-party licensing metadata."""

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
with (ROOT / "pyproject.toml").open("rb") as source:
    project = tomllib.load(source)["project"]

assert project["license"] == "MIT"
assert project["license-files"] == ["LICENSE", "NOTICE"]
license_text = (ROOT / "LICENSE").read_text()
assert license_text.startswith("MIT License\n")
assert "Permission is hereby granted, free of charge" in license_text
notice_text = (ROOT / "NOTICE").read_text()
assert notice_text.startswith("GrooveMap database-schema\n")
assert "current source tree is licensed under the MIT License" in notice_text

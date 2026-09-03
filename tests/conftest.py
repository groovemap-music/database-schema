"""Shared fixtures for the database-schema test suite."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING

import pytest


if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _scrub_otel_environment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Remove every OTEL_* variable so tests never inherit telemetry config from the shell.

    The OpenTelemetry SDK reads its configuration from these standard environment variables;
    a developer's shell or CI runner exporting one of them (for example
    OTEL_EXPORTER_OTLP_ENDPOINT) would otherwise make a test connect toward a real collector
    or silently change which code path it exercises.
    """
    for name in list(os.environ):
        if name.startswith("OTEL_"):
            monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture(autouse=True)
def _reset_telemetry_provider(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Guarantee a pristine common.telemetry provider before and after every test."""
    from common import telemetry

    monkeypatch.setattr(telemetry, "_provider", None)
    monkeypatch.setattr(telemetry, "_sdk_provider", None)
    yield
    monkeypatch.setattr(telemetry, "_provider", None)
    monkeypatch.setattr(telemetry, "_sdk_provider", None)

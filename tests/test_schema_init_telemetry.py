"""Tests for the groovemap.schema_init.duration domain instrument.

These exercise the real OpenTelemetry SDK (through common.telemetry.setup_telemetry) with the
OTLP exporter substituted for an in-memory reader, so nothing here performs network I/O.

Only one test drives setup_telemetry() down its "configured" (non-no-op) path, and it does so
exactly once: the OpenTelemetry API's global meter provider registry can be set at most once
per process (a second set_meter_provider() call is a silent no-op), and
groovemap_schema.initializer captures its meter once at import time via that global registry.
Both scenarios below therefore share a single setup_telemetry() call and a single reader,
sequencing two runs of main() with shutdown_telemetry() stubbed out so the provider (and the
module-level instrument cache built from it) is never rebuilt mid-test.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource

from groovemap_schema import initializer


if TYPE_CHECKING:
    from collections.abc import Iterator

DURATION_METRIC = "groovemap.schema_init.duration"


@pytest.fixture(autouse=True)
def _disable_file_logging() -> Iterator[None]:
    with patch.object(initializer, "setup_logging"):
        yield


@pytest.fixture
def in_memory_metrics(monkeypatch: pytest.MonkeyPatch) -> InMemoryMetricReader:
    """Route setup_telemetry()'s SDK provider through an InMemoryMetricReader.

    Substitutes the OTLP HTTP exporter that common.telemetry._build_sdk_provider would
    otherwise construct, so setup_telemetry("schema-init") still exercises its real
    "configured" code path (endpoint set, real SdkMeterProvider installed and registered as
    the process-wide OpenTelemetry meter provider) without ever opening a socket.
    """
    from common import telemetry as runtime_telemetry

    reader = InMemoryMetricReader()

    def _fake_build_sdk_provider(service_name: str, service_version: str | None) -> MeterProvider:  # noqa: ARG001
        return MeterProvider(resource=Resource.create({SERVICE_NAME: service_name}), metric_readers=[reader])

    monkeypatch.setattr(runtime_telemetry, "_build_sdk_provider", _fake_build_sdk_provider)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector.invalid:4318")
    return reader


def _mock_driver() -> MagicMock:
    driver = MagicMock()
    session = AsyncMock()
    session.__aenter__.return_value = session
    result = AsyncMock()
    result.single.return_value = {"health": 1}
    session.run.return_value = result
    driver.session.return_value = session
    driver.close = AsyncMock()
    return driver


def _duration_attribute_sets(metrics_data: Any) -> set[tuple[tuple[str, Any], ...]]:
    """Flatten every groovemap.schema_init.duration data point into its attribute set."""
    attribute_sets: set[tuple[tuple[str, Any], ...]] = set()
    for resource_metrics in metrics_data.resource_metrics:
        for scope_metrics in resource_metrics.scope_metrics:
            for metric in scope_metrics.metrics:
                if metric.name != DURATION_METRIC:
                    continue
                for point in metric.data.data_points:
                    assert set(point.attributes.keys()) == {"store", "outcome"}, (
                        "attribute sets are closed: no id, host, or other free text may ride along"
                    )
                    attribute_sets.add(tuple(sorted(point.attributes.items())))
    return attribute_sets


@pytest.mark.asyncio
async def test_schema_init_duration_recorded_on_success_and_failure(
    in_memory_metrics: InMemoryMetricReader,
) -> None:
    pool = AsyncMock()
    driver = _mock_driver()

    # Run 1: every store succeeds -- the main path the acceptance criteria call out.
    with (
        patch.object(initializer, "_ensure_postgres_database"),
        patch.object(initializer, "AsyncPostgreSQLPool", return_value=pool),
        patch.object(initializer, "create_postgres_schema", new_callable=AsyncMock, return_value=0),
        patch.object(initializer, "AsyncResilientNeo4jDriver", return_value=driver),
        patch.object(initializer, "create_neo4j_schema", new_callable=AsyncMock, return_value=0),
        # Stubbed so the provider setup_telemetry() installed for this test is never torn
        # down mid-test; the real shutdown_telemetry() is exercised in test_initializer.py.
        patch.object(initializer, "shutdown_telemetry"),
    ):
        assert await initializer.main() == 0

    success_attributes = _duration_attribute_sets(in_memory_metrics.get_metrics_data())
    assert (("outcome", "success"), ("store", "postgresql")) in success_attributes
    assert (("outcome", "success"), ("store", "neo4j")) in success_attributes
    assert (("outcome", "success"), ("store", "all")) in success_attributes

    # Run 2, same provider: PostgreSQL fails -- the failure outcome must be recorded too, for
    # both the failing store and the overall run.
    failing_pool = AsyncMock()
    failing_pool.initialize.side_effect = ConnectionError("unavailable")
    with (
        patch.object(initializer, "_ensure_postgres_database"),
        patch.object(initializer, "AsyncPostgreSQLPool", return_value=failing_pool),
        patch.object(initializer, "AsyncResilientNeo4jDriver", return_value=driver),
        patch.object(initializer, "create_neo4j_schema", new_callable=AsyncMock, return_value=0),
        patch.object(initializer, "shutdown_telemetry"),
    ):
        assert await initializer.main() == 1

    failure_attributes = _duration_attribute_sets(in_memory_metrics.get_metrics_data())
    assert (("outcome", "failure"), ("store", "postgresql")) in failure_attributes
    assert (("outcome", "failure"), ("store", "all")) in failure_attributes
    assert (("outcome", "success"), ("store", "neo4j")) in failure_attributes

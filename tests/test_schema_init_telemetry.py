"""Tests for the domain telemetry the initializer produces: metrics and schema_init spans.

These exercise the real OpenTelemetry SDK (through common.telemetry.setup_telemetry) with the
OTLP exporters substituted for in-memory ones, so nothing here performs network I/O.

Only one test drives setup_telemetry() down its configured METRICS path, and it does so exactly
once: the OpenTelemetry API's global meter provider registry can be set at most once per
process (a second set_meter_provider() call is a silent no-op), and groovemap_schema.initializer
captures its meter once at import time via that global registry. Both metric scenarios below
therefore share a single setup_telemetry() call and a single reader, sequencing two runs of
main() with shutdown_telemetry() stubbed out so the provider (and the module-level instrument
cache built from it) is never rebuilt mid-test.

Tracing has no such constraint: common.telemetry keeps the installed TracerProvider itself and
get_tracer() reads it on every call, so the span tests below each set up and tear down their own
provider. They run after the metric test in file order and keep metrics export off, which leaves
the one global meter registration to the test that needs it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from common import telemetry as runtime_telemetry
from common.tracing import db_span
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from groovemap_schema import initializer


if TYPE_CHECKING:
    from collections.abc import Iterator

    from opentelemetry.sdk.trace import ReadableSpan

DURATION_METRIC = "groovemap.schema_init.duration"


@pytest.fixture(autouse=True)
def _disable_file_logging() -> Iterator[None]:
    with patch.object(initializer, "setup_logging"):
        yield


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


@pytest.fixture
def in_memory_metrics(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[InMemoryMetricReader, InMemorySpanExporter]]:
    """Route setup_telemetry()'s SDK providers through in-memory readers, with tracing off.

    Substitutes the OTLP HTTP exporters that common.telemetry would otherwise construct, so
    setup_telemetry("schema-init") still exercises its real "configured" metrics path (endpoint
    set, real SdkMeterProvider installed and registered as the process-wide OpenTelemetry meter
    provider) without ever opening a socket. OTEL_TRACES_EXPORTER=none is the second half of the
    contract under test: the two signals are independent, so metrics must flow while the span
    exporter stays empty.
    """
    reader = InMemoryMetricReader()
    span_exporter = InMemorySpanExporter()

    def _fake_build_sdk_provider(service_name: str, service_version: str | None) -> MeterProvider:  # noqa: ARG001
        return MeterProvider(resource=Resource.create({SERVICE_NAME: service_name}), metric_readers=[reader])

    def _fake_build_sdk_tracer_provider(service_name: str, service_version: str | None) -> TracerProvider:  # noqa: ARG001
        provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
        provider.add_span_processor(SimpleSpanProcessor(span_exporter))
        return provider

    monkeypatch.setattr(runtime_telemetry, "_build_sdk_provider", _fake_build_sdk_provider)
    monkeypatch.setattr(runtime_telemetry, "_build_sdk_tracer_provider", _fake_build_sdk_tracer_provider)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector.invalid:4318")
    monkeypatch.setenv("OTEL_TRACES_EXPORTER", "none")
    yield reader, span_exporter


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
async def test_metrics_flow_with_tracing_off_on_success_and_failure(
    in_memory_metrics: tuple[InMemoryMetricReader, InMemorySpanExporter],
) -> None:
    reader, span_exporter = in_memory_metrics
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

    success_attributes = _duration_attribute_sets(reader.get_metrics_data())
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

    failure_attributes = _duration_attribute_sets(reader.get_metrics_data())
    assert (("outcome", "failure"), ("store", "postgresql")) in failure_attributes
    assert (("outcome", "failure"), ("store", "all")) in failure_attributes
    assert (("outcome", "success"), ("store", "neo4j")) in failure_attributes

    # OTEL_TRACES_EXPORTER=none: the endpoint is shared, so only an independent tracing switch
    # keeps this empty across two full runs that opened schema_init spans.
    assert span_exporter.get_finished_spans() == ()


@pytest.fixture
def in_memory_spans(monkeypatch: pytest.MonkeyPatch) -> Iterator[InMemorySpanExporter]:
    """Install a real SDK TracerProvider exporting into memory, with metrics export off.

    Metrics stay off deliberately: the one process-wide meter provider registration belongs to
    the metric test above, and a second one would silently do nothing. The provider is torn down
    on the way in and out so this fixture neither inherits nor leaks telemetry state -- main()
    installs its own inside the test.
    """
    exporter = InMemorySpanExporter()

    def _fake_build_sdk_tracer_provider(service_name: str, service_version: str | None) -> TracerProvider:  # noqa: ARG001
        provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
        provider.add_span_processor(SimpleSpanProcessor(exporter))
        return provider

    monkeypatch.setattr(runtime_telemetry, "_build_sdk_tracer_provider", _fake_build_sdk_tracer_provider)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector.invalid:4318")
    monkeypatch.setenv("OTEL_METRICS_EXPORTER", "none")
    monkeypatch.setenv("OTEL_TRACES_SAMPLER", "always_on")
    runtime_telemetry.shutdown_telemetry()
    yield exporter
    runtime_telemetry.shutdown_telemetry()


def _spans_by_name(exporter: InMemorySpanExporter) -> dict[str, ReadableSpan]:
    spans = exporter.get_finished_spans()
    names = [span.name for span in spans]
    assert len(names) == len(set(names)), f"span names must stay unique per run: {names}"
    return {span.name: span for span in spans}


async def _postgres_schema_opening_a_db_span(_pool: Any) -> int:
    """Stand in for create_postgres_schema, opening the kind of db span the pool opens for free."""
    with db_span("postgresql", "execute"):
        return 0


@pytest.mark.asyncio
async def test_each_store_gets_a_root_schema_init_span_that_db_spans_nest_under(
    in_memory_spans: InMemorySpanExporter,
) -> None:
    pool = AsyncMock()
    driver = _mock_driver()

    with (
        patch.object(initializer, "_ensure_postgres_database"),
        patch.object(initializer, "AsyncPostgreSQLPool", return_value=pool),
        patch.object(initializer, "create_postgres_schema", _postgres_schema_opening_a_db_span),
        patch.object(initializer, "AsyncResilientNeo4jDriver", return_value=driver),
        patch.object(initializer, "create_neo4j_schema", new_callable=AsyncMock, return_value=0),
    ):
        assert await initializer.main() == 0

    spans = _spans_by_name(in_memory_spans)
    assert set(spans) == {"schema_init postgresql", "schema_init neo4j", "execute postgresql"}

    for store in ("postgresql", "neo4j"):
        span = spans[f"schema_init {store}"]
        assert span.parent is None, "each store's span is a trace root, so one store reads on its own"
        assert dict(span.attributes or {}) == {"store": store, "outcome": "success"}
        assert span.status.status_code is not StatusCode.ERROR

    root = spans["schema_init postgresql"]
    db = spans["execute postgresql"]
    assert db.parent is not None
    assert db.parent.span_id == (root.context.span_id if root.context else None)
    assert db.context is not None
    assert db.context.trace_id == (root.context.trace_id if root.context else None)


@pytest.mark.asyncio
async def test_a_failing_store_fails_its_span_with_the_outcome_attribute(
    in_memory_spans: InMemorySpanExporter,
) -> None:
    failing_pool = AsyncMock()
    failing_pool.initialize.side_effect = ConnectionError("unavailable")
    driver = _mock_driver()

    with (
        patch.object(initializer, "_ensure_postgres_database"),
        patch.object(initializer, "AsyncPostgreSQLPool", return_value=failing_pool),
        patch.object(initializer, "AsyncResilientNeo4jDriver", return_value=driver),
        patch.object(initializer, "create_neo4j_schema", new_callable=AsyncMock, return_value=0),
    ):
        assert await initializer.main() == 1

    spans = _spans_by_name(in_memory_spans)
    failed = spans["schema_init postgresql"]
    assert dict(failed.attributes or {}) == {"store": "postgresql", "outcome": "failure"}
    assert failed.status.status_code is StatusCode.ERROR
    assert failed.status.description is None, "a failed span carries no message, stack trace, or payload"

    succeeded = spans["schema_init neo4j"]
    assert dict(succeeded.attributes or {}) == {"store": "neo4j", "outcome": "success"}
    assert succeeded.status.status_code is not StatusCode.ERROR


@pytest.mark.asyncio
async def test_without_an_endpoint_no_span_is_recorded(monkeypatch: pytest.MonkeyPatch) -> None:
    """The unconfigured default: schema_init spans exist in code but record nothing."""
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    runtime_telemetry.shutdown_telemetry()
    try:
        runtime_telemetry.setup_telemetry(initializer.TELEMETRY_SERVICE_NAME)
        with initializer._tracer().start_as_current_span("schema_init postgresql") as span:
            assert span.is_recording() is False
    finally:
        runtime_telemetry.shutdown_telemetry()

"""One-shot database initializer used by the GrooveMap deployment stack.

The command ensures that the target PostgreSQL database exists and then applies
the versioned PostgreSQL and Neo4j definitions in parallel. Every schema
statement is idempotent, so running the command at each deployment is safe.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any, NoReturn

import psycopg
import structlog
from common import (
    AsyncPostgreSQLPool,
    AsyncResilientNeo4jDriver,
    get_meter,
    get_tracer,
    neo4j_security_kwargs,
    parse_postgres_host_port,
    setup_logging,
    setup_telemetry,
    shutdown_telemetry,
    start_event_loop_monitor,
)
from common.config import _build_neo4j_uri, get_secret
from opentelemetry.trace import NoOpTracer, StatusCode
from psycopg import sql

from groovemap_schema import __version__
from groovemap_schema.neo4j import create_neo4j_schema
from groovemap_schema.postgres import create_postgres_schema


if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

logger = structlog.get_logger(__name__)

NEO4J_URI = _build_neo4j_uri()
NEO4J_USERNAME = os.environ.get("NEO4J_USERNAME", "neo4j")
NEO4J_PASSWORD = get_secret("NEO4J_PASSWORD", "groovemap")

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "localhost")
POSTGRES_USERNAME = get_secret("POSTGRES_USERNAME", "groovemap")
POSTGRES_PASSWORD = get_secret("POSTGRES_PASSWORD", "groovemap")
POSTGRES_DATABASE = os.environ.get("POSTGRES_DATABASE", "groovemap")

SERVICE_NAME = "database-schema"
BANNER = r"""
    _      _        _                             _
 __| |__ _| |_ __ _| |__  __ _ ___ ___ ___ ___ __| |_  ___ _ __  __ _
/ _` / _` |  _/ _` | '_ \/ _` (_-</ -_)___(_-</ _| ' \/ -_) '  \/ _` |
\__,_\__,_|\__\__,_|_.__/\__,_/__/\___|   /__/\__|_||_\___|_|_|_\__,_|
                         database-schema
""".strip("\n")

# service.name for OTEL telemetry: the docker-compose service key, distinct from SERVICE_NAME
# above (the structured-logging label, which stays "database-schema" for compatibility).
TELEMETRY_SERVICE_NAME = "schema-init"

# The instrumentation scope both signals this service produces are reported under.
TELEMETRY_SCOPE = "groovemap.schema_init"

# Captured once at import, per the OTEL API's proxy-provider pattern: a meter obtained before
# setup_telemetry() runs still starts recording correctly once setup_telemetry() installs the
# real provider. Instruments built from it are created lazily, on first use.
_METER = get_meter(TELEMETRY_SCOPE)
_schema_init_duration: Any | None = None


def _duration_histogram() -> Any:
    """Return the shared groovemap.schema_init.duration histogram, built on first use."""
    global _schema_init_duration
    if _schema_init_duration is None:
        _schema_init_duration = _METER.create_histogram(
            "groovemap.schema_init.duration",
            unit="s",
            description="Duration of a database-schema store initialization.",
        )
    return _schema_init_duration


def _record_schema_init_duration(store: str, outcome: str, duration_s: float) -> None:
    """Record one groovemap.schema_init.duration measurement. Never raises."""
    try:
        _duration_histogram().record(duration_s, {"store": store, "outcome": outcome})
    except Exception:
        logger.debug("Could not record groovemap.schema_init.duration", exc_info=True)


def _tracer() -> Any:
    """Return the tracer this service opens its domain spans with. Never raises.

    Resolved on every use instead of captured at import, which is the opposite of the meter
    above: get_tracer reads the installed TracerProvider at call time, so a tracer taken before
    setup_telemetry() would hand out non-recording spans for the rest of the process.
    """
    try:
        return get_tracer(TELEMETRY_SCOPE)
    except Exception:
        logger.debug("Could not obtain a tracer; schema_init spans are disabled", exc_info=True)
        return NoOpTracer()


def _close_schema_init_span(span: Any, outcome: str) -> None:
    """Record a run's outcome on its schema_init span, failing the span when it did not succeed.

    Never raises: the span describes an initialization, it must not decide whether one worked.
    Only the closed store/outcome pair is attached -- no statement, no error message, no id.
    """
    try:
        span.set_attribute("outcome", outcome)
        if outcome != "success":
            span.set_status(StatusCode.ERROR)
    except Exception:
        logger.debug("Could not annotate the schema_init span", exc_info=True)


async def _initialize_store(store: str, initialize: Callable[[], Awaitable[bool]]) -> bool:
    """Run one store initialization inside its root `schema_init {store}` span.

    The span is that store's trace root, so the db spans the PostgreSQL pool and the Neo4j
    driver open on their own nest underneath it. It carries the same closed store/outcome pair
    as the groovemap.schema_init.duration measurement taken around it, and both are produced
    here so the metric and the span can never disagree about a run. The two stores initialize
    concurrently and each is its own trace, which is what makes either one readable alone.
    """
    start = time.perf_counter()
    succeeded = False
    with _tracer().start_as_current_span(
        f"schema_init {store}",
        attributes={"store": store},
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            succeeded = await initialize()
        finally:
            outcome = "success" if succeeded else "failure"
            _record_schema_init_duration(store, outcome, time.perf_counter() - start)
            _close_schema_init_span(span, outcome)
    return succeeded


def _postgres_connection_params() -> dict[str, Any]:
    """Build bounded PostgreSQL connection parameters from the environment."""
    default_port = int(os.environ.get("POSTGRES_PORT", "5432") or "5432")
    host, port = parse_postgres_host_port(POSTGRES_HOST, default_port)
    return {
        "host": host,
        "port": port,
        "dbname": POSTGRES_DATABASE,
        "user": POSTGRES_USERNAME,
        "password": POSTGRES_PASSWORD,
        # An unbounded admin connection would block every dependent service.
        "connect_timeout": 10,
    }


def _ensure_postgres_database(params: dict[str, Any]) -> None:
    """Create the target PostgreSQL database when it does not yet exist."""
    admin_params = {**params, "dbname": "postgres"}
    logger.info("Ensuring PostgreSQL database exists", database=POSTGRES_DATABASE)
    with (
        psycopg.connect(**admin_params, autocommit=True) as connection,
        connection.cursor() as cursor,
    ):
        cursor.execute("SELECT 1 FROM pg_database WHERE datname = %s", (POSTGRES_DATABASE,))
        if cursor.fetchone():
            logger.info("PostgreSQL database already exists", database=POSTGRES_DATABASE)
            return
        cursor.execute(  # nosemgrep: identifier is escaped by psycopg.sql.Identifier
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(POSTGRES_DATABASE))
        )
        logger.info("PostgreSQL database created", database=POSTGRES_DATABASE)


async def _apply_postgres_schema(params: dict[str, Any]) -> bool:
    """Apply all PostgreSQL schema statements. Telemetry is owned by _initialize_store."""
    pool: AsyncPostgreSQLPool | None = None
    try:
        pool = AsyncPostgreSQLPool(
            connection_params=params,
            max_connections=5,
            min_connections=1,
            max_retries=5,
            health_check_interval=30,
        )
        await pool.initialize()
        failures = await create_postgres_schema(pool)
        if failures:
            logger.error("PostgreSQL schema had partial failures", failure_count=failures)
            return False
        return True
    except Exception as error:
        logger.error("PostgreSQL schema initialization failed", error=str(error))
        return False
    finally:
        if pool is not None:
            await pool.close()


async def _init_postgres(params: dict[str, Any]) -> bool:
    """Apply the PostgreSQL schema inside its measured `schema_init postgresql` span."""
    return await _initialize_store("postgresql", lambda: _apply_postgres_schema(params))


async def _apply_neo4j_schema() -> bool:
    """Verify Neo4j connectivity and apply all schema statements."""
    driver: AsyncResilientNeo4jDriver | None = None
    try:
        driver = AsyncResilientNeo4jDriver(
            uri=NEO4J_URI,
            auth=(NEO4J_USERNAME, NEO4J_PASSWORD),
            **neo4j_security_kwargs(),
        )
        async with driver.session(database="neo4j") as session:
            result = await session.run("RETURN 1 AS health")
            await result.single()
        failures = await create_neo4j_schema(driver)
        if failures:
            logger.error("Neo4j schema had partial failures", failure_count=failures)
            return False
        return True
    except Exception as error:
        logger.error("Neo4j schema initialization failed", error=str(error))
        return False
    finally:
        if driver is not None:
            await driver.close()


async def _init_neo4j() -> bool:
    """Apply the Neo4j schema inside its measured `schema_init neo4j` span."""
    return await _initialize_store("neo4j", _apply_neo4j_schema)


async def main() -> int:
    """Run the one-shot initializer and return its process status."""
    setup_logging(SERVICE_NAME, log_file=Path("/logs/database-schema.log"))
    setup_telemetry(TELEMETRY_SERVICE_NAME)
    # This one-shot job does its work on an asyncio loop, so event-loop lag is a signal it can
    # actually produce. Started from the running loop, as the monitor requires, and cancelled
    # by shutdown_telemetry() in the finally below.
    start_event_loop_monitor()
    try:
        print(BANNER)  # noqa: T201 -- repository-name startup banner is intentional
        logger.info("Database schema initializer starting")

        overall_start = time.perf_counter()
        params = _postgres_connection_params()
        try:
            _ensure_postgres_database(params)
        except Exception as error:
            logger.error("Failed to ensure PostgreSQL database exists", error=str(error))
            _record_schema_init_duration("all", "failure", time.perf_counter() - overall_start)
            return 1

        postgres_ok, neo4j_ok = await asyncio.gather(_init_postgres(params), _init_neo4j())
        overall_outcome = "success" if postgres_ok and neo4j_ok else "failure"
        _record_schema_init_duration("all", overall_outcome, time.perf_counter() - overall_start)

        if postgres_ok and neo4j_ok:
            logger.info("Database schema initialization complete")
            return 0

        failed_systems = [name for name, succeeded in (("PostgreSQL", postgres_ok), ("Neo4j", neo4j_ok)) if not succeeded]
        logger.error("Database schema initialization failed", systems=", ".join(failed_systems))
        return 1
    finally:
        shutdown_telemetry()


def cli(argv: list[str] | None = None) -> NoReturn:
    """Parse the stable command interface and run the one-shot initializer."""
    parser = argparse.ArgumentParser(
        prog=SERVICE_NAME,
        description="Apply the GrooveMap PostgreSQL and Neo4j schemas.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)
    raise SystemExit(asyncio.run(main()))


if __name__ == "__main__":
    cli()

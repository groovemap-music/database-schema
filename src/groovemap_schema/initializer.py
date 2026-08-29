"""One-shot database initializer used by the GrooveMap deployment stack.

The command ensures that the target PostgreSQL database exists and then applies
the versioned PostgreSQL and Neo4j definitions in parallel. Every schema
statement is idempotent, so running the command at each deployment is safe.
"""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from typing import Any, NoReturn

import psycopg
import structlog
from common import (
    AsyncPostgreSQLPool,
    AsyncResilientNeo4jDriver,
    neo4j_security_kwargs,
    parse_postgres_host_port,
    setup_logging,
)
from common.config import _build_neo4j_uri, get_secret
from psycopg import sql

from groovemap_schema.neo4j import create_neo4j_schema
from groovemap_schema.postgres import create_postgres_schema


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


async def _init_postgres(params: dict[str, Any]) -> bool:
    """Apply all PostgreSQL schema statements."""
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


async def _init_neo4j() -> bool:
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


async def main() -> int:
    """Run the one-shot initializer and return its process status."""
    setup_logging(SERVICE_NAME, log_file=Path("/logs/database-schema.log"))
    print(BANNER)  # noqa: T201 -- repository-name startup banner is intentional
    logger.info("Database schema initializer starting")

    params = _postgres_connection_params()
    try:
        _ensure_postgres_database(params)
    except Exception as error:
        logger.error("Failed to ensure PostgreSQL database exists", error=str(error))
        return 1

    postgres_ok, neo4j_ok = await asyncio.gather(_init_postgres(params), _init_neo4j())
    if postgres_ok and neo4j_ok:
        logger.info("Database schema initialization complete")
        return 0

    failed_systems = [name for name, succeeded in (("PostgreSQL", postgres_ok), ("Neo4j", neo4j_ok)) if not succeeded]
    logger.error("Database schema initialization failed", systems=", ".join(failed_systems))
    return 1


def cli() -> NoReturn:
    """Console-script boundary."""
    raise SystemExit(asyncio.run(main()))


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

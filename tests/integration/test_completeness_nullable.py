"""`insights.data_completeness.completeness_pct` becomes nullable, on a real PostgreSQL.

A deployed database has the column NOT NULL, with rows in it. The schema must converge it without
touching those rows, and a writer that still supplies a number must keep working at every step of the
consumer rollout (schema, analytics-engine, catalog-api).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg import sql

from groovemap_schema import initializer
from groovemap_schema.postgres import _execute_schema_statements, _schema_statements


if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

OLD_TABLE = """
CREATE SCHEMA IF NOT EXISTS insights;
CREATE TABLE insights.data_completeness (
    entity_type     TEXT NOT NULL,
    total_count     BIGINT NOT NULL,
    with_image      BIGINT NOT NULL DEFAULT 0,
    with_year       BIGINT NOT NULL DEFAULT 0,
    with_country    BIGINT NOT NULL DEFAULT 0,
    with_genre      BIGINT NOT NULL DEFAULT 0,
    completeness_pct NUMERIC(5,2) NOT NULL DEFAULT 0,
    computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (entity_type)
)
"""
WRITE = (
    "INSERT INTO insights.data_completeness (entity_type, total_count, completeness_pct) VALUES (%s, %s, %s) "
    "ON CONFLICT (entity_type) DO UPDATE SET total_count = EXCLUDED.total_count, completeness_pct = EXCLUDED.completeness_pct"
)


def _params(dbname: str) -> dict[str, Any]:
    return {**initializer._postgres_connection_params(), "dbname": dbname}


async def _rows(dbname: str, query: Any, parameters: Any = None) -> list[tuple[Any, ...]]:
    async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as connection:
        cursor = await connection.execute(query, parameters)
        return await cursor.fetchall() if cursor.description else []


async def _apply_schema(dbname: str) -> None:
    async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as connection, connection.cursor() as cursor:
        _success, failures = await _execute_schema_statements(cursor, _schema_statements())
    assert failures == 0


async def _nullable(dbname: str) -> str:
    rows = await _rows(
        dbname,
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_schema = 'insights' AND table_name = 'data_completeness' AND column_name = 'completeness_pct'",
    )
    return str(rows[0][0])


@pytest_asyncio.fixture
async def deployed() -> AsyncIterator[str]:
    """A database holding the old NOT NULL table with scored rows, as a deployed one does."""
    dbname = f"completeness_null_{uuid4().hex[:12]}"
    await _rows("postgres", sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    try:
        for statement in OLD_TABLE.split(";\n"):
            await _rows(dbname, statement)
        await _rows(dbname, WRITE, ("releases", 100, "73.50"))
        await _rows(dbname, WRITE, ("artists", 40, "0"))
        yield dbname
    finally:
        await _rows("postgres", sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname)))


async def test_an_old_database_converges_without_touching_its_rows(deployed: str) -> None:
    assert await _nullable(deployed) == "NO"
    with pytest.raises(psycopg.errors.NotNullViolation):
        await _rows(deployed, WRITE, ("labels", 5, None))

    await _apply_schema(deployed)

    assert await _nullable(deployed) == "YES"
    print("\nUPGRADE: completeness_pct NOT NULL -> nullable; existing rows kept")
    assert await _rows(deployed, "SELECT entity_type, completeness_pct FROM insights.data_completeness ORDER BY entity_type") == [
        ("artists", 0),
        ("releases", 73.5),
    ]


async def test_null_is_stored_and_a_number_still_is(deployed: str) -> None:
    await _apply_schema(deployed)

    await _rows(deployed, WRITE, ("artists", 40, None))
    await _rows(deployed, WRITE, ("labels", 5, None))
    await _rows(deployed, WRITE, ("releases", 101, "74.25"))

    assert await _rows(deployed, "SELECT entity_type, completeness_pct FROM insights.data_completeness ORDER BY entity_type") == [
        ("artists", None),
        ("labels", None),
        ("releases", 74.25),
    ]


async def test_a_second_apply_changes_nothing(deployed: str) -> None:
    await _apply_schema(deployed)
    await _apply_schema(deployed)

    assert await _nullable(deployed) == "YES"


async def test_a_fresh_database_creates_the_column_nullable() -> None:
    dbname = f"completeness_fresh_{uuid4().hex[:12]}"
    await _rows("postgres", sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    try:
        await _apply_schema(dbname)
        assert await _nullable(dbname) == "YES"
    finally:
        await _rows("postgres", sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname)))

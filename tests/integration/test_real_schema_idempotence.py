"""Real-engine schema initialization and idempotence proof."""

from __future__ import annotations

import asyncio
from typing import Any

import psycopg
import pytest
from neo4j import AsyncGraphDatabase

from groovemap_schema import initializer
from groovemap_schema.neo4j import SCHEMA_STATEMENTS


pytestmark = pytest.mark.integration

EXPECTED_POSTGRES_TABLES = {
    "public": {
        "admin_audit_log",
        "app_config",
        "app_tokens",
        "artists",
        "extraction_history",
        "labels",
        "masters",
        "oauth_tokens",
        "queue_metrics",
        "releases",
        "service_health_metrics",
        "sync_history",
        "user_collections",
        "user_wantlists",
        "users",
    },
    "insights": {
        "artist_centrality",
        "community_counts",
        "computation_log",
        "data_completeness",
        "genre_trends",
        "label_longevity",
        "monthly_anniversaries",
        "release_rarity",
    },
    "musicbrainz": {
        "artists",
        "external_links",
        "labels",
        "relationships",
        "release_groups",
        "releases",
    },
}

EXPECTED_COLUMNS = {
    ("public", "releases", "media", "jsonb"),
    ("public", "user_collections", "media", "jsonb"),
    ("public", "user_collections", "metadata", "jsonb"),
    ("public", "user_wantlists", "media", "jsonb"),
    ("insights", "release_rarity", "family_signals", "jsonb"),
    ("insights", "release_rarity", "media_families", "jsonb"),
    ("musicbrainz", "artists", "discogs_artist_id", "bigint"),
    ("musicbrainz", "labels", "discogs_label_id", "bigint"),
    ("musicbrainz", "release_groups", "discogs_master_id", "bigint"),
    ("musicbrainz", "releases", "discogs_release_id", "bigint"),
    ("musicbrainz", "releases", "media", "jsonb"),
}


async def postgres_rows(query: str, parameters: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Query the disposable target database through the production connection settings."""
    connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params())
    async with connection, connection.cursor() as cursor:
        await cursor.execute(query, parameters)
        return await cursor.fetchall()


async def postgres_snapshot() -> tuple[tuple[Any, ...], ...]:
    """Capture stable table, column, constraint, and index definitions."""
    rows = await postgres_rows(
        """
        SELECT 'column', table_schema, table_name, column_name,
               data_type, is_nullable, COALESCE(column_default, '')
        FROM information_schema.columns
        WHERE table_schema IN ('public', 'insights', 'musicbrainz')
        UNION ALL
        SELECT 'constraint', namespace.nspname, table_class.relname,
               constraint_row.conname, constraint_row.contype::text,
               pg_get_constraintdef(constraint_row.oid), ''
        FROM pg_constraint AS constraint_row
        JOIN pg_class AS table_class ON table_class.oid = constraint_row.conrelid
        JOIN pg_namespace AS namespace ON namespace.oid = table_class.relnamespace
        WHERE namespace.nspname IN ('public', 'insights', 'musicbrainz')
        UNION ALL
        SELECT 'index', schemaname, tablename, indexname, indexdef, '', ''
        FROM pg_indexes
        WHERE schemaname IN ('public', 'insights', 'musicbrainz')
        ORDER BY 1, 2, 3, 4
        """
    )
    return tuple(rows)


async def assert_expected_postgres_schema() -> None:
    """Assert the cross-service tables and high-risk additive columns exist."""
    table_rows = await postgres_rows(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_type = 'BASE TABLE'
          AND table_schema IN ('public', 'insights', 'musicbrainz')
        """
    )
    actual_tables: dict[str, set[str]] = {schema: set() for schema in EXPECTED_POSTGRES_TABLES}
    for schema, table in table_rows:
        actual_tables[schema].add(table)
    assert actual_tables == EXPECTED_POSTGRES_TABLES

    column_rows = await postgres_rows(
        """
        SELECT table_schema, table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema IN ('public', 'insights', 'musicbrainz')
        """
    )
    assert set(column_rows) >= EXPECTED_COLUMNS

    relationship_key = await postgres_rows(
        """
        SELECT array_agg(attribute.attname ORDER BY key_column.ordinality)
        FROM pg_constraint AS constraint_row
        JOIN pg_class AS table_class ON table_class.oid = constraint_row.conrelid
        JOIN pg_namespace AS namespace ON namespace.oid = table_class.relnamespace
        CROSS JOIN LATERAL unnest(constraint_row.conkey) WITH ORDINALITY AS key_column(attnum, ordinality)
        JOIN pg_attribute AS attribute
          ON attribute.attrelid = table_class.oid AND attribute.attnum = key_column.attnum
        WHERE namespace.nspname = 'musicbrainz'
          AND table_class.relname = 'relationships'
          AND constraint_row.conname = 'relationships_natural_key'
        """
    )
    assert relationship_key == [
        (
            [
                "source_mbid",
                "target_mbid",
                "source_entity_type",
                "target_entity_type",
                "relationship_type",
                "begin_date",
                "end_date",
                "attributes",
            ],
        )
    ]


async def neo4j_rows(query: str) -> list[tuple[Any, ...]]:
    """Query the disposable Neo4j through the production connection settings."""
    driver = AsyncGraphDatabase.driver(
        initializer.NEO4J_URI,
        auth=(initializer.NEO4J_USERNAME, initializer.NEO4J_PASSWORD),
    )
    try:
        async with driver.session(database="neo4j") as session:
            result = await session.run(query)
            return [tuple(record.values()) async for record in result]
    finally:
        await driver.close()


async def neo4j_snapshot() -> tuple[tuple[Any, ...], ...]:
    """Capture stable constraint and index metadata, excluding online state."""
    constraints = await neo4j_rows(
        """
        SHOW CONSTRAINTS YIELD name, type, entityType, labelsOrTypes, properties
        RETURN 'constraint', name, type, entityType, labelsOrTypes, properties
        ORDER BY name
        """
    )
    indexes = await neo4j_rows(
        """
        SHOW INDEXES YIELD name, type, entityType, labelsOrTypes, properties, owningConstraint
        RETURN 'index', name, type, entityType, labelsOrTypes, properties, owningConstraint
        ORDER BY name
        """
    )
    return tuple(constraints + indexes)


async def assert_expected_neo4j_schema() -> None:
    """Assert every declared named schema object exists on the real engine."""
    expected = {name for name, _statement in SCHEMA_STATEMENTS}
    rows = await neo4j_snapshot()
    actual = {row[1] for row in rows}
    assert expected <= actual


async def apply_schema() -> None:
    """Run the same store initializers the one-shot command uses."""
    parameters = initializer._postgres_connection_params()
    initializer._ensure_postgres_database(parameters)
    postgres_ok, neo4j_ok = await asyncio.gather(
        initializer._init_postgres(parameters),
        initializer._init_neo4j(),
    )
    assert postgres_ok is True
    assert neo4j_ok is True


async def seed_sentinels() -> None:
    """Write data whose survival proves the second pass is non-destructive."""
    connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params())
    async with connection, connection.cursor() as cursor:
        await cursor.execute(
            "INSERT INTO app_config (key, value) VALUES (%s, %s)",
            ("integration-sentinel", "survives-second-pass"),
        )
        await connection.commit()

    driver = AsyncGraphDatabase.driver(
        initializer.NEO4J_URI,
        auth=(initializer.NEO4J_USERNAME, initializer.NEO4J_PASSWORD),
    )
    try:
        async with driver.session(database="neo4j") as session:
            result = await session.run("CREATE (:IntegrationSentinel {value: 'survives-second-pass'})")
            await result.consume()
    finally:
        await driver.close()


async def assert_sentinels_survive() -> None:
    postgres = await postgres_rows("SELECT value FROM app_config WHERE key = 'integration-sentinel'")
    neo4j = await neo4j_rows("MATCH (node:IntegrationSentinel) RETURN node.value")
    assert postgres == [("survives-second-pass",)]
    assert neo4j == [("survives-second-pass",)]


@pytest.mark.asyncio
async def test_both_schema_initializers_are_real_engine_idempotent() -> None:
    await apply_schema()
    await assert_expected_postgres_schema()
    await assert_expected_neo4j_schema()
    first_postgres = await postgres_snapshot()
    first_neo4j = await neo4j_snapshot()
    await seed_sentinels()

    await apply_schema()
    await assert_expected_postgres_schema()
    await assert_expected_neo4j_schema()

    assert await postgres_snapshot() == first_postgres
    assert await neo4j_snapshot() == first_neo4j
    await assert_sentinels_survive()

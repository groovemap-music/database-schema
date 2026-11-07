"""Real-engine proof for the `musicbrainz.relationships` index retirement.

`idx_mb_rels_source (source_mbid)` was retired while `relationships_natural_key` led with
`source_mbid`. That constraint is gone too (gm-database-schema-00ke), so no index leads with
`source_mbid` any more, and the choice is not to bring the narrow index back: PostgreSQL 18's B-tree
skip scan answers a `source_mbid` lookup from `idx_mb_rels_endpoint_source (source_entity_type,
target_entity_type, source_mbid)`, whose leading columns have nine values between them. These tests
measure what the narrow index would cost per row, show the same lookups return the same rows through
the endpoint index at a comparable latency, and prove an upgrade drops an existing narrow index and a
second apply changes nothing. `idx_mb_rels_type` stays: `graph.artist_member_of`'s fill filters
`relationship_type = 'MEMBER_OF'`. Each test gets its own database.
"""

from __future__ import annotations

import time
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


pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.timeout(300)]

ROWS = 300_000
SOURCE_INDEX = "idx_mb_rels_source"
_CREATE_SOURCE_INDEX = "CREATE INDEX idx_mb_rels_source ON musicbrainz.relationships (source_mbid)"


def _params(dbname: str) -> dict[str, Any]:
    return {**initializer._postgres_connection_params(), "dbname": dbname}


@pytest_asyncio.fixture
async def connection() -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    dbname = f"mb_rels_{uuid4().hex[:12]}"
    async with await psycopg.AsyncConnection.connect(**_params("postgres"), autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    try:
        async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as conn:
            async with conn.cursor() as cursor:
                _success, failures = await _execute_schema_statements(cursor, _schema_statements())
            assert failures == 0
            yield conn
    finally:
        async with await psycopg.AsyncConnection.connect(**_params("postgres"), autocommit=True) as admin:
            await admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname)))


async def _seed(conn: psycopg.AsyncConnection[Any]) -> None:
    # Random UUID endpoints, about 30 relationship types over the artist, release and label pairs,
    # and the dates and attributes MusicBrainz carries on a minority of rows.
    await conn.execute(
        """
        INSERT INTO musicbrainz.relationships
            (source_mbid, target_mbid, source_entity_type, target_entity_type, relationship_type, begin_date, end_date, attributes)
        SELECT gen_random_uuid(), gen_random_uuid(),
               (ARRAY['artist', 'release', 'label'])[1 + g %% 3], (ARRAY['artist', 'release', 'label'])[1 + (g / 3) %% 3],
               'TYPE_' || (g %% 30),
               CASE WHEN g %% 5 = 0 THEN (1960 + g %% 60)::text END, CASE WHEN g %% 11 = 0 THEN (1990 + g %% 30)::text END,
               CASE WHEN g %% 4 = 0 THEN jsonb_build_object('instrument', ARRAY['guitar', 'vocals'], 'credited_as', 'name ' || g) END
        FROM generate_series(1, %s) AS g
        """,
        (ROWS,),
    )
    await conn.execute("ANALYZE musicbrainz.relationships")


async def _index_bytes(conn: psycopg.AsyncConnection[Any]) -> dict[str, int]:
    rows = await (
        await conn.execute(
            "SELECT indexrelid::regclass::text, pg_relation_size(indexrelid) FROM pg_index WHERE indrelid = 'musicbrainz.relationships'::regclass"
        )
    ).fetchall()
    return {name.removeprefix("musicbrainz."): size for name, size in rows}


async def _lookup(conn: psycopg.AsyncConnection[Any], source: Any, runs: int = 30) -> tuple[list[tuple[Any, ...]], float, str]:
    query = "SELECT id, target_mbid, relationship_type FROM musicbrainz.relationships WHERE source_mbid = %s ORDER BY id"
    samples: list[float] = []
    rows: list[tuple[Any, ...]] = []
    for _ in range(runs):
        start = time.perf_counter()
        rows = await (await conn.execute(query, (source,))).fetchall()
        samples.append((time.perf_counter() - start) * 1000)
    plan = "\n".join(line for (line,) in await (await conn.execute("EXPLAIN " + query, (source,))).fetchall())
    return rows, sorted(samples)[runs // 2], plan


async def test_the_source_index_is_not_created_on_a_fresh_database(connection: psycopg.AsyncConnection[Any]) -> None:
    assert SOURCE_INDEX not in await _index_bytes(connection)


async def test_the_endpoint_index_answers_the_lookup_the_source_index_did(connection: psycopg.AsyncConnection[Any]) -> None:
    await _seed(connection)
    await connection.execute(_CREATE_SOURCE_INDEX)
    sources = [
        row[0] for row in await (await connection.execute("SELECT source_mbid FROM musicbrainz.relationships ORDER BY id LIMIT 25")).fetchall()
    ]
    before_bytes = await _index_bytes(connection)
    before = [await _lookup(connection, source) for source in sources]

    await connection.execute("DROP INDEX musicbrainz.idx_mb_rels_source")
    after_bytes = await _index_bytes(connection)
    after = [await _lookup(connection, source) for source in sources]

    heap = (await (await connection.execute("SELECT pg_relation_size('musicbrainz.relationships')")).fetchone() or (0,))[0]
    print(
        f"\nindex bytes per row: {sum(before_bytes.values()) / ROWS:.0f} before, {sum(after_bytes.values()) / ROWS:.0f} after "
        f"(heap {heap / ROWS:.0f}); source index {before_bytes[SOURCE_INDEX] / ROWS:.0f}"
    )
    print(f"indexes, bytes per row: { {name: round(size / ROWS) for name, size in sorted(before_bytes.items())} }")
    median_before = sorted(ms for _rows, ms, _plan in before)[len(before) // 2]
    median_after = sorted(ms for _rows, ms, _plan in after)[len(after) // 2]
    print(f"source_mbid lookup median over {len(sources)} sources: {median_before:.3f} ms with, {median_after:.3f} ms without")

    assert sum(after_bytes.values()) < sum(before_bytes.values())
    for (rows_before, _ms, plan_before), (rows_after, _ms2, plan_after) in zip(before, after, strict=True):
        assert rows_before == rows_after
        assert rows_before
        assert SOURCE_INDEX in plan_before, plan_before
        assert "idx_mb_rels_endpoint_source" in plan_after, plan_after
        assert "source_mbid = " in plan_after, plan_after
    assert median_after < 5 * median_before + 1


async def test_no_index_leads_with_source_mbid_and_the_lookup_is_still_an_index_scan(connection: psycopg.AsyncConnection[Any]) -> None:
    """The consequence of retiring the constraint, stated: the lookup rides the endpoint index's skip scan."""
    leading = await (
        await connection.execute(
            """
            SELECT indexrelid::regclass::text
            FROM pg_index
            WHERE indrelid = 'musicbrainz.relationships'::regclass
              AND indkey[0] = (SELECT attnum FROM pg_attribute WHERE attrelid = indrelid AND attname = 'source_mbid')
            """
        )
    ).fetchall()
    assert leading == []
    await _seed(connection)
    source = (await (await connection.execute("SELECT source_mbid FROM musicbrainz.relationships ORDER BY id LIMIT 1 OFFSET 100")).fetchone() or (None,))[0]
    rows, median_ms, plan = await _lookup(connection, source)
    print(f"\nsource_mbid lookup through the endpoint index: {median_ms:.3f} ms median, {len(rows)} row(s)")
    assert rows
    assert "Seq Scan" not in plan, plan
    assert "idx_mb_rels_endpoint_source" in plan, plan
    assert median_ms < 50


async def test_applying_the_schema_drops_an_existing_source_index_and_is_idempotent(connection: psycopg.AsyncConnection[Any]) -> None:
    await connection.execute(_CREATE_SOURCE_INDEX)
    assert SOURCE_INDEX in await _index_bytes(connection)
    for _ in range(2):
        async with connection.cursor() as cursor:
            _success, failures = await _execute_schema_statements(cursor, _schema_statements())
        assert failures == 0
        indexes = await _index_bytes(connection)
        assert SOURCE_INDEX not in indexes
        assert "relationships_natural_key" not in indexes
        for kept in (
            "relationships_key",
            "idx_mb_rels_type",
            "idx_mb_rels_target",
            "idx_mb_rels_endpoint_source",
            "idx_mb_rels_endpoint_target",
        ):
            assert kept in indexes, kept

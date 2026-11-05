"""Real-engine proof for the MusicBrainz relationships hash key (gm-database-schema-ngw9).

`musicbrainz.relationships` keeps its eight-column `UNIQUE NULLS NOT DISTINCT` constraint, so the loader's
current `ON CONFLICT (<eight columns>)` still works, and gains a second unique index over
`musicbrainz.relationship_key(...)`, a SHA-256 over the same eight values. Dropping the constraint is a
later step, after `musicbrainz-sql-loader` names the expression (gm-musicbrainz-sql-loader-1twk). These
tests run both upsert targets on a real engine, hold the hash key to the constraint's rule (jsonb attribute
order, NULL dates, field boundaries), measure the two keys' index per row, and add the hash index to a
database that predates it. Each test gets its own database.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg import sql
from psycopg.types.json import Jsonb

from groovemap_schema import initializer
from groovemap_schema.postgres import _execute_schema_statements, _schema_statements


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.timeout(300)]

A = "00000000-0000-0000-0000-00000000000a"
B = "00000000-0000-0000-0000-00000000000b"
_KEY = (
    "musicbrainz.relationship_key(source_mbid, target_mbid, source_entity_type, target_entity_type, "
    "relationship_type, begin_date, end_date, attributes)"
)
# The loader's upsert, with the conflict target changed to the key expression.
_UPSERT = (
    "INSERT INTO musicbrainz.relationships "  # noqa: S608
    "(source_mbid, source_entity_type, target_mbid, target_entity_type, relationship_type, attributes, begin_date, end_date, ended) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
    f"ON CONFLICT ({_KEY}) DO UPDATE SET ended = EXCLUDED.ended, updated_at = NOW() RETURNING (xmax = 0) AS inserted"
)


def _params(dbname: str) -> dict[str, Any]:
    return {**initializer._postgres_connection_params(), "dbname": dbname}


@pytest_asyncio.fixture
async def connection() -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    dbname = f"mb_rel_key_{uuid4().hex[:12]}"
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


async def _upsert(
    conn: psycopg.AsyncConnection[Any],
    *,
    source: str = A,
    target: str = B,
    types: tuple[str, str] = ("artist", "artist"),
    rel: str = "MEMBER_OF",
    attributes: Any = None,
    begin: str | None = None,
    end: str | None = None,
) -> bool:
    row = await (
        await conn.execute(
            _UPSERT, (source, types[0], target, types[1], rel, Jsonb(attributes) if attributes is not None else None, begin, end, False)
        )
    ).fetchone()
    assert row is not None
    return bool(row[0])


async def _count(conn: psycopg.AsyncConnection[Any]) -> int:
    return (await (await conn.execute("SELECT count(*) FROM musicbrainz.relationships")).fetchone() or (0,))[0]


async def test_the_loaders_upsert_inserts_once_and_updates_the_same_relationship(connection: psycopg.AsyncConnection[Any]) -> None:
    assert await _upsert(connection, attributes={"instrument": ["guitar"]}, begin="1990") is True
    assert await _upsert(connection, attributes={"instrument": ["guitar"]}, begin="1990") is False
    assert await _count(connection) == 1


async def test_jsonb_attribute_key_order_does_not_make_a_second_relationship(connection: psycopg.AsyncConnection[Any]) -> None:
    await connection.execute(
        _UPSERT.replace("%s, %s, %s, %s, %s, %s, %s, %s, %s", "%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s"),
        (A, "artist", B, "artist", "PERFORMER", json.dumps({"b": 1, "a": 2}), None, None, False),
    )
    await connection.execute(
        _UPSERT.replace("%s, %s, %s, %s, %s, %s, %s, %s, %s", "%s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s"),
        (A, "artist", B, "artist", "PERFORMER", json.dumps({"a": 2, "b": 1}), None, None, False),
    )
    assert await _count(connection) == 1


async def test_what_made_two_relationships_distinct_still_does(connection: psycopg.AsyncConnection[Any]) -> None:
    assert await _upsert(connection, begin="1990", end="1995") is True  # a band membership
    assert await _upsert(connection, begin="2001", end=None) is True  # the member re-joined
    assert await _upsert(connection, attributes={"instrument": ["guitar"]}) is True
    assert await _upsert(connection, attributes={"instrument": ["vocals"]}) is True
    assert await _upsert(connection, rel="PERFORMER") is True
    assert await _upsert(connection, types=("artist", "label")) is True
    assert await _upsert(connection, source=B, target=A) is True
    assert await _count(connection) == 7


async def test_null_is_one_value_and_is_not_an_empty_string(connection: psycopg.AsyncConnection[Any]) -> None:
    """The constraint was NULLS NOT DISTINCT: two NULL dates are one key, and NULL is not ''."""
    assert await _upsert(connection, begin=None, end=None) is True
    assert await _upsert(connection, begin=None, end=None) is False
    assert await _upsert(connection, begin="", end=None) is True
    assert await _upsert(connection, begin=None, end="") is True
    assert await _upsert(connection, attributes={}) is True  # an empty object is not a missing one
    assert await _count(connection) == 4


async def test_a_value_moved_across_a_field_boundary_is_a_different_relationship(connection: psycopg.AsyncConnection[Any]) -> None:
    assert await _upsert(connection, rel="ab", begin="c") is True
    assert await _upsert(connection, rel="a", begin="bc") is True
    assert await _upsert(connection, rel="a", begin="b", end="c") is True
    assert await _count(connection) == 3


async def test_the_plain_insert_without_on_conflict_is_still_a_unique_violation(connection: psycopg.AsyncConnection[Any]) -> None:
    await _upsert(connection, begin="1990")
    with pytest.raises(psycopg.errors.UniqueViolation):
        await connection.execute(
            "INSERT INTO musicbrainz.relationships (source_mbid, source_entity_type, target_mbid, target_entity_type, relationship_type, begin_date) "
            "VALUES (%s, 'artist', %s, 'artist', 'MEMBER_OF', '1990')",
            (A, B),
        )


_OLD_UPSERT = (
    "INSERT INTO musicbrainz.relationships "
    "(source_mbid, source_entity_type, target_mbid, target_entity_type, relationship_type, attributes, begin_date, end_date, ended) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
    "ON CONFLICT (source_mbid, target_mbid, source_entity_type, target_entity_type, relationship_type, begin_date, end_date, attributes) "
    "DO UPDATE SET ended = EXCLUDED.ended, updated_at = NOW() RETURNING (xmax = 0) AS inserted"
)


async def test_the_loaders_current_conflict_target_still_works_beside_the_hash_key(connection: psycopg.AsyncConnection[Any]) -> None:
    """The change is additive: the unchanged loader inserts once and updates on repeat."""
    args = (A, "artist", B, "artist", "MEMBER_OF", Jsonb({"instrument": ["guitar"]}), "1990", None, False)
    first = await (await connection.execute(_OLD_UPSERT, args)).fetchone()
    second = await (await connection.execute(_OLD_UPSERT, args)).fetchone()
    assert first == (True,)
    assert second == (False,)
    assert await _count(connection) == 1
    # and the expression target finds the same row
    assert await _upsert(connection, attributes={"instrument": ["guitar"]}, begin="1990") is False
    assert await _count(connection) == 1


async def test_the_hash_key_costs_less_index_per_row_than_the_constraint(connection: psycopg.AsyncConnection[Any]) -> None:
    rows = 200_000
    await connection.execute(
        """
        INSERT INTO musicbrainz.relationships
            (source_mbid, target_mbid, source_entity_type, target_entity_type, relationship_type, begin_date, end_date, attributes)
        SELECT gen_random_uuid(), gen_random_uuid(), (ARRAY['artist', 'release', 'label'])[1 + g %% 3],
               (ARRAY['artist', 'release', 'label'])[1 + (g / 3) %% 3], 'TYPE_' || (g %% 30),
               CASE WHEN g %% 5 = 0 THEN (1960 + g %% 60)::text END, CASE WHEN g %% 11 = 0 THEN (1990 + g %% 30)::text END,
               CASE WHEN g %% 4 = 0 THEN jsonb_build_object('instrument', ARRAY['guitar', 'vocals'], 'credited_as', 'name ' || g) END
        FROM generate_series(1, %s) AS g
        """,
        (rows,),
    )
    sizes = dict(
        await (
            await connection.execute(
                "SELECT indexrelid::regclass::text, pg_relation_size(indexrelid) FROM pg_index WHERE indrelid = 'musicbrainz.relationships'::regclass"
            )
        ).fetchall()
    )
    old, new = sizes["musicbrainz.relationships_natural_key"] / rows, sizes["musicbrainz.relationships_key"] / rows
    print(f"\nkey index bytes per row: {old:.0f} eight-column constraint, {new:.0f} hash key; both present now, so the table pays {old + new:.0f}")
    print(f"saving once the constraint is dropped in the later step: {old - new:.0f} bytes per row")
    assert new < old


async def test_a_database_that_predates_the_hash_key_gains_it_and_keeps_the_constraint(connection: psycopg.AsyncConnection[Any]) -> None:
    await connection.execute("DROP INDEX musicbrainz.relationships_key")
    await connection.execute(
        "INSERT INTO musicbrainz.relationships (source_mbid, source_entity_type, target_mbid, target_entity_type, relationship_type, begin_date) "
        "SELECT gen_random_uuid(), 'artist', gen_random_uuid(), 'artist', 'MEMBER_OF', g::text FROM generate_series(1, 500) g"
    )
    for _ in range(2):
        async with connection.cursor() as cursor:
            _success, failures = await _execute_schema_statements(cursor, _schema_statements())
        assert failures == 0
        constraint = await (await connection.execute("SELECT count(*) FROM pg_constraint WHERE conname = 'relationships_natural_key'")).fetchone()
        index = await (
            await connection.execute("SELECT indisunique, indisvalid FROM pg_index WHERE indexrelid = 'musicbrainz.relationships_key'::regclass")
        ).fetchone()
        assert constraint == (1,)
        assert index == (True, True)
        assert await _count(connection) == 500
    with pytest.raises(psycopg.errors.UniqueViolation):
        await connection.execute(
            "INSERT INTO musicbrainz.relationships (source_mbid, source_entity_type, target_mbid, target_entity_type, relationship_type, begin_date) "
            "SELECT source_mbid, 'artist', target_mbid, 'artist', 'MEMBER_OF', '1' FROM musicbrainz.relationships WHERE begin_date = '1'"
        )

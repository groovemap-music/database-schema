"""Real-engine proof for the MusicBrainz relationships hash key (gm-database-schema-ngw9, -00ke).

`musicbrainz.relationships` is keyed by one unique index over `musicbrainz.relationship_key(...)`, a SHA-256
over the eight values the retired `relationships_natural_key` constraint covered. `musicbrainz-sql-loader`
v0.6.0 names the expression as its conflict target (gm-musicbrainz-sql-loader-1twk), and the schema drops
the constraint (gm-database-schema-00ke). These tests run the loader's upsert on a real engine, hold the
hash key to the constraint's rule (jsonb attribute order, NULL dates, field boundaries), measure index
bytes per row with the constraint, with both keys and with the hash key alone, and upgrade databases that
carry both keys, with a populated table, to prove the drop is safe and the constraint stays gone.
Each test gets its own database.
"""

from __future__ import annotations

import json
import time
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
_CONSTRAINT = (
    "ALTER TABLE musicbrainz.relationships ADD CONSTRAINT relationships_natural_key UNIQUE NULLS NOT DISTINCT "
    "(source_mbid, target_mbid, source_entity_type, target_entity_type, relationship_type, begin_date, end_date, attributes)"
)
_CONSTRAINT_ROWS = "SELECT count(*) FROM pg_constraint WHERE conname = 'relationships_natural_key'"
_SEED = """
    INSERT INTO musicbrainz.relationships
        (source_mbid, target_mbid, source_entity_type, target_entity_type, relationship_type, begin_date, end_date, attributes)
    SELECT gen_random_uuid(), gen_random_uuid(), (ARRAY['artist', 'release', 'label'])[1 + g %% 3],
           (ARRAY['artist', 'release', 'label'])[1 + (g / 3) %% 3], 'TYPE_' || (g %% 30),
           CASE WHEN g %% 5 = 0 THEN (1960 + g %% 60)::text END, CASE WHEN g %% 11 = 0 THEN (1990 + g %% 30)::text END,
           CASE WHEN g %% 4 = 0 THEN jsonb_build_object('instrument', ARRAY['guitar', 'vocals'], 'credited_as', 'name ' || g) END
    FROM generate_series(1, %s) AS g
"""


async def _apply(conn: psycopg.AsyncConnection[Any]) -> None:
    async with conn.cursor() as cursor:
        _success, failures = await _execute_schema_statements(cursor, _schema_statements())
    assert failures == 0


async def _index_sizes(conn: psycopg.AsyncConnection[Any]) -> dict[str, int]:
    rows = await (
        await conn.execute(
            "SELECT indexrelid::regclass::text, pg_relation_size(indexrelid) FROM pg_index WHERE indrelid = 'musicbrainz.relationships'::regclass"
        )
    ).fetchall()
    return dict(rows)


async def test_a_fresh_database_has_the_hash_key_and_no_natural_key_constraint(connection: psycopg.AsyncConnection[Any]) -> None:
    assert await (await connection.execute(_CONSTRAINT_ROWS)).fetchone() == (0,)
    index = await (
        await connection.execute("SELECT indisunique, indisvalid FROM pg_index WHERE indexrelid = 'musicbrainz.relationships_key'::regclass")
    ).fetchone()
    assert index == (True, True)


async def test_a_conflict_target_naming_the_eight_columns_fails_once_the_constraint_is_gone(connection: psycopg.AsyncConnection[Any]) -> None:
    """The breaking part of the change, proved: a writer older than the hash target can no longer upsert."""
    args = (A, "artist", B, "artist", "MEMBER_OF", None, "1990", None, False)
    with pytest.raises(psycopg.errors.InvalidColumnReference, match="no unique or exclusion constraint matching the ON CONFLICT"):
        await connection.execute(_OLD_UPSERT, args)
    assert await _count(connection) == 0
    assert await _upsert(connection, begin="1990") is True


async def test_a_number_written_at_two_scales_is_two_relationships_now_that_the_constraint_is_gone(
    connection: psycopg.AsyncConnection[Any],
) -> None:
    """`1` and `1.0` are two hash keys; while the constraint stood the second was an error."""
    assert await _upsert(connection, attributes={"count": 1}) is True
    assert await _upsert(connection, attributes={"count": 1.0}) is True
    assert await _count(connection) == 2


async def test_index_bytes_per_row_fall_below_the_figure_before_the_hash_key(connection: psycopg.AsyncConnection[Any]) -> None:
    """Measure the table's index per row three ways on the same data: constraint alone, both keys, hash alone."""
    rows = 200_000
    per_row: dict[str, tuple[float, float]] = {}
    for label, prepare in (
        ("constraint only (before ngw9)", ("DROP INDEX musicbrainz.relationships_key", _CONSTRAINT)),
        ("both keys (after ngw9)", (_CONSTRAINT,)),
        ("hash key only (after 00ke)", ()),
    ):
        await connection.execute("TRUNCATE musicbrainz.relationships")
        await connection.execute("DROP INDEX IF EXISTS musicbrainz.relationships_key")
        await connection.execute("ALTER TABLE musicbrainz.relationships DROP CONSTRAINT IF EXISTS relationships_natural_key")
        await _apply_key_index(connection)
        for statement in prepare:
            await connection.execute(statement)
        await connection.execute(_SEED, (rows,))
        sizes = await _index_sizes(connection)
        keys = sum(size for name, size in sizes.items() if name.endswith(("relationships_natural_key", "relationships_key")))
        per_row[label] = (keys / rows, sum(sizes.values()) / rows)
    for label, (keys, total) in per_row.items():
        print(f"\n{label}: key index {keys:.0f} B/row, all indexes {total:.0f} B/row", end="")
    print()
    before, both, after = per_row.values()
    assert both[1] > before[1]
    assert after[1] < before[1]
    assert after[0] < before[0]


async def _apply_key_index(conn: psycopg.AsyncConnection[Any]) -> None:
    """Recreate the hash index the way the schema declares it (a no-op where it is present)."""
    statements = dict(_schema_statements())
    await conn.execute(statements["musicbrainz.relationships_key index"])


async def test_a_populated_database_with_both_keys_loses_the_constraint_and_keeps_every_row(connection: psycopg.AsyncConnection[Any]) -> None:
    rows = 100_000
    await connection.execute(_CONSTRAINT)
    await connection.execute(_SEED, (rows,))
    assert await (await connection.execute(_CONSTRAINT_ROWS)).fetchone() == (1,)
    before = await (await connection.execute("SELECT count(*), sum(hashtext(id::text)) FROM musicbrainz.relationships")).fetchone()
    for attempt in range(2):
        started = time.perf_counter()
        await _apply(connection)
        elapsed = time.perf_counter() - started
        print(f"\napply {attempt + 1} on {rows:,} rows with both keys: {elapsed:.2f} s")
        assert await (await connection.execute(_CONSTRAINT_ROWS)).fetchone() == (0,)
        index = await (
            await connection.execute("SELECT indisunique, indisvalid FROM pg_index WHERE indexrelid = 'musicbrainz.relationships_key'::regclass")
        ).fetchone()
        assert index == (True, True)
        assert await (await connection.execute("SELECT count(*), sum(hashtext(id::text)) FROM musicbrainz.relationships")).fetchone() == before
    # a row that was already there is still found by the hash target, and a plain duplicate still violates the key
    existing = await (
        await connection.execute("SELECT source_mbid, target_mbid, source_entity_type, target_entity_type, relationship_type FROM musicbrainz.relationships LIMIT 1")
    ).fetchone()
    assert existing is not None
    with pytest.raises(psycopg.errors.UniqueViolation):
        await connection.execute(
            "INSERT INTO musicbrainz.relationships (source_mbid, target_mbid, source_entity_type, target_entity_type, relationship_type) "
            "SELECT source_mbid, target_mbid, source_entity_type, target_entity_type, relationship_type FROM musicbrainz.relationships "
            "WHERE begin_date IS NULL AND end_date IS NULL AND attributes IS NULL LIMIT 1"
        )


async def test_the_constraint_is_kept_when_the_hash_key_is_missing_or_not_valid(connection: psycopg.AsyncConnection[Any]) -> None:
    """The drop never leaves the table with no key: it needs relationships_key unique, valid and ready."""
    retire = dict(_schema_statements())["musicbrainz.relationships_natural_key retire"]
    await connection.execute("DROP INDEX musicbrainz.relationships_key")
    await connection.execute(_CONSTRAINT)

    await connection.execute(retire)
    assert await (await connection.execute(_CONSTRAINT_ROWS)).fetchone() == (1,)  # no hash index at all

    await _apply_key_index(connection)
    await connection.execute("UPDATE pg_index SET indisvalid = false WHERE indexrelid = 'musicbrainz.relationships_key'::regclass")
    await connection.execute(retire)
    assert await (await connection.execute(_CONSTRAINT_ROWS)).fetchone() == (1,)  # an invalid one

    await connection.execute("UPDATE pg_index SET indisvalid = true WHERE indexrelid = 'musicbrainz.relationships_key'::regclass")
    await connection.execute(retire)
    assert await (await connection.execute(_CONSTRAINT_ROWS)).fetchone() == (0,)  # now it goes
    await connection.execute(retire)  # and a second run finds nothing to do


async def test_a_database_that_predates_the_hash_key_gains_it_and_loses_the_constraint(connection: psycopg.AsyncConnection[Any]) -> None:
    await connection.execute("DROP INDEX musicbrainz.relationships_key")
    await connection.execute(_CONSTRAINT)
    await connection.execute(
        "INSERT INTO musicbrainz.relationships (source_mbid, source_entity_type, target_mbid, target_entity_type, relationship_type, begin_date) "
        "SELECT gen_random_uuid(), 'artist', gen_random_uuid(), 'artist', 'MEMBER_OF', g::text FROM generate_series(1, 500) g"
    )
    for _ in range(2):
        await _apply(connection)
        assert await (await connection.execute(_CONSTRAINT_ROWS)).fetchone() == (0,)
        index = await (
            await connection.execute("SELECT indisunique, indisvalid FROM pg_index WHERE indexrelid = 'musicbrainz.relationships_key'::regclass")
        ).fetchone()
        assert index == (True, True)
        assert await _count(connection) == 500
    with pytest.raises(psycopg.errors.UniqueViolation):
        await connection.execute(
            "INSERT INTO musicbrainz.relationships (source_mbid, source_entity_type, target_mbid, target_entity_type, relationship_type, begin_date) "
            "SELECT source_mbid, 'artist', target_mbid, 'artist', 'MEMBER_OF', '1' FROM musicbrainz.relationships WHERE begin_date = '1'"
        )

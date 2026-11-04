"""Real-engine proof that a credit of any length can be stored whole.

PostgreSQL refuses a btree entry over 2704 bytes after compression, and a Discogs credit name
past that limit stopped the first SQL load on `person_pkey` on 2026-11-04. Every test here writes
through the statements the loaders run: named columns under a target-less `ON CONFLICT DO NOTHING`.
Each one gets its own database, so the upgrade test can stand up the shape a deployed database has.
"""

from __future__ import annotations

import random
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from neo4j import AsyncGraphDatabase
from psycopg import sql
from psycopg.types.json import Jsonb

from groovemap_schema import initializer
from groovemap_schema.neo4j import SCHEMA_STATEMENTS
from groovemap_schema.postgres import (
    _FREE_TEXT_KEYS,
    _RETIRED_BTREE_INDEXES,
    _execute_schema_statements,
    _schema_statements,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# A btree limit that counts compressed bytes is not reached by a long repetitive string, so the
# values are random: 1200 CJK characters are 3600 bytes that pglz cannot shrink. The upgrade test
# proves that against the old key rather than assuming it.
LIMIT_BYTES = 2704


def incompressible(seed: int, characters: int = 1200) -> str:
    """Return multi-byte text pglz cannot compress under the btree limit."""
    generator = random.Random(seed)  # noqa: S311 -- reproducible test data, not a secret
    return "".join(chr(generator.randrange(0x4E00, 0x9FFF)) for _ in range(characters))


LONG_NAME = incompressible(1)
LONG_ROLE = "Featuring [" + incompressible(2) + "]"
# A second person who differs from the first only past the limit, where a prefix key would stop.
SHARED_PREFIX_NAME = LONG_NAME + incompressible(3, characters=4)


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


@pytest_asyncio.fixture
async def database() -> AsyncIterator[str]:
    """Create one disposable database with the current schema applied."""
    dbname = f"free_text_keys_{uuid4().hex[:12]}"
    await _rows("postgres", sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    try:
        await _apply_schema(dbname)
        yield dbname
    finally:
        await _rows("postgres", sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname)))


async def _write_credit(dbname: str, name: str, role: str, release_id: str = "111", artist_id: str = "7") -> None:
    """Write one credit the way `tableinator.graph_writer` does, vertex first."""
    for statement, row in (
        ("INSERT INTO graph.person (name) VALUES (%s) ON CONFLICT DO NOTHING", (name,)),
        ("INSERT INTO graph.credited_on (person_name, release_id, role) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING", (name, release_id, role)),
        ("INSERT INTO graph.same_as (person_name, artist_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (name, artist_id)),
        (
            "INSERT INTO graph.track_credited_on (person_name, release_id, track_ordinal, sub_track_ordinal, track_position, role) "
            "VALUES (%s, %s, 1, 0, 'A1', %s) ON CONFLICT DO NOTHING",
            (name, release_id, role),
        ),
    ):
        await _rows(dbname, statement, row)


async def test_a_credit_past_the_btree_limit_is_stored_whole(database: str) -> None:
    assert len(LONG_NAME.encode()) > LIMIT_BYTES
    assert len(LONG_ROLE.encode()) > LIMIT_BYTES

    await _write_credit(database, LONG_NAME, LONG_ROLE)

    assert await _rows(database, "SELECT name FROM graph.person") == [(LONG_NAME,)]
    assert await _rows(database, "SELECT person_name, release_id, role FROM graph.credited_on") == [(LONG_NAME, "111", LONG_ROLE)]
    assert await _rows(database, "SELECT person_name, artist_id FROM graph.same_as") == [(LONG_NAME, "7")]
    assert await _rows(database, "SELECT person_name, role FROM graph.track_credited_on") == [(LONG_NAME, LONG_ROLE)]


async def test_the_key_is_the_whole_value_not_a_prefix(database: str) -> None:
    await _write_credit(database, LONG_NAME, "Producer")
    await _write_credit(database, SHARED_PREFIX_NAME, "Producer")
    await _write_credit(database, LONG_NAME, LONG_ROLE)
    await _write_credit(database, LONG_NAME, LONG_ROLE + "x")

    assert await _rows(database, "SELECT count(*) FROM graph.person") == [(2,)]
    assert await _rows(database, "SELECT count(*) FROM graph.same_as") == [(2,)]
    assert await _rows(database, "SELECT count(*) FROM graph.credited_on") == [(4,)]
    assert await _rows(database, "SELECT count(*) FROM graph.track_credited_on") == [(4,)]


async def test_the_same_credit_still_deduplicates_to_one_row(database: str) -> None:
    for _ in range(2):
        await _write_credit(database, LONG_NAME, LONG_ROLE)
        await _write_credit(database, "Alice", "Producer")

    assert await _rows(database, "SELECT count(*) FROM graph.person") == [(2,)]
    assert await _rows(database, "SELECT count(*) FROM graph.credited_on") == [(2,)]
    assert await _rows(database, "SELECT count(*) FROM graph.same_as") == [(2,)]
    assert await _rows(database, "SELECT count(*) FROM graph.track_credited_on") == [(2,)]


async def test_a_duplicate_without_on_conflict_is_still_a_unique_violation(database: str) -> None:
    await _rows(database, "INSERT INTO graph.person (name) VALUES (%s)", (LONG_NAME,))
    with pytest.raises(psycopg.errors.UniqueViolation):
        await _rows(database, "INSERT INTO graph.person (name) VALUES (%s)", (LONG_NAME,))
    with pytest.raises(psycopg.errors.NotNullViolation):
        await _rows(database, "INSERT INTO graph.person (name) VALUES (NULL)")


async def test_a_long_derived_company_id_is_stored_whole(database: str) -> None:
    company_id = "name:" + LONG_NAME
    for _ in range(2):
        await _rows(database, "INSERT INTO graph.company (company_id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING", (company_id, LONG_NAME))
        await _rows(
            database,
            "INSERT INTO graph.credited_to (release_id, company_id, role, role_category, source) "
            "VALUES ('111', %s, 'Pressed By', 'manufacturing', 'discogs') ON CONFLICT DO NOTHING",
            (company_id,),
        )

    assert await _rows(database, "SELECT company_id FROM graph.company") == [(company_id,)]
    assert await _rows(database, "SELECT company_id, role, source FROM graph.credited_to") == [(company_id, "Pressed By", "discogs")]


async def test_long_names_and_titles_load_into_the_entity_tables(database: str) -> None:
    """The entity row is written on the same transaction as the credits it carries."""
    title = incompressible(4)
    for table, field, value in (
        ("artists", "name", LONG_NAME),
        ("labels", "name", LONG_NAME),
        ("masters", "title", title),
        ("releases", "title", title),
    ):
        await _rows(
            database,
            sql.SQL("INSERT INTO {} (data_id, hash, data) VALUES ('1', 'h', %s)").format(sql.Identifier(table)),
            (Jsonb({field: value}),),
        )
    for table, parameters in (
        ("artists", ("00000000-0000-7000-8000-000000000001", LONG_NAME)),
        ("labels", ("00000000-0000-7000-8000-000000000002", LONG_NAME)),
    ):
        await _rows(database, sql.SQL("INSERT INTO musicbrainz.{} (mbid, name) VALUES (%s, %s)").format(sql.Identifier(table)), parameters)

    assert await _rows(database, "SELECT data->>'name' FROM artists WHERE data->>'name' = %s", (LONG_NAME,)) == [(LONG_NAME,)]
    assert await _rows(database, "SELECT data->>'title' FROM releases WHERE data->>'title' = %s", (title,)) == [(title,)]
    assert await _rows(database, "SELECT name FROM musicbrainz.artists WHERE name = %s", (LONG_NAME,)) == [(LONG_NAME,)]


async def test_the_bootstrap_fill_carries_a_long_credit(database: str) -> None:
    document = {
        "title": "Long Credits",
        "extraartists": [{"name": LONG_NAME, "role": LONG_ROLE, "id": "7"}, {"name": SHARED_PREFIX_NAME, "role": "Producer", "id": "8"}],
    }
    await _rows(database, "INSERT INTO releases (data_id, hash, data) VALUES ('111', 'h', %s)", (Jsonb(document),))

    await _rows(database, "SELECT * FROM graph.bootstrap_fill()")

    assert sorted(await _rows(database, "SELECT name FROM graph.person")) == sorted([(LONG_NAME,), (SHARED_PREFIX_NAME,)])
    assert await _rows(database, "SELECT count(*) FROM graph.credited_on") == [(2,)]
    assert await _rows(database, "SELECT artist_id FROM graph.same_as WHERE person_name = %s", (LONG_NAME,)) == [("7",)]


async def _plan(dbname: str, query: str, *, setting: str | None = None) -> str:
    async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as connection:
        if setting:
            await connection.execute(setting)  # type: ignore[arg-type]
        cursor = await connection.execute(f"EXPLAIN {query}")  # type: ignore[arg-type]
        return "\n".join(row[0] for row in await cursor.fetchall())


async def test_lookups_by_name_stay_on_an_index(database: str) -> None:
    """A consumer's query names the column; nothing has to hash its parameter."""
    await _rows(
        database,
        """
        INSERT INTO graph.person (name) SELECT 'Person ' || i FROM generate_series(1, 5000) AS i
        """,
    )
    await _rows(
        database,
        """
        INSERT INTO graph.credited_on (person_name, release_id, role)
        SELECT 'Person ' || i, (i % 1000)::text, 'Producer' FROM generate_series(1, 5000) AS i
        """,
    )
    await _rows(
        database,
        "INSERT INTO graph.same_as (person_name, artist_id) SELECT 'Person ' || i, i::text FROM generate_series(1, 5000) AS i",
    )
    await _rows(database, "INSERT INTO graph.person (name) VALUES (%s)", (LONG_NAME,))
    await _rows(database, "ANALYZE graph.person")
    await _rows(database, "ANALYZE graph.credited_on")
    await _rows(database, "ANALYZE graph.same_as")

    assert "person_name_hash" in await _plan(database, "SELECT name FROM graph.person WHERE name = 'Person 42'")
    assert "credited_on_person_name_hash" in await _plan(database, "SELECT release_id FROM graph.credited_on WHERE person_name = 'Person 42'")
    assert "same_as_person_name_hash" in await _plan(database, "SELECT artist_id FROM graph.same_as WHERE person_name = 'Person 42'")
    assert "credited_on_key" in await _plan(database, "SELECT person_name FROM graph.credited_on WHERE release_id = '7'")
    assert "same_as_key" in await _plan(database, "SELECT person_name FROM graph.same_as WHERE artist_id = '7'")
    # A table this small is cheaper to scan, so the planner is asked for the index it can use.
    trigram_plan = await _plan(database, "SELECT name FROM graph.person WHERE name ILIKE '%erson 4242%'", setting="SET enable_seqscan = off")
    assert "person_name_trgm" in trigram_plan
    assert await _rows(database, "SELECT count(*) FROM graph.person WHERE name ILIKE %s", (f"%{LONG_NAME[-40:]}%",)) == [(1,)]


# The six relations and six indexes as v0.6.0 declares them, which is what a deployed database holds.
_OLD_SHAPE = """
DROP TABLE graph.person, graph.company, graph.credited_on, graph.same_as, graph.track_credited_on, graph.credited_to CASCADE;
CREATE TABLE graph.person (name text PRIMARY KEY);
CREATE TABLE graph.company (company_id text PRIMARY KEY, name text, discogs_label_id text);
CREATE TABLE graph.credited_on (
    person_name   text NOT NULL,
    release_id    text NOT NULL,
    role          text NOT NULL,
    role_category text GENERATED ALWAYS AS (graph.credit_role_category(role)) STORED,
    PRIMARY KEY (person_name, release_id, role)
);
CREATE INDEX credited_on_reverse ON graph.credited_on (release_id, person_name);
CREATE INDEX credited_on_role_category ON graph.credited_on (role_category, person_name);
CREATE TABLE graph.same_as (person_name text NOT NULL, artist_id text NOT NULL, PRIMARY KEY (person_name, artist_id));
CREATE INDEX same_as_reverse ON graph.same_as (artist_id);
CREATE TABLE graph.track_credited_on (
    person_name       text NOT NULL,
    release_id        text NOT NULL,
    track_ordinal     bigint NOT NULL,
    sub_track_ordinal bigint NOT NULL,
    track_position    text,
    role              text NOT NULL,
    role_category     text GENERATED ALWAYS AS (graph.credit_role_category(role)) STORED,
    PRIMARY KEY (person_name, release_id, track_ordinal, sub_track_ordinal, role)
);
CREATE INDEX track_credited_on_reverse ON graph.track_credited_on (release_id, track_ordinal, sub_track_ordinal, person_name);
CREATE TABLE graph.credited_to (
    release_id    text NOT NULL,
    company_id    text NOT NULL,
    role          text NOT NULL,
    role_category text NOT NULL DEFAULT 'other',
    source        text NOT NULL,
    PRIMARY KEY (release_id, company_id, role, source)
);
CREATE INDEX credited_to_reverse ON graph.credited_to (company_id, release_id);
DROP INDEX public.idx_artists_name, public.idx_labels_name, public.idx_masters_title, public.idx_releases_title;
DROP INDEX musicbrainz.idx_mb_artists_name, musicbrainz.idx_mb_labels_name;
CREATE INDEX idx_artists_name ON public.artists ((data->>'name'));
CREATE INDEX idx_labels_name ON public.labels ((data->>'name'));
CREATE INDEX idx_masters_title ON public.masters ((data->>'title'));
CREATE INDEX idx_releases_title ON public.releases ((data->>'title'));
CREATE INDEX idx_mb_artists_name ON musicbrainz.artists (name);
CREATE INDEX idx_mb_labels_name ON musicbrainz.labels (name);
"""


async def _key_shape(dbname: str) -> list[tuple[Any, ...]]:
    return await _rows(
        dbname,
        """
        SELECT schemaname, tablename, indexname, indexdef FROM pg_indexes
        WHERE schemaname IN ('graph', 'public', 'musicbrainz')
          AND (tablename = ANY(%s) OR indexname = ANY(%s))
        ORDER BY 1, 2, 3
        """,
        (list(_FREE_TEXT_KEYS), [name.rsplit(".", 1)[-1] for name in _RETIRED_BTREE_INDEXES]),
    )


async def _index_method(dbname: str, index: str) -> str:
    rows = await _rows(
        dbname,
        "SELECT method.amname FROM pg_class AS relation JOIN pg_am AS method ON method.oid = relation.relam WHERE relation.oid = to_regclass(%s)",
        (index,),
    )
    return str(rows[0][0])


async def test_an_existing_database_upgrades_in_place_and_converges(database: str) -> None:
    fresh = await _key_shape(database)
    async with await psycopg.AsyncConnection.connect(**_params(database), autocommit=True) as connection:
        await connection.execute(_OLD_SHAPE)  # type: ignore[arg-type]

    # A partly loaded database: rows already in place, and the value that stopped the load.
    await _write_credit(database, "Alice", "Producer")
    await _rows(database, "INSERT INTO graph.company (company_id, name) VALUES ('9', 'Pressing Plant')")
    await _rows(
        database,
        "INSERT INTO graph.credited_to (release_id, company_id, role, role_category, source) VALUES ('111', '9', 'Pressed By', 'manufacturing', 'discogs')",
    )
    with pytest.raises(psycopg.errors.ProgramLimitExceeded, match="person_pkey"):
        await _rows(database, "INSERT INTO graph.person (name) VALUES (%s) ON CONFLICT DO NOTHING", (LONG_NAME,))
    heap_file = await _rows(database, "SELECT pg_relation_filenode('graph.credited_on')")

    await _apply_schema(database)

    # The same indexes a fresh database has, and no table rewrite to get them.
    assert await _key_shape(database) == fresh
    assert await _rows(database, "SELECT pg_relation_filenode('graph.credited_on')") == heap_file
    for index in _RETIRED_BTREE_INDEXES:
        assert await _index_method(database, index) == "hash", index
    for relation in _FREE_TEXT_KEYS:
        primary_keys = await _rows(database, "SELECT 1 FROM pg_constraint WHERE conrelid = to_regclass(%s) AND contype = 'p'", (f"graph.{relation}",))
        assert primary_keys == [], relation
    required = await _rows(
        database,
        "SELECT attname, attnotnull FROM pg_attribute WHERE attrelid IN ('graph.person'::regclass, 'graph.company'::regclass) "
        "AND attname IN ('name', 'company_id') ORDER BY 1, 2",
    )
    assert ("company_id", True) in required
    assert ("name", True) in required

    # Everything already loaded survived, and is still the key it was.
    assert await _rows(database, "SELECT name FROM graph.person") == [("Alice",)]
    assert await _rows(database, "SELECT person_name, role_category FROM graph.credited_on") == [("Alice", "production")]
    await _write_credit(database, "Alice", "Producer")
    assert await _rows(database, "SELECT count(*) FROM graph.credited_on") == [(1,)]

    # And the record that stopped the load now goes through.
    await _write_credit(database, LONG_NAME, LONG_ROLE)
    assert await _rows(database, "SELECT count(*) FROM graph.person") == [(2,)]

    # A second apply finds nothing left to do.
    await _apply_schema(database)
    assert await _key_shape(database) == fresh
    assert await _rows(database, "SELECT count(*) FROM graph.credited_on") == [(2,)]


async def test_an_interrupted_upgrade_leaves_the_old_key_whole(database: str) -> None:
    async with await psycopg.AsyncConnection.connect(**_params(database), autocommit=True) as connection:
        await connection.execute(_OLD_SHAPE)  # type: ignore[arg-type]
    await _write_credit(database, "Alice", "Producer")
    migration = dict(_schema_statements())["graph.credited_on free-text key migration"]
    failing = migration.replace("DROP INDEX IF EXISTS graph.credited_on_reverse;", "RAISE EXCEPTION 'interrupted';")
    assert failing != migration

    with pytest.raises(psycopg.errors.RaiseException):
        await _rows(database, failing)

    assert await _rows(database, "SELECT conname FROM pg_constraint WHERE conrelid = 'graph.credited_on'::regclass AND contype = 'p'") == [
        ("credited_on_pkey",)
    ]
    assert await _rows(database, "SELECT to_regclass('graph.credited_on_key')") == [(None,)]
    await _apply_schema(database)
    assert await _rows(database, "SELECT count(*) FROM graph.credited_on") == [(1,)]
    assert await _rows(database, "SELECT conname FROM pg_constraint WHERE conrelid = 'graph.credited_on'::regclass AND contype = 'p'") == []


async def test_the_mirrored_neo4j_key_takes_the_same_long_name() -> None:
    """`Person.name` keys the same value in the other store, under its own index limit."""
    driver = AsyncGraphDatabase.driver(initializer.NEO4J_URI, auth=(initializer.NEO4J_USERNAME, initializer.NEO4J_PASSWORD))
    constraint = dict(SCHEMA_STATEMENTS)["person_name"]
    try:
        async with driver.session(database="neo4j") as session:
            await (await session.run(constraint)).consume()
            for _ in range(2):
                await (await session.run("MERGE (:Person {name: $name})", name=LONG_NAME)).consume()
            result = await session.run("MATCH (person:Person {name: $name}) RETURN count(person) AS people", name=LONG_NAME)
            record = await result.single()
            assert record is not None
            assert record["people"] == 1
            await (await session.run("MATCH (person:Person {name: $name}) DELETE person", name=LONG_NAME)).consume()
    finally:
        await driver.close()

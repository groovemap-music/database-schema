"""Real-engine proof of the `graph.issued_on` key-order upgrade (gm-database-schema-ly5r).

The key was `(release_id, medium_id, source)`; it is now `(release_id, source, medium_id)` so that
`DELETE ... WHERE release_id = $1 AND source = $2` is one prefix search. Each test stands up the old
shape in its own database, populated, and applies the schema the way the initializer does.
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

_NEW_KEY = "(release_id, source, medium_id)"
_OLD_SHAPE = """
DROP TABLE graph.issued_on;
CREATE TABLE graph.issued_on (
    release_id text NOT NULL, medium_id text NOT NULL, source text NOT NULL, qty bigint NOT NULL DEFAULT 1,
    PRIMARY KEY (release_id, medium_id, source)
);
CREATE INDEX issued_on_reverse ON graph.issued_on (medium_id, release_id);
CREATE INDEX issued_on_source ON graph.issued_on (source);
INSERT INTO graph.issued_on
SELECT r::text, 'vinyl_' || (k + 1), CASE WHEN r % 5 = 0 THEN 'musicbrainz' ELSE 'discogs' END, 1
FROM generate_series(1, 20000) AS r, generate_series(0, 1) AS k;
"""


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


async def _key(dbname: str) -> list[tuple[Any, ...]]:
    return await _rows(
        dbname,
        "SELECT conname, pg_get_indexdef(conindid) FROM pg_constraint WHERE conrelid = 'graph.issued_on'::regclass AND contype = 'p'",
    )


@pytest_asyncio.fixture
async def old_database() -> AsyncIterator[str]:
    """A populated database whose `graph.issued_on` still has the old key."""
    dbname = f"issued_on_key_{uuid4().hex[:12]}"
    await _rows("postgres", sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    try:
        await _apply_schema(dbname)
        await _rows(dbname, _OLD_SHAPE)  # type: ignore[arg-type]
        assert "(release_id, medium_id, source)" in (await _key(dbname))[0][1]
        yield dbname
    finally:
        await _rows("postgres", sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname)))


async def _assert_converged(dbname: str) -> None:
    key = await _key(dbname)
    assert [(name, definition.rsplit("btree ", 1)[1]) for name, definition in key] == [("issued_on_pkey", _NEW_KEY)]
    assert await _rows(dbname, "SELECT count(*) FROM graph.issued_on") == [(40000,)]
    assert await _rows(dbname, "SELECT indexname FROM pg_indexes WHERE tablename = 'issued_on' ORDER BY 1") == [
        ("issued_on_pkey",),
        ("issued_on_reverse",),
        ("issued_on_source",),
    ]
    # The columns keep their NOT NULL; the key swap does not take it away.
    assert await _rows(dbname, "SELECT count(*) FROM pg_attribute WHERE attrelid = 'graph.issued_on'::regclass AND attnum > 0 AND attnotnull") == [
        (4,)
    ]


async def test_the_blocking_path_builds_the_key_and_swaps_it(old_database: str) -> None:
    """No pre-built index: the initializer builds `issued_on_pkey_new` itself (loaders stopped)."""
    await _apply_schema(old_database)
    await _assert_converged(old_database)


async def test_a_concurrently_prebuilt_index_is_swapped_in_without_a_rebuild(old_database: str) -> None:
    async with await psycopg.AsyncConnection.connect(**_params(old_database), autocommit=True) as connection:
        await connection.execute(f"CREATE UNIQUE INDEX CONCURRENTLY issued_on_pkey_new ON graph.issued_on {_NEW_KEY}")  # type: ignore[arg-type]
    prebuilt = await _rows(old_database, "SELECT oid FROM pg_class WHERE relname = 'issued_on_pkey_new'")
    await _apply_schema(old_database)
    await _assert_converged(old_database)
    # The pre-built index became the key: same relation, renamed, not rebuilt.
    assert await _rows(old_database, "SELECT oid FROM pg_class WHERE relname = 'issued_on_pkey'") == prebuilt


async def test_an_invalid_leftover_index_is_rebuilt_not_swapped_in(old_database: str) -> None:
    """A failed CONCURRENTLY build leaves an INVALID index; `ALTER ... USING INDEX` would refuse or mis-key."""
    await _rows(old_database, f"CREATE UNIQUE INDEX issued_on_pkey_new ON graph.issued_on {_NEW_KEY}")  # type: ignore[arg-type]
    await _rows(old_database, "UPDATE pg_index SET indisvalid = false WHERE indexrelid = 'graph.issued_on_pkey_new'::regclass")
    await _apply_schema(old_database)
    await _assert_converged(old_database)
    assert await _rows(old_database, "SELECT indisvalid FROM pg_index WHERE indexrelid = 'graph.issued_on_pkey'::regclass") == [(True,)]


async def test_a_rerun_is_a_no_op(old_database: str) -> None:
    await _apply_schema(old_database)
    before = await _rows(old_database, "SELECT oid FROM pg_class WHERE relname = 'issued_on_pkey'")
    await _apply_schema(old_database)
    await _assert_converged(old_database)
    assert await _rows(old_database, "SELECT oid FROM pg_class WHERE relname = 'issued_on_pkey'") == before


async def test_a_fresh_database_is_created_with_the_new_key() -> None:
    dbname = f"issued_on_fresh_{uuid4().hex[:12]}"
    await _rows("postgres", sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    try:
        await _apply_schema(dbname)
        assert [definition.rsplit("btree ", 1)[1] for _name, definition in await _key(dbname)] == [_NEW_KEY]
    finally:
        await _rows("postgres", sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname)))


async def test_both_loaders_write_paths_work_and_the_lookup_is_one_prefix_search(old_database: str) -> None:
    await _apply_schema(old_database)
    async with await psycopg.AsyncConnection.connect(**_params(old_database), autocommit=True) as connection:
        # discogs-sql-loader: target-less DO NOTHING, row-wise and unnest, plus an explicit natural-key target.
        for statement, parameters in (
            (
                "INSERT INTO graph.issued_on (release_id, medium_id, source, qty) VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING",
                ("1", "vinyl_1", "discogs", 9),
            ),
            (
                "INSERT INTO graph.issued_on (release_id, medium_id, source, qty) SELECT * FROM unnest(%s::text[], %s::text[], %s::text[], %s::bigint[]) "
                "ON CONFLICT DO NOTHING",
                (["1", "50001"], ["vinyl_1", "vinyl_1"], ["discogs", "discogs"], [9, 3]),
            ),
            (
                "INSERT INTO graph.issued_on (release_id, medium_id, source, qty) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (release_id, medium_id, source) DO UPDATE SET qty = EXCLUDED.qty",
                ("50001", "vinyl_1", "discogs", 4),
            ),
        ):
            await connection.execute(statement, parameters)  # type: ignore[arg-type]
        assert (
            await (
                await connection.execute("SELECT qty FROM graph.issued_on WHERE release_id = '1' AND source = 'discogs' AND medium_id = 'vinyl_1'")
            ).fetchall()
        ) == [(1,)]
        assert (await (await connection.execute("SELECT qty FROM graph.issued_on WHERE release_id = '50001'")).fetchall()) == [(4,)]
        # musicbrainz-sql-loader: scoped delete, then a plain insert of the release's rows.
        deleted = await connection.execute("DELETE FROM graph.issued_on WHERE release_id = %s AND source = %s", ("5", "musicbrainz"))
        assert deleted.rowcount == 2
        for medium in ("vinyl_1", "vinyl_2"):
            await connection.execute(
                "INSERT INTO graph.issued_on (release_id, medium_id, source, qty) VALUES (%s, %s, %s, %s)", ("5", medium, "musicbrainz", 1)
            )
        await connection.execute("ANALYZE graph.issued_on")
        plan = await (
            await connection.execute("EXPLAIN (COSTS OFF) DELETE FROM graph.issued_on WHERE release_id = '5' AND source = 'musicbrainz'")
        ).fetchall()
    text = "\n".join(line for (line,) in plan)
    assert "issued_on_pkey" in text
    assert "(release_id = '5'::text) AND (source = 'musicbrainz'::text)" in text

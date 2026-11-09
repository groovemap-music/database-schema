"""Real-engine proof that the two refresh owners serialize (gm-database-schema-gqs2).

`discogs-sql-loader` and `musicbrainz-sql-loader` both call `graph.refresh_artist_member_of()` and then
`graph.refresh_vertex_degree()` on their own `extraction_complete` latch. Both truncate and refill, so the
functions take a transaction-scoped advisory lock: a second caller queues behind the first's transaction
and then rebuilds from the sources as they stand, ending with the full row set. A `musicbrainz` job row
is also accepted by `public.loader_derived_refresh_job`.
"""

from __future__ import annotations

import asyncio
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

_REFRESH = "SELECT * FROM graph.refresh_artist_member_of()"
_DEGREE = "SELECT * FROM graph.refresh_vertex_degree()"


def _params(dbname: str) -> dict[str, Any]:
    return {**initializer._postgres_connection_params(), "dbname": dbname}


@pytest_asyncio.fixture
async def dbname() -> AsyncIterator[str]:
    name = f"refresh_{uuid4().hex[:12]}"
    async with await psycopg.AsyncConnection.connect(**_params("postgres"), autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        async with await psycopg.AsyncConnection.connect(**_params(name), autocommit=True) as conn, conn.cursor() as cursor:
            _success, failures = await _execute_schema_statements(cursor, _schema_statements())
        assert failures == 0
        yield name
    finally:
        async with await psycopg.AsyncConnection.connect(**_params("postgres"), autocommit=True) as admin:
            await admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


async def _connect(dbname: str) -> psycopg.AsyncConnection[Any]:
    return await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True)


async def _waiting_on_advisory_lock(dbname: str) -> int:
    async with await _connect(dbname) as conn:
        cursor = await conn.execute("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted")
        row = await cursor.fetchone()
    assert row is not None
    return int(row[0])


async def _wait_until_queued(dbname: str) -> None:
    for _ in range(100):
        if await _waiting_on_advisory_lock(dbname):
            return
        await asyncio.sleep(0.1)
    pytest.fail("the second refresh never queued on the advisory lock")


# Each refresh reads a different source: the union reads `graph.member_of`, the degree sums `graph.artist_member_of`.
@pytest.mark.parametrize(
    ("refresh", "relation", "source"),
    [(_REFRESH, "graph.artist_member_of", "graph.member_of"), (_DEGREE, "graph.vertex_degree", "graph.artist_member_of")],
)
async def test_a_concurrent_refresh_queues_and_ends_with_the_full_row_set(dbname: str, refresh: str, relation: str, source: str) -> None:
    """The second owner waits out the first's transaction, then sees a source row committed meanwhile."""
    first, second, writer = await _connect(dbname), await _connect(dbname), await _connect(dbname)
    try:
        columns = "(member_artist_id, group_artist_id)" if source == "graph.member_of" else "(member_artist_id, group_artist_id, source)"
        values = "('{}', '{}')" if source == "graph.member_of" else "('{}', '{}', 'discogs')"
        await writer.execute(f"INSERT INTO {source} {columns} VALUES {values.format(1, 2)}")  # noqa: S608
        await first.execute("BEGIN")
        await first.execute(refresh)  # the first owner holds the lock to its commit
        queued = asyncio.create_task(second.execute(refresh))
        await _wait_until_queued(dbname)
        assert not queued.done()

        # A source row lands while the second owner waits; only a rebuild that runs after it can include it.
        await writer.execute(f"INSERT INTO {source} {columns} VALUES {values.format(3, 4)}")  # noqa: S608
        await first.execute("COMMIT")
        await asyncio.wait_for(queued, timeout=30)

        cursor = await writer.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.SQL(relation)))
        row = await cursor.fetchone()
        assert row is not None
        # artist_member_of keeps 2 rows; vertex_degree counts the four artists those rows touch.
        assert row[0] == (2 if relation == "graph.artist_member_of" else 4)
    finally:
        for conn in (first, second, writer):
            await conn.close()


async def test_the_refresh_pair_in_one_transaction_is_not_interleaved(dbname: str) -> None:
    """A loader's member-of then degree pass runs to commit before the other loader's begins."""
    first, second, writer = await _connect(dbname), await _connect(dbname), await _connect(dbname)
    try:
        await writer.execute("INSERT INTO graph.member_of (member_artist_id, group_artist_id) VALUES ('1', '2')")
        await first.execute("BEGIN")
        await first.execute(_REFRESH)
        await first.execute(_DEGREE)

        async def other_owner() -> None:
            await second.execute(_REFRESH)
            await second.execute(_DEGREE)

        queued = asyncio.create_task(other_owner())
        await _wait_until_queued(dbname)
        await first.execute("COMMIT")
        await asyncio.wait_for(queued, timeout=30)
        cursor = await writer.execute("SELECT count(*) FROM graph.artist_member_of")
        members = await cursor.fetchone()
        cursor = await writer.execute("SELECT count(*) FROM graph.vertex_degree")
        degrees = await cursor.fetchone()
        assert members is not None
        assert degrees is not None
        assert (members[0], degrees[0]) == (1, 2)
    finally:
        for conn in (first, second, writer):
            await conn.close()


async def test_the_job_table_accepts_a_musicbrainz_refresh_job(dbname: str) -> None:
    """The latch row and the job row for `loader = 'musicbrainz'` insert like a Discogs one."""
    async with await _connect(dbname) as conn:
        await conn.execute("INSERT INTO loader_extraction_latch (loader, version, generation) VALUES ('musicbrainz', '20261101', 1)")
        await conn.execute("INSERT INTO loader_derived_refresh_job (loader, version, generation) VALUES ('musicbrainz', '20261101', 1)")
        cursor = await conn.execute("SELECT loader, state FROM loader_derived_refresh_job")
        assert await cursor.fetchall() == [("musicbrainz", "pending")]

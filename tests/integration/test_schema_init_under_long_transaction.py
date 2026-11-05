"""Real-engine proof that schema init does not queue behind a long rarity transaction.

The rarity writer holds one transaction open for 1.4-1.8 h on lux. `CREATE INDEX IF NOT EXISTS`
takes SHARE on the table before it finds the index exists, and `ADD COLUMN IF NOT EXISTS` takes
ACCESS EXCLUSIVE before it finds the column, so a schema init during that window waited for the
writer to commit and, once an ALTER was queued, every later reader of the table waited behind it.
Each test gets its own database and starts the writer's transaction as the real job does: an open
transaction holding a row lock on `insights.release_rarity`.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg import sql

from groovemap_schema import initializer, postgres
from groovemap_schema.postgres import _execute_schema_statements, _schema_statements, create_postgres_schema


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.timeout(300)]

HOLD_S = 6.0
LOCK_TIMEOUT_MS = 500
ATTEMPTS = 2
_ADD_COLUMN = "ALTER TABLE insights.release_rarity ADD COLUMN IF NOT EXISTS label_ids BIGINT[]"
_CREATE_INDEX = "CREATE INDEX IF NOT EXISTS idx_release_rarity_tier_score ON insights.release_rarity (tier, rarity_score DESC, release_id)"


def _params(dbname: str) -> dict[str, Any]:
    return {**initializer._postgres_connection_params(), "dbname": dbname}


class _Pool:
    """The slice of the resilient pool `create_postgres_schema` uses: `connection()`."""

    def __init__(self, dbname: str) -> None:
        self._dbname = dbname

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
        async with await psycopg.AsyncConnection.connect(**_params(self._dbname)) as conn:
            yield conn


@pytest_asyncio.fixture
async def dbname() -> AsyncIterator[str]:
    name = f"init_lock_{uuid4().hex[:12]}"
    async with await psycopg.AsyncConnection.connect(**_params("postgres"), autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        async with await psycopg.AsyncConnection.connect(**_params(name), autocommit=True) as conn:
            async with conn.cursor() as cursor:
                _success, failures = await _execute_schema_statements(cursor, _schema_statements())
            assert failures == 0
            await conn.execute("INSERT INTO insights.release_rarity (release_id, rarity_score, tier) VALUES (1, 1.0, 'common')")
        yield name
    finally:
        async with await psycopg.AsyncConnection.connect(**_params("postgres"), autocommit=True) as admin:
            await admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


@asynccontextmanager
async def _rarity_writer(name: str) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    """Hold what the rarity job holds: an open transaction that has written the table."""
    async with await psycopg.AsyncConnection.connect(**_params(name)) as writer:
        await writer.execute("UPDATE insights.release_rarity SET rarity_score = rarity_score + 1 WHERE release_id = 1")
        try:
            yield writer
        finally:
            await writer.rollback()


async def _reader_latencies(name: str, stop: asyncio.Event) -> list[float]:
    """Read the table every 50 ms the way catalog-api does, recording each read's latency."""
    latencies: list[float] = []
    async with await psycopg.AsyncConnection.connect(**_params(name), autocommit=True) as reader:
        while not stop.is_set():
            start = time.perf_counter()
            await reader.execute("SELECT count(*) FROM insights.release_rarity WHERE tier = 'common'")
            latencies.append(time.perf_counter() - start)
            await asyncio.sleep(0.05)
    return latencies


async def test_before_an_unbounded_alter_blocks_the_writer_and_every_reader(dbname: str) -> None:
    """The old behaviour: the statement waits for the writer and the readers queue behind it."""
    async with _rarity_writer(dbname):
        stop = asyncio.Event()
        reader = asyncio.create_task(_reader_latencies(dbname, stop))
        await asyncio.sleep(0.3)

        async def old_init_statement() -> None:
            async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as conn:
                await conn.execute(_ADD_COLUMN)

        init = asyncio.create_task(old_init_statement())
        await asyncio.sleep(3.0)
        still_waiting = not init.done()
        blocked_for = 3.0
    # The writer has ended: the queued ALTER finishes and the readers behind it are released.
    await init
    stop.set()
    latencies = await reader
    assert still_waiting, "the no-op ADD COLUMN returned while the writer held its transaction"
    assert max(latencies) >= blocked_for - 0.5, f"readers were not stalled: {max(latencies):.2f}s"
    print(f"\nBEFORE: init waiting after {blocked_for:.0f}s (no bound); slowest reader {max(latencies):.2f}s")


async def test_after_a_finished_schema_takes_no_lock_during_a_held_transaction(dbname: str) -> None:
    """Re-running init against a complete schema skips every no-op and never touches the table's lock."""
    async with _rarity_writer(dbname):
        stop = asyncio.Event()
        reader = asyncio.create_task(_reader_latencies(dbname, stop))
        await asyncio.sleep(0.3)
        start = time.perf_counter()
        failures = await create_postgres_schema(_Pool(dbname), lock_timeout_ms=LOCK_TIMEOUT_MS, lock_attempts=ATTEMPTS)
        elapsed = time.perf_counter() - start
        stop.set()
        latencies = await reader
    assert failures == 0
    assert elapsed < HOLD_S / 2, f"init took {elapsed:.2f}s with the writer holding its transaction"
    assert max(latencies) < 0.5, f"readers stalled {max(latencies):.2f}s"
    print(f"\nAFTER (schema complete): init {elapsed:.2f}s, failures {failures}, slowest reader {max(latencies) * 1000:.0f} ms")


async def test_after_a_needed_statement_fails_fast_and_readers_stall_at_most_the_timeout(
    dbname: str, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A statement that really needs the lock gives up within a stated bound, and a re-run converges."""
    monkeypatch.setattr(postgres, "SCHEMA_INIT_LOCK_RETRY_PAUSE_S", 0.2)
    async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as conn:
        await conn.execute("ALTER TABLE insights.release_rarity DROP COLUMN label_ids")

    async with _rarity_writer(dbname):
        stop = asyncio.Event()
        reader = asyncio.create_task(_reader_latencies(dbname, stop))
        await asyncio.sleep(0.3)
        start = time.perf_counter()
        with caplog.at_level("ERROR"):
            failures = await create_postgres_schema(_Pool(dbname), lock_timeout_ms=LOCK_TIMEOUT_MS, lock_attempts=ATTEMPTS)
        elapsed = time.perf_counter() - start
        stop.set()
        latencies = await reader

    bound = LOCK_TIMEOUT_MS / 1000 * ATTEMPTS + 0.2 * (ATTEMPTS - 1)
    assert failures > 0
    assert elapsed < bound + 3.0, f"init took {elapsed:.2f}s against a bound of about {bound:.1f}s"
    assert elapsed < HOLD_S, "init waited for the writer"
    assert "Schema init stopped at" in caplog.text
    assert "rarity job" in caplog.text
    assert max(latencies) < LOCK_TIMEOUT_MS / 1000 + 0.5, f"a reader stalled {max(latencies):.2f}s"
    print(
        f"\nAFTER (column missing): init gave up in {elapsed:.2f}s (bound ~{bound:.1f}s), "
        f"failures {failures}, slowest reader {max(latencies) * 1000:.0f} ms"
    )

    # The writer has ended. The same init now finishes and the column is back.
    assert await create_postgres_schema(_Pool(dbname), lock_timeout_ms=LOCK_TIMEOUT_MS, lock_attempts=ATTEMPTS) == 0
    async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as conn:
        cursor = await conn.execute(
            "SELECT 1 FROM information_schema.columns WHERE table_schema = 'insights' AND table_name = 'release_rarity' AND column_name = 'label_ids'"
        )
        assert await cursor.fetchone() is not None


async def test_the_session_timeout_does_not_leak_back_into_the_pool(dbname: str) -> None:
    """The connection goes back to the pool: it must not carry the init's lock_timeout."""
    seen: list[str] = []

    class _Recording(_Pool):
        @asynccontextmanager
        async def connection(self) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
            async with super().connection() as conn:
                yield conn
                cursor = await conn.execute("SHOW lock_timeout")
                row = await cursor.fetchone()
                assert row is not None
                seen.append(str(row[0]))

    assert await create_postgres_schema(_Recording(dbname)) == 0
    assert seen == ["0"]


async def test_no_op_statements_are_the_only_ones_skipped(dbname: str) -> None:
    """The skip applies to an existing index or column and nothing else: a missing one is still sent."""
    async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as conn:
        await conn.execute("DROP INDEX insights.idx_release_rarity_tier_score")
        await conn.execute("ALTER TABLE insights.release_rarity DROP COLUMN label_ids")
    assert await create_postgres_schema(_Pool(dbname)) == 0
    async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as conn:
        index = await (await conn.execute("SELECT to_regclass('insights.idx_release_rarity_tier_score')")).fetchone()
        column = await (
            await conn.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_schema = 'insights' AND table_name = 'release_rarity' AND column_name = 'label_ids'"
            )
        ).fetchone()
    assert index is not None
    assert index[0] is not None
    assert column is not None

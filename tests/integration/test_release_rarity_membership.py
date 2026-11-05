"""Real-engine proof that a label's or artist's rarity page and exact total come from one statement.

`/api/rarity/label/{id}` used to ship every release id of the label from Neo4j to Postgres
(762,594 ids, 15.7 s for label 1818). `insights.release_rarity` now carries `label_ids` and
`artist_ids` with GIN indexes, so the page and the total are one query. The old statement is run
against the same rows to show the answers are identical and what each costs. Each test gets its own
database.
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
BIG_LABEL = 1818
SMALL_LABEL = 999_001
BIG_ARTIST = 7
PAGE = 20

_ONE_STATEMENT = (
    "SELECT release_id, rarity_score, count(*) OVER () AS total FROM insights.release_rarity "
    "WHERE {column} @> ARRAY[%s]::bigint[] ORDER BY rarity_score DESC, release_id LIMIT %s OFFSET %s"
)
_SHIPPED_IDS = "SELECT release_id, rarity_score FROM insights.release_rarity WHERE release_id = ANY(%s) ORDER BY rarity_score DESC, release_id LIMIT %s OFFSET %s"


def _params(dbname: str) -> dict[str, Any]:
    return {**initializer._postgres_connection_params(), "dbname": dbname}


@pytest_asyncio.fixture
async def connection() -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    dbname = f"rarity_members_{uuid4().hex[:12]}"
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
    # A third of the table is on the big label; fifty releases are on the small one; every release
    # has one or two artists, and the big artist is on a tenth of them. Releases with no label or
    # artist keep NULL, as a release the writer found none for does.
    await conn.execute(
        """
        INSERT INTO insights.release_rarity (release_id, rarity_score, tier, label_ids, artist_ids)
        SELECT g, ((g * 37) %% 1000) / 10.0, 'common',
               CASE WHEN g %% 3 = 0 THEN ARRAY[%(big)s::bigint] WHEN g %% 6000 = 1 THEN ARRAY[%(small)s::bigint, 5]
                    WHEN g %% 7 = 0 THEN NULL ELSE ARRAY[(g %% 5000 + 10)::bigint] END,
               CASE WHEN g %% 10 = 0 THEN ARRAY[%(artist)s::bigint, (g %% 900)::bigint] ELSE ARRAY[(g %% 90000 + 100)::bigint] END
        FROM generate_series(1, %(rows)s) AS g
        """,
        {"big": BIG_LABEL, "small": SMALL_LABEL, "artist": BIG_ARTIST, "rows": ROWS},
    )
    await conn.execute("ANALYZE insights.release_rarity")


async def _median_ms(conn: psycopg.AsyncConnection[Any], query: str, params: tuple[Any, ...], runs: int = 7) -> tuple[list[tuple[Any, ...]], float]:
    samples: list[float] = []
    rows: list[tuple[Any, ...]] = []
    for _ in range(runs):
        start = time.perf_counter()
        rows = await (await conn.execute(query, params)).fetchall()
        samples.append((time.perf_counter() - start) * 1000)
    return rows, sorted(samples)[runs // 2]


@pytest.mark.parametrize(("column", "key"), [("label_ids", SMALL_LABEL), ("artist_ids", BIG_ARTIST), ("label_ids", BIG_LABEL)])
async def test_page_and_exact_total_come_from_one_statement(connection: psycopg.AsyncConnection[Any], column: str, key: int) -> None:
    await _seed(connection)
    rows, ms = await _median_ms(connection, _ONE_STATEMENT.format(column=column), (key, PAGE, 0))
    truth = (
        await (await connection.execute(f"SELECT count(*) FROM insights.release_rarity WHERE {column} @> ARRAY[%s]::bigint[]", (key,))).fetchone()  # noqa: S608
        or (0,)
    )[0]
    assert truth > 0
    assert len(rows) == min(PAGE, truth)
    assert {total for _id, _score, total in rows} == {truth}  # the total is exact on every row of the page
    # and the page is the same one the shipped-ids statement returns for the same members
    members = [
        r[0]
        for r in await (
            await connection.execute(f"SELECT release_id FROM insights.release_rarity WHERE {column} @> ARRAY[%s]::bigint[]", (key,))  # noqa: S608
        ).fetchall()
    ]
    old_rows, old_ms = await _median_ms(connection, _SHIPPED_IDS, (members, PAGE, 0))
    print(f"\n{column}={key} ({truth} releases): shipped ids + separate count {old_ms:.1f} ms+, one statement {ms:.1f} ms")
    assert [(i, s) for i, s, _t in rows] == old_rows


async def test_a_deep_page_and_the_total_agree(connection: psycopg.AsyncConnection[Any]) -> None:
    await _seed(connection)
    truth = (
        await (
            await connection.execute("SELECT count(*) FROM insights.release_rarity WHERE label_ids @> ARRAY[%s]::bigint[]", (BIG_LABEL,))
        ).fetchone()
        or (0,)
    )[0]
    last = (truth // PAGE) * PAGE
    rows = await (await connection.execute(_ONE_STATEMENT.format(column="label_ids"), (BIG_LABEL, PAGE, last))).fetchall()
    assert rows
    assert {total for _id, _score, total in rows} == {truth}
    assert len(rows) == truth - last
    assert await (await connection.execute(_ONE_STATEMENT.format(column="label_ids"), (BIG_LABEL, PAGE, truth + PAGE))).fetchall() == []


async def test_the_selective_lookup_uses_the_gin_index(connection: psycopg.AsyncConnection[Any]) -> None:
    await _seed(connection)
    plan = "\n".join(
        line
        for (line,) in await (await connection.execute("EXPLAIN " + _ONE_STATEMENT.format(column="label_ids"), (SMALL_LABEL, PAGE, 0))).fetchall()
    )
    assert "idx_release_rarity_label_ids" in plan, plan


async def test_a_table_that_predates_the_columns_gains_them_and_a_second_apply_is_a_no_op(connection: psycopg.AsyncConnection[Any]) -> None:
    await connection.execute("INSERT INTO insights.release_rarity (release_id, rarity_score, tier) VALUES (1, 5.0, 'common')")
    await connection.execute("DROP INDEX insights.idx_release_rarity_label_ids")
    await connection.execute("ALTER TABLE insights.release_rarity DROP COLUMN label_ids, DROP COLUMN artist_ids")
    for _ in range(2):
        async with connection.cursor() as cursor:
            _success, failures = await _execute_schema_statements(cursor, _schema_statements())
        assert failures == 0
    columns = {
        r[0]
        for r in await (
            await connection.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema = 'insights' AND table_name = 'release_rarity'"
            )
        ).fetchall()
    }
    assert {"label_ids", "artist_ids"} <= columns
    assert await (await connection.execute("SELECT label_ids, artist_ids FROM insights.release_rarity WHERE release_id = 1")).fetchall() == [
        (None, None)
    ]
    indexes = {r[0] for r in await (await connection.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'release_rarity'")).fetchall()}
    assert {"idx_release_rarity_label_ids", "idx_release_rarity_artist_ids"} <= indexes

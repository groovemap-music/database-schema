"""Real-engine proof that activity keyset paging is served by the primary key and stays flat.

analytics-engine reads `activity.events` a page at a time with `ORDER BY occurred_at, event_id
LIMIT n` and the cursor `(occurred_at, event_id) > ($1, $2)`. The table's primary key is already
`(occurred_at, event_id)` on every partition, so a page is an index range scan: no sort, and the
rows read per page do not grow with the table or with how deep the cursor is. An audit that
measured 102 ms per page at 400k events used a table without that key; these tests hold the real
schema to the property instead, so a later change to the key or the ordering fails here.
Each test gets its own database.
"""

from __future__ import annotations

import re
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


# Seeding fires the per-row idempotency claim trigger, so a few tens of thousands of rows take a while.
pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.timeout(300)]

PAGE = 1000
SUBJECTS = 20_000
ELIGIBLE = 15_000
WINDOW = ("2026-11-01T00:00:00Z", "2027-01-01T00:00:00Z")

_COLUMNS = "event_id, event_type, schema_version, subject_id, session_id, occurred_at, producer, consent_purposes"
_WHERE = "occurred_at >= %s AND occurred_at < %s AND %s = ANY(consent_purposes) AND subject_id = ANY(%s)"
_FIRST = f"SELECT {_COLUMNS} FROM activity.events WHERE {_WHERE} ORDER BY occurred_at, event_id LIMIT %s"  # noqa: S608
_NEXT = f"SELECT {_COLUMNS} FROM activity.events WHERE {_WHERE} AND (occurred_at, event_id) > (%s, %s) ORDER BY occurred_at, event_id LIMIT %s"  # noqa: S608


def _params(dbname: str) -> dict[str, Any]:
    return {**initializer._postgres_connection_params(), "dbname": dbname}


def _subject(index: int) -> str:
    return f"00000000-0000-0000-0000-{index:012d}"


@pytest_asyncio.fixture
async def connection() -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    dbname = f"activity_paging_{uuid4().hex[:12]}"
    async with await psycopg.AsyncConnection.connect(**_params("postgres"), autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    try:
        async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as conn:
            async with conn.cursor() as cursor:
                _success, failures = await _execute_schema_statements(cursor, _schema_statements())
            assert failures == 0
            await conn.execute("SELECT activity.ensure_month_partition('events', '2026-11-01')")
            yield conn
    finally:
        async with await psycopg.AsyncConnection.connect(**_params("postgres"), autocommit=True) as admin:
            await admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname)))


async def _seed(conn: psycopg.AsyncConnection[Any], rows: int) -> None:
    await conn.execute(
        """
        INSERT INTO activity.events (event_type, schema_version, subject_id, occurred_at, producer, consent_purposes, idempotency_key)
        SELECT 'view', 1, ('00000000-0000-0000-0000-' || lpad((g %% %s)::text, 12, '0'))::uuid,
               '2026-11-01'::timestamptz + g * interval '3 seconds', 'test', ARRAY['model_training'], 'key' || g
        FROM generate_series(1, %s) AS g
        """,
        (SUBJECTS, rows),
    )
    await conn.execute("ANALYZE activity.events")


async def _page_plan(conn: psycopg.AsyncConnection[Any], fraction: float) -> tuple[str, int]:
    """Return the plan text and shared buffers touched for the page after the row `fraction` of the way in."""
    subjects = [_subject(i) for i in range(ELIGIBLE)]
    cursor = await (
        await conn.execute(
            "SELECT occurred_at, event_id FROM activity.events ORDER BY occurred_at, event_id OFFSET (SELECT (count(*) * %s)::bigint FROM activity.events) LIMIT 1",
            (fraction,),
        )
    ).fetchone()
    assert cursor is not None
    args = (*WINDOW, "model_training", subjects, cursor[0], cursor[1], PAGE)
    plan = "\n".join(line for (line,) in await (await conn.execute("EXPLAIN (ANALYZE, BUFFERS) " + _NEXT, args)).fetchall())
    buffers = sum(int(n) for n in re.findall(r"shared (?:hit|read)=(\d+)", plan.split("\n")[1]))
    return plan, buffers


async def test_the_primary_key_is_the_paging_order_on_the_events_and_impressions_tables(connection: psycopg.AsyncConnection[Any]) -> None:
    rows = await (
        await connection.execute(
            "SELECT conrelid::regclass::text, pg_get_constraintdef(oid) FROM pg_constraint WHERE contype = 'p' AND conrelid IN ('activity.events'::regclass, 'activity.impressions'::regclass)"
        )
    ).fetchall()
    assert dict(rows) == {
        "activity.events": "PRIMARY KEY (occurred_at, event_id)",
        "activity.impressions": "PRIMARY KEY (occurred_at, impression_id)",
    }


async def test_a_page_is_an_index_range_scan_with_no_sort_and_the_same_rows_at_any_depth(connection: psycopg.AsyncConnection[Any]) -> None:
    await _seed(connection, 60_000)
    first = "\n".join(
        line
        for (line,) in await (
            await connection.execute("EXPLAIN " + _FIRST, (*WINDOW, "model_training", [_subject(i) for i in range(ELIGIBLE)], PAGE))
        ).fetchall()
    )
    assert "_pkey" in first, first
    assert "Sort" not in first.replace("Sort Key", ""), first
    assert "Seq Scan" not in first, first

    early_plan, early = await _page_plan(connection, 0.05)
    late_plan, late = await _page_plan(connection, 0.95)
    for plan in (early_plan, late_plan):
        assert "_pkey" in plan, plan
        assert "Seq Scan" not in plan, plan
    # a page reads the same few pages of index and heap wherever the cursor is
    assert late <= early * 2 + 50, (early, late)


async def test_cost_per_page_does_not_grow_with_the_table(connection: psycopg.AsyncConnection[Any]) -> None:
    await _seed(connection, 5_000)
    _plan, small = await _page_plan(connection, 0.5)
    await connection.execute(
        """
        INSERT INTO activity.events (event_type, schema_version, subject_id, occurred_at, producer, consent_purposes, idempotency_key)
        SELECT 'view', 1, ('00000000-0000-0000-0000-' || lpad((g %% %s)::text, 12, '0'))::uuid,
               '2026-12-01'::timestamptz + g * interval '3 seconds', 'test', ARRAY['model_training'], 'later' || g
        FROM generate_series(1, 15000) AS g
        """,
        (SUBJECTS,),
    )
    await connection.execute("ANALYZE activity.events")
    # the same cursor, now in a table four times the size
    _plan, large = await _page_plan(connection, 0.1)
    assert large <= small * 2 + 50, (small, large)

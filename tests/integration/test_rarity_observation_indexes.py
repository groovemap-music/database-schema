"""Real-engine proof that the rarity tier browse and the observation cascade use their indexes.

The tier browse is catalog-api's `get_rarity_leaderboard` statement, run literally:
`WHERE tier = %s ORDER BY rarity_score DESC, release_id LIMIT %s OFFSET %s`, and its keyset form
`tier = $1 AND rarity_score <= $2::real AND (rarity_score < $2::real OR release_id > $3)`. The
data follows the writer: a score rounded to 0.1, the tier derived from that score by the
thresholds in `api/rarity/core.py`, and one score holding most of a tier, so a tie group is
large. The rows must be identical with and without `idx_release_rarity_tier_score`: the index
changes the plan, never the answer. Each test gets its own database.
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


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

ROWS = 300_000
PAGE = 1000
TIER_INDEX = "idx_release_rarity_tier_score"

_ROUTE = (
    "SELECT release_id, title, artist_name, year, rarity_score, tier, hidden_gem_score "
    "FROM insights.release_rarity WHERE tier = %s ORDER BY rarity_score DESC, release_id LIMIT %s OFFSET %s"
)
_PAGE_FIRST = "SELECT release_id, rarity_score FROM insights.release_rarity WHERE tier = %s ORDER BY rarity_score DESC, release_id LIMIT %s"
_PAGE_NEXT = (
    "SELECT release_id, rarity_score FROM insights.release_rarity "
    "WHERE tier = %s AND rarity_score <= %s::real AND (rarity_score < %s::real OR release_id > %s) "
    "ORDER BY rarity_score DESC, release_id LIMIT %s"
)
TIE_SCORE = 8.3


def _params(dbname: str) -> dict[str, Any]:
    return {**initializer._postgres_connection_params(), "dbname": dbname}


async def _rows(dbname: str, query: Any, parameters: Any = None) -> list[tuple[Any, ...]]:
    async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as connection:
        cursor = await connection.execute(query, parameters)
        return await cursor.fetchall() if cursor.description else []


@pytest_asyncio.fixture
async def database() -> AsyncIterator[str]:
    dbname = f"rarity_idx_{uuid4().hex[:12]}"
    await _rows("postgres", sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    try:
        async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as connection, connection.cursor() as cursor:
            _success, failures = await _execute_schema_statements(cursor, _schema_statements())
        assert failures == 0
        yield dbname
    finally:
        await _rows("postgres", sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname)))


async def _seed_rarity(dbname: str) -> None:
    # Scores are rounded to 0.1 and the tier follows from the score, as the writer does. 55% of
    # the rows sit on one score (a common release scores low and alike), the rest spread over 0-100.
    await _rows(
        dbname,
        """
        INSERT INTO insights.release_rarity (release_id, rarity_score, tier)
        SELECT g, score,
               CASE WHEN score >= 80 THEN 'ultra-rare' WHEN score >= 60 THEN 'rare' WHEN score >= 40 THEN 'scarce'
                    WHEN score >= 20 THEN 'uncommon' ELSE 'common' END
        FROM (SELECT g, CASE WHEN g %% 100 < 55 THEN %(tie)s::numeric ELSE ((g * 37) %% 1000) / 10.0 END AS score
              FROM generate_series(1, %(rows)s) AS g) AS scored
        """,
        {"tie": TIE_SCORE, "rows": ROWS},
    )
    await _rows(dbname, "ANALYZE insights.release_rarity")


async def _plan(dbname: str, query: str, parameters: tuple[Any, ...]) -> str:
    rows = await _rows(dbname, "EXPLAIN " + query, parameters)
    return "\n".join(line for (line,) in rows)


async def _timed(dbname: str, query: str, parameters: tuple[Any, ...], runs: int = 15) -> tuple[list[tuple[Any, ...]], float]:
    """Return the rows and the median milliseconds over `runs` executions on one connection."""
    samples: list[float] = []
    rows: list[tuple[Any, ...]] = []
    async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as connection:
        for _ in range(runs):
            start = time.perf_counter()
            rows = await (await connection.execute(query, parameters)).fetchall()
            samples.append((time.perf_counter() - start) * 1000)
    return rows, sorted(samples)[runs // 2]


async def _walk(dbname: str) -> list[tuple[Any, ...]]:
    """Page through the whole tier with the keyset query, as a client does."""
    collected: list[tuple[Any, ...]] = []
    async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as connection:
        page = await (await connection.execute(_PAGE_FIRST, ("common", PAGE))).fetchall()
        while page:
            collected.extend(page)
            last_id, last_score = page[-1]
            page = await (await connection.execute(_PAGE_NEXT, ("common", last_score, last_score, last_id, PAGE))).fetchall()
    return collected


async def test_the_literal_route_and_the_keyset_page_use_the_composite_index(database: str) -> None:
    await _seed_rarity(database)
    plans = [
        await _plan(database, _ROUTE, ("common", 20, 0)),
        await _plan(database, _ROUTE, ("ultra-rare", 20, 0)),
        await _plan(database, _PAGE_FIRST, ("common", PAGE)),
        await _plan(database, _PAGE_NEXT, ("common", TIE_SCORE, TIE_SCORE, 50_000, PAGE)),
        await _plan(database, _PAGE_NEXT, ("ultra-rare", 90.0, 90.0, 1000, PAGE)),
    ]
    for plan in plans:
        assert TIER_INDEX in plan, plan
        assert "Sort" not in plan, plan  # neither a Sort nor an Incremental Sort of a tie group


# (label, query, parameters). The tie-group page is the case a descending release_id gets wrong.
_CASES: list[tuple[str, str, tuple[Any, ...]]] = [
    ("route, common first page", _ROUTE, ("common", 20, 0)),
    ("route, ultra-rare first page", _ROUTE, ("ultra-rare", 20, 0)),
    ("keyset, page inside the common tie group", _PAGE_NEXT, ("common", TIE_SCORE, TIE_SCORE, 50_000, 20)),
    ("keyset, deep ultra-rare page", _PAGE_NEXT, ("ultra-rare", 85.0, 85.0, 5000, 20)),
]


async def test_the_index_changes_the_plan_and_the_speed_but_not_the_rows(database: str) -> None:
    await _seed_rarity(database)
    with_index = [await _timed(database, query, params) for _label, query, params in _CASES]
    walked_with = await _walk(database)

    # the variant with release_id descending, to show what the ascending order buys in a tie group
    await _rows(database, sql.SQL("DROP INDEX insights.{}").format(sql.Identifier(TIER_INDEX)))
    await _rows(database, "CREATE INDEX idx_variant_desc ON insights.release_rarity (tier, rarity_score DESC, release_id DESC)")
    await _rows(database, "ANALYZE insights.release_rarity")
    variant = [await _timed(database, query, params) for _label, query, params in _CASES]
    await _rows(database, "DROP INDEX insights.idx_variant_desc")

    without_index = [await _timed(database, query, params) for _label, query, params in _CASES]
    walked_without = await _walk(database)

    for (label, _query, _unused), (rows_with, ms_with), (rows_var, ms_var), (rows_without, ms_without) in zip(
        _CASES, with_index, variant, without_index, strict=True
    ):
        print(f"\n{label} over {ROWS} rows: {ms_without:.2f} ms no index, {ms_var:.2f} ms release_id DESC index, {ms_with:.2f} ms shipped index")
        assert rows_with == rows_without == rows_var
        assert rows_with, label
    assert walked_with == walked_without
    # complete: every common row is reached exactly once, in order
    expected = (await _rows(database, "SELECT count(*) FROM insights.release_rarity WHERE tier = 'common'"))[0][0]
    assert len(walked_with) == expected
    assert len({release_id for release_id, _score in walked_with}) == expected
    assert [row[1] for row in walked_with] == sorted((row[1] for row in walked_with), reverse=True)


async def _seed_observations(dbname: str, copies: int = 2000, per_copy: int = 100) -> None:
    await _rows(dbname, "INSERT INTO users (email, hashed_password) VALUES ('u@example.org', 'x')")
    await _rows(dbname, "INSERT INTO catalog_items (id, kind) VALUES ('00000000-0000-0000-0000-000000000001', 'release')")
    await _rows(
        dbname,
        "INSERT INTO owned_copies (user_id, item_id) SELECT (SELECT id FROM users), '00000000-0000-0000-0000-000000000001' FROM generate_series(1, %s)",
        (copies,),
    )
    await _rows(
        dbname,
        "INSERT INTO observations (user_id, owned_copy_id, kind, value, source) "
        "SELECT (SELECT id FROM users), c.id, 'grading', 'VG+', 'user' FROM owned_copies c, generate_series(1, %s)",
        (per_copy,),
    )
    await _rows(dbname, "ANALYZE observations")


async def _delete_copies_ms(dbname: str, count: int = 50) -> float:
    ids = [row[0] for row in await _rows(dbname, "SELECT id FROM owned_copies ORDER BY id LIMIT %s", (count,))]
    start = time.perf_counter()
    await _rows(dbname, "DELETE FROM owned_copies WHERE id = ANY(%s)", (ids,))
    return (time.perf_counter() - start) * 1000


async def test_the_observation_lookup_uses_the_index_and_the_cascade_still_deletes(database: str) -> None:
    await _seed_observations(database)
    copy_id = (await _rows(database, "SELECT id FROM owned_copies ORDER BY id LIMIT 1"))[0][0]
    plan = await _plan(database, "SELECT 1 FROM ONLY observations WHERE owned_copy_id = %s", (copy_id,))
    assert "idx_observations_owned_copy_id" in plan, plan

    with_ms = await _delete_copies_ms(database)
    assert await _rows(database, "SELECT count(*) FROM observations") == [(2000 * 100 - 50 * 100,)]

    # the same deletes against the table as it was before the index
    await _rows(database, "DROP INDEX idx_observations_owned_copy_id")
    without_ms = await _delete_copies_ms(database)
    print(f"\ndelete of 50 owned copies (100 observations each, 200k rows): {without_ms:.1f} ms without, {with_ms:.1f} ms with")
    assert await _rows(database, "SELECT count(*) FROM observations") == [(2000 * 100 - 100 * 100,)]


async def test_applying_the_schema_again_is_a_no_op_for_both_indexes(database: str) -> None:
    async with await psycopg.AsyncConnection.connect(**_params(database), autocommit=True) as connection, connection.cursor() as cursor:
        _success, failures = await _execute_schema_statements(cursor, _schema_statements())
    assert failures == 0
    names = {
        name
        for (name,) in await _rows(
            database, "SELECT indexname FROM pg_indexes WHERE indexname = ANY(%s)", ([TIER_INDEX, "idx_observations_owned_copy_id"],)
        )
    }
    assert names == {TIER_INDEX, "idx_observations_owned_copy_id"}

"""Real-engine proof that the set-based `first_year` returns the correlated form's values, faster.

`genre_stats` and `style_stats` used to find `first_year` with a correlated subquery per tag that
joined each tag edge to the `graph.release` view, so a release's document was read once for every
tag it carries. The bootstrap now reads each release once. These tests run both bodies against the
same rows, including every malformed `year` the importer can leave in a document, and require the
same rows back. Each test gets its own database.
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
from groovemap_schema.postgres import _COUNTER_BOOTSTRAP, _execute_schema_statements, _first_year_by_tag, _schema_statements


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.timeout(300)]

RELEASES = 120_000
GENRES = 15
STYLES = 60

# The body before the change, kept verbatim as the reference the new one must equal.
_LEGACY = {
    "genre": """
SELECT genre.name AS name,
       (SELECT min(btrim(release.year)::integer)
          FROM graph.in_genre AS edge
          JOIN graph.release AS release ON release.release_id = edge.release_id
         WHERE edge.genre_name = genre.name
           AND btrim(release.year) ~ '^[0-9]+$'
           AND btrim(release.year)::numeric > 0) AS first_year
FROM graph.genre AS genre
""",
    "style": """
SELECT style.name AS name,
       (SELECT min(btrim(release.year)::integer)
          FROM graph.in_style AS edge
          JOIN graph.release AS release ON release.release_id = edge.release_id
         WHERE edge.style_name = style.name
           AND btrim(release.year) ~ '^[0-9]+$'
           AND btrim(release.year)::numeric > 0) AS first_year
FROM graph.style AS style
""",
}


def _params(dbname: str) -> dict[str, Any]:
    return {**initializer._postgres_connection_params(), "dbname": dbname}


@pytest_asyncio.fixture
async def connection() -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    dbname = f"first_year_{uuid4().hex[:12]}"
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
    # Years cycle through the shapes a document can hold: valid, padded, zero, empty, text,
    # missing, short and long. Each release carries one to three genres and styles.
    await conn.execute(
        """
        INSERT INTO releases (data_id, hash, data)
        SELECT g::text, 'h' || g,
               jsonb_strip_nulls(jsonb_build_object(
                   'year', CASE g %% 10 WHEN 0 THEN NULL WHEN 1 THEN '' WHEN 2 THEN 'abc' WHEN 3 THEN '0'
                                        WHEN 4 THEN ' ' || (1950 + g %% 70) || ' ' WHEN 5 THEN '0000' WHEN 6 THEN '999'
                                        ELSE (1950 + g %% 70)::text END,
                   'genres', (SELECT jsonb_agg('Genre ' || ((g + k) %% %(genres)s)) FROM generate_series(0, g %% 3) AS k),
                   'styles', (SELECT jsonb_agg('Style ' || ((g * 7 + k) %% %(styles)s)) FROM generate_series(0, g %% 3) AS k)))
        FROM generate_series(1, %(releases)s) AS g
        """,
        {"genres": GENRES, "styles": STYLES, "releases": RELEASES},
    )
    await conn.execute("INSERT INTO graph.genre (name) SELECT 'Genre ' || g FROM generate_series(0, %s) AS g", (GENRES,))
    await conn.execute("INSERT INTO graph.style (name) SELECT 'Style ' || g FROM generate_series(0, %s) AS g", (STYLES,))
    await conn.execute(
        "INSERT INTO graph.in_genre (release_id, genre_name) SELECT DISTINCT data_id, jsonb_array_elements_text(data -> 'genres') FROM releases"
    )
    await conn.execute(
        "INSERT INTO graph.in_style (release_id, style_name) SELECT DISTINCT data_id, jsonb_array_elements_text(data -> 'styles') FROM releases"
    )
    await conn.execute("ANALYZE")


def _first_year_select(body: str) -> str:
    return f"SELECT name, first_year FROM ({body.strip()}) AS stats ORDER BY name"  # noqa: S608


def _set_based(kind: str) -> str:
    """The new first_year alone, as the bootstrap body joins it, without the count subqueries."""
    edge, column = (f"in_{kind}", f"{kind}_name")
    return (
        f"SELECT vertex.name AS name, first_year.first_year AS first_year FROM graph.{kind} AS vertex "  # noqa: S608
        f"LEFT JOIN ({_first_year_by_tag(edge, column)}) AS first_year ON first_year.name = vertex.name"
    )


async def _run(conn: psycopg.AsyncConnection[Any], body: str) -> tuple[list[tuple[Any, ...]], float]:
    start = time.perf_counter()
    rows = await (await conn.execute(body)).fetchall()
    return rows, (time.perf_counter() - start) * 1000


@pytest.mark.parametrize("kind", ["genre", "style"])
async def test_the_set_based_body_returns_the_correlated_bodys_values_faster(connection: psycopg.AsyncConnection[Any], kind: str) -> None:
    await _seed(connection)
    new_rows, new_ms = await _run(connection, _first_year_select(_set_based(kind)))
    old_rows, old_ms = await _run(connection, _first_year_select(_LEGACY[kind]))
    # and the shipped bootstrap body, with its count columns, agrees on the same column
    shipped_rows, _ = await _run(connection, _first_year_select(_COUNTER_BOOTSTRAP[f"{kind}_stats"]))

    print(f"\n{kind}_stats first_year over {RELEASES} releases: {old_ms:.0f} ms correlated, {new_ms:.0f} ms set-based")
    assert new_rows == old_rows
    assert shipped_rows == old_rows
    assert any(year is not None for _name, year in new_rows)
    assert new_ms < old_ms


async def test_a_tag_with_no_dated_release_gets_a_null_first_year(connection: psycopg.AsyncConnection[Any]) -> None:
    await connection.execute('INSERT INTO releases (data_id, hash, data) VALUES (\'1\', \'h\', \'{"year": "abc", "genres": ["Lonely"]}\')')
    await connection.execute("INSERT INTO graph.genre (name) VALUES ('Lonely'), ('Empty')")
    await connection.execute("INSERT INTO graph.in_genre (release_id, genre_name) VALUES ('1', 'Lonely')")
    rows = await (await connection.execute(_first_year_select(_COUNTER_BOOTSTRAP["genre_stats"]))).fetchall()
    assert rows == [("Empty", None), ("Lonely", None)]


async def _delete_unreferenced_items_ms(conn: psycopg.AsyncConnection[Any], count: int) -> float:
    ids = [
        row[0] for row in await (await conn.execute("SELECT id FROM catalog_items WHERE kind = 'label' ORDER BY id LIMIT %s", (count,))).fetchall()
    ]
    start = time.perf_counter()
    await conn.execute("DELETE FROM catalog_items WHERE id = ANY(%s)", (ids,))
    return (time.perf_counter() - start) * 1000


async def test_deleting_orphaned_catalog_items_checks_the_move_ledger_through_its_indexes(connection: psycopg.AsyncConnection[Any]) -> None:
    """`common.identity` deletes orphaned items; each delete checks both ledger columns."""
    await connection.execute(
        "INSERT INTO catalog_items (id, kind) VALUES ('00000000-0000-0000-0000-00000000000a', 'release'), ('00000000-0000-0000-0000-00000000000b', 'release')"
    )
    await connection.execute("INSERT INTO catalog_items (kind) SELECT 'label' FROM generate_series(1, 400)")
    await connection.execute(
        "INSERT INTO catalog_item_supersessions (id, superseded_id, survivor_id, cause, decision_ref) "
        "VALUES ('00000000-0000-0000-0000-0000000000c1', '00000000-0000-0000-0000-00000000000a', '00000000-0000-0000-0000-00000000000b', 'edition_promotion', gen_random_uuid())"
    )
    await connection.execute(
        "INSERT INTO catalog_item_moves (supersession_id, table_name, row_id, from_item_id, to_item_id) "
        "SELECT '00000000-0000-0000-0000-0000000000c1', 'artifacts', gen_random_uuid(), "
        "'00000000-0000-0000-0000-00000000000a', '00000000-0000-0000-0000-00000000000b' FROM generate_series(1, 150000)"
    )
    await connection.execute("ANALYZE catalog_item_moves")
    plan = "\n".join(
        line
        for (line,) in await (
            await connection.execute("EXPLAIN SELECT 1 FROM ONLY catalog_item_moves WHERE from_item_id = '00000000-0000-0000-0000-0000000000ff'")
        ).fetchall()
    )
    assert "idx_catalog_item_moves_from_item_id" in plan, plan

    with_ms = await _delete_unreferenced_items_ms(connection, 150)
    await connection.execute("DROP INDEX idx_catalog_item_moves_from_item_id")
    await connection.execute("DROP INDEX idx_catalog_item_moves_to_item_id")
    without_ms = await _delete_unreferenced_items_ms(connection, 150)
    print(f"\ndelete of 150 orphaned items against 150k ledger rows: {without_ms:.0f} ms without, {with_ms:.0f} ms with")
    assert with_ms < without_ms

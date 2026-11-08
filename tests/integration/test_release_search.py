"""The stored release title vector (gm-database-schema-fi4a), proved on a real PostgreSQL.

`public.release_search` must hold `RELEASE_TITLE_TSVECTOR` of every release and nothing else,
whether the release was there before the triggers (the backfill) or written after them (the
triggers), and whatever the loaders write concurrently with a backfill.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg import sql
from psycopg.types.json import Jsonb

from groovemap_schema import initializer
from groovemap_schema.postgres import (
    _RELEASE_SEARCH_STATEMENTS,
    RELEASE_SEARCH_ROLLBACK_STATEMENTS,
    RELEASE_TITLE_TSVECTOR,
    _execute_schema_statements,
    _execute_schema_statements_bounded,
    _schema_statements,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TRIGGERS = dict(_RELEASE_SEARCH_STATEMENTS)["releases release_search capture triggers"]

# Titles a release document really carries, including none at all.
_TITLES: list[Any] = ["Love Will Tear Us Apart", "  Lovers' Rock  ", "", None, "The Best Of", 1999, "Ça Plane Pour Moi", "lóve"]

# The loader's set-based upsert (discogs-sql-loader tableinator/batch_writer.py), verbatim but for the media column.
_UPSERT = (
    "INSERT INTO releases (hash, data_id, data, gm_item_id, updated_at) "
    "SELECT incoming.hash, incoming.data_id, incoming.data, incoming.gm_item_id, NOW() "
    "FROM unnest(%s::text[], %s::text[], %s::jsonb[], %s::uuid[]) AS incoming(hash, data_id, data, gm_item_id) "
    "ON CONFLICT (data_id) DO UPDATE SET hash = EXCLUDED.hash, data = EXCLUDED.data, gm_item_id = EXCLUDED.gm_item_id, updated_at = NOW()"
)


def _params(dbname: str) -> dict[str, Any]:
    return {**initializer._postgres_connection_params(), "dbname": dbname}


async def _rows(dbname: str, query: Any, parameters: Any = None) -> list[tuple[Any, ...]]:
    async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as connection:
        cursor = await connection.execute(query, parameters)
        return await cursor.fetchall() if cursor.description else []


def _document(data_id: int, title: Any) -> dict[str, Any]:
    document: dict[str, Any] = {"id": data_id, "tracklist": [{"position": "A1", "title": f"track {data_id}"}]}
    if title is not None:
        document["title"] = title
    return document


async def _upsert(dbname: str, documents: dict[str, dict[str, Any]]) -> None:
    ids = list(documents)
    await _rows(dbname, _UPSERT, ([f"h{data_id}" for data_id in ids], ids, [Jsonb(documents[data_id]) for data_id in ids], [None] * len(ids)))


async def _drift(dbname: str) -> tuple[int, int, int]:
    [(missing, orphaned, stale)] = await _rows(dbname, "SELECT * FROM public.release_search_drift()")
    return missing, orphaned, stale


async def _exact(dbname: str) -> bool:
    """Return whether the side table is exactly the canonical vector of every release, computed here."""
    [(differences,)] = await _rows(
        dbname,
        f"SELECT count(*) FROM (SELECT data_id, {RELEASE_TITLE_TSVECTOR} FROM releases "  # noqa: S608 -- module constant
        "EXCEPT SELECT release_id, title_tsv FROM release_search) AS absent",
    )
    [(extra,)] = await _rows(
        dbname,
        f"SELECT count(*) FROM (SELECT release_id, title_tsv FROM release_search "  # noqa: S608 -- module constant
        f"EXCEPT SELECT data_id, {RELEASE_TITLE_TSVECTOR} FROM releases) AS extra",
    )
    return differences == 0 and extra == 0


@pytest_asyncio.fixture
async def database() -> AsyncIterator[str]:
    dbname = f"release_search_{uuid4().hex[:12]}"
    await _rows("postgres", sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    try:
        async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as connection, connection.cursor() as cursor:
            _success, failures = await _execute_schema_statements(cursor, _schema_statements())
        assert failures == 0
        yield dbname
    finally:
        await _rows("postgres", sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname)))


async def _without_triggers(dbname: str, documents: dict[str, dict[str, Any]]) -> None:
    """Write DOCUMENTS as releases that predate the triggers, then put the triggers back."""
    for statement in RELEASE_SEARCH_ROLLBACK_STATEMENTS[:4]:
        await _rows(dbname, statement)
    await _upsert(dbname, documents)
    await _rows(dbname, _TRIGGERS)


async def test_the_backfill_stores_the_canonical_vector_of_every_existing_release(database: str) -> None:
    documents = {str(i): _document(i, _TITLES[i % len(_TITLES)]) for i in range(1, 2001)}
    await _without_triggers(database, documents)
    assert await _rows(database, "SELECT count(*) FROM release_search") == [(0,)]

    await _rows(database, "CALL public.release_search_backfill(3)")

    assert await _rows(database, "SELECT count(*) FROM release_search") == [(2000,)]
    assert await _exact(database)
    assert await _drift(database) == (0, 0, 0)
    # Rerunning finds nothing to do and changes nothing.
    await _rows(database, "CALL public.release_search_backfill()")
    assert await _exact(database)


async def test_loader_writes_keep_the_vector_current(database: str) -> None:
    await _upsert(database, {str(i): _document(i, _TITLES[i % len(_TITLES)]) for i in range(1, 51)})
    assert await _exact(database)

    # A changed document re-ranks under its new title; an unchanged one keeps its row.
    await _upsert(database, {"7": _document(7, "Love Love Love"), "8": _document(8, None)})
    assert await _rows(database, "SELECT title_tsv::text FROM release_search WHERE release_id = '7'") == [("'love':1,2,3",)]
    assert await _exact(database)

    # The loader's stale-row purge, a renamed id, and a single-row write.
    await _rows(database, "DELETE FROM releases WHERE data_id IN ('1', '2', '3')")
    await _rows(database, "UPDATE releases SET data_id = '4000' WHERE data_id = '4'")
    await _rows(
        database,
        "INSERT INTO releases (hash, data_id, data) VALUES ('x', '5000', %s)",
        (Jsonb(_document(5000, "Single Love")),),
    )
    assert await _exact(database)
    assert await _drift(database) == (0, 0, 0)

    await _rows(database, "TRUNCATE releases")
    assert await _rows(database, "SELECT count(*) FROM release_search") == [(0,)]


async def test_an_update_that_sets_neither_id_nor_data_writes_no_vector(database: str) -> None:
    await _upsert(database, {str(i): _document(i, "Love Song") for i in range(1, 11)})
    before = await _rows(database, "SELECT release_id, xmin::text FROM release_search ORDER BY release_id")

    # The loader's refresh of every unchanged record, and its media and identity backfills.
    await _rows(database, "UPDATE releases SET updated_at = NOW()")
    await _rows(database, "UPDATE releases SET media = '{}'::jsonb, gm_item_id = gen_random_uuid()")

    assert await _rows(database, "SELECT release_id, xmin::text FROM release_search ORDER BY release_id") == before


async def test_a_role_that_writes_releases_needs_no_grant_on_the_side_table(database: str) -> None:
    role = f"release_writer_{uuid4().hex[:8]}"
    await _rows(database, sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
    try:
        await _rows(database, sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON releases TO {}").format(sql.Identifier(role)))
        async with await psycopg.AsyncConnection.connect(**_params(database), autocommit=True) as connection:
            await connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            await connection.execute(_UPSERT, (["h1"], ["1"], [Jsonb(_document(1, "Love"))], [None]))
            await connection.execute("UPDATE releases SET data = data || '{\"title\": \"Lovely\"}' WHERE data_id = '1'")
            await connection.execute("DELETE FROM releases WHERE data_id = '1'")
        assert await _rows(database, "SELECT count(*) FROM release_search") == [(0,)]
    finally:
        await _rows(database, sql.SQL("REVOKE ALL ON releases FROM {}").format(sql.Identifier(role)))
        await _rows(database, sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


async def test_a_backfill_concurrent_with_a_load_misses_nothing_and_keeps_the_loads_vectors(database: str) -> None:
    """Every write a load makes during a backfill, interleaved with one-page batches.

    The `updated_at` refresh moves rows without firing a trigger, which is what the backfill's
    second pass exists for. An unlocked backfill deadlocked against this load.
    """
    await _without_triggers(database, {str(i): _document(i, _TITLES[i % len(_TITLES)]) for i in range(1, 3001)})
    done = asyncio.Event()

    async def load() -> int:
        rounds = 0
        while not done.is_set() or rounds < 5:
            base = 1 + (rounds * 97) % 2900
            await _upsert(database, {str(i): _document(i, f"Round {rounds} Love") for i in range(base, base + 40)})
            await _rows(database, "UPDATE releases SET updated_at = NOW() WHERE data_id = ANY(%s)", ([str(i) for i in range(base, base + 300)],))
            await _rows(database, "DELETE FROM releases WHERE data_id = ANY(%s)", ([str(base + 41), str(base + 42)],))
            await _upsert(database, {f"n{rounds}-{i}": _document(i, "New Love") for i in range(3)})
            rounds += 1
        return rounds

    async def backfill() -> None:
        try:
            await _rows(database, "CALL public.release_search_backfill(1)")
        finally:
            done.set()

    rounds, _ = await asyncio.gather(load(), backfill())

    assert rounds >= 5
    assert await _drift(database) == (0, 0, 0)
    assert await _exact(database)


async def test_reapplying_waits_on_no_writer(database: str) -> None:
    """A load's open transaction does not hold up a reapply through the initializer.

    The writer holds ROW EXCLUSIVE on `releases` and, through its trigger, on `release_search`;
    the trigger statement's guard and the initializer's no-op probe send nothing that conflicts.
    """
    async with await psycopg.AsyncConnection.connect(**_params(database)) as writer:
        await writer.execute(_UPSERT, (["h1"], ["1"], [Jsonb(_document(1, "Love"))], [None]))
        async with await psycopg.AsyncConnection.connect(**_params(database), autocommit=True) as applier, applier.cursor() as cursor:
            await cursor.execute("SET lock_timeout = '200ms'")
            _success, failures = await _execute_schema_statements_bounded(
                cursor, _RELEASE_SEARCH_STATEMENTS, lock_timeout_ms=200, attempts=1, retry_pause_s=0
            )
        assert failures == 0
        await writer.rollback()


async def test_the_rollback_removes_everything_and_leaves_releases_writable(database: str) -> None:
    await _upsert(database, {"1": _document(1, "Love")})
    for statement in RELEASE_SEARCH_ROLLBACK_STATEMENTS:
        await _rows(database, statement)

    assert await _rows(database, "SELECT to_regclass('public.release_search')") == [(None,)]
    assert await _rows(database, "SELECT count(*) FROM pg_trigger WHERE tgrelid = 'releases'::regclass AND tgname LIKE 'release_search%'") == [(0,)]
    assert await _rows(database, "SELECT count(*) FROM pg_proc WHERE proname LIKE 'release_search%'") == [(0,)]
    await _upsert(database, {"1": _document(1, "Love Again"), "2": _document(2, "Love")})
    await _rows(database, "DELETE FROM releases")


async def test_a_capture_function_attached_to_another_table_refuses_to_run(database: str) -> None:
    """Attached elsewhere by its owner (PUBLIC cannot), the truncate capture must not empty the side table."""
    await _upsert(database, {"1": _document(1, "Love")})
    await _rows(database, "CREATE TABLE decoy (data_id VARCHAR, data JSONB)")
    await _rows(
        database,
        "CREATE TRIGGER decoy_truncate AFTER TRUNCATE ON decoy FOR EACH STATEMENT EXECUTE FUNCTION public.release_search_capture_truncated()",
    )
    with pytest.raises(psycopg.errors.RaiseException, match=r"fires only on public\.releases"):
        await _rows(database, "TRUNCATE decoy")
    assert await _rows(database, "SELECT count(*) FROM release_search") == [(1,)]
    assert await _rows(
        database,
        "SELECT count(*) FROM pg_proc WHERE proname LIKE 'release\\_search\\_capture\\_%' AND has_function_privilege('public', oid, 'EXECUTE')",
    ) == [(0,)]


async def test_a_function_planted_on_the_callers_search_path_cannot_change_the_backfill(database: str) -> None:
    """The procedure runs under the caller's search_path; its SQL runs in steps that pin theirs."""
    await _without_triggers(database, {str(i): _document(i, _TITLES[i % len(_TITLES)]) for i in range(1, 101)})
    await _rows(database, "CREATE SCHEMA planted")
    await _rows(
        database,
        "CREATE FUNCTION planted.to_tsvector(text, text) RETURNS tsvector LANGUAGE sql AS $$ SELECT 'planted'::tsvector $$",
    )
    async with await psycopg.AsyncConnection.connect(**_params(database), autocommit=True) as connection:
        await connection.execute("SET search_path = planted, public")
        # The plant does win for unqualified SQL on this path, so the test below is not vacuous.
        cursor = await connection.execute("SELECT to_tsvector('english', 'love')::text")
        assert await cursor.fetchone() == ("'planted'",)
        await connection.execute("CALL public.release_search_backfill(1)")
    assert await _exact(database)
    assert await _rows(database, "SELECT count(*) FROM release_search WHERE title_tsv = 'planted'::tsvector") == [(0,)]


async def test_public_cannot_run_the_backfill_or_its_steps(database: str) -> None:
    assert (
        await _rows(
            database,
            "SELECT proname FROM pg_proc WHERE proname LIKE 'release\\_search\\_backfill%' AND has_function_privilege('public', oid, 'EXECUTE')",
        )
        == []
    )

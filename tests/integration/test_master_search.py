"""The stored master title vector and year (gm-database-schema-nu0c), proved on a real PostgreSQL.

`public.master_search` must hold `MASTER_TITLE_TSVECTOR` and `MASTER_YEAR` of every master and
nothing else, whether the master was there before the triggers (the backfill) or written after
them (the triggers), and whatever the loaders write concurrently with a backfill.
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
    _MASTER_SEARCH_STATEMENTS,
    MASTER_SEARCH_ROLLBACK_STATEMENTS,
    MASTER_TITLE_TSVECTOR,
    MASTER_YEAR,
    _execute_schema_statements,
    _execute_schema_statements_bounded,
    _schema_statements,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

_TRIGGERS = dict(_MASTER_SEARCH_STATEMENTS)["masters master_search capture triggers"]

# Titles and years a master document really carries, including none at all. Discogs writes the
# year as a JSON number, a JSON null, or leaves it out; a string is kept as it is.
_TITLES: list[Any] = ["Love Will Tear Us Apart", "  Lovers' Rock  ", "", None, "The Best Of", 1999, "Ça Plane Pour Moi", "lóve"]
_YEARS: list[Any] = [1988, None, "absent", 2003, "1979", 0, "", 1967, 2021]

# The loader's set-based upsert (discogs-sql-loader tableinator/batch_writer.py) for masters.
_UPSERT = (
    "INSERT INTO masters (hash, data_id, data, gm_item_id, updated_at) "
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


def _document(data_id: int, title: Any, year: Any = "absent") -> dict[str, Any]:
    document: dict[str, Any] = {"id": data_id, "artists": [{"id": data_id * 7, "name": f"artist {data_id}"}], "main_release": data_id * 3}
    if title is not None:
        document["title"] = title
    if year != "absent":
        document["year"] = year
    return document


def _sample(first: int, last: int) -> dict[str, dict[str, Any]]:
    return {str(i): _document(i, _TITLES[i % len(_TITLES)], _YEARS[i % len(_YEARS)]) for i in range(first, last + 1)}


async def _upsert(dbname: str, documents: dict[str, dict[str, Any]]) -> None:
    ids = list(documents)
    await _rows(dbname, _UPSERT, ([f"h{data_id}" for data_id in ids], ids, [Jsonb(documents[data_id]) for data_id in ids], [None] * len(ids)))


async def _drift(dbname: str) -> tuple[int, int, int]:
    [(missing, orphaned, stale)] = await _rows(dbname, "SELECT * FROM public.master_search_drift()")
    return missing, orphaned, stale


async def _exact(dbname: str) -> bool:
    """Return whether the side table is exactly the canonical vector and year of every master, computed here."""
    canonical = f"SELECT data_id, {MASTER_TITLE_TSVECTOR}, {MASTER_YEAR} FROM masters"  # noqa: S608 -- module constants
    stored = "SELECT master_id, title_tsv, year FROM master_search"
    [(absent,)] = await _rows(dbname, f"SELECT count(*) FROM ({canonical} EXCEPT {stored}) AS absent")  # noqa: S608
    [(extra,)] = await _rows(dbname, f"SELECT count(*) FROM ({stored} EXCEPT {canonical}) AS extra")  # noqa: S608
    return absent == 0 and extra == 0


@pytest_asyncio.fixture
async def database() -> AsyncIterator[str]:
    dbname = f"master_search_{uuid4().hex[:12]}"
    await _rows("postgres", sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    try:
        async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as connection, connection.cursor() as cursor:
            _success, failures = await _execute_schema_statements(cursor, _schema_statements())
        assert failures == 0
        yield dbname
    finally:
        await _rows("postgres", sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname)))


async def _without_triggers(dbname: str, documents: dict[str, dict[str, Any]]) -> None:
    """Write DOCUMENTS as masters that predate the triggers, then put the triggers back."""
    for statement in MASTER_SEARCH_ROLLBACK_STATEMENTS[:4]:
        await _rows(dbname, statement)
    await _upsert(dbname, documents)
    await _rows(dbname, _TRIGGERS)


async def test_the_backfill_stores_the_canonical_vector_and_year_of_every_existing_master(database: str) -> None:
    await _without_triggers(database, _sample(1, 2000))
    assert await _rows(database, "SELECT count(*) FROM master_search") == [(0,)]

    await _rows(database, "CALL public.master_search_backfill(3)")

    assert await _rows(database, "SELECT count(*) FROM master_search") == [(2000,)]
    assert await _exact(database)
    assert await _drift(database) == (0, 0, 0)
    # The year is the document's text, NULL only where the document has none.
    assert await _rows(database, "SELECT year FROM master_search WHERE master_id IN ('9', '10', '13', '14', '15') ORDER BY master_id::int") == [
        ("1988",),
        (None,),
        ("1979",),
        ("0",),
        ("",),
    ]
    # Rerunning finds nothing to do and changes nothing.
    await _rows(database, "CALL public.master_search_backfill()")
    assert await _exact(database)


async def test_loader_writes_keep_the_vector_and_year_current(database: str) -> None:
    await _upsert(database, _sample(1, 50))
    assert await _exact(database)

    # A changed title re-ranks, a changed year moves decade, a dropped year empties it.
    await _upsert(database, {"7": _document(7, "Love Love Love", 1977), "8": _document(8, "Samba", 1990), "9": _document(9, None)})
    await _upsert(database, {"8": _document(8, "Samba", 1991)})
    assert await _rows(
        database, "SELECT master_id, title_tsv::text, year FROM master_search WHERE master_id IN ('7', '8', '9') ORDER BY master_id"
    ) == [
        ("7", "'love':1,2,3", "1977"),
        ("8", "'samba':1", "1991"),
        ("9", "", None),
    ]
    assert await _exact(database)

    # The loader's stale-row purge, a renamed id, and a single-row write.
    await _rows(database, "DELETE FROM masters WHERE data_id IN ('1', '2', '3')")
    await _rows(database, "UPDATE masters SET data_id = '4000' WHERE data_id = '4'")
    await _rows(database, "INSERT INTO masters (hash, data_id, data) VALUES ('x', '5000', %s)", (Jsonb(_document(5000, "Single Love", 2001)),))
    await _rows(database, "UPDATE masters SET data = jsonb_set(data, '{year}', '1965') WHERE data_id = '5'")
    assert await _rows(database, "SELECT count(*) FROM master_search WHERE master_id IN ('1', '2', '3', '4')") == [(0,)]
    assert await _rows(database, "SELECT year FROM master_search WHERE master_id IN ('4000', '5000', '5') ORDER BY master_id") == [
        ("1979",),  # '4000', moved with its document
        ("1965",),
        ("2001",),
    ]
    assert await _exact(database)
    assert await _drift(database) == (0, 0, 0)

    await _rows(database, "TRUNCATE masters")
    assert await _rows(database, "SELECT count(*) FROM master_search") == [(0,)]


async def test_an_update_that_sets_neither_id_nor_data_writes_no_row(database: str) -> None:
    await _upsert(database, {str(i): _document(i, "Love Song", 1980) for i in range(1, 11)})
    before = await _rows(database, "SELECT master_id, xmin::text FROM master_search ORDER BY master_id")

    # The loader's refresh of every unchanged record, and its identity backfill.
    await _rows(database, "UPDATE masters SET updated_at = NOW()")
    await _rows(database, "UPDATE masters SET gm_item_id = gen_random_uuid(), hash = hash || 'x'")
    # A loader upsert of an unchanged document fires the trigger but rewrites nothing.
    await _upsert(database, {str(i): _document(i, "Love Song", 1980) for i in range(1, 11)})

    assert await _rows(database, "SELECT master_id, xmin::text FROM master_search ORDER BY master_id") == before


async def test_a_role_that_writes_masters_needs_no_grant_on_the_side_table(database: str) -> None:
    role = f"master_writer_{uuid4().hex[:8]}"
    await _rows(database, sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
    try:
        await _rows(database, sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON masters TO {}").format(sql.Identifier(role)))
        async with await psycopg.AsyncConnection.connect(**_params(database), autocommit=True) as connection:
            await connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            await connection.execute(_UPSERT, (["h1"], ["1"], [Jsonb(_document(1, "Love", 1999))], [None]))
            await connection.execute("UPDATE masters SET data = data || '{\"title\": \"Lovely\"}' WHERE data_id = '1'")
            await connection.execute("DELETE FROM masters WHERE data_id = '1'")
        assert await _rows(database, "SELECT count(*) FROM master_search") == [(0,)]
    finally:
        await _rows(database, sql.SQL("REVOKE ALL ON masters FROM {}").format(sql.Identifier(role)))
        await _rows(database, sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


async def test_a_backfill_concurrent_with_a_load_misses_nothing_and_keeps_the_loads_rows(database: str) -> None:
    """Every write a load makes during a backfill, interleaved with one-page batches.

    The `updated_at` refresh moves rows without firing a trigger, which is what the backfill's
    second pass exists for.
    """
    await _without_triggers(database, _sample(1, 3000))
    done = asyncio.Event()

    async def load() -> int:
        rounds = 0
        while not done.is_set() or rounds < 5:
            base = 1 + (rounds * 97) % 2900
            await _upsert(database, {str(i): _document(i, f"Round {rounds} Love", 1950 + rounds) for i in range(base, base + 40)})
            await _rows(database, "UPDATE masters SET updated_at = NOW() WHERE data_id = ANY(%s)", ([str(i) for i in range(base, base + 300)],))
            await _rows(database, "DELETE FROM masters WHERE data_id = ANY(%s)", ([str(base + 41), str(base + 42)],))
            await _upsert(database, {f"n{rounds}-{i}": _document(i, "New Love", 2020) for i in range(3)})
            rounds += 1
        return rounds

    async def backfill() -> None:
        try:
            await _rows(database, "CALL public.master_search_backfill(1)")
        finally:
            done.set()

    rounds, _ = await asyncio.gather(load(), backfill())

    assert rounds >= 5
    assert await _drift(database) == (0, 0, 0)
    assert await _exact(database)


async def test_the_drift_check_finds_each_kind_of_difference(database: str) -> None:
    await _upsert(database, _sample(1, 20))
    assert await _drift(database) == (0, 0, 0)
    for statement in MASTER_SEARCH_ROLLBACK_STATEMENTS[:4]:
        await _rows(database, statement)
    await _rows(database, "DELETE FROM masters WHERE data_id = '1'")  # an orphaned side row
    await _upsert(database, {"9000": _document(9000, "Unseen", 1999)})  # a missing one
    await _rows(database, "UPDATE masters SET data = jsonb_set(data, '{year}', '1901') WHERE data_id = '2'")  # a stale year
    await _rows(database, "UPDATE masters SET data = jsonb_set(data, '{title}', '\"Retitled\"') WHERE data_id = '3'")  # a stale vector
    assert await _drift(database) == (1, 1, 2)


async def test_reapplying_waits_on_no_writer(database: str) -> None:
    """A load's open transaction does not hold up a reapply through the initializer."""
    async with await psycopg.AsyncConnection.connect(**_params(database)) as writer:
        await writer.execute(_UPSERT, (["h1"], ["1"], [Jsonb(_document(1, "Love", 1999))], [None]))
        async with await psycopg.AsyncConnection.connect(**_params(database), autocommit=True) as applier, applier.cursor() as cursor:
            await cursor.execute("SET lock_timeout = '200ms'")
            _success, failures = await _execute_schema_statements_bounded(
                cursor, _MASTER_SEARCH_STATEMENTS, lock_timeout_ms=200, attempts=1, retry_pause_s=0
            )
        assert failures == 0
        await writer.rollback()


async def test_the_rollback_removes_everything_and_leaves_masters_writable(database: str) -> None:
    await _upsert(database, {"1": _document(1, "Love", 1999)})
    release_triggers = await _rows(
        database, "SELECT tgname FROM pg_trigger WHERE tgrelid = 'releases'::regclass AND tgname LIKE 'release_search%' ORDER BY tgname"
    )
    assert release_triggers
    for statement in MASTER_SEARCH_ROLLBACK_STATEMENTS:
        await _rows(database, statement)

    assert await _rows(database, "SELECT to_regclass('public.master_search')") == [(None,)]
    assert await _rows(database, "SELECT count(*) FROM pg_trigger WHERE tgrelid = 'masters'::regclass AND tgname LIKE 'master_search%'") == [(0,)]
    assert await _rows(database, "SELECT count(*) FROM pg_proc WHERE proname LIKE 'master_search%'") == [(0,)]
    # release_search is untouched by the masters rollback.
    assert (
        await _rows(database, "SELECT tgname FROM pg_trigger WHERE tgrelid = 'releases'::regclass AND tgname LIKE 'release_search%' ORDER BY tgname")
        == release_triggers
    )
    await _upsert(database, {"1": _document(1, "Love Again", 2000), "2": _document(2, "Love", 2001)})
    await _rows(database, "DELETE FROM masters")


async def test_a_capture_function_attached_to_another_table_refuses_to_run(database: str) -> None:
    """Attached elsewhere by its owner (PUBLIC cannot), the truncate capture must not empty the side table."""
    await _upsert(database, {"1": _document(1, "Love", 1999)})
    for decoy, table in (("decoy", "decoy"), ("releases", "public.releases")):
        if decoy == "decoy":
            await _rows(database, "CREATE TABLE decoy (data_id VARCHAR, data JSONB)")
        await _rows(
            database,
            f"CREATE TRIGGER decoy_truncate AFTER TRUNCATE ON {table} FOR EACH STATEMENT EXECUTE FUNCTION public.master_search_capture_truncated()",
        )
        with pytest.raises(psycopg.errors.RaiseException, match=r"fires only on public\.masters"):
            await _rows(database, f"TRUNCATE {table}")
        await _rows(database, f"DROP TRIGGER decoy_truncate ON {table}")
    assert await _rows(database, "SELECT count(*) FROM master_search") == [(1,)]
    assert await _rows(
        database,
        "SELECT count(*) FROM pg_proc WHERE proname LIKE 'master\\_search\\_%' AND proname <> 'master_search_drift' AND has_function_privilege('public', oid, 'EXECUTE')",
    ) == [(0,)]


async def test_a_function_planted_on_the_callers_search_path_cannot_change_the_backfill(database: str) -> None:
    """The procedure runs under the caller's search_path; its SQL runs in steps that pin theirs."""
    await _without_triggers(database, _sample(1, 100))
    await _rows(database, "CREATE SCHEMA planted")
    await _rows(database, "CREATE FUNCTION planted.to_tsvector(text, text) RETURNS tsvector LANGUAGE sql AS $$ SELECT 'planted'::tsvector $$")
    async with await psycopg.AsyncConnection.connect(**_params(database), autocommit=True) as connection:
        await connection.execute("SET search_path = planted, public")
        cursor = await connection.execute("SELECT to_tsvector('english', 'love')::text")
        assert await cursor.fetchone() == ("'planted'",)
        await connection.execute("CALL public.master_search_backfill(1)")
    assert await _exact(database)
    assert await _rows(database, "SELECT count(*) FROM master_search WHERE title_tsv = 'planted'::tsvector") == [(0,)]

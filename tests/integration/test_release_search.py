"""The stored release title vector (gm-database-schema-fi4a) and the search facet and filter
attributes beside it (gm-database-schema-1sle), proved on a real PostgreSQL.

`public.release_search` must hold `RELEASE_TITLE_TSVECTOR` and catalog-api's facet and filter
inputs of every release and nothing else, whether the release was there before the triggers (the
backfill) or written after them (the triggers), whatever the loaders write concurrently with a
backfill, and on a database that already holds v0.11.0's title-only table.
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

# The loader's set-based upsert (discogs-sql-loader tableinator/batch_writer.py), verbatim.
_UPSERT = (
    "INSERT INTO releases (hash, data_id, data, media, gm_item_id, updated_at) "
    "SELECT incoming.hash, incoming.data_id, incoming.data, incoming.media, incoming.gm_item_id, NOW() "
    "FROM unnest(%s::text[], %s::text[], %s::jsonb[], %s::jsonb[], %s::uuid[]) AS incoming(hash, data_id, data, media, gm_item_id) "
    "ON CONFLICT (data_id) DO UPDATE SET hash = EXCLUDED.hash, data = EXCLUDED.data, media = EXCLUDED.media, "
    "gm_item_id = EXCLUDED.gm_item_id, updated_at = NOW()"
)
# Its media backfill of hash-unchanged rows (the same file's `_backfill`), verbatim.
_MEDIA_BACKFILL = (
    "UPDATE releases SET media = incoming.value, updated_at = NOW() "
    "FROM unnest(%s::jsonb[], %s::text[]) AS incoming(value, data_id) WHERE releases.data_id = incoming.data_id"
)

# What catalog-api's facets and filters read today (api/queries/search_queries.py), over a release
# `r`, written out here rather than taken from the schema: release_search must hold exactly these.
_CATALOG_INPUTS = "r.data->'genres', r.data->>'year', r.data->>'country', r.media->'families'"
_STORED_INPUTS = "s.genres, s.year, s.country, s.media_families"

# Genres, years, countries and media blocks of every shape a document or block can carry.
_GENRES: list[Any] = [["Rock", "Jazz"], ["Electronic"], [], None, "missing", "Rock", ["Rock", None, 7]]
_YEARS: list[Any] = [1999, "1999", 1975, "missing", None, 2024, 1969.0]
_COUNTRIES: list[Any] = ["US", "UK", "Japan", "missing", None, "", 5, "Germany"]
_MEDIA: list[Any] = [
    None,
    {},
    {"families": ["vinyl"], "items": [{"medium": "vinyl_12", "family": "vinyl"}]},
    {"families": ["optical", "vinyl"], "items": [{"medium": "cd"}, {"medium": "vinyl_7"}]},
    {"families": [], "items": []},
    {"families": "vinyl", "items": {"medium": "cd"}},
    {"families": ["digital"], "items": [{"medium": ["cd", "file"]}, {"medium": None}, {"medium": {"cd": 1}}, {"other": "cd"}]},
    {"families": ["tape"], "items": [{"medium": [["cd"]]}, {"medium": "cassette"}]},
    {"items": [{"medium": "cd"}]},
]


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
    for key, values in (("genres", _GENRES), ("year", _YEARS), ("country", _COUNTRIES)):
        value = values[data_id % len(values)]
        if value != "missing":
            document[key] = value
    return document


def _media(data_id: int) -> Any:
    media = _MEDIA[data_id % len(_MEDIA)]
    return None if media is None else Jsonb(media)


async def _upsert(dbname: str, documents: dict[str, dict[str, Any]], media: dict[str, Any] | None = None) -> None:
    ids = list(documents)
    blocks = [(media or {}).get(data_id, _media(int(data_id)) if data_id.isdigit() else None) for data_id in ids]
    await _rows(dbname, _UPSERT, ([f"h{data_id}" for data_id in ids], ids, [Jsonb(documents[data_id]) for data_id in ids], blocks, [None] * len(ids)))


async def _drift(dbname: str) -> tuple[int, int, int]:
    [(missing, orphaned, stale)] = await _rows(dbname, "SELECT * FROM public.release_search_drift()")
    return missing, orphaned, stale


async def _exact(dbname: str) -> bool:
    """Return whether the side table is exactly the canonical vector and catalog-api's inputs of every release.

    Computed here, from the expressions catalog-api reads, not from the schema's own. The
    mediums column is checked through the filter it answers, in `test_every_filter_and_facet...`.
    """
    vector = RELEASE_TITLE_TSVECTOR.replace("data->>", "r.data->>")
    [(differences,)] = await _rows(
        dbname,
        f"SELECT count(*) FROM (SELECT r.data_id, {vector}, {_CATALOG_INPUTS}, r.media IS NULL FROM releases AS r "  # noqa: S608 -- module constants
        f"EXCEPT SELECT s.release_id, s.title_tsv, {_STORED_INPUTS}, s.media_mediums IS NULL FROM release_search AS s) AS absent",
    )
    [(extra,)] = await _rows(
        dbname,
        f"SELECT count(*) FROM (SELECT s.release_id, s.title_tsv, {_STORED_INPUTS}, s.media_mediums IS NULL FROM release_search AS s "  # noqa: S608 -- module constants
        f"EXCEPT SELECT r.data_id, {vector}, {_CATALOG_INPUTS}, r.media IS NULL FROM releases AS r) AS extra",
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
    for statement in (statement for statement in RELEASE_SEARCH_ROLLBACK_STATEMENTS if statement.startswith("DROP TRIGGER")):
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


async def test_an_update_that_changes_neither_document_nor_media_writes_nothing(database: str) -> None:
    await _upsert(database, {str(i): _document(i, "Love Song") for i in range(1, 11)})
    before = await _rows(database, "SELECT release_id, xmin::text FROM release_search ORDER BY release_id")

    # The loader's refresh of every unchanged record, its identity backfill, and a media
    # backfill that rewrites the block it already holds.
    await _rows(database, "UPDATE releases SET updated_at = NOW()")
    await _rows(database, "UPDATE releases SET gm_item_id = gen_random_uuid()")
    await _rows(database, "UPDATE releases SET media = media, updated_at = NOW()")

    assert await _rows(database, "SELECT release_id, xmin::text FROM release_search ORDER BY release_id") == before


async def test_a_media_backfill_rewrites_only_the_media_columns(database: str) -> None:
    """discogs-sql-loader fills `media` on hash-unchanged rows with an UPDATE that sets nothing else."""
    await _upsert(database, {str(i): _document(i, "Love Song") for i in range(1, 11)}, media=dict.fromkeys(map(str, range(1, 11))))
    assert await _rows(database, "SELECT count(*) FROM release_search WHERE media_mediums IS NULL") == [(10,)]
    vectors = await _rows(database, "SELECT release_id, title_tsv::text, genres, year, country FROM release_search ORDER BY release_id")

    block = {"families": ["optical"], "items": [{"medium": "cd"}]}
    await _rows(database, _MEDIA_BACKFILL, ([Jsonb(block)] * 5, [str(i) for i in range(1, 6)]))

    assert await _rows(
        database, "SELECT release_id, media_families, media_mediums FROM release_search WHERE release_id IN ('1', '2') ORDER BY 1"
    ) == [
        ("1", ["optical"], ["cd"]),
        ("2", ["optical"], ["cd"]),
    ]
    assert await _rows(database, "SELECT count(*) FROM release_search WHERE media_mediums IS NULL") == [(5,)]
    assert await _rows(database, "SELECT release_id, title_tsv::text, genres, year, country FROM release_search ORDER BY release_id") == vectors
    assert await _exact(database)
    # Back to no block at all: the release passes every media filter again.
    await _rows(database, "UPDATE releases SET media = NULL WHERE data_id = '1'")
    assert await _rows(database, "SELECT media_families, media_mediums FROM release_search WHERE release_id = '1'") == [(None, None)]
    assert await _drift(database) == (0, 0, 0)


async def test_every_filter_and_facet_answers_as_it_does_over_releases(database: str) -> None:
    """catalog-api's clauses over the side table, row by row against the same clauses over the document.

    Three-valued, so a NULL that a WHERE drops must stay a NULL (or a false). The year clause
    casts with `::int`, which fails on a non-integer year over either table; such rows are left out.
    """
    documents = {str(i): _document(i, "Love") for i in range(1, 400)}
    await _upsert(database, documents)
    clauses = [
        # (over releases r, over release_search s, rows it applies to)
        (
            "(r.data->'genres' IS NULL OR r.data->'genres' ?| ARRAY['Rock', 'Jazz'])",
            "(s.genres IS NULL OR s.genres ?| ARRAY['Rock', 'Jazz'])",
            "TRUE",
        ),
        (
            "(r.data->>'country' IS NULL OR r.data->>'country' = ANY(ARRAY['US', 'Japan', '']))",
            "(s.country IS NULL OR s.country = ANY(ARRAY['US', 'Japan', '']))",
            "TRUE",
        ),
        (
            "((r.data->>'year') IS NULL OR (r.data->>'year')::int >= 1970) AND ((r.data->>'year') IS NULL OR (r.data->>'year')::int <= 1999)",
            "(s.year IS NULL OR s.year::int >= 1970) AND (s.year IS NULL OR s.year::int <= 1999)",
            "(r.data->>'year') ~ '^[0-9]+$' OR r.data->>'year' IS NULL",
        ),
        *(
            (
                f"(r.media IS NULL OR ((r.media->'families') ?| ARRAY[{families}]{''.join(f" OR jsonb_path_exists(r.media, '$.items[*] ? (@.medium == $m)', jsonb_build_object('m', '{m}'::text))" for m in mediums)}))",
                f"(s.media_mediums IS NULL OR (s.media_families ?| ARRAY[{families}]{''.join(f" OR s.media_mediums ? '{m}'" for m in mediums)}))",
                "TRUE",
            )
            for families, mediums in (("'vinyl'", ()), ("'tape'", ("cd",)), ("NULL::text", ("cd", "file")), ("NULL::text", ("vinyl_7",)))
        ),
    ]
    for over_releases, over_side, applies in clauses:
        mismatched = await _rows(
            database,
            f"SELECT r.data_id, {over_releases}, {over_side} FROM releases AS r JOIN release_search AS s ON s.release_id = r.data_id "  # noqa: S608 -- test constants
            f"WHERE ({applies}) AND ({over_releases}) IS DISTINCT FROM ({over_side})",
        )
        assert mismatched == [], over_side
    # The facets, over the rows whose value they can read (a scalar fails in both, as it does today).
    for over_releases, over_side in (("r.data->'genres'", "s.genres"), ("r.media->'families'", "s.media_families")):
        readable = (
            f"SELECT {over_releases} AS stored_in_releases, {over_side} AS stored_in_side FROM releases AS r "  # noqa: S608 -- test constants
            f"JOIN release_search AS s ON s.release_id = r.data_id WHERE jsonb_typeof({over_releases}) = 'array' OFFSET 0"
        )
        facet = "SELECT value, count(*) FROM ({}) AS t, jsonb_array_elements_text(t.{}) AS value GROUP BY value ORDER BY value NULLS FIRST"
        assert await _rows(database, facet.format(readable, "stored_in_releases")) == await _rows(database, facet.format(readable, "stored_in_side"))
    decade = "SELECT ({0}::int / 10 * 10)::text || 's', count(*) FROM releases AS r JOIN release_search AS s ON s.release_id = r.data_id WHERE {0} IS NOT NULL AND {0} ~ '^[0-9]{{4}}$' GROUP BY 1 ORDER BY 1"
    assert await _rows(database, decade.format("(r.data->>'year')")) == await _rows(database, decade.format("s.year"))


async def test_a_title_only_table_from_v0_11_0_gains_the_attributes_and_the_backfill_fills_them(database: str) -> None:
    """lux holds release_search (release_id, title_tsv), filled, under v0.11.0's four triggers."""
    await _upsert(database, {str(i): _document(i, _TITLES[i % len(_TITLES)]) for i in range(1, 501)})
    for statement in RELEASE_SEARCH_ROLLBACK_STATEMENTS:
        await _rows(database, statement)
    vector = RELEASE_TITLE_TSVECTOR.replace("data->>", "r.data->>")
    await _rows(database, "CREATE TABLE public.release_search (release_id VARCHAR PRIMARY KEY, title_tsv TSVECTOR NOT NULL)")
    await _rows(database, f"INSERT INTO public.release_search SELECT r.data_id, {vector} FROM releases AS r")  # noqa: S608 -- module constant
    await _rows(
        database,
        "CREATE FUNCTION public.release_search_capture_updated() RETURNS trigger LANGUAGE plpgsql AS $f$ BEGIN RETURN NULL; END $f$",
    )
    for operation in ("insert", "update", "delete", "truncate"):
        await _rows(
            database,
            f"CREATE TRIGGER release_search_capture_{operation} AFTER {operation.upper()} ON releases FOR EACH STATEMENT "
            "EXECUTE FUNCTION public.release_search_capture_updated()",
        )

    async with await psycopg.AsyncConnection.connect(**_params(database), autocommit=True) as connection, connection.cursor() as cursor:
        _success, failures = await _execute_schema_statements_bounded(
            cursor, _RELEASE_SEARCH_STATEMENTS, lock_timeout_ms=2000, attempts=1, retry_pause_s=0
        )
    assert failures == 0
    assert await _rows(database, "SELECT count(*) FROM release_search WHERE genres IS NULL AND year IS NULL AND media_mediums IS NULL") == [(500,)]
    assert await _rows(database, "SELECT count(*) FROM pg_trigger WHERE tgrelid = 'releases'::regclass AND tgname LIKE 'release_search%'") == [(5,)]
    assert (await _drift(database))[2] > 0

    await _rows(database, "CALL public.release_search_backfill(2)")
    assert await _exact(database)
    assert await _drift(database) == (0, 0, 0)

    # A reapply sends nothing that changes the table or its triggers.
    before = await _rows(
        database, "SELECT oid::text, tgname FROM pg_trigger WHERE tgrelid = 'releases'::regclass AND tgname LIKE 'release_search%' ORDER BY 2"
    )
    async with await psycopg.AsyncConnection.connect(**_params(database), autocommit=True) as connection, connection.cursor() as cursor:
        _success, failures = await _execute_schema_statements_bounded(
            cursor, _RELEASE_SEARCH_STATEMENTS, lock_timeout_ms=2000, attempts=1, retry_pause_s=0
        )
    assert failures == 0
    assert (
        await _rows(
            database, "SELECT oid::text, tgname FROM pg_trigger WHERE tgrelid = 'releases'::regclass AND tgname LIKE 'release_search%' ORDER BY 2"
        )
        == before
    )


async def test_a_role_that_writes_releases_needs_no_grant_on_the_side_table(database: str) -> None:
    role = f"release_writer_{uuid4().hex[:8]}"
    await _rows(database, sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
    try:
        await _rows(database, sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON releases TO {}").format(sql.Identifier(role)))
        async with await psycopg.AsyncConnection.connect(**_params(database), autocommit=True) as connection:
            await connection.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(role)))
            await connection.execute(_UPSERT, (["h1"], ["1"], [Jsonb(_document(1, "Love"))], [None], [None]))
            await connection.execute("UPDATE releases SET data = data || '{\"title\": \"Lovely\"}' WHERE data_id = '1'")
            await connection.execute(_MEDIA_BACKFILL, ([Jsonb({"families": ["vinyl"]})], ["1"]))
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
            await _upsert(database, {str(i): _document(i + rounds, f"Round {rounds} Love") for i in range(base, base + 40)})
            await _rows(database, "UPDATE releases SET updated_at = NOW() WHERE data_id = ANY(%s)", ([str(i) for i in range(base, base + 300)],))
            await _rows(database, _MEDIA_BACKFILL, ([_media(rounds + i) for i in range(60)], [str(base + 45 + i) for i in range(60)]))
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
        await writer.execute(_UPSERT, (["h1"], ["1"], [Jsonb(_document(1, "Love"))], [None], [None]))
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

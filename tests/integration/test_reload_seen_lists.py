"""Real-engine proof for the reload seen lists (gm-database-schema-uw6d).

A reload proves a row is still upstream by appending its key to an unlogged `loader_seen` list
instead of rewriting the row, and the stale-row purge deletes a row only when it is older than
the extraction AND absent from the list. These tests drive the lists the way both loaders will:
they append in the batch's own transaction, purge through `loader_seen.<list>_stale`, compare the
two purge sets through `loader_seen.<list>_shadow`, and restart a list with `loader_seen.start`.
They prove the purge set matches the touch-based one, that a list emptied by a real crash
purges nothing, that redelivered, rolled-back, concurrent and pre-extraction appends count
correctly, that MusicBrainz's no-op upserts rewrite nothing, and that appending writes almost no
WAL where the touch writes every row. Each test gets its own database.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg import errors, sql

from groovemap_schema import initializer
from groovemap_schema.postgres import (
    _SEEN_LIST_STATEMENTS,
    SEEN_LIST_NOT_INTACT_SQLSTATE,
    SEEN_LISTS,
    _execute_schema_statements,
    _schema_statements,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.timeout(300)]

# An extraction that began a minute ago, against lists started a day ago: what a monthly reload
# looks like, compressed. Rows the dump no longer has were last written a month ago.
_STARTED_AT = "now() - interval '1 minute'"
_SEEN_LIST_NAMES = frozenset(name for name, _statement in _SEEN_LIST_STATEMENTS)

# discogs-sql-loader's two batch writes, reduced to what the purge reads.
_TOUCH = "UPDATE releases SET updated_at = NOW() WHERE data_id = ANY(%s)"
_UPSERT_CHANGED = (
    "INSERT INTO releases (hash, data_id, data, updated_at) SELECT 'changed', i, '{}'::jsonb, NOW() FROM unnest(%s::text[]) AS i "
    "ON CONFLICT (data_id) DO UPDATE SET hash = EXCLUDED.hash, data = EXCLUDED.data, updated_at = NOW()"
)
# The append both loaders add to the batch transaction.
_APPEND = "INSERT INTO loader_seen.discogs_releases (seen_key) SELECT unnest(%s::text[])"

A = UUID("00000000-0000-0000-0000-00000000000a")
B = UUID("00000000-0000-0000-0000-00000000000b")
_REL_KEY = (
    "musicbrainz.relationship_key(source_mbid, target_mbid, source_entity_type, target_entity_type, "
    "relationship_type, begin_date, end_date, attributes)"
)
# musicbrainz-sql-loader's relationship upsert with the no-op check: a conflict that changes
# nothing updates nothing, so the row is not rewritten and `updated_at` keeps its age.
_REL_UPSERT = (
    "INSERT INTO musicbrainz.relationships "  # noqa: S608
    "(source_mbid, source_entity_type, target_mbid, target_entity_type, relationship_type, attributes, begin_date, end_date, ended) "
    "VALUES (%s, 'artist', %s, 'artist', %s, NULL, NULL, NULL, %s) "
    f"ON CONFLICT ({_REL_KEY}) DO UPDATE SET ended = EXCLUDED.ended, updated_at = NOW() "
    "WHERE musicbrainz.relationships.ended IS DISTINCT FROM EXCLUDED.ended"
)
_REL_APPEND = (
    "INSERT INTO loader_seen.musicbrainz_relationships (seen_key) "
    "VALUES (loader_seen.hash_key(musicbrainz.relationship_key(%s, %s, 'artist', 'artist', %s, NULL, NULL, NULL)))"
)
# The external-link conflict target is every column, so a no-op conflict is DO NOTHING.
_LINK_UPSERT = (
    "INSERT INTO musicbrainz.external_links (mbid, entity_type, service_name, url) VALUES (%s, 'artist', 'discogs', %s) "
    "ON CONFLICT (mbid, entity_type, service_name, url) DO NOTHING"
)
_LINK_APPEND = (
    "INSERT INTO loader_seen.musicbrainz_external_links (seen_key) "
    "VALUES (loader_seen.hash_key(musicbrainz.external_link_key(%s, 'artist', 'discogs', %s)))"
)


def _params(dbname: str) -> dict[str, Any]:
    return {**initializer._postgres_connection_params(), "dbname": dbname}


async def _apply(conn: psycopg.AsyncConnection[Any], *, without_seen_lists: bool = False) -> None:
    statements = [(name, statement) for name, statement in _schema_statements() if not (without_seen_lists and name in _SEEN_LIST_NAMES)]
    async with conn.cursor() as cursor:
        _success, failures = await _execute_schema_statements(cursor, statements)
    assert failures == 0


@pytest_asyncio.fixture
async def dbname() -> AsyncIterator[str]:
    name = f"seen_{uuid4().hex[:12]}"
    async with await psycopg.AsyncConnection.connect(**_params("postgres"), autocommit=True) as admin:
        await admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        yield name
    finally:
        async with await _connect_retrying("postgres") as admin:
            await admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


@pytest_asyncio.fixture
async def connection(dbname: str) -> AsyncIterator[psycopg.AsyncConnection[Any]]:
    async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as conn:
        await _apply(conn)
        # The lists were started by the schema apply, a moment ago; a real reload starts weeks after.
        await conn.execute("UPDATE loader_seen.lists SET opened_at = now() - interval '1 day'")
        yield conn


async def _connect_retrying(dbname: str, *, seconds: float = 60) -> psycopg.AsyncConnection[Any]:
    deadline = time.monotonic() + seconds
    while True:
        try:
            return await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True)
        except psycopg.OperationalError:
            if time.monotonic() > deadline:
                raise
            await asyncio.sleep(0.5)


async def _value(conn: psycopg.AsyncConnection[Any], query: str, params: Any = None) -> Any:
    row = await (await conn.execute(query, params)).fetchone()
    assert row is not None
    return row[0]


async def _seed_releases(conn: psycopg.AsyncConnection[Any], count: int, *, payload: str = "{}") -> None:
    """Load COUNT releases last written a month ago, as the previous dump left them."""
    await conn.execute(
        "INSERT INTO releases (hash, data_id, data, updated_at) "
        "SELECT md5(g::text), g::text, %s::jsonb, now() - interval '30 days' FROM generate_series(1, %s) AS g",
        (payload, count),
    )


def _ids(first: int, last: int) -> list[str]:
    return [str(i) for i in range(first, last + 1)]


async def _batch(conn: psycopg.AsyncConnection[Any], *, unchanged: list[str], changed: list[str], touch: bool) -> None:
    """One loader batch: changed rows upserted, unchanged ones touched or not, all appended."""
    async with conn.transaction():
        if changed:
            await conn.execute(_UPSERT_CHANGED, (changed,))
        if touch and unchanged:
            await conn.execute(_TOUCH, (unchanged,))
        await conn.execute(_APPEND, (unchanged + changed,))


async def _stale(conn: psycopg.AsyncConnection[Any], seen_list: str = "discogs_releases") -> set[Any]:
    rows = await (await conn.execute(f"SELECT * FROM loader_seen.{seen_list}_stale({_STARTED_AT}) ORDER BY 1")).fetchall()  # noqa: S608
    return {row[0] for row in rows}


async def _shadow(conn: psycopg.AsyncConnection[Any], seen_list: str = "discogs_releases") -> dict[str, Any]:
    cursor = await conn.execute(f"SELECT * FROM loader_seen.{seen_list}_shadow({_STARTED_AT})")  # noqa: S608
    row = await cursor.fetchone()
    assert row is not None and cursor.description is not None
    return {column.name: value for column, value in zip(cursor.description, row, strict=True)}


async def test_a_fresh_schema_has_started_unlogged_index_free_lists(connection: psycopg.AsyncConnection[Any]) -> None:
    for seen in SEEN_LISTS:
        persistence = await _value(connection, "SELECT relpersistence FROM pg_class WHERE oid = %s::regclass", (seen.relation,))
        assert persistence == "u", seen.relation
        indexes = await _value(connection, "SELECT count(*) FROM pg_index WHERE indrelid = %s::regclass", (seen.relation,))
        assert indexes == 0, seen.relation
    assert await _value(connection, "SELECT relpersistence FROM pg_class WHERE oid = 'loader_seen.lists'::regclass") == "u"
    registered = await (await connection.execute("SELECT loader, data_type FROM loader_seen.lists ORDER BY 1, 2")).fetchall()
    assert registered == sorted((seen.loader, seen.data_type) for seen in SEEN_LISTS)


async def test_the_seen_purge_deletes_exactly_what_the_touch_purge_did(connection: psycopg.AsyncConnection[Any]) -> None:
    # 100 rows from the last dump. This dump still has 1-90 (1-80 unchanged, 81-90 changed),
    # adds 101-105, and no longer has 91-100.
    await _seed_releases(connection, 100)
    await _batch(connection, unchanged=_ids(1, 40), changed=_ids(81, 85), touch=False)
    await _batch(connection, unchanged=_ids(41, 80), changed=[*_ids(86, 90), *_ids(101, 105)], touch=False)

    assert await _stale(connection) == set(_ids(91, 100))
    # The rows the extraction did not rewrite kept their age: the reload wrote none of them.
    assert await _value(connection, f"SELECT count(*) FROM releases WHERE updated_at < {_STARTED_AT}") == 90  # noqa: S608


async def test_a_shadow_reload_compares_equal_when_every_refresh_is_appended(connection: psycopg.AsyncConnection[Any]) -> None:
    await _seed_releases(connection, 100)
    await _batch(connection, unchanged=_ids(1, 80), changed=_ids(81, 90), touch=True)

    shadow = await _shadow(connection)
    assert shadow == {
        "intact": True,
        "total_rows": 100,
        "touch_purge": 10,
        "seen_purge": 10,
        "refreshed_not_seen": 0,
        "seen_not_refreshed": 0,
        "refreshed_not_seen_sample": [],
        "seen_not_refreshed_sample": [],
    }


async def test_a_shadow_reload_names_a_path_that_refreshes_without_appending(connection: psycopg.AsyncConnection[Any]) -> None:
    await _seed_releases(connection, 100)
    async with connection.transaction():
        await connection.execute(_TOUCH, (_ids(1, 80),))
        await connection.execute(_APPEND, (_ids(1, 75),))  # 76-80 refreshed by a path that forgot the list
        await connection.execute(_APPEND, (["95"],))  # seen, but never refreshed

    shadow = await _shadow(connection)
    assert shadow["touch_purge"] == 20  # 81-100
    assert shadow["seen_purge"] == 24  # 76-94, 96-100
    assert shadow["refreshed_not_seen"] == 5
    assert shadow["refreshed_not_seen_sample"] == _ids(76, 80)
    assert shadow["seen_not_refreshed"] == 1
    assert shadow["seen_not_refreshed_sample"] == ["95"]


async def test_a_list_lost_to_a_real_crash_purges_nothing(dbname: str, connection: psycopg.AsyncConnection[Any]) -> None:
    if not await _value(connection, "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"):
        pytest.skip("crashing the server needs a superuser")
    await _seed_releases(connection, 100)
    await _batch(connection, unchanged=_ids(1, 90), changed=[], touch=False)
    assert await _stale(connection) == set(_ids(91, 100))

    # SIGKILL one backend: the postmaster treats it as a crash, restarts every backend and runs
    # crash recovery, which resets every unlogged relation to empty. Logged rows survive.
    victim = await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True)
    pid = await _value(victim, "SELECT pg_backend_pid()")
    with contextlib.suppress(psycopg.OperationalError):
        await connection.execute(sql.SQL("COPY (SELECT 1) TO PROGRAM {}").format(sql.Literal(f"kill -9 {pid}")))
    with contextlib.suppress(psycopg.Error):
        await victim.close()
    with contextlib.suppress(psycopg.Error):
        await connection.close()

    async with await _connect_retrying(dbname) as recovered:
        assert await _value(recovered, "SELECT count(*) FROM releases") == 100
        assert await _value(recovered, "SELECT count(*) FROM loader_seen.discogs_releases") == 0
        assert await _value(recovered, "SELECT count(*) FROM loader_seen.lists") == 0
        status = await (await recovered.execute(f"SELECT * FROM loader_seen.status('discogs', 'releases', {_STARTED_AT})")).fetchone()  # noqa: S608
        assert status == (None, False, 0)
        with pytest.raises(psycopg.Error) as refused:
            await _stale(recovered)
        assert refused.value.sqlstate == SEEN_LIST_NOT_INTACT_SQLSTATE
        assert await _value(recovered, "SELECT count(*) FROM releases") == 100

        # The rest of the reload arrives after recovery; the list is still not intact for it.
        await _batch(recovered, unchanged=_ids(1, 90), changed=[], touch=False)
        assert await _value(recovered, f"SELECT loader_seen.intact('discogs', 'releases', {_STARTED_AT})") is False
        # Restarting the list re-arms it for the next extraction, not for this one.
        await recovered.execute("SELECT loader_seen.start('discogs', 'releases')")
        assert await _value(recovered, f"SELECT loader_seen.intact('discogs', 'releases', {_STARTED_AT})") is False
        assert await _value(recovered, "SELECT loader_seen.intact('discogs', 'releases', now() + interval '16 minutes')") is True


async def test_status_counts_each_key_appended_since_the_extraction_once(dbname: str, connection: psycopg.AsyncConnection[Any]) -> None:
    await _seed_releases(connection, 100)
    # A straggler of the previous extraction, appended before this one started.
    await connection.execute("INSERT INTO loader_seen.discogs_releases (seen_at, seen_key) VALUES (now() - interval '2 hours', '99')")
    await _batch(connection, unchanged=_ids(1, 30), changed=[], touch=False)
    await _batch(connection, unchanged=_ids(21, 30), changed=[], touch=False)  # redelivered: counted once
    with contextlib.suppress(RuntimeError):
        async with connection.transaction():  # a batch that rolled back: its keys never committed
            await connection.execute(_APPEND, (_ids(31, 40),))
            raise RuntimeError

    async def concurrent(first: int, last: int) -> None:
        async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as other:
            await _batch(other, unchanged=_ids(first, last), changed=[], touch=False)

    await asyncio.gather(concurrent(41, 45), concurrent(46, 50))

    status = await (await connection.execute(f"SELECT intact, seen_keys FROM loader_seen.status('discogs', 'releases', {_STARTED_AT})")).fetchone()  # noqa: S608
    # 1-30 and 41-50. Against an extractor record_count of 100 that is half: the loader's count
    # guard skips the purge, which would otherwise delete 31-40 and 51-100.
    assert status == (True, 40)
    assert await _stale(connection) == {*_ids(31, 40), *_ids(51, 100)}


async def test_a_list_started_after_the_extraction_began_is_not_intact(connection: psycopg.AsyncConnection[Any]) -> None:
    started = await _value(connection, "SELECT loader_seen.start('discogs', 'releases')")
    assert await _value(connection, "SELECT opened_at FROM loader_seen.lists WHERE loader = 'discogs' AND data_type = 'releases'") == started
    assert await _value(connection, f"SELECT loader_seen.intact('discogs', 'releases', {_STARTED_AT})") is False
    # The clock margin: a list started 10 minutes before the extraction still does not count.
    assert await _value(connection, "SELECT loader_seen.intact('discogs', 'releases', now() + interval '10 minutes')") is False
    assert await _value(connection, "SELECT loader_seen.intact('discogs', 'releases', now() + interval '16 minutes')") is True
    # Other lists are untouched by one list's restart.
    assert await _value(connection, f"SELECT loader_seen.intact('discogs', 'artists', {_STARTED_AT})") is True


async def test_start_empties_one_list_in_the_purge_transaction(connection: psycopg.AsyncConnection[Any]) -> None:
    await connection.execute("INSERT INTO loader_seen.discogs_releases (seen_key) VALUES ('1'), ('2')")
    await connection.execute("INSERT INTO loader_seen.discogs_artists (seen_key) VALUES ('1')")
    with contextlib.suppress(RuntimeError):
        async with connection.transaction():
            await connection.execute("SELECT loader_seen.start('discogs', 'releases')")
            raise RuntimeError  # the purge failed: the list and its start time stay as they were
    assert await _value(connection, "SELECT count(*) FROM loader_seen.discogs_releases") == 2
    assert await _value(connection, f"SELECT loader_seen.intact('discogs', 'releases', {_STARTED_AT})") is True
    await connection.execute("SELECT loader_seen.start('discogs', 'releases')")
    assert await _value(connection, "SELECT count(*) FROM loader_seen.discogs_releases") == 0
    assert await _value(connection, "SELECT count(*) FROM loader_seen.discogs_artists") == 1


async def test_start_and_status_refuse_an_unknown_list(connection: psycopg.AsyncConnection[Any]) -> None:
    with pytest.raises(errors.InvalidParameterValue):
        await connection.execute("SELECT loader_seen.start('discogs', 'tracks')")
    with pytest.raises(errors.InvalidParameterValue):
        await connection.execute("SELECT * FROM loader_seen.status('musicbrainz', 'releases', now())")


async def test_reapplying_the_schema_keeps_a_list_and_its_start_time(connection: psycopg.AsyncConnection[Any]) -> None:
    await connection.execute(_APPEND, (_ids(1, 10),))
    before = await _value(connection, "SELECT opened_at FROM loader_seen.lists WHERE loader = 'discogs' AND data_type = 'releases'")
    await _apply(connection)
    assert await _value(connection, "SELECT count(*) FROM loader_seen.discogs_releases") == 10
    assert await _value(connection, "SELECT opened_at FROM loader_seen.lists WHERE loader = 'discogs' AND data_type = 'releases'") == before


async def test_upgrading_a_previous_release_database_adds_started_lists(dbname: str) -> None:
    async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as conn:
        await _apply(conn, without_seen_lists=True)
        assert await _value(conn, "SELECT to_regnamespace('loader_seen') IS NULL") is True
        await _seed_releases(conn, 10)
        await _apply(conn)
        assert await _value(conn, "SELECT count(*) FROM loader_seen.lists") == len(SEEN_LISTS)
        assert await _value(conn, "SELECT count(*) FROM releases") == 10
        # Started at the upgrade, so the first extraction that began before it purges nothing.
        assert await _value(conn, f"SELECT loader_seen.intact('discogs', 'releases', {_STARTED_AT})") is False


async def test_musicbrainz_no_op_upserts_rewrite_nothing_and_the_lists_keep_the_rows(connection: psycopg.AsyncConnection[Any]) -> None:
    rels = [(A, B, "MEMBER_OF", False), (A, B, "PERFORMER", False), (B, A, "PRODUCER", False)]
    links = [(A, "https://www.discogs.com/artist/1"), (A, "https://www.discogs.com/artist/2"), (B, "https://www.discogs.com/artist/3")]
    for rel in rels:
        await connection.execute(_REL_UPSERT, rel)
    for link in links:
        await connection.execute(_LINK_UPSERT, link)
    await connection.execute("UPDATE musicbrainz.relationships SET updated_at = now() - interval '30 days'")
    await connection.execute("UPDATE musicbrainz.external_links SET updated_at = now() - interval '30 days'")
    rel_versions = await (await connection.execute("SELECT id, xmin::text FROM musicbrainz.relationships ORDER BY id")).fetchall()
    link_versions = await (await connection.execute("SELECT id, xmin::text FROM musicbrainz.external_links ORDER BY id")).fetchall()

    # The reload: MEMBER_OF unchanged and seen from both endpoints, PERFORMER now ended, PRODUCER
    # gone; links 1 and 3 still there, 2 gone.
    async with connection.transaction():
        for source, target, rel, ended in [(A, B, "MEMBER_OF", False), (A, B, "MEMBER_OF", False), (A, B, "PERFORMER", True)]:
            await connection.execute(_REL_UPSERT, (source, target, rel, ended))
            await connection.execute(_REL_APPEND, (source, target, rel))
        for mbid, url in (links[0], links[2]):
            await connection.execute(_LINK_UPSERT, (mbid, url))
            await connection.execute(_LINK_APPEND, (mbid, url))

    rel_after = dict(await (await connection.execute("SELECT id, xmin::text FROM musicbrainz.relationships ORDER BY id")).fetchall())
    member_of, performer, producer = (row[0] for row in rel_versions)
    assert rel_after[member_of] == dict(rel_versions)[member_of]  # not rewritten, though upserted twice
    assert rel_after[performer] != dict(rel_versions)[performer]  # changed, so written
    link_after = dict(await (await connection.execute("SELECT id, xmin::text FROM musicbrainz.external_links ORDER BY id")).fetchall())
    assert link_after == dict(link_versions)

    assert await _stale(connection, "musicbrainz_relationships") == {producer}
    assert await _stale(connection, "musicbrainz_external_links") == {link_versions[1][0]}
    status = await (await connection.execute(f"SELECT seen_keys FROM loader_seen.status('musicbrainz', 'relationships', {_STARTED_AT})")).fetchone()  # noqa: S608
    assert status == (2,)  # MEMBER_OF seen twice counts once
    shadow = await _shadow(connection, "musicbrainz_relationships")
    # Without the touch the shadow sees what the switch will do: MEMBER_OF kept (seen), PERFORMER
    # kept (changed and seen), PRODUCER deleted.
    assert (shadow["seen_purge"], shadow["refreshed_not_seen"]) == (1, 0)


async def test_the_external_link_key_separates_its_fields(connection: psycopg.AsyncConnection[Any]) -> None:
    same = await _value(
        connection,
        "SELECT musicbrainz.external_link_key(%s, 'artist', 'ab', 'c') = musicbrainz.external_link_key(%s, 'artist', 'a', 'bc')",
        (A, A),
    )
    assert same is False
    assert await _value(connection, "SELECT length(musicbrainz.external_link_key(%s, 'artist', 'discogs', 'u'))", (A,)) == 32


async def _wal_bytes(conn: psycopg.AsyncConnection[Any], statement: str, params: Any) -> int:
    await conn.execute("CHECKPOINT")
    before = await _value(conn, "SELECT pg_current_wal_insert_lsn()::text")
    await conn.execute(statement, params)
    return int(await _value(conn, "SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(), %s::pg_lsn)", (before,)))


async def test_appending_a_reload_writes_almost_no_wal_where_the_touch_rewrites_every_row(connection: psycopg.AsyncConnection[Any]) -> None:
    if not await _value(connection, "SELECT rolsuper FROM pg_roles WHERE rolname = current_user"):
        pytest.skip("CHECKPOINT needs a superuser or pg_checkpoint")
    await _seed_releases(connection, 2000, payload=json.dumps({"title": "x" * 600}))
    await connection.execute("VACUUM ANALYZE releases")
    ids = _ids(1, 2000)

    touch = await _wal_bytes(connection, _TOUCH, (ids,))
    append = await _wal_bytes(connection, _APPEND, (ids,))
    assert touch > 2000 * 100, touch  # every row rewritten, each new page logged whole
    assert append < touch / 50, (append, touch)

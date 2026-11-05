"""Real-engine proof of the precomputed Identity Ledger (epics gm-database-schema-k8e9 and xen5).

Every test runs in its own fresh database, so the tables start empty and the
fixture below is the only history they hold: the suite's shared database
collects other tests' items and supersessions.

Incremental maintenance is held to two independent recomputes: catalog-api's
prototype statements, verbatim, and `identity_ledger_verify()`.  The randomized
history at the end of this file is what the maintenance's reach is proved by,
because a hand-written case only covers the dependencies its author thought of.
"""

from __future__ import annotations

import contextlib
import random
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from groovemap_schema import initializer
from groovemap_schema.postgres import (
    IDENTITY_LEDGER_REFRESH_GRANTS,
    IDENTITY_LEDGER_ROLLBACK_STATEMENTS,
    IDENTITY_LEDGER_VIEWS,
    _ledger_density_select,
    _ledger_edition_events_select,
    _ledger_edition_parents_select,
    _ledger_work_summary_select,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


pytestmark = pytest.mark.integration

_REFERENCE_SQL = (Path(__file__).parent / "fixtures" / "identity_ledger_reference.sql").read_text()
_REFERENCE_SCHEMA = "ledger_reference"

# Each view's columns, in catalog-api's order; parity compares whole rows.
_COLUMNS = {
    "identity_ledger_edition_events": "edition_id, at, type, events",
    "identity_ledger_edition_parents": "edition_id, work_id",
    "identity_ledger_work_summary": "work_id, event, evented_editions, repaired_editions, first_event, last_event",
    "identity_ledger_density": "kind, month, type, dated_from, events",
}


def _at(month: int, day: int, year: int = 2026) -> datetime:
    return datetime(year, month, day, 12, tzinfo=UTC)


# The fixture's instants. Every item is minted at MINT, so no mint is an edition event.
MINT = _at(1, 5)
LINKED = _at(2, 10)  # E1's first MusicBrainz release: linked at ingest
SECOND_ROW = _at(3, 15)  # E1's second MusicBrainz release
CLOSED = _at(10, 25)  # the second row's alias closed, not by a supersession
BACKFILL = _at(11, 20, 2025)  # the full-export backfill's link_added
LOAD = _at(10, 3)  # a load's link_changed
REATTACHED = _at(10, 12)  # A re-attached into B: A's alias closed and reopened on B
PROMOTED = _at(9, 1)  # C promoted into D ...
REVERTED = _at(10, 20)  # ... and reverted
LINKED_E5 = _at(8, 8)  # E5's MusicBrainz release, whose release group's work is superseded
WORK_MERGED = _at(7, 1)  # W4 superseded into W2


class Fixture:
    """The fixture's native ids, by role, so expectations can name them."""

    def __init__(self) -> None:
        self.e1, self.a, self.b, self.c, self.d, self.e5 = (uuid4() for _ in range(6))
        self.w1, self.w2, self.w3, self.w4 = (uuid4() for _ in range(4))
        self.rg2, self.rg4 = str(uuid4()), str(uuid4())
        self.mb1, self.mb2, self.mb7, self.mb9 = (str(uuid4()) for _ in range(4))


async def _seed(cursor: Any, fixture: Fixture) -> None:
    """Write the epic's cases as catalog-api and the loaders would leave them.

    - E1, a two-parent edition: Discogs release r1 (master m100, work W1) and a
      MusicBrainz release linked at ingest (release group RG2, work W2), then a
      second MusicBrainz row whose alias is later closed; the backfill's
      link_added and a load's link_changed name the first row.
    - A re-attachment: A's Discogs alias closed and reopened on B at the
      supersession instant, so the move itself is no alias event.
    - A promotion of C into D, reverted.
    - E5, whose MusicBrainz release group's work W4 is superseded into W2.
    - A link change no current alias holds, and one on a release group.
    """
    f = fixture
    items = [(item, "release") for item in (f.e1, f.a, f.b, f.c, f.d, f.e5)]
    items += [(item, "master") for item in (f.w1, f.w2, f.w3, f.w4)]
    await cursor.executemany("INSERT INTO catalog_items (id, kind) VALUES (%s, %s)", items)

    aliases = [
        ("discogs", "release", "r1", f.e1, MINT, None),
        ("musicbrainz", "release", f.mb1, f.e1, LINKED, None),
        ("musicbrainz", "release", f.mb2, f.e1, SECOND_ROW, CLOSED),
        ("discogs", "release", "r3a", f.a, MINT, REATTACHED),
        ("discogs", "release", "r3b", f.b, MINT, None),
        ("discogs", "release", "r3a", f.b, REATTACHED, None),
        ("discogs", "release", "r6", f.c, MINT, None),
        ("discogs", "release", "r5", f.d, MINT, None),
        ("discogs", "release", "r7", f.e5, MINT, None),
        ("musicbrainz", "release", f.mb7, f.e5, LINKED_E5, None),
        ("discogs", "master", "m100", f.w1, MINT, None),
        ("musicbrainz", "master", f.rg2, f.w2, MINT, None),
        ("discogs", "master", "m300", f.w3, MINT, None),
        ("musicbrainz", "master", f.rg4, f.w4, MINT, None),
    ]
    await cursor.executemany(
        "INSERT INTO provider_aliases (provider, entity_kind, external_id, native_id, valid_from, valid_to) VALUES (%s, %s, %s, %s, %s, %s)",
        aliases,
    )
    await cursor.executemany(
        "INSERT INTO catalog_item_supersessions (superseded_id, survivor_id, cause, decision_ref, valid_from, valid_to) "
        "VALUES (%s, %s, %s, %s, %s, %s)",
        [
            (f.a, f.b, "catalog_reattachment", uuid4(), REATTACHED, None),
            (f.c, f.d, "edition_promotion", uuid4(), PROMOTED, REVERTED),
            (f.w4, f.w2, "edition_promotion", uuid4(), WORK_MERGED, None),
        ],
    )
    await cursor.executemany(
        "INSERT INTO identity_link_events "
        "(provider, entity_kind, external_id, event, discogs_id_before, discogs_id_after, event_at, dated_from, source_ref) "
        "VALUES ('musicbrainz', %s, %s, %s, %s, %s, %s, %s, %s)",
        [
            ("release", f.mb1, "link_added", None, 1, BACKFILL, "musicbrainz_fullexport", "mbdump-20251120"),
            ("release", f.mb1, "link_changed", 1, 2, LOAD, "groovemap_load", "load-20261003"),
            ("release", f.mb9, "link_added", None, 9, BACKFILL, "musicbrainz_fullexport", "mbdump-20251120"),
            ("master", f.rg4, "link_added", None, 4, BACKFILL, "musicbrainz_fullexport", "mbdump-20251120"),
        ],
    )
    await cursor.executemany(
        "INSERT INTO graph.derived_from (release_id, master_id) VALUES (%s, %s)",
        [("r1", "m100"), ("r3a", "m300"), ("r3b", "m300"), ("r5", "m300")],
    )
    await cursor.executemany(
        "INSERT INTO musicbrainz.releases (mbid, name, release_group_mbid) VALUES (%s, %s, %s)",
        [(f.mb1, "E1", f.rg2), (f.mb2, "E1 again", f.rg2), (f.mb7, "E5", f.rg4)],
    )


def _admin_params() -> dict[str, Any]:
    return {**initializer._postgres_connection_params(), "dbname": "postgres"}


@contextlib.asynccontextmanager
async def _ledger_database() -> AsyncIterator[dict[str, Any]]:
    """Yield the connection parameters of a fresh database the schema was applied to twice."""
    database = f"ledger_test_{uuid4().hex[:12]}"
    admin = await psycopg.AsyncConnection.connect(**_admin_params(), autocommit=True)
    try:
        await admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        params = {**initializer._postgres_connection_params(), "dbname": database}
        # Acceptance 1: zero failures on a fresh PostgreSQL, and again on a second run.
        assert await initializer._apply_postgres_schema(params) is True
        assert await initializer._apply_postgres_schema(params) is True
        yield params
    finally:
        await admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database)))
        await admin.close()


async def _connect(params: dict[str, Any], **overrides: Any) -> psycopg.AsyncConnection[Any]:
    return await psycopg.AsyncConnection.connect(**{**params, **overrides}, autocommit=True)


async def _rows(connection: psycopg.AsyncConnection[Any], query: Any, parameters: Any = None) -> list[tuple[Any, ...]]:
    async with connection.cursor() as cursor:
        await cursor.execute(query, parameters)
        return await cursor.fetchall()


async def _view_rows(connection: psycopg.AsyncConnection[Any], view: str, schema: str = "public") -> set[tuple[Any, ...]]:
    query = sql.SQL("SELECT {columns} FROM {view}").format(columns=sql.SQL(_COLUMNS[view]), view=sql.Identifier(schema, view))
    return set(await _rows(connection, query))


async def _view_locks(connection: psycopg.AsyncConnection[Any], pid: int) -> set[tuple[str, str]]:
    """The (view, lock mode) pairs PID holds on the four views."""
    rows = await _rows(
        connection,
        """
        SELECT relation::regclass::text, mode
        FROM pg_locks
        WHERE pid = %s AND granted AND relation = ANY(%s::regclass[]::oid[])
        """,
        (pid, [f"public.{view}" for view in IDENTITY_LEDGER_VIEWS]),
    )
    return {(name.removeprefix("public."), mode) for name, mode in rows}


async def _refresh_state(connection: psycopg.AsyncConnection[Any]) -> list[tuple[Any, ...]]:
    return await _rows(connection, "SELECT refreshed_at, duration, source_watermark FROM identity_ledger_refresh_state")


def _expected_edition_events(f: Fixture) -> set[tuple[Any, ...]]:
    return {
        (f.e1, LINKED, "linked_at_ingest", 1),
        (f.e1, SECOND_ROW, "evidence_attached", 1),
        (f.e1, CLOSED, "alias_closed", 1),
        (f.e1, BACKFILL, "link_added", 1),
        (f.e1, LOAD, "link_changed", 1),
        # Both sides of the re-attachment, resolved to the survivor; the alias
        # it closed on A and reopened on B is no event.
        (f.b, REATTACHED, "reattached", 2),
        # A reverted promotion resolves nothing: each side keeps its own events.
        (f.c, PROMOTED, "promoted", 1),
        (f.d, PROMOTED, "promoted", 1),
        (f.c, REVERTED, "reverted", 1),
        (f.d, REVERTED, "reverted", 1),
        (f.e5, LINKED_E5, "linked_at_ingest", 1),
    }


def _expected_parents(f: Fixture) -> set[tuple[UUID, UUID]]:
    # C names a release no master derives; W4 resolves to W2.
    return {(f.e1, f.w1), (f.e1, f.w2), (f.b, f.w3), (f.d, f.w3), (f.e5, f.w2)}


def _expected_work_summary(f: Fixture) -> set[tuple[Any, ...]]:
    e1_types = [
        ("linked_at_ingest", LINKED, LINKED),
        ("evidence_attached", SECOND_ROW, SECOND_ROW),
        ("alias_closed", CLOSED, CLOSED),
        ("link_added", BACKFILL, BACKFILL),
        ("link_changed", LOAD, LOAD),
    ]
    rows = {
        (f.w1, "any", 1, 0, BACKFILL, CLOSED),
        (f.w2, "any", 2, 0, BACKFILL, CLOSED),
        (f.w3, "any", 2, 2, PROMOTED, REVERTED),
        (f.w3, "reattached", 1, 1, REATTACHED, REATTACHED),
        (f.w3, "promoted", 1, 1, PROMOTED, PROMOTED),
        (f.w3, "reverted", 1, 1, REVERTED, REVERTED),
    }
    rows |= {(f.w1, event, 1, 0, first, last) for event, first, last in e1_types}
    rows |= {(f.w2, event, 1, 0, first, last) for event, first, last in e1_types if event != "linked_at_ingest"}
    rows.add((f.w2, "linked_at_ingest", 2, 0, LINKED, LINKED_E5))
    return rows


async def _build_reference(connection: psycopg.AsyncConnection[Any]) -> None:
    """Build catalog-api's reference views, verbatim, in a scratch schema, populated."""
    await connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(_REFERENCE_SCHEMA)))
    await connection.execute(sql.SQL("SET search_path = {}, public").format(sql.Identifier(_REFERENCE_SCHEMA)))
    await connection.execute(_REFERENCE_SQL.encode())
    await connection.execute("RESET search_path")


async def _refresh_and_compare(connection: psycopg.AsyncConnection[Any], context: Any = None) -> None:
    """Refresh, then require both recomputes to agree with all four tables, row for row."""
    await _rows(connection, "SELECT identity_ledger_refresh()")
    await _assert_equal_to_a_full_recompute(connection, context)


async def _assert_equal_to_a_full_recompute(connection: psycopg.AsyncConnection[Any], context: Any = None) -> None:
    for view in IDENTITY_LEDGER_VIEWS:
        await connection.execute(sql.SQL("REFRESH MATERIALIZED VIEW {}").format(sql.Identifier(_REFERENCE_SCHEMA, view)))
        assert await _view_rows(connection, view) == await _view_rows(connection, view, _REFERENCE_SCHEMA), (view, context)
    assert await _rows(connection, "SELECT relation, missing, unexpected, pending_changes FROM identity_ledger_verify() ORDER BY relation") == [
        (view, 0, 0, 0) for view in sorted(IDENTITY_LEDGER_VIEWS)
    ], context


@pytest.mark.asyncio
async def test_the_tables_are_empty_and_unrefreshed_until_the_first_refresh() -> None:
    """A schema apply runs no history inline; the state row, not emptiness, says "not refreshed yet"."""
    async with _ledger_database() as params:
        connection = await _connect(params)
        async with connection:
            async with connection.cursor() as cursor:
                await _seed(cursor, Fixture())
            for view in IDENTITY_LEDGER_VIEWS:
                assert await _rows(connection, sql.SQL("SELECT * FROM {} LIMIT 1").format(sql.Identifier(view))) == []
            assert await _refresh_state(connection) == []


@pytest.mark.asyncio
async def test_the_first_refresh_fills_every_table_equal_to_the_reference() -> None:
    """Acceptance 2: one refresh fills all four, row for row as catalog-api defines them."""
    fixture = Fixture()
    async with _ledger_database() as params:
        connection = await _connect(params)
        refresher = await _connect(params)
        async with connection, refresher:
            async with connection.cursor() as cursor:
                await _seed(cursor, fixture)

            # The first refresh takes no lock a reader queues behind.
            async with refresher.transaction():
                ((refreshed_at,),) = await _rows(refresher, "SELECT identity_ledger_refresh()")
                locks = await _view_locks(connection, refresher.info.backend_pid)
                assert locks
                assert not any(mode == "AccessExclusiveLock" for _view, mode in locks)
            ((state_at, duration, watermark),) = await _refresh_state(connection)
            assert (state_at, watermark) == (refreshed_at, None)
            assert duration.total_seconds() >= 0

            # The fixture's ledger, by hand.
            assert await _view_rows(connection, "identity_ledger_edition_events") == _expected_edition_events(fixture)
            assert await _view_rows(connection, "identity_ledger_edition_parents") == _expected_parents(fixture)
            assert await _view_rows(connection, "identity_ledger_work_summary") == _expected_work_summary(fixture)
            density = {
                (kind, month, kind_type, dated_from): events
                for kind, month, kind_type, dated_from, events in await _view_rows(connection, "identity_ledger_density")
            }
            # A supersession counts once here, and a link change counts with or without a holder.
            assert density[("release", "2026-10", "reattached", "groovemap")] == 1
            assert density[("release", "2025-11", "link_added", "musicbrainz_fullexport")] == 2
            assert density[("release", "2026-01", "minted", "groovemap")] == 6
            assert density[("master", "2026-07", "promoted", "groovemap")] == 1
            assert density[("master", "2025-11", "link_added", "musicbrainz_fullexport")] == 1

            # And catalog-api's reference definitions agree on every row.
            await _build_reference(connection)
            for view in IDENTITY_LEDGER_VIEWS:
                assert await _view_rows(connection, view, _REFERENCE_SCHEMA), view
            await _assert_equal_to_a_full_recompute(connection)

            # Applying the schema again leaves the filled tables as they are.
            assert await initializer._apply_postgres_schema(params) is True
            assert await _view_rows(connection, "identity_ledger_work_summary") == _expected_work_summary(fixture)


@pytest.mark.asyncio
async def test_the_table_columns_keep_the_types_the_views_published() -> None:
    """A reader sees the same columns and types the materialized views had, in the same order."""
    async with _ledger_database() as params:
        connection = await _connect(params)
        async with connection:
            await _build_reference(connection)
            shape = """
                SELECT attname, format_type(atttypid, atttypmod) FROM pg_attribute
                WHERE attrelid = %s::regclass AND attnum > 0 AND NOT attisdropped ORDER BY attnum
            """
            for view in IDENTITY_LEDGER_VIEWS:
                assert await _rows(connection, shape, (f"public.{view}",)) == await _rows(connection, shape, (f"{_REFERENCE_SCHEMA}.{view}",)), view


@pytest.mark.asyncio
async def test_a_later_refresh_never_blocks_a_reader() -> None:
    """Acceptance 3: a reader in another session neither blocks nor is blocked by the refresh."""
    fixture = Fixture()
    async with _ledger_database() as params:
        writer = await _connect(params)
        reader = await _connect(params)
        late_reader = await _connect(params)
        refresher = await _connect(params)
        async with writer, reader, late_reader, refresher:
            async with writer.cursor() as cursor:
                await _seed(cursor, fixture)
            ((first_at,),) = await _rows(writer, "SELECT identity_ledger_refresh()")
            summary = "SELECT evented_editions, last_event FROM identity_ledger_work_summary WHERE work_id = %s AND event = 'link_changed'"
            assert await _rows(writer, summary, (fixture.w1,)) == [(1, LOAD)]

            # A later load changes E1's link again.
            relinked = _at(11, 2)
            await writer.execute(
                "INSERT INTO identity_link_events "
                "(provider, entity_kind, external_id, event, discogs_id_before, discogs_id_after, event_at, dated_from, source_ref) "
                "VALUES ('musicbrainz', 'release', %s, 'link_changed', 2, 3, %s, 'groovemap_load', 'load-20261102')",
                (fixture.mb1, relinked),
            )

            await reader.execute("BEGIN")
            assert await _rows(reader, summary, (fixture.w1,)) == [(1, LOAD)]
            await refresher.execute("SET lock_timeout = '5s'")
            watermark = {"musicbrainz": "load-20261102", "discogs": "20261101"}
            await refresher.execute("BEGIN")
            ((second_at,),) = await _rows(refresher, "SELECT identity_ledger_refresh(%s)", (Jsonb(watermark),))
            assert second_at > first_at

            # Mid-refresh, before it commits: no ACCESS EXCLUSIVE, so a new reader
            # with a short lock timeout reads at once and sees the previous rows.
            locks = await _view_locks(writer, refresher.info.backend_pid)
            assert locks
            assert not any(mode == "AccessExclusiveLock" for _view, mode in locks)
            await late_reader.execute("SET lock_timeout = '1s'")
            assert await _rows(late_reader, summary, (fixture.w1,)) == [(1, LOAD)]
            assert await _rows(reader, summary, (fixture.w1,)) == [(1, LOAD)]

            await refresher.execute("COMMIT")
            await reader.execute("COMMIT")
            assert await _rows(late_reader, summary, (fixture.w1,)) == [(1, relinked)]
            ((state_at, duration, state_watermark),) = await _refresh_state(writer)
            assert state_at == second_at
            assert duration.total_seconds() >= 0
            assert state_watermark == watermark


@pytest.mark.asyncio
async def test_refreshing_needs_the_owner_or_maintain_on_every_view() -> None:
    """Acceptance 5: catalog-api's login, the owner today, refreshes; another login needs the stated grant."""
    fixture = Fixture()
    async with _ledger_database() as params:
        role, password = f"ledger_refresher_{uuid4().hex[:12]}", uuid4().hex
        owner = await _connect(params)
        async with owner:
            await owner.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
            try:
                other = await _connect(params, user=role, password=password)
                async with other:
                    with pytest.raises(psycopg.errors.InsufficientPrivilege):
                        await _rows(other, "SELECT identity_ledger_refresh()")

                    # The grant the design note states for a separate catalog-api login.
                    await owner.execute(
                        sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON {} TO {}").format(
                            sql.SQL(", ").join(sql.Identifier(table) for table in IDENTITY_LEDGER_REFRESH_GRANTS), sql.Identifier(role)
                        )
                    )
                    await owner.execute(
                        sql.SQL("GRANT SELECT ON provider_aliases, catalog_items, catalog_item_supersessions, identity_link_events TO {}").format(
                            sql.Identifier(role)
                        )
                    )
                    await owner.execute(sql.SQL("GRANT USAGE ON SCHEMA graph, musicbrainz TO {}").format(sql.Identifier(role)))
                    await owner.execute(sql.SQL("GRANT SELECT ON graph.derived_from, musicbrainz.releases TO {}").format(sql.Identifier(role)))
                    # Both a first build and a later, per-item refresh.
                    await _rows(other, "SELECT identity_ledger_refresh()")
                    async with owner.cursor() as cursor:
                        await _seed(cursor, fixture)
                    await _rows(other, "SELECT identity_ledger_refresh()")
                    assert await _view_rows(owner, "identity_ledger_work_summary") == _expected_work_summary(fixture)

                # The owning login, which every service connects as today, needs no grant.
                ((refreshed_at,),) = await _rows(owner, "SELECT identity_ledger_refresh()")
                assert [row[0] for row in await _refresh_state(owner)] == [refreshed_at]
            finally:
                await owner.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                await owner.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


@pytest.mark.asyncio
async def test_a_writer_that_cannot_write_the_log_directly_still_writes_its_source() -> None:
    """Capture runs as the schema owner, so a role granted only a source keeps working."""
    async with _ledger_database() as params:
        role, password = f"ledger_writer_{uuid4().hex[:12]}", uuid4().hex
        owner = await _connect(params)
        async with owner:
            await owner.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
            try:
                await owner.execute(sql.SQL("GRANT INSERT, SELECT ON catalog_items, provider_aliases TO {}").format(sql.Identifier(role)))
                writer = await _connect(params, user=role, password=password)
                async with writer:
                    item = uuid4()
                    await writer.execute("INSERT INTO catalog_items (id, kind) VALUES (%s, 'release')", (item,))
                    await writer.execute(
                        "INSERT INTO provider_aliases (provider, entity_kind, external_id, native_id) VALUES ('discogs', 'release', 'r1', %s)",
                        (item,),
                    )
                assert await _rows(owner, "SELECT DISTINCT item_id FROM identity_ledger_changes") == [(item,)]
            finally:
                await owner.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                await owner.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


@pytest.mark.asyncio
async def test_a_transaction_that_commits_after_a_refresh_is_not_lost() -> None:
    """What a timestamp watermark gets wrong: a write older than the refresh, committed after it."""
    fixture = Fixture()
    async with _ledger_database() as params:
        writer = await _connect(params)
        late = await _connect(params)
        async with writer, late:
            async with writer.cursor() as cursor:
                await _seed(cursor, fixture)
            await _build_reference(writer)
            await _rows(writer, "SELECT identity_ledger_refresh()")

            await late.execute("BEGIN")
            await late.execute(
                "INSERT INTO provider_aliases (provider, entity_kind, external_id, native_id, valid_from) "
                "VALUES ('musicbrainz', 'release', %s, %s, %s)",
                (str(uuid4()), fixture.b, _at(4, 1)),
            )
            await _rows(writer, "SELECT identity_ledger_refresh()")
            await late.execute("COMMIT")

            await _refresh_and_compare(writer)
            assert (fixture.b, _at(4, 1), "linked_at_ingest", 1) in await _view_rows(writer, "identity_ledger_edition_events")


@pytest.mark.asyncio
async def test_a_refresh_with_no_change_writes_nothing_but_the_state_row() -> None:
    """Cost scales with changes: with none, not one ledger row is touched."""
    fixture = Fixture()
    async with _ledger_database() as params:
        connection = await _connect(params)
        async with connection:
            async with connection.cursor() as cursor:
                await _seed(cursor, fixture)
            await _rows(connection, "SELECT identity_ledger_refresh()")

            ctids = sql.SQL(" UNION ALL ").join(
                sql.SQL("SELECT {}, ctid::text, xmin::text FROM {}").format(sql.Literal(table), sql.Identifier(table))
                for table in (*IDENTITY_LEDGER_VIEWS, "identity_ledger_item_mints", "identity_ledger_item_events")
            )
            before = await _rows(connection, ctids)
            assert before
            await _rows(connection, "SELECT identity_ledger_refresh()")
            assert await _rows(connection, ctids) == before


@pytest.mark.asyncio
async def test_a_truncated_source_rebuilds_on_the_next_refresh() -> None:
    """TRUNCATE names no rows, so it asks for a rebuild, and the deltas are not applied twice."""
    fixture = Fixture()
    async with _ledger_database() as params:
        connection = await _connect(params)
        async with connection:
            async with connection.cursor() as cursor:
                await _seed(cursor, fixture)
            await _build_reference(connection)
            await _rows(connection, "SELECT identity_ledger_refresh()")

            # A delta waits in the log when the rebuild is asked for.
            await connection.execute(
                "INSERT INTO identity_link_events (provider, entity_kind, external_id, event, discogs_id_before, discogs_id_after, "
                "event_at, dated_from, source_ref) VALUES ('musicbrainz', 'release', %s, 'link_changed', 2, 3, %s, 'groovemap_load', 'x')",
                (fixture.mb1, _at(11, 2)),
            )
            await connection.execute("TRUNCATE graph.derived_from")
            await _refresh_and_compare(connection)
            assert await _rows(connection, "SELECT count(*) FROM identity_ledger_density_changes") == [(0,)]


@pytest.mark.asyncio
async def test_a_disabled_capture_trigger_stops_the_refresh() -> None:
    """A source no longer followed fails loudly, rather than answering stale rows."""
    async with _ledger_database() as params:
        connection = await _connect(params)
        async with connection:
            await connection.execute("ALTER TABLE provider_aliases DISABLE TRIGGER identity_ledger_capture_insert")
            with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match="capture triggers are incomplete"):
                await _rows(connection, "SELECT identity_ledger_refresh()")
            await connection.execute("ALTER TABLE provider_aliases ENABLE TRIGGER identity_ledger_capture_insert")
            await _rows(connection, "SELECT identity_ledger_refresh()")


@pytest.mark.asyncio
async def test_verify_reports_a_tampered_table_and_a_pending_change() -> None:
    """The verification path can fail: it finds rows wrong in either direction, and says when it is not settled."""
    fixture = Fixture()
    async with _ledger_database() as params:
        connection = await _connect(params)
        async with connection:
            async with connection.cursor() as cursor:
                await _seed(cursor, fixture)
            await _rows(connection, "SELECT identity_ledger_refresh()")
            verify = "SELECT relation, missing, unexpected, pending_changes FROM identity_ledger_verify() WHERE relation = 'identity_ledger_density'"
            assert await _rows(connection, verify) == [("identity_ledger_density", 0, 0, 0)]

            await connection.execute(
                "UPDATE identity_ledger_density SET events = events + 1 WHERE (kind, month, type) = ('release', '2026-01', 'minted')"
            )
            assert await _rows(connection, verify) == [("identity_ledger_density", 1, 1, 0)]

            await connection.execute("SELECT identity_ledger_request_rebuild()")
            assert await _rows(connection, verify) == [("identity_ledger_density", 1, 1, 1)]
            await _rows(connection, "SELECT identity_ledger_refresh()")
            assert await _rows(connection, verify) == [("identity_ledger_density", 0, 0, 0)]


async def _make_v063_views(connection: psycopg.AsyncConnection[Any]) -> None:
    """Replace the tables with the materialized views v0.6.3 published, refreshed, with a state row."""
    await connection.execute("DROP TABLE " + ", ".join(IDENTITY_LEDGER_VIEWS))
    bodies = (
        _ledger_edition_events_select(),
        _ledger_edition_parents_select("SELECT DISTINCT edition_id FROM public.identity_ledger_edition_events"),
        _ledger_work_summary_select("public.identity_ledger_edition_events", "public.identity_ledger_edition_parents"),
        _ledger_density_select(),
    )
    for view, body in zip(IDENTITY_LEDGER_VIEWS, bodies, strict=True):
        await connection.execute(f"CREATE MATERIALIZED VIEW public.{view} AS {body}".encode())
    await connection.execute("INSERT INTO identity_ledger_refresh_state (refreshed_at, duration) VALUES (now(), '45 seconds')")


@pytest.mark.asyncio
async def test_upgrading_from_the_materialized_views_rebuilds_on_the_first_refresh() -> None:
    """The upgrade on a database holding data: views go, the state row goes, the first refresh fills the tables."""
    fixture = Fixture()
    async with _ledger_database() as params:
        connection = await _connect(params)
        async with connection:
            async with connection.cursor() as cursor:
                await _seed(cursor, fixture)
            await _build_reference(connection)
            await _make_v063_views(connection)

            # Writes that land before capture exists must be in the first build.
            await connection.execute("DROP TRIGGER identity_ledger_capture_insert ON provider_aliases")
            await connection.execute(
                "INSERT INTO provider_aliases (provider, entity_kind, external_id, native_id, valid_from) "
                "VALUES ('musicbrainz', 'release', %s, %s, %s)",
                (str(uuid4()), fixture.b, _at(4, 1)),
            )

            assert await initializer._apply_postgres_schema(params) is True
            relkinds = dict(
                await _rows(connection, "SELECT relname, relkind::text FROM pg_class WHERE relname = ANY(%s)", (list(IDENTITY_LEDGER_VIEWS),))
            )
            assert relkinds == dict.fromkeys(IDENTITY_LEDGER_VIEWS, "r")
            assert await _refresh_state(connection) == []
            await _refresh_and_compare(connection)
            assert await _refresh_state(connection) != []

            # A second apply finds nothing to migrate.
            assert await initializer._apply_postgres_schema(params) is True
            await _assert_equal_to_a_full_recompute(connection)


@pytest.mark.asyncio
async def test_rolling_back_restores_the_materialized_views_equal_to_the_reference() -> None:
    """The documented rollback: remove what the upgrade added, and v0.6.3's own statements apply again."""
    fixture = Fixture()
    async with _ledger_database() as params:
        connection = await _connect(params)
        async with connection:
            async with connection.cursor() as cursor:
                await _seed(cursor, fixture)
            await _build_reference(connection)
            await _rows(connection, "SELECT identity_ledger_refresh()")

            for statement in IDENTITY_LEDGER_ROLLBACK_STATEMENTS:
                await connection.execute(statement.encode())
            assert await _rows(connection, "SELECT count(*) FROM pg_trigger WHERE tgname LIKE 'identity\\_ledger\\_%'") == [(0,)]
            assert await _rows(
                connection, "SELECT count(*) FROM pg_proc WHERE proname LIKE 'identity\\_ledger\\_%' AND proname <> 'identity_ledger_refresh'"
            ) == [(0,)]
            assert await _refresh_state(connection) == []

            # The source tables were never touched, and every statement of the old
            # schema that names a ledger relation can run: nothing of the name remains.
            await connection.execute(
                "CREATE TABLE identity_ledger_edition_events_probe AS SELECT 1 AS probe; DROP TABLE identity_ledger_edition_events_probe"
            )
            bodies = (
                _ledger_edition_events_select(),
                _ledger_edition_parents_select("SELECT DISTINCT edition_id FROM public.identity_ledger_edition_events"),
                _ledger_work_summary_select("public.identity_ledger_edition_events", "public.identity_ledger_edition_parents"),
                _ledger_density_select(),
            )
            for view, body in zip(IDENTITY_LEDGER_VIEWS, bodies, strict=True):
                await connection.execute(f"CREATE MATERIALIZED VIEW IF NOT EXISTS public.{view} AS {body}".encode())
                await connection.execute(sql.SQL("REFRESH MATERIALIZED VIEW {}").format(sql.Identifier(_REFERENCE_SCHEMA, view)))
                assert await _view_rows(connection, view) == await _view_rows(connection, view, _REFERENCE_SCHEMA), view


class History:
    """A deterministic random history of everything the sources admit, written as the writers would."""

    KINDS = ("release", "master", "artist", "label")

    def __init__(self, cursor: Any, seed: int) -> None:
        self.cursor = cursor
        self.random = random.Random(seed)  # noqa: S311 -- reproducible test data, not security
        self.clock = _at(1, 1, 2024)
        self.items: dict[str, list[UUID]] = {kind: [] for kind in self.KINDS}
        self.instants: list[datetime] = [self.clock]

    def tick(self) -> datetime:
        """Return a later instant, sometimes one already used, so instants collide as a merge's do."""
        if self.random.random() < 0.2:
            return self.random.choice(self.instants)
        self.clock += timedelta(days=self.random.randint(1, 45))
        self.instants.append(self.clock)
        return self.clock

    async def one(self, query: str, parameters: Any = None) -> Any:
        await self.cursor.execute(query, parameters)
        row = await self.cursor.fetchone()
        return row

    async def mint(self) -> None:
        kind = self.random.choice(self.KINDS)
        item = uuid4()
        at = self.tick()
        order = self.random.random()
        statements = [
            ("INSERT INTO catalog_items (id, kind) VALUES (%s, %s)", (item, kind)),
            (
                "INSERT INTO provider_aliases (provider, entity_kind, external_id, native_id, valid_from) VALUES ('discogs', %s, %s, %s, %s)",
                (kind, uuid4().hex, item, at),
            ),
        ]
        # An alias written before its item, which no foreign key forbids.
        for statement, parameters in statements if order < 0.9 else reversed(statements):
            await self.cursor.execute(statement, parameters)
        self.items[kind].append(item)

    async def attach(self) -> None:
        kind = self.random.choice(self.KINDS)
        if not self.items[kind]:
            return
        item = self.random.choice(self.items[kind])
        provider = self.random.choice(("discogs", "musicbrainz", "musicbrainz", "wikidata"))
        # Reuse a link event's or release group's id now and then, so an alias arrives for a row already there.
        known = await self.one(
            "SELECT external_id FROM identity_link_events WHERE entity_kind = %s AND random() < 0.5 "
            "AND NOT EXISTS (SELECT 1 FROM provider_aliases AS alias WHERE alias.provider = 'musicbrainz' AND alias.entity_kind = %s "
            "AND alias.external_id = identity_link_events.external_id AND alias.valid_to IS NULL) LIMIT 1",
            (kind, kind),
        )
        external = known[0] if known and provider == "musicbrainz" else str(uuid4())
        # Sometimes earlier than the item's mint, which moves the mint.
        at = self.tick() if self.random.random() < 0.85 else _at(1, 1, 2023) + timedelta(days=self.random.randint(0, 300))
        await self.cursor.execute(
            "INSERT INTO provider_aliases (provider, entity_kind, external_id, native_id, valid_from) VALUES (%s, %s, %s, %s, %s)",
            (provider, kind, external, item, at),
        )

    async def close(self) -> None:
        row = await self.one("SELECT id FROM provider_aliases WHERE valid_to IS NULL ORDER BY random() LIMIT 1")
        if row:
            await self.cursor.execute("UPDATE provider_aliases SET valid_to = %s WHERE id = %s", (self.tick(), row[0]))

    async def remove(self) -> None:
        row = await self.one("SELECT id FROM provider_aliases ORDER BY random() LIMIT 1")
        if row:
            await self.cursor.execute("DELETE FROM provider_aliases WHERE id = %s", (row[0],))

    async def forget(self) -> None:
        """Delete an unreferenced item, as the loaders' mint rollback does."""
        row = await self.one(
            "SELECT id, kind FROM catalog_items AS item WHERE NOT EXISTS (SELECT 1 FROM catalog_item_supersessions AS s "
            "WHERE item.id IN (s.superseded_id, s.survivor_id)) ORDER BY random() LIMIT 1"
        )
        if row:
            await self.cursor.execute("DELETE FROM catalog_items WHERE id = %s", (row[0],))
            self.items[row[1]].remove(row[0])

    async def supersede(self) -> None:
        kind = self.random.choice(("release", "release", "master"))
        if len(self.items[kind]) < 2:
            return
        superseded, survivor = self.random.sample(self.items[kind], 2)
        if await self.one("SELECT 1 FROM catalog_item_supersessions WHERE superseded_id = %s AND valid_to IS NULL", (superseded,)):
            return
        at = self.tick()
        cause = self.random.choice(("edition_promotion", "catalog_reattachment"))
        await self.cursor.execute(
            "INSERT INTO catalog_item_supersessions (superseded_id, survivor_id, cause, decision_ref, valid_from) VALUES (%s, %s, %s, %s, %s)",
            (superseded, survivor, cause, uuid4(), at),
        )
        # A re-attachment moves an alias at the supersession's instant, which is no alias event.
        moved = await self.one(
            "SELECT id, provider, entity_kind, external_id FROM provider_aliases WHERE native_id = %s AND valid_to IS NULL LIMIT 1", (superseded,)
        )
        if moved and cause == "catalog_reattachment":
            await self.cursor.execute("UPDATE provider_aliases SET valid_to = %s WHERE id = %s", (at, moved[0]))
            await self.cursor.execute(
                "INSERT INTO provider_aliases (provider, entity_kind, external_id, native_id, valid_from) VALUES (%s, %s, %s, %s, %s)",
                (moved[1], moved[2], moved[3], survivor, at),
            )

    async def revert(self) -> None:
        row = await self.one("SELECT id FROM catalog_item_supersessions WHERE valid_to IS NULL ORDER BY random() LIMIT 1")
        if row:
            await self.cursor.execute("UPDATE catalog_item_supersessions SET valid_to = %s WHERE id = %s", (self.tick(), row[0]))

    async def erase_supersession(self) -> None:
        row = await self.one(
            "SELECT id FROM catalog_item_supersessions AS s WHERE NOT EXISTS (SELECT 1 FROM catalog_item_supersessions AS later WHERE later.via_id = s.id) "
            "ORDER BY random() LIMIT 1"
        )
        if row:
            await self.cursor.execute("DELETE FROM catalog_item_supersessions WHERE id = %s", (row[0],))

    async def link(self) -> None:
        kind = self.random.choice((*self.KINDS, "release", "release"))
        held = await self.one(
            "SELECT external_id FROM provider_aliases WHERE provider = 'musicbrainz' AND entity_kind = %s AND valid_to IS NULL ORDER BY random() LIMIT 1",
            (kind,),
        )
        external = held[0] if held and self.random.random() < 0.8 else str(uuid4())
        await self.cursor.execute(
            "INSERT INTO identity_link_events (provider, entity_kind, external_id, event, discogs_id_before, discogs_id_after, event_at, dated_from, source_ref) "
            "VALUES ('musicbrainz', %s, %s, 'link_added', NULL, 1, %s, %s, %s) ON CONFLICT DO NOTHING",
            (kind, external, self.tick(), self.random.choice(("musicbrainz_fullexport", "groovemap_load")), uuid4().hex),
        )

    async def unlink(self) -> None:
        row = await self.one("SELECT id FROM identity_link_events ORDER BY random() LIMIT 1")
        if row:
            await self.cursor.execute("DELETE FROM identity_link_events WHERE id = %s", (row[0],))

    async def redate_link(self) -> None:
        row = await self.one("SELECT id FROM identity_link_events ORDER BY random() LIMIT 1")
        if row:
            await self.cursor.execute("UPDATE identity_link_events SET event_at = %s WHERE id = %s", (self.tick(), row[0]))

    async def derive(self) -> None:
        release = await self.one(
            "SELECT external_id FROM provider_aliases WHERE provider = 'discogs' AND entity_kind = 'release' ORDER BY random() LIMIT 1"
        )
        master = await self.one(
            "SELECT external_id FROM provider_aliases WHERE provider = 'discogs' AND entity_kind = 'master' ORDER BY random() LIMIT 1"
        )
        if release and master:
            await self.cursor.execute(
                "INSERT INTO graph.derived_from (release_id, master_id) VALUES (%s, %s) ON CONFLICT DO NOTHING", (release[0], master[0])
            )

    async def underive(self) -> None:
        row = await self.one("SELECT release_id, master_id FROM graph.derived_from ORDER BY random() LIMIT 1")
        if row:
            await self.cursor.execute("DELETE FROM graph.derived_from WHERE release_id = %s AND master_id = %s", row)

    async def mb_release(self) -> None:
        release = await self.one(
            "SELECT external_id FROM provider_aliases WHERE provider = 'musicbrainz' AND entity_kind = 'release' AND external_id !~ '^[0-9a-f]{32}$' ORDER BY random() LIMIT 1"
        )
        group = await self.one(
            "SELECT external_id FROM provider_aliases WHERE provider = 'musicbrainz' AND entity_kind = 'master' AND external_id !~ '^[0-9a-f]{32}$' ORDER BY random() LIMIT 1"
        )
        if release and group:
            await self.cursor.execute(
                "INSERT INTO musicbrainz.releases (mbid, name, release_group_mbid) VALUES (%s, 'x', %s) "
                "ON CONFLICT (mbid) DO UPDATE SET release_group_mbid = EXCLUDED.release_group_mbid",
                (release[0], group[0]),
            )

    async def mb_release_touch(self) -> None:
        await self.cursor.execute(
            "UPDATE musicbrainz.releases SET name = name || 'y' WHERE mbid IN (SELECT mbid FROM musicbrainz.releases ORDER BY random() LIMIT 1)"
        )

    async def mb_release_gone(self) -> None:
        await self.cursor.execute("DELETE FROM musicbrainz.releases WHERE mbid IN (SELECT mbid FROM musicbrainz.releases ORDER BY random() LIMIT 1)")

    async def rebuild(self) -> None:
        await self.cursor.execute("SELECT identity_ledger_request_rebuild()")

    async def step(self) -> None:
        operations = [
            (self.mint, 12),
            (self.attach, 12),
            (self.close, 5),
            (self.remove, 2),
            (self.forget, 2),
            (self.supersede, 6),
            (self.revert, 3),
            (self.erase_supersession, 1),
            (self.link, 6),
            (self.unlink, 1),
            (self.redate_link, 1),
            (self.derive, 6),
            (self.underive, 2),
            (self.mb_release, 5),
            (self.mb_release_touch, 1),
            (self.mb_release_gone, 1),
        ]
        await self.random.choices([operation for operation, _weight in operations], [weight for _operation, weight in operations])[0]()


@pytest.mark.asyncio
@pytest.mark.parametrize("seed", range(6))
async def test_any_history_refreshed_in_steps_equals_a_full_recompute(seed: int) -> None:
    """The acceptance criterion: after every batch of changes, the tables equal both recomputes."""
    async with _ledger_database() as params:
        connection = await _connect(params)
        async with connection:
            await _build_reference(connection)
            history = History(connection.cursor(), seed)
            for _ in range(40):
                await history.mint()
            await _refresh_and_compare(connection, (seed, "first"))

            for batch in range(25):
                for _ in range(history.random.randint(1, 12)):
                    await history.step()
                if history.random.random() < 0.05:
                    await history.rebuild()
                await _refresh_and_compare(connection, (seed, batch))

            # Maintained state is what a rebuild computes, so the stores never drift either.
            stores = ["identity_ledger_item_mints", "identity_ledger_item_events"]
            maintained = [set(await _rows(connection, sql.SQL("SELECT * FROM {}").format(sql.Identifier(table)))) for table in stores]
            await history.rebuild()
            await _refresh_and_compare(connection, (seed, "rebuilt"))
            assert [set(await _rows(connection, sql.SQL("SELECT * FROM {}").format(sql.Identifier(table)))) for table in stores] == maintained

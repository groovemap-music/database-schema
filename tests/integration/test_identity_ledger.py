"""Real-engine proof of the precomputed Identity Ledger (epic gm-database-schema-k8e9).

Every test runs in its own fresh database, so the views start unpopulated and
the fixture below is the only history they hold: the suite's shared database
collects other tests' items and supersessions, and once refreshed a view never
returns to unpopulated.
"""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from groovemap_schema import initializer
from groovemap_schema.postgres import IDENTITY_LEDGER_VIEWS


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


async def _populated(connection: psycopg.AsyncConnection[Any]) -> dict[str, bool]:
    rows = await _rows(
        connection,
        "SELECT relname, relispopulated FROM pg_class WHERE relkind = 'm' AND relnamespace = 'public'::regnamespace",
    )
    return dict(rows)


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


@pytest.mark.asyncio
async def test_the_views_are_unreadable_until_the_first_refresh() -> None:
    """Acceptance 4: a view not yet refreshed raises the error catalog-api checks for."""
    async with _ledger_database() as params:
        connection = await _connect(params)
        async with connection:
            assert await _populated(connection) == dict.fromkeys(IDENTITY_LEDGER_VIEWS, False)
            for view in IDENTITY_LEDGER_VIEWS:
                with pytest.raises(psycopg.errors.ObjectNotInPrerequisiteState, match=f'materialized view "{view}" has not been populated'):
                    await _rows(connection, sql.SQL("SELECT * FROM {} LIMIT 1").format(sql.Identifier(view)))
            assert await _refresh_state(connection) == []


@pytest.mark.asyncio
async def test_the_first_refresh_populates_every_view_equal_to_the_reference() -> None:
    """Acceptance 2: one refresh populates all four, row for row as catalog-api defines them."""
    fixture = Fixture()
    async with _ledger_database() as params:
        connection = await _connect(params)
        refresher = await _connect(params)
        async with connection, refresher:
            async with connection.cursor() as cursor:
                await _seed(cursor, fixture)

            # The first refresh builds each view plainly: CONCURRENTLY refuses an
            # unpopulated view, so a plain build's ACCESS EXCLUSIVE is the price.
            async with refresher.transaction():
                ((refreshed_at,),) = await _rows(refresher, "SELECT identity_ledger_refresh()")
                exclusive = await _view_locks(connection, refresher.info.backend_pid)
                assert {(view, "AccessExclusiveLock") for view in IDENTITY_LEDGER_VIEWS} <= exclusive
            assert await _populated(connection) == dict.fromkeys(IDENTITY_LEDGER_VIEWS, True)
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
                reference = await _view_rows(connection, view, _REFERENCE_SCHEMA)
                assert reference, view
                assert await _view_rows(connection, view) == reference, view

            # Applying the schema again leaves the populated views as they are.
            assert await initializer._apply_postgres_schema(params) is True
            assert await _populated(connection) == dict.fromkeys(IDENTITY_LEDGER_VIEWS, True)
            assert await _view_rows(connection, "identity_ledger_work_summary") == _expected_work_summary(fixture)


@pytest.mark.asyncio
async def test_a_later_refresh_is_concurrent_and_never_blocks_a_reader() -> None:
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

            # The reader holds its ACCESS SHARE lock on the summary in an open
            # transaction. A plain REFRESH would wait for it and hit the
            # refresher's lock timeout; a concurrent one does not wait.
            await reader.execute("BEGIN")
            assert await _rows(reader, summary, (fixture.w1,)) == [(1, LOAD)]
            await refresher.execute("SET lock_timeout = '5s'")
            watermark = {"musicbrainz": "load-20261102", "discogs": "20261101"}
            await refresher.execute("BEGIN")
            ((second_at,),) = await _rows(refresher, "SELECT identity_ledger_refresh(%s)", (Jsonb(watermark),))
            assert second_at > first_at

            # Mid-refresh, before it commits: it holds EXCLUSIVE, never ACCESS
            # EXCLUSIVE, so a new reader with a short lock timeout reads at once
            # and sees the rows of the previous refresh.
            locks = await _view_locks(writer, refresher.info.backend_pid)
            assert {(view, "ExclusiveLock") for view in IDENTITY_LEDGER_VIEWS} <= locks
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
                        sql.SQL("GRANT MAINTAIN ON {} TO {}").format(
                            sql.SQL(", ").join(sql.Identifier(view) for view in IDENTITY_LEDGER_VIEWS), sql.Identifier(role)
                        )
                    )
                    await owner.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE ON identity_ledger_refresh_state TO {}").format(sql.Identifier(role)))
                    # Both a first, plain refresh and a later, concurrent one.
                    await _rows(other, "SELECT identity_ledger_refresh()")
                    await _rows(other, "SELECT identity_ledger_refresh()")

                # The owning login, which every service connects as today, needs no grant.
                ((refreshed_at,),) = await _rows(owner, "SELECT identity_ledger_refresh()")
                assert [row[0] for row in await _refresh_state(owner)] == [refreshed_at]
            finally:
                await owner.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                await owner.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))

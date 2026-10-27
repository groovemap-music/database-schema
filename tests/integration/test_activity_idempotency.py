"""Real-engine proof that activity.events records a retried event once, across partitions.

`activity.events` is range-partitioned by month on `occurred_at`, so its own unique key has
to include `occurred_at` and cannot see a retry stamped a second, a minute, or a month after
the original. `activity.event_idempotency` is the non-partitioned key table a BEFORE INSERT
trigger claims; these tests drive it the way a writer does, through
`INSERT ... ON CONFLICT (occurred_at, idempotency_key) DO NOTHING RETURNING event_id`, and
prove the retry, cross-subject, concurrency, immutability, erasure, retention, and
existing-database cases.

Every test runs in its own fresh database: the cases depend on which keys are already held,
and the convergence cases need a database initialized by the previous release's statements.
"""

from __future__ import annotations

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

import psycopg
import pytest
from psycopg import errors, sql

from groovemap_schema import initializer
from groovemap_schema.postgres import _execute_schema_statements, _schema_statements


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# Mid-month, so a one-second and a one-minute retry stay in the original's partition and
# only the month cases cross a partition boundary.
ORIGINAL_AT = datetime(2026, 10, 15, 12, 0, 0, tzinfo=UTC)
ORIGINAL_PARTITION = "activity.events_y2026m10"

# The statements this change adds. A database initialized without them is one the previous
# release left behind, which is what the convergence cases start from.
_IDEMPOTENCY_STATEMENT_NAMES = frozenset(
    {
        "activity.event_idempotency table",
        "idx_activity_event_idempotency_occurred_at",
        "activity.reject_key_mutation function",
        "activity.event_idempotency reject_mutation trigger",
        "activity.claim_idempotency_key function",
        "activity.release_idempotency_key function",
        "activity.events release_idempotency_key trigger",
        "activity.release_subject_idempotency_keys function",
        "activity.user_subjects release_idempotency_keys trigger",
        "activity.purge_idempotency_keys function",
        "activity.events claim_idempotency_key trigger",
        "activity.erasures add idempotency_keys_deleted",
    }
)

# catalog-api's insert, reduced to the envelope columns the key depends on.
_INSERT_EVENT = """
INSERT INTO activity.events (
    event_type, schema_version, subject_id, occurred_at, producer, consent_purposes, idempotency_key
) VALUES (%s, 1, %s, %s, 'integration-test', ARRAY['product_analytics'], %s)
ON CONFLICT (occurred_at, idempotency_key) DO NOTHING
RETURNING event_id
"""

# The same insert with no conflict clause, as a writer without one would send it.
_PLAIN_INSERT_EVENT = _INSERT_EVENT.replace("ON CONFLICT (occurred_at, idempotency_key) DO NOTHING\n", "")

# catalog-api's erasure transaction as it stands (api/routers/activity.py): it names no key
# table, so whatever closes the keys has to be the schema's own.
_ERASE_SUBJECT = (
    "DELETE FROM activity.events WHERE subject_id = %s",
    "DELETE FROM activity.impressions WHERE subject_id = %s",
    "DELETE FROM activity.user_subjects WHERE subject_id = %s",
)


def _admin_params() -> dict[str, Any]:
    return {**initializer._postgres_connection_params(), "dbname": "postgres"}


async def _apply_previous_release(params: dict[str, Any]) -> None:
    """Initialize `params` with every statement except the ones this change adds."""
    statements = [(name, statement) for name, statement in _schema_statements() if name not in _IDEMPOTENCY_STATEMENT_NAMES]
    assert len(statements) == sum(1 for _ in _schema_statements()) - len(_IDEMPOTENCY_STATEMENT_NAMES)
    connection = await psycopg.AsyncConnection.connect(**params, autocommit=True)
    async with connection, connection.cursor() as cursor:
        _success, failures = await _execute_schema_statements(cursor, statements)
    assert failures == 0


@contextlib.asynccontextmanager
async def _activity_database(*, previous_release: bool = False) -> AsyncIterator[dict[str, Any]]:
    """Yield the connection parameters of a fresh database with the activity schema applied."""
    database = f"activity_test_{uuid4().hex[:12]}"
    admin = await psycopg.AsyncConnection.connect(**_admin_params(), autocommit=True)
    try:
        await admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        params = {**initializer._postgres_connection_params(), "dbname": database}
        if previous_release:
            await _apply_previous_release(params)
        else:
            assert await initializer._apply_postgres_schema(params) is True
        async with await _connect(params) as connection:
            for month in ("2026-10-01", "2026-11-01"):
                await connection.execute("SELECT activity.ensure_month_partition('events', %s)", (month,))
        yield params
    finally:
        await admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database)))
        await admin.close()


async def _connect(params: dict[str, Any], *, autocommit: bool = True) -> psycopg.AsyncConnection[Any]:
    return await psycopg.AsyncConnection.connect(**params, autocommit=autocommit)


async def _rows(connection: psycopg.AsyncConnection[Any], query: str, parameters: Any = None) -> list[tuple[Any, ...]]:
    async with connection.cursor() as cursor:
        await cursor.execute(query, parameters)
        return await cursor.fetchall()


async def _record(
    connection: psycopg.AsyncConnection[Any],
    subject_id: UUID,
    key: str,
    occurred_at: datetime,
    *,
    statement: str = _INSERT_EVENT,
) -> UUID | None:
    """Insert one event and return its id, or None when nothing was inserted."""
    rows = await _rows(connection, statement, ("recommendation.saved", subject_id, occurred_at, key))
    return rows[0][0] if rows else None


async def _events(connection: psycopg.AsyncConnection[Any], subject_id: UUID, key: str) -> list[tuple[Any, ...]]:
    return await _rows(
        connection,
        """
        SELECT event_id, occurred_at, tableoid::regclass::text
        FROM activity.events
        WHERE subject_id = %s AND idempotency_key = %s
        ORDER BY occurred_at, event_id
        """,
        (subject_id, key),
    )


async def _keys(connection: psycopg.AsyncConnection[Any], subject_id: UUID | None = None) -> list[tuple[Any, ...]]:
    query = "SELECT subject_id, idempotency_key, event_id, occurred_at FROM activity.event_idempotency"
    if subject_id is None:
        return await _rows(connection, f"{query} ORDER BY subject_id, idempotency_key")
    return await _rows(connection, f"{query} WHERE subject_id = %s ORDER BY idempotency_key", (subject_id,))


@pytest.mark.parametrize(
    ("retry_after", "retry_partition"),
    [
        pytest.param(timedelta(seconds=1), ORIGINAL_PARTITION, id="later-second"),
        pytest.param(timedelta(minutes=1), ORIGINAL_PARTITION, id="later-minute"),
        pytest.param(-timedelta(hours=1), ORIGINAL_PARTITION, id="earlier-hour"),
        pytest.param(timedelta(days=31), "activity.events_y2026m11", id="later-month-partition"),
        pytest.param(timedelta(days=61), "activity.events_default", id="default-partition"),
    ],
)
async def test_a_retry_is_skipped_whatever_its_occurrence_time(retry_after: timedelta, retry_partition: str) -> None:
    subject_id, key = uuid4(), f"recommendation.saved:{uuid4()}"
    async with _activity_database() as params, await _connect(params) as connection:
        original = await _record(connection, subject_id, key, ORIGINAL_AT)
        assert original is not None

        # An event at the retry's instant routes to `retry_partition`, where the occurrence key
        # (occurred_at, idempotency_key) has nothing to collide with.
        control_key = f"control:{uuid4()}"
        await _record(connection, subject_id, control_key, ORIGINAL_AT + retry_after)
        [(_control, _at, routed)] = await _events(connection, subject_id, control_key)
        assert routed == retry_partition

        assert await _record(connection, subject_id, key, ORIGINAL_AT + retry_after) is None

        # One row, and it is the original: the retry neither adds a row nor moves the occurrence.
        assert await _events(connection, subject_id, key) == [(original, ORIGINAL_AT, ORIGINAL_PARTITION)]
        [(_subject, claimed_key, claimed_by, claimed_at)] = [row for row in await _keys(connection, subject_id) if row[1] == key]
        assert (claimed_key, claimed_by, claimed_at) == (key, original, ORIGINAL_AT)


async def test_a_same_instant_retry_still_meets_the_occurrence_key_clause() -> None:
    """The trigger skips it first; the ON CONFLICT target catalog-api names still resolves."""
    subject_id, key = uuid4(), f"fit.opened:{uuid4()}"
    async with _activity_database() as params, await _connect(params) as connection:
        original = await _record(connection, subject_id, key, ORIGINAL_AT)
        assert await _record(connection, subject_id, key, ORIGINAL_AT) is None
        assert await _events(connection, subject_id, key) == [(original, ORIGINAL_AT, ORIGINAL_PARTITION)]
        constraint = await _rows(
            connection,
            """
            SELECT pg_get_constraintdef(oid) FROM pg_catalog.pg_constraint
            WHERE conrelid = 'activity.events'::regclass AND contype = 'u'
            """,
        )
        assert constraint == [("UNIQUE (occurred_at, idempotency_key)",)]


async def test_a_plain_insert_retry_is_skipped_without_an_error() -> None:
    subject_id, key = uuid4(), f"search.query:{uuid4()}"
    async with _activity_database() as params, await _connect(params) as connection:
        original = await _record(connection, subject_id, key, ORIGINAL_AT, statement=_PLAIN_INSERT_EVENT)
        for retry_at in (ORIGINAL_AT, ORIGINAL_AT + timedelta(seconds=1), ORIGINAL_AT + timedelta(days=31)):
            assert await _record(connection, subject_id, key, retry_at, statement=_PLAIN_INSERT_EVENT) is None
        assert await _events(connection, subject_id, key) == [(original, ORIGINAL_AT, ORIGINAL_PARTITION)]


async def test_distinct_events_are_each_recorded() -> None:
    """The key is per (subject, idempotency key): anything else is a new fact."""
    first, second = uuid4(), uuid4()
    opened, saved = f"recommendation.opened:{uuid4()}", f"recommendation.saved:{uuid4()}"
    async with _activity_database() as params, await _connect(params) as connection:
        inserted = [
            await _record(connection, first, opened, ORIGINAL_AT),
            await _record(connection, first, saved, ORIGINAL_AT + timedelta(seconds=5)),
            # Another subject's event with the same key is that subject's own fact.
            await _record(connection, second, opened, ORIGINAL_AT + timedelta(seconds=9)),
        ]
        assert None not in inserted
        assert len(await _events(connection, first, opened)) == 1
        assert len(await _events(connection, first, saved)) == 1
        assert len(await _events(connection, second, opened)) == 1
        assert len(await _keys(connection)) == 3


async def test_another_subjects_occurrence_key_claims_nothing() -> None:
    """Two subjects under one key at one instant: the kept occurrence key refuses the second.

    The second row is refused exactly as before this table existed (skipped by ON CONFLICT,
    raised by a plain INSERT), and it claims no key, so the second subject's own event under
    that key at its own instant is still recorded.
    """
    first, second, key = uuid4(), uuid4(), f"search.query:{uuid4()}"
    async with _activity_database() as params, await _connect(params) as connection:
        original = await _record(connection, first, key, ORIGINAL_AT)
        assert await _record(connection, second, key, ORIGINAL_AT) is None
        with pytest.raises(errors.UniqueViolation):
            await _record(connection, second, key, ORIGINAL_AT, statement=_PLAIN_INSERT_EVENT)
        assert await _events(connection, first, key) == [(original, ORIGINAL_AT, ORIGINAL_PARTITION)]
        assert await _events(connection, second, key) == []
        assert await _keys(connection, second) == []

        later = await _record(connection, second, key, ORIGINAL_AT + timedelta(minutes=1))
        assert later is not None
        assert await _keys(connection, second) == [(second, key, later, ORIGINAL_AT + timedelta(minutes=1))]


async def test_concurrent_retries_record_one_row() -> None:
    """In-flight copies of one event, each stamped differently and across two partitions, race to one row."""
    subject_id, key = uuid4(), f"fit.opened:{uuid4()}"
    offsets = [timedelta(seconds=offset) for offset in range(6)] + [timedelta(days=31), timedelta(days=31, seconds=1)]
    async with _activity_database() as params:
        connections = [await _connect(params) for _ in offsets]
        try:
            results = await asyncio.gather(
                *(_record(connection, subject_id, key, ORIGINAL_AT + offset) for connection, offset in zip(connections, offsets, strict=True))
            )
        finally:
            for connection in connections:
                await connection.close()

        inserted = [event_id for event_id in results if event_id is not None]
        assert len(inserted) == 1
        async with await _connect(params) as connection:
            [(event_id, _occurred_at, _partition)] = await _events(connection, subject_id, key)
            assert event_id == inserted[0]
            [(_subject, _key, claimed_by, _claimed_at)] = await _keys(connection, subject_id)
            assert claimed_by == event_id


async def _wait_until_blocked(observer: psycopg.AsyncConnection[Any], pid: int) -> None:
    """Return once backend `pid` is waiting on a lock, or fail after ten seconds."""
    for _attempt in range(200):
        waiting = await _rows(observer, "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s", (pid,))
        if waiting == [("Lock",)]:
            return
        await asyncio.sleep(0.05)
    pytest.fail(f"backend {pid} never waited on the in-flight claim")


@pytest.mark.parametrize("first_outcome", ["commit", "rollback"])
async def test_a_retry_waits_for_an_in_flight_claim(first_outcome: str) -> None:
    """A retry behind an uncommitted original skips if it commits and records if it rolls back."""
    subject_id, key = uuid4(), f"recommendation.saved:{uuid4()}"
    async with (
        _activity_database() as params,
        await _connect(params, autocommit=False) as first,
        await _connect(params) as second,
        await _connect(params) as observer,
    ):
        original = await _record(first, subject_id, key, ORIGINAL_AT)
        assert original is not None
        retry = asyncio.create_task(_record(second, subject_id, key, ORIGINAL_AT + timedelta(days=31)))
        await _wait_until_blocked(observer, second.info.backend_pid)
        assert not retry.done()

        if first_outcome == "commit":
            await first.commit()
            assert await retry is None
            assert await _events(observer, subject_id, key) == [(original, ORIGINAL_AT, ORIGINAL_PARTITION)]
        else:
            await first.rollback()
            recorded = await retry
            assert recorded is not None
            assert await _events(observer, subject_id, key) == [(recorded, ORIGINAL_AT + timedelta(days=31), "activity.events_y2026m11")]
        assert len(await _keys(observer, subject_id)) == 1


async def test_a_row_missing_a_key_column_is_reported_by_activity_events() -> None:
    async with _activity_database() as params, await _connect(params) as connection:
        with pytest.raises(errors.NotNullViolation) as raised:
            await _record(connection, uuid4(), None, ORIGINAL_AT)  # type: ignore[arg-type]
        assert raised.value.diag.column_name == "idempotency_key"
        assert raised.value.diag.table_name == "events_y2026m10"
        assert await _keys(connection) == []


async def test_the_key_table_is_append_only() -> None:
    """A key whose event is stored is as immutable as the event."""
    subject_id, key = uuid4(), f"fit.opened:{uuid4()}"
    async with _activity_database() as params, await _connect(params) as connection:
        await _record(connection, subject_id, key, ORIGINAL_AT)
        for statement in (
            "UPDATE activity.event_idempotency SET occurred_at = occurred_at + INTERVAL '1 day' WHERE subject_id = %s",
            "DELETE FROM activity.event_idempotency WHERE subject_id = %s",
        ):
            with pytest.raises(errors.RestrictViolation, match=r"activity\.event_idempotency is append-only"):
                await connection.execute(statement, (subject_id,))
        assert len(await _keys(connection, subject_id)) == 1


async def _strand_a_key(params: dict[str, Any], stranded: UUID, occupant: UUID, key: str) -> UUID:
    """Leave `stranded` holding `key` for an event that was never stored, and return that event id.

    The only way left to do it: `occupant`'s insert under the same occurrence key is still
    uncommitted, so the claim's check cannot see it, and the occurrence key refuses the
    stranded subject's row only once the occupant commits.
    """
    async with (
        await _connect(params, autocommit=False) as first,
        await _connect(params) as second,
        await _connect(params) as observer,
    ):
        assert await _record(first, occupant, key, ORIGINAL_AT) is not None
        refused = asyncio.create_task(_record(second, stranded, key, ORIGINAL_AT))
        await _wait_until_blocked(observer, second.info.backend_pid)
        await first.commit()
        assert await refused is None
        assert await _events(observer, stranded, key) == []
        [unstored] = [row[2] for row in await _keys(observer, stranded) if row[1] == key]
        assert await _rows(observer, "SELECT count(*) FROM activity.events WHERE event_id = %s", (unstored,)) == [(0,)]
        return unstored


async def test_a_key_naming_no_stored_event_does_not_suppress_the_subjects_event() -> None:
    stranded, occupant, key = uuid4(), uuid4(), f"search.query:{uuid4()}"
    async with _activity_database() as params:
        unstored = await _strand_a_key(params, stranded, occupant, key)
        async with await _connect(params) as connection:
            later = await _record(connection, stranded, key, ORIGINAL_AT + timedelta(minutes=1))
            assert later is not None
            assert later != unstored
            assert await _keys(connection, stranded) == [(stranded, key, later, ORIGINAL_AT + timedelta(minutes=1))]
            # And from here the key dedupes as any other.
            assert await _record(connection, stranded, key, ORIGINAL_AT + timedelta(days=31)) is None


async def _linked_subject(connection: psycopg.AsyncConnection[Any]) -> tuple[UUID, UUID]:
    """Create a user and its pseudonym link, returning (user_id, subject_id)."""
    [(user_id,)] = await _rows(
        connection,
        "INSERT INTO users (email, hashed_password) VALUES (%s, 'not-a-real-hash') RETURNING id",
        (f"{uuid4()}@example.test",),
    )
    [(subject_id,)] = await _rows(connection, "INSERT INTO activity.user_subjects (user_id) VALUES (%s) RETURNING subject_id", (user_id,))
    return user_id, subject_id


async def test_erasure_leaves_no_key_rows_without_naming_the_key_table() -> None:
    keys = [f"recommendation.opened:{uuid4()}", f"recommendation.saved:{uuid4()}"]
    stranded_key = f"search.query:{uuid4()}"
    async with _activity_database() as params, await _connect(params) as connection:
        _user, erased = await _linked_subject(connection)
        _other_user, kept = await _linked_subject(connection)
        # Each subject at its own instants: the occurrence key spans subjects.
        for minutes, subject_id in enumerate((erased, kept), start=1):
            for offset, key in enumerate(keys):
                assert await _record(connection, subject_id, key, ORIGINAL_AT + timedelta(days=31 * offset, minutes=minutes)) is not None
        # One key no event deletion can release: its event was never stored.
        await _strand_a_key(params, erased, kept, stranded_key)
        held = await _rows(connection, "SELECT count(*) FROM activity.event_idempotency WHERE subject_id = %s", (erased,))
        assert held == [(3,)]

        # catalog-api's erasure transaction, unchanged: it never names activity.event_idempotency.
        async with connection.transaction():
            await connection.execute("SET LOCAL groovemap.erasure = 'on'")
            for statement in _ERASE_SUBJECT:
                await connection.execute(statement, (erased,))
            await connection.execute(
                """
                INSERT INTO activity.erasures (subject_id, requested_at, completed_at, events_deleted, impressions_deleted, idempotency_keys_deleted)
                VALUES (%s, NOW(), NOW(), 2, 0, %s)
                """,
                (erased, held[0][0]),
            )

        assert await _keys(connection, erased) == []
        assert await _rows(connection, "SELECT count(*) FROM activity.events WHERE subject_id = %s", (erased,)) == [(0,)]
        assert await _rows(connection, "SELECT idempotency_keys_deleted FROM activity.erasures WHERE subject_id = %s", (erased,)) == [(3,)]
        assert {row[1] for row in await _keys(connection, kept)} == {*keys, stranded_key}
        # The bypass did not outlive its transaction.
        with pytest.raises(errors.RestrictViolation):
            await connection.execute("DELETE FROM activity.event_idempotency WHERE subject_id = %s", (kept,))


async def test_unlinking_without_erasure_keeps_the_keys_of_stored_events() -> None:
    """A user deleted outright cascades to its link: only keys with no stored event go with it."""
    key, stranded_key = f"fit.opened:{uuid4()}", f"search.query:{uuid4()}"
    async with _activity_database() as params, await _connect(params) as connection:
        user_id, subject_id = await _linked_subject(connection)
        stored = await _record(connection, subject_id, key, ORIGINAL_AT + timedelta(minutes=5))
        await _strand_a_key(params, subject_id, uuid4(), stranded_key)

        await connection.execute("DELETE FROM users WHERE id = %s", (user_id,))

        assert await _keys(connection, subject_id) == [(subject_id, key, stored, ORIGINAL_AT + timedelta(minutes=5))]


async def test_the_purge_releases_the_keys_of_a_dropped_partition() -> None:
    subject_id = uuid4()
    kept, dropped, retried = (f"fit.opened:{uuid4()}" for _ in range(3))
    november = ORIGINAL_AT + timedelta(days=31)
    async with _activity_database() as params, await _connect(params) as connection:
        kept_id = await _record(connection, subject_id, kept, ORIGINAL_AT)
        await _record(connection, subject_id, dropped, november)
        await _record(connection, subject_id, retried, november)

        # The retention runbook's partition drop: DDL, so no row trigger fires.
        await connection.execute("DROP TABLE activity.events_y2026m11")
        assert len(await _keys(connection, subject_id)) == 3
        # The dropped month's events are gone, so a retry under one of its keys is stored
        # afresh (here into the DEFAULT partition) and takes the key over.
        again = await _record(connection, subject_id, retried, november)
        assert again is not None
        assert await _events(connection, subject_id, retried) == [(again, november, "activity.events_default")]

        purged = await _rows(connection, "SELECT activity.purge_idempotency_keys('2026-12-01')")
        assert purged == [(1,)]
        held = {row[1]: row[2] for row in await _keys(connection, subject_id)}
        # The October key is older than the bound but its event is stored, so it still dedupes.
        assert held == {kept: kept_id, retried: again}
        assert await _rows(connection, "SELECT activity.purge_idempotency_keys('2026-12-01')") == [(0,)]


async def _seed_previous_release_history(connection: psycopg.AsyncConnection[Any]) -> dict[str, Any]:
    """Write what the previous release let through: one event, retried into two more rows."""
    subject_id, other = uuid4(), uuid4()
    retried, single = f"recommendation.saved:{uuid4()}", f"fit.opened:{uuid4()}"
    duplicates = [
        await _record(connection, subject_id, retried, at)
        for at in (ORIGINAL_AT + timedelta(minutes=1), ORIGINAL_AT, ORIGINAL_AT + timedelta(days=31))
    ]
    return {
        "subject_id": subject_id,
        "other": other,
        "retried": retried,
        "single": single,
        "earliest": duplicates[1],
        "duplicates": duplicates,
        "single_id": await _record(connection, subject_id, single, ORIGINAL_AT),
        # The occurrence key spans subjects, so another subject's event under the same key
        # needs its own instant to land at all.
        "other_id": await _record(connection, other, retried, ORIGINAL_AT + timedelta(minutes=2)),
    }


async def test_an_existing_database_converges_and_keeps_its_duplicates() -> None:
    async with _activity_database(previous_release=True) as params, await _connect(params) as connection:
        assert await _rows(connection, "SELECT to_regclass('activity.event_idempotency')") == [(None,)]
        seeded = await _seed_previous_release_history(connection)
        assert None not in (*seeded["duplicates"], seeded["single_id"], seeded["other_id"])
        assert len(await _events(connection, seeded["subject_id"], seeded["retried"])) == 3

        assert await initializer._apply_postgres_schema(params) is True

        # The duplicates are kept; removing them is a separate data decision.
        assert len(await _events(connection, seeded["subject_id"], seeded["retried"])) == 3
        # Each key went to its earliest occurrence, the one a retry repeats.
        assert await _keys(connection, seeded["subject_id"]) == sorted(
            [
                (seeded["subject_id"], seeded["retried"], seeded["earliest"], ORIGINAL_AT),
                (seeded["subject_id"], seeded["single"], seeded["single_id"], ORIGINAL_AT),
            ],
            key=lambda row: row[1],
        )
        assert await _keys(connection, seeded["other"]) == [
            (seeded["other"], seeded["retried"], seeded["other_id"], ORIGINAL_AT + timedelta(minutes=2))
        ]

        # The trigger is on the parent and cloned onto every partition, including one made later.
        await connection.execute("SELECT activity.ensure_month_partition('events', '2026-12-01')")
        triggered = await _rows(
            connection,
            """
            SELECT t.tgrelid::regclass::text FROM pg_catalog.pg_trigger AS t
            WHERE t.tgname = 'activity_events_claim_idempotency_key'
            ORDER BY 1
            """,
        )
        assert [row[0] for row in triggered] == [
            "activity.events",
            "activity.events_default",
            "activity.events_y2026m10",
            "activity.events_y2026m11",
            "activity.events_y2026m12",
        ]
        for retry_at in (ORIGINAL_AT + timedelta(seconds=1), ORIGINAL_AT + timedelta(days=61)):
            assert await _record(connection, seeded["subject_id"], seeded["retried"], retry_at) is None
        assert len(await _events(connection, seeded["subject_id"], seeded["retried"])) == 3


async def test_the_backfill_runs_once_and_later_inits_skip_it() -> None:
    """A key removed after convergence stays removed: a later init does not rescan the events."""
    async with _activity_database(previous_release=True) as params, await _connect(params) as connection:
        seeded = await _seed_previous_release_history(connection)
        assert await initializer._apply_postgres_schema(params) is True
        async with connection.transaction():
            await connection.execute("SET LOCAL groovemap.erasure = 'on'")
            await connection.execute(
                "DELETE FROM activity.event_idempotency WHERE subject_id = %s AND idempotency_key = %s",
                (seeded["subject_id"], seeded["single"]),
            )

        assert await initializer._apply_postgres_schema(params) is True
        held = {row[1] for row in await _keys(connection, seeded["subject_id"])}
        assert held == {seeded["retried"]}
        # The event the removed key named is still there, so only a rescan of activity.events
        # could have restored the key; its absence is the guard holding.
        assert len(await _events(connection, seeded["subject_id"], seeded["single"])) == 1

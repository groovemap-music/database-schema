"""Measure the v0.6.3 credit keys against the v0.6.2 shape, on the real tables and statements.

Usage: measure_credit_key.py "<libpq DSN of a disposable database>" <pairs>

Applies the schema from this checkout, loads 1M synthetic credits into each credit relation in
interleaved pairs, then times the real upgrade statements. Each run empties the table first, so
the data on disk stays under 1 GB.
"""

# ruff: noqa: T201

from __future__ import annotations

import asyncio
import statistics
import sys
import time
from typing import Any

import psycopg

from groovemap_schema import postgres


ROWS = 1_000_000
BATCH = 260
# The dump's rate of credits per release, in dump order, with power-law names.
STAGE = """
DROP SCHEMA IF EXISTS bench CASCADE;
CREATE SCHEMA bench;
CREATE UNLOGGED TABLE bench.src AS
SELECT g,
       'Credit Person ' || floor(661775 * power(random(), 3))::int AS person_name,
       (10 + floor(g / 2.6))::bigint::text AS release_id,
       (ARRAY['Producer','Written-By','Mastered By','Mixed By','Engineer','Composed By','Lyrics By','Vocals','Guitar','Bass','Drums','Keyboards','Design','Photography By','Artwork','Liner Notes','Lacquer Cut By','Recorded By','Arranged By','Music By','Featuring','Remix','Co-producer','Executive Producer','Backing Vocals','Saxophone','Trumpet','Piano','Violin','Conductor','Orchestra','A&R','Management','Coordinator','Cover','Layout','Words By','Performer','Programmed By','Edited By','Written-By, Producer','Producer, Mixed By','Bass Guitar','Lead Vocals','Sleeve Notes'])[1 + floor(45 * power(((g * 2654435761::bigint) % 4294967296)::float8 / 4294967296, 2.0))::int] AS role,
       1 + g % 12 AS track_ordinal
FROM generate_series(1, 1000000) AS g;
CREATE UNIQUE INDEX src_g ON bench.src (g);
ANALYZE bench.src;
"""
LOAD = {
    "credited_on": (
        "INSERT INTO graph.credited_on (person_name, release_id, role) SELECT person_name, release_id, role "
        "FROM bench.src WHERE g > %s AND g <= %s ON CONFLICT DO NOTHING"
    ),
    "track_credited_on": (
        "INSERT INTO graph.track_credited_on (person_name, release_id, track_ordinal, sub_track_ordinal, track_position, role) "
        "SELECT person_name, release_id, track_ordinal, 0, 'A1', role FROM bench.src WHERE g > %s AND g <= %s ON CONFLICT DO NOTHING"
    ),
}
TO_TWO_HASH = {
    "credited_on": (
        "DROP INDEX graph.credited_on_key",
        "CREATE UNIQUE INDEX credited_on_key ON graph.credited_on (release_id, graph.text_key(person_name), graph.text_key(role))",
        "CREATE INDEX credited_on_person_name_hash ON graph.credited_on USING hash (person_name)",
    ),
    "track_credited_on": (
        "DROP INDEX graph.track_credited_on_key",
        "CREATE UNIQUE INDEX track_credited_on_key ON graph.track_credited_on "
        "(release_id, track_ordinal, sub_track_ordinal, graph.text_key(person_name), graph.text_key(role))",
        "CREATE INDEX track_credited_on_person_name_hash ON graph.track_credited_on USING hash (person_name)",
    ),
}
TRUNCATE = {"credited_on": "TRUNCATE graph.credited_on", "track_credited_on": "TRUNCATE graph.track_credited_on"}


def statement(name: str) -> str:
    return str(dict(postgres._schema_statements())[name])


async def to_two_hash(cursor: Any, relation: str) -> None:
    for ddl in TO_TWO_HASH[relation]:
        await cursor.execute(ddl)


async def to_combined(cursor: Any, relation: str) -> None:
    await cursor.execute(statement(f"graph.{relation} two-hash key migration"))


async def scalar(cursor: Any, query: str) -> Any:
    await cursor.execute(query)
    row = await cursor.fetchone()
    return row[0]


async def load(connection: Any, relation: str) -> dict[str, float]:
    """Load one relation a batch at a time and report time, WAL, and index size."""
    cursor = connection.cursor()
    await cursor.execute(TRUNCATE[relation])
    await cursor.execute("CHECKPOINT")
    start_lsn = await scalar(cursor, "SELECT pg_current_wal_lsn()")
    started = time.perf_counter()
    for low in range(0, ROWS, BATCH):
        await cursor.execute(LOAD[relation], (low, low + BATCH))
    seconds = time.perf_counter() - started
    wal = await scalar(cursor, f"SELECT pg_wal_lsn_diff(pg_current_wal_lsn(), '{start_lsn}')")
    return {
        "seconds": seconds,
        "wal_mb": float(wal) / 2**20,
        "key_mb": await scalar(cursor, f"SELECT pg_relation_size('graph.{relation}_key')") / 2**20,
        "indexes_mb": await scalar(cursor, f"SELECT pg_indexes_size('graph.{relation}')") / 2**20,
    }


async def timed(cursor: Any, sql: str) -> float:
    started = time.perf_counter()
    await cursor.execute(sql)
    return time.perf_counter() - started


async def upgrades(connection: Any) -> None:
    """Time the real statements over a loaded table, from each deployed shape."""
    cursor = connection.cursor()
    for relation in LOAD:
        await to_two_hash(cursor, relation)
        await load(connection, relation)
        before = await scalar(cursor, f"SELECT pg_indexes_size('graph.{relation}')") / 2**20
        seconds = await timed(cursor, statement(f"graph.{relation} two-hash key migration"))
        after = await scalar(cursor, f"SELECT pg_indexes_size('graph.{relation}')") / 2**20
        key = await scalar(cursor, f"SELECT pg_relation_size('graph.{relation}_key')") / 2**20
        repeat = await timed(cursor, statement(f"graph.{relation} two-hash key migration"))
        print(
            f"upgrade from v0.6.2 {relation}: {seconds:.2f} s, indexes {before:.0f} to {after:.0f} MB, key {key:.0f} MB, second run {repeat * 1000:.1f} ms"
        )

    await cursor.execute("DROP INDEX graph.credited_on_key, graph.credited_on_role_category")
    await cursor.execute("ALTER TABLE graph.credited_on ADD PRIMARY KEY (person_name, release_id, role)")
    await cursor.execute("CREATE INDEX credited_on_reverse ON graph.credited_on (release_id, person_name)")
    await cursor.execute("CREATE INDEX credited_on_role_category ON graph.credited_on (role_category, person_name)")
    seconds = await timed(cursor, statement("graph.credited_on free-text key migration"))
    key = await scalar(cursor, "SELECT pg_relation_size('graph.credited_on_key')") / 2**20
    print(f"upgrade from v0.6.0 credited_on: {seconds:.2f} s, key {key:.0f} MB")


async def main(dsn: str, pairs: int) -> None:
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as connection:
        cursor = connection.cursor()
        await postgres._execute_schema_statements(cursor, postgres._schema_statements())
        await cursor.execute(STAGE)
        for relation in LOAD:
            results: dict[str, list[dict[str, float]]] = {"v0.6.2": [], "v0.6.3": []}
            for _ in range(pairs):
                await to_two_hash(cursor, relation)
                results["v0.6.2"].append(await load(connection, relation))
                await to_combined(cursor, relation)
                results["v0.6.3"].append(await load(connection, relation))
            for shape, runs in results.items():
                times = [run["seconds"] for run in runs]
                print(
                    f"{relation} {shape}: load {statistics.median(times):.1f} s ({min(times):.1f} to {max(times):.1f}), "
                    f"key {runs[0]['key_mb']:.0f} MB, indexes {runs[0]['indexes_mb']:.0f} MB, WAL {runs[0]['wal_mb']:.0f} MB",
                    flush=True,
                )
            deltas = [new["seconds"] - old["seconds"] for old, new in zip(results["v0.6.2"], results["v0.6.3"], strict=True)]
            print(
                f"{relation} paired: median {statistics.median(deltas):.1f} s, faster in {sum(delta < 0 for delta in deltas)} of {pairs}", flush=True
            )
        await upgrades(connection)


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1], int(sys.argv[2])))

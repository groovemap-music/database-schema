"""Measure an unchanged reload: the updated_at touch against the seen list (gm-database-schema-uw6d).

Usage: bench.py "<libpq DSN of a disposable database>" RELEASES RELATIONSHIPS ENTITIES

The database must already hold the full schema (`apply.py`) and RELEASES rows of fi4a's toasted
`releases` sample (`../2026-11-07-release-title-search-vector/generate.py`), loaded with ids
1..RELEASES. Never point this at a live database: it rewrites every row and checkpoints.

Each write runs in loader-sized batches (discogs-sql-loader's default of 100 ids), one transaction
per batch, after a CHECKPOINT, so every page is logged whole the first time it is touched, as it
is over an hour-long reload under a 5-minute checkpoint_timeout.
"""

from __future__ import annotations

import json
import sys
import time
import uuid
from typing import Any

import psycopg


BATCH = 100
TOUCH = "UPDATE releases SET updated_at = NOW() WHERE data_id = ANY(%s)"
APPEND = "INSERT INTO loader_seen.discogs_releases (seen_key) SELECT unnest(%s::text[])"
REL_KEY = (
    "musicbrainz.relationship_key(source_mbid, target_mbid, source_entity_type, target_entity_type, "
    "relationship_type, begin_date, end_date, attributes)"
)
_REL_COLUMNS = "source_mbid, source_entity_type, target_mbid, target_entity_type, relationship_type, attributes, begin_date, end_date, ended"
_REL_SOURCE = "SELECT s, 'artist', t, 'artist', r, NULL, b, NULL, false FROM unnest(%s::uuid[], %s::uuid[], %s::text[], %s::text[]) AS i(s, t, r, b)"
REL_INSERT = f"INSERT INTO musicbrainz.relationships ({_REL_COLUMNS}) {_REL_SOURCE} ON CONFLICT ({REL_KEY}) "
REL_TOUCH = REL_INSERT + "DO UPDATE SET ended = EXCLUDED.ended, updated_at = NOW()"
REL_NO_OP = (
    REL_INSERT + "DO UPDATE SET ended = EXCLUDED.ended, updated_at = NOW() WHERE musicbrainz.relationships.ended IS DISTINCT FROM EXCLUDED.ended"
)
# The lock-free no-op: only a changed row is updated (an UPDATE locks only the rows its WHERE
# keeps), and a new one inserted; ON CONFLICT DO NOTHING takes no lock on the existing row.
REL_UPDATE_CHANGED = (
    "UPDATE musicbrainz.relationships AS r SET ended = false, updated_at = NOW() "
    "FROM unnest(%s::uuid[], %s::uuid[], %s::text[], %s::text[]) AS i(s, t, r, b) "
    "WHERE musicbrainz.relationship_key(r.source_mbid, r.target_mbid, r.source_entity_type, r.target_entity_type, "
    "r.relationship_type, r.begin_date, r.end_date, r.attributes) = musicbrainz.relationship_key(i.s, i.t, 'artist', 'artist', i.r, i.b, NULL, NULL) "
    "AND r.ended IS DISTINCT FROM false"
)
REL_DO_NOTHING = REL_INSERT + "DO NOTHING"
REL_APPEND = (
    "INSERT INTO loader_seen.musicbrainz_relationships (seen_key) "
    "SELECT loader_seen.hash_key(musicbrainz.relationship_key(s, t, 'artist', 'artist', r, b, NULL, NULL)) FROM unnest(%s::uuid[], %s::uuid[], %s::text[], %s::text[]) AS i(s, t, r, b)"
)


# A MusicBrainz entity row as wide as lux's musicbrainz.releases heap (1.26 KB a row).
MB_COLUMNS = "(mbid, name, status, data)"
MB_SOURCE = "FROM unnest(%s::uuid[], %s::text[], %s::text[], %s::jsonb[]) AS i(mbid, name, status, data)"
MB_UPSERT = f"INSERT INTO musicbrainz.releases {MB_COLUMNS} SELECT i.mbid, i.name, i.status, i.data {MB_SOURCE} ON CONFLICT (mbid) DO UPDATE SET "
MB_TOUCH = MB_UPSERT + "name = EXCLUDED.name, status = EXCLUDED.status, data = EXCLUDED.data, updated_at = NOW()"
MB_LOCKING_NO_OP = MB_TOUCH + (
    " WHERE (musicbrainz.releases.name, musicbrainz.releases.status, musicbrainz.releases.data) "
    "IS DISTINCT FROM (EXCLUDED.name, EXCLUDED.status, EXCLUDED.data)"
)
MB_UPDATE_CHANGED = (
    f"UPDATE musicbrainz.releases AS e SET name = i.name, status = i.status, data = i.data, updated_at = NOW() {MB_SOURCE} "  # noqa: S608
    "WHERE e.mbid = i.mbid AND (e.name, e.status, e.data) IS DISTINCT FROM (i.name, i.status, i.data)"
)
MB_INSERT_NEW = f"INSERT INTO musicbrainz.releases {MB_COLUMNS} SELECT i.mbid, i.name, i.status, i.data {MB_SOURCE} ON CONFLICT (mbid) DO NOTHING"


def report(values: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(values, default=str) + "\n")
    sys.stdout.flush()


def one(cursor: Any, query: str, params: Any = None) -> Any:
    cursor.execute(query, params)
    row = cursor.fetchone()
    return row[0] if row else None


def stats(cursor: Any, relation: str) -> tuple[int, int, int, int]:
    cursor.execute("SELECT pg_stat_force_next_flush()")
    cursor.execute(
        "SELECT n_tup_upd, n_tup_hot_upd, n_dead_tup, pg_total_relation_size(relid) FROM pg_stat_all_tables WHERE relid = %s::regclass",
        (relation,),
    )
    row = cursor.fetchone()
    return row if row else (0, 0, 0, 0)


def run(conn: Any, label: str, relation: str, batches: list[tuple[Any, ...]], statements: list[str]) -> dict[str, Any]:
    with conn.cursor() as cursor:
        cursor.execute("CHECKPOINT")
        upd0, hot0, _dead0, size0 = stats(cursor, relation)
        lsn = one(cursor, "SELECT pg_current_wal_insert_lsn()::text")
        started = time.perf_counter()
        for params in batches:
            with conn.transaction():
                for statement in statements:
                    cursor.execute(statement, params)
        elapsed = time.perf_counter() - started
        wal = int(one(cursor, "SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(), %s::pg_lsn)", (lsn,)))
        upd1, hot1, dead1, size1 = stats(cursor, relation)
    rows = sum(len(params[0]) for params in batches)
    result = {
        "run": label,
        "rows": rows,
        "seconds": round(elapsed, 2),
        "us_per_row": round(elapsed / rows * 1e6, 1),
        "wal_bytes": wal,
        "wal_per_row": round(wal / rows, 1),
        "updates": upd1 - upd0,
        "hot": hot1 - hot0,
        "dead_tuples": dead1,
        "relation_growth_bytes": size1 - size0,
    }
    report(result)
    return result


def timed(conn: Any, label: str, query: str, params: Any = None) -> None:
    with conn.cursor() as cursor:
        cursor.execute("SELECT temp_bytes FROM pg_stat_database WHERE datname = current_database()")
        temp0 = cursor.fetchone()[0]
        started = time.perf_counter()
        cursor.execute(query, params)
        rows = cursor.fetchall()
        elapsed = time.perf_counter() - started
        cursor.execute("SELECT pg_stat_force_next_flush()")
        cursor.execute("SELECT temp_bytes FROM pg_stat_database WHERE datname = current_database()")
        temp1 = cursor.fetchone()[0]
    report({"query": label, "seconds": round(elapsed, 2), "rows": len(rows), "first": rows[0] if rows else None, "temp_bytes": temp1 - temp0})


def main(dsn: str, releases: int, relationships: int, entities: int) -> None:
    with psycopg.connect(dsn, autocommit=True) as conn, conn.cursor() as cursor:
        # The previous dump wrote every row a month ago; this extraction began just before the reload.
        cursor.execute("ALTER TABLE releases DISABLE TRIGGER USER")
        cursor.execute("UPDATE releases SET updated_at = now() - interval '30 days'")
        cursor.execute("ALTER TABLE releases ENABLE TRIGGER USER")
        cursor.execute("UPDATE loader_seen.lists SET opened_at = now() - interval '1 day'")
        cursor.execute("TRUNCATE loader_seen.discogs_releases, loader_seen.musicbrainz_relationships")
        cursor.execute("VACUUM (ANALYZE, FREEZE) releases")
        started_at = one(cursor, "SELECT (now() - interval '1 minute')::text")
        report(
            {
                "releases": releases,
                "heap": one(cursor, "SELECT pg_size_pretty(pg_relation_size('releases'))"),
                "total": one(cursor, "SELECT pg_size_pretty(pg_total_relation_size('releases'))"),
                "indexes": one(cursor, "SELECT count(*) FROM pg_index WHERE indrelid = 'releases'::regclass"),
            },
        )

        ids = [str(i) for i in range(1, releases + 1)]
        batches = [(ids[i : i + BATCH],) for i in range(0, len(ids), BATCH)]
        # The dump no longer has the last 0.5%: what the purge must find.
        present = batches[: int(len(batches) * 0.995)]
        run(conn, "discogs seen append", "loader_seen.discogs_releases", present, [APPEND])
        cursor.execute("ANALYZE loader_seen.discogs_releases")
        report({"seen_list_bytes": one(cursor, "SELECT pg_total_relation_size('loader_seen.discogs_releases')")})
        timed(conn, "status", "SELECT * FROM loader_seen.status('discogs', 'releases', %s)", (started_at,))
        timed(conn, "stale", "SELECT * FROM loader_seen.discogs_releases_stale(%s)", (started_at,))
        timed(conn, "old purge count", "SELECT count(*) FROM releases WHERE updated_at < %s", (started_at,))
        run(conn, "discogs touch", "releases", present, [TOUCH])
        timed(
            conn,
            "shadow",
            "SELECT intact, total_rows, touch_purge, seen_purge, refreshed_not_seen, seen_not_refreshed FROM loader_seen.discogs_releases_shadow(%s)",
            (started_at,),
        )

        # MusicBrainz: RELATIONSHIPS artist-artist rows, loaded once, then reloaded unchanged
        # three ways. Each run starts from rows last written a month ago.
        rows = [(uuid.UUID(int=i % 50_000 + 1), uuid.UUID(int=10**9 + i), f"type{i % 7}", str(1960 + i % 60)) for i in range(relationships)]
        rels = [tuple(list(column) for column in zip(*rows[i : i + BATCH * 5], strict=True)) for i in range(0, len(rows), BATCH * 5)]

        def age_relationships() -> None:
            cursor.execute("UPDATE musicbrainz.relationships SET updated_at = now() - interval '30 days'")
            cursor.execute("VACUUM (ANALYZE, FREEZE) musicbrainz.relationships")
            cursor.execute("TRUNCATE loader_seen.musicbrainz_relationships")

        for params in rels:
            cursor.execute(REL_DO_NOTHING, params)
        age_relationships()
        run(conn, "mb relationships: touch upsert (today)", "musicbrainz.relationships", rels, [REL_TOUCH])
        age_relationships()
        run(conn, "mb relationships: DO UPDATE ... WHERE changed + append", "musicbrainz.relationships", rels, [REL_NO_OP, REL_APPEND])
        age_relationships()
        run(
            conn,
            "mb relationships: UPDATE changed + INSERT DO NOTHING + append",
            "musicbrainz.relationships",
            rels,
            [REL_UPDATE_CHANGED, REL_DO_NOTHING, REL_APPEND],
        )
        timed(conn, "mb relationships stale", "SELECT * FROM loader_seen.musicbrainz_relationships_stale(%s)", (started_at,))

        # MusicBrainz entities: ENTITIES releases-wide rows, reloaded unchanged three ways.
        payload = json.dumps({"tracks": [{"title": "x" * 40, "length": 1000 + n} for n in range(12)]})
        entity_rows = [(uuid.UUID(int=2 * 10**9 + i), f"Release {i}", "Official", payload) for i in range(entities)]
        mb = [tuple(list(column) for column in zip(*entity_rows[i : i + BATCH], strict=True)) for i in range(0, len(entity_rows), BATCH)]
        for params in mb:
            cursor.execute(MB_INSERT_NEW, params)

        def age_entities() -> None:
            cursor.execute("ALTER TABLE musicbrainz.releases DISABLE TRIGGER USER")
            cursor.execute("UPDATE musicbrainz.releases SET updated_at = now() - interval '30 days'")
            cursor.execute("ALTER TABLE musicbrainz.releases ENABLE TRIGGER USER")
            cursor.execute("VACUUM (ANALYZE, FREEZE) musicbrainz.releases")

        age_entities()
        report(
            {"mb releases heap bytes per row": one(cursor, "SELECT pg_relation_size('musicbrainz.releases') / count(*) FROM musicbrainz.releases")}
        )
        run(conn, "mb entities: upsert (today)", "musicbrainz.releases", mb, [MB_TOUCH])
        age_entities()
        run(conn, "mb entities: DO UPDATE ... WHERE changed", "musicbrainz.releases", mb, [MB_LOCKING_NO_OP])
        age_entities()
        run(conn, "mb entities: UPDATE changed + INSERT DO NOTHING", "musicbrainz.releases", mb, [MB_UPDATE_CHANGED, MB_INSERT_NEW])


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]))

"""Measure K=50 storage on synthetic rows in an explicitly disposable test database.

Run against a fresh local PostgreSQL 19 container, never a catalog database. Only scalar
measurements are printed; synthetic ids and scores are never exported.
"""

import asyncio
import json
import os

import psycopg

from groovemap_schema.postgres import _ARTIST_SIMILARITY_STATEMENTS


async def main() -> None:
    """Create the new contract, populate one million artist lists, report relation sizes."""
    connection = await psycopg.AsyncConnection.connect(os.environ["SIMILARITY_STORAGE_TEST_DSN"], autocommit=True)
    async with connection, connection.cursor() as cursor:
        # Refuse to replace existing tables; this measurement owns a fresh database only.
        await cursor.execute("SELECT to_regclass('public.artist_embedding_releases'), to_regclass('public.artist_similar_artists')")
        existing = await cursor.fetchone()
        if existing != (None, None):
            raise ValueError("storage measurement requires a fresh disposable database")
        for _name, statement in _ARTIST_SIMILARITY_STATEMENTS:
            await cursor.execute(statement)
        await cursor.execute(
            "INSERT INTO public.artist_embedding_releases (model_version, source_dump_id, source_dump_date, k) "
            "VALUES (%s, 'synthetic-only', DATE '2026-09-01', 50) RETURNING release_id",
            ("synthetic-model-" + "x" * 132,),
        )
        row = await cursor.fetchone()
        assert row is not None
        await cursor.execute(
            """
            INSERT INTO public.artist_similar_artists (release_id, artist_id, similar_artist_ids, scores)
            SELECT %s, (1000000 + artist)::text,
                ARRAY(SELECT (1000000 + ((artist * 53 + rank * 997) %% 9000000))::text FROM generate_series(1, 50) rank),
                ARRAY(SELECT (1.0 - rank / 51.0)::real FROM generate_series(1, 50) rank)
            FROM generate_series(1, 1000000) artist
            """,
            (row[0],),
        )
        await cursor.execute("VACUUM ANALYZE public.artist_similar_artists")
        await cursor.execute(
            "SELECT version(), COUNT(*), AVG(pg_column_size(a)), "
            "pg_table_size('public.artist_similar_artists'), pg_indexes_size('public.artist_similar_artists'), "
            "pg_total_relation_size('public.artist_similar_artists') FROM public.artist_similar_artists a"
        )
        result = await cursor.fetchone()
        assert result is not None
        print(
            json.dumps(
                dict(zip(["version", "artists", "avg_row_bytes", "table_bytes", "index_bytes", "total_bytes"], result, strict=True)), default=str
            )
        )


if __name__ == "__main__":
    asyncio.run(main())

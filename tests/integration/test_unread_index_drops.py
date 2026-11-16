"""Real-engine proof that schema-init retires the unread indexes and keeps everything else.

`docs/audits/2026-11-14-unread-index-drops.md` names each index. A fresh database must never build
one, a database that holds one (the previous release's shape, recreated below from its original
`CREATE INDEX`) must lose it on the next schema-init, and a rerun must change nothing. The reads
that remained are planned over the real schema to show none of them leaned on a retired index.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg import sql

from groovemap_schema import initializer
from groovemap_schema.postgres import _UNREAD_INDEX_DROPS, _execute_schema_statements, _schema_statements


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# The previous release's CREATE INDEX for each retired index, which is what an upgraded database holds.
_PREVIOUS_RELEASE: dict[str, str] = {
    "public.idx_artists_hash": "CREATE INDEX idx_artists_hash ON public.artists (hash)",
    "public.idx_labels_hash": "CREATE INDEX idx_labels_hash ON public.labels (hash)",
    "public.idx_masters_hash": "CREATE INDEX idx_masters_hash ON public.masters (hash)",
    "public.idx_releases_hash": "CREATE INDEX idx_releases_hash ON public.releases (hash)",
    "public.idx_artists_name": "CREATE INDEX idx_artists_name ON public.artists USING hash ((data->>'name'))",
    "public.idx_labels_name": "CREATE INDEX idx_labels_name ON public.labels USING hash ((data->>'name'))",
    "public.idx_masters_title": "CREATE INDEX idx_masters_title ON public.masters USING hash ((data->>'title'))",
    "public.idx_releases_title": "CREATE INDEX idx_releases_title ON public.releases USING hash ((data->>'title'))",
    "public.idx_releases_labels": "CREATE INDEX idx_releases_labels ON public.releases USING GIN ((data->'labels'))",
    "public.idx_releases_identifiers": "CREATE INDEX idx_releases_identifiers ON public.releases USING GIN ((data->'identifiers'))",
    "public.idx_releases_companies": "CREATE INDEX idx_releases_companies ON public.releases USING GIN ((data->'companies'))",
    "insights.idx_release_rarity_gem": "CREATE INDEX idx_release_rarity_gem ON insights.release_rarity (hidden_gem_score DESC NULLS LAST)",
    "musicbrainz.idx_mb_artists_name": "CREATE INDEX idx_mb_artists_name ON musicbrainz.artists USING hash (name)",
    "musicbrainz.idx_mb_labels_name": "CREATE INDEX idx_mb_labels_name ON musicbrainz.labels USING hash (name)",
    "musicbrainz.idx_mb_links_mbid": "CREATE INDEX idx_mb_links_mbid ON musicbrainz.external_links (mbid)",
    "graph.track_by_artist_reverse": "CREATE INDEX track_by_artist_reverse ON graph.track_by_artist (artist_id, release_id, track_ordinal, sub_track_ordinal)",
    # gm-database-schema-ntr5
    "graph.credited_to_company_id_hash": "CREATE INDEX credited_to_company_id_hash ON graph.credited_to USING hash (company_id)",
    "graph.person_name_trgm": "CREATE INDEX person_name_trgm ON graph.person USING gin (name gin_trgm_ops)",
    "graph.person_name_hash": "CREATE INDEX person_name_hash ON graph.person USING hash (name)",
    "graph.company_company_id_hash": "CREATE INDEX company_company_id_hash ON graph.company USING hash (company_id)",
    "public.idx_releases_year": "CREATE INDEX idx_releases_year ON public.releases ((data->>'year'))",
    "public.idx_masters_year": "CREATE INDEX idx_masters_year ON public.masters ((data->>'year'))",
    "public.idx_releases_genres": "CREATE INDEX idx_releases_genres ON public.releases USING GIN ((data->'genres'))",
    "public.idx_releases_media_families": "CREATE INDEX idx_releases_media_families ON public.releases USING GIN ((media->'families'))",
}

# Indexes that look like these but have a reader or a documented rare path, and must survive.
_KEPT = (
    "public.idx_releases_gm_item_id",  # catalog_merge recompute: WHERE gm_item_id = ANY(...)
    "public.idx_artists_gm_item_id",
    "public.idx_masters_gm_item_id",
    "public.idx_labels_gm_item_id",
    "public.idx_releases_country",
    "insights.idx_release_rarity_artist_ids",  # documented containment route, deploy-gated
    "graph.by_artist_reverse",
    "graph.same_as_person_name_hash",  # the embedding pipeline joins the credits to same_as by name
    "graph.person_key",  # the unique free-text keys: every loader's ON CONFLICT
    "graph.company_key",
    "graph.credited_to_key",
)


def _params(dbname: str) -> dict[str, Any]:
    return {**initializer._postgres_connection_params(), "dbname": dbname}


async def _rows(dbname: str, query: Any, parameters: Any = None) -> list[tuple[Any, ...]]:
    async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as connection:
        cursor = await connection.execute(query, parameters)
        return await cursor.fetchall() if cursor.description else []


async def _init(dbname: str) -> None:
    async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as connection, connection.cursor() as cursor:
        _success, failures = await _execute_schema_statements(cursor, _schema_statements())
    assert failures == 0


async def _present(dbname: str, names: tuple[str, ...]) -> dict[str, bool]:
    return {name: (await _rows(dbname, "SELECT to_regclass(%s) IS NOT NULL", (name,)))[0][0] for name in names}


@pytest_asyncio.fixture
async def database() -> AsyncIterator[str]:
    dbname = f"unread_idx_{uuid4().hex[:12]}"
    await _rows("postgres", sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    try:
        yield dbname
    finally:
        await _rows("postgres", sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname)))


async def test_every_retired_index_has_its_previous_release_statement() -> None:
    assert set(_PREVIOUS_RELEASE) == set(_UNREAD_INDEX_DROPS)


async def test_a_fresh_database_never_builds_a_retired_index(database: str) -> None:
    await _init(database)
    assert not any((await _present(database, _UNREAD_INDEX_DROPS)).values())
    assert all((await _present(database, _KEPT)).values())


async def test_an_upgrade_from_the_previous_release_drops_exactly_the_retired_indexes(database: str) -> None:
    await _init(database)
    for statement in _PREVIOUS_RELEASE.values():
        await _rows(database, statement)
    assert all((await _present(database, _UNREAD_INDEX_DROPS)).values())
    before = {name for (name,) in await _rows(database, "SELECT schemaname || '.' || indexname FROM pg_indexes")}

    await _init(database)

    after = {name for (name,) in await _rows(database, "SELECT schemaname || '.' || indexname FROM pg_indexes")}
    assert before - after == set(_UNREAD_INDEX_DROPS)
    assert after - before == set()
    assert all((await _present(database, _KEPT)).values())

    await _init(database)  # a rerun is a no-op
    assert {name for (name,) in await _rows(database, "SELECT schemaname || '.' || indexname FROM pg_indexes")} == after


async def test_the_reads_that_remain_do_not_need_a_retired_index(database: str) -> None:
    """The loaders read an entity row by primary key and the track relation by its key, never by hash."""
    await _init(database)
    await _rows(
        database,
        """INSERT INTO public.releases (data_id, hash, data) SELECT g::text, md5(g::text), jsonb_build_object('title', 't' || g)
           FROM generate_series(1, 20000) AS g""",
    )
    await _rows(
        database,
        """INSERT INTO graph.track_by_artist (release_id, track_ordinal, sub_track_ordinal, artist_id)
           SELECT (g % 5000)::text, g, 0, (g % 700)::text FROM generate_series(1, 20000) AS g""",
    )
    await _rows(database, "ANALYZE public.releases")
    await _rows(database, "ANALYZE graph.track_by_artist")
    plans = {
        "entity by key": "SELECT data_id, hash FROM public.releases WHERE data_id = ANY(ARRAY['7', '8', '9'])",
        "track relation by release": "SELECT artist_id FROM graph.track_by_artist WHERE release_id = '7'",
    }
    for label, query in plans.items():
        plan = "\n".join(line for (line,) in await _rows(database, "EXPLAIN " + query))
        assert "Index" in plan and "Seq Scan" not in plan, f"{label}: {plan}"


async def test_the_free_text_reads_that_remain_use_the_keys_and_the_same_as_lookup(database: str) -> None:
    """gm-database-schema-ntr5: what still reads `person`, `company`, `credited_to` and `same_as`.

    The Discogs loader deletes `credited_to` by `release_id` and `source`, which leads the unique key;
    every writer's ON CONFLICT probes the `<relation>_key` index; and the embedding pipeline joins the
    credits to `same_as` by `person_name`, which is the one lookup index that stays.
    """
    await _init(database)
    await _rows(database, "INSERT INTO graph.person (name) SELECT 'Person ' || g FROM generate_series(1, 20000) AS g")
    await _rows(database, "INSERT INTO graph.company (company_id, name) SELECT g::text, 'Company ' || g FROM generate_series(1, 20000) AS g")
    await _rows(
        database,
        """INSERT INTO graph.credited_to (release_id, company_id, role, source)
           SELECT (g % 5000)::text, (g % 900)::text, 'Pressed By', 'discogs' FROM generate_series(1, 20000) AS g""",
    )
    await _rows(
        database,
        "INSERT INTO graph.same_as (person_name, artist_id) SELECT 'Person ' || g, g::text FROM generate_series(1, 20000) AS g",
    )
    for relation in ("person", "company", "credited_to", "same_as"):
        await _rows(database, f"ANALYZE graph.{relation}")
    plans = {
        "credited_to delete by release": (
            "credited_to_key",
            "SELECT 1 FROM graph.credited_to WHERE release_id = ANY(ARRAY['7', '8']) AND source = 'discogs'",
        ),
        "person by key": ("person_key", "SELECT 1 FROM graph.person WHERE graph.text_key(name) = graph.text_key('Person 42')"),
        "company by key": ("company_key", "SELECT 1 FROM graph.company WHERE graph.text_key(company_id) = graph.text_key('42')"),
        "same_as by name": ("same_as_person_name_hash", "SELECT artist_id FROM graph.same_as WHERE person_name = 'Person 42'"),
    }
    for label, (index, query) in plans.items():
        plan = "\n".join(line for (line,) in await _rows(database, "EXPLAIN " + query))
        assert index in plan and "Seq Scan" not in plan, f"{label}: {plan}"

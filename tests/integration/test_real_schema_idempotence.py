"""Real-engine schema initialization and idempotence proof."""

from __future__ import annotations

import asyncio
import random
from datetime import date
from typing import Any
from uuid import uuid4

import psycopg
import pytest
from common.credit_roles import categorize_role
from common.media import medium_ids, medium_label
from neo4j import AsyncGraphDatabase
from psycopg import sql
from psycopg.types.json import Jsonb

from groovemap_schema import initializer
from groovemap_schema.neo4j import SCHEMA_STATEMENTS
from groovemap_schema.postgres import (
    _EDGE_TABLES,
    _GRAPH_STATEMENTS,
    _VERTEX_KIND_NAMES,
    ARTIST_EMBEDDINGS_HNSW_INDEX_NAME,
    EMBEDDING_PIPELINE_ROLE,
    MUSICBRAINZ_RELATIONSHIP_TYPES,
    VECTOR_EXTENSION,
    _widen_to_bigint,
    build_artist_embeddings_index,
    build_artist_embeddings_version_index,
    graph_bootstrap_statements,
    phase0_comparison_statements,
    publish_artist_embedding_release,
    retire_artist_embeddings_version,
    retire_artist_similar_artists_version,
)


pytestmark = pytest.mark.integration


EXPECTED_POSTGRES_TABLES = {
    "public": {
        "admin_audit_log",
        "app_config",
        "app_tokens",
        "artifacts",
        "artists",
        "artist_embedding_releases",
        "artist_similar_artists",
        "catalog_item_moves",
        "catalog_item_supersessions",
        "catalog_items",
        "collection_snapshots",
        "extraction_history",
        "labels",
        "loader_extraction_latch",
        "loader_derived_refresh_cursor",
        "loader_derived_refresh_job",
        "masters",
        "oauth_tokens",
        "observations",
        "owned_copies",
        "provider_aliases",
        "queue_metrics",
        "releases",
        "service_health_metrics",
        "sync_history",
        "user_collections",
        "user_wantlists",
        "users",
    },
    "insights": {
        "activity_summary",
        "artist_centrality",
        "community_counts",
        "computation_log",
        "data_completeness",
        "genre_trends",
        "label_longevity",
        "monthly_anniversaries",
        "release_rarity",
    },
    "musicbrainz": {
        "artists",
        "external_links",
        "labels",
        "relationships",
        "release_groups",
        "releases",
    },
}

EXPECTED_COLUMNS = {
    ("public", "releases", "media", "jsonb"),
    ("public", "user_collections", "media", "jsonb"),
    ("public", "user_collections", "metadata", "jsonb"),
    ("public", "user_wantlists", "media", "jsonb"),
    ("insights", "release_rarity", "family_signals", "jsonb"),
    ("insights", "release_rarity", "media_families", "jsonb"),
    ("musicbrainz", "artists", "discogs_artist_id", "bigint"),
    ("musicbrainz", "labels", "discogs_label_id", "bigint"),
    ("musicbrainz", "release_groups", "discogs_master_id", "bigint"),
    ("musicbrainz", "releases", "discogs_release_id", "bigint"),
    ("musicbrainz", "releases", "media", "jsonb"),
    ("musicbrainz", "relationships", "updated_at", "timestamp with time zone"),
    ("musicbrainz", "external_links", "updated_at", "timestamp with time zone"),
    # discogs-sql-loader's startup probe requires exactly these `information_schema`
    # types (`REQUIRED_COLUMNS` in `tableinator/extraction_latch.py`) or it declines
    # the relation and runs degraded.
    ("public", "loader_extraction_latch", "loader", "text"),
    ("public", "loader_extraction_latch", "version", "text"),
    ("public", "loader_extraction_latch", "signals", "ARRAY"),
    ("public", "loader_extraction_latch", "created_at", "timestamp with time zone"),
    ("public", "loader_extraction_latch", "updated_at", "timestamp with time zone"),
    ("public", "loader_extraction_latch", "refreshed_at", "timestamp with time zone"),
    ("public", "loader_extraction_latch", "generation", "bigint"),
    ("public", "loader_derived_refresh_cursor", "loader", "text"),
    ("public", "loader_derived_refresh_cursor", "generation", "bigint"),
    ("public", "loader_derived_refresh_cursor", "version", "text"),
    ("public", "loader_derived_refresh_job", "loader", "text"),
    ("public", "loader_derived_refresh_job", "version", "text"),
    ("public", "loader_derived_refresh_job", "generation", "bigint"),
    ("public", "loader_derived_refresh_job", "state", "text"),
    ("public", "loader_derived_refresh_job", "attempt_count", "integer"),
    ("public", "loader_derived_refresh_job", "next_attempt_at", "timestamp with time zone"),
    ("public", "loader_derived_refresh_job", "lease_owner", "text"),
    ("public", "loader_derived_refresh_job", "lease_token", "uuid"),
    ("public", "loader_derived_refresh_job", "lease_epoch", "bigint"),
    ("public", "loader_derived_refresh_job", "lease_expires_at", "timestamp with time zone"),
    ("public", "loader_derived_refresh_job", "last_error", "text"),
    ("public", "loader_derived_refresh_job", "started_at", "timestamp with time zone"),
    ("public", "loader_derived_refresh_job", "completed_at", "timestamp with time zone"),
    ("public", "loader_derived_refresh_job", "superseded_at", "timestamp with time zone"),
    # catalog-api codes its merge steps to ADR 0009's 2026-09-25 amendment, so
    # the supersession and ledger columns are pinned at their engine types.
    ("public", "catalog_item_supersessions", "superseded_id", "uuid"),
    ("public", "catalog_item_supersessions", "survivor_id", "uuid"),
    ("public", "catalog_item_supersessions", "cause", "text"),
    ("public", "catalog_item_supersessions", "decision_ref", "uuid"),
    ("public", "catalog_item_supersessions", "valid_from", "timestamp with time zone"),
    ("public", "catalog_item_supersessions", "valid_to", "timestamp with time zone"),
    ("public", "catalog_item_supersessions", "via_id", "uuid"),
    ("public", "catalog_item_moves", "supersession_id", "uuid"),
    ("public", "catalog_item_moves", "table_name", "text"),
    ("public", "catalog_item_moves", "row_id", "uuid"),
    ("public", "catalog_item_moves", "from_item_id", "uuid"),
    ("public", "catalog_item_moves", "to_item_id", "uuid"),
    ("public", "catalog_item_moves", "user_id", "uuid"),
    ("public", "catalog_item_moves", "moved_at", "timestamp with time zone"),
}

# The endpoint-pair indexes every one of the sixteen typed MusicBrainz
# relationship views filters on, in both directions.
EXPECTED_MUSICBRAINZ_INDEXES = {
    ("musicbrainz", "relationships", "idx_mb_rels_endpoint_source"),
    ("musicbrainz", "relationships", "idx_mb_rels_endpoint_target"),
    ("musicbrainz", "relationships", "idx_mb_rels_type"),
    ("musicbrainz", "artists", "idx_mb_artists_discogs_id"),
    ("musicbrainz", "relationships", "idx_mb_rels_updated_at"),
    ("musicbrainz", "external_links", "idx_mb_links_updated_at"),
}

# Native identity keys rely on the engine's built-in uuidv7(), which both the required
# PostgreSQL 18 tier and the advisory PostgreSQL 19 beta tier must render identically.
EXPECTED_UUIDV7_DEFAULTS = {
    ("public", "artifacts", "id"),
    ("public", "catalog_item_moves", "id"),
    ("public", "catalog_item_supersessions", "id"),
    ("public", "catalog_items", "id"),
    ("public", "collection_snapshots", "id"),
    ("public", "observations", "id"),
    ("public", "owned_copies", "id"),
    ("public", "provider_aliases", "id"),
}


# The schemas whose catalogs the snapshot and the expectations cover.
SCHEMAS = ("public", "insights", "musicbrainz", "graph")

# Every graph relation the initializer declares, split by the shape it declares.
EXPECTED_GRAPH_VIEWS = {name.removeprefix("graph.").removesuffix(" view") for name, _statement in _GRAPH_STATEMENTS if name.endswith(" view")}
EXPECTED_GRAPH_TABLES = {name.removeprefix("graph.").removesuffix(" table") for name, _statement in _GRAPH_STATEMENTS if name.endswith(" table")}
EXPECTED_GRAPH_RELATIONS = EXPECTED_GRAPH_VIEWS | EXPECTED_GRAPH_TABLES

# The schema holding the retained phase 0 view definitions. It is created by the
# parity test alone; the initializer never emits it and a deployed database
# never carries it.
PHASE0_SCHEMA = "graph_phase0"

# The functions rendered from the runtime's credit-role and media taxonomies.
EXPECTED_GRAPH_FUNCTIONS = {
    name.removeprefix("graph.").removesuffix(" function") for name, _statement in _GRAPH_STATEMENTS if name.endswith(" function")
}

# The key columns the property graph joins on, with the type each must resolve
# to on a real engine. A Discogs id read out of JSONB has to land as text so it
# joins `releases.data_id`; a MusicBrainz key has to stay a uuid; a collection
# edge has to have cast its BIGINT release id down to text.
EXPECTED_GRAPH_COLUMNS = {
    # Every key the property graph joins on is text. PostgreSQL 19 beta 3
    # rejects an edge whose endpoint resolves to a `character varying` vertex
    # key, and the four appended `<entity>_key` restatements that used to work
    # around that are retired: the published key is text itself now.
    ("graph", "artist", "artist_id", "text"),
    ("graph", "label", "label_id", "text"),
    ("graph", "master", "master_id", "text"),
    ("graph", "release", "release_id", "text"),
    ("graph", "release", "media_families", "ARRAY"),
    ("graph", "release", "formats", "ARRAY"),
    ("graph", "release", "catalog_number", "text"),
    ("graph", "artist", "gm_id", "uuid"),
    ("graph", "release", "gm_id", "uuid"),
    ("graph", "genre", "name", "text"),
    ("graph", "style", "name", "text"),
    ("graph", "person", "name", "text"),
    ("graph", "company", "company_id", "text"),
    ("graph", "medium", "medium_id", "text"),
    ("graph", "media_family", "name", "text"),
    ("graph", "by_artist", "release_id", "text"),
    ("graph", "by_artist", "artist_id", "text"),
    ("graph", "on_label", "label_id", "text"),
    ("graph", "derived_from", "master_id", "text"),
    ("graph", "in_genre", "genre_name", "text"),
    ("graph", "credited_on", "role_category", "text"),
    ("graph", "credited_to", "source", "text"),
    ("graph", "issued_on", "source", "text"),
    ("graph", "issued_on", "qty", "bigint"),
    ("graph", "part_of", "style_name", "text"),
    ("graph", "part_of", "genre_name", "text"),
    ("graph", "member_of", "member_artist_id", "text"),
    ("graph", "member_of", "group_artist_id", "text"),
    ("graph", "sublabel_of", "sublabel_id", "text"),
    ("graph", "sublabel_of", "parent_label_id", "text"),
    ("graph", "genre_stats", "release_count", "bigint"),
    ("graph", "genre_stats", "first_year", "integer"),
    ("graph", "style_stats", "genre_count", "bigint"),
    ("graph", "label_stats", "label_id", "text"),
    ("graph", "artist_degree", "degree", "bigint"),
    # The four projections each label binds, carrying its counters as
    # properties exactly as the Neo4j node of the same name carries them.
    ("graph", "genre_vertex", "name", "text"),
    ("graph", "genre_vertex", "release_count", "bigint"),
    ("graph", "genre_vertex", "style_count", "bigint"),
    ("graph", "genre_vertex", "first_year", "integer"),
    ("graph", "style_vertex", "genre_count", "bigint"),
    ("graph", "style_vertex", "first_year", "integer"),
    ("graph", "label_vertex", "label_id", "text"),
    ("graph", "label_vertex", "genre_count", "bigint"),
    ("graph", "artist_vertex", "artist_id", "text"),
    ("graph", "artist_vertex", "degree", "bigint"),
    ("graph", "release_degree_base", "degree", "bigint"),
    # The per-vertex degree. `kind` is the one-byte internal type on purpose:
    # the whole argument for a 97 MB relation is that a vertex costs a byte
    # and a bigint rather than a row of text.
    ("graph", "vertex_degree", "kind", '"char"'),
    ("graph", "vertex_degree", "key", "text"),
    ("graph", "vertex_degree", "degree", "bigint"),
    ("graph", "release_degree", "degree", "bigint"),
    ("graph", "artist_genre", "genre_name", "text"),
    ("graph", "label_genre", "label_id", "text"),
    ("graph", "mb_artist", "mbid", "uuid"),
    ("graph", "mb_release", "mbid", "uuid"),
    # The native id each MusicBrainz vertex shares, by name and type, with the
    # Discogs vertices (design ADR 0012's amendment, section 3).
    ("graph", "mb_artist", "gm_item_id", "uuid"),
    ("graph", "mb_label", "gm_item_id", "uuid"),
    ("graph", "mb_release", "gm_item_id", "uuid"),
    ("graph", "mb_release_group", "gm_item_id", "uuid"),
    ("graph", "mb_rel_artist_artist", "source_mbid", "uuid"),
    ("graph", "mb_rel_artist_artist", "target_mbid", "uuid"),
    ("graph", "mb_rel_artist_artist", "relationship_type", "text"),
    ("graph", "mb_rel_artist_artist", "raw_relationship_type", "text"),
    ("graph", "mb_rel_artist_artist", "attributes", "jsonb"),
    ("graph", "app_user", "user_id", "uuid"),
    ("graph", "catalog_item", "item_id", "uuid"),
    ("graph", "collected", "user_id", "uuid"),
    ("graph", "collected", "release_id", "character varying"),
    ("graph", "collected", "instance_id", "bigint"),
    ("graph", "wants", "release_id", "character varying"),
    ("graph", "owns", "item_id", "uuid"),
}


async def postgres_rows(query: str, parameters: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Query the disposable target database through the production connection settings."""
    connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params())
    async with connection, connection.cursor() as cursor:
        await cursor.execute(query, parameters)
        return await cursor.fetchall()


async def postgres_snapshot() -> tuple[tuple[Any, ...], ...]:
    """Capture stable table, column, constraint, and index definitions."""
    rows = await postgres_rows(
        """
        SELECT 'column', table_schema, table_name, column_name,
               data_type, is_nullable, COALESCE(column_default, '')
        FROM information_schema.columns
        WHERE table_schema IN ('public', 'insights', 'musicbrainz', 'graph')
        UNION ALL
        SELECT 'constraint', namespace.nspname, table_class.relname,
               constraint_row.conname, constraint_row.contype::text,
               pg_get_constraintdef(constraint_row.oid), ''
        FROM pg_constraint AS constraint_row
        JOIN pg_class AS table_class ON table_class.oid = constraint_row.conrelid
        JOIN pg_namespace AS namespace ON namespace.oid = table_class.relnamespace
        WHERE namespace.nspname IN ('public', 'insights', 'musicbrainz', 'graph')
        UNION ALL
        SELECT 'index', schemaname, tablename, indexname, indexdef, '', ''
        FROM pg_indexes
        WHERE schemaname IN ('public', 'insights', 'musicbrainz', 'graph')
        UNION ALL
        SELECT 'view', schemaname, viewname, definition, '', '', ''
        FROM pg_views
        WHERE schemaname = 'graph'
        ORDER BY 1, 2, 3, 4
        """
    )
    return tuple(rows)


async def assert_every_edge_table_is_indexed_both_ways() -> None:
    """Assert each edge table carries its primary key and a reverse-pair index."""
    for relation, _columns, reverse, _extras in _EDGE_TABLES:
        rows = await postgres_rows(
            "SELECT indexdef FROM pg_indexes WHERE schemaname = 'graph' AND tablename = %s",
            (relation,),
        )
        definitions = [row[0] for row in rows]
        assert any(definition.endswith(f"({reverse})") for definition in definitions), (relation, definitions)
        assert any("_pkey" in definition for definition in definitions), (relation, definitions)


async def assert_expected_postgres_schema() -> None:
    """Assert the cross-service tables and high-risk additive columns exist."""
    table_rows = await postgres_rows(
        """
        SELECT table_schema, table_name
        FROM information_schema.tables
        WHERE table_type = 'BASE TABLE'
          AND table_schema IN ('public', 'insights', 'musicbrainz')
        """
    )
    actual_tables: dict[str, set[str]] = {schema: set() for schema in EXPECTED_POSTGRES_TABLES}
    for schema, table in table_rows:
        actual_tables[schema].add(table)
    # `public.artist_embeddings` exists only where pgvector does; see
    # `assert_the_artist_embeddings_table_matches_the_server` for the rest of
    # its shape.
    expected_tables = {schema: set(tables) for schema, tables in EXPECTED_POSTGRES_TABLES.items()}
    if await vector_extension_present():
        expected_tables["public"].add("artist_embeddings")
    assert actual_tables == expected_tables

    column_rows = await postgres_rows(
        """
        SELECT table_schema, table_name, column_name, data_type
        FROM information_schema.columns
        WHERE table_schema IN ('public', 'insights', 'musicbrainz', 'graph')
        """
    )
    assert set(column_rows) >= EXPECTED_COLUMNS
    uuidv7_rows = await postgres_rows(
        """
        SELECT table_schema, table_name, column_name
        FROM information_schema.columns
        WHERE table_schema IN ('public', 'insights', 'musicbrainz')
          AND column_default = 'uuidv7()'
        """
    )
    assert set(uuidv7_rows) == EXPECTED_UUIDV7_DEFAULTS

    assert set(column_rows) >= EXPECTED_GRAPH_COLUMNS

    view_rows = await postgres_rows("SELECT viewname FROM pg_views WHERE schemaname = 'graph'")
    assert {row[0] for row in view_rows} == EXPECTED_GRAPH_VIEWS

    # Every relation the spike turned into a table is a table on the engine, not
    # a view that happens to have the right columns.
    graph_table_rows = await postgres_rows(
        "SELECT table_name FROM information_schema.tables WHERE table_schema = 'graph' AND table_type = 'BASE TABLE'"
    )
    assert {row[0] for row in graph_table_rows} == EXPECTED_GRAPH_TABLES

    index_rows = await postgres_rows("SELECT schemaname, tablename, indexname FROM pg_indexes WHERE schemaname = 'musicbrainz'")
    assert set(index_rows) >= EXPECTED_MUSICBRAINZ_INDEXES

    # Both directions of every edge table are indexed. The primary key serves
    # the forward walk; a table indexed only that way is a sequential scan in
    # the other direction, which is the failure the views already had.
    await assert_every_edge_table_is_indexed_both_ways()

    # The rendered relationship map answers with the Neo4j vocabulary a ported
    # Cypher query asks for, and with NULL where the enricher writes no edge.
    for raw, mapped in MUSICBRAINZ_RELATIONSHIP_TYPES.items():
        answer = await postgres_rows("SELECT graph.mb_relationship_type(%s)", (raw,))
        assert answer == [(mapped,)], raw
    assert await postgres_rows("SELECT graph.mb_relationship_type('not a relationship')") == [(None,)]
    assert await postgres_rows("SELECT graph.mb_relationship_type(NULL)") == [(None,)]

    function_rows = await postgres_rows(
        """
        SELECT routine_name FROM information_schema.routines
        WHERE routine_schema = 'graph'
        """
    )
    assert {row[0] for row in function_rows} == EXPECTED_GRAPH_FUNCTIONS

    # The rendered taxonomies have to agree with the Python they were built from,
    # on the engine rather than only in the statement text.
    role_answers = await postgres_rows(
        """
        SELECT graph.credit_role_category('Producer'),
               graph.credit_role_category('  RECORDED BY  '),
               graph.credit_role_category('Recorded By, Mastering Engineer'),
               graph.credit_role_category('Interpretive Dance')
        """
    )
    assert role_answers == [
        (
            categorize_role("Producer"),
            categorize_role("  RECORDED BY  "),
            categorize_role("Recorded By, Mastering Engineer"),
            categorize_role("Interpretive Dance"),
        )
    ]

    sample_medium = sorted(medium_ids())[0]
    label_answers = await postgres_rows("SELECT graph.medium_label(%s), graph.medium_label('not_a_medium')", (sample_medium,))
    assert label_answers == [(medium_label(sample_medium), "not_a_medium")]

    # A view that parses is not yet a view that runs: PostgreSQL only plans the
    # body when it is read. Selecting from every relation proves each one is
    # executable on this engine, which is the whole point of running the suite
    # against two major versions.
    #
    # Executability is the claim, not emptiness. A sibling test seeds fixture
    # rows into the same disposable database, so asserting a zero count here
    # would make this test pass or fail on which of the two pytest happened to
    # run first. Counting and requiring a non-negative answer proves the view
    # planned and ran without borrowing an ordering guarantee the suite does
    # not give.
    for view in sorted(EXPECTED_GRAPH_RELATIONS):
        # `view` comes from the initializer's own statement list, not from input.
        counted = await postgres_rows(f"SELECT count(*) FROM graph.{view}")  # noqa: S608
        assert len(counted) == 1
        assert counted[0][0] >= 0

    relationship_key = await postgres_rows(
        """
        SELECT array_agg(attribute.attname ORDER BY key_column.ordinality)
        FROM pg_constraint AS constraint_row
        JOIN pg_class AS table_class ON table_class.oid = constraint_row.conrelid
        JOIN pg_namespace AS namespace ON namespace.oid = table_class.relnamespace
        CROSS JOIN LATERAL unnest(constraint_row.conkey) WITH ORDINALITY AS key_column(attnum, ordinality)
        JOIN pg_attribute AS attribute
          ON attribute.attrelid = table_class.oid AND attribute.attnum = key_column.attnum
        WHERE namespace.nspname = 'musicbrainz'
          AND table_class.relname = 'relationships'
          AND constraint_row.conname = 'relationships_natural_key'
        """
    )
    assert relationship_key == [
        (
            [
                "source_mbid",
                "target_mbid",
                "source_entity_type",
                "target_entity_type",
                "relationship_type",
                "begin_date",
                "end_date",
                "attributes",
            ],
        )
    ]

    # The loader's startup probe declines the relation unless it carries a
    # primary key (or unique constraint) on exactly its key columns, since its
    # `ON CONFLICT (loader, version)` upsert depends on one existing. This is
    # a `contype = 'p'` check, not a name-only one, so a plain index with the
    # same name would not satisfy it.
    latch_key = await postgres_rows(
        """
        SELECT array_agg(attribute.attname ORDER BY key_column.ordinality)
        FROM pg_constraint AS constraint_row
        JOIN pg_class AS table_class ON table_class.oid = constraint_row.conrelid
        JOIN pg_namespace AS namespace ON namespace.oid = table_class.relnamespace
        CROSS JOIN LATERAL unnest(constraint_row.conkey) WITH ORDINALITY AS key_column(attnum, ordinality)
        JOIN pg_attribute AS attribute
          ON attribute.attrelid = table_class.oid AND attribute.attnum = key_column.attnum
        WHERE namespace.nspname = 'public'
          AND table_class.relname = 'loader_extraction_latch'
          AND constraint_row.contype = 'p'
        """
    )
    assert latch_key == [(["loader", "version"],)]


# ── PostgreSQL server metadata ───────────────────────────────────────────────


async def server_version_num() -> int:
    """Return the connected engine's `server_version_num`."""
    rows = await postgres_rows("SELECT current_setting('server_version_num')::int")
    return int(rows[0][0])


# ── The vector extension, artist embeddings, and the embedding pipeline role ─
# (ADR 0013 and its 2026-09-24 amendment)


async def vector_extension_present() -> bool:
    """Return whether `vector` is installed on this engine.

    The required PostgreSQL 18 tier runs the bare official image and never has
    it; the advisory PostgreSQL 19 tier's local image layers pgvector onto it
    (see `just test-integration-pg19`) and always does.
    """
    rows = await postgres_rows("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = %s)", (VECTOR_EXTENSION,))
    return bool(rows[0][0])


async def artist_embeddings_primary_key() -> list[str]:
    """Return `public.artist_embeddings`'s primary-key columns, in key order."""
    rows = await postgres_rows(
        """
        SELECT array_agg(attribute.attname ORDER BY key_column.ordinality)
        FROM pg_constraint AS constraint_row
        JOIN pg_class AS table_class ON table_class.oid = constraint_row.conrelid
        JOIN pg_namespace AS namespace ON namespace.oid = table_class.relnamespace
        CROSS JOIN LATERAL unnest(constraint_row.conkey) WITH ORDINALITY AS key_column(attnum, ordinality)
        JOIN pg_attribute AS attribute
          ON attribute.attrelid = table_class.oid AND attribute.attnum = key_column.attnum
        WHERE namespace.nspname = 'public'
          AND table_class.relname = 'artist_embeddings'
          AND constraint_row.contype = 'p'
        """
    )
    return [] if not rows or rows[0][0] is None else list(rows[0][0])


async def assert_the_artist_embeddings_table_matches_the_server() -> None:
    """Assert `public.artist_embeddings` exists, with its shape, only where pgvector does."""
    exists = await postgres_rows("SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'artist_embeddings'")
    if not await vector_extension_present():
        assert exists == [], "a server without pgvector must carry no public.artist_embeddings table"
        return
    assert exists == [(1,)]

    columns = await postgres_rows(
        """
        SELECT column_name, is_nullable, udt_name
        FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = 'artist_embeddings'
        """
    )
    assert {(name, nullable) for name, nullable, _udt in columns} == {
        ("artist_id", "NO"),
        ("model_version", "NO"),
        ("embedding", "NO"),
        ("source_dump_id", "NO"),
        ("source_dump_date", "NO"),
        ("computed_at", "NO"),
    }
    assert {udt for name, _nullable, udt in columns if name == "embedding"} == {"halfvec"}

    assert await artist_embeddings_primary_key() == ["artist_id", "model_version"]


async def assert_the_embedding_pipeline_role_matches_the_server() -> None:
    """Assert the pipeline role's grants match the ADR 0013 amendment exactly.

    `CREATEROLE` is always available to the integration credential, so the
    role exists on both tiers; only its grant on `artist_embeddings` (and the
    table itself) depends on pgvector.
    """
    role_rows = await postgres_rows("SELECT rolcanlogin FROM pg_roles WHERE rolname = %s", (EMBEDDING_PIPELINE_ROLE,))
    assert role_rows == [(False,)], "the pipeline role must be NOLOGIN"

    for privilege in ("INSERT", "UPDATE", "DELETE", "SELECT"):
        catalog_rows = await postgres_rows("SELECT has_table_privilege(%s, 'public.artists', %s)", (EMBEDDING_PIPELINE_ROLE, privilege))
        assert catalog_rows == [(False,)], f"the pipeline role must not hold {privilege} on a catalog table"

    graph_read = await postgres_rows("SELECT has_table_privilege(%s, 'graph.artist', 'SELECT')", (EMBEDDING_PIPELINE_ROLE,))
    assert graph_read == [(True,)]
    graph_write = await postgres_rows("SELECT has_table_privilege(%s, 'graph.artist', 'INSERT')", (EMBEDDING_PIPELINE_ROLE,))
    assert graph_write == [(False,)]

    # `artist_similar_artists` and `artist_embedding_releases` are created unconditionally
    # (see the module comment above `_ARTIST_SIMILARITY_STATEMENTS` in postgres.py), so this
    # grant, unlike the one on `artist_embeddings` below, holds on both tiers.
    for table in ("public.artist_similar_artists", "public.artist_embedding_releases"):
        for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
            similarity_rows = await postgres_rows("SELECT has_table_privilege(%s, %s, %s)", (EMBEDDING_PIPELINE_ROLE, table, privilege))
            assert similarity_rows == [(True,)], f"the pipeline role must hold {privilege} on {table}"

    if not await vector_extension_present():
        return

    for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE"):
        embedding_rows = await postgres_rows("SELECT has_table_privilege(%s, 'public.artist_embeddings', %s)", (EMBEDDING_PIPELINE_ROLE, privilege))
        assert embedding_rows == [(True,)], f"the pipeline role must hold {privilege} on artist_embeddings"


async def neo4j_rows(query: str) -> list[tuple[Any, ...]]:
    """Query the disposable Neo4j through the production connection settings."""
    driver = AsyncGraphDatabase.driver(
        initializer.NEO4J_URI,
        auth=(initializer.NEO4J_USERNAME, initializer.NEO4J_PASSWORD),
    )
    try:
        async with driver.session(database="neo4j") as session:
            result = await session.run(query)
            return [tuple(record.values()) async for record in result]
    finally:
        await driver.close()


async def neo4j_snapshot() -> tuple[tuple[Any, ...], ...]:
    """Capture stable constraint and index metadata, excluding online state."""
    constraints = await neo4j_rows(
        """
        SHOW CONSTRAINTS YIELD name, type, entityType, labelsOrTypes, properties
        RETURN 'constraint', name, type, entityType, labelsOrTypes, properties
        ORDER BY name
        """
    )
    indexes = await neo4j_rows(
        """
        SHOW INDEXES YIELD name, type, entityType, labelsOrTypes, properties, owningConstraint
        RETURN 'index', name, type, entityType, labelsOrTypes, properties, owningConstraint
        ORDER BY name
        """
    )
    return tuple(constraints + indexes)


async def assert_expected_neo4j_schema() -> None:
    """Assert every declared named schema object exists on the real engine."""
    expected = {name for name, _statement in SCHEMA_STATEMENTS}
    rows = await neo4j_snapshot()
    actual = {row[1] for row in rows}
    assert expected <= actual


async def apply_schema() -> None:
    """Run the same store initializers the one-shot command uses."""
    parameters = initializer._postgres_connection_params()
    initializer._ensure_postgres_database(parameters)
    postgres_ok, neo4j_ok = await asyncio.gather(
        initializer._init_postgres(parameters),
        initializer._init_neo4j(),
    )
    assert postgres_ok is True
    assert neo4j_ok is True


async def seed_sentinels() -> None:
    """Write data whose survival proves the second pass is non-destructive."""
    connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params())
    async with connection, connection.cursor() as cursor:
        await cursor.execute(
            "INSERT INTO app_config (key, value) VALUES (%s, %s)",
            ("integration-sentinel", "survives-second-pass"),
        )
        await connection.commit()

    driver = AsyncGraphDatabase.driver(
        initializer.NEO4J_URI,
        auth=(initializer.NEO4J_USERNAME, initializer.NEO4J_PASSWORD),
    )
    try:
        async with driver.session(database="neo4j") as session:
            result = await session.run("CREATE (:IntegrationSentinel {value: 'survives-second-pass'})")
            await result.consume()
    finally:
        await driver.close()


async def assert_sentinels_survive() -> None:
    postgres = await postgres_rows("SELECT value FROM app_config WHERE key = 'integration-sentinel'")
    neo4j = await neo4j_rows("MATCH (node:IntegrationSentinel) RETURN node.value")
    assert postgres == [("survives-second-pass",)]
    assert neo4j == [("survives-second-pass",)]


@pytest.mark.asyncio
async def test_both_schema_initializers_are_real_engine_idempotent() -> None:
    await apply_schema()
    await assert_expected_postgres_schema()
    await assert_expected_neo4j_schema()
    await assert_the_artist_embeddings_table_matches_the_server()
    await assert_the_embedding_pipeline_role_matches_the_server()
    first_postgres = await postgres_snapshot()
    first_neo4j = await neo4j_snapshot()
    await seed_sentinels()

    await apply_schema()
    await assert_expected_postgres_schema()
    await assert_expected_neo4j_schema()
    await assert_the_artist_embeddings_table_matches_the_server()
    await assert_the_embedding_pipeline_role_matches_the_server()

    assert await postgres_snapshot() == first_postgres
    assert await neo4j_snapshot() == first_neo4j
    await assert_sentinels_survive()


# ── The artist embeddings HNSW index and its build procedure (ADR 0013) ──────
# Runs on both tiers, but only the advisory PostgreSQL 19 tier has pgvector
# (see `just test-integration-pg19`): the required PostgreSQL 18 tier proves
# the build procedure itself is a correct no-op without the extension,
# matching how `assert_the_artist_embeddings_table_matches_the_server` treats
# the table it indexes.

_HNSW_FIXTURE_MODEL_VERSION = "gm-database-schema-lhp2.3-integration-fixture"
_HNSW_FIXTURE_ROW_COUNT = 5000
_HNSW_FIXTURE_DIMENSIONS = 128
# Comfortably below `POSTGRES_INTEGRATION_SHM_SIZE=256m` (see `just
# test-integration-pg19`) and far below the ~2 GB ADR 0013 documents for
# production -- this fixture is a few thousand rows, not millions.
_HNSW_FIXTURE_MAINTENANCE_WORK_MEM = "64MB"


def _synthetic_halfvec_literal(rng: random.Random) -> str:
    """A pgvector text literal for one random 128-dimensional vector."""
    values = (rng.uniform(-1.0, 1.0) for _dim in range(_HNSW_FIXTURE_DIMENSIONS))
    return "[" + ",".join(f"{value:.6f}" for value in values) + "]"


async def seed_artist_embeddings_hnsw_fixture(cursor: Any) -> None:
    """Insert a few thousand synthetic 128-dim rows under their own model_version.

    Plenty to build and exercise the index without the multi-GiB scale ADR
    0013's footprint spike measured on real entity counts -- this fixture only
    has to prove the DDL and the build procedure work, not reproduce the
    spike's size or recall numbers.
    """
    rng = random.Random(0)  # noqa: S311 -- deterministic synthetic test vectors, not cryptography
    rows = [
        (str(artist_id), _HNSW_FIXTURE_MODEL_VERSION, _synthetic_halfvec_literal(rng), "integration-test-fixture", "2026-09-01")
        for artist_id in range(_HNSW_FIXTURE_ROW_COUNT)
    ]
    await cursor.executemany(
        """
        INSERT INTO public.artist_embeddings (artist_id, model_version, embedding, source_dump_id, source_dump_date)
        VALUES (%s, %s, %s::halfvec, %s, %s)
        ON CONFLICT (artist_id, model_version) DO NOTHING
        """,
        rows,
    )
    await cursor.execute("ANALYZE public.artist_embeddings")


async def hnsw_index_definition() -> str | None:
    """Return `pg_indexes.indexdef` for the artist embeddings HNSW index, or None."""
    rows = await postgres_rows(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = %s",
        (ARTIST_EMBEDDINGS_HNSW_INDEX_NAME,),
    )
    return rows[0][0] if rows else None


@pytest.mark.asyncio
async def test_build_artist_embeddings_index_is_guarded_on_the_extension() -> None:
    """`build_artist_embeddings_index` is a correct no-op without pgvector.

    Mirrors `assert_the_artist_embeddings_table_matches_the_server`'s split:
    the required PostgreSQL 18 tier proves the guard closes; the advisory
    PostgreSQL 19 tier (which has pgvector) proves the rest below.
    """
    await apply_schema()
    connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params())
    async with connection, connection.cursor() as cursor:
        await connection.set_autocommit(True)
        failures = await build_artist_embeddings_index(cursor, maintenance_work_mem=_HNSW_FIXTURE_MAINTENANCE_WORK_MEM)

    if not await vector_extension_present():
        assert failures == 0
        assert await hnsw_index_definition() is None
        return

    assert failures == 0
    assert await hnsw_index_definition() is not None


@pytest.mark.asyncio
async def test_build_artist_embeddings_index_builds_a_usable_hnsw_index() -> None:
    """The operator procedure builds an index the planner actually uses.

    Advisory PostgreSQL 19 tier only -- pgvector is not on the required
    PostgreSQL 18 tier's bare official image. Builds the index over a
    synthetic fixture with `maintenance_work_mem` raised only for the build
    (never a standing setting, per ADR 0013), then asserts the planner picks
    it for a plain `ORDER BY ... LIMIT k` nearest-neighbour query -- the shape
    the ADR's similar-artist retrieval use runs.
    """
    if not await vector_extension_present():
        pytest.skip("pgvector is not installed on this tier; this scenario only exists when it is available")
    await apply_schema()

    connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params())
    async with connection, connection.cursor() as cursor:
        # Independent of test order and of `test_build_artist_embeddings_index_is_guarded_on_the_extension`,
        # which may have already built this index over an empty table: this
        # test's whole point is building over an *already-populated* table,
        # the scenario ADR 0013's build-memory precondition is about, so it
        # drops any earlier build first rather than relying on incremental
        # index maintenance over rows inserted after the fact.
        await connection.set_autocommit(True)
        await cursor.execute(f"DROP INDEX IF EXISTS public.{ARTIST_EMBEDDINGS_HNSW_INDEX_NAME}")
        await connection.set_autocommit(False)

        await seed_artist_embeddings_hnsw_fixture(cursor)
        await connection.commit()
        await connection.set_autocommit(True)
        failures = await build_artist_embeddings_index(cursor, maintenance_work_mem=_HNSW_FIXTURE_MAINTENANCE_WORK_MEM)
    assert failures == 0

    index_definition = await hnsw_index_definition()
    assert index_definition is not None
    assert "USING hnsw" in index_definition
    assert "halfvec_cosine_ops" in index_definition
    assert "m='16'" in index_definition
    assert "ef_construction='64'" in index_definition

    probe = random.Random(1)  # noqa: S311 -- deterministic synthetic test vector, not cryptography
    query_vector = _synthetic_halfvec_literal(probe)
    plan_rows = await postgres_rows(
        "EXPLAIN SELECT artist_id FROM public.artist_embeddings ORDER BY embedding <=> %s::halfvec LIMIT 5",
        (query_vector,),
    )
    plan = "\n".join(row[0] for row in plan_rows)
    assert ARTIST_EMBEDDINGS_HNSW_INDEX_NAME in plan, f"planner did not use the HNSW index:\n{plan}"


async def index_relfilenode(index_name: str) -> int:
    """Return the physical file identifier PostgreSQL currently backs `index_name` with.

    `REINDEX` builds a brand-new physical file and swaps it in under the same
    index name and OID, so a changed `relfilenode` is what actually building
    something new looks like -- unlike `CREATE INDEX IF NOT EXISTS`, which
    would leave this untouched.
    """
    rows = await postgres_rows(
        "SELECT relfilenode FROM pg_class WHERE relnamespace = 'public'::regnamespace AND relname = %s",
        (index_name,),
    )
    return rows[0][0]


async def index_is_valid(index_name: str) -> bool:
    rows = await postgres_rows(
        "SELECT indisvalid FROM pg_index WHERE indexrelid = %s::regclass",
        (f"public.{index_name}",),
    )
    return bool(rows[0][0])


@pytest.mark.asyncio
async def test_build_artist_embeddings_index_rebuild_true_actually_rebuilds() -> None:
    """`rebuild=True` runs `REINDEX INDEX`, which -- unlike the default
    `CREATE INDEX IF NOT EXISTS` -- replaces the index's physical file even
    though its name and OID stay the same; `relfilenode` is what makes that
    replacement observable. Also proves `maintenance_work_mem` is back to
    this connection's own inherited value afterward, on the same connection
    the raise happened on, not merely on a fresh one.
    """
    if not await vector_extension_present():
        pytest.skip("pgvector is not installed on this tier; this scenario only exists when it is available")
    await apply_schema()

    connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params())
    async with connection, connection.cursor() as cursor:
        await connection.set_autocommit(True)

        await cursor.execute("SHOW maintenance_work_mem")
        baseline_maintenance_work_mem = (await cursor.fetchall())[0][0]

        # Ensure a real, already-built index exists first -- `rebuild=True`
        # rebuilds an existing index, it does not create one from nothing.
        first_build_failures = await build_artist_embeddings_index(cursor, maintenance_work_mem=_HNSW_FIXTURE_MAINTENANCE_WORK_MEM)
        assert first_build_failures == 0
        before_relfilenode = await index_relfilenode(ARTIST_EMBEDDINGS_HNSW_INDEX_NAME)

        rebuild_failures = await build_artist_embeddings_index(cursor, maintenance_work_mem=_HNSW_FIXTURE_MAINTENANCE_WORK_MEM, rebuild=True)

        await cursor.execute("SHOW maintenance_work_mem")
        after_maintenance_work_mem = (await cursor.fetchall())[0][0]

    assert rebuild_failures == 0
    after_relfilenode = await index_relfilenode(ARTIST_EMBEDDINGS_HNSW_INDEX_NAME)
    assert after_relfilenode != before_relfilenode, "REINDEX did not actually replace the index's physical file"
    assert await index_is_valid(ARTIST_EMBEDDINGS_HNSW_INDEX_NAME) is True
    assert after_maintenance_work_mem == baseline_maintenance_work_mem, "maintenance_work_mem was not reset after the rebuild"


# ── Per-model_version partial HNSW indexes (ADR 0013 follow-on, gm-database-schema-19g5) ──
# See docs/architecture.md, "Serving kNN through the monthly refresh: per-version partial
# indexes". Advisory PostgreSQL 19 tier only, same as every other HNSW scenario above --
# pgvector is not on the required PostgreSQL 18 tier's bare official image.

_VERSION_INDEX_FIXTURE_ROW_COUNT = 2000
_VERSION_INDEX_FIXTURE_MAINTENANCE_WORK_MEM = "64MB"

# Real `analytics-engine` shape, not a repository-local placeholder: `stored_model_version`
# there composes `FastRPConfig.model_version` (`insights/embeddings/fastrp.py` --
# `f"fastrp-v{ALGORITHM_VERSION}:dim={dim}:weights={weights}:beta={beta:g}:proj=..."`, itself
# over 100 characters), an edge-set version, and the dump id. Using the real, long,
# `:`-and-`=`-and-`,`-laden shape here -- rather than a short placeholder string -- exercises
# the same long-string, multi-separator value `_sql_string_literal`'s quoting has to handle in
# production, not a simplified stand-in. `_LIVE_INDEX_NAME`/`_NEW_INDEX_NAME` are
# `insights.embedding_pipeline._index_name`'s real output for these two exact strings
# (computed independently of this module -- see `TestCrossRepoIndexNameAgreement` in
# tests/test_postgres_schema.py for the same cross-repo proof at the unit level).
_VERSION_INDEX_METHOD_VERSION = "fastrp-v1:dim=128:weights=1,1,1:beta=-1:proj=achlioptas-s3:rows=splitmix64(blake2b64(kind,key)):seed=20260924"
_VERSION_INDEX_LIVE_MODEL_VERSION = f"{_VERSION_INDEX_METHOD_VERSION}:edges-v2@discogs_20260801"
_VERSION_INDEX_LIVE_INDEX_NAME = "idx_artist_embeddings_discogs_20260801_d0bb00bf9c8a_hnsw"
_VERSION_INDEX_NEW_MODEL_VERSION = f"{_VERSION_INDEX_METHOD_VERSION}:edges-v2@discogs_20260901"
_VERSION_INDEX_NEW_INDEX_NAME = "idx_artist_embeddings_discogs_20260901_29a277e88af8_hnsw"


async def _seed_artist_embeddings_version_fixture(cursor: Any, model_version: str, *, seed: int) -> None:
    """Insert `_VERSION_INDEX_FIXTURE_ROW_COUNT` synthetic rows under one `model_version`."""
    rng = random.Random(seed)  # noqa: S311 -- deterministic synthetic test vectors, not cryptography
    rows = [
        (str(artist_id), model_version, _synthetic_halfvec_literal(rng), "integration-test-fixture", "2026-09-01")
        for artist_id in range(_VERSION_INDEX_FIXTURE_ROW_COUNT)
    ]
    await cursor.executemany(
        """
        INSERT INTO public.artist_embeddings (artist_id, model_version, embedding, source_dump_id, source_dump_date)
        VALUES (%s, %s, %s::halfvec, %s, %s)
        ON CONFLICT (artist_id, model_version) DO NOTHING
        """,
        rows,
    )
    await cursor.execute("ANALYZE public.artist_embeddings")


async def _model_version_row_count(model_version: str) -> int:
    rows = await postgres_rows("SELECT count(*) FROM public.artist_embeddings WHERE model_version = %s", (model_version,))
    return int(rows[0][0])


async def _index_definition(index_name: str) -> str | None:
    """Return `pg_indexes.indexdef` for `index_name`, or None -- generalizes `hnsw_index_definition`
    (which is hardcoded to the whole-table index) to any of this file's per-version fixture indexes.
    """
    rows = await postgres_rows(
        "SELECT indexdef FROM pg_indexes WHERE schemaname = 'public' AND indexname = %s",
        (index_name,),
    )
    return rows[0][0] if rows else None


async def _assert_planner_uses_partial_index_for_model_version(index_name: str, model_version: str, query_vector: str) -> None:
    """Assert the planner picks `index_name` for a `model_version`-filtered kNN query.

    The shape `catalog-api`'s similar-artist retrieval runs (docs/architecture.md,
    "Querying it: iterative index scans for filtered queries") -- restricting to one
    `model_version` and ordering by distance. A partial index whose predicate matches the
    query's `WHERE` clause exactly does not have the filtered-recall problem an unfiltered
    HNSW scan has, so this does not need `hnsw.iterative_scan` raised to observe the planner
    choosing it.
    """
    plan_rows = await postgres_rows(
        "EXPLAIN SELECT artist_id FROM public.artist_embeddings WHERE model_version = %s ORDER BY embedding <=> %s::halfvec LIMIT 5",
        (model_version, query_vector),
    )
    plan = "\n".join(row[0] for row in plan_rows)
    assert index_name in plan, f"planner did not use {index_name} for model_version={model_version!r}:\n{plan}"


@pytest.mark.asyncio
async def test_build_artist_embeddings_version_index_serves_knn_without_an_index_gap() -> None:
    """The whole point of gm-database-schema-19g5: kNN for the *live* `model_version` keeps
    using its own partial index, untouched, while a second `model_version` is loaded and
    indexed alongside it -- no drop, no gap, no blocked reads. Then proves the retire half
    of the flow: dropping the live version's index and rows leaves the new version's index
    and rows exactly as they were.
    """
    if not await vector_extension_present():
        pytest.skip("pgvector is not installed on this tier; this scenario only exists when it is available")
    await apply_schema()

    connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params())
    async with connection, connection.cursor() as cursor:
        await connection.set_autocommit(True)
        # A clean slate, independent of test order and of the whole-table index scenarios
        # above: this test's whole point is two *partial* indexes coexisting, which the
        # whole-table index (built over every model_version, including these fixtures)
        # would otherwise also match and make the planner's choice ambiguous.
        await cursor.execute(f"DROP INDEX IF EXISTS public.{ARTIST_EMBEDDINGS_HNSW_INDEX_NAME}")
        await cursor.execute(f"DROP INDEX IF EXISTS public.{_VERSION_INDEX_LIVE_INDEX_NAME}")
        await cursor.execute(f"DROP INDEX IF EXISTS public.{_VERSION_INDEX_NEW_INDEX_NAME}")
        await cursor.execute(
            "DELETE FROM public.artist_embeddings WHERE model_version IN (%s, %s)",
            (_VERSION_INDEX_LIVE_MODEL_VERSION, _VERSION_INDEX_NEW_MODEL_VERSION),
        )

        # Step 0: the live model_version is already loaded and indexed -- the steady state
        # before a monthly refresh starts.
        await _seed_artist_embeddings_version_fixture(cursor, _VERSION_INDEX_LIVE_MODEL_VERSION, seed=10)
        live_build_failures = await build_artist_embeddings_version_index(
            cursor,
            _VERSION_INDEX_LIVE_MODEL_VERSION,
            _VERSION_INDEX_LIVE_INDEX_NAME,
            maintenance_work_mem=_VERSION_INDEX_FIXTURE_MAINTENANCE_WORK_MEM,
        )
        assert live_build_failures == 0

        live_probe = random.Random(11)  # noqa: S311 -- deterministic synthetic test vector, not cryptography
        live_query_vector = _synthetic_halfvec_literal(live_probe)
        await _assert_planner_uses_partial_index_for_model_version(
            _VERSION_INDEX_LIVE_INDEX_NAME, _VERSION_INDEX_LIVE_MODEL_VERSION, live_query_vector
        )

        # Step 1 (analytics-engine's load) + step 2 (the operator's build): a second
        # model_version is loaded and indexed. The live version's rows and index are never
        # touched by either step.
        await _seed_artist_embeddings_version_fixture(cursor, _VERSION_INDEX_NEW_MODEL_VERSION, seed=20)
        new_build_failures = await build_artist_embeddings_version_index(
            cursor,
            _VERSION_INDEX_NEW_MODEL_VERSION,
            _VERSION_INDEX_NEW_INDEX_NAME,
            maintenance_work_mem=_VERSION_INDEX_FIXTURE_MAINTENANCE_WORK_MEM,
        )
        assert new_build_failures == 0

        # The crux of the bead: the live version's kNN still uses its own index -- no gap
        # opened by the second model_version's load and build.
        await _assert_planner_uses_partial_index_for_model_version(
            _VERSION_INDEX_LIVE_INDEX_NAME, _VERSION_INDEX_LIVE_MODEL_VERSION, live_query_vector
        )

        new_probe = random.Random(21)  # noqa: S311 -- deterministic synthetic test vector, not cryptography
        new_query_vector = _synthetic_halfvec_literal(new_probe)
        await _assert_planner_uses_partial_index_for_model_version(_VERSION_INDEX_NEW_INDEX_NAME, _VERSION_INDEX_NEW_MODEL_VERSION, new_query_vector)

        assert await _model_version_row_count(_VERSION_INDEX_LIVE_MODEL_VERSION) == _VERSION_INDEX_FIXTURE_ROW_COUNT
        assert await _model_version_row_count(_VERSION_INDEX_NEW_MODEL_VERSION) == _VERSION_INDEX_FIXTURE_ROW_COUNT

        # Step 3 (after catalog-api's switch, simulated by this test simply moving on):
        # retire the superseded live version.
        retire_failures = await retire_artist_embeddings_version(cursor, _VERSION_INDEX_LIVE_MODEL_VERSION, _VERSION_INDEX_LIVE_INDEX_NAME)
        assert retire_failures == 0

    assert await _index_definition(_VERSION_INDEX_LIVE_INDEX_NAME) is None, "the retired version's index must be gone"
    assert await _model_version_row_count(_VERSION_INDEX_LIVE_MODEL_VERSION) == 0, "the retired version's rows must be gone"

    # The new version -- never touched by retiring the old one -- is exactly as it was.
    assert await _index_definition(_VERSION_INDEX_NEW_INDEX_NAME) is not None
    assert await _model_version_row_count(_VERSION_INDEX_NEW_MODEL_VERSION) == _VERSION_INDEX_FIXTURE_ROW_COUNT
    await _assert_planner_uses_partial_index_for_model_version(_VERSION_INDEX_NEW_INDEX_NAME, _VERSION_INDEX_NEW_MODEL_VERSION, new_query_vector)


@pytest.mark.asyncio
async def test_derived_refresh_job_keys_claim_locks_and_repeated_initialization() -> None:
    """Two workers cannot claim one job, and an old generation cannot masquerade as current."""
    await apply_schema()
    loader = f"job-test-{uuid4().hex}"
    first, second = f"first-{uuid4().hex}", f"second-{uuid4().hex}"
    connection_a = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params(), autocommit=True)
    connection_b = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params(), autocommit=True)
    async with connection_a, connection_b:
        async with connection_a.transaction(), connection_a.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO loader_derived_refresh_cursor (loader, generation, version) VALUES (%s, 1, %s)",
                (loader, first),
            )
            await cursor.execute(
                "INSERT INTO loader_extraction_latch (loader, version, generation) VALUES (%s, %s, 1)",
                (loader, first),
            )
            await cursor.execute(
                "INSERT INTO loader_derived_refresh_job (loader, version, generation) VALUES (%s, %s, 1)",
                (loader, first),
            )

        try:
            # A second initializer does not wipe a durably scheduled job.
            await apply_schema()
            assert await postgres_rows("SELECT state, attempt_count FROM loader_derived_refresh_job WHERE loader = %s", (loader,)) == [("pending", 0)]

            async with connection_a.transaction(), connection_a.cursor() as cursor_a:
                await cursor_a.execute(
                    "SELECT version FROM loader_derived_refresh_job WHERE loader = %s AND state = 'pending' FOR UPDATE SKIP LOCKED",
                    (loader,),
                )
                assert await cursor_a.fetchall() == [(first,)]
                async with connection_b.transaction(), connection_b.cursor() as cursor_b:
                    await cursor_b.execute(
                        "SELECT version FROM loader_derived_refresh_job WHERE loader = %s AND state = 'pending' FOR UPDATE SKIP LOCKED",
                        (loader,),
                    )
                    assert await cursor_b.fetchall() == []
                await cursor_a.execute(
                    "UPDATE loader_derived_refresh_job SET state = 'leased', next_attempt_at = NULL, "
                    "lease_owner = 'worker-a', lease_token = %s, lease_epoch = lease_epoch + 1, "
                    "lease_expires_at = NOW() + INTERVAL '1 minute' WHERE loader = %s AND version = %s",
                    (uuid4(), loader, first),
                )

            async with connection_a.transaction(), connection_a.cursor() as cursor_a:
                await cursor_a.execute("SELECT generation FROM loader_derived_refresh_cursor WHERE loader = %s FOR UPDATE", (loader,))
                assert await cursor_a.fetchone() == (1,)
                async with connection_b.transaction(), connection_b.cursor() as cursor_b:
                    await cursor_b.execute(
                        "SELECT generation FROM loader_derived_refresh_cursor WHERE loader = %s FOR UPDATE SKIP LOCKED",
                        (loader,),
                    )
                    assert await cursor_b.fetchall() == []

            # The next extraction can advance the cursor and fence the old job.
            async with connection_b.transaction(), connection_b.cursor() as cursor_b:
                await cursor_b.execute(
                    "INSERT INTO loader_extraction_latch (loader, version, generation) VALUES (%s, %s, 2)",
                    (loader, second),
                )
                await cursor_b.execute(
                    "UPDATE loader_derived_refresh_cursor SET generation = 2, version = %s WHERE loader = %s",
                    (second, loader),
                )
            assert await postgres_rows(
                "SELECT job.generation = cursor.generation AND job.version = cursor.version "
                "FROM loader_derived_refresh_job AS job JOIN loader_derived_refresh_cursor AS cursor USING (loader) "
                "WHERE job.loader = %s",
                (loader,),
            ) == [(False,)]

            with pytest.raises(psycopg.errors.ForeignKeyViolation):
                async with connection_b.transaction(), connection_b.cursor() as cursor_b:
                    await cursor_b.execute(
                        "INSERT INTO loader_derived_refresh_job (loader, version, generation) VALUES (%s, %s, 3)",
                        (loader, second),
                    )
        finally:
            await connection_a.execute("DELETE FROM loader_derived_refresh_job WHERE loader = %s", (loader,))
            await connection_a.execute("DELETE FROM loader_extraction_latch WHERE loader = %s", (loader,))
            await connection_a.execute("DELETE FROM loader_derived_refresh_cursor WHERE loader = %s", (loader,))


# ── Precomputed similar-artist lists and the embedding-release pointer (D serving mode) ──
# `artist_similar_artists` and `artist_embedding_releases` are created unconditionally (see
# the module comment above `_ARTIST_SIMILARITY_STATEMENTS` in postgres.py), so -- unlike the
# HNSW sections above -- these tests need no pgvector gate and run identically on both the
# required PostgreSQL 18 tier and the advisory PostgreSQL 19 tier.

_SIMILARITY_FIXTURE_SOURCE_DUMP_DATE = date(2026, 9, 1)


async def _seed_artist_similar_artists_fixture(cursor: Any, model_version: str, *, artist_count: int = 5) -> int:
    """Insert `artist_count` artists' worth of synthetic rank-1..3 similar-artist rows.

    Returns the row count inserted (`artist_count * 3`), so callers can assert against it
    without hardcoding the fixture's own shape a second time.
    """
    rows = [
        (str(artist_id), model_version, rank, str(1_000_000 + artist_id * 10 + rank), 1.0 - (rank * 0.1))
        for artist_id in range(artist_count)
        for rank in (1, 2, 3)
    ]
    await cursor.executemany(
        """
        INSERT INTO public.artist_similar_artists (artist_id, model_version, rank, similar_artist_id, score)
        VALUES (%s, %s, %s, %s, %s)
        ON CONFLICT (artist_id, model_version, rank) DO NOTHING
        """,
        rows,
    )
    return len(rows)


async def _delete_artist_similarity_fixture(model_version: str) -> None:
    """Remove every row a similarity test wrote for `model_version`, from both tables."""
    connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params())
    async with connection, connection.cursor() as cursor:
        await cursor.execute("DELETE FROM public.artist_similar_artists WHERE model_version = %s", (model_version,))
        await cursor.execute("DELETE FROM public.artist_embedding_releases WHERE model_version = %s", (model_version,))
        await connection.commit()


@pytest.mark.asyncio
async def test_at_most_one_current_release_is_enforced_by_the_database() -> None:
    """`idx_artist_embedding_releases_is_current` refuses a second `is_current` row outright."""
    await apply_schema()
    version_a, version_b = f"integration-test-{uuid4().hex}", f"integration-test-{uuid4().hex}"
    try:
        connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params())
        async with connection, connection.cursor() as cursor:
            await cursor.execute(
                "INSERT INTO public.artist_embedding_releases (model_version, source_dump_id, source_dump_date, k, artists, is_current) "
                "VALUES (%s, %s, %s, %s, %s, TRUE)",
                (version_a, "integration-test-fixture", _SIMILARITY_FIXTURE_SOURCE_DUMP_DATE, 10, 5),
            )
            await connection.commit()

            with pytest.raises(psycopg.errors.UniqueViolation):
                await cursor.execute(
                    "INSERT INTO public.artist_embedding_releases (model_version, source_dump_id, source_dump_date, k, artists, is_current) "
                    "VALUES (%s, %s, %s, %s, %s, TRUE)",
                    (version_b, "integration-test-fixture", _SIMILARITY_FIXTURE_SOURCE_DUMP_DATE, 10, 5),
                )
            await connection.rollback()
    finally:
        await _delete_artist_similarity_fixture(version_a)
        await _delete_artist_similarity_fixture(version_b)


@pytest.mark.asyncio
async def test_publish_artist_embedding_release_flips_is_current_atomically() -> None:
    """`publish_artist_embedding_release` clears the old current release and sets the new one, together."""
    await apply_schema()
    version_a, version_b = f"integration-test-{uuid4().hex}", f"integration-test-{uuid4().hex}"
    try:
        connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params(), autocommit=True)
        async with connection, connection.cursor() as cursor:
            assert (
                await publish_artist_embedding_release(
                    cursor,
                    version_a,
                    source_dump_id="integration-test-fixture",
                    source_dump_date=_SIMILARITY_FIXTURE_SOURCE_DUMP_DATE,
                    k=10,
                    artists=5,
                )
                == 0
            )
            assert await postgres_rows("SELECT is_current FROM public.artist_embedding_releases WHERE model_version = %s", (version_a,)) == [(True,)]

            assert (
                await publish_artist_embedding_release(
                    cursor,
                    version_b,
                    source_dump_id="integration-test-fixture",
                    source_dump_date=_SIMILARITY_FIXTURE_SOURCE_DUMP_DATE,
                    k=12,
                    artists=6,
                )
                == 0
            )
            # The old current release is cleared, the new one is current, and the database
            # never held two -- the invariant this atomic flip exists to protect.
            assert await postgres_rows("SELECT is_current FROM public.artist_embedding_releases WHERE model_version = %s", (version_a,)) == [(False,)]
            assert await postgres_rows("SELECT is_current FROM public.artist_embedding_releases WHERE model_version = %s", (version_b,)) == [(True,)]
            assert await postgres_rows("SELECT COUNT(*) FROM public.artist_embedding_releases WHERE is_current") == [(1,)]

            # Idempotent: republishing the already-current version changes nothing but succeeds.
            assert (
                await publish_artist_embedding_release(
                    cursor,
                    version_b,
                    source_dump_id="integration-test-fixture",
                    source_dump_date=_SIMILARITY_FIXTURE_SOURCE_DUMP_DATE,
                    k=12,
                    artists=6,
                )
                == 0
            )
            assert await postgres_rows("SELECT COUNT(*) FROM public.artist_embedding_releases WHERE is_current") == [(1,)]
    finally:
        await _delete_artist_similarity_fixture(version_a)
        await _delete_artist_similarity_fixture(version_b)


@pytest.mark.asyncio
async def test_retire_artist_similar_artists_version_refuses_the_current_version() -> None:
    """`retire_artist_similar_artists_version` refuses the live version and deletes a superseded one."""
    await apply_schema()
    current_version, retired_version = f"integration-test-{uuid4().hex}", f"integration-test-{uuid4().hex}"
    try:
        connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params(), autocommit=True)
        async with connection, connection.cursor() as cursor:
            await _seed_artist_similar_artists_fixture(cursor, retired_version)
            current_row_count = await _seed_artist_similar_artists_fixture(cursor, current_version)
            for model_version in (retired_version, current_version):
                assert (
                    await publish_artist_embedding_release(
                        cursor,
                        model_version,
                        source_dump_id="integration-test-fixture",
                        source_dump_date=_SIMILARITY_FIXTURE_SOURCE_DUMP_DATE,
                        k=3,
                        artists=5,
                    )
                    == 0
                )

            # Refuses the current version outright -- nothing about it is deleted.
            assert await retire_artist_similar_artists_version(cursor, current_version) == 1
            assert await postgres_rows("SELECT COUNT(*) FROM public.artist_similar_artists WHERE model_version = %s", (current_version,)) == [
                (current_row_count,)
            ]
            assert await postgres_rows("SELECT COUNT(*) FROM public.artist_embedding_releases WHERE model_version = %s", (current_version,)) == [(1,)]

            # A superseded version is fully retired -- rows and release row both -- without
            # touching the still-current version.
            assert await retire_artist_similar_artists_version(cursor, retired_version, delete_release=True) == 0
            assert await postgres_rows("SELECT COUNT(*) FROM public.artist_similar_artists WHERE model_version = %s", (retired_version,)) == [(0,)]
            assert await postgres_rows("SELECT COUNT(*) FROM public.artist_embedding_releases WHERE model_version = %s", (retired_version,)) == [(0,)]
            assert await postgres_rows("SELECT COUNT(*) FROM public.artist_similar_artists WHERE model_version = %s", (current_version,)) == [
                (current_row_count,)
            ]
    finally:
        await _delete_artist_similarity_fixture(retired_version)
        await _delete_artist_similarity_fixture(current_version)


# ── Graph projection fixtures ────────────────────────────────────────────────
# One deliberately awkward record per source table. Each carries the shapes the
# enricher filters — a repeated reference, the Discogs `0` sentinel, an element
# with no id, an empty role, a raw `formats`-era companies list — so the views
# are proved to drop exactly what `graphinator` drops rather than merely to run.
GRAPH_FIXTURE_RELEASE = {
    "id": 111,
    "title": "Test Release",
    "year": "1969",
    "country": "  UK  ",
    "artists": [{"id": 7, "name": "Alice"}, {"id": 7, "name": "Alice"}, {"id": 0, "name": "Various"}, {"name": "No id"}],
    "labels": [{"id": 9, "catno": "X1"}, {"id": 9, "catno": "X2"}],
    "master_id": 55,
    "formats": [{"name": "Vinyl", "qty": "1"}, {"name": "LP"}, {"name": "  "}, {"qty": "1"}],
    "genres": ["Rock"],
    "styles": ["Prog Rock", "Psychedelic"],
    "extraartists": [
        {"name": "Alice", "role": "Producer", "id": 7},
        {"name": "Bob", "role": "Recorded By, Mastering Engineer"},
        {"name": "Nobody", "role": ""},
        {"role": "Engineer"},
    ],
    "companies": {
        "items": [
            {"discogs_id": 42, "name": "Plant Ltd", "role": "Pressed By", "role_category": "manufacturing"},
            {"name": "  Cutting   Room  ", "role": "Lacquer Cut At"},
            {"discogs_id": 0, "name": "Zero", "role": "Distributed By"},
            {"name": "No role"},
        ]
    },
    # Track-level credits and performers (gm-database-schema-ug3v). "Carol" is
    # credited only here, never at release level, which is what proves
    # `graph.person` and `graph.same_as` have to read this source too.
    #
    # `tracklist`, and everything nested inside a track, is stored exactly as
    # `discogs-ingestion` converts it from XML: `normalize_release` never
    # recurses into it, so several children are the real xmltodict array
    # (`{"track": [...]}`) and one child collapses to a bare object under the
    # same key (`{"artist": {...}}`) rather than a one-element array -- both
    # shapes are deliberately exercised below, once each, rather than only the
    # plain-array shape a synthetic fixture might otherwise default to. Two
    # top-level tracks make `tracklist` the array form; track A2's medley makes
    # `sub_tracks` the array form (two sub-tracks); sub-track A2a's own
    # `extraartists`/`artists` make the single-object form (one credit, one
    # performer each); and sub-track A2b carries neither key at all, which
    # must contribute nothing rather than raise.
    "tracklist": {
        "track": [
            {
                "position": "A1",
                "title": "Track One",
                "extraartists": {
                    "artist": [
                        {"name": "Carol", "role": "Vocals", "id": 21},
                        {"name": "No id artist", "role": "Guitar"},
                    ]
                },
                "artists": {"artist": [{"id": 22, "name": "Dana"}, {"id": 0, "name": "Various"}]},
            },
            {
                "position": "A2",
                "title": "Medley",
                "sub_tracks": {
                    "track": [
                        {
                            "position": "A2a",
                            "title": "Medley Part One",
                            "extraartists": {"artist": {"name": "Eve", "role": "Mixed By", "id": 23}},
                            "artists": {"artist": {"id": 24, "name": "Frank"}},
                        },
                        {"position": "A2b", "title": "Medley Part Two"},
                    ]
                },
            },
        ]
    },
}

GRAPH_FIXTURE_MEDIA = {
    "families": ["vinyl"],
    "items": [
        {"medium": "vinyl_12", "family": "vinyl", "qty": 1},
        {"medium": "vinyl_12", "family": "vinyl", "qty": 1},
        {"medium": "unmapped", "family": ""},
    ],
}

# A pre-cutover record: `companies` is still the raw Discogs list and `artists` has
# been flattened to strings. Neither may raise; both must contribute nothing.
#
# `tracklist` is unrelated to those malformed fields -- each is guarded
# independently -- and carries exactly one track, which is the xmltodict
# single-object form at the container's own top level (`{"track": {...}}`
# rather than `{"track": [...]}`), the one variant release 111's tracklist
# does not exercise.
GRAPH_FIXTURE_MALFORMED = {
    "id": 222,
    "title": "Malformed",
    "artists": "not-an-array",
    "companies": [{"name": "Raw"}],
    "genres": None,
    "tracklist": {
        "track": {
            "position": "1",
            "title": "Solo",
            "extraartists": {"artist": {"name": "Grace", "role": "Producer", "id": 25}},
            "artists": {"artist": {"name": "Henry", "id": 26}},
        }
    },
}

GRAPH_FIXTURE_ARTIST = {
    "id": 7,
    "name": "Alice",
    "members": [{"id": 8}, {"id": 0}, {"name": "No id"}],
    "groups": [{"id": 10}],
    "aliases": [{"id": 11}],
}

GRAPH_FIXTURE_LABEL = {"id": 9, "name": "Label Nine", "parentLabel": {"id": 12}, "sublabels": [{"id": 13}, {"name": "No id"}]}

GRAPH_FIXTURE_MASTER = {"id": 55, "title": "Test Master", "year": "1968", "artists": [{"id": 7}], "genres": ["Rock"], "styles": ["Prog Rock"]}

ARTIST_MBID = "11111111-1111-4111-8111-111111111111"
OTHER_ARTIST_MBID = "22222222-2222-4222-8222-222222222222"
DANGLING_MBID = "33333333-3333-4333-8333-333333333333"
# A MusicBrainz artist whose Discogs counterpart asserts no membership at all:
# Discogs artist 11 is an alias endpoint and nothing else, so the membership
# below exists only in `musicbrainz.relationships`. It is what proves the
# MEMBER_OF union carries a path `graph.member_of` alone does not have.
MEMBER_ONLY_MBID = "44444444-4444-4444-8444-444444444444"


async def seed_graph_fixtures() -> None:
    """Write one record per source table, then let the views be read back."""
    connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params())
    async with connection, connection.cursor() as cursor:
        for table, data_id, document, media in (
            ("releases", "111", GRAPH_FIXTURE_RELEASE, GRAPH_FIXTURE_MEDIA),
            ("releases", "222", GRAPH_FIXTURE_MALFORMED, None),
        ):
            await cursor.execute(
                f"INSERT INTO {table} (data_id, hash, data, media) VALUES (%s, %s, %s, %s) ON CONFLICT (data_id) DO NOTHING",  # noqa: S608
                (data_id, f"hash-{data_id}", Jsonb(document), Jsonb(media) if media else None),
            )
        for table, data_id, document in (
            ("artists", "7", GRAPH_FIXTURE_ARTIST),
            ("labels", "9", GRAPH_FIXTURE_LABEL),
            ("masters", "55", GRAPH_FIXTURE_MASTER),
        ):
            await cursor.execute(
                f"INSERT INTO {table} (data_id, hash, data) VALUES (%s, %s, %s) ON CONFLICT (data_id) DO NOTHING",  # noqa: S608
                (data_id, f"hash-{data_id}", Jsonb(document)),
            )

        await cursor.execute(
            """
            INSERT INTO musicbrainz.artists (mbid, name, discogs_artist_id)
            VALUES (%s, %s, %s), (%s, %s, %s), (%s, %s, %s)
            ON CONFLICT (mbid) DO NOTHING
            """,
            (ARTIST_MBID, "Alice", 7, OTHER_ARTIST_MBID, "The Band", 10, MEMBER_ONLY_MBID, "Session Player", 11),
        )
        await cursor.execute(
            """
            INSERT INTO musicbrainz.relationships
                (source_mbid, source_entity_type, target_mbid, target_entity_type, relationship_type, attributes, begin_date, end_date, ended)
            VALUES (%s, 'artist', %s, 'artist', 'member of band', %s, NULL, NULL, FALSE),
                   (%s, 'artist', %s, 'artist', 'member of band', %s, NULL, NULL, FALSE),
                   (%s, 'artist', %s, 'artist', 'collaboration', %s, NULL, NULL, FALSE)
            ON CONFLICT DO NOTHING
            """,
            (
                ARTIST_MBID,
                OTHER_ARTIST_MBID,
                Jsonb([]),
                MEMBER_ONLY_MBID,
                OTHER_ARTIST_MBID,
                Jsonb([]),
                ARTIST_MBID,
                DANGLING_MBID,
                Jsonb([]),
            ),
        )

        await cursor.execute(
            "INSERT INTO users (email, hashed_password) VALUES (%s, %s) ON CONFLICT (email) DO NOTHING RETURNING id",
            ("graph-fixture@example.test", "not-a-real-hash"),
        )
        row = await cursor.fetchone()
        if row is None:
            await cursor.execute("SELECT id FROM users WHERE email = %s", ("graph-fixture@example.test",))
            row = await cursor.fetchone()
        assert row is not None
        user_id = row[0]

        await cursor.execute(
            """
            INSERT INTO user_collections (user_id, release_id, instance_id) VALUES (%s, 111, 900), (%s, 999, 901)
            ON CONFLICT DO NOTHING
            """,
            (user_id, user_id),
        )
        await cursor.execute(
            "INSERT INTO user_wantlists (user_id, release_id) VALUES (%s, 111), (%s, 999) ON CONFLICT DO NOTHING",
            (user_id, user_id),
        )
        await cursor.execute("INSERT INTO catalog_items (kind) VALUES ('release') RETURNING id")
        item_row = await cursor.fetchone()
        assert item_row is not None
        await cursor.execute("INSERT INTO owned_copies (user_id, item_id) VALUES (%s, %s)", (user_id, item_row[0]))
        await connection.commit()


async def execute_all(statements: list[tuple[str, Any]]) -> None:
    """Run every statement in order against the disposable target database."""
    connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params())
    async with connection, connection.cursor() as cursor:
        for _name, statement in statements:
            await cursor.execute(statement)
        await connection.commit()


async def bootstrap_the_loader_tables() -> None:
    """Create the retained phase 0 views and fill every table from them once.

    This is the loaders' job in production — `discogs-sql-loader` writes an edge
    row in the same transaction as the document it came from, and recomputes the
    counters on the `extraction_complete` message it already handles. Nothing in
    this repository writes a graph row, so a test that wants rows has to stand
    in for the loader, and the only honest way to do that is to fill the tables
    from the definitions the relations published before they were tables.

    Re-running it is a no-op: every bootstrap statement is ON CONFLICT DO
    NOTHING, so the tests that share this database do not fight over the rows.
    """
    await execute_all(phase0_comparison_statements(PHASE0_SCHEMA))
    await execute_all(graph_bootstrap_statements(PHASE0_SCHEMA))


async def assert_graph_relations_project_the_enricher_rules() -> None:
    """Read every projection back and compare it to what graphinator would write."""
    assert await postgres_rows("SELECT release_id, artist_id FROM graph.by_artist ORDER BY 1, 2") == [("111", "7")]
    assert await postgres_rows("SELECT release_id, label_id FROM graph.on_label ORDER BY 1, 2") == [("111", "9")]
    assert await postgres_rows("SELECT release_id, master_id FROM graph.derived_from ORDER BY 1, 2") == [("111", "55")]
    assert await postgres_rows("SELECT release_id, genre_name FROM graph.in_genre ORDER BY 1, 2") == [("111", "Rock")]
    assert await postgres_rows("SELECT release_id, style_name FROM graph.in_style ORDER BY 1, 2") == [
        ("111", "Prog Rock"),
        ("111", "Psychedelic"),
    ]
    assert await postgres_rows("SELECT master_id, artist_id FROM graph.master_by_artist ORDER BY 1, 2") == [("55", "7")]
    assert await postgres_rows("SELECT master_id, genre_name FROM graph.master_in_genre ORDER BY 1, 2") == [("55", "Rock")]
    assert await postgres_rows("SELECT style_name, genre_name FROM graph.part_of ORDER BY 1, 2") == [
        ("Prog Rock", "Rock"),
        ("Psychedelic", "Rock"),
    ]
    assert await postgres_rows("SELECT member_artist_id, group_artist_id FROM graph.member_of ORDER BY 1, 2") == [("7", "10"), ("8", "7")]
    assert await postgres_rows("SELECT alias_artist_id, artist_id FROM graph.alias_of ORDER BY 1, 2") == [("11", "7")]
    assert await postgres_rows("SELECT sublabel_id, parent_label_id FROM graph.sublabel_of ORDER BY 1, 2") == [("13", "9"), ("9", "12")]

    assert await postgres_rows("SELECT name FROM graph.genre ORDER BY 1") == [("Rock",)]
    assert await postgres_rows("SELECT name FROM graph.style ORDER BY 1") == [("Prog Rock",), ("Psychedelic",)]
    assert await postgres_rows("SELECT country, genres, styles, media_families FROM graph.release WHERE release_id = '111'") == [
        ("UK", ["Rock"], ["Prog Rock", "Psychedelic"], ["vinyl"])
    ]
    # `formats` and `catalog_number` are what the graph writes onto `:Release`
    # from `data->'formats'[].name` and `labels[0].catno`.
    assert await postgres_rows("SELECT formats, catalog_number FROM graph.release WHERE release_id = '111'") == [(["Vinyl", "LP"], "X1")]
    assert await postgres_rows("SELECT formats, catalog_number FROM graph.release WHERE release_id = '222'") == [([], None)]
    # `gm_id` is NULL until the identity resolver mints one. The join is a LEFT
    # join precisely so an unresolved entity is still a vertex.
    assert await postgres_rows("SELECT gm_id FROM graph.release WHERE release_id = '111'") == [(None,)]
    # The malformed record neither raises nor contributes.
    assert await postgres_rows("SELECT country, genres, media_families FROM graph.release WHERE release_id = '222'") == [(None, [], [])]

    assert await postgres_rows("SELECT name FROM graph.person ORDER BY 1") == [
        ("Alice",),
        ("Bob",),
        ("Carol",),
        ("Eve",),
        ("Grace",),
        ("No id artist",),
    ]
    assert await postgres_rows("SELECT person_name, release_id, role, role_category FROM graph.credited_on ORDER BY 1") == [
        ("Alice", "111", "Producer", categorize_role("Producer")),
        ("Bob", "111", "Recorded By, Mastering Engineer", categorize_role("Recorded By, Mastering Engineer")),
    ]
    # Track-level credits and performers (gm-database-schema-ug3v) resolve through
    # the same `graph.same_as`, so "Carol" (release 111, array-shaped extraartists),
    # "Eve" (release 111, single-object-shaped extraartists on a sub-track), and
    # "Grace" (release 222, single-object-shaped tracklist) -- none credited at
    # release level -- all appear here too.
    assert await postgres_rows("SELECT person_name, artist_id FROM graph.same_as ORDER BY 1") == [
        ("Alice", "7"),
        ("Carol", "21"),
        ("Eve", "23"),
        ("Grace", "25"),
    ]

    # The track and its sub-track both contribute, "Carol"/"Eve"/"Grace" (track-only)
    # are `:Person` rows, and the empty second sub-track contributes nothing.
    # `track_ordinal`/`sub_track_ordinal` identify the track, not `position`:
    # `0` is "on the track itself", the sub-track's own ordinal otherwise.
    assert await postgres_rows("SELECT name FROM graph.person WHERE name IN ('Carol', 'Grace')") == [("Carol",), ("Grace",)]
    assert await postgres_rows(
        "SELECT person_name, release_id, track_ordinal, sub_track_ordinal, track_position, role, role_category "
        "FROM graph.track_credited_on ORDER BY 2, 3, 4, 1"
    ) == [
        ("Carol", "111", 1, 0, "A1", "Vocals", categorize_role("Vocals")),
        ("No id artist", "111", 1, 0, "A1", "Guitar", categorize_role("Guitar")),
        ("Eve", "111", 2, 1, "A2a", "Mixed By", categorize_role("Mixed By")),
        ("Grace", "222", 1, 0, "1", "Producer", categorize_role("Producer")),
    ]
    assert await postgres_rows(
        "SELECT release_id, track_ordinal, sub_track_ordinal, track_position, artist_id FROM graph.track_by_artist ORDER BY 1, 2, 3"
    ) == [
        ("111", 1, 0, "A1", "22"),
        ("111", 2, 1, "A2a", "24"),
        ("222", 1, 0, "1", "26"),
    ]

    assert await postgres_rows("SELECT company_id, name, discogs_label_id FROM graph.company ORDER BY 1") == [
        ("42", "Plant Ltd", "42"),
        ("name:cutting room", "Cutting   Room", None),
        ("name:zero", "Zero", None),
    ]
    assert await postgres_rows("SELECT release_id, company_id, role, role_category, source FROM graph.credited_to ORDER BY 2") == [
        ("111", "42", "Pressed By", "manufacturing", "discogs"),
        ("111", "name:cutting room", "Lacquer Cut At", "other", "discogs"),
        ("111", "name:zero", "Distributed By", "other", "discogs"),
    ]

    assert await postgres_rows("SELECT medium_id, family, label FROM graph.medium ORDER BY 1") == [("vinyl_12", "vinyl", medium_label("vinyl_12"))]
    assert await postgres_rows("SELECT name FROM graph.media_family ORDER BY 1") == [("vinyl",)]
    assert await postgres_rows("SELECT medium_id, family_name FROM graph.in_family ORDER BY 1") == [("vinyl_12", "vinyl")]
    # Two entries resolving to the same canonical medium are one edge, qty summed.
    assert await postgres_rows("SELECT release_id, medium_id, source, qty FROM graph.issued_on ORDER BY 1") == [("111", "vinyl_12", "discogs", 2)]

    # The relationship whose target the loader has not stored is dropped.
    assert await postgres_rows(
        "SELECT source_mbid::text, target_mbid::text, relationship_type, raw_relationship_type FROM graph.mb_rel_artist_artist ORDER BY 1"
    ) == [
        (ARTIST_MBID, OTHER_ARTIST_MBID, "MEMBER_OF", "member of band"),
        (MEMBER_ONLY_MBID, OTHER_ARTIST_MBID, "MEMBER_OF", "member of band"),
    ]
    assert await postgres_rows("SELECT count(*) FROM graph.mb_rel_artist_label") == [(0,)]

    # A collection row naming a release the catalog does not hold is dropped.
    assert await postgres_rows("SELECT release_id, instance_id FROM graph.collected ORDER BY 1") == [("111", 900)]
    assert await postgres_rows("SELECT release_id FROM graph.wants ORDER BY 1") == [("111",)]
    assert await postgres_rows("SELECT count(*) FROM graph.owns") == [(1,)]


@pytest.mark.asyncio
async def test_graph_relations_project_the_enricher_rules() -> None:
    """The relations hold exactly what graphinator would write, on a real engine.

    Runs after the idempotence proof above and leaves its fixture rows in place;
    that proof reads the catalog, never the data.
    """
    await apply_schema()
    await seed_graph_fixtures()
    await bootstrap_the_loader_tables()
    await assert_graph_relations_project_the_enricher_rules()
    await assert_the_counter_relations_sum_the_edge_tables()


async def assert_the_counter_relations_sum_the_edge_tables() -> None:
    """The counters are sums over the edge tables and never re-read a document."""
    assert await postgres_rows("SELECT name, release_count, style_count FROM graph.genre_stats ORDER BY 1") == [("Rock", 1, 2)]
    assert await postgres_rows("SELECT name, release_count, genre_count FROM graph.style_stats ORDER BY 1") == [
        ("Prog Rock", 1, 1),
        ("Psychedelic", 1, 1),
    ]
    assert await postgres_rows("SELECT label_id, release_count, artist_count, genre_count FROM graph.label_stats ORDER BY 1") == [("9", 1, 1, 1)]

    # Artist 7 is on release 111, on master 55, is Alice's `same_as` target, sits
    # in two membership rows and is one alias endpoint: six edges.
    assert await postgres_rows("SELECT degree FROM graph.artist_degree WHERE artist_id = '7'") == [(6,)]

    # The one relation split across two owners. The loader-written base counts
    # the catalog edges; the view adds one collection row and one wantlist row.
    base = await postgres_rows("SELECT degree FROM graph.release_degree_base WHERE release_id = '111'")
    live = await postgres_rows("SELECT degree FROM graph.release_degree WHERE release_id = '111'")
    assert base and live
    assert live[0][0] == base[0][0] + 2

    assert await postgres_rows("SELECT artist_id, genre_name, release_count FROM graph.artist_genre ORDER BY 1, 2") == [("7", "Rock", 1)]
    assert await postgres_rows("SELECT label_id, genre_name, release_count FROM graph.label_genre ORDER BY 1, 2") == [("9", "Rock", 1)]


# ── The cross-provenance MEMBER_OF union ─────────────────────────────────────
# Neo4j holds a Discogs band membership and a MusicBrainz "member of band"
# assertion in one MEMBER_OF relationship space, so `shortestPath` traverses
# both. `graph.artist_member_of` is that space on the relational side, and this
# is the proof that both provenances reach it.

UNION_ROWS = "SELECT member_artist_id, group_artist_id, source FROM graph.artist_member_of ORDER BY 1, 2, 3"

# Discogs artist 7 is a member of 10 in BOTH provenances — the artist document
# says so and so does MusicBrainz — and the two are separate rows because
# `source` is in the key. (8, 7) is Discogs alone, from the `members` block.
# (11, 10) is MusicBrainz alone: Discogs artist 11 asserts no membership at all,
# so a traversal over `graph.member_of` never reaches it.
EXPECTED_UNION_ROWS = [
    ("11", "10", "musicbrainz"),
    ("7", "10", "discogs"),
    ("7", "10", "musicbrainz"),
    ("8", "7", "discogs"),
]

# What the refresh reports, one row per provenance in the order it returns them.
EXPECTED_UNION_REPORT = [("discogs", 2), ("musicbrainz", 2)]

# A membership no provenance asserts. The refresh has to remove it, which is
# what truncate-and-insert buys and an upsert would not.
STALE_MEMBERSHIP = ("999", "998", "discogs")


@pytest.mark.asyncio
async def test_the_member_of_union_holds_both_provenances_and_converges() -> None:
    """A MusicBrainz-only membership is in the union, and a re-run changes nothing.

    61.6% of the MEMBER_OF edge class is MusicBrainz provenance (spike
    gm-database-schema-gkt.1), reachable only by crossing to a Discogs id
    through `musicbrainz.artists.discogs_artist_id`. This is the claim that the
    crossing happens and that re-running it converges in both directions.
    """
    await apply_schema()
    await seed_graph_fixtures()
    await bootstrap_the_loader_tables()

    # The bootstrap fill and the refresh share one body, so both answer this.
    assert await postgres_rows(UNION_ROWS) == EXPECTED_UNION_ROWS

    reported = await postgres_rows("SELECT source, row_count FROM graph.refresh_artist_member_of()")
    assert reported == EXPECTED_UNION_REPORT
    assert await postgres_rows(UNION_ROWS) == EXPECTED_UNION_ROWS

    # The MusicBrainz-only membership is the point: `graph.member_of` alone does
    # not carry it, so a traversal reading that relation returns a different
    # path set from the Cypher it replaces.
    assert await postgres_rows("SELECT count(*) FROM graph.member_of WHERE member_artist_id = '11'") == [(0,)]
    assert await postgres_rows("SELECT count(*) FROM graph.artist_member_of WHERE member_artist_id = '11'") == [(1,)]

    # And the Discogs half is there in full, including the membership both
    # provenances assert, which is two rows rather than a collision.
    assert await postgres_rows("SELECT source FROM graph.artist_member_of WHERE member_artist_id = '7' ORDER BY 1") == [
        ("discogs",),
        ("musicbrainz",),
    ]

    # Re-running converges, and a membership nothing asserts is removed.
    await execute_all([("stale membership", "INSERT INTO graph.artist_member_of VALUES ('999', '998', 'discogs')")])
    assert await postgres_rows("SELECT source, row_count FROM graph.refresh_artist_member_of()") == EXPECTED_UNION_REPORT
    assert await postgres_rows(UNION_ROWS) == EXPECTED_UNION_ROWS
    assert await postgres_rows(
        "SELECT count(*) FROM graph.artist_member_of WHERE member_artist_id = %s AND group_artist_id = %s",
        STALE_MEMBERSHIP[:2],
    ) == [(0,)]

    # Both directions are indexed: an artist-to-artist walk enters this relation
    # from the group end as often as from the member end.
    definitions = [
        row[0] for row in await postgres_rows("SELECT indexdef FROM pg_indexes WHERE schemaname = 'graph' AND tablename = 'artist_member_of'")
    ]
    assert any(definition.endswith("(group_artist_id, member_artist_id)") for definition in definitions), definitions
    assert any("_pkey" in definition for definition in definitions), definitions


# ── The per-vertex degree that orders frontier expansion ─────────────────────
# `graph.vertex_degree` holds one bigint per vertex of the path traversal
# surface. It decides which side of a bidirectional search to expand next and
# which vertex of that side's frontier to expand first, and it changes no
# answer. Spike gm-database-schema-gkt.1 measured the distance-5 variance
# falling from 1,774 ms to 334 ms with it, answers unchanged.

DEGREE_ROWS = "SELECT kind, key, degree FROM graph.vertex_degree ORDER BY 1, 2"

# What the graph fixture's ten edges make of it, counted by hand.
#
# - `r`/`111` carries six: one `by_artist`, one `on_label`, one `derived_from`,
#   one `in_genre`, two `in_style`. Release 222 is malformed and carries none,
#   so it has no row at all rather than a zero.
# - `a`/`7` carries six: one `by_artist`, one `master_by_artist`, one `alias_of`
#   as the alias target, and three `artist_member_of` — `(7, 10)` under both
#   provenances plus `(8, 7)` from the group end.
# - `a`/`10` carries three, every one of them a membership: `(7, 10)` twice and
#   `(11, 10)` once. `a`/`11` carries two, its alias edge and the
#   MusicBrainz-only membership `graph.member_of` does not have.
# - `m`/`55` carries four, `g`/`Rock` two, `s`/`Prog Rock` two — each of those
#   is one release edge and one master edge — and `s`/`Psychedelic` one.
EXPECTED_DEGREE_ROWS = [
    ("a", "10", 3),
    ("a", "11", 2),
    ("a", "7", 6),
    ("a", "8", 1),
    ("g", "Rock", 2),
    ("l", "9", 1),
    ("m", "55", 4),
    ("r", "111", 6),
    ("s", "Prog Rock", 2),
    ("s", "Psychedelic", 1),
]

# What the refresh reports, a row per vertex kind in the order it returns them.
EXPECTED_DEGREE_REPORT = [("a", 4), ("g", 1), ("l", 1), ("m", 1), ("r", 1), ("s", 2)]

# A vertex no edge justifies. The refresh has to remove it, which is what
# truncate-and-insert buys and an upsert would not: a vertex that kept a stale
# degree would go on looking like a hub and the search would keep expanding the
# wrong frontier.
STALE_VERTEX = ("a", "997")

# The artist rows of `graph.vertex_degree` against `graph.artist_degree`, which
# counts the same artist the way Neo4j's `COUNT { (a)--() }` does. The two are
# the same sum with two deliberate substitutions, and this states them as
# arithmetic rather than as prose:
#
# - **`same_as` is subtracted.** `graph.artist_degree` counts the person-to-artist
#   edge because a Neo4j `:Artist` node carries it. No path query traverses it —
#   it is not one of the six types `_PATH_REL_TYPES` names — so an expansion
#   would never visit a `:Person` through it and counting it would misorder the
#   frontier rather than describe it.
# - **`member_of` is swapped for `artist_member_of`.** `graph.artist_degree`
#   counts the Discogs relation; the traversal surface reads the cross-provenance
#   union, where 61.6% of the edge class lives and where a membership both
#   providers assert is two rows because `source` is in the key. That dual
#   provenance counting twice is the deliberate choice this relation makes: the
#   number is the rows an expansion of that vertex will scan.
#
# Everything else is identical, so the query below has to return no rows.
DEGREE_AGREES_WITH_ARTIST_DEGREE = """
SELECT artist.artist_id,
       artist.degree AS artist_degree,
       COALESCE(vertex.degree, 0) AS vertex_degree,
       adjusted.expected
FROM graph.artist_degree AS artist
CROSS JOIN LATERAL (
    SELECT artist.degree
         - (SELECT count(*) FROM graph.same_as AS person WHERE person.artist_id = artist.artist_id)
         - (SELECT count(*) FROM graph.member_of AS discogs
             WHERE discogs.member_artist_id = artist.artist_id OR discogs.group_artist_id = artist.artist_id)
         + (SELECT count(*) FROM graph.artist_member_of AS union_edge
             WHERE union_edge.member_artist_id = artist.artist_id OR union_edge.group_artist_id = artist.artist_id)
      AS expected
) AS adjusted
LEFT JOIN graph.vertex_degree AS vertex ON vertex.kind = 'a' AND vertex.key = artist.artist_id
WHERE COALESCE(vertex.degree, 0) <> adjusted.expected
"""

# Every artist the traversal surface knows must be an artist the counter knows:
# the surface adds a relation to `artist_degree`'s set, it never adds a vertex,
# because every relation it reads that `artist_degree` does not — the union —
# has both endpoints in `graph.member_of`'s key space.
DEGREE_KNOWS_NO_UNCOUNTED_ARTIST = """
SELECT vertex.key
FROM graph.vertex_degree AS vertex
LEFT JOIN graph.artist_degree AS artist ON artist.artist_id = vertex.key
WHERE vertex.kind = 'a' AND artist.artist_id IS NULL
"""


@pytest.mark.asyncio
async def test_the_vertex_degree_sums_both_directions_of_the_traversal_surface() -> None:
    """One bigint per vertex, summed over the ten relations a path query walks."""
    await apply_schema()
    await seed_graph_fixtures()
    await bootstrap_the_loader_tables()

    # The bootstrap fill and the refresh share one body, so both answer this.
    assert await postgres_rows(DEGREE_ROWS) == EXPECTED_DEGREE_ROWS

    # A vertex with no edge has no row rather than a zero, which is what keeps
    # the relation the size of the traversal surface rather than of the catalog.
    # Release 222 is in `graph.release` and carries nothing.
    assert await postgres_rows("SELECT count(*) FROM graph.release WHERE release_id = '222'") == [(1,)]
    assert await postgres_rows("SELECT count(*) FROM graph.vertex_degree WHERE kind = 'r' AND key = '222'") == [(0,)]

    # `kind` is a discriminator alongside the key, not a prefix on it: the
    # pathfinder's node identity is the pair, and an equality on a concatenated
    # token could not use the text indexes the edge tables carry.
    assert await postgres_rows("SELECT count(*) FROM graph.vertex_degree WHERE strpos(key, ':') > 0") == [(0,)]
    assert await postgres_rows(
        """
        SELECT data_type FROM information_schema.columns
        WHERE table_schema = 'graph' AND table_name = 'vertex_degree' AND column_name = 'kind'
        """
    ) == [('"char"',)]

    # The MusicBrainz-only membership is in the count, which is the whole reason
    # the surface reads the union: artist 11 has an alias edge and nothing else
    # in `graph.member_of`, so a degree over that relation would read 1.
    assert await postgres_rows("SELECT degree FROM graph.vertex_degree WHERE kind = 'a' AND key = '11'") == [(2,)]
    assert await postgres_rows("SELECT count(*) FROM graph.member_of WHERE member_artist_id = '11' OR group_artist_id = '11'") == [(0,)]


@pytest.mark.asyncio
async def test_the_vertex_degree_agrees_with_artist_degree_on_every_artist() -> None:
    """The artist rows are `graph.artist_degree` with two deliberate substitutions."""
    await apply_schema()
    await seed_graph_fixtures()
    await bootstrap_the_loader_tables()

    # Neither comparison below is empty agreeing with empty. `graph.artist_degree`
    # is seven, not four: artists 21, 23, and 25 (gm-database-schema-ug3v) are new
    # `graph.same_as` targets of a track-only credit. `graph.track_by_artist`'s own
    # performers (22, 24, 26) are not among them -- that relation is not one of
    # the sources `artist_degree` sums either. `vertex_degree` stays four --
    # `same_as` is not one of the ten path relations it sums (it is subtracted
    # in the formula below), so a `same_as`-only artist never gets a row there.
    assert await postgres_rows("SELECT count(*) FROM graph.artist_degree") == [(7,)]
    assert await postgres_rows("SELECT count(*) FROM graph.vertex_degree WHERE kind = 'a'") == [(4,)]

    assert await postgres_rows(DEGREE_AGREES_WITH_ARTIST_DEGREE) == []
    assert await postgres_rows(DEGREE_KNOWS_NO_UNCOUNTED_ARTIST) == []

    # Artist 8 is the case where both substitutions are inert — it is in no
    # `same_as` row, and its one membership is Discogs-only, so the union holds
    # exactly the row `graph.member_of` does. There the two relations agree
    # outright, which is what says the adjustment above is an adjustment rather
    # than a licence to differ.
    assert await postgres_rows("SELECT degree FROM graph.artist_degree WHERE artist_id = '8'") == [(1,)]
    assert await postgres_rows("SELECT degree FROM graph.vertex_degree WHERE kind = 'a' AND key = '8'") == [(1,)]

    # And these are the two artists that deliberately differ, with the reason.
    # Artist 10 is asserted a member by both providers and by two artists, so
    # the union holds three rows where `graph.member_of` holds one — the dual
    # provenance counts twice, because an expansion really does scan both rows.
    assert await postgres_rows("SELECT degree FROM graph.artist_degree WHERE artist_id = '10'") == [(1,)]
    assert await postgres_rows("SELECT degree FROM graph.vertex_degree WHERE kind = 'a' AND key = '10'") == [(3,)]
    assert await postgres_rows("SELECT count(*) FROM graph.artist_member_of WHERE group_artist_id = '10'") == [(3,)]

    # Artist 7 is the case where the two substitutions cancel: it loses one
    # `same_as` edge and swaps two Discogs memberships for three union rows.
    assert await postgres_rows("SELECT count(*) FROM graph.same_as WHERE artist_id = '7'") == [(1,)]
    assert await postgres_rows("SELECT degree FROM graph.artist_degree WHERE artist_id = '7'") == [(6,)]
    assert await postgres_rows("SELECT degree FROM graph.vertex_degree WHERE kind = 'a' AND key = '7'") == [(6,)]


@pytest.mark.asyncio
async def test_the_vertex_degree_refresh_reports_every_kind_and_converges() -> None:
    """Re-running changes nothing, and a vertex no edge justifies is removed."""
    await apply_schema()
    await seed_graph_fixtures()
    await bootstrap_the_loader_tables()

    reported = await postgres_rows("SELECT kind, row_count FROM graph.refresh_vertex_degree()")
    assert reported == EXPECTED_DEGREE_REPORT
    assert await postgres_rows(DEGREE_ROWS) == EXPECTED_DEGREE_ROWS

    # Re-running converges, and a vertex nothing asserts is removed.
    await execute_all([("stale vertex", "INSERT INTO graph.vertex_degree VALUES ('a', '997', 99)")])
    assert await postgres_rows("SELECT kind, row_count FROM graph.refresh_vertex_degree()") == EXPECTED_DEGREE_REPORT
    assert await postgres_rows(DEGREE_ROWS) == EXPECTED_DEGREE_ROWS
    assert await postgres_rows(
        "SELECT count(*) FROM graph.vertex_degree WHERE kind = %s AND key = %s",
        STALE_VERTEX,
    ) == [(0,)]

    # The pair is the key, so the pathfinder's lookup is a point read on the
    # primary key and the relation carries nothing else. That is the whole
    # argument for it: one bigint per vertex against a second copy of the edges.
    definitions = [
        row[0] for row in await postgres_rows("SELECT indexdef FROM pg_indexes WHERE schemaname = 'graph' AND tablename = 'vertex_degree'")
    ]
    assert len(definitions) == 1, definitions
    assert definitions[0].endswith("(kind, key)"), definitions


# The widening guard is proved against one column; the four are generated from
# the same helper, so what holds for this one holds for all of them.
WIDENED_TABLE = "labels"
WIDENED_COLUMN = "discogs_label_id"
WIDENED_VIEW = "graph.mb_label"


async def dependent_views_on(table: str, column: str) -> list[str]:
    """Return the views reading one MusicBrainz column, by the catalog's own account."""
    rows = await postgres_rows(
        """
        SELECT DISTINCT dependent.relnamespace::regnamespace::text || '.' || dependent.relname
        FROM pg_depend AS dependency
        JOIN pg_rewrite AS rule ON rule.oid = dependency.objid
        JOIN pg_class AS dependent ON dependent.oid = rule.ev_class
        WHERE dependency.classid = 'pg_rewrite'::regclass
          AND dependency.refclassid = 'pg_class'::regclass
          AND dependency.refobjid = ('musicbrainz.' || %s)::regclass
          AND dependency.refobjsubid = (
              SELECT attribute.attnum
              FROM pg_attribute AS attribute
              WHERE attribute.attrelid = ('musicbrainz.' || %s)::regclass
                AND attribute.attname = %s
          )
          AND dependent.relkind IN ('v', 'm')
          AND dependent.oid <> ('musicbrainz.' || %s)::regclass
        ORDER BY 1
        """,
        (table, table, column, table),
    )
    return [row[0] for row in rows]


async def column_type(table: str, column: str) -> str:
    rows = await postgres_rows(
        """
        SELECT data_type FROM information_schema.columns
        WHERE table_schema = 'musicbrainz' AND table_name = %s AND column_name = %s
        """,
        (table, column),
    )
    return str(rows[0][0])


@pytest.mark.asyncio
async def test_the_widening_skips_a_narrow_column_a_view_already_reads() -> None:
    """The guard survives the one state that used to be unrecoverable.

    `_execute_schema_statements` logs a failed statement and continues, so a run
    whose ALTER failed transiently still goes on to create the graph views over
    the column it failed to widen. This reconstructs exactly that state — narrow
    column, dependent view — and proves the widening now says why it is skipping
    instead of raising `cannot alter type of a column used by a view or rule` on
    this and every later startup.

    Restores the schema before it returns; the widening is re-run with the view
    dropped, which is the path that does widen.
    """
    await apply_schema()
    assert await column_type(WIDENED_TABLE, WIDENED_COLUMN) == "bigint"
    assert await dependent_views_on(WIDENED_TABLE, WIDENED_COLUMN) == [WIDENED_VIEW]

    definition_rows = await postgres_rows("SELECT pg_get_viewdef(%s::regclass, true)", (WIDENED_VIEW,))
    definition = str(definition_rows[0][0]).rstrip().rstrip(";")

    notices: list[str] = []
    connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params())
    await connection.set_autocommit(True)
    connection.add_notice_handler(lambda diagnostic: notices.append(diagnostic.message_primary or ""))
    try:
        async with connection.cursor() as cursor:
            await cursor.execute(f"DROP VIEW {WIDENED_VIEW} CASCADE")
            await cursor.execute(f"ALTER TABLE musicbrainz.{WIDENED_TABLE} ALTER COLUMN {WIDENED_COLUMN} TYPE INTEGER")
            await cursor.execute(f"CREATE VIEW {WIDENED_VIEW} AS {definition}")

            # The state the reviewer reproduced, now established on purpose.
            assert await column_type(WIDENED_TABLE, WIDENED_COLUMN) == "integer"
            assert await dependent_views_on(WIDENED_TABLE, WIDENED_COLUMN) == [WIDENED_VIEW]

            notices.clear()
            # Must not raise. Before the dependency check this was
            # `cannot alter type of a column used by a view or rule`.
            await cursor.execute(_widen_to_bigint(WIDENED_TABLE, WIDENED_COLUMN))

        assert any(f"skipping widen of musicbrainz.{WIDENED_TABLE}.{WIDENED_COLUMN}" in notice for notice in notices), notices
        assert any(WIDENED_VIEW in notice for notice in notices), notices
        # Skipped, not silently half-applied.
        assert await column_type(WIDENED_TABLE, WIDENED_COLUMN) == "integer"
    finally:
        async with connection.cursor() as cursor:
            await cursor.execute(f"DROP VIEW IF EXISTS {WIDENED_VIEW} CASCADE")
        await connection.close()
        await apply_schema()

    # With nothing depending on it the same statement does widen, and the run
    # that widens it rebuilds every view the reproduction dropped.
    assert await column_type(WIDENED_TABLE, WIDENED_COLUMN) == "bigint"
    assert await dependent_views_on(WIDENED_TABLE, WIDENED_COLUMN) == [WIDENED_VIEW]
    view_rows = await postgres_rows("SELECT viewname FROM pg_views WHERE schemaname = 'graph'")
    assert {row[0] for row in view_rows} == EXPECTED_GRAPH_VIEWS
    # On PostgreSQL 19 the CASCADE also pruned the view's element out of
    # `graph.catalog`; the same run re-declares it.


# ── The shortest path# ── The shortest path across the traversal surface ───────────────────────────
# `graph.find_shortest_path` is the vertex-at-a-time bidirectional search spike
# gm-database-schema-gkt.1 prototyped. These are its answers, on a real engine,
# against a hand-computed table.
#
# **How the spike's endpoints map onto this fixture.** They do not, and the
# spike says why: `sql/pick-endpoints.sql` records that its ids are catalog-scale
# ids — artist 5665 of 120,000 at the synthetic scale, artist 1 of 1,500 at its
# own fixture scale — and that measuring them against a catalog that does not
# hold them "would be measuring nothing eight times". This fixture is five
# documents. So what is carried across is the CASE LIST rather than the ids:
# `bench/workloads.py` names eight cases — `d1-artist-artist`,
# `d1-artist-release`, `d2` through `d6` artist-to-artist, and `unreachable` —
# and every one of them is reproduced here as the same case shape over vertices
# this fixture really has, with the depth computed by hand from the ten edges
# the graph fixture seeds rather than read back from the function.
#
# The seeded graph is ten vertices and fourteen edges, and its eccentricity is
# 3: there is no pair at distance 4 in it and no pair that cannot reach each
# other. So the four shallow cases are fixture-native and the rest need a
# subgraph, which `seed_path_fixture` builds — an eleven-hop chain of Discogs
# memberships and one two-artist component nothing else touches. The chain is
# written into `graph.member_of`, which is a relation a loader owns, and the
# union and the degree are then REBUILT from it by their own refresh functions,
# so the fixture is assembled the way production assembles it rather than by
# writing rows into a derived relation behind the refresh's back.
#
# The fixture graph, for the hand computation. Artist 7 is on release 111 and
# master 55; release 111 carries label 9, genre Rock, and styles Prog Rock and
# Psychedelic, and derives from master 55; master 55 carries Rock and Prog Rock;
# artist 11 is an alias of 7; artist 8 is a member of 7; artist 7 is a member of
# 10 under BOTH provenances and artist 11 is a member of 10 under MusicBrainz
# alone.
PATH_CHAIN_HOPS = 11
PATH_CHAIN_HEAD = "9000"
PATH_ISOLATED_PAIR = ("9101", "9102")


def path_chain_artist(offset: int) -> str:
    """Return the id of the chain artist OFFSET hops from the head."""
    return str(int(PATH_CHAIN_HEAD) + offset)


async def seed_path_fixture() -> None:
    """Write the Discogs memberships the deep and unreachable cases need, then rebuild.

    An eleven-hop chain, so a pair at every distance from 1 to 11 exists, and one
    two-artist component nothing else in the fixture touches, so an unreachable
    pair exists. Both go into `graph.member_of`, which `discogs-sql-loader` owns;
    `graph.artist_member_of` and `graph.vertex_degree` are then rebuilt from it
    by the functions the contract names, so nothing here writes a derived
    relation directly and a later rebuild would reproduce exactly this graph.

    Re-running it is a no-op: the inserts are ON CONFLICT DO NOTHING and both
    refreshes are truncate-and-insert.
    """
    connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params())
    async with connection, connection.cursor() as cursor:
        await cursor.execute(
            """
            INSERT INTO graph.member_of (member_artist_id, group_artist_id)
            SELECT (%s::bigint + hop)::text, (%s::bigint + hop + 1)::text
            FROM generate_series(0, %s - 1) AS hop
            ON CONFLICT DO NOTHING
            """,
            (PATH_CHAIN_HEAD, PATH_CHAIN_HEAD, PATH_CHAIN_HOPS),
        )
        await cursor.execute(
            "INSERT INTO graph.member_of (member_artist_id, group_artist_id) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            PATH_ISOLATED_PAIR,
        )
        await cursor.execute("SELECT count(*) FROM graph.refresh_artist_member_of()")
        await cursor.execute("SELECT count(*) FROM graph.refresh_vertex_degree()")
        await connection.commit()


PILOT_ARTISTS = {
    "1": "Anchor",
    "2": "Near Two",
    "3": "Near Three",
    "4": "Near Four",
    "5": "Far Five",
    "6": "Far Six",
    "7": "Far Seven",
}

PILOT_RELEASES = {
    "2001": ["1", "2"],
    "2002": ["1", "2"],
    "2003": ["1", "2", "3"],
    "2004": ["1", "3"],
    "2005": ["1", "4"],
    "2006": ["2", "5"],
    "2007": ["3", "5"],
    "2008": ["3", "6"],
    "2009": ["4", "7"],
}


async def seed_pilot_fixtures() -> None:
    """Write the collaboration neighbourhood the relational path tests read."""
    connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params())
    async with connection, connection.cursor() as cursor:
        for artist_id, name in PILOT_ARTISTS.items():
            await cursor.execute(
                "INSERT INTO artists (data_id, hash, data) VALUES (%s, %s, %s) ON CONFLICT (data_id) DO NOTHING",
                (artist_id, f"hash-artist-{artist_id}", Jsonb({"id": int(artist_id), "name": name})),
            )
        for release_id, artist_ids in PILOT_RELEASES.items():
            document = {
                "id": int(release_id),
                "title": f"Pilot {release_id}",
                "artists": [{"id": int(artist_id), "name": PILOT_ARTISTS[artist_id]} for artist_id in artist_ids],
            }
            await cursor.execute(
                "INSERT INTO releases (data_id, hash, data) VALUES (%s, %s, %s) ON CONFLICT (data_id) DO NOTHING",
                (release_id, f"hash-release-{release_id}", Jsonb(document)),
            )
        await connection.commit()


async def seed_explore_fixture() -> None:
    """Build the complete, engine-independent fixture behind Explore's answers.

    Artist 4 is reached through the pilot collaboration neighbourhood, while
    the other expected vertices come from the graph and path fixtures.  Seed
    all three explicitly so this test does not depend on a PostgreSQL-19-only
    pilot test having run earlier in the session.
    """
    await seed_graph_fixtures()
    await seed_pilot_fixtures()
    await bootstrap_the_loader_tables()
    await seed_path_fixture()


# The answer set: one row per case, with the distance computed by hand from the
# graph above and never read back from the function. `None` for a depth means
# the case has no answer within its cap.
#
# The first block is the spike's four shallow cases over fixture vertices, and it
# deliberately crosses every vertex kind the surface carries — artist, release,
# master, label, genre, style — because the spike's own cases are artist-to-artist
# and artist-to-release and would exercise two of the six.
#
# The second block is the depth cases and the cap, on the chain. The third is
# what the clamp does at each end of `[1, 10]`. The fourth is the unreachable
# pair, in all three ways a pair can fail to be connected.
PATH_CASES: list[tuple[str, str, str, str, str, int | None, bool, int | None]] = [
    # name                          from            to               cap   found  depth
    ("identity", "a", "8", "a", "8", 6, True, 0),
    ("d1-artist-artist", "a", "8", "a", "7", 6, True, 1),
    ("d1-artist-release", "a", "7", "r", "111", 6, True, 1),
    # Artist 11 reaches 10 only through the MusicBrainz half of the union: it
    # asserts no Discogs membership at all, so this hop does not exist in
    # `graph.member_of` and a search over that relation alone would not find it.
    ("d1-artist-artist-musicbrainz", "a", "11", "a", "10", 6, True, 1),
    ("d1-release-genre", "r", "111", "g", "Rock", 6, True, 1),
    ("d1-master-style", "m", "55", "s", "Prog Rock", 6, True, 1),
    ("d2-artist-artist", "a", "8", "a", "10", 6, True, 2),
    ("d2-artist-release", "a", "8", "r", "111", 6, True, 2),
    # master 55 → release 111 → label 9. The master carries no label itself.
    ("d2-master-label", "m", "55", "l", "9", 6, True, 2),
    # Distance 3 is the fixture graph's eccentricity, and every pair that far
    # apart crosses a vertex kind: artist 8 reaches every other artist in two.
    # The spike's `d3-artist-artist` is therefore a chain case, with `d4`
    # through `d6`, and these are what distance 3 looks like on the fixture.
    ("d3-artist-artist", "a", PATH_CHAIN_HEAD, "a", path_chain_artist(3), 6, True, 3),
    ("d3-artist-label", "a", "8", "l", "9", 6, True, 3),
    ("d3-artist-genre", "a", "8", "g", "Rock", 6, True, 3),
    ("d3-label-artist", "l", "9", "a", "10", 6, True, 3),
    # style Psychedelic is on release 111 alone, so it is three hops from the
    # alias endpoint: Psychedelic → 111 → artist 7 → artist 11.
    ("d3-style-artist", "s", "Psychedelic", "a", "11", 6, True, 3),
    ("d4-artist-artist", "a", PATH_CHAIN_HEAD, "a", path_chain_artist(4), 6, True, 4),
    ("d5-artist-artist", "a", PATH_CHAIN_HEAD, "a", path_chain_artist(5), 6, True, 5),
    ("d6-artist-artist", "a", PATH_CHAIN_HEAD, "a", path_chain_artist(6), 6, True, 6),
    # The default of 6 is a real bound, not a suggestion.
    ("d7-beyond-the-default", "a", PATH_CHAIN_HEAD, "a", path_chain_artist(7), 6, False, None),
    ("d7-under-a-raised-cap", "a", PATH_CHAIN_HEAD, "a", path_chain_artist(7), 7, True, 7),
    # The clamp, at both ends. 99 becomes 10, which answers distance 10 and not
    # distance 11; 0 becomes 1, which answers distance 1 and not distance 2; and
    # a null max_depth is the default of 6 rather than no bound at all.
    ("d10-at-the-ceiling", "a", PATH_CHAIN_HEAD, "a", path_chain_artist(10), 99, True, 10),
    ("d11-past-the-ceiling", "a", PATH_CHAIN_HEAD, "a", path_chain_artist(11), 99, False, None),
    ("d1-at-the-floor", "a", PATH_CHAIN_HEAD, "a", path_chain_artist(1), 0, True, 1),
    ("d2-under-the-floor", "a", PATH_CHAIN_HEAD, "a", path_chain_artist(2), 0, False, None),
    ("d2-with-a-null-cap", "a", PATH_CHAIN_HEAD, "a", path_chain_artist(2), None, True, 2),
    ("d7-with-a-null-cap", "a", PATH_CHAIN_HEAD, "a", path_chain_artist(7), None, False, None),
    # Unreachable three ways: two live components, the chain against the
    # isolated pair, and a vertex the catalog does not hold at all.
    ("unreachable-across-components", "a", "8", "a", PATH_ISOLATED_PAIR[0], 6, False, None),
    ("unreachable-from-the-chain", "a", PATH_CHAIN_HEAD, "a", PATH_ISOLATED_PAIR[0], 6, False, None),
    ("unreachable-absent-vertex", "a", "8", "a", "424242", 6, False, None),
]

FIND_SHORTEST_PATH = 'SELECT found, depth, nodes, rels FROM graph.find_shortest_path(%s::"char", %s, %s::"char", %s, %s)'
EXPLORE_TRAVERSAL = 'SELECT id, name, type, path_names, rel_types, dist FROM graph.explore_traversal(%s::"char", %s, %s, %s)'

# The paths themselves, for the cases where the fixture admits exactly one and
# the answer is therefore not a tie broken arbitrarily. `shortestPath` promises
# nothing about which of several equal-length paths it returns, so the answer
# set above compares distances — which is the rule `bench/compare.py` states in
# code — and only these are pinned whole.
PATH_ROUTES: dict[str, tuple[list[str], list[str]]] = {
    "identity": (["a:8"], []),
    "d1-artist-artist": (["a:8", "a:7"], ["MEMBER_OF"]),
    "d1-artist-release": (["a:7", "r:111"], ["BY"]),
    "d1-artist-artist-musicbrainz": (["a:11", "a:10"], ["MEMBER_OF"]),
    "d2-artist-artist": (["a:8", "a:7", "a:10"], ["MEMBER_OF", "MEMBER_OF"]),
    "d2-artist-release": (["a:8", "a:7", "r:111"], ["MEMBER_OF", "BY"]),
    "d2-master-label": (["m:55", "r:111", "l:9"], ["DERIVED_FROM", "ON"]),
    "d3-artist-label": (["a:8", "a:7", "r:111", "l:9"], ["MEMBER_OF", "BY", "ON"]),
    "d3-style-artist": (["s:Psychedelic", "r:111", "a:7", "a:11"], ["IS", "BY", "ALIAS_OF"]),
    "d3-label-artist": (["l:9", "r:111", "a:7", "a:10"], ["ON", "BY", "MEMBER_OF"]),
}


@pytest.mark.asyncio
async def test_the_shortest_path_answers_the_spike_case_set_at_fixture_scale() -> None:
    """Every case in the spike's list, at this fixture's scale, against a hand-computed table."""
    await apply_schema()
    await seed_graph_fixtures()
    await bootstrap_the_loader_tables()
    await seed_path_fixture()

    # Neither half of the fixture is empty agreeing with empty.
    assert await postgres_rows("SELECT count(*) FROM graph.artist_member_of WHERE member_artist_id >= '9000'") == [(PATH_CHAIN_HOPS + 1,)]
    assert await postgres_rows("SELECT count(*) FROM graph.vertex_degree WHERE kind = 'a' AND key >= '9000'") == [(PATH_CHAIN_HOPS + 3,)]

    answers: dict[str, tuple[bool, int | None]] = {}
    for name, from_kind, from_key, to_kind, to_key, cap, _found, _depth in PATH_CASES:
        rows = await postgres_rows(FIND_SHORTEST_PATH, (from_kind, from_key, to_kind, to_key, cap))
        # One row always, even when there is no path: the caller asked a
        # question and "no path within this depth" is an answer to it.
        assert len(rows) == 1, name
        found, depth, nodes, rels = rows[0]
        answers[name] = (found, depth)

        if not found:
            assert (depth, nodes, rels) == (None, None, None), name
            continue

        # `nodes` is one longer than `rels`, and both agree with the depth.
        assert len(nodes) == depth + 1, name
        assert len(rels) == depth, name
        # Every vertex is the `kind:key` pair, and no vertex repeats — a
        # membership both provenances assert is two rows of the union and one
        # visited vertex, which is what the seen set being keyed on the vertex
        # buys.
        assert len(set(nodes)) == len(nodes), name
        for node in nodes:
            kind, _, key = node.partition(":")
            assert kind in _VERTEX_KIND_NAMES, name
            assert key, name
        # `rels` carries the Neo4j relationship type and never the provenance.
        assert set(rels) <= {"BY", "ON", "IS", "ALIAS_OF", "MEMBER_OF", "DERIVED_FROM"}, name
        assert nodes[0] == f"{from_kind}:{from_key}", name
        assert nodes[-1] == f"{to_kind}:{to_key}", name

        if name in PATH_ROUTES:
            assert (nodes, rels) == PATH_ROUTES[name], name

    expected = {name: (found, depth) for name, _fk, _fkey, _tk, _tkey, _cap, found, depth in PATH_CASES}
    assert answers == expected


@pytest.mark.asyncio
async def test_the_shortest_path_reaches_a_membership_only_musicbrainz_asserts() -> None:
    """MEMBER_OF crosses both provenances, so the union is what is walked, not graph.member_of."""
    await apply_schema()
    await seed_graph_fixtures()
    await bootstrap_the_loader_tables()
    await seed_path_fixture()

    # Artist 11 asserts no Discogs membership at all, so this hop exists only in
    # the union. 61.6% of the MEMBER_OF edge class is MusicBrainz provenance.
    assert await postgres_rows("SELECT count(*) FROM graph.member_of WHERE member_artist_id = '11' OR group_artist_id = '11'") == [(0,)]
    assert await postgres_rows(FIND_SHORTEST_PATH, ("a", "11", "a", "10", 6)) == [(True, 1, ["a:11", "a:10"], ["MEMBER_OF"])]

    # And the membership BOTH provenances assert is two rows of the union and
    # one hop of one path, reported as the type rather than as either provider.
    assert await postgres_rows("SELECT count(*) FROM graph.artist_member_of WHERE member_artist_id = '7' AND group_artist_id = '10'") == [(2,)]
    rows = await postgres_rows(FIND_SHORTEST_PATH, ("a", "7", "a", "10", 6))
    assert rows == [(True, 1, ["a:7", "a:10"], ["MEMBER_OF"])]


@pytest.mark.asyncio
async def test_the_shortest_path_walks_no_relation_a_path_query_never_traverses() -> None:
    """`same_as`, `part_of`, `sublabel_of`, `credited_on`, `issued_on` are not path edges."""
    await apply_schema()
    await seed_graph_fixtures()
    await bootstrap_the_loader_tables()
    await seed_path_fixture()

    # Person "Alice" is `same_as` artist 7 and `credited_on` release 111, so a
    # search that walked either would reach her; `:Person` is not on the
    # traversal surface, so nothing reaches her and no path runs through her.
    assert await postgres_rows("SELECT count(*) FROM graph.same_as WHERE person_name = 'Alice'") == [(1,)]
    assert await postgres_rows(FIND_SHORTEST_PATH, ("a", "7", "a", "8", 6)) == [(True, 1, ["a:7", "a:8"], ["MEMBER_OF"])]

    # Style "Prog Rock" is `part_of` genre "Rock". Walking that edge would make
    # them adjacent; the real distance is two, through release 111 or master 55.
    assert await postgres_rows("SELECT count(*) FROM graph.part_of WHERE style_name = 'Prog Rock' AND genre_name = 'Rock'") == [(1,)]
    found, depth, nodes, _rels = (await postgres_rows(FIND_SHORTEST_PATH, ("s", "Prog Rock", "g", "Rock", 6)))[0]
    assert (found, depth) == (True, 2)
    assert nodes[1] in {"r:111", "m:55"}

    # Label 9 is `sublabel_of` label 12. Walking that edge would put 12 one hop
    # from label 9; nothing on the surface reaches it, so it is unreachable.
    assert await postgres_rows("SELECT count(*) FROM graph.sublabel_of WHERE sublabel_id = '9' AND parent_label_id = '12'") == [(1,)]
    assert await postgres_rows(FIND_SHORTEST_PATH, ("l", "9", "l", "12", 6)) == [(False, None, None, None)]


@pytest.mark.asyncio
async def test_explore_traversal_matches_the_fixture_discovery_sets() -> None:
    """The spike's *1..1 through *1..3 case shape, as exact fixture-scale sets."""
    await apply_schema()
    await seed_explore_fixture()

    expected = {
        1: {("7", "artist", 1)},
        2: {("7", "artist", 1), ("10", "artist", 2), ("11", "artist", 2)},
        3: {
            ("7", "artist", 1),
            ("10", "artist", 2),
            ("11", "artist", 2),
            ("4", "artist", 3),
            ("9", "label", 3),
            ("Rock", "genre", 3),
            ("Prog Rock", "style", 3),
            ("Psychedelic", "style", 3),
        },
    }

    for hops, discoveries in expected.items():
        rows = await postgres_rows(EXPLORE_TRAVERSAL, ("a", "8", hops, 100))
        assert {(row[0], row[2], row[5]) for row in rows} == discoveries
        for _id, _name, _type, path_names, rel_types, dist in rows:
            assert len(path_names) == dist + 1
            assert len(rel_types) == dist
            assert path_names[0] == "8"
            assert set(rel_types) <= {"BY", "ON", "IS", "ALIAS_OF", "MEMBER_OF", "DERIVED_FROM"}


@pytest.mark.asyncio
async def test_explore_traversal_enforces_its_caps_and_mandatory_limit() -> None:
    await apply_schema()
    await seed_explore_fixture()

    # The hop argument is clamped at both ends and the row limit is a hard cap.
    floor = await postgres_rows(EXPLORE_TRAVERSAL, ("a", "8", 0, 100))
    ceiling = await postgres_rows(EXPLORE_TRAVERSAL, ("a", "8", 99, 100))
    assert {(row[0], row[5]) for row in floor} == {("7", 1)}
    assert len(ceiling) == 8
    assert len(await postgres_rows(EXPLORE_TRAVERSAL, ("a", "8", 3, 2))) == 2

    connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params())
    async with connection, connection.cursor() as cursor:
        with pytest.raises(psycopg.errors.NullValueNotAllowed):
            await cursor.execute(EXPLORE_TRAVERSAL, ("a", "8", 2, None))


# ── Two sessions searching at once ───────────────────────────────────────────
# The seen set is `TEMPORARY`, and the spike is explicit that this is the whole
# reason: its own harness used `UNLOGGED` so plans could be captured from a
# second connection, and it records that "two concurrent path requests against
# one unlogged table would corrupt each other's search". This is the proof that
# the shipped relation is per-session — that two searches running at the same
# time hold two different relations in two different temporary schemas, cannot
# see each other's rows, and answer their own question.

SEEN_SET_IDENTITY = """
SELECT namespace.nspname, relation.oid::bigint
FROM pg_class AS relation
JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
WHERE relation.oid = to_regclass('pg_temp.graph_path_seen')
"""

SEEN_SET_ROWS = "SELECT count(*) FROM pg_temp.graph_path_seen"

# Two searches whose seen sets are different sizes and whose answers differ, so
# a shared relation could not produce both.
CONCURRENT_SEARCHES = (
    ("a", PATH_CHAIN_HEAD, "a", path_chain_artist(6), 6, (True, 6)),
    ("a", "8", "a", "9", 6, (False, None)),
)


async def rows_on(connection: psycopg.AsyncConnection[Any], query: str, parameters: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Run one query on an already-open connection, so the session is the unit."""
    async with connection.cursor() as cursor:
        await cursor.execute(query, parameters)
        return await cursor.fetchall()


@pytest.mark.asyncio
async def test_two_sessions_search_at_once_without_cross_talk() -> None:
    """Two concurrent searches hold two seen sets, and each answers its own question."""
    await apply_schema()
    await seed_graph_fixtures()
    await bootstrap_the_loader_tables()
    await seed_path_fixture()

    parameters = initializer._postgres_connection_params()
    first = await psycopg.AsyncConnection.connect(**parameters)
    second = await psycopg.AsyncConnection.connect(**parameters)
    try:
        # 1. In flight at the same time, many times over. Each round issues both
        # searches concurrently and commits both, so every round also re-enters
        # a seen set the previous round's commit has just emptied.
        for _round in range(25):
            results = await asyncio.gather(
                *(
                    rows_on(connection, FIND_SHORTEST_PATH, (from_kind, from_key, to_kind, to_key, cap))
                    for connection, (from_kind, from_key, to_kind, to_key, cap, _expected) in zip((first, second), CONCURRENT_SEARCHES, strict=True)
                )
            )
            for rows, (_fk, _fkey, _tk, _tkey, _cap, expected) in zip(results, CONCURRENT_SEARCHES, strict=True):
                assert len(rows) == 1
                assert (rows[0][0], rows[0][1]) == expected
            await asyncio.gather(first.commit(), second.commit())

        # 2. The two seen sets are two relations, in two temporary schemas.
        # A session cannot even name the other's: `pg_temp` is its own.
        ((first_schema, first_oid),) = await rows_on(first, SEEN_SET_IDENTITY)
        ((second_schema, second_oid),) = await rows_on(second, SEEN_SET_IDENTITY)
        assert first_schema.startswith("pg_temp"), first_schema
        assert second_schema.startswith("pg_temp"), second_schema
        assert first_schema != second_schema
        assert first_oid != second_oid

        # 3. With both transactions held open, neither sees the other's rows.
        # The chain search seeds two endpoints and discovers five more vertices
        # before it touches; the unreachable search closes the component around
        # artist 9, which nothing on the surface holds, after seeding two. A
        # shared relation would make these two numbers the same.
        await asyncio.gather(
            *(
                rows_on(connection, FIND_SHORTEST_PATH, (from_kind, from_key, to_kind, to_key, cap))
                for connection, (from_kind, from_key, to_kind, to_key, cap, _expected) in zip((first, second), CONCURRENT_SEARCHES, strict=True)
            )
        )
        first_rows = (await rows_on(first, SEEN_SET_ROWS))[0][0]
        second_rows = (await rows_on(second, SEEN_SET_ROWS))[0][0]
        assert first_rows > second_rows > 0, (first_rows, second_rows)

        # 4. And the commit is what empties them, not the next call: that is the
        # per-call TRUNCATE the spike measured at about 4 ms of a 5.2 ms floor,
        # retired. Both are non-empty until they commit, and empty afterwards.
        await asyncio.gather(first.commit(), second.commit())
        assert await rows_on(first, SEEN_SET_ROWS) == [(0,)]
        assert await rows_on(second, SEEN_SET_ROWS) == [(0,)]
    finally:
        await first.close()
        await second.close()


@pytest.mark.asyncio
async def test_two_searches_in_one_transaction_do_not_pollute_each_other() -> None:
    """The commit empties the seen set, so a second call in one transaction must too."""
    await apply_schema()
    await seed_graph_fixtures()
    await bootstrap_the_loader_tables()
    await seed_path_fixture()

    connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params())
    async with connection:
        deep = ("a", PATH_CHAIN_HEAD, "a", path_chain_artist(6), 6)
        shallow = ("a", PATH_CHAIN_HEAD, "a", path_chain_artist(2), 6)
        assert await rows_on(connection, FIND_SHORTEST_PATH, deep) == [
            (True, 6, [f"a:{path_chain_artist(hop)}" for hop in range(7)], ["MEMBER_OF"] * 6)
        ]
        # Same transaction, no commit between: the first search's seen set is
        # still in the relation when the second one starts, and the second
        # answer has to be the second question's.
        assert (await rows_on(connection, SEEN_SET_ROWS))[0][0] > 0
        assert await rows_on(connection, FIND_SHORTEST_PATH, shallow) == [
            (True, 2, [f"a:{path_chain_artist(hop)}" for hop in range(3)], ["MEMBER_OF"] * 2)
        ]
        # And the deep one still answers deeply afterwards.
        assert (await rows_on(connection, FIND_SHORTEST_PATH, deep))[0][1] == 6
        await connection.commit()


# ── The artist embedding table's exact cosine ordering (PG19 tier only) ──────


def _halfvec_literal(active: dict[int, float], dims: int = 128) -> str:
    """Return a `[...]` literal for a 128-dim vector, one at ACTIVE's indices."""
    values = [0.0] * dims
    for index, value in active.items():
        values[index] = value
    return "[" + ",".join(str(value) for value in values) + "]"


@pytest.mark.asyncio
async def test_the_artist_embeddings_table_orders_rows_by_exact_cosine_distance() -> None:
    """Insert three 128-dim vectors and confirm ORDER BY <=> ranks them by angle.

    No HNSW index exists yet — that is the next bead — so this is a brute-force
    exact search, which is exactly what the acceptance criterion asks for.
    """
    await apply_schema()
    if not await vector_extension_present():
        pytest.skip("pgvector is not installed on this tier; see the absence assertion in the idempotence test")

    marker = f"cosine-order-{uuid4().hex}"
    near, mid, far = f"{marker}-near", f"{marker}-mid", f"{marker}-far"
    query_vector = _halfvec_literal({0: 1.0})

    connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params())
    async with connection, connection.cursor() as cursor:
        await cursor.execute(
            """
            INSERT INTO public.artist_embeddings
                (artist_id, model_version, embedding, source_dump_id, source_dump_date)
            VALUES
                (%(near)s, %(v)s, %(near_vec)s::halfvec, 'dump-1', '2026-09-01'),
                (%(mid)s,  %(v)s, %(mid_vec)s::halfvec,  'dump-1', '2026-09-01'),
                (%(far)s,  %(v)s, %(far_vec)s::halfvec,  'dump-1', '2026-09-01')
            """,
            {
                "near": near,
                "mid": mid,
                "far": far,
                "v": marker,
                # near == the query vector (distance 0); mid leans mostly toward
                # it (small angle); far is orthogonal (distance 1, the maximum).
                "near_vec": _halfvec_literal({0: 1.0}),
                "mid_vec": _halfvec_literal({0: 0.9, 1: 0.1}),
                "far_vec": _halfvec_literal({1: 1.0}),
            },
        )
        await connection.commit()

    ordered = await postgres_rows(
        """
        SELECT artist_id
        FROM public.artist_embeddings
        WHERE model_version = %s
        ORDER BY embedding <=> %s::halfvec
        """,
        (marker, query_vector),
    )
    assert ordered == [(near,), (mid,), (far,)]


# ── "available but not superuser" (PG19 tier only) ────────────────────────────


async def _rows_in(database: str, query: str, parameters: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    """Query DATABASE, rather than the suite's default database, once."""
    params = {**initializer._postgres_connection_params(), "dbname": database}
    connection = await psycopg.AsyncConnection.connect(**params)
    async with connection, connection.cursor() as cursor:
        await cursor.execute(query, parameters)
        return await cursor.fetchall()


@pytest.mark.asyncio
async def test_available_but_not_superuser_skips_the_extension_with_zero_failures() -> None:
    """pgvector is available but the connecting role cannot install it (not a
    superuser, and `vector` carries no `trusted = true`) — the schema still
    initializes cleanly, with the extension and `artist_embeddings` skipped
    rather than failed.

    A fresh, owned-by-the-test-role database is used so the extension is
    genuinely not-yet-installed there, unlike the suite's shared database
    (already initialized as a superuser by `apply_schema` elsewhere in this
    file).
    """
    if not await vector_extension_present():
        pytest.skip("pgvector is not installed on this tier; this scenario only exists when it is available")

    admin_params = initializer._postgres_connection_params()
    role = f"embed_test_nonsuper_{uuid4().hex[:12]}"
    password = uuid4().hex
    database = f"embed_test_db_{uuid4().hex[:12]}"

    admin_connection = await psycopg.AsyncConnection.connect(**{**admin_params, "dbname": "postgres"}, autocommit=True)
    try:
        async with admin_connection.cursor() as cursor:
            # NOSUPERUSER, and no CREATEROLE either: this role also exercises
            # the pipeline role's own guard, which must skip it too, without
            # that skip counting as a failure either.
            await cursor.execute(
                sql.SQL("CREATE ROLE {role} LOGIN PASSWORD {password} NOSUPERUSER NOCREATEROLE").format(
                    role=sql.Identifier(role), password=sql.Literal(password)
                )
            )
            # PostgreSQL 15+ makes a database's `public` schema owned by
            # `pg_database_owner`, whose membership tracks the database's own
            # owner — so making the test role the owner is what gives it
            # CREATE on `public` (and every schema it creates itself) without
            # granting it superuser or any catalog-wide privilege.
            await cursor.execute(
                sql.SQL("CREATE DATABASE {database} OWNER {role}").format(database=sql.Identifier(database), role=sql.Identifier(role))
            )

        nonsuper_params = {**admin_params, "dbname": database, "user": role, "password": password}
        succeeded = await initializer._apply_postgres_schema(nonsuper_params)
        assert succeeded is True, "a non-superuser connection must still leave the schema healthy (zero failures)"

        assert await _rows_in(database, "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')") == [(False,)]
        assert (
            await _rows_in(
                database,
                "SELECT 1 FROM information_schema.tables WHERE table_schema = 'public' AND table_name = 'artist_embeddings'",
            )
            == []
        )
        # `pg_roles` is cluster-wide, not per-database, so `embedding_pipeline`
        # is visible here regardless of this run (the suite's earlier,
        # superuser passes already created it against the shared database).
        # What this run's own CREATEROLE guard controls is whether it granted
        # that role anything in *this* database's `graph` schema — and here it
        # must not have, since the test role holds no CREATEROLE either.
        assert (
            await _rows_in(
                database,
                "SELECT 1 FROM information_schema.role_table_grants WHERE grantee = %s AND table_schema = 'graph'",
                (EMBEDDING_PIPELINE_ROLE,),
            )
            == []
        )
    finally:
        await admin_connection.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database)))
        await admin_connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
        await admin_connection.close()


# ── "already installed" ignores superuser (PG19 tier only) ───────────────────


@pytest.mark.asyncio
async def test_already_installed_creates_artist_embeddings_for_a_non_superuser_too() -> None:
    """Once `vector` is installed, a non-superuser connection still gets the
    full vector schema -- `_vector_schema_skip_reasons` treats "already
    installed" as unconditional, the same as any other `IF NOT EXISTS` object
    in this module, regardless of the connecting role's privilege.

    Distinct from `test_available_but_not_superuser_skips_the_extension_with_zero_failures`
    above: that test covers *not yet installed and not superuser* (skips);
    this one covers *already installed, connecting role merely lacks
    superuser* (creates everything the extension gates, in full). lhp2.2
    covered this branch with unit tests only (`_vector_schema_skip_reasons`);
    this is the real-engine proof.
    """
    if not await vector_extension_present():
        pytest.skip("pgvector is not installed on this tier; this scenario only exists when it is available")

    admin_params = initializer._postgres_connection_params()
    role = f"embed_test_installed_{uuid4().hex[:12]}"
    password = uuid4().hex
    database = f"embed_test_installed_db_{uuid4().hex[:12]}"

    admin_connection = await psycopg.AsyncConnection.connect(**{**admin_params, "dbname": "postgres"}, autocommit=True)
    try:
        async with admin_connection.cursor() as cursor:
            # NOSUPERUSER, and no CREATEROLE either -- the pipeline role's own
            # grant stays independently gated on CREATEROLE even once the
            # extension gate above it is wide open.
            await cursor.execute(
                sql.SQL("CREATE ROLE {role} LOGIN PASSWORD {password} NOSUPERUSER NOCREATEROLE").format(
                    role=sql.Identifier(role), password=sql.Literal(password)
                )
            )
            await cursor.execute(
                sql.SQL("CREATE DATABASE {database} OWNER {role}").format(database=sql.Identifier(database), role=sql.Identifier(role))
            )
            # Installed as the admin (superuser) connection, before the
            # non-superuser initializer run below -- exactly the state a
            # server the operator has already provisioned pgvector on is in.
            admin_in_database = await psycopg.AsyncConnection.connect(**{**admin_params, "dbname": database}, autocommit=True)
            async with admin_in_database, admin_in_database.cursor() as install_cursor:
                await install_cursor.execute(f"CREATE EXTENSION IF NOT EXISTS {VECTOR_EXTENSION}")

        nonsuper_params = {**admin_params, "dbname": database, "user": role, "password": password}
        succeeded = await initializer._apply_postgres_schema(nonsuper_params)
        assert succeeded is True, "an already-installed extension must not need superuser to finish the schema"

        assert await _rows_in(database, "SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')") == [(True,)]
        assert await _rows_in(
            database,
            "SELECT column_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'artist_embeddings' AND column_name = 'embedding'",
        ) == [("embedding",)]
        assert await _rows_in(
            database,
            "SELECT udt_name FROM information_schema.columns WHERE table_schema = 'public' AND table_name = 'artist_embeddings' AND column_name = 'embedding'",
        ) == [("halfvec",)]
        # CREATEROLE is independently gated: the extension being installed does
        # not also grant this role's own guard.
        assert (
            await _rows_in(
                database,
                "SELECT 1 FROM information_schema.role_table_grants WHERE grantee = %s AND table_schema = 'graph'",
                (EMBEDDING_PIPELINE_ROLE,),
            )
            == []
        )
    finally:
        await admin_connection.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database)))
        await admin_connection.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))
        await admin_connection.close()


# ── Superseded catalog items (ADR 0009, 2026-09-25 amendment) ────────────────
# catalog-api writes every supersession and ledger row; these tests play that
# writer's part by hand to prove the shape admits the amendment's merge,
# chain compression, and revert, and refuses what it must.

_SUPERSEDE = (
    "INSERT INTO catalog_item_supersessions (superseded_id, survivor_id, cause, decision_ref, via_id) VALUES (%s, %s, %s, %s, %s) RETURNING id"
)


async def _mint_items(cursor: Any, count: int) -> list[Any]:
    """Insert COUNT release-kind catalog items and return their ids."""
    ids = []
    for _ in range(count):
        await cursor.execute("INSERT INTO catalog_items (kind) VALUES ('release') RETURNING id")
        row = await cursor.fetchone()
        assert row is not None
        ids.append(row[0])
    return ids


async def _resolve(cursor: Any, native_id: Any) -> Any:
    await cursor.execute("SELECT public.resolve_catalog_item(%s)", (native_id,))
    row = await cursor.fetchone()
    assert row is not None
    return row[0]


async def _live_vertices(cursor: Any, ids: list[Any]) -> set[Any]:
    await cursor.execute("SELECT item_id FROM graph.catalog_item WHERE item_id = ANY(%s)", (ids,))
    return {row[0] for row in await cursor.fetchall()}


async def _no_current_survivor_is_itself_superseded(cursor: Any) -> bool:
    await cursor.execute(
        """
        SELECT NOT EXISTS (
            SELECT 1
            FROM catalog_item_supersessions AS outer_row
            JOIN catalog_item_supersessions AS inner_row ON inner_row.superseded_id = outer_row.survivor_id
            WHERE outer_row.valid_to IS NULL AND inner_row.valid_to IS NULL
        )
        """
    )
    row = await cursor.fetchone()
    return bool(row is not None and row[0])


@pytest.mark.asyncio
async def test_supersession_resolves_one_hop_through_merge_compression_and_revert() -> None:
    """A → B, then B → C compressed via `via_id`, then both reverted in reverse order."""
    await apply_schema()
    connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params(), autocommit=True)
    async with connection, connection.cursor() as cursor:
        item_a, item_b, item_c = await _mint_items(cursor, 3)
        promotion, reattachment = uuid4(), uuid4()

        # Not superseded, and never minted: both resolve to themselves.
        assert await _resolve(cursor, item_a) == item_a
        stranger = uuid4()
        assert await _resolve(cursor, stranger) == stranger
        assert await _live_vertices(cursor, [item_a, item_b, item_c]) == {item_a, item_b, item_c}

        # Merge A into B.
        await cursor.execute(_SUPERSEDE, (item_a, item_b, "edition_promotion", promotion, None))
        a_to_b = (await cursor.fetchone() or (None,))[0]
        assert await _resolve(cursor, item_a) == item_b
        assert await _live_vertices(cursor, [item_a, item_b, item_c]) == {item_b, item_c}

        # B is superseded into C in one transaction: close A → B, open B → C,
        # and open A → C naming B → C as its `via_id`.
        async with connection.transaction():
            await cursor.execute(_SUPERSEDE, (item_b, item_c, "catalog_reattachment", reattachment, None))
            b_to_c = (await cursor.fetchone() or (None,))[0]
            await cursor.execute("UPDATE catalog_item_supersessions SET valid_to = NOW() WHERE id = %s", (a_to_b,))
            await cursor.execute(_SUPERSEDE, (item_a, item_c, "edition_promotion", promotion, b_to_c))
        for item in (item_a, item_b, item_c):
            assert await _resolve(cursor, item) == item_c
        assert await _no_current_survivor_is_itself_superseded(cursor)
        assert await _live_vertices(cursor, [item_a, item_b, item_c]) == {item_c}

        # Revert B → C: close it and every open row compressed through it, and
        # re-open the predecessor A → B as a new row.
        async with connection.transaction():
            await cursor.execute(
                "UPDATE catalog_item_supersessions SET valid_to = NOW() WHERE (id = %s OR via_id = %s) AND valid_to IS NULL",
                (b_to_c, b_to_c),
            )
            assert cursor.rowcount == 2
            await cursor.execute(_SUPERSEDE, (item_a, item_b, "edition_promotion", promotion, None))
        assert await _resolve(cursor, item_a) == item_b
        assert await _resolve(cursor, item_b) == item_b
        assert await _no_current_survivor_is_itself_superseded(cursor)

        # Revert A → B: the former id resolves to itself and is live again.
        await cursor.execute(
            "UPDATE catalog_item_supersessions SET valid_to = NOW() WHERE superseded_id = %s AND valid_to IS NULL",
            (item_a,),
        )
        assert await _resolve(cursor, item_a) == item_a
        assert await _live_vertices(cursor, [item_a, item_b, item_c]) == {item_a, item_b, item_c}

        # History is kept by closing rows, never by rewriting them.
        await cursor.execute(
            "SELECT count(*) FILTER (WHERE valid_to IS NULL), count(*) FROM catalog_item_supersessions WHERE superseded_id = ANY(%s)",
            ([item_a, item_b],),
        )
        assert await cursor.fetchone() == (0, 4)


@pytest.mark.asyncio
async def test_supersession_refuses_a_second_survivor_an_unknown_cause_and_itself() -> None:
    await apply_schema()
    connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params(), autocommit=True)
    async with connection, connection.cursor() as cursor:
        item_a, item_b, item_c = await _mint_items(cursor, 3)
        await cursor.execute(_SUPERSEDE, (item_a, item_b, "catalog_reattachment", uuid4(), None))

        with pytest.raises(psycopg.errors.UniqueViolation):
            await cursor.execute(_SUPERSEDE, (item_a, item_c, "catalog_reattachment", uuid4(), None))
        with pytest.raises(psycopg.errors.CheckViolation):
            await cursor.execute(_SUPERSEDE, (item_c, item_b, "upstream_merge", uuid4(), None))
        with pytest.raises(psycopg.errors.CheckViolation):
            await cursor.execute(_SUPERSEDE, (item_c, item_c, "edition_promotion", uuid4(), None))
        with pytest.raises(psycopg.errors.ForeignKeyViolation):
            await cursor.execute(_SUPERSEDE, (item_c, uuid4(), "edition_promotion", uuid4(), None))

        # A closed row does not count against the one current survivor.
        await cursor.execute("UPDATE catalog_item_supersessions SET valid_to = NOW() WHERE superseded_id = %s", (item_a,))
        await cursor.execute(_SUPERSEDE, (item_a, item_c, "catalog_reattachment", uuid4(), None))
        assert await _resolve(cursor, item_a) == item_c


@pytest.mark.asyncio
async def test_move_ledger_is_keyed_per_merge_and_erased_with_its_user() -> None:
    await apply_schema()
    connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params(), autocommit=True)
    async with connection, connection.cursor() as cursor:
        item_a, item_b = await _mint_items(cursor, 2)
        await cursor.execute(
            "INSERT INTO users (email, hashed_password) VALUES (%s, 'x') RETURNING id",
            (f"{uuid4().hex}@example.invalid",),
        )
        user_id = (await cursor.fetchone() or (None,))[0]
        await cursor.execute("INSERT INTO owned_copies (user_id, item_id) VALUES (%s, %s) RETURNING id", (user_id, item_a))
        copy_id = (await cursor.fetchone() or (None,))[0]

        # The merge: supersede, re-point, ledger.
        async with connection.transaction():
            await cursor.execute(_SUPERSEDE, (item_a, item_b, "catalog_reattachment", uuid4(), None))
            supersession = (await cursor.fetchone() or (None,))[0]
            await cursor.execute("UPDATE owned_copies SET item_id = %s WHERE item_id = %s", (item_b, item_a))
            await cursor.execute(
                "INSERT INTO catalog_item_moves (supersession_id, table_name, row_id, from_item_id, to_item_id, user_id) "
                "VALUES (%s, 'owned_copies', %s, %s, %s, %s)",
                (supersession, copy_id, item_a, item_b, user_id),
            )

        with pytest.raises(psycopg.errors.UniqueViolation):
            await cursor.execute(
                "INSERT INTO catalog_item_moves (supersession_id, table_name, row_id, from_item_id, to_item_id, user_id) "
                "VALUES (%s, 'owned_copies', %s, %s, %s, %s)",
                (supersession, copy_id, item_a, item_b, user_id),
            )
        with pytest.raises(psycopg.errors.CheckViolation):
            await cursor.execute(
                "INSERT INTO catalog_item_moves (supersession_id, table_name, row_id, from_item_id, to_item_id, user_id) "
                "VALUES (%s, 'observations', %s, %s, %s, %s)",
                (supersession, uuid4(), item_a, item_b, user_id),
            )

        # The revert moves back exactly the ledgered rows still on the survivor.
        await cursor.execute("INSERT INTO owned_copies (user_id, item_id) VALUES (%s, %s) RETURNING id", (user_id, item_b))
        later_copy = (await cursor.fetchone() or (None,))[0]
        await cursor.execute(
            """
            UPDATE owned_copies AS copy SET item_id = move.from_item_id
            FROM catalog_item_moves AS move
            WHERE move.supersession_id = %s AND move.table_name = 'owned_copies'
              AND copy.id = move.row_id AND copy.item_id = move.to_item_id
            """,
            (supersession,),
        )
        await cursor.execute("SELECT id, item_id FROM owned_copies WHERE user_id = %s", (user_id,))
        assert dict(await cursor.fetchall()) == {copy_id: item_a, later_copy: item_b}

        # Erasure: the ledger goes with the user.
        await cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        await cursor.execute("SELECT count(*) FROM catalog_item_moves WHERE supersession_id = %s", (supersession,))
        assert await cursor.fetchone() == (0,)


@pytest.mark.asyncio
async def test_supersession_and_dependent_indexes_exist() -> None:
    await apply_schema()
    rows = await postgres_rows(
        "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' AND tablename IN "
        "('artifacts', 'owned_copies', 'catalog_item_supersessions', 'catalog_item_moves')"
    )
    definitions = dict(rows)
    assert definitions["idx_artifacts_item_id"].endswith("(item_id)")
    assert definitions["idx_owned_copies_item_id"].endswith("(item_id)")
    assert "UNIQUE" in definitions["idx_catalog_item_supersessions_superseded_id"]
    assert definitions["idx_catalog_item_supersessions_superseded_id"].endswith("WHERE (valid_to IS NULL)")
    assert "idx_catalog_item_moves_user_id" in definitions


# ── The MusicBrainz vertices' native id (design ADR 0012 amendment, section 3) ─

_MB_VERTEX_TABLES = (
    ("mb_artist", "artists"),
    ("mb_label", "labels"),
    ("mb_release", "releases"),
    ("mb_release_group", "release_groups"),
)


@pytest.mark.asyncio
async def test_every_musicbrainz_vertex_publishes_its_gm_item_id() -> None:
    """Each `mb_*` view reads `gm_item_id` off its table."""
    admin_params = initializer._postgres_connection_params()
    database = f"mb_gm_item_id_{uuid4().hex[:12]}"
    admin_connection = await psycopg.AsyncConnection.connect(**{**admin_params, "dbname": "postgres"}, autocommit=True)
    try:
        await admin_connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        assert await initializer._apply_postgres_schema({**admin_params, "dbname": database}) is True

        seeded: dict[str, tuple[Any, Any]] = {}
        connection = await psycopg.AsyncConnection.connect(**{**admin_params, "dbname": database}, autocommit=True)
        async with connection, connection.cursor() as cursor:
            for label, table in _MB_VERTEX_TABLES:
                kind = {"artists": "artist", "labels": "label", "releases": "release", "release_groups": "master"}[table]
                await cursor.execute("INSERT INTO catalog_items (kind) VALUES (%s) RETURNING id", (kind,))
                item_id = (await cursor.fetchone() or (None,))[0]
                mbid = uuid4()
                await cursor.execute(
                    sql.SQL("INSERT INTO musicbrainz.{} (mbid, name, gm_item_id) VALUES (%s, %s, %s)").format(sql.Identifier(table)),
                    (mbid, f"gm-item-id {label}", item_id),
                )
                seeded[label] = (mbid, item_id)

        for label, (mbid, item_id) in seeded.items():
            # `label` is one of the four view names in `_MB_VERTEX_TABLES`.
            assert await _rows_in(database, f"SELECT gm_item_id FROM graph.{label} WHERE mbid = %s", (mbid,)) == [(item_id,)]  # noqa: S608

    finally:
        await admin_connection.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(database)))
        await admin_connection.close()

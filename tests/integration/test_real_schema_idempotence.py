"""Real-engine schema initialization and idempotence proof."""

from __future__ import annotations

import asyncio
from typing import Any

import psycopg
import pytest
from common.credit_roles import categorize_role
from common.media import medium_ids, medium_label
from neo4j import AsyncGraphDatabase
from psycopg.types.json import Jsonb

from groovemap_schema import initializer
from groovemap_schema.neo4j import SCHEMA_STATEMENTS
from groovemap_schema.postgres import (
    _BOOTSTRAP_FILL_ORDER,
    _COUNTER_BEARING_VERTICES,
    _EDGE_TABLES,
    _GRAPH_STATEMENTS,
    MUSICBRAINZ_RELATIONSHIP_TYPES,
    PROPERTY_GRAPH_MINIMUM_SERVER_VERSION,
    PROPERTY_GRAPH_RELATION,
    PROPERTY_GRAPH_SCHEMA,
    PROPERTY_GRAPH_SWITCH,
    _property_graph_edges,
    _property_graph_vertices,
    _widen_to_bigint,
    graph_bootstrap_statements,
    phase0_comparison_statements,
)


pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _enable_the_property_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run both tiers with the switch on, so only the server version decides.

    The PostgreSQL 18 tier proving the graph absent is then a statement about
    the version gate rather than about which environment the suite happened to
    inherit, and the PostgreSQL 19 tier gets the graph without the integration
    script having to know the difference.
    """
    monkeypatch.setenv(PROPERTY_GRAPH_SWITCH, "enabled")


# The relkind PostgreSQL 19 beta3 gives a property graph in `pg_class`. It is its
# own kind, beside `r` for a table and `v` for a view.
PROPERTY_GRAPH_RELKIND = "g"

EXPECTED_POSTGRES_TABLES = {
    "public": {
        "admin_audit_log",
        "app_config",
        "app_tokens",
        "artifacts",
        "artists",
        "catalog_items",
        "collection_snapshots",
        "extraction_history",
        "labels",
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
}

# The endpoint-pair indexes every one of the sixteen typed MusicBrainz
# relationship views filters on, in both directions.
EXPECTED_MUSICBRAINZ_INDEXES = {
    ("musicbrainz", "relationships", "idx_mb_rels_endpoint_source"),
    ("musicbrainz", "relationships", "idx_mb_rels_endpoint_target"),
    ("musicbrainz", "relationships", "idx_mb_rels_type"),
    ("musicbrainz", "artists", "idx_mb_artists_discogs_id"),
}

# Native identity keys rely on the engine's built-in uuidv7(), which both the required
# PostgreSQL 18 tier and the advisory PostgreSQL 19 beta tier must render identically.
EXPECTED_UUIDV7_DEFAULTS = {
    ("public", "artifacts", "id"),
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

# Every element of `graph.catalog`, and the nine relations that bind none: they
# hold rows or counters for a label that binds a view joining them, plus the
# loader-written half of release degree.
DECLARED_ELEMENTS = (*_property_graph_vertices(), *_property_graph_edges())
STORAGE_ONLY_RELATIONS = EXPECTED_GRAPH_RELATIONS - {element.element for element in DECLARED_ELEMENTS}

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
    ("graph", "release_degree", "degree", "bigint"),
    ("graph", "artist_genre", "genre_name", "text"),
    ("graph", "label_genre", "label_id", "text"),
    ("graph", "mb_artist", "mbid", "uuid"),
    ("graph", "mb_release", "mbid", "uuid"),
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
    assert actual_tables == EXPECTED_POSTGRES_TABLES

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


# ── The catalog property graph ───────────────────────────────────────────────


async def server_version_num() -> int:
    """Return the connected engine's `server_version_num`."""
    rows = await postgres_rows("SELECT current_setting('server_version_num')::int")
    return int(rows[0][0])


async def property_graph_relkind() -> str | None:
    """Return the relkind of `graph.catalog`, or None when nothing carries the name."""
    rows = await postgres_rows(
        """
        SELECT relation.relkind
        FROM pg_class AS relation
        JOIN pg_namespace AS namespace ON namespace.oid = relation.relnamespace
        WHERE namespace.nspname = %s AND relation.relname = %s
        """,
        (PROPERTY_GRAPH_SCHEMA, PROPERTY_GRAPH_RELATION),
    )
    return None if not rows else str(rows[0][0])


async def property_graph_snapshot() -> tuple[tuple[Any, ...], ...]:
    """Capture every element, label, and property of `graph.catalog`.

    Empty on PostgreSQL 18, where the `pg_propgraph_*` catalogs do not exist and
    the gate has closed on the server version anyway. On 19 this is what proves a
    second apply changed nothing: the property graph has no `IF NOT EXISTS`, so
    the initializer skips it on the catalog check rather than re-running it.
    """
    if await server_version_num() < PROPERTY_GRAPH_MINIMUM_SERVER_VERSION:
        return ()
    rows = await postgres_rows(
        """
        SELECT element.pgealias,
               element.pgerelid::regclass::text,
               element.pgekind::text,
               label.pgllabel,
               property.pgpname,
               format_type(property.pgptypid, property.pgptypmod)
        FROM pg_propgraph_element AS element
        JOIN pg_propgraph_element_label AS element_label ON element_label.pgelelid = element.oid
        JOIN pg_propgraph_label AS label ON label.oid = element_label.pgellabelid
        JOIN pg_propgraph_label_property AS label_property ON label_property.plpellabelid = element_label.oid
        JOIN pg_propgraph_property AS property ON property.oid = label_property.plppropid
        WHERE element.pgepgid = (%s || '.' || %s)::regclass
        ORDER BY 1, 4, 5
        """,
        (PROPERTY_GRAPH_SCHEMA, PROPERTY_GRAPH_RELATION),
    )
    return tuple(rows)


async def assert_the_property_graph_matches_the_server() -> None:
    """Assert `graph.catalog` exists on PostgreSQL 19 and nowhere else."""
    relkind = await property_graph_relkind()
    if await server_version_num() < PROPERTY_GRAPH_MINIMUM_SERVER_VERSION:
        assert relkind is None, "PostgreSQL 18 must carry no graph.catalog relation"
        assert await property_graph_snapshot() == ()
        return
    assert relkind == PROPERTY_GRAPH_RELKIND

    # Every element table is one of the graph schema's own views, and every one
    # of them is declared: the graph covers the schema rather than a subset of it.
    elements = await postgres_rows(
        """
        SELECT element.pgealias, element.pgerelid::regclass::text, element.pgekind::text
        FROM pg_propgraph_element AS element
        WHERE element.pgepgid = (%s || '.' || %s)::regclass
        """,
        (PROPERTY_GRAPH_SCHEMA, PROPERTY_GRAPH_RELATION),
    )
    # An alias is the label; the relation underneath it differs for the four
    # labels that bind a counter projection.
    assert {alias for alias, _relation, _kind in elements} == {element.view for element in DECLARED_ELEMENTS}
    assert {relation for _alias, relation, _kind in elements} == {f"graph.{element.element}" for element in DECLARED_ELEMENTS}
    assert {f"graph.{relation}" for relation in STORAGE_ONLY_RELATIONS}.isdisjoint({relation for _alias, relation, _kind in elements})
    assert {kind for _alias, _relation, kind in elements} == {"e", "v"}

    # SQL/PGQ requires one data type per property name across the whole graph.
    # `pg_propgraph_property` is the engine's own register of that, so a single
    # row per name is the rule holding rather than a restatement of it.
    duplicates = await postgres_rows(
        """
        SELECT property.pgpname
        FROM pg_propgraph_property AS property
        WHERE property.pgppgid = (%s || '.' || %s)::regclass
        GROUP BY property.pgpname
        HAVING count(*) > 1
        """,
        (PROPERTY_GRAPH_SCHEMA, PROPERTY_GRAPH_RELATION),
    )
    assert duplicates == []

    # The four names the views spell two ways, unified on text.
    unified = await postgres_rows(
        """
        SELECT property.pgpname, format_type(property.pgptypid, property.pgptypmod)
        FROM pg_propgraph_property AS property
        WHERE property.pgppgid = (%s || '.' || %s)::regclass
          AND property.pgpname IN ('artist_id', 'label_id', 'master_id', 'discogs_label_id')
        ORDER BY 1
        """,
        (PROPERTY_GRAPH_SCHEMA, PROPERTY_GRAPH_RELATION),
    )
    assert unified == [("artist_id", "text"), ("discogs_label_id", "text"), ("label_id", "text"), ("master_id", "text")]

    # The structural key columns stay out of the published properties.
    structural = await postgres_rows(
        """
        SELECT property.pgpname
        FROM pg_propgraph_property AS property
        WHERE property.pgppgid = (%s || '.' || %s)::regclass
          AND property.pgpname LIKE '%%\\_key'
        """,
        (PROPERTY_GRAPH_SCHEMA, PROPERTY_GRAPH_RELATION),
    )
    assert structural == []


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
    await assert_the_property_graph_matches_the_server()
    first_postgres = await postgres_snapshot()
    first_neo4j = await neo4j_snapshot()
    first_property_graph = await property_graph_snapshot()
    await seed_sentinels()

    await apply_schema()
    await assert_expected_postgres_schema()
    await assert_expected_neo4j_schema()
    await assert_the_property_graph_matches_the_server()

    assert await postgres_snapshot() == first_postgres
    assert await neo4j_snapshot() == first_neo4j
    assert await property_graph_snapshot() == first_property_graph
    await assert_sentinels_survive()


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
GRAPH_FIXTURE_MALFORMED = {
    "id": 222,
    "title": "Malformed",
    "artists": "not-an-array",
    "companies": [{"name": "Raw"}],
    "genres": None,
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
            INSERT INTO musicbrainz.artists (mbid, name, discogs_artist_id) VALUES (%s, %s, %s), (%s, %s, %s)
            ON CONFLICT (mbid) DO NOTHING
            """,
            (ARTIST_MBID, "Alice", 7, OTHER_ARTIST_MBID, "The Band", 10),
        )
        await cursor.execute(
            """
            INSERT INTO musicbrainz.relationships
                (source_mbid, source_entity_type, target_mbid, target_entity_type, relationship_type, attributes, begin_date, end_date, ended)
            VALUES (%s, 'artist', %s, 'artist', 'member of band', %s, NULL, NULL, FALSE),
                   (%s, 'artist', %s, 'artist', 'collaboration', %s, NULL, NULL, FALSE)
            ON CONFLICT DO NOTHING
            """,
            (ARTIST_MBID, OTHER_ARTIST_MBID, Jsonb([]), ARTIST_MBID, DANGLING_MBID, Jsonb([])),
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

    assert await postgres_rows("SELECT name FROM graph.person ORDER BY 1") == [("Alice",), ("Bob",)]
    assert await postgres_rows("SELECT person_name, release_id, role, role_category FROM graph.credited_on ORDER BY 1") == [
        ("Alice", "111", "Producer", categorize_role("Producer")),
        ("Bob", "111", "Recorded By, Mastering Engineer", categorize_role("Recorded By, Mastering Engineer")),
    ]
    assert await postgres_rows("SELECT person_name, artist_id FROM graph.same_as ORDER BY 1") == [("Alice", "7")]

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
    ) == [(ARTIST_MBID, OTHER_ARTIST_MBID, "MEMBER_OF", "member of band")]
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


# ── GRAPH_TABLE smoke queries ────────────────────────────────────────────────
# Pattern matching over the same sentinel rows the view projections are checked
# against, so a passing query is a statement about the declared graph rather
# than about a second fixture written to suit it.

# The bead's own two-hop shape. Release 111 carries one usable artist — the
# document repeats `{"id": 7}` and the enricher's filters drop the `0` sentinel
# and the element with no id — so `a` and `b` bind to the same artist. The
# traversal is still two hops through two edge bindings, which is what is being
# proved; the query below it walks the same two hops between distinct vertices.
TWO_HOP_RELEASE_ARTIST = """
SELECT * FROM GRAPH_TABLE (graph.catalog
    MATCH (a IS artist)<-[IS by_artist]-(r IS release)-[IS by_artist]->(b IS artist)
    COLUMNS (r.release_id AS release_id, a.artist_id AS left_artist_id, b.artist_id AS right_artist_id, a.name AS artist_name)
) ORDER BY 1, 2, 3
"""

# Release to artist through the master, over three vertex labels and two of the
# restated text keys.
TWO_HOP_RELEASE_MASTER_ARTIST = """
SELECT * FROM GRAPH_TABLE (graph.catalog
    MATCH (r IS release)-[IS derived_from]->(m IS master)-[IS master_by_artist]->(a IS artist)
    COLUMNS (r.release_id AS release_id, m.master_id AS master_id, a.artist_id AS artist_id, a.name AS artist_name)
) ORDER BY 1, 2, 3
"""

MUSICBRAINZ_RELATIONSHIP = """
SELECT * FROM GRAPH_TABLE (graph.catalog
    MATCH (s IS mb_artist)-[e IS mb_rel_artist_artist]->(t IS mb_artist)
    COLUMNS (s.name AS source_name, e.relationship_type AS relationship_type, t.name AS target_name)
) ORDER BY 1, 2, 3
"""

# The same edges reached through the label all sixteen endpoint pairs share.
MUSICBRAINZ_RELATIONSHIP_SHARED_LABEL = """
SELECT * FROM GRAPH_TABLE (graph.catalog
    MATCH (s IS mb_artist)-[e IS mb_related]->(t IS mb_artist)
    COLUMNS (s.name AS source_name, e.relationship_type AS relationship_type, t.name AS target_name)
) ORDER BY 1, 2, 3
"""


# The same edges, read through the raw string the loader stored. Both properties
# are published on all sixteen pair relations, which is what lets them go on
# sharing one `mb_related` label.
MUSICBRAINZ_RELATIONSHIP_RAW_TYPE = """
SELECT * FROM GRAPH_TABLE (graph.catalog
    MATCH (s IS mb_artist)-[e IS mb_related]->(t IS mb_artist)
    COLUMNS (s.name AS source_name, e.raw_relationship_type AS raw_relationship_type, t.name AS target_name)
) ORDER BY 1, 2, 3
"""


@pytest.mark.asyncio
async def test_graph_table_queries_run_over_the_sentinel_rows() -> None:
    """Pattern matching resolves on the engine, not only in the statement text."""
    await apply_schema()
    if await server_version_num() < PROPERTY_GRAPH_MINIMUM_SERVER_VERSION:
        pytest.skip("CREATE PROPERTY GRAPH needs PostgreSQL 19")
    await seed_graph_fixtures()
    await bootstrap_the_loader_tables()

    assert await postgres_rows(TWO_HOP_RELEASE_ARTIST) == [("111", "7", "7", "Alice")]
    assert await postgres_rows(TWO_HOP_RELEASE_MASTER_ARTIST) == [("111", "55", "7", "Alice")]

    # The dangling relationship — a target mbid the loader never stored — is
    # absent, because the edge view inner-joins both endpoint tables. The type
    # reads as the Neo4j name a ported Cypher query asks for, not as the raw
    # MusicBrainz string the loader stored.
    assert await postgres_rows(MUSICBRAINZ_RELATIONSHIP) == [("Alice", "MEMBER_OF", "The Band")]
    assert await postgres_rows(MUSICBRAINZ_RELATIONSHIP_SHARED_LABEL) == [("Alice", "MEMBER_OF", "The Band")]
    assert await postgres_rows(MUSICBRAINZ_RELATIONSHIP_RAW_TYPE) == [("Alice", "member of band", "The Band")]


# ── Counters as properties of the label Neo4j carries them on ───────────────
# `graphinator` writes release_count, artist_count, label_count, style_count and
# first_year onto `:Genre`, and the equivalents onto `:Style`, `:Label` and
# `:Artist`. Eight catalog-api functions read them as node properties, so the
# parity claim is that they read as properties of the same label here. These
# queries are the claim: a bare read, a filter, and a read across an edge.

COUNTERS_ON_THE_GENRE_LABEL = """
SELECT * FROM GRAPH_TABLE (graph.catalog
    MATCH (g IS genre)
    COLUMNS (g.name AS name, g.release_count AS release_count, g.style_count AS style_count, g.first_year AS first_year)
) ORDER BY 1
"""

FILTERING_ON_A_COUNTER = """
SELECT * FROM GRAPH_TABLE (graph.catalog
    MATCH (g IS genre WHERE g.release_count > 0)
    COLUMNS (g.name AS name, g.release_count AS release_count)
) ORDER BY 1
"""

COUNTERS_READ_ACROSS_AN_EDGE = """
SELECT * FROM GRAPH_TABLE (graph.catalog
    MATCH (r IS release)-[IS in_genre]->(g IS genre)
    COLUMNS (r.release_id AS release_id, g.name AS name, g.release_count AS release_count)
) ORDER BY 1, 2
"""

DEGREE_ON_THE_ARTIST_LABEL = """
SELECT * FROM GRAPH_TABLE (graph.catalog
    MATCH (a IS artist WHERE a.artist_id = '7')
    COLUMNS (a.artist_id AS artist_id, a.name AS name, a.degree AS degree)
)
"""

# Release degree is the one counter that is NOT a property of its label, and
# this is the carry-forward spelling a rewrite uses instead.
RELEASE_DEGREE_AS_ITS_OWN_LABEL = """
SELECT * FROM GRAPH_TABLE (graph.catalog
    MATCH (r IS release_degree WHERE r.release_id = '111')
    COLUMNS (r.release_id AS release_id, r.degree AS degree)
)
"""

# The two-hop the pilot family walks, read with no counter named. The planner
# removes each LEFT JOIN to a uniquely-keyed counter relation outright, so the
# shape costs nothing on the path catalog-api actually migrates first.
PILOT_TWO_HOP_PLAN = """
EXPLAIN (COSTS OFF)
SELECT * FROM GRAPH_TABLE (graph.catalog
    MATCH (anchor IS artist WHERE anchor.artist_id = '1')
          <-[IS by_artist]-(credit IS release)-[IS by_artist]->(peer IS artist)
    COLUMNS (peer.artist_id AS collaborator_id, peer.name AS collaborator_name, credit.release_id AS release_id)
)
"""


@pytest.mark.asyncio
async def test_the_counters_read_as_properties_of_the_label_neo4j_carries_them_on() -> None:
    """`g.release_count` reads off `:Genre`, exactly as the Cypher it replaces does."""
    await apply_schema()
    if await server_version_num() < PROPERTY_GRAPH_MINIMUM_SERVER_VERSION:
        pytest.skip("GRAPH_TABLE needs PostgreSQL 19")
    await seed_graph_fixtures()
    await bootstrap_the_loader_tables()

    # A LEFT JOIN to a uniquely-keyed relation neither drops a vertex nor
    # doubles one. Proving it on the engine is what says the projection is a
    # presentation of the storage relation rather than a second population.
    for relation, storage, _counters, _carried, _counted in _COUNTER_BEARING_VERTICES:
        # Both names come from the initializer's own statement list.
        projected = await postgres_rows(f"SELECT count(*) FROM graph.{relation}")  # noqa: S608
        stored = await postgres_rows(f"SELECT count(*) FROM graph.{storage}")  # noqa: S608
        assert projected == stored, relation
        assert stored[0][0] > 0, storage

    assert await postgres_rows(COUNTERS_ON_THE_GENRE_LABEL) == [("Rock", 1, 2, 1969)]
    assert await postgres_rows(FILTERING_ON_A_COUNTER) == [("Rock", 1)]
    assert await postgres_rows(COUNTERS_READ_ACROSS_AN_EDGE) == [("111", "Rock", 1)]
    assert await postgres_rows(DEGREE_ON_THE_ARTIST_LABEL) == [("7", "Alice", 6)]

    # Release degree stays a label of its own; this is the one carry-forward
    # spelling, and the reason is the live half of the count rather than any
    # SQL/PGQ limit.
    base = await postgres_rows("SELECT degree FROM graph.release_degree_base WHERE release_id = '111'")
    assert await postgres_rows(RELEASE_DEGREE_AS_ITS_OWN_LABEL) == [("111", base[0][0] + 2)]


@pytest.mark.asyncio
async def test_the_counter_join_is_removed_when_no_counter_is_read() -> None:
    """A LEFT JOIN to a uniquely-keyed relation costs nothing when nothing reads it.

    This is what makes carrying the counters on the label free on the pilot
    path: the two-hop collaborator walk names no counter, so neither artist
    binding pays for `graph.artist_degree` at all.
    """
    await apply_schema()
    if await server_version_num() < PROPERTY_GRAPH_MINIMUM_SERVER_VERSION:
        pytest.skip("GRAPH_TABLE needs PostgreSQL 19")
    await seed_pilot_fixtures()
    await bootstrap_the_loader_tables()
    await execute_all([("analyze", "ANALYZE graph.artist, graph.artist_degree, graph.by_artist")])

    plan = "\n".join(str(row[0]) for row in await postgres_rows(PILOT_TWO_HOP_PLAN))
    assert "artist_degree" not in plan, plan
    assert "by_artist" in plan, plan

    # The join is present the moment a counter is named, which is what proves
    # the absence above is the planner removing it rather than the property
    # being unreachable.
    named = "\n".join(
        str(row[0])
        for row in await postgres_rows(
            "EXPLAIN (COSTS OFF) " + DEGREE_ON_THE_ARTIST_LABEL.replace("'7'", "'1'"),
        )
    )
    assert "artist_degree" in named, named


@pytest.mark.asyncio
async def test_the_property_graph_is_absent_below_postgresql_19() -> None:
    """The required tier must be untouched by a feature it cannot carry."""
    await apply_schema()
    if await server_version_num() >= PROPERTY_GRAPH_MINIMUM_SERVER_VERSION:
        pytest.skip("this engine is PostgreSQL 19 or later")
    assert await property_graph_relkind() is None
    assert await postgres_rows(
        "SELECT count(*) FROM pg_class WHERE relnamespace = 'graph'::regnamespace AND relkind = %s",
        (PROPERTY_GRAPH_RELKIND,),
    ) == [(0,)]


# ── The pilot family, over tables and over the retained phase 0 views ────────
# catalog-api's first GRAPH_TABLE migration is the collaborator family in
# `api/queries/network_pg_queries.py`, written against the template in its
# `docs/graph-table-migration-template.md`. It touches two vertex labels and one
# edge label — `artist`, `release`, and `by_artist` — and the whole point of
# re-declaring `graph.catalog` over tables is that those queries do not move.
#
# So they are run here verbatim, character for character as catalog-api holds
# them, against two property graphs over the same rows: `graph.catalog`, whose
# `by_artist` is the loader-written table, and a second graph whose `by_artist`
# is the retained phase 0 view. A difference between them is a difference in
# what the edge model holds, because nothing else about the two declarations
# differs.

PILOT_GRAPH = "graph_pilot_phase0.catalog"

# The second declaration. The two vertex element tables are the shipped views —
# they were never materialized — so the edge relation is the only thing that
# changes between the two graphs, which is what makes the comparison mean
# something.
PILOT_PHASE0_GRAPH = f"""
CREATE PROPERTY GRAPH {PILOT_GRAPH}
    VERTEX TABLES (
        graph.release AS release KEY (release_id)
            LABEL release PROPERTIES ALL COLUMNS,
        graph.artist AS artist KEY (artist_id)
            LABEL artist PROPERTIES ALL COLUMNS
    )
    EDGE TABLES (
        {PHASE0_SCHEMA}.by_artist AS by_artist KEY (release_id, artist_id)
            SOURCE KEY (release_id) REFERENCES release (release_id)
            DESTINATION KEY (artist_id) REFERENCES artist (artist_id)
            LABEL by_artist PROPERTIES ALL COLUMNS
    )
"""

# Verbatim from catalog-api `api/queries/network_pg_queries.py:75`.
ARTIST_IDENTITY_SQL = """
SELECT anchor_row.artist_id, anchor_row.artist_name
FROM GRAPH_TABLE (graph.catalog
    MATCH (anchor IS artist WHERE anchor.artist_id = %(artist_id)s)
    COLUMNS (anchor.artist_id AS artist_id, anchor.name AS artist_name)
) AS anchor_row
LIMIT 1
"""

# Verbatim from catalog-api `api/queries/network_pg_queries.py:146`, with the
# f-string fragments expanded exactly as the module composes them.
MULTI_HOP_COLLABORATORS_SQL = """
WITH direct AS (
    SELECT collaborator_id, collaborator_name, release_id
    FROM GRAPH_TABLE (graph.catalog
        MATCH (anchor IS artist WHERE anchor.artist_id = %(artist_id)s)
              <-[IS by_artist]-(credit IS release)-[IS by_artist]->(peer IS artist)
        WHERE peer.artist_id <> anchor.artist_id
        COLUMNS (
            peer.artist_id AS collaborator_id,
            peer.name AS collaborator_name,
            credit.release_id AS release_id
        )
    ) AS hop
),
indirect AS (
    SELECT collaborator_id, collaborator_name, bridge_id
    FROM GRAPH_TABLE (graph.catalog
        MATCH (anchor IS artist WHERE anchor.artist_id = %(artist_id)s)
              <-[IS by_artist]-(near IS release)-[IS by_artist]->(bridge IS artist)
              <-[IS by_artist]-(far IS release)-[IS by_artist]->(peer IS artist)
        WHERE bridge.artist_id <> anchor.artist_id
          AND peer.artist_id <> anchor.artist_id
          AND peer.artist_id <> bridge.artist_id
          AND far.release_id <> near.release_id
        COLUMNS (
            peer.artist_id AS collaborator_id,
            peer.name AS collaborator_name,
            bridge.artist_id AS bridge_id
        )
    ) AS hop
)
SELECT collaborator_id AS artist_id,
       collaborator_name AS artist_name,
       distance,
       collaboration_count
FROM (
    SELECT collaborator_id,
           collaborator_name,
           1 AS distance,
           count(DISTINCT release_id)::bigint AS collaboration_count
    FROM direct
    GROUP BY collaborator_id, collaborator_name
    UNION ALL
    SELECT collaborator_id,
           collaborator_name,
           2 AS distance,
           count(DISTINCT bridge_id)::bigint AS collaboration_count
    FROM indirect
    WHERE %(depth)s >= 2
      AND NOT EXISTS (SELECT 1
        FROM GRAPH_TABLE (graph.catalog
            MATCH (anchor IS artist WHERE anchor.artist_id = %(artist_id)s)
                  <-[IS by_artist]-(credit IS release)-[IS by_artist]->(peer IS artist)
            COLUMNS (peer.artist_id AS collaborator_id)
        ) AS one_hop
        WHERE one_hop.collaborator_id = indirect.collaborator_id
      )
    GROUP BY collaborator_id, collaborator_name
) AS reachable
ORDER BY distance ASC, collaboration_count DESC
LIMIT %(limit)s
"""

# Verbatim from catalog-api `api/queries/network_pg_queries.py:181`.
COUNT_MULTI_HOP_COLLABORATORS_SQL = """
WITH direct AS (
    SELECT collaborator_id, collaborator_name, release_id
    FROM GRAPH_TABLE (graph.catalog
        MATCH (anchor IS artist WHERE anchor.artist_id = %(artist_id)s)
              <-[IS by_artist]-(credit IS release)-[IS by_artist]->(peer IS artist)
        WHERE peer.artist_id <> anchor.artist_id
        COLUMNS (
            peer.artist_id AS collaborator_id,
            peer.name AS collaborator_name,
            credit.release_id AS release_id
        )
    ) AS hop
),
indirect AS (
    SELECT collaborator_id, collaborator_name, bridge_id
    FROM GRAPH_TABLE (graph.catalog
        MATCH (anchor IS artist WHERE anchor.artist_id = %(artist_id)s)
              <-[IS by_artist]-(near IS release)-[IS by_artist]->(bridge IS artist)
              <-[IS by_artist]-(far IS release)-[IS by_artist]->(peer IS artist)
        WHERE bridge.artist_id <> anchor.artist_id
          AND peer.artist_id <> anchor.artist_id
          AND peer.artist_id <> bridge.artist_id
          AND far.release_id <> near.release_id
        COLUMNS (
            peer.artist_id AS collaborator_id,
            peer.name AS collaborator_name,
            bridge.artist_id AS bridge_id
        )
    ) AS hop
)
SELECT count(*)::bigint AS total
FROM (
    SELECT collaborator_id FROM direct
    UNION
    SELECT collaborator_id
    FROM indirect
    WHERE %(depth)s >= 2
      AND NOT EXISTS (SELECT 1
        FROM GRAPH_TABLE (graph.catalog
            MATCH (anchor IS artist WHERE anchor.artist_id = %(artist_id)s)
                  <-[IS by_artist]-(credit IS release)-[IS by_artist]->(peer IS artist)
            COLUMNS (peer.artist_id AS collaborator_id)
        ) AS one_hop
        WHERE one_hop.collaborator_id = indirect.collaborator_id
      )
) AS reachable
"""

PILOT_QUERIES = (
    ("artist identity", ARTIST_IDENTITY_SQL, {"artist_id": "1"}),
    ("collaborators, depth 1", MULTI_HOP_COLLABORATORS_SQL, {"artist_id": "1", "depth": 1, "limit": 50}),
    ("collaborators, depth 2", MULTI_HOP_COLLABORATORS_SQL, {"artist_id": "1", "depth": 2, "limit": 50}),
    ("collaborators, limited", MULTI_HOP_COLLABORATORS_SQL, {"artist_id": "1", "depth": 2, "limit": 4}),
    ("collaborator count", COUNT_MULTI_HOP_COLLABORATORS_SQL, {"artist_id": "1", "depth": 2}),
    ("unknown artist", MULTI_HOP_COLLABORATORS_SQL, {"artist_id": "does-not-exist", "depth": 2, "limit": 50}),
)

# A collaboration neighbourhood two hops deep around artist 1. Artists 2, 3, and
# 4 are direct collaborators over three, two, and one release; 5, 6, and 7 are
# reachable only through them.
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
    """Write the collaboration neighbourhood the pilot family reads."""
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


@pytest.mark.asyncio
async def test_the_pilot_family_reads_the_tables_exactly_as_it_read_the_views() -> None:
    """catalog-api's first GRAPH_TABLE migration does not move when the shape does.

    Both graphs are declared over the same two vertex views and differ only in
    where `by_artist` comes from, so the comparison isolates the one thing this
    batch changed about that read path.
    """
    await apply_schema()
    if await server_version_num() < PROPERTY_GRAPH_MINIMUM_SERVER_VERSION:
        pytest.skip("GRAPH_TABLE needs PostgreSQL 19")
    await seed_pilot_fixtures()
    await bootstrap_the_loader_tables()

    await execute_all(
        [
            ("pilot comparison schema", f"CREATE SCHEMA IF NOT EXISTS {PILOT_GRAPH.split('.')[0]}"),
            ("pilot comparison graph", f"DROP PROPERTY GRAPH IF EXISTS {PILOT_GRAPH}"),
            ("pilot comparison graph", PILOT_PHASE0_GRAPH),
        ]
    )

    # The fill has to have produced the edges, or every query below returns
    # nothing from both graphs and the comparison proves nothing.
    edges = await postgres_rows("SELECT count(*) FROM graph.by_artist")
    assert edges[0][0] >= sum(len(artists) for artists in PILOT_RELEASES.values())
    # `PHASE0_SCHEMA` is a module constant, not input from a row or a request.
    assert await postgres_rows(f"SELECT count(*) FROM {PHASE0_SCHEMA}.by_artist") == edges  # noqa: S608

    matched = 0
    for label, query, parameters in PILOT_QUERIES:
        over_tables = await postgres_rows_with(query, parameters)
        over_views = await postgres_rows_with(query.replace("graph.catalog", PILOT_GRAPH), parameters)
        # Sorted rather than compared in place: both sides order by
        # `(distance, collaboration_count)` and neither adds a tiebreaker, so a
        # tie would make row order legitimately unspecified on both sides and
        # the comparison would be testing the two planners instead of the two
        # edge relations.
        assert sorted(over_tables) == sorted(over_views), label
        if parameters["artist_id"] == "1":
            assert over_tables, f"{label} returned nothing from either graph"
            matched += 1
    assert matched == len(PILOT_QUERIES) - 1

    # The depth-1 answer is the neighbourhood the fixture states, so the two
    # graphs agreeing is not two identical empty results.
    depth_one = await postgres_rows_with(MULTI_HOP_COLLABORATORS_SQL, {"artist_id": "1", "depth": 1, "limit": 50})
    assert [(row[0], row[3]) for row in depth_one] == [("2", 3), ("3", 2), ("4", 1)]


async def postgres_rows_with(query: str, parameters: dict[str, Any]) -> list[tuple[Any, ...]]:
    """Query the disposable target database with named placeholders."""
    connection = await psycopg.AsyncConnection.connect(**initializer._postgres_connection_params())
    async with connection, connection.cursor() as cursor:
        await cursor.execute(query, parameters)
        return await cursor.fetchall()


# What each counter relation's row count has to be, restated independently of
# how `graph.bootstrap_fill` computes it. The fill reaches these numbers with a
# `GROUP BY` or a correlated count; each query below is a distinct count of the
# relation's key instead, so the comparison is a second opinion rather than the
# fill's own arithmetic read back.
COUNTER_ROW_COUNTS = {
    "genre_stats": "SELECT count(*) FROM graph.genre",
    "style_stats": "SELECT count(*) FROM graph.style",
    "label_stats": "SELECT count(DISTINCT label_id) FROM graph.on_label",
    "artist_degree": """
        SELECT count(*) FROM (
            SELECT artist_id FROM graph.by_artist
            UNION SELECT artist_id FROM graph.master_by_artist
            UNION SELECT artist_id FROM graph.same_as
            UNION SELECT member_artist_id FROM graph.member_of
            UNION SELECT group_artist_id FROM graph.member_of
            UNION SELECT alias_artist_id FROM graph.alias_of
            UNION SELECT artist_id FROM graph.alias_of
        ) AS endpoint
    """,
    "release_degree_base": """
        SELECT count(*) FROM (
            SELECT release_id FROM graph.by_artist
            UNION SELECT release_id FROM graph.on_label
            UNION SELECT release_id FROM graph.in_genre
            UNION SELECT release_id FROM graph.in_style
            UNION SELECT release_id FROM graph.derived_from
            UNION SELECT release_id FROM graph.credited_on
            UNION SELECT release_id FROM graph.credited_to
            UNION SELECT release_id FROM graph.issued_on
        ) AS endpoint
    """,
    "artist_genre": """
        SELECT count(*) FROM (
            SELECT DISTINCT by_artist.artist_id, in_genre.genre_name
            FROM graph.by_artist AS by_artist
            JOIN graph.in_genre AS in_genre ON in_genre.release_id = by_artist.release_id
        ) AS pair
    """,
    "label_genre": """
        SELECT count(*) FROM (
            SELECT DISTINCT on_label.label_id, in_genre.genre_name
            FROM graph.on_label AS on_label
            JOIN graph.in_genre AS in_genre ON in_genre.release_id = on_label.release_id
        ) AS pair
    """,
}

# A row no document justifies. The fill has to remove it, which an upsert never
# would, and which is the whole reason each step empties its relation first.
STALE_GENRE = "Not In Any Document"


async def run_the_bootstrap_fill() -> dict[str, int]:
    """Run the fill and return the row count it reports for each relation, in order."""
    rows = await postgres_rows("SELECT relation, row_count FROM graph.bootstrap_fill()")
    return {str(relation).removeprefix("graph."): int(count) for relation, count in rows}


async def filled_relation_counts() -> dict[str, int]:
    """Return what each filled relation actually holds, by the engine's own count."""
    counts: dict[str, int] = {}
    for relation in _BOOTSTRAP_FILL_ORDER:
        # `_BOOTSTRAP_FILL_ORDER` is a module constant, not input from a row.
        rows = await postgres_rows(f"SELECT count(*) FROM graph.{relation}")  # noqa: S608
        counts[relation] = int(rows[0][0])
    return counts


async def filled_relation_rows() -> dict[str, list[str]]:
    """Return every row of every filled relation, ordered so two runs compare."""
    snapshot: dict[str, list[str]] = {}
    for relation in _BOOTSTRAP_FILL_ORDER:
        rows = await postgres_rows(f"SELECT * FROM graph.{relation}")  # noqa: S608
        # Sorted by their rendering rather than by value: several relations mix
        # a null `first_year` in with integers, and tuple ordering across None
        # and int raises rather than ordering.
        snapshot[relation] = sorted(repr(row) for row in rows)
    return snapshot


@pytest.mark.asyncio
async def test_the_bootstrap_fill_reproduces_the_phase_0_projection() -> None:
    """The fill is the phase 0 projection of the documents, and re-running converges.

    This is the claim the bootstrap makes and the only one it makes: every
    loader-owned relation ends up holding exactly what the view that preceded it
    published, computed once from the documents rather than on every read. The
    loaders supersede all of it on their first pass.
    """
    await apply_schema()
    await seed_graph_fixtures()
    await execute_all(phase0_comparison_statements(PHASE0_SCHEMA))

    reported = await run_the_bootstrap_fill()
    assert list(reported) == list(_BOOTSTRAP_FILL_ORDER)

    stored = await filled_relation_counts()
    assert reported == stored
    print("bootstrap_fill row counts: " + ", ".join(f"{relation}={count}" for relation, count in reported.items()))

    # Every vertex and edge relation holds exactly what its retained phase 0
    # view publishes, which is the definition the fill was rendered from.
    for relation in _BOOTSTRAP_FILL_ORDER:
        if relation in COUNTER_ROW_COUNTS:
            continue
        # `PHASE0_SCHEMA` and the relation are module constants, not input.
        expected = await postgres_rows(f"SELECT count(*) FROM {PHASE0_SCHEMA}.{relation}")  # noqa: S608
        assert stored[relation] == expected[0][0], relation

    for relation, query in COUNTER_ROW_COUNTS.items():
        expected = await postgres_rows(query)
        assert stored[relation] == expected[0][0], relation

    # None of the comparisons above is zero agreeing with zero.
    assert all(count > 0 for count in stored.values()), stored

    # Re-running changes nothing: same reported counts, same rows.
    before = await filled_relation_rows()
    assert await run_the_bootstrap_fill() == reported
    assert await filled_relation_rows() == before

    # And it converges downward, which is what an upsert would not do.
    await execute_all([("stale row", f"INSERT INTO graph.genre (name) VALUES ('{STALE_GENRE}')")])  # noqa: S608
    assert await run_the_bootstrap_fill() == reported
    assert await postgres_rows("SELECT count(*) FROM graph.genre WHERE name = %s", (STALE_GENRE,)) == [(0,)]

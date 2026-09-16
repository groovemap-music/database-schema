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
from groovemap_schema.postgres import _GRAPH_STATEMENTS


pytestmark = pytest.mark.integration

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


# The schemas whose catalogs the snapshot and the expectations cover.
SCHEMAS = ("public", "insights", "musicbrainz", "graph")

# Every graph relation the initializer declares, by view name.
EXPECTED_GRAPH_VIEWS = {name.removeprefix("graph.").removesuffix(" view") for name, _statement in _GRAPH_STATEMENTS if name.endswith(" view")}

# The functions rendered from the runtime's credit-role and media taxonomies.
EXPECTED_GRAPH_FUNCTIONS = {
    name.removeprefix("graph.").removesuffix(" function") for name, _statement in _GRAPH_STATEMENTS if name.endswith(" function")
}

# The key columns the property graph joins on, with the type each must resolve
# to on a real engine. A Discogs id read out of JSONB has to land as text so it
# joins `releases.data_id`; a MusicBrainz key has to stay a uuid; a collection
# edge has to have cast its BIGINT release id down to text.
EXPECTED_GRAPH_COLUMNS = {
    ("graph", "artist", "artist_id", "character varying"),
    ("graph", "release", "release_id", "character varying"),
    ("graph", "release", "media_families", "ARRAY"),
    ("graph", "genre", "name", "text"),
    ("graph", "style", "name", "text"),
    ("graph", "by_artist", "release_id", "character varying"),
    ("graph", "by_artist", "artist_id", "text"),
    ("graph", "on_label", "label_id", "text"),
    ("graph", "derived_from", "master_id", "text"),
    ("graph", "in_genre", "genre_name", "text"),
    ("graph", "part_of", "style_name", "text"),
    ("graph", "part_of", "genre_name", "text"),
    ("graph", "member_of", "member_artist_id", "text"),
    ("graph", "member_of", "group_artist_id", "character varying"),
    ("graph", "sublabel_of", "parent_label_id", "text"),
    ("graph", "mb_artist", "mbid", "uuid"),
    ("graph", "mb_release", "mbid", "uuid"),
    ("graph", "mb_rel_artist_artist", "source_mbid", "uuid"),
    ("graph", "mb_rel_artist_artist", "target_mbid", "uuid"),
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
        WHERE namespace.nspname IN ('public', 'insights', 'musicbrainz')
        UNION ALL
        SELECT 'index', schemaname, tablename, indexname, indexdef, '', ''
        FROM pg_indexes
        WHERE schemaname IN ('public', 'insights', 'musicbrainz')
        UNION ALL
        SELECT 'view', schemaname, viewname, definition, '', '', ''
        FROM pg_views
        WHERE schemaname = 'graph'
        ORDER BY 1, 2, 3, 4
        """
    )
    return tuple(rows)


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
    assert set(column_rows) >= EXPECTED_GRAPH_COLUMNS

    view_rows = await postgres_rows("SELECT viewname FROM pg_views WHERE schemaname = 'graph'")
    assert {row[0] for row in view_rows} == EXPECTED_GRAPH_VIEWS

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
    for view in sorted(EXPECTED_GRAPH_VIEWS):
        # `view` comes from the initializer's own statement list, not from input.
        assert await postgres_rows(f"SELECT count(*) FROM graph.{view}") == [(0,)]  # noqa: S608

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
    first_postgres = await postgres_snapshot()
    first_neo4j = await neo4j_snapshot()
    await seed_sentinels()

    await apply_schema()
    await assert_expected_postgres_schema()
    await assert_expected_neo4j_schema()

    assert await postgres_snapshot() == first_postgres
    assert await neo4j_snapshot() == first_neo4j
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


async def assert_graph_views_project_the_enricher_rules() -> None:
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
    assert await postgres_rows("SELECT source_mbid::text, target_mbid::text, relationship_type FROM graph.mb_rel_artist_artist ORDER BY 1") == [
        (ARTIST_MBID, OTHER_ARTIST_MBID, "member of band")
    ]
    assert await postgres_rows("SELECT count(*) FROM graph.mb_rel_artist_label") == [(0,)]

    # A collection row naming a release the catalog does not hold is dropped.
    assert await postgres_rows("SELECT release_id, instance_id FROM graph.collected ORDER BY 1") == [("111", 900)]
    assert await postgres_rows("SELECT release_id FROM graph.wants ORDER BY 1") == [("111",)]
    assert await postgres_rows("SELECT count(*) FROM graph.owns") == [(1,)]


@pytest.mark.asyncio
async def test_graph_views_project_the_enricher_rules() -> None:
    """The views drop exactly what graphinator drops, on a real engine.

    Runs after the idempotence proof above and leaves its fixture rows in place;
    that proof reads the catalog, never the data.
    """
    await apply_schema()
    await seed_graph_fixtures()
    await assert_graph_views_project_the_enricher_rules()

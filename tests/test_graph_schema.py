"""Tests for the `graph` schema of vertex and edge views."""

from __future__ import annotations

import re
from pathlib import Path

from common.credit_roles import ROLE_CATEGORIES, categorize_role
from common.media import medium_ids, medium_label

from groovemap_schema.postgres import (
    _COUNTER_BEARING_VERTICES,
    _COUNTER_BOOTSTRAP,
    _EDGE_TABLES,
    _GRAPH_STATEMENTS,
    _TEXT_KEY_RETYPES,
    MUSICBRAINZ_RELATIONSHIP_LABEL,
    MUSICBRAINZ_RELATIONSHIP_TYPES,
    PROPERTY_GRAPH_STATEMENT,
    _property_graph_edges,
    _property_graph_vertices,
    _role_category_branches,
    _schema_statements,
    graph_bootstrap_statements,
    phase0_comparison_statements,
)


GRAPH_STATEMENTS = dict(_GRAPH_STATEMENTS)

# The relations spike gm-database-schema-9c8.1 turned into loader-written
# tables. Everything else in the schema is still a view.
MATERIALIZED_VERTICES = ("genre", "style", "person", "media_family", "medium", "company")
MATERIALIZED_EDGES = tuple(relation for relation, _columns, _reverse, _extras in _EDGE_TABLES)

# Table 3 relations, which replace node properties rather than a phase 0 view.
COUNTER_TABLES = (
    "genre_stats",
    "style_stats",
    "label_stats",
    "artist_degree",
    "release_degree_base",
    "artist_genre",
    "label_genre",
)

MATERIALIZED = frozenset((*MATERIALIZED_VERTICES, *MATERIALIZED_EDGES, *COUNTER_TABLES))

# The phase 0 view bodies, retained so the projection rules stay under test
# after the relation that published them became a table.
PHASE0_STATEMENTS = dict(phase0_comparison_statements("graph_phase0"))

# Every vertex view, with the column that keys it. This is the contract the
# CREATE PROPERTY GRAPH bead and catalog-api read against, so it is spelled out
# here rather than derived from the statements it is meant to police.
VERTEX_KEYS = {
    "artist": ("artist_id",),
    "label": ("label_id",),
    "master": ("master_id",),
    "release": ("release_id",),
    "genre": ("name",),
    "style": ("name",),
    "mb_artist": ("mbid",),
    "mb_label": ("mbid",),
    "mb_release": ("mbid",),
    "mb_release_group": ("mbid",),
    "app_user": ("user_id",),
    "catalog_item": ("item_id",),
    "person": ("name",),
    "company": ("company_id",),
    "medium": ("medium_id",),
    "media_family": ("name",),
    # Neo4j carries release degree on `:Release`, but its live half is two
    # lateral counts no unique key makes removable, so it is the one counter
    # that stays a label of its own rather than a property of the label it
    # describes. The other four are properties of `genre`, `style`, `label`,
    # and `artist`; see `COUNTER_PROPERTIES`.
    "release_degree": ("release_id",),
}

# The relation each counter-bearing label binds, and the counter properties it
# has to publish. This is the parity claim: `graphinator` writes these onto the
# Neo4j node of the same name, so a ported Cypher query reads them off the same
# label rather than off a second one it would have to learn.
COUNTER_PROPERTIES = {
    "genre": ("genre_vertex", ("release_count", "artist_count", "label_count", "style_count", "first_year")),
    "style": ("style_vertex", ("release_count", "artist_count", "label_count", "genre_count", "first_year")),
    "label": ("label_vertex", ("release_count", "artist_count", "genre_count")),
    "artist": ("artist_vertex", ("degree",)),
}

# The relations that hold rows or counters but bind no label of their own,
# because the label that describes them binds a projection joining the two.
STORAGE_ONLY = frozenset(
    {
        "genre",
        "style",
        "artist",
        "label",
        "genre_stats",
        "style_stats",
        "label_stats",
        "artist_degree",
        "release_degree_base",
    }
)

# Every edge view, with its key column set and the (source, target) columns that
# join to the vertex views above.
EDGE_KEYS = {
    "by_artist": (("release_id", "artist_id"), "release_id", "artist_id"),
    "on_label": (("release_id", "label_id"), "release_id", "label_id"),
    "derived_from": (("release_id", "master_id"), "release_id", "master_id"),
    "in_genre": (("release_id", "genre_name"), "release_id", "genre_name"),
    "in_style": (("release_id", "style_name"), "release_id", "style_name"),
    "master_by_artist": (("master_id", "artist_id"), "master_id", "artist_id"),
    "master_in_genre": (("master_id", "genre_name"), "master_id", "genre_name"),
    "master_in_style": (("master_id", "style_name"), "master_id", "style_name"),
    "part_of": (("style_name", "genre_name"), "style_name", "genre_name"),
    "member_of": (("member_artist_id", "group_artist_id"), "member_artist_id", "group_artist_id"),
    "alias_of": (("alias_artist_id", "artist_id"), "alias_artist_id", "artist_id"),
    "sublabel_of": (("sublabel_id", "parent_label_id"), "sublabel_id", "parent_label_id"),
    "collected": (("collection_id",), "user_id", "release_id"),
    "wants": (("wantlist_id",), "user_id", "release_id"),
    "owns": (("owned_copy_id",), "user_id", "item_id"),
    "credited_on": (("person_name", "release_id", "role"), "person_name", "release_id"),
    "same_as": (("person_name", "artist_id"), "person_name", "artist_id"),
    "credited_to": (("release_id", "company_id", "role", "source"), "release_id", "company_id"),
    "issued_on": (("release_id", "medium_id", "source"), "release_id", "medium_id"),
    "in_family": (("medium_id", "family_name"), "medium_id", "family_name"),
    "artist_genre": (("artist_id", "genre_name"), "artist_id", "genre_name"),
    "label_genre": (("label_id", "genre_name"), "label_id", "genre_name"),
}

# The functions rendered from the runtime's shared vocabularies. Every one is
# called from a relation body, so all three have to precede the relations.
VOCABULARY_FUNCTIONS = {"credit_role_category", "medium_label", "mb_relationship_type"}

# `graph.bootstrap_fill` is the fourth and is not one of them: nothing calls it,
# it reads and writes the relations rather than being read by one, and it is the
# only thing the schema declares that writes a graph row.
EXPECTED_FUNCTIONS = VOCABULARY_FUNCTIONS | {"bootstrap_fill"}

MUSICBRAINZ_PAIRS = [
    (source, target) for source in ("artist", "label", "release", "release_group") for target in ("artist", "label", "release", "release_group")
]

# Reserved in SQL:2016 or in PostgreSQL, and therefore unusable as a bare
# relation name or as a future property-graph label.
RESERVED_WORDS = frozenset(
    {
        "all",
        "and",
        "any",
        "as",
        "between",
        "both",
        "case",
        "cast",
        "check",
        "column",
        "constraint",
        "create",
        "current_user",
        "default",
        "desc",
        "distinct",
        "do",
        "else",
        "end",
        "except",
        "false",
        "for",
        "from",
        "grant",
        "group",
        "having",
        "in",
        "initially",
        "intersect",
        "into",
        "is",
        "like",
        "limit",
        "not",
        "null",
        "offset",
        "on",
        "only",
        "or",
        "order",
        "primary",
        "references",
        "returning",
        "select",
        "session_user",
        "some",
        "symmetric",
        "table",
        "then",
        "to",
        "trailing",
        "true",
        "union",
        "unique",
        "user",
        "using",
        "when",
        "where",
        "window",
        "with",
    }
)


def view_names() -> set[str]:
    """Return every relation the graph schema declares, whatever its shape."""
    return {name.removeprefix("graph.").rsplit(" ", 1)[0] for name in GRAPH_STATEMENTS if name.endswith((" view", " table"))}


def table_names() -> set[str]:
    """Return every relation the graph schema declares as a table."""
    return {name.removeprefix("graph.").removesuffix(" table") for name in GRAPH_STATEMENTS if name.endswith(" table")}


def function_names() -> set[str]:
    """Return every function name the graph schema declares."""
    return {name.removeprefix("graph.").removesuffix(" function") for name in GRAPH_STATEMENTS if name.endswith(" function")}


def statement_for(relation: str) -> str:
    """Return the SQL that defines one relation's projection.

    For a relation that is still a view this is its shipped body. For one that
    is now a loader-written table it is the retained phase 0 body, which is the
    definition the bootstrap fills the table from and therefore still the
    statement of what a row in it means. Keeping one accessor is what lets the
    projection-rule tests below go on testing the rule rather than the shape.
    """
    if relation in MATERIALIZED:
        return PHASE0_STATEMENTS[f"graph_phase0.{relation} view"]
    return GRAPH_STATEMENTS[f"graph.{relation} view"]


def ddl_for(relation: str) -> str:
    """Return the CREATE TABLE statement for one loader-written relation."""
    return GRAPH_STATEMENTS[f"graph.{relation} table"]


def index_statements_for(relation: str) -> list[str]:
    """Return every index statement declared against one graph relation."""
    return [statement for name, statement in _GRAPH_STATEMENTS if name.endswith(" index") and f"ON graph.{relation} " in statement]


def projection_statements() -> list[tuple[str, str]]:
    """Return every statement whose body projects a catalog table."""
    return [*_GRAPH_STATEMENTS, *phase0_comparison_statements("graph_phase0")]


class TestGraphSchemaStatement:
    """The schema itself is created once, first, and is never dropped."""

    def test_schema_statement_is_idempotent(self) -> None:
        assert GRAPH_STATEMENTS["graph schema"] == "CREATE SCHEMA IF NOT EXISTS graph"

    def test_schema_is_the_first_graph_statement(self) -> None:
        assert _GRAPH_STATEMENTS[0][0] == "graph schema"

    def test_graph_statements_run_after_every_table(self) -> None:
        """A view can only be created once the table it reads exists."""
        names = [name for name, _statement in _schema_statements()]
        graph_names = [name for name, _statement in _GRAPH_STATEMENTS]
        assert names[-len(graph_names) :] == graph_names

    def test_only_a_guarded_migration_drops_anything(self) -> None:
        """A relation is dropped only by the statement replacing it, and only if it is still there."""
        for name, statement in _GRAPH_STATEMENTS:
            if "DROP" not in statement.upper():
                continue
            assert name.endswith(" migration"), f"{name} contains a DROP"
            relation = name.removeprefix("graph.").rsplit(" ", 2)[0]
            assert f"DROP VIEW graph.{relation} CASCADE;" in statement, name
            assert ("IF EXISTS (" in statement and "relkind = 'v'" in statement) or "data_type <> 'text'" in statement, name

    def test_every_migration_is_guarded_and_names_one_relation(self) -> None:
        migrations = [name for name, _statement in _GRAPH_STATEMENTS if name.endswith(" migration")]
        expected = {f"graph.{relation} view-to-table migration" for relation in MATERIALIZED - set(COUNTER_TABLES)}
        expected |= {f"graph.{relation} key-type migration" for relation, _column in _TEXT_KEY_RETYPES}
        assert set(migrations) == expected
        assert len(migrations) == len(expected)

    def test_a_counter_relation_never_replaces_a_view(self) -> None:
        """Table 3 relations are new; nothing of theirs was ever published as a view."""
        names = {name for name, _statement in _GRAPH_STATEMENTS}
        for relation in COUNTER_TABLES:
            assert f"graph.{relation} view-to-table migration" not in names

    def test_every_migration_precedes_the_relation_it_frees(self) -> None:
        names = [name for name, _statement in _GRAPH_STATEMENTS]
        last_migration = max(index for index, name in enumerate(names) if name.endswith(" migration"))
        for relation in MATERIALIZED_VERTICES:
            assert names.index(f"graph.{relation} table") > last_migration

    def test_every_table_uses_if_not_exists(self) -> None:
        for relation in table_names():
            assert ddl_for(relation).startswith(f"CREATE TABLE IF NOT EXISTS graph.{relation} (")

    def test_every_index_uses_if_not_exists(self) -> None:
        for name, statement in _GRAPH_STATEMENTS:
            if name.endswith(" index"):
                assert "CREATE INDEX IF NOT EXISTS" in statement, name

    def test_the_tables_precede_the_views_that_read_them(self) -> None:
        names = [name for name, _statement in _GRAPH_STATEMENTS]
        last_table = max(index for index, name in enumerate(names) if name.endswith(" table"))
        for view in ("part_of", "in_family", "release_degree"):
            assert names.index(f"graph.{view} view") > last_table

    def test_every_view_uses_create_or_replace(self) -> None:
        for name, statement in _GRAPH_STATEMENTS:
            if not name.endswith(" view"):
                continue
            view = name.removeprefix("graph.").removesuffix(" view")
            assert statement.startswith(f"CREATE OR REPLACE VIEW graph.{view} AS\n")

    def test_every_function_uses_create_or_replace(self) -> None:
        for name, statement in _GRAPH_STATEMENTS:
            if not name.endswith(" function"):
                continue
            function = name.removeprefix("graph.").removesuffix(" function")
            assert f"CREATE OR REPLACE FUNCTION graph.{function}(" in statement

    def test_functions_precede_the_relations_that_call_them(self) -> None:
        """`graph.credited_on` generates a column with one of them, so it is not only views."""
        names = [name for name, _statement in _GRAPH_STATEMENTS]
        last_vocabulary = max(names.index(f"graph.{function} function") for function in VOCABULARY_FUNCTIONS)
        for relation in ("credited_on", "medium"):
            assert names.index(f"graph.{relation} table") > last_vocabulary
        assert names.index("graph.mb_rel_artist_artist view") > last_vocabulary

    def test_the_bootstrap_fill_is_declared_after_everything_it_touches(self) -> None:
        """It writes every table and its counter bodies read `graph.part_of`."""
        names = [name for name, _statement in _GRAPH_STATEMENTS]
        assert names[-1] == "graph.bootstrap_fill function"

    def test_statement_names_are_unique(self) -> None:
        names = [name for name, _statement in _GRAPH_STATEMENTS]
        assert len(names) == len(set(names))


class TestRelationNames:
    """Names are the contract: they become property-graph labels verbatim."""

    def test_declared_relations_are_exactly_the_documented_set(self) -> None:
        expected = set(VERTEX_KEYS) | set(EDGE_KEYS) | {f"mb_rel_{source}_{target}" for source, target in MUSICBRAINZ_PAIRS}
        assert view_names() == expected | STORAGE_ONLY | {relation for relation, _columns in COUNTER_PROPERTIES.values()}

    def test_every_materialized_relation_is_a_table_and_nothing_else_is(self) -> None:
        assert table_names() == MATERIALIZED

    def test_the_relations_table_2_keeps_as_views_are_still_views(self) -> None:
        """Table 2 materializes an edge only where a caller pays for it."""
        for relation in ("part_of", "in_family", "sublabel_of", "collected", "wants", "owns"):
            assert f"graph.{relation} view" in GRAPH_STATEMENTS
            assert relation not in table_names()

    def test_no_relation_name_is_reserved(self) -> None:
        assert not (view_names() & RESERVED_WORDS)

    def test_no_relation_name_exceeds_the_identifier_limit(self) -> None:
        for name in view_names():
            assert len(name.encode()) <= 63, name

    def test_the_account_vertex_avoids_the_reserved_user_spelling(self) -> None:
        assert "user" not in view_names()
        assert "app_user" in view_names()


class TestVertexViews:
    """Every vertex view exposes the key column its edges join to."""

    def test_each_vertex_exposes_its_key_column(self) -> None:
        for view, keys in VERTEX_KEYS.items():
            if view in COUNTER_TABLES or view == "release_degree":
                continue
            statement = statement_for(view)
            for key in keys:
                assert f"AS {key}\n" in statement or f"AS {key}," in statement, f"{view} is missing {key}"

    def test_each_vertex_table_keys_on_its_documented_column(self) -> None:
        for relation in table_names():
            if relation not in VERTEX_KEYS:
                continue
            (key,) = VERTEX_KEYS[relation]
            assert re.search(rf"^\s+{key}\s+text PRIMARY KEY", ddl_for(relation), re.MULTILINE), relation

    def test_every_counter_relation_carries_the_columns_table_3_lists(self) -> None:
        columns = {
            "genre_stats": ("name", "release_count", "artist_count", "label_count", "style_count", "first_year"),
            "style_stats": ("name", "release_count", "artist_count", "label_count", "genre_count", "first_year"),
            "label_stats": ("label_id", "release_count", "artist_count", "genre_count"),
            "artist_degree": ("artist_id", "degree"),
            "release_degree_base": ("release_id", "degree"),
            "artist_genre": ("artist_id", "genre_name", "release_count"),
            "label_genre": ("label_id", "genre_name", "release_count"),
        }
        for relation, expected in columns.items():
            statement = ddl_for(relation)
            for column in expected:
                assert re.search(rf"^\s+{column}\s", statement, re.MULTILINE), f"{relation} is missing {column}"

    def test_the_counter_indexes_table_3_lists_exist(self) -> None:
        expected = {
            "genre_stats": "(first_year)",
            "style_stats": "(first_year)",
            "label_stats": "(release_count)",
            "artist_degree": "(degree DESC)",
        }
        for relation, columns in expected.items():
            assert any(statement.endswith(columns) for statement in index_statements_for(relation)), relation

    def test_release_degree_sums_a_loader_base_against_live_personal_counts(self) -> None:
        """The one relation split across two owners."""
        statement = statement_for("release_degree")
        assert "graph.release_degree_base" in statement
        assert "public.user_collections" in statement
        assert "public.user_wantlists" in statement
        assert "::bigint" in statement

    def test_release_degree_never_casts_a_non_numeric_catalog_id(self) -> None:
        """A cast that can raise would take the whole relation down on one bad row."""
        statement = statement_for("release_degree")
        assert "CASE WHEN base.release_id ~ '^[0-9]+$' THEN base.release_id::bigint END" in statement

    def test_the_genre_aggregates_are_keyed_on_the_pair_and_indexed_in_reverse(self) -> None:
        for relation, reverse in (("artist_genre", "(genre_name, artist_id)"), ("label_genre", "(genre_name, label_id)")):
            assert "PRIMARY KEY (" in ddl_for(relation), relation
            assert any(statement.endswith(reverse) for statement in index_statements_for(relation)), relation

    def test_autocomplete_relations_carry_a_guarded_trigram_index(self) -> None:
        """A view cannot carry an index at all, which is half of why these are tables."""
        for relation in ("genre", "style", "person"):
            trigram = [statement for statement in index_statements_for(relation) if "gin_trgm_ops" in statement]
            assert len(trigram) == 1, relation
            assert "WHERE extname = 'pg_trgm'" in trigram[0], relation

    def test_the_trigram_extension_is_created_before_the_indexes_that_need_it(self) -> None:
        names = [name for name, _statement in _GRAPH_STATEMENTS]
        assert names.index("pg_trgm extension") < names.index("graph.genre_name_trgm index")

    def test_the_company_table_does_not_recompute_the_producers_identity_rule(self) -> None:
        """SQL `lower` only approximates Python `casefold`; the loader owns the rule."""
        statement = ddl_for("company")
        assert "lower(" not in statement
        assert "regexp_replace" not in statement
        assert "company_id       text PRIMARY KEY" in statement

    def test_discogs_vertices_publish_their_key_as_text(self) -> None:
        """A `character varying` vertex key has no equality operator in PostgreSQL 19 beta 3."""
        for view, column in (("artist", "artist_id"), ("label", "label_id"), ("master", "master_id"), ("release", "release_id")):
            statement = statement_for(view)
            assert re.search(rf"data_id::text\s+AS {column}\b", statement), view
            assert f"{view}_key" not in statement

    def test_no_relation_carries_an_appended_key_restatement(self) -> None:
        for name, statement in _GRAPH_STATEMENTS:
            for retired in ("artist_key", "label_key", "master_key", "release_key"):
                assert retired not in statement, f"{name} still carries {retired}"

    def test_the_discogs_vertices_expose_the_native_identity(self) -> None:
        """`gm_id` comes from the one table catalog-api's projection job reads."""
        for view, table in (("artist", "artists"), ("label", "labels"), ("master", "masters"), ("release", "releases")):
            statement = statement_for(view)
            assert "alias.native_id" in statement, view
            assert "AS gm_id" in statement, view
            assert "LEFT JOIN public.provider_aliases AS alias" in statement, view
            assert f"alias.external_id = {table}.data_id" in statement, view
            assert "alias.valid_to IS NULL" in statement, view

    def test_release_exposes_the_two_columns_the_graph_wrote_twice(self) -> None:
        statement = statement_for("release")
        assert "AS formats" in statement
        assert "data -> 'formats'" in statement
        assert "AS catalog_number" in statement
        assert "data -> 'labels' -> 0 ->> 'catno'" in statement

    def test_release_exposes_the_indexed_scalar_fields(self) -> None:
        statement = statement_for("release")
        for fragment in ("data ->> 'title'", "data ->> 'year'", "data ->> 'country'"):
            assert fragment in statement

    def test_release_and_master_expose_their_tag_arrays(self) -> None:
        for view in ("release", "master"):
            statement = statement_for(view)
            assert "AS genres" in statement
            assert "AS styles" in statement

    def test_release_exposes_the_canonical_media_families(self) -> None:
        assert "releases.media -> 'families'" in statement_for("release")

    def test_genre_and_style_span_releases_and_masters(self) -> None:
        for view in ("genre", "style"):
            statement = statement_for(view)
            assert "public.releases" in statement
            assert "public.masters" in statement
            assert statement.count("SELECT DISTINCT") == 1

    def test_account_view_exposes_no_personal_or_credential_column(self) -> None:
        statement = statement_for("app_user").lower()
        for column in ("email", "hashed_password", "totp_secret", "totp_recovery_codes"):
            assert column not in statement


class TestEdgeViews:
    """Every edge view exposes a stable key set and resolvable endpoints."""

    def test_each_edge_exposes_its_key_and_endpoint_columns(self) -> None:
        for view, (keys, source, target) in EDGE_KEYS.items():
            if view in COUNTER_TABLES:
                continue
            statement = statement_for(view)
            for column in {*keys, source, target}:
                assert f"AS {column}\n" in statement or f"AS {column}," in statement, f"{view} is missing {column}"

    def test_every_edge_table_keys_on_the_forward_pair(self) -> None:
        for relation, (keys, _source, _target) in EDGE_KEYS.items():
            if relation not in table_names():
                continue
            assert f"PRIMARY KEY ({', '.join(keys)})" in ddl_for(relation), relation

    def test_every_edge_table_indexes_the_reverse_pair(self) -> None:
        """A table indexed one way only is a sequential scan in the other."""
        for relation, _columns, reverse, _extras in _EDGE_TABLES:
            assert f"CREATE INDEX IF NOT EXISTS {relation}_reverse ON graph.{relation} ({reverse})" in index_statements_for(relation), relation

    def test_every_edge_key_column_is_text(self) -> None:
        for relation, columns, _reverse, _extras in _EDGE_TABLES:
            for line in columns.strip().splitlines():
                stripped = line.strip()
                if stripped.startswith("PRIMARY KEY") or not stripped:
                    continue
                assert " text " in stripped or " bigint " in stripped, f"{relation}: {stripped}"
                assert "varchar" not in stripped and "character varying" not in stripped, f"{relation}: {stripped}"

    def test_no_edge_table_declares_a_foreign_key(self) -> None:
        """A loader writes an edge before it has necessarily ingested the endpoint."""
        for relation in table_names():
            assert "REFERENCES" not in ddl_for(relation), relation

    def test_credited_on_generates_its_category_rather_than_trusting_the_writer(self) -> None:
        statement = ddl_for("credited_on")
        assert "role_category text GENERATED ALWAYS AS (graph.credit_role_category(role)) STORED" in statement
        assert any(statement.endswith("(role_category, person_name)") for statement in index_statements_for("credited_on"))

    def test_source_stays_in_the_key_of_both_provider_written_edges(self) -> None:
        """Two providers write the same edge and must not collide, as ADR 0011 has it."""
        for relation in ("credited_to", "issued_on"):
            statement = ddl_for(relation)
            assert "source        text NOT NULL" in statement or "source     text NOT NULL" in statement, relation
            assert ", source)" in statement, relation
            assert any(candidate.endswith("(source)") for candidate in index_statements_for(relation)), relation

    def test_reference_edges_skip_elements_without_a_usable_id(self) -> None:
        """A falsy id and the Discogs `0` sentinel both drop the element."""
        for view in ("by_artist", "on_label", "master_by_artist", "member_of", "alias_of", "sublabel_of", "derived_from"):
            statement = statement_for(view)
            assert "NULLIF(btrim(" in statement
            assert "<> '0'" in statement

    def test_repeatable_arrays_are_deduplicated(self) -> None:
        """A release lists a label once per catalogue number; the key holds it once."""
        for view in ("by_artist", "on_label", "in_genre", "in_style", "master_by_artist", "master_in_genre", "master_in_style", "alias_of"):
            assert "SELECT DISTINCT" in statement_for(view)

    def test_two_sided_relations_are_unioned_rather_than_appended(self) -> None:
        """UNION, not UNION ALL: a reciprocal pair states the same edge twice."""
        for view in ("member_of", "sublabel_of"):
            statement = statement_for(view)
            assert "\nUNION\n" in statement
            assert "UNION ALL" not in statement

    def test_derived_from_needs_no_deduplication(self) -> None:
        """`master_id` is a scalar, so the unnest that would repeat it is absent."""
        assert "DISTINCT" not in statement_for("derived_from")

    def test_part_of_only_fires_on_a_single_genre_record(self) -> None:
        statement = statement_for("part_of")
        assert "jsonb_array_length" in statement
        assert ") = 1" in statement

    def test_part_of_reads_releases_and_masters(self) -> None:
        statement = statement_for("part_of")
        assert "public.releases" in statement
        assert "public.masters" in statement


class TestJsonbGuards:
    """An unnest of an unchecked document never raises on a malformed record."""

    def test_every_unnest_is_guarded_by_a_type_check(self) -> None:
        """Either a CASE wraps the argument, or the subquery's own WHERE gates it."""
        for name, statement in _GRAPH_STATEMENTS:
            for match in re.finditer(r"jsonb_array_elements(?:_text)?\(", statement):
                head, tail = statement[: match.start()], statement[match.end() :]
                wrapped_in_case = tail.startswith("CASE WHEN jsonb_typeof(")
                gated_by_where = head.endswith("ARRAY(SELECT ") and ") WHERE jsonb_typeof(" in tail.split("\n", 1)[0]
                assert wrapped_in_case or gated_by_where, f"{name} unnests without a guard: {tail[:60]}"

    def test_array_projections_guard_inside_the_subquery(self) -> None:
        for view in ("release", "master", "mb_release"):
            statement = statement_for(view)
            assert "WHERE jsonb_typeof(" in statement


class TestMusicBrainzEdgeViews:
    """The polymorphic relationship table is split into typed, resolvable edges."""

    def test_one_view_exists_per_ordered_endpoint_pair(self) -> None:
        for source, target in MUSICBRAINZ_PAIRS:
            assert f"graph.mb_rel_{source}_{target} view" in GRAPH_STATEMENTS

    def test_each_view_filters_on_its_own_pair(self) -> None:
        spellings = {"artist": "artist", "label": "label", "release": "release", "release_group": "release-group"}
        for source, target in MUSICBRAINZ_PAIRS:
            statement = statement_for(f"mb_rel_{source}_{target}")
            assert f"relationship.source_entity_type = '{spellings[source]}'" in statement
            assert f"relationship.target_entity_type = '{spellings[target]}'" in statement

    def test_each_view_inner_joins_both_endpoint_tables(self) -> None:
        tables = {
            "artist": "musicbrainz.artists",
            "label": "musicbrainz.labels",
            "release": "musicbrainz.releases",
            "release_group": "musicbrainz.release_groups",
        }
        for source, target in MUSICBRAINZ_PAIRS:
            statement = statement_for(f"mb_rel_{source}_{target}")
            assert f"JOIN {tables[source]} AS source_entity ON source_entity.mbid = relationship.source_mbid" in statement
            assert f"JOIN {tables[target]} AS target_entity ON target_entity.mbid = relationship.target_mbid" in statement

    def test_each_view_exposes_the_relationship_properties(self) -> None:
        for source, target in MUSICBRAINZ_PAIRS:
            statement = statement_for(f"mb_rel_{source}_{target}")
            for column in ("relationship_id", "source_mbid", "target_mbid", "relationship_type", "begin_date", "end_date", "ended", "attributes"):
                assert f"AS {column}" in statement


# Copied from musicbrainz-graph-enricher's brainzgraphinator/_projections.py.
# Pinned entry by entry rather than compared against an import, because the
# enricher is a separate service this package does not depend on: a silent
# divergence has to fail here rather than be invisible until a ported query
# returns nothing.
ENRICHER_MAP = {
    "member of band": "MEMBER_OF",
    "collaboration": "COLLABORATED_WITH",
    "teacher": "TAUGHT",
    "tribute": "TRIBUTE_TO",
    "founder": "FOUNDED",
    "supporting musician": "SUPPORTED",
    "subgroup": "SUBGROUP_OF",
    "artist rename": "RENAMED_TO",
}


class TestMusicBrainzRelationshipMap:
    """The two stores disagree on the relationship vocabulary; the map closes it."""

    def test_the_map_is_the_enrichers_map_entry_for_entry(self) -> None:
        assert MUSICBRAINZ_RELATIONSHIP_TYPES == ENRICHER_MAP
        assert len(MUSICBRAINZ_RELATIONSHIP_TYPES) == 8

    def test_every_entry_is_rendered_into_the_function(self) -> None:
        statement = statement_for_function("mb_relationship_type")
        for raw, mapped in ENRICHER_MAP.items():
            assert f"WHEN '{raw}' THEN '{mapped}'" in statement, raw

    def test_the_rendered_case_has_no_fallback(self) -> None:
        """The enricher writes no edge for an unmapped string, so the answer is NULL."""
        statement = statement_for_function("mb_relationship_type")
        assert "ELSE" not in statement
        assert statement.count("WHEN ") == len(ENRICHER_MAP)

    def test_the_function_is_immutable_and_strict(self) -> None:
        statement = statement_for_function("mb_relationship_type")
        assert "IMMUTABLE" in statement
        assert "RETURNS NULL ON NULL INPUT" in statement

    def test_every_pair_view_publishes_the_mapped_name_and_the_raw_string(self) -> None:
        """One shared label needs one property set across all sixteen relations."""
        for source, target in MUSICBRAINZ_PAIRS:
            statement = statement_for(f"mb_rel_{source}_{target}")
            assert "graph.mb_relationship_type(relationship.relationship_type) AS relationship_type" in statement
            assert "relationship.relationship_type AS raw_relationship_type" in statement


class TestPhase0Comparison:
    """The retained definitions are a test fixture, never a shipped relation."""

    def test_the_initializer_never_creates_a_phase_0_relation(self) -> None:
        shipped = {name for name, _statement in _schema_statements()}
        for name, _statement in phase0_comparison_statements("graph_phase0"):
            assert name not in shipped, name
        for _name, statement in _schema_statements():
            assert "graph_phase0" not in str(statement)

    def test_one_retained_definition_per_materialized_relation(self) -> None:
        retained = {
            name.removeprefix("graph_phase0.").removesuffix(" view")
            for name, _statement in phase0_comparison_statements("graph_phase0")
            if name.endswith(" view")
        }
        assert retained == MATERIALIZED - set(COUNTER_TABLES)

    def test_the_bootstrap_fills_every_loader_written_table(self) -> None:
        filled = {name.removeprefix("graph.").removesuffix(" bootstrap") for name, _statement in graph_bootstrap_statements("graph_phase0")}
        assert filled == MATERIALIZED

    def test_the_bootstrap_never_overwrites_a_loader_written_row(self) -> None:
        for name, statement in graph_bootstrap_statements("graph_phase0"):
            assert statement.endswith("ON CONFLICT DO NOTHING"), name
            assert statement.startswith("INSERT INTO graph."), name

    def test_the_bootstrap_does_not_write_the_generated_category(self) -> None:
        statement = dict(graph_bootstrap_statements("graph_phase0"))["graph.credited_on bootstrap"]
        assert "role_category" not in statement

    def test_the_degree_relations_sum_edge_tables_rather_than_documents(self) -> None:
        bootstrap = dict(graph_bootstrap_statements("graph_phase0"))
        for relation in ("artist_degree", "release_degree_base"):
            statement = bootstrap[f"graph.{relation} bootstrap"]
            assert "jsonb" not in statement, relation
            assert "public.releases" not in statement, relation
            assert "graph.by_artist" in statement, relation

    def test_label_stats_release_count_does_not_fan_out_over_the_left_joins(self) -> None:
        """A label's release_count must be over its own distinct releases.

        `label_stats.release_count` is `on_label` LEFT JOINed to both
        `by_artist` and `in_genre`, which multiplies one release's row into
        one per (artist, genre) pair. `artist_count` and `genre_count` already
        deduplicate with DISTINCT on the column they name; release_count has
        to as well, or a release with several artists and genres is counted
        several times over instead of once. The real-engine proof is
        `test_label_stats_release_count_does_not_fan_out_over_artists_and_genres`
        in `tests/integration/test_real_schema_idempotence.py`.
        """
        statement = _COUNTER_BOOTSTRAP["label_stats"]
        assert "count(DISTINCT on_label.release_id) AS release_count" in statement
        assert "count(DISTINCT by_artist.artist_id) AS artist_count" in statement
        assert "count(DISTINCT in_genre.genre_name) AS genre_count" in statement


class TestBootstrapFill:
    """The one shipped thing that writes a graph row, and what keeps it honest."""

    def test_it_is_declared_as_a_reporting_function(self) -> None:
        statement = statement_for_function("bootstrap_fill")
        assert statement.startswith("CREATE OR REPLACE FUNCTION graph.bootstrap_fill()\n")
        assert "RETURNS TABLE (relation text, row_count bigint)" in statement
        assert "LANGUAGE plpgsql" in statement

    def test_it_fills_every_loader_written_table_and_nothing_else(self) -> None:
        assert truncated_relations() == MATERIALIZED

    def test_every_relation_is_emptied_before_it_is_refilled(self) -> None:
        """An upsert converges upward only; a row the documents dropped has to go."""
        statement = statement_for_function("bootstrap_fill")
        for relation in MATERIALIZED:
            truncate = statement.index(f"TRUNCATE graph.{relation};")
            insert = statement.index(f"INSERT INTO graph.{relation} (")
            assert truncate < insert, relation
        assert "ON CONFLICT" not in statement

    def test_it_reports_one_row_per_relation(self) -> None:
        statement = statement_for_function("bootstrap_fill")
        assert statement.count("RETURN NEXT;") == len(MATERIALIZED)
        assert statement.count("RAISE NOTICE") == len(MATERIALIZED)
        for relation in MATERIALIZED:
            assert f"relation := 'graph.{relation}';" in statement, relation

    def test_the_vertex_tables_are_filled_before_the_edge_tables(self) -> None:
        """`part_of` and `in_family` inner-join them, so an early edge is silently absent."""
        statement = statement_for_function("bootstrap_fill")
        last_vertex = max(statement.index(f"TRUNCATE graph.{relation};") for relation in MATERIALIZED_VERTICES)
        first_edge = min(statement.index(f"TRUNCATE graph.{relation};") for relation in MATERIALIZED_EDGES)
        assert last_vertex < first_edge

    def test_the_counters_are_filled_after_every_edge_table(self) -> None:
        """Each one sums the edge tables, so an early counter converges on zero."""
        statement = statement_for_function("bootstrap_fill")
        last_edge = max(statement.index(f"TRUNCATE graph.{relation};") for relation in MATERIALIZED_EDGES)
        first_counter = min(statement.index(f"TRUNCATE graph.{relation};") for relation in COUNTER_TABLES)
        assert last_edge < first_counter

    def test_it_never_writes_the_generated_category(self) -> None:
        """Naming a generated column in an INSERT is an error, not an overwrite."""
        statement = statement_for_function("bootstrap_fill")
        assert "INSERT INTO graph.credited_on (person_name, release_id, role)\n" in statement

    def test_every_body_is_the_retained_phase_0_definition(self) -> None:
        """The fill and the parity comparison read one text, so they cannot drift."""
        rendered = " ".join(statement_for_function("bootstrap_fill").split())
        for relation in MATERIALIZED - set(COUNTER_TABLES):
            body = " ".join(statement_for(relation).split())
            body = body.removeprefix(f"CREATE OR REPLACE VIEW graph_phase0.{relation} AS ")
            assert body in rendered, relation

    def test_the_counter_bodies_read_the_edge_tables_and_no_document(self) -> None:
        statement = statement_for_function("bootstrap_fill")
        counters = statement[min(statement.index(f"TRUNCATE graph.{relation};") for relation in COUNTER_TABLES) :]
        assert "jsonb" not in counters
        assert "public.releases" not in counters
        assert "graph.by_artist" in counters


class TestCollectionEdgeViews:
    """Personal collections join the catalog on the stringified Discogs id."""

    def test_collected_and_wants_cast_the_release_key_to_text(self) -> None:
        for view, table in (("collected", "collection"), ("wants", "wantlist")):
            statement = statement_for(view)
            assert f"JOIN public.releases AS releases ON releases.data_id = {table}.release_id::text" in statement

    def test_collected_exposes_the_instance_key(self) -> None:
        assert "AS instance_id" in statement_for("collected")

    def test_owns_joins_the_native_catalog_item(self) -> None:
        statement = statement_for("owns")
        assert "AS item_id" in statement
        assert "public.owned_copies" in statement


class TestSchemaQualification:
    """A view's meaning must not depend on the search_path that created it."""

    def test_every_base_relation_is_schema_qualified(self) -> None:
        for name, statement in _GRAPH_STATEMENTS:
            if not name.endswith(" view"):
                continue
            for match in re.finditer(r"\b(?:FROM|JOIN)\s+([a-z_][a-z_0-9.]*)(\()?", statement):
                if match.group(2) is not None:
                    continue  # a set-returning function, not a stored relation
                relation = match.group(1)
                assert "." in relation, f"{name} reads unqualified relation {relation}"


def truncated_relations() -> set[str]:
    """Return every relation `graph.bootstrap_fill` empties before refilling it."""
    statement = statement_for_function("bootstrap_fill")
    return set(re.findall(r"TRUNCATE graph\.([a-z_]+);", statement))


def statement_for_function(function: str) -> str:
    """Return the CREATE OR REPLACE FUNCTION statement for one rendered vocabulary."""
    return GRAPH_STATEMENTS[f"graph.{function} function"]


def rendered_role_branches() -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Return the (exact, contained) branch lists the rendered CASE actually carries."""
    statement = statement_for_function("credit_role_category")
    exact = re.findall(r"WHEN normalized\.role = '(.*?)' THEN '(.*?)'", statement)
    contained = re.findall(r"WHEN strpos\(normalized\.role, '(.*?)'\) > 0 THEN '(.*?)'", statement)
    return exact, contained


def resolve_like_the_rendered_case(raw_role: str) -> str:
    """Resolve a role the way the rendered CASE does, branch order included."""
    normalized = raw_role.lower().strip()
    exact, contained = rendered_role_branches()
    for fragment, category in exact:
        if normalized == fragment:
            return category
    for fragment, category in contained:
        if fragment in normalized:
            return category
    return "other"


class TestRenderedVocabularies:
    """The SQL functions are rendered from the runtime's taxonomies, not restated."""

    def test_both_functions_are_declared(self) -> None:
        assert function_names() == EXPECTED_FUNCTIONS

    def test_functions_are_immutable_and_strict(self) -> None:
        for function in VOCABULARY_FUNCTIONS:
            statement = statement_for_function(function)
            assert "IMMUTABLE" in statement
            assert "PARALLEL SAFE" in statement
            assert "RETURNS NULL ON NULL INPUT" in statement

    def test_every_role_fragment_is_rendered_with_its_category(self) -> None:
        expected = {role: category for category, roles in ROLE_CATEGORIES.items() for role in roles}
        exact, contained = rendered_role_branches()
        assert dict(exact) == expected
        assert dict(contained) == expected

    def test_substring_branches_are_globally_longest_first(self) -> None:
        """A generic fragment must never pre-empt a longer one from another category."""
        _exact, contained = rendered_role_branches()
        lengths = [len(fragment) for fragment, _category in contained]
        assert lengths == sorted(lengths, reverse=True)

    def test_rendered_order_matches_the_declared_scan_order(self) -> None:
        exact, contained = rendered_role_branches()
        declared = _role_category_branches()
        assert exact == declared
        assert contained == declared

    def test_the_rendered_case_agrees_with_categorize_role(self) -> None:
        """The drift guard: every fragment, plus compounds that cross categories."""
        samples = [role for _category, roles in ROLE_CATEGORIES.items() for role in roles]
        samples += [
            "Producer",
            "  RECORDED BY  ",
            "Recorded By, Mixed By",
            "Recorded By, Mastering Engineer",
            "Executive-Producer",
            "Lacquer Cut By",
            "Backing Vocals",
            "A&R",
            "Interpretive Dance",
            "",
        ]
        for sample in samples:
            assert resolve_like_the_rendered_case(sample) == categorize_role(sample), sample

    def test_every_medium_id_is_rendered_with_its_label(self) -> None:
        statement = statement_for_function("medium_label")
        rendered = dict(re.findall(r"WHEN '(.*?)' THEN '(.*?)'", statement))
        assert rendered == {medium: medium_label(medium).replace("'", "''") for medium in medium_ids()}

    def test_an_unknown_medium_falls_back_to_its_own_id(self) -> None:
        assert "ELSE medium_id" in statement_for_function("medium_label")

    def test_single_quotes_in_a_label_are_escaped(self) -> None:
        statement = statement_for_function("medium_label")
        for medium in medium_ids():
            assert f"WHEN '{medium}' THEN '{medium_label(medium).replace(chr(39), chr(39) * 2)}'" in statement


class TestCreditAndCompanyViews:
    """Person and company relations follow the projections, not a second reading."""

    def test_person_is_keyed_on_the_credit_name(self) -> None:
        statement = statement_for("person")
        assert "credit.person_name AS name" in statement
        assert "SELECT DISTINCT" in statement

    def test_a_credit_needs_both_a_name_and_a_role(self) -> None:
        for view in ("person", "credited_on", "same_as"):
            statement = statement_for(view)
            assert "credit.value ->> 'name'" in statement
            assert "credit.value ->> 'role'" in statement

    def test_credited_on_derives_its_category_from_the_rendered_taxonomy(self) -> None:
        assert "graph.credit_role_category(credit.role)" in statement_for("credited_on")

    def test_same_as_only_fires_when_the_credit_carries_an_artist_id(self) -> None:
        statement = statement_for("same_as")
        assert "NULLIF(btrim(credit.artist_id)" in statement
        assert "<> '0'" in statement

    def test_company_identity_prefers_a_positive_discogs_id(self) -> None:
        statement = statement_for("company")
        assert "'^0*[1-9][0-9]*$'" in statement
        assert "'name:' || lower(regexp_replace(" in statement

    def test_company_picks_one_representative_name_deterministically(self) -> None:
        statement = statement_for("company")
        assert "SELECT DISTINCT ON (credit.company_id)" in statement
        assert "ORDER BY credit.company_id" in statement

    def test_credited_to_keeps_the_first_entry_in_source_order(self) -> None:
        statement = statement_for("credited_to")
        assert "DISTINCT ON (credit.release_id, credit.company_id, credit.role)" in statement
        assert "credit.entry_position" in statement

    def test_credited_to_defaults_an_absent_category_rather_than_nulling_it(self) -> None:
        assert "COALESCE(NULLIF(btrim(item.value ->> 'role_category'), ''), 'other')" in statement_for("credited_to")

    def test_company_rows_need_both_an_identity_and_a_role(self) -> None:
        for view in ("company", "credited_to"):
            statement = statement_for(view)
            assert "credit.company_id IS NOT NULL" in statement
            assert "credit.role IS NOT NULL" in statement


class TestMediaViews:
    """Media relations span both providers over one shared medium vocabulary."""

    def test_media_relations_read_both_providers(self) -> None:
        for view in ("medium", "media_family", "issued_on"):
            statement = statement_for(view)
            assert "public.releases" in statement
            assert "musicbrainz.releases" in statement

    def test_in_family_projects_the_medium_table_rather_than_re_unnesting(self) -> None:
        """The media taxonomy bounds it at a few dozen rows, so Table 2 keeps it a view."""
        statement = GRAPH_STATEMENTS["graph.in_family view"]
        assert "FROM graph.medium AS medium" in statement
        assert "JOIN graph.media_family AS family" in statement
        assert "jsonb_array_elements" not in statement

    def test_the_musicbrainz_side_joins_down_to_the_discogs_release_key(self) -> None:
        assert "JOIN public.releases AS releases ON releases.data_id = mb_release.discogs_release_id::text" in statement_for("issued_on")

    def test_medium_labels_come_from_the_rendered_taxonomy(self) -> None:
        assert "graph.medium_label(media.medium_id)" in statement_for("medium")

    def test_issued_on_sums_the_quantity_per_medium_and_source(self) -> None:
        statement = statement_for("issued_on")
        assert "SUM(media.qty)::bigint AS qty" in statement
        assert "GROUP BY media.release_id, media.medium_id, media.provider" in statement

    def test_source_is_part_of_the_issued_on_key(self) -> None:
        """Each provider writes its own edge to a Medium node they share."""
        statement = statement_for("issued_on")
        assert "'discogs'::text" in statement
        assert "'musicbrainz'::text" in statement
        assert "AS source" in statement

    def test_an_unusable_quantity_defaults_to_one(self) -> None:
        statement = statement_for("issued_on")
        assert "jsonb_typeof(item.value -> 'qty') = 'number'" in statement
        assert "ELSE 1 END" in statement

    def test_a_media_item_needs_both_a_medium_and_a_family(self) -> None:
        """`in_family` is absent: it reads the medium table, which already applied this."""
        for view in ("medium", "issued_on"):
            statement = statement_for(view)
            assert "item.value ->> 'medium'" in statement
            assert "item.value ->> 'family'" in statement


class TestPropertyGraph:
    """`CREATE PROPERTY GRAPH graph.catalog` over the views above (PostgreSQL 19)."""

    def test_the_statement_names_the_catalog_graph(self) -> None:
        name, statement = PROPERTY_GRAPH_STATEMENT
        assert name == "graph.catalog property graph"
        assert statement.startswith("CREATE PROPERTY GRAPH graph.catalog\n")

    def test_the_statement_drops_nothing(self) -> None:
        """There is no IF NOT EXISTS for a property graph, and no DROP either."""
        assert "DROP" not in PROPERTY_GRAPH_STATEMENT[1].upper()

    def test_the_statement_is_not_part_of_the_unconditional_schema(self) -> None:
        """It is gated on the server version and on an operator switch."""
        assert PROPERTY_GRAPH_STATEMENT[0] not in {name for name, _statement in _schema_statements()}
        assert PROPERTY_GRAPH_STATEMENT[0] not in {name for name, _statement in _GRAPH_STATEMENTS}

    def test_every_element_table_is_a_declared_graph_relation(self) -> None:
        elements = {element.element for element in (*_property_graph_vertices(), *_property_graph_edges())}
        assert elements == view_names() - STORAGE_ONLY

    def test_only_the_counter_bearing_labels_bind_a_relation_of_another_name(self) -> None:
        renamed = {vertex.view: vertex.element for vertex in _property_graph_vertices() if vertex.element != vertex.view}
        assert renamed == {label: relation for label, (relation, _columns) in COUNTER_PROPERTIES.items()}
        for edge in _property_graph_edges():
            assert edge.element == edge.view, edge.view

    def test_each_counter_bearing_label_publishes_its_counters_as_properties(self) -> None:
        """The parity claim: `g.release_count` reads off `:Genre`, as it does in Neo4j."""
        for label, (relation, columns) in COUNTER_PROPERTIES.items():
            vertex = next(candidate for candidate in _property_graph_vertices() if candidate.view == label)
            assert vertex.element == relation, label
            # PROPERTIES ALL COLUMNS, so every column of the projection is a
            # property and the projection's column list is the property list.
            assert vertex.properties is None, label
            statement = GRAPH_STATEMENTS[f"graph.{relation} view"]
            for column in columns:
                assert f"AS {column}\n" in statement or f"AS {column}," in statement, f"{relation} is missing {column}"

    def test_each_counter_projection_left_joins_its_uniquely_keyed_counter_relation(self) -> None:
        """A LEFT JOIN to a uniquely-keyed relation is removed when nothing reads it."""
        for relation, storage, counters, _carried, _counted in _COUNTER_BEARING_VERTICES:
            statement = GRAPH_STATEMENTS[f"graph.{relation} view"]
            assert f"FROM graph.{storage} AS {storage}" in statement, relation
            assert f"LEFT JOIN graph.{counters} AS {counters} ON" in statement, relation
            assert "PRIMARY KEY" in ddl_for(counters), counters

    def test_a_count_reads_zero_and_a_first_year_reads_null(self) -> None:
        """Every caller does arithmetic on a count; none may divide by a null."""
        for relation, _storage, counters, _carried, counted in _COUNTER_BEARING_VERTICES:
            statement = GRAPH_STATEMENTS[f"graph.{relation} view"]
            for column in counted:
                assert f"COALESCE({counters}.{column}, 0)::bigint AS {column}" in statement, f"{relation}.{column}"
            if "first_year" in statement:
                assert f"{counters}.first_year AS first_year" in statement, relation
                assert "COALESCE(" + counters + ".first_year" not in statement, relation

    def test_release_degree_is_the_one_counter_that_stays_its_own_label(self) -> None:
        """Its live half is two lateral counts the planner cannot remove."""
        labels = {vertex.view for vertex in _property_graph_vertices()}
        assert "release_degree" in labels
        assert not labels & {"genre_stats", "style_stats", "label_stats", "artist_degree"}
        statement = statement_for("release_degree")
        assert "CROSS JOIN LATERAL" in statement
        assert "release_degree" not in GRAPH_STATEMENTS["graph.release view"]

    def test_every_vertex_and_edge_alias_is_unique(self) -> None:
        aliases = [vertex.view for vertex in _property_graph_vertices()] + [edge.view for edge in _property_graph_edges()]
        assert len(aliases) == len(set(aliases))

    def test_every_documented_vertex_is_declared(self) -> None:
        declared = {vertex.view for vertex in _property_graph_vertices()}
        assert declared == set(VERTEX_KEYS)

    def test_every_documented_edge_is_declared(self) -> None:
        declared = {edge.view for edge in _property_graph_edges()}
        expected = set(EDGE_KEYS) | {f"mb_rel_{source}_{target}" for source, target in MUSICBRAINZ_PAIRS}
        assert declared == expected

    def test_edge_keys_are_the_documented_key_columns(self) -> None:
        for edge in _property_graph_edges():
            if edge.view not in EDGE_KEYS:
                continue
            key, _source, _target = EDGE_KEYS[edge.view]
            assert edge.key == key, edge.view

    def test_musicbrainz_edges_are_keyed_on_the_surrogate_id(self) -> None:
        for source, target in MUSICBRAINZ_PAIRS:
            edge = next(candidate for candidate in _property_graph_edges() if candidate.view == f"mb_rel_{source}_{target}")
            assert edge.key == ("relationship_id",)
            assert (edge.source, edge.destination) == (f"mb_{source}", f"mb_{target}")

    def test_edge_endpoints_are_the_documented_source_and_target_columns(self) -> None:
        for edge in _property_graph_edges():
            if edge.view not in EDGE_KEYS:
                continue
            _key, source_column, target_column = EDGE_KEYS[edge.view]
            assert edge.source_key == (source_column,), edge.view
            assert edge.destination_key == (target_column,), edge.view

    def test_every_endpoint_resolves_to_a_declared_vertex_alias(self) -> None:
        vertices = {vertex.view: vertex for vertex in _property_graph_vertices()}
        for edge in _property_graph_edges():
            for alias, columns in ((edge.source, edge.source_columns), (edge.destination, edge.destination_columns)):
                assert alias in vertices, f"{edge.view} references unknown vertex {alias}"
                assert columns == vertices[alias].key, f"{edge.view} does not reference {alias}'s key"

    def test_every_vertex_is_keyed_on_its_documented_key_column(self) -> None:
        """Every key is published; none is an appended restatement any more."""
        for vertex in _property_graph_vertices():
            assert vertex.key == VERTEX_KEYS[vertex.view], vertex.view

    def test_no_endpoint_resolves_to_a_retired_key_restatement(self) -> None:
        for edge in _property_graph_edges():
            for columns in (edge.source_columns, edge.destination_columns):
                for column in columns:
                    assert not column.endswith("_key"), edge.view

    def test_the_colliding_property_names_are_cast_to_one_type(self) -> None:
        """SQL/PGQ requires one data type per property name across the graph."""
        casts = {
            ("mb_label", "discogs_label_id::text AS discogs_label_id"),
            ("collected", "release_id::text AS release_id"),
            ("wants", "release_id::text AS release_id"),
        }
        elements = {element.view: element.properties for element in (*_property_graph_vertices(), *_property_graph_edges())}
        for view, cast in casts:
            properties = elements[view]
            assert properties is not None, view
            assert cast in properties, view

    def test_only_the_two_personal_edges_still_cast_release_id(self) -> None:
        """Every other relation publishes it as `text` already."""
        casting = {
            element.view
            for element in (*_property_graph_vertices(), *_property_graph_edges())
            if any(item.startswith("release_id::") for item in element.properties or ())
        }
        assert casting == {"collected", "wants"}

    def test_every_other_element_publishes_all_of_its_columns(self) -> None:
        forced = {"mb_label", "collected", "wants"}
        for element in (*_property_graph_vertices(), *_property_graph_edges()):
            if element.view in forced:
                assert element.properties is not None, element.view
            else:
                assert element.properties is None, element.view
        elements = len(_property_graph_vertices()) + len(_property_graph_edges())
        assert PROPERTY_GRAPH_STATEMENT[1].count("PROPERTIES ALL COLUMNS") == elements - len(forced) + len(MUSICBRAINZ_PAIRS)

    def test_the_shared_musicbrainz_label_is_on_exactly_the_sixteen_pair_views(self) -> None:
        shared = {edge.view for edge in _property_graph_edges() if MUSICBRAINZ_RELATIONSHIP_LABEL in edge.extra_labels}
        assert shared == {f"mb_rel_{source}_{target}" for source, target in MUSICBRAINZ_PAIRS}

    def test_no_label_is_a_reserved_word(self) -> None:
        for label in self.labels():
            assert label not in RESERVED_WORDS, f"label {label} is reserved"

    def test_every_element_carries_its_own_view_name_as_a_label(self) -> None:
        statement = PROPERTY_GRAPH_STATEMENT[1]
        for element in (*_property_graph_vertices(), *_property_graph_edges()):
            assert f"LABEL {element.view} " in statement, element.view

    def test_the_declared_labels_are_the_relations_plus_the_shared_one(self) -> None:
        """A label is a relation name verbatim, except the four that bind a projection."""
        projections = {relation for relation, _columns in COUNTER_PROPERTIES.values()}
        expected = (view_names() - STORAGE_ONLY - projections) | set(COUNTER_PROPERTIES) | {MUSICBRAINZ_RELATIONSHIP_LABEL}
        assert self.labels() == expected

    def test_every_edge_declares_explicit_keys_and_references(self) -> None:
        """No endpoint is inferred from a foreign key: views carry none."""
        statement = PROPERTY_GRAPH_STATEMENT[1]
        assert statement.count("SOURCE KEY (") == len(_property_graph_edges())
        assert statement.count("DESTINATION KEY (") == len(_property_graph_edges())
        assert statement.count(") REFERENCES ") == 2 * len(_property_graph_edges())

    @staticmethod
    def labels() -> set[str]:
        """Return every label the statement declares."""
        return set(re.findall(r"LABEL (\w+) ", PROPERTY_GRAPH_STATEMENT[1]))


# docs/architecture.md carries the rendered statement in full so a catalog-api
# rewrite can cite an exact label, key, or property without running a
# PostgreSQL 19 server. That only holds while the two agree.
ARCHITECTURE_DOC = Path(__file__).resolve().parents[1] / "docs" / "architecture.md"


def documented_property_graph_ddl() -> str:
    """Return the fenced SQL block in the architecture doc holding the statement."""
    blocks = re.findall(r"```sql\n(.*?)```", ARCHITECTURE_DOC.read_text(), re.DOTALL)
    declarations = [block for block in blocks if block.startswith("CREATE PROPERTY GRAPH")]
    assert len(declarations) == 1, f"expected one CREATE PROPERTY GRAPH block, found {len(declarations)}"
    return declarations[0]


class TestPropertyGraphDocumentation:
    """The documented DDL is the generated DDL, not a copy that drifted from it."""

    def test_the_documented_ddl_is_byte_identical_to_the_generator_output(self) -> None:
        assert documented_property_graph_ddl() == PROPERTY_GRAPH_STATEMENT[1] + ";\n"

    def test_the_documented_ddl_is_the_whole_statement(self) -> None:
        """A truncated paste would still start with CREATE PROPERTY GRAPH."""
        documented = documented_property_graph_ddl()
        assert documented.rstrip().endswith(");")
        for element in (*_property_graph_vertices(), *_property_graph_edges()):
            assert f"graph.{element.element} AS {element.view} " in documented, element.view

"""Tests for the `graph` schema of vertex and edge views."""

from __future__ import annotations

import re

from groovemap_schema.postgres import _GRAPH_STATEMENTS, _schema_statements


GRAPH_STATEMENTS = dict(_GRAPH_STATEMENTS)

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
    "user_account": ("user_id",),
    "catalog_item": ("item_id",),
}

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
}

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
    """Return every relation name the graph schema declares."""
    return {name.removeprefix("graph.").removesuffix(" view") for name in GRAPH_STATEMENTS if name != "graph schema"}


def statement_for(view: str) -> str:
    """Return the CREATE OR REPLACE VIEW statement for one graph relation."""
    return GRAPH_STATEMENTS[f"graph.{view} view"]


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

    def test_no_statement_drops_anything(self) -> None:
        for name, statement in _GRAPH_STATEMENTS:
            assert "DROP" not in statement.upper(), f"{name} contains a DROP"

    def test_every_view_uses_create_or_replace(self) -> None:
        for name, statement in _GRAPH_STATEMENTS:
            if name == "graph schema":
                continue
            view = name.removeprefix("graph.").removesuffix(" view")
            assert statement.startswith(f"CREATE OR REPLACE VIEW graph.{view} AS\n")

    def test_statement_names_are_unique(self) -> None:
        names = [name for name, _statement in _GRAPH_STATEMENTS]
        assert len(names) == len(set(names))


class TestRelationNames:
    """Names are the contract: they become property-graph labels verbatim."""

    def test_declared_relations_are_exactly_the_documented_set(self) -> None:
        expected = set(VERTEX_KEYS) | set(EDGE_KEYS) | {f"mb_rel_{source}_{target}" for source, target in MUSICBRAINZ_PAIRS}
        assert view_names() == expected

    def test_no_relation_name_is_reserved(self) -> None:
        assert not (view_names() & RESERVED_WORDS)

    def test_no_relation_name_exceeds_the_identifier_limit(self) -> None:
        for name in view_names():
            assert len(name.encode()) <= 63, name

    def test_the_account_vertex_avoids_the_reserved_user_spelling(self) -> None:
        assert "user" not in view_names()
        assert "user_account" in view_names()


class TestVertexViews:
    """Every vertex view exposes the key column its edges join to."""

    def test_each_vertex_exposes_its_key_column(self) -> None:
        for view, keys in VERTEX_KEYS.items():
            statement = statement_for(view)
            for key in keys:
                assert f"AS {key}\n" in statement or f"AS {key}," in statement, f"{view} is missing {key}"

    def test_discogs_vertices_read_the_catalog_key_as_text(self) -> None:
        for view, column in (("artist", "artist_id"), ("label", "label_id"), ("master", "master_id"), ("release", "release_id")):
            assert f"data_id          AS {column}" in statement_for(view) or f"data_id         AS {column}" in statement_for(view)

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
        statement = statement_for("user_account").lower()
        for column in ("email", "hashed_password", "totp_secret", "totp_recovery_codes"):
            assert column not in statement


class TestEdgeViews:
    """Every edge view exposes a stable key set and resolvable endpoints."""

    def test_each_edge_exposes_its_key_and_endpoint_columns(self) -> None:
        for view, (keys, source, target) in EDGE_KEYS.items():
            statement = statement_for(view)
            for column in {*keys, source, target}:
                assert f"AS {column}\n" in statement or f"AS {column}," in statement, f"{view} is missing {column}"

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
            if name == "graph schema":
                continue
            for match in re.finditer(r"\b(?:FROM|JOIN)\s+([a-z_][a-z_0-9.]*)(\()?", statement):
                if match.group(2) is not None:
                    continue  # a set-returning function, not a stored relation
                relation = match.group(1)
                assert "." in relation, f"{name} reads unqualified relation {relation}"

"""Tests for the `graph` schema of vertex and edge views."""

from __future__ import annotations

import re

from common.credit_roles import ROLE_CATEGORIES, categorize_role
from common.media import medium_ids, medium_label

from groovemap_schema.postgres import _GRAPH_STATEMENTS, _role_category_branches, _schema_statements


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
    "app_user": ("user_id",),
    "catalog_item": ("item_id",),
    "person": ("name",),
    "company": ("company_id",),
    "medium": ("medium_id",),
    "media_family": ("name",),
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
    "credited_on": (("person_name", "release_id", "role"), "person_name", "release_id"),
    "same_as": (("person_name", "artist_id"), "person_name", "artist_id"),
    "credited_to": (("release_id", "company_id", "role", "source"), "release_id", "company_id"),
    "issued_on": (("release_id", "medium_id", "source"), "release_id", "medium_id"),
    "in_family": (("medium_id", "family_name"), "medium_id", "family_name"),
}

# The functions rendered from the runtime's shared vocabularies.
EXPECTED_FUNCTIONS = {"credit_role_category", "medium_label"}

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
    return {name.removeprefix("graph.").removesuffix(" view") for name in GRAPH_STATEMENTS if name.endswith(" view")}


def function_names() -> set[str]:
    """Return every function name the graph schema declares."""
    return {name.removeprefix("graph.").removesuffix(" function") for name in GRAPH_STATEMENTS if name.endswith(" function")}


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

    def test_functions_precede_the_views_that_call_them(self) -> None:
        names = [name for name, _statement in _GRAPH_STATEMENTS]
        last_function = max(index for index, name in enumerate(names) if name.endswith(" function"))
        for view in ("credited_on", "medium"):
            assert names.index(f"graph.{view} view") > last_function

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
        assert "app_user" in view_names()


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
        statement = statement_for("app_user").lower()
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
            if not name.endswith(" view"):
                continue
            for match in re.finditer(r"\b(?:FROM|JOIN)\s+([a-z_][a-z_0-9.]*)(\()?", statement):
                if match.group(2) is not None:
                    continue  # a set-returning function, not a stored relation
                relation = match.group(1)
                assert "." in relation, f"{name} reads unqualified relation {relation}"


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
        for function in EXPECTED_FUNCTIONS:
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

    def test_media_views_read_both_providers(self) -> None:
        for view in ("medium", "media_family", "issued_on", "in_family"):
            statement = statement_for(view)
            assert "public.releases" in statement
            assert "musicbrainz.releases" in statement

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
        for view in ("medium", "issued_on", "in_family"):
            statement = statement_for(view)
            assert "item.value ->> 'medium'" in statement
            assert "item.value ->> 'family'" in statement

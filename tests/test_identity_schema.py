"""Tests for the native identity tables, provider aliases, and native-id columns.

Covers ADR 0009 (native identity and provider aliases): the five natively keyed
entities (`catalog_items`, `artifacts`, `owned_copies`, `collection_snapshots`,
`observations`), the single `provider_aliases` table with its currently-valid
uniqueness rule, and the additive nullable `gm_item_id` / `owned_copy_id`
columns on every provider-keyed table.

Every assertion is made against the statement lists, never against a database.
"""

from groovemap_schema.postgres import (
    _ENTITY_TABLES,
    _GRAPH_STATEMENTS,
    _MUSICBRAINZ_INDEXES,
    _MUSICBRAINZ_TABLES,
    _USER_TABLES,
    _entity_schema_statements,
    _schema_statements,
)


# The five native entity tables from ADR 0009, plus the alias table, in the
# order they must appear in _USER_TABLES.
_IDENTITY_TABLES = [
    "catalog_items table",
    "artifacts table",
    "owned_copies table",
    "collection_snapshots table",
    "observations table",
    "provider_aliases table",
]

# The closed entity-kind set from taxonomy/identity/v1 that catalog_items.kind
# is checked against.
_CATALOG_KINDS = ["release", "master", "artist", "label"]

# The four MusicBrainz entity tables that carry a native catalog item.
_MUSICBRAINZ_ENTITIES = ["artists", "labels", "releases", "release_groups"]

# Every statement name that existed before ADR 0009 and ADR 0010, snapshotted
# from the commit those records landed on.  Both programs are expand-only, so
# this list must survive verbatim and in order.
_LEGACY_STATEMENT_NAMES = [
    "artists table",
    "idx_artists_hash",
    "idx_artists_updated_at",
    "labels table",
    "idx_labels_hash",
    "idx_labels_updated_at",
    "masters table",
    "idx_masters_hash",
    "idx_masters_updated_at",
    "releases table",
    "idx_releases_hash",
    "idx_releases_updated_at",
    "idx_artists_name",
    "idx_labels_name",
    "idx_masters_title",
    "idx_masters_year",
    "idx_releases_title",
    "idx_releases_year",
    "idx_releases_country",
    "idx_releases_genres",
    "idx_releases_labels",
    "releases add media column",
    "idx_releases_media_families",
    "idx_artists_fts",
    "idx_labels_fts",
    "idx_masters_fts",
    "idx_releases_fts",
    "users table",
    "users.password_changed_at column",
    "users.totp_secret column",
    "users.totp_enabled column",
    "users.totp_recovery_codes column",
    "users.totp_failed_attempts column",
    "users.totp_locked_until column",
    "oauth_tokens table",
    "app_tokens table",
    "idx_app_tokens_user_active",
    "idx_app_tokens_token_lookup",
    "app_config table",
    "user_collections table",
    "user_collections.media column",
    "idx_user_collections_user_id",
    "idx_user_collections_release_id",
    "idx_user_collections_media_families",
    "user_wantlists table",
    "user_wantlists.media column",
    "idx_user_wantlists_user_id",
    "idx_user_wantlists_release_id",
    "sync_history table",
    "idx_sync_history_user_started",
    "idx_sync_history_running",
    "extraction_history",
    "idx_extraction_history_status",
    "idx_extraction_history_created_at",
    "queue_metrics table",
    "idx_queue_metrics_recorded_queue",
    "service_health_metrics table",
    "idx_service_health_recorded_service",
    "admin_audit_log table",
    "idx_admin_audit_log_created_at",
    "idx_admin_audit_log_admin_id",
    "insights schema",
    "insights.artist_centrality table",
    "insights.genre_trends table",
    "insights.label_longevity table",
    "insights.monthly_anniversaries table",
    "insights.data_completeness table",
    "insights.release_rarity table",
    "idx_release_rarity_score",
    "idx_release_rarity_tier",
    "idx_release_rarity_gem",
    "insights.community_counts table",
    "idx_community_counts_fetched",
    "insights.computation_log table",
    "idx_computation_log_type_started",
    "idx_anniversaries_month_year",
    "idx_genre_trends_genre",
    "insights.release_rarity add collection_prevalence",
    "insights.release_rarity add media_families",
    "insights.release_rarity add family_signals",
    "insights.release_rarity add medium_rarity",
    "musicbrainz schema",
    "musicbrainz.artists table",
    "musicbrainz.labels table",
    "musicbrainz.releases table",
    "musicbrainz.releases.media column",
    "musicbrainz.release_groups table",
    "musicbrainz.relationships table",
    "musicbrainz.relationships widen natural key to include begin_date/end_date/attributes",
    "musicbrainz.external_links table",
    "musicbrainz.artists.discogs_artist_id widen to BIGINT",
    "musicbrainz.labels.discogs_label_id widen to BIGINT",
    "musicbrainz.releases.discogs_release_id widen to BIGINT",
    "musicbrainz.release_groups.discogs_master_id widen to BIGINT",
    "idx_mb_artists_discogs_id",
    "idx_mb_labels_discogs_id",
    "idx_mb_releases_discogs_id",
    "idx_mb_releases_media_families",
    "idx_mb_release_groups_discogs_id",
    "idx_mb_artists_name",
    "idx_mb_labels_name",
    "idx_mb_rels_source",
    "idx_mb_rels_target",
    "idx_mb_rels_type",
    "idx_mb_links_mbid",
    "idx_mb_links_service",
]

# Every statement name the two records add.  Listing them explicitly is what
# keeps the snapshot test above honest: a new statement must be declared here
# before the legacy comparison will pass.
_ADDED_STATEMENT_NAMES = frozenset(
    {
        "artists add gm_item_id column",
        "idx_artists_gm_item_id",
        "labels add gm_item_id column",
        "idx_labels_gm_item_id",
        "masters add gm_item_id column",
        "idx_masters_gm_item_id",
        "releases add gm_item_id column",
        "idx_releases_gm_item_id",
        "user_collections.gm_item_id column",
        "user_collections.owned_copy_id column",
        "idx_user_collections_gm_item_id",
        "idx_user_collections_owned_copy_id",
        "user_wantlists.gm_item_id column",
        "idx_user_wantlists_gm_item_id",
        "catalog_items table",
        "artifacts table",
        "owned_copies table",
        "idx_owned_copies_user_id",
        "idx_owned_copies_collection_row_id",
        "collection_snapshots table",
        "idx_collection_snapshots_user_taken_at",
        "observations table",
        "idx_observations_user_id",
        "idx_observations_artifact_kind",
        "provider_aliases table",
        "idx_provider_aliases_provider_entity_kind_external_id",
        "idx_provider_aliases_native_id",
        "musicbrainz.artists.gm_item_id column",
        "musicbrainz.labels.gm_item_id column",
        "musicbrainz.releases.gm_item_id column",
        "musicbrainz.release_groups.gm_item_id column",
        "idx_mb_artists_gm_item_id",
        "idx_mb_labels_gm_item_id",
        "idx_mb_releases_gm_item_id",
        "idx_mb_release_groups_gm_item_id",
        "activity schema",
        "activity.user_subjects table",
        "activity.consent_grants table",
        "idx_activity_consent_grants_user_purpose",
        "activity.events table",
        "activity.events_default partition",
        "idx_activity_events_subject_occurred_at",
        "idx_activity_events_type_occurred_at",
        "activity.impressions table",
        "activity.impressions_default partition",
        "idx_activity_impressions_subject_occurred_at",
        "idx_activity_impressions_candidate_set_id",
        "activity.ensure_month_partition function",
        "activity.reject_mutation function",
        "activity.events reject_mutation trigger",
        "activity.impressions reject_mutation trigger",
        "activity.erasures table",
        "idx_activity_erasures_subject_id",
        "insights.activity_summary table",
        "idx_activity_summary_date",
        "idx_releases_identifiers",
        "idx_releases_companies",
        "musicbrainz.relationships.updated_at column",
        "musicbrainz.external_links.updated_at column",
        "idx_mb_rels_updated_at",
        "idx_mb_links_updated_at",
        "loader_extraction_latch table",
    }
)

# The graph schema adds one statement for the schema itself, one for the trigram
# extension, one per rendered vocabulary function, one per loader-written table
# and index, one guarded migration per relation whose shape or key type moved,
# and one per surviving view. They are transcribed here for the same reason the
# names above are: the snapshot test only proves that no LEGACY statement moved
# or changed, and it can only do that if every new name is declared by hand.
# The endpoint-pair indexes spike gm-database-schema-9c8.2 found missing. Every
# one of the sixteen `graph.mb_rel_<source>_<target>` views filters on the pair
# and nothing indexed it, so they belong to the graph work rather than to the
# legacy MusicBrainz statement set.
_ADDED_MUSICBRAINZ_INDEX_NAMES = frozenset({"idx_mb_rels_endpoint_source", "idx_mb_rels_endpoint_target"})

_GRAPH_STATEMENT_NAMES = frozenset(
    {
        "graph schema",
        "pg_trgm extension",
        "graph.credit_role_category function",
        "graph.medium_label function",
        "graph.mb_relationship_type function",
        "graph.genre view-to-table migration",
        "graph.style view-to-table migration",
        "graph.person view-to-table migration",
        "graph.media_family view-to-table migration",
        "graph.medium view-to-table migration",
        "graph.company view-to-table migration",
        "graph.by_artist view-to-table migration",
        "graph.on_label view-to-table migration",
        "graph.derived_from view-to-table migration",
        "graph.in_genre view-to-table migration",
        "graph.in_style view-to-table migration",
        "graph.master_by_artist view-to-table migration",
        "graph.master_in_genre view-to-table migration",
        "graph.master_in_style view-to-table migration",
        "graph.member_of view-to-table migration",
        "graph.alias_of view-to-table migration",
        "graph.credited_on view-to-table migration",
        "graph.same_as view-to-table migration",
        "graph.credited_to view-to-table migration",
        "graph.issued_on view-to-table migration",
        "graph.artist key-type migration",
        "graph.label key-type migration",
        "graph.master key-type migration",
        "graph.release key-type migration",
        "graph.sublabel_of key-type migration",
        "graph.genre table",
        "graph.genre_name_trgm index",
        "graph.style table",
        "graph.style_name_trgm index",
        "graph.person table",
        "graph.person_name_trgm index",
        "graph.media_family table",
        "graph.medium table",
        "graph.company table",
        "graph.by_artist table",
        "graph.by_artist_reverse index",
        "graph.on_label table",
        "graph.on_label_reverse index",
        "graph.derived_from table",
        "graph.derived_from_reverse index",
        "graph.in_genre table",
        "graph.in_genre_reverse index",
        "graph.in_style table",
        "graph.in_style_reverse index",
        "graph.master_by_artist table",
        "graph.master_by_artist_reverse index",
        "graph.master_in_genre table",
        "graph.master_in_genre_reverse index",
        "graph.master_in_style table",
        "graph.master_in_style_reverse index",
        "graph.member_of table",
        "graph.member_of_reverse index",
        "graph.alias_of table",
        "graph.alias_of_reverse index",
        "graph.credited_on table",
        "graph.credited_on_reverse index",
        "graph.credited_on_role_category index",
        "graph.same_as table",
        "graph.same_as_reverse index",
        "graph.credited_to table",
        "graph.credited_to_reverse index",
        "graph.credited_to_source index",
        "graph.issued_on table",
        "graph.issued_on_reverse index",
        "graph.issued_on_source index",
        "graph.artist_member_of table",
        "graph.artist_member_of_reverse index",
        "graph.genre_stats table",
        "graph.genre_stats_first_year index",
        "graph.style_stats table",
        "graph.style_stats_first_year index",
        "graph.label_stats table",
        "graph.label_stats_release_count index",
        "graph.artist_degree table",
        "graph.artist_degree_degree index",
        "graph.release_degree_base table",
        "graph.vertex_degree table",
        "graph.artist_genre table",
        "graph.artist_genre_reverse index",
        "graph.label_genre table",
        "graph.label_genre_reverse index",
        "graph.artist view",
        "graph.label view",
        "graph.master view",
        "graph.release view",
        "graph.part_of view",
        "graph.sublabel_of view",
        "graph.mb_artist view",
        "graph.mb_label view",
        "graph.mb_release view",
        "graph.mb_release_group view",
        "graph.mb_rel_artist_artist view",
        "graph.mb_rel_artist_label view",
        "graph.mb_rel_artist_release view",
        "graph.mb_rel_artist_release_group view",
        "graph.mb_rel_label_artist view",
        "graph.mb_rel_label_label view",
        "graph.mb_rel_label_release view",
        "graph.mb_rel_label_release_group view",
        "graph.mb_rel_release_artist view",
        "graph.mb_rel_release_label view",
        "graph.mb_rel_release_release view",
        "graph.mb_rel_release_release_group view",
        "graph.mb_rel_release_group_artist view",
        "graph.mb_rel_release_group_label view",
        "graph.mb_rel_release_group_release view",
        "graph.mb_rel_release_group_release_group view",
        "graph.app_user view",
        "graph.catalog_item view",
        "graph.collected view",
        "graph.wants view",
        "graph.owns view",
        "graph.in_family view",
        "graph.genre_vertex view",
        "graph.style_vertex view",
        "graph.label_vertex view",
        "graph.artist_vertex view",
        "graph.release_degree view",
        "graph.refresh_artist_member_of function",
        "graph.refresh_vertex_degree function",
        "graph.find_shortest_path function",
        "graph.bootstrap_fill function",
    }
)


def _user_tables() -> dict[str, str]:
    return dict(_USER_TABLES)


def _user_table_names() -> list[str]:
    return [name for name, _stmt in _USER_TABLES]


def _entity_statements() -> dict[str, str]:
    return {name: str(stmt) for name, stmt in _entity_schema_statements()}


class TestCatalogItems:
    """catalog_items — one row per catalog entity, keyed by a UUID version 7."""

    def test_table_defined(self) -> None:
        assert "catalog_items table" in _user_tables()

    def test_is_idempotent(self) -> None:
        assert "CREATE TABLE IF NOT EXISTS catalog_items" in _user_tables()["catalog_items table"]

    def test_primary_key_defaults_to_uuidv7(self) -> None:
        """PostgreSQL 18 is pinned, so uuidv7() is a standard facility."""
        stmt = _user_tables()["catalog_items table"]
        assert "id         UUID PRIMARY KEY DEFAULT uuidv7()" in stmt

    def test_kind_check_is_the_closed_catalog_set(self) -> None:
        stmt = _user_tables()["catalog_items table"]
        assert "CHECK (kind IN (" in stmt
        for kind in _CATALOG_KINDS:
            assert f"'{kind}'" in stmt, f"catalog kind '{kind}' missing from the CHECK"

    def test_created_at_present(self) -> None:
        assert "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()" in _user_tables()["catalog_items table"]


class TestArtifacts:
    """artifacts — one specific edition of a release-kind item, user-creatable."""

    def test_table_defined(self) -> None:
        assert "artifacts table" in _user_tables()

    def test_references_catalog_items_and_users(self) -> None:
        stmt = _user_tables()["artifacts table"]
        assert "item_id    UUID NOT NULL REFERENCES catalog_items(id)" in stmt
        assert "created_by UUID REFERENCES users(id)" in stmt

    def test_created_by_is_nullable(self) -> None:
        """A catalog-minted artifact has no creating user."""
        stmt = _user_tables()["artifacts table"]
        assert "created_by UUID REFERENCES users(id)" in stmt
        assert "created_by UUID NOT NULL" not in stmt

    def test_declared_after_catalog_items(self) -> None:
        names = _user_table_names()
        assert names.index("catalog_items table") < names.index("artifacts table")


class TestOwnedCopies:
    """owned_copies — the physical copy as a first-class entity."""

    def test_table_defined(self) -> None:
        assert "owned_copies table" in _user_tables()

    def test_required_columns(self) -> None:
        stmt = _user_tables()["owned_copies table"]
        for column in (
            "id",
            "user_id",
            "artifact_id",
            "item_id",
            "collection_row_id",
            "acquired_at",
            "created_at",
            "updated_at",
        ):
            assert column in stmt, f"Missing column '{column}' in owned_copies"

    def test_cascades_on_user_delete(self) -> None:
        assert "user_id           UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE" in _user_tables()["owned_copies table"]

    def test_collection_row_link_is_set_null(self) -> None:
        """The copy outlives the provider-keyed collection row it was synced from."""
        stmt = _user_tables()["owned_copies table"]
        assert "collection_row_id UUID REFERENCES user_collections(id) ON DELETE SET NULL" in stmt

    def test_user_id_index_defined(self) -> None:
        stmt = _user_tables()["idx_owned_copies_user_id"]
        assert "CREATE INDEX IF NOT EXISTS idx_owned_copies_user_id ON owned_copies (user_id)" in stmt

    def test_collection_row_uniqueness_is_partial(self) -> None:
        """collection_row_id is nullable, so uniqueness must skip the NULLs."""
        stmt = _user_tables()["idx_owned_copies_collection_row_id"]
        assert "CREATE UNIQUE INDEX IF NOT EXISTS" in stmt
        assert "ON owned_copies (collection_row_id)" in stmt
        assert "WHERE collection_row_id IS NOT NULL" in stmt

    def test_declared_after_users_and_user_collections(self) -> None:
        names = _user_table_names()
        assert names.index("users table") < names.index("owned_copies table")
        assert names.index("user_collections table") < names.index("owned_copies table")
        assert names.index("artifacts table") < names.index("owned_copies table")

    def test_indexes_follow_the_table(self) -> None:
        names = _user_table_names()
        for index in ("idx_owned_copies_user_id", "idx_owned_copies_collection_row_id"):
            assert names.index("owned_copies table") < names.index(index)


class TestCollectionSnapshots:
    """collection_snapshots — content-hashed collection membership at an instant."""

    def test_table_defined(self) -> None:
        assert "collection_snapshots table" in _user_tables()

    def test_required_columns_and_types(self) -> None:
        stmt = _user_tables()["collection_snapshots table"]
        assert "content_hash BYTEA NOT NULL" in stmt
        assert "item_count   INTEGER NOT NULL" in stmt
        assert "copy_ids     UUID[] NOT NULL" in stmt
        assert "taken_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()" in stmt

    def test_cascades_on_user_delete(self) -> None:
        assert "REFERENCES users(id) ON DELETE CASCADE" in _user_tables()["collection_snapshots table"]

    def test_user_taken_at_index_is_descending(self) -> None:
        """The hot read is the newest snapshot for one user."""
        stmt = _user_tables()["idx_collection_snapshots_user_taken_at"]
        assert "ON collection_snapshots (user_id, taken_at DESC)" in stmt
        assert "IF NOT EXISTS" in stmt


class TestObservations:
    """observations — user-captured evidence about a copy or an edition."""

    def test_table_defined(self) -> None:
        assert "observations table" in _user_tables()

    def test_required_columns(self) -> None:
        stmt = _user_tables()["observations table"]
        for column in ("kind", "value", "source", "confidence", "observed_at", "created_at"):
            assert column in stmt, f"Missing column '{column}' in observations"

    def test_kind_value_and_source_are_required(self) -> None:
        stmt = _user_tables()["observations table"]
        assert "kind          TEXT NOT NULL" in stmt
        assert "value         TEXT NOT NULL" in stmt
        assert "source        TEXT NOT NULL" in stmt

    def test_confidence_is_optional(self) -> None:
        """A user assertion has no confidence; an inference does."""
        assert "confidence    REAL," in _user_tables()["observations table"]

    def test_at_least_one_subject_is_required(self) -> None:
        stmt = _user_tables()["observations table"]
        assert "CHECK (owned_copy_id IS NOT NULL OR artifact_id IS NOT NULL)" in stmt

    def test_owned_copy_cascade(self) -> None:
        assert "owned_copy_id UUID REFERENCES owned_copies(id) ON DELETE CASCADE" in _user_tables()["observations table"]

    def test_indexes_defined(self) -> None:
        tables = _user_tables()
        assert "ON observations (user_id)" in tables["idx_observations_user_id"]
        assert "ON observations (artifact_id, kind)" in tables["idx_observations_artifact_kind"]

    def test_declared_after_owned_copies_and_artifacts(self) -> None:
        names = _user_table_names()
        assert names.index("owned_copies table") < names.index("observations table")
        assert names.index("artifacts table") < names.index("observations table")


class TestProviderAliases:
    """provider_aliases — every provider identifier demoted to evidence."""

    def test_table_defined(self) -> None:
        assert "provider_aliases table" in _user_tables()

    def test_vocabulary_columns_present(self) -> None:
        """The column set published in taxonomy/identity/v1."""
        stmt = _user_tables()["provider_aliases table"]
        for column in (
            "provider",
            "entity_kind",
            "external_id",
            "native_id",
            "valid_from",
            "valid_to",
            "confidence",
            "source",
            "asserted_at",
        ):
            assert column in stmt, f"Missing column '{column}' in provider_aliases"

    def test_required_columns_are_not_null(self) -> None:
        stmt = _user_tables()["provider_aliases table"]
        for fragment in (
            "provider    TEXT NOT NULL",
            "entity_kind TEXT NOT NULL",
            "external_id TEXT NOT NULL",
            "native_id   UUID NOT NULL",
            "valid_from  TIMESTAMPTZ NOT NULL DEFAULT NOW()",
            "confidence  REAL NOT NULL DEFAULT 1.0",
            "source      TEXT NOT NULL DEFAULT 'catalog'",
            "asserted_at TIMESTAMPTZ NOT NULL DEFAULT NOW()",
        ):
            assert fragment in stmt, f"Missing NOT NULL column '{fragment}'"

    def test_valid_to_is_nullable(self) -> None:
        """An open validity interval is what marks the currently valid row."""
        stmt = _user_tables()["provider_aliases table"]
        assert "valid_to    TIMESTAMPTZ," in stmt

    def test_external_id_is_text_not_an_integer(self) -> None:
        """Providers are namespaces, not numbers: a barcode is not a BIGINT."""
        assert "external_id TEXT NOT NULL" in _user_tables()["provider_aliases table"]

    def test_lookup_or_create_key_is_partial_and_unique(self) -> None:
        stmt = _user_tables()["idx_provider_aliases_provider_entity_kind_external_id"]
        assert "CREATE UNIQUE INDEX IF NOT EXISTS" in stmt
        assert "ON provider_aliases (provider, entity_kind, external_id)" in stmt
        assert "WHERE valid_to IS NULL" in stmt

    def test_native_id_index_defined(self) -> None:
        """Reverse resolution: every alias asserted about one native id."""
        stmt = _user_tables()["idx_provider_aliases_native_id"]
        assert "ON provider_aliases (native_id)" in stmt
        assert "IF NOT EXISTS" in stmt

    def test_indexes_follow_the_table(self) -> None:
        names = _user_table_names()
        for index in (
            "idx_provider_aliases_provider_entity_kind_external_id",
            "idx_provider_aliases_native_id",
        ):
            assert names.index("provider_aliases table") < names.index(index)


class TestIdentityTableOrdering:
    """The identity block is declared after everything it references."""

    def test_all_identity_tables_present(self) -> None:
        names = _user_table_names()
        for table in _IDENTITY_TABLES:
            assert table in names, f"Missing identity table statement '{table}'"

    def test_declared_in_dependency_order(self) -> None:
        names = _user_table_names()
        positions = [names.index(table) for table in _IDENTITY_TABLES]
        assert positions == sorted(positions)

    def test_declared_after_users_and_collections(self) -> None:
        names = _user_table_names()
        first = min(names.index(table) for table in _IDENTITY_TABLES)
        for prerequisite in ("users table", "user_collections table", "user_wantlists table"):
            assert names.index(prerequisite) < first

    def test_every_identity_statement_is_idempotent(self) -> None:
        tables = _user_tables()
        for table in _IDENTITY_TABLES:
            assert "IF NOT EXISTS" in tables[table]

    def test_no_drop_in_identity_statements(self) -> None:
        tables = _user_tables()
        for table in _IDENTITY_TABLES:
            assert "DROP" not in tables[table].upper()

    def test_native_tables_use_uuidv7_not_gen_random_uuid(self) -> None:
        """Native ids are time-ordered so index locality behaves like a sequence."""
        tables = _user_tables()
        for table in _IDENTITY_TABLES:
            assert "DEFAULT uuidv7()" in tables[table], f"{table} does not mint a UUID version 7"
            assert "gen_random_uuid()" not in tables[table]


class TestDiscogsEntityNativeIdColumns:
    """artists, labels, masters, releases each gain gm_item_id."""

    def test_column_added_for_every_entity_table(self) -> None:
        statements = _entity_statements()
        for table in _ENTITY_TABLES:
            name = f"{table} add gm_item_id column"
            assert name in statements, f"Missing gm_item_id column for '{table}'"
            assert "ADD COLUMN IF NOT EXISTS" in statements[name]
            assert "gm_item_id UUID" in statements[name]

    def test_index_added_for_every_entity_table(self) -> None:
        statements = _entity_statements()
        for table in _ENTITY_TABLES:
            name = f"idx_{table}_gm_item_id"
            assert name in statements, f"Missing gm_item_id index for '{table}'"
            assert "CREATE INDEX IF NOT EXISTS" in statements[name]

    def test_column_precedes_its_index(self) -> None:
        names = [name for name, _stmt in _entity_schema_statements()]
        for table in _ENTITY_TABLES:
            assert names.index(f"{table} add gm_item_id column") < names.index(f"idx_{table}_gm_item_id")

    def test_column_follows_its_create_table(self) -> None:
        names = [name for name, _stmt in _entity_schema_statements()]
        for table in _ENTITY_TABLES:
            assert names.index(f"{table} table") < names.index(f"{table} add gm_item_id column")

    def test_data_id_primary_key_is_unchanged(self) -> None:
        """Expand only: the Discogs id stays the primary key under contract v1."""
        statements = _entity_statements()
        for table in _ENTITY_TABLES:
            assert "data_id" in statements[f"{table} table"]
            assert "PRIMARY KEY" in statements[f"{table} table"]


class TestUserTableNativeIdColumns:
    """user_collections and user_wantlists gain native-id columns."""

    def test_user_collections_gm_item_id(self) -> None:
        stmt = _user_tables()["user_collections.gm_item_id column"]
        assert stmt == "ALTER TABLE user_collections ADD COLUMN IF NOT EXISTS gm_item_id UUID"

    def test_user_collections_owned_copy_id(self) -> None:
        stmt = _user_tables()["user_collections.owned_copy_id column"]
        assert stmt == "ALTER TABLE user_collections ADD COLUMN IF NOT EXISTS owned_copy_id UUID"

    def test_user_wantlists_gm_item_id(self) -> None:
        stmt = _user_tables()["user_wantlists.gm_item_id column"]
        assert stmt == "ALTER TABLE user_wantlists ADD COLUMN IF NOT EXISTS gm_item_id UUID"

    def test_indexes_defined(self) -> None:
        tables = _user_tables()
        assert "ON user_collections (gm_item_id)" in tables["idx_user_collections_gm_item_id"]
        assert "ON user_collections (owned_copy_id)" in tables["idx_user_collections_owned_copy_id"]
        assert "ON user_wantlists (gm_item_id)" in tables["idx_user_wantlists_gm_item_id"]

    def test_columns_precede_their_indexes(self) -> None:
        names = _user_table_names()
        for column, index in (
            ("user_collections.gm_item_id column", "idx_user_collections_gm_item_id"),
            ("user_collections.owned_copy_id column", "idx_user_collections_owned_copy_id"),
            ("user_wantlists.gm_item_id column", "idx_user_wantlists_gm_item_id"),
        ):
            assert names.index(column) < names.index(index)

    def test_legacy_provider_keys_retained(self) -> None:
        """release_id and instance_id stay until a contraction decision retires them."""
        stmt = _user_tables()["user_collections table"]
        assert "release_id   BIGINT NOT NULL" in stmt
        assert "instance_id  BIGINT" in stmt


class TestMusicBrainzNativeIdColumns:
    """The four MusicBrainz entity tables gain gm_item_id."""

    def test_column_added_for_every_table(self) -> None:
        statements = dict(_MUSICBRAINZ_TABLES)
        for table in _MUSICBRAINZ_ENTITIES:
            name = f"musicbrainz.{table}.gm_item_id column"
            assert name in statements, f"Missing gm_item_id column for musicbrainz.{table}"
            assert statements[name] == f"ALTER TABLE musicbrainz.{table} ADD COLUMN IF NOT EXISTS gm_item_id UUID"

    def test_index_added_for_every_table(self) -> None:
        statements = dict(_MUSICBRAINZ_INDEXES)
        for table, index in (
            ("artists", "idx_mb_artists_gm_item_id"),
            ("labels", "idx_mb_labels_gm_item_id"),
            ("releases", "idx_mb_releases_gm_item_id"),
            ("release_groups", "idx_mb_release_groups_gm_item_id"),
        ):
            assert index in statements, f"Missing gm_item_id index for musicbrainz.{table}"
            assert f"ON musicbrainz.{table} (gm_item_id)" in statements[index]
            assert "IF NOT EXISTS" in statements[index]

    def test_columns_are_declared_before_the_index_list_runs(self) -> None:
        """_MUSICBRAINZ_TABLES is yielded in full before _MUSICBRAINZ_INDEXES."""
        names = [name for name, _stmt in _schema_statements()]
        last_column = max(names.index(f"musicbrainz.{table}.gm_item_id column") for table in _MUSICBRAINZ_ENTITIES)
        first_index = min(
            names.index(index)
            for index in (
                "idx_mb_artists_gm_item_id",
                "idx_mb_labels_gm_item_id",
                "idx_mb_releases_gm_item_id",
                "idx_mb_release_groups_gm_item_id",
            )
        )
        assert last_column < first_index

    def test_mbid_primary_key_is_unchanged(self) -> None:
        statements = dict(_MUSICBRAINZ_TABLES)
        for table in _MUSICBRAINZ_ENTITIES:
            assert "mbid UUID PRIMARY KEY" in statements[f"musicbrainz.{table} table"]


class TestLegacyStatementsUnchanged:
    """Every statement that existed before ADR 0009 and ADR 0010 survives, in order."""

    def test_legacy_names_are_intact_and_ordered(self) -> None:
        names = [name for name, _stmt in _schema_statements()]
        added = _ADDED_STATEMENT_NAMES | _GRAPH_STATEMENT_NAMES | _ADDED_MUSICBRAINZ_INDEX_NAMES
        surviving = [name for name in names if name not in added]
        assert surviving == _LEGACY_STATEMENT_NAMES

    def test_no_legacy_name_was_reused_for_a_new_statement(self) -> None:
        added = _ADDED_STATEMENT_NAMES | _GRAPH_STATEMENT_NAMES | _ADDED_MUSICBRAINZ_INDEX_NAMES
        assert not (added & set(_LEGACY_STATEMENT_NAMES))

    def test_declared_graph_names_match_the_schema(self) -> None:
        """A new graph view has to be transcribed above before the snapshot passes."""
        assert {name for name, _stmt in _GRAPH_STATEMENTS} == _GRAPH_STATEMENT_NAMES

    def test_statement_names_are_unique(self) -> None:
        names = [name for name, _stmt in _schema_statements()]
        assert len(names) == len(set(names))

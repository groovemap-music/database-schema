"""Tests for the PostgreSQL schema definitions."""

from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from groovemap_schema.postgres import (
    _ACTIVITY_STATEMENTS,
    _ARTIST_EMBEDDING_RELEASES_CLEAR_CURRENT_SQL,
    _ARTIST_EMBEDDING_RELEASES_CURRENT_INDEX,
    _ARTIST_EMBEDDING_RELEASES_IS_CURRENT_QUERY,
    _ARTIST_EMBEDDING_RELEASES_STATEMENT,
    _ARTIST_EMBEDDING_RELEASES_VERSION_DELETE_SQL,
    _ARTIST_EMBEDDINGS_HNSW_INDEX_REINDEX_STATEMENT,
    _ARTIST_EMBEDDINGS_HNSW_INDEX_STATEMENT,
    _ARTIST_EMBEDDINGS_STATEMENT,
    _ARTIST_EMBEDDINGS_VERSION_DELETE_SQL,
    _ARTIST_SIMILAR_ARTISTS_MODEL_VERSION_INDEX,
    _ARTIST_SIMILAR_ARTISTS_STATEMENT,
    _ARTIST_SIMILAR_ARTISTS_VERSION_DELETE_SQL,
    _ARTIST_SIMILARITY_STATEMENTS,
    _ARTIST_SIMILARITY_TABLES_GRANT,
    _EMBEDDINGS_TABLE_GRANT,
    _ENTITY_TABLES,
    _GRAPH_STATEMENTS,
    _HNSW_BUILD_MAINTENANCE_WORK_MEM,
    _INSIGHTS_TABLES,
    _IS_SUPERUSER_QUERY,
    _MUSICBRAINZ_INDEXES,
    _MUSICBRAINZ_TABLES,
    _PIPELINE_ROLE_STATEMENTS,
    _RESET_MAINTENANCE_WORK_MEM,
    _SPECIFIC_INDEXES,
    _USER_TABLES,
    _VECTOR_EXTENSION_INSTALLED_QUERY,
    _VECTOR_SCHEMA_STATEMENTS,
    ARTIST_EMBEDDINGS_HNSW_INDEX_NAME,
    EMBEDDING_PIPELINE_ROLE,
    VECTOR_EXTENSION,
    _apply_vector_schema,
    _artist_embeddings_version_index_create_statement,
    _artist_embeddings_version_index_drop_statement,
    _connection_is_autocommit,
    _set_maintenance_work_mem_statement,
    _sql_string_literal,
    _valid_index_name,
    _valid_maintenance_work_mem,
    _valid_model_version,
    _vector_schema_skip_reasons,
    build_artist_embeddings_index,
    build_artist_embeddings_version_index,
    create_postgres_schema,
    publish_artist_embedding_release,
    retire_artist_embeddings_version,
    retire_artist_similar_artists_version,
)


@pytest.fixture
def mock_pool() -> MagicMock:
    """Mock AsyncPostgreSQLPool with async context manager support."""
    pool = MagicMock()
    mock_conn = AsyncMock()
    mock_cursor = AsyncMock()
    mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
    mock_cursor.__aexit__ = AsyncMock(return_value=False)
    mock_cursor.execute = AsyncMock()
    # Closed by default, like the property graph switch being off: the vector
    # extension and CREATEROLE probes both read a falsy first column, so
    # `_apply_vector_schema` skips its statements unless a test overrides this.
    mock_cursor.fetchone = AsyncMock(return_value=(False,))
    mock_conn.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_conn.__aexit__ = AsyncMock(return_value=False)
    # cursor() is synchronous in psycopg (returns an async context manager, not a coroutine)
    mock_conn.cursor = MagicMock(return_value=mock_cursor)
    pool.connection.return_value = mock_conn
    return pool


class TestEntityTables:
    """Verify the entity table list is correct."""

    def test_all_four_tables_present(self) -> None:
        assert set(_ENTITY_TABLES) == {"artists", "labels", "masters", "releases"}

    def test_table_order_is_stable(self) -> None:
        assert _ENTITY_TABLES == ["artists", "labels", "masters", "releases"]


class TestSpecificIndexes:
    """Verify the per-table index definitions."""

    def test_not_empty(self) -> None:
        assert len(_SPECIFIC_INDEXES) > 0

    def test_each_entry_is_name_sql_pair(self) -> None:
        for entry in _SPECIFIC_INDEXES:
            assert len(entry) == 2, f"Expected (name, sql) pair, got: {entry!r}"
            name, stmt = entry
            assert isinstance(name, str) and name
            assert isinstance(stmt, str) and stmt

    def test_all_indexes_use_if_not_exists(self) -> None:
        for name, stmt in _SPECIFIC_INDEXES:
            assert "IF NOT EXISTS" in stmt, f"Index '{name}' is missing IF NOT EXISTS"

    def test_no_drop_statements(self) -> None:
        for name, stmt in _SPECIFIC_INDEXES:
            assert "DROP" not in stmt.upper(), f"Index '{name}' contains a DROP statement"

    def test_covers_all_entity_tables(self) -> None:
        # _SPECIFIC_INDEXES also carries the "releases add media column" ALTER
        # (it must run before the media GIN index below it), which has no
        # "ON <table>" clause — only CREATE INDEX statements name a table that way.
        index_tables = {stmt.split("ON ")[1].split(" ")[0] for _, stmt in _SPECIFIC_INDEXES if "CREATE INDEX" in stmt.upper()}
        for table in _ENTITY_TABLES:
            assert table in index_tables, f"No specific indexes for table '{table}'"

    def test_releases_has_gin_indexes(self) -> None:
        release_gin = [n for n, s in _SPECIFIC_INDEXES if "releases" in s and "GIN" in s]
        assert len(release_gin) >= 2

    def test_fts_gin_indexes_defined(self) -> None:
        """All four entity tables must have a GIN FTS index."""
        fts_names = {name for name, _ in _SPECIFIC_INDEXES if name.startswith("idx_") and "_fts" in name}
        assert fts_names == {
            "idx_artists_fts",
            "idx_labels_fts",
            "idx_masters_fts",
            "idx_releases_fts",
        }

    def test_fts_indexes_use_gin(self) -> None:
        """FTS indexes must use USING GIN with to_tsvector."""
        fts = {name: stmt for name, stmt in _SPECIFIC_INDEXES if "_fts" in name}
        for name, stmt in fts.items():
            assert "USING GIN" in stmt, f"{name} missing USING GIN"
            assert "to_tsvector" in stmt, f"{name} missing to_tsvector"
            assert "english" in stmt, f"{name} missing language 'english'"

    def test_identifiers_and_companies_gin_indexes_defined(self) -> None:
        """ADR 0011: containment queries over the additive identifiers/companies
        blocks need the same GIN index shape media families already has."""
        indexes = dict(_SPECIFIC_INDEXES)
        assert (
            indexes["idx_releases_identifiers"] == "CREATE INDEX IF NOT EXISTS idx_releases_identifiers ON releases USING GIN ((data->'identifiers'))"
        )
        assert indexes["idx_releases_companies"] == "CREATE INDEX IF NOT EXISTS idx_releases_companies ON releases USING GIN ((data->'companies'))"


class TestInsightsSchema:
    """Tests for insights schema tables."""

    def test_insights_tables_are_defined(self) -> None:
        """Verify the _INSIGHTS_TABLES list contains all insight tables."""
        table_names = [name for name, _stmt in _INSIGHTS_TABLES]
        assert "insights schema" in table_names
        assert "insights.artist_centrality table" in table_names
        assert "insights.genre_trends table" in table_names
        assert "insights.label_longevity table" in table_names
        assert "insights.monthly_anniversaries table" in table_names
        assert "insights.data_completeness table" in table_names
        assert "insights.computation_log table" in table_names


class TestAppTokensTable:
    """Schema-shape tests for the app_tokens third-party auth table."""

    def _user_tables_dict(self) -> dict[str, str]:
        return dict(_USER_TABLES)

    def test_app_tokens_table_defined(self) -> None:
        assert "app_tokens table" in self._user_tables_dict()

    def test_app_tokens_required_columns(self) -> None:
        stmt = self._user_tables_dict()["app_tokens table"]
        for column in (
            "id",
            "user_id",
            "name",
            "scope",
            "token_hash",
            "created_at",
            "last_used_at",
            "revoked_at",
        ):
            assert column in stmt, f"Missing column '{column}' in app_tokens schema"

    def test_app_tokens_token_hash_is_sha256_sized(self) -> None:
        """SHA-256 hex digests are exactly 64 chars; VARCHAR(64) prevents accidental other-algorithm storage."""
        stmt = self._user_tables_dict()["app_tokens table"]
        assert "token_hash   VARCHAR(64)" in stmt or "token_hash VARCHAR(64)" in stmt

    def test_app_tokens_cascade_on_user_delete(self) -> None:
        stmt = self._user_tables_dict()["app_tokens table"]
        assert "REFERENCES users(id) ON DELETE CASCADE" in stmt

    def test_app_tokens_scope_is_text_array(self) -> None:
        stmt = self._user_tables_dict()["app_tokens table"]
        assert "scope        TEXT[] NOT NULL" in stmt or "scope TEXT[] NOT NULL" in stmt

    def test_app_tokens_partial_indexes_defined(self) -> None:
        names = {name for name, _ in _USER_TABLES}
        assert "idx_app_tokens_user_active" in names
        assert "idx_app_tokens_token_lookup" in names

    def test_app_tokens_indexes_are_partial_on_active_rows(self) -> None:
        """Both indexes must skip revoked rows to keep lookups fast as tombstones accumulate."""
        idx_stmts = {name: stmt for name, stmt in _USER_TABLES if name.startswith("idx_app_tokens_")}
        for name, stmt in idx_stmts.items():
            assert "WHERE revoked_at IS NULL" in stmt, f"{name} is not a partial index on active rows"

    def test_app_tokens_token_lookup_index_keyed_by_hash(self) -> None:
        """The lookup index must be on token_hash — this is the hot path for require_app_token."""
        stmt = dict(_USER_TABLES)["idx_app_tokens_token_lookup"]
        assert "ON app_tokens (token_hash)" in stmt

    def test_app_tokens_user_active_index_keyed_by_user_id(self) -> None:
        """The user-active index serves the settings page list view."""
        stmt = dict(_USER_TABLES)["idx_app_tokens_user_active"]
        assert "ON app_tokens (user_id)" in stmt

    def test_app_tokens_no_drop_in_schema(self) -> None:
        """Tombstone semantics: revoked rows are NEVER deleted; the schema must never drop the table."""
        stmt = self._user_tables_dict()["app_tokens table"]
        assert "DROP" not in stmt.upper()


class TestExtractionLatchTable:
    """The loader family's durable extraction latch (bead gm-database-schema-fyl).

    The name (`public.loader_extraction_latch`, the only relation discogs-sql-loader
    probes) and the required columns' types are pinned to what
    `tableinator/extraction_latch.py`'s `REQUIRED_COLUMNS` and startup probe
    demand, so a relation this schema declares is never silently declined by
    the loader. The `loader` discriminator is itself required by
    `REQUIRED_COLUMNS`, and its `PRIMARY KEY (loader, version)` is what the
    probe verifies via `pg_constraint` (a PRIMARY KEY or UNIQUE constraint
    spanning exactly those two columns; a bare unique index is declined)
    before it scopes every statement to `loader = 'discogs'`.
    """

    def _user_tables_dict(self) -> dict[str, str]:
        return dict(_USER_TABLES)

    def test_table_defined(self) -> None:
        assert "loader_extraction_latch table" in self._user_tables_dict()

    def test_required_columns(self) -> None:
        stmt = self._user_tables_dict()["loader_extraction_latch table"]
        for column in ("loader", "version", "signals", "created_at", "updated_at", "refreshed_at"):
            assert column in stmt, f"Missing column '{column}' in loader_extraction_latch schema"

    def test_loader_and_version_are_not_null(self) -> None:
        """Both key columns must be populated; `PRIMARY KEY (loader, version)` also implies this."""
        stmt = self._user_tables_dict()["loader_extraction_latch table"]
        assert "loader       TEXT NOT NULL" in stmt or "loader TEXT NOT NULL" in stmt
        assert "version      TEXT NOT NULL" in stmt or "version TEXT NOT NULL" in stmt

    def test_primary_key_is_loader_and_version(self) -> None:
        """Matches the loader's `_key_columns()` and its `ON CONFLICT (loader, version)` upsert."""
        stmt = self._user_tables_dict()["loader_extraction_latch table"]
        assert "PRIMARY KEY (loader, version)" in stmt
        assert "CONSTRAINT loader_extraction_latch_pkey" in stmt

    def test_signals_is_a_text_array_defaulting_empty(self) -> None:
        stmt = self._user_tables_dict()["loader_extraction_latch table"]
        assert "signals      TEXT[] NOT NULL DEFAULT '{}'" in stmt or "signals TEXT[] NOT NULL DEFAULT '{}'" in stmt

    def test_refreshed_at_is_nullable(self) -> None:
        """Unset until the derived-relation pass completes for the extraction."""
        stmt = self._user_tables_dict()["loader_extraction_latch table"]
        assert "refreshed_at TIMESTAMPTZ,\n" in stmt

    def test_no_drop_in_schema(self) -> None:
        stmt = self._user_tables_dict()["loader_extraction_latch table"]
        assert "DROP" not in stmt.upper()

    def test_is_idempotent(self) -> None:
        stmt = self._user_tables_dict()["loader_extraction_latch table"]
        assert "IF NOT EXISTS" in stmt.upper()


class TestDurableDerivedRefreshJob:
    """The schema owns job DDL while the current loader stays inline."""

    def test_additive_latch_generation_and_cursor(self) -> None:
        statements = dict(_USER_TABLES)
        assert "ADD COLUMN IF NOT EXISTS generation BIGINT" in statements["loader_extraction_latch generation column"]
        assert "(loader, generation) WHERE generation IS NOT NULL" in statements["idx_loader_extraction_latch_generation"]
        cursor = statements["loader_derived_refresh_cursor table"]
        assert "loader             TEXT PRIMARY KEY" in cursor
        assert "generation         BIGINT NOT NULL DEFAULT 0" in cursor
        assert "version            TEXT" in cursor
        assert "loader_derived_refresh_cursor_version_check" in cursor

    def test_job_has_durable_claim_and_fence_fields(self) -> None:
        statements = dict(_USER_TABLES)
        job = statements["loader_derived_refresh_job table"]
        for field in (
            "state",
            "attempt_count",
            "next_attempt_at",
            "lease_owner",
            "lease_token",
            "lease_epoch",
            "lease_expires_at",
            "last_error",
            "created_at",
            "updated_at",
            "started_at",
            "completed_at",
            "superseded_at",
        ):
            assert field in job
        assert "PRIMARY KEY (loader, version)" in job
        assert "UNIQUE (loader, generation)" in job
        assert "FOREIGN KEY (loader, version, generation) REFERENCES loader_extraction_latch (loader, version, generation)" in job
        assert "char_length(last_error) <= 1024" in job
        assert "loader_derived_refresh_job_lease_check" in job
        assert "loader_derived_refresh_job_due_check" in job
        assert "WHERE state IN ('pending', 'retry')" in statements["idx_loader_derived_refresh_job_due"]
        assert "WHERE state = 'leased'" in statements["idx_loader_derived_refresh_job_lease_expiry"]


class TestCreatePostgresSchema:
    """Test create_postgres_schema with a mock pool."""

    @pytest.mark.asyncio
    async def test_runs_all_statements(self, mock_pool: MagicMock) -> None:
        await create_postgres_schema(mock_pool)

        cursor = mock_pool.connection.return_value.__aenter__.return_value.cursor.return_value
        # 5 statements per entity table (CREATE TABLE + hash index + updated_at
        # index + the additive gm_item_id column and its index)
        # + specific indexes + user tables + insights tables + activity statements
        # + musicbrainz tables/indexes
        expected_calls = (
            len(_ENTITY_TABLES) * 5
            + len(_SPECIFIC_INDEXES)
            + len(_USER_TABLES)
            + len(_INSIGHTS_TABLES)
            + len(_ACTIVITY_STATEMENTS)
            + len(_MUSICBRAINZ_TABLES)
            + len(_MUSICBRAINZ_INDEXES)
            + len(_ARTIST_SIMILARITY_STATEMENTS)
            + len(_GRAPH_STATEMENTS)
            # `_apply_vector_schema` always probes pg_extension and pg_roles
            # (CREATEROLE); with the shared fixture's default (False), the
            # extension gate also probes pg_available_extensions before
            # closing, since it is not yet installed.
            + 3
        )
        assert cursor.execute.await_count == expected_calls

    @pytest.mark.asyncio
    async def test_continues_after_individual_failure(self, mock_pool: MagicMock) -> None:
        """A failing statement must not abort the rest."""
        cursor = mock_pool.connection.return_value.__aenter__.return_value.cursor.return_value
        call_count = 0

        async def flaky(*args: Any, **kwargs: Any) -> None:  # noqa: ARG001
            nonlocal call_count
            call_count += 1
            if call_count % 4 == 0:
                raise Exception("Simulated PostgreSQL error")

        cursor.execute = AsyncMock(side_effect=flaky)

        # Must not raise
        await create_postgres_schema(mock_pool)

        expected_calls = (
            len(_ENTITY_TABLES) * 5
            + len(_SPECIFIC_INDEXES)
            + len(_USER_TABLES)
            + len(_INSIGHTS_TABLES)
            + len(_ACTIVITY_STATEMENTS)
            + len(_MUSICBRAINZ_TABLES)
            + len(_MUSICBRAINZ_INDEXES)
            + len(_ARTIST_SIMILARITY_STATEMENTS)
            + len(_GRAPH_STATEMENTS)
            # `_apply_vector_schema` always probes pg_extension and pg_roles
            # (CREATEROLE); with the shared fixture's default (False), the
            # extension gate also probes pg_available_extensions before
            # closing, since it is not yet installed.
            + 3
        )
        assert cursor.execute.await_count == expected_calls

    @pytest.mark.asyncio
    async def test_all_statements_create_if_not_exists(self, mock_pool: MagicMock) -> None:
        """All statements sent to PostgreSQL must be idempotent."""
        cursor = mock_pool.connection.return_value.__aenter__.return_value.cursor.return_value
        captured: list[str] = []

        async def capture(stmt: Any, *_: Any, **__: Any) -> None:
            # Handle both psycopg sql.SQL objects and plain strings
            captured.append(str(stmt))

        cursor.execute = AsyncMock(side_effect=capture)
        await create_postgres_schema(mock_pool)

        for stmt in captured:
            upper = stmt.upper()
            # ALTER COLUMN ... TYPE is inherently idempotent (re-applying the same
            # target type is a no-op), and CREATE OR REPLACE is the idempotency
            # form PostgreSQL offers for a function or a trigger, neither of which
            # has an IF NOT EXISTS spelling. Both still have to clear the DROP
            # guard below.
            # A DO block is idempotent by its own guard rather than by an
            # IF NOT EXISTS clause: it reads the catalog and acts only when the
            # state it is migrating from is still there, so a second apply is a
            # no-op. Every one of them is checked below instead.
            guarded_block = upper.startswith("DO $")
            # A read-only probe (the vector-extension and CREATEROLE gates) has
            # no state to reapply and is trivially idempotent by never writing
            # anything.
            read_only = upper.strip().startswith("SELECT")
            exempt = ("ALTER COLUMN" in upper and "TYPE " in upper) or "CREATE OR REPLACE" in upper or guarded_block or read_only
            if not exempt:
                assert "IF NOT EXISTS" in upper, f"Statement is not idempotent: {stmt[:80]}..."
            if guarded_block:
                assert "END IF;" in upper, f"Unguarded DO block: {stmt[:80]}..."
            # A multi-statement blob could still hide an un-guarded DROP that would
            # not be idempotent — any DROP must be guarded, either by IF EXISTS or
            # by the catalog probe of the DO block that wraps it.
            if "DROP " in upper:
                assert "IF EXISTS" in upper, f"Statement contains an un-guarded DROP: {stmt[:80]}..."

    @pytest.mark.asyncio
    async def test_all_fail_gracefully(self, mock_pool: MagicMock) -> None:
        """All statements failing should not raise — schema init is best-effort."""
        cursor = mock_pool.connection.return_value.__aenter__.return_value.cursor.return_value
        cursor.execute = AsyncMock(side_effect=Exception("PostgreSQL unavailable"))

        # Must not raise
        await create_postgres_schema(mock_pool)

        expected_calls = (
            len(_ENTITY_TABLES) * 5
            + len(_SPECIFIC_INDEXES)
            + len(_USER_TABLES)
            + len(_INSIGHTS_TABLES)
            + len(_ACTIVITY_STATEMENTS)
            + len(_MUSICBRAINZ_TABLES)
            + len(_MUSICBRAINZ_INDEXES)
            + len(_ARTIST_SIMILARITY_STATEMENTS)
            + len(_GRAPH_STATEMENTS)
            # `_apply_vector_schema` always probes pg_extension and pg_roles
            # (CREATEROLE); with the shared fixture's default (False), the
            # extension gate also probes pg_available_extensions before
            # closing, since it is not yet installed.
            + 3
        )
        assert cursor.execute.await_count == expected_calls

    @pytest.mark.asyncio
    async def test_creates_tables_before_indexes(self, mock_pool: MagicMock) -> None:
        """Tables must be created before their indexes."""
        cursor = mock_pool.connection.return_value.__aenter__.return_value.cursor.return_value
        call_order: list[str] = []

        async def track(stmt: Any, *_: Any, **__: Any) -> None:
            call_order.append(str(stmt))

        cursor.execute = AsyncMock(side_effect=track)
        await create_postgres_schema(mock_pool)

        # Find positions of CREATE TABLE vs CREATE INDEX calls
        table_positions = [i for i, s in enumerate(call_order) if "CREATE TABLE" in s]
        index_positions = [i for i, s in enumerate(call_order) if "CREATE INDEX" in s]

        assert table_positions, "No CREATE TABLE statements found"
        assert index_positions, "No CREATE INDEX statements found"

        # Each table's CREATE TABLE must appear before its first index
        assert min(table_positions) < max(index_positions)

    @pytest.mark.asyncio
    async def test_pool_connection_used(self, mock_pool: MagicMock) -> None:
        await create_postgres_schema(mock_pool)
        mock_pool.connection.assert_called_once()


class TestVectorSchemaSkipReasons:
    """The three-state extension gate, the independent role gate, and the one grant that needs both."""

    def test_already_installed_ignores_availability_and_superuser(self) -> None:
        """Once installed, nothing else about the extension gate matters."""
        reasons = _vector_schema_skip_reasons(vector_installed=True, vector_available=False, is_superuser=False, can_create_role=True)
        assert reasons["extension"] is None

    def test_unavailable_is_the_first_extension_reason(self) -> None:
        reasons = _vector_schema_skip_reasons(vector_installed=False, vector_available=False, is_superuser=False, can_create_role=True)
        assert reasons["extension"] == f"{VECTOR_EXTENSION} extension is not available on this server"
        assert reasons["role"] is None
        assert reasons["embedding_grant"] == reasons["extension"]

    def test_available_but_not_superuser_is_the_second_extension_reason(self) -> None:
        """`vector` carries no `trusted = true`, unlike `pg_trgm`."""
        reasons = _vector_schema_skip_reasons(vector_installed=False, vector_available=True, is_superuser=False, can_create_role=True)
        assert reasons["extension"] is not None
        assert "not a superuser" in reasons["extension"]
        assert reasons["embedding_grant"] == reasons["extension"]

    def test_available_and_superuser_installs_it(self) -> None:
        reasons = _vector_schema_skip_reasons(vector_installed=False, vector_available=True, is_superuser=True, can_create_role=True)
        assert reasons["extension"] is None

    def test_missing_createrole_is_the_role_reason(self) -> None:
        reasons = _vector_schema_skip_reasons(vector_installed=True, vector_available=True, is_superuser=True, can_create_role=False)
        assert reasons["extension"] is None
        assert reasons["role"] == "connecting role lacks CREATEROLE"
        assert reasons["embedding_grant"] == reasons["role"]

    def test_both_gates_closed_reports_the_extension_reason_on_the_shared_grant(self) -> None:
        reasons = _vector_schema_skip_reasons(vector_installed=False, vector_available=False, is_superuser=False, can_create_role=False)
        assert reasons["extension"] is not None
        assert reasons["role"] is not None
        assert reasons["embedding_grant"] == reasons["extension"]

    def test_both_gates_open_creates_everything(self) -> None:
        reasons = _vector_schema_skip_reasons(vector_installed=True, vector_available=True, is_superuser=True, can_create_role=True)
        assert reasons == {"extension": None, "role": None, "embedding_grant": None}


class TestArtistEmbeddingsStatement:
    """Static shape checks over the declared table, independent of any engine."""

    def test_the_table_declares_a_halfvec_128_column(self) -> None:
        assert "halfvec(128)" in _ARTIST_EMBEDDINGS_STATEMENT[1]

    def test_the_primary_key_is_artist_and_model_version(self) -> None:
        assert "PRIMARY KEY (artist_id, model_version)" in _ARTIST_EMBEDDINGS_STATEMENT[1]

    def test_every_column_is_not_null(self) -> None:
        body = _ARTIST_EMBEDDINGS_STATEMENT[1]
        for column in ("artist_id", "model_version", "embedding", "source_dump_id", "source_dump_date", "computed_at"):
            assert f"{column}" in body
        assert body.count("NOT NULL") == 6

    def test_the_statement_is_idempotent(self) -> None:
        assert "CREATE TABLE IF NOT EXISTS" in _ARTIST_EMBEDDINGS_STATEMENT[1]

    def test_the_pipeline_grant_holds_exactly_the_adr_amendment_privileges(self) -> None:
        assert _EMBEDDINGS_TABLE_GRANT[1] == (f"GRANT SELECT, INSERT, UPDATE, DELETE ON public.artist_embeddings TO {EMBEDDING_PIPELINE_ROLE}")

    def test_the_role_holds_no_privilege_outside_graph_and_its_own_table(self) -> None:
        statements = " ".join(statement for _name, statement in _PIPELINE_ROLE_STATEMENTS)
        assert "public" not in statements
        assert "insights" not in statements
        assert "musicbrainz" not in statements

    def test_the_role_is_nologin(self) -> None:
        assert f"CREATE ROLE {EMBEDDING_PIPELINE_ROLE} NOLOGIN" in _PIPELINE_ROLE_STATEMENTS[0][1]


class TestArtistSimilarityStatements:
    """Static shape checks over the two D-serving-mode tables, independent of any engine."""

    def test_neither_table_mentions_vector_or_halfvec(self) -> None:
        """Neither table may require the pgvector extension."""
        for _name, statement in _ARTIST_SIMILARITY_STATEMENTS:
            assert "vector" not in statement.lower()

    def test_artist_similar_artists_is_keyed_by_artist_model_version_rank(self) -> None:
        assert "PRIMARY KEY (artist_id, model_version, rank)" in _ARTIST_SIMILAR_ARTISTS_STATEMENT[1]

    def test_artist_similar_artists_carries_no_foreign_key(self) -> None:
        """Matches `artist_embeddings`: `graph.artist` is a view, not an FK target."""
        assert "REFERENCES" not in _ARTIST_SIMILAR_ARTISTS_STATEMENT[1]
        assert "FOREIGN KEY" not in _ARTIST_SIMILAR_ARTISTS_STATEMENT[1]

    def test_artist_similar_artists_rank_is_bounded_below(self) -> None:
        assert "CHECK (rank >= 1)" in _ARTIST_SIMILAR_ARTISTS_STATEMENT[1]

    def test_artist_similar_artists_statement_is_idempotent(self) -> None:
        assert "CREATE TABLE IF NOT EXISTS" in _ARTIST_SIMILAR_ARTISTS_STATEMENT[1]

    def test_the_lookup_index_leads_with_model_version(self) -> None:
        assert "ON public.artist_similar_artists (model_version, artist_id)" in _ARTIST_SIMILAR_ARTISTS_MODEL_VERSION_INDEX[1]

    def test_artist_embedding_releases_is_keyed_by_model_version(self) -> None:
        assert "model_version    TEXT PRIMARY KEY" in _ARTIST_EMBEDDING_RELEASES_STATEMENT[1]

    def test_artist_embedding_releases_statement_is_idempotent(self) -> None:
        assert "CREATE TABLE IF NOT EXISTS" in _ARTIST_EMBEDDING_RELEASES_STATEMENT[1]

    def test_at_most_one_current_release_is_a_partial_unique_index(self) -> None:
        assert "CREATE UNIQUE INDEX IF NOT EXISTS" in _ARTIST_EMBEDDING_RELEASES_CURRENT_INDEX[1]
        assert "WHERE is_current" in _ARTIST_EMBEDDING_RELEASES_CURRENT_INDEX[1]

    def test_the_pipeline_grant_holds_read_and_write_on_both_tables(self) -> None:
        assert _ARTIST_SIMILARITY_TABLES_GRANT[1] == (
            f"GRANT SELECT, INSERT, UPDATE, DELETE ON public.artist_similar_artists, public.artist_embedding_releases TO {EMBEDDING_PIPELINE_ROLE}"
        )

    def test_the_statements_are_reachable_from_schema_statements(self) -> None:
        from groovemap_schema.postgres import _schema_statements

        names = [name for name, _statement in _schema_statements()]
        assert "public.artist_similar_artists table" in names
        assert "public.artist_embedding_releases table" in names


class TestPublishArtistEmbeddingRelease:
    """`publish_artist_embedding_release` — the atomic is_current flip."""

    @staticmethod
    def _cursor() -> AsyncMock:
        cursor = AsyncMock()
        cursor.execute = AsyncMock()
        # `conn.transaction()` is a plain (sync) call returning an async context manager.
        cursor.connection = AsyncMock()
        cursor.connection.transaction = MagicMock(return_value=AsyncMock())
        return cursor

    @pytest.mark.asyncio
    async def test_refuses_a_blank_model_version(self) -> None:
        cursor = self._cursor()
        result = await publish_artist_embedding_release(cursor, "  ", source_dump_id="dump-1", source_dump_date=date(2026, 9, 1), k=10, artists=100)
        assert result == 1
        cursor.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_clears_the_old_current_before_upserting_the_new_one(self) -> None:
        cursor = self._cursor()
        result = await publish_artist_embedding_release(
            cursor, "fastrp-v3", source_dump_id="dump-1", source_dump_date=date(2026, 9, 1), k=10, artists=100
        )
        assert result == 0
        statements = [str(call.args[0]) for call in cursor.execute.await_args_list]
        assert len(statements) == 2
        assert statements[0] == _ARTIST_EMBEDDING_RELEASES_CLEAR_CURRENT_SQL
        assert "INSERT INTO public.artist_embedding_releases" in statements[1]
        assert "ON CONFLICT (model_version) DO UPDATE" in statements[1]

    @pytest.mark.asyncio
    async def test_a_failure_is_counted_rather_than_raised(self) -> None:
        cursor = self._cursor()
        cursor.execute = AsyncMock(side_effect=RuntimeError("Simulated PostgreSQL error"))
        result = await publish_artist_embedding_release(
            cursor, "fastrp-v3", source_dump_id="dump-1", source_dump_date=date(2026, 9, 1), k=10, artists=100
        )
        assert result == 1


class TestRetireArtistSimilarArtistsVersion:
    """`retire_artist_similar_artists_version` — refuses the current version, deletes the rest."""

    @staticmethod
    def _cursor(*, is_current: bool | None) -> AsyncMock:
        cursor = AsyncMock()
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(return_value=None if is_current is None else (is_current,))
        connection = AsyncMock()
        connection.transaction = MagicMock(return_value=AsyncMock())
        cursor.connection = connection
        return cursor

    @pytest.mark.asyncio
    async def test_refuses_a_blank_model_version(self) -> None:
        cursor = self._cursor(is_current=False)
        assert await retire_artist_similar_artists_version(cursor, "   ") == 1
        cursor.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_refuses_the_current_version(self) -> None:
        cursor = self._cursor(is_current=True)
        result = await retire_artist_similar_artists_version(cursor, "fastrp-v3")
        assert result == 1
        assert cursor.execute.await_args_list[0].args[0] == _ARTIST_EMBEDDING_RELEASES_IS_CURRENT_QUERY
        # No delete was attempted once the current-version check refused.
        assert cursor.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_deletes_rows_for_a_superseded_version(self) -> None:
        cursor = self._cursor(is_current=False)
        result = await retire_artist_similar_artists_version(cursor, "fastrp-v2")
        assert result == 0
        statements = [str(call.args[0]) for call in cursor.execute.await_args_list]
        assert _ARTIST_SIMILAR_ARTISTS_VERSION_DELETE_SQL in statements
        assert _ARTIST_EMBEDDING_RELEASES_VERSION_DELETE_SQL not in statements

    @pytest.mark.asyncio
    async def test_delete_release_also_deletes_the_release_row(self) -> None:
        cursor = self._cursor(is_current=False)
        result = await retire_artist_similar_artists_version(cursor, "fastrp-v2", delete_release=True)
        assert result == 0
        statements = [str(call.args[0]) for call in cursor.execute.await_args_list]
        assert _ARTIST_SIMILAR_ARTISTS_VERSION_DELETE_SQL in statements
        assert _ARTIST_EMBEDDING_RELEASES_VERSION_DELETE_SQL in statements

    @pytest.mark.asyncio
    async def test_a_missing_release_row_is_not_current_and_is_not_refused(self) -> None:
        """A version with no `artist_embedding_releases` row at all can still be retired."""
        cursor = self._cursor(is_current=None)
        result = await retire_artist_similar_artists_version(cursor, "fastrp-v1")
        assert result == 0

    @pytest.mark.asyncio
    async def test_an_unreadable_is_current_check_is_counted_rather_than_raised(self) -> None:
        cursor = self._cursor(is_current=False)
        cursor.execute = AsyncMock(side_effect=RuntimeError("Simulated PostgreSQL error"))
        result = await retire_artist_similar_artists_version(cursor, "fastrp-v2")
        assert result == 1

    @pytest.mark.asyncio
    async def test_a_failing_delete_is_counted_rather_than_raised(self) -> None:
        cursor = self._cursor(is_current=False)

        async def fail_on_the_delete(statement: Any, *_: Any, **__: Any) -> None:
            if str(statement) == _ARTIST_SIMILAR_ARTISTS_VERSION_DELETE_SQL:
                raise RuntimeError("Simulated PostgreSQL error")

        cursor.execute = AsyncMock(side_effect=fail_on_the_delete)
        result = await retire_artist_similar_artists_version(cursor, "fastrp-v2")
        assert result == 1


class TestApplyVectorSchema:
    """The gate as the initializer runs it, against the shared cursor fake.

    Probe order and count depend on the branch: `_vector_extension_installed`
    always runs first; `_vector_extension_available` only when not installed;
    `_is_superuser` only when not installed but available; `_can_create_role`
    always runs, independent of the other three.
    """

    @staticmethod
    def _cursor(rows: list[Any]) -> AsyncMock:
        cursor = AsyncMock()
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(side_effect=rows)
        return cursor

    @staticmethod
    def _statements(cursor: AsyncMock) -> list[str]:
        return [str(call.args[0]) for call in cursor.execute.await_args_list]

    @pytest.mark.asyncio
    async def test_already_installed_creates_the_table_regardless_of_superuser(self) -> None:
        # installed=True (superuser/available never probed), CREATEROLE present
        cursor = self._cursor([(True,), (True,)])

        assert await _apply_vector_schema(cursor) == 0
        statements = self._statements(cursor)
        # 2 probes (installed, role) + the vector schema's 3 statements + the role's 3
        # + the artist_embeddings grant + the artist-similarity tables grant (role-gated
        # only, so it fires here too)
        assert len(statements) == 2 + len(_VECTOR_SCHEMA_STATEMENTS) + len(_PIPELINE_ROLE_STATEMENTS) + 1 + 1
        assert statements[-2] == _EMBEDDINGS_TABLE_GRANT[1]
        assert statements[-1] == _ARTIST_SIMILARITY_TABLES_GRANT[1]

    @pytest.mark.asyncio
    async def test_unavailable_skips_the_extension_table_and_grant(self) -> None:
        # installed=False, available=False (superuser never probed), CREATEROLE present
        cursor = self._cursor([(False,), (False,), (True,)])

        assert await _apply_vector_schema(cursor) == 0
        statements = self._statements(cursor)
        # The artist-similarity tables grant is gated on CREATEROLE alone, not on the
        # (here closed) extension gate, so it still fires.
        assert len(statements) == 3 + len(_PIPELINE_ROLE_STATEMENTS) + 1
        assert not any("CREATE EXTENSION" in s.upper() for s in statements)
        assert not any("artist_embeddings" in s for s in statements)
        assert statements[-1] == _ARTIST_SIMILARITY_TABLES_GRANT[1]

    @pytest.mark.asyncio
    async def test_available_but_not_superuser_skips_the_extension_table_and_grant(self) -> None:
        # installed=False, available=True, superuser=False, CREATEROLE present
        cursor = self._cursor([(False,), (True,), (False,), (True,)])

        assert await _apply_vector_schema(cursor) == 0
        statements = self._statements(cursor)
        assert len(statements) == 4 + len(_PIPELINE_ROLE_STATEMENTS) + 1
        assert not any("CREATE EXTENSION" in s.upper() for s in statements)
        assert not any("artist_embeddings" in s for s in statements)
        assert statements[-1] == _ARTIST_SIMILARITY_TABLES_GRANT[1]

    @pytest.mark.asyncio
    async def test_available_and_superuser_installs_and_creates_everything(self) -> None:
        # installed=False, available=True, superuser=True, CREATEROLE present
        cursor = self._cursor([(False,), (True,), (True,), (True,)])

        assert await _apply_vector_schema(cursor) == 0
        statements = self._statements(cursor)
        assert len(statements) == 4 + len(_VECTOR_SCHEMA_STATEMENTS) + len(_PIPELINE_ROLE_STATEMENTS) + 1 + 1
        assert statements[-2] == _EMBEDDINGS_TABLE_GRANT[1]
        assert statements[-1] == _ARTIST_SIMILARITY_TABLES_GRANT[1]

    @pytest.mark.asyncio
    async def test_missing_createrole_skips_the_role_and_its_grants(self) -> None:
        # installed=True, CREATEROLE absent
        cursor = self._cursor([(True,), (False,)])

        assert await _apply_vector_schema(cursor) == 0
        statements = self._statements(cursor)
        assert len(statements) == 2 + len(_VECTOR_SCHEMA_STATEMENTS)
        assert not any(EMBEDDING_PIPELINE_ROLE in s for s in statements)

    @pytest.mark.asyncio
    async def test_both_gates_closed_only_probes(self) -> None:
        # installed=False, available=False, CREATEROLE absent
        cursor = self._cursor([(False,), (False,), (False,)])

        assert await _apply_vector_schema(cursor) == 0
        assert len(self._statements(cursor)) == 3

    @pytest.mark.asyncio
    async def test_a_failing_statement_is_counted_rather_than_raised(self) -> None:
        cursor = self._cursor([(True,), (True,)])

        async def fail_on_the_grant(statement: Any, *_: Any, **__: Any) -> None:
            if str(statement) == _EMBEDDINGS_TABLE_GRANT[1]:
                raise RuntimeError("Simulated PostgreSQL error")

        cursor.execute = AsyncMock(side_effect=fail_on_the_grant)
        assert await _apply_vector_schema(cursor) == 1

    @pytest.mark.asyncio
    async def test_an_unreadable_probe_skips_rather_than_raises(self) -> None:
        cursor = AsyncMock()
        cursor.execute = AsyncMock(side_effect=Exception("PostgreSQL unavailable"))

        assert await _apply_vector_schema(cursor) == 0

    @pytest.mark.asyncio
    async def test_an_unreadable_superuser_probe_skips_rather_than_raises(self) -> None:
        """Not installed, available, but the superuser probe itself fails."""
        cursor = self._cursor([(False,), (True,), (True,)])

        async def fail_on_the_superuser_probe(statement: Any, *_: Any, **__: Any) -> None:
            if str(statement) == _IS_SUPERUSER_QUERY:
                raise RuntimeError("Simulated PostgreSQL error")

        cursor.execute = AsyncMock(side_effect=fail_on_the_superuser_probe)
        assert await _apply_vector_schema(cursor) == 0
        assert not any("artist_embeddings" in s for s in self._statements(cursor))

    @pytest.mark.asyncio
    async def test_the_full_run_creates_nothing_when_both_gates_are_closed_by_default(self, mock_pool: MagicMock) -> None:
        """`mock_pool`'s fetchone default (False) closes both gates."""
        cursor = mock_pool.connection.return_value.__aenter__.return_value.cursor.return_value
        captured: list[str] = []

        async def capture(stmt: Any, *_: Any, **__: Any) -> None:
            captured.append(str(stmt))

        cursor.execute = AsyncMock(side_effect=capture)
        await create_postgres_schema(mock_pool)

        assert not any("artist_embeddings" in statement for statement in captured)
        assert not any(EMBEDDING_PIPELINE_ROLE in statement for statement in captured)


class TestArtistEmbeddingsHnswIndexStatement:
    """Static shape checks over the declared HNSW index, independent of any engine."""

    def test_the_index_name_matches_the_declared_constant(self) -> None:
        assert _ARTIST_EMBEDDINGS_HNSW_INDEX_STATEMENT[0] == ARTIST_EMBEDDINGS_HNSW_INDEX_NAME

    def test_the_statement_is_idempotent(self) -> None:
        assert f"CREATE INDEX IF NOT EXISTS {ARTIST_EMBEDDINGS_HNSW_INDEX_NAME}" in _ARTIST_EMBEDDINGS_HNSW_INDEX_STATEMENT[1]

    def test_it_targets_the_embedding_column_on_artist_embeddings(self) -> None:
        assert "ON public.artist_embeddings USING hnsw (embedding halfvec_cosine_ops)" in _ARTIST_EMBEDDINGS_HNSW_INDEX_STATEMENT[1]

    def test_it_uses_the_adr_0013_build_parameters(self) -> None:
        assert "WITH (m = 16, ef_construction = 64)" in _ARTIST_EMBEDDINGS_HNSW_INDEX_STATEMENT[1]

    def test_the_index_is_not_part_of_the_statements_the_initializer_runs_automatically(self) -> None:
        """The whole point of this bead: `create_postgres_schema` must never build this on its own."""
        assert _ARTIST_EMBEDDINGS_HNSW_INDEX_STATEMENT not in _VECTOR_SCHEMA_STATEMENTS
        names = {name for name, _statement in _VECTOR_SCHEMA_STATEMENTS}
        assert ARTIST_EMBEDDINGS_HNSW_INDEX_NAME not in names


class TestArtistEmbeddingsHnswIndexReindexStatement:
    """Static shape checks over the rebuild statement `rebuild=True` runs instead."""

    def test_it_reindexes_the_same_index_by_name(self) -> None:
        assert _ARTIST_EMBEDDINGS_HNSW_INDEX_REINDEX_STATEMENT[1] == f"REINDEX INDEX public.{ARTIST_EMBEDDINGS_HNSW_INDEX_NAME}"

    def test_it_is_not_concurrent(self) -> None:
        """Plain `REINDEX INDEX`, not `CONCURRENTLY` -- see the module comment for why."""
        assert "CONCURRENTLY" not in _ARTIST_EMBEDDINGS_HNSW_INDEX_REINDEX_STATEMENT[1]

    def test_it_is_not_part_of_the_statements_the_initializer_runs_automatically(self) -> None:
        assert _ARTIST_EMBEDDINGS_HNSW_INDEX_REINDEX_STATEMENT not in _VECTOR_SCHEMA_STATEMENTS


class TestValidMaintenanceWorkMem:
    """The validator standing between a caller-supplied string and the `SET` statement it is
    interpolated into -- `SET`'s value position takes no bind parameter.
    """

    @pytest.mark.parametrize("value", ["2GB", "64MB", "256MB", "1TB", "500kB", "1kB"])
    def test_accepts_a_bare_positive_integer_with_a_postgres_memory_unit(self, value: str) -> None:
        assert _valid_maintenance_work_mem(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "2 GB",  # a space
            "-1GB",  # negative
            "0GB",  # zero
            "2gb",  # lowercase unit
            "2GiB",  # not a PostgreSQL unit
            "2",  # no unit at all
            "GB",  # no quantity
            "2GB;",  # trailing punctuation
            "2GB' OR '1'='1",  # a quote-escape attempt
            "2GB'; DROP TABLE artist_embeddings; --",  # a statement-injection attempt
        ],
    )
    def test_rejects_anything_else(self, value: str) -> None:
        assert _valid_maintenance_work_mem(value) is False


class TestBuildArtistEmbeddingsIndex:
    """`build_artist_embeddings_index` is the operator procedure, never something
    `create_postgres_schema` calls on its own -- see `TestApplyVectorSchema` and
    `TestVectorSchemaFullRun` for the initializer's side of that split.
    """

    @staticmethod
    def _cursor(rows: list[Any]) -> AsyncMock:
        cursor = AsyncMock()
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(side_effect=rows)
        return cursor

    @staticmethod
    def _statements(cursor: AsyncMock) -> list[str]:
        return [str(call.args[0]) for call in cursor.execute.await_args_list]

    @pytest.mark.asyncio
    async def test_not_installed_skips_without_touching_maintenance_work_mem(self) -> None:
        cursor = self._cursor([(False,)])

        assert await build_artist_embeddings_index(cursor) == 0
        statements = self._statements(cursor)
        assert statements == [_VECTOR_EXTENSION_INSTALLED_QUERY]

    @pytest.mark.asyncio
    async def test_installed_raises_builds_and_reverts_in_order(self) -> None:
        cursor = self._cursor([(True,)])

        assert await build_artist_embeddings_index(cursor) == 0
        statements = self._statements(cursor)
        assert statements == [
            _VECTOR_EXTENSION_INSTALLED_QUERY,
            _set_maintenance_work_mem_statement(_HNSW_BUILD_MAINTENANCE_WORK_MEM),
            _ARTIST_EMBEDDINGS_HNSW_INDEX_STATEMENT[1],
            _RESET_MAINTENANCE_WORK_MEM,
        ]

    @pytest.mark.asyncio
    async def test_a_failing_build_still_reverts_maintenance_work_mem(self) -> None:
        cursor = self._cursor([(True,)])

        async def fail_on_the_index(statement: Any, *_: Any, **__: Any) -> None:
            if str(statement) == _ARTIST_EMBEDDINGS_HNSW_INDEX_STATEMENT[1]:
                raise RuntimeError("Simulated PostgreSQL error")

        cursor.execute = AsyncMock(side_effect=fail_on_the_index)
        assert await build_artist_embeddings_index(cursor) == 1
        statements = self._statements(cursor)
        assert statements[-1] == _RESET_MAINTENANCE_WORK_MEM

    @pytest.mark.asyncio
    async def test_a_failing_reset_does_not_undo_a_successful_build(self) -> None:
        cursor = self._cursor([(True,)])

        async def fail_on_reset(statement: Any, *_: Any, **__: Any) -> None:
            if str(statement) == _RESET_MAINTENANCE_WORK_MEM:
                raise RuntimeError("Simulated PostgreSQL error")

        cursor.execute = AsyncMock(side_effect=fail_on_reset)
        assert await build_artist_embeddings_index(cursor) == 0

    @pytest.mark.asyncio
    async def test_a_failing_memory_bump_skips_the_build_entirely(self) -> None:
        cursor = self._cursor([(True,)])

        async def fail_on_set(statement: Any, *_: Any, **__: Any) -> None:
            if str(statement) == _set_maintenance_work_mem_statement(_HNSW_BUILD_MAINTENANCE_WORK_MEM):
                raise RuntimeError("Simulated PostgreSQL error")

        cursor.execute = AsyncMock(side_effect=fail_on_set)
        assert await build_artist_embeddings_index(cursor) == 1
        statements = self._statements(cursor)
        assert _ARTIST_EMBEDDINGS_HNSW_INDEX_STATEMENT[1] not in statements

    @pytest.mark.asyncio
    async def test_an_unreadable_probe_skips_rather_than_raises(self) -> None:
        cursor = AsyncMock()
        cursor.execute = AsyncMock(side_effect=Exception("PostgreSQL unavailable"))

        assert await build_artist_embeddings_index(cursor) == 0

    @pytest.mark.asyncio
    async def test_maintenance_work_mem_is_overridable_for_a_resource_bound_caller(self) -> None:
        """A test host can exercise the exact same SET/CREATE INDEX/RESET sequence
        against a small synthetic fixture without requesting the ~2 GB ADR 0013
        documents for production.
        """
        cursor = self._cursor([(True,)])

        assert await build_artist_embeddings_index(cursor, maintenance_work_mem="64MB") == 0
        statements = self._statements(cursor)
        assert statements[1] == _set_maintenance_work_mem_statement("64MB")

    @pytest.mark.asyncio
    async def test_an_invalid_maintenance_work_mem_is_rejected_before_touching_the_database(self) -> None:
        """`_valid_maintenance_work_mem` runs before the memory bump, so a rejected value never
        reaches a `SET` statement, let alone the index build.
        """
        cursor = self._cursor([(True,)])

        assert await build_artist_embeddings_index(cursor, maintenance_work_mem="2GB'; DROP TABLE artist_embeddings; --") == 1
        statements = self._statements(cursor)
        # Only the extension-installed probe ran; no SET, no index DDL, no RESET.
        assert statements == [_VECTOR_EXTENSION_INSTALLED_QUERY]

    @pytest.mark.asyncio
    async def test_rebuild_true_reindexes_instead_of_create_index_if_not_exists(self) -> None:
        cursor = self._cursor([(True,)])

        assert await build_artist_embeddings_index(cursor, rebuild=True) == 0
        statements = self._statements(cursor)
        assert statements == [
            _VECTOR_EXTENSION_INSTALLED_QUERY,
            _set_maintenance_work_mem_statement(_HNSW_BUILD_MAINTENANCE_WORK_MEM),
            _ARTIST_EMBEDDINGS_HNSW_INDEX_REINDEX_STATEMENT[1],
            _RESET_MAINTENANCE_WORK_MEM,
        ]
        assert _ARTIST_EMBEDDINGS_HNSW_INDEX_STATEMENT[1] not in statements

    @pytest.mark.asyncio
    async def test_a_failing_reindex_still_reverts_maintenance_work_mem(self) -> None:
        cursor = self._cursor([(True,)])

        async def fail_on_the_reindex(statement: Any, *_: Any, **__: Any) -> None:
            if str(statement) == _ARTIST_EMBEDDINGS_HNSW_INDEX_REINDEX_STATEMENT[1]:
                raise RuntimeError("Simulated PostgreSQL error")

        cursor.execute = AsyncMock(side_effect=fail_on_the_reindex)
        assert await build_artist_embeddings_index(cursor, rebuild=True) == 1
        statements = self._statements(cursor)
        assert statements[-1] == _RESET_MAINTENANCE_WORK_MEM


class TestValidIndexName:
    """The validator standing between a caller-supplied index name and the DDL it is
    interpolated into -- an index name (unlike an ordinary value) has no bind-parameter
    spelling at all.
    """

    @pytest.mark.parametrize(
        "value",
        [
            "idx_artist_embeddings_embedding_hnsw",
            # analytics-engine's insights.embedding_pipeline._index_name output for two
            # real stored model_versions -- see TestCrossRepoIndexNameAgreement below for
            # the full statement these come from.
            "idx_artist_embeddings_2026_09_01_25f0ccd57c2e_hnsw",
            "idx_artist_embeddings_discogs_monthly_2026_10_7f38cf02fe7b_hnsw",
            "_leading_underscore",
            "a",
            "a" * 63,
        ],
    )
    def test_accepts_a_safe_lowercase_identifier(self, value: str) -> None:
        assert _valid_index_name(value) is True

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "a" * 64,  # over NAMEDATALEN - 1
            "Idx_Mixed_Case",
            "1_leading_digit",
            "idx with spaces",
            "idx-with-dashes",
            'idx"; DROP TABLE artist_embeddings; --',
            "idx_artist_embeddings_embedding_hnsw;",
        ],
    )
    def test_rejects_anything_else(self, value: str) -> None:
        assert _valid_index_name(value) is False


class TestValidModelVersion:
    """`model_version` is always escaped or bound, never interpolated bare, so this only
    has to catch an empty or blank caller mistake.
    """

    @pytest.mark.parametrize("value", ["fastrp-d128-seed7:edges-v2@2026-09-01", "x", "  padded but not blank  "])
    def test_accepts_non_blank_text(self, value: str) -> None:
        assert _valid_model_version(value) is True

    @pytest.mark.parametrize("value", ["", " ", "\t\n"])
    def test_rejects_blank_text(self, value: str) -> None:
        assert _valid_model_version(value) is False


class TestSqlStringLiteral:
    def test_wraps_in_single_quotes(self) -> None:
        assert _sql_string_literal("fastrp-d128-seed7:edges-v2@2026-09-01") == "'fastrp-d128-seed7:edges-v2@2026-09-01'"

    def test_escapes_an_embedded_quote_by_doubling_it(self) -> None:
        assert _sql_string_literal("o'brien") == "'o''brien'"

    def test_a_statement_injection_attempt_stays_a_single_literal(self) -> None:
        value = "x'; DROP TABLE artist_embeddings; --"
        literal = _sql_string_literal(value)
        assert literal == "'x''; DROP TABLE artist_embeddings; --'"
        assert literal.count("'") % 2 == 0


class TestCrossRepoIndexNameAgreement:
    """`analytics-engine`'s `insights.embedding_pipeline._log_operator_step` logs the exact
    `CREATE INDEX CONCURRENTLY` statement an operator runs; these two (model_version,
    index_name) pairs are real output of its `_index_name`, computed independently of this
    module. `_artist_embeddings_version_index_create_statement` must reproduce the same
    statement text byte-for-byte given the same inputs -- proof the two repositories agree
    in practice, not merely by design, without this module reimplementing the slug-and-digest
    derivation itself (see the module comment above `_valid_index_name` in postgres.py).
    """

    @pytest.mark.parametrize(
        ("model_version", "index_name", "expected_where"),
        [
            (
                "fastrp-d128-seed7:edges-v2@2026-09-01",
                "idx_artist_embeddings_2026_09_01_25f0ccd57c2e_hnsw",
                "WHERE model_version = 'fastrp-d128-seed7:edges-v2@2026-09-01'",
            ),
            (
                "fastrp-d128-seed7:edges-v2@discogs-monthly-2026-10-dump",
                "idx_artist_embeddings_discogs_monthly_2026_10_7f38cf02fe7b_hnsw",
                "WHERE model_version = 'fastrp-d128-seed7:edges-v2@discogs-monthly-2026-10-dump'",
            ),
        ],
    )
    def test_matches_analytics_engines_logged_statement(self, model_version: str, index_name: str, expected_where: str) -> None:
        statement = _artist_embeddings_version_index_create_statement(index_name, model_version)
        assert statement == (
            f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {index_name} "
            f"ON public.artist_embeddings USING hnsw (embedding halfvec_cosine_ops) "
            f"WITH (m = 16, ef_construction = 64) "
            f"{expected_where}"
        )
        assert _valid_index_name(index_name) is True


class TestArtistEmbeddingsVersionIndexDropStatement:
    def test_drops_concurrently_if_exists_by_name(self) -> None:
        assert (
            _artist_embeddings_version_index_drop_statement("idx_artist_embeddings_example_hnsw")
            == "DROP INDEX CONCURRENTLY IF EXISTS public.idx_artist_embeddings_example_hnsw"
        )


class TestConnectionIsAutocommit:
    def test_true_when_the_connections_autocommit_flag_is_true(self) -> None:
        cursor = MagicMock()
        cursor.connection = MagicMock(autocommit=True)
        assert _connection_is_autocommit(cursor) is True

    def test_false_when_the_connections_autocommit_flag_is_false(self) -> None:
        cursor = MagicMock()
        cursor.connection = MagicMock(autocommit=False)
        assert _connection_is_autocommit(cursor) is False

    def test_false_when_the_cursor_has_no_connection_attribute(self) -> None:
        cursor = object()
        assert _connection_is_autocommit(cursor) is False


class TestBuildArtistEmbeddingsVersionIndex:
    """`build_artist_embeddings_version_index` is the per-`model_version` follow-on to
    `build_artist_embeddings_index` above (gm-database-schema-19g5) -- CONCURRENTLY, a
    caller-supplied index name, and INVALID-index cleanup on failure.
    """

    _MODEL_VERSION = "fastrp-d128-seed7:edges-v2@2026-09-01"
    _INDEX_NAME = "idx_artist_embeddings_2026_09_01_25f0ccd57c2e_hnsw"

    @classmethod
    def _cursor(cls, rows: list[Any], *, autocommit: bool = True) -> AsyncMock:
        cursor = AsyncMock()
        cursor.execute = AsyncMock()
        cursor.fetchone = AsyncMock(side_effect=rows)
        cursor.connection = MagicMock(autocommit=autocommit)
        return cursor

    @staticmethod
    def _statements(cursor: AsyncMock) -> list[str]:
        return [str(call.args[0]) for call in cursor.execute.await_args_list]

    def _create_statement(self) -> str:
        return _artist_embeddings_version_index_create_statement(self._INDEX_NAME, self._MODEL_VERSION)

    def _drop_statement(self) -> str:
        return _artist_embeddings_version_index_drop_statement(self._INDEX_NAME)

    @pytest.mark.asyncio
    async def test_not_installed_skips_without_touching_maintenance_work_mem(self) -> None:
        cursor = self._cursor([(False,)])

        assert await build_artist_embeddings_version_index(cursor, self._MODEL_VERSION, self._INDEX_NAME) == 0
        assert self._statements(cursor) == [_VECTOR_EXTENSION_INSTALLED_QUERY]

    @pytest.mark.asyncio
    async def test_installed_raises_builds_and_reverts_in_order(self) -> None:
        cursor = self._cursor([(True,)])

        assert await build_artist_embeddings_version_index(cursor, self._MODEL_VERSION, self._INDEX_NAME) == 0
        assert self._statements(cursor) == [
            _VECTOR_EXTENSION_INSTALLED_QUERY,
            _set_maintenance_work_mem_statement(_HNSW_BUILD_MAINTENANCE_WORK_MEM),
            self._create_statement(),
            _RESET_MAINTENANCE_WORK_MEM,
        ]

    @pytest.mark.asyncio
    async def test_an_invalid_index_name_is_rejected_before_touching_maintenance_work_mem(self) -> None:
        cursor = self._cursor([(True,)])

        assert await build_artist_embeddings_version_index(cursor, self._MODEL_VERSION, "not an identifier") == 1
        assert self._statements(cursor) == [_VECTOR_EXTENSION_INSTALLED_QUERY]

    @pytest.mark.asyncio
    async def test_a_blank_model_version_is_rejected_before_touching_maintenance_work_mem(self) -> None:
        cursor = self._cursor([(True,)])

        assert await build_artist_embeddings_version_index(cursor, "  ", self._INDEX_NAME) == 1
        assert self._statements(cursor) == [_VECTOR_EXTENSION_INSTALLED_QUERY]

    @pytest.mark.asyncio
    async def test_an_invalid_maintenance_work_mem_is_rejected_before_touching_the_database(self) -> None:
        cursor = self._cursor([(True,)])

        assert (
            await build_artist_embeddings_version_index(
                cursor, self._MODEL_VERSION, self._INDEX_NAME, maintenance_work_mem="2GB'; DROP TABLE artist_embeddings; --"
            )
            == 1
        )
        assert self._statements(cursor) == [_VECTOR_EXTENSION_INSTALLED_QUERY]

    @pytest.mark.asyncio
    async def test_a_non_autocommit_connection_is_rejected_before_touching_maintenance_work_mem(self) -> None:
        cursor = self._cursor([(True,)], autocommit=False)

        assert await build_artist_embeddings_version_index(cursor, self._MODEL_VERSION, self._INDEX_NAME) == 1
        assert self._statements(cursor) == [_VECTOR_EXTENSION_INSTALLED_QUERY]

    @pytest.mark.asyncio
    async def test_a_failing_memory_bump_skips_the_build_entirely(self) -> None:
        cursor = self._cursor([(True,)])

        async def fail_on_set(statement: Any, *_: Any, **__: Any) -> None:
            if str(statement) == _set_maintenance_work_mem_statement(_HNSW_BUILD_MAINTENANCE_WORK_MEM):
                raise RuntimeError("Simulated PostgreSQL error")

        cursor.execute = AsyncMock(side_effect=fail_on_set)
        assert await build_artist_embeddings_version_index(cursor, self._MODEL_VERSION, self._INDEX_NAME) == 1
        statements = self._statements(cursor)
        assert self._create_statement() not in statements
        assert _RESET_MAINTENANCE_WORK_MEM not in statements

    @pytest.mark.asyncio
    async def test_a_failing_build_drops_any_invalid_index_and_still_reverts_maintenance_work_mem(self) -> None:
        cursor = self._cursor([(True,)])
        create_statement = self._create_statement()

        async def fail_on_the_index(statement: Any, *_: Any, **__: Any) -> None:
            if str(statement) == create_statement:
                raise RuntimeError("Simulated PostgreSQL error")

        cursor.execute = AsyncMock(side_effect=fail_on_the_index)
        assert await build_artist_embeddings_version_index(cursor, self._MODEL_VERSION, self._INDEX_NAME) == 1
        assert self._statements(cursor) == [
            _VECTOR_EXTENSION_INSTALLED_QUERY,
            _set_maintenance_work_mem_statement(_HNSW_BUILD_MAINTENANCE_WORK_MEM),
            create_statement,
            self._drop_statement(),
            _RESET_MAINTENANCE_WORK_MEM,
        ]

    @pytest.mark.asyncio
    async def test_a_failing_cleanup_drop_still_reverts_maintenance_work_mem_and_reports_failure(self) -> None:
        cursor = self._cursor([(True,)])
        create_statement = self._create_statement()
        drop_statement = self._drop_statement()

        async def fail_on_index_and_cleanup(statement: Any, *_: Any, **__: Any) -> None:
            if str(statement) in (create_statement, drop_statement):
                raise RuntimeError("Simulated PostgreSQL error")

        cursor.execute = AsyncMock(side_effect=fail_on_index_and_cleanup)
        assert await build_artist_embeddings_version_index(cursor, self._MODEL_VERSION, self._INDEX_NAME) == 1
        assert self._statements(cursor)[-1] == _RESET_MAINTENANCE_WORK_MEM

    @pytest.mark.asyncio
    async def test_a_failing_reset_does_not_undo_a_successful_build(self) -> None:
        cursor = self._cursor([(True,)])

        async def fail_on_reset(statement: Any, *_: Any, **__: Any) -> None:
            if str(statement) == _RESET_MAINTENANCE_WORK_MEM:
                raise RuntimeError("Simulated PostgreSQL error")

        cursor.execute = AsyncMock(side_effect=fail_on_reset)
        assert await build_artist_embeddings_version_index(cursor, self._MODEL_VERSION, self._INDEX_NAME) == 0

    @pytest.mark.asyncio
    async def test_maintenance_work_mem_is_overridable_for_a_resource_bound_caller(self) -> None:
        cursor = self._cursor([(True,)])

        assert await build_artist_embeddings_version_index(cursor, self._MODEL_VERSION, self._INDEX_NAME, maintenance_work_mem="64MB") == 0
        assert self._statements(cursor)[1] == _set_maintenance_work_mem_statement("64MB")


class TestRetireArtistEmbeddingsVersion:
    """`retire_artist_embeddings_version` is the cleanup half of the per-`model_version`
    flow: drop the superseded index, then delete its rows -- see the module comment above
    `build_artist_embeddings_version_index` in postgres.py for why the index is dropped
    first.
    """

    _MODEL_VERSION = "fastrp-d128-seed7:edges-v2@2026-08-01"
    _INDEX_NAME = "idx_artist_embeddings_2026_08_01_deadbeefcafe_hnsw"

    @classmethod
    def _cursor(cls, *, autocommit: bool = True) -> AsyncMock:
        cursor = AsyncMock()
        cursor.execute = AsyncMock()
        cursor.connection = MagicMock(autocommit=autocommit)
        return cursor

    @staticmethod
    def _statements(cursor: AsyncMock) -> list[tuple[Any, ...]]:
        return [tuple(call.args) for call in cursor.execute.await_args_list]

    @pytest.mark.asyncio
    async def test_drops_the_index_then_deletes_the_rows_in_order(self) -> None:
        cursor = self._cursor()

        assert await retire_artist_embeddings_version(cursor, self._MODEL_VERSION, self._INDEX_NAME) == 0
        assert self._statements(cursor) == [
            (_artist_embeddings_version_index_drop_statement(self._INDEX_NAME),),
            (_ARTIST_EMBEDDINGS_VERSION_DELETE_SQL, (self._MODEL_VERSION,)),
        ]

    @pytest.mark.asyncio
    async def test_an_invalid_index_name_is_rejected_before_touching_the_database(self) -> None:
        cursor = self._cursor()

        assert await retire_artist_embeddings_version(cursor, self._MODEL_VERSION, "not an identifier") == 1
        assert self._statements(cursor) == []

    @pytest.mark.asyncio
    async def test_a_blank_model_version_is_rejected_before_touching_the_database(self) -> None:
        cursor = self._cursor()

        assert await retire_artist_embeddings_version(cursor, "   ", self._INDEX_NAME) == 1
        assert self._statements(cursor) == []

    @pytest.mark.asyncio
    async def test_a_non_autocommit_connection_is_rejected_before_touching_the_database(self) -> None:
        cursor = self._cursor(autocommit=False)

        assert await retire_artist_embeddings_version(cursor, self._MODEL_VERSION, self._INDEX_NAME) == 1
        assert self._statements(cursor) == []

    @pytest.mark.asyncio
    async def test_a_failing_drop_never_attempts_the_delete(self) -> None:
        cursor = self._cursor()
        cursor.execute = AsyncMock(side_effect=RuntimeError("Simulated PostgreSQL error"))

        assert await retire_artist_embeddings_version(cursor, self._MODEL_VERSION, self._INDEX_NAME) == 1
        assert len(cursor.execute.await_args_list) == 1

    @pytest.mark.asyncio
    async def test_a_failing_delete_is_reported_after_a_successful_drop(self) -> None:
        cursor = self._cursor()
        drop_statement = _artist_embeddings_version_index_drop_statement(self._INDEX_NAME)

        async def fail_on_delete(statement: Any, *_: Any, **__: Any) -> None:
            if str(statement) != drop_statement:
                raise RuntimeError("Simulated PostgreSQL error")

        cursor.execute = AsyncMock(side_effect=fail_on_delete)
        assert await retire_artist_embeddings_version(cursor, self._MODEL_VERSION, self._INDEX_NAME) == 1


class TestVectorSchemaFullRun:
    """`create_postgres_schema` never builds the HNSW index, even when every other
    vector-schema gate is wide open.
    """

    @pytest.mark.asyncio
    async def test_the_full_run_never_builds_the_hnsw_index(self, mock_pool: MagicMock) -> None:
        cursor = mock_pool.connection.return_value.__aenter__.return_value.cursor.return_value
        # Open every gate: vector installed, CREATEROLE present.
        cursor.fetchone = AsyncMock(return_value=(True,))
        captured: list[str] = []

        async def capture(stmt: Any, *_: Any, **__: Any) -> None:
            captured.append(str(stmt))

        cursor.execute = AsyncMock(side_effect=capture)
        await create_postgres_schema(mock_pool)

        assert not any(ARTIST_EMBEDDINGS_HNSW_INDEX_NAME in statement for statement in captured)
        assert not any("maintenance_work_mem" in statement for statement in captured)

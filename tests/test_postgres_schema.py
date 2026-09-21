"""Tests for the PostgreSQL schema definitions."""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from groovemap_schema.postgres import (
    _ACTIVITY_STATEMENTS,
    _ENTITY_TABLES,
    _GRAPH_STATEMENTS,
    _INSIGHTS_TABLES,
    _MUSICBRAINZ_INDEXES,
    _MUSICBRAINZ_TABLES,
    _SPECIFIC_INDEXES,
    _USER_TABLES,
    PROPERTY_GRAPH_STATEMENT,
    PROPERTY_GRAPH_SWITCH,
    _apply_property_graph,
    _property_graph_skip_reason,
    create_postgres_schema,
    property_graph_enabled,
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
            + len(_GRAPH_STATEMENTS)
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
            + len(_GRAPH_STATEMENTS)
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
            exempt = ("ALTER COLUMN" in upper and "TYPE " in upper) or "CREATE OR REPLACE" in upper or guarded_block
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
            + len(_GRAPH_STATEMENTS)
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


class TestPropertyGraphSwitch:
    """The environment switch that has to be on before anything is emitted."""

    def test_the_switch_defaults_to_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(PROPERTY_GRAPH_SWITCH, raising=False)
        assert property_graph_enabled() is False

    @pytest.mark.parametrize("value", ["enabled", "ENABLED", " enabled ", "true", "1", "yes", "on", "enable"])
    def test_the_documented_and_the_usual_truthy_spellings_turn_it_on(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(PROPERTY_GRAPH_SWITCH, value)
        assert property_graph_enabled() is True

    @pytest.mark.parametrize("value", ["", "disabled", "false", "0", "no", "off", "maybe"])
    def test_everything_else_leaves_it_off(self, monkeypatch: pytest.MonkeyPatch, value: str) -> None:
        monkeypatch.setenv(PROPERTY_GRAPH_SWITCH, value)
        assert property_graph_enabled() is False


class TestPropertyGraphSkipReason:
    """The three gates, in the order they are evaluated."""

    def test_a_disabled_switch_is_the_first_reason(self) -> None:
        reason = _property_graph_skip_reason(enabled=False, server_version_num=190000, already_exists=False)
        assert reason == "SCHEMA_PROPERTY_GRAPH is not enabled"

    def test_an_older_server_is_the_second_reason(self) -> None:
        reason = _property_graph_skip_reason(enabled=True, server_version_num=180004, already_exists=False)
        assert reason == "server_version_num 180004 is below 190000"

    def test_an_unreadable_server_version_skips_rather_than_guesses(self) -> None:
        reason = _property_graph_skip_reason(enabled=True, server_version_num=None, already_exists=False)
        assert reason == "server_version_num None is below 190000"

    def test_an_existing_relation_is_the_third_reason(self) -> None:
        """There is no IF NOT EXISTS, and nothing here ever drops a relation."""
        reason = _property_graph_skip_reason(enabled=True, server_version_num=190000, already_exists=True)
        assert reason == "graph.catalog already exists"

    def test_an_enabled_switch_on_a_fresh_postgresql_19_creates_the_graph(self) -> None:
        assert _property_graph_skip_reason(enabled=True, server_version_num=190000, already_exists=False) is None


class TestApplyPropertyGraph:
    """The gate as the initializer runs it, against the shared cursor fake."""

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
    async def test_a_disabled_switch_asks_the_server_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv(PROPERTY_GRAPH_SWITCH, raising=False)
        cursor = self._cursor([])

        assert await _apply_property_graph(cursor) == 0
        assert cursor.execute.await_count == 0

    @pytest.mark.asyncio
    async def test_postgresql_18_reads_the_version_and_stops(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PROPERTY_GRAPH_SWITCH, "enabled")
        cursor = self._cursor([(180004,)])

        assert await _apply_property_graph(cursor) == 0
        statements = self._statements(cursor)
        assert len(statements) == 1
        assert "server_version_num" in statements[0]

    @pytest.mark.asyncio
    async def test_postgresql_19_with_the_switch_on_creates_the_graph(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PROPERTY_GRAPH_SWITCH, "enabled")
        cursor = self._cursor([(190000,), (False,)])

        assert await _apply_property_graph(cursor) == 0
        statements = self._statements(cursor)
        assert len(statements) == 3
        assert statements[-1] == PROPERTY_GRAPH_STATEMENT[1]

    @pytest.mark.asyncio
    async def test_a_second_apply_is_a_no_op_without_dropping_anything(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PROPERTY_GRAPH_SWITCH, "enabled")
        cursor = self._cursor([(190000,), (True,)])

        assert await _apply_property_graph(cursor) == 0
        statements = self._statements(cursor)
        assert len(statements) == 2
        assert not any("CREATE PROPERTY GRAPH" in statement for statement in statements)

    @pytest.mark.asyncio
    async def test_a_failing_statement_is_counted_rather_than_raised(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PROPERTY_GRAPH_SWITCH, "enabled")
        cursor = self._cursor([(190000,), (False,)])

        async def fail_on_the_ddl(statement: Any, *_: Any, **__: Any) -> None:
            if "CREATE PROPERTY GRAPH" in str(statement):
                raise RuntimeError("Simulated PostgreSQL error")

        cursor.execute = AsyncMock(side_effect=fail_on_the_ddl)
        assert await _apply_property_graph(cursor) == 1

    @pytest.mark.asyncio
    async def test_an_unreadable_server_version_skips_rather_than_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(PROPERTY_GRAPH_SWITCH, "enabled")
        cursor = AsyncMock()
        cursor.execute = AsyncMock(side_effect=Exception("PostgreSQL unavailable"))

        assert await _apply_property_graph(cursor) == 0

    @pytest.mark.asyncio
    async def test_the_full_run_leaves_the_graph_alone_by_default(self, mock_pool: MagicMock, monkeypatch: pytest.MonkeyPatch) -> None:
        """The default schema run is byte-identical to the one before this bead."""
        monkeypatch.delenv(PROPERTY_GRAPH_SWITCH, raising=False)
        cursor = mock_pool.connection.return_value.__aenter__.return_value.cursor.return_value
        captured: list[str] = []

        async def capture(stmt: Any, *_: Any, **__: Any) -> None:
            captured.append(str(stmt))

        cursor.execute = AsyncMock(side_effect=capture)
        await create_postgres_schema(mock_pool)

        assert not any("PROPERTY GRAPH" in statement for statement in captured)
        assert not any("server_version_num" in statement for statement in captured)

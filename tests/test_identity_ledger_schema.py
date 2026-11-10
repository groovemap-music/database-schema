"""Tests for the precomputed Identity Ledger (epics gm-database-schema-k8e9 and xen5).

The four tables, their unique indexes, the capture triggers, the refresh state
row, and `identity_ledger_refresh()`.  Every assertion is made against the
statement lists, never against a database; the integration suite pins the tables'
rows to catalog-api's reference definitions under a random history.
"""

import re

from groovemap_schema.postgres import (
    _GRAPH_STATEMENTS,
    _IDENTITY_LEDGER_STATEMENTS,
    _LEDGER_SOURCES,
    IDENTITY_LEDGER_REFRESH_GRANTS,
    IDENTITY_LEDGER_ROLLBACK_STATEMENTS,
    IDENTITY_LEDGER_STORES,
    IDENTITY_LEDGER_VIEWS,
    _schema_statements,
)


_STATEMENTS = dict(_IDENTITY_LEDGER_STATEMENTS)

# Each table's unique index: catalog-api's key, and what density's upsert infers.
_UNIQUE_KEYS = {
    "identity_ledger_edition_events": ("idx_identity_ledger_edition_events_key", "(edition_id, at, type)"),
    "identity_ledger_edition_parents": ("idx_identity_ledger_edition_parents_key", "(edition_id, work_id)"),
    "identity_ledger_work_summary": ("idx_identity_ledger_work_summary_key", "(event, work_id)"),
    "identity_ledger_work_summary_day": ("idx_identity_ledger_work_summary_day_key", "(event, day, work_id, prev_day)"),
    "identity_ledger_work_events": ("idx_identity_ledger_work_events_key", "(event, work_id, at)"),
    "identity_ledger_density": ("idx_identity_ledger_density_key", "(kind, month, type, dated_from)"),
}


class TestIdentityLedgerStatements:
    def test_the_refresh_state_exists_before_the_retirement_that_empties_it(self) -> None:
        names = [name for name, _statement in _IDENTITY_LEDGER_STATEMENTS]
        assert names.index("identity_ledger_refresh_state table") < names.index("identity ledger materialized views retirement")

    def test_the_retirement_runs_before_a_table_takes_a_views_name(self) -> None:
        names = [name for name, _statement in _IDENTITY_LEDGER_STATEMENTS]
        retirement = names.index("identity ledger materialized views retirement")
        assert all(retirement < names.index(f"{view} table") for view in IDENTITY_LEDGER_VIEWS)

    def test_the_rebuild_request_runs_after_the_retirement_and_before_any_table_is_created(self) -> None:
        names = [name for name, _statement in _IDENTITY_LEDGER_STATEMENTS]
        request = names.index("identity ledger rebuild request for new derived tables")
        assert names.index("identity ledger materialized views retirement") < request
        assert all(request < names.index(f"{view} table") for view in IDENTITY_LEDGER_VIEWS)

    def test_the_rebuild_request_is_guarded_by_the_state_row_and_names_every_derived_table(self) -> None:
        statement = _STATEMENTS["identity ledger rebuild request for new derived tables"]
        assert "identity_ledger_refresh_state" in statement
        assert "WHERE rebuild" in statement
        assert "INSERT INTO public.identity_ledger_changes (rebuild) VALUES (true)" in statement
        for view in IDENTITY_LEDGER_VIEWS:
            assert f"to_regclass('public.{view}') IS NULL" in statement

    def test_they_run_after_the_graph_statements_they_read(self) -> None:
        names = [name for name, _statement in _schema_statements()]
        first = names.index("identity_ledger_refresh_state table")
        assert first > max(names.index(name) for name, _statement in _GRAPH_STATEMENTS)
        assert first > names.index("musicbrainz.releases table")
        assert first > names.index("identity_link_events table")

    def test_every_source_is_captured_before_the_refresh_exists(self) -> None:
        names = [name for name, _statement in _IDENTITY_LEDGER_STATEMENTS]
        for table, _function in _LEDGER_SOURCES:
            assert names.index(f"{table} identity ledger capture triggers") < names.index("identity_ledger_refresh function"), table

    def test_every_capture_is_statement_level_and_cannot_be_half_applied(self) -> None:
        for table, _function in _LEDGER_SOURCES:
            statement = _STATEMENTS[f"{table} identity ledger capture triggers"]
            assert statement.startswith("DO $capture$")
            assert "SET LOCAL lock_timeout" in statement
            assert "FOR EACH ROW" not in statement
            assert statement.count("FOR EACH STATEMENT") == 4
            assert "AFTER TRUNCATE" in statement

    def test_every_capture_function_runs_as_the_owner_on_a_fixed_search_path(self) -> None:
        captures = [statement for name, statement in _IDENTITY_LEDGER_STATEMENTS if name.startswith("identity_ledger_capture_")]
        assert len(captures) == len(_LEDGER_SOURCES) + 1
        for statement in captures:
            assert "SECURITY DEFINER" in statement
            assert "SET search_path = pg_catalog, pg_temp" in statement

    def test_the_change_log_has_no_key_for_writers_to_deadlock_on(self) -> None:
        for store in ("identity_ledger_changes", "identity_ledger_density_changes"):
            assert "PRIMARY KEY" not in _STATEMENTS[f"{store} table"]
            assert not [name for name in _STATEMENTS if name.startswith(f"idx_{store}")]

    def test_every_view_is_a_table_created_empty(self) -> None:
        for view in IDENTITY_LEDGER_VIEWS:
            assert f"CREATE TABLE IF NOT EXISTS {view} (" in _STATEMENTS[f"{view} table"]
        assert not [statement for statement in _STATEMENTS.values() if "CREATE MATERIALIZED VIEW" in statement]

    def test_every_view_has_its_unique_key(self) -> None:
        for view, (index, columns) in _UNIQUE_KEYS.items():
            assert _STATEMENTS[index] == f"CREATE UNIQUE INDEX IF NOT EXISTS {index} ON public.{view} {columns}"
        unique = [statement for statement in _STATEMENTS.values() if statement.startswith("CREATE UNIQUE INDEX")]
        assert len(unique) == len(IDENTITY_LEDGER_VIEWS)

    def test_a_refresh_grant_covers_every_table_the_refresh_writes(self) -> None:
        assert set(IDENTITY_LEDGER_REFRESH_GRANTS) == {*IDENTITY_LEDGER_VIEWS, *IDENTITY_LEDGER_STORES, "identity_ledger_refresh_state"}

    def test_the_retirement_is_guarded_and_resets_the_state_row(self) -> None:
        statement = _STATEMENTS["identity ledger materialized views retirement"]
        for view in IDENTITY_LEDGER_VIEWS:
            assert f"to_regclass('public.{view}')) = 'm'" in statement
            assert f"DROP MATERIALIZED VIEW IF EXISTS public.{view};" in statement
        assert "IF retired THEN\n        DELETE FROM public.identity_ledger_refresh_state;" in statement

    def test_the_rollback_removes_every_object_the_upgrade_added(self) -> None:
        script = "\n".join(IDENTITY_LEDGER_ROLLBACK_STATEMENTS)
        for view in (*IDENTITY_LEDGER_VIEWS, *IDENTITY_LEDGER_STORES):
            assert f"public.{view}" in script
        for name in _STATEMENTS:
            if name.endswith(" function") and name != "identity_ledger_refresh function":
                assert f"public.{name.removesuffix(' function')}()" in script, name
        assert script.count("DROP TRIGGER IF EXISTS") == len(_LEDGER_SOURCES) * 4

    def test_read_indexes_serve_the_window_and_the_page(self) -> None:
        assert _STATEMENTS["idx_identity_ledger_edition_events_at"].endswith("ON public.identity_ledger_edition_events (at)")
        assert _STATEMENTS["idx_identity_ledger_edition_events_type_at"].endswith("ON public.identity_ledger_edition_events (type, at)")
        assert _STATEMENTS["idx_identity_ledger_work_summary_page"].endswith(
            "ON public.identity_ledger_work_summary (event, last_event DESC, work_id)"
        )
        for name, statement in _IDENTITY_LEDGER_STATEMENTS:
            if name.startswith("idx_"):
                assert statement.startswith(("CREATE INDEX IF NOT EXISTS ", "CREATE UNIQUE INDEX IF NOT EXISTS ")), name

    def test_the_work_summary_keys_its_unfiltered_row_on_a_sentinel_not_null(self) -> None:
        statement = _STATEMENTS["identity_ledger_verify function"]
        assert "'any'::text AS event" in statement
        assert "('reattached', 'promoted', 'reverted')" in statement

    def test_the_day_rollup_chains_each_edition_to_its_previous_day_so_a_window_counts_it_once(self) -> None:
        statement = _STATEMENTS["identity_ledger_work_summary_day table"]
        assert "prev_day    DATE NOT NULL" in statement  # -infinity for an edition's first day, never NULL
        verify = _STATEMENTS["identity_ledger_verify function"]
        assert "lag(day) OVER (PARTITION BY work_id, edition_id ORDER BY day)" in verify
        assert "lag(day) OVER (PARTITION BY work_id, edition_id, type ORDER BY day)" in verify
        assert "'-infinity'::date" in verify
        assert "(event.at AT TIME ZONE 'UTC')::date" in verify
        assert "'repaired'" in verify

    def test_the_day_rollup_is_maintained_for_dirty_works_and_rebuilt_whole(self) -> None:
        for name in ("identity_ledger_maintain function", "identity_ledger_rebuild function"):
            assert (
                "INSERT INTO public.identity_ledger_work_summary_day (work_id, event, day, prev_day, editions, first_event, last_event)"
                in _STATEMENTS[name]
            )
        assert (
            "DELETE FROM public.identity_ledger_work_summary_day AS stored USING pg_temp.ledger_works AS dirty"
            in _STATEMENTS["identity_ledger_maintain function"]
        )

    def test_the_work_events_chain_each_instant_to_its_neighbours_over_the_whole_work(self) -> None:
        statement = _STATEMENTS["identity_ledger_work_events table"]
        assert "prev_at TIMESTAMPTZ,\n" in statement  # NULL at a work's first instant
        assert "next_at TIMESTAMPTZ\n" in statement  # NULL at its last
        verify = _STATEMENTS["identity_ledger_verify function"]
        assert "lag(at) OVER work AS prev_at, lead(at) OVER work AS next_at" in verify
        assert "WINDOW work AS (PARTITION BY event, work_id ORDER BY at)" in verify
        assert "SELECT 'any'::text AS event, work_id, at FROM work_at GROUP BY work_id, at" in verify

    def test_the_work_events_read_indexes_serve_the_window_total_and_the_page(self) -> None:
        assert _STATEMENTS["idx_identity_ledger_work_events_page"].endswith(
            "ON public.identity_ledger_work_events (event, at DESC, work_id) INCLUDE (prev_at, next_at)"
        )
        assert [name for name in _STATEMENTS if name.startswith("idx_identity_ledger_work_events_")] == [
            "idx_identity_ledger_work_events_key",
            "idx_identity_ledger_work_events_page",
        ]

    def test_the_work_events_are_replaced_whole_for_dirty_works_and_rebuilt_whole(self) -> None:
        for name in ("identity_ledger_maintain function", "identity_ledger_rebuild function"):
            assert "INSERT INTO public.identity_ledger_work_events (event, work_id, at, prev_at, next_at)" in _STATEMENTS[name]
        maintain = _STATEMENTS["identity_ledger_maintain function"]
        assert (
            "DELETE FROM public.identity_ledger_work_events AS stored USING pg_temp.ledger_works AS dirty WHERE stored.work_id = dirty.work_id"
            in maintain
        )
        assert "JOIN pg_temp.ledger_works AS dirty_work ON dirty_work.work_id = parent.work_id" in maintain
        assert "DELETE FROM public.identity_ledger_work_events;" in _STATEMENTS["identity_ledger_rebuild function"]
        assert "ANALYZE public.identity_ledger_work_events;" in _STATEMENTS["identity_ledger_rebuild function"]

    def test_the_rebuild_request_heals_an_unfilled_work_events_table(self) -> None:
        statement = _STATEMENTS["identity ledger rebuild request for new derived tables"]
        assert "OR NOT EXISTS (SELECT 1 FROM public.identity_ledger_work_events)" in statement

    def test_verify_recomputes_every_kind_in_one_statement(self) -> None:
        statement = _STATEMENTS["identity_ledger_verify function"]
        assert re.findall(r"'(\w+)'::text AS kind", statement) == ["release", "master", "artist", "label"]
        assert "LANGUAGE sql" in statement
        assert "STABLE" in statement
        for view in IDENTITY_LEDGER_VIEWS:
            assert f"public.{view}" in statement

    def test_the_refresh_state_is_one_row(self) -> None:
        statement = _STATEMENTS["identity_ledger_refresh_state table"]
        assert "CREATE TABLE IF NOT EXISTS identity_ledger_refresh_state" in statement
        assert "singleton        BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton)" in statement
        assert "refreshed_at     TIMESTAMPTZ NOT NULL" in statement
        assert "duration         INTERVAL NOT NULL" in statement
        assert "source_watermark JSONB\n" in statement

    def test_the_refresh_drains_then_maintains_or_rebuilds(self) -> None:
        statement = _STATEMENTS["identity_ledger_refresh function"]
        assert "CREATE OR REPLACE FUNCTION public.identity_ledger_refresh(source_watermark jsonb DEFAULT NULL)" in statement
        assert "RETURNS timestamptz" in statement
        assert "REFRESH MATERIALIZED VIEW" not in statement
        assert statement.index("pg_advisory_xact_lock") < statement.index("DELETE FROM public.identity_ledger_changes")
        assert statement.index("DELETE FROM public.identity_ledger_changes") < statement.index("PERFORM public.identity_ledger_rebuild()")
        assert "hashtext('groovemap:identity-ledger-refresh')" in statement
        assert "capture triggers are incomplete" in statement
        assert "ON CONFLICT (singleton) DO UPDATE" in statement
        assert "source_watermark = EXCLUDED.source_watermark" in statement
        assert "RETURN started;" in statement

    def test_a_rebuild_drains_the_deltas_in_the_statement_that_counts_the_sources(self) -> None:
        statement = _STATEMENTS["identity_ledger_rebuild function"]
        assert "WITH drained AS (DELETE FROM public.identity_ledger_density_changes RETURNING 1)" in statement
        assert "TRUNCATE" not in statement

    def test_no_grant_is_declared(self) -> None:
        """catalog-api refreshes as the owning login; the design note states the grant otherwise needed."""
        for statement in _STATEMENTS.values():
            assert "GRANT" not in statement.upper()

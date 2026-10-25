"""Tests for the precomputed Identity Ledger (epic gm-database-schema-k8e9).

The four materialized views, their unique indexes, the refresh state row, and
`identity_ledger_refresh()`.  Every assertion is made against the statement
lists, never against a database; the integration suite pins the views' rows
to catalog-api's reference definitions and the refresh's locking.
"""

import re

from groovemap_schema.postgres import (
    _GRAPH_STATEMENTS,
    _IDENTITY_LEDGER_STATEMENTS,
    IDENTITY_LEDGER_VIEWS,
    _schema_statements,
)


_STATEMENTS = dict(_IDENTITY_LEDGER_STATEMENTS)

# Each view's unique index, over the columns REFRESH ... CONCURRENTLY matches rows on.
_UNIQUE_KEYS = {
    "identity_ledger_edition_events": ("idx_identity_ledger_edition_events_key", "(edition_id, at, type)"),
    "identity_ledger_edition_parents": ("idx_identity_ledger_edition_parents_key", "(edition_id, work_id)"),
    "identity_ledger_work_summary": ("idx_identity_ledger_work_summary_key", "(event, work_id)"),
    "identity_ledger_density": ("idx_identity_ledger_density_key", "(kind, month, type, dated_from)"),
}


def _view_statement(view: str) -> str:
    return _STATEMENTS[f"{view} materialized view"]


class TestIdentityLedgerStatements:
    def test_statements_are_declared_in_dependency_order(self) -> None:
        assert [name for name, _statement in _IDENTITY_LEDGER_STATEMENTS] == [
            "identity_ledger_edition_events materialized view",
            "idx_identity_ledger_edition_events_key",
            "idx_identity_ledger_edition_events_at",
            "idx_identity_ledger_edition_events_type_at",
            "identity_ledger_edition_parents materialized view",
            "idx_identity_ledger_edition_parents_key",
            "identity_ledger_work_summary materialized view",
            "idx_identity_ledger_work_summary_key",
            "idx_identity_ledger_work_summary_page",
            "identity_ledger_density materialized view",
            "idx_identity_ledger_density_key",
            "identity_ledger_refresh_state table",
            "identity_ledger_refresh function",
        ]

    def test_they_run_after_the_graph_statements_they_read(self) -> None:
        names = [name for name, _statement in _schema_statements()]
        assert names.index("identity_ledger_edition_events materialized view") > max(names.index(name) for name, _statement in _GRAPH_STATEMENTS)
        assert names.index("identity_ledger_edition_events materialized view") > names.index("musicbrainz.releases table")
        assert names.index("identity_ledger_edition_events materialized view") > names.index("identity_link_events table")

    def test_every_view_reads_only_views_declared_before_it(self) -> None:
        for position, view in enumerate(IDENTITY_LEDGER_VIEWS):
            body = _view_statement(view).split(" AS\n", 1)[1]
            read = set(re.findall(r"public\.(identity_ledger_\w+)", body))
            assert read <= set(IDENTITY_LEDGER_VIEWS[:position]), (view, read)

    def test_every_view_is_created_idempotently_and_unpopulated(self) -> None:
        for view in IDENTITY_LEDGER_VIEWS:
            statement = _view_statement(view)
            assert f"CREATE MATERIALIZED VIEW IF NOT EXISTS public.{view} AS" in statement
            assert statement.rstrip().endswith("WITH NO DATA"), view

    def test_every_view_has_a_unique_index_for_concurrent_refresh(self) -> None:
        for view, (index, columns) in _UNIQUE_KEYS.items():
            assert _STATEMENTS[index] == f"CREATE UNIQUE INDEX IF NOT EXISTS {index} ON public.{view} {columns}"
        unique = [statement for statement in _STATEMENTS.values() if "UNIQUE INDEX" in statement]
        assert len(unique) == len(IDENTITY_LEDGER_VIEWS)

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
        statement = _view_statement("identity_ledger_work_summary")
        assert "'any'::text AS event" in statement
        assert "('reattached', 'promoted', 'reverted')" in statement

    def test_the_density_view_reports_every_catalog_kind(self) -> None:
        statement = _view_statement("identity_ledger_density")
        assert re.findall(r"'(\w+)'::text AS kind", statement) == ["release", "master", "artist", "label"]
        assert statement.count("UNION ALL\n(") == 3

    def test_the_edition_views_read_release_items_only(self) -> None:
        statement = _view_statement("identity_ledger_edition_events")
        assert set(re.findall(r"kind = '(\w+)'", statement)) == {"release"}

    def test_the_refresh_state_is_one_row(self) -> None:
        statement = _STATEMENTS["identity_ledger_refresh_state table"]
        assert "CREATE TABLE IF NOT EXISTS identity_ledger_refresh_state" in statement
        assert "singleton        BOOLEAN PRIMARY KEY DEFAULT true CHECK (singleton)" in statement
        assert "refreshed_at     TIMESTAMPTZ NOT NULL" in statement
        assert "duration         INTERVAL NOT NULL" in statement
        assert "source_watermark JSONB\n" in statement

    def test_the_refresh_populates_plainly_then_concurrently_in_dependency_order(self) -> None:
        statement = _STATEMENTS["identity_ledger_refresh function"]
        assert "CREATE OR REPLACE FUNCTION public.identity_ledger_refresh(source_watermark jsonb DEFAULT NULL)" in statement
        assert "RETURNS timestamptz" in statement
        order = re.search(r"ARRAY\[(.*?)\]", statement)
        assert order is not None
        assert order.group(1) == ", ".join(f"'{view}'" for view in IDENTITY_LEDGER_VIEWS)
        assert "relispopulated" in statement
        assert "REFRESH MATERIALIZED VIEW CONCURRENTLY public.%I" in statement
        assert "REFRESH MATERIALIZED VIEW public.%I" in statement
        assert "ON CONFLICT (singleton) DO UPDATE" in statement
        assert "source_watermark = EXCLUDED.source_watermark" in statement
        assert "RETURN started;" in statement

    def test_no_grant_is_declared(self) -> None:
        """catalog-api refreshes as the owning login; the design note states the grant otherwise needed."""
        for statement in _STATEMENTS.values():
            assert "GRANT" not in statement.upper()

"""Tests for activity_summary schema definition."""

from groovemap_schema.postgres import _INSIGHTS_TABLES


# The full _INSIGHTS_TABLES name order as of the computation_log table, before
# activity_summary was added — asserted unchanged below so this bead's insertion
# is additive-only and does not reorder or drop any existing entry.
_NAMES_THROUGH_COMPUTATION_LOG = [
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
]

_NAMES_AFTER_ACTIVITY_SUMMARY = [
    "idx_anniversaries_month_year",
    "idx_genre_trends_genre",
    "insights.release_rarity add collection_prevalence",
    "insights.release_rarity add media_families",
    "insights.release_rarity add family_signals",
    "insights.release_rarity add medium_rarity",
]


class TestActivitySummarySchema:
    def test_activity_summary_table_defined(self) -> None:
        """Verify activity_summary table exists in _INSIGHTS_TABLES."""
        names = [name for name, _ddl in _INSIGHTS_TABLES]
        assert "insights.activity_summary table" in names

    def test_activity_summary_date_index_defined(self) -> None:
        """Verify the summary_date descending index exists."""
        names = [name for name, _ddl in _INSIGHTS_TABLES]
        assert "idx_activity_summary_date" in names

    def test_activity_summary_table_is_idempotent(self) -> None:
        ddl = dict(_INSIGHTS_TABLES)["insights.activity_summary table"]
        assert "CREATE TABLE IF NOT EXISTS insights.activity_summary" in ddl

    def test_activity_summary_date_index_is_idempotent(self) -> None:
        stmt = dict(_INSIGHTS_TABLES)["idx_activity_summary_date"]
        assert stmt == ("CREATE INDEX IF NOT EXISTS idx_activity_summary_date ON insights.activity_summary (summary_date DESC)")

    def test_activity_summary_ddl_has_required_columns(self) -> None:
        """Verify DDL includes all required columns."""
        ddl = dict(_INSIGHTS_TABLES)["insights.activity_summary table"]
        for col in [
            "summary_date",
            "dimension",
            "dimension_key",
            "record_count",
            "subject_count",
            "candidate_set_count",
            "computed_at",
        ]:
            assert col in ddl, f"Column {col} missing from DDL"

    def test_activity_summary_dimension_check_constraint(self) -> None:
        ddl = dict(_INSIGHTS_TABLES)["insights.activity_summary table"]
        assert "CHECK (dimension IN ('event_type', 'policy_id'))" in ddl

    def test_activity_summary_primary_key(self) -> None:
        ddl = dict(_INSIGHTS_TABLES)["insights.activity_summary table"]
        assert "PRIMARY KEY (summary_date, dimension, dimension_key)" in ddl

    def test_placed_immediately_after_computation_log_and_its_index(self) -> None:
        """activity_summary and its index sit right after computation_log's pair."""
        names = [name for name, _ddl in _INSIGHTS_TABLES]
        idx = names.index("idx_computation_log_type_started")
        assert names[idx + 1] == "insights.activity_summary table"
        assert names[idx + 2] == "idx_activity_summary_date"

    def test_existing_statement_list_otherwise_unchanged(self) -> None:
        """The two new entries are inserted without reordering or dropping any other."""
        names = [name for name, _ddl in _INSIGHTS_TABLES]
        expected = [
            *_NAMES_THROUGH_COMPUTATION_LOG,
            "insights.activity_summary table",
            "idx_activity_summary_date",
            *_NAMES_AFTER_ACTIVITY_SUMMARY,
        ]
        assert names == expected

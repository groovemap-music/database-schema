"""Tests for the Genre Atlas insights tables (gm-design-4804, B).

Statement-text assertions only; the constraints are exercised on a real engine in
tests/integration/test_real_schema_idempotence.py.
"""

import re

import pytest

from groovemap_schema.postgres import _INSIGHTS_TABLES


_STATEMENTS = dict(_INSIGHTS_TABLES)

_TABLES = {
    "insights.style_year_counts table": ("style", "year", "release_count", "master_count", "computed_at"),
    "insights.style_parents table": ("style", "genre", "share", "is_dominant", "computed_at"),
    "insights.genre_atlas_sectors table": (
        "month",
        "genre",
        "ring_order",
        "sector_start",
        "sector_end",
        "x",
        "y",
        "computed_at",
    ),
    "insights.style_atlas_layout table": ("month", "node_kind", "style", "genre", "x", "y", "computed_at"),
}


def _columns(ddl: str) -> list[str]:
    body = ddl[ddl.index("(") + 1 :]
    return [m.group(1) for line in body.splitlines() if (m := re.match(r"\s{12}([a-z_]+)\s+[A-Z]", line))]


@pytest.mark.parametrize("name", _TABLES)
def test_table_is_idempotent_in_the_insights_schema(name: str) -> None:
    table = name.removesuffix(" table")
    assert f"CREATE TABLE IF NOT EXISTS {table} (" in _STATEMENTS[name]


@pytest.mark.parametrize(("name", "columns"), _TABLES.items())
def test_table_has_the_designed_columns_in_order(name: str, columns: tuple[str, ...]) -> None:
    assert tuple(_columns(_STATEMENTS[name])) == columns


@pytest.mark.parametrize("name", _TABLES)
def test_table_stamps_computed_at(name: str) -> None:
    assert "computed_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()" in _STATEMENTS[name]


def test_style_year_counts_primary_key_and_non_negative_counts() -> None:
    ddl = _STATEMENTS["insights.style_year_counts table"]
    assert "PRIMARY KEY (style, year)" in ddl
    assert "release_count   BIGINT NOT NULL CHECK (release_count >= 0)" in ddl
    assert "master_count    BIGINT NOT NULL CHECK (master_count >= 0)" in ddl


def test_style_parents_share_is_a_fraction_in_the_half_open_unit_interval() -> None:
    ddl = _STATEMENTS["insights.style_parents table"]
    assert "PRIMARY KEY (style, genre)" in ddl
    assert "share           DOUBLE PRECISION NOT NULL CHECK (share > 0 AND share <= 1)" in ddl


def test_style_parents_allows_one_dominant_parent_per_style() -> None:
    assert _STATEMENTS["idx_style_parents_one_dominant"] == (
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_style_parents_one_dominant ON insights.style_parents (style) WHERE is_dominant"
    )


def test_genre_atlas_sectors_primary_key_and_no_nullable_column() -> None:
    ddl = _STATEMENTS["insights.genre_atlas_sectors table"]
    assert "PRIMARY KEY (month, genre)" in ddl
    assert "month           DATE NOT NULL" in ddl
    for column in _TABLES["insights.genre_atlas_sectors table"]:
        assert re.search(rf"^\s+{column}\s+[A-Z]+.*NOT NULL", ddl, re.MULTILINE), column


def test_style_atlas_layout_primary_key_keeps_prior_months() -> None:
    ddl = _STATEMENTS["insights.style_atlas_layout table"]
    assert "PRIMARY KEY (month, node_kind, style, genre)" in ddl
    assert "month           DATE NOT NULL" in ddl


def test_style_atlas_layout_places_only_styles_and_ghosts() -> None:
    ddl = _STATEMENTS["insights.style_atlas_layout table"]
    assert "CHECK (node_kind IN ('style', 'ghost'))" in ddl
    assert "style           TEXT NOT NULL" in ddl
    assert "genre           TEXT NOT NULL" in ddl
    assert "ring_order" not in ddl
    assert "sector_" not in ddl


def test_atlas_statements_sit_after_the_activity_summary_index() -> None:
    names = [name for name, _ddl in _INSIGHTS_TABLES]
    start = names.index("idx_activity_summary_date") + 1
    assert names[start : start + 5] == [
        "insights.style_year_counts table",
        "insights.style_parents table",
        "idx_style_parents_one_dominant",
        "insights.genre_atlas_sectors table",
        "insights.style_atlas_layout table",
    ]

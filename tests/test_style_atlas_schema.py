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
    "insights.genre_year_counts table": ("genre", "year", "release_count", "computed_at"),
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
    "insights.style_landmark_masters table": ("style", "master_id", "title", "year", "release_count", "rank", "computed_at"),
    "insights.style_cotags table": ("style", "other", "release_count", "rank", "computed_at"),
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


def test_genre_year_counts_primary_key_and_non_negative_counts() -> None:
    ddl = _STATEMENTS["insights.genre_year_counts table"]
    assert "PRIMARY KEY (genre, year)" in ddl
    assert "genre           TEXT NOT NULL" in ddl
    assert "year            INT NOT NULL" in ddl
    assert "release_count   BIGINT NOT NULL CHECK (release_count >= 0)" in ddl


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


def test_style_landmark_masters_keys_rank_and_nullability() -> None:
    ddl = _STATEMENTS["insights.style_landmark_masters table"]
    assert "PRIMARY KEY (style, master_id)" in ddl
    assert "UNIQUE (style, rank)" in ddl
    assert "style           TEXT NOT NULL" in ddl
    assert "master_id       TEXT NOT NULL" in ddl
    assert "title           TEXT NOT NULL" in ddl
    assert "release_count   BIGINT NOT NULL CHECK (release_count >= 0)" in ddl
    assert "rank            INT NOT NULL CHECK (rank BETWEEN 1 AND 25)" in ddl
    # year is the only nullable column: a master may have no release year.
    assert re.search(r"^\s+year\s+INT,?$", ddl, re.MULTILINE)


def test_style_cotags_keys_rank_and_self_edge_check() -> None:
    ddl = _STATEMENTS["insights.style_cotags table"]
    assert "PRIMARY KEY (style, other)" in ddl
    assert "UNIQUE (style, rank)" in ddl
    assert "CHECK (style <> other)" in ddl
    assert "style           TEXT NOT NULL" in ddl
    assert "other           TEXT NOT NULL" in ddl
    assert "release_count   BIGINT NOT NULL CHECK (release_count >= 0)" in ddl
    assert "rank            INT NOT NULL CHECK (rank BETWEEN 1 AND 25)" in ddl
    # Every column is NOT NULL: there is no nullable column to list.
    assert re.search(r"^\s+\w+\s+\w+,?$", ddl, re.MULTILINE) is None


def test_atlas_statements_sit_after_the_activity_summary_index() -> None:
    names = [name for name, _ddl in _INSIGHTS_TABLES]
    start = names.index("idx_activity_summary_date") + 1
    assert names[start : start + 8] == [
        "insights.style_year_counts table",
        "insights.genre_year_counts table",
        "insights.style_parents table",
        "idx_style_parents_one_dominant",
        "insights.genre_atlas_sectors table",
        "insights.style_atlas_layout table",
        "insights.style_landmark_masters table",
        "insights.style_cotags table",
    ]

"""Tests for the retired metrics-history tables (ADR 0018)."""

from __future__ import annotations

import pytest

from groovemap_schema.postgres import _schema_statements


RETIRED_TABLES = ["queue_metrics", "service_health_metrics"]


def test_retired_tables_are_dropped_idempotently() -> None:
    """Each retired table has one DROP TABLE IF EXISTS step."""
    statements = dict(_schema_statements())
    for table in RETIRED_TABLES:
        assert statements[f"{table} table retired"] == f"DROP TABLE IF EXISTS {table}"


@pytest.mark.parametrize("table", RETIRED_TABLES)
def test_retired_tables_are_never_created(table: str) -> None:
    """No statement creates a retired table or an index on it."""
    for _name, statement in _schema_statements():
        text = str(statement)
        assert f"CREATE TABLE IF NOT EXISTS {table}" not in text
        assert not (text.lstrip().startswith("CREATE") and f"ON {table} " in text)

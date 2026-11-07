"""pg_stat_statements is created when the server preloads it, and skipped with a notice otherwise.

The gate runs against the same cursor fake as the vector gate. Probe order: installed, then
preloaded (only when not installed), then superuser (only when preloaded and not installed).
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest

from groovemap_schema.postgres import (
    _PG_STAT_STATEMENTS_STATEMENT,
    PG_STAT_STATEMENTS_EXTENSION,
    _apply_pg_stat_statements,
    _pg_stat_statements_preloaded,
)


def _cursor(rows: list[Any], *, create_error: Exception | None = None) -> AsyncMock:
    cursor = AsyncMock()
    cursor.fetchone = AsyncMock(side_effect=rows)

    async def execute(statement: str, *_args: Any) -> None:
        if create_error is not None and "CREATE EXTENSION" in statement:
            raise create_error

    cursor.execute = AsyncMock(side_effect=execute)
    return cursor


def _statements(cursor: AsyncMock) -> list[str]:
    return [str(call.args[0]) for call in cursor.execute.await_args_list]


def _creates(cursor: AsyncMock) -> list[str]:
    return [s for s in _statements(cursor) if "CREATE EXTENSION" in s]


def test_the_statement_is_idempotent_and_names_the_extension() -> None:
    name, statement = _PG_STAT_STATEMENTS_STATEMENT
    assert name == "pg_stat_statements extension"
    assert statement == "CREATE EXTENSION IF NOT EXISTS pg_stat_statements"
    assert PG_STAT_STATEMENTS_EXTENSION == "pg_stat_statements"


@pytest.mark.asyncio
async def test_already_installed_is_a_no_op_and_sends_no_create() -> None:
    cursor = _cursor([(True,)])

    assert await _apply_pg_stat_statements(cursor) == 0
    assert _creates(cursor) == []
    assert len(_statements(cursor)) == 1


@pytest.mark.asyncio
async def test_not_preloaded_skips_with_no_create_and_no_failure() -> None:
    cursor = _cursor([(False,), ("auto_explain, pg_trgm",)])

    assert await _apply_pg_stat_statements(cursor) == 0
    assert _creates(cursor) == []


@pytest.mark.asyncio
async def test_preloaded_but_not_superuser_skips_with_no_create() -> None:
    cursor = _cursor([(False,), ("pg_stat_statements",), (False,)])

    assert await _apply_pg_stat_statements(cursor) == 0
    assert _creates(cursor) == []


@pytest.mark.asyncio
async def test_preloaded_and_superuser_creates_the_extension() -> None:
    cursor = _cursor([(False,), ("pg_stat_statements",), (True,)])

    assert await _apply_pg_stat_statements(cursor) == 0
    assert _creates(cursor) == ["CREATE EXTENSION IF NOT EXISTS pg_stat_statements"]


@pytest.mark.asyncio
async def test_a_failed_create_counts_as_one_failed_statement() -> None:
    cursor = _cursor([(False,), ("pg_stat_statements",), (True,)], create_error=RuntimeError("boom"))

    assert await _apply_pg_stat_statements(cursor) == 1


@pytest.mark.asyncio
async def test_preload_list_is_matched_as_a_whole_name() -> None:
    assert await _pg_stat_statements_preloaded(_cursor([("pg_stat_statements,auto_explain",)])) is True
    assert await _pg_stat_statements_preloaded(_cursor([("pg_stat_statements_extra",)])) is False
    assert await _pg_stat_statements_preloaded(_cursor([("",)])) is False

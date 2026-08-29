"""Behavior and regression tests for the runnable database initializer."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from groovemap_schema import initializer


@pytest.fixture(autouse=True)
def _disable_file_logging() -> Any:
    with patch.object(initializer, "setup_logging"):
        yield


def _mock_connection(fetchone_result: Any) -> MagicMock:
    cursor = MagicMock()
    cursor.fetchone.return_value = fetchone_result
    cursor_context = MagicMock()
    cursor_context.__enter__.return_value = cursor
    connection = MagicMock()
    connection.__enter__.return_value = connection
    connection.cursor.return_value = cursor_context
    return connection


def _mock_driver() -> MagicMock:
    driver = MagicMock()
    session = AsyncMock()
    session.__aenter__.return_value = session
    result = AsyncMock()
    result.single.return_value = {"health": 1}
    session.run.return_value = result
    driver.session.return_value = session
    driver.close = AsyncMock()
    return driver


class TestPostgresConfiguration:
    def test_embedded_port_and_bounded_timeout(self) -> None:
        with (
            patch.object(initializer, "POSTGRES_HOST", "pooler:6432"),
            patch.object(initializer, "POSTGRES_DATABASE", "groovemap_test"),
            patch.object(initializer, "POSTGRES_USERNAME", "user"),
            patch.object(initializer, "POSTGRES_PASSWORD", "password"),
        ):
            params = initializer._postgres_connection_params()

        assert params == {
            "host": "pooler",
            "port": 6432,
            "dbname": "groovemap_test",
            "user": "user",
            "password": "password",
            "connect_timeout": 10,
        }

    def test_host_without_port_uses_configured_default(self) -> None:
        with patch.object(initializer, "POSTGRES_HOST", "postgres"), patch.dict(initializer.os.environ, {"POSTGRES_PORT": "5433"}):
            params = initializer._postgres_connection_params()
        assert params["host"] == "postgres"
        assert params["port"] == 5433


class TestEnsurePostgresDatabase:
    def test_existing_database_is_not_recreated(self) -> None:
        connection = _mock_connection((1,))
        with patch.object(initializer.psycopg, "connect", return_value=connection) as connect:
            initializer._ensure_postgres_database({"dbname": "groovemap", "connect_timeout": 10})

        connect.assert_called_once_with(dbname="postgres", connect_timeout=10, autocommit=True)
        assert connection.cursor.return_value.__enter__.return_value.execute.call_count == 1

    def test_missing_database_is_created_with_escaped_identifier(self) -> None:
        connection = _mock_connection(None)
        with patch.object(initializer.psycopg, "connect", return_value=connection):
            initializer._ensure_postgres_database({"dbname": "groovemap", "connect_timeout": 10})

        cursor = connection.cursor.return_value.__enter__.return_value
        assert cursor.execute.call_count == 2


class TestPostgresInitialization:
    @pytest.mark.asyncio
    async def test_success_initializes_and_closes_pool(self) -> None:
        pool = AsyncMock()
        with (
            patch.object(initializer, "AsyncPostgreSQLPool", return_value=pool),
            patch.object(initializer, "create_postgres_schema", new_callable=AsyncMock, return_value=0),
        ):
            assert await initializer._init_postgres({"host": "postgres"}) is True
        pool.initialize.assert_awaited_once()
        pool.close.assert_awaited_once()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("failure_count", [1, 3])
    async def test_partial_schema_failure_is_fatal(self, failure_count: int) -> None:
        pool = AsyncMock()
        with (
            patch.object(initializer, "AsyncPostgreSQLPool", return_value=pool),
            patch.object(
                initializer,
                "create_postgres_schema",
                new_callable=AsyncMock,
                return_value=failure_count,
            ),
        ):
            assert await initializer._init_postgres({}) is False
        pool.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connection_failure_is_reported_and_pool_is_closed(self) -> None:
        pool = AsyncMock()
        pool.initialize.side_effect = ConnectionError("unavailable")
        with patch.object(initializer, "AsyncPostgreSQLPool", return_value=pool):
            assert await initializer._init_postgres({}) is False
        pool.close.assert_awaited_once()


class TestNeo4jInitialization:
    @pytest.mark.asyncio
    async def test_connectivity_schema_and_close(self) -> None:
        driver = _mock_driver()
        with (
            patch.object(initializer, "AsyncResilientNeo4jDriver", return_value=driver),
            patch.object(initializer, "create_neo4j_schema", new_callable=AsyncMock, return_value=0),
        ):
            assert await initializer._init_neo4j() is True
        driver.session.assert_called_once_with(database="neo4j")
        driver.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_partial_schema_failure_is_fatal(self) -> None:
        driver = _mock_driver()
        with (
            patch.object(initializer, "AsyncResilientNeo4jDriver", return_value=driver),
            patch.object(initializer, "create_neo4j_schema", new_callable=AsyncMock, return_value=2),
        ):
            assert await initializer._init_neo4j() is False
        driver.close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_connectivity_failure_is_reported_and_driver_is_closed(self) -> None:
        driver = _mock_driver()
        driver.session.side_effect = ConnectionError("unavailable")
        with patch.object(initializer, "AsyncResilientNeo4jDriver", return_value=driver):
            assert await initializer._init_neo4j() is False
        driver.close.assert_awaited_once()


class TestMain:
    @pytest.mark.asyncio
    async def test_initializers_run_concurrently(self) -> None:
        postgres_started = asyncio.Event()
        neo4j_started = asyncio.Event()

        async def postgres(_: dict[str, Any]) -> bool:
            postgres_started.set()
            await neo4j_started.wait()
            return True

        async def neo4j() -> bool:
            neo4j_started.set()
            await postgres_started.wait()
            return True

        with (
            patch.object(initializer, "_ensure_postgres_database"),
            patch.object(initializer, "_init_postgres", side_effect=postgres),
            patch.object(initializer, "_init_neo4j", side_effect=neo4j),
        ):
            assert await initializer.main() == 0

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("postgres_ok", "neo4j_ok", "status"),
        [(True, True, 0), (False, True, 1), (True, False, 1), (False, False, 1)],
    )
    async def test_combines_schema_results(self, postgres_ok: bool, neo4j_ok: bool, status: int) -> None:
        with (
            patch.object(initializer, "_ensure_postgres_database"),
            patch.object(initializer, "_init_postgres", new_callable=AsyncMock, return_value=postgres_ok),
            patch.object(initializer, "_init_neo4j", new_callable=AsyncMock, return_value=neo4j_ok),
        ):
            assert await initializer.main() == status

    @pytest.mark.asyncio
    async def test_admin_database_failure_stops_before_schema_application(self) -> None:
        with (
            patch.object(initializer, "_ensure_postgres_database", side_effect=ConnectionError("unavailable")),
            patch.object(initializer, "_init_postgres", new_callable=AsyncMock) as postgres,
            patch.object(initializer, "_init_neo4j", new_callable=AsyncMock) as neo4j,
        ):
            assert await initializer.main() == 1
        postgres.assert_not_awaited()
        neo4j.assert_not_awaited()


def test_runtime_defaults_use_groovemap_identity() -> None:
    assert initializer.POSTGRES_DATABASE == "groovemap"
    assert initializer.POSTGRES_USERNAME == "groovemap"
    assert initializer.POSTGRES_PASSWORD == "groovemap"
    assert initializer.NEO4J_PASSWORD == "groovemap"
    assert initializer.SERVICE_NAME == "database-schema"
    assert initializer.SERVICE_NAME in initializer.BANNER

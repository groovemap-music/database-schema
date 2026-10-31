"""Real-engine proof of pre-provisioned mode: a non-superuser owner on a shared server.

An operator provisions the database, the runtime role, and the extensions as an administrator;
schema-init then verifies and applies schema inside that one database as a role that is neither
a superuser nor allowed to create roles. A second application's database and role live on the
same server throughout and must come out unchanged.
"""

from __future__ import annotations

import contextlib
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from groovemap_schema import initializer


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]


def _admin_params(dbname: str = "postgres") -> dict[str, Any]:
    return {**initializer._postgres_connection_params(), "dbname": dbname}


async def _admin_execute(statements: list[str], dbname: str = "postgres") -> None:
    async with await psycopg.AsyncConnection.connect(**_admin_params(dbname), autocommit=True) as connection:
        for statement in statements:
            await connection.execute(statement)  # type: ignore[arg-type]


async def _rows(dbname: str, query: str, params: Any = None, *, user_params: dict[str, Any] | None = None) -> list[tuple[Any, ...]]:
    async with await psycopg.AsyncConnection.connect(**(user_params or _admin_params(dbname)), autocommit=True) as connection:
        cursor = await connection.execute(query, params)  # type: ignore[arg-type]
        return await cursor.fetchall()


async def _cluster_fingerprint(other_database: str, other_role: str) -> dict[str, Any]:
    """Everything observable about the neighbour application's database and role."""
    return {
        "role": await _rows("postgres", "SELECT rolsuper, rolcreaterole, rolcreatedb, rolcanlogin FROM pg_roles WHERE rolname = %s", (other_role,)),
        "database": await _rows("postgres", "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s", (other_database,)),
        "relations": await _rows(
            other_database,
            "SELECT n.nspname, c.relname FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast') ORDER BY 1, 2",
        ),
        "schemas": await _rows(other_database, "SELECT nspname FROM pg_namespace ORDER BY 1"),
        "extensions": await _rows(other_database, "SELECT extname FROM pg_extension ORDER BY 1"),
        "rows": await _rows(other_database, "SELECT id, note FROM neighbour_notes ORDER BY id"),
    }


@contextlib.asynccontextmanager
async def _shared_server(*, trgm: bool = True, create_database: bool = True, owner: bool = True) -> AsyncIterator[dict[str, Any]]:
    """Provision the least-privilege layout and a neighbour application, then clean both up."""
    suffix = uuid4().hex[:10]
    role, database, password = f"gm_owner_{suffix}", f"gm_db_{suffix}", uuid4().hex
    other_role, other_database = f"other_app_{suffix}", f"other_db_{suffix}"
    ident = sql.Identifier

    def render(template: str, *names: str) -> str:
        return sql.SQL(template).format(*(ident(name) for name in names)).as_string()

    await _admin_execute(
        [
            sql.SQL("CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE").format(ident(role), sql.Literal(password)).as_string(),
            sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER").format(ident(other_role)).as_string(),
            render("CREATE DATABASE {} OWNER {}", other_database, other_role),
        ]
    )
    await _admin_execute(["CREATE EXTENSION IF NOT EXISTS pg_trgm"], other_database)
    await _admin_execute(
        [
            "CREATE TABLE neighbour_notes (id int PRIMARY KEY, note text)",
            "INSERT INTO neighbour_notes VALUES (1, 'untouched')",
        ],
        other_database,
    )
    if create_database:
        owner_name = role if owner else None
        await _admin_execute([render("CREATE DATABASE {} OWNER {}", database, owner_name) if owner_name else render("CREATE DATABASE {}", database)])
        if trgm:
            await _admin_execute(["CREATE EXTENSION IF NOT EXISTS pg_trgm"], database)
    params = {**_admin_params(database), "user": role, "password": password}
    try:
        yield {"params": params, "role": role, "database": database, "other_database": other_database, "other_role": other_role}
    finally:
        await _admin_execute(
            [render("DROP DATABASE IF EXISTS {} WITH (FORCE)", database), render("DROP DATABASE IF EXISTS {} WITH (FORCE)", other_database)]
        )
        await _admin_execute([render("DROP ROLE IF EXISTS {}", role), render("DROP ROLE IF EXISTS {}", other_role)])


async def test_a_non_superuser_owner_initializes_and_the_neighbour_is_untouched() -> None:
    async with _shared_server() as server:
        params = server["params"]
        flags = await _rows("postgres", "SELECT rolsuper, rolcreaterole FROM pg_roles WHERE rolname = %s", (server["role"],))
        assert flags == [(False, False)]
        before = await _cluster_fingerprint(server["other_database"], server["other_role"])
        roles_before = await _rows("postgres", "SELECT rolname FROM pg_roles ORDER BY 1")
        databases_before = await _rows("postgres", "SELECT datname FROM pg_database ORDER BY 1")

        assert initializer._verify_preprovisioned_database(params) == []
        assert await initializer._apply_postgres_schema(params, preprovisioned=True) is True
        # A second run is idempotent.
        assert await initializer._apply_postgres_schema(params, preprovisioned=True) is True

        tables = await _rows(server["database"], "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
        assert tables[0][0] > 0
        assert await _cluster_fingerprint(server["other_database"], server["other_role"]) == before
        assert await _rows("postgres", "SELECT rolname FROM pg_roles ORDER BY 1") == roles_before
        assert await _rows("postgres", "SELECT datname FROM pg_database ORDER BY 1") == databases_before


async def test_the_apply_never_installs_an_extension() -> None:
    """pg_trgm is trusted, so the owner could install it: only the mode's own restraint stops that."""
    async with _shared_server(trgm=False) as server:
        assert await initializer._apply_postgres_schema(server["params"], preprovisioned=True) is True
        installed = await _rows(server["database"], "SELECT extname FROM pg_extension WHERE extname IN ('pg_trgm', 'vector')")
        assert installed == []


async def test_a_missing_database_fails_closed_with_the_admin_sql() -> None:
    async with _shared_server(create_database=False) as server:
        problems = initializer._verify_preprovisioned_database(server["params"])
        assert len(problems) == 1
        assert "CREATE DATABASE" in problems[0]
        assert f'OWNER "{server["role"]}"' in problems[0]


async def test_a_missing_pg_trgm_fails_closed_with_the_admin_sql() -> None:
    async with _shared_server(trgm=False) as server:
        problems = initializer._verify_preprovisioned_database(server["params"])
        assert len(problems) == 1
        assert "pg_trgm" in problems[0]
        assert "CREATE EXTENSION IF NOT EXISTS pg_trgm;" in problems[0]


async def test_insufficient_privilege_fails_closed_with_the_admin_sql() -> None:
    async with _shared_server(owner=False) as server:
        await _admin_execute(
            [sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(server["database"]), sql.Identifier(server["role"])).as_string()]
        )
        problems = initializer._verify_preprovisioned_database(server["params"])
        joined = "\n".join(problems)
        assert "cannot create schemas" in joined
        assert "ALTER DATABASE" in joined
        assert "GRANT CREATE ON DATABASE" in joined


async def test_main_exits_nonzero_before_touching_anything_when_the_database_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    async with _shared_server(create_database=False) as server:
        # Scoped, because _admin_params() reads the same module globals as the initializer.
        with monkeypatch.context() as patch:
            patch.setenv("POSTGRES_PREPROVISIONED", "true")
            patch.setattr(initializer, "POSTGRES_DATABASE", server["database"])
            patch.setattr(initializer, "POSTGRES_USERNAME", server["role"])
            patch.setattr(initializer, "POSTGRES_PASSWORD", server["params"]["password"])
            assert await initializer.main() == 1
        assert await _rows("postgres", "SELECT 1 FROM pg_database WHERE datname = %s", (server["database"],)) == []

"""Real-engine proof for the pipeline role's read of the loader extraction latch.

analytics-engine's nightly similar-artists job runs as `embedding_pipeline` and reads
`public.loader_extraction_latch` before each run, so it can skip while a Discogs or MusicBrainz
load holds the latch. The role gets exactly one grant for that: SELECT on the latch. These tests
log in as a member of the role and prove it can read the latch, cannot write it, and still cannot
read or write anything else in `public`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from psycopg import errors, sql

from groovemap_schema import initializer
from groovemap_schema.postgres import (
    _PIPELINE_ROLE_STATEMENTS,
    EMBEDDING_PIPELINE_ROLE,
    _execute_schema_statements,
    _schema_statements,
)


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


pytestmark = [pytest.mark.integration, pytest.mark.asyncio, pytest.mark.timeout(300)]

_LATCH = "public.loader_extraction_latch"


def _params(dbname: str, **overrides: Any) -> dict[str, Any]:
    return {**initializer._postgres_connection_params(), "dbname": dbname, **overrides}


async def _connect(dbname: str, **overrides: Any) -> psycopg.AsyncConnection[Any]:
    return await psycopg.AsyncConnection.connect(**_params(dbname, **overrides), autocommit=True)


@pytest_asyncio.fixture
async def dbname() -> AsyncIterator[str]:
    name = f"latch_grant_{uuid4().hex[:12]}"
    async with await _connect("postgres") as admin:
        await admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
    try:
        async with await _connect(name) as conn, conn.cursor() as cursor:
            _success, failures = await _execute_schema_statements(cursor, _schema_statements())
            assert failures == 0
            # The role and its grants are applied by `_apply_vector_schema`, after the tables exist.
            _success, failures = await _execute_schema_statements(cursor, _PIPELINE_ROLE_STATEMENTS)
            assert failures == 0
        yield name
    finally:
        async with await _connect("postgres") as admin:
            await admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(name)))


async def test_the_pipeline_role_reads_the_latch_and_cannot_write_it_or_read_anything_else(dbname: str) -> None:
    role, password = f"latch_reader_{uuid4().hex[:12]}", uuid4().hex
    owner = await _connect(dbname)
    async with owner:
        await owner.execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
        await owner.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(EMBEDDING_PIPELINE_ROLE), sql.Identifier(role)))
        try:
            await owner.execute(
                "INSERT INTO loader_extraction_latch (loader, version, signals) VALUES ('discogs', '20261101', ARRAY['extraction_complete'])"
            )
            await owner.execute("INSERT INTO artists (hash, data_id, data) VALUES ('h', 'a1', '{}'::jsonb)")

            reader = await _connect(dbname, user=role, password=password)
            async with reader:
                # The one thing the grant exists for: the job's `_LATCH_SQL` read.
                cursor = await reader.execute("SELECT loader, version, signals FROM public.loader_extraction_latch")
                assert await cursor.fetchall() == [("discogs", "20261101", ["extraction_complete"])]

                # Read-only on the latch: every write is refused.
                with pytest.raises(errors.InsufficientPrivilege):
                    await reader.execute("UPDATE public.loader_extraction_latch SET signals = '{}'")
                with pytest.raises(errors.InsufficientPrivilege):
                    await reader.execute("DELETE FROM public.loader_extraction_latch")
                with pytest.raises(errors.InsufficientPrivilege):
                    await reader.execute("INSERT INTO public.loader_extraction_latch (loader, version) VALUES ('musicbrainz', '20261101')")
                with pytest.raises(errors.InsufficientPrivilege):
                    await reader.execute("TRUNCATE public.loader_extraction_latch")

                # Nothing else new in `public`: the catalog table is not readable.
                with pytest.raises(errors.InsufficientPrivilege):
                    await reader.execute("SELECT * FROM public.artists")
        finally:
            # Privileges granted to the login role live in this database; clear them before dropping it.
            async with await _connect(dbname) as cleanup:
                await cleanup.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
            await owner.execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))

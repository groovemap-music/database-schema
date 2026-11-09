"""Apply this checkout's full PostgreSQL schema to a disposable database.

Usage: apply.py "<libpq DSN of a disposable database>"
"""

from __future__ import annotations

import asyncio
import sys

import psycopg

from groovemap_schema.postgres import _execute_schema_statements, _schema_statements


async def main(dsn: str) -> None:
    async with await psycopg.AsyncConnection.connect(dsn, autocommit=True) as conn, conn.cursor() as cursor:
        _success, failures = await _execute_schema_statements(cursor, _schema_statements())
    if failures:
        raise SystemExit(f"{failures} schema statements failed")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1]))

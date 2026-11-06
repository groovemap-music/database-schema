"""`graph.strip_person_name` is `common.person_names.strip_person_name`, proved on a real PostgreSQL.

A bootstrap fill that kept a name's padding would re-create the padded Person rows the loaders and
the enricher no longer write, and the stores would split on the key. Every code point is checked
against the Python rule, not a sample, and the fill is run on credits padded with the characters
the services were measured to carry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import uuid4

import psycopg
import pytest
import pytest_asyncio
from common.person_names import _is_padding, strip_person_name
from psycopg import sql
from psycopg.types.json import Jsonb

from groovemap_schema import initializer
from groovemap_schema.postgres import _execute_schema_statements, _schema_statements


if TYPE_CHECKING:
    from collections.abc import AsyncIterator


pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

# Names a credit really carries, padded with ASCII and Unicode whitespace, controls and format marks,
# interior runs and marks that must survive, and names that are nothing but padding.
NAMES = [
    "Alice",
    " Alice ",
    "\tAlice\n",
    "\N{NO-BREAK SPACE}Alice\N{NO-BREAK SPACE}",
    "\N{ZERO WIDTH SPACE}Alice\N{RIGHT-TO-LEFT MARK}",
    "\N{ZERO WIDTH NO-BREAK SPACE}Alice\N{ZERO WIDTH NO-BREAK SPACE}",
    "\N{IDEOGRAPHIC SPACE}Alice\N{IDEOGRAPHIC SPACE}",
    "\N{LINE SEPARATOR}Alice\N{PARAGRAPH SEPARATOR}",
    "\N{SOFT HYPHEN}Alice\N{SOFT HYPHEN}",
    "A  B",
    "A\N{RIGHT-TO-LEFT MARK}B",
    " A  B\N{ZERO WIDTH SPACE} ",
    "\N{CANCEL TAG}Alice\N{TAG LATIN SMALL LETTER A}",
    "Zo\N{LATIN SMALL LETTER E WITH DIAERESIS}",
    "\N{ZERO WIDTH NON-JOINER}",
    "   ",
    "\N{ZERO WIDTH SPACE}\N{ZERO WIDTH NON-JOINER}\N{ZERO WIDTH JOINER}",
    "",
]


def _params(dbname: str) -> dict[str, Any]:
    return {**initializer._postgres_connection_params(), "dbname": dbname}


async def _rows(dbname: str, query: Any, parameters: Any = None) -> list[tuple[Any, ...]]:
    async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as connection:
        cursor = await connection.execute(query, parameters)
        return await cursor.fetchall() if cursor.description else []


@pytest_asyncio.fixture
async def database() -> AsyncIterator[str]:
    dbname = f"person_strip_{uuid4().hex[:12]}"
    await _rows("postgres", sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
    try:
        async with await psycopg.AsyncConnection.connect(**_params(dbname), autocommit=True) as connection, connection.cursor() as cursor:
            _success, failures = await _execute_schema_statements(cursor, _schema_statements())
        assert failures == 0
        yield dbname
    finally:
        await _rows("postgres", sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname)))


async def test_the_sql_rule_agrees_with_python_on_every_code_point(database: str) -> None:
    padding = [code_point for code_point in range(1, 0x110000) if not 0xD800 <= code_point <= 0xDFFF and _is_padding(chr(code_point))]
    assert all(strip_person_name(f"{chr(c)}x{chr(c)}") == "x" for c in padding)

    differing = await _rows(
        database,
        """
        SELECT code_point
        FROM generate_series(1, 1114111) AS code_point
        WHERE code_point NOT BETWEEN 55296 AND 57343
          AND (graph.strip_person_name(chr(code_point) || 'x' || chr(code_point)) = 'x') <> (code_point = ANY(%s))
        """,
        (padding,),
    )
    print(f"\nCODE POINTS: {1_112_063 - 1} checked, {len(padding)} are padding, {len(differing)} differ between SQL and Python")
    assert differing == []


@pytest.mark.parametrize("name", NAMES)
async def test_the_sql_rule_returns_exactly_what_python_returns(database: str, name: str) -> None:
    assert await _rows(database, "SELECT graph.strip_person_name(%s)", (name,)) == [(strip_person_name(name),)]


async def test_null_stays_null_and_the_interior_is_never_touched(database: str) -> None:
    assert await _rows(database, "SELECT graph.strip_person_name(NULL)") == [(None,)]
    assert await _rows(database, "SELECT graph.strip_person_name(%s)", ("​A ‏ B​",)) == [("A ‏ B",)]


async def test_a_bootstrap_fill_keys_people_exactly_as_the_loaders_do(database: str) -> None:
    credits = [{"name": name, "role": "Producer", "id": str(index)} for index, name in enumerate(NAMES, start=10)]
    track = {"position": "A1", "title": "T", "extraartists": {"artist": [{"name": " Track Person​", "role": "Mixed By", "id": "99"}]}}
    document = {"title": "Padded", "extraartists": credits, "tracklist": {"track": [track]}}
    await _rows(database, "INSERT INTO releases (data_id, hash, data) VALUES ('111', 'h', %s)", (Jsonb(document),))

    await _rows(database, "SELECT * FROM graph.bootstrap_fill()")

    expected = sorted({strip_person_name(name) for name in [*NAMES, " Track Person​"] if strip_person_name(name)})
    people = sorted(name for (name,) in await _rows(database, "SELECT name FROM graph.person"))
    print(f"\nFILL: {len(people)} Person keys from {len(NAMES) + 1} padded credits; Python rule expects {len(expected)}; equal: {people == expected}")
    assert people == expected
    assert all(name == strip_person_name(name) and name for name in people)
    assert sorted(name for (name,) in await _rows(database, "SELECT DISTINCT person_name FROM graph.credited_on")) == [
        name for name in expected if name != "Track Person"
    ]
    assert await _rows(database, "SELECT person_name FROM graph.track_credited_on") == [("Track Person",)]
    assert await _rows(database, "SELECT count(*) FROM graph.same_as WHERE person_name <> graph.strip_person_name(person_name)") == [(0,)]

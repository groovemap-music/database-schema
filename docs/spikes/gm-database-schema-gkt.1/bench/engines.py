"""The two engine adapters.

Throwaway spike harness for gm-database-schema-gkt.1.

Both adapters hold ONE connection open for the whole run and issue every
iteration on it. That is not a convenience. gm-database-schema-9c8.3 measured
through `docker exec psql` and `docker exec cypher-shell`, one process per
statement, which is fine when the cheapest number in the table is 11 ms and
fatal here: the vertex-at-a-time search answers several cases in single-digit
milliseconds and a process spawn is tens of milliseconds of noise on top. The
figure both adapters report is server-side execution time, which is the same
quantity on both sides and the same quantity the sibling spikes reported.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

import psycopg
from neo4j import GraphDatabase

from .workloads import (
    EXPLORE_ROW_LIMIT,
    PATH_VARIANTS,
    Workload,
    cases_for,
    explore_start_for,
)


if TYPE_CHECKING:
    from collections.abc import Iterator


#: `_PATH_REL_TYPES` from api/queries/neo4j_queries.py:83, verbatim.
PATH_REL_TYPES: Final = "BY|ON|IS|ALIAS_OF|MEMBER_OF|DERIVED_FROM"


@dataclass
class Answer:
    """What a workload returned, as opposed to how long it took.

    ``found``/``depth`` are the shortest-path answer; ``rows`` is the explore
    answer as a set of ``(id, type, dist)`` triples. The path itself is NOT part
    of the comparison and the document says why: `shortestPath` returns one
    shortest path and breaks ties arbitrarily, so two correct engines routinely
    return different four-hop paths for the same pair.
    """

    found: bool | None = None
    depth: int | None = None
    rows: frozenset[tuple[str, str, int]] = frozenset()
    work: dict[str, Any] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.work is None:
            self.work = {}


class PostgresEngine:
    """PostgreSQL 19, running the PL/pgSQL searches in `sql/`."""

    name = "postgres"

    def __init__(self, dsn: str, statement_timeout_s: int, scale: str) -> None:
        self._cases = cases_for(scale)
        self._explore_start = explore_start_for(scale)
        self._conn = psycopg.connect(dsn, autocommit=True)
        with self._conn.cursor() as cur:
            cur.execute("SELECT set_config('statement_timeout', %s, false)", (f"{statement_timeout_s}s",))
            # `pf.seen` is truncated per call, so its statistics never describe
            # what is in it. The searches force their own access paths through
            # the shape of their queries; this only keeps the planner from
            # spending time on an estimate it cannot make.
            cur.execute("SELECT set_config('plan_cache_mode', 'auto', false)")

    def configure(self, workload: Workload) -> None:
        """Apply this workload's own statement budget before it is measured."""
        with self._conn.cursor() as cur:
            cur.execute("SELECT set_config('statement_timeout', %s, false)", (f"{workload.timeout_s}s",))

    def close(self) -> None:
        self._conn.close()

    def version(self) -> str:
        with self._conn.cursor() as cur:
            cur.execute("SELECT version()")
            row = cur.fetchone()
        return str(row[0]) if row else "unknown"

    def settings(self) -> dict[str, str]:
        keys = (
            "shared_buffers",
            "effective_cache_size",
            "work_mem",
            "maintenance_work_mem",
            "max_parallel_workers_per_gather",
            "max_parallel_workers",
            "random_page_cost",
            "jit",
            "server_version",
        )
        out: dict[str, str] = {}
        with self._conn.cursor() as cur:
            for key in keys:
                cur.execute("SELECT current_setting(%s)", (key,))
                row = cur.fetchone()
                out[key] = str(row[0]) if row else ""
        return out

    # -- statement construction --------------------------------------------
    #
    # The function name is looked up in a closed dictionary in `workloads.py`,
    # never taken from input, so the interpolation below cannot carry anything a
    # caller chose. Endpoints and depth are bound parameters.

    def _statement(self, workload: Workload) -> tuple[str, tuple[Any, ...]]:
        if workload.kind == "path":
            fn = PATH_VARIANTS[workload.variant]
            case = self._cases[workload.case]
            sql = f"SELECT found, depth, expanded, probes, seen_rows, levels FROM {fn}(%s, %s, %s, %s, %s)"  # noqa: S608
            return sql, (case.from_kind, case.from_key, case.to_kind, case.to_key, workload.depth)
        fn = {"pf-explore": "pf.explore", "pf-explore-no-is": "pf.explore_no_is"}[workload.variant]
        kind, key, _ = self._explore_start
        sql = f"SELECT id, type, dist FROM {fn}(%s, %s, %s, %s)"  # noqa: S608
        return sql, (kind, key, workload.depth, EXPLORE_ROW_LIMIT)

    def _answer_statement(self, workload: Workload) -> tuple[str, tuple[Any, ...]]:
        """The same workload run for its ANSWER rather than its latency.

        For explore that means the unbounded discovery set. `LIMIT 100` over a
        distance band that holds several hundred qualifying vertices returns an
        arbitrary hundred on either engine, so comparing two arbitrary hundreds
        would prove nothing; comparing the whole sets proves what the acceptance
        criterion is actually asking about.
        """
        if workload.kind == "path":
            return self._statement(workload)
        fn = {"pf-explore": "pf.explore", "pf-explore-no-is": "pf.explore_no_is"}[workload.variant]
        kind, key, _ = self._explore_start
        sql = f"SELECT id, type, dist FROM {fn}(%s, %s, %s, NULL)"  # noqa: S608
        return sql, (kind, key, workload.depth)

    # -- measurement --------------------------------------------------------

    def run_once(self, workload: Workload) -> float:
        sql, params = self._statement(workload)
        with self._conn.cursor() as cur:
            started = time.perf_counter_ns()
            cur.execute(sql, params, prepare=True)
            cur.fetchall()
            return (time.perf_counter_ns() - started) / 1_000_000

    def answer(self, workload: Workload) -> Answer:
        sql, params = self._answer_statement(workload)
        with self._conn.cursor() as cur:
            cur.execute(sql, params)
            rows = cur.fetchall()
        if workload.kind == "path":
            found, depth, expanded, probes, seen_rows, levels = rows[0]
            return Answer(
                found=bool(found),
                depth=depth,
                work={"expanded": expanded, "probes": probes, "seen_rows": seen_rows, "levels": levels},
            )
        return Answer(rows=frozenset((str(r[0]), str(r[1]), int(r[2])) for r in rows))

    def plan(self, workload: Workload) -> str:
        """`EXPLAIN (ANALYZE, BUFFERS)` over the whole search.

        Buffer accounting in PostgreSQL is a process-global counter that the
        executor takes deltas of around each node, so the statements the PL/pgSQL
        body runs internally ARE included in the top node's totals. That is what
        makes this the index-access figure the bead asks for rather than a
        measurement of the function-call node alone.
        """
        sql, params = self._statement(workload)
        with self._conn.cursor() as cur:
            cur.execute("EXPLAIN (ANALYZE, BUFFERS, VERBOSE) " + sql, params)
            return "\n".join(str(r[0]) for r in cur.fetchall())

    def buffers(self, workload: Workload) -> dict[str, int]:
        text = self.plan(workload)
        out: dict[str, int] = {}
        match = re.search(r"Buffers: shared hit=(\d+)(?: read=(\d+))?(?: dirtied=(\d+))?(?: written=(\d+))?", text)
        if match:
            out["shared_hit"] = int(match.group(1))
            out["shared_read"] = int(match.group(2) or 0)
            out["shared_dirtied"] = int(match.group(3) or 0)
            out["shared_written"] = int(match.group(4) or 0)
        out["total_accesses"] = out.get("shared_hit", 0) + out.get("shared_read", 0)
        return out


class Neo4jEngine:
    """Neo4j, running `find_shortest_path` and `get_explore_traversal` verbatim."""

    name = "neo4j"

    def __init__(self, uri: str, user: str, password: str, timeout_s: int, scale: str) -> None:
        self._cases = cases_for(scale)
        self._explore_start = explore_start_for(scale)
        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._driver.verify_connectivity()
        self._timeout = timeout_s

    def configure(self, workload: Workload) -> None:
        self._timeout = workload.timeout_s

    def close(self) -> None:
        self._driver.close()

    def version(self) -> str:
        with self._driver.session() as session:
            rec = session.run("CALL dbms.components() YIELD name, versions, edition RETURN name, versions, edition").single()
        return f"{rec['name']} {rec['versions'][0]} {rec['edition']}" if rec else "unknown"

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        with self._driver.session() as session:
            for rec in session.run("MATCH (n) RETURN labels(n)[0] AS label, count(*) AS n"):
                out[f"node:{rec['label']}"] = rec["n"]
            for rec in session.run("MATCH ()-[r]->() RETURN type(r) AS t, count(*) AS n"):
                out[f"rel:{rec['t']}"] = rec["n"]
        return out

    # -- statement construction --------------------------------------------
    #
    # Both depth values are interpolated rather than bound, because Cypher does
    # not accept a parameter inside a variable-length pattern's bound. That is
    # the reason both product functions clamp server-side, and the reason these
    # two strings are templates. The interpolated values are ints from a closed
    # set in `workloads.py`.

    def _path_cypher(self, workload: Workload) -> tuple[str, dict[str, Any]]:
        case = self._cases[workload.case]
        cypher = f"""
        MATCH (a:{case.from_label} {{id: $from_id}}), (b:{case.to_label} {{id: $to_id}})
        MATCH p = shortestPath((a)-[:{PATH_REL_TYPES}*..{int(workload.depth)}]-(b))
        RETURN [node IN nodes(p) | {{
                   id: coalesce(node.id, node.name),
                   name: coalesce(node.name, node.title, ''),
                   labels: labels(node)
               }}] AS nodes,
               [rel IN relationships(p) | type(rel)] AS rels
        """
        return cypher, {"from_id": case.from_key, "to_id": case.to_key}

    def _explore_cypher(self, workload: Workload, *, limit: int | None) -> tuple[str, dict[str, Any]]:
        _, key, label = self._explore_start
        tail = f"LIMIT {int(limit)}" if limit is not None else ""
        cypher = f"""
        MATCH (start:{label} {{id: $entity_id}})
        MATCH path = (start)-[:{PATH_REL_TYPES}*1..{int(workload.depth)}]-(discovered)
        WHERE discovered <> start
          AND (discovered:Artist OR discovered:Label OR discovered:Genre OR discovered:Style)
        WITH discovered,
             [n IN nodes(path) | coalesce(n.name, n.title, n.id)] AS path_names,
             [r IN relationships(path) | type(r)] AS rel_types,
             length(path) AS dist
        ORDER BY dist
        WITH discovered, collect({{path_names: path_names, rel_types: rel_types, dist: dist}})[0] AS best
        RETURN coalesce(discovered.id, discovered.name) AS id,
               coalesce(discovered.name, discovered.id) AS name,
               CASE
                 WHEN discovered:Artist THEN 'artist'
                 WHEN discovered:Label THEN 'label'
                 WHEN discovered:Genre THEN 'genre'
                 WHEN discovered:Style THEN 'style'
               END AS type,
               best.path_names AS path_names, best.rel_types AS rel_types, best.dist AS dist
        ORDER BY best.dist
        {tail}
        """
        return cypher, {"entity_id": key}

    def _statement(self, workload: Workload) -> tuple[str, dict[str, Any]]:
        if workload.kind == "path":
            return self._path_cypher(workload)
        return self._explore_cypher(workload, limit=EXPLORE_ROW_LIMIT)

    def run_once(self, workload: Workload) -> float:
        cypher, params = self._statement(workload)
        with self._driver.session() as session:
            started = time.perf_counter_ns()
            result = session.run(cypher, params, timeout=self._timeout)
            result.consume()
            return (time.perf_counter_ns() - started) / 1_000_000

    def answer(self, workload: Workload) -> Answer:
        if workload.kind == "path":
            cypher, params = self._path_cypher(workload)
            with self._driver.session() as session:
                rec = session.run(cypher, params, timeout=self._timeout).single()
            if rec is None:
                return Answer(found=False, depth=None)
            return Answer(found=True, depth=len(rec["rels"]))
        cypher, params = self._explore_cypher(workload, limit=None)
        with self._driver.session() as session:
            rows = [(str(r["id"]), str(r["type"]), int(r["dist"])) for r in session.run(cypher, params, timeout=self._timeout)]
        return Answer(rows=frozenset(rows))

    def plan(self, workload: Workload) -> str:
        cypher, params = self._statement(workload)
        with self._driver.session() as session:
            summary = session.run("PROFILE " + cypher, params, timeout=self._timeout).consume()
        return _render_profile(summary.profile) if summary.profile else ""

    def buffers(self, workload: Workload) -> dict[str, int]:
        """Neo4j's equivalent of a buffer count is its database-access count."""
        cypher, params = self._statement(workload)
        with self._driver.session() as session:
            summary = session.run("PROFILE " + cypher, params, timeout=self._timeout).consume()
        return {"total_accesses": _db_hits(summary.profile) if summary.profile else 0}


def _db_hits(node: dict[str, Any]) -> int:
    return int(node.get("dbHits", 0)) + sum(_db_hits(child) for child in node.get("children", ()))


def _render_profile(node: dict[str, Any], depth: int = 0) -> str:
    return "\n".join(_render_lines(node, depth))


def _render_lines(node: dict[str, Any], depth: int) -> Iterator[str]:
    pad = "  " * depth
    yield f"{pad}+{node.get('operatorType', '?')}  rows={node.get('rows', 0)}  dbHits={node.get('dbHits', 0)}"
    args = node.get("args") or {}
    if "Details" in args:
        yield f"{pad}   {args['Details']}"
    for child in node.get("children", ()):
        yield from _render_lines(child, depth + 1)

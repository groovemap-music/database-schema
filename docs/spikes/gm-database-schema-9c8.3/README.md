# gm-database-schema-9c8.3 — spike scaffolding

Throwaway measurement harness for
[`../gm-database-schema-9c8.3-variable-length-paths.md`](../gm-database-schema-9c8.3-variable-length-paths.md).

Nothing here ships. No product code imports it, `groovemap_schema` does not know
it exists, and the only schema it creates lives in its own `path` namespace
beside the shipped `graph` schema and the sibling spike's `graph_mat`.

It is a **layer on top of** `gm-database-schema-9c8.1`, not a replacement for it.
That spike's generator, loader and materialized edge relations are reused
unmodified; this one adds the two edge relations the path set needs and which
9c8.1 had no reason to build, one undirected traversal surface over all of them,
and the search algorithms.

## Files

| File | What it is |
| --- | --- |
| `artist_edges.py` | Replays 9c8.1's generator and emits the Discogs artist-to-artist `ALIAS_OF` / `MEMBER_OF` edges it writes only to its Neo4j target. |
| `stage.sql` | Creates the `path` schema and the staging table `artist_edges.py` is streamed into. |
| `augment.sql` | Writes the `aliases` and `groups` blocks into the artist documents, then builds `path.alias_of`, `path.member_of` and `path.mb_member_of` by reading the **shipped** views back. |
| `edges.sql` | `path.edge`, the undirected six-type traversal surface: twenty-two branches over ten relations, both directions, no rows stored. |
| `bfs.sql` | `path.shortest_uni`, `path.shortest_bi` and `path.traverse` — level-synchronous BFS with a global visited set, over `path.edge`. |
| `adjacency.sql` | `path.node` and `path.adj`, the dense `bigint` precomputed adjacency, and the same two searches over it. |
| `load-path-edges.sh` | Runs all of the above against a catalog 9c8.1 has already loaded. |
| `run-postgres.sh` | Measures the prototypes. Endpoints are hardcoded, not chosen at run time. |
| `run-neo4j.sh` | Builds the Neo4j baseline and measures `shortestPath` and the explore traversal, keeping every `PROFILE`. |
| `explain-postgres.sh` | Captures the plans behind the timings: `EXPLAIN (ANALYZE, BUFFERS)` for the recursive CTE, and `auto_explain` with `log_nested_statements` for the per-level expansion inside the PL/pgSQL searches. |
| `summarize.sh` | Collapses a results directory into the medians the spike document reports. |
| `results/` | The captured evidence, kept. See [`results/README.md`](results/README.md). |
| `queries/p1-naive-recursive-cte.sql` | `shortestPath` translated literally into `WITH RECURSIVE`. |
| `queries/p2-shortest-uni.sql`, `p3-shortest-bi.sql`, `p5-traverse.sql` | Call sites for the three searches in `bfs.sql`. |
| `queries/shortest-path.cypher`, `explore-traversal.cypher` | `find_shortest_path` and `get_explore_traversal`, verbatim from catalog-api, with the two values those functions interpolate left as template slots. |

## The catalog

The same one, from the same fixed seed `20260917`. Scales, row counts and
regeneration are in
[`../gm-database-schema-9c8.1/README.md`](../gm-database-schema-9c8.1/README.md);
the edge breakdown is in that spike's document. This spike adds 22,451 `ALIAS_OF`
and 58,766 `MEMBER_OF` undirected edges that 9c8.1 loaded into Neo4j and not into
PostgreSQL — `augment.sql` explains at length why they cannot be left out and why
they have to come from the generator's own stream rather than be invented.

The resulting traversal surface, at the synthetic scale:

| Relationship type | Directed rows in `path.edge` |
| --- | --- |
| `IS` | 9,775,586 |
| `BY` | 4,640,924 |
| `ON` | 2,658,188 |
| `DERIVED_FROM` | 1,400,776 |
| `MEMBER_OF` | 117,532 |
| `ALIAS_OF` | 44,902 |
| **Total** | **18,637,908** |

over **1,390,455** vertices. Each undirected edge appears twice, once per
direction, so that is 9,318,954 edges.

## Endpoints

Hardcoded in `run-postgres.sh` and `run-neo4j.sh`, not chosen by degree at run
time — 9c8.1 selected its seeds with `ORDER BY degree DESC OFFSET n` and no
tiebreak, so a rerun there could silently measure a different vertex. The rule
that produced these, and the reason there is no case beyond distance 6, are in
the spike document's Method section.

| Case | From | To | Distance |
| --- | --- | --- | --- |
| `d1-artist-artist` | artist 5665 | artist 9458 | 1 |
| `d1-artist-release` | artist 5665 | release 3638 | 1 |
| `d2-artist-artist` | artist 5665 | artist 1 | 2 |
| `d3-artist-artist` | artist 5665 | artist 2 | 3 |
| `d4-artist-artist` | artist 5665 | artist 9 | 4 |
| `d5-artist-artist` | artist 55563 | artist 4814 | 5 |
| `d6-artist-artist` | artist 55563 | artist 32509 | 6 |
| `unreachable` | artist 5665 | artist 103111 | none |

## Reproducing

Requires Docker, and about 12 GB of free disk if the two engines are built one
after the other as below. They do not fit at once.

```bash
# 1-3. Build the catalog exactly as gm-database-schema-9c8.1 documents, with the
#      container named gm9c83-pg and published on 55433 so the two spikes can be
#      reproduced side by side. Note the volume mount: postgres:19 wants
#      /var/lib/postgresql, not /var/lib/postgresql/data.
docker volume create gm9c83-pgdata
docker run --detach --name gm9c83-pg \
  --publish 127.0.0.1:55433:5432 --volume gm9c83-pgdata:/var/lib/postgresql \
  --env POSTGRES_USER=groovemap --env POSTGRES_PASSWORD=spike-password \
  --env POSTGRES_DB=postgres --shm-size=1g \
  postgres:19beta3-alpine@sha256:b1692e50613a21e61c424859f943b9e193ae73e5a8c68abd5382dfb235bf15fc \
  -c shared_buffers=1536MB -c effective_cache_size=4GB -c work_mem=128MB \
  -c maintenance_work_mem=512MB -c max_parallel_workers_per_gather=2 \
  -c max_parallel_workers=2 -c random_page_cost=1.1 -c track_io_timing=on -c jit=off

SCHEMA_PROPERTY_GRAPH=enabled uv run python -c "
import asyncio
from groovemap_schema import initializer
params = {'host': '127.0.0.1', 'port': 55433, 'dbname': 'groovemap',
          'user': 'groovemap', 'password': 'spike-password'}
initializer._ensure_postgres_database(params)
raise SystemExit(0 if asyncio.run(initializer._apply_postgres_schema(params)) else 1)
"

PG_CONTAINER=gm9c83-pg ../gm-database-schema-9c8.1/load-postgres.sh synthetic

# 4. Add the path edge surface and the searches.
PG_CONTAINER=gm9c83-pg ./load-path-edges.sh synthetic

# 5. Measure. About an hour: the exhaustive cases are two minutes each.
PG_CONTAINER=gm9c83-pg ./run-postgres.sh /tmp/gm9c83-results
PG_CONTAINER=gm9c83-pg ./explain-postgres.sh /tmp/gm9c83-results

# 6. Reclaim the disk before the second engine. Neo4j and PostgreSQL do not fit
#    at the same time, and each was measured with the other stopped anyway so
#    neither competes for the page cache of the other.
docker rm --force gm9c83-pg && docker volume rm gm9c83-pgdata

# 7. Measure Neo4j.
./run-neo4j.sh synthetic /tmp/gm9c83-results ~/.cache/gm9c83-spike/csv-synthetic

# 8. Collapse both into the tables in the spike document.
./summarize.sh /tmp/gm9c83-results
```

The CSV directory is staged into a Docker volume rather than bind-mounted,
because a bind mount only works from a path Docker Desktop shares and the
importer sees an empty `/import` otherwise.

## Cleaning up

```bash
docker rm --force gm9c83-pg gm9c83-neo4j
docker volume rm gm9c83-pgdata gm9c83-neo4j-data
rm -rf ~/.cache/gm9c83-spike
```

Inside a database that is being kept, `DROP SCHEMA path CASCADE` removes
everything this spike created and leaves 9c8.1's `graph_mat` and the shipped
`graph` schema untouched — except for the `aliases` and `groups` blocks
`augment.sql` writes into `public.artists`, which
`UPDATE public.artists SET data = data - 'aliases' - 'groups' - 'members'`
removes.

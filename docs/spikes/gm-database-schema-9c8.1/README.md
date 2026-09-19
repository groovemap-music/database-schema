# gm-database-schema-9c8.1 — spike scaffolding

Throwaway measurement harness for
[`../gm-database-schema-9c8.1-graph-table-performance.md`](../gm-database-schema-9c8.1-graph-table-performance.md).

Nothing here ships. No product code imports it, `groovemap_schema` does not know
it exists, and the only schema it creates lives in its own `graph_mat` namespace
beside the shipped `graph` schema. It is checked in so the numbers in the spike
document can be reproduced and so the sibling spike **gm-database-schema-9c8.3**
can rebuild the identical graph for its shortest-path prototypes.

## Files

| File | What it is |
| --- | --- |
| `generate.py` | Deterministic catalog generator. Emits PostgreSQL `\copy` CSV or Neo4j bulk-import CSV from one record stream. |
| `materialize.sql` | Throwaway DDL. Builds `graph_mat` — materialized vertex and edge relations with indexes — and declares a second property graph over them. |
| `load-postgres.sh` | Streams one scale into the running PostgreSQL container, then runs `materialize.sql`. |
| `run-postgres.sh` | Measures the three reads against both PostgreSQL declarations. |
| `run-neo4j.sh` | Generates, bulk-imports, and measures the Neo4j baseline. |
| `summarize.sh` | Turns a results directory into the timing table in the spike document. |
| `queries/*.sql` | The three reads ported to `GRAPH_TABLE`. One text per read, run against either declaration. |
| `queries/*.cypher` | The same three reads, verbatim from catalog-api. |

## Scales

Both are defined in `generate.py` and are reproduced exactly by the fixed seed
`20260917`. The row counts below are what the generator actually produces; the
edge totals are measured from the database after loading, not predicted.

| | `fixture` | `synthetic` |
| --- | --- | --- |
| Discogs releases | 5,000 | 1,000,000 |
| Discogs artists | 1,500 | 120,000 |
| Discogs labels | 300 | 20,000 |
| Discogs masters | 1,800 | 250,000 |
| MusicBrainz artists | 800 | 60,000 |
| MusicBrainz relationships (generated) | 4,000 | 300,000 |

Cardinality per release is drawn from fixed weights, and artist and label
references are drawn from a power-law so a few entities carry a large share of
the catalog. That skew is deliberate: under uniform assignment a two-hop
collaborator expansion is uniformly cheap and hides the cost the spike exists to
measure. The means the weights imply are about 2.0 artists, 1.3 labels, 1.7
genres and 2.3 styles per release, with 70% of releases carrying a master.

## Determinism

Every attribute is a pure function of `(seed, salt, entity id)` through a
SplitMix64 stream, not a position in a `random` sequence. Three consequences
matter:

- The PostgreSQL and Neo4j targets cannot drift. They walk the same records in
  the same order, so a difference in a measured timing is a difference in engine,
  never a difference in data.
- A single table can be regenerated on its own. `--table releases` does not have
  to replay a hundred thousand artists first.
- Nothing is held in memory. A million release documents stream to stdout.

Re-running any command below with the same `--seed` reproduces the same bytes.

## Reproducing

Requires Docker. The PostgreSQL image is the digest the repository's
`test-integration-pg19` tier pins and the Neo4j image is the digest
`scripts/test-integration.sh` pins, so the engines are the ones this project
already runs.

```bash
# 1. PostgreSQL 19 beta 3, with the property graph switch on.
docker run --detach --name gm9c81-pg \
  --publish 127.0.0.1:55432:5432 \
  --env POSTGRES_USER=groovemap --env POSTGRES_PASSWORD=spike-password \
  --env POSTGRES_DB=postgres --shm-size=1g \
  postgres:19beta3-alpine@sha256:b1692e50613a21e61c424859f943b9e193ae73e5a8c68abd5382dfb235bf15fc \
  -c shared_buffers=1536MB -c effective_cache_size=4GB -c work_mem=128MB \
  -c maintenance_work_mem=512MB -c max_parallel_workers_per_gather=2 \
  -c max_parallel_workers=2 -c random_page_cost=1.1 -c track_io_timing=on -c jit=off

# 2. Apply the product schema. The packaged CLI writes to /logs, which does not
#    exist outside the container image, so the schema is applied the way the
#    integration suite applies it.
SCHEMA_PROPERTY_GRAPH=enabled uv run python -c "
import asyncio
from groovemap_schema import initializer
params = {'host': '127.0.0.1', 'port': 55432, 'dbname': 'groovemap',
          'user': 'groovemap', 'password': 'spike-password'}
initializer._ensure_postgres_database(params)
raise SystemExit(0 if asyncio.run(initializer._apply_postgres_schema(params)) else 1)
"

# 3. Load a scale and build the materialized declaration beside the shipped one.
./load-postgres.sh synthetic

# 4. Measure both PostgreSQL declarations. Prints the chosen seed entities.
./run-postgres.sh synthetic /tmp/gm9c81-results

# 5. Measure Neo4j, with PostgreSQL stopped so neither engine is competing for
#    the page cache of the other. The seed ids are the ones step 4 printed.
#    The CSV directory must be somewhere Docker is allowed to bind-mount.
docker stop gm9c81-pg
./run-neo4j.sh synthetic /tmp/gm9c81-results ~/.cache/gm9c81-spike/csv-synthetic <seed_artist> <seed_label>

# 6. Collapse the results into the table in the spike document.
./summarize.sh /tmp/gm9c81-results synthetic
```

Seed entities are chosen by degree rather than hardcoded, so the same script
picks a representative read at either scale, and `run-postgres.sh` prints which
ids it chose. The artist is at the 90th percentile of release degree among
artists that also carry a MusicBrainz relationship; the label is the busiest one.

## Cleaning up

```bash
docker rm --force gm9c81-pg gm9c81-neo4j
docker volume rm gm9c81-neo4j-data
rm -rf ~/.cache/gm9c81-spike
```

Inside a database that is being kept, `DROP PROPERTY GRAPH graph_mat.catalog;
DROP SCHEMA graph_mat CASCADE;` removes everything this spike created. The
shipped `graph` schema is never modified.

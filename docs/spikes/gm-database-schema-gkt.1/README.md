# gm-database-schema-gkt.1 — spike scaffolding

Throwaway measurement harness for
[`../gm-database-schema-gkt.1-procedural-pathfinder.md`](../gm-database-schema-gkt.1-procedural-pathfinder.md).

Nothing here ships. No product code imports it, `groovemap_schema` does not know
it exists, and the only schema it creates lives in its own `pf` namespace beside
the shipped `graph` schema, gm-database-schema-9c8.1's `graph_mat` and
gm-database-schema-9c8.3's `path`.

It is a **layer on top of** those two spikes, not a replacement for either.
9c8.1's generator, loader and materialized edge relations are reused unmodified,
and so are 9c8.3's `artist_edges.py`, `stage.sql` and `augment.sql`, which
supply the artist-to-artist edges. What this spike adds is one traversal surface,
one search algorithm, and a benchmark harness with a cloud mode.

## What it is measuring

gm-database-schema-9c8.3 concluded that "Neo4j's shortest-path expander can stop
in the middle of a level and a SQL statement cannot". That is true of a SQL
statement and not true of a PL/pgSQL loop. This spike replaces the unit of work:
where 9c8.3 expanded a whole breadth-first level in one statement, this expands
**one vertex at a time**, checks after each one whether the other side of a
bidirectional search has already seen any of its neighbours, and returns at the
first touch. `sql/pathfinder.sql.in` carries the argument for why first touch is
the shortest path and not one hop too long.

## Files

| File | What it is |
| --- | --- |
| `run.sh` | The driver. Local Docker mode and IBM Cloud mode, both scales. `--help` for the modes. |
| `load.sh` | Builds the `pf` schema on a catalog 9c8.1 has loaded and 9c8.3's `augment.sql` has extended. |
| `neo4j-build.sh` | Imports the same generator output into Neo4j and starts it. Build only; the measuring is `bench/runner.py`. |
| `sql/state.sql` | `pf.seen`, the one request-scoped relation the searches need, and `pf.trace`. |
| `sql/edges.sql` | `pf.edge`, the undirected six-type surface, and `pf.edge_no_is` without the IS class. |
| `sql/degree.sql` | `pf.degree`, one bigint per vertex. The only precomputed structure any variant needs. |
| `sql/pathfinder.sql.in` | **A template.** `load.sh` instantiates it three times; see its header. |
| `sql/hstore.sql` | The same search with the seen set in a PL/pgSQL variable, for the Recommendation. |
| `sql/pick-endpoints.sql` | How the fixture-scale endpoint cases were chosen, so the choice can be audited. |
| `bench/workloads.py` | The cases, the variants, the iteration counts and the budgets, as data. |
| `bench/engines.py` | The two adapters. One connection per run, server-side timing on both sides. |
| `bench/runner.py` | The measurement loop: warm-up, iterations, p50/p95, answers, buffers, plans. |
| `bench/calibration.py` | What the machine can do. Standard library only, so it runs on a bare instance. |
| `bench/compare.py` | The answer check against Neo4j, and the Verdict gate evaluated rather than asserted. |
| `bench/report.py` | The tables the spike document quotes, generated rather than transcribed. |
| `infra/` | Terraform for IBM Cloud VPC. Two instances on one profile, SSH restricted to the driver. |
| `cloud/` | What runs ON an instance: common bootstrap, then one bootstrap per engine. |
| `results/` | The captured evidence, kept. See [`results/README.md`](results/README.md). |

## Where the harness shape comes from

`investigations/` on the `db-alternatives` branch of
`SimplicityGuy/discogsography`: hardware calibration, workloads declared as data
with their own iteration counts, a runner reporting p50/p95, a comparison step, a
generated report, `small` and `large` scale points, one script with a local
Docker mode and a cloud mode. Three things are deliberately different and the
spike document says so rather than leaving a reader to find out:

- That harness's cloud mode is Ansible against Hetzner Cloud. This one is
  **Terraform against IBM Cloud VPC**, which is what the bead asks for.
- That harness rescales measured latencies by a calibration factor and reports
  the result. It does this in two places with two mutually inconsistent
  conventions. **Nothing here is rescaled.** Every latency is one a machine named
  beside it actually took, and calibration is reported so a reader can compare
  the machines themselves.
- Its workloads are engine-agnostic because every engine it measures speaks a
  graph query language. Here one engine runs a PL/pgSQL function and the other
  runs Cypher, so a workload names a **case** and each adapter supplies its own
  statement for it.

## Scales

9c8.1's two, under the names the db-alternatives harness uses:

| | `small` (9c8.1 `fixture`) | `large` (9c8.1 `synthetic`) |
| --- | --- | --- |
| Discogs releases | 5,000 | 1,000,000 |
| Vertices in `pf.edge` | 9,041 | 1,390,455 |
| Directed rows in `pf.edge` | 100,126 | 18,637,908 |

## Endpoints

The large-scale cases are gm-database-schema-9c8.3's, hardcoded and unchanged so
the two spikes' tables can be read against each other. **The small scale needs
its own**, and that is a finding rather than a convenience: 9c8.3's ids are
synthetic-scale ids and the fixture catalog has 1,500 artists, so at that scale
every one of them is a lookup of a vertex that does not exist. A first run of
this harness measured eight misses in under five milliseconds each and would have
reported it as "the small scale is fast". `sql/pick-endpoints.sql` states the
rule and derives the fixture set; `bench/workloads.py` carries both tables.

## Reproducing

Requires Docker, and about 22 GB of free disk if both engines are built at the
large scale. `run.sh` refuses to start a build with less than 12 GB free.

```bash
# Everything, locally, at the large scale: build both engines, measure both,
# check the answers against Neo4j, render the tables.
./run.sh local large

# The same at the fixture scale.
./run.sh local small

# IBM Cloud, both scales, then tear down and verify the account is empty.
# Reads ~/.config/groovemap/ibmcloud.env. Nothing from it is echoed or written
# into infra/.
./run.sh --cloud

# Remove every container and volume this spike created. Nothing else is pruned.
./run.sh --clean
```

`run.sh --cloud-keep` leaves the instances up for inspection, and
`run.sh --cloud-destroy` then removes them and asks IBM Cloud — not the
Terraform state — whether anything named `gmgkt1-` survives.

## Cleaning up

```bash
./run.sh --cloud-destroy   # if a cloud run is outstanding
./run.sh --clean
```

`--clean` also deletes `infra/terraform.tfstate` once it lists no resources, and
that is not tidiness. The repository's `secret-scan` gate runs
`gitleaks dir .`, which scans the **working directory** rather than only tracked
files, and a destroyed deployment's state file still holds resource CRNs that
gitleaks reads as generic API keys. `.gitignore` keeps it out of every commit,
but leaving it on disk turns `just check` red. A state that still lists resources
is never deleted; `--clean` says so and stops.

Inside a database that is being kept, `DROP SCHEMA pf CASCADE` removes everything
this spike created and leaves 9c8.1's `graph_mat`, 9c8.3's `path` and the shipped
`graph` schema untouched.

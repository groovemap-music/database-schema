# Spike gm-database-schema-9c8.1 — GRAPH_TABLE read performance: views, materialized edges, Neo4j

Status: complete. Verdict below.

## Question

The foundation molecule (gm-database-schema-6z8) landed a `graph` schema of 52
views over the Discogs JSONB documents and the MusicBrainz tables, and a
version-gated `CREATE PROPERTY GRAPH graph.catalog` declared over those views.
Nothing has yet asked it to serve a read.

**Can the declared graph, as shipped, serve the hot fixed-length read families
that catalog-api serves from Neo4j today — and if not, are materialized edge
relations required, or does something cheaper suffice?**

Concretely, three questions the answer has to separate:

1. Do the graph views over JSONB hold up at catalog scale, or does the cost of
   re-deriving edges from documents on every read put them out of reach?
2. If materialization is needed, is it enough to materialize — matching Neo4j's
   access path — or does PostgreSQL remain structurally behind a native graph
   store for multi-hop reads?
3. If materialization is needed, which mechanism: a materialized view the
   database refreshes, or edge tables the loader writes?

## Method

### Environment

Every number below was produced on one machine, in one sitting, with both
engines pinned by digest to the images this repository's own integration tiers
use.

| | |
| --- | --- |
| Host | Apple M1 Pro, 8 performance + 2 efficiency cores, 32 GiB RAM |
| Host OS | macOS 26.6.2 (build 25G83) |
| Docker | Engine 29.5.2, Linux VM 6.8.0-117-generic, Ubuntu 24.04.4 LTS |
| Docker VM resources | **2 vCPU, 7.7 GiB RAM** |
| PostgreSQL | 19beta3 on aarch64-unknown-linux-musl, gcc (Alpine 15.2.0) |
| PostgreSQL image | `postgres:19beta3-alpine@sha256:b1692e50613a21e61c424859f943b9e193ae73e5a8c68abd5382dfb235bf15fc` (linux/arm64) |
| Neo4j | Kernel 2026.07.1, community edition, Cypher 5/25 |
| Neo4j image | `neo4j:2026-community@sha256:dbc377fb9cd8fe8dabc19d3041b197d5ca0ef8bae514cea175b8df265e5b7a76` (linux/arm64) |

The Docker VM's two vCPUs are the sharpest limit on these numbers and are worth
stating plainly. They depress every absolute figure here relative to a
production host, and they depress parallel plans hardest — PostgreSQL was
configured with `max_parallel_workers_per_gather=2` and could not have used more.
They do not distort the comparison, because all three backends were measured
under the identical constraint, and the spike's question is which backend, not
how many milliseconds.

PostgreSQL server settings, chosen once and held constant across every
measurement: `shared_buffers=1536MB`, `effective_cache_size=4GB`,
`work_mem=128MB`, `maintenance_work_mem=512MB`,
`max_parallel_workers_per_gather=2`, `max_parallel_workers=2`,
`random_page_cost=1.1`, `track_io_timing=on`, `jit=off`. Neo4j was given a 2 GiB
heap and a 2 GiB page cache. Each engine was measured with the other stopped, so
neither was competing for the other's page cache.

The product schema was applied by the shipped initializer with
`SCHEMA_PROPERTY_GRAPH=enabled`, producing all 52 `graph` views, both rendered
functions, and `graph.catalog` as a `relkind='g'` relation.

### Data

Two scales, both produced by `gm-database-schema-9c8.1/generate.py` from the
fixed seed `20260917`. Regeneration is documented in
[`gm-database-schema-9c8.1/README.md`](gm-database-schema-9c8.1/README.md); the
sibling spike gm-database-schema-9c8.3 can rebuild this exact graph for its
shortest-path prototypes.

Attributes are derived from a SplitMix64 stream keyed on
`(seed, salt, entity id)` rather than from a position in a `random` sequence, so
the PostgreSQL documents and the Neo4j nodes and relationships are the same
catalog rather than two catalogs of the same size, and a million release
documents stream to stdout without being collected.

Artist and label references are drawn from a power-law rather than uniformly. A
uniform catalog would make every two-hop expansion equally cheap and would hide
the cost this spike exists to measure; real Discogs degree is heavy-tailed, and
the seeds below sit on that tail.

| | fixture | synthetic |
| --- | --- | --- |
| Discogs releases | 5,000 | 1,000,000 |
| Discogs artists | 1,500 | 120,000 |
| Discogs labels | 300 | 20,000 |
| Discogs masters | 1,800 | 250,000 |
| MusicBrainz artists | 800 | 60,000 |
| MusicBrainz artist-to-artist relationships | 3,399 | 254,925 |
| **Total graph edges** | **52,406** | **9,492,662** |

Synthetic edge breakdown, counted from the database rather than predicted:

| Edge relation | Rows |
| --- | --- |
| `by_artist` | 1,920,257 |
| `in_style` | 2,265,063 |
| `in_genre` | 1,644,556 |
| `on_label` | 1,329,094 |
| `derived_from` | 700,388 |
| `master_in_style` | 567,024 |
| `master_in_genre` | 411,150 |
| `master_by_artist` | 400,205 |
| `mb_rel_artist_artist` | 254,925 |
| **Total** | **9,492,662** |

The synthetic scale therefore clears the bead's floor of one million releases
and five million edges with room to spare. `public.releases` occupies 1,096 MB
with its shipped indexes.

### Backends

All three run the same three reads over the same catalog.

**(a) `graph` views over JSONB, as shipped.** `GRAPH_TABLE (graph.catalog ...)`,
untouched.

**(b) The same graph over materialized edge relations.** A `graph_mat` schema of
plain tables populated by `INSERT ... SELECT` from the shipped views, each edge
relation indexed in **both** directions, and a second
`CREATE PROPERTY GRAPH graph_mat.catalog` declared over them with label names
identical to the shipped graph. One query text therefore runs against either
declaration with only the graph name changed, and reading the shipped views to
populate the tables proves the two declarations hold exactly the same rows — so
a difference in timing is a difference in access path and not in cardinality.

Key columns are `text` throughout. PostgreSQL 19 beta 3 cannot resolve an
equality operator for a `character varying` property-graph vertex key, which is
why the shipped Discogs vertex views carry appended `*_key` text columns;
declaring the materialized tables as `text` avoids needing that workaround twice.

**(c) Neo4j Cypher.** The same catalog bulk-imported with
`neo4j-admin database import full`, with lookup indexes created afterward on
`Artist.id`, `Label.id`, `Release.id`, `Master.id`, `Genre.name` and
`Style.name` — without them the baseline would be measuring a label scan of a
million nodes and would flatter PostgreSQL for the wrong reason. Neo4j's edges
are derived from the same generator output, by the rules the graph views encode.

### The three reads

Ported from catalog-api, read-only, at
`/Users/Robert/workspaces/github/groovemap-music/catalog-api/api/queries/`. The
Cypher is used verbatim for backend (c); the `GRAPH_TABLE` ports are in
[`gm-database-schema-9c8.1/queries/`](gm-database-schema-9c8.1/queries/).

**Q1 — multi-hop collaborators at depth 2** (`network_queries.py`,
`get_multi_hop_collaborators`). A one-hop leg and a two-hop leg unioned, the
two-hop leg anti-joined against the one-hop leg so a direct collaborator is never
re-reported at distance two, then `min(hops)`, `sum(shared)`, order, limit 50.
The name projection happens after the limit in both ports, as the Cypher does it.
This is the read family the whole spike turns on: it walks `by_artist` four times
in the two-hop leg alone.

**Q2 — label-to-genre aggregation, "label DNA"** (`label_dna_queries.py`,
`get_label_genre_profile`). Two edge families off one release, entered from the
label end, `count(DISTINCT release)` per genre. No limit — the genre vocabulary
is fifteen names, so the result is small however large the label is, and all the
cost is in reaching the label's releases.

**Q3 — artist MusicBrainz relationship listing** (`musicbrainz_queries.py`,
`get_artist_mb_relationships`). Both directions unioned, keyed on a Discogs
artist id.

Q3's shapes differ between the two systems in a way that is a property of the
data models, not of this spike, and it is why its numbers should be read
differently from Q1's and Q2's. In Neo4j one relationship space holds every
artist-to-artist edge from every provider, so `WHERE r.source = 'musicbrainz'` is
a filter the planner must expand past. In PostgreSQL `musicbrainz.relationships`
is MusicBrainz-only by construction — the schema gives it no `source` column —
and `graph.mb_rel_artist_artist` already restricts to the artist-to-artist pair,
so the provenance filter costs nothing because the relation *is* the filter. The
Discogs-sourced edges the Cypher excludes live in `graph.alias_of` and
`graph.member_of` and are never touched. The PostgreSQL port also pays for
crossing from the Discogs key space into the MusicBrainz key space through
`mb_artist.discogs_artist_id`, which the Cypher does not pay, so the comparison
is not one-sided.

### Measurement protocol

Per (backend, query, scale): one warm-up run, discarded; one
`EXPLAIN (ANALYZE, BUFFERS)` or Cypher `PROFILE` capture; then five timed runs.
Medians are reported, with every run listed beside them so the spread stays
visible. A mean over five runs on a laptop is at the mercy of whatever else the
machine did during run three.

PostgreSQL timings are psql `\timing`. Neo4j timings are the sum of
cypher-shell's *ready to start consuming* and *results consumed after another*,
which is the comparable figure: both start when the statement is submitted and
both end when the last row is out. A statement budget of 900 seconds was set on
the PostgreSQL side; exceeding it is recorded as the result rather than waited
out.

Seed entities were chosen by degree rather than hardcoded, so the same harness
picks a representative read at either scale. The artist sits at the 90th
percentile of release degree among artists that also carry a MusicBrainz
relationship — the busiest artist is a pathological hub whose two-hop
neighbourhood is most of the catalog, the median artist appears on one release,
and an artist with no MusicBrainz edge would turn Q3 into a measurement of how
fast an empty result comes back. The label is the busiest one, which is the label
DNA request that actually hurts.

| | fixture | synthetic |
| --- | --- | --- |
| Seed artist | `52` | `5665` |
| Seed artist `by_artist` degree | 19 | 46 |
| Seed artist MusicBrainz degree | 21 | 4 |
| Seed label | `1` | `1` |
| Seed label `on_label` degree | 290 | 5,389 |

## Evidence

### Timings

Median of five timed runs after a discarded warm-up, in milliseconds. Every run
is listed so the spread is visible.

**Fixture scale — 5,000 releases, 52,406 edges**

| Read | views over JSONB | materialized edges | Neo4j |
| --- | --- | --- | --- |
| Q1 collaborators, depth 2 | 77.8 | **10.6** | 49.0 |
| Q2 label DNA | 26.0 | **2.0** | 3.0 |
| Q3 MusicBrainz relationships | 1.1 | **0.7** | 4.0 |

**Synthetic scale — 1,000,000 releases, 9,492,662 edges**

| Read | views over JSONB | materialized edges | Neo4j |
| --- | --- | --- | --- |
| Q1 collaborators, depth 2 | 12,586 | **180.3** | 5,876 |
| Q2 label DNA | 3,227 | **36.6** | 16.0 |
| Q3 MusicBrainz relationships | 1.2 | **0.7** | 2.0 |

Individual runs, synthetic scale:

```
query                  backend         median ms   runs (ms)
q1-collaborators       views             12586.0   13091.359 12586.030 12604.514 12516.837 12310.296
q1-collaborators       materialized        180.3   191.783 179.700 180.998 180.291 178.807
q1-collaborators       neo4j              5876.0   6032 6156 5869 5876 5708
q2-label-dna           views              3227.4   3214.290 3132.477 3228.327 3227.360 3253.974
q2-label-dna           materialized         36.6   48.666 36.621 36.631 36.101 36.149
q2-label-dna           neo4j                16.0   22 19 16 15 14
q3-mb-relationships    views                 1.2   4.011 1.190 1.086 1.053 1.188
q3-mb-relationships    materialized          0.7   2.883 0.713 1.082 0.689 0.676
q3-mb-relationships    neo4j                 2.0   5 3 2 2 1
```

Individual runs, fixture scale:

```
q1-collaborators       views                77.8   83.886 77.645 77.784 77.557 77.930
q1-collaborators       materialized         10.6   13.842 10.580 10.691 10.638 10.532
q1-collaborators       neo4j                49.0   64 49 50 49 44
q2-label-dna           views                26.0   31.134 25.962 25.923 25.998 26.842
q2-label-dna           materialized          2.0   4.267 2.079 1.980 1.994 1.891
q2-label-dna           neo4j                 3.0   4 2 2 3 3
q3-mb-relationships    views                 1.1   3.788 1.180 1.018 1.122 1.088
q3-mb-relationships    materialized          0.7   2.583 0.718 0.782 0.660 0.684
q3-mb-relationships    neo4j                 4.0   4 3 5 9 3
```

Work done, from `EXPLAIN (ANALYZE, BUFFERS)` and Cypher `PROFILE` at synthetic
scale:

| Read | views buffers | materialized buffers | Neo4j db accesses |
| --- | --- | --- | --- |
| Q1 | 2,126,732 | 256,893 | 37,143,257 |
| Q2 | 840,586 | 32,472 | 89,663 |
| Q3 | 36 | 35 | 161 |

### The finding that explains every number above

An edge view over a JSONB document is not uniformly expensive. It is cheap in
one direction and unusable in the other, and every one of the three reads enters
from the unusable side.

`./gm-database-schema-9c8.1/probe-direction.sql`, at synthetic scale, probes
`graph.by_artist` once by source and once by target:

```
-- by SOURCE: WHERE release_id = '424242'
 HashAggregate (actual time=0.055..0.057 rows=2.00 loops=1)
   Buffers: shared hit=4
   ->  Nested Loop (actual time=0.050..0.052 rows=2.00 loops=1)
         ->  Index Scan using releases_pkey on releases entity (actual time=0.031..0.031 rows=1.00 loops=1)
               Index Cond: ((data_id)::text = '424242'::text)
         ->  Function Scan on jsonb_array_elements element (actual time=0.038..0.038 rows=2.00 loops=1)
 Execution Time: 0.130 ms

-- by TARGET: WHERE artist_id = '5665'
 Unique (actual time=749.894..765.076 rows=46.00 loops=1)
   Buffers: shared hit=289945
   ->  Gather Merge (actual time=749.893..765.066 rows=46.00 loops=1)
         ->  Nested Loop (actual time=27.339..500.977 rows=15.33 loops=3)
               ->  Parallel Index Scan using releases_pkey on releases entity (actual time=0.015..99.035 rows=333333.33 loops=3)
               ->  Function Scan on jsonb_array_elements element (actual time=0.001..0.001 rows=0.00 loops=1000000)
 Execution Time: 751.183 ms
```

Four buffers against 289,945; 0.13 ms against 751 ms. Same view, same row count
returned, one predicate moved from one end of the edge to the other.

The reason is structural. The source column is `releases.data_id`, a real column
with a primary key, so the planner pushes an equality on it through the view's
`DISTINCT` down to an index scan of one heap row. The target lives *inside* the
JSONB array the view unnests: the value does not exist until
`jsonb_array_elements` has produced it, so no predicate on it can be evaluated
before the row has been read and expanded. Every probe by target is a full scan
of `public.releases` plus a full unnest of every array in it.

**No index fixes this.** A GIN index on `data -> 'artists'` would serve a
containment predicate written against the document, but the view does not write
one — it unnests first and filters afterward, and PostgreSQL does not rewrite the
second form into the first. Nor can an expression index be built over a
set-returning function. This is why the schema's existing GIN indexes on
`data -> 'genres'` and `data -> 'labels'` do not appear in any plan below: they
are unreachable from the views that read those same keys.

And the target side is the side the API reads from. "This artist's
collaborators", "this label's genres" — every request in catalog-api names an
artist or a label and asks what hangs off it.

The same two probes against the materialized declaration:

```
-- by SOURCE
 Index Only Scan using by_artist_pkey on by_artist (actual time=0.034..0.037 rows=2.00 loops=1)
   Buffers: shared hit=5
 Execution Time: 0.051 ms

-- by TARGET
 Index Only Scan using by_artist_reverse on by_artist (actual time=0.030..0.034 rows=46.00 loops=1)
   Buffers: shared hit=4
 Execution Time: 0.053 ms
```

Both directions are index-only scans of a handful of buffers. That is the whole
of the difference the timing table reports.

### Q1 — collaborators at depth 2

The ported query names `by_artist` four times in the two-hop leg and once in the
one-hop leg. Because none of those references can be narrowed by the seed artist,
**each becomes its own full pass over `public.releases`**. At synthetic scale the
plan contains nine separate scans of the million-row table:

```
->  Index Scan using releases_pkey on releases entity            (rows=1000000.00 loops=1)
->  Index Scan using releases_pkey on releases entity_1          (rows=1000000.00 loops=1)
->  Index Scan using releases_pkey on releases entity_3          (rows=959450.00 loops=1)
->  Index Scan using releases_pkey on releases entity_5          (rows=959450.00 loops=1)
->  Parallel Index Scan using releases_pkey on releases entity_2 (rows=333333.33 loops=3)
->  Parallel Index Scan using releases_pkey on releases entity_4 (rows=333333.33 loops=3)
->  Index Only Scan using releases_pkey on releases              (rows=959446.00 loops=1)
->  Index Only Scan using releases_pkey on releases releases_1   (rows=1000000.00 loops=1)
->  Index Only Scan using releases_pkey on releases releases_2   (rows=959446.00 loops=1)

Planning Time: 5.503 ms
Execution Time: 16176.692 ms
```

Note these are index scans, not lookups: the planner walks the whole primary key
because it has no narrower option. 2.13 million buffers for a query that returns
fifty rows.

The materialized declaration answers the same query entirely through index-only
scans, which is what a graph traversal is supposed to look like:

```
->  Index Only Scan using by_artist_reverse on by_artist by_artist_4 (rows=46.00 loops=1)
->  Index Only Scan using artist_pkey on artist artist_3             (rows=1.00 loops=1)
->  Index Only Scan using release_pkey on release release_2          (rows=1.00 loops=46)
->  Index Only Scan using by_artist_pkey on by_artist by_artist_5    (rows=2.43 loops=46)
->  Index Only Scan using artist_pkey on artist artist_4             (rows=0.59 loops=112)
->  Index Only Scan using by_artist_reverse on by_artist by_artist_2 (rows=295.58 loops=66)
->  Index Only Scan using release_pkey on release release_1          (rows=1.00 loops=19508)

Planning Time: 8.660 ms
Execution Time: 208.007 ms
```

The seed artist's 46 releases fan out to 112 first-hop artists, then to 19,508
second-hop release visits. The traversal is proportional to the neighbourhood,
not to the catalog.

Neo4j takes 5,876 ms and 37,143,257 database accesses on the same question —
**33 times slower than the materialized PostgreSQL graph**. The `PROFILE` shows
why, and it is not a storage deficiency: the Cypher's anti-join
`NOT EXISTS { MATCH (a)<-[:BY]-(:Release)-[:BY]->(hop2) }` is evaluated by
re-expanding the seed's one-hop neighbourhood once per second-hop candidate,
while PostgreSQL evaluates the identical predicate as a single hash anti-join
against the already-materialized `hop1` CTE. The 37 million accesses are that
re-expansion. This is a property of the query as catalog-api writes it, and it is
worth carrying forward on its own: the read family is expensive on Neo4j today.

### Q2 — label DNA

The busiest label carries 5,389 releases. The views backend cannot use that to
narrow anything, so it scans both `releases` and `masters` in full — `masters`
because `graph.genre`, the vertex view, is `SELECT DISTINCT` over an unnest of
every release *and* every master, to yield fifteen rows:

```
->  Parallel Seq Scan on releases releases_1 (rows=333333.33 loops=3)
->  Parallel Seq Scan on masters             (rows=125000.00 loops=2)

Execution Time: 4213.731 ms
```

840,586 buffers, 3.2 seconds, for fifteen rows of output. The materialized
backend reads the fifteen-row `genre` table with a sequential scan of one page
and reaches the label's releases through `on_label_reverse`, finishing in 36.6 ms
and 32,472 buffers — a 88-fold improvement.

Neo4j is faster still here at 16 ms and 89,663 accesses. Q2 is a pure
neighbourhood gather with no anti-join, which is the shape a native graph store
is built for, and PostgreSQL's 36.6 ms includes a `count(DISTINCT release_id)`
over 5,389 releases that the Cypher also performs but over a smaller
intermediate. Both are comfortably inside any interactive budget.

### Q3 — MusicBrainz relationship listing

All three backends are effectively free: 1.2 ms, 0.7 ms and 2.0 ms, with 36, 35
and 161 accesses respectively, and no degradation from fixture to synthetic
scale.

This read is the control, and it is the one that proves the diagnosis rather than
merely illustrating it. `graph.mb_rel_artist_artist` reads
`musicbrainz.relationships`, a real table with real indexed columns — no JSONB,
no unnest. It is the only one of the three reads whose views-backend cost does
not move between 5,000 and 1,000,000 releases. Materializing it changes nothing
measurable, because there was nothing to precompute.

Read against Q1 and Q2, that is the cleanest statement of the finding: the
problem is not GRAPH_TABLE, not the property graph declaration, and not
PostgreSQL. It is deriving edges from JSONB documents at read time.

### Keeping materialized edges current

The read numbers say materialization is necessary. They do not say which
mechanism should write it, because a materialized view and a loader-written table
are the same heap and the same index at read time. That difference is entirely in
the write path, so it was measured separately
(`./gm-database-schema-9c8.1/refresh-cost.sql`, synthetic scale, on
`by_artist`'s 1,920,257 rows):

| Operation | Time |
| --- | --- |
| Initial build of the materialized view | 2,873 ms |
| `CREATE UNIQUE INDEX` (required for concurrent refresh) | 614 ms |
| `CREATE INDEX` on the reverse direction | 2,082 ms |
| `REFRESH MATERIALIZED VIEW` | 5,098 ms |
| `REFRESH MATERIALIZED VIEW CONCURRENTLY` | 6,925 ms |
| Loader-style rewrite of **one** changed release's edges | **1.163 ms** |

Building the entire `graph_mat` schema — all nine edge relations, all seven
vertex relations, every index, and the second property graph declaration — took
**59 seconds** at synthetic scale from a standing start.

Two things follow. A full rebuild is cheap in absolute terms: a minute of
catalog-wide work is a viable nightly or post-backfill job, so materialized views
are not disqualified by cost. But a refresh pays per catalog while an incremental
write pays per changed release, and the ratio at this scale is about 4,400 to 1
for a single relation. `REFRESH MATERIALIZED VIEW` also takes an
`ACCESS EXCLUSIVE` lock for its whole duration, blocking every reader; the
`CONCURRENTLY` variant avoids that but is *more* expensive, not less, because it
recomputes the relation and then diffs it against the existing copy.

One detail is worth recording because it contradicts the obvious guess: writing
one release's edges by reading the shipped view back —
`INSERT ... SELECT ... FROM graph.by_artist WHERE release_id = '424242'` — costs
1.163 ms, not a full scan. That is the cheap direction of the asymmetry above.
The loader needs no privileged access to its own parsed document to maintain edge
tables efficiently; the shipped views are a perfectly good source for the single
release it just wrote.

## Verdict

**GO** — with a required change.

The declared property graph is sound and `GRAPH_TABLE` on PostgreSQL 19 beta 3 is
fit for these read families. Every ported query planned and executed correctly
against both declarations, returned identical results, and the materialized
declaration served the hardest of the three reads in 180 ms over a million
releases and nine and a half million edges. On the depth-2 collaborator read it
beat Neo4j by a factor of 33.

**The graph views over JSONB, as shipped, cannot serve these reads.** At
1,000,000 releases the collaborator read takes 12.6 seconds and the label DNA
read 3.2 seconds, against interactive budgets measured in tens of milliseconds.
The cause is not tuning and not a missing index: an edge view can only be probed
from the side that is a real column, and every one of these reads enters from the
side that lives inside the document. That gap widens with the catalog and cannot
be closed by indexing.

This is a NO-GO for the views as a read surface and a GO for the graph they
declare. The declaration, the label vocabulary, the `*_key` text-column
workaround for the PostgreSQL 19 vertex-key restriction, and the query ports all
hold; only the relations underneath the declaration have to change.

## Recommendation

**Loader-written edge tables are required.** Views do not suffice. Materialized
views would suffice for reads and are a reasonable interim step, but they are the
wrong long-term shape.

### Why not materialized views

A materialized view reads identically to a table, and a full rebuild of the whole
graph takes about a minute at this scale, so cost alone does not rule it out. The
objection is the refresh model. `REFRESH MATERIALIZED VIEW` holds an
`ACCESS EXCLUSIVE` lock for its whole duration and `REFRESH ... CONCURRENTLY`
costs more, not less. Both recompute the entire relation for a catalog that
changes one release at a time, and the measured ratio against an incremental
write is roughly 4,400 to 1. A catalog under continuous ingestion wants a write
path proportional to what changed.

If edge tables cannot be scheduled immediately, materialized views with the index
set below are a legitimate stopgap that buys the full read improvement. They
should be understood as an interim step, not the destination.

### What to build

Nine edge relations as plain tables, written by the loader in the same
transaction that writes the release or master document, plus the vertex relations
that are aggregates rather than projections. `materialize.sql` in the spike
directory is a working reference for the shape and populates every relation from
the shipped views, which is also a correctness check.

Vertex relations to materialize, in priority order:

- `graph.genre` and `graph.style` — `SELECT DISTINCT` over a full unnest of every
  release **and** every master, to yield fifteen and a few hundred rows. These
  are the worst offenders and the reason a scan of `masters` appears in the label
  DNA plan, which otherwise has nothing to do with masters.
- `graph.person` — `SELECT DISTINCT` over a full unnest of every release's
  `extraartists` block. Releases only, not masters, but the same shape.
- `graph.medium` — sorts the whole media unnest to yield about fifty rows.
- `graph.company` and `graph.media_family` — the same aggregate shape over the
  companies and media blocks.

The four Discogs entity vertex views (`artist`, `label`, `master`, `release`) are
straight projections of an indexed table and do not need materializing on read
grounds. `materialize.sql` copies them anyway, because a property graph's vertex
tables and edge tables have to agree on a key type, and giving them `text` keys
is what retires the appended `*_key` workaround.

### The index set

Every edge relation needs **both** directions indexed. This is the operative
conclusion, not a detail: the reads enter these edges from the target end far
more often than from the source end, and an edge table indexed only on its
primary key reproduces the exact failure the views have.

| Relation | Primary key (forward) | Second index (reverse) |
| --- | --- | --- |
| `by_artist` | `(release_id, artist_id)` | `(artist_id, release_id)` |
| `on_label` | `(release_id, label_id)` | `(label_id, release_id)` |
| `in_genre` | `(release_id, genre_name)` | `(genre_name, release_id)` |
| `in_style` | `(release_id, style_name)` | `(style_name, release_id)` |
| `derived_from` | `(release_id)` | `(master_id, release_id)` |
| `master_by_artist` | `(master_id, artist_id)` | `(artist_id, master_id)` |
| `master_in_genre` | `(master_id, genre_name)` | `(genre_name, master_id)` |
| `master_in_style` | `(master_id, style_name)` | `(style_name, master_id)` |
| `mb_rel_artist_artist` | `(relationship_id)` | `(source_mbid)` and `(target_mbid)` |

Plus `mb_artist (discogs_artist_id)`, which is the column the MusicBrainz read
crosses key spaces on.

Both-direction coverage is what turns the whole traversal into index-only scans.
Every column listed is in its index, so no heap visit is needed — which is why
the materialized Q1 plan contains no heap access at all.

### Key types

Declare every key column as `text`, not `character varying`. PostgreSQL 19 beta 3
cannot resolve an equality operator for a `character varying` property-graph
vertex key, which is why the shipped Discogs vertex views carry appended `*_key`
text columns. Edge tables written as `text` from the start need no such
workaround, and the appended key columns can be dropped from the vertex relations
when they are materialized.

### Not recommended

Do not add GIN indexes to the release documents hoping to rescue the views. The
existing GIN indexes on `data -> 'genres'` and `data -> 'labels'` are already
unreachable from the views that read those keys, for the structural reason in the
Evidence section, and more of them would change nothing.

### Carry forward

Two findings belong to other beads rather than this one.

**The collaborator query is expensive on Neo4j today**, at 5.9 seconds and 37
million database accesses over this catalog. The cost is the Cypher's
`NOT EXISTS` anti-join, which re-expands the seed's one-hop neighbourhood once
per second-hop candidate. Whatever happens to the storage question, that query as
catalog-api writes it is worth revisiting — and the measured 33-fold win for
PostgreSQL on this read is a point in favour of the migration, not merely a
neutral result.

**The synthetic dataset is reusable.** Spike gm-database-schema-9c8.3 should
regenerate it with seed `20260917` rather than build its own; the invocation and
the exact row counts are in
[`gm-database-schema-9c8.1/README.md`](gm-database-schema-9c8.1/README.md).

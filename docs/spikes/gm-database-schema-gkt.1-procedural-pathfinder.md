# Spike gm-database-schema-gkt.1 — a vertex-at-a-time bidirectional pathfinder

Status: complete. Verdict below.

## Question

[gm-database-schema-9c8.3](gm-database-schema-9c8.3-variable-length-paths.md)
measured three level-synchronous searches over relational edge tables and found
every one of them three orders of magnitude slower than Neo4j. Its explanation
was not about storage and not about indexes:

> It is that **Neo4j's shortest-path expander can stop in the middle of a level
> and a SQL statement cannot.** Neo4j answers `d4` in 3,120 database accesses
> because it returns the instant the two searches touch. PostgreSQL visits
> 1,254,448 vertices to answer the same question, because the unit of work in SQL
> is a statement and the result of a statement is a set.

That is true of a SQL statement. It is not true of a PL/pgSQL loop, and the
untested design is the one that exploits the difference: a bidirectional search
that expands **one frontier vertex at a time**, checks after each expansion
whether the other side has already seen any of that vertex's neighbours, and
returns at the first touch. Nothing after that vertex in the level is expanded
and the rest of the level is never materialised.

**Can that search serve the two variable-length workloads at parity with Neo4j,
at GrooveMap scale, inside interactive budgets?** Four questions the answer has
to separate:

1. Is the search **correct** — does first touch return a shortest path, or one
   hop too long?
2. Does it close the gap, and by how much?
3. Does it close the gap **enough**, at the one-million-release scale, on a
   machine a product would run on?
4. Does the same idea rescue `get_explore_traversal`, which 9c8.3 also failed at
   `*1..3` and which has no target to stop at?

## Method

### The request budget

Unchanged from 9c8.3, restated because the Verdict is read against it.

| Budget | Value | Where it comes from |
| --- | --- | --- |
| Hard ceiling | **120 s** | `find_shortest_path` passes `timeout=120` to the Neo4j transaction. |
| MCP surface | **30 s** | The MCP server builds its HTTP client with `timeout=30.0`, and that one budget covers both `explore_*` lookups **and** the path call. |
| Verdict gate | **1 s p95** | The bead's own gate, at the large scale, with answers equal to Neo4j. |

The MCP tool's `max_depth` still defaults to **10**, not to 6. The surface with
the tightest budget is the one that asks for the deepest search.

### The algorithm, and why first touch is exact

`sql/pathfinder.sql.in` carries the full argument; this is the shape of it,
because a Verdict rests on the search being right rather than merely fast.

Let `df` and `db` be the depths to which the forward and backward searches are
**complete**, and let the invariant be: no path of length `df + db` or shorter
exists. Suppose the forward side now expands level `df + 1` and a probe on a
frontier vertex `v` finds a neighbour `w` in the backward seen set at depth
`b ≤ db`. That witnesses a walk of length `df + 1 + b`. By the invariant the true
distance is at least `df + db + 1`, and `df + 1 + b ≤ df + 1 + db`. So the
witnessed walk has length exactly `df + db + 1`, which is the true distance.

Two consequences matter. **First touch is exact** — there is no need to keep
looking for a better one, because every touch found anywhere in that level has
the same length. And **the order within a level is free**: correctness does not
depend on it, so a level may be expanded cheapest-vertex-first.

9c8.3 warns that "stopping the moment the frontiers touch is the version of this
algorithm that is off by one", and it is right about the version it describes —
one that compares the two **frontiers**. This one compares each newly reachable
vertex against the whole of the opposite **seen** set, which is the version that
is not.

Per expanded vertex the search runs two statements: a **touch probe**, a LATERAL
subquery carrying its own `LIMIT` so the planner cannot pull it up into a join
and must probe `pf.seen`'s primary key once per candidate neighbour, with an
outer `LIMIT 1` that stops the scan at the first hit; and an **expansion**, an
`INSERT … ON CONFLICT DO NOTHING` of the neighbours neither side has seen. Both
are plain SQL inside PL/pgSQL, so PostgreSQL prepares and caches each one per
session and a search that expands 3,000 vertices pays for two plans rather than
six thousand.

Three variants were measured, from one template so they cannot drift:

| Variant | Surface | Frontier order | Side chosen by |
| --- | --- | --- | --- |
| `pf.find_path` | all six types | index order | frontier cardinality |
| `pf.find_path_deg` | all six types | ascending degree | summed frontier degree |
| `pf.find_path_no_is` | no `IS` class | index order | frontier cardinality |

`pf.find_path_deg` needs one precomputed relation, `pf.degree`: one `bigint` per
vertex, **97 MB** at the large scale. That is deliberately not the structure
9c8.3 priced — its `path.adj` is a second copy of the whole edge set at 1,649 MB
for a 1.3× gain that did not change its verdict.

### The explore traversal

`get_explore_traversal` has no target, so it cannot stop at a touch. 9c8.3 wrote
that "`LIMIT 100` cannot rescue it, because the Cypher orders by distance and
takes the best path per discovered node, which is an aggregate over the whole
traversal". That is a true statement about the Cypher and a false one about the
**result**, and the difference is most of this spike's Verdict.

The projection is `ORDER BY dist LIMIT 100` over one row per discovered node.
Breadth-first discovery produces nodes in non-decreasing distance order, so once
100 qualifying nodes have been discovered, nothing discovered later can displace
any of them. The traversal can stop, and the rows it holds are a correct answer
to the query as written. `pf.explore` stops there.

### Machines

Every number is a number a named machine actually took. **Nothing is rescaled.**
The `investigations/` harness this one is adapted from rescales latencies by a
calibration factor, in two places, with two mutually inconsistent conventions;
that is not reproduced. Calibration is reported so the two machines can be
compared directly.

| | Local | Cloud |
| --- | --- | --- |
| Machine | Docker Desktop VM on Apple M1 Pro | IBM Cloud VPC `bx2-8x32`, `us-south-1` |
| Kernel / arch | 6.8.0 aarch64 | 6.8.0-1062-ibm x86_64 |
| vCPU | 2 | 8 |
| RAM | 7.7 GiB | 31.4 GiB |
| SHA-256, one thread | **460,438 ops/s** | **102,658 ops/s** |
| Memory read | 39,798 MB/s | 29,796 MB/s |
| Disk sequential write | 534 MB/s | 52 MB/s |
| Disk random read 4k | 484,114 IOPS | 405,735 IOPS |

That table is the single most important thing in this section and it is not the
result anyone expects. **The cloud instance has four times the cores and four
times the memory of the laptop VM, and each of its cores is 4.5 times slower**,
with a tenth of the sequential write throughput. This workload is a long chain of
small dependent index probes inside one backend — it is almost perfectly
single-threaded — so the cloud numbers are uniformly **worse** than the local
ones, by roughly the ratio the calibration predicts. Neo4j pays the same penalty:
its `*1..3` explore goes from 3,052 ms locally to 5,295 ms on the cloud.

The multi-threaded calibration figure is not quoted anywhere and should not be:
the interpreter lock distorts it badly enough on the 8 vCPU instance that it
comes out below that machine's own single-thread figure.

PostgreSQL settings were the ones both sibling spikes held constant
(`shared_buffers=1536MB`, `work_mem=128MB`, `random_page_cost=1.1`, `jit=off`, …)
on the laptop, and scaled with the instance on the cloud (`shared_buffers=8GB`,
`effective_cache_size=24GB`, `maintenance_work_mem=2GB`). Neo4j was given a
2 GiB heap and 2 GiB page cache locally and 8 GiB / 12 GiB on the cloud. Images
are the digests this project already pins:
`postgres:19beta3-alpine@sha256:b1692e50…` and
`neo4j:2026-community@sha256:dbc377fb…`, both verified as multi-architecture
indexes so the amd64 instances ran the same build as the arm64 laptop.

Locally the two engines were measured with the other **stopped**: they do not fit
in a 7.7 GiB page cache together. On the cloud they were separate instances on an
identical profile and ran **concurrently**, sharing nothing.

### Cost

| | |
| --- | --- |
| Profile | `bx2-8x32` (8 vCPU, 32 GiB), `us-south-1` |
| Instances | 2, one per engine, identical profile |
| Usage | ≈ **1.1 instance-hours** total — the first pair lived about 7 minutes before a `user_data` change replaced them, the second pair 26 minutes |
| Storage | 2 × 250 GB general-purpose boot volumes for the same period |
| Network | 2 floating IPs; SSH restricted to the driving machine's egress address, nothing else open |

IBM Cloud billing reported **no usage for the account** when the deployment was
destroyed, so there is no invoiced figure to quote and the catalog pricing API
would not return a rate for this profile during the run. At any published
on-demand rate for a `bx2-8x32` the total is comfortably under two dollars.

`terraform destroy` removed all ten resources, and the check afterwards asks IBM
Cloud rather than the state file:

```
Destroy complete! Resources: 10 destroyed.
── verifying nothing named gmgkt1- survives
no instances
no floating ips
no vpcs
no volumes
```

### Data

gm-database-schema-9c8.1's two scales, regenerated from the same fixed seed
`20260917`, with 9c8.3's `artist_edges.py` and `augment.sql` applied. The reload
reproduced 9c8.1's edge total exactly — **9,492,662** — and 9c8.3's traversal
surface exactly:

| Relationship type | Directed rows, large | Directed rows, small |
| --- | --- | --- |
| `IS` | 9,775,586 | 53,128 |
| `BY` | 4,640,924 | 24,722 |
| `ON` | 2,658,188 | 13,252 |
| `DERIVED_FROM` | 1,400,776 | 6,912 |
| `MEMBER_OF` | 117,532 | 1,542 |
| `ALIAS_OF` | 44,902 | 570 |
| **Total** | **18,637,908** | **100,126** |
| over vertices | 1,390,455 | 9,041 |

`MEMBER_OF` is two relations, not one, and the split is worth stating because a
rewrite that misses it returns different paths: **22,577 rows come from Discogs
and 36,189 from MusicBrainz**, so 61.6% of that edge class has MusicBrainz
provenance. In Neo4j both share one relationship space and `shortestPath`
traverses both. On the relational side the MusicBrainz half is reached through
`graph.mb_rel_artist_artist`, which filters `musicbrainz.relationships` to
artist-to-artist pairs and exposes the type the enricher already mapped; the ids
are MBIDs and cross to Discogs ids only through
`musicbrainz.artists.discogs_artist_id`. The bead names this mapping
`graph.mb_relationship_type`; no relation of that name exists in the shipped
schema, because the enricher applies the vocabulary before the row is stored and
what the relational side reads back is the mapped name.

### Endpoints

The large-scale cases are 9c8.3's, hardcoded and unchanged.

**The fixture scale needed its own, and that is a finding rather than a
convenience.** 9c8.3's ids are synthetic-scale ids — artist 5665 of 120,000 — and
the fixture catalog holds 1,500 artists, so at that scale every one of those
cases is a lookup of a vertex that does not exist. The first run of this harness
measured exactly that: eight misses, none over five milliseconds, which would
have gone into this document as "the small scale is fast". `sql/pick-endpoints.sql`
states the rule and derives the replacements, which are then written down rather
than chosen at run time:

| Case | Large (9c8.3) | Small (derived) | Distance |
| --- | --- | --- | --- |
| `d1-artist-artist` | 5665 → 9458 | 1 → 2 | 1 |
| `d1-artist-release` | 5665 → r3638 | 1 → r30 | 1 |
| `d2-artist-artist` | 5665 → 1 | 1 → 3 | 2 |
| `d3-artist-artist` | 5665 → 2 | 1 → 33 | 3 |
| `d4-artist-artist` | 5665 → 9 | 1 → 82 | 4 |
| `d5-artist-artist` | 55563 → 4814 | 407 → 61 | 5 |
| `d6-artist-artist` | 55563 → 32509 | 407 → 389 | 6 |
| `unreachable` | 5665 → 103111 | 1 → 950 | none |

The fixture root is artist 1, its highest-degree artist; its eccentricity is 4,
so the distance-5 and distance-6 cases start from artist 407, the lowest-numbered
degree-1 artist on the rim — the same structure 9c8.3 had to use, for the same
reason. Fourteen of the 1,500 fixture artists carry no edge at all and every
other one is reachable from the root, so the miss case is an isolated vertex
rather than a second component.

### How answers were compared

**Shortest path compares the distance, not the path.** `shortestPath` returns one
shortest path and breaks ties arbitrarily; 9c8.3 recorded two different correct
four-hop answers for the same pair on two runs of the identical query. Comparing
paths would report correctness as a failure.

**Explore compares the unbounded discovery set.** The product query is
`ORDER BY dist LIMIT 100` over a distance band holding several hundred qualifying
vertices, so the hundred that comes back is an arbitrary hundred on either
engine. The check therefore runs both engines with the limit removed and compares
the full `(id, type, dist)` sets. The `pf-*-no-is` variants are excluded from the
comparison entirely: they search a different graph on purpose.

### Harness

Its shape is adapted from the owner's earlier database-alternatives
investigation — the `investigations/` tree on that project's `db-alternatives`
branch, named here by branch rather than by repository because this repository's
distribution contract forbids the retired project name in published source.
From it: hardware calibration, workloads declared as data
with their own iteration counts, a runner reporting p50 and p95, a comparison
step, a generated report, `small` and `large` scale points, one script with a
local Docker mode and a cloud mode. Three differences are deliberate:

- That harness's cloud mode is **Ansible against Hetzner Cloud**. This one is
  **Terraform against IBM Cloud VPC**, which is what the bead asks for. There is
  no controller and no bastion: the driver runs **on** each engine's instance
  against an engine on loopback, because several cases here answer in single-digit
  milliseconds and a driver on a laptop in another country would be measuring the
  Atlantic.
- That harness rescales latencies by a calibration factor. Nothing here is
  rescaled.
- Its workloads are engine-agnostic because every engine it measures speaks a
  graph language. Here a workload names a **case** and each adapter supplies its
  own statement.

Thirty iterations after three discarded warm-ups for every workload the Verdict
reads, on one connection held open for the run. Percentiles are nearest-rank, so
every number is a run that happened. The diagnostic variants run fewer: the
`hstore` rows are five iterations with a 20 s budget, because their finding is
"did not finish" and confirming that eight times at two minutes an iteration is
two hours of wall clock.

## Evidence

Every table below is generated by `gm-database-schema-gkt.1/bench/report.py` from
the results files kept under
[`gm-database-schema-gkt.1/results/`](gm-database-schema-gkt.1/results/), together
with 204 retained plan captures — `EXPLAIN (ANALYZE, BUFFERS)` on the PostgreSQL
side and Cypher `PROFILE` on the Neo4j side, for every headline case at every
scale in both modes.

### Shortest path at the large scale, p95, both modes

The Verdict gate is 1,000 ms. Milliseconds.

| Case | d | `find_path` local | `find_path_deg` local | Neo4j local | `find_path` cloud | `find_path_deg` cloud | Neo4j cloud |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `d1-artist-artist` | 1 | 4.81 | 5.40 | **5.76** | 14.5 | 14.5 | **5.39** |
| `d1-artist-release` | 1 | 4.94 | 5.32 | **6.67** | 13.9 | 14.8 | **4.51** |
| `d2-artist-artist` | 2 | 16.4 | 21.8 | **9.60** | 28.0 | 47.0 | **9.86** |
| `d3-artist-artist` | 3 | 71.7 | 40.6 | **8.10** | 172.4 | 99.5 | **6.71** |
| `d4-artist-artist` | 4 | 61.7 | 76.6 | **6.00** | 145.5 | 177.4 | **5.13** |
| `d5-artist-artist` | 5 | **1,586** | 328.3 | **44.8** | **4,059** | 729.1 | **101.0** |
| `d6-artist-artist` | 6 | **2,379** | **3,442** | **43.9** | **4,131** | **4,518** | **72.9** |
| `unreachable` | — | 6.43 | 5.22 | **2.71** | 24.8 | 22.8 | **2.80** |

Every distance agrees with Neo4j on every case, at both scales, in both modes.

### What that is against gm-database-schema-9c8.3

Same laptop, same Docker VM, same two image digests, same catalog from the same
seed. Medians, milliseconds. 9c8.3's best PostgreSQL prototype was `p4`,
bidirectional search over a precomputed dense adjacency.

| Case | 9c8.3 `p3` bidirectional | 9c8.3 `p4` + adjacency | **This spike** | Gain over 9c8.3's best |
| --- | --- | --- | --- | --- |
| `d2-artist-artist` | 162.4 | 131.9 | **13.2** | 10× |
| `d3-artist-artist` | 78.1 | 54.3 | **66.2** | 0.8× |
| `d4-artist-artist` | 24,654 | 18,798 | **55.5** | **339×** |
| `d5-artist-artist` | 1,656 | 1,270 | **1,535** | 0.8× |
| `d6-artist-artist` | 12,199 | 8,585 | **1,551** | 5.5× |
| `unreachable` | 13.3 | 8.2 | **5.79** | 1.4× |
| explore `*1..3` | 21,404 (`p5`) | — | **13.9** | **1,536×** |

One caveat belongs with this table rather than under it. 9c8.3 measured through
`docker exec psql`, one process per statement, which adds roughly eight to ten
milliseconds to every number in its columns; this spike measures over one held
connection. That is immaterial for the `d4`, `d6` and explore rows and it is most
of the apparent difference in the `d2` row, so the small rows here should be read
as "about the same" rather than as a precise ratio.

The `d4` row is the one the spike was built to produce, and it is the whole
argument in one line: 18.8 seconds to 55 milliseconds, from changing the unit of
work and nothing else. `d3` and `d5` are flat or slightly worse, which is honest
and expected — a vertex-at-a-time loop pays per-statement overhead that a
set-at-a-time expansion amortises, and those two cases never had a level it
could bail out of early.

### Why: what the searches actually did

Large scale, local. PostgreSQL buffers are shared hits plus reads over the whole
call including every nested statement; Neo4j's column is `PROFILE` database
accesses.

| Case | Vertices expanded | Touch probes | Seen rows | PostgreSQL buffers | Neo4j DbHits |
| --- | --- | --- | --- | --- | --- |
| `d1-artist-artist` | 0 | 1 | 2 | 321 | 13 |
| `d1-artist-release` | 0 | 1 | 2 | 244 | 72 |
| `d2-artist-artist` | 1 | 2 | 57 | 10,196 | 9,184 |
| `d3-artist-artist` | 2 | 3 | 6,393 | 57,396 | 6,657 |
| `d4-artist-artist` | **57** | 58 | **2,609** | 27,172 | 2,900 |
| `d5-artist-artist` | 11 | 12 | **148,597** | 1,665,900 | 150,184 |
| `d6-artist-artist` | 18 | 19 | **148,604** | 1,700,928 | 150,151 |
| `unreachable` | 2 | 2 | 57 | 629 | 66 |

Read the `d4` row against 9c8.3's. That spike's bidirectional search visited
**1,254,448** vertices to answer it. This one expands **57** and sees 2,609,
against Neo4j's 2,900 accesses — the two engines are now doing the same amount of
work, because they are now running the same algorithm. The early exit is not a
constant-factor improvement; it removes the level.

The level profiles say the same thing, and they also say where the remaining
problem is:

```
d4  find_path      levels {55, 2185, 367}            expanded 57   seen     2,609
d4  find_path_deg  levels {55, 367, 2185}            expanded 57   seen     2,609
d5  find_path      levels {1, 8, 42, 148544}         expanded 11   seen   148,597
d5  find_path_deg  levels {1, 8, 42, 21762}          expanded 47   seen    21,838
d6  find_path      levels {1, 8, 7, 42, 148544}      expanded 18   seen   148,604
d6  find_path_deg  levels {1, 7, 8, 42, 148544}      expanded 18   seen   148,604
unreachable  find_path      levels {55, 0}           expanded  2   seen        57
unreachable  find_path_deg  levels {0}               expanded  1   seen         2
```

**The floor is one hub expansion.** Fifteen Genre vertices carry an average
degree of 137,047 and 440 Style vertices carry 6,437, against 20.7 for an artist
and 7.9 for a release. When neither side touches before it has to cross one of
those, the search inserts its whole neighbourhood: 148,544 rows, and measured on
its own that single expansion is **1,299 ms** against **128 ms** to scan the same
neighbours without inserting them. The insert is ten times the scan, and it is
90% of `d5` and `d6`.

Degree ordering is what a precomputed degree relation buys, and it buys exactly
what the theory says and no more. At `d5` it cuts the level from 148,544 to
21,762 and the time from 1,535 ms to 313 ms, because a cheap vertex in that level
touches before the hub is reached. At `d6` it changes nothing — both ends have to
cross a hub before they meet, so the hub is expanded whatever order it is in, and
the extra bookkeeping makes that case slightly **worse**. It is a variance
reducer, not a fix.

### The depth cap against a miss

The only case where the cap decides the work is the one with nothing to find.
Medians, large scale, local.

| Depth cap | `find_path` | `find_path_deg` | Neo4j |
| --- | --- | --- | --- |
| 1 | 7.14 | 6.70 | **2.28** |
| 2 | 7.64 | 6.91 | **2.36** |
| 3 | 7.26 | 6.74 | **2.43** |
| 4 | 7.49 | 7.27 | **2.35** |
| 6 | 8.17 | 7.01 | **2.33** |
| 8 | 6.47 | 6.76 | **2.19** |
| 10 | 5.79 | 4.93 | **2.22** |

Flat, at single-digit milliseconds, all the way to the product's cap of 10 —
against 9c8.3's one-ended search, which took **135,829 ms** on the same query.
The cost of a miss is the cost of discovering that one side has no frontier left,
and bidirectional search discovers that immediately when an endpoint is isolated.
This column is why the Verdict does not have to carve out the miss.

### Bounded explore traversal

| Hops | `pf.explore` p50 local | p95 local | Neo4j local | p50 cloud | p95 cloud | Neo4j cloud | Neo4j DbHits |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `*1..1` | 4.56 | 4.96 | **3.20** | 23.4 | 24.1 | **2.72** | 123 |
| `*1..2` | 13.9 | 15.0 | **8.29** | 46.5 | 48.0 | **11.7** | 3,017 |
| `*1..3` | **13.9** | **14.9** | **3,052** | **46.9** | **48.7** | **5,295** | 24,902,823 |

At `*1..3` PostgreSQL is **219 times faster than Neo4j** locally and **113 times
faster** on the cloud, and it is the only row in this document where that is true.
It is not a storage result. Both engines are asked the same question, and Neo4j
answers it by enumerating every path to every node within three hops — 24.9
million database accesses to return a hundred rows — because its planner cannot
see that the `ORDER BY dist LIMIT 100` lets the traversal stop. `pf.explore`
stops as soon as a hundred qualifying vertices have been discovered, which at
this start vertex happens inside level 2, so `*1..3` costs the same as `*1..2`.

The answers are equal as sets, checked with the limit removed on both sides:

| Hops | PostgreSQL rows | Neo4j rows | Equal |
| --- | --- | --- | --- |
| `*1..1` | 2 | 2 | yes |
| `*1..2` | 270 | 270 | yes |
| `*1..3` | 2,261 | 2,261 | yes |

### The small scale

Everything fits, in both modes, with room to spare. p95, milliseconds.

| Case | `find_path` local | `find_path_deg` local | Neo4j local | `find_path` cloud | `find_path_deg` cloud |
| --- | --- | --- | --- | --- | --- |
| `d1-artist-artist` | 7.92 | 7.47 | **6.99** | 14.5 | 14.0 |
| `d2-artist-artist` | 12.4 | 10.4 | **6.53** | 31.6 | 21.3 |
| `d3-artist-artist` | 14.2 | 23.0 | **6.30** | 37.6 | 76.0 |
| `d4-artist-artist` | 18.4 | 27.7 | **5.09** | 50.9 | 61.5 |
| `d5-artist-artist` | 25.0 | 25.6 | **5.68** | 65.8 | 57.7 |
| `d6-artist-artist` | 25.4 | 31.7 | **6.61** | 70.4 | 77.1 |
| `unreachable` | 14.7 | 8.43 | **3.41** | 38.8 | 21.9 |

Worst p95 across every gated workload: **31.7 ms** local, **77.1 ms** cloud.
Explore `*1..3` is 16.8 ms local and 34.3 ms cloud, against Neo4j's 187.9 ms and
354.6 ms — the early stop is worth 11× and 10× even at a scale where nothing is
slow. The small scale is a **GO** in
both modes, and it is worth being clear that this says nothing about the large
one: the fixture catalog's hub vertices have degree 794, not 137,653.

### The cost of the `IS` edge class, for information only

**This is not a recommendation to drop `IS`.** It is in `_PATH_REL_TYPES`, a path
through a shared genre is a real answer someone asked for, and removing the class
changes answers — which is why these rows are excluded from the comparison
against Neo4j. They are here so that whoever later argues about hub handling has
the magnitude in front of them. Medians, large scale, local.

| Case | With `IS` | Without `IS` | Ratio |
| --- | --- | --- | --- |
| `d1-artist-artist` | 4.65 | 4.60 | 1.01× |
| `d2-artist-artist` | 13.2 | 13.5 | 0.97× |
| `d3-artist-artist` | 66.2 | 67.1 | 0.99× |
| `d4-artist-artist` | 55.5 | 37.9 | 1.47× |
| `d5-artist-artist` | **1,535** | **13.4** | **114.6×** |
| `d6-artist-artist` | **1,551** | **126.2** | **12.3×** |
| `unreachable` | 5.79 | 8.14 | 0.71× |
| explore `*1..2` | 13.9 | 10.4 | 1.34× |
| explore `*1..3` | 13.9 | 12.0 | 1.16× |

The `IS` class is 52% of the edge surface and essentially all of the cost of the
two cases that fail the gate, and essentially none of the cost of everything
else. Both of the failing cases would pass without it. That is a statement about
where the cost lives, not a proposal.

### The seen set: a relation against an hstore

The seen set is a relation, `pf.seen`. The obvious objection is that a table is
heavy for what is really one request's working memory. Medians, large scale,
local.

| Case | Seen rows | Relation | hstore in a PL/pgSQL variable |
| --- | --- | --- | --- |
| `d1-artist-artist` | 2 | 4.65 | **1.05** |
| `d1-artist-release` | 2 | 4.66 | **0.74** |
| `d2-artist-artist` | 57 | 13.2 | **2.92** |
| `d3-artist-artist` | 6,393 | **66.2** | 234.6 |
| `d4-artist-artist` | 2,609 | **55.5** | 75.7 |
| `d5-artist-artist` | 148,597 | **1,535** | did not finish in 20 s |
| `d6-artist-artist` | 148,604 | **1,551** | did not finish in 20 s |
| `unreachable` | 57 | **5.79** | 1.70 |

The crossover is somewhere between a few hundred and a few thousand seen
vertices. Below it the hstore is three to six times faster; above it the cost is
quadratic and the search does not finish, because a PL/pgSQL variable is passed
into a SQL statement **by value** and each of the two statements per expanded
vertex serialises the whole set again.

The bottom of that table is the reason the relation wins. The top of it is worth
keeping, though, because it locates a real and fixable overhead: the ~4.6 ms
floor on the relation column is not search work at all — those searches expand
**zero** vertices. It is the per-call `TRUNCATE pf.seen`, which allocates a new
relation file every request. The hstore column, which truncates nothing, answers
the same cases in under 1.1 ms.

### What a depth cap costs, on a pair that is actually connected

The cap sweep above runs on the unreachable pair, where one endpoint is isolated
and the cap never decides anything. This one runs the `d6` pair — genuinely six
hops apart — against caps below its own distance. It is the only situation in
which lowering the product's cap changes the **cost** of a request rather than
only its answer. Large scale, local, p50 / p95 in milliseconds.

| Cap | `find_path` | `find_path_deg` | Neo4j | Answer |
| --- | --- | --- | --- | --- |
| 2 | 5.05 / 7.26 | 5.56 / 5.88 | **5.35 / 6.97** | no path |
| 3 | 5.44 / 5.68 | 6.02 / 6.67 | **4.45 / 7.29** | no path |
| 4 | **9.55 / 11.51** | 9.95 / 10.86 | **3.71 / 5.98** | no path |
| 6 | 1,727 / 1,783 | 2,093 / 2,149 | **39.7 / 41.9** | found, d = 6 |
| 10 | 1,638 / 1,694 | 2,073 / 2,086 | **39.0 / 44.7** | found, d = 6 |

This is the most actionable table in the document, and it is a result
gm-database-schema-9c8.3 could not have produced. There, the cap barely moved the
cost — a one-ended search exhausts the component whatever the cap says, and its
miss column ran 11.8 ms, 21.3 ms, 23,738 ms, 96,224 ms, 135,829 ms. Here **the
cap is a real cost control**, because a search that stops at first touch never
reaches the level it was not allowed to reach. Capping at 4 turns the worst case
in this catalog from 1.7 seconds into **9.6 milliseconds**, a factor of 171, and
it does it on both engines.

What it costs is answers, not correctness: at cap 4 a pair six hops apart is
reported as having no path, which is exactly what
`shortestPath((a)-[…*..4]-(b))` means and exactly what Neo4j returns for the same
query.

### What the row limit buys, and what it does not

The `*1..3` figure of 13.9 ms is easy to misread as a property of the
vertex-at-a-time loop. It is not. It is a property of the early stop, and the
same loop over the same surface with the limit removed says so:

| `*1..n`, large scale, local | With `LIMIT 100` | Unbounded |
| --- | --- | --- |
| `*1..2` | 13.9 ms | 119 ms |
| `*1..3` | **13.9 ms** | **29,774 ms** |

Unbounded, this spike's traversal costs what 9c8.3's level-synchronous one cost
(21,404 ms) — the same order, on the same machine, for the same reason. **The row
limit is worth 2,137× and the procedural loop is worth nothing here**, because
without a target and without a limit there is nothing to stop early for. That is
the honest reading, and it matters for the Recommendation: what makes explore
cheap is the bound, so the bound is the thing that must not be removed.

## Verdict

**NO-GO for shortest path at the one-million-release scale, with a measured
ceiling of 2.4 s p95 locally and 4.1 s on the cloud, both at distance 6. GO for
the bounded explore traversal at every hop count including `*1..3`, in both
modes, at both scales. GO for shortest path at a depth cap of 4.**

Answers equal Neo4j's on every case, at both scales, in both modes: every path
length, and every explore discovery set compared as a set.

The bead's gate is conjunctive — every shortest-path case including the miss at
or under 1 s p95 **and** explore `*1..3` at or under 1 s — and two of the eight
path cases miss it at the product's own cap of 10. So the gate is not met. But
the shape of this NO-GO is nothing like 9c8.3's, and the Recommendation turns on
the difference.

| | 9c8.3, best prototype | This spike | Gate |
| --- | --- | --- | --- |
| Cases inside 1 s p95, cap 10 | 4 of 8 | **6 of 8** | 8 of 8 |
| Worst case | 18.8 s | **2.4 s** | 1 s |
| Miss at cap 10 | 8.2 ms | **6.4 ms** | 1 s |
| explore `*1..3` | 21.4 s | **14.9 ms** | 1 s |
| Gap to Neo4j at `d4` | 3,760× | **10×** | — |
| Worst case at cap 4 | 96 s (one-ended) | **11.5 ms** | 1 s |

### Maximum depth servable inside the budget

Read against the three budgets in Method, using `pf.find_path` at the large
scale. p95, worst of the two modes.

| Distance found | p95 | Inside 120 s | Inside MCP 30 s | Inside the 1 s gate |
| --- | --- | --- | --- | --- |
| 1 | 14.5 ms | yes | yes | **yes** |
| 2 | 28.0 ms | yes | yes | **yes** |
| 3 | 172 ms | yes | yes | **yes** |
| 4 | 146 ms | yes | yes | **yes** |
| 5 | 4,059 ms | yes | yes | **no** |
| 6 | 4,131 ms | yes | yes | **no** |
| no path | 24.8 ms | yes | yes | **yes** |

Unlike 9c8.3, **the cost now tracks something a cap can control.** There the cost
did not track depth at all — distance 4 was the most expensive case and distance
5 was fifteen times cheaper — so a depth cap bounded a quantity that was not the
one that hurt. Here the cost is monotone in the cap on the one case where the cap
binds, and capping at 4 bounds every case in this catalog at 11.5 ms.

### Is the vertex-at-a-time design the right one?

**Yes, and it is the difference between a rewrite being conceivable and not.**
Against the same catalog on the same machine it turns 9c8.3's worst case from
18.8 s into 55 ms, closes the `d4` gap to Neo4j from 3,760× to 10×, and brings
six of eight cases inside a budget that previously held four. It does that by
expanding 57 vertices where a level-synchronous search expanded 1,254,448.

### What still fails, and why

One thing, and it is narrow. **When neither end touches before the search has to
cross a Genre vertex, the search pays to insert that vertex's whole
neighbourhood** — 148,544 rows, 1,299 ms measured on its own, against 128 ms to
scan the same neighbours without inserting them. That is 90% of `d5` and `d6` and
it is the entire remaining gap.

It is not a storage problem and it is not an index problem: the scan is already
an index-only scan and already fast. It is the write. Two of the three plausible
attacks on it were measured and neither is sufficient — degree ordering removes
it at `d5` and not at `d6`, and a precomputed dense adjacency bought 9c8.3 1.3×.
The third is described in the Recommendation and was **not** measured.

## Recommendation

### Phase 3 should implement this, at a depth cap of 4

Not at 10, and not at 6. The measurements support a cap of 4 directly: every case
in this catalog answers inside 11.5 ms p95 at that cap, on both engines, in both
modes, and the cap is enforced the same way `find_shortest_path` already enforces
its own — clamped server-side, because the value is interpolated into the query
rather than bound.

A cap of 4 is not the loss it sounds like in this catalog. From artist 5665 the
entire 1,390,450-vertex component falls inside four levels; 9c8.3 established
that nothing is at distance 5 or beyond from it, and the only pairs this spike
could find at distance 5 or 6 have a degree-1 rim artist at one end. The pairs a
cap of 4 refuses to answer are pairs where at least one endpoint is nearly
isolated — which is the case where "no path within 4 hops" is also the more
useful answer.

### The function signature

```sql
graph.find_shortest_path(
    from_kind  "char",   -- a l r m g s, the vertex discriminator
    from_key   text,
    to_kind    "char",
    to_key     text,
    max_depth  int DEFAULT 4   -- clamped server-side to [1, 4]
) RETURNS TABLE (found boolean, depth int, nodes text[], rels text[])

graph.explore_traversal(
    from_kind  "char",
    from_key   text,
    hops       int DEFAULT 2,  -- clamped to [1, 3]
    row_limit  int DEFAULT 100 -- NOT NULLABLE. See below.
) RETURNS TABLE (id text, name text, type text, path_names text[], rel_types text[], dist int)
```

`(kind, key)` as two columns rather than one `'a:5665'` token, because an
equality on a concatenation cannot use the `text` indexes that already exist and
would need an expression index on all ten relations in both directions.

### Indexes and relations

Everything needed at read time already exists. `materialize.sql` builds both
directions of all eight Discogs relations and 9c8.3's `augment.sql` builds both
directions of the two artist-to-artist ones. Phase 3 adds:

- **One request-scoped relation**, the seen set, keyed `(side, kind, key)` with a
  secondary index on `(side, depth, kind, key)` so the frontier scan is index-only.
  It must be **`TEMPORARY … ON COMMIT DELETE ROWS`**, not `UNLOGGED` — this
  harness uses `UNLOGGED` only so plans could be captured from a second session,
  and two concurrent path requests against one unlogged table would corrupt each
  other's search.
- **Do not build a second frontier relation.** Deriving the frontier from the
  seen set with `WHERE side = s AND depth = d` removed a heap insert, an index
  insert and a sequence `nextval` per discovered vertex and cut the worst
  measured case by about two fifths.
- **`pf.degree` is optional and worth building**: one `bigint` per vertex, 97 MB
  at this scale, refreshed with the edge relations. It removes the `d5`-shaped
  variance (1,535 ms to 313 ms) without changing any answer, because expansion
  order is free. It does **not** help `d6` and made it slightly worse, so it is a
  variance reducer and must not be sold as a fix. It is much cheaper than the
  dense adjacency 9c8.3 priced at 1,649 MB for 1.3×; build this one instead.
- **The MEMBER_OF union is not optional.** 61.6% of that edge class has
  MusicBrainz provenance and reaches a Discogs id only through
  `musicbrainz.artists.discogs_artist_id`. Resolve it at build time into a
  materialized artist-to-artist relation, as `augment.sql` does; a traversal that
  reads `graph.member_of` alone silently returns different paths from the Cypher
  it replaces.

### Two fixed overheads worth removing before anyone benchmarks this again

- **The per-call `TRUNCATE` is ~3.6 ms of the ~4.6 ms floor.** Cases that expand
  zero vertices still cost 4.6 ms, and the hstore variant, which truncates
  nothing, answers the same cases in under 1.1 ms. `ON COMMIT DELETE ROWS` on a
  temporary table gets this for free.
- **Do not use an hstore or an array for the seen set.** It is three to six times
  faster below a few hundred vertices and does not finish at all above a hundred
  thousand, because a PL/pgSQL variable is passed into a SQL statement by value
  and is re-serialised on every statement. The crossover is inside the range this
  workload actually visits.

### The depth cap, the MCP default, and the explore cap

- **`find_path`'s MCP default of 10 should change to 4**, and this is the
  strongest single recommendation here. The surface with the tightest budget
  (30 s, shared with two `explore_*` lookups) currently asks for the deepest
  search, and at cap 10 a distance-6 pair costs 4.1 s on the cloud machine.
  At cap 4 it costs 11.5 ms. The MCP regression test that asserts the default of
  10 should be changed with it.
- **The route default of 6 should also become 4.** Nothing in this catalog is
  answerable at 5 or 6 within the gate, on either engine, in either mode.
- **The explore cap of 3 can stay exactly as it is.** `*1..3` is 14.9 ms p95
  locally and 48.7 ms on the cloud — 219× and 113× faster than Neo4j — and it is
  the one workload in this document where the relational implementation is the
  better engine. Raising it beyond 3 is a separate question this spike did not
  measure.
- **The explore row limit must stay mandatory.** It is worth 2,137× and it is the
  entire reason explore passes. An `explore_traversal` that accepts a null or
  absent `row_limit` re-creates 9c8.3's 21-second traversal exactly.

### What to try next if depth beyond 4 is wanted

One idea, described because it is the obvious next move and because it was **not
measured and is not proven**: *hub deferral*. When a frontier vertex's degree is
far above the level's median, skip its expansion and leave it in the seen set.
Adjacency is symmetric, so the other side's touch probe will find that hub
directly when it reaches any of its neighbours, and a path through the hub is
still discovered. What is not obvious is whether the exactness argument above
survives — it relies on each level being expanded completely — and a search that
returns a wrong distance is worse than a slow one. Anyone picking this up should
prove the invariant before implementing it, and should check it against this
spike's answer set, which is committed under `results/`.

The alternative that does not need a proof is to leave distances beyond 4 on
Neo4j. `shortestPath` answers `d6` in 39 ms at cap 10; the relational search
answers it in 1.6 s. If the product wants six hops, that is where six hops should
be served from.

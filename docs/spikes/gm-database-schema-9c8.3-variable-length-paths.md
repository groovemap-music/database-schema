# Spike gm-database-schema-9c8.3 — variable-length paths: recursive CTE shortest path and bounded traversal

Status: complete. Verdict below.

## Question

PostgreSQL 19's `GRAPH_TABLE` has no variable-length path pattern. There is no
`*..10`, no `shortestPath`, and no `*1..n`. Two catalog-api functions are built
entirely on those constructs:

- `find_shortest_path` (`api/queries/neo4j_queries.py:97`) —
  `shortestPath((a)-[:BY|ON|IS|ALIAS_OF|MEMBER_OF|DERIVED_FROM*..d]-(b))`, `d`
  clamped to `[1, 10]`, default 6, 120 s transaction timeout. It surfaces as
  `GET /api/path`, the NLQ `find_path` tool, and the MCP `find_path` tool.
- `get_explore_traversal` (`api/queries/recommend_queries.py:403`) — the same six
  relationship types at `*1..n`, `n` clamped to `[1, 3]`, default 2, shortest
  path per discovered node, limit 100.

The sibling spike gm-database-schema-9c8.2 classified both as **recursive CTE**
rewrites and gated them on this spike.

**Can these two reads be served from relational edges at all, and with what depth
caps?** Three questions the answer has to separate:

1. Does a recursive CTE express `shortestPath` at a cost that fits the request
   budget, or does it need a different algorithm?
2. If it needs a different algorithm, is bidirectional search enough, or is a
   precomputed adjacency structure required on top?
3. Whatever the answer, what depth can actually be served — and is that the same
   number the product currently advertises?

## Method

### The request budget

Three budgets apply to the same query and they are not the same number. The
spike measures against all three and says which one binds.

| Budget | Value | Where it comes from |
| --- | --- | --- |
| Hard ceiling | **120 s** | `find_shortest_path` passes `timeout=120` to the Neo4j transaction. Beyond this the route returns 504. |
| MCP surface | **30 s** | `mcp_server/server.py:55` builds `httpx.AsyncClient(timeout=30.0)`. That one budget covers the two `explore_*` lookups **and** the path call. |
| Interactive | not specified | `GET /api/path` sets no route timeout of its own and the NLQ engine calls the query in-process. Nothing in the code states a target for a user-facing lookup. |

The third one has no number in the repository, so this document does not invent
one; it reports the measured latency and leaves the product judgement to the
Recommendation. What it does say plainly is that a budget of 120 s is a
**timeout**, not a target, and a path lookup that takes twenty seconds is inside
every budget written down here and still unusable in a page that draws a graph.

One more fact belongs in the budget section rather than in a footnote: the MCP
tool's `max_depth` **defaults to 10**, not to 6
(`mcp-server/tests/test_mcp_tools_regression.py:85` asserts it). The surface with
the tightest budget is the surface that asks for the deepest search.

### Environment

Every number below was produced on one machine, in one sitting. It is the same
machine, the same Docker, and the same two image digests the sibling spike
gm-database-schema-9c8.1 measured on, so the two documents' numbers can be read
against each other.

| | |
| --- | --- |
| Host | Apple M1 Pro, 10 cores, 32 GiB RAM |
| Host OS | macOS 26.6.2 (build 25G83) |
| Docker | Engine 29.5.2, Linux VM 6.8.0-117-generic, Ubuntu 24.04.4 LTS |
| Docker VM resources | **2 vCPU, 7.7 GiB RAM** |
| PostgreSQL | 19beta3 on aarch64-unknown-linux-musl, gcc (Alpine 15.2.0) |
| PostgreSQL image | `postgres:19beta3-alpine@sha256:b1692e50613a21e61c424859f943b9e193ae73e5a8c68abd5382dfb235bf15fc` |
| Neo4j | Kernel 2026.07.1, community edition, Cypher 25, COST planner, SLOTTED runtime |
| Neo4j image | `neo4j:2026-community@sha256:dbc377fb9cd8fe8dabc19d3041b197d5ca0ef8bae514cea175b8df265e5b7a76` |

PostgreSQL server settings, held constant: `shared_buffers=1536MB`,
`effective_cache_size=4GB`, `work_mem=128MB`, `maintenance_work_mem=512MB`,
`max_parallel_workers_per_gather=2`, `max_parallel_workers=2`,
`random_page_cost=1.1`, `track_io_timing=on`, `jit=off`. Neo4j was given a 2 GiB
heap and a 2 GiB page cache. Each engine was measured with the other **removed**,
not merely stopped — see "What the disk allowed" below.

The two vCPUs depress every absolute figure here relative to a production host.
They do not distort the comparison, because both engines were measured under the
identical constraint.

### Data

gm-database-schema-9c8.1's synthetic scale, regenerated from the same fixed seed
`20260917`: 1,000,000 releases, 250,000 masters, 120,000 artists, 20,000 labels,
60,000 MusicBrainz artists, and the 9,492,662 edges that spike counted. The
loader, the generator and `materialize.sql` were reused unmodified, and the
reload reproduced every one of its edge counts exactly.

#### The two edges that were missing, and why they could not be skipped

`_PATH_REL_TYPES` is `BY|ON|IS|ALIAS_OF|MEMBER_OF|DERIVED_FROM`. Eight of the ten
relations behind those six types are materialized by 9c8.1. `alias_of` and
`member_of` are not — that spike's three reads never touch them.

They cannot be left out here. They are the **only artist-to-artist edges in the
path set**. Without them every artist-to-artist path is forced through a Release,
which is precisely the structure whose cost this spike exists to bound.

Worse, the sibling catalog is asymmetric on exactly these edges.
`generate.py`'s `iter_mb_relationships` types 15% of its stream from
`DISCOGS_RELATIONSHIP_TYPES = ("ALIAS_OF", "MEMBER_OF")` and writes those rows to
the **Neo4j** target only; it drops them from the PostgreSQL target because in
the shipped schema those edges are derived from Discogs artist documents, and the
generator emits no `aliases` or `groups` block for them to be derived from.
Neo4j had them, PostgreSQL did not. Measuring across that gap would not have been
a comparison.

So `gm-database-schema-9c8.3/artist_edges.py` replays the same stream, and
`augment.sql` writes the blocks into the artist documents and then reads the
**shipped** `graph.alias_of` and `graph.member_of` views back — the same check
`materialize.sql` makes for the other eight relations. Nothing in
`gm-database-schema-9c8.1/` was edited.

One consequence of that replay is a finding in its own right and is carried
forward below: `MEMBER_OF` is in `_PATH_REL_TYPES` **and** in the MusicBrainz
enricher's own type vocabulary. In Neo4j both provenances share one relationship
space, so `shortestPath` traverses both. In PostgreSQL they are two relations in
two key spaces.

#### The graph that results

| Relationship type | Directed rows | Undirected edges |
| --- | --- | --- |
| `IS` | 9,775,586 | 4,887,793 |
| `BY` | 4,640,924 | 2,320,462 |
| `ON` | 2,658,188 | 1,329,094 |
| `DERIVED_FROM` | 1,400,776 | 700,388 |
| `MEMBER_OF` | 117,532 | 58,766 |
| `ALIAS_OF` | 44,902 | 22,451 |
| **Total** | **18,637,908** | **9,318,954** |

over **1,390,455** vertices. Neo4j's own count of the same six types is
9,318,968 — fourteen edges more, the duplicates PostgreSQL's `DISTINCT` collapsed
and Neo4j kept, a difference of 0.00015%. Neo4j additionally holds 218,727
MusicBrainz relationships of the other five types, which `shortestPath` never
matches.

#### The shape that decides everything

Mean degree by vertex kind, and it is not close to uniform:

| Kind | Vertices | Mean degree |
| --- | --- | --- |
| **Genre** | **15** | **137,047** |
| **Style** | **440** | **6,437** |
| Label | 20,000 | 66 |
| Artist | 120,000 | 21 |
| Master | 250,000 | 8 |
| Release | 1,000,000 | 8 |

Fifteen Genre vertices carry 2,055,706 of the 18,637,908 directed rows between
them. Every release is one hop from a genre and every genre is one hop from
137,000 releases, so **any two releases are within four hops**, and an artist is
one hop from a release.

That is not an artefact of the generator. Discogs genre is a fifteen-value
vocabulary on every release; the real catalog has this shape more strongly, not
less.

### The prototypes

Four, in the order a straight port would arrive at them. All four run over
`path.edge`, a view that presents the ten relations as one undirected surface:
twenty-two branches, each relation once per direction, no rows stored. Node
identity is `(kind, key)` rather than a concatenated `'r:123'` token, because an
equality on a concatenation cannot use `by_artist_pkey` and would need an
expression index on every relation in both directions.

**p1 — naive recursive CTE.** `WITH RECURSIVE`, carrying the path, cycle-guarded
with `NOT (token = ANY(seen))`. This is what a literal translation produces.

**p2 — one-ended BFS with a global visited set.** Level-synchronous, driven from
PL/pgSQL, `ON CONFLICT DO NOTHING` as the visited check and `RETURNING` as the
next frontier. The bead calls this "bounded UNION unrolling"; the loop is that
unrolling with the bound supplied at call time.

**p3 — bidirectional BFS.** The same machinery from both ends, expanding
whichever frontier is smaller, taking `min(forward.depth + backward.depth)` over
every vertex both sides have seen. This is `shortestPath`'s own algorithm.

**p4 — bidirectional BFS over a precomputed adjacency.** One `path.adj` relation
of 18,637,908 `(src bigint, dst bigint, rel "char")` rows with a covering index,
over a dense `bigint` node space. This is the "do we need to build something
extra" arm.

Why p2 is a loop and not one statement is worth stating, because it bounds what
`WITH RECURSIVE` can ever do here. A recursive CTE may not reference its own
working table twice, so it cannot anti-join the frontier against everything seen
so far. `UNION` instead of `UNION ALL` deduplicates the whole output row, which
prunes only while the row is the bare vertex — the moment a depth or a path array
is carried, two arrivals at one vertex are two distinct rows and the pruning
stops. PostgreSQL's `CYCLE ... SET ... USING` clause expresses p1's guard more
neatly and prunes exactly as much: it too is per-path, not global.

**p5 — bounded traversal** reuses p2's machinery with no target, then projects
`get_explore_traversal`'s output from the parent pointers.

### Endpoints

Hardcoded, not chosen by degree at run time. 9c8.1's reviewer noted that its
seeds were picked with `ORDER BY degree DESC OFFSET n` and no tiebreak, so a
rerun could silently measure a different vertex.

Artist **5665** is 9c8.1's seed artist and is one endpoint wherever it can be.
The others are the lowest-numbered vertex of the wanted kind at the wanted depth
in one exhaustive search out of it. That search reaches all 1,390,450 vertices of
the component in **four** levels, so distances 5 and 6 have to start elsewhere:
artist **55563**, the lowest-numbered degree-1 artist on the rim of 5665's
search, has eccentricity 6.

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

**There is no case at distance 7, 8 or 10, and that absence is a result rather
than an omission.** Nothing in this catalog is further than 6 from either root.
The bead asked for pairs at 8 and 10; they do not exist, for the reason the
degree table gives.

The unreachable pair is honest about what it is. Artist 103111 is one of five
artists with no path edge at all, so it is its own component of size one. That
makes it the cheapest possible miss for any algorithm that searches from both
ends, and the most expensive possible miss for one that does not. A catalog with
two large disconnected components would be harder for both, and this one does not
contain such a pair to measure.

### Measurement protocol

Per (prototype, case, cap): one warm-up, discarded; then five timed runs when the
warm-up came back inside three seconds, three inside thirty, two beyond that.
Medians are reported with every run beside them. Five runs of a two-minute
exhaustion would be ten minutes of wall clock spent sharpening a median that is
already unambiguous.

PostgreSQL timings are psql `\timing`. Neo4j timings are the sum of
cypher-shell's *ready to start consuming* and *results consumed after another*,
which is the rule 9c8.1 used, so the two spikes' ratios mean the same thing.
Every PostgreSQL plan and every Cypher `PROFILE` was **kept**; the load-bearing
ones are quoted below.

`statement_timeout` was 900 s for p2 through p5 and **120 s for p1**, because 120
s is the product's own budget and "did not finish inside it" is the measurement.

### What the disk allowed

The host volume had about 12 GiB free at the start and this spike had to fit
inside it. Two things had to change as a result, and both are reported rather
than smoothed over.

**p1 needed a `temp_file_limit`.** `WITH RECURSIVE` materializes its entire
result before the outer `ORDER BY depth LIMIT 1` can look at it, and p1's result
is every simple path up to the cap. Left unbounded it writes that to disk until
the volume fills: the first attempt took the host from 6.0 GiB free to 2.6 GiB in
under two minutes on **one statement**. Bounded at 512 MB the backend raises
`temporary file size exceeds temp_file_limit` instead. That is a stricter test
than the 120 s timeout and p1 fails it earlier, so where the table below says
"over temp limit" it means the statement's intermediate exceeded 512 MB of temp
files, not that it ran out of time.

**The two engines were measured one after the other, not side by side.** The
PostgreSQL container and volume were destroyed before Neo4j was built. 9c8.1
stopped its PostgreSQL container for the same reason — neither engine competing
for the other's page cache — so the protocol is the same; only the reason for the
teardown is different.

The full synthetic scale fit. Nothing was measured at a reduced scale.

## Evidence

### Shortest path, at the product's cap of 10

Median of the timed runs, in milliseconds. `d` is the distance actually found;
all three PostgreSQL searches agree with each other and with Neo4j on every case,
which is the correctness check.

| Case | d | p1 naive CTE | p2 one-ended BFS | p3 bidirectional | p4 bi + adjacency | **Neo4j** |
| --- | --- | --- | --- | --- | --- | --- |
| `d1-artist-artist` | 1 | over temp limit | 11.7 | 12.4 | 7.1 | **4** |
| `d1-artist-release` | 1 | — | 11.7 | 12.5 | 7.0 | **2** |
| `d2-artist-artist` | 2 | over temp limit | 21.5 | 180.0 | 138.3 | **8** |
| `d3-artist-artist` | 3 | — | 24,096 | 92.1 | 57.3 | **7** |
| `d4-artist-artist` | 4 | — | 97,828 | 22,603 | 18,851 | **5** |
| `d5-artist-artist` | 5 | — | 99,473 | 1,617 | 1,254 | **34** |
| `d6-artist-artist` | 6 | — | 137,208 | 12,111 | 8,569 | **39** |
| `unreachable` | — | — | 135,274 | 12.9 | 8.2 | **3** |

Work done, from the same runs — vertices visited on the PostgreSQL side, database
accesses from Cypher `PROFILE` on the Neo4j side:

| Case | p2 visited | p3 visited | Neo4j DbHits |
| --- | --- | --- | --- |
| `d1-artist-artist` | 56 | 57 | 13 |
| `d2-artist-artist` | 423 | 18,927 | 8,036 |
| `d3-artist-artist` | 1,252,262 | 6,760 | 6,649 |
| `d4-artist-artist` | 1,390,450 | 1,254,448 | 3,178 |
| `d5-artist-artist` | 1,389,893 | 170,359 | 150,129 |
| `d6-artist-artist` | 1,390,450 | 963,879 | 150,163 |
| `unreachable` | 1,390,450 | 57 | 66 |

Every run, so the spread stays visible:

```
p2-bfs-uni  d1-artist-artist   10   11.7      13.477 11.595 11.361 13.475 11.695
p2-bfs-uni  d2-artist-artist   10   21.5      21.490 21.436 21.559 22.458 20.958
p2-bfs-uni  d3-artist-artist   10   24095.5   24247.041 23975.960 24095.487
p2-bfs-uni  d4-artist-artist   10   97828.1   98216.162 97440.048
p2-bfs-uni  d5-artist-artist   10   99473.4   99488.754 99458.115
p2-bfs-uni  d6-artist-artist   10  137208.0   136529.082 137886.867
p2-bfs-uni  unreachable        10  135274.2   134120.911 136427.481
p3-bfs-bi   d1-artist-artist   10   12.4      13.065 11.909 11.755 13.205 12.448
p3-bfs-bi   d2-artist-artist   10  180.0      175.995 180.004 180.321 181.291 177.290
p3-bfs-bi   d3-artist-artist   10   92.1      90.754 92.500 90.823 92.088 92.079
p3-bfs-bi   d4-artist-artist   10  22603.3    22308.422 22778.100 22603.261
p3-bfs-bi   d5-artist-artist   10   1616.7    1616.664 1614.862 1622.205 1625.063 1610.847
p3-bfs-bi   d6-artist-artist   10  12110.6    12110.571 12255.927 11989.399
p3-bfs-bi   unreachable        10   12.9      12.950 12.207 12.268 13.137 13.001
p4-adj-bi   d1-artist-artist   10    7.1      7.108 7.787 6.535 7.900 7.098
p4-adj-bi   d2-artist-artist   10  138.3      138.252 138.166 143.414 146.890 137.633
p4-adj-bi   d3-artist-artist   10   57.3      58.195 56.020 57.289 59.837 56.412
p4-adj-bi   d4-artist-artist   10  18851.5    19040.946 18795.940 18851.461
p4-adj-bi   d5-artist-artist   10   1254.1    1237.976 1254.771 1228.928 1254.119 1256.857
p4-adj-bi   d6-artist-artist   10   8569.2    8436.317 8569.169 8643.520
p4-adj-bi   unreachable        10    8.2      8.216 8.238 7.458 8.244 7.231
neo4j       d1-artist-artist   10    4.0      4 4 3
neo4j       d2-artist-artist   10    8.0      6 11 8
neo4j       d3-artist-artist   10    7.0      7 11 5
neo4j       d4-artist-artist   10    5.0      4 13 5
neo4j       d5-artist-artist   10   34.0      33 34 34
neo4j       d6-artist-artist   10   39.0      39 39 35
neo4j       unreachable        10    3.0      4 3 2
```

### The naive recursive CTE never reaches depth 3

p1 against the depth cap, on the easiest artist-to-artist case:

| Cap | p1 |
| --- | --- |
| 1 | 5.0 ms |
| 2 | 9.5 ms |
| 3 | **over temp limit** |
| 4–10 | not attempted; the recursion is monotone in the cap |

The plan at cap 2 shows exactly where it goes:

```
 Limit (actual time=4.998..5.001 rows=1.00 loops=1)
   CTE walk
     ->  Recursive Union (actual time=0.002..4.778 rows=511.00 loops=1)
           ->  Nested Loop (actual time=0.022..1.523 rows=170.00 loops=3)
                 ->  WorkTable Scan on walk w (rows=18.67 loops=3)
                       Filter: (depth < 2)
                 ->  Append (actual time=0.010..0.078 rows=9.11 loops=56)
                       ->  Index Only Scan using by_artist_pkey on by_artist
                             Index Cond: (release_id = w.key)
                             Filter: ((w.kind = 'r') AND ((('a' || ':') || artist_id) <> ALL (w.seen)))
                             Index Searches: 56
                       ->  Index Only Scan using by_artist_reverse on by_artist by_artist_1
                             Index Cond: (artist_id = w.key)
                             Filter: ((w.kind = 'a') AND ((('r' || ':') || release_id) <> ALL (w.seen)))
                             Index Searches: 56
                       ... twenty more branches, each its own index search ...
```

Two things are visible. Every branch is an index-only scan, so the access path is
right: the `Append` costs twenty-two index searches per working-table row, a
constant factor, not a scan. And the recursion produces 511 rows at cap 2 for a
graph whose 2-neighbourhood of this vertex has 423 vertices — already more paths
than vertices. At cap 3 the seed's frontier reaches the Genre vertices, each path
through one of them forks 137,000 ways, and the count of simple paths passes what
512 MB of temp files can hold. **`WITH RECURSIVE` cannot express this search**,
not because of PostgreSQL's recursion but because the thing it enumerates is the
wrong set.

### Why the level-synchronous searches are slow: one plan

The p2 expansion at level 4 of the `d4` case, captured through `auto_explain`
with `log_nested_statements`:

```
duration: 80250.938 ms  plan:
  Insert on frontier (actual time=80250.925..80250.935 rows=0.00 loops=1)
    Buffers: shared hit=44978636 dirtied=2490 written=2490
    CTE nxt
      ->  Insert on visited (actual time=297.775..80112.219 rows=138188.00 loops=1)
            Conflict Resolution: NOTHING
            Tuples Inserted: 138188
            Conflicting Tuples: 10867975
            Buffers: shared hit=44696173
            ->  Hash Join (actual time=297.703..8387.036 rows=11006163.00 loops=1)
                  Hash Cond: ((('r') = f.kind) AND (by_artist.release_id = f.key))
                  ->  Append (actual time=0.009..2625.882 rows=18637908.00 loops=1)
                        ->  Seq Scan on by_artist (rows=1920257.00 loops=1)
                        ->  Seq Scan on by_artist by_artist_1 (rows=1920257.00 loops=1)
                        ->  Seq Scan on master_by_artist (rows=400205.00 loops=1)
                        ... all twenty-two branches, sequentially scanned ...
```

One level: a full sequential scan of **all 18,637,908 edge rows**, 11,006,163
candidate arrivals produced, 10,867,975 of them discarded as already visited, and
**138,188 new vertices** to show for it. 44.7 million buffers, 80 seconds, a 1.3%
yield.

That is the whole of the PostgreSQL side of the timing table, and it has two
causes that have to be separated because only one of them is fixable.

The **fixable** one is the access path. The frontier at level 3 holds 1.25 million
vertices, so the planner stops probing indexes and hash-joins the entire edge
surface instead, which is the right choice for a frontier that size. p4 measures
what removing that costs: a dense `bigint` adjacency with a covering index cuts
`d4` from 22,603 ms to 18,851 ms and `d6` from 12,111 ms to 8,569 ms. Real, worth
having, and **nowhere near enough** — about 1.2 to 1.4 times, against a gap of
three orders of magnitude.

The **unfixable** one is that a SQL statement cannot stop in the middle. p3 and
Neo4j run the same algorithm, and both must expand the level that crosses the
Genre vertices. Neo4j's expander checks for an intersection as each neighbour is
produced and **returns the moment the two searches touch**; it never finishes the
level. A level-synchronous implementation has no way to do that, because the unit
of work is a statement and the statement's result is a set. So p3 pays for all
1,254,448 vertices of the level in which the answer was found, and Neo4j pays for
3,178 database accesses.

This is why `d4` is the worst case in the table and `d5` is sixty times better
despite being further: from artist 55563 the frontier stays small for four levels
and the search meets before either side crosses a Genre vertex.

It is also why the bidirectional search is sometimes *worse* than the one-ended
one. At `d2` p3 takes 180 ms against p2's 21.5 ms, because artist 1's
neighbourhood is 18,870 vertices and the one-ended search found the target before
it had any reason to look at them.

### The Neo4j baseline

`PROFILE` for the `d4` case, kept in full in
[`gm-database-schema-9c8.3/`](gm-database-schema-9c8.3/) results:

```
| Plan      | Statement   | Version | Planner | Runtime   | Time | DbHits | Rows |
| "PROFILE" | "READ_ONLY" | "25"    | "COST"  | "SLOTTED" | 6    | 3178   | 1    |

| Operator          | Details                                                            | Rows | DB Hits |
| +ProduceResults   | nodes, rels                                                        |    1 |       0 |
| +Projection       | [node IN nodes(p) | {id: ..., name: ..., labels: labels(node)}]    |    1 |      18 |
| +ShortestPath     | p = (a)-[anon_0:BY|ON|IS|ALIAS_OF|MEMBER_OF|DERIVED_FROM*..10]-(b) |    1 |    3156 |
| +CartesianProduct |                                                                    |    1 |       0 |
| | +NodeIndexSeek  | RANGE INDEX b:Artist(id) WHERE id = $to_id                         |    1 |       2 |
| +NodeIndexSeek    | RANGE INDEX a:Artist(id) WHERE id = $from_id                       |    1 |       2 |

Total database accesses: 3178, total allocated memory: 216576
```

The path it returned, which is also the clearest single illustration of the
degree table:

```
Willow Union (Artist 5665)
  -BY->   Interlude Reissue (Release 816039)
  -IS->   Non-Music (Genre)
  -IS->   Resonance Remixes (Master 247669)
  -BY->   Marble Junction (Artist 9)
```

Four hops between two arbitrary artists, and the middle of it is a Genre vertex
of degree 137,193. Every artist-to-artist path in this catalog that is not a
direct alias or a shared release looks like this.

### Depth cap against a miss

The only case in which the cap decides the work is the one where there is nothing
to find. Median milliseconds:

| Cap | p2 one-ended | p3 bidirectional | p4 bi + adjacency | Neo4j |
| --- | --- | --- | --- | --- |
| 1 | 11.1 | 12.2 | 7.5 | 2 |
| 2 | 20.4 | 13.4 | 7.7 | 3 |
| 3 | **23,666** | 13.0 | 7.5 | 3 |
| 4 | **100,785** | 12.6 | 8.5 | 3 |
| 6 | — | — | — | 3 |
| 8 | — | — | — | 3 |
| 10 | **135,274** | 13.4 | 7.6 | 2 |

Caps 5 and above are one measurement for the one-ended search, not four: the
component is exhausted at level 5, so the loop leaves at the same place whether
the cap said 6, 8 or 10. That is worth stating on its own — **for a one-ended
search the cap stops mattering above the eccentricity of the source**, and the
cost of a miss is the cost of the component, not of the cap.

The flat 13 ms column for p3 and the flat 3 ms column for Neo4j are both an
artefact of this particular unreachable pair: artist 103111 is isolated, so the
backward frontier is empty at its first level and both bidirectional searches
retire immediately. A miss between two well-connected vertices in different
components would cost the smaller component, and this catalog has no such pair to
measure.

### Bounded traversal — `get_explore_traversal`'s `*1..n`

| Hops | PostgreSQL (p5) | Neo4j | Neo4j DbHits |
| --- | --- | --- | --- |
| `*1..1` | 11.7 ms | 3 ms | 123 |
| `*1..2` | 23.2 ms | 10 ms | 3,007 |
| `*1..3` | **22,908 ms** | **3,356 ms** | **24,902,813** |

```
p5-traverse  explore-artist-5665  1      11.7   12.021 11.609 11.611 11.842 11.725
p5-traverse  explore-artist-5665  2      23.2   24.449 22.988 23.193 22.682 23.278
p5-traverse  explore-artist-5665  3   22908.5   22997.570 22908.464 22717.489
neo4j        explore-artist-5665  1       3.0   3 3 3
neo4j        explore-artist-5665  2      10.0   23 10 7
neo4j        explore-artist-5665  3    3356.0   3356 3404 3270
```

This read has no early exit on either engine. `LIMIT 100` cannot rescue it,
because the Cypher orders by distance and takes the best path per discovered
node, which is an aggregate over the whole traversal. Both engines must exhaust
level `n`, and level 3 is where the Genre vertices are. Neo4j's 24.9 million
database accesses for a hundred rows say the same thing PostgreSQL's 23 seconds
do; Neo4j is seven times faster at being unusable.

`*1..1` and `*1..2` are comfortable on both. `*1..2` is the function's default.

### The correctness cross-check

Every case was answered independently by the one-ended search, the bidirectional
search, the bidirectional search over the dense adjacency, and Neo4j. All four
agree on every distance, including the two the naive CTE could reach. The
bidirectional implementation takes `min(forward.depth + backward.depth)` over the
whole of both visited sets rather than stopping when the frontiers touch, which
is the version of that algorithm that is not off by one, and the agreement is the
evidence that it is right.

Level profiles, from the same runs:

```
d4  one-ended    {55, 367, 1251839, 138188}           visited 1,390,450
d4  bidirectional{55, 2185, 367, 1251839}             visited 1,254,448
d5  one-ended    {1, 8, 148544, 572892, 668447}       visited 1,389,893
d5  bidirectional{1, 8, 42, 148544, 21762}            visited   170,359
unreachable  one-ended     {55, 367, 1251839, 138188, 0}  visited 1,390,450
unreachable  bidirectional {55, 0}                        visited        57
```

## Verdict

**Conditional GO for shortest path at a cap of 2. NO-GO for everything above it,
and NO-GO for `*1..3` traversal.**

### Maximum depth servable within the request budget

Read against the three budgets in the Method section, using the best PostgreSQL
prototype measured (p4, bidirectional over a precomputed dense adjacency):

| Distance found | Best PostgreSQL | Inside 120 s? | Inside the MCP 30 s? | Usable interactively? |
| --- | --- | --- | --- | --- |
| 1 | 7 ms | yes | yes | **yes** |
| 2 | 138 ms | yes | yes | **yes** |
| 3 | 57 ms | yes | yes | yes, but see below |
| 4 | 18.9 s | yes | barely, and it shares the budget | **no** |
| 5 | 1.3 s | yes | yes | marginal |
| 6 | 8.6 s | yes | no, with two lookups to pay for | **no** |
| no path | 8 ms | yes | yes | yes, for an isolated endpoint only |

The honest reading of that table is not "depth 6 is servable because 8.6 s is
under 120 s". It is that **the cost does not track the depth at all**. Distance 4
is the most expensive case in the table and distance 5 is fifteen times cheaper,
because what decides the cost is whether the search has to expand a Genre vertex
before the two ends meet — and the caller cannot know that in advance. A depth
cap is the wrong control surface for this workload: it bounds a quantity that is
not the one that hurts.

What can be promised is bounded by the worst case at each distance, not the
measured one, and the worst case at distance 3 or more is a hub expansion. So:

- **Depth 2 is servable.** 138 ms worst measured, and a two-hop neighbourhood
  cannot reach a Genre vertex from an Artist without passing through a Release,
  which is one level of at most a few hundred.
- **Depth 3 and 4 are not servable interactively.** Measured at 57 ms and 18.9 s
  for the same cap, on the same engine, four hops apart.
- **Depth 5 and above is not servable at all**, and there is nothing at depth 7
  or beyond to serve.

### Is bidirectional search needed?

**Yes, and it is not optional.** It is the difference between 135 s and 13 ms on
a miss, and between 99 s and 1.6 s at distance 5. A one-ended search cannot serve
this workload at any cap above 2 — at cap 3 it already costs 24 s.

But it is not sufficient. Bidirectional search still pays for the whole of the
level in which it finds the answer, and one level here is 1.25 million vertices.

### Is precomputed adjacency needed?

**It is worth building and it does not change the verdict.** A dense `bigint`
adjacency with a covering index is 1.2 to 1.4 times faster than the same search
over the twenty-two-branch view — 18.9 s against 22.6 s at distance 4, 8.6 s
against 12.1 s at distance 6. It costs 1,649 MB at this scale, a second copy of
the edge set to keep current, and a stable node numbering across a growing
catalog.

That is a reasonable trade for the cases that are servable, and it does not
rescue the cases that are not. Anyone reading this table hoping that the right
index makes depth 6 work should read the ratio column instead: the gap to Neo4j
at distance 4 is 3,770-fold, and precomputed adjacency closes 1.2 of it.

### The thing that actually separates the two engines

It is not storage and it is not the index. It is that **Neo4j's shortest-path
expander can stop in the middle of a level and a SQL statement cannot.** Neo4j
answers `d4` in 3,178 database accesses because it returns the instant the two
searches touch. PostgreSQL visits 1,254,448 vertices to answer the same question,
because the unit of work in SQL is a statement and the result of a statement is a
set. Nothing about PostgreSQL 19, `GRAPH_TABLE`, or the shape of the edge tables
changes that, and no future `GRAPH_TABLE` variable-length syntax would either
unless it came with an operator that terminates early.

This is the opposite result from the sibling spike, and the two are consistent.
gm-database-schema-9c8.1 found PostgreSQL **33 times faster** than Neo4j on the
depth-2 collaborator read, because that read is a fixed-length join and set-at-a-
time evaluation is exactly what a join wants. This spike finds Neo4j **3,770
times faster** on variable-length shortest path, because set-at-a-time evaluation
is exactly what an early-terminating search does not want. The migration's
read-family classification is the right axis; the fixed-length families cross and
the variable-length ones do not.

## Recommendation

**Keep `find_shortest_path` and `get_explore_traversal` on Neo4j.** This is what
gm-database-schema-9c8.2's rewrite plan already assumed for these two functions
and what the program plan says; this spike confirms it with numbers rather than
leaving it as an assumption, and narrows what a partial migration could offer.

### The product-facing caps

If and only if a relational path service is built anyway — for a deployment with
no Neo4j, or as a fallback — these are the caps the measurements support. They
are **lower than what the product advertises today**.

| Surface | Current | Recommended relational cap | Why |
| --- | --- | --- | --- |
| `GET /api/path` | `ge=1, le=10`, default 6 | **`le=2`, default 2** | 138 ms at depth 2; 18.9 s at depth 4 on the same engine |
| NLQ `find_path` | clamped `[1, 10]`, default 6 | **clamp `[1, 2]`, default 2** | Model-steerable. The clamp is the only thing between a tool call and a 20 s query |
| MCP `find_path` | `[1, 10]`, **default 10** | **clamp `[1, 2]`, default 2** | Tightest budget, 30 s shared with two lookups, and the deepest default of the three |
| `explore` traversal | `1 <= hops <= 3`, default 2 | **`1 <= hops <= 2`, default 2** | 23 ms at `*1..2`; 22.9 s at `*1..3` |

Two of these are worth calling out on their own.

**The MCP default of 10 should change regardless of which engine serves the
query.** It is the deepest default of the three surfaces behind the tightest
timeout, and it is the one a model reaches for without being asked. On Neo4j
today that default is survivable because `shortestPath` terminates early; it is
survivable by luck rather than by design.

**Dropping the explore traversal from 3 hops to 2 costs almost nothing.** The
default is already 2, `*1..3` is a thousand times more expensive than `*1..2` on
PostgreSQL and 330 times more on Neo4j, and it returns the same 100 rows. This is
the one recommendation that applies to the current Neo4j deployment immediately
and does not wait for any migration.

### If depth beyond 2 is a product requirement

It cannot be served by traversing these edges at request time on either engine —
Neo4j's own 24.9 million accesses for `*1..3` say so. It needs a different data
structure, and the spike's evidence points at which:

- **Suppress the hub vertices for pathfinding.** Essentially every long path in
  the table runs through a Genre vertex, and a path that says "these two artists
  are connected because both releases are tagged Non-Music" is not an answer a
  user wants. Dropping `IS` from `_PATH_REL_TYPES` would both improve the answers
  and remove the 137,000-degree vertices from the search. **This is the single
  highest-value follow-up in the document and it is a product question, not a
  storage question.** It should be asked before any more engineering is spent on
  making the current traversal faster.
- **Precompute what is actually asked for.** If the product wants "how is artist
  A connected to artist B", an artist-to-artist projection over `BY`, `ALIAS_OF`
  and `MEMBER_OF` is a graph of 120,000 vertices rather than 1.39 million, with
  no hub vertices, and its diameter is what a path query should be exploring.

### What to build if a relational path service is built

Not a recursive CTE. The evidence is unambiguous: p1 cannot reach depth 3.

The shape that works for the servable cases is the one `bfs.sql` and
`adjacency.sql` prototype: a bidirectional level-synchronous search, driven from
outside the statement, over a dense `bigint` adjacency relation with a covering
index. `path.adj` in the spike directory is a working reference. It needs, in
priority order, the `ALIAS_OF` and `MEMBER_OF` edge tables that 9c8.1 did not
recommend because its reads did not need them, then the node numbering, then the
adjacency itself.

### Carry forward

**`MEMBER_OF` spans two provenances and a naive rewrite loses half of them.**
This is the most actionable item here and it is a correctness issue, not a
performance one. `MEMBER_OF` is in `_PATH_REL_TYPES` and is also the first entry
in the MusicBrainz enricher's relationship vocabulary. In Neo4j both provenances
share one relationship space, so `shortestPath` traverses both: at the synthetic
scale that is 22,580 Discogs band memberships and 36,186 MusicBrainz "member of"
assertions. In PostgreSQL the first come from `graph.member_of`, keyed on Discogs
artist ids, and the second from `musicbrainz.relationships`, keyed on MBIDs and
reaching a Discogs id only through `musicbrainz.artists.discogs_artist_id`. **A
rewrite that traverses `graph.member_of` alone silently drops 62% of the
`MEMBER_OF` edges and returns different paths from the Cypher it replaces.**
`augment.sql` shows the union that does not.

**The graph's effective diameter is 4, and the product's cap is 10.** No vertex
in this catalog is more than 6 hops from either root measured, and from an
ordinary artist the whole 1.39-million-vertex component is within 4. The clamp at
`MAX_PATH_DEPTH = 10` has never been able to return a path longer than about 6 on
data of this shape; what it does instead is let a miss search six levels it could
never have needed. That is true on Neo4j today, where it is cheap, and it is the
difference between 20 ms and 135 seconds on any relational implementation.

**The sibling spike's dataset now has its artist-to-artist edges on both sides.**
`artist_edges.py` and `augment.sql` close the asymmetry described in Method.
Anything reusing the 9c8.1 catalog for a read that touches `ALIAS_OF` or
`MEMBER_OF` should apply them, or it will measure Neo4j against a PostgreSQL
graph that is missing 81,217 edges.

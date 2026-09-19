#!/usr/bin/env bash
# Measure the two claims the Evidence section makes about WHY distances 5 and 6
# cost what they cost, and keep the captures.
#
# Throwaway spike harness for gm-database-schema-gkt.1. Both of these were
# originally run ad hoc at a psql prompt and quoted from the terminal, which is
# exactly the habit gm-database-schema-9c8.3 was bounced for. They are a script
# now, and their output is committed under results/.
#
#   1. HUB EXPANSION. The document attributes ~90% of the distance-5 and
#      distance-6 cost to inserting one Genre vertex's whole neighbourhood. That
#      is a claim about a write, so it is measured as a write: the same
#      neighbourhood scanned without inserting, then scanned and inserted, both
#      under EXPLAIN (ANALYZE, BUFFERS).
#
#   2. COVERAGE. The recommendation of a depth cap of 4 leans on the catalog's
#      shape — that the component around the seed artist is exhausted within four
#      levels. gm-database-schema-9c8.3 established that and this spike inherited
#      it. Inheriting a fact that a recommendation rests on is not good enough,
#      so it is re-measured here with an exhaustive breadth-first search and the
#      level profile is kept.
#
#   3. THE ROW LIMIT. The explore traversal's headline figure is what it costs
#      WITH its row limit. The claim that the limit is what makes it cheap needs
#      the other half, so the same traversal is run unbounded and timed.
set -euo pipefail

PG_CONTAINER="${PG_CONTAINER:-gmgkt1-pg}"
PGPASSWORD="${PGPASSWORD:-spike-password}"
out="${1:?usage: hub-cost.sh <results-dir>}"
mkdir -p "$out"

psql_run() {
    docker exec -i -e PGPASSWORD="$PGPASSWORD" "$PG_CONTAINER" \
        psql -U groovemap -d groovemap -v ON_ERROR_STOP=1 -q "$@"
}

# ---------------------------------------------------------------------------
# 1. What one hub expansion costs
# ---------------------------------------------------------------------------
{
    echo "== gm-database-schema-gkt.1 — the cost of expanding ONE hub vertex"
    echo "=="
    echo "== The highest-degree vertex in the traversal surface, scanned and then"
    echo "== scanned-and-inserted, which is what pf.find_path does per frontier"
    echo "== vertex. Three repetitions of each so the figure is not one sample."
    echo
    psql_run -c "SELECT kind, key, deg FROM pf.degree ORDER BY deg DESC, key LIMIT 5"

    for run in 1 2 3; do
        echo "---------------- repetition $run: SCAN ONLY (the touch probe's access path)"
        psql_run -c "
            EXPLAIN (ANALYZE, BUFFERS)
            SELECT count(*) FROM pf.edge e
            WHERE e.src_kind = 'g'
              AND e.src_key = (SELECT key FROM pf.degree WHERE kind = 'g' ORDER BY deg DESC, key LIMIT 1)"

        echo "---------------- repetition $run: SCAN AND INSERT (the expansion)"
        psql_run -c "TRUNCATE pf.seen"
        psql_run -c "
            EXPLAIN (ANALYZE, BUFFERS)
            INSERT INTO pf.seen (side, kind, key, depth, par_kind, par_key, rel)
            SELECT 0, e.dst_kind, e.dst_key, 1, 'g', 'hub', e.rel
            FROM pf.edge e
            WHERE e.src_kind = 'g'
              AND e.src_key = (SELECT key FROM pf.degree WHERE kind = 'g' ORDER BY deg DESC, key LIMIT 1)
            ON CONFLICT (side, kind, key) DO NOTHING"
    done
    psql_run -c "TRUNCATE pf.seen"
} > "$out/hub-expansion.txt" 2>&1

# ---------------------------------------------------------------------------
# 2. How far the catalog actually reaches
# ---------------------------------------------------------------------------
{
    echo "== gm-database-schema-gkt.1 — exhaustive breadth-first search from the seed"
    echo "=="
    echo "== Re-measured rather than inherited from gm-database-schema-9c8.3,"
    echo "== because the recommendation of a depth cap of 4 rests on it."
    echo
    echo "-- level profile out of artist 5665, every vertex kind"
    psql_run -c "
        SELECT v_depth AS depth, v_kind AS kind, count(*) AS vertices
        FROM pf.bfs_all('a', '5665', 12) GROUP BY 1, 2 ORDER BY 1, 2"
    echo "-- the same, collapsed, with the running total"
    psql_run -c "
        WITH b AS (SELECT * FROM pf.bfs_all('a', '5665', 12))
        SELECT v_depth AS depth, count(*) AS vertices,
               sum(count(*)) OVER (ORDER BY v_depth) AS cumulative
        FROM b GROUP BY 1 ORDER BY 1"
    echo "-- how much of the traversal surface that is"
    psql_run -c "
        WITH b AS (SELECT * FROM pf.bfs_all('a', '5665', 12))
        SELECT (SELECT count(*) FROM b) AS reached,
               (SELECT count(*) FROM pf.degree) AS vertices_with_an_edge,
               (SELECT max(v_depth) FROM b) AS eccentricity_of_artist_5665"
    echo "-- and the rim root the distance-5 and distance-6 cases start from"
    psql_run -c "
        SELECT v_depth AS depth, count(*) FILTER (WHERE v_kind = 'a') AS artists
        FROM pf.bfs_all('a', '55563', 12) GROUP BY 1 ORDER BY 1"
} > "$out/coverage.txt" 2>&1

# ---------------------------------------------------------------------------
# 3. What the explore row limit is worth
# ---------------------------------------------------------------------------
{
    echo "== gm-database-schema-gkt.1 — pf.explore with and without its row limit"
    echo "=="
    echo "== The bounded figure is measured by bench/runner.py over 30 iterations."
    echo "== This is the unbounded companion: the same traversal with _limit NULL,"
    echo "== which is also the query the answer-equality check runs. Three"
    echo "== repetitions, because at three hops it is tens of seconds and thirty"
    echo "== would be a quarter of an hour to sharpen a figure whose point is its"
    echo "== order of magnitude."
    echo
    for hops in 2 3; do
        for run in 1 2 3; do
            echo "---------------- *1..$hops unbounded, repetition $run"
            psql_run -c "SET statement_timeout = '300s'" -c "
                EXPLAIN (ANALYZE, BUFFERS)
                SELECT count(*) FROM pf.explore('a', '5665', $hops, NULL)"
        done
        echo "---------------- *1..$hops BOUNDED at 100, for the comparison"
        psql_run -c "
            EXPLAIN (ANALYZE, BUFFERS)
            SELECT count(*) FROM pf.explore('a', '5665', $hops, 100)"
    done
} > "$out/explore-row-limit.txt" 2>&1

echo "wrote $out/hub-expansion.txt, $out/coverage.txt and $out/explore-row-limit.txt"
